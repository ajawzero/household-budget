"""After importing every good sample, no TEXT in the database looks sensitive (PLAN 11).

Every TEXT column of every table is scanned with detect_sensitive (the trip
tables with the trips-only rules too, like booking-code, and the reward_*
input tables with the rewards rules). Exempt:
hash-typed columns (checked by format instead), `cusip` (format) and `last4`
(format). `run_sections.json` holds validated JSON with money strings, so it is
scanned field by field through the format models, like the importer does.
`guard_json`, `tags_json`, `fields_json`, `base_json` and `rate_changes_json` are
scanned value by value, nested lists and objects too, except a credit guard's
hashes (row_sig, and near: txn_key -> row_sig), which are checked by format, like
txn_key. So is a credit choice's events.entity_key, a txn_key. Password hashes are
checked by format.
"""

from __future__ import annotations

import json
import re
import sqlite3

import pytest
from api_helpers import ADMIN, ADMIN_PW, PROD_ORIGIN, PROD_URL, add_users, app_for, login
from bad_cases import INJECTED_VALUES, PRIVATE_RULES_EXAMPLE, private_name, spaced_luhn
from conftest import SAMPLE_RUNS, SAMPLES
from fastapi.testclient import TestClient
from test_rewards_inputs_foundation import REWARD_TABLES, add_reward_inputs

from app import cli
from app.db.connect import read_connection, write_connection
from app.db.migrate import migrate
from app.importer.apply import load_stored_run
from app.importer.models import CUSIP_PATTERN, LAST4_PATTERN, SHA256_PATTERN
from app.importer.rescan import HASH_SETTING
from app.importer.runner import import_folder
from app.importer.sanitize import detect_sensitive, load_private_rules, scan_model
from app.services.rewards_inputs import GUARD_FIELDS, GUARD_HASHES

HASH_COLUMNS = {
    "txn_key",
    "token_hash",
    "sha256",
    "manifest_sha256",
    "source_sha256",
    "content_sha256",
    "prev_txn_key",
    "created_from_txn_key",
    "paycheck_key",
    "contribution_key",
}
# JSON columns: text values are scanned one by one, numbers and dates by format.
JSON_COLUMNS = {"guard_json", "tags_json", "fields_json", "base_json", "rate_changes_json"}
FORMAT_COLUMNS = {
    "cusip": CUSIP_PATTERN,
    "last4": LAST4_PATTERN,
    "password_hash": r"\$argon2id\$\S+",
}


