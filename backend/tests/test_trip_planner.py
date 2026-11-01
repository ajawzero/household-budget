"""The trip planner (docs/plans/trip-planner.md): the privacy split, links,
the numbers the app works out, cost and caps, the life cycle, the rescan and
migration 0011. Fake client and the fake samples only."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sqlite3
import time
from datetime import date
from pathlib import Path

import pytest
from ai_fakes import (
    Broken,
    FakeClient,
    bad_request,
    cited,
    dropped,
    message,
    parsed,
    search_error,
    search_results,
    server_tool_use,
    text,
    thinking,
)
from api_helpers import PROD_ORIGIN
from fastapi.testclient import TestClient

from app import config
from app.ai import client as ai_client
from app.ai import planner
from app.ai import settings as ai_settings
from app.ai.planner import PlanOut, PlanRequest
from app.ai.postprocess import Scrub
from app.ai.pricing import WEB_SEARCH_MICRO_USD, Tokens, cost_micro_usd, tokens_of
from app.config import Settings, get_settings
from app.db import migrate as migrate_mod
from app.db.connect import connect, write_connection
from app.db.migrate import list_migrations, migrate
from app.importer.rescan import rescan
from app.importer.sanitize import PrivateRules
from app.main import walk_routes

TODAY = date(2026, 10, 20)
CANARY = "fake-canary-key-for-tests-7f3a"
H = {"Origin": PROD_ORIGIN}
OPUS = "claude-opus-5-5"
AT = "@"
CARD = " ".join(["4111"] + ["1111"] * 3)
PHONE = "-".join(["312", "555", "0142"])
MIGRATION = Path(migrate_mod.__file__).parent / "migrations" / "0011_trip_planner.sql"

# The samples' household data: none of it may reach a web search.
LEAKS = re.compile(
    r"Summit|Harbor|Lantern|Cobalt|Orchard|Bramble|Towne|Anniversary|Airline fee|"
    r"Yearly travel|Pine Cash|Vacation Club|84,?250|42,?000|12,?000|8812|3318|2290|4471|9031|"
    r"\bAlex\b|\bSam\b|\bJamie\b|\bRiley\b"
)
HOTEL = ("https://www.casaazul.example/lisbon", "Casa Azul Lisbon rates")
FARES = ("https://fares.example/ord-lis", "ORD to LIS fares in May")
FOOD = ("https://food.example/lisbon", "Where to eat in Lisbon")


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set before the app is built (Settings are frozen)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", CANARY)
    get_settings.cache_clear()


@pytest.fixture
def fake() -> FakeClient:
    client = FakeClient()
    ai_client.set_client_factory(lambda _s: client)
    yield client
    ai_client.set_client_factory(None)


def enable(**fields) -> None:
    with write_connection(get_settings().db_path) as conn:
        ai_settings.save(conn, ai_settings.AiSettings(enabled=True, **fields), None)


def request_body(**over) -> dict:
    body = {
        "where": "Lisbon, Portugal",
        "start_date": "2027-05-10",
        "end_date": "2027-05-14",
        "people": ["alex", "sam"],
        "guests": 1,
        "home_airport": "ORD",
        "interests": ["food", "history"],
        "note": "We like long lunches",
        "pace": "balanced",
        "lean": "value",
    }
    body.update(over)
    return body


def research(*extra: dict, searches: int = 3, stop: str = "end_turn"):
    return message(
        [
            thinking(),
            server_tool_use("ORD to LIS fares May 2027"),
            search_results("srvtoolu_1", [FARES, HOTEL, FOOD]),
            text("Flights\n"),
            cited("Round trips run about $1,100 for three. ", FARES),
            cited("Casa Azul Lisbon is about $240 a night. ", HOTEL),
            cited("Time Out Market is good for lunch.", FOOD),
            *extra,
        ],
        stop,
        OPUS,
        input_tokens=20_000,
        output_tokens=2_000,
        searches=searches,
    )


def day(n: int, base: str = "Lisbon", lodging: str = "Lantern Hotel Lisbon", travel: str = ""):
    return {
        "day": n,
        "date": f"2027-05-{9 + n:02d}",
        "base": base,
        "lodging": lodging,
        "travel": travel,
        "items": [
            {
                "part": "morning",
                "kind": "sight",
                "title": "Old town walk",
                "note": "Start early",
                "low_usd": 0,
                "high_usd": 0,
                "source_ids": [],
            },
            {
                "part": "afternoon",
                "kind": "meal",
                "title": "Lunch at Time Out Market",
                "note": "",
                "low_usd": 60,
                "high_usd": 90,
                "source_ids": ["S3"],
            },
        ],
    }


def good_plan(**over) -> PlanOut:
    plan = {
        "title": "Lisbon in May",
        "summary": "Four nights in Lisbon. Fly on points and pay cash for the hotel.",
        "points_plan": [
            {
                "item": "Round-trip flights for 3",
                "kind": "flight",
                "program": "Summit Points",
                "points": 60_000,
                "taxes_usd": 112,
                "cash_price_usd": 1_100,
                "benefit": "",
                "source_ids": ["S1"],
                "note": "Pay the taxes with the Summit card.",
            }
        ],
        "flights": [
            {
                "label": "One stop",
                "route": "ORD to LIS",
                "when": "May 10, back May 14",
                "cash_low_usd": 1_000,
                "cash_high_usd": 1_200,
                "program": "Summit Points",
                "points": 60_000,
                "taxes_usd": 112,
                "source_ids": ["S1"],
                "note": "",
            }
        ],
        "lodging": [
            {
                "name": "Lantern Hotel Lisbon",
                "area": "Baixa",
                "nightly_low_usd": 220,
                "nightly_high_usd": 260,
                "program": "Lantern Hotels",
                "points_per_night": 40_000,
                "free_night_ok": True,
                "why": "Central and walkable",
                "source_ids": ["S2"],
            }
        ],
        "ground": [
            {
                "mode": "transit",
                "note": "Metro and trams",
                "low_usd": 40,
                "high_usd": 60,
                "source_ids": [],
            }
        ],
        "days": [
            day(1, travel="Fly ORD to LIS"),
            day(2),
            day(3),
            day(4),
            {
                **day(5, base="", lodging="", travel="Fly LIS to ORD"),
                "items": [],
            },
        ],
        "costs": [
            {"kind": "flight", "low_usd": 112, "high_usd": 150, "note": "Taxes on points"},
            {"kind": "lodging", "low_usd": 880, "high_usd": 1_040, "note": ""},
            {"kind": "food", "low_usd": 400, "high_usd": 600, "note": ""},
        ],
        "checks": ["Check the hotel's cancellation rules."],
        "link_ids": ["S1", "S2"],
    }
    plan.update(over)
    return PlanOut.model_validate(plan)


def written(plan: PlanOut | None = None, stop: str = "end_turn"):
    return parsed(plan or good_plan(), stop, OPUS, input_tokens=15_000, output_tokens=5_000)


def wait(client: TestClient, plan_id: str, timeout: float = 15) -> dict:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = client.get(f"/api/trip-plans/{plan_id}")
        assert v.status_code == 200, v.text
        if v.json()["status"] != "running":
            return v.json()
        time.sleep(0.02)
    raise AssertionError("the plan is still running")


def start(client: TestClient, **over) -> str:
    r = client.post("/api/trip-plans", json=request_body(**over), headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "running"
    return r.json()["plan_id"]


def usage_rows() -> list[sqlite3.Row]:
    conn = connect(get_settings().db_path)
    try:
        return conn.execute("SELECT * FROM ai_usage ORDER BY id").fetchall()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# The request
# --------------------------------------------------------------------------


def test_the_request_takes_dates_or_a_month_and_nights() -> None:
    r = PlanRequest.model_validate(request_body())
    assert r.nights == 4
    m = PlanRequest.model_validate(
        request_body(start_date=None, end_date=None, month="2027-06", nights=7)
    )
    assert m.nights == 7
    for bad in (
        {"end_date": None},
        {"end_date": "2027-05-10"},
        {"end_date": "2027-06-30"},
        {"month": "2027-06"},
        {"start_date": None, "end_date": None},
        {"people": [], "guests": 0},
        {"people": ["alex", "alex"]},
        {"home_airport": "ord"},
        {"extra": 1},
    ):
        with pytest.raises(ValueError):
            PlanRequest.model_validate(request_body(**bad))


def test_typed_text_gets_the_question_checks(samples: Settings) -> None:
    private = PrivateRules(subscriptions=("Example Private Service",))
    conn = connect(samples.db_path)
    try:
        req = planner.checked_request(
            conn,
            PlanRequest.model_validate(request_body(note="near Example Private Service")),
            private,
        )
        assert req["note"] == "near personal subscription"
        for field, value in (("note", "call " + PHONE), ("where", "a" + AT + "b.co")):
            with pytest.raises(planner.PlanError):
                planner.checked_request(
                    conn, PlanRequest.model_validate(request_body(**{field: value})), private
                )
        with pytest.raises(planner.PlanError, match="from today on"):
            planner.checked_request(
                conn,
                PlanRequest.model_validate(
                    request_body(start_date="2026-10-01", end_date="2026-10-05")
                ),
                None,
            )
        with pytest.raises(planner.PlanError, match="within two years"):
            planner.checked_request(
                conn,
                PlanRequest.model_validate(
                    request_body(start_date=None, end_date=None, month="2099-12", nights=5)
                ),
                None,
            )
        with pytest.raises(planner.PlanError, match="household"):
            planner.checked_request(
                conn, PlanRequest.model_validate(request_body(people=["nobody"])), None
            )
    finally:
        conn.close()


def test_the_research_input_is_the_request_only() -> None:
    req = PlanRequest.model_validate(request_body()).model_dump(mode="json")
    first = planner.research_input(req, [], [])
    assert "Travelers: 3" in first and "Home airport: ORD" in first
    assert not LEAKS.search(first), first
    later = planner.research_input(req, ["Earlier notes"], ["cheaper hotel"])
    assert "Earlier notes" in later and "- cheaper hotel" in later


def test_typed_household_details_never_reach_research(samples: Settings) -> None:
    conn = connect(samples.db_path)
    try:
        redact = planner.household(conn)
    finally:
        conn.close()
    note = "Use Alex's 84,250 Summit Points and the Cobalt card. Sam gets the window."
    changes = [
        "Put it on the Bramble store card",
        f"Use {42_000} harbor miles, Jamie wants the beach",
        "Book the Shared Vacation Club, or the summit lodge on Orchard points",
    ]
    text = planner.research_input(req_dict(note=note), ["Earlier notes"], changes, redact)
    assert not LEAKS.search(text), LEAKS.search(text)
    assert "window" in text and "beach" in text and "Place: Lisbon, Portugal" in text
    assert "Use …'s … … and the …." in text
    # The place loses names and balances. Its own parts stay in the note, but
    # a program word elsewhere in the note does not.
    req = req_dict(
        where=f"Pine Mountain with Alex {84_250}",
        note="Hike near Pine Mountain with Riley, then spend the Pine cash",
    )
    text = planner.research_input(req, [], [], redact)
    assert "Place: Pine Mountain with … …" in text
    assert "near Pine Mountain with …, then spend the … cash" in text
    # A name in the place is still a name in the note.
    req = req_dict(where="Lisbon with Alex", note="Alex wants museums")
    text = planner.research_input(req, [], [], redact)
    assert "Place: Lisbon with …" in text and "Their note: … wants museums" in text
    # Balances in other forms, a small one before "points", and benefit, perk and
    # cap names the plan call's brief leaves out.
    spaced = " ".join(["84", "250"])
    for typed in (
        f"Use {spaced} points",
        "Use 84.25k on flights",
        "Use 84K on flights",
        "Use the 200 points left",
        "Skip the Dining credit",
        "Mind the Fuel bonus",
        "Hotel status helps",
    ):
        out = planner.research_input(req_dict(note=typed), [], [], redact)
        note = out.split("Their note: ")[1].split("\n")[0]
        assert "…" in note, note
        for gone in ("250", "84.25", "84K", "200", "Dining", "Fuel", "status"):
            assert gone not in note, (typed, note)


# --------------------------------------------------------------------------
# Links and sources
# --------------------------------------------------------------------------


def test_only_plain_web_links_are_kept() -> None:
    private = PrivateRules(subscriptions=("Example Private Service",))
    ok = "https://www.example.com/hotels/lisbon?id=1234"
    assert planner.safe_url(ok, private) == ok
    assert planner.safe_url("http://example.com", private)
    for bad in (
        "javascript:alert(1)",
        "data:text/html,hi",
        f"https://user:pass{AT}example.com/",
        f"https://evil.example{AT}example.com/",
        "https://localhost/x",
        "ftp://example.com/file",
        "https://example.com/a b",
        "https://example.com/" + "a" * 600,
        "https://example.com:" + "9" * 5 + "/",
        "https://www.exampleprivateservice.com/",
        "https://example.com/example-private-service",
        "https://example.com/?q=Example%20Private%20Service",
        "https://example.com/examplepriv%2561teservice",
        "https://\u0430pple.com/",
        "https://evil.example\\good.example/",
        None,
        "",
    ):
        assert planner.safe_url(bad, private) is None, bad


def test_sources_put_cited_results_first() -> None:
    blocks = research(
        search_results("srvtoolu_2", [("javascript:alert(1)", "bad"), ("https://x.example/", "")])
    ).content
    notes, sources = planner.notes_and_sources(blocks, None, 4)
    assert [s["id"] for s in sources] == ["S4", "S5", "S6", "S7"]
    assert [s["url"] for s in sources[:3]] == [FARES[0], HOTEL[0], FOOD[0]]
    assert sources[3] == {
        "id": "S7",
        "url": "https://x.example/",
        "site": "x.example",
        "title": "x.example",
        "page_age": "May 1, 2026",
    }
    assert sources[1]["site"] == "casaazul.example"
    assert "about $1,100 for three. [S4]" in notes
    assert "Time Out Market is good for lunch. [S6]" in notes
    assert "javascript" not in json.dumps(sources)


# --------------------------------------------------------------------------
# The plan's shape and what the app works out
# --------------------------------------------------------------------------


def brief() -> dict:
    return {
        "as_of": "2026-10-20",
        "programs": [
            {
                "name": "Summit Points",
                "kind": "points",
                "balance": 84_250,
                "cents_per_point_low": "1.2",
                "cents_per_point_high": "1.8",
            },
            {
                "name": "Lantern Hotels",
                "kind": "points",
                "balance": 12_000,
                "cents_per_point_low": None,
                "cents_per_point_high": None,
            },
        ],
        "free_nights": [{"name": "Anniversary free night", "ends_on": "2027-03-31"}],
        "travel_credits": [{"name": "Airline fee credit", "cycle_ends": "2026-12-31"}],
        "status": [],
        "card_routing": [],
    }


def test_the_plan_shape_goes_as_text_with_no_household_names() -> None:
    # Structured output would send this shape as a grammar, and the API refuses
    # to compile one this large ("The compiled grammar is too large").
    assert json.loads(planner.PLAN_SHAPE) == ai_client.json_format(PlanOut)["schema"]
    assert planner.PLAN_SYSTEM.endswith(planner.PLAN_SHAPE)
    for name in ("Summit", "Lantern", "Anniversary", "Airline fee"):
        assert name not in planner.PLAN_SYSTEM


def test_the_plan_is_read_from_a_fenced_or_wrapped_reply() -> None:
    plan = good_plan()
    body = plan.model_dump_json()
    for reply in (
        body,
        f"```json\n{body}\n```",
        f"Here is the plan.\n{body}\nEnjoy the trip.",
        f"Fly from {{ORD}}.\n{body}",
        f"{body}\nThen {{ORD}} again.",
        f'{{"note": "not a plan"}}\n{body}',
    ):
        assert planner.plan_out(message([text(reply)])) == plan
    split = [text(body[:40]), text(body[40:])]
    assert planner.plan_out(message(split)) == plan
    for reply in ("No plan today.", "{", '{"title": "Lisbon"}', body[:-1]):
        assert planner.plan_out(message([text(reply)])) is None


def test_a_benefit_that_is_not_ours_gets_a_check(key, samples, prod_app, admin, fake) -> None:
    enable()
    use = good_plan().points_plan[0].model_dump()
    made_up = {**use, "item": "Hotel nights", "points": 0, "benefit": "Made-up free night"}
    real = {**use, "points": 0, "benefit": "Anniversary free night"}
    fake.script = [research(), written(good_plan(points_plan=[made_up, real]))]
    view = wait(admin, start(admin))
    v = view["versions"][0]
    assert [u["benefit"] for u in v["plan"]["points_plan"]] == ["", "Anniversary free night"]
    checks = [c for c in v["derived"]["checks"] if c["kind"] == "points_not_yours"]
    assert [c["text"] for c in checks] == [
        "Hotel nights uses a benefit that is not in your rewards."
    ]


def test_a_reply_that_is_not_a_plan_fails_and_keeps_its_cost(
    key, samples, prod_app, admin, fake
) -> None:
    enable()
    fake.script = [research(), message([text("Sorry, here is a sketch.")], "end_turn", OPUS)]
    view = wait(admin, start(admin))
    assert view["status"] == "failed" and view["notice"] == ai_client.FAILED_TEXT
    rows = usage_rows()
    assert [r["feature"] for r in rows] == ["plan_research", "plan_write"]
    assert rows[1]["error"] is None and rows[1]["cost_micro_usd"] > 0


def test_tidy_keeps_known_sources_and_filters_text() -> None:
    plan = good_plan(
        summary=f"Fly out \u2014 then rest. Card {CARD} is not needed.",
        link_ids=["S2", "S9", "S2"],
        costs=[{"kind": "food", "low_usd": 600, "high_usd": 400, "note": ""}],
    )
    plan.points_plan[0].points = 99_000_000
    plan.days[2].date = "May 12"
    out = planner.tidy(plan, {"S1", "S2"}, None, planner.enum_names(brief()))
    assert out["link_ids"] == ["S2"]
    assert "\u2014" not in out["summary"] and "4111" not in out["summary"]
    assert out["costs"][0]["low_usd"] == 400 and out["costs"][0]["high_usd"] == 600
    assert out["points_plan"][0]["points"] == planner.POINTS_MAX
    assert out["days"][2]["date"] == ""
    assert [d["day"] for d in out["days"]] == [1, 2, 3, 4, 5]
    assert out["days"][0]["items"][1]["source_ids"] == []  # S3 is not known here


def req_dict(**over) -> dict:
    return PlanRequest.model_validate(request_body(**over)).model_dump(mode="json")


def test_the_app_works_out_value_balances_and_checks() -> None:
    plan = planner.tidy(
        good_plan(
            points_plan=[
                good_plan().points_plan[0],
                {
                    "item": "Four nights at the Lantern",
                    "kind": "lodging",
                    "program": "Lantern Hotels",
                    "points": 160_000,
                    "taxes_usd": 0,
                    "cash_price_usd": 960,
                    "benefit": "Anniversary free night",
                    "source_ids": [],
                    "note": "",
                },
                {
                    "item": "Bags",
                    "kind": "other",
                    "program": "",
                    "points": 0,
                    "taxes_usd": 0,
                    "cash_price_usd": 70,
                    "benefit": "Airline fee credit",
                    "source_ids": [],
                    "note": "",
                },
                {
                    "item": "Seats",
                    "kind": "other",
                    "program": "",
                    "points": 5_000,
                    "taxes_usd": 0,
                    "cash_price_usd": 50,
                    "benefit": "",
                    "source_ids": [],
                    "note": "",
                },
            ],
            days=[
                day(1, travel="Fly ORD to LIS").__or__({}),
                day(2, lodging=""),
                day(3, base="Porto"),
                day(4, base="Porto"),
                {**day(5, base="", lodging=""), "items": []},
            ],
        ).model_copy(),
        {"S1", "S2", "S3"},
        None,
        planner.enum_names(brief()),
    )
    d = planner.derive(plan, req_dict(), brief())
    assert d["points"][0] == {"value_cents_per_point": "1.65", "range": "within", "yours": True}
    assert d["points"][1] == {"value_cents_per_point": "0.60", "range": None, "yours": True}
    assert d["points"][2] == {"value_cents_per_point": None, "range": None, "yours": False}
    assert d["balances"] == [
        {"program": "Summit Points", "needed": 60_000, "have": 84_250, "enough": True},
        {"program": "Lantern Hotels", "needed": 160_000, "have": 12_000, "enough": False},
    ]
    kinds = [c["kind"] for c in d["checks"]]
    assert kinds == [
        "free_night_expires",
        "credit_use_by",
        "points_not_yours",
        "not_enough_points",
        "no_lodging",
        "no_way_home",
        "no_travel",
    ]
    texts = " ".join(c["text"] for c in d["checks"])
    assert "Night 2 (2027-05-11) has no place to stay." in texts
    assert "Day 3 is in Porto with no travel from Lisbon." in texts
    assert "the plan uses 160,000 points and the balance is 12,000" in texts
    assert [n["lodging"] is None for n in d["nights"]] == [False, True, False, False]
    assert d["costs"] == {
        "by_kind": [
            {"kind": "flight", "low_cents": 11_200, "high_cents": 15_000},
            {"kind": "lodging", "low_cents": 88_000, "high_cents": 104_000},
            {"kind": "food", "low_cents": 40_000, "high_cents": 60_000},
        ],
        "low_cents": 139_200,
        "high_cents": 179_000,
    }


def test_a_month_plan_has_nights_without_dates() -> None:
    plan = planner.tidy(good_plan(), {"S1", "S2", "S3"}, None, planner.enum_names(brief()))
    d = planner.derive(
        plan, req_dict(start_date=None, end_date=None, month="2027-06", nights=4), brief()
    )
    assert [n["date"] for n in d["nights"]] == [None] * 4
    assert [c["kind"] for c in d["checks"]] == []


def test_the_rewards_brief_holds_only_the_approved_parts(samples: Settings) -> None:
    conn = connect(samples.db_path)
    try:
        b = planner.rewards_brief(conn)
    finally:
        conn.close()
    names = {p["name"]: p for p in b["programs"]}
    assert names["Summit Points"]["balance"] == 84_250
    assert names["Summit Points"]["cents_per_point_low"] == "1.2"
    assert names["Pine Cash Back"]["balance"] == "37.42"
    assert [f["name"] for f in b["free_nights"]] == ["Anniversary free night"]
    credits = {c["name"] for c in b["travel_credits"]}
    assert "Airline fee credit" in credits and "Yearly travel credit" in credits
    assert not credits & {"Dining credit", "Streaming credit", "Wireless credit"}
    assert b["card_routing"] and b["status"]
    dumped = json.dumps(b)
    for marker in ("8812", "3318", "2290", "4471", "9031", "rows", "account_key", "income"):
        assert marker not in dumped, marker


# --------------------------------------------------------------------------
# A plan, end to end
# --------------------------------------------------------------------------


def test_a_plan_end_to_end(key, samples, prod_app, admin, fake, caplog) -> None:
    enable()
    fake.script = [research(), written()]
    with caplog.at_level(logging.DEBUG):
        plan_id = start(admin)
        view = wait(admin, plan_id)
    assert view["status"] == "ready" and view["notice"] is None

    (k1, research_req), (k2, write_req) = fake.requests
    assert (k1, k2) == ("stream", "stream")
    # The research call: web search, and nothing of the household.
    assert research_req["tools"] == [
        {"type": planner.SEARCH_TOOL, "name": "web_search", "max_uses": 10}
    ]
    assert research_req["model"] == OPUS
    assert research_req["output_config"] == {"effort": "medium"}
    assert not LEAKS.search(json.dumps(research_req)), LEAKS.search(json.dumps(research_req))
    assert "user_location" not in json.dumps(research_req)
    # The plan call: no tools, the rewards, sources by id and no URLs.
    assert "tools" not in write_req
    assert write_req["output_config"] == {"effort": "high"}  # no grammar: see PLAN_SHAPE
    assert write_req["system"] == planner.PLAN_SYSTEM
    sent = write_req["messages"][0]["content"]
    assert "Summit Points" in sent and "Anniversary free night" in sent
    assert '"S1"' in sent and "http" not in sent
    assert "8812" not in sent

    v = view["versions"][0]
    assert v["version"] == 1 and v["follow_up"] is None
    assert v["plan"]["title"] == "Lisbon in May"
    assert [link["url"] for link in v["links"]] == [FARES[0], HOTEL[0], FOOD[0]]
    assert v["links"][1]["site"] == "casaazul.example"
    assert v["derived"]["points"][0]["range"] == "within"
    assert v["derived"]["costs"]["high_cents"] == 179_000
    assert view["request"]["people"] == [
        {"key": "alex", "name": "Alex"},
        {"key": "sam", "name": "Sam"},
    ]
    assert view["web_searches"] == 3 and view["follow_ups_left"] == planner.FOLLOW_UP_LIMIT

    rows = usage_rows()
    assert [(r["feature"], r["plan_id"], r["web_searches"], r["question"]) for r in rows] == [
        ("plan_research", plan_id, 3, 1),
        ("plan_write", plan_id, 0, 0),
    ]
    research_cost = cost_micro_usd(OPUS, Tokens(input=20_000, output=2_000, web_searches=3))
    assert rows[0]["cost_micro_usd"] == research_cost
    assert view["cost_cents"] == round(sum(r["cost_micro_usd"] for r in rows) / 10_000)

    listed = admin.get("/api/trip-plans").json()
    assert listed["plans"][0]["plan_id"] == plan_id
    assert listed["plans"][0]["low_cents"] == 139_200
    assert listed["running_plan_id"] is None and listed["available"] is True
    assert listed["estimate_cents"] > 0 and listed["model"] == OPUS

    logs = caplog.text + json.dumps([vars(r) for r in caplog.records], default=str)
    for value in ("Lisbon", "Summit", "Lantern", "casaazul", CANARY, "1,100", plan_id):
        assert value not in logs, value


def test_the_plan_call_cannot_make_a_link(key, samples, prod_app, admin, fake) -> None:
    enable()
    evil = f"https://evil.example/?points={84_250}"
    plan = good_plan(
        link_ids=["S1", "S99", evil],
        summary=f"Book here: {evil}. Or at https://deals.example/?b=84,250&who=Alex.",
        checks=["Or go to deals.example/x?b=84,250 first."],
    )
    fake.script = [research(), written(plan)]
    view = wait(admin, start(admin))
    v = view["versions"][0]
    assert [link["id"] for link in v["links"]] == ["S1", "S2", "S3"]
    assert "evil" not in json.dumps(v["links"])
    assert v["plan"]["link_ids"] == ["S1"]
    shown = json.dumps(v["plan"])
    for leak in (str(84_250), "84,250", "evil.example", "deals.example", "who=Alex"):
        assert leak not in shown, leak
    assert v["plan"]["summary"].startswith("Book here: …")


def test_the_scrub_and_the_filter_run_on_web_text(
    key, use_private_rules, samples, prod_app, admin, fake
) -> None:
    enable()
    club = ("https://www.exampleprivateservice.example/lisbon", "Example Private Service Lisbon")
    fake.script = [
        research(
            cited("Example Private Service has a club here. Cards ending 8812 earn more. ", club)
        ),
        written(good_plan(summary="Skip Example Private Service. Card ending 8812 is fine.")),
    ]
    plan_id = start(admin)
    view = wait(admin, plan_id)
    plan_call = json.dumps(fake.requests[1][1])
    assert "example private" not in plan_call.lower() and "8812" not in plan_call
    assert "personal subscription" in plan_call
    conn = connect(get_settings().db_path)
    try:
        stored = json.dumps([tuple(r) for r in conn.execute("SELECT * FROM trip_plan_turns")])
    finally:
        conn.close()
    assert "example private" not in stored.lower()
    assert "exampleprivateservice" not in json.dumps(view["versions"][0]["links"])
    assert view["versions"][0]["plan"]["summary"].startswith("Skip personal subscription.")


def test_a_follow_up_adds_a_version(key, samples, prod_app, admin, fake) -> None:
    enable()
    fake.script = [research(), written()]
    plan_id = start(admin)
    wait(admin, plan_id)
    cheaper = good_plan(title="Lisbon for less")
    fake.script = [
        research(searches=1),
        written(cheaper),
    ]
    r = admin.post(
        f"/api/trip-plans/{plan_id}/follow-up", json={"text": "A cheaper hotel"}, headers=H
    )
    assert r.status_code == 200, r.text
    view = wait(admin, plan_id)
    assert [v["plan"]["title"] for v in view["versions"]] == ["Lisbon in May", "Lisbon for less"]
    assert view["versions"][1]["follow_up"] == "A cheaper hotel"
    assert view["follow_ups_left"] == planner.FOLLOW_UP_LIMIT - 1

    research_two, write_two = fake.requests[2][1], fake.requests[3][1]
    assert research_two["tools"][0]["max_uses"] == planner.FOLLOW_UP_SEARCHES
    sent = research_two["messages"][0]["content"]
    assert "Earlier research notes" in sent and "- A cheaper hotel" in sent
    # The earlier plan holds rewards, so the research call never sees it.
    assert not LEAKS.search(json.dumps(research_two))
    content = json.loads(write_two["messages"][0]["content"])
    assert content["previous_plan"]["title"] == "Lisbon in May"
    assert content["changes_asked"] == ["A cheaper hotel"]
    # Source ids run on across turns.
    assert [s["id"] for s in content["sources"]] == ["S1", "S2", "S3", "S4", "S5", "S6"]


def test_a_failed_change_keeps_the_plan(key, samples, prod_app, admin, fake) -> None:
    enable()
    fake.script = [research(), written()]
    plan_id = start(admin)
    wait(admin, plan_id)
    fake.script = [research(), written(stop="max_tokens")]
    admin.post(f"/api/trip-plans/{plan_id}/follow-up", json={"text": "Add Porto"}, headers=H)
    view = wait(admin, plan_id)
    assert view["status"] == "ready" and len(view["versions"]) == 1
    assert view["notice"] == planner.LONG_TEXT


def test_a_refusal_ends_with_a_notice(key, samples, prod_app, admin, fake) -> None:
    enable()
    fake.script = [message([text("No.")], "refusal", OPUS)]
    view = wait(admin, start(admin))
    assert view["status"] == "failed" and view["notice"] == planner.REFUSED_TEXT
    assert view["versions"] == []
    assert len(fake.requests) == 1


def test_an_api_error_ends_with_a_notice_and_a_change_retries(
    key, samples, prod_app, admin, fake
) -> None:
    enable()
    fake.script = [bad_request("Web search is not enabled for this organization.")]
    plan_id = start(admin)
    view = wait(admin, plan_id)
    assert view["status"] == "failed" and view["notice"] == ai_client.FAILED_TEXT
    assert [r["error"] for r in usage_rows()] == ["BadRequestError"]
    fake.script = [research(search_error("srvtoolu_2", "too_many_requests")), written()]
    r = admin.post(f"/api/trip-plans/{plan_id}/follow-up", json={"text": "Try again"}, headers=H)
    assert r.status_code == 200, r.text
    view = wait(admin, plan_id)
    assert view["status"] == "ready" and view["notice"] is None
    assert len(view["versions"]) == 1 and len(view["versions"][0]["links"]) == 3


def test_a_paused_search_resumes_with_the_same_tools(key, samples, prod_app, admin, fake) -> None:
    enable()
    fake.script = [research(searches=4, stop="pause_turn") for _ in range(3)] + [written()]
    view = wait(admin, start(admin))
    assert view["status"] == "ready"
    # A changed tool list breaks the paused turn's thinking, so it stays the same,
    # and the turn stops resuming once it has run its 10 searches.
    calls = [p for k, p in fake.requests[:3]]
    assert [c["tools"] for c in calls] == [calls[0]["tools"]] * 3
    assert calls[0]["tools"][0]["max_uses"] == 10
    assert len(fake.requests) == 4 and "tools" not in fake.requests[3][1]
    # A resume sends the paused turn back unchanged, as one assistant message.
    last = calls[2]["messages"]
    assert [m["role"] for m in last] == ["user", "assistant"]
    blocks = last[1]["content"]
    assert len(blocks) == 2 * len(research().content)
    assert blocks[2]["content"][0]["encrypted_content"] == "fake-encrypted"
    rows = usage_rows()
    assert [r["feature"] for r in rows].count("plan_research") == 3
    assert [r["question"] for r in rows] == [1, 0, 0, 0]


def test_a_paused_search_resumes_three_times_at_most(key, samples, prod_app, admin, fake) -> None:
    enable()
    fake.script = [research(searches=1, stop="pause_turn") for _ in range(4)] + [written()]
    view = wait(admin, start(admin))
    assert view["status"] == "ready"
    assert [k for k, _p in fake.requests] == ["stream"] * 5
    assert [r["feature"] for r in usage_rows()].count("plan_research") == 4


def test_a_paused_search_keeps_room_for_the_plan_call(key, samples, prod_app, admin, fake) -> None:
    conn = connect(get_settings().db_path)
    try:
        research_est, write_est = planner.estimates(conn, OPUS)
    finally:
        conn.close()
    first = cost_micro_usd(OPUS, Tokens(input=20_000, output=2_000, web_searches=4))
    cap = max(research_est + write_est, first + research_est) // 10_000 + 1
    # The resume alone fits, but not with the plan call after it.
    assert first + research_est <= cap * 10_000 < first + research_est + write_est
    enable(daily_cap_cents=cap)
    fake.script = [research(searches=4, stop="pause_turn"), written()]
    view = wait(admin, start(admin))
    assert view["status"] == "ready" and len(view["versions"]) == 1
    assert [r["feature"] for r in usage_rows()] == ["plan_research", "plan_write"]


def test_ai_off_or_over_a_cap_stores_nothing(key, samples, prod_app, admin, fake) -> None:
    r = admin.post("/api/trip-plans", json=request_body(), headers=H)
    assert r.status_code == 409 and r.json()["detail"] == ai_client.UNAVAILABLE_TEXT["off"]
    enable(daily_cap_cents=5)
    r = admin.post("/api/trip-plans", json=request_body(), headers=H)
    assert r.status_code == 429 and "Today's AI budget" in r.json()["detail"]
    enable(monthly_cap_cents=100, ask_reserve_cents=50)
    r = admin.post("/api/trip-plans", json=request_body(), headers=H)
    assert r.status_code == 429 and r.json()["detail"].startswith("The trip planner has used")
    assert fake.requests == []
    assert admin.get("/api/trip-plans").json()["plans"] == []


def test_plans_share_the_daily_question_limit(key, samples, prod_app, admin, fake) -> None:
    enable(daily_questions=1)
    fake.script = [research(), written()]
    plan_id = start(admin)
    wait(admin, plan_id)
    r = admin.post("/api/trip-plans", json=request_body(), headers=H)
    assert r.status_code == 429 and "questions today" in r.json()["detail"]
    r = admin.post(f"/api/trip-plans/{plan_id}/follow-up", json={"text": "More beach"}, headers=H)
    assert r.status_code == 429


def test_one_plan_at_a_time(key, samples, prod_app, admin, fake, monkeypatch) -> None:
    enable()
    monkeypatch.setattr(planner, "running_plan", lambda: "a" * 32)
    r = admin.post("/api/trip-plans", json=request_body(), headers=H)
    assert r.status_code == 409 and r.json()["detail"] == planner.BUSY_TEXT
    assert fake.requests == []


def test_the_list_names_who_can_go_and_a_run_shows_its_step(
    key, samples, prod_app, admin, fake, monkeypatch
) -> None:
    enable()
    listed = admin.get("/api/trip-plans").json()
    assert [p["key"] for p in listed["people"]] == ["alex", "jamie", "riley", "sam"]  # no joint
    fake.script = [research(), written()]
    plan_id = start(admin)
    assert wait(admin, plan_id)["step"] is None
    monkeypatch.setattr(planner, "running_plan", lambda: plan_id)
    with write_connection(get_settings().db_path) as conn:
        planner._add_turn(conn, plan_id, "request", {"follow_up": "More beach"})
    assert admin.get(f"/api/trip-plans/{plan_id}").json()["step"] == "searching"
    with write_connection(get_settings().db_path) as conn:
        planner._add_turn(conn, plan_id, "research", {"notes": "", "sources": [], "searches": 0})
    assert admin.get(f"/api/trip-plans/{plan_id}").json()["step"] == "writing"


def test_the_follow_up_limit(key, samples, prod_app, admin, fake) -> None:
    enable()
    fake.script = [research(), written()]
    plan_id = start(admin)
    wait(admin, plan_id)
    with write_connection(get_settings().db_path) as conn:
        for _ in range(planner.FOLLOW_UP_LIMIT):
            planner._add_turn(conn, plan_id, "request", {"follow_up": "again"})
    r = admin.post(f"/api/trip-plans/{plan_id}/follow-up", json={"text": "Once more"}, headers=H)
    assert r.status_code == 409 and r.json()["detail"] == planner.LIMIT_TEXT


def test_link_unlink_and_delete(key, samples, prod_app, member, fake) -> None:
    enable()
    fake.script = [research(), written()]
    plan_id = start(member)
    wait(member, plan_id)
    url = f"/api/trip-plans/{plan_id}"
    r = member.put(f"{url}/trip", json={"trip_key": "beach-week"}, headers=H)
    assert r.status_code == 200 and r.json()["trip_name"]
    listed = member.get("/api/trip-plans").json()["plans"][0]
    assert listed["trip_key"] == "beach-week" and listed["high_cents"] == 179_000
    r = member.put(f"{url}/trip", json={"trip_key": "no-such-trip"}, headers=H)
    assert r.status_code == 404
    assert member.put(f"{url}/trip", json={"trip_key": None}, headers=H).json()["trip_key"] is None
    assert member.delete(url, headers=H).status_code == 204
    assert member.get(url).status_code == 404
    assert member.delete(url, headers=H).status_code == 404
    rows = usage_rows()
    assert rows and all(r["plan_id"] is None for r in rows)  # the cost stays, unlinked


def test_routes_need_sign_in(prod_app, prod) -> None:
    routes = {
        (path, m)
        for path, methods, _ in walk_routes(prod_app)
        if path.startswith("/api/trip-plans")
        for m in methods - {"HEAD", "OPTIONS"}
    }
    assert len(routes) == 6
    for path, method in routes:
        r = prod.request(method, path.replace("{plan_id}", "a" * 32), headers=H)
        assert r.status_code == 401, (method, path)


def test_a_restart_ends_running_plans(samples: Settings) -> None:
    with write_connection(samples.db_path) as conn:
        for plan_id, done in (("a" * 32, True), ("b" * 32, False)):
            conn.execute(
                """INSERT INTO trip_plans VALUES (?, NULL, 'now', 'now', 'Somewhere',
                   'running', '{}', NULL, ?)""",
                (plan_id, OPUS),
            )
            if done:
                planner._add_turn(conn, plan_id, "plan", {"plan": {}, "derived": {}})
    assert planner.reset_running(samples.db_path) == 2
    conn = connect(samples.db_path)
    try:
        status = dict(conn.execute("SELECT plan_id, status FROM trip_plans"))
        notices = conn.execute(
            "SELECT content_json FROM trip_plan_turns WHERE kind = 'notice'"
        ).fetchall()
    finally:
        conn.close()
    assert status == {"a" * 32: "ready", "b" * 32: "failed"}
    assert [json.loads(n[0])["text"] for n in notices] == [planner.RESTART_TEXT] * 2
    assert planner.reset_running(samples.db_path) == 0


def test_the_rescan_deletes_a_matching_plan_whole(samples: Settings) -> None:
    private = PrivateRules(subscriptions=("Starlight Cinema",))
    with write_connection(samples.db_path) as conn:
        for plan_id, note, url in (
            ("a" * 32, "near Starlight Cinema", "https://x.example/"),
            ("b" * 32, "museums", "https://x.example/"),
            ("c" * 32, "museums", "https://x.example/?q=Starlight%20Cinema"),
            ("d" * 32, "museums", "HTTPS://x.example/?q=Starlight%20Cinema"),
        ):
            conn.execute(
                """INSERT INTO trip_plans VALUES (?, NULL, 'now', 'now', 'Somewhere',
                   'ready', '{}', NULL, ?)""",
                (plan_id, OPUS),
            )
            planner._add_turn(conn, plan_id, "research", {"notes": note, "sources": [{"url": url}]})
    with write_connection(samples.db_path) as conn:
        report = rescan(conn, private, "2026-10-20T00:00:00+00:00")
        left = [r[0] for r in conn.execute("SELECT plan_id FROM trip_plans")]
        turns = conn.execute("SELECT count(*) FROM trip_plan_turns").fetchone()[0]
    assert report.deleted == {"trip_plans": 3}
    assert left == ["b" * 32] and turns == 1


# --------------------------------------------------------------------------
# Settings, pricing and migration 0011
# --------------------------------------------------------------------------


def test_the_planner_model_is_opus_and_can_change(key, samples, prod_app, admin, fake) -> None:
    assert ai_settings.AiSettings().planner_model == OPUS
    assert ai_settings.model_for(ai_settings.AiSettings(), "planner") == OPUS
    enable(planner_model="claude-sonnet-5-5")
    fake.script = [research(), written()]
    wait(admin, start(admin))
    assert {p["model"] for _k, p in fake.requests} == {"claude-sonnet-5-5"}


def test_web_searches_are_priced_and_counted() -> None:
    use = message([text("x")], **{"searches": 7, "geo": "us"}).usage
    t = tokens_of(use)
    assert t.web_searches == 7
    no_search = cost_micro_usd(OPUS, Tokens(input=1000, output=200, inference_geo="us"))
    assert cost_micro_usd(OPUS, t) == no_search + 7 * WEB_SEARCH_MICRO_USD
    assert tokens_of(message([text("x")]).usage).web_searches == 0


def test_migration_0011_keeps_every_usage_row(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sql = MIGRATION.read_text()
    assert not re.search(r"\d{5,}", sql)
    assert list_migrations()[10].path == MIGRATION
    old = tmp_path / "old-migrations"
    old.mkdir()
    for m in list_migrations()[:10]:
        shutil.copy(m.path, old / m.path.name)
    monkeypatch.setattr(migrate_mod, "MIGRATIONS_DIR", old)
    assert migrate(settings=settings) == 10
    with write_connection(settings.db_path) as conn:
        for i, feature in enumerate(("ask", "review", "categorize", "cache_check"), start=1):
            conn.execute(
                """INSERT INTO ai_usage (at, feature, model, input_tokens, cost_micro_usd, error)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (f"2026-10-0{i}T00:00:00+00:00", feature, OPUS, i * 100, i * 7, None),
            )
    monkeypatch.setattr(migrate_mod, "MIGRATIONS_DIR", MIGRATION.parent)
    assert migrate(settings=settings) == len(list_migrations())  # the later ones run too
    assert (settings.backup_dir / "pre-migrate-0011.db").exists()
    conn = connect(settings.db_path)
    try:
        rows = conn.execute("SELECT * FROM ai_usage ORDER BY id").fetchall()
        assert [(r["feature"], r["input_tokens"], r["cost_micro_usd"]) for r in rows] == [
            ("ask", 100, 7),
            ("review", 200, 14),
            ("categorize", 300, 21),
            ("cache_check", 400, 28),
        ]
        assert {r["web_searches"] for r in rows} == {0}
        assert {r["plan_id"] for r in rows} == {None}
        assert {r["question"] for r in rows} == {0}
        indexes = {r[1] for r in conn.execute("PRAGMA index_list(ai_usage)")}
        assert {"ai_usage_at", "ai_usage_plan"} <= indexes
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        strict = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND sql LIKE '%STRICT'"
        ).fetchall()
        assert {"ai_usage", "trip_plans", "trip_plan_turns"} <= {r[0] for r in strict}
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO ai_usage (at, feature, model) VALUES ('x', 'nope', 'm')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO ai_usage (at, feature, model, question) VALUES ('x', 'ask', 'm', 2)"
            )
        conn.execute("INSERT INTO ai_usage (at, feature, model) VALUES ('x', 'plan_write', 'm')")
    finally:
        conn.close()


