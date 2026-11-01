"""Auth units (PLAN 5.1): argon2id, rehash on login, sessions, throttles, CLI users."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator

import pytest
from api_helpers import ADMIN, ADMIN_PW, MEMBER, MEMBER_PW, add_users
from argon2 import PasswordHasher

from app import cli
from app.auth import clock
from app.auth.login import OK, THROTTLED, WRONG, login
from app.auth.passwords import hash_password, needs_rehash, verify_password
from app.auth.sessions import create_session, lookup_session, token_hash
from app.auth.users import UserError, create_user, find_user
from app.config import Settings
from app.db.connect import connect, write_connection


@pytest.fixture
def users(settings: Settings) -> Iterator[Settings]:
    add_users(settings)
    yield settings


def test_hash_is_argon2id() -> None:
    h = hash_password("a-test-password")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "a-test-password")
    assert not verify_password(h, "another-password")
    assert not verify_password(None, "a-test-password")
    assert not verify_password("not-a-hash", "a-test-password")


def test_an_empty_hash_never_verifies() -> None:
    # Both fall back to the dummy hash; its own password must not get through.
    for stored in (None, ""):
        assert not verify_password(stored, "not-a-real-password")


def test_login_upgrades_an_old_hash(users: Settings) -> None:
    weak = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1).hash(ADMIN_PW)
    assert needs_rehash(weak)
    with write_connection(users.db_path) as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE username = ?", (weak, ADMIN))
    with connect(users.db_path) as conn:
        assert login(conn, ADMIN, ADMIN_PW).status == OK
        stored = find_user(conn, ADMIN)[1]
    assert stored != weak and not needs_rehash(stored)


def test_login_is_case_insensitive_on_username(users: Settings) -> None:
    with connect(users.db_path) as conn:
        assert login(conn, " Alex ", ADMIN_PW).status == OK


def test_inactive_user_cannot_log_in(users: Settings) -> None:
    with write_connection(users.db_path) as conn:
        conn.execute("UPDATE users SET active = 0 WHERE username = ?", (MEMBER,))
    with connect(users.db_path) as conn:
        assert login(conn, MEMBER, MEMBER_PW).status == WRONG


def test_session_token_stored_as_sha256(users: Settings) -> None:
    with connect(users.db_path) as conn:
        user = find_user(conn, ADMIN)[0]
        with write_connection(users.db_path) as w:
            token = create_session(w, user.id)
        row = conn.execute("SELECT token_hash FROM sessions").fetchone()
        assert row[0] == hashlib.sha256(token.encode()).hexdigest() == token_hash(token)
        assert token not in json.dumps([tuple(r) for r in conn.execute("SELECT * FROM sessions")])
        assert lookup_session(conn, token) == user
        assert lookup_session(conn, token + "x") is None
        assert lookup_session(conn, None) is None
        assert lookup_session(conn, "a" * 500) is None


def test_lockout_counts_only_that_user(users: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    now = clock.now()
    monkeypatch.setattr(clock, "now", lambda: now)
    with connect(users.db_path) as conn:
        for _ in range(5):
            assert login(conn, ADMIN, "bad-password-1").status == WRONG
        assert login(conn, ADMIN, ADMIN_PW).status == THROTTLED
        assert login(conn, MEMBER, MEMBER_PW).status == OK
        kinds = [r[0] for r in conn.execute("SELECT kind FROM events ORDER BY id")]
    assert kinds == ["login_failed"] * 5 + ["login"]


def test_create_user_rules(users: Settings) -> None:
    with write_connection(users.db_path) as conn:
        for name, display, role in (
            ("A", "x", "member"),
            ("ok-name", "", "member"),
            ("ok-name", "x", "owner"),
            (ADMIN, "x", "member"),
        ):
            with pytest.raises(UserError):
                create_user(conn, name, display, "a-test-password", role)


# --------------------------------------------------------------------------
# CLI: passwords come from getpass only
# --------------------------------------------------------------------------


def _getpass(monkeypatch: pytest.MonkeyPatch, *answers: str) -> list[str]:
    asked: list[str] = []
    queue = list(answers)

    def fake(prompt: str = "") -> str:
        asked.append(prompt)
        return queue.pop(0)

    monkeypatch.setattr(cli.getpass, "getpass", fake)
    return asked


def test_cli_create_user(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asked = _getpass(monkeypatch, "cli-test-pass-1", "cli-test-pass-1")
    assert cli.main(["create-user", "pat", "--display-name", "Pat Example", "--admin"]) == 0
    assert len(asked) == 2
    out = capsys.readouterr().out
    assert "cli-test-pass-1" not in out
    with connect(settings.db_path) as conn:
        user, h = find_user(conn, "pat")
        assert user.role == "admin" and user.display_name == "Pat Example"
        assert h.startswith("$argon2id$") and verify_password(h, "cli-test-pass-1")
        assert login(conn, "pat", "cli-test-pass-1").status == OK

    _getpass(monkeypatch, "member-pass-123", "member-pass-123")
    assert cli.main(["create-user", "kim", "--display-name", "Kim Example"]) == 0
    with connect(settings.db_path) as conn:
        assert find_user(conn, "kim")[0].role == "member"


def test_cli_create_user_rejects_mismatch_and_short(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _getpass(monkeypatch, "cli-test-pass-1", "cli-test-pass-2")
    assert cli.main(["create-user", "pat", "--display-name", "Pat"]) == 2
    _getpass(monkeypatch, "short")
    assert cli.main(["create-user", "pat", "--display-name", "Pat"]) == 2
    with connect(settings.db_path) as conn:
        assert find_user(conn, "pat") is None


def test_cli_never_takes_a_password_argument(settings: Settings) -> None:
    for argv in (
        ["create-user", "pat", "--display-name", "Pat", "--password", "x"],
        ["create-user", "pat", "--display-name", "Pat", "secret-pass"],
        ["set-password", "pat", "secret-pass"],
    ):
        with pytest.raises(SystemExit):
            cli.main(argv)


def test_cli_set_password_ends_sessions(
    users: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with connect(users.db_path) as conn:
        assert login(conn, MEMBER, MEMBER_PW).status == OK
        assert login(conn, MEMBER, MEMBER_PW).status == OK
        assert login(conn, ADMIN, ADMIN_PW).status == OK
    _getpass(monkeypatch, "fresh-pass-abcde", "fresh-pass-abcde")
    assert cli.main(["set-password", MEMBER]) == 0
    assert "2 sessions signed out" in capsys.readouterr().out
    with connect(users.db_path) as conn:
        left = conn.execute(
            "SELECT u.username FROM sessions s JOIN users u ON u.id = s.user_id"
        ).fetchall()
        assert [r[0] for r in left] == [ADMIN]
        assert login(conn, MEMBER, MEMBER_PW).status == WRONG
        assert login(conn, MEMBER, "fresh-pass-abcde").status == OK
    _getpass(monkeypatch)
    assert cli.main(["set-password", "nobody"]) == 2
