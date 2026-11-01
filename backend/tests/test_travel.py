"""Travel (docs/plans/phase-7.md sections 2.2, 2.3, 4 and 7): the trips service
and API, the trip filter on Transactions, trip labels on flags and plans, the
Dashboard's next-trip line and the trips part of the Checks block.

Fake samples only. The second backfill run sends "lake-weekend" (final) and
"city-break"; the 2026-10-12 monthly run sends "ski-weekend" and "beach-week"
and removes "city-break" with a tombstone. Extra trips here are made up.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from api_helpers import PROD_ORIGIN
from fastapi.testclient import TestClient
from pydantic import TypeAdapter

from app import config
from app.config import Settings
from app.db.connect import connect, write_connection
from app.importer.apply import apply_trips
from app.importer.models import Trip, TripTombstone
from app.services import trips as tr
from app.services.balances import clear_cache
from app.services.checks import checks
from app.services.dashboard import dashboard
from app.services.plans import flags_page, plans_page

TODAY = date(2026, 10, 20)
BACKFILL = "2026-10-06-2"
MONTHLY = "2026-10-12"
LODGE = "266ff36d91a25cd0275e2d08e4761c6a35b6b0c34d200f1199eca25530873037"  # Jul 3, -210.00
GAS = "143631cd89a06364a9a14eef77536ace8d9306e491e181785508d01b9c43e0b8"  # Jul 6, -53.47
REFUND = "2b2b94a989ad2f4b7d7f12d6ba2f9a7ab3a578575fbb3291a6ac22748a119f32"  # Jun 29, +45.00
DEPOSIT = "0e637d21773cefc30c55d6e860bd23db1fd0302d127e678f50c190a72bf8f2f3"  # Jan 9, +2,931.20
ITEMS = TypeAdapter(list[Trip | TripTombstone])
H = {"Origin": PROD_ORIGIN}


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    clear_cache()


def on(monkeypatch: pytest.MonkeyPatch, day: date) -> None:
    monkeypatch.setattr(config, "household_today", lambda: day)
    clear_cache()


def page(settings: Settings) -> dict:
    conn = connect(settings.db_path)
    try:
        return tr.trips_page(conn)
    finally:
        conn.close()


def every(p: dict) -> dict[str, dict]:
    return {t["key"]: t for t in p["active"] + p["completed"] + p["older"]}


def run_with_trips(settings: Settings, run_id: str, items: list[dict], mode: str = "correction"):
    """A newer stored run carrying only `trips`, applied like the importer does."""
    with write_connection(settings.db_path) as w:
        w.execute(
            """INSERT INTO import_runs (run_id, run_date, run_seq, mode, status, started_at,
                   schema_version)
               VALUES (?, ?, 1, ?, 'imported', '2026-10-20T00:00:00+00:00', '1.3')""",
            (run_id, run_id[:10], mode),
        )
        w.execute(
            "INSERT INTO run_sections (run_id, section, sha256, json) VALUES (?, 'trips', ?, ?)",
            (run_id, f"{run_id}-trips".ljust(64, "0")[:64], json.dumps(items)),
        )
        apply_trips(w, ITEMS.validate_python(items), run_id)


def item(key: str, kind: str, status: str, tender: str = "cash", **f: object) -> dict:
    return {
        "key": key,
        "kind": kind,
        "label": key.replace("-", " ").capitalize(),
        "status": status,
        "tender": tender,
        **f,
    }


def trip(key: str, status: str, items: list[dict], **f: object) -> dict:
    out = {
        "key": key,
        "name": key.split("-")[0].capitalize() + " trip",
        "status": status,
        "items": sorted(items, key=lambda i: i["key"]),
    }
    out |= f
    if "month" not in f and "start_date" not in f:
        out |= {"start_date": "2026-11-06", "end_date": "2026-11-08"}
    return out


def tag(settings: Settings, txn_key: str, trip_key: str | None) -> None:
    """Stands in for a row imported with this trip_key (the app never tags rows itself)."""
    with write_connection(settings.db_path) as w:
        w.execute("UPDATE transactions SET trip_key = ? WHERE txn_key = ?", (trip_key, txn_key))


# ---- item and trip math ----


@pytest.mark.parametrize(
    ("status", "tender", "amount", "cash"),
    [
        ("posted", "cash", 1000, 1000),
        ("booked", "cash", 1000, 1000),
        ("booked", "points", 1120, 1120),  # the cash taxes and fees on a points booking
        ("booked", "free_night", 2500, 2500),  # resort fee on a free night
        ("posted", "points_credit", 4000, -4000),  # a points credit takes away
        ("posted", "covered", 12_000, 0),  # someone else paid
        ("excluded", "cash", 3500, 0),
        ("estimate", "cash", None, 0),
        ("tbd", "cash", None, 0),
    ],
)
def test_item_cash(status: str, tender: str, amount: int | None, cash: int) -> None:
    assert tr.item_cash(status, tender, amount) == cash


def test_versus_estimate() -> None:
    assert tr.versus_estimate(40_000, 45_000, 60_000) == "under"
    assert tr.versus_estimate(45_000, 45_000, 60_000) == "within"
    assert tr.versus_estimate(60_001, 45_000, 60_000) == "over"
    assert tr.versus_estimate(50_000, None, None) is None


# ---- the samples ----


def test_sample_trips_and_totals(samples: Settings) -> None:
    p = page(samples)
    assert [t["key"] for t in p["active"]] == ["ski-weekend", "beach-week"]  # by start
    assert [t["key"] for t in p["completed"]] == ["lake-weekend"]
    assert p["older"] == [] and p["trip_count"] == 3
    t = every(p)
    assert "city-break" not in t  # removed by the monthly run's tombstone

    ski = t["ski-weekend"]["totals"]
    # Flights on points: 11.20 in fees. Gear 180, rental car 300, free night 0. Less a 40 credit.
    assert ski["cash_cents"] == 1_120 + 18_000 + 30_000 + 0 - 4_000 == 45_120
    assert (ski["estimate_low_cents"], ski["estimate_high_cents"], ski["tbd_count"]) == (
        30_000,
        42_000,
        1,
    )
    assert ski["owed_to_us_cents"] == 15_000 and ski["we_owe_cents"] == 0
    assert ski["our_net_cents"] == 45_120 - 15_000
    assert ski["points"] == [
        {
            "program_key": "harbor-miles",
            "program_name": "Harbor Miles",
            "points_micro": 50_000 * 10**6,
        }
    ]
    assert ski["by_kind"] == {"flight": 1_120, "lodging": 0, "food": 0, "other": 44_000}
    assert sum(ski["by_kind"].values()) == ski["cash_cents"]

    lake = t["lake-weekend"]
    # Gas 53.47 and the lodge 210. The covered boat and the excluded souvenirs do not count.
    assert lake["totals"]["cash_cents"] == 26_347
    assert lake["totals"]["our_net_cents"] == 26_347  # the IOU was waived
    assert lake["versus_estimate"] == "under"  # est. was 450 to 600
    assert lake["people"][0] == {"key": "alex", "name": "Alex"} and lake["guests"] == 2
    excluded = [i for i in lake["items"] if i["status"] == "excluded"]
    assert [(i["key"], i["cash_cents"]) for i in excluded] == [("souvenirs", 0)]

    beach = t["beach-week"]
    assert beach["month"] == "2027-06" and beach["start_date"] is None  # never inferred
    assert beach["end_date"] is None
    assert beach["totals"]["cash_cents"] == 0
    # The snorkel tour (150 to 250) is a maybe item (format 1.4): never in a total.
    assert beach["totals"]["estimate_low_cents"] == 90_000 + 120_000
    assert beach["totals"]["estimate_high_cents"] == 130_000 + 180_000
    assert [(i["key"], i["status"]) for i in beach["items"]][-1] == ("snorkel-tour", "estimate")

    tiles = p["tiles"]
    assert tiles["booked_cents"] == 45_120 + 26_347 == p["summary"]["booked_cents"]
    assert tiles["estimate_low_cents"] == 30_000 + 210_000
    assert tiles["estimate_high_cents"] == 42_000 + 310_000
    assert tiles["tbd_count"] == 1
    assert (tiles["collect_cents"], tiles["collect_count"], tiles["pay_count"]) == (15_000, 1, 0)


def test_trips_keep_their_own_source_run(samples: Settings) -> None:
    """A final backfill trip stays without a newer monthly run sending it."""
    p = page(samples)
    t = every(p)
    assert t["lake-weekend"]["source_run_id"] == BACKFILL
    assert t["lake-weekend"]["source_run_date"] == "2026-10-06"
    assert t["ski-weekend"]["source_run_id"] == MONTHLY
    assert (p["source_run_id"], p["source_run_date"]) == (MONTHLY, "2026-10-12")


def test_open_items(samples: Settings) -> None:
    o = page(samples)["open_items"]
    assert [(x["trip_key"], x["key"], x["item_label"]) for x in o["collect"]] == [
        ("ski-weekend", "car-share", "Rental car")
    ]
    assert o["pay"] == []
    assert [(x["trip_key"], x["key"], x["status"]) for x in o["items"]] == [
        ("ski-weekend", "lift-tickets", "estimate"),
        ("ski-weekend", "parking", "tbd"),
        ("beach-week", "flights", "estimate"),
        ("beach-week", "rental", "estimate"),
        ("beach-week", "snorkel-tour", "estimate"),  # a maybe item stays open (format 1.4)
    ]


def test_due_items_are_listed_by_date(samples: Settings) -> None:
    """Format 1.4: the booked rental car is due Nov 10 on the Summit card. It is the one
    due item, and the snorkel tour the one maybe item, on the samples."""
    p = page(samples)
    due = p["open_items"]["due"]
    assert [
        (x["trip_key"], x["key"], x["due_on"], x["account_name"], x["cash_cents"]) for x in due
    ] == [("ski-weekend", "rental-car", "2026-11-10", "Summit card", 30_000)]
    assert due[0]["trip_name"] == "Ski weekend" and due[0]["maybe"] is False
    items = {(k, i["key"]): i for k, t in every(p).items() for i in t["items"]}
    assert (
        items["ski-weekend", "rental-car"]["due_on"],
        items["ski-weekend", "rental-car"]["maybe"],
    ) == ("2026-11-10", False)
    snorkel = items["beach-week", "snorkel-tour"]
    assert snorkel["maybe"] is True and snorkel["due_on"] is None
    rest = [
        i
        for k, i in items.items()
        if k not in {("ski-weekend", "rental-car"), ("beach-week", "snorkel-tour")}
    ]
    assert rest and all(i["due_on"] is None and i["maybe"] is False for i in rest)


def test_only_paying_due_items_are_due(samples: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Due: booked with a due date and it adds cash. Covered items, points credits and
    $0 free nights stay out. By due date across trips; a past one stays."""
    due_on = "2026-11-05"
    run_with_trips(
        samples,
        "2026-10-16",
        [
            trip(
                "cabin-nov26",
                "booked",
                [
                    item("cash", "lodging", "booked", amount="99.00", due_on="2026-11-03"),
                    item("paid", "lodging", "booked", amount="50.00"),  # no due date
                    item("covered", "food", "booked", "covered", amount="40.00", due_on=due_on),
                    item(
                        "credit", "other", "booked", "points_credit", amount="25.00", due_on=due_on
                    ),
                    item("free", "lodging", "booked", "free_night", amount="0.00", due_on=due_on),
                ],
            ),
            trip(
                "lake-dec26",
                "booked",
                [
                    item(
                        "fees",
                        "lodging",
                        "booked",
                        "free_night",
                        amount="12.50",
                        due_on="2026-11-01",
                        account_key="summit-card",
                    )
                ],
                start_date="2026-12-04",
                end_date="2026-12-06",
            ),
        ],
    )
    made_up = {"cabin-nov26", "lake-dec26"}
    due = [x for x in page(samples)["open_items"]["due"] if x["trip_key"] in made_up]
    assert [(x["trip_key"], x["key"], x["due_on"], x["cash_cents"]) for x in due] == [
        ("lake-dec26", "fees", "2026-11-01", 1_250),  # resort fees on a free night count
        ("cabin-nov26", "cash", "2026-11-03", 9_900),
    ]
    assert due[0]["account_name"] == "Summit card" and due[1]["account_name"] is None
    on(monkeypatch, date(2026, 11, 11))  # both dates have passed, nothing posted yet
    due = [x for x in page(samples)["open_items"]["due"] if x["trip_key"] in made_up]
    assert [x["key"] for x in due] == ["fees", "cash"]