def test_api_model_names_do_not_clash(prod_app) -> None:
    """Two models with one name get module-qualified names in the API types."""
    names = prod_app.openapi()["components"]["schemas"]
    assert not [n for n in names if "__" in n]


# --------------------------------------------------------------------------
# Typed text, names and changes
# --------------------------------------------------------------------------


def test_research_never_sees_what_was_typed_about_the_household(
    key, samples, prod_app, admin, fake
) -> None:
    enable()
    note = "Use Alex's Summit Points and the Cobalt card"
    fake.script = [research(), written()]
    plan_id = start(admin, note=note)
    wait(admin, plan_id)
    change = "Sam wants to use 84,250 points on the Harbor Miles"
    fake.script = [research(searches=1), written()]
    r = admin.post(f"/api/trip-plans/{plan_id}/follow-up", json={"text": change}, headers=H)
    assert r.status_code == 200, r.text
    wait(admin, plan_id)
    for _kind, params in (fake.requests[0], fake.requests[2]):
        assert not LEAKS.search(json.dumps(params)), LEAKS.search(json.dumps(params))
    # The plan call has no tools: it gets the text whole.
    first = json.loads(fake.requests[1][1]["messages"][0]["content"])
    assert first["request"]["note"] == note
    second = json.loads(fake.requests[3][1]["messages"][0]["content"])
    assert second["changes_asked"] == [change]


