"""Rewards inputs, chunk E (docs/plans/rewards-inputs.md sections 5, 6 and 8):
benefit files. Preview, apply with base_sig, the template, and the safety
rules on the three routes.

Fake samples only. Household today is 2026-10-20. Summit card: Airline fee
credit (semi-annual $50, ends 2026-12-31), Anniversary free night (ends
2027-03-31), Dining credit (monthly $10), Hotel status and Yearly travel
credit. Cobalt card: Streaming credit and the Fuel bonus cap.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from collections.abc import Iterator
from datetime import date

import pytest
from api_helpers import (
    ADMIN,
    ADMIN_PW,
    PROD_ORIGIN,
    PROD_URL,
    add_users,
    app_for,
    import_samples,
    login,
)
from fastapi.testclient import TestClient
from test_rewards_choices import mark, put_terms
from test_rewards_inputs_benefits import (
    LOUNGE,
    PHONE,
    REWARD_TABLES,
    _flagged,
    _text_columns,
    add,
    amounts,
    ben,
    changed,
    chat_rewards,
    counts,
    edit,
    edit_row,
    events,
    fee_credits,
    on,
    page,
    patch,
    q,
    send,
)

from app import config
from app.api.rewards_models import BenefitFileItemOut
from app.config import Settings
from app.db.connect import connect, read_connection, write_transaction
from app.importer.models import KEY_MAX, KEY_PATTERN, RewardBenefit
from app.importer.sanitize import PrivateRules
from app.logging import JsonFormatter
from app.main import walk_routes
from app.services import rewards_files as rf
from app.services import rewards_inputs as ri
from app.services import rewards_overlay as ov
from app.services.balances import clear_cache

TODAY = date(2026, 10, 20)
NOW = "2026-10-20T12:00:00+00:00"
H = {"Origin": PROD_ORIGIN}
SUMMIT = "summit-card"
PREVIEW = "/api/rewards/benefit-files/preview"
APPLY = "/api/rewards/benefit-files/apply"
TEMPLATE = "/api/rewards/benefit-files/template"
SUMMIT_KEYS = [
    "airline-fee-credit",
    "anniversary-night",
    "dining-credit",
    "hotel-status",
    "travel-credit",
]
# The chat's Dining credit in file form, with no account_key (the file's is used).
DINING = {
    "key": "dining-credit",
    "name": "Dining credit",
    "kind": "credit",
    "cadence": "monthly",
    "amount": "10.00",
    "counted_value": "100.00",
    "how_to_use": "Order from the partner delivery app",
}
NEW = {
    "key": "lounge-credit",
    "name": "Lounge credit",
    "kind": "credit",
    "cadence": "quarterly",
    "amount": "25.00",
}
SIG = re.compile(r"[0-9a-f]{64}")


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    clear_cache()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def file(*benefits: dict, **wrapper) -> dict:
    body = {
        "kind": "budget-app-benefits",
        "version": 1,
        "account_key": SUMMIT,
        "starts_on": "2027-01-01",
        "mode": "merge",
        "benefits": list(benefits),
    }
    return body | wrapper


def post_preview(c: TestClient, body: dict):
    return c.post(PREVIEW, json=body, headers=H)


def preview(c: TestClient, body: dict) -> dict:
    r = post_preview(c, body)
    assert r.status_code == 200, r.text
    return r.json()


def apply(c: TestClient, body: dict, sig: str | None):
    return c.post(APPLY, json=body | {"base_sig": sig}, headers=H)


def applied(c: TestClient, body: dict) -> dict:
    out = preview(c, body)
    assert out["problems"] == [], out["problems"]
    r = apply(c, body, out["base_sig"])
    assert r.status_code == 200, r.text
    return r.json()


def template(c: TestClient, card: str = SUMMIT) -> dict:
    r = c.get(TEMPLATE, params={"account_key": card})
    assert r.status_code == 200, r.text
    return r.json()


def problems(body: dict) -> list[tuple[str, str]]:
    return [(p["where"], p["rule"]) for p in body["problems"]]


def chat_item(settings: Settings, key: str) -> dict:
    """The chat's benefit in file form: no cycles."""
    b = next(b for b in chat_rewards(settings)["benefits"] if b["key"] == key)
    return {f: v for f, v in b.items() if f != "cycles"}


def words(settings: Settings) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in q(settings, "SELECT benefit_key, term FROM reward_match_terms ORDER BY 1, 2"):
        out.setdefault(r[0], []).append(r[1])
    return out


def page_benefit(c: TestClient, key: str) -> dict | None:
    r = c.get("/api/rewards")
    assert r.status_code == 200
    return next((b for b in r.json()["benefits"] if b["key"] == key), None)


# --------------------------------------------------------------------------
# The template and its round trip
# --------------------------------------------------------------------------


def test_the_template_item_has_the_benefit_fields() -> None:
    assert set(BenefitFileItemOut.model_fields) == set(RewardBenefit.model_fields) | {"match_terms"}
    # the in-app rules cover every field of an item
    assert set(rf.FileItemIn.model_fields) == set(RewardBenefit.model_fields)


def test_the_template_round_trip_gives_no_changes(samples: Settings, admin: TestClient) -> None:
    t = template(admin)
    assert (t["kind"], t["version"], t["account_key"]) == ("budget-app-benefits", 1, SUMMIT)
    assert (t["starts_on"], t["mode"]) == ("2026-10-20", "merge")
    assert [b["key"] for b in t["benefits"]] == SUMMIT_KEYS
    for b in t["benefits"]:  # the chat's, without its cycles, used or used_on
        assert b == chat_item(samples, b["key"])
    for mode in ("merge", "replace"):
        out = preview(admin, t | {"mode": mode})
        assert out["no_changes"] and out["unchanged"] == 5, out
        assert (out["to_add"], out["to_change"], out["to_end"], out["problems"]) == ([],) * 4
        assert SIG.fullmatch(out["base_sig"])
    r = apply(admin, t, out["base_sig"])
    assert r.status_code == 200
    assert r.json() == {"saved": 0, "added": 0, "changed": 0, "ended": 0, "unchanged": 5}
    assert counts(samples) == dict.fromkeys(REWARD_TABLES, 0)
    assert events(samples) == []


def test_the_template_holds_the_inputs_and_round_trips(
    samples: Settings, admin: TestClient
) -> None:
    edit(admin, "dining-credit", note="Use it on Fridays", amount="12.00")
    lounge = add(admin, **LOUNGE).json()["key"]
    cycles = [
        {"start": "2025-01-01", "end": "2026-12-31"},
        {"start": "2027-01-01", "end": "2027-12-31", "amount": "60.00"},
    ]
    entry = add(
        admin, **LOUNGE | {"name": "Global entry credit", "cadence": "multi_year"}, cycles=cycles
    ).json()["key"]
    assert patch(admin, entry, set={"amount": "30.00"}, amount_from="2026-01-01").status_code == 200
    assert put_terms(admin, "dining-credit", ["DINING CREDIT", "Cafe"]).status_code == 200
    assert patch(admin, "travel-credit", hidden=True).status_code == 200
    t = template(admin)
    by = {b["key"]: b for b in t["benefits"]}
    assert list(by) == sorted({*SUMMIT_KEYS, lounge, entry} - {"travel-credit"})  # hidden: out
    assert (by["dining-credit"]["note"], by["dining-credit"]["amount"]) == (
        "Use it on Fridays",
        "12.00",
    )
    assert by["dining-credit"]["match_terms"] == ["Cafe", "DINING CREDIT"]
    assert "match_terms" not in by["airline-fee-credit"]
    assert "cycles" not in by[lounge] and "cycles" not in by["dining-credit"]
    assert by[entry]["cycles"] == cycles  # stored, with no pinned amounts
    assert by[entry]["amount"] == "30.00"
    for b in t["benefits"]:
        assert "used" not in json.dumps(b.get("cycles", [])) and None not in b.values()
    before = counts(samples)
    for mode in ("merge", "replace"):
        out = preview(admin, t | {"mode": mode})
        assert out["no_changes"] and out["unchanged"] == 6, out
    r = apply(admin, t | {"mode": "replace"}, out["base_sig"])
    assert r.status_code == 200 and r.json()["saved"] == 0
    assert counts(samples) == before