def test_summary_chart_numbers_add_up(samples: Settings) -> None:
    s = page(samples)["summary"]
    assert [r["key"] for r in s["rows"]] == ["lake-weekend", "ski-weekend", "beach-week"]
    for r in s["rows"]:
        parts = r["flight_cents"] + r["lodging_cents"] + r["food_cents"] + r["other_cents"]
        assert parts == r["total_cents"]
    lake = s["rows"][0]
    assert (lake["lodging_cents"], lake["other_cents"]) == (21_000, 5_347)


def test_tagged_rows_against_items(samples: Settings) -> None:
    t = every(page(samples))
    assert t["lake-weekend"]["tagged"] == {
        "row_count": 2,
        "out_cents": 26_347,
        "posted_cents": 26_347,
        "out_differs": False,
        "deposits_cents": 0,
        "settled_to_us_cents": 0,
        "deposits_differ": False,
    }
    ski = t["ski-weekend"]["tagged"]  # the posted 40 points credit has no tagged row
    assert (ski["out_cents"], ski["posted_cents"], ski["out_differs"]) == (0, -4_000, True)


# ---- made-up trips: IOUs, scope, points, covered estimates ----


def test_iou_directions_and_statuses(samples: Settings) -> None:
    run_with_trips(
        samples,
        "2026-10-16",
        [
            trip(
                "cabin-nov26",
                "in_progress",
                [item("cabin", "lodging", "booked", amount="600.00")],
                ious=[
                    {
                        "key": "a-open",
                        "direction": "owed_to_us",
                        "counterparty": "friend",
                        "amount": "100.00",
                        "status": "open",
                    },
                    {
                        "key": "b-settled",
                        "direction": "owed_to_us",
                        "counterparty": "relative",
                        "amount": "50.00",
                        "status": "settled",
                        "settled_on": "2026-10-15",
                    },
                    {
                        "key": "c-waived",
                        "direction": "owed_to_us",
                        "counterparty": "friend",
                        "amount": "70.00",
                        "status": "waived",
                    },
                    {
                        "key": "d-we-owe",
                        "direction": "we_owe",
                        "counterparty": "relative",
                        "amount": "40.00",
                        "status": "open",
                    },
                    {
                        "key": "e-we-paid",
                        "direction": "we_owe",
                        "counterparty": "friend",
                        "amount": "10.00",
                        "status": "settled",
                        "settled_on": "2026-10-14",
                    },
                    {
                        "key": "f-waived",
                        "direction": "we_owe",
                        "counterparty": "friend",
                        "amount": "500.00",
                        "status": "waived",
                    },
                ],
            )
        ],
    )
    p = page(samples)
    totals = every(p)["cabin-nov26"]["totals"]
    assert totals["owed_to_us_cents"] == 15_000  # open and settled, not waived
    assert totals["we_owe_cents"] == 5_000
    assert totals["our_net_cents"] == 60_000 - 15_000 + 5_000
    o = p["open_items"]
    assert [x["key"] for x in o["collect"]] == ["a-open", "car-share"]  # by trip start
    assert [x["key"] for x in o["pay"]] == ["d-we-owe"]
    assert (p["tiles"]["pay_cents"], p["tiles"]["pay_count"]) == (4_000, 1)
    assert (p["tiles"]["collect_cents"], p["tiles"]["collect_count"]) == (25_000, 2)


