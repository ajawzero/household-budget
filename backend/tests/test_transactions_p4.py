"""Phase 4 transactions (PLAN 3.4, 4.11 step 6, 5.5, 5.6, 8.2): search, summary,
edits with guards, bulk edits, rules, the activity timeline, re-keying and orphans."""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from pathlib import Path

import pytest
from api_helpers import (
    ADMIN,
    ADMIN_PW,
    MEMBER,
    MEMBER_PW,
    PROD_ORIGIN,
    PROD_URL,
    SAMPLES,
    TODAY,
    add_users,
    app_for,
    login,
)
from bad_cases import (
    INJECTED_VALUES,
    _add_cents,
    edit_csv,
    edit_snapshot,
    read_json,
    refresh_manifest,
    write_json,
)
from conftest import SAMPLE_RUNS
from fastapi.testclient import TestClient
from test_logs import captured

from app.config import Settings
from app.db.connect import connect, write_connection
from app.db.migrate import migrate
from app.importer.runner import import_folder, reapply
from app.services.fingerprint import fingerprint
from app.services.overrides import reattach_overrides

H = {"Origin": PROD_ORIGIN}
MAPLE = "maple-joint-checking:2026-09-30"
MAPLE_CSV = "transactions/maple-joint-checking-2026-09-30.csv"
# Fake bad values from bad_cases: a Luhn card number, a split account number,
# a phone number, a digit run, a private name. Plus an email built here.
LUHN = INJECTED_VALUES[2]
PRIVATE = INJECTED_VALUES[8]
EMAIL = "me" + "@" + "example.com"
SENSITIVE = [LUHN, INJECTED_VALUES[1], INJECTED_VALUES[6], INJECTED_VALUES[0], EMAIL]


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def q(settings: Settings, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn = connect(settings.db_path)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def key_of(settings: Settings, description: str, statement_key: str = MAPLE) -> str:
    rows = q(
        settings,
        "SELECT txn_key FROM v_transactions WHERE description = ? AND statement_key = ?",
        (description, statement_key),
    )
    assert len(rows) == 1, (description, len(rows))
    return rows[0][0]


def search(client: TestClient, **body: object) -> dict:
    r = client.post("/api/transactions/search", json=body, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


def search_all(client: TestClient, **body: object) -> list[dict]:
    rows, cursor = [], None
    for _ in range(500):
        d = search(client, **body, cursor=cursor)
        rows += d["rows"]
        cursor = d["next_cursor"]
        if cursor is None:
            return rows
    raise AssertionError("cursor never ended")


def patch(client: TestClient, key: str, body: dict) -> object:
    return client.patch(f"/api/transactions/{key}", json=body, headers=H)


def events(client: TestClient, key: str) -> list[dict]:
    r = client.get(f"/api/transactions/{key}/events")
    assert r.status_code == 200, r.text
    return r.json()["events"]


def live(settings: Settings, where: str = "1 = 1", params: tuple = ()) -> list[sqlite3.Row]:
    return q(
        settings,
        f"""SELECT t.*, a.owner_person_key, c.group_key, c.kind AS category_kind
            FROM v_transactions t JOIN accounts a USING (account_key)
            LEFT JOIN categories c ON c.category_key = t.category_key
            WHERE {where} ORDER BY txn_date DESC, txn_key DESC""",
        params,
    )


def import_three(settings: Settings) -> None:
    migrate(settings=settings)
    for run in SAMPLE_RUNS[:3]:
        assert import_folder(SAMPLES / run, settings=settings, today=TODAY).status == "imported"


def fp(settings: Settings) -> str:
    conn = connect(settings.db_path)
    try:
        return fingerprint(conn)
    finally:
        conn.close()


@pytest.fixture
def three(settings: Settings, admin: TestClient) -> TestClient:
    """Admin client over the first three sample runs (the correction not yet in)."""
    import_three(settings)
    return admin


@pytest.fixture
def full(samples: Settings, admin: TestClient) -> TestClient:
    return admin


def drop_atm_correction(tmp_path: Path, run_id: str = "2026-10-16") -> Path:
    """A correction run that removes the ATM withdrawal from the Maple statement."""
    run = tmp_path / run_id
    shutil.copytree(SAMPLES / "2026-10-14", run)
    for name in ("snapshot.json", "manifest.json"):
        obj = read_json(run / name)
        obj["run_id"] = run_id
        write_json(run / name, obj)
    refresh_manifest(run)

    def drop(rows: list[dict]) -> None:
        rows[:] = [r for r in rows if r["description"] != "ATM WITHDRAWAL"]

    def fix(snap: dict) -> None:
        st = snap["statements"][0]
        st["closing"] = _add_cents(st["closing"], 10000)
        st["debits"] = _add_cents(st["debits"], 10000)
        st["txn_sum"] = _add_cents(st["txn_sum"], 10000)
        st["txn_count"] -= 1
        snap["balances"][0]["balance"] = st["closing"]

    edit_csv(run, MAPLE_CSV, drop)
    edit_snapshot(run, fix)
    return run


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------


ROW_FIELDS = {
    "txn_key",
    "txn_date",
    "post_date",
    "account_key",
    "account_name",
    "institution",
    "description",
    "merchant",
    "imported_merchant",
    "category_key",
    "category_name",
    "category_emoji",
    "category_group",
    "category_kind",
    "category_source",
    "rule_id",
    "imported_category_key",
    "cardholder",
    "cardholder_label",
    "owner",
    "owner_label",
    "amount_cents",
    "txn_type",
    "is_transfer",
    "is_reimbursable",
    "exclude_from_budget",
    "hidden",
    "needs_review",
    "pre_history",
    "plan_key",
    "recurring_key",
    "trip_key",
    "trip_name",
    "import_notes",
    "notes",
    "tags",
    "has_override",
    "overridden",
}


def test_search_payload_days_and_summary(samples: Settings, full: TestClient) -> None:
    d = search(full, limit=25)
    assert set(d) == {"rows", "next_cursor", "days", "summary"}
    assert len(d["rows"]) == 25 and d["next_cursor"]
    for r in d["rows"]:
        assert set(r) == ROW_FIELDS
        assert r["category_source"] == "imported" and r["overridden"] == []
    every = live(samples, "hidden = 0")
    dates = sorted({r["txn_date"] for r in d["rows"]})
    assert [x["date"] for x in d["days"]] == dates
    for day in d["days"]:
        same = [r for r in every if r["txn_date"] == day["date"]]
        assert day["count"] == len(same)
        assert day["total_cents"] == sum(r["amount_cents"] for r in same)
    s = d["summary"]
    assert s["count"] == len(every)
    assert s["total_cents"] == sum(r["amount_cents"] for r in every)
    assert s["first_date"] == min(r["txn_date"] for r in every)
    spend = sum(
        -r["amount_cents"]
        for r in every
        if r["category_kind"] == "spending" and not r["is_transfer"]
    )
    assert s["spending_cents"] == spend
    income = sum(
        r["amount_cents"] for r in every if r["category_kind"] == "income" and not r["is_transfer"]
    )
    assert s["income_cents"] == income
    big = max(
        (r for r in every if not r["is_transfer"] and r["category_kind"] != "transfer"),
        key=lambda r: abs(r["amount_cents"]),
    )
    assert abs(s["largest"]["amount_cents"]) == abs(big["amount_cents"])
    # Later pages carry no summary.
    assert search(full, limit=25, cursor=d["next_cursor"])["summary"] is None
    assert search(full, include_summary=False)["summary"] is None


@pytest.mark.parametrize("sort", ["date_desc", "date_asc", "amount_desc", "amount_asc"])
def test_search_cursor_walks_every_row_once(samples: Settings, full: TestClient, sort) -> None:
    rows = search_all(full, sort=sort, limit=9)
    keys = [r["txn_key"] for r in rows]
    assert len(keys) == len(set(keys)) == len(live(samples, "hidden = 0"))
    if sort.startswith("date"):
        vals = [(r["txn_date"], r["txn_key"]) for r in rows]
    else:
        vals = [(abs(r["amount_cents"]), r["txn_key"]) for r in rows]
    assert vals == sorted(vals, reverse=sort.endswith("desc"))


def test_search_cursor_must_match_sort(samples: Settings, full: TestClient) -> None:
    cursor = search(full, limit=5)["next_cursor"]
    r = full.post(
        "/api/transactions/search", json={"cursor": cursor, "sort": "amount_asc"}, headers=H
    )
    assert r.status_code == 400 and r.json() == {"detail": "Invalid cursor."}
    r = full.post("/api/transactions/search", json={"cursor": "!!!"}, headers=H)
    assert r.status_code == 400


def test_search_filters(samples: Settings, full: TestClient) -> None:
    every = live(samples, "hidden = 0")

    def keys(rows) -> list[str]:
        return sorted(r["txn_key"] for r in rows)

    def check(body: dict, pred) -> None:
        got = search_all(full, **body, limit=500)
        want = [r for r in every if pred(r)]
        assert want, body
        assert keys(got) == keys(want), body

    check({"q": "green leaf"}, lambda r: "GREEN LEAF" in r["description"].upper())
    check(
        {"q": "Fibernet"},
        lambda r: "FIBERNET" in (r["description"] + (r["merchant"] or "")).upper(),
    )
    month = every[-1]["txn_date"][:7]
    check({"months": [month]}, lambda r: r["txn_date"].startswith(month))
    check(
        {"date_from": "2026-09-05", "date_to": "2026-09-12"},
        lambda r: "2026-09-05" <= r["txn_date"] <= "2026-09-12",
    )
    acct = every[0]["account_key"]
    check({"accounts": [acct]}, lambda r: r["account_key"] == acct)
    check({"categories": ["groceries"]}, lambda r: r["category_key"] == "groceries")
    check({"category_groups": ["lifestyle"]}, lambda r: r["group_key"] == "lifestyle")
    owner = every[0]["cardholder_person_key"] or every[0]["owner_person_key"]
    check(
        {"owners": [owner]},
        lambda r: (r["cardholder_person_key"] or r["owner_person_key"]) == owner,
    )
    check(
        {"amount_min_cents": 5000, "amount_max_cents": 10000},
        lambda r: 5000 <= abs(r["amount_cents"]) <= 10000,
    )
    check({"direction": "in"}, lambda r: r["amount_cents"] > 0)
    check({"direction": "out"}, lambda r: r["amount_cents"] < 0)
    check({"is_transfer": True}, lambda r: r["is_transfer"] == 1)
    check({"is_transfer": False}, lambda r: not r["is_transfer"])
    check({"uncategorized": False}, lambda r: r["category_kind"] != "uncategorized")
    check(
        {"recurring_key": "fibernet-internet"}, lambda r: r["recurring_key"] == "fibernet-internet"
    )
    check(
        {"direction": "out", "categories": ["groceries"], "months": ["2026-09"]},
        lambda r: (
            r["amount_cents"] < 0
            and r["category_key"] == "groceries"
            and r["txn_date"].startswith("2026-09")
        ),
    )
    # Wildcards in the text are literal.
    assert search(full, q="%")["rows"] == []
    assert search(full, q="_")["rows"] == []


def test_hidden_filter_and_summary_endpoint(samples: Settings, full: TestClient) -> None:
    key = key_of(samples, "ATM WITHDRAWAL")
    assert patch(full, key, {"hidden": True}).status_code == 200
    keys = {r["txn_key"] for r in search_all(full, limit=500)}
    assert key not in keys
    only = search(full, hidden="only")["rows"]
    assert [r["txn_key"] for r in only] == [key] and only[0]["hidden"] is True
    assert key in {r["txn_key"] for r in search_all(full, hidden="include", limit=500)}

    body = {"months": ["2026-09"], "categories": ["groceries", "dining"], "direction": "out"}
    via_search = search(full, **body)["summary"]
    r = full.get("/api/transactions/summary", params=body)
    assert r.status_code == 200 and r.json() == via_search
    # Search text never goes in a URL.
    assert full.get("/api/transactions/summary", params={"q": "green"}).status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"limit": 0},
        {"limit": 501},
        {"months": ["2026-13"]},
        {"accounts": ["Bad Key"]},
        {"date_from": "2026-02-31"},
        {"q": "x" * 101},
        {"sort": "merchant"},
        {"unknown": 1},
        {"amount_min_cents": -1},
    ],
)
def test_search_bad_body(samples: Settings, full: TestClient, body: dict) -> None:
    r = full.post("/api/transactions/search", json=body, headers=H)
    assert r.status_code == 422
    for v in body.values():
        if len(str(v)) > 4:
            assert str(v) not in r.text