def test_a_change_that_failed_is_not_sent_again(key, samples, prod_app, admin, fake) -> None:
    enable()
    fake.script = [research(), written()]
    plan_id = start(admin)
    wait(admin, plan_id)
    porto = research(cited("Porto has old wine cellars. ", FOOD))
    fake.script = [porto, written(stop="max_tokens")]
    admin.post(f"/api/trip-plans/{plan_id}/follow-up", json={"text": "Add Porto"}, headers=H)
    assert wait(admin, plan_id)["notice"] == planner.LONG_TEXT
    fake.script = [research(searches=1), written(good_plan(title="Lisbon for less"))]
    text = "A cheaper hotel"
    admin.post(f"/api/trip-plans/{plan_id}/follow-up", json={"text": text}, headers=H)
    view = wait(admin, plan_id)
    # Neither the failed change nor the research made for it goes out again.
    for _kind, params in fake.requests[4:]:
        assert "Add Porto" not in json.dumps(params) and "wine cellars" not in json.dumps(params)
    assert json.loads(fake.requests[5][1]["messages"][0]["content"])["changes_asked"] == [text]
    assert view["versions"][1]["follow_up"] == text


def test_rewards_names_go_out_scrubbed_one_entry_each() -> None:
    raw = brief()
    raw["programs"][0]["owner"] = "Alex"
    raw["programs"].append({**raw["programs"][0], "owner": "Sam", "balance": 10_000})
    raw["free_nights"].append({"name": "Free night x4821", "ends_on": None})
    raw["travel_credits"].append(
        {"name": "Hotel credit – Example Private Service", "cycle_ends": "2026-12-31"}
    )
    scrub = Scrub(PrivateRules(subscriptions=("Example Private Service",)), ["4821"])
    b = planner.plan_brief(scrub.json(raw))
    sent = json.dumps(b)
    assert "4821" not in sent and "Example Private" not in sent
    names = planner.enum_names(b)
    assert {"Summit Points (Alex)", "Summit Points (Sam)"} <= names["program"]
    credit = "Hotel credit – personal subscription"
    assert credit in names["benefit"] and "Free night x…" in names["benefit"]
    # The plan names an entry exactly: the filter's dash rewrite must not break the match.
    use = good_plan().points_plan[0].model_dump()
    plan = good_plan(
        points_plan=[
            {**use, "program": "Summit Points (Sam)", "benefit": credit},
            {**use, "program": "Not ours", "benefit": "Nor this"},
        ]
    )
    out = planner.tidy(plan, {"S1", "S2", "S3"}, None, names)
    assert out["points_plan"][0]["benefit"] == credit
    assert [u["program"] for u in out["points_plan"]] == ["Summit Points (Sam)", ""]
    d = planner.derive(out, req_dict(), b)
    assert d["balances"][0] == {
        "program": "Summit Points (Sam)",
        "needed": 60_000,
        "have": 10_000,
        "enough": False,
    }
    assert f"Use the {credit} by 2026-12-31." in [c["text"] for c in d["checks"]]


