"""Class picks (migration 0013, Settings > Prices): an admin picks a holding's asset
class, in place of the one the statement gave or left out ("No class").

The pick regroups the holding (Allocation, Holdings by class, the account drawer,
Ask) and never moves a value or a price mode. It survives `cli reapply`. 0013 upgrades
a populated version 12 database with its pre-migrate copy, and an app at version 12
starts on the new database. Fake samples only.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import get_args

import pytest
from api_helpers import MEMBER, MEMBER_PW, PROD_ORIGIN, login
from fastapi.testclient import TestClient

from app.ai import facts
from app.api.market_models import ClassChoice
from app.config import Settings
from app.db import migrate as migrate_mod
from app.db.connect import connect, read_connection, write_connection, write_transaction
from app.db.migrate import list_migrations, migrate, user_version
from app.importer.runner import reapply
from app.market.classes import CLASS_CHOICES
from app.services import investments, networth
from app.services.fingerprint import fingerprint

MIGRATION = Path(migrate_mod.MIGRATIONS_DIR) / "0013_holding_classes.sql"
H = {"Origin": PROD_ORIGIN}
BROKER = "lakeview-brokerage"
SEP30 = "2026-09-30"
LOOSE = {"account_key": "kids-529", "holding_key": "loose-fund"}
LOOSE_CENTS = 10_000


@pytest.fixture
def conn(samples: Settings):
    c = connect(samples.db_path)
    yield c
    c.close()


def _add_loose_fund(conn: sqlite3.Connection) -> None:
    """A holding the chat sent with no class, in the 529's Sep 30 statement."""
    sep = conn.execute(
        """SELECT statement_key, run_id FROM statements
           WHERE account_key = 'kids-529' AND period_end = ? AND is_current = 1""",
        (SEP30,),
    ).fetchone()
    with write_transaction(conn):
        conn.execute(
            """INSERT INTO holdings (statement_key, account_key, as_of, holding_key,
                   holding_name, value_cents, run_id)
               VALUES (?, 'kids-529', ?, 'loose-fund', 'Loose Fund', ?, ?)""",
            (sep["statement_key"], SEP30, LOOSE_CENTS, sep["run_id"]),
        )


def _put(client: TestClient, body: dict):
    return client.put("/api/symbols/class", json=body, headers=H)


def _values(conn: sqlite3.Connection) -> dict[tuple[str, str], tuple]:
    return {
        (r["account_key"], r["holding_key"]): (r["value_cents"], r["mode"], r["estimated"])
        for r in investments.holding_rows(conn)
    }


def _classes(alloc: dict) -> dict[str, int]:
    return {c["key"]: c["value_cents"] for c in alloc["classes"]}


# --------------------------------------------------------------------------
# Migration 0013
# --------------------------------------------------------------------------


def test_migration_0013_adds_one_table(db: sqlite3.Connection) -> None:
    assert list_migrations()[12].path == MIGRATION
    assert user_version(db) == len(list_migrations()) >= 13
    sql = MIGRATION.read_text()
    assert "—" not in sql and not re.search(r"[0-9]{5,}", sql)
    code = "\n".join(line for line in sql.splitlines() if not line.startswith("--"))
    assert code.count("CREATE TABLE") == 1
    assert "ALTER" not in code and "DROP" not in code and "INSERT" not in code
    row = db.execute("SELECT sql FROM sqlite_schema WHERE name = 'holding_classes'").fetchone()
    assert row[0].rstrip().endswith("STRICT")
    # App-owned: no foreign key to the imported holdings, so reapply never drops a pick.
    refs = {r["table"] for r in db.execute("PRAGMA foreign_key_list(holding_classes)")}
    assert refs == {"users"}
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """INSERT INTO holding_classes (account_key, holding_key, asset_class, updated_at)
               VALUES ('a', 'b', '', 'now')"""
        )


def test_class_choices_match_the_api() -> None:
    assert get_args(ClassChoice) == CLASS_CHOICES