# --------------------------------------------------------------------------
# Edits
# --------------------------------------------------------------------------


def test_patch_sets_and_clears_overrides(samples: Settings, full: TestClient) -> None:
    key = key_of(samples, "ATM WITHDRAWAL")
    before = q(samples, "SELECT * FROM transactions WHERE txn_key = ?", (key,))[0]
    r = patch(
        full,
        key,
        {
            "category_key": "gifts",
            "merchant": "  Corner   ATM ",
            "is_reimbursable": True,
            "notes": "Cash for the school fair",
            "tags": ["school", "School", " fair "],
        },
    )
    assert r.status_code == 200, r.text
    row = r.json()
    assert row["category_key"] == "gifts" and row["category_source"] == "override"
    assert row["merchant"] == "Corner ATM" and row["imported_merchant"] == before["merchant"]
    assert row["is_reimbursable"] is True and row["tags"] == ["school", "fair"]
    assert row["notes"] == "Cash for the school fair"
    assert set(row["overridden"]) == {
        "category_key",
        "merchant",
        "is_reimbursable",
        "notes",
        "tags",
    }
    ov = q(samples, "SELECT * FROM transaction_overrides WHERE txn_key = ?", (key,))[0]
    assert json.loads(ov["guard_json"]) == {
        "desc_norm": before["desc_norm"],
        "merchant": before["merchant"],
        "amount_cents": before["amount_cents"],
        "txn_date": before["txn_date"],
    }
    # The import is never changed.
    after = q(samples, "SELECT * FROM transactions WHERE txn_key = ?", (key,))[0]
    assert dict(after) == dict(before)

    # null clears one field; other fields stay.
    row = patch(full, key, {"category_key": None}).json()
    assert row["category_key"] == before["category_key"] and row["category_source"] == "imported"
    assert row["merchant"] == "Corner ATM"
    # Clearing everything removes the override row.
    row = patch(
        full,
        key,
        {"merchant": "", "is_reimbursable": None, "notes": "  ", "tags": []},
    ).json()
    assert row["has_override"] is False and row["overridden"] == []
    assert q(samples, "SELECT count(*) FROM transaction_overrides")[0][0] == 0

    kinds = [e["kind"] for e in events(full, key)]
    assert kinds[0] == "imported"
    assert kinds[1:] == [
        "category_changed",
        "merchant_changed",
        "reimbursable_changed",
        "note_added",
        "tags_changed",
        "category_changed",
        "merchant_changed",
        "reimbursable_changed",
        "note_removed",
        "tags_changed",
    ]
    ev = events(full, key)
    assert ev[1]["from_value"] == before["category_key"] and ev[1]["to_value"] == "gifts"
    assert ev[1]["user"] == "Alex Example" and ev[1]["via"] == "edit"
    # Note text is never copied into events.
    texts = q(samples, "SELECT from_value, to_value FROM transaction_events")
    assert all("school fair" not in (v or "") for r in texts for v in r)