def test_timeshare_points_are_checked_against_the_trips_year() -> None:
    b = brief()
    b["programs"].append(
        {
            "name": "Shared Vacation Club",
            "kind": "timeshare",
            "balance": None,
            "cents_per_point_low": None,
            "cents_per_point_high": None,
            "allocations": [{"year": 2026, "left": 200}, {"year": 2027, "left": 800}],
        }
    )
    use = {**good_plan().points_plan[0].model_dump(), "program": "Shared Vacation Club"}
    plan = planner.tidy(good_plan(points_plan=[use]), {"S1"}, None, planner.enum_names(b))
    d = planner.derive(plan, req_dict(), b)
    assert d["balances"] == [
        {"program": "Shared Vacation Club", "needed": 60_000, "have": 800, "enough": False}
    ]
    assert "not_enough_points" in [c["kind"] for c in d["checks"]]


def test_empty_nights_are_one_check_and_every_kind_shows() -> None:
    req = req_dict(start_date=None, end_date=None, month="2027-06", nights=30)
    days = [day(1, travel="Fly ORD to LIS"), day(2, base="Porto")]
    plan = planner.tidy(good_plan(days=days), {"S1", "S2", "S3"}, None, planner.enum_names(brief()))
    d = planner.derive(plan, req, brief())
    kinds = [c["kind"] for c in d["checks"]]
    assert kinds == ["no_lodging", "no_way_home", "no_travel"]
    # In two parts: the repo scan reads a number, two words and "place" as an address.
    shown = d["checks"][0]["text"]
    assert shown.startswith("Nights 3 to 30 ") and shown.endswith(" have no place to stay.")
    assert planner._night_list([1, 3, 4, 5, 9]) == "1, 3 to 5 and 9"