def test_0013_upgrades_a_populated_v12_database(samples: Settings) -> None:
    """The copy is written first, the table starts empty, the data is the same, and an
    app at version 12 (it only runs higher migrations) starts on the new database."""
    with read_connection(samples.db_path) as conn:
        before = fingerprint(conn)
    with write_connection(samples.db_path) as w:
        w.execute("DROP TABLE holding_classes")
        w.execute("DROP TABLE budget_target_edits")  # 0014 runs again too
        w.execute("PRAGMA user_version = 12")

    assert migrate(settings=samples) == len(list_migrations()) >= 13
    backup = samples.backup_dir / "pre-migrate-0013.db"
    assert backup.exists() and backup.stat().st_mode & 0o777 == 0o600
    old = sqlite3.connect(backup)
    try:
        assert user_version(old) == 12
        names = {r[0] for r in old.execute("SELECT name FROM sqlite_schema")}
        assert "holding_classes" not in names
    finally:
        old.close()
    with read_connection(samples.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM holding_classes").fetchone()[0] == 0
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert fingerprint(conn) == before
    # Nothing pending at the newest version: a second start changes nothing.
    assert migrate(settings=samples) == len(list_migrations())


# --------------------------------------------------------------------------
# Picks
# --------------------------------------------------------------------------


def test_a_pick_takes_a_holding_out_of_no_class(conn: sqlite3.Connection, admin: TestClient):
    _add_loose_fund(conn)
    alloc = admin.get("/api/investments/allocation").json()
    assert _classes(alloc)["unclassified"] == LOOSE_CENTS
    values = _values(conn)
    total = alloc["total_cents"]
    nw = admin.get("/api/dashboard").json()["net_worth"]["value_cents"]

    r = _put(admin, {**LOOSE, "asset_class": "bond"})
    assert r.status_code == 200, r.text
    row = r.json()
    assert (row["asset_class"], row["class_pick"]) == (None, "bond")
    assert row["mode"] == row["rule_mode"] == "statement"

    alloc = admin.get("/api/investments/allocation").json()
    classes = _classes(alloc)
    assert "unclassified" not in classes and classes["bond"] == LOOSE_CENTS
    assert alloc["total_cents"] == total
    by_class = admin.get("/api/investments/holdings?group_by=class").json()
    bonds = next(g for g in by_class["groups"] if g["key"] == "bond")
    assert [h["holding_key"] for h in bonds["holdings"]] == ["loose-fund"]
    drawer = admin.get("/api/accounts/kids-529").json()
    assert {h["key"]: h["asset_class"] for h in drawer["holdings"]}["loose-fund"] == "bond"
    # No value moves.
    assert _values(conn) == values
    assert admin.get("/api/dashboard").json()["net_worth"]["value_cents"] == nw

    # None goes back to the statement's class.
    r = _put(admin, {**LOOSE, "asset_class": None})
    assert r.status_code == 200 and r.json()["class_pick"] is None
    assert _classes(admin.get("/api/investments/allocation").json())["unclassified"] == LOOSE_CENTS
    events = conn.execute(
        "SELECT entity_key, detail_json FROM events WHERE entity = 'holding_classes' ORDER BY id"
    ).fetchall()
    assert [(e[0], json.loads(e[1])) for e in events] == [
        ("kids-529:loose-fund", {"asset_class": "bond"}),
        ("kids-529:loose-fund", {"asset_class": None}),
    ]


def test_a_pick_never_changes_the_price_mode(conn: sqlite3.Connection, admin: TestClient):
    """Cash is a constant class for the price rules. A cash pick on a priced fund
    regroups it and leaves it priced daily; the cash rules for its row follow the pick."""
    values = _values(conn)
    body = {"account_key": BROKER, "holding_key": "broad-market", "asset_class": "cash"}
    row = _put(admin, body).json()
    assert row["mode"] == row["rule_mode"] == "api" and row["symbol"] == "BRDMKT"
    assert _values(conn) == values
    stock = next(r for r in investments.holding_rows(conn) if r["holding_key"] == "broad-market")
    assert stock["asset_class"] == "cash" and stock["concentrated"] is False
    assert stock["gain_cents"] is None
    assert "us-stock" not in _classes(admin.get("/api/investments/allocation").json())


def test_a_pick_survives_reapply(samples: Settings, conn: sqlite3.Connection, admin: TestClient):
    body = {"account_key": BROKER, "holding_key": "broad-market", "asset_class": "intl-stock"}
    assert _put(admin, body).status_code == 200
    assert reapply(settings=samples)["changes"] == 0
    row = next(
        r
        for r in admin.get("/api/symbols").json()["rows"]
        if (r["account_key"], r["holding_key"]) == (BROKER, "broad-market")
    )
    assert (row["asset_class"], row["class_pick"]) == ("us-stock", "intl-stock")
    assert "intl-stock" in _classes(admin.get("/api/investments/allocation").json())


def test_ask_sees_the_pick(samples: Settings, conn: sqlite3.Connection, admin: TestClient):
    before = facts.signature(conn)
    body = {"account_key": BROKER, "holding_key": "broad-market", "asset_class": "balanced"}
    assert _put(admin, body).status_code == 200
    assert facts.signature(conn) != before
    held = [f for f in facts.build(conn, None) if f[0] == "holding"]
    assert any(v.get("asset_class") == "balanced" for _, _, v in held)


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({**LOOSE, "holding_key": "nope", "asset_class": "bond"}, 404),
        ({**LOOSE, "account_key": "nope", "asset_class": None}, 404),
        ({**LOOSE, "asset_class": "crypto"}, 422),
        ({**LOOSE, "asset_class": ""}, 422),
        ({**LOOSE, "asset_class": "bond", "note": "x"}, 422),
        ({"account_key": "kids-529", "asset_class": "bond"}, 422),
    ],
)
def test_bad_picks_are_refused(conn: sqlite3.Connection, admin: TestClient, body, status):
    _add_loose_fund(conn)
    assert _put(admin, body).status_code == status
    assert conn.execute("SELECT count(*) FROM holding_classes").fetchone()[0] == 0


def test_a_member_sees_picks_and_cannot_change_them(
    samples: Settings, conn: sqlite3.Connection, prod: TestClient
) -> None:
    _add_loose_fund(conn)
    assert login(prod, MEMBER, MEMBER_PW, PROD_ORIGIN).status_code == 200
    assert _put(prod, {**LOOSE, "asset_class": "bond"}).status_code == 403
    assert conn.execute("SELECT count(*) FROM holding_classes").fetchone()[0] == 0
    rows = prod.get("/api/symbols").json()["rows"]
    assert all("class_pick" in r for r in rows)


def test_account_detail_without_picks_keeps_the_statement_class(conn: sqlite3.Connection):
    d = networth.account_detail(conn, BROKER)
    assert {h["key"]: h["asset_class"] for h in d["holdings"]} == {
        "broad-market": "us-stock",
        "cash": "cash",
    }
