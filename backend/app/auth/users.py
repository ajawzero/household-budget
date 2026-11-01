"""Users: two in v1 (the owner admin, Sam member). Created from the CLI."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from app.auth import clock
from app.auth.passwords import hash_password

USERNAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
ROLES = ("admin", "member")


class UserError(ValueError):
    """A plain message safe to show. Never holds a password."""


@dataclass(frozen=True)
class User:
    id: int
    username: str
    display_name: str
    role: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def public(self) -> dict[str, str]:
        return {"username": self.username, "display_name": self.display_name, "role": self.role}


def normalize_username(name: str) -> str:
    return name.strip().lower()


def _user(row: sqlite3.Row) -> User:
    return User(row["id"], row["username"], row["display_name"], row["role"])


def find_user(conn: sqlite3.Connection, username: str) -> tuple[User, str] | None:
    """(user, password_hash) for an active user, else None."""
    row = conn.execute(
        "SELECT * FROM users WHERE username = ? AND active = 1", (normalize_username(username),)
    ).fetchone()
    return (_user(row), row["password_hash"]) if row else None


def get_user(conn: sqlite3.Connection, user_id: int) -> User | None:
    row = conn.execute("SELECT * FROM users WHERE id = ? AND active = 1", (user_id,)).fetchone()
    return _user(row) if row else None


def password_hash_of(conn: sqlite3.Connection, user_id: int) -> str | None:
    row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["password_hash"] if row else None


def create_user(
    conn: sqlite3.Connection, username: str, display_name: str, password: str, role: str
) -> User:
    """Insert a user. Call inside a write transaction."""
    username = normalize_username(username)
    display_name = display_name.strip()
    if not USERNAME_RE.match(username):
        raise UserError("Username must be 2 to 32 lowercase letters, digits, '-' or '_'.")
    if not display_name or len(display_name) > 64:
        raise UserError("Display name must be 1 to 64 characters.")
    if role not in ROLES:
        raise UserError("Role must be admin or member.")
    if conn.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
        raise UserError("That username already exists.")
    cur = conn.execute(
        """INSERT INTO users (username, display_name, password_hash, role, active, created_at)
           VALUES (?, ?, ?, ?, 1, ?)""",
        (username, display_name, hash_password(password), role, clock.iso(clock.now())),
    )
    return User(int(cur.lastrowid or 0), username, display_name, role)


def store_password_hash(conn: sqlite3.Connection, user_id: int, password_hash: str) -> None:
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
