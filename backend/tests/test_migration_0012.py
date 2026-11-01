"""Migration 0012 and the format 1.4 columns (docs/plans/format-14.md sections 4 and 8):
accounts.original_cents and rate_changes_json, trip_items.due_on and maybe.

The importer fills them, a registry field the run leaves out keeps its value, `[]`
clears the schedule, `cli reapply` refills them, and 0012 upgrades a populated
version 11 database: the pre-migrate copy is written, every table stays STRICT, and
reapply twice gives the same fingerprint. Fake samples only, with the monthly run
stripped to 1.3 and these four fields put back with known values.
"""

from __future__ import annotations

import copy
import json
import re
import sqlite3
from pathlib import Path

import pytest
from bad_cases import (
    MONTHLY,
    _v13,
    read_json,
    refresh_manifest,
    strip_newer,
    v14_run,
    write_json,
)
from conftest import SAMPLE_RUNS
from test_rewards_inputs_foundation import add_reward_inputs

from app import cli
from app.config import Settings
from app.db import migrate as migrate_mod
from app.db.connect import connect, read_connection, write_connection
from app.db.migrate import list_migrations, migrate, user_version
from app.importer.runner import IMPORTED, import_folder, reapply
from app.services import fingerprint as fingerprint_mod
from app.services import trips as tr
from app.services.fingerprint import fingerprint

MIGRATION = Path(migrate_mod.MIGRATIONS_DIR) / "0012_format_14.sql"
NEW_COLUMNS = {
    ("accounts", "original_cents"): "INTEGER",
    ("accounts", "rate_changes_json"): "TEXT",
    ("trip_items", "due_on"): "TEXT",
    ("trip_items", "maybe"): "INTEGER",
}
SCHEDULE = [{"apr": "23.99", "effective_on": "2026-11-01"}]
SCHEDULE_JSON = '[{"apr":"23.99","effective_on":"2026-11-01"}]'
SNORKEL = {
    "key": "snorkel-tour",
    "kind": "activities",
    "label": "Snorkel tour",
    "status": "estimate",
    "tender": "cash",
    "estimate_low": "150.00",
    "estimate_high": "250.00",
    "maybe": True,
}
# What that 1.4 monthly run stores in the four columns. Nothing else is set.
ACCOUNT_TERMS = {
    "pinecrest-mortgage": (800_000, None),
    "summit-card": (None, SCHEDULE_JSON),
    "van-loan": (900_000, None),
}
ITEM_TERMS = {
    ("beach-week", "snorkel-tour"): (None, 1),
    ("ski-weekend", "rental-car"): ("2027-01-15", None),
}


def _by_key(items: list[dict]) -> dict[str, dict]:
    return {i["key"]: i for i in items}


def _add_14_fields(snap: dict) -> None:
    """The four 1.4 fields on the monthly sample (orchard-card sends [] for none)."""
    accounts = _by_key(snap["accounts"])
    accounts["van-loan"]["original"] = "9000.00"
    accounts["pinecrest-mortgage"]["original"] = "8000.00"
    accounts["summit-card"]["rate_changes"] = copy.deepcopy(SCHEDULE)
    accounts["orchard-card"]["rate_changes"] = []
    trips = _by_key([t for t in snap["trips"] if not t.get("deleted")])
    _by_key(trips["ski-weekend"]["items"])["rental-car"]["due_on"] = "2027-01-15"
    beach = trips["beach-week"]
    items = [i for i in beach["items"] if i["key"] != SNORKEL["key"]] + [dict(SNORKEL)]
    beach["items"] = sorted(items, key=lambda i: i["key"])


def _v14_data(tmp: Path) -> Path:
    """The samples, with the monthly run as a 1.4 run that carries the four fields and
    no other 1.4 part."""

    def edit(snap: dict) -> None:
        strip_newer(snap, "1.3")
        _add_14_fields(snap)

    return v14_run(edit)(tmp).parent