def test_patch_marks_transfer_and_member_can_edit(settings: Settings, prod: TestClient) -> None:
    migrate(settings=settings)
    for run in SAMPLE_RUNS:
        import_folder(SAMPLES / run, settings=settings, today=TODAY)
    assert login(prod, MEMBER, MEMBER_PW, PROD_ORIGIN).status_code == 200
    key = key_of(settings, "ATM WITHDRAWAL")
    row = patch(prod, key, {"is_transfer": True}).json()
    assert row["is_transfer"] is True and row["overridden"] == ["is_transfer"]
    ev = events(prod, key)[-1]
    assert (ev["kind"], ev["from_value"], ev["to_value"], ev["user"]) == (
        "transfer_changed",
        "false",
        "true",
        "Sam Example",
    )
    flows = q(settings, "SELECT flow FROM v_cashflow_rows WHERE txn_key = ?", (key,))
    assert flows[0][0] == "transfer"


@pytest.mark.parametrize(
    "body,status",
    [
        ({}, 422),
        ({"category_key": "no-such-category"}, 422),
        ({"category_key": "Bad Key"}, 422),
        ({"merchant": "x" * 65}, 422),
        ({"notes": "x" * 501}, 422),
        ({"tags": ["a"] * 11}, 422),
        ({"tags": ["x" * 33]}, 422),
        ({"merchant": "bad\x00name"}, 422),
        ({"merchant": "AB\u202eCD"}, 422),
        ({"tags": ["a\u2066b"]}, 422),
        ({"is_transfer": "maybe"}, 422),
        ({"txn_type": "refund"}, 422),
    ],
)
def test_patch_bad_body(samples: Settings, full: TestClient, body: dict, status: int) -> None:
    key = key_of(samples, "ATM WITHDRAWAL")
    r = patch(full, key, body)
    assert r.status_code == status, r.text
    assert q(samples, "SELECT count(*) FROM transaction_overrides")[0][0] == 0