def test_covered_and_excluded_items_leave_out_estimates_and_tbd(samples: Settings) -> None:
    run_with_trips(
        samples,
        "2026-10-16",
        [
            trip(
                "cabin-nov26",
                "planning",
                [
                    item(
                        "a",
                        "food",
                        "estimate",
                        "covered",
                        estimate_low="50.00",
                        estimate_high="80.00",
                    ),
                    item("b", "food", "tbd", "covered"),
                    item("c", "food", "estimate", estimate_low="20.00", estimate_high="30.00"),
                    item("d", "food", "tbd"),
                    item("e", "food", "excluded", amount="99.00"),
                    item(
                        "f",
                        "flight",
                        "booked",
                        "points",
                        amount="25.60",
                        points="3000",
                        program_key="other-air",
                    ),
                    item(
                        "g",
                        "flight",
                        "estimate",
                        "points",
                        estimate_low="10.00",
                        estimate_high="20.00",
                        points="9000",
                        program_key="other-air",
                    ),
                    item(
                        "h",
                        "flight",
                        "posted",
                        "covered",
                        amount="300.00",
                        points="1000",
                        program_key="other-air",
                    ),
                ],
            )
        ],
    )
    t = every(page(samples))["cabin-nov26"]["totals"]
    assert t["cash_cents"] == 2_560  # only the points booking's cash fees
    assert (t["estimate_low_cents"], t["estimate_high_cents"], t["tbd_count"]) == (3_000, 5_000, 1)
    # Only booked and posted points that are ours. A program Rewards does not know keeps its key.
    assert t["points"] == [
        {"program_key": "other-air", "program_name": "other-air", "points_micro": 3_000 * 10**6}
    ]


