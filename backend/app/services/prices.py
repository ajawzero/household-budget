"""Settings, Prices: price status, symbol modes and overrides (PLAN 6.2, 8.2).

- Status: whether TIINGO_API_KEY is set (never the key), the schedule, the
  last price run, each api symbol's newest close and source, error codes
  from the last run, and the 1% sanity check.
- Symbols: every holding of a current statement, newest statement first per
  holding, with its rule mode, the override and the mode in use.
- Overrides (admin): set or remove one per (account, holding). The note is
  typed text and passes detect_sensitive and the private lists. An event row
  records the change with the mode only.
- Class picks (admin): set or remove one per (account, holding), from the
  fixed list in market/classes.py. An event row records the class key.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta

from app.importer.sanitize import PrivateRules
from app.jobs.scheduler import last_success
from app.market.classes import CLASS_CHOICES, class_picks
from app.market.common import ERROR_LABELS, SYMBOL_RE
from app.market.job import JOB_NAME, SCHEDULE_LABEL, STALE_DAYS
from app.market.modes import mode_map, overrides, rule_mode
from app.market.sanity import sanity
from app.market.store import last_dates
from app.services.overrides import EditError, check_text

NO_HOLDING = "No such holding."
BAD_CLASS = "Pick a class from the list."


def _last_run(conn: sqlite3.Connection) -> tuple[dict | None, dict]:
    row = conn.execute(
        "SELECT * FROM job_runs WHERE job = ? ORDER BY id DESC LIMIT 1", (JOB_NAME,)
    ).fetchone()
    if row is None:
        return None, {}
    summary = json.loads(row["summary_json"]) if row["summary_json"] else {}
    calls = summary.get("calls") or {}
    run = {
        "status": "ok" if row["status"] == "ok" else "failed",
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "symbols": int(summary.get("symbols", 0)),
        "updated": int(summary.get("updated", 0)),
        "tiingo_calls": int(calls.get("tiingo", 0)),
        "yahoo_calls": int(calls.get("yahoo", 0)),
        "stopped": [s for s in summary.get("stopped", []) if s in ("tiingo", "yahoo")],
    }
    return run, summary


def status(conn: sqlite3.Connection, key_set: bool, today: date) -> dict:
    run, summary = _last_run(conn)
    errors = summary.get("errors") or {}
    deferred = set(summary.get("deferred") or [])
    last = last_dates(conn)
    counts: dict[str, int] = {}
    for m in mode_map(conn).values():
        if m.mode == "api" and m.symbol:
            counts[m.symbol] = counts.get(m.symbol, 0) + 1
    fresh_from = (today - timedelta(days=STALE_DAYS)).isoformat()
    symbols = []
    for sym in sorted(counts):
        d, source = last.get(sym, (None, None))
        error = errors.get(sym) or ("deferred" if sym in deferred else None)
        symbols.append(
            {
                "symbol": sym,
                "holdings": counts[sym],
                "last_date": d,
                "source": source,
                "stale": d is None or d < fresh_from,
                "error": error,
                "error_label": ERROR_LABELS.get(error) if error else None,
            }
        )
    success = last_success(conn, JOB_NAME)
    return {
        "tiingo_key_set": key_set,
        "schedule": SCHEDULE_LABEL,
        "last_run": run,
        "last_success_at": success.isoformat(timespec="seconds") if success else None,
        "symbols": symbols,
        "sanity": sanity(conn),
    }


def symbol_rows(conn: sqlite3.Connection) -> list[dict]:
    names = {r["account_key"]: r["display_name"] for r in conn.execute("SELECT * FROM accounts")}
    over = overrides(conn)
    modes = mode_map(conn)
    newest: dict[tuple[str, str], sqlite3.Row] = {}
    for r in conn.execute(
        """SELECT h.* FROM holdings h JOIN statements s
             ON s.statement_key = h.statement_key AND s.is_current = 1
           ORDER BY h.as_of"""
    ):
        newest[(r["account_key"], r["holding_key"])] = r
    picks = class_picks(conn)
    rows = []
    for (acct, hk), r in newest.items():
        o = over.get((acct, hk))
        m = modes[(acct, hk)]
        rows.append(
            {
                "account_key": acct,
                "account_name": names.get(acct, acct),
                "holding_key": hk,
                "name": r["holding_name"],
                "printed_symbol": r["symbol"],
                "cusip": r["cusip"],
                "asset_class": r["asset_class"],
                "class_pick": picks.get((acct, hk)),
                "last_statement_date": r["as_of"],
                "rule_mode": rule_mode(hk, r["symbol"], r["asset_class"]).mode,
                "mode": m.mode,
                "symbol": m.symbol,
                "override": None
                if o is None
                else {
                    "price_mode": o["price_mode"],
                    "symbol": o["symbol"],
                    "note": o["note"],
                    "updated_at": o["updated_at"],
                },
            }
        )
    rows.sort(key=lambda x: (x["account_name"], x["account_key"], x["name"], x["holding_key"]))
    return rows


def set_override(
    conn: sqlite3.Connection,
    *,
    account_key: str,
    holding_key: str,
    price_mode: str | None,
    symbol: str | None,
    note: str | None,
    user_id: int,
    now: str,
    private: PrivateRules | None,
) -> None:
    """Set or remove one override. Raises EditError. Call inside a write transaction."""
    held = conn.execute(
        """SELECT h.symbol FROM holdings h JOIN statements s
             ON s.statement_key = h.statement_key AND s.is_current = 1
           WHERE h.account_key = ? AND h.holding_key = ?
           ORDER BY h.as_of DESC LIMIT 1""",
        (account_key, holding_key),
    ).fetchone()
    if held is None:
        raise EditError(404, NO_HOLDING)
    if price_mode is None:
        conn.execute(
            "DELETE FROM symbol_map WHERE account_key = ? AND holding_key = ?",
            (account_key, holding_key),
        )
    else:
        if symbol is not None:
            symbol = symbol.strip().upper() or None
        if price_mode != "api" and symbol is not None:
            raise EditError(422, "Only api mode takes a symbol.")
        if symbol is not None and not SYMBOL_RE.match(symbol):
            raise EditError(422, "A symbol is up to 12 capital letters, digits, dots or dashes.")
        if price_mode == "api" and symbol is None and not held["symbol"]:
            raise EditError(422, "This holding has no printed symbol. Enter one for api mode.")
        note = (note or "").strip() or None
        if note is not None:
            check_text("notes", [note], private)
        conn.execute(
            """INSERT INTO symbol_map (account_key, holding_key, price_mode, symbol, note,
                   updated_by, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (account_key, holding_key) DO UPDATE SET
                 price_mode = excluded.price_mode, symbol = excluded.symbol,
                 note = excluded.note, updated_by = excluded.updated_by,
                 updated_at = excluded.updated_at""",
            (account_key, holding_key, price_mode, symbol, note, user_id, now),
        )
    conn.execute(
        """INSERT INTO events (at, user_id, kind, entity, entity_key, detail_json)
           VALUES (?, ?, 'setting_changed', 'symbol_map', ?, ?)""",
        (
            now,
            user_id,
            f"{account_key}:{holding_key}",
            json.dumps({"price_mode": price_mode}, sort_keys=True),
        ),
    )


def set_class(
    conn: sqlite3.Connection,
    *,
    account_key: str,
    holding_key: str,
    asset_class: str | None,
    user_id: int,
    now: str,
) -> None:
    """Set or remove one class pick (None removes it). Raises EditError. Call inside a
    write transaction."""
    held = conn.execute(
        """SELECT 1 FROM holdings h JOIN statements s
             ON s.statement_key = h.statement_key AND s.is_current = 1
           WHERE h.account_key = ? AND h.holding_key = ? LIMIT 1""",
        (account_key, holding_key),
    ).fetchone()
    if held is None:
        raise EditError(404, NO_HOLDING)
    if asset_class is None:
        conn.execute(
            "DELETE FROM holding_classes WHERE account_key = ? AND holding_key = ?",
            (account_key, holding_key),
        )
    else:
        if asset_class not in CLASS_CHOICES:
            raise EditError(422, BAD_CLASS)
        conn.execute(
            """INSERT INTO holding_classes (account_key, holding_key, asset_class,
                   updated_by, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (account_key, holding_key) DO UPDATE SET
                 asset_class = excluded.asset_class, updated_by = excluded.updated_by,
                 updated_at = excluded.updated_at""",
            (account_key, holding_key, asset_class, user_id, now),
        )
    conn.execute(
        """INSERT INTO events (at, user_id, kind, entity, entity_key, detail_json)
           VALUES (?, ?, 'setting_changed', 'holding_classes', ?, ?)""",
        (
            now,
            user_id,
            f"{account_key}:{holding_key}",
            json.dumps({"asset_class": asset_class}, sort_keys=True),
        ),
    )
