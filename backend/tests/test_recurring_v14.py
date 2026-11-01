"""Format 1.4 on Recurring and Ask (docs/plans/format-14.md sections 3.2, 6 and 7): tag
sections right after their plan group, keep and tag on the All view, totals and plan
lines still by plan group, the personal subscription never tagged, and keep and tag in
the AI facts. Fake samples only."""

from __future__ import annotations

import json
from datetime import date

import pytest
from bad_cases import MONTHLY
from fastapi.testclient import TestClient
from test_state_v14 import _add_run

from app import config
from app.ai import tools
from app.ai.facts import FACTS_VERSION, HIDDEN, RECURRING_FIELDS, build, ensure_facts
from app.ai.prompts import ASK_RULES
from app.ai.safe import ai_connection
from app.config import Settings
from app.db.connect import connect, write_connection
from app.importer.models import PERSONAL_SUBSCRIPTION, Recurring
from app.services import recurring as rec
from app.services.balances import clear_cache
from app.state import readers

TODAY = date(2026, 10, 20)
KEEP_RULE = "A bill with keep is one the household chose to keep; do not suggest cancelling it."


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    clear_cache()


def _all(admin: TestClient) -> dict:
    r = admin.get("/api/recurring", params={"view": "all"})
    assert r.status_code == 200
    return r.json()


def test_tagged_bills_get_a_section_right_after_their_plan_group(
    samples: Settings, admin: TestClient
) -> None:
    r = _all(admin)
    sections = r["sections"]
    assert [s["key"] for s in sections] == [
        "lifestyle",
        "lifestyle.streaming",
        "housing",
        "kids",
        "kids.hobbies",
        "services",
        "services.hobbies",
        "fees",
        "savings",
    ]
    by_key = {s["key"]: s for s in sections}
    groups = {s["key"]: s["label"] for s in sections if s["kind"] == "bills"}
    for key, label in (
        ("lifestyle.streaming", "Streaming"),
        ("kids.hobbies", "Hobbies"),
        ("services.hobbies", "Hobbies"),
    ):
        group = key.split(".")[0]
        assert by_key[key] == {
            "key": key,
            "label": label,
            "kind": "tag",
            "group_key": group,
            "group_label": groups[group],
        }
    others = [s for s in sections if s["kind"] != "tag"]
    assert all(s["group_key"] is None and s["group_label"] is None for s in others)
    items = {i["key"]: i for i in r["items"]}
    assert {k: items[k]["section_key"] for k in items if k in SECTION_KEYS} == SECTION_KEYS
    # The plan group stays the category's group.
    assert items["cloudtunes"]["group_key"] == "lifestyle"
    assert items["music-lessons"]["group_key"] == "kids"
    assert items["gym"]["group_key"] == "services"


SECTION_KEYS = {
    "cloudtunes": "lifestyle.streaming",
    "reelhouse": "lifestyle.streaming",
    "gym": "services.hobbies",
    "music-lessons": "kids.hobbies",
    "personal-subscription": "lifestyle",
    "summit-annual-fee": "fees",
}


def test_tags_move_no_totals_or_plan_lines(samples: Settings, admin: TestClient) -> None:
    r = _all(admin)
    bills = [i for i in r["items"] if i["counted"] and i["section_key"] != "savings"]
    assert {"gym", "music-lessons"} <= {i["key"] for i in bills}
    assert r["totals"] == {
        "monthly_equivalent_cents": sum(i["monthly_equivalent_cents"] for i in bills),
        "net_monthly_equivalent_cents": sum(i["net_monthly_equivalent_cents"] for i in bills),
        "count": len(bills),
    }
    conn = connect(samples.db_path)
    try:
        lines = rec.plan_lines(conn, "2026-11")
    finally:
        conn.close()
    assert "music-lessons" in {x["key"] for x in lines["kids"]}
    assert "gym" in {x["key"] for x in lines["services"]}
    assert all("." not in group for group in lines)


def test_keep_and_tag_on_the_all_view(samples: Settings, admin: TestClient) -> None:
    items = {i["key"]: i for i in _all(admin)["items"]}
    want = {
        "music-lessons": (True, "hobbies"),
        "summit-annual-fee": (True, None),
        "cloudtunes": (None, "streaming"),
        "personal-subscription": (None, None),
    }
    assert {k: (items[k]["keep"], items[k]["tag"]) for k in want} == want
    # keep is true or null, never false.
    kept = {k for k, i in items.items() if i["keep"] is not None}
    assert kept == {"music-lessons", "summit-annual-fee"}
    assert all(items[k]["keep"] is True for k in kept)