def test_maybe_items_leave_out_estimates_and_tbd(samples: Settings) -> None:
    """A maybe item (1.4, a cost not booked that may be added) counts in no total, and
    stays in the still-open list."""
    run_with_trips(
        samples,
        "2026-10-16",
        [
            trip(
                "cabin-nov26",
                "planning",
                [
                    item(
                        "a",
                        "food",
                        "estimate",
                        estimate_low="50.00",
                        estimate_high="80.00",
                        maybe=True,
                    ),
                    item("b", "food", "tbd", maybe=True),
                    item("c", "food", "estimate", estimate_low="20.00", estimate_high="30.00"),
                    item("d", "food", "tbd"),
                    item("e", "lodging", "booked", amount="99.00", due_on="2026-11-01"),
                ],
            )
        ],
    )
    p = page(samples)
    t = every(p)["cabin-nov26"]["totals"]
    assert (t["estimate_low_cents"], t["estimate_high_cents"], t["tbd_count"]) == (2_000, 3_000, 1)
    assert t["cash_cents"] == 9_900
    still_open = [i["key"] for i in p["open_items"]["items"] if i["trip_key"] == "cabin-nov26"]
    assert still_open == ["a", "b", "c", "d"]
    maybe = {i["key"]: i["maybe"] for i in every(p)["cabin-nov26"]["items"]}
    assert maybe == {"a": True, "b": True, "c": False, "d": False, "e": False}
    # The tiles count only c and d on top of the samples' 300 to 420 and 210 to 310.
    tiles = p["tiles"]
    assert tiles["estimate_low_cents"] == 30_000 + 210_000 + 2_000
    assert tiles["estimate_high_cents"] == 42_000 + 310_000 + 3_000
    assert tiles["tbd_count"] == 1 + 1
    assert ("cabin-nov26", "e") in [(x["trip_key"], x["key"]) for x in p["open_items"]["due"]]


