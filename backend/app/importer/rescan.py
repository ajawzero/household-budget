"""Privacy rescan and purge (PLAN 5.8, docs/plans/privacy-rescan.md).

When the private rules file changes, its terms are looked for in everything
stored, and cleaned:

- Stored runs (`run_sections`) are walked by their import types. Free text is
  masked (`PrivateRules.mask`). A key or fixed word that matches is never
  masked: it is listed, and its run is listed for a purge.
- A recurring item whose name is masked to "Personal subscription" also loses
  its `tag` and `keep` (format 1.4), so nothing hints at what it is. The import
  rule `recurring-tag-private` never lets that name carry a tag.
- A flag whose update text is masked gets its updates sorted again, and an update
  that now repeats another is dropped (format 1.4: sorted by on and text, no repeats).
- A masked description changes the row's txn_key (a hash of its desc_norm).
  Every reference moves to the new key in the same write: edits, their
  events, rules made from a row, suggestions, credit choices and events. A
  credit choice's row_sig and near hashes are recomputed from the masked text.
- Every other table is checked column by column, read from the schema:
  free text is masked, keys are listed, hashes are left alone (checked by
  format). JSON columns are checked value by value. Ask threads with a match
  are deleted whole. Match words and merchant rules with a match are deleted.
- The imported tables are rebuilt from the cleaned runs (`replay`), AI facts
  are cleared for a rebuild, and everything is checked again. Anything left
  that is not a listed key fails the rescan, and the whole write rolls back.

`purge_run` removes a run that cannot be cleaned: status `purged`, its
`run_sections` deleted, guard hashes of rows no other run holds dropped, then
`replay`.

Nothing here returns, logs or raises with a matched value or a listed key.
Results are table, column and field names, counts and run ids.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from app.importer.apply import IMPORTED_TABLES, ROWS_PREFIX, canonical, replay, sha256_text
from app.importer.keys import desc_norm, txn_keys
from app.importer.models import CsvRow, FieldPath, Snapshot, is_personal_subscription, text_paths
from app.importer.sanitize import PrivateRules
from app.services.rewards_match import row_sig

log = logging.getLogger("app.importer")

JOB_NAME = "privacy_rescan"
PURGE_JOB = "purge_run"
# settings row: {"sha256": <hash of the private rules file>}. Every rescan stamps
# its updated_at, and the server's caches key on it (balances.import_signature).
# A purge changes import_runs, which they key on too.
HASH_SETTING = "privacy_rules"

_HEX64 = re.compile(r"[0-9a-f]{64}")
_RUN_FIELDS = ("schema_version", "run_id", "mode")
# Rebuilt, deleted whole or walked by type, so the column walk leaves them out.
_OWN_WALK = frozenset(
    {
        "run_sections",
        "ai_facts",
        "ai_facts_state",
        "ai_threads",
        "ai_messages",
        "trip_plans",
        "trip_plan_turns",
    }
)
_DELETE_ON_MATCH = {"reward_match_terms": "term", "merchant_rules": "pattern"}
# Columns that hold a txn_key. The rescan moves them to the masked row's key.
_TXN_KEY_COLUMNS = (
    ("transaction_overrides", "txn_key"),
    ("transaction_events", "txn_key"),
    ("transaction_events", "prev_txn_key"),
    ("merchant_rules", "created_from_txn_key"),
    ("category_suggestions", "txn_key"),
    ("reward_credit_decisions", "txn_key"),
    ("events", "entity_key"),
)
# Dropped from a recurring item whose masked name is the personal subscription's
# (format 1.4, docs/plans/format-14.md section 5): a tag or keep mark would hint at
# what the bill is, and recurring-tag-private never lets that name carry a tag.
# Counted in Report.masked as "runs:recurring.<field>".
_SUBSCRIPTION_HINTS = ("tag", "keep")


class RescanError(Exception):
    """The rescan or purge stopped. Holds a reason code and counts, never values."""

    def __init__(self, reason: str, summary: dict[str, Any] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.summary = {"error": reason, **(summary or {})}


@dataclass
class Report:
    """What the rescan found and did. Names, counts and run ids only."""

    masked: Counter[str] = field(default_factory=Counter)  # "table.column" or "runs:<field>"
    listed: Counter[str] = field(default_factory=Counter)  # keys that match: never masked
    deleted: Counter[str] = field(default_factory=Counter)  # table -> rows deleted
    runs: set[str] = field(default_factory=set)  # stored runs that held a match
    purge: set[str] = field(default_factory=set)  # runs holding a matching key
    rekeyed: int = 0  # txn_keys that moved
    lost: int = 0  # credit choices that lost their row in the rescan
    since: str | None = None  # first import day of a run in `runs`: backups from then hold it

    @property
    def found(self) -> bool:
        return bool(self.masked or self.listed or self.deleted)

    def summary(self) -> dict[str, Any]:
        return {
            "masked": dict(sorted(self.masked.items())),
            "listed": dict(sorted(self.listed.items())),
            "deleted": dict(sorted(self.deleted.items())),
            "runs": sorted(self.runs),
            "purge": sorted(self.purge),
            "rekeyed": self.rekeyed,
            "lost": self.lost,
            "since": self.since,
        }


# --------------------------------------------------------------------------
# Column and field kinds
# --------------------------------------------------------------------------


def _is_hash(name: str) -> bool:
    return name.endswith(("sha256", "_hash")) or name in ("signature", "row_sig", "near")


def _is_key(name: str) -> bool:
    return name in ("key", "username") or name.endswith(("_key", "_id"))


def _shape(path: FieldPath) -> str:
    """('trips', 0, 'items', 2, 'note') -> 'trips.items.note' (no indexes)."""
    return ".".join(p for p in path if isinstance(p, str))


def _hit(private: PrivateRules, text: str) -> bool:
    return bool(text) and not _HEX64.fullmatch(text) and bool(private.check(text))


def _mask_value(private: PrivateRules, name: str, text: str) -> str:
    masked = private.mask(text)
    assert masked is not None
    return desc_norm(masked) if name == "desc_norm" else masked


def _tidy_updates(flag: Any, report: Report) -> None:
    """Keep a flag's updates sorted by (on, text) with no repeats after masking, as the
    import rule wants. A repeat is dropped, counted in Report.deleted."""
    if not isinstance(flag, dict) or not isinstance(flag.get("updates"), list):
        return
    kept: dict[tuple[str, str], Any] = {}
    for u in sorted(flag["updates"], key=lambda u: (u["on"], u["text"])):
        if (u["on"], u["text"]) in kept:
            report.deleted["runs:flags.updates"] += 1
        else:
            kept[(u["on"], u["text"])] = u
    flag["updates"] = list(kept.values())


def _drop_hints(item: Any, report: Report) -> None:
    """Drop the tag and keep of a stored recurring item now named Personal subscription."""
    if not isinstance(item, dict):
        return
    for name in _SUBSCRIPTION_HINTS:
        if item.pop(name, None) is not None:
            report.masked[f"runs:recurring.{name}"] += 1


# --------------------------------------------------------------------------
# Stored runs
# --------------------------------------------------------------------------


def _set(obj: Any, path: FieldPath, value: str) -> bool:
    """Set obj at path. False when the path is not in the stored JSON (a default)."""
    for part in path[:-1]:
        try:
            obj = obj[part]
        except KeyError, IndexError, TypeError:
            return False
    try:
        if obj[path[-1]] is None:
            return False
        obj[path[-1]] = value
    except KeyError, IndexError, TypeError:
        return False
    return True


def _stored_runs(conn: sqlite3.Connection) -> Iterator[tuple[sqlite3.Row, dict[str, Any]]]:
    """(import_runs row, {section: parsed JSON}) for every run with stored sections."""
    runs = conn.execute(
        """SELECT run_id, mode, schema_version FROM import_runs
           WHERE run_id IN (SELECT DISTINCT run_id FROM run_sections) ORDER BY run_id"""
    ).fetchall()
    for run in runs:
        sections = {
            r["section"]: json.loads(r["json"])
            for r in conn.execute(
                "SELECT section, json FROM run_sections WHERE run_id = ?", (run["run_id"],)
            )
        }
        yield run, sections


def _snapshot(run: sqlite3.Row, sections: dict[str, Any]) -> Snapshot:
    snap = {k: v for k, v in sections.items() if not k.startswith(ROWS_PREFIX)}
    return Snapshot.model_validate(
        {
            **snap,
            "schema_version": run["schema_version"],
            "run_id": run["run_id"],
            "mode": run["mode"],
        }
    )


def _row_idents(rows: list[CsvRow]) -> list[tuple[str, int, str]]:
    return [(r.txn_date, r.amount_cents, r.description) for r in rows]


@dataclass
class _Moves:
    keys: dict[str, str] = field(default_factory=dict)  # old txn_key -> new
    sigs: dict[str, str] = field(default_factory=dict)  # old row_sig -> new
    clash: set[str] = field(default_factory=set)  # runs where one old key gets two new keys
    known: set[str] = field(default_factory=set)  # row_sig of every stored row, before masking
    # new txn_key -> the old keys that end there, and old txn_key -> its runs (every row)
    olds: dict[str, set[str]] = field(default_factory=dict)
    runs: dict[str, set[str]] = field(default_factory=dict)


def _clean_runs(conn: sqlite3.Connection, private: PrivateRules, report: Report) -> _Moves:
    """Mask free text in every stored run, list matching keys, and work out
    the txn_key and row_sig moves the masked rows cause."""
    moves = _Moves()
    for run, sections in _stored_runs(conn):
        run_id = run["run_id"]
        changed: set[str] = set()
        flags: set[int] = set()  # flags whose update text was masked
        for path, text, is_text in text_paths(_snapshot(run, sections)):
            if path[0] in _RUN_FIELDS or not _hit(private, text):
                continue
            report.runs.add(run_id)
            if not is_text:
                report.listed[f"runs:{_shape(path)}"] += 1
                report.purge.add(run_id)
                continue
            masked = _mask_value(private, str(path[-1]), text)
            if not _set(sections, path, masked):
                continue
            report.masked[f"runs:{_shape(path)}"] += 1
            changed.add(str(path[0]))
            if _shape(path) == "recurring.name" and is_personal_subscription(masked):
                _drop_hints(sections["recurring"][path[1]], report)
            if _shape(path) == "flags.updates.text":
                flags.add(int(path[1]))
        for i in sorted(flags):  # after the loop: the paths above hold the old indexes
            _tidy_updates(sections["flags"][i], report)
        for section, rows in sections.items():
            if not section.startswith(ROWS_PREFIX):
                continue
            before = [CsvRow.model_validate(r) for r in rows]
            moves.known.update(row_sig(r.description, r.merchant) for r in before)
            for i, row in enumerate(before):
                for path, text, is_text in text_paths(row):
                    if not _hit(private, text):
                        continue
                    report.runs.add(run_id)
                    name = f"runs:rows.{_shape(path)}"
                    if not is_text:
                        report.listed[name] += 1
                        report.purge.add(run_id)
                    elif _set(rows, (i, *path), _mask_value(private, str(path[-1]), text)):
                        report.masked[name] += 1
                        changed.add(section)
            skey = section[len(ROWS_PREFIX) :]
            old_keys = txn_keys(skey, _row_idents(before))
            after = [CsvRow.model_validate(r) for r in rows] if section in changed else before
            new_keys = txn_keys(skey, _row_idents(after)) if section in changed else old_keys
            for old, new in zip(old_keys, new_keys, strict=True):
                moves.olds.setdefault(new, set()).add(old)
                moves.runs.setdefault(old, set()).add(run_id)
            if section not in changed:
                continue
            for old, new, b, a in zip(old_keys, new_keys, before, after, strict=True):
                if old != new:
                    if moves.keys.setdefault(old, new) != new:
                        moves.clash.add(run_id)
                sig_b, sig_a = (
                    row_sig(b.description, b.merchant),
                    row_sig(a.description, a.merchant),
                )
                if sig_b != sig_a:
                    moves.sigs[sig_b] = sig_a
        for section in sorted(changed):
            text = canonical(sections[section])
            conn.execute(
                "UPDATE run_sections SET json = ?, sha256 = ? WHERE run_id = ? AND section = ?",
                (text, sha256_text(text), run_id, section),
            )
        if changed:  # the cleaned run must still load
            _snapshot(run, sections)
    return moves


# --------------------------------------------------------------------------
# Moving keys and hashes
# --------------------------------------------------------------------------


def _referenced(conn: sqlite3.Connection, keys: set[str]) -> set[str]:
    """The keys an edit, event, rule, suggestion or credit choice points at."""
    found: set[str] = set()
    ordered = sorted(keys)
    for i in range(0, len(ordered), 500):
        part = ordered[i : i + 500]
        marks = ", ".join("?" for _ in part)
        for table, col in _TXN_KEY_COLUMNS:
            found |= {
                r[0]
                for r in conn.execute(f"SELECT {col} FROM {table} WHERE {col} IN ({marks})", part)
            }
    return found


def _merged_runs(conn: sqlite3.Connection, moves: _Moves) -> set[str]:
    """Runs where masking gives two stored rows one key while something points
    at one of the rows that moves. The rescan cannot tell which row it meant,
    so it stops (a correction that changed only the private words, for
    example). A merge nothing points at is safe: the replay rebuilds it."""
    merged = {new: olds for new, olds in moves.olds.items() if len(olds) > 1}
    moving = {old for new, olds in merged.items() for old in olds if old != new}
    hit = _referenced(conn, moving)
    return {
        run
        for new, olds in merged.items()
        if any(old != new and old in hit for old in olds)
        for old in olds
        for run in moves.runs[old]
    }


def _move_keys(conn: sqlite3.Connection, moves: _Moves) -> None:
    """Every txn_key reference to its new key, in two steps: first to the new
    key in upper case (txn_keys are lower case hex, so no live key is taken
    while a chain such as a -> b, b -> c moves), then to lower case."""
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS rescan_moves (old TEXT PRIMARY KEY, new TEXT)")
    conn.execute("DELETE FROM rescan_moves")
    conn.executemany("INSERT INTO rescan_moves (old, new) VALUES (?, ?)", moves.keys.items())
    try:
        for table, col in _TXN_KEY_COLUMNS:
            conn.execute(
                f"""UPDATE {table} SET {col} =
                      upper((SELECT new FROM rescan_moves WHERE old = {table}.{col}))
                    WHERE {col} IN (SELECT old FROM rescan_moves)"""
            )
            conn.execute(
                f"""UPDATE {table} SET {col} = lower({col})
                    WHERE {col} IN (SELECT upper(new) FROM rescan_moves)"""
            )
    except sqlite3.IntegrityError:
        raise RescanError("key-clash") from None
    finally:
        conn.execute("DROP TABLE rescan_moves")


def _move_guards(conn: sqlite3.Connection, moves: _Moves) -> None:
    """Credit choice guards: row_sig and near from the masked text, near keyed
    by the new txn_keys (rewards-inputs plan, section 9). A hash of a row no
    stored run holds (a purged run) cannot be recomputed, so it is dropped:
    without its row_sig the choice is lost."""
    for r in conn.execute("SELECT txn_key, guard_json FROM reward_credit_decisions").fetchall():
        guard = json.loads(r["guard_json"])
        if not isinstance(guard, dict):
            continue
        new = {k: v for k, v in guard.items() if k != "row_sig"}
        if guard.get("row_sig") in moves.known:
            new["row_sig"] = moves.sigs.get(guard["row_sig"], guard["row_sig"])
        if isinstance(guard.get("near"), dict):
            new["near"] = {
                moves.keys.get(k, k): moves.sigs.get(v, v)
                for k, v in guard["near"].items()
                if v in moves.known
            }
        if new != guard:
            conn.execute(
                "UPDATE reward_credit_decisions SET guard_json = ? WHERE txn_key = ?",
                (_dump(new), r["txn_key"]),
            )


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------


def _dump(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _tables(conn: sqlite3.Connection) -> list[str]:
    return [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
    ]


def _text_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})") if r["type"] == "TEXT"]


def _walk_json(
    private: PrivateRules, obj: Any, name: str, where: str, report: Report, mask: bool
) -> tuple[Any, bool]:
    """Mask matching JSON values (`mask`) and list matching keys. Returns the
    new value and whether a value is still a match that is not a key."""
    left = False
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and _hit(private, k):
                report.listed[f"{where}:key"] += 1
            out[k], more = _walk_json(private, v, k, where, report, mask)
            left |= more
        return out, left
    if isinstance(obj, list):
        items = [_walk_json(private, v, name, where, report, mask) for v in obj]
        return [v for v, _ in items], any(more for _, more in items)
    if isinstance(obj, str) and not _is_hash(name) and _hit(private, obj):
        if _is_key(name):
            report.listed[f"{where}.{name}"] += 1
        elif mask:
            report.masked[f"{where}.{name}"] += 1
            return _mask_value(private, name, obj), False
        else:
            return obj, True
    return obj, left


def _json_or_none(value: str) -> Any:
    """Parsed JSON, or None when the text is not JSON (then it is checked as text)."""
    try:
        return json.loads(value)
    except ValueError:
        return None


def _clean_tables(
    conn: sqlite3.Connection, private: PrivateRules, report: Report, *, mask: bool
) -> bool:
    """Check every TEXT column of every table but the imported and own-walk
    ones (or of every table, `mask` False, for the final check). Returns
    whether a match that is not a key is left."""
    left = False
    skip = _OWN_WALK | (frozenset(IMPORTED_TABLES) if mask else frozenset())
    for table in _tables(conn):
        if table in skip:
            continue
        cols = [c for c in _text_columns(conn, table) if not _is_hash(c)]
        if not cols:
            continue
        delete_col = _DELETE_ON_MATCH.get(table)
        for r in conn.execute(f"SELECT rowid AS _rid, {', '.join(cols)} FROM {table}").fetchall():
            updates: dict[str, str] = {}
            for col in cols:
                value = r[col]
                if not isinstance(value, str):
                    continue
                obj = _json_or_none(value) if col == "json" or col.endswith("_json") else None
                if obj is not None:
                    new, more = _walk_json(private, obj, col, f"{table}.{col}", report, mask)
                    left |= more
                    if new != obj:
                        updates[col] = _dump(new)
                elif not _hit(private, value):
                    continue
                elif col == delete_col and mask:
                    updates = {}
                    conn.execute(f"DELETE FROM {table} WHERE rowid = ?", (r["_rid"],))
                    report.deleted[table] += 1
                    break
                elif _is_key(col):
                    report.listed[f"{table}.{col}"] += 1
                elif mask:
                    updates[col] = _mask_value(private, col, value)
                    report.masked[f"{table}.{col}"] += 1
                else:
                    left = True
            if updates:
                sets = ", ".join(f"{c} = ?" for c in updates)
                conn.execute(
                    f"UPDATE {table} SET {sets} WHERE rowid = ?", (*updates.values(), r["_rid"])
                )
    return left


def _strings(obj: Any) -> Iterator[str]:
    """Every string in parsed JSON, object keys too."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def _ask_threads(conn: sqlite3.Connection, private: PrivateRules) -> list[str]:
    """Ask threads whose title or any message matches."""
    hits = {
        r["thread_id"]
        for r in conn.execute("SELECT thread_id, title FROM ai_threads")
        if _hit(private, r["title"])
    }
    for r in conn.execute("SELECT thread_id, content_json FROM ai_messages"):
        if r["thread_id"] in hits:
            continue
        try:
            texts = list(_strings(json.loads(r["content_json"])))
        except ValueError:
            texts = [r["content_json"]]
        if any(_hit(private, t) for t in texts):
            hits.add(r["thread_id"])
    return sorted(hits)