def test_template_rules(samples: Settings, admin: TestClient, member: TestClient) -> None:
    assert [b["key"] for b in template(member, "cobalt-card")["benefits"]] == [
        "fuel-bonus",
        "streaming-credit",
    ]
    assert template(admin, "bramble-store-card")["benefits"] == []
    for card in ("harbor-checking", "mystery-account"):
        r = admin.get(TEMPLATE, params={"account_key": card})
        assert r.status_code == 404 and r.json()["detail"] == rf.NOT_A_CARD
        assert "mystery-account" not in r.text
    for card in ("check-car-123", "Summit Card"):
        r = admin.get(TEMPLATE, params={"account_key": card})
        assert r.status_code == 422 and card not in r.text
    assert admin.get(TEMPLATE).status_code == 422


def test_a_card_with_no_benefits_round_trips(samples: Settings, admin: TestClient) -> None:
    empty = template(admin, "bramble-store-card")
    assert empty["benefits"] == []
    out = preview(admin, empty)
    assert out["no_changes"] and out["problems"] == [] and SIG.fullmatch(out["base_sig"])
    assert apply(admin, empty, out["base_sig"]).json()["saved"] == 0
    # Replace mode needs a benefit, so an empty file never ends a whole card.
    out = preview(admin, empty | {"mode": "replace"})
    assert problems(out) == [("benefits", "file-items")] and out["base_sig"] is None
    assert counts(samples) == dict.fromkeys(REWARD_TABLES, 0) and events(samples) == []


# --------------------------------------------------------------------------
# Merge, replace, kind changes and keys
# --------------------------------------------------------------------------


def test_a_merge_file_previews_and_applies(samples: Settings, admin: TestClient) -> None:
    assert put_terms(admin, "airline-fee-credit", ["AIRLINE FEE"]).status_code == 200
    body = file(
        DINING | {"amount": "12.00", "note": "New rules", "match_terms": ["DINING CREDIT"]},
        NEW | {"match_terms": ["LOUNGE PASS"]},
        chat_item(samples, "airline-fee-credit"),  # no match_terms: its words stay
    )
    out = preview(admin, body)
    assert out["to_change"] == [
        {
            "key": "dining-credit",
            "name": "Dining credit",
            "fields": ["amount", "note"],
            "amount_from": "2027-01-01",
            "terms": True,
        }
    ]
    assert out["to_add"] == [
        {
            "key": "app-lounge-credit",
            "name": "Lounge credit",
            "kind": "credit",
            "terms": True,
        }
    ]
    assert (out["to_end"], out["unchanged"], out["no_changes"]) == ([], 1, False)
    card = page_benefit(admin, "dining-credit")["account_name"]
    assert (out["account_key"], out["account_name"], out["mode"], out["starts_on"]) == (
        SUMMIT,
        card,
        "merge",
        "2027-01-01",
    )
    assert counts(samples)["reward_benefit_edits"] == 0  # a preview writes nothing
    r = apply(admin, body, out["base_sig"])
    assert r.status_code == 200, r.text
    assert r.json() == {"saved": 2, "added": 1, "changed": 1, "ended": 0, "unchanged": 1}
    row = edit_row(samples, "dining-credit")
    assert (row["origin"], row["fields"], row["base"]) == (
        "chat",
        {"amount": "12.00", "note": "New rules"},
        {"amount": "10.00", "note": None},
    )
    assert row["amount_from"] == "2027-01-01"
    app = edit_row(samples, "app-lounge-credit")
    assert (app["origin"], app["account_key"]) == ("app", SUMMIT)
    # a calendar credit keeps its start next to the body
    assert app["fields"] == NEW | {
        "key": "app-lounge-credit",
        "account_key": SUMMIT,
        "starts_on": "2027-01-01",
    }
    assert words(samples) == {
        "airline-fee-credit": ["AIRLINE FEE"],
        "app-lounge-credit": ["LOUNGE PASS"],
        "dining-credit": ["DINING CREDIT"],
    }
    dining = page_benefit(admin, "dining-credit")
    assert dining["amount_cents"] == 1_200 and dining["amount_from"] == "2027-01-01"
    assert {c["amount_cents"] for c in [*dining["past_cycles"], dining["cycle"]]} == {1_000}
    assert page_benefit(admin, "app-lounge-credit")["origin"] == "app"
    again = preview(admin, body)  # the same file again changes nothing
    assert again["no_changes"] and again["unchanged"] == 3


def test_replace_mode_ends_the_rest_of_the_card(samples: Settings, admin: TestClient) -> None:
    lounge = add(admin, **LOUNGE).json()["key"]
    assert patch(admin, "hotel-status", hidden=True).status_code == 200
    body = file(chat_item(samples, "dining-credit"), mode="replace")
    out = preview(admin, body)
    # Airline fee credit already ends on 2026-12-31. The hidden status perk stays.
    assert out["to_end"] == [
        {"key": "anniversary-night", "name": "Anniversary free night", "ends_on": "2026-12-31"},
        {"key": lounge, "name": "Lounge credit", "ends_on": "2026-12-31"},
        {"key": "travel-credit", "name": "Yearly travel credit", "ends_on": "2026-12-31"},
    ]
    assert (out["to_add"], out["to_change"], out["unchanged"]) == ([], [], 1)
    r = apply(admin, body, out["base_sig"])
    assert r.json() == {"saved": 3, "added": 0, "changed": 0, "ended": 3, "unchanged": 1}
    night = edit_row(samples, "anniversary-night")
    assert (night["fields"], night["base"]) == (
        {"ends_on": "2026-12-31"},
        {"ends_on": "2027-03-31"},
    )
    assert edit_row(samples, "travel-credit")["fields"] == {"ends_on": "2026-12-31"}
    assert edit_row(samples, lounge)["fields"]["ends_on"] == "2026-12-31"
    status = edit_row(samples, "hotel-status")
    assert (status["fields"], status["hidden"]) == ({}, 1)
    assert edit_row(samples, "airline-fee-credit") is None
    assert page_benefit(admin, "travel-credit")["ends_on"] == "2026-12-31"
    assert preview(admin, body)["no_changes"]


def test_replace_mode_lists_an_end_the_edit_blocks(samples: Settings, admin: TestClient) -> None:
    edit(admin, "hotel-status", status_tier="Platinum")
    # the chat makes it another kind: the edit no longer fits (rule 9)
    send(samples, changed("hotel-status", kind="other", program_key=None, status_tier=None))
    out = preview(admin, file(DINING, mode="replace"))
    assert problems(out) == [("mode", "edit-no-fit")]
    assert out["problems"][0]["message"] == ri.NO_FIT_FIRST
    assert out["base_sig"] is None and out["to_end"] == []
    assert preview(admin, file(DINING))["no_changes"]  # merge leaves it alone