def test_emoji_with_joiners_are_allowed(samples: Settings, full: TestClient) -> None:
    key = key_of(samples, "ATM WITHDRAWAL")
    fire = "fire \u2764\ufe0f\u200d\U0001f525"
    family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"
    r = patch(full, key, {"notes": fire, "tags": [family]})
    assert r.status_code == 200, r.text
    assert r.json()["notes"] == fire and r.json()["tags"] == [family]


def test_typed_text_never_reaches_events(samples: Settings, full: TestClient) -> None:
    key = key_of(samples, "ATM WITHDRAWAL")
    assert patch(full, key, {"merchant": "Corner Shop", "tags": ["school fair"]}).status_code == 200
    assert patch(full, key, {"merchant": None, "tags": None}).status_code == 200
    ev = [e for e in events(full, key) if e["kind"] in ("merchant_changed", "tags_changed")]
    assert [e["kind"] for e in ev] == [
        "merchant_changed",
        "tags_changed",
        "merchant_changed",
        "tags_changed",
    ]
    assert all(e["from_value"] is None and e["to_value"] is None for e in ev)
    texts = q(samples, "SELECT from_value, to_value FROM transaction_events")
    flat = " ".join(v or "" for r in texts for v in r)
    assert "Corner Shop" not in flat and "school fair" not in flat


