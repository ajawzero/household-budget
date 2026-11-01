"""DB layer: connections, pragmas, write lock, migrations, backups, seeds, views."""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from pathlib import Path

import pytest

from app.db import migrate as migrate_mod
from app.db.connect import WRITE_LOCK, connect, read_connection, write_transaction
from app.db.migrate import (
    backup_before_migrate,
    list_migrations,
    migrate,
    seed_categories,
    user_version,
)


def test_pragmas(db: sqlite3.Connection) -> None:
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert db.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL


def test_fresh_migrate_has_every_table_strict_and_no_backup(db, settings) -> None:
    assert user_version(db) == len(list_migrations())
    tables = {
        r["name"]: r["sql"]
        for r in db.execute("SELECT name, sql FROM sqlite_schema WHERE type = 'table'")
    }
    expected = {
        "users",
        "sessions",
        "settings",
        "events",
        "import_runs",
        "run_sections",
        "job_runs",
        "people",
        "accounts",
        "category_groups",
        "categories",
        "category_aliases",
        "statements",
        "transactions",
        "transaction_overrides",
        "balances",
        "holdings",
    }
    assert expected <= set(tables)
    for name in expected:
        assert tables[name].rstrip().endswith("STRICT"), name
    views = {r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type = 'view'")}
    assert {"v_transactions", "v_cashflow_rows"} <= views
    assert not settings.backup_dir.exists() or not list(settings.backup_dir.iterdir())


def test_categories_seeded_and_idempotent(db, settings) -> None:
    def counts() -> tuple[int, int]:
        return (
            db.execute("SELECT count(*) FROM categories").fetchone()[0],
            db.execute("SELECT count(*) FROM category_groups").fetchone()[0],
        )

    assert counts() == (53, 11)
    migrate(settings=settings)
    assert counts() == (53, 11)
    kinds = dict(db.execute("SELECT category_key, kind FROM categories"))
    assert kinds["paycheck"] == "income" and kinds["groceries"] == "spending"


def _pre_phase7_categories(new_file: Path, out: Path) -> Path:
    """categories.json as it was before Phase 7: no Transportation group, fuel in
    Lifestyle (104), vehicle registration in Insurance (603)."""
    data = json.loads(new_file.read_text(encoding="utf-8"))
    data["groups"] = [g for g in data["groups"] if g["key"] != "transportation"]
    old = {"fuel": ("lifestyle", 104), "vehicle-registration": ("insurance", 603)}
    cats = []
    for c in data["categories"]:
        if c["key"] in old:
            c["group"], c["sort"] = old[c["key"]]
        elif c["group"] == "transportation":
            continue
        cats.append(c)
    data["categories"] = cats
    out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return out


def test_seeding_moves_fuel_and_registration_into_transportation(settings, tmp_path) -> None:
    """Phase 7: a database seeded before the Transportation group gets it at the next
    start, with no migration. Keys stay, so every row keeps its category and moves
    group at read time."""
    old_file = _pre_phase7_categories(settings.categories_file, tmp_path / "old.json")
    migrate(categories_file=old_file, settings=settings)
    conn = connect(settings.db_path)
    try:
        assert conn.execute("SELECT count(*) FROM categories").fetchone()[0] == 49
        group = "SELECT group_key FROM categories WHERE category_key = ?"
        assert conn.execute(group, ("fuel",)).fetchone()[0] == "lifestyle"

        migrate(settings=settings)  # the next start, with the new file
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        rows = conn.execute(
            """SELECT c.category_key, c.name, c.emoji, c.sort, c.kind,
                      g.name, g.sort, g.counts_in_spending
               FROM categories c JOIN category_groups g USING (group_key)
               WHERE c.group_key = 'transportation' ORDER BY c.sort"""
        ).fetchall()
        t = ("Transportation", 25, 1)
        assert [tuple(r) for r in rows] == [
            ("fuel", "Fuel", "⛽", 251, "spending", *t),
            ("vehicle-maintenance", "Maintenance and repairs", "🛠️", 252, "spending", *t),
            ("parking", "Parking", "🅿️", 253, "spending", *t),
            ("tolls", "Tolls", "🛣️", 254, "spending", *t),
            ("rideshare-and-transit", "Rideshare and transit", "🚕", 255, "spending", *t),
            ("vehicle-registration", "Vehicle registration", "🪪", 256, "spending", *t),
        ]
        insurance = {
            r[0]
            for r in conn.execute(
                "SELECT category_key FROM categories WHERE group_key = 'insurance'"
            )
        }
        assert insurance == {"life-insurance", "auto-and-home-insurance"}
    finally:
        conn.close()


def test_history_follows_the_new_group(samples, db, settings) -> None:
    """Groups join at read time: fuel rows imported earlier show Transportation."""
    fuel = """SELECT DISTINCT c.group_key FROM v_transactions AS v
              JOIN categories AS c USING (category_key) WHERE v.category_key = 'fuel'"""
    assert [r[0] for r in db.execute(fuel)] == ["transportation"]
    db.execute("UPDATE categories SET group_key = 'lifestyle' WHERE category_key = 'fuel'")
    assert [r[0] for r in db.execute(fuel)] == ["lifestyle"]  # as before Phase 7
    seed_categories(db, settings.categories_file)
    assert [r[0] for r in db.execute(fuel)] == ["transportation"]


def test_last4_check(db) -> None:
    db.execute("INSERT INTO people VALUES ('pat', 'Pat', NULL, 'adult', 'r', 'r')")
    base = (
        "INSERT INTO accounts (account_key, display_name, institution, type, "
        "owner_person_key, ownership, account_group, is_asset, include_in_net_worth, "
        "in_cashflow, statement_cadence, last4, first_run_id, run_id) "
        "VALUES (?, 'A', 'Bank', 'checking', 'pat', 'individual', 'cash', 1, 1, 1, "
        "'monthly', ?, 'r', 'r')"
    )
    db.execute(base, ("ok-four", "1234"))
    db.execute(base, ("ok-three", "123"))
    db.execute(base, ("ok-none", None))
    for bad in ("12345", "12a4", "12", "１２３４"):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(base, ("bad-" + str(len(bad)), bad))


def test_strict_types_enforced(db) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO category_groups VALUES ('g', 'G', 'not a number', 1)")


def test_write_transaction_rolls_back_and_holds_lock(db) -> None:
    with pytest.raises(RuntimeError), write_transaction(db):
        db.execute("INSERT INTO settings VALUES ('a', '1', NULL, 't')")
        raise RuntimeError
    assert db.execute("SELECT count(*) FROM settings").fetchone()[0] == 0

    seen = []
    with write_transaction(db):
        t = threading.Thread(target=lambda: seen.append(WRITE_LOCK.acquire(blocking=False)))
        t.start()
        t.join()
        db.execute("INSERT INTO settings VALUES ('b', '1', NULL, 't')")
    assert seen == [False]
    with read_connection() as other:
        assert other.execute("SELECT count(*) FROM settings").fetchone()[0] == 1


def test_readonly_connection_cannot_write(db, settings) -> None:
    ro = connect(settings.db_path, readonly=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("INSERT INTO settings VALUES ('a', '1', NULL, 't')")
    finally:
        ro.close()


def _real_migrations_plus_one(tmp_path: Path) -> tuple[Path, int]:
    """A copy of the real migrations folder, and the next free version number."""
    mdir = tmp_path / "migrations"
    mdir.mkdir()
    real = migrate_mod.list_migrations()
    for m in real:
        shutil.copy(m.path, mdir)
    return mdir, real[-1].version + 1


def test_pre_migrate_backup(db, settings, tmp_path: Path, monkeypatch) -> None:
    db.execute("INSERT INTO settings VALUES ('kept', '1', NULL, 't')")
    mdir, nxt = _real_migrations_plus_one(tmp_path)
    (mdir / f"{nxt:04d}_extra.sql").write_text("CREATE TABLE extra (x INTEGER) STRICT;\n")
    monkeypatch.setattr(migrate_mod, "MIGRATIONS_DIR", mdir)
    assert migrate(settings=settings) == nxt
    copy = settings.backup_dir / f"pre-migrate-{nxt:04d}.db"
    assert copy.exists()
    old = sqlite3.connect(copy)
    try:
        assert old.execute("PRAGMA user_version").fetchone()[0] == nxt - 1
        assert old.execute("SELECT key FROM settings").fetchall() == [("kept",)]
    finally:
        old.close()
    assert migrate(settings=settings) == nxt  # nothing pending: no new copy
    assert len(list(settings.backup_dir.glob("pre-migrate-*.db"))) == 1


def test_pre_migrate_keeps_last_five(db, tmp_path: Path) -> None:
    for v in range(2, 9):
        backup_before_migrate(db, tmp_path / "bk", v)
    names = sorted(p.name for p in (tmp_path / "bk").iterdir())
    assert names == [f"pre-migrate-{v:04d}.db" for v in range(4, 9)]


def test_failed_migration_rolls_back(db, settings, tmp_path: Path, monkeypatch) -> None:
    mdir, nxt = _real_migrations_plus_one(tmp_path)
    (mdir / f"{nxt:04d}_bad.sql").write_text("CREATE TABLE half (x INTEGER) STRICT;\nNOT SQL;\n")
    monkeypatch.setattr(migrate_mod, "MIGRATIONS_DIR", mdir)
    with pytest.raises(sqlite3.Error):
        migrate(settings=settings)
    assert user_version(db) == nxt - 1
    assert db.execute("SELECT count(*) FROM sqlite_schema WHERE name = 'half'").fetchone()[0] == 0