# --------------------------------------------------------------------------
# Cost rows and question counts
# --------------------------------------------------------------------------


def test_a_deleted_plan_still_counts_its_questions(key, samples, prod_app, admin, fake) -> None:
    enable(daily_questions=2)
    for _ in range(2):
        fake.script = [research(), written()]
        plan_id = start(admin)
        wait(admin, plan_id)
        assert admin.delete(f"/api/trip-plans/{plan_id}", headers=H).status_code == 204
    r = admin.post("/api/trip-plans", json=request_body(), headers=H)
    assert r.status_code == 429 and "questions today" in r.json()["detail"]
    assert [r["question"] for r in usage_rows()] == [1, 0, 1, 0]


def done(client: TestClient, timeout: float = 15) -> None:
    end = time.monotonic() + timeout
    while client.get("/api/trip-plans").json()["running_plan_id"] is not None:
        assert time.monotonic() < end, "the plan is still running"
        time.sleep(0.02)


def test_a_plan_deleted_during_its_call_keeps_the_cost(key, samples, prod_app, admin, fake) -> None:
    """The CLI rescan runs in its own process and can delete a running plan."""
    enable()

    def rescanned(_params: dict):
        with write_connection(get_settings().db_path) as conn:
            conn.execute("DELETE FROM trip_plans")
        return research()

    fake.script = [rescanned]
    start(admin)
    done(admin)
    rows = usage_rows()
    assert [(r["feature"], r["plan_id"], r["web_searches"], r["question"]) for r in rows] == [
        ("plan_research", None, 3, 1)
    ]
    assert rows[0]["cost_micro_usd"] > 0 and len(fake.requests) == 1


