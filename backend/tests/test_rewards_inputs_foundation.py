"""Rewards inputs, chunk A (docs/plans/rewards-inputs.md sections 2, 3, 8 and 10):
migration 0010, the no-op overlay in one read transaction, the four call sites,
the fingerprint, reapply and re-import, and the AI's reach.

Fake samples only. With no app rows, every reader gives the R1 output.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from conftest import SAMPLE_RUNS, SAMPLES
from fastapi.testclient import TestClient
from goldens import E14_FIELDS, digest, older

from app import cli, config
from app.ai.facts import signature
from app.ai.safe import AuthorizerDenied, ai_connection, run
from app.config import Settings
from app.db import migrate as migrate_mod
from app.db.connect import connect, read_connection, write_connection, write_transaction
from app.db.migrate import list_migrations, migrate, user_version
from app.importer.apply import IMPORTED_TABLES
from app.importer.models import Rewards
from app.importer.runner import IMPORTED, UNCHANGED, import_folder, reapply
from app.logging import JsonFormatter
from app.services import fingerprint as fp_mod
from app.services import rewards as rw
from app.services import rewards_inputs as ri
from app.services import rewards_match as rm
from app.services import rewards_overlay as ov
from app.services import trips
from app.services.balances import clear_cache
from app.services.dashboard import dashboard
from app.services.fingerprint import TABLES, fingerprint
from app.state import readers

TODAY = date(2026, 10, 20)
MICRO = 1_000_000
NOW = "2026-10-20T12:00:00+00:00"
MIGRATION = Path(migrate_mod.MIGRATIONS_DIR) / "0010_rewards_inputs.sql"

# The six tables and their fingerprint keys (plan section 2).
REWARD_TABLES = {
    "reward_balances": ("program_key", "as_of"),
    "reward_app_programs": ("program_key",),
    "reward_benefit_edits": ("benefit_key",),
    "reward_match_terms": ("benefit_key", "term"),
    "reward_credit_decisions": ("txn_key",),
    "reward_cycle_marks": ("benefit_key", "cycle_start"),
}


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    clear_cache()


def on(monkeypatch: pytest.MonkeyPatch, day: date) -> None:
    monkeypatch.setattr(config, "household_today", lambda: day)
    clear_cache()


# Fields later chunks add to the page. The R1 digests leave them out.
NEW_PAGE_FIELDS = ("notices", "orphans", "hidden", "review_counts")  # chunks B, C and D2
NEW_CHECK_FIELDS = ("counts",)  # chunk D2
NEW_PROGRAM_FIELDS = ("origin", "chat_balance_micro")  # chunk B
# chunk C
NEW_BENEFIT_FIELDS = (
    "origin",
    "edited_fields",
    "notices",
    "hidden",
    "match_off",
    "terms_count",
    "amount_from",
)
NEW_CAP_PERK_FIELDS = ("origin",)  # chunk C
NEW_CYCLE_FIELDS = ("source", "chat_used_cents", "covered", "over_cents", "rows")  # chunk D2
NEW_MILESTONE_FIELDS = ("bills", "bills_monthly_cents", "bills_to_move")  # gap audit C7
NEW_REDEMPTION_FIELDS = ("trip_name",)  # gap audit E-0


def _r1_cycle(c: dict | None) -> dict | None:
    return None if c is None else {k: v for k, v in c.items() if k not in NEW_CYCLE_FIELDS}


def _r1_benefit(b: dict) -> dict:
    out = {k: v for k, v in b.items() if k not in NEW_BENEFIT_FIELDS}
    out["cycle"] = _r1_cycle(b["cycle"])
    out["past_cycles"] = [_r1_cycle(c) for c in b["past_cycles"]]
    return out


def r1(page: dict) -> dict:
    """The page with only its R1 fields."""
    page = older(page, E14_FIELDS.get("/api/rewards", {}))
    out = {k: v for k, v in page.items() if k not in NEW_PAGE_FIELDS}
    out["programs"] = [
        {k: v for k, v in p.items() if k not in NEW_PROGRAM_FIELDS} for p in page["programs"]
    ]
    out["benefits"] = [_r1_benefit(b) for b in page["benefits"]]
    for name in ("caps", "perks"):
        out[name] = [
            {k: v for k, v in x.items() if k not in NEW_CAP_PERK_FIELDS} for x in page[name]
        ]
    out["milestones"] = [
        {k: v for k, v in m.items() if k not in NEW_MILESTONE_FIELDS} for m in page["milestones"]
    ]
    out["redemptions"] = [
        {k: v for k, v in x.items() if k not in NEW_REDEMPTION_FIELDS} for x in page["redemptions"]
    ]
    return out


def r1_checks(block: dict) -> dict:
    """The Checks rewards block with only its R1 fields."""
    return {k: v for k, v in block.items() if k not in NEW_CHECK_FIELDS}


def insert(conn: sqlite3.Connection, table: str, row: dict) -> None:
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in REWARD_TABLES}


def dump(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    return {
        t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2")]
        for t in REWARD_TABLES
    }


def add_reward_inputs(conn: sqlite3.Connection, user_id: int | None = None) -> None:
    """One or more fake rows in every reward table. Call inside a write transaction.
    The credit decision uses a real txn_key when the samples are in, with its
    guard as set_choice saves it (ri.choice_guard)."""
    row = conn.execute("SELECT txn_key FROM transactions ORDER BY txn_key LIMIT 1").fetchone()
    txn_key = row[0] if row else "ab" * 32
    by = {"updated_by": user_id, "updated_at": NOW}
    insert(
        conn,
        "reward_balances",
        {
            "program_key": "harbor-miles",
            "as_of": "2026-10-15",
            "balance_micro": 52_000 * MICRO,
            "unit": "whole",
        }
        | by,
    )
    insert(
        conn,
        "reward_app_programs",
        {
            "program_key": "app-lantern-air",
            "name": "Lantern Air",
            "kind": "miles",
            "cpp_low_micro": MICRO,
            "cpp_high_micro": 2 * MICRO,
            "note": "Family pool",
        }
        | by,
    )
    insert(
        conn,
        "reward_benefit_edits",
        {
            "benefit_key": "dining-credit",
            "origin": "chat",
            "account_key": "summit-card",
            "fields_json": json.dumps({"amount": "12.00", "note": "Use it at the corner cafe"}),
            "base_json": json.dumps({"amount": "10.00", "note": None}),
            "amount_from": "2026-11-01",
        }
        | by,
    )
    body = {
        "key": "app-lounge-credit",
        "account_key": "summit-card",
        "name": "Lounge credit",
        "kind": "credit",
        "cadence": "multi_year",
        "amount": "50.00",
        "cycles": [{"start": "2026-01-01", "end": "2027-12-31"}],
    }
    insert(
        conn,
        "reward_benefit_edits",
        {
            "benefit_key": "app-lounge-credit",
            "origin": "app",
            "account_key": "summit-card",
            "fields_json": json.dumps(body),
        }
        | by,
    )
    insert(
        conn, "reward_match_terms", {"benefit_key": "dining-credit", "term": "DINING CREDIT"} | by
    )
    sig = rm.row_sig("DINING CREDIT", "Dining credit")
    guard = ri.choice_guard(conn, txn_key) or {
        "account_key": "summit-card",
        "txn_date": "2026-09-12",
        "amount_cents": 1_000,
        "desc_norm": "DINING CREDIT",
        "merchant": "Dining credit",
        "run_id": "2026-10-12",
        "row_sig": sig,
        "near": {txn_key: sig},
    }
    insert(
        conn,
        "reward_credit_decisions",
        {
            "txn_key": txn_key,
            "decision": "link",
            "benefit_key": "dining-credit",
            "cycle_start": "2026-09-01",
            "guard_json": json.dumps(guard),
        }
        | by,
    )
    insert(
        conn,
        "reward_cycle_marks",
        {
            "benefit_key": "wireless-credit",
            "cycle_start": "2026-10-01",
            "used_cents": 500,
            "used_on": "2026-10-05",
        }
        | by,
    )


# --------------------------------------------------------------------------
# Migration
# --------------------------------------------------------------------------


def test_migration_adds_six_strict_app_tables(db: sqlite3.Connection) -> None:
    assert list_migrations()[9].path == MIGRATION
    assert user_version(db) == len(list_migrations()) >= 10
    tables = {
        r["name"]: r["sql"]
        for r in db.execute("SELECT name, sql FROM sqlite_schema WHERE type = 'table'")
    }
    assert {t for t in tables if t.startswith("reward_")} == set(REWARD_TABLES)
    for t in REWARD_TABLES:
        assert tables[t].rstrip().endswith("STRICT"), t
        fks = db.execute(f"PRAGMA foreign_key_list({t})").fetchall()
        assert fks and {(f["table"], f["from"], f["on_delete"]) for f in fks} == {
            ("users", "updated_by", "SET NULL")
        }, t
        assert t not in IMPORTED_TABLES
        assert TABLES[t] == (REWARD_TABLES[t], "")
        cols = fp_mod._columns(db, t)
        assert "updated_by" not in cols and "updated_at" not in cols
    assert {"updated_by", "updated_at"} <= fp_mod._BOOKKEEPING
    # Balances are deleted by id: a deleted id is never given again.
    assert "INTEGER PRIMARY KEY AUTOINCREMENT" in tables["reward_balances"]
    indexes = {
        r[0]: r[1]
        for r in db.execute("SELECT name, tbl_name FROM sqlite_schema WHERE type = 'index'")
    }
    assert indexes["reward_benefit_edits_account"] == "reward_benefit_edits"
    assert indexes["reward_match_terms_one"] == "reward_match_terms"
    assert indexes["reward_credit_decisions_benefit"] == "reward_credit_decisions"
    views = {r[0] for r in db.execute("SELECT sql FROM sqlite_schema WHERE type = 'view'")}
    assert not any("reward_" in sql for sql in views)  # no new views, none read them


def test_migration_holds_no_long_digit_run() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    assert max(len(d) for d in re.findall(r"[0-9]+", sql)) < 5


def test_upgrade_from_9_makes_a_pre_migrate_copy(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = tmp_path / "migrations-r1"
    old.mkdir()
    for m in list_migrations():
        if m.version <= 9:
            shutil.copy(m.path, old)
    monkeypatch.setattr(migrate_mod, "MIGRATIONS_DIR", old)
    assert migrate(settings=settings) == 9
    with write_connection(settings.db_path) as w:
        w.execute("INSERT INTO settings VALUES ('kept', '1', NULL, 't')")
    monkeypatch.setattr(migrate_mod, "MIGRATIONS_DIR", MIGRATION.parent)
    assert migrate(settings=settings) == len(list_migrations())
    copy = settings.backup_dir / "pre-migrate-0010.db"
    assert copy.exists()
    before = sqlite3.connect(copy)
    try:
        assert before.execute("PRAGMA user_version").fetchone()[0] == 9
        names = {r[0] for r in before.execute("SELECT name FROM sqlite_schema")}
        assert not any(n.startswith("reward_") for n in names)
    finally:
        before.close()
    with read_connection(settings.db_path) as conn:
        assert conn.execute("SELECT key FROM settings").fetchall()[0][0] == "kept"
        assert counts(conn) == dict.fromkeys(REWARD_TABLES, 0)


# Each case breaks one CHECK of migration 0010 and nothing else. The third item
# names the CHECK, and every CHECK in the file has a case (proved below).
GOOD = {
    "reward_balances": {
        "program_key": "harbor-miles",
        "as_of": "2026-10-15",
        "balance_micro": 0,
        "unit": "cash",
        "updated_at": NOW,
    },
    "reward_app_programs": {
        "program_key": "app-lantern-air",
        "name": "Lantern Air",
        "kind": "points",
        "updated_at": NOW,
    },
    "reward_benefit_edits": {
        "benefit_key": "dining-credit",
        "origin": "chat",
        "account_key": "summit-card",
        "updated_at": NOW,
    },
    "reward_match_terms": {"benefit_key": "dining-credit", "term": "DIN", "updated_at": NOW},
    "reward_credit_decisions": {
        "txn_key": "ab" * 32,
        "decision": "link",
        "benefit_key": "dining-credit",
        "guard_json": "{}",
        "updated_at": NOW,
    },
    "reward_cycle_marks": {
        "benefit_key": "dining-credit",
        "cycle_start": "2026-10-01",
        "used_cents": 0,
        "updated_at": NOW,
    },
}
CHECKS = [
    ("reward_balances", {"program_key": ""}, "balances.program_key"),
    ("reward_balances", {"program_key": "k" * 41}, "balances.program_key"),
    ("reward_balances", {"as_of": "2026-1-015"}, "balances.as_of"),
    ("reward_balances", {"as_of": "Oct 15 26"}, "balances.as_of"),
    ("reward_balances", {"balance_micro": -1}, "balances.balance_micro"),
    ("reward_balances", {"unit": "points"}, "balances.unit"),
    ("reward_balances", {"unit": "WHOLE"}, "balances.unit"),
    ("reward_app_programs", {"program_key": "lantern-air"}, "programs.program_key"),
    ("reward_app_programs", {"program_key": "app-"}, "programs.program_key"),
    ("reward_app_programs", {"program_key": "app-" + "k" * 37}, "programs.program_key"),
    ("reward_app_programs", {"kind": "timeshare"}, "programs.kind"),
    ("reward_app_programs", {"program_group": "tertiary"}, "programs.program_group"),
    ("reward_app_programs", {"cpp_low_micro": 1}, "programs.cpp_pair"),
    ("reward_app_programs", {"cpp_high_micro": 1}, "programs.cpp_pair"),
    (
        "reward_app_programs",
        {"kind": "cash_back", "cpp_low_micro": 1, "cpp_high_micro": 2},
        "programs.cpp_range",
    ),
    ("reward_app_programs", {"cpp_low_micro": 3, "cpp_high_micro": 2}, "programs.cpp_range"),
    ("reward_app_programs", {"cpp_low_micro": -1, "cpp_high_micro": 2}, "programs.cpp_range"),
    ("reward_benefit_edits", {"benefit_key": ""}, "edits.benefit_key"),
    ("reward_benefit_edits", {"benefit_key": "k" * 41}, "edits.benefit_key"),
    (
        "reward_benefit_edits",
        {"benefit_key": "app-lounge-credit", "origin": "import"},
        "edits.origin",
    ),
    ("reward_benefit_edits", {"fields_json": "not json"}, "edits.fields_json"),
    ("reward_benefit_edits", {"fields_json": "[]"}, "edits.fields_json"),
    ("reward_benefit_edits", {"fields_json": '"text"'}, "edits.fields_json"),
    ("reward_benefit_edits", {"base_json": "{"}, "edits.base_json"),
    ("reward_benefit_edits", {"base_json": "[1]"}, "edits.base_json"),
    ("reward_benefit_edits", {"hidden": 2}, "edits.hidden"),
    ("reward_benefit_edits", {"match_off": -1}, "edits.match_off"),
    ("reward_benefit_edits", {"origin": "app"}, "edits.app_key"),
    ("reward_match_terms", {"term": "AB"}, "terms.term"),
    ("reward_match_terms", {"term": "T" * 41}, "terms.term"),
    ("reward_credit_decisions", {"txn_key": "ab" * 31}, "decisions.txn_key"),
    ("reward_credit_decisions", {"decision": "maybe", "benefit_key": None}, "decisions.decision"),
    (
        "reward_credit_decisions",
        {"decision": "not_benefit", "benefit_key": None, "reason": "lost"},
        "decisions.reason",
    ),
    ("reward_credit_decisions", {"guard_json": "nope"}, "decisions.guard_json"),
    ("reward_credit_decisions", {"guard_json": "[]"}, "decisions.guard_json"),
    ("reward_credit_decisions", {"benefit_key": None}, "decisions.link_benefit"),
    (
        "reward_credit_decisions",
        {"decision": "not_benefit", "reason": "return"},
        "decisions.link_benefit",
    ),
    ("reward_credit_decisions", {"reason": "return"}, "decisions.reason_pair"),
    (
        "reward_credit_decisions",
        {"decision": "not_benefit", "benefit_key": None},
        "decisions.reason_pair",
    ),
    (
        "reward_credit_decisions",
        {
            "decision": "not_benefit",
            "benefit_key": None,
            "reason": "other",
            "cycle_start": "2026-09-01",
        },
        "decisions.cycle_start",
    ),
    ("reward_cycle_marks", {"used_cents": -1}, "marks.used_cents"),
]
# NOT NULL, STRICT types and the users foreign key.
OTHER_RULES = [
    ("reward_balances", {"updated_at": None}),
    ("reward_balances", {"unit": None}),  # NULL passes a CHECK; NOT NULL stops it
    ("reward_balances", {"balance_micro": "lots"}),
    ("reward_balances", {"updated_by": 999}),
    ("reward_app_programs", {"name": None}),
    ("reward_benefit_edits", {"account_key": None}),
    ("reward_match_terms", {"benefit_key": None}),
    ("reward_credit_decisions", {"guard_json": None}),
    ("reward_cycle_marks", {"cycle_start": None}),
    ("reward_cycle_marks", {"used_cents": 1.5}),
]


def _check_spans(sql: str) -> list[tuple[int, int]]:
    """Where each CHECK (...) of the migration starts and ends."""
    spans = []
    for m in re.finditer(r"\bCHECK \(", sql):
        depth, i = 0, m.end() - 1
        while True:
            depth += {"(": 1, ")": -1}.get(sql[i], 0)
            i += 1
            if depth == 0:
                break
        spans.append((m.start(), i))
    return spans


def _takes(sql: str, table: str, row: dict) -> bool:
    """True when the row goes in on a fresh in-memory schema built from `sql`."""
    mem = sqlite3.connect(":memory:")
    try:
        mem.executescript(sql)
        insert(mem, table, row)
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        mem.close()


def test_every_check_has_a_case_that_breaks_only_it() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    spans = _check_spans(sql)
    assert len(spans) == 25
    for table, row in GOOD.items():
        assert _takes(sql, table, row), table
    owner: dict[str, set[int]] = {}
    for table, change, label in CHECKS:
        row = GOOD[table] | change
        assert not _takes(sql, table, row), label
        # Only taking out its own CHECK lets the row in: no other rule stops it.
        opens = [
            i
            for i, (a, b) in enumerate(spans)
            if _takes(sql[:a] + "CHECK (1)" + sql[b:], table, row)
        ]
        assert len(opens) == 1, label
        owner.setdefault(label, set()).update(opens)
    assert all(len(found) == 1 for found in owner.values()), owner
    assert sorted(i for found in owner.values() for i in found) == list(range(len(spans)))


def test_good_rows_go_in(db: sqlite3.Connection) -> None:
    with write_transaction(db):
        for table, row in GOOD.items():
            insert(db, table, row)
        # the other unit, and the other side of each pair rule
        insert(
            db,
            "reward_balances",
            GOOD["reward_balances"] | {"program_key": "summit-points", "unit": "whole"},
        )
        insert(
            db,
            "reward_app_programs",
            GOOD["reward_app_programs"]
            | {
                "program_key": "app-pine-air",
                "kind": "miles",
                "cpp_low_micro": MICRO,
                "cpp_high_micro": MICRO,
            },
        )
        insert(
            db,
            "reward_benefit_edits",
            GOOD["reward_benefit_edits"]
            | {
                "benefit_key": "app-lounge-credit",
                "origin": "app",
                "hidden": 1,
                "match_off": 1,
            },
        )
        insert(
            db,
            "reward_credit_decisions",
            GOOD["reward_credit_decisions"]
            | {
                "txn_key": "cd" * 32,
                "decision": "not_benefit",
                "benefit_key": None,
                "reason": "points",
            },
        )
        insert(
            db,
            "reward_credit_decisions",
            GOOD["reward_credit_decisions"]
            | {
                "txn_key": "ef" * 32,
                "cycle_start": "2026-09-01",
            },
        )
    row = db.execute("SELECT * FROM reward_app_programs WHERE program_key = 'app-lantern-air'")
    assert row.fetchone()["program_group"] == "secondary"
    edit = db.execute("SELECT * FROM reward_benefit_edits WHERE benefit_key = 'dining-credit'")
    e = edit.fetchone()
    assert (e["fields_json"], e["base_json"], e["hidden"], e["match_off"]) == ("{}", "{}", 0, 0)


@pytest.mark.parametrize(("table", "change", "check"), CHECKS)
def test_every_check_fires(db: sqlite3.Connection, table: str, change: dict, check: str) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, table, GOOD[table] | change)
    assert counts(db)[table] == 0


@pytest.mark.parametrize(("table", "change"), OTHER_RULES)
def test_not_null_types_and_users_key(db: sqlite3.Connection, table: str, change: dict) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, table, GOOD[table] | change)


def test_unique_keys(db: sqlite3.Connection) -> None:
    for table, row in GOOD.items():
        insert(db, table, row)
        with pytest.raises(sqlite3.IntegrityError):
            insert(db, table, row)
    # one match word per benefit, case left out; the same word on another benefit is fine
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "reward_match_terms", GOOD["reward_match_terms"] | {"term": "din"})
    insert(db, "reward_match_terms", GOOD["reward_match_terms"] | {"benefit_key": "travel"})
    # balances: one per program per day, whatever the unit
    with pytest.raises(sqlite3.IntegrityError):
        insert(db, "reward_balances", GOOD["reward_balances"] | {"unit": "whole"})
    insert(db, "reward_balances", GOOD["reward_balances"] | {"as_of": "2026-10-16"})


def test_deleting_a_user_keeps_the_inputs(samples: Settings) -> None:
    from app.auth.users import create_user

    with write_connection(samples.db_path) as w:
        user = create_user(w, "casey", "Casey Example", "member-test-pass-9", "member")
        add_reward_inputs(w, user.id)
    with write_connection(samples.db_path) as w:
        w.execute("DELETE FROM users WHERE id = ?", (user.id,))
    with read_connection(samples.db_path) as conn:
        assert counts(conn)["reward_benefit_edits"] == 2
        for t in REWARD_TABLES:
            ids = {r[0] for r in conn.execute(f"SELECT updated_by FROM {t}")}
            assert ids == {None}, t


# --------------------------------------------------------------------------
# The overlay
# --------------------------------------------------------------------------


def _effective(conn: sqlite3.Connection) -> ov.Effective | None:
    return ov.effective_rewards(conn, readers.monthly_runs(conn))


# What matching finds on the samples with no app rows (chunk D2).
MATCH_META = ("cycle_sources", "chat_used", "chat_used_on", "covered", "credit_rows", "review")
MATCH_READS = ("FROM v_transactions", "FROM statements", "FROM transactions")


def test_none_before_the_first_13_run(db: sqlite3.Connection) -> None:
    assert _effective(db) is None
    with write_transaction(db):
        add_reward_inputs(db)
    assert _effective(db) is None  # inputs alone show nothing


def test_no_app_rows_gives_the_chat_view(samples: Settings) -> None:
    with read_connection(samples.db_path) as conn:
        eff = _effective(conn)
        chat = readers.rewards(readers.monthly_runs(conn))
    assert eff is not None
    assert eff.rewards == eff.chat == chat
    assert eff.rewards is not eff.chat  # rebuilt with model_validate
    # Only what matching finds with no words: the chat's cycle sources, the
    # covered cycles, and every credit row to review.
    found = {f: getattr(eff.meta, f) for f in MATCH_META}
    assert eff.meta == ov.OverlayMeta(**found)
    assert set(eff.meta.cycle_sources.values()) == {"chat"}
    assert eff.meta.cycle_rows == {} and eff.meta.flags == [] and eff.meta.chat_only == []


def test_inputs_are_read_in_one_read_transaction(samples: Settings) -> None:
    with write_connection(samples.db_path) as w:
        add_reward_inputs(w)
    conn = connect(samples.db_path)
    seen: list[tuple[str, bool]] = []
    try:
        runs = readers.monthly_runs(conn)
        conn.set_trace_callback(lambda sql: seen.append((sql.strip(), conn.in_transaction)))
        eff = ov.effective_rewards(conn, runs)
        conn.set_trace_callback(None)
        assert eff is not None and not conn.in_transaction
    finally:
        conn.close()
    sql = [s for s, _ in seen]
    assert sql[0] == "BEGIN" and sql[-1] == "COMMIT"
    reads = [(s, tx) for s, tx in seen if s.startswith("SELECT")]
    n = len(ov.INPUT_TABLES)
    assert [s.split()[3] for s, _ in reads[:n]] == list(ov.INPUT_TABLES)
    # then the statement rows for matching, in the same transaction
    assert reads[n:] and all(any(m in s for m in MATCH_READS) for s, _ in reads[n:])
    assert all(tx for _, tx in reads)
    assert sql.count("BEGIN") == 1


@pytest.mark.parametrize(
    "site", [rw.credits_ending, rw.rewards_page, rw.checks, trips._names], ids=lambda f: f.__name__
)
def test_each_call_site_reads_runs_and_inputs_in_one_snapshot(samples: Settings, site) -> None:
    with write_connection(samples.db_path) as w:
        add_reward_inputs(w)
    conn = connect(samples.db_path)
    seen: list[str] = []
    try:
        conn.set_trace_callback(lambda sql: seen.append(sql.strip()))
        site(conn)
        conn.set_trace_callback(None)
        assert not conn.in_transaction
    finally:
        conn.close()
    assert seen.count("BEGIN") == seen.count("COMMIT") == 1
    begin, commit = seen.index("BEGIN"), seen.index("COMMIT")
    inside = seen[begin + 1 : commit]
    # the chat's rewards come from run_sections, read after BEGIN with the inputs
    assert sum("FROM run_sections" in s for s in inside) == 1
    assert "FROM run_sections" in inside[0]
    n = len(ov.INPUT_TABLES)
    assert [s.split()[3] for s in inside[1 : n + 1]] == list(ov.INPUT_TABLES)
    assert inside[n + 1 :] and all(any(m in s for m in MATCH_READS) for s in inside[n + 1 :])
    assert not any("FROM run_sections" in s or "reward_" in s for s in seen[commit:])


def test_inside_a_write_it_uses_the_callers_transaction(samples: Settings) -> None:
    conn = connect(samples.db_path)
    seen: list[str] = []
    try:
        with write_transaction(conn):
            add_reward_inputs(conn)
            conn.set_trace_callback(seen.append)
            inputs = ov.load_inputs(conn)
            conn.set_trace_callback(None)
            assert conn.in_transaction  # still the caller's, not committed
        assert len(inputs.edits) == 2 and inputs.edits[0]["benefit_key"] == "app-lounge-credit"
        assert len(inputs.balances) == len(inputs.terms) == len(inputs.marks) == 1
        assert len(inputs.programs) == len(inputs.decisions) == 1
    finally:
        conn.close()
    assert not any(s.strip() in ("BEGIN", "COMMIT", "ROLLBACK") for s in seen)


def test_a_failed_read_rolls_back_its_transaction(db: sqlite3.Connection) -> None:
    with pytest.raises(RuntimeError), ov.read_transaction(db):
        assert db.in_transaction
        raise RuntimeError("stop")
    assert not db.in_transaction


def _program(data: dict, key: str) -> dict:
    return next(p for p in data["programs"] if p["key"] == key)


def _benefit(data: dict, key: str) -> dict:
    return next(b for b in data["benefits"] if b["key"] == key)


def test_the_merge_sorts_every_list(samples: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    def backwards(chat: Rewards, inputs: ov.Inputs, meta: ov.OverlayMeta) -> dict:
        data = chat.model_dump(mode="json", exclude_none=True)
        for name in ov.LISTS:
            data[name].reverse()
        for b in data["benefits"]:
            for inner in ("cycles", "category_keys"):
                if inner in b:
                    b[inner].reverse()
        for p in data["programs"]:
            for inner in ("earned_on", "allocations"):
                if inner in p:
                    p[inner].reverse()
        # the samples hold one card and one category here, so add a second out of order
        _program(data, "harbor-miles")["earned_on"] = ["summit-card", "cobalt-card"]
        _benefit(data, "fuel-bonus")["category_keys"] = ["groceries", "fuel"]
        return data

    monkeypatch.setattr(ov, "_merge", backwards)
    with read_connection(samples.db_path) as conn:
        eff = _effective(conn)
    assert eff is not None and eff.meta.notices == []
    want = eff.chat.model_dump(mode="json", exclude_none=True)
    _program(want, "harbor-miles")["earned_on"] = ["cobalt-card", "summit-card"]
    _benefit(want, "fuel-bonus")["category_keys"] = ["fuel", "groceries"]
    assert eff.rewards == Rewards.model_validate(want)
    club = _program(eff.rewards.model_dump(mode="json"), "shared-vacation-club")
    assert [a["year"] for a in club["allocations"]] == [2026, 2027]


def _bad_balance(data: dict) -> None:
    data["programs"][0]["balance"] = "QX7B2K points"


def _strict_type(data: dict) -> None:
    data["programs"][0]["balance"] = 52_000  # a number, not a string: strict refuses it


def _duplicate_key(data: dict) -> None:
    data["programs"].append(dict(data["programs"][0], name="Harbor Miles QX7B2K"))


def _unknown_program(data: dict) -> None:
    data["redemptions"][0]["program_key"] = "no-such-program"


def _duplicate_in(name: str):
    def breaks(data: dict) -> None:
        data[name].append(dict(data[name][0]))

    breaks.__name__ = f"_duplicate_in_{name}"
    return breaks


def _duplicate_cycle(data: dict) -> None:
    dining = _benefit(data, "dining-credit")
    dining["cycles"].append(dict(dining["cycles"][0]))


def _duplicate_earned_on(data: dict) -> None:
    _program(data, "harbor-miles")["earned_on"] = ["cobalt-card", "cobalt-card"]


def _duplicate_category_key(data: dict) -> None:
    _benefit(data, "fuel-bonus")["category_keys"] = ["fuel", "fuel"]


def _duplicate_allocation_year(data: dict) -> None:
    club = _program(data, "shared-vacation-club")
    club["allocations"].append(dict(club["allocations"][0]))


def _unknown_benefit_program(data: dict) -> None:
    _benefit(data, "hotel-status")["program_key"] = "no-such-program"


# A malformed dict fails in the sort, before model_validate sees it.
def _item_without_key(data: dict) -> None:
    del _benefit(data, "dining-credit")["key"]


def _cycle_without_start(data: dict) -> None:
    del _benefit(data, "dining-credit")["cycles"][0]["start"]


def _cycle_with_null_start(data: dict) -> None:
    _benefit(data, "dining-credit")["cycles"][0]["start"] = None


@pytest.mark.parametrize(
    ("breaks", "error"),
    [
        (_bad_balance, "ValidationError"),
        (_strict_type, "ValidationError"),
        (_duplicate_key, "OverlayError"),
        *[(_duplicate_in(name), "OverlayError") for name in ov.LISTS[1:]],
        (_unknown_program, "OverlayError"),
        (_duplicate_cycle, "OverlayError"),
        (_duplicate_earned_on, "OverlayError"),
        (_duplicate_category_key, "OverlayError"),
        (_duplicate_allocation_year, "OverlayError"),
        (_unknown_benefit_program, "OverlayError"),
        (_item_without_key, "KeyError"),
        (_cycle_without_start, "KeyError"),
        (_cycle_with_null_start, "TypeError"),
    ],
)
def test_a_failed_rebuild_shows_the_chats_view_with_a_notice(
    samples_v13: Settings, monkeypatch: pytest.MonkeyPatch, caplog, breaks, error: str
) -> None:
    def broken(chat: Rewards, inputs: ov.Inputs, meta: ov.OverlayMeta) -> dict:
        data = chat.model_dump(mode="json", exclude_none=True)
        breaks(data)
        meta.notices.append(ov.Notice("kept", "Never shown"))
        return data

    monkeypatch.setattr(ov, "_merge", broken)
    caplog.set_level(logging.WARNING, logger="app.rewards")
    conn = connect(samples_v13.db_path)
    try:
        eff = _effective(conn)
        page = rw.rewards_page(conn)
    finally:
        conn.close()
    assert eff is not None and eff.rewards is eff.chat
    assert eff.meta == ov.OverlayMeta(
        notices=[ov.Notice("overlay_failed", "Your edits could not be shown. Nothing was lost.")]
    )
    assert digest(r1(page)) == R1["2026-10-20"]["page"]
    records = [r for r in caplog.records if r.name == "app.rewards"]
    assert records and {r.getMessage() for r in records} == {"rewards overlay failed"}
    assert {r.error for r in records} == {error}
    lines = "\n".join(JsonFormatter().format(r) for r in records)
    for value in ("QX7B2K", str(52_000), "no-such-program", "Harbor Miles", "dining-credit"):
        assert value not in lines and value not in caplog.text


# --------------------------------------------------------------------------
# No-op golden: the R1 output, captured on the samples at 3129a75 (R1)
# --------------------------------------------------------------------------

# sha256 of the canonical JSON (sorted keys, no spaces) of each R1 body. A later
# chunk that adds fields takes them out before hashing. A sample regeneration
# (chunk D1) recaptures these on the R1 code. The gap audit (A10) gave Lantern
# Hotels best uses, avoid and a points expiry in the samples: the page digests
# were recaptured, and with those three fields set back to empty they give the
# old R1 digests, so nothing else moved. Format 1.4 (E-a) gave the monthly sample a done
# redemption and a maybe trip item, which move the page and trips digests: these tests
# run on the samples_v13 fixture (the monthly run in its 1.3 form, the 1.3 sample byte
# for byte), so the digests stay as they are.
R1 = {
    "2026-10-20": {
        "page": "e6ea7600c9ff3936018f6224cd40da1c1aca6a129920a82acb723c75857340da",
        "checks": "75f14a3142d50c7ee6a7526325c664ce6aeeb7fbc212ccaa1c939cc1cbbf756d",
        "trips": "33bf9a3dea849eb30ab98931dae6ae6223115bd540da1249f4dee2c530059a7a",
        "line": {"count": 1, "left_cents": 1_000, "first_end": "2026-10-31"},
    },
    "2026-12-20": {
        "page": "ba0cf18492b187de2a7b0e2dff9c9b5a229510343e46e0f0fcddc295528379ad",
        "checks": "75f14a3142d50c7ee6a7526325c664ce6aeeb7fbc212ccaa1c939cc1cbbf756d",
        "trips": "7493a9f4b2fdbf1f37f89e66be937bb18f545c1c679f4e7e068eb619cd66c530",
        "line": {"count": 4, "left_cents": 18_500, "first_end": "2026-12-31"},
    },
}
R1_PROGRAM_NAMES = {
    "harbor-miles": "Harbor Miles",
    "lantern-hotels": "Lantern Hotels",
    "pine-cash-back": "Pine Cash Back",
    "shared-vacation-club": "Shared Vacation Club",
    "summit-points": "Summit Points",
}


@pytest.mark.parametrize("day", sorted(R1))
def test_r1_output_is_unchanged_with_empty_tables(
    samples_v13: Settings, admin: TestClient, monkeypatch: pytest.MonkeyPatch, day: str
) -> None:
    on(monkeypatch, date.fromisoformat(day))
    want = R1[day]
    page = admin.get("/api/rewards")
    assert page.status_code == 200 and digest(r1(page.json())) == want["page"]
    assert digest(r1_checks(admin.get("/api/checks").json()["rewards"])) == want["checks"]
    assert admin.get("/api/dashboard").json()["credits_ending"] == want["line"]
    trips_body = older(admin.get("/api/trips").json(), E14_FIELDS.get("/api/trips", {}))
    assert digest(trips_body) == want["trips"]
    with read_connection(samples_v13.db_path) as conn:
        assert trips._names(conn).programs == R1_PROGRAM_NAMES
        assert counts(conn) == dict.fromkeys(REWARD_TABLES, 0)
        # the services give what the API gives
        assert digest(r1(rw.rewards_page(conn))) == want["page"]
        assert digest(r1_checks(rw.checks(conn))) == want["checks"]
        assert dashboard(conn)["credits_ending"] == want["line"]


# --------------------------------------------------------------------------
# The four call sites read the effective view
# --------------------------------------------------------------------------


def _changed(real):
    """effective_rewards with Harbor Miles renamed and stale, and dining taken out."""

    def fake(conn: sqlite3.Connection, runs: list[readers.StateRun]) -> ov.Effective | None:
        eff = real(conn, runs)
        if eff is None:
            return None
        data = eff.rewards.model_dump(mode="json", exclude_none=True)
        harbor = next(p for p in data["programs"] if p["key"] == "harbor-miles")
        harbor |= {"name": "Harbor Miles Plus", "as_of": "2026-05-01"}
        data["benefits"] = [b for b in data["benefits"] if b["key"] != "dining-credit"]
        return ov.Effective(Rewards.model_validate(data), eff.chat, eff.meta)

    return fake


def test_every_call_site_reads_the_effective_view(
    samples: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rw, "effective_rewards", _changed(ov.effective_rewards))
    monkeypatch.setattr(trips, "effective_rewards", _changed(ov.effective_rewards))
    conn = connect(samples.db_path)
    try:
        page = rw.rewards_page(conn)
        assert "dining-credit" not in {b["key"] for b in page["benefits"]}
        assert {p["key"]: p["name"] for p in page["programs"]}["harbor-miles"] == (
            "Harbor Miles Plus"
        )
        fee_credits = {c["key"] for row in page["fees"]["rows"] for c in row["credits"]}
        assert "dining-credit" not in fee_credits and "travel-credit" in fee_credits
        assert rw.credits_ending(conn) is None  # dining was the one
        stale = {s["key"] for s in rw.checks(conn)["stale"]}
        assert {"harbor-miles", "lantern-hotels"} <= stale
        assert trips._names(conn).programs["harbor-miles"] == "Harbor Miles Plus"
        points = trips.trips_page(conn)["tiles"]["points"]
        assert "Harbor Miles Plus" in {p["program_name"] for p in points}
    finally:
        conn.close()


def test_trip_redemptions_stay_on_the_chats_rewards(
    samples: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(conn, runs):
        raise AssertionError("unknown_trips reads the chat's redemptions")

    monkeypatch.setattr(trips, "effective_rewards", refuse)
    with read_connection(samples.db_path) as conn:
        trips.unknown_trips(conn)


# --------------------------------------------------------------------------
# Reapply, re-import and the fingerprint
# --------------------------------------------------------------------------


def test_reapply_and_reimport_keep_every_input(settings: Settings, capsys) -> None:
    migrate(settings=settings)
    for run_id in SAMPLE_RUNS[:-1]:
        assert import_folder(SAMPLES / run_id, settings=settings).status == IMPORTED
    with write_connection(settings.db_path) as w:
        add_reward_inputs(w)
        before = dump(w)
    assert all(before.values())
    # a new run goes through apply and touches no input
    result = import_folder(SAMPLES / SAMPLE_RUNS[-1], settings=settings)
    assert result.status == IMPORTED
    assert not any(t.startswith("reward_") for t in result.counts["tables"])
    with read_connection(settings.db_path) as conn:
        assert dump(conn) == before
        live = fingerprint(conn)
    # the same run again changes nothing
    again = import_folder(SAMPLES / SAMPLE_RUNS[-1], settings=settings)
    assert again.status == UNCHANGED and again.changes == 0
    assert reapply(settings) == {"runs": len(SAMPLE_RUNS), "changes": 0, "tables": {}}
    assert cli.main(["reapply"]) == 0
    assert capsys.readouterr().out.strip().endswith(live)
    with read_connection(settings.db_path) as conn:
        assert dump(conn) == before
        assert fingerprint(conn) == live


def test_the_fingerprint_covers_every_input(samples: Settings) -> None:
    with read_connection(samples.db_path) as conn:
        empty = fingerprint(conn)
    with write_connection(samples.db_path) as w:
        add_reward_inputs(w)
    with read_connection(samples.db_path) as conn:
        full = fingerprint(conn)
        state = fp_mod.imported_state(conn)
    assert full != empty
    for t in REWARD_TABLES:
        assert state[t], t
    # bookkeeping columns stay out of it
    with write_connection(samples.db_path) as w:
        for t in REWARD_TABLES:
            w.execute(f"UPDATE {t} SET updated_at = '2026-10-21T08:00:00+00:00'")
    with read_connection(samples.db_path) as conn:
        assert fingerprint(conn) == full
    # losing the rows of any one table shows
    for t in REWARD_TABLES:
        conn = connect(samples.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(f"DELETE FROM {t}")
            assert fingerprint(conn) != full, t
            conn.rollback()
        finally:
            conn.close()


# --------------------------------------------------------------------------
# The AI's reach
# --------------------------------------------------------------------------


@pytest.mark.parametrize("table", sorted(REWARD_TABLES))
def test_the_ai_connection_cannot_read_the_inputs(samples: Settings, table: str) -> None:
    with write_connection(samples.db_path) as w:
        add_reward_inputs(w)
    with ai_connection(samples.db_path) as conn:
        assert table in conn.blocked
        with pytest.raises(AuthorizerDenied):
            run(conn, f"SELECT * FROM {table}")
        with pytest.raises(sqlite3.DatabaseError):  # the authorizer alone stops it too
            conn._conn.execute(f"SELECT * FROM {table}").fetchall()


def test_reward_inputs_leave_the_ai_facts_signature_alone(samples: Settings) -> None:
    conn = connect(samples.db_path)
    try:
        before = signature(conn)
        with write_transaction(conn):
            add_reward_inputs(conn)
        assert signature(conn) == before
    finally:
        conn.close()