def _import_all(settings: Settings, data: Path) -> None:
    migrate(settings=settings)
    results = [import_folder(data / run, settings=settings) for run in SAMPLE_RUNS]
    assert [r.status for r in results] == [IMPORTED] * len(SAMPLE_RUNS), [
        (r.run_id, [(i.rule, i.where) for i in r.issues]) for r in results
    ]


@pytest.fixture
def v14(settings: Settings, tmp_path: Path) -> Path:
    """The samples imported with that 1.4 monthly run. Returns the run folders."""
    data = _v14_data(tmp_path)
    _import_all(settings, data)
    return data


def _terms(settings: Settings) -> tuple[dict, dict]:
    """The four columns where set: accounts by key, trip items by (trip, item)."""
    with read_connection(settings.db_path) as conn:
        accounts = {
            r[0]: (r[1], r[2])
            for r in conn.execute(
                """SELECT account_key, original_cents, rate_changes_json FROM accounts
                   WHERE original_cents IS NOT NULL OR rate_changes_json IS NOT NULL"""
            )
        }
        items = {
            (r[0], r[1]): (r[2], r[3])
            for r in conn.execute(
                """SELECT trip_key, item_key, due_on, maybe FROM trip_items
                   WHERE due_on IS NOT NULL OR maybe IS NOT NULL"""
            )
        }
    return accounts, items


def _fingerprint(settings: Settings) -> str:
    with read_connection(settings.db_path) as conn:
        return fingerprint(conn)


def _run_id(settings: Settings, table: str, key_col: str, key: str) -> str:
    with read_connection(settings.db_path) as conn:
        sql = f"SELECT run_id FROM {table} WHERE {key_col} = ?"
        return conn.execute(sql, (key,)).fetchone()[0]


def _later_run(data: Path, run_id: str, **sections: object) -> Path:
    """A small later 1.4 correction run that carries `sections`."""
    dst = data / run_id
    dst.mkdir()
    snap: dict = {"schema_version": "1.4", "run_id": run_id, "mode": "correction"}
    write_json(dst / "snapshot.json", snap | {"statements": []} | sections)
    manifest = {
        "schema_version": "1.4",
        "run_id": run_id,
        "mode": "correction",
        "created_at": f"{run_id}T18:00:00-05:00",
        "generator": {"skill": "make_samples", "version": "1"},
        "files": [{"path": "snapshot.json", "sha256": "0" * 64, "bytes": 0}],
    }
    write_json(dst / "manifest.json", manifest)
    refresh_manifest(dst)
    return dst


def _monthly_accounts(data: Path, *keys: str) -> list[dict]:
    accounts = _by_key(read_json(data / MONTHLY / "snapshot.json")["accounts"])
    return [copy.deepcopy(accounts[k]) for k in sorted(keys)]


def _import(settings: Settings, run: Path) -> None:
    result = import_folder(run, settings=settings)
    assert result.status == IMPORTED, [(i.rule, i.where) for i in result.issues]


# --------------------------------------------------------------------------
# The migration
# --------------------------------------------------------------------------


