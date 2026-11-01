"""Run `migrations/NNNN_*.sql` in order, tracked by PRAGMA user_version (PLAN 2.2, 5.8).

Before any migration on a database that already holds data, a copy is made with
`VACUUM INTO <backup_dir>/pre-migrate-NNNN.db` (NNNN = the first migration to
run). The newest 5 copies are kept. A brand new, empty database is not copied.

After migrating, the category tables are seeded from `schema/categories.json`.
Seeding is idempotent: it inserts new groups and categories and updates names,
emoji and sort. It never deletes a category (keys are append-only).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings, get_settings
from app.db.connect import WRITE_LOCK, connect

log = logging.getLogger("app.db")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
KEEP_PRE_MIGRATE = 5
_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


@dataclass(frozen=True)
class Migration:
    version: int
    path: Path


def list_migrations(directory: Path | None = None) -> list[Migration]:
    found = []
    for p in (directory or MIGRATIONS_DIR).iterdir():
        m = _NAME.match(p.name)
        if m:
            found.append(Migration(int(m[1]), p))
    found.sort(key=lambda m: m.version)
    versions = [m.version for m in found]
    if versions != list(range(1, len(versions) + 1)):
        raise RuntimeError("Migration files must be numbered 0001, 0002, ... with no gaps.")
    return found


def user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _is_empty(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT count(*) FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'")
    return user_version(conn) == 0 and row.fetchone()[0] == 0


def backup_before_migrate(conn: sqlite3.Connection, backup_dir: Path, version: int) -> Path:
    """VACUUM INTO a pre-migrate copy, then keep only the newest KEEP_PRE_MIGRATE copies."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    target = backup_dir / f"pre-migrate-{version:04d}.db"
    if target.exists():
        target.unlink()
    conn.execute("VACUUM INTO ?", (str(target),))
    os.chmod(target, 0o600)
    copies = sorted(backup_dir.glob("pre-migrate-*.db"), key=lambda p: p.stat().st_mtime_ns)
    for old in copies[:-KEEP_PRE_MIGRATE]:
        old.unlink()
    return target


def _apply(conn: sqlite3.Connection, m: Migration) -> None:
    sql = m.path.read_text(encoding="utf-8")
    try:
        conn.executescript(f"BEGIN IMMEDIATE;\n{sql}\nPRAGMA user_version = {m.version};\nCOMMIT;")
    except sqlite3.Error:
        if conn.in_transaction:
            conn.rollback()
        raise


def seed_categories(conn: sqlite3.Connection, categories_file: Path) -> None:
    """Upsert category groups and categories from categories.json. Never deletes."""
    data = json.loads(categories_file.read_text(encoding="utf-8"))
    with WRITE_LOCK:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for g in data["groups"]:
                conn.execute(
                    """INSERT INTO category_groups (group_key, name, sort, counts_in_spending)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT (group_key) DO UPDATE SET
                         name = excluded.name, sort = excluded.sort,
                         counts_in_spending = excluded.counts_in_spending""",
                    (g["key"], g["name"], int(g["sort"]), int(bool(g["counts_in_spending"]))),
                )
            for c in data["categories"]:
                conn.execute(
                    """INSERT INTO categories (category_key, name, group_key, emoji, kind, sort)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT (category_key) DO UPDATE SET
                         name = excluded.name, group_key = excluded.group_key,
                         emoji = excluded.emoji, kind = excluded.kind, sort = excluded.sort""",
                    (c["key"], c["name"], c["group"], c.get("emoji"), c["kind"], int(c["sort"])),
                )
            for a in data.get("aliases", []):
                conn.execute(
                    "INSERT OR REPLACE INTO category_aliases (old_key, new_key) VALUES (?, ?)",
                    (a["old_key"], a["new_key"]),
                )
        except BaseException:
            conn.rollback()
            raise
        conn.commit()


def migrate(
    db_path: Path | None = None,
    backup_dir: Path | None = None,
    categories_file: Path | None = None,
    settings: Settings | None = None,
) -> int:
    """Bring the database up to the newest migration and seed categories.

    Returns the final user_version.
    """
    s = settings or get_settings()
    db_path = db_path or s.db_path
    backup_dir = backup_dir or s.backup_dir
    categories_file = categories_file or s.categories_file
    conn = connect(db_path)
    try:
        current = user_version(conn)
        pending = [m for m in list_migrations() if m.version > current]
        if pending:
            with WRITE_LOCK:
                if not _is_empty(conn):
                    backup_before_migrate(conn, backup_dir, pending[0].version)
                for m in pending:
                    _apply(conn, m)
                    log.info("migration applied", extra={"version": m.version})
        seed_categories(conn, categories_file)
        return user_version(conn)
    finally:
        conn.close()