def test_scope_this_year_last_year_and_later(
    samples: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_with_trips(
        samples,
        "2026-10-16",
        [
            trip(
                "cabin-may25",
                "final",
                [item("x", "lodging", "posted", amount="100.00")],
                month="2025-05",
            ),
            trip(
                "coast-dec24",
                "final",
                [item("x", "lodging", "posted", amount="200.00")],
                start_date="2024-12-30",
                end_date="2025-01-02",
            ),  # ends in 2025: in scope
            trip(
                "hills-jun24",
                "complete",
                [item("x", "lodging", "posted", amount="400.00")],
                start_date="2024-06-01",
                end_date="2024-06-03",
            ),
            trip(
                "lake-aug23",
                "booked",
                [item("x", "lodging", "booked", amount="800.00")],
                month="2023-08",
            ),  # left open, but older
        ],
        mode="backfill",
    )
    p = page(samples)
    assert p["from_year"] == 2025
    assert [t["key"] for t in p["completed"]] == ["lake-weekend", "cabin-may25", "coast-dec24"]
    assert [t["key"] for t in p["older"]] == ["hills-jun24", "lake-aug23"]
    assert "lake-aug23" not in [t["key"] for t in p["active"]]
    assert p["tiles"]["booked_cents"] == 45_120 + 26_347 + 10_000 + 20_000
    assert p["summary"]["booked_cents"] == p["tiles"]["booked_cents"]
    assert {r["key"] for r in p["summary"]["rows"]} == {
        "lake-weekend",
        "ski-weekend",
        "beach-week",
        "cabin-may25",
        "coast-dec24",
    }
    on(monkeypatch, date(2027, 1, 2))
    p = page(samples)
    assert p["from_year"] == 2026
    assert [t["key"] for t in p["older"]][:3] == ["cabin-may25", "coast-dec24", "hills-jun24"]


def test_month_only_trip_is_the_next_trip(
    samples: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    on(monkeypatch, date(2027, 2, 1))  # the ski weekend is over
    conn = connect(samples.db_path)
    try:
        nxt = tr.next_trip(conn)
    finally:
        conn.close()
    assert nxt == {
        "key": "beach-week",
        "name": "Beach week",
        "status": "planning",
        "start_date": None,
        "month": "2027-06",
        "cash_cents": 0,
        "estimate_low_cents": 210_000,
        "estimate_high_cents": 310_000,
        "tbd_count": 0,
    }
    on(monkeypatch, date(2027, 6, 30))  # still in its month
    conn = connect(samples.db_path)
    try:
        assert tr.next_trip(conn)["key"] == "beach-week"
        on(monkeypatch, date(2027, 7, 1))
        assert tr.next_trip(conn) is None
    finally:
        conn.close()


# ---- corrections and tombstones ----


def test_correction_replaces_the_whole_trip(samples: Settings) -> None:
    lake = every(page(samples))["lake-weekend"]
    sent = {
        "key": "lake-weekend",
        "name": "Lake weekend (corrected)",
        "status": "final",
        "start_date": "2026-07-03",
        "end_date": "2026-07-06",
        "items": [item("lodging", "lodging", "posted", amount="210.00")],
    }
    run_with_trips(samples, "2026-10-16", [sent])
    after = every(page(samples))["lake-weekend"]
    assert after["name"] == "Lake weekend (corrected)" and after["source_run_id"] == "2026-10-16"
    assert [i["key"] for i in after["items"]] == ["lodging"]  # the gas item went with the old one
    assert after["ious"] == [] and lake["ious"] != []
    assert after["totals"]["cash_cents"] == 21_000
    assert after["tagged"]["out_cents"] == 26_347 and after["tagged"]["out_differs"] is True


def test_tombstone_removes_and_labels_fall_back(samples: Settings, admin: TestClient) -> None:
    run_with_trips(samples, "2026-10-16", [{"deleted": True, "key": "ski-weekend"}])
    assert "ski-weekend" not in every(page(samples))
    assert admin.get("/api/trips/ski-weekend").status_code == 404
    conn = connect(samples.db_path)
    try:
        plans = plans_page(conn)
        comps = [c for card in plans["cards"] for p in card["plans"] for c in p["components"]]
        ski = [c for c in comps if c["trip_key"] == "ski-weekend"]
        assert ski and all(c["trip_name"] is None for c in ski)
        unknown = {u["key"]: u for u in tr.unknown_trips(conn)}
    finally:
        conn.close()
    assert unknown["ski-weekend"]["status"] == "removed"
    assert unknown["ski-weekend"]["plans"] == 1 and unknown["ski-weekend"]["redemptions"] == 1
    assert unknown["day-trip-sep26"]["status"] == "not_sent"


# ---- Transactions: the trip filter and the trip name ----


def test_search_by_trip_any_date_with_pages(samples: Settings, admin: TestClient) -> None:
    tag(samples, DEPOSIT, "lake-weekend")  # January, long before the trip
    body = {"trip_key": "lake-weekend", "limit": 2}
    first = admin.post("/api/transactions/search", json=body, headers=H).json()
    assert first["summary"]["count"] == 3
    assert [r["txn_key"] for r in first["rows"]] == [GAS, LODGE]
    assert all(r["trip_name"] == "Lake weekend" for r in first["rows"])
    nxt = admin.post(
        "/api/transactions/search", json={**body, "cursor": first["next_cursor"]}, headers=H
    ).json()
    assert [r["txn_key"] for r in nxt["rows"]] == [DEPOSIT]
    assert nxt["next_cursor"] is None and nxt["summary"] is None
    summary = admin.get("/api/transactions/summary", params={"trip_key": "lake-weekend"}).json()
    assert summary["count"] == 3
    other = admin.post(
        "/api/transactions/search", json={"trip_key": "ski-weekend"}, headers=H
    ).json()
    assert other["rows"] == [] and other["summary"]["count"] == 0


def test_rows_with_an_unknown_trip_have_no_name(samples: Settings, admin: TestClient) -> None:
    out = admin.post(
        "/api/transactions/search", json={"trip_key": "day-trip-sep26"}, headers=H
    ).json()
    assert len(out["rows"]) == 1
    assert out["rows"][0]["trip_key"] == "day-trip-sep26" and out["rows"][0]["trip_name"] is None
    untagged = admin.post("/api/transactions/search", json={"limit": 500}, headers=H).json()["rows"]
    assert {r["trip_name"] for r in untagged if r["trip_key"] is None} == {None}


def test_trip_filter_rejects_a_bad_key(samples: Settings, admin: TestClient) -> None:
    r = admin.post("/api/transactions/search", json={"trip_key": "Lake Weekend"}, headers=H)
    assert r.status_code == 422


def test_tagged_rows_follow_overrides_refunds_and_deposits(
    samples: Settings, admin: TestClient
) -> None:
    tag(samples, REFUND, "lake-weekend")  # a return: money back on a purchase
    tag(samples, DEPOSIT, "lake-weekend")  # a deposit, not a refund
    lake = every(page(samples))["lake-weekend"]["tagged"]
    assert lake["row_count"] == 4
    assert lake["out_cents"] == 26_347 - 4_500 and lake["out_differs"] is True
    assert lake["deposits_cents"] == 293_120 and lake["deposits_differ"] is True
    # Hidden rows and transfers are left out, as everywhere else.
    assert (
        admin.patch(f"/api/transactions/{REFUND}", json={"hidden": True}, headers=H).status_code
        == 200
    )
    assert (
        admin.patch(
            f"/api/transactions/{DEPOSIT}", json={"is_transfer": True}, headers=H
        ).status_code
        == 200
    )
    lake = every(page(samples))["lake-weekend"]["tagged"]
    assert lake["row_count"] == 3  # the hidden row is off the Transactions page too
    assert (lake["out_cents"], lake["out_differs"]) == (26_347, False)
    assert (lake["deposits_cents"], lake["deposits_differ"]) == (0, False)


def test_tagged_points_credit_row_is_a_refund(samples: Settings) -> None:
    """A points credit row is `refund` (schema/README.md section 6), so it nets against
    the posted points credit item. Typed `adjustment`, it would count as a deposit."""
    tag(samples, REFUND, "ski-weekend")
    with write_connection(samples.db_path) as w:  # stands in for a +40.00 card credit row
        w.execute("UPDATE transactions SET amount_cents = 4000 WHERE txn_key = ?", (REFUND,))
    ski = every(page(samples))["ski-weekend"]["tagged"]
    assert ski["row_count"] == 1
    assert ski["out_cents"] == ski["posted_cents"] == -4_000 and ski["out_differs"] is False
    assert (ski["deposits_cents"], ski["deposits_differ"]) == (0, False)
    with write_connection(samples.db_path) as w:
        w.execute("UPDATE transactions SET txn_type = 'adjustment' WHERE txn_key = ?", (REFUND,))
    ski = every(page(samples))["ski-weekend"]["tagged"]
    assert (ski["out_cents"], ski["out_differs"]) == (0, True)
    assert (ski["deposits_cents"], ski["deposits_differ"]) == (4_000, True)


def test_settled_iou_matches_a_tagged_deposit(samples: Settings) -> None:
    run_with_trips(
        samples,
        "2026-10-16",
        [
            trip(
                "cabin-nov26",
                "complete",
                [item("cabin", "lodging", "posted", amount="2931.70")],
                ious=[
                    {
                        "key": "share",
                        "direction": "owed_to_us",
                        "counterparty": "relative",
                        "amount": "2930.50",
                        "status": "settled",
                        "settled_on": "2026-01-09",
                    }
                ],
            )
        ],
    )
    tag(samples, DEPOSIT, "cabin-nov26")
    tagged = every(page(samples))["cabin-nov26"]["tagged"]
    assert (tagged["deposits_cents"], tagged["settled_to_us_cents"]) == (293_120, 293_050)
    assert tagged["deposits_differ"] is False  # 70 cents apart is within $1
    assert tagged["out_cents"] == 0 and tagged["out_differs"] is True


# ---- flags and plans ----


def test_flag_and_plan_trip_labels(samples: Settings) -> None:
    conn = connect(samples.db_path)
    try:
        flags = {f["key"]: f for f in flags_page(conn, include_archived=True)["flags"]}
        plans = plans_page(conn)
    finally:
        conn.close()
    lake = flags["lake-weekend-costs"]
    assert (lake["related_kind"], lake["related_label"]) == ("trip", "Lake weekend")
    comps = [c for card in plans["cards"] for p in card["plans"] for c in p["components"]]
    names = {c["trip_key"]: c["trip_name"] for c in comps if c["trip_key"]}
    assert names == {"ski-weekend": "Ski weekend"}
    assert all(c["trip_name"] is None for c in comps if not c["trip_key"])


def test_flag_about_a_removed_trip_has_no_label(samples: Settings) -> None:
    run_with_trips(samples, "2026-10-16", [{"deleted": True, "key": "lake-weekend"}])
    conn = connect(samples.db_path)
    try:
        flags = {f["key"]: f for f in flags_page(conn, include_archived=True)["flags"]}
    finally:
        conn.close()
    # Before chunk 4 this showed the key made to look like a name ("Lake weekend").
    assert flags["lake-weekend-costs"]["related_label"] is None


# ---- API ----


def test_api_trips(samples: Settings, member: TestClient) -> None:
    r = member.get("/api/trips")
    assert r.status_code == 200
    body = r.json()
    assert body["today"] == "2026-10-20" and body["trip_count"] == 3
    one = member.get("/api/trips/lake-weekend")
    assert one.status_code == 200
    assert one.json() == every(body)["lake-weekend"]


def test_api_trips_needs_sign_in(samples: Settings, prod: TestClient) -> None:
    assert prod.get("/api/trips").status_code == 401
    assert prod.get("/api/trips/lake-weekend").status_code == 401


def test_api_unknown_and_bad_trip_keys(samples: Settings, admin: TestClient) -> None:
    r = admin.get("/api/trips/no-such-trip")
    assert r.status_code == 404 and r.json()["detail"] == "No trip with that key."
    assert admin.get("/api/trips/city-break").status_code == 404  # tombstoned
    assert admin.get("/api/trips/Bad_Key").status_code == 422


def test_empty_before_any_trips(db) -> None:  # noqa: ANN001
    p = tr.trips_page(db)
    assert p["trip_count"] == 0 and p["source_run_id"] is None
    assert p["active"] == p["completed"] == p["older"] == []
    assert p["tiles"]["booked_cents"] == 0 and p["tiles"]["points"] == []
    assert tr.next_trip(db) is None
    assert tr.checks(db) == {"trip_count": 0, "unknown": [], "tagged": []}


# ---- Dashboard and Checks ----


def test_dashboard_next_trip_keeps_credits_line(samples: Settings) -> None:
    conn = connect(samples.db_path)
    try:
        d = dashboard(conn)
    finally:
        conn.close()
    assert d["next_trip"] == {
        "key": "ski-weekend",
        "name": "Ski weekend",
        "status": "booked",
        "start_date": "2027-01-15",
        "month": None,
        "cash_cents": 45_120,
        "estimate_low_cents": 30_000,
        "estimate_high_cents": 42_000,
        "tbd_count": 1,
    }
    assert d["credits_ending"] == {"count": 1, "left_cents": 1_000, "first_end": "2026-10-31"}


def test_dashboard_in_progress_trip_counts(
    samples: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_with_trips(
        samples,
        "2026-10-16",
        [trip("cabin-oct26", "in_progress", [], start_date="2026-10-18", end_date="2026-10-21")],
    )
    conn = connect(samples.db_path)
    try:
        assert dashboard(conn)["next_trip"]["key"] == "cabin-oct26"  # started, not ended
        on(monkeypatch, date(2026, 10, 22))
        assert dashboard(conn)["next_trip"]["key"] == "ski-weekend"
    finally:
        conn.close()


RENTAL_CAR = {
    "date": "2026-11-10",
    "kind": "trip",
    "key": "rental-car",
    "name": "Rental car",
    "amount_cents": 30_000,
    "account_name": "Summit card",
    "trip_key": "ski-weekend",
    "trip_name": "Ski weekend",
}


def _upcoming(settings: Settings) -> list[dict]:
    conn = connect(settings.db_path)
    try:
        return dashboard(conn)["upcoming"]
    finally:
        conn.close()


def test_dashboard_lists_trip_payments_due_within_30_days(
    samples: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Format 1.4: a booked trip item due in the next 30 days is an upcoming payment.
    On the same day it sorts after the plan payments (kind trip after plan)."""
    up = _upcoming(samples)
    trips_due = [u for u in up if u["kind"] == "trip"]
    assert trips_due == [RENTAL_CAR]
    nov10 = [(u["kind"], u["key"]) for u in up if u["date"] == "2026-11-10"]
    assert nov10[-2:] == [("plan", "plan-sofa"), ("trip", "rental-car")]
    assert all(u["trip_key"] is None and u["trip_name"] is None for u in up if u["kind"] != "trip")
    on(monkeypatch, date(2026, 10, 11))  # Nov 10 is day 30: still in
    assert RENTAL_CAR in _upcoming(samples)
    on(monkeypatch, date(2026, 10, 10))
    assert not any(u["kind"] == "trip" for u in _upcoming(samples))
    on(monkeypatch, date(2026, 11, 11))  # past: Travel says "Was due", the Dashboard drops it
    assert not any(u["kind"] == "trip" for u in _upcoming(samples))


def test_dashboard_trip_payments_skip_items_that_add_no_cash(samples: Settings) -> None:
    run_with_trips(
        samples,
        "2026-10-16",
        [
            trip(
                "cabin-nov26",
                "planning",
                [
                    item(
                        "covered", "food", "booked", "covered", amount="40.00", due_on="2026-11-02"
                    ),
                    item(
                        "free",
                        "lodging",
                        "booked",
                        "free_night",
                        amount="0.00",
                        due_on="2026-11-02",
                    ),
                    item("cash", "lodging", "booked", amount="99.00", due_on="2026-11-02"),
                ],
            )
        ],
    )
    got = [
        (u["trip_key"], u["key"], u["amount_cents"])
        for u in _upcoming(samples)
        if u["kind"] == "trip"
    ]
    assert got == [("cabin-nov26", "cash", 9_900), ("ski-weekend", "rental-car", 30_000)]


def test_checks_trips_block_keeps_rewards(samples: Settings) -> None:
    conn = connect(samples.db_path)
    try:
        c = checks(conn)
    finally:
        conn.close()
    t = c["trips"]
    assert t["unknown"] == [
        {
            "key": "day-trip-sep26",
            "status": "not_sent",
            "rows": 1,
            "plans": 0,
            "flags": 0,
            "redemptions": 0,
        }
    ]
    lines = {x["key"]: x for x in t["tagged"]}
    assert set(lines) == {"lake-weekend", "ski-weekend"}  # beach week has nothing to compare
    assert lines["lake-weekend"]["out_differs"] is False
    assert lines["ski-weekend"]["out_differs"] is True
    # The Rewards part is unchanged.
    assert c["rewards"]["stale"][0]["key"] == "lantern-hotels"
    assert {g["key"] for g in c["rewards"]["gaps"]} >= {"summit-spend", "fuel-bonus"}


def test_checks_and_dashboard_api(samples: Settings, admin: TestClient) -> None:
    body = admin.get("/api/checks").json()
    assert body["trips"]["unknown"][0]["key"] == "day-trip-sep26"
    assert body["rewards"]["stale"][0]["key"] == "lantern-hotels"
    dash = admin.get("/api/dashboard").json()
    assert dash["next_trip"]["key"] == "ski-weekend"
    assert dash["credits_ending"]["count"] == 1
    assert [u for u in dash["upcoming"] if u["kind"] == "trip"] == [RENTAL_CAR]
    trips_body = admin.get("/api/trips").json()
    assert [x["key"] for x in trips_body["open_items"]["due"]] == ["rental-car"]