def test_migration_0012_adds_four_columns(db: sqlite3.Connection) -> None:
    assert list_migrations()[11].path == MIGRATION
    assert user_version(db) >= 12
    sql = MIGRATION.read_text()
    assert "\u2014" not in sql and not re.search(r"[0-9]{5,}", sql)
    code = "\n".join(line for line in sql.splitlines() if not line.startswith("--"))
    assert code.count("ALTER TABLE") == code.count("ADD COLUMN") == code.count("CHECK") == 4
    assert "CREATE" not in code and "DROP" not in code
    for (table, column), kind in NEW_COLUMNS.items():
        types = {r["name"]: r["type"] for r in db.execute(f"PRAGMA table_info({table})")}
        assert types[column] == kind
    for table in ("accounts", "trip_items"):
        row = db.execute("SELECT sql FROM sqlite_schema WHERE name = ?", (table,)).fetchone()
        assert row[0].rstrip().endswith("STRICT"), table
    # The AI view names its columns, so the AI never sees the new ones.
    safe = {r["name"] for r in db.execute("PRAGMA table_info(accounts_safe)")}
    assert safe and not safe & {"original_cents", "rate_changes_json"}


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE accounts SET original_cents = 0 WHERE account_key = 'van-loan'",
        "UPDATE accounts SET original_cents = -100 WHERE account_key = 'van-loan'",
        "UPDATE accounts SET rate_changes_json = 'not json' WHERE account_key = 'summit-card'",
        "UPDATE accounts SET rate_changes_json = '[{' WHERE account_key = 'orchard-card'",
        """UPDATE trip_items SET due_on = '2027-01-10'
           WHERE trip_key = 'ski-weekend' AND item_key = 'points-credit'""",  # posted
        """UPDATE trip_items SET due_on = '2027-01-10'
           WHERE trip_key = 'beach-week' AND item_key = 'flights'""",  # estimate
        # rental-car has due_on, so it cannot move off booked
        """UPDATE trip_items SET status = 'posted'
           WHERE trip_key = 'ski-weekend' AND item_key = 'rental-car'""",
        "UPDATE trip_items SET maybe = 0 WHERE item_key = 'snorkel-tour'",
        "UPDATE trip_items SET maybe = 2 WHERE item_key = 'snorkel-tour'",
        """UPDATE trip_items SET maybe = 1
           WHERE trip_key = 'ski-weekend' AND item_key = 'rental-car'""",  # booked
        """UPDATE trip_items SET maybe = 1
           WHERE trip_key = 'ski-weekend' AND item_key = 'points-credit'""",  # posted
        # snorkel-tour is a maybe item, so it cannot become booked
        """UPDATE trip_items SET status = 'booked', amount_cents = 100, estimate_low_cents = NULL,
           estimate_high_cents = NULL WHERE item_key = 'snorkel-tour'""",
    ],
)
def test_0012_checks_refuse(v14: Path, settings: Settings, sql: str) -> None:
    with pytest.raises(sqlite3.IntegrityError), write_connection(settings.db_path) as w:
        w.execute(sql)
    assert _terms(settings) == (ACCOUNT_TERMS, ITEM_TERMS)  # rolled back


def test_0012_checks_allow_the_rest(v14: Path, settings: Settings) -> None:
    with write_connection(settings.db_path) as w:
        w.execute("UPDATE accounts SET original_cents = 1 WHERE account_key = 'family-car'")
        w.execute("UPDATE accounts SET rate_changes_json = '[]' WHERE account_key = 'orchard-card'")
        w.execute(
            """UPDATE trip_items SET maybe = 1
               WHERE trip_key = 'ski-weekend' AND item_key IN ('lift-tickets', 'parking')"""
        )
        w.execute("UPDATE trip_items SET maybe = NULL")
        w.execute("UPDATE trip_items SET due_on = NULL, status = 'posted' WHERE due_on IS NOT NULL")
    assert _terms(settings) == (
        {
            **ACCOUNT_TERMS,
            "family-car": (1, None),
            "orchard-card": (None, "[]"),
        },
        {},
    )


def _drop_new_columns(db_path: Path) -> None:
    """Make a migrated database a version 11 one: drop the four columns 0012 adds, and
    the tables 0013 and 0014 add."""
    raw = sqlite3.connect(db_path)
    try:
        with raw:
            raw.execute("DROP TABLE holding_classes")
            raw.execute("DROP TABLE budget_target_edits")
            for table, column in NEW_COLUMNS:
                raw.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        raw.execute("PRAGMA user_version = 11")
    finally:
        raw.close()