def _text_columns(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    out = []
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for t in tables:
        for col in conn.execute(f"PRAGMA table_info({t})"):
            if col[2].upper() == "TEXT":
                out.append((t, col[1]))
    return out


@pytest.fixture
def imported(settings):
    migrate(settings=settings)
    for run in SAMPLE_RUNS:
        assert import_folder(SAMPLES / run, settings=settings).status == "imported"
    return settings


def _json_texts(value: str) -> list[str]:
    """Text values of a JSON column, nested ones too. Numbers and ISO dates are
    checked by format."""
    out: list[str] = []

    def walk(v: object) -> None:
        if isinstance(v, dict):
            for item in v.values():
                walk(item)
        elif isinstance(v, list):
            for item in v:
                walk(item)
        elif isinstance(v, str):
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", v):
                out.append(v)
        else:
            assert v is None or isinstance(v, int)  # bool is an int

    walk(json.loads(value))
    return out


def _guard_texts(value: str) -> list[str]:
    """A credit guard's text values to scan. row_sig and near hold sha256 hex
    only (plan section 2): checked by format and left out of the scan, where
    their digit runs would read as numbers."""
    guard = json.loads(value)
    near = guard.pop("near", {})
    hashes = [guard.pop("row_sig", None)] if "row_sig" in guard else []
    if isinstance(near, dict):
        hashes += [*near, *near.values()]
    ok = isinstance(near, dict) and all(
        isinstance(h, str) and re.fullmatch(SHA256_PATTERN, h) for h in hashes
    )
    assert ok, ("reward_credit_decisions", "guard_json")
    return _json_texts(json.dumps(guard))


def _section(table: str) -> str | None:
    """The snapshot section whose sectioned rules (booking-code) apply to a table."""
    if table.startswith("trip"):
        return "trips"
    return "rewards" if table.startswith("reward_") else None


def assert_text_columns_clean(private) -> None:
    hits = []
    with read_connection() as conn:
        columns = _text_columns(conn)
        assert ("transactions", "description") in columns
        for table, col in columns:
            if (table, col) == ("run_sections", "json"):
                continue
            values = [
                r[0] for r in conn.execute(f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL")
            ]
            if col in HASH_COLUMNS:
                assert all(re.fullmatch(SHA256_PATTERN, v) for v in values), (table, col)
                continue
            if col in FORMAT_COLUMNS:
                assert all(re.fullmatch(FORMAT_COLUMNS[col], v) for v in values), (table, col)
                continue
            if (table, col) == ("events", "entity_key"):  # a credit choice's is a txn_key
                rows = conn.execute(
                    """SELECT kind = 'rewards_edit' AND entity = 'credit', entity_key
                       FROM events WHERE entity_key IS NOT NULL"""
                ).fetchall()
                assert all(re.fullmatch(SHA256_PATTERN, k) for c, k in rows if c), (table, col)
                values = [k for c, k in rows if not c]
            if (table, col) == ("settings", "value_json"):  # the private rules file's hash
                rows = conn.execute("SELECT key = ?, value_json FROM settings", (HASH_SETTING,))
                rows = rows.fetchall()
                hashes = [json.loads(v)["sha256"] for c, v in rows if c]
                assert all(re.fullmatch(SHA256_PATTERN, h) for h in hashes), (table, col)
                values = [v for c, v in rows if not c]
            if (table, col) == ("reward_credit_decisions", "guard_json"):
                values = [t for v in values for t in _guard_texts(v)]
            elif col in JSON_COLUMNS:
                values = [t for v in values for t in _json_texts(v)]
            section = _section(table)
            for v in values:
                if detect_sensitive(v, section=section) or private.check(v):
                    hits.append((table, col))
        assert not hits, sorted(set(hits))


def test_every_text_column_is_clean(imported) -> None:
    private = load_private_rules(PRIVATE_RULES_EXAMPLE)
    with read_connection() as conn:  # the trip tables hold sample text to scan
        for table in ("trips", "trip_people", "trip_items", "trip_ious"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] > 0, table
        # The 1.4 sample fills the new TEXT columns, so the scan below sees them.
        for table, col in (("accounts", "rate_changes_json"), ("trip_items", "due_on")):
            sql = f"SELECT count(*) FROM {table} WHERE {col} IS NOT NULL"
            assert conn.execute(sql).fetchone()[0] > 0, (table, col)
    assert_text_columns_clean(private)
    with read_connection() as conn:
        # run_sections: field-aware scan of every stored run
        for (run_id,) in conn.execute("SELECT run_id FROM import_runs"):
            run = load_stored_run(conn, run_id)
            assert scan_model(run.snapshot, "snapshot:", private) == []
            for rows in run.rows.values():
                for row in rows:
                    assert scan_model(row, "row:", private) == []


def test_reward_inputs_are_scanned_with_the_rewards_rules(imported) -> None:
    """The reward_* tables (migration 0010): every TEXT column, the JSON ones value
    by value, with the rewards-only rules. txn_key is checked as a hash."""
    private = load_private_rules(PRIVATE_RULES_EXAMPLE)
    with write_connection() as w:
        add_reward_inputs(w)
    with read_connection() as conn:
        for table in REWARD_TABLES:
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] > 0, table
        columns = {(t, c) for t, c in _text_columns(conn) if t.startswith("reward_")}
    assert {
        ("reward_benefit_edits", "fields_json"),
        ("reward_benefit_edits", "base_json"),
        ("reward_credit_decisions", "guard_json"),
        ("reward_credit_decisions", "txn_key"),
    } <= columns
    assert_text_columns_clean(private)
    # The credit choice holds a guard as set_choice saves it: hashes in row_sig and near.
    with read_connection() as conn:
        [(raw,)] = conn.execute("SELECT guard_json FROM reward_credit_decisions").fetchall()
    guard = json.loads(raw)
    assert set(guard) == {*GUARD_FIELDS, *GUARD_HASHES} and guard["near"]
    for bad in ({"row_sig": "DINING CREDIT"}, {"near": {guard["row_sig"]: "Dining"}}):
        with write_connection() as w:
            w.execute(
                "UPDATE reward_credit_decisions SET guard_json = ?", (json.dumps(guard | bad),)
            )
        with pytest.raises(AssertionError) as hit:
            assert_text_columns_clean(private)
        assert "guard_json" in str(hit.value)
    with write_connection() as w:
        w.execute("UPDATE reward_credit_decisions SET guard_json = ?", (raw,))
    # A booking code passes the general rules but not the rewards ones.
    code = "Record locator QX7B2K"
    assert detect_sensitive(code) == [] and detect_sensitive(code, section="rewards") != []
    with write_connection() as w:
        w.execute("UPDATE reward_app_programs SET note = ?", (code,))
        nested = {"cycles": [{"start": "2026-01-01", "note": code}]}
        w.execute(
            "UPDATE reward_benefit_edits SET fields_json = ? WHERE origin = 'app'",
            (json.dumps(nested),),
        )
        w.execute("UPDATE reward_credit_decisions SET txn_key = ?", ("z" * 64,))  # not hex
    with pytest.raises(AssertionError) as hit:
        assert_text_columns_clean(private)
    assert "reward_credit_decisions" in str(hit.value)  # the hash format check
    with write_connection() as w:
        w.execute("DELETE FROM reward_credit_decisions")
    with pytest.raises(AssertionError) as hit:
        assert_text_columns_clean(private)
    assert "('reward_app_programs', 'note')" in str(hit.value)
    assert "('reward_benefit_edits', 'fields_json')" in str(hit.value)


def test_every_last4_is_three_or_four_digits(imported) -> None:
    with read_connection() as conn:
        values = [r[0] for r in conn.execute("SELECT last4 FROM accounts")]
    assert values and all(v is None or re.fullmatch(r"[0-9]{3,4}", v) for v in values)
    assert any(v is not None for v in values)


@pytest.mark.parametrize(
    "builder,value",
    [
        (spaced_luhn, "4111 1111 1111 1111"),
        (private_name, "EXAMPLE PRIVATE SERVICE"),
    ],
)
def test_rejected_values_appear_nowhere(
    use_private_rules, tmp_path, capsys, caplog, builder, value
) -> None:
    settings = use_private_rules
    migrate(settings=settings)
    for run in SAMPLE_RUNS[:2]:
        import_folder(SAMPLES / run, settings=settings)
    run_dir = builder(tmp_path)
    assert cli.main(["import", str(run_dir)]) == 1
    out = capsys.readouterr()
    conn = sqlite3.connect(settings.db_path)
    try:
        dump = "\n".join(conn.iterdump())
    finally:
        conn.close()
    digits = value.replace(" ", "")
    for text in (out.out, out.err, caplog.text, dump):
        assert value not in text
        assert digits not in text


def test_api_edits_leave_the_database_clean(use_private_rules, monkeypatch) -> None:
    """PLAN 11: after edits through the API, with bad values tried and refused, every
    TEXT column is still clean and the bad values appear nowhere."""
    settings = use_private_rules
    add_users(settings)
    for run in SAMPLE_RUNS:
        assert import_folder(SAMPLES / run, settings=settings).status == "imported"
    private = load_private_rules(PRIVATE_RULES_EXAMPLE)
    bad = [INJECTED_VALUES[2], INJECTED_VALUES[6], INJECTED_VALUES[8]]
    app = app_for(monkeypatch, dev=False)
    h = {"Origin": PROD_ORIGIN}
    with TestClient(app, base_url=PROD_URL) as c:
        assert login(c, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
        rows = c.post("/api/transactions/search", json={"limit": 3}, headers=h).json()["rows"]
        big = c.post(
            "/api/transactions/search",
            json={"sort": "amount_desc", "limit": 1},
            headers=h,
        ).json()["rows"]
        keys = [r["txn_key"] for r in rows + big]
        for key in keys:
            ok = {"category_key": "gifts", "merchant": "Corner Shop", "notes": "Paid 12.50 back"}
            r = c.patch(f"/api/transactions/{key}", json={**ok, "tags": ["trip 2026"]}, headers=h)
            assert r.status_code == 200
            for value in bad:
                for body in ({"notes": value}, {"merchant": value}, {"tags": [value]}):
                    r = c.patch(f"/api/transactions/{key}", json=body, headers=h)
                    assert r.status_code == 422 and value not in r.text
        r = c.post(
            "/api/rules",
            json={
                "match_kind": "merchant_equals",
                "pattern": "Corner Shop",
                "category_key": "pets",
            },
            headers=h,
        )
        assert r.status_code == 201
        for value in bad:
            r = c.post(
                "/api/rules",
                json={
                    "match_kind": "description_contains",
                    "pattern": value,
                    "category_key": "pets",
                },
                headers=h,
            )
            assert r.status_code == 422 and value not in r.text
        # A credit choice: its guard holds hashes, and its event's entity_key is a txn_key.
        with read_connection() as conn:
            [(credit,)] = conn.execute(
                "SELECT txn_key FROM v_transactions WHERE description = 'STATEMENT CREDIT'"
            ).fetchall()
        body = {"decision": "not_benefit", "reason": "other"}
        assert c.put(f"/api/rewards/credits/{credit}", json=body, headers=h).status_code == 200
    assert_text_columns_clean(private)
    with read_connection() as conn:
        assert conn.execute("SELECT count(*) FROM reward_credit_decisions").fetchone()[0] == 1
        events = conn.execute("SELECT entity_key FROM events WHERE entity = 'credit'")
        assert [r[0] for r in events] == [credit]
        assert conn.execute("SELECT count(*) FROM transaction_overrides").fetchone()[0] == 4
        assert conn.execute("SELECT count(*) FROM transaction_events").fetchone()[0] > 4
        dump = "\n".join(conn.iterdump())
    for value in bad:
        assert value not in dump and value.replace(" ", "") not in dump