def test_the_personal_subscription_never_shows_a_tag(samples: Settings, admin: TestClient) -> None:
    """A stored run is not checked again, so a tag on the personal subscription that got
    past import (format 1.4 section 3.2) still never shows."""
    with write_connection(samples.db_path) as w:
        (raw,) = w.execute(
            "SELECT json FROM run_sections WHERE run_id = ? AND section = 'recurring'",
            (MONTHLY,),
        ).fetchone()
        bills = json.loads(raw)
        private = [b for b in bills if b["name"] == PERSONAL_SUBSCRIPTION]
        assert len(private) == 1
        private[0]["tag"] = "streaming"
        _add_run(w, "2026-10-20", "monthly", recurring=bills)
    clear_cache()
    conn = connect(samples.db_path)
    try:
        runs = readers.monthly_runs(conn)
        stored = [b for b in readers.recurring(runs) if b.name == PERSONAL_SUBSCRIPTION]
    finally:
        conn.close()
    assert readers.section_source(runs, "recurring") == "2026-10-20"
    assert [b.tag for b in stored] == ["streaming"]  # the stored run carries it
    r = _all(admin)
    item = next(i for i in r["items"] if i["name"] == PERSONAL_SUBSCRIPTION)
    assert (item["tag"], item["section_key"]) == (None, "lifestyle")
    streaming = {i["key"] for i in r["items"] if i["section_key"] == "lifestyle.streaming"}
    assert streaming == {"cloudtunes", "reelhouse"}
    conn = connect(samples.db_path)
    try:
        facts = {(k, key): v for k, key, v in build(conn, None)}
    finally:
        conn.close()
    shown = [i for i in facts[("recurring", "all")]["items"] if i["name"] == PERSONAL_SUBSCRIPTION]
    assert len(shown) == 1 and shown[0]["tag"] is None
    # Case and spaces do not matter.
    bill = Recurring.model_validate(
        {
            "key": "x",
            "name": "  personal   SUBSCRIPTION ",
            "category_key": "subscriptions",
            "amount": "9.99",
            "cadence": "monthly",
            "status": "active",
            "tag": "hobbies",
        }
    )
    assert rec.shown_tag(bill) is None
    assert rec.shown_tag(bill.model_copy(update={"name": "Example Stream"})) == "hobbies"


def test_a_private_bill_not_yet_masked_shows_no_keep_or_tag_to_ask(
    samples: Settings, private_rules
) -> None:
    """A private bill the rescan has not masked yet (first start with the private list,
    or a failed rescan) keeps its own name in the store. Facts hide the name, and keep
    and tag go too: they would hint at what it is (Codex review)."""
    with write_connection(samples.db_path) as w:
        (raw,) = w.execute(
            "SELECT json FROM run_sections WHERE run_id = ? AND section = 'recurring'",
            (MONTHLY,),
        ).fetchone()
        bills = json.loads(raw)
        private = next(b for b in bills if b["name"] == PERSONAL_SUBSCRIPTION)
        private.update(name=private_rules.subscriptions[0], tag="streaming", keep=True)
        _add_run(w, "2026-10-20", "monthly", recurring=bills)
    clear_cache()
    conn = connect(samples.db_path)
    try:
        facts = {(k, key): v for k, key, v in build(conn, private_rules)}
    finally:
        conn.close()
    items = facts[("recurring", "all")]["items"]
    hidden = [i for i in items if i["name"] == HIDDEN]
    assert len(hidden) == 1
    assert (hidden[0]["tag"], hidden[0]["keep"]) == (None, None)
    # Other bills keep theirs.
    assert {i["tag"] for i in items if i["name"] != HIDDEN} >= {"streaming"}


def test_section_order_unit() -> None:
    groups = {"a": {"name": "Group A", "sort": 1}, "b": {"name": "Group B", "sort": 2}}
    ctx = rec.Ctx(categories={}, accounts={}, people={}, groups=groups)  # type: ignore[arg-type]
    keys = {"savings", "b.software", "b", "b.streaming", "a.hobbies", "other.software", "other"}
    out = rec._sections(keys, ctx)
    assert [(s["key"], s["label"], s["kind"], s["group_key"], s["group_label"]) for s in out] == [
        # A tag section keeps its group's place when the group has no untagged bill.
        ("a.hobbies", "Hobbies", "tag", "a", "Group A"),
        ("b", "Group B", "bills", None, None),
        # Tag order: streaming, hobbies, software.
        ("b.streaming", "Streaming", "tag", "b", "Group B"),
        ("b.software", "Apps and software", "tag", "b", "Group B"),
        ("other", "Other", "bills", None, None),
        ("other.software", "Apps and software", "tag", "other", "Other"),
        ("savings", "Savings and transfers", "savings", None, None),
    ]
    assert rec._sections(set(), ctx) == []


def test_ask_sees_keep_and_tag(samples: Settings) -> None:
    assert FACTS_VERSION >= 6
    assert {"keep", "tag"} <= set(RECURRING_FIELDS)
    ensure_facts(samples.db_path, None)
    with ai_connection(samples.db_path) as conn:
        result = tools.execute(conn, "plans_and_recurring", {"show": "recurring"})
    assert not result.is_error
    items = {i["name"]: i for i in json.loads(result.content)["recurring"]["items"]}
    assert all({"keep", "tag"} <= set(i) for i in items.values())
    assert (items["Riverside Music Lessons"]["keep"], items["Riverside Music Lessons"]["tag"]) == (
        True,
        "hobbies",
    )
    assert items["Summit card annual fee"]["keep"] is True
    assert items["Cloudtunes"]["tag"] == "streaming"
    assert items[PERSONAL_SUBSCRIPTION]["tag"] is None
    assert KEEP_RULE in " ".join(ASK_RULES.split())
    [definition] = [d for d in tools.definitions() if d["name"] == "plans_and_recurring"]
    assert "keep" in definition["description"] and "tag" in definition["description"]


def test_monthly_and_calendar_rows_have_no_new_fields(samples: Settings, admin: TestClient) -> None:
    """keep and tag are on the All view items only. Monthly, calendar and Dashboard rows
    read them from there, so their own bodies do not change."""
    monthly = admin.get("/api/recurring", params={"month": "2026-10"}).json()
    rows = monthly["upcoming"] + monthly["complete"]
    days = admin.get("/api/recurring/calendar", params={"month": "2026-10"}).json()["days"]
    rows += [row for day in days for row in day["items"]]
    assert rows and all(not {"keep", "tag"} & set(row) for row in rows)
    assert monthly["sections"] == []