def test_patch_unknown_or_removed_row_is_404(samples: Settings, full: TestClient) -> None:
    assert patch(full, "a" * 64, {"hidden": True}).status_code == 404
    assert patch(full, "not-a-key", {"hidden": True}).status_code == 422
    gone = q(samples, "SELECT txn_key FROM transactions WHERE removed_at IS NOT NULL")[0][0]
    assert patch(full, gone, {"hidden": True}).status_code == 404
    assert full.get(f"/api/transactions/{'b' * 64}/events").status_code == 404


@pytest.mark.parametrize("field", ["merchant", "notes", "tags"])
@pytest.mark.parametrize("value", SENSITIVE)
def test_patch_sensitive_text_is_422_without_echo(
    samples: Settings, full: TestClient, field: str, value: str
) -> None:
    key = key_of(samples, "ATM WITHDRAWAL")
    body = {field: [value] if field == "tags" else value}
    r = patch(full, key, body)
    assert r.status_code == 422
    assert value not in r.text and value.replace(" ", "") not in r.text
    assert "Remove that part" in r.json()["detail"]
    assert q(samples, "SELECT count(*) FROM transaction_overrides")[0][0] == 0


def test_private_names_are_rejected(
    use_private_rules: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = use_private_rules
    add_users(settings)
    for run in SAMPLE_RUNS:
        import_folder(SAMPLES / run, settings=settings, today=TODAY)
    app = app_for(monkeypatch, dev=False, BUDGET_PRIVATE_RULES=str(settings.private_rules))
    with TestClient(app, base_url=PROD_URL) as c:
        assert login(c, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
        key = key_of(settings, "ATM WITHDRAWAL")
        for body in ({"notes": f"for {PRIVATE.lower()}"}, {"merchant": PRIVATE}):
            r = patch(c, key, body)
            assert r.status_code == 422 and PRIVATE.lower() not in r.text.lower()
        r = c.post(
            "/api/rules",
            json={"match_kind": "merchant_equals", "pattern": PRIVATE, "category_key": "misc"},
            headers=H,
        )
        assert r.status_code == 422 and PRIVATE not in r.text


# --------------------------------------------------------------------------
# Bulk
# --------------------------------------------------------------------------


def test_bulk_edit_and_atomicity(samples: Settings, full: TestClient) -> None:
    keys = [r["txn_key"] for r in live(samples, "description = 'GREEN LEAF MARKET'")]
    assert len(keys) >= 3
    r = full.post(
        "/api/transactions/bulk",
        json={"txn_keys": [*keys, "c" * 64], "changes": {"category_key": "dining"}},
        headers=H,
    )
    assert r.status_code == 404
    assert q(samples, "SELECT count(*) FROM transaction_overrides")[0][0] == 0
    assert q(samples, "SELECT count(*) FROM transaction_events")[0][0] == 0
    r = full.post(
        "/api/transactions/bulk",
        json={"txn_keys": keys, "changes": {"category_key": "dining", "tags": ["weekly"]}},
        headers=H,
    )
    assert r.status_code == 200 and r.json() == {"count": len(keys), "updated": len(keys)}
    got = search_all(full, q="green leaf", limit=500)
    assert {g["category_key"] for g in got if g["txn_key"] in keys} == {"dining"}
    ev = q(samples, "SELECT DISTINCT via FROM transaction_events")
    assert [e[0] for e in ev] == ["bulk"]
    # A sensitive value in a bulk edit writes nothing.
    r = full.post(
        "/api/transactions/bulk",
        json={"txn_keys": keys, "changes": {"notes": LUHN}},
        headers=H,
    )
    assert r.status_code == 422 and LUHN not in r.text
    assert (
        q(samples, "SELECT count(*) FROM transaction_overrides WHERE notes IS NOT NULL")[0][0] == 0
    )
    too_many = full.post(
        "/api/transactions/bulk",
        json={"txn_keys": ["d" * 64] * 501, "changes": {"hidden": True}},
        headers=H,
    )
    assert too_many.status_code == 422


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------


def _rule(client: TestClient, kind: str, pattern: str, category: str, **extra) -> object:
    return client.post(
        "/api/rules",
        json={"match_kind": kind, "pattern": pattern, "category_key": category, **extra},
        headers=H,
    )


def test_rules_precedence_and_read_time(samples: Settings, full: TestClient) -> None:
    rows = live(samples, "description LIKE '%GREEN LEAF%'")
    green = [r["txn_key"] for r in rows]
    one = green[0]
    other = next(r["txn_key"] for r in rows[1:] if r["category_key"] == "groceries")
    stored_before = q(samples, "SELECT txn_key, category_key FROM transactions ORDER BY txn_key")

    pv = full.post(
        "/api/rules/preview",
        json={
            "match_kind": "description_contains",
            "pattern": "green leaf",
            "category_key": "pets",
        },
        headers=H,
    )
    assert pv.status_code == 200
    assert pv.json() == {"matches": len(green), "edited": 0, "would_change": len(green)}

    r = _rule(full, "description_contains", "green  leaf ", "pets", created_from_txn_key=one)
    assert r.status_code == 201, r.text
    contains = r.json()
    assert contains["pattern"] == "green leaf" and contains["applied_count"] == len(green)
    row = search(full, q="green leaf", limit=500)["rows"][0]
    assert row["category_key"] == "pets" and row["category_source"] == "rule"
    assert row["rule_id"] == contains["id"]

    # merchant equals beats description contains.
    r = _rule(full, "merchant_equals", "GREEN LEAF market", "dining")
    assert r.status_code == 201
    equals = r.json()
    cats = {x["category_key"] for x in search_all(full, q="green leaf", limit=500)}
    assert cats == {"dining"}

    # An override beats every rule.
    row = patch(full, one, {"category_key": "gifts"}).json()
    assert row["category_source"] == "override" and row["rule_id"] is None

    # Rules are never written into transactions.
    stored_after = q(samples, "SELECT txn_key, category_key FROM transactions ORDER BY txn_key")
    assert [tuple(r) for r in stored_after] == [tuple(r) for r in stored_before]

    # Turning the equals rule off brings back the contains rule.
    r = full.patch(f"/api/rules/{equals['id']}", json={"active": False}, headers=H)
    assert r.status_code == 200 and r.json()["active"] is False
    rows = {x["txn_key"]: x for x in search_all(full, q="green leaf", limit=500)}
    assert rows[one]["category_key"] == "gifts"
    assert {rows[k]["category_key"] for k in green if k != one} == {"pets"}

    # Rule changes are in the timeline of the rows they changed.
    ev = [e for e in events(full, other) if e["via"] == "rule"]
    assert [(e["from_value"], e["to_value"]) for e in ev] == [
        ("groceries", "pets"),
        ("pets", "dining"),
        ("dining", "pets"),
    ]
    assert ev[0]["rule_id"] == contains["id"]
    assert not [e for e in events(full, one) if e["via"] == "rule" and e["id"] > ev[1]["id"]]

    listed = full.get("/api/rules").json()["rules"]
    assert [x["id"] for x in listed] == [equals["id"], contains["id"]]
    assert listed[1]["applied_count"] == len(green) - 1
    assert listed[1]["created_from_txn_key"] == one and listed[1]["created_by"] == "Alex Example"


def test_rule_rules(samples: Settings, full: TestClient, member_too) -> None:
    r = _rule(full, "merchant_equals", "Fibernet", "phone")
    assert r.status_code == 201
    rid = r.json()["id"]
    assert _rule(full, "merchant_equals", "FIBERNET", "phone").status_code == 409
    assert _rule(full, "description_contains", "ab", "phone").status_code == 422
    assert _rule(full, "merchant_equals", "Fibernet two", "no-such").status_code == 422
    assert _rule(full, "merchant_equals", "x" * 65, "phone").status_code == 422
    assert (
        _rule(full, "merchant_equals", "y", "phone", created_from_txn_key="e" * 64).status_code
        == 404
    )
    for value in SENSITIVE:
        r = _rule(full, "description_contains", value, "phone")
        assert r.status_code == 422 and value not in r.text
    # Control and bidi characters are refused in every pattern field.
    for bad in ("AB\u202eC\x00D", "ABC\u200fD", "AB\x07CD"):
        assert _rule(full, "merchant_equals", bad, "phone").status_code == 422
        preview = {"match_kind": "merchant_equals", "pattern": bad}
        assert full.post("/api/rules/preview", json=preview, headers=H).status_code == 422
        r = full.patch(f"/api/rules/{rid}", json={"pattern": bad}, headers=H)
        assert r.status_code == 422
    assert full.patch("/api/rules/999", json={"active": False}, headers=H).status_code == 404
    assert full.patch(f"/api/rules/{rid}", json={}, headers=H).status_code == 422
    assert full.patch(f"/api/rules/{rid}", json={"pattern": LUHN}, headers=H).status_code == 422
    r = full.patch(f"/api/rules/{rid}", json={"category_key": "internet"}, headers=H)
    assert r.status_code == 200 and r.json()["category_key"] == "internet"

    # A member cannot delete a rule. An admin can.
    member = member_too()
    assert member.get("/api/rules").status_code == 200
    assert member.delete(f"/api/rules/{rid}", headers=H).status_code == 403
    assert full.delete(f"/api/rules/{rid}", headers=H).status_code == 204
    assert full.delete(f"/api/rules/{rid}", headers=H).status_code == 404
    assert full.get("/api/rules").json() == {"rules": []}


@pytest.fixture
def member_too(settings: Settings, monkeypatch: pytest.MonkeyPatch):
    """A second client, signed in as the member, on the same database."""
    clients: list[TestClient] = []

    def make() -> TestClient:
        c = TestClient(app_for(monkeypatch, dev=False), base_url=PROD_URL)
        c.__enter__()
        clients.append(c)
        assert login(c, MEMBER, MEMBER_PW, PROD_ORIGIN).status_code == 200
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


# --------------------------------------------------------------------------
# Import: re-key and orphans (PLAN 4.11 step 6)
# --------------------------------------------------------------------------


def test_edits_survive_a_correction_that_changes_the_row(
    settings: Settings, three: TestClient
) -> None:
    """The sample correction fixes FIBERNET INTERNT (new key) and recategorizes a
    GREEN LEAF row (same key). Both edits stay; the first is re-keyed."""
    typo = key_of(settings, "FIBERNET INTERNT")
    green = q(
        settings,
        """SELECT txn_key FROM v_transactions WHERE statement_key = ?
           AND description = 'GREEN LEAF MARKET' AND txn_date = '2026-09-16'""",
        (MAPLE,),
    )[0][0]
    assert patch(three, typo, {"category_key": "phone", "notes": "Check plan"}).status_code == 200
    assert patch(three, green, {"is_transfer": True}).status_code == 200

    result = import_folder(SAMPLES / "2026-10-14", settings=settings, today=TODAY)
    assert result.status == "imported"
    assert result.counts["rekeyed"] == 1 and result.counts["orphaned"] == 0

    fixed = key_of(settings, "FIBERNET INTERNET")
    assert fixed != typo
    row = search(three, q="fibernet internet")["rows"][0]
    assert row["txn_key"] == fixed and row["category_key"] == "phone"
    assert row["notes"] == "Check plan"
    green_row = next(r for r in search_all(three, limit=500) if r["txn_key"] == green)
    assert green_row["is_transfer"] is True
    assert three.get("/api/transactions/orphaned-edits").json() == {"rows": []}

    # The guard now describes the new row, and the timeline moved with the edit.
    ov = q(settings, "SELECT guard_json FROM transaction_overrides WHERE txn_key = ?", (fixed,))
    assert json.loads(ov[0][0])["desc_norm"] == "FIBERNET INTERNET"
    ev = events(three, fixed)
    assert [e["kind"] for e in ev] == ["imported", "category_changed", "note_added", "rekeyed"]
    assert ev[-1]["prev_txn_key"] == typo and ev[-1]["via"] == "import"

    # Reapply and re-import change nothing and log nothing new.
    before_fp = fp(settings)
    n_events = q(settings, "SELECT count(*) FROM transaction_events")[0][0]
    assert reapply(settings)["changes"] == 0
    assert import_folder(SAMPLES / "2026-10-14", settings=settings, today=TODAY).changes == 0
    assert fp(settings) == before_fp
    assert q(settings, "SELECT count(*) FROM transaction_events")[0][0] == n_events


def test_edit_on_a_removed_row_is_orphaned(
    settings: Settings, three: TestClient, tmp_path: Path
) -> None:
    assert import_folder(SAMPLES / "2026-10-14", settings=settings, today=TODAY).ok
    atm = key_of(settings, "ATM WITHDRAWAL")
    assert patch(three, atm, {"category_key": "gifts", "tags": ["fair"]}).status_code == 200

    run = drop_atm_correction(tmp_path)
    result = import_folder(run, settings=settings, today=TODAY)
    assert result.status == "imported", result.issues
    assert result.counts["orphaned"] == 1 and result.counts["rekeyed"] == 0
    assert all(r["txn_key"] != atm for r in search_all(three, limit=500))

    orphans = three.get("/api/transactions/orphaned-edits").json()["rows"]
    assert len(orphans) == 1
    o = orphans[0]
    assert o["txn_key"] == atm and o["amount_cents"] == -10000
    assert o["txn_date"] == "2026-09-09" and o["desc_norm"] == "ATM WITHDRAWAL"
    assert o["account_key"] == "maple-joint-checking" and o["removed_at"]
    assert o["orphaned_at"] and o["updated_by"] == "Alex Example"
    assert o["override"]["category_key"] == "gifts" and o["override"]["tags"] == ["fair"]
    assert o["override"]["is_transfer"] is None
    assert [e["kind"] for e in events(three, atm)][-1] == "orphaned"

    # Replays keep one orphaned event, and the orphan list is the same.
    n_events = q(settings, "SELECT count(*) FROM transaction_events")[0][0]
    reapply(settings)
    assert q(settings, "SELECT count(*) FROM transaction_events")[0][0] == n_events
    assert len(three.get("/api/transactions/orphaned-edits").json()["rows"]) == 1


def test_correction_before_edits_arrive_out_of_order(
    settings: Settings, three: TestClient, tmp_path: Path
) -> None:
    """An earlier run arriving late triggers a replay. Moved edits stay moved."""
    typo = key_of(settings, "FIBERNET INTERNT")
    assert patch(three, typo, {"category_key": "phone"}).status_code == 200
    assert import_folder(SAMPLES / "2026-10-14", settings=settings, today=TODAY).ok
    fixed = key_of(settings, "FIBERNET INTERNET")
    late = tmp_path / "2026-10-13"
    shutil.copytree(SAMPLES / "2026-10-14", late)
    for name in ("snapshot.json", "manifest.json"):
        obj = read_json(late / name)
        obj["run_id"] = "2026-10-13"
        write_json(late / name, obj)
    refresh_manifest(late)
    assert import_folder(late, settings=settings, today=TODAY).ok  # replays all runs
    rows = q(settings, "SELECT txn_key, category_key FROM transaction_overrides")
    assert [tuple(r) for r in rows] == [(fixed, "phone")]
    kinds = [e["kind"] for e in events(three, fixed)]
    assert kinds.count("rekeyed") == 1


def _ov(conn: sqlite3.Connection, key: str, guard: dict) -> None:
    conn.execute(
        """INSERT INTO transaction_overrides (txn_key, category_key, guard_json, updated_at)
           VALUES (?, 'gifts', ?, 't')""",
        (key, json.dumps(guard)),
    )


def test_reattach_needs_one_best_match(db, settings: Settings) -> None:
    g = {"desc_norm": "CAFE", "merchant": "Cafe", "amount_cents": -500, "txn_date": "2026-09-10"}
    old = dict(g)

    def cand(desc: str, merchant: str | None, amount: int, day: str) -> dict:
        return {"desc_norm": desc, "merchant": merchant, "amount_cents": amount, "txn_date": day}

    with write_connection(settings.db_path) as conn:
        _ov(conn, "a" * 64, g)
        # Two equal candidates: orphaned.
        moved = reattach_overrides(
            conn,
            {"a" * 64: old},
            {
                "b" * 64: cand("CAFE", "Cafe", -600, "2026-09-11"),
                "c" * 64: cand("CAFE", "Cafe", -600, "2026-09-09"),
            },
            "now",
        )
        assert moved == (0, 1)
        # The same amount wins over a closer date. A different text never matches.
        moved = reattach_overrides(
            conn,
            {"a" * 64: old},
            {
                "b" * 64: cand("CAFE", "Cafe", -600, "2026-09-10"),
                "c" * 64: cand("CAFE", "Cafe", -500, "2026-09-14"),
                "d" * 64: cand("BAKERY", "Bakery", -500, "2026-09-10"),
            },
            "now",
        )
        assert moved == (1, 0)
        keys = [r[0] for r in conn.execute("SELECT txn_key FROM transaction_overrides")]
        assert keys == ["c" * 64]
        # A merchant match alone is enough, and a row that has an override is skipped.
        _ov(conn, "e" * 64, g)
        moved = reattach_overrides(
            conn,
            {"e" * 64: old},
            {
                "c" * 64: cand("CAFE", "Cafe", -500, "2026-09-10"),
                "f" * 64: cand("SQ *CAFE", "cafe", -900, "2026-09-20"),
            },
            "now",
        )
        assert moved == (1, 0)
        kinds = [r[0] for r in conn.execute("SELECT kind FROM transaction_events ORDER BY id")]
        assert kinds == ["orphaned", "rekeyed", "rekeyed"]


# --------------------------------------------------------------------------
# Logs
# --------------------------------------------------------------------------


def test_transaction_calls_log_no_values(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    add_users(settings)
    for run in SAMPLE_RUNS:
        import_folder(SAMPLES / run, settings=settings, today=TODAY)
    key = key_of(settings, "ATM WITHDRAWAL")
    app = app_for(monkeypatch, dev=False)
    with TestClient(app, base_url=PROD_URL) as c:
        logging.getLogger().setLevel(logging.DEBUG)
        assert login(c, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
        search(c, q="Zebra search words", accounts=["maple-joint-checking"])
        assert (
            patch(c, key, {"notes": "Quokka note text", "merchant": "Wombat Cash"}).status_code
            == 200
        )
        assert patch(c, key, {"notes": LUHN}).status_code == 422
        assert _rule(c, "merchant_equals", "Platypus Shop", "misc").status_code == 201
        c.get(f"/api/transactions/{key}/events")
        c.get("/api/transactions/summary", params={"accounts": "maple-joint-checking"})
    text = captured(caplog, capsys.readouterr().out)
    assert '"route": "/api/transactions/{txn_key}"' in text
    for value in ("Zebra", "Quokka", "Wombat", "Platypus", key, LUHN, "maple-joint-checking"):
        assert value not in text, value