def test_0012_upgrades_a_populated_v11_database(settings: Settings, tmp_path: Path) -> None:
    """0012 on a copy of a 1.3 sample database (format-14.md section 8): the backup is
    written, every table stays STRICT, and reapply twice gives the same fingerprint."""
    data = _v13(lambda snap: None)(tmp_path).parent
    _import_all(settings, data)
    with write_connection(settings.db_path) as w:
        add_reward_inputs(w)  # app inputs: imports never write them, 0012 keeps them
    fresh = _fingerprint(settings)

    _drop_new_columns(settings.db_path)
    with read_connection(settings.db_path) as conn, pytest.MonkeyPatch.context() as mp:
        assert user_version(conn) == 11
        assert "due_on" not in {r["name"] for r in conn.execute("PRAGMA table_info(trip_items)")}
        # A version 11 database has no budget_target_edits (0014) to fingerprint.
        mp.delitem(fingerprint_mod.TABLES, "budget_target_edits")
        assert fingerprint(conn) != fresh  # the fingerprint changes once, at the upgrade

    assert migrate(settings=settings) == len(list_migrations()) >= 12
    backup = settings.backup_dir / "pre-migrate-0012.db"
    assert backup.exists() and backup.stat().st_mode & 0o777 == 0o600
    old = sqlite3.connect(backup)
    try:
        assert user_version(old) == 11
        assert "original_cents" not in {r[1] for r in old.execute("PRAGMA table_info(accounts)")}
    finally:
        old.close()

    with read_connection(settings.db_path) as conn:
        not_strict = conn.execute(
            """SELECT name FROM sqlite_schema WHERE type = 'table'
               AND name NOT LIKE 'sqlite_%' AND sql NOT LIKE '%STRICT'"""
        ).fetchall()
        assert not_strict == []
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        for table, column in NEW_COLUMNS:
            sql = f"SELECT count(*) FROM {table} WHERE {column} IS NOT NULL"
            assert conn.execute(sql).fetchone()[0] == 0
        assert fingerprint(conn) == fresh
    for _ in range(2):
        assert reapply(settings=settings)["changes"] == 0
        assert _fingerprint(settings) == fresh


# --------------------------------------------------------------------------
# The importer
# --------------------------------------------------------------------------


def test_import_fills_the_14_columns(v14: Path, settings: Settings) -> None:
    assert _terms(settings) == (ACCOUNT_TERMS, ITEM_TERMS)  # orchard-card's [] is NULL
    with read_connection(settings.db_path) as conn:
        row = conn.execute(
            "SELECT json FROM run_sections WHERE run_id = ? AND section = 'accounts'", (MONTHLY,)
        ).fetchone()
    # The run JSON keeps what the run sent, [] too.
    stored = _by_key(json.loads(row[0]))
    assert stored["orchard-card"]["rate_changes"] == []
    assert stored["summit-card"]["rate_changes"] == SCHEDULE
    assert "rate_changes" not in stored["harbor-savings"]


def test_a_left_out_debt_term_keeps_its_value_and_an_empty_list_clears(
    v14: Path, settings: Settings
) -> None:
    before = _fingerprint(settings)
    kept = _monthly_accounts(v14, "summit-card", "van-loan")
    for a in kept:
        a.pop("original", None)
        a.pop("rate_changes", None)
    _import(settings, _later_run(v14, "2026-10-16", accounts=kept))
    assert _terms(settings) == (ACCOUNT_TERMS, ITEM_TERMS)
    assert _run_id(settings, "accounts", "account_key", "summit-card") == MONTHLY
    assert _run_id(settings, "accounts", "account_key", "van-loan") == MONTHLY
    assert _fingerprint(settings) == before

    cleared = _monthly_accounts(v14, "summit-card", "van-loan")
    _by_key(cleared)["summit-card"]["rate_changes"] = []
    _by_key(cleared)["van-loan"]["original"] = None
    _import(settings, _later_run(v14, "2026-10-17", accounts=cleared))
    want = {k: v for k, v in ACCOUNT_TERMS.items() if k not in ("summit-card", "van-loan")}
    assert _terms(settings) == (want, ITEM_TERMS)
    assert _run_id(settings, "accounts", "account_key", "summit-card") == "2026-10-17"
    assert _run_id(settings, "accounts", "account_key", "van-loan") == "2026-10-17"
    assert _fingerprint(settings) != before