def test_a_file_never_changes_a_chat_benefits_kind_or_cadence(
    samples: Settings, admin: TestClient
) -> None:
    # a cadence or kind change is done in the app (End this
    # benefit, then Add a benefit). A file that tries is refused whole.
    cadence = DINING | {"cadence": "quarterly", "amount": "30.00", "match_terms": ["DINING CREDIT"]}
    kind = {"key": "hotel-status", "name": "Hotel status", "kind": "other", "note": "Ask the desk"}
    travel = chat_item(samples, "travel-credit") | {"note": "Book early"}
    sig = preview(admin, file(NEW))["base_sig"]
    for body, found in (
        (file(cadence), [("benefits[0].cadence", "file-kind-change")]),
        (file(kind), [("benefits[0].kind", "file-kind-change")]),
        # next to good items, and in replace mode: still nothing is written
        (file(NEW, cadence, mode="replace"), [("benefits[1].cadence", "file-kind-change")]),
        (file(travel, kind), [("benefits[1].kind", "file-kind-change")]),
    ):
        for r in (post_preview(admin, body), apply(admin, body, sig)):
            assert problems(r.json()) == found, r.text
            assert r.json()["problems"][0]["message"] == rf.FILE_RULES["file-kind-change"]
            for value in ("quarterly", "30.00", "Ask the desk", "Book early", "DINING CREDIT"):
                assert value not in r.text
        out = preview(admin, body)
        assert out["base_sig"] is None and not out["no_changes"]
        assert (out["to_add"], out["to_change"], out["to_end"]) == ([], [], [])
        assert apply(admin, body, sig).status_code == 422
    assert counts(samples) == dict.fromkeys(REWARD_TABLES, 0) and events(samples) == []
    # Done in the app instead, the card's list round trips.
    assert patch(admin, "dining-credit", set={"ends_on": "2026-12-31"}).status_code == 200
    made = add(admin, **LOUNGE | {"name": "Dining credit", "amount": "30.00"}).json()["key"]
    t = template(admin)
    assert made in {b["key"] for b in t["benefits"]}
    assert preview(admin, t)["no_changes"]


def test_an_app_key_takes_the_new_body(samples: Settings, admin: TestClient) -> None:
    key = add(admin, **LOUNGE).json()["key"]
    item = NEW | {"key": key, "amount": "30.00", "note": "Two visits"}
    out = preview(admin, file(item))
    assert out["to_change"] == [
        {
            "key": key,
            "name": "Lounge credit",
            "fields": ["amount", "note"],
            "amount_from": "2027-01-01",
            "terms": False,
        }
    ]
    assert applied(admin, file(item))["changed"] == 1
    row = edit_row(samples, key)
    assert (row["fields"]["amount"], row["fields"]["note"]) == ("30.00", "Two visits")
    assert (row["amount_before"], row["amount_from"]) == ("25.00", "2027-01-01")
    # sent as <key> it is the same benefit
    assert preview(admin, file(item | {"key": "lounge-credit"}))["no_changes"]


SPA = LOUNGE | {"name": "Spa credit", "cadence": "monthly", "amount": "10.00"}
SPA_YEARLY = {"name": "Spa credit", "kind": "credit", "cadence": "annual", "amount": "100.00"}


def test_a_file_never_changes_an_app_benefits_kind_or_cadence(
    samples: Settings, admin: TestClient
) -> None:
    # Codex found three more failures in the end plus add a file made of an
    # app key's cadence change, so a file refuses it too.
    key = add(admin, **SPA).json()["key"]
    assert key == "app-spa-credit"
    assert mark(admin, key, "2026-01-01", "10.00", "2026-01-12").status_code == 200
    # A calendar credit a file added from next year: nothing before its start.
    applied(admin, file(NEW))
    before = (counts(samples), edit_row(samples, key), edit_row(samples, "app-lounge-credit"))
    page_before = page(admin)
    yearly = SPA_YEARLY | {"key": key}
    perk = {"key": key, "name": "Spa credit", "kind": "other", "note": "Ask the spa"}
    lounge = NEW | {"key": "app-lounge-credit", "cadence": "annual", "amount": "90.00"}
    sig = preview(admin, file(NEW))["base_sig"]
    for body, found in (
        (file(yearly), [("benefits[0].cadence", "file-kind-change")]),
        (file(yearly | {"key": "spa-credit"}), [("benefits[0].cadence", "file-kind-change")]),
        (file(yearly, starts_on="2026-01-01"), [("benefits[0].cadence", "file-kind-change")]),
        (file(perk), [("benefits[0].kind", "file-kind-change")]),
        (file(lounge), [("benefits[0].cadence", "file-kind-change")]),
    ):
        for r in (post_preview(admin, body), apply(admin, body, sig)):
            assert problems(r.json()) == found, r.text
            for value in ("annual", "100.00", "90.00", "Ask the spa"):
                assert value not in r.text
        assert apply(admin, body, sig).status_code == 422
    after = (counts(samples), edit_row(samples, key), edit_row(samples, "app-lounge-credit"))
    assert after == before and page(admin) == page_before
    assert [e["entity"] for e in events(samples)].count("file") == 1  # the first file only


def test_a_new_key_is_app_key_cut_to_fit(samples: Settings, admin: TestClient) -> None:
    long = "a" * 20 + "-" + "b" * 19
    assert len(long) == KEY_MAX
    out = preview(admin, file(NEW | {"key": long}, NEW | {"key": "app-spa-credit"}))
    made = [a["key"] for a in out["to_add"]]
    assert made == ["app-" + long[: KEY_MAX - 4], "app-spa-credit"]
    for key in made:
        assert len(key) <= KEY_MAX and re.fullmatch(KEY_PATTERN, key)
    applied(admin, file(NEW | {"key": long}))
    assert preview(admin, file(NEW | {"key": long}))["no_changes"]


def test_match_terms_replace_the_words_only_when_sent(samples: Settings, admin: TestClient) -> None:
    assert put_terms(admin, "dining-credit", ["DINING CREDIT"]).status_code == 200
    assert preview(admin, file(DINING))["no_changes"]
    assert preview(admin, file(DINING | {"match_terms": None}))["no_changes"]
    assert preview(admin, file(DINING | {"match_terms": ["dining credit"]}))["to_change"] == [
        {
            "key": "dining-credit",
            "name": "Dining credit",
            "fields": [],
            "amount_from": None,
            "terms": True,
        }
    ]
    out = applied(admin, file(DINING | {"match_terms": []}))
    assert out == {"saved": 1, "added": 0, "changed": 1, "ended": 0, "unchanged": 0}
    assert words(samples) == {}
    assert edit_row(samples, "dining-credit") is None  # words alone make no edit row


# --------------------------------------------------------------------------
# Starts, amounts set back and words on other kinds
# --------------------------------------------------------------------------


def test_a_new_credit_from_a_later_day_counts_nothing_before(
    samples: Settings, admin: TestClient
) -> None:
    before = page(admin)["fees"]
    applied(admin, file(NEW))  # from 2027-01-01
    body = page(admin)
    lounge = ben(body, "app-lounge-credit")
    assert (lounge["group"], lounge["past_cycles"], lounge["cycle"]["start"]) == (
        "upcoming",
        [],
        "2027-01-01",
    )
    assert fee_credits(body, SUMMIT)["app-lounge-credit"]["full_cents"] == 0
    assert body["fees"]["credits_cents"] == before["credits_cents"]
    # From a day inside a quarter: that quarter starts on the day.
    spa = NEW | {"key": "spa-credit", "name": "Spa credit"}
    applied(admin, file(spa, starts_on="2026-08-15"))
    assert amounts(ben(page(admin), "app-spa-credit")) == [
        ("2026-08-15", 2_500),
        ("2026-10-01", 2_500),
    ]
    # The next year it is a plain calendar credit.
    with connect(samples.db_path) as conn:
        row = conn.execute(
            "SELECT * FROM reward_benefit_edits WHERE benefit_key = 'app-spa-credit'"
        ).fetchone()
    fields = json.loads(row["fields_json"])
    item = ov.app_benefit("app-spa-credit", SUMMIT, fields, None, None, 2027)
    assert [c["start"] for c in item["cycles"]] == [f"2027-{m:02d}-01" for m in (1, 4, 7, 10)]
    assert "starts_on" not in item