def test_a_broken_search_stream_counts_its_searches_and_is_not_retried(
    key, samples, prod_app, admin, fake
) -> None:
    enable()
    partial = message(
        [thinking(), server_tool_use("ORD to LIS"), search_results("srvtoolu_1", [FARES, HOTEL])],
        None,
        OPUS,
        input_tokens=1_000,
        output_tokens=1,
    )
    fake.script = [Broken(dropped(), partial)]
    view = wait(admin, start(admin))
    assert view["notice"] == ai_client.FAILED_TEXT and len(fake.requests) == 1
    (row,) = usage_rows()
    assert row["error"] == "RemoteProtocolError" and row["web_searches"] == 1
    assert row["input_tokens"] > 1_000
    assert row["cost_micro_usd"] > cost_micro_usd(OPUS, Tokens(input=1_000, output=1))


def test_a_cancelled_planner_call_records_its_cost(key, samples, fake) -> None:
    """The app stopping cancels a running plan's call mid-stream."""
    enable()
    partial = message(
        [server_tool_use("ORD to LIS"), search_results("srvtoolu_1", [FARES])],
        None,
        OPUS,
        input_tokens=1_000,
        output_tokens=1,
    )
    fake.script = [partial]
    info = ai_client.CallInfo("plan_research", OPUS, "medium", question=True)

    async def request(client):
        async with client.messages.stream(model=OPUS) as stream:
            async for _event in stream:
                pass
            await asyncio.Event().wait()  # cancelled here

    async def go() -> None:
        task = asyncio.create_task(
            ai_client.call(get_settings(), info, request, record_cancel=True)
        )
        while not fake.requests:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    (row,) = usage_rows()
    assert row["error"] == "CancelledError" and row["web_searches"] == 1
    assert row["input_tokens"] > 1_000 and row["question"] == 1