def _trip_plans(conn: sqlite3.Connection, private: PrivateRules) -> list[str]:
    """Trip plans whose title, request, trip key or any turn matches. Every
    string is also read as the planner reads a link (%-escapes, look-alike
    letters), so a link in any case or form is found."""
    from app.ai.planner import private_in_url

    def hit(text: str) -> bool:
        if _hit(private, text):
            return True
        return bool(text) and not _HEX64.fullmatch(text) and private_in_url(text, private)

    hits = set()
    for r in conn.execute("SELECT plan_id, title, request_json, trip_key FROM trip_plans"):
        try:
            texts = list(_strings(json.loads(r["request_json"])))
        except ValueError:
            texts = [r["request_json"]]
        if any(_hit(private, t) for t in [r["title"], r["trip_key"] or "", *texts]):
            hits.add(r["plan_id"])
    for r in conn.execute("SELECT plan_id, content_json FROM trip_plan_turns"):
        if r["plan_id"] in hits:
            continue
        try:
            texts = list(_strings(json.loads(r["content_json"])))
        except ValueError:
            texts = [r["content_json"]]
        if any(hit(t) for t in texts):
            hits.add(r["plan_id"])
    return sorted(hits)


def _lost(conn: sqlite3.Connection) -> int:
    """Credit choices that lost their row (the Rewards page's "lost" flags)."""
    from app.services.rewards_overlay import effective_rewards
    from app.state import readers

    eff = effective_rewards(conn, readers.monthly_runs(conn))
    return sum(1 for f in eff.meta.flags if f.kind == "lost") if eff is not None else 0