def test_a_later_add_of_any_kind_counts_nothing_this_year(
    samples: Settings, admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Codex: a benefit with a yearly value that a file adds from next year
    # counted in this year's Fees. Every add keeps its start, for every kind.
    before = page(admin)
    night = NIGHT | {"counted_value": "90.00"}
    perk = {"key": "spa-perk", "name": "Spa perk", "kind": "other", "counted_value": "40.00"}
    applied(admin, file(night, perk))  # from 2027-01-01
    for key in ("app-spa-night", "app-spa-perk"):
        assert edit_row(samples, key)["fields"]["starts_on"] == "2027-01-01"
    body = page(admin)
    fees = fee_credits(body, SUMMIT)
    assert (fees["app-spa-night"]["cents"], fees["app-spa-perk"]["cents"]) == (0, 0)
    assert (fees["app-spa-night"]["full_cents"], fees["app-spa-night"]["counted"]) == (0, True)
    assert body["fees"]["credits_cents"] == before["fees"]["credits_cents"]
    assert body["tiles"]["credits_left_cents"] == before["tiles"]["credits_left_cents"]
    # The start is the app's own: the template leaves it out and round trips.
    t = template(admin)
    assert all("starts_on" not in b for b in t["benefits"])
    assert preview(admin, t)["no_changes"]
    # A start inside this year counts this year.
    applied(admin, file(night | {"key": "lake-night"}, starts_on="2026-10-20"))
    assert fee_credits(page(admin), SUMMIT)["app-lake-night"]["cents"] == 9_000
    # Next year the later ones count their value.
    on(monkeypatch, date(2027, 2, 1))
    fees = fee_credits(page(admin), SUMMIT)
    assert (fees["app-spa-night"]["cents"], fees["app-spa-perk"]["cents"]) == (9_000, 4_000)


def test_a_later_credit_with_its_own_cycles_counts_nothing_this_year(
    samples: Settings, admin: TestClient
) -> None:
    before = page(admin)
    line = admin.get("/api/dashboard").json()["credits_ending"]
    # Its first cycle holds today and ends within 14 days. Without the start it
    # would count in Credits left, the Dashboard line and Fees.
    cycles = [
        {"start": "2025-11-01", "end": "2026-10-31"},
        {"start": "2026-11-01", "end": "2028-10-31"},
    ]
    entry = NEW | {"key": "entry-credit", "name": "Entry credit", "cadence": "multi_year"}
    entry |= {"amount": "100.00", "cycles": cycles}
    applied(admin, file(entry))  # from 2027-01-01
    body = page(admin)
    b = ben(body, "app-entry-credit")
    assert (b["group"], amounts(b)) == ("upcoming", [("2027-01-01", 10_000)])
    assert b["cycle"]["end"] == "2028-10-31"
    assert body["tiles"]["credits_left_cents"] == before["tiles"]["credits_left_cents"]
    assert admin.get("/api/dashboard").json()["credits_ending"] == line
    assert fee_credits(body, SUMMIT)["app-entry-credit"]["full_cents"] == 0
    assert body["fees"]["credits_cents"] == before["fees"]["credits_cents"]
    # The template keeps its own cycles and round trips.
    t = template(admin)
    assert next(x for x in t["benefits"] if x["key"] == "app-entry-credit")["cycles"] == cycles
    assert preview(admin, t)["no_changes"]
    # Cycles that all end before starts_on would leave nothing: a problem.
    gone = ONE_TIME | {
        "key": "gone-credit",
        "cycles": [{"start": "2026-01-01", "end": "2026-06-30"}],
    }
    assert problems(preview(admin, file(gone))) == [("benefits[0].cycles", "file-needs-cycles")]
    assert edit_row(samples, "app-gone-credit") is None


def test_an_in_year_credit_with_its_own_cycles_is_like_an_in_app_add(
    samples: Settings, admin: TestClient
) -> None:
    # Review: a multi-year or one-time credit a file added from a day this
    # year had its cycle cut there. Earlier credit rows stopped matching, so a
    # used credit showed as open in Credits left, and a cycle from years ago
    # counted its full amount in this year's Fees. It now keeps its cycles.
    once = {"name": "Once credit", "kind": "credit", "cadence": "one_time", "amount": "25.00"}
    once |= {"cycles": [{"start": "2026-01-01", "end": "2026-12-31"}]}
    entry = {"name": "Entry credit", "kind": "credit", "cadence": "multi_year"}
    entry |= {"amount": "120.00", "cycles": [{"start": "2023-01-01", "end": "2026-12-31"}]}
    keys = ("app-once-credit", "app-entry-credit")

    def seen() -> tuple:
        body = page(admin)
        fees = fee_credits(body, SUMMIT)
        return (
            [ben(body, k) for k in keys],
            [fees[k] for k in keys],
            body["fees"]["credits_cents"],
            body["tiles"]["credits_left_cents"],
            admin.get("/api/dashboard").json()["credits_ending"],
        )

    for item in (once, entry):
        assert add(admin, account_key=SUMMIT, **item).json()["key"] in keys
    assert put_terms(admin, keys[0], ["DINING CREDIT"]).status_code == 200
    in_app = seen()
    for k in keys:
        assert admin.delete(f"/api/rewards/benefits/{k}", headers=H).status_code == 204
    sent = (
        once | {"key": "once-credit", "match_terms": ["DINING CREDIT"]},
        entry | {"key": "entry-credit"},
    )
    applied(admin, file(*sent, starts_on="2026-10-20"))
    assert seen() == in_app
    for k in keys:  # no start kept: the cycles are the file's
        assert "starts_on" not in edit_row(samples, k)["fields"]
    b, e = in_app[0]
    assert (b["cycle"]["start"], b["cycle"]["source"], b["cycle"]["used_cents"]) == (
        "2026-01-01",
        "rows",
        8_000,
    )
    assert (b["cycle"]["left_cents"], e["cycle"]["start"]) == (0, "2023-01-01")
    assert in_app[1][1]["cents"] == 0  # its cycle counts in 2023
    # A one-time credit whose cycle ended this year records a past credit.
    past = once | {"key": "past-credit", "cycles": [{"start": "2026-01-01", "end": "2026-06-30"}]}
    applied(admin, file(past, starts_on="2026-10-20"))
    assert amounts(ben(page(admin), "app-past-credit")) == [("2026-01-01", 2_500)]


def test_new_cycles_that_end_before_the_start_are_a_problem(
    samples: Settings, admin: TestClient
) -> None:
    # Review: a file that changed the cycles of a credit a file added from a
    # later year could cut every cycle away. The credit then showed with no
    # cycle and counted nothing, with no word.
    cycles = [{"start": "2026-11-01", "end": "2028-10-31"}]
    entry = NEW | {"key": "entry-credit", "name": "Entry credit", "cadence": "multi_year"}
    entry |= {"amount": "100.00", "cycles": cycles}
    applied(admin, file(entry))  # from 2027-01-01
    row = edit_row(samples, "app-entry-credit")
    assert row["fields"]["starts_on"] == "2027-01-01"
    early = entry | {"key": "app-entry-credit"}
    early["cycles"] = [{"start": "2025-01-01", "end": "2026-06-30"}]
    body = file(early, starts_on="2026-10-20")
    out = preview(admin, body)
    assert problems(out) == [("benefits[0].cycles", "file-needs-cycles")]
    assert out["to_change"] == []
    r = apply(admin, body, preview(admin, file())["base_sig"])  # a clean check's sig
    assert r.status_code == 422
    assert [(p["where"], p["rule"]) for p in r.json()["problems"]] == problems(out)
    assert edit_row(samples, "app-entry-credit") == row
    assert amounts(ben(page(admin), "app-entry-credit")) == [("2027-01-01", 10_000)]
    # One cycle that ends on or after the start is enough.
    early["cycles"] = [{"start": "2025-01-01", "end": "2027-06-30"}]
    out = applied(admin, file(early, starts_on="2026-10-20"))
    assert out["changed"] == 1
    assert amounts(ben(page(admin), "app-entry-credit")) == [("2027-01-01", 10_000)]
    assert ben(page(admin), "app-entry-credit")["cycle"]["end"] == "2027-06-30"


def test_setting_the_amount_back_needs_the_earlier_start(
    samples: Settings, admin: TestClient
) -> None:
    # A chat key: $12 from 2026-10-01, then the file sends the chat's $10.
    edit(admin, "dining-credit", amount="12.00")
    out = preview(admin, file(DINING))  # from 2027-01-01
    assert problems(out) == [("benefits[0].amount", "amount-back")]
    assert out["problems"][0]["message"] == rf.FILE_RULES["amount-back"]
    assert amounts(page_benefit(admin, "dining-credit"))[-1] == ("2026-10-01", 1_200)
    out = preview(admin, file(DINING, starts_on="2026-10-01"))  # the change's own start
    assert out["to_change"] == [
        {
            "key": "dining-credit",
            "name": "Dining credit",
            "fields": ["amount"],
            "amount_from": None,
            "terms": False,
        }
    ]
    applied(admin, file(DINING, starts_on="2026-09-01"))
    assert {a for _, a in amounts(page_benefit(admin, "dining-credit"))} == {1_000}
    # An app key: $25, then $30 from 2026-07-01, then the file sends $25 again.
    key = add(admin, **LOUNGE).json()["key"]
    assert patch(admin, key, set={"amount": "30.00"}, amount_from="2026-07-01").status_code == 200
    item = NEW | {"key": key}
    assert problems(preview(admin, file(item))) == [("benefits[0].amount", "amount-back")]
    assert [a for _, a in amounts(page_benefit(admin, key))] == [2_500, 2_500, 3_000, 3_000]
    applied(admin, file(item, starts_on="2026-07-01"))
    assert [a for _, a in amounts(page_benefit(admin, key))] == [2_500] * 4


def test_words_go_on_credits_only(samples: Settings, admin: TestClient) -> None:
    # The chat makes a credit another kind: its words stay, but the template
    # leaves them out, so it round trips. An empty list clears them.
    assert put_terms(admin, "travel-credit", ["TRAVEL CREDIT"]).status_code == 200
    send(samples, changed("travel-credit", kind="other", cadence=None, amount=None, cycles=None))
    t = template(admin)
    by = {b["key"]: b for b in t["benefits"]}
    assert "match_terms" not in by["travel-credit"]
    assert preview(admin, t)["no_changes"]
    travel = by["travel-credit"] | {"match_terms": []}
    assert preview(admin, file(travel))["to_change"][0]["terms"] is True
    applied(admin, file(travel))
    assert words(samples) == {}


def test_a_new_key_never_takes_another_cards_app_key(samples: Settings, admin: TestClient) -> None:
    cobalt = add(admin, **LOUNGE | {"account_key": "cobalt-card"}).json()["key"]
    assert cobalt == "app-lounge-credit"
    out = preview(admin, file(NEW))
    assert problems(out) == []
    assert [a["key"] for a in out["to_add"]] == ["app-lounge-credit-2"]
    applied(admin, file(NEW))
    assert edit_row(samples, cobalt)["account_key"] == "cobalt-card"
    assert edit_row(samples, "app-lounge-credit-2")["account_key"] == SUMMIT
    assert preview(admin, file(NEW))["no_changes"]  # sent again it is the same benefit
    # Twice in one file is still twice.
    out = preview(admin, file(NEW | {"key": "spa-credit"}, NEW | {"key": "app-spa-credit"}))
    assert problems(out) == [("benefits[1].key", "file-key-twice")]


def test_a_huge_amount_writes_nothing_and_the_page_still_reads(
    samples: Settings, admin: TestClient
) -> None:
    body = file(NEW | {"amount": HUGE})
    assert problems(preview(admin, body)) == [("benefits[0].amount", "money-format")]
    sig = preview(admin, file(DINING))["base_sig"]
    assert apply(admin, body, sig).status_code == 422
    assert counts(samples) == dict.fromkeys(REWARD_TABLES, 0)
    for path in ("/api/rewards", "/api/rewards/review", "/api/dashboard", "/api/checks"):
        assert admin.get(path).status_code == 200, path


def test_text_follows_the_in_app_rules(samples: Settings, admin: TestClient) -> None:
    refused = {
        "c1": DINING | {"note": f"Ask for QX7\u0090B2K, call {PHONE[:3]}\u0090{PHONE[3:]}"},
        "c1-street": DINING | {"how_to_use": "42 Elm\u0099 Street"},
        "bidi": NEW | {"name": "Lounge \u202ecredit"},
        "zero-width": NEW | {"name": "\u200b"},
    }
    sig = preview(admin, file(DINING))["base_sig"]
    for item in refused.values():
        for r in (post_preview(admin, file(item)), apply(admin, file(item), sig)):
            assert r.json()["problems"], item
            for raw in ("QX7", "555", "Elm", "\u202e", "\u200b"):
                assert raw not in r.text
    assert counts(samples) == dict.fromkeys(REWARD_TABLES, 0)
    # Line breaks and runs of spaces fold to one space, as in the app. The
    # folded text is what is scanned and stored.
    applied(admin, file(DINING | {"note": "  Call\u2028ahead   first "}))
    assert edit_row(samples, "dining-credit")["fields"] == {"note": "Call ahead first"}
    assert preview(admin, file(DINING | {"note": "Call ahead first"}))["no_changes"]


# --------------------------------------------------------------------------
# Problems: a place and a rule, never a value
# --------------------------------------------------------------------------

NIGHT = {
    "key": "spa-night",
    "name": "Spa night",
    "kind": "free_night",
    "program_key": "lantern-hotels",
}
CAP = {
    "key": "grocery-cap",
    "name": "Grocery cap",
    "kind": "earning_cap",
    "cap": "500.00",
    "earn_before": "3x",
    "earn_after": "1x",
    "category_keys": ["groceries"],
}
ONE_TIME = NEW | {"cadence": "one_time", "cycles": [{"start": "2027-01-01", "end": "2027-12-31"}]}
STREAMING = {"key": "streaming-credit", "name": "Streaming credit", "kind": "credit"} | {
    "cadence": "quarterly",
    "amount": "15.00",
}
# Money the in-app rules refuse: past int()'s digit limit, and 10 million.
HUGE = "1" + "0" * 5_000 + ".00"
TEN_MILLION = "1" + "0" * 7 + ".00"


@pytest.mark.parametrize(
    ("body", "found"),
    [
        (
            file(DINING, NEW, DINING | {"key": "x-credit", "amount": "12"}),
            [("benefits[2].amount", "money-format")],
        ),
        (file(DINING | {"amount": "-1.00"}), [("benefits[0].amount", "money-sign")]),
        (file(DINING | {"QX7B2K": "x"}), [("benefits[0].(unknown field)", "schema")]),
        (file(DINING) | {"QX7B2K": 1}, [("(unknown field)", "schema")]),
        (file(DINING) | {"kind": "other"}, [("kind", "schema")]),
        (file(DINING) | {"version": 2}, [("version", "schema")]),
        (file(DINING, starts_on="2027-02-30"), [("starts_on", "date-format")]),
        (file(DINING, mode="all"), [("mode", "schema")]),
        (file(mode="replace"), [("benefits", "file-items")]),
        (file(*[NEW | {"key": f"credit-n{i}"} for i in range(51)]), [("benefits", "file-items")]),
        (file(DINING, "dining"), [("benefits[1]", "schema")]),
        (file(DINING, account_key="mystery-account"), [("account_key", "file-card")]),
        (file(DINING, account_key="harbor-checking"), [("account_key", "file-card")]),
        (file(DINING, account_key="check-car-123"), [("account_key", "key-format")]),
        (
            file(DINING | {"account_key": "cobalt-card"}),
            [("benefits[0].account_key", "file-other-card")],
        ),
        (
            file(
                NEW | {"cadence": "one_time", "cycles": [ONE_TIME["cycles"][0] | {"used": "5.00"}]}
            ),
            [("benefits[0].cycles[0].used", "upload-used")],
        ),
        (
            file(ONE_TIME | {"cycles": [ONE_TIME["cycles"][0] | {"used_on": "2027-02-01"}]}),
            [("benefits[0].cycles[0].used_on", "upload-used")],
        ),
        (
            file(DINING | {"cycles": [{"start": "2026-01-01", "end": "2026-01-31"}]}),
            [("benefits[0].cycles", "file-chat-cycles")],
        ),
        (file(NEW | {"cadence": "multi_year"}), [("benefits[0].cycles", "file-needs-cycles")]),
        (
            file(NEW | {"cycles": [{"start": "2027-01-01", "end": "2027-03-31"}]}),
            [("benefits[0].cycles", "file-no-cycles")],
        ),
        (
            file(ONE_TIME | {"cycles": ONE_TIME["cycles"] * 2}),
            [("benefits[0].cycles", "cycle-starts")],
        ),
        (file(DINING, DINING), [("benefits[1].key", "file-key-twice")]),
        (file(NEW, NEW | {"key": "app-lounge-credit"}), [("benefits[1].key", "file-key-twice")]),
        (file(STREAMING), [("benefits[0].key", "file-key-other-card")]),
        (
            file(CAP | {"category_keys": ["groceries", "no-such-category"]}),
            [("benefits[0].category_keys[1]", "file-category")],
        ),
        (
            file(NIGHT | {"program_key": "mystery-account"}),
            [("benefits[0].program_key", "file-program")],
        ),
        (file(NEW | {"amount": None}), [("benefits[0]", "reward-benefit-fields")]),
        (file(NEW | {"key": "Lounge Credit"}), [("benefits[0].key", "key-format")]),
        (file(NEW | {"auto": "yes"}), [("benefits[0].auto", "schema")]),
        (
            file(DINING | {"match_terms": ["CREDIT", "ab", "CAFE", "cafe"]}),
            [
                ("benefits[0].match_terms[0]", "term-generic"),
                ("benefits[0].match_terms[1]", "term-format"),
                ("benefits[0].match_terms[3]", "term-twice"),
            ],
        ),
        (
            file(DINING | {"match_terms": [f"CAFE {c}" for c in "ABCDEFGHIJK"]}),
            [("benefits[0].match_terms", "terms-format")],
        ),
        (file(DINING | {"match_terms": "CAFE"}), [("benefits[0].match_terms", "terms-format")]),
        (file(NIGHT | {"match_terms": ["SPA"]}), [("benefits[0].match_terms", "terms-credit")]),
        # The in-app add's rules: money, dates, names, text and the caps.
        (file(NEW | {"amount": HUGE}), [("benefits[0].amount", "money-format")]),
        (file(NEW | {"amount": TEN_MILLION}), [("benefits[0].amount", "money-format")]),
        (file(DINING | {"counted_value": HUGE}), [("benefits[0].counted_value", "money-format")]),
        (file(CAP | {"cap": TEN_MILLION}), [("benefits[0].cap", "money-format")]),
        (
            file(CAP | {"reported_spent": HUGE, "as_of": "2026-09-30"}),
            [("benefits[0].reported_spent", "money-format")],
        ),
        (
            file(ONE_TIME | {"cycles": [ONE_TIME["cycles"][0] | {"amount": HUGE}]}),
            [("benefits[0].cycles[0].amount", "money-format")],
        ),
        (file(DINING, starts_on="0001-01-01"), [("starts_on", "date-range")]),
        (file(DINING, starts_on="1999-12-31", mode="replace"), [("starts_on", "date-range")]),
        (file(DINING, starts_on="2100-01-01"), [("starts_on", "date-range")]),
        (file(DINING | {"ends_on": "1999-12-31"}), [("benefits[0].ends_on", "date-range")]),
        (file(NIGHT | {"enroll_by": "0001-01-01"}), [("benefits[0].enroll_by", "date-range")]),
        (
            file(ONE_TIME | {"cycles": [{"start": "2027-01-01", "end": "9999-12-31"}]}),
            [("benefits[0].cycles[0].end", "date-range")],
        ),
        (
            file(ONE_TIME | {"cycles": ONE_TIME["cycles"] * 21}),
            [("benefits[0].cycles", "file-cycles")],
        ),
        (
            file(CAP | {"category_keys": [f"cat-{c}" for c in "abcdefghijklmnopqrstu"]}),
            [("benefits[0].category_keys", "file-categories")],
        ),
        (file(NEW | {"name": "L" * 101}), [("benefits[0].name", "name-format")]),
        (file(NEW | {"name": "\u200b"}), [("benefits[0].name", "name-format")]),
        (file(NEW | {"name": "Lounge \u202ecredit"}), [("benefits[0].name", "name-format")]),
        (file(DINING | {"note": "Ask for QX7\u0090B2K"}), [("benefits[0].note", "text-format")]),
        (
            file(DINING | {"how_to_use": "Tap\u0085here"}),
            [("benefits[0].how_to_use", "text-format")],
        ),
    ],
)
def test_problems_carry_a_place_and_a_rule(
    samples: Settings, admin: TestClient, body: dict, found: list[tuple[str, str]]
) -> None:
    r = post_preview(admin, body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert problems(out) == found
    assert out["base_sig"] is None and not out["no_changes"]
    assert (out["to_add"], out["to_change"], out["to_end"], out["unchanged"]) == ([], [], [], 0)
    for p in out["problems"]:
        assert p["message"] and p["message"] == (
            rf.FILE_RULES.get(p["rule"]) or rf.RULES.get(p["rule"])
        )
    for value in (
        "QX7B2K",
        "mystery-account",
        "check-car-123",
        "no-such-category",
        "Lounge Credit",
    ):
        assert value not in r.text
    sig = preview(admin, file(DINING))["base_sig"]
    r = apply(admin, body, sig)
    assert r.status_code == 422
    assert r.json() == {"detail": rf.HAS_PROBLEMS, "problems": out["problems"]}
    assert counts(samples) == dict.fromkeys(REWARD_TABLES, 0)
    assert events(samples) == []


@pytest.mark.parametrize("raw", [b"", b"{not json", b"[]", b'"text"', b"\xff\xfe"])
def test_a_file_that_is_not_a_json_object(samples: Settings, admin: TestClient, raw: bytes) -> None:
    for path in (PREVIEW, APPLY):
        r = admin.post(path, content=raw, headers=H | {"Content-Type": "application/json"})
        assert r.status_code == (200 if path == PREVIEW else 422)
        assert problems(r.json()) == [("(file)", "json")]


def test_a_key_on_another_card_fails_the_file(samples: Settings, admin: TestClient) -> None:
    lounge = add(admin, **LOUNGE | {"account_key": "cobalt-card"}).json()["key"]
    out = preview(admin, file(DINING | {"note": "Fine"}, STREAMING, NEW | {"key": lounge}))
    assert problems(out) == [
        ("benefits[1].key", "file-key-other-card"),
        ("benefits[2].key", "file-key-other-card"),
    ]
    assert out["to_change"] == [] and out["base_sig"] is None


def test_an_amount_change_after_an_earlier_start_is_a_problem(
    samples: Settings, admin: TestClient
) -> None:
    edit(admin, "dining-credit", amount="12.00")  # starts 2026-10-01, this cycle
    out = preview(admin, file(DINING | {"amount": "14.00"}))
    assert problems(out) == [("benefits[0].amount", "amount-start")]
    assert out["problems"][0]["message"] == ri.AMOUNT_LATER
    assert (
        preview(admin, file(DINING | {"amount": "14.00"}, starts_on="2026-09-01"))["to_change"][0][
            "amount_from"
        ]
        == "2026-09-01"
    )


def test_a_key_in_use_takes_the_next_free_one(samples: Settings, admin: TestClient) -> None:
    with connect(samples.db_path) as conn, write_transaction(conn):
        conn.execute(
            """INSERT INTO reward_match_terms (benefit_key, term, updated_by, updated_at)
               VALUES ('app-spa-credit', 'SPA', NULL, ?)""",
            (NOW,),
        )
    # <key> is made into a free app key. A sent app- key is taken as it is.
    out = preview(admin, file(NEW | {"key": "spa-credit"}))
    assert [a["key"] for a in out["to_add"]] == ["app-spa-credit-2"]
    out = preview(admin, file(NEW | {"key": "app-spa-credit"}))
    assert problems(out) == [("benefits[0].key", "file-key-taken")]


# --------------------------------------------------------------------------
# All or nothing, and base_sig
# --------------------------------------------------------------------------


def test_a_bad_item_3_writes_nothing(
    samples: Settings, admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    good = file(
        DINING | {"note": "A", "match_terms": ["DINING CREDIT"]},
        NEW,
        chat_item(samples, "airline-fee-credit") | {"note": "B"},
        chat_item(samples, "travel-credit") | {"note": "C"},
    )
    sig = preview(admin, good)["base_sig"]
    bad = copy.deepcopy(good)
    bad["benefits"][3]["amount"] = "1e3"
    r = apply(admin, bad, sig)
    assert r.status_code == 422
    assert [(p["where"], p["rule"]) for p in r.json()["problems"]] == [
        ("benefits[3].amount", "money-format")
    ]
    assert counts(samples) == dict.fromkeys(REWARD_TABLES, 0) and events(samples) == []
    # A failure in the middle of the writes rolls every one of them back.
    raw = json.dumps(good | {"base_sig": sig}).encode()

    def fails(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(ri, "replace_terms", fails)
    conn = connect(samples.db_path)
    try:
        with pytest.raises(RuntimeError), write_transaction(conn):
            rf.apply(conn, raw, private=None, today=TODAY, user_id=None, now=NOW)
    finally:
        conn.close()
    assert counts(samples) == dict.fromkeys(REWARD_TABLES, 0) and events(samples) == []


def test_a_stale_base_sig_gives_409(samples: Settings, admin: TestClient) -> None:
    body = file(DINING | {"note": "New rules"})
    changes = [
        lambda: edit(admin, "travel-credit", note="Changed in between"),
        lambda: put_terms(admin, "travel-credit", ["TRAVEL"]),
        lambda: patch(admin, "hotel-status", hidden=True),
        lambda: add(admin, **LOUNGE),
        lambda: send(samples, changed("anniversary-night", note="Chat note")),
    ]
    for change in changes:
        sig = preview(admin, body)["base_sig"]
        change()
        r = apply(admin, body, sig)
        assert r.status_code == 409 and r.json()["detail"] == rf.STALE
        assert edit_row(samples, "dining-credit") is None
    assert apply(admin, body, "0" * 64).status_code == 409
    assert apply(admin, body, "not-a-sig").status_code == 422
    # a change on another card leaves the sig as it was
    sig = preview(admin, body)["base_sig"]
    edit(admin, "streaming-credit", note="Elsewhere")
    r = apply(admin, body, sig)
    assert r.status_code == 200 and r.json()["changed"] == 1
    assert edit_row(samples, "dining-credit")["fields"] == {"note": "New rules"}


# --------------------------------------------------------------------------
# The body: 256 KB at most, chunked bodies too
# --------------------------------------------------------------------------


def test_a_body_over_256_kb_is_413(samples: Settings, admin: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        yield b'{"kind": "budget-app-benefits", "benefits": [{"note": "'
        for _ in range(65):
            yield b"x" * (4 * 1024)
        yield b'"}]}'

    json_headers = H | {"Content-Type": "application/json"}
    for path in (PREVIEW, APPLY):
        r = admin.post(path, content=chunks(), headers=json_headers)
        assert r.request.headers.get("transfer-encoding") == "chunked"
        assert "content-length" not in r.request.headers
        assert r.status_code == 413 and r.json() == {"detail": "This file is too big."}
        big = json.dumps(file(DINING | {"note": "y" * rf.FILE_MAX_BYTES}))
        r = admin.post(path, content=big, headers=json_headers)
        assert r.status_code == 413 and r.json()["detail"] == rf.TOO_BIG
    # exactly 256 KB is read and checked
    text = json.dumps(file(DINING))
    exact = text + " " * (rf.FILE_MAX_BYTES - len(text))
    r = admin.post(PREVIEW, content=exact, headers=json_headers)
    assert len(exact) == rf.FILE_MAX_BYTES and r.status_code == 200 and r.json()["no_changes"]
    assert counts(samples) == dict.fromkeys(REWARD_TABLES, 0) and events(samples) == []


# --------------------------------------------------------------------------
# Safety: sessions, admins, text, events and logs
# --------------------------------------------------------------------------

FILE_ROUTES = {
    ("/api/rewards/benefit-files/preview", "POST"),
    ("/api/rewards/benefit-files/apply", "POST"),
    ("/api/rewards/benefit-files/template", "GET"),
}


def test_the_file_routes_need_a_session(prod_app, prod: TestClient) -> None:
    routes = {
        (path, m)
        for path, methods, _ in walk_routes(prod_app)
        if path.startswith("/api/rewards")
        for m in methods - {"HEAD", "OPTIONS"}
    }
    assert routes >= FILE_ROUTES
    for path, method in FILE_ROUTES:
        r = prod.request(method, path, json=file(DINING), headers=H)
        assert r.status_code == 401, (method, path)
    r = prod.get(TEMPLATE, params={"account_key": SUMMIT})
    assert r.status_code == 401


def test_members_get_403_on_preview_and_apply(samples: Settings, member: TestClient) -> None:
    for path in (PREVIEW, APPLY):
        r = member.post(path, json=file(DINING | {"note": "Mine"}), headers=H)
        assert r.status_code == 403 and r.json()["detail"] == "Only an admin can do this."
        big = b"x" * (rf.FILE_MAX_BYTES * 2)
        assert member.post(path, content=big, headers=H).status_code == 403  # never read
    assert [b["key"] for b in template(member)["benefits"]] == SUMMIT_KEYS
    assert counts(samples) == dict.fromkeys(REWARD_TABLES, 0) and events(samples) == []


def test_every_file_route_is_409_before_the_first_13_run(
    settings: Settings, admin: TestClient
) -> None:
    sig = "0" * 64
    for r in (
        post_preview(admin, file(DINING)),
        apply(admin, file(DINING), sig),
        admin.get(TEMPLATE, params={"account_key": SUMMIT}),
    ):
        assert r.status_code == 409 and r.json()["detail"] == ri.NO_REWARDS
    assert counts(settings) == dict.fromkeys(REWARD_TABLES, 0)


def test_private_text_is_refused_and_never_echoed_or_stored(
    use_private_rules: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = use_private_rules
    add_users(settings)
    import_samples(settings)
    flagged = _flagged()
    assert len(flagged) >= 12 and "QX7B2K" in flagged and "EXAMPLE PRIVATE SERVICE" in flagged
    app = app_for(monkeypatch, dev=False, BUDGET_PRIVATE_RULES=str(settings.private_rules))
    with TestClient(app, base_url=PROD_URL) as c:
        assert login(c, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
        exact = {
            "booking": (
                DINING | {"note": "Ask for QX7B2K"},
                [("benefits[0].note", "booking-code")],
            ),
            "private": (
                NEW | {"name": "EXAMPLE PRIVATE SERVICE pass"},
                [("benefits[0].name", "private-subscription")],
            ),
            "key": (
                NEW | {"key": "example-private-service"},
                [("benefits[0].key", "private-subscription")],
            ),
            "word": (
                DINING | {"match_terms": ["EXAMPLE PRIVATE SERVICE", "QX7B2K"]},
                [
                    ("benefits[0].match_terms[0]", "private-subscription"),
                    ("benefits[0].match_terms[1]", "booking-code"),
                ],
            ),
        }
        sig = preview(c, file(DINING))["base_sig"]
        for item, found in exact.values():
            r = post_preview(c, file(item))
            assert problems(r.json()) == found
            r = apply(c, file(item), sig)
            assert r.status_code == 422 and problems(r.json()) == found
        for value in [*flagged, "check-car-123", "mystery-account"]:
            texts = [
                DINING | {"note": f"Ask {value}"},
                DINING | {"how_to_use": value},
                NEW | {"name": f"Lounge {value}"},
                CAP | {"earn_before": value},
                DINING | {"match_terms": [value]},
            ]
            keys = [NIGHT | {"program_key": value}, CAP | {"category_keys": [value]}]
            for item in (texts if value in flagged else []) + keys:
                for r in (post_preview(c, file(item)), apply(c, file(item), sig)):
                    assert r.json()["problems"], (value, item)
                    assert value not in r.text and value.replace(" ", "") not in r.text
            for r in (
                post_preview(c, file(DINING, account_key=value)),
                apply(c, file(DINING, account_key=value), sig),
                post_preview(c, file(DINING) | {value: 1}),
                post_preview(c, file(DINING | {value: 1})),
            ):
                assert r.json()["problems"] and value not in r.text
    with read_connection(settings.db_path) as conn:
        stored = _text_columns(conn)
        dump = "\n".join(conn.iterdump())
    for value in [*flagged, "check-car-123", "mystery-account"]:
        assert value not in stored and value not in dump, value
    assert counts(settings) == dict.fromkeys(REWARD_TABLES, 0)


def test_a_made_key_is_scanned_too(samples: Settings) -> None:
    private = PrivateRules(subscriptions=("app lounge",))
    raw = json.dumps(file(NEW)).encode()
    conn = connect(samples.db_path)
    try:
        out = rf.preview(conn, raw, private=private, today=TODAY)
    finally:
        conn.close()
    assert problems(out) == [("benefits[0].key", "private-subscription")]
    assert "lounge" not in json.dumps(out["problems"])


EVENT_FIELDS = {"action", "added", "changed", "ended", "unchanged", "terms"}


def test_events_hold_counts_only(samples: Settings, admin: TestClient) -> None:
    applied(admin, file(DINING | {"note": "Riley's", "amount": "12.34"}, NEW | {"note": "Spa"}))
    item = DINING | {"amount": "31.37", "match_terms": ["DINING CREDIT"]}
    # the rest but the airline credit (it ends 2026-12-31 already) end
    applied(admin, file(item, mode="replace"))
    rows = events(samples)
    assert [(r["entity"], r["entity_key"]) for r in rows] == [("file", SUMMIT), ("file", SUMMIT)]
    details = [json.loads(r["detail_json"]) for r in rows]
    assert details == [
        {"action": "apply", "added": 1, "changed": 1, "ended": 0, "unchanged": 0, "terms": 0},
        {"action": "apply", "added": 0, "changed": 1, "ended": 4, "unchanged": 0, "terms": 1},
    ]
    for d in details:
        assert set(d) == EVENT_FIELDS
        assert all(isinstance(v, int) for k, v in d.items() if k != "action")
    text = "\n".join(r["detail_json"] for r in rows)
    for value in ("Riley", "12.34", "31.37", "Spa", "Dining credit", "DINING", "2027-01-01"):
        assert value not in text


def test_file_writes_outside_a_write_transaction_raise(samples: Settings) -> None:
    raw = json.dumps(file(DINING | {"note": "A"}) | {"base_sig": "0" * 64}).encode()
    conn = connect(samples.db_path)
    try:
        calls = [
            lambda: rf.apply(conn, raw, private=None, today=TODAY, user_id=None, now=NOW),
            lambda: ri.replace_terms(conn, "dining-credit", ["CAFE"], user_id=None, now=NOW),
            lambda: ri.save_benefit_row(
                conn, "dining-credit", "chat", SUMMIT, ri.row_state(None), user_id=None, now=NOW
            ),
            lambda: ri.record_event(conn, None, NOW, "file", SUMMIT, {"action": "apply"}),
        ]
        for call in calls:
            assert not conn.in_transaction
            with pytest.raises(RuntimeError):
                call()
    finally:
        conn.close()
    assert counts(samples) == dict.fromkeys(REWARD_TABLES, 0) and events(samples) == []


def test_logs_hold_no_values_and_no_body(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    add_users(settings)
    app = app_for(monkeypatch, dev=False)
    with TestClient(app, base_url=PROD_URL) as c:  # lifespan installs the JSON handler
        logging.getLogger().setLevel(logging.DEBUG)
        import_samples(settings)
        assert login(c, ADMIN, ADMIN_PW, PROD_ORIGIN).status_code == 200
        body = file(
            DINING | {"note": "Corner cafe", "amount": "31.37"}, NEW | {"name": "Lantern lounge"}
        )
        sig = preview(c, body)["base_sig"]
        assert apply(c, body, sig).status_code == 200
        post_preview(c, file(DINING | {"note": f"Ask {PHONE}"}))
        apply(c, file(DINING | {"note": f"Ask {PHONE}"}), sig)
        apply(c, body, sig)  # 409: stale now
        post_preview(c, file(DINING, account_key="mystery-account"))
        c.post(PREVIEW, content=b"z" * (rf.FILE_MAX_BYTES + 1), headers=H)  # 413
        template(c)
    fmt = JsonFormatter()
    text = capsys.readouterr().out + "\n".join(fmt.format(r) for r in caplog.records)
    for path in (PREVIEW, APPLY, TEMPLATE):
        assert f'"route": "{path}"' in text
    for value in (
        "31.37",
        "Corner cafe",
        "Lantern lounge",
        PHONE,
        "mystery-account",
        "budget-app-benefits",
        "dining-credit",
        "zzzz",
        "account_key",
        sig,
    ):
        assert value not in text, value