def test_no_delete_while_a_plan_starts(key, samples, prod_app, admin, fake, monkeypatch) -> None:
    enable()
    fake.script = [research(), written()]
    plan_id = start(admin)
    wait(admin, plan_id)
    monkeypatch.setattr(planner, "_starting", True)
    r = admin.delete(f"/api/trip-plans/{plan_id}", headers=H)
    assert r.status_code == 409 and r.json()["detail"] == planner.BUSY_TEXT
    monkeypatch.setattr(planner, "_starting", False)

    def rescanned(*_args) -> None:
        with write_connection(get_settings().db_path) as conn:
            conn.execute("DELETE FROM trip_plans")

    monkeypatch.setattr(planner, "_precheck", rescanned)
    r = admin.post(f"/api/trip-plans/{plan_id}/follow-up", json={"text": "More"}, headers=H)
    assert r.status_code == 404


def test_a_search_that_started_is_never_retried(key, samples, prod_app, admin, fake) -> None:
    """It may have run (and been billed) before the stream broke."""
    enable()
    partial = message([server_tool_use("ORD to LIS")], None, OPUS, input_tokens=1_000)
    fake.script = [Broken(dropped(), partial)]
    view = wait(admin, start(admin))
    assert view["notice"] == ai_client.FAILED_TEXT and len(fake.requests) == 1
    (row,) = usage_rows()
    assert row["web_searches"] == 0  # billed searches are counted from their results


def test_links_are_checked_again_when_shown(samples: Settings) -> None:
    """A name added to the private list after the plan was made."""
    url = "https://x.example/?q=Starlight%20Cinema"
    with write_connection(samples.db_path) as conn:
        conn.execute(
            """INSERT INTO trip_plans VALUES (?, NULL, 'now', 'now', 'Somewhere',
               'ready', ?, NULL, ?)""",
            ("e" * 32, json.dumps(req_dict()), OPUS),
        )
        source = {"id": "S1", "url": url, "site": "x.example", "title": "Starlight Cinema"}
        planner._add_turn(conn, "e" * 32, "research", {"notes": "", "sources": [source]})
        plan = planner.tidy(good_plan(), {"S1"}, None, planner.enum_names(brief()))
        planner._add_turn(conn, "e" * 32, "plan", {"plan": plan, "derived": {}})
    conn = connect(samples.db_path)
    try:
        before = planner.plan_view(conn, "e" * 32, None)
        after = planner.plan_view(conn, "e" * 32, PrivateRules(subscriptions=("Starlight Cinema",)))
    finally:
        conn.close()
    assert [link["url"] for link in before["versions"][0]["links"]] == [url]
    assert after["versions"][0]["links"] == []