# --------------------------------------------------------------------------
# Rescan
# --------------------------------------------------------------------------


def rescan(conn: sqlite3.Connection, private: PrivateRules, now: str) -> Report:
    """Find and clean the private lists' terms everywhere. The caller holds the
    write transaction; a RescanError means it must roll back."""
    report = Report()
    lost_before = _lost(conn)
    moves = _clean_runs(conn, private, report)
    if report.runs:
        marks = ", ".join("?" for _ in report.runs)
        report.since = conn.execute(
            f"SELECT min(substr(started_at, 1, 10)) FROM import_runs WHERE run_id IN ({marks})",
            sorted(report.runs),
        ).fetchone()[0]
    moves.clash |= _merged_runs(conn, moves)
    if moves.clash:
        report.purge |= moves.clash
        raise RescanError("key-clash", report.summary())
    report.rekeyed = len(moves.keys)
    _move_keys(conn, moves)
    _move_guards(conn, moves)
    threads = _ask_threads(conn, private)
    conn.executemany("DELETE FROM ai_threads WHERE thread_id = ?", [(t,) for t in threads])
    if threads:
        report.deleted["ai_threads"] = len(threads)
    plans = _trip_plans(conn, private)
    conn.executemany("DELETE FROM trip_plans WHERE plan_id = ?", [(p,) for p in plans])
    if plans:
        report.deleted["trip_plans"] = len(plans)
    _clean_tables(conn, private, report, mask=True)
    if report.masked or report.deleted:
        conn.execute("DELETE FROM ai_facts")
        conn.execute("DELETE FROM ai_facts_state")
    if any(k.startswith("runs:") for k in report.masked):
        replay(conn, now)
    if (
        _clean_tables(conn, private, Report(), mask=False)
        or _ask_threads(conn, private)
        or _trip_plans(conn, private)
    ):
        raise RescanError("still-matches", report.summary())
    report.lost = max(0, _lost(conn) - lost_before)
    return report