def test_a_term_change_moves_the_run_id_and_the_fingerprint(v14: Path, settings: Settings) -> None:
    """A trip whose only change is due_on is replaced. A schedule change updates the row."""
    before = _fingerprint(settings)
    monthly = read_json(v14 / MONTHLY / "snapshot.json")
    ski = next(t for t in monthly["trips"] if t["key"] == "ski-weekend")
    _by_key(ski["items"])["rental-car"]["due_on"] = "2027-01-16"
    _import(settings, _later_run(v14, "2026-10-16", trips=[ski]))
    assert _terms(settings)[1][("ski-weekend", "rental-car")] == ("2027-01-16", None)
    assert _run_id(settings, "trips", "trip_key", "ski-weekend") == "2026-10-16"
    assert _run_id(settings, "trips", "trip_key", "beach-week") == MONTHLY
    middle = _fingerprint(settings)
    assert middle != before

    summit = _monthly_accounts(v14, "summit-card")
    summit[0]["rate_changes"] = [
        {"apr": "22.49", "effective_on": "2026-11-01"},
        {"apr": "21.99", "effective_on": "2027-02-01"},
    ]
    _import(settings, _later_run(v14, "2026-10-17", accounts=summit))
    assert _terms(settings)[0]["summit-card"] == (
        None,
        '[{"apr":"22.49","effective_on":"2026-11-01"},{"apr":"21.99","effective_on":"2027-02-01"}]',
    )
    assert _run_id(settings, "accounts", "account_key", "summit-card") == "2026-10-17"
    assert _fingerprint(settings) not in (before, middle)


def test_reapply_refills_the_14_columns(v14: Path, settings: Settings) -> None:
    """cli reapply fills the columns from the stored runs (runs imported before 0012
    left them NULL), then a second reapply changes nothing."""
    terms, before = _terms(settings), _fingerprint(settings)
    with write_connection(settings.db_path) as w:
        w.execute("UPDATE accounts SET original_cents = NULL, rate_changes_json = NULL")
        w.execute("UPDATE trip_items SET due_on = NULL, maybe = NULL")
    assert _terms(settings) == ({}, {}) and _fingerprint(settings) != before
    assert cli.main(["reapply"]) == 0
    assert _terms(settings) == terms and _fingerprint(settings) == before
    for _ in range(2):
        assert reapply(settings=settings)["changes"] == 0
        assert _terms(settings) == terms and _fingerprint(settings) == before


# --------------------------------------------------------------------------
# Trip totals leave maybe items out
# --------------------------------------------------------------------------


def test_a_maybe_item_stays_out_of_the_trip_totals(v14: Path, settings: Settings) -> None:
    """The beach week's maybe snorkel tour (150 to 250) leaves its estimate as it was,
    and stays in the still-open list. Since E-b the items carry maybe and due_on."""
    conn = connect(settings.db_path)
    try:
        page = tr.trips_page(conn)
    finally:
        conn.close()
    beach = next(t for t in page["active"] if t["key"] == "beach-week")
    totals = beach["totals"]
    assert (totals["estimate_low_cents"], totals["estimate_high_cents"]) == (210_000, 310_000)
    assert totals["tbd_count"] == 0 and totals["cash_cents"] == 0
    assert [i["key"] for i in beach["items"]] == ["flights", "rental", "snorkel-tour"]
    assert {i["key"]: i["maybe"] for i in beach["items"]} == {
        "flights": False,
        "rental": False,
        "snorkel-tour": True,
    }
    assert all(i["due_on"] is None for i in beach["items"])
    still_open = [(i["trip_key"], i["key"]) for i in page["open_items"]["items"]]
    assert ("beach-week", "snorkel-tour") in still_open