def stored_hash(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT value_json FROM settings WHERE key = ?", (HASH_SETTING,)).fetchone()
    if row is None:
        return None
    value = json.loads(row["value_json"])
    return value.get("sha256") if isinstance(value, dict) else None


def store_hash(conn: sqlite3.Connection, digest: str, now: str) -> None:
    conn.execute(
        """INSERT INTO settings (key, value_json, updated_by, updated_at) VALUES (?, ?, NULL, ?)
           ON CONFLICT (key) DO UPDATE SET value_json = excluded.value_json,
             updated_by = NULL, updated_at = excluded.updated_at""",
        (HASH_SETTING, json.dumps({"sha256": digest}), now),
    )


# --------------------------------------------------------------------------
# Purge
# --------------------------------------------------------------------------


def _run_sigs(conn: sqlite3.Connection, where: str, run_id: str) -> set[str]:
    sigs: set[str] = set()
    for r in conn.execute(
        f"SELECT json FROM run_sections WHERE section LIKE 'rows:%' AND {where}", (run_id,)
    ):
        for row in json.loads(r["json"]):
            sigs.add(row_sig(row["description"], row.get("merchant")))
    return sigs


def purge_run(conn: sqlite3.Connection, run_id: str, now: str) -> dict[str, Any]:
    """Mark an imported run purged, delete its stored sections, drop credit
    choice hashes of rows no other run holds, and rebuild the imported tables.
    The caller holds the write transaction."""
    rec = conn.execute("SELECT status FROM import_runs WHERE run_id = ?", (run_id,)).fetchone()
    if rec is None or rec["status"] != "imported":
        raise RescanError("not-imported")
    gone = _run_sigs(conn, "run_id = ?", run_id) - _run_sigs(conn, "run_id <> ?", run_id)
    guards = 0
    for r in conn.execute("SELECT txn_key, guard_json FROM reward_credit_decisions").fetchall():
        guard = json.loads(r["guard_json"])
        if not isinstance(guard, dict):
            continue
        new = {k: v for k, v in guard.items() if not (k == "row_sig" and v in gone)}
        if isinstance(guard.get("near"), dict):
            new["near"] = {k: v for k, v in guard["near"].items() if v not in gone}
        if new != guard:
            guards += 1
            conn.execute(
                "UPDATE reward_credit_decisions SET guard_json = ? WHERE txn_key = ?",
                (_dump(new), r["txn_key"]),
            )
    sections = conn.execute("DELETE FROM run_sections WHERE run_id = ?", (run_id,)).rowcount
    conn.execute("UPDATE import_runs SET status = 'purged' WHERE run_id = ?", (run_id,))
    conn.execute("DELETE FROM ai_facts")
    conn.execute("DELETE FROM ai_facts_state")
    replay(conn, now)
    return {"run": run_id, "sections": sections, "guards": guards}
