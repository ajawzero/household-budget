"""Rewards (docs/plans/phase-7.md sections 4 and 7): cycle states, the value
table, card spend from rows for caps and milestones, pace and projection,
fees net of credits, the API, the Dashboard line and the Checks block.

Fake samples only: the 2026-10-12 monthly run carries the rewards.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import config
from app.config import Settings
from app.db.connect import connect, write_connection
from app.importer.models import Rewards
from app.services import rewards as rw
from app.services.balances import clear_cache
from app.services.checks import checks
from app.services.dashboard import dashboard

TODAY = date(2026, 10, 20)


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
        return rw.rewards_page(conn)
    finally:
        conn.close()


def by_key(rows: list[dict]) -> dict[str, dict]:
    return {r["key"]: r for r in rows}


def snapshot_section(conn: sqlite3.Connection, section: str) -> object:
    row = conn.execute(
        "SELECT json FROM run_sections WHERE run_id = '2026-10-12' AND section = ?", (section,)
    ).fetchone()
    return json.loads(row["json"])


def add_monthly_run(settings: Settings, run_id: str, **sections: object) -> None:
    """A newer stored monthly run with only these sections (like test_state_v13)."""
    with write_connection(settings.db_path) as w:
        w.execute(
            """INSERT INTO import_runs (run_id, run_date, run_seq, mode, status, started_at,
                   schema_version)
               VALUES (?, ?, 1, 'monthly', 'imported', '2026-10-20T00:00:00+00:00', '1.3')""",
            (run_id, run_id[:10]),
        )
        for name, value in sections.items():
            w.execute(
                "INSERT INTO run_sections (run_id, section, sha256, json) VALUES (?, ?, ?, ?)",
                (run_id, name, f"{run_id}-{name}".ljust(64, "0")[:64], json.dumps(value)),
            )


# ---- cycle states ----


@pytest.mark.parametrize(
    ("amount", "used", "end", "state"),
    [
        (1000, 1000, "2026-10-31", "done"),
        (1000, 1500, "2026-09-30", "done"),  # done stays done after the end
        (1000, 600, "2026-10-31", "partial"),
        (1000, 0, "2026-10-31", "open"),
        (1000, 0, "2026-10-20", "open"),  # the last day is still open
        (1000, 600, "2026-10-19", "expired"),
        (1000, 0, "2026-09-30", "expired"),
    ],
)
def test_cycle_state_from_dates_and_amounts(amount: int, used: int, end: str, state: str) -> None:
    assert rw.cycle_state(amount, used, end, TODAY) == state


def test_benefits_in_use_or_lose_order(samples: Settings) -> None:
    out = page(samples)
    order = [(b["group"], b["key"]) for b in out["benefits"]]
    assert order == [
        ("open", "dining-credit"),  # ends Oct 31
        ("open", "airline-fee-credit"),  # Dec 31, then by name
        ("open", "streaming-credit"),
        ("open", "travel-credit"),  # Yearly travel credit
        ("open", "anniversary-night"),  # a free night, by its expiry
        ("auto", "wireless-credit"),
    ]
    groups = [g for g, _ in order]
    assert groups == sorted(groups, key=rw.GROUP_ORDER.__getitem__)
    opens = [b for b in out["benefits"] if b["group"] == "open"]
    assert [b["deadline"] for b in opens] == sorted(b["deadline"] for b in opens)
    b = by_key(out["benefits"])
    assert set(b) == {
        "airline-fee-credit",
        "anniversary-night",
        "dining-credit",
        "streaming-credit",
        "travel-credit",
        "wireless-credit",
    }  # caps and status perks have their own lists
    dining = b["dining-credit"]
    assert dining["cycle"]["start"] == "2026-10-01" and dining["cycle"]["state"] == "open"
    assert dining["days_left"] == 11 and dining["cycle"]["left_cents"] == 1000
    past = dining["past_cycles"]
    assert len(past) == 9 and [c["state"] for c in past[:8]] == ["done"] * 8
    sept = past[8]
    assert sept["state"] == "expired" and sept["used_cents"] == 600 and sept["left_cents"] == 0
    assert sept["days_left"] is None  # a past deadline shows no countdown
    assert b["travel-credit"]["enroll_flag"] is True
    assert b["anniversary-night"]["program_name"] == "Lantern Hotels"
    assert b["anniversary-night"]["deadline"] == "2027-03-31"


def test_used_this_cycle_and_upcoming(samples: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    on(monkeypatch, date(2026, 3, 20))
    b = by_key(page(samples)["benefits"])
    assert b["airline-fee-credit"]["group"] == "used"  # used Mar 14 for Jan to Jun
    assert b["streaming-credit"]["group"] == "used"
    assert b["dining-credit"]["group"] == "used"
    on(monkeypatch, date(2027, 1, 5))
    b = by_key(page(samples)["benefits"])
    assert b["dining-credit"]["group"] == "ended" and b["dining-credit"]["cycle"] is None
    assert b["dining-credit"]["past_cycles"][-1]["start"] == "2026-12-01"
    assert b["dining-credit"]["past_cycles"][-1]["amount_cents"] == 2000  # the bigger December


def test_past_deadlines_never_flag(samples: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    on(monkeypatch, date(2026, 11, 2))
    out = page(samples)
    b = by_key(out["benefits"])
    assert b["travel-credit"]["enroll_flag"] is False  # enroll-by was Oct 31
    october = b["dining-credit"]["past_cycles"][-1]
    assert october["start"] == "2026-10-01" and october["state"] == "expired"
    assert b["dining-credit"]["cycle"]["start"] == "2026-11-01"
    assert out["tiles"]["next_deadline"]["date"] >= "2026-11-02"


@pytest.mark.parametrize("day", [19, 20, 21])
@pytest.mark.parametrize("auto", [False, True])
def test_credit_end_limits_usable_cycles_and_summaries(
    samples: Settings, monkeypatch: pytest.MonkeyPatch, day: int, auto: bool
) -> None:
    conn = connect(samples.db_path)
    section = snapshot_section(conn, "rewards")
    conn.close()
    dining = next(b for b in section["benefits"] if b["key"] == "dining-credit")
    dining.update(ends_on="2026-10-20", auto=auto)
    add_monthly_run(samples, "2026-10-20", rewards=section)
    on(monkeypatch, date(2026, 10, day))
    out = page(samples)
    b = by_key(out["benefits"])["dining-credit"]
    conn = connect(samples.db_path)
    try:
        soon = rw.credits_ending(conn)
    finally:
        conn.close()
    if day <= 20:
        assert b["group"] == ("auto" if auto else "open")
        assert b["cycle"]["end"] == b["deadline"] == "2026-10-20"
        assert b["days_left"] == b["cycle"]["days_left"] == 20 - day
        assert (out["tiles"]["credits_left_cents"], out["tiles"]["credits_left_count"]) == (
            18_000,
            5,
        )
        if not auto:
            assert out["tiles"]["next_deadline"]["date"] == "2026-10-20"
            assert soon == {"count": 1, "left_cents": 1000, "first_end": "2026-10-20"}
        else:
            assert soon is None
    else:
        assert b["group"] == "ended" and b["cycle"] is None
        assert b["deadline"] is None and b["days_left"] is None
        assert (out["tiles"]["credits_left_cents"], out["tiles"]["credits_left_count"]) == (
            17_000,
            4,
        )
        assert soon is None
        assert out["tiles"]["next_deadline"]["key"] != "dining-credit"
        past = b["past_cycles"]
        assert len(past) == 10  # November and December never become usable cycles
        assert past[-1]["end"] == "2026-10-20" and past[-1]["state"] == "expired"
        assert past[-1]["left_cents"] == 0 and past[-1]["days_left"] is None


@pytest.mark.parametrize(("used", "state"), [("6.00", "expired"), ("10.00", "done")])
def test_ended_credit_preserves_past_usage(samples: Settings, used: str, state: str) -> None:
    conn = connect(samples.db_path)
    section = snapshot_section(conn, "rewards")
    conn.close()
    dining = next(b for b in section["benefits"] if b["key"] == "dining-credit")
    dining["ends_on"] = "2026-10-19"
    october = next(c for c in dining["cycles"] if c["start"] == "2026-10-01")
    october.update(used=used, used_on="2026-10-18")
    add_monthly_run(samples, "2026-10-20", rewards=section)
    b = by_key(page(samples)["benefits"])["dining-credit"]
    assert b["group"] == "ended" and b["cycle"] is None
    assert len(b["past_cycles"]) == 10
    assert b["past_cycles"][8]["used_cents"] == 600  # September history stays
    past = b["past_cycles"][-1]
    assert past["state"] == state and past["used_on"] == "2026-10-18"
    assert past["used_cents"] == (600 if state == "expired" else 1000)
    assert past["left_cents"] == 0 and past["days_left"] is None


@pytest.mark.parametrize("day", [19, 20, 21])
def test_credit_enrollment_deadline_cannot_outlast_benefit(
    samples: Settings, monkeypatch: pytest.MonkeyPatch, day: int
) -> None:
    conn = connect(samples.db_path)
    section = snapshot_section(conn, "rewards")
    conn.close()
    dining = next(b for b in section["benefits"] if b["key"] == "dining-credit")
    dining.update(ends_on="2026-10-20", enrolled=False, enroll_by="2026-10-31")
    add_monthly_run(samples, "2026-10-20", rewards=section)
    on(monkeypatch, date(2026, 10, day))
    out = page(samples)
    b = by_key(out["benefits"])["dining-credit"]
    assert b["enroll_by"] == "2026-10-20"
    assert b["enroll_flag"] is (day <= 20)
    if day <= 20:
        assert out["tiles"]["next_deadline"]["kind"] == "enroll_by"
        assert out["tiles"]["next_deadline"]["date"] == "2026-10-20"
    else:
        assert out["tiles"]["next_deadline"]["key"] != "dining-credit"


def test_benefit_ending_before_its_cycles_has_no_upcoming_credit(samples: Settings) -> None:
    conn = connect(samples.db_path)
    section = snapshot_section(conn, "rewards")
    conn.close()
    dining = next(b for b in section["benefits"] if b["key"] == "dining-credit")
    dining["ends_on"] = "2026-10-19"
    dining["cycles"] = [c for c in dining["cycles"] if c["start"] >= "2026-11-01"]
    add_monthly_run(samples, "2026-10-20", rewards=section)
    b = by_key(page(samples)["benefits"])["dining-credit"]
    assert b["group"] == "ended" and b["cycle"] is None and b["past_cycles"] == []


# ---- tiles, points and value ----


def test_tiles(samples: Settings) -> None:
    t = page(samples)["tiles"]
    # 84,250 x 1.2 to 1.8, 42,000 x 1.0 to 1.4, 12,000 x 0.5 to 0.7 cents, and $37.42 cash back
    assert t["value_low_cents"] == 101_100 + 42_000 + 6_000 + 3_742
    assert t["value_high_cents"] == 151_650 + 58_800 + 8_400 + 3_742
    assert t["valued_count"] == 4
    # Not used yet this cycle: dining 10, airline 50, streaming 15, travel 100, wireless 5 (auto)
    assert (t["credits_left_cents"], t["credits_left_count"]) == (18_000, 5)
    assert t["next_deadline"] == {
        "date": "2026-10-31",
        "label": "Enroll in Yearly travel credit",
        "kind": "enroll_by",
        "key": "travel-credit",
        "days_left": 11,
    }


def _deadline_rewards(samples: Settings, **program_changes: dict) -> Rewards:
    """The sample rewards with no credits, so programs and the free night lead."""
    conn = connect(samples.db_path)
    section = snapshot_section(conn, "rewards")
    conn.close()
    section["benefits"] = [b for b in section["benefits"] if b["kind"] != "credit"]
    for p in section["programs"]:
        p.update(program_changes.get(p["key"], {}))
    return Rewards.model_validate(section)


def test_next_deadline_timeshare_book_by_then_expiry(samples: Settings) -> None:
    r = _deadline_rewards(samples)
    assert rw.next_deadline(r, date(2026, 11, 2)) == {
        "date": "2026-11-30",
        "label": "Book Shared Vacation Club 2026",
        "kind": "book_by",
        "key": "shared-vacation-club",
        "days_left": 28,
    }
    assert rw.next_deadline(r, date(2026, 12, 1)) == {
        "date": "2026-12-31",
        "label": "Shared Vacation Club 2026 points expire",
        "kind": "expires",
        "key": "shared-vacation-club",
        "days_left": 30,
    }


def test_next_deadline_skips_a_timeshare_year_with_no_points_left(samples: Settings) -> None:
    programs = _deadline_rewards(samples).programs
    club = next(p for p in programs if p.key == "shared-vacation-club")
    allocations = [a.model_dump(exclude_none=True) for a in club.allocations or []]
    allocations[0]["used"] = "1000"  # 2026 is all used
    r = _deadline_rewards(samples, **{"shared-vacation-club": {"allocations": allocations}})
    assert rw.next_deadline(r, date(2026, 11, 2)) == {
        "date": "2027-03-31",
        "label": "Anniversary free night expires",
        "kind": "expires",
        "key": "anniversary-night",
        "days_left": 149,
    }


def test_next_deadline_program_expiry(samples: Settings) -> None:
    r = _deadline_rewards(samples, **{"lantern-hotels": {"expires_on": "2027-02-15"}})
    assert rw.next_deadline(r, date(2027, 1, 5)) == {
        "date": "2027-02-15",
        "label": "Lantern Hotels points expire",
        "kind": "expires",
        "key": "lantern-hotels",
        "days_left": 41,
    }


def test_value_table(samples: Settings) -> None:
    out = page(samples)
    table = out["value_table"]
    rows = by_key(table["rows"])
    assert [r["key"] for r in table["rows"]] == [
        "summit-points",
        "harbor-miles",
        "lantern-hotels",
        "pine-cash-back",
        "shared-vacation-club",
    ]
    assert (rows["summit-points"]["low_cents"], rows["summit-points"]["high_cents"]) == (
        101_100,
        151_650,
    )
    assert rows["pine-cash-back"]["basis"] == "face"
    assert rows["pine-cash-back"]["low_cents"] == rows["pine-cash-back"]["high_cents"] == 3_742
    club = rows["shared-vacation-club"]
    assert club["basis"] == "not_valued" and club["low_cents"] is None
    assert table["low_cents"] == out["tiles"]["value_low_cents"]
    assert table["high_cents"] == out["tiles"]["value_high_cents"]


def test_value_rounds_half_up() -> None:
    assert rw._value_cents("125", "0.5") == 63  # 62.5 cents
    assert rw._value_cents("3", "1.25") == 4  # 3.75 cents
    assert rw._value_cents("0", "1.8") == 0


def test_programs(samples: Settings) -> None:
    programs = page(samples)["programs"]
    progs = by_key(programs)
    keys = [p["key"] for p in programs]
    assert keys[:2] == ["harbor-miles", "summit-points"]  # primary first, by name
    summit = progs["summit-points"]
    assert summit["balance_micro"] == 84_250 * 1_000_000 and summit["stale"] is False
    assert [c["name"] for c in summit["earned_on"]] == ["Summit card"]
    assert summit["best_uses"] == ["Transfer to airline partners", "Hotel stays at peak times"]
    harbor = progs["harbor-miles"]
    assert harbor["owner_name"] == "Sam" and harbor["status_via_name"] == "Cobalt card"
    lantern = progs["lantern-hotels"]
    assert lantern["stale"] is True  # Jun 30 is over 90 days back
    # A secondary program carries best uses, avoid and a points expiry too.
    assert lantern["group"] == "secondary"
    assert lantern["best_uses"] == ["Free nights in the off season"]
    assert lantern["avoid"] == ["Points plus cash stays"]
    assert lantern["expires_on"] == "2027-09-30"
    club = progs["shared-vacation-club"]
    years = {a["year"]: a for a in club["allocations"]}
    assert years[2026]["left_micro"] == 200 * 1_000_000
    assert years[2027]["left_micro"] == 800 * 1_000_000  # 200 pulled forward
    assert years[2026]["book_by"] == "2026-11-30"


def test_redemptions_show_have_from_the_balance(samples: Settings) -> None:
    out = page(samples)["redemptions"]
    assert [(x["status"], x["key"]) for x in out] == [
        ("ready", "ski-flights"),
        ("consider", "beach-hotel"),
        ("done", "spring-stay"),  # format 1.4 sample: a done redemption with done_on
    ]
    ski, beach, spring = out
    assert ski["points_needed_micro"] == 50_000 * 1_000_000
    assert ski["have_micro"] == 42_000 * 1_000_000 and ski["enough"] is False
    assert beach["have_micro"] == 84_250 * 1_000_000 and beach["enough"] is True
    assert ski["trip_key"] == "ski-weekend" and ski["trip_name"] == "Ski weekend"
    assert beach["trip_name"] == "Beach week"
    assert spring["points_needed_micro"] == 20_000 * 1_000_000 and spring["enough"] is None
    assert spring["trip_key"] is None and spring["trip_name"] is None
    assert spring["done_on"] == "2026-04-18"  # format 1.4
    assert ski["done_on"] is None and beach["done_on"] is None


def test_done_redemptions_newest_first_and_last_redeemed(samples: Settings) -> None:
    """Format 1.4: done ones newest first by done_on, undated ones last. A program's
    last redeemed day is the newest done_on among its own done redemptions."""
    conn = connect(samples.db_path)
    try:
        section = snapshot_section(conn, "rewards")
    finally:
        conn.close()
    base = {"status": "done", "points_needed": "1000"}
    section["redemptions"] += [
        {
            **base,
            "key": "march-stay",
            "title": "A march stay",
            "program_key": "lantern-hotels",
            "done_on": "2026-03-01",
        },
        {
            **base,
            "key": "fall-flight",
            "title": "Fall flight",
            "program_key": "harbor-miles",
            "done_on": "2026-09-15",
        },
        {**base, "key": "old-stay", "title": "An old stay", "program_key": "lantern-hotels"},
    ]
    add_monthly_run(samples, "2026-10-20", rewards=section)
    p = page(samples)
    done = [(x["key"], x["done_on"]) for x in p["redemptions"] if x["status"] == "done"]
    assert done == [
        ("fall-flight", "2026-09-15"),
        ("spring-stay", "2026-04-18"),
        ("march-stay", "2026-03-01"),
        ("old-stay", None),
    ]
    assert all(x["done_on"] is None for x in p["redemptions"] if x["status"] != "done")
    last = {x["key"]: x["last_redeemed_on"] for x in p["programs"]}
    assert last["lantern-hotels"] == "2026-04-18"  # its own newest, not harbor's
    assert last["harbor-miles"] == "2026-09-15"
    assert last["summit-points"] is None  # its beach hotel is only a consider


def test_program_views_match_the_page(samples: Settings) -> None:
    """The PATCH and balance answers carry last_redeemed_on like the page."""
    conn = connect(samples.db_path)
    try:
        views = rw.program_views(conn, {"lantern-hotels", "summit-points"})
        programs = by_key(rw.rewards_page(conn)["programs"])
    finally:
        conn.close()
    assert views["lantern-hotels"] == programs["lantern-hotels"]
    assert views["lantern-hotels"]["last_redeemed_on"] == "2026-04-18"
    assert views["summit-points"] == programs["summit-points"]
    assert views["summit-points"]["last_redeemed_on"] is None


def test_a_done_redemption_is_never_short(samples: Settings) -> None:
    # Its points_needed is the points it used, so the balance now says nothing.
    conn = connect(samples.db_path)
    try:
        section = snapshot_section(conn, "rewards")
    finally:
        conn.close()
    for x in section["redemptions"]:
        if x["key"] == "ski-flights":
            x["status"] = "done"  # 50,000 needed against a 42,000 balance
    add_monthly_run(samples, "2026-10-20", rewards=section)
    out = page(samples)["redemptions"]
    assert [(x["status"], x["key"]) for x in out] == [
        ("consider", "beach-hotel"),
        ("done", "spring-stay"),  # dated done items first, newest first; undated last
        ("done", "ski-flights"),
    ]
    beach, _, ski = out
    assert ski["enough"] is None and ski["points_needed_micro"] == 50_000 * 1_000_000
    assert ski["trip_name"] == "Ski weekend"
    assert beach["enough"] is True


def test_a_redemption_names_only_a_trip_the_app_has(samples: Settings, admin: TestClient) -> None:
    conn = connect(samples.db_path)
    try:
        section = snapshot_section(conn, "rewards")
    finally:
        conn.close()
    for x in section["redemptions"]:
        if x["key"] == "beach-hotel":
            x["trip_key"] = "no-such-trip"
        else:
            x.pop("trip_key", None)
    add_monthly_run(samples, "2026-10-20", rewards=section)
    out = by_key(admin.get("/api/rewards").json()["redemptions"])
    assert (out["beach-hotel"]["trip_key"], out["beach-hotel"]["trip_name"]) == (
        "no-such-trip",
        None,
    )
    assert (out["ski-flights"]["trip_key"], out["ski-flights"]["trip_name"]) == (None, None)


def test_routing_in_display_order(samples: Settings) -> None:
    routes = page(samples)["routing"]
    assert [r["display_order"] for r in routes] == sorted(r["display_order"] for r in routes)
    assert routes[0]["spend"] == "Dining and groceries"
    assert routes[0]["account_name"] == "Summit card" and routes[0]["earn"] == "4x"


# ---- card spend from rows ----


def _rows_sum(conn: sqlite3.Connection, account: str, year: str, types: tuple[str, ...]) -> int:
    marks = ", ".join("?" for _ in types)
    return -conn.execute(
        f"""SELECT coalesce(sum(amount_cents), 0) FROM transactions
            WHERE account_key = ? AND removed_at IS NULL AND substr(txn_date, 1, 4) = ?
              AND txn_type IN ({marks})""",
        (account, year, *types),
    ).fetchone()[0]


def test_card_spend_counts_purchases_minus_refunds(samples: Settings) -> None:
    conn = connect(samples.db_path)
    try:
        s = rw.card_spend(conn, "summit-card", 2026)
        assert s.cents == _rows_sum(conn, "summit-card", "2026", ("purchase", "refund"))
        assert s.cents == 530_242
        # The Plan It purchase counts. Plan fees, the annual fee, payments and the
        # Plan It adjustments do not.
        plan = conn.execute(
            """SELECT -amount_cents FROM transactions WHERE account_key = 'summit-card'
               AND plan_key IS NOT NULL AND txn_type = 'purchase'"""
        ).fetchone()[0]
        assert plan == 60_000
        assert _rows_sum(conn, "summit-card", "2026", ("plan_fee",)) == 2_000
        assert _rows_sum(conn, "summit-card", "2026", ("fee",)) == 25_000
        assert s.cents == sum(s.by_month.values()) == sum(s.by_cardholder.values())
        # A refund nets against purchases.
        june = s.by_month["2026-06"]
        assert june == 57_705 + 25_071 - 4_500
        # December 2025 rows on the same statement stay out.
        assert s.rows_from == "2026-01-01" and s.rows_through == "2026-09-10"
        assert "2025-12" not in s.by_month
        cobalt = rw.card_spend(conn, "cobalt-card", 2026)
        assert cobalt.rows_from == "2026-05-01"  # the statements start in May
        assert rw.card_spend(conn, "summit-card", 2027).rows_through is None  # no rows yet
    finally:
        conn.close()


def test_card_spend_leaves_out_hidden_rows(samples: Settings) -> None:
    with write_connection(samples.db_path) as w:
        key = w.execute(
            """SELECT txn_key, amount_cents FROM transactions WHERE account_key = 'summit-card'
               AND txn_type = 'purchase' AND txn_date = '2026-04-20'"""
        ).fetchone()
        w.execute(
            """INSERT INTO transaction_overrides (txn_key, hidden, updated_at)
               VALUES (?, 1, '2026-10-20T00:00:00+00:00')""",
            (key["txn_key"],),
        )
    conn = connect(samples.db_path)
    try:
        assert rw.card_spend(conn, "summit-card", 2026).cents == 530_242 + key["amount_cents"]
    finally:
        conn.close()


def test_cap_spend_by_category_and_cardholder(samples: Settings) -> None:
    conn = connect(samples.db_path)
    try:
        s = rw.card_spend(conn, "summit-card", 2026, ["fuel"], at=("2026-06-30",))
        want = -conn.execute(
            """SELECT sum(amount_cents) FROM v_transactions WHERE account_key = 'summit-card'
               AND category_key = 'fuel' AND txn_type IN ('purchase', 'refund')
               AND txn_date >= '2026-01-01'"""
        ).fetchone()[0]
        assert s.cents == want > 0
        assert set(s.by_cardholder) <= {"alex", "sam", None}
        assert sum(s.by_cardholder.values()) == s.cents
        assert 0 < s.at["2026-06-30"] < s.cents
    finally:
        conn.close()


def test_cap_on_the_page(samples: Settings) -> None:
    caps = page(samples)["caps"]
    assert len(caps) == 1
    fuel = caps[0]
    assert fuel["key"] == "fuel-bonus" and fuel["account_name"] == "Cobalt card"
    assert fuel["cap_cents"] == 600_000 and fuel["categories"] == [{"key": "fuel", "name": "Fuel"}]
    # No fuel rows on the Cobalt card in the samples: the chat's number shows too.
    assert fuel["spent_cents"] == 0 and fuel["left_cents"] == 600_000
    assert fuel["reported_spent_cents"] == 182_040 and fuel["reported_differs"] is True
    assert fuel["app_at_reported_cents"] == 0


def test_cap_split_by_cardholder_on_the_page(samples: Settings) -> None:
    conn = connect(samples.db_path)
    rewards = snapshot_section(conn, "rewards")
    conn.close()
    for b in rewards["benefits"]:
        if b["key"] == "fuel-bonus":
            b["account_key"] = "summit-card"
    add_monthly_run(samples, "2026-10-20", rewards=rewards)
    fuel = page(samples)["caps"][0]
    names = [h["name"] for h in fuel["by_cardholder"]]
    assert set(names) == {"Alex", "Sam"}
    assert sum(h["cents"] for h in fuel["by_cardholder"]) == fuel["spent_cents"] > 0
    cents = [h["cents"] for h in fuel["by_cardholder"]]
    assert cents == sorted(cents, reverse=True)


# ---- pace and projection ----


def test_pace_projection_and_need() -> None:
    # 90 days in (Jan 1 to Mar 31), 275 days left, in a 365-day year.
    p = rw.pace(100_000, 1_000_000, 2026, "2026-03-31")
    assert p["projection_cents"] == 405_556  # 100,000 x 365 / 90
    assert p["need_per_month_cents"] == 99_545  # 900,000 x 365 / (12 x 275)
    done = rw.pace(1_200_000, 1_000_000, 2026, "2026-10-31")
    assert done["need_per_month_cents"] is None
    year_end = rw.pace(500_000, 1_000_000, 2026, "2026-12-31")
    assert year_end == {"projection_cents": 500_000, "need_per_month_cents": None}
    assert rw.pace(0, 1_000_000, 2026, None) == {
        "projection_cents": None,
        "need_per_month_cents": None,
    }


def test_milestones(samples: Settings) -> None:
    ms = page(samples)["milestones"]
    assert [m["key"] for m in ms] == ["summit-spend", "cobalt-spend"]  # open first
    summit, cobalt = ms
    assert summit["spent_cents"] == 530_242
    assert summit["to_go_cents"] == 3_000_000 - 530_242
    assert summit["rows_through"] == "2026-09-10"
    assert (
        summit["projection_cents"]
        == rw.pace(530_242, 3_000_000, 2026, "2026-09-10")["projection_cents"]
    )
    assert summit["pace"] == "behind" and summit["need_per_month_cents"] > 0
    months = summit["months"]
    assert [m["month"] for m in months] == [f"2026-{n:02d}" for n in range(1, 10)]
    cums = [m["cumulative_cents"] for m in months]
    assert cums == sorted(cums) and cums[-1] == summit["spent_cents"]
    # The chat's 14,650.00 as of Sep 30 is more than $25 away from the rows.
    assert summit["reported_spent_cents"] == 1_465_000 and summit["reported_differs"] is True
    assert summit["app_at_reported_cents"] == 530_242
    assert cobalt["status"] == "met" and cobalt["pace"] == "met"
    assert cobalt["to_go_cents"] == 0 and cobalt["need_per_month_cents"] is None
    assert cobalt["rows_from"] == "2026-05-01"
    # No rows before May: the chart starts there, not with four months at zero.
    assert [m["month"] for m in cobalt["months"]] == [f"2026-{n:02d}" for n in range(5, 10)]
    assert cobalt["months"][-1]["cumulative_cents"] == cobalt["spent_cents"]


def test_reported_spend_within_25_dollars_shows_one_number(samples: Settings) -> None:
    conn = connect(samples.db_path)
    rewards = snapshot_section(conn, "rewards")
    conn.close()
    for m in rewards["milestones"]:
        if m["key"] == "summit-spend":
            m["reported_spent"] = "5320.00"  # rows say 5,302.42 through Sep 30
    add_monthly_run(samples, "2026-10-20", rewards=rewards)
    summit = by_key(page(samples)["milestones"])["summit-spend"]
    assert summit["reported_differs"] is False


# ---- bills on a milestone card (gap audit C7) ----


def test_milestone_bills_on_the_samples(samples: Settings) -> None:
    ms = by_key(page(samples)["milestones"])
    summit, cobalt = ms["summit-spend"], ms["cobalt-spend"]
    # Quarterly 120.00 is 40.00 a month. The card's annual fee is left out: fees
    # never count toward the milestone.
    assert summit["bills"] == [
        {"key": "pest-control", "name": "Shield Pest Control", "monthly_cents": 4_000},
    ]
    assert summit["bills_monthly_cents"] == 4_000
    # Wave Mobile is paid from checking and set to move to the Summit card.
    assert summit["bills_to_move"] == [
        {
            "key": "wave-mobile",
            "name": "Wave Mobile",
            "monthly_cents": 6_500,
            "account_name": "Sam checking (Harbor)",
        }
    ]
    assert [b["key"] for b in cobalt["bills"]] == ["personal-subscription"]
    assert cobalt["bills_monthly_cents"] == 1_499 and cobalt["bills_to_move"] == []


def test_milestone_bills_count_active_items_only(samples: Settings) -> None:
    conn = connect(samples.db_path)
    recurring = snapshot_section(conn, "recurring")
    conn.close()
    status = {"pest-control": "paused", "summit-annual-fee": "ended", "wave-mobile": "ending"}
    for item in recurring:
        item["status"] = status.get(item["key"], item["status"])
        if item["status"] in ("ending", "ended"):
            item.pop("keep", None)  # format 1.4: keep is only for an active or paused item

    def bill(key: str, name: str, amount: str, on: str, **kw: str) -> dict:
        return {
            "key": key,
            "name": name,
            "category_key": "subscriptions",
            "amount": amount,
            "cadence": "monthly",
            "status": kw.pop("status", "active"),
            "account_key": on,
            **kw,
        }

    recurring += [
        # On the card already: a bill, not a move.
        bill(
            "on-summit", "Beta stream", "40.00", "summit-card", preferred_account_key="summit-card"
        ),
        # Same monthly cents: the name breaks the tie.
        bill("on-summit-2", "alpha news", "50.00", "summit-card", credit_offset="10.00"),
        bill(
            "from-cobalt",
            "Cloud storage",
            "9.99",
            "cobalt-card",
            preferred_account_key="summit-card",
        ),
        bill(
            "paused-move",
            "Gym",
            "30.00",
            "harbor-checking",
            status="paused",
            preferred_account_key="summit-card",
        ),
    ]
    add_monthly_run(samples, "2026-10-20", recurring=recurring)
    ms = by_key(page(samples)["milestones"])
    summit = ms["summit-spend"]
    assert [b["key"] for b in summit["bills"]] == ["on-summit-2", "on-summit"]
    assert [b["monthly_cents"] for b in summit["bills"]] == [4_000, 4_000]  # net of the credit
    assert summit["bills_monthly_cents"] == 8_000
    moves = summit["bills_to_move"]
    assert [b["key"] for b in moves] == ["wave-mobile", "from-cobalt"]  # ending still counts
    assert moves[1] == {
        "key": "from-cobalt",
        "name": "Cloud storage",
        "monthly_cents": 999,
        "account_name": "Cobalt card",
    }
    # The Cobalt card keeps its own bill; the one moving away is still on it.
    assert [b["key"] for b in ms["cobalt-spend"]["bills"]] == [
        "personal-subscription",
        "from-cobalt",
    ]


def _keys(obj: object) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in _keys(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in _keys(v)}
    return set()


def test_milestone_bills_stay_out_of_ai(samples: Settings) -> None:
    """Ask's facts and the trip planner's brief never carry milestones or their bills."""
    from app.ai.facts import build
    from app.ai.planner import rewards_brief

    kept_out = {"milestones", "bills_monthly_cents", "bills_to_move"}
    conn = connect(samples.db_path)
    try:
        assert page(samples)["milestones"][0]["bills_to_move"]  # the samples have one
        facts = build(conn, None)
        brief = rewards_brief(conn)
    finally:
        conn.close()
    assert facts and not kept_out & _keys([v for _, _, v in facts])
    assert not (kept_out | {"bills"}) & _keys(brief)
    assert "Shield Pest Control" not in repr(brief)


# ---- fees ----


def test_fees_from_recurring_never_both(samples: Settings) -> None:
    fees = page(samples)["fees"]
    assert [r["key"] for r in fees["rows"]] == ["summit-annual-fee"]  # not also the registry's
    row = fees["rows"][0]
    assert row["source"] == "recurring" and row["gross_cents"] == 25_000
    assert row["renews_on"] == "2027-03-01"
    credits = {c["key"]: c for c in row["credits"]}
    assert set(credits) == {"airline-fee-credit", "dining-credit", "travel-credit"}
    assert credits["airline-fee-credit"]["cents"] == 10_000  # two cycles of 50.00
    assert credits["dining-credit"]["cents"] == 10_000 and credits["dining-credit"]["counted"]
    assert credits["dining-credit"]["full_cents"] == 11 * 1_000 + 2_000  # the bigger December
    assert credits["travel-credit"]["cents"] == 10_000
    assert row["credits_cents"] == 30_000 and row["net_cents"] == -5_000
    assert row["net_positive"] is True
    assert (fees["gross_cents"], fees["credits_cents"], fees["net_cents"]) == (
        25_000,
        30_000,
        -5_000,
    )


def test_fees_registry_per_person_and_memberships(samples: Settings) -> None:
    conn = connect(samples.db_path)
    recurring = snapshot_section(conn, "recurring")
    conn.close()
    recurring += [
        {
            "key": "summit-user-fee",
            "name": "Summit card authorized user fee",
            "category_key": "annual-fees",
            "amount": "75.00",
            "cadence": "annual",
            "status": "active",
            "account_key": "summit-card",
            "owner": "sam",
        },
        {
            "key": "warehouse-club",
            "name": "Warehouse club",
            "category_key": "memberships",
            "amount": "65.00",
            "cadence": "annual",
            "status": "active",
            "renewal_date": "2027-05-01",
        },
        {
            "key": "museum-monthly",
            "name": "Museum",
            "category_key": "memberships",
            "amount": "8.00",
            "cadence": "monthly",
            "status": "active",
        },
        {
            "key": "old-card-fee",
            "name": "Old card fee",
            "category_key": "annual-fees",
            "amount": "95.00",
            "cadence": "annual",
            "status": "ended",
        },
    ]
    recurring.sort(key=lambda x: x["key"])
    add_monthly_run(samples, "2026-10-20", recurring=recurring)
    with write_connection(samples.db_path) as w:
        w.execute(
            """UPDATE accounts SET annual_fee_cents = 9500, fee_renews_on = '2027-06-01'
               WHERE account_key = 'cobalt-card'"""
        )
    rows = by_key(page(samples)["fees"]["rows"])
    assert set(rows) == {
        "summit-annual-fee",
        "summit-user-fee",
        "card:cobalt-card",
        "warehouse-club",
    }
    user = rows["summit-user-fee"]
    assert user["gross_cents"] == 7_500 and user["credits"] == [] and user["net_cents"] == 7_500
    assert user["owner_name"] == "Sam"
    assert rows["summit-annual-fee"]["credits_cents"] == 30_000  # credits on the largest row only
    cobalt = rows["card:cobalt-card"]
    assert cobalt["source"] == "registry" and cobalt["renews_on"] == "2027-06-01"
    assert cobalt["credits"] == [
        {
            "key": "streaming-credit",
            "name": "Streaming credit",
            "cents": 6_000,
            "full_cents": 6_000,
            "counted": False,
        }
    ]
    assert cobalt["net_cents"] == 3_500 and cobalt["net_positive"] is False
    club = rows["warehouse-club"]
    assert club["kind"] == "membership" and club["credits"] == [] and club["gross_cents"] == 6_500


@pytest.mark.parametrize(
    ("status", "keys"),
    [("ending", ["summit-annual-fee"]), ("paused", []), ("ended", [])],
)
def test_fees_inactive_fee_item_keeps_the_registry_fee_out(
    samples: Settings, status: str, keys: list[str]
) -> None:
    conn = connect(samples.db_path)
    recurring = snapshot_section(conn, "recurring")
    registry = conn.execute(
        "SELECT annual_fee_cents FROM accounts WHERE account_key = 'summit-card'"
    ).fetchone()[0]
    conn.close()
    assert registry == 25_000  # the registry fee that must not come back
    for item in recurring:
        if item["key"] == "summit-annual-fee":
            item["status"] = status  # paused: waived this year; ended: card downgraded
            if status != "paused":
                item.pop("keep")  # format 1.4: keep is only for an active or paused item
    add_monthly_run(samples, "2026-10-20", recurring=recurring)
    rows = page(samples)["fees"]["rows"]
    assert [r["key"] for r in rows] == keys
    if status == "ending":
        assert rows[0]["source"] == "recurring" and rows[0]["gross_cents"] == 25_000
        assert rows[0]["credits_cents"] == 30_000


def test_fees_credit_ending_mid_year_matches_benefits(samples: Settings) -> None:
    conn = connect(samples.db_path)
    section = snapshot_section(conn, "rewards")
    conn.close()
    airline = next(b for b in section["benefits"] if b["key"] == "airline-fee-credit")
    airline["ends_on"] = "2026-06-30"  # the Jul to Dec cycle stays listed
    add_monthly_run(samples, "2026-10-20", rewards=section)
    out = page(samples)
    b = by_key(out["benefits"])["airline-fee-credit"]
    assert b["group"] == "ended" and [c["start"] for c in b["past_cycles"]] == ["2026-01-01"]
    row = by_key(out["fees"]["rows"])["summit-annual-fee"]
    credit = by_key(row["credits"])["airline-fee-credit"]
    assert credit["cents"] == credit["full_cents"] == 5_000  # Jan to Jun only
    assert row["credits_cents"] == 25_000 and row["net_cents"] == 0
    assert row["net_positive"] is False


def test_fees_count_a_free_night_with_counted_value(samples: Settings) -> None:
    conn = connect(samples.db_path)
    section = snapshot_section(conn, "rewards")
    conn.close()
    night = next(b for b in section["benefits"] if b["key"] == "anniversary-night")
    night["counted_value"] = "150.00"
    add_monthly_run(samples, "2026-10-20", rewards=section)
    row = by_key(page(samples)["fees"]["rows"])["summit-annual-fee"]
    # Same value both ways, so the list shows no "of" figure.
    assert by_key(row["credits"])["anniversary-night"] == {
        "key": "anniversary-night",
        "name": "Anniversary free night",
        "cents": 15_000,
        "full_cents": 15_000,
        "counted": True,
    }
    assert row["credits_cents"] == 45_000 and row["net_cents"] == -20_000
    assert row["net_positive"] is True
    # The status perk has no counted_value, so it stays out.
    assert "hotel-status" not in by_key(row["credits"])


def test_year_value_without_cycles() -> None:
    from app.importer.models import RewardBenefit

    base = {"key": "x", "account_key": "summit-card", "name": "X", "kind": "credit"}
    monthly = RewardBenefit.model_validate({**base, "cadence": "monthly", "amount": "10.00"})
    assert rw._year_value(monthly, 2026) == 12_000
    multi = RewardBenefit.model_validate({**base, "cadence": "multi_year", "amount": "100.00"})
    assert rw._year_value(multi, 2026) == 0
    gone = RewardBenefit.model_validate(
        {**base, "cadence": "annual", "amount": "50.00", "ends_on": "2025-12-31"}
    )
    assert rw._year_value(gone, 2026) == 0


@pytest.mark.parametrize(
    ("cadence", "amount", "ends_on", "cents"),
    [
        ("monthly", "10.00", "2026-06-30", 6_000),  # Jan to Jun
        ("monthly", "10.00", "2026-06-01", 6_000),  # June starts on the end date
        ("quarterly", "15.00", "2026-08-15", 4_500),  # Jan, Apr and Jul
        ("semi_annual", "50.00", "2026-06-30", 5_000),
        ("semi_annual", "50.00", "2026-07-01", 10_000),
        ("annual", "100.00", "2026-03-31", 10_000),
        ("monthly", "10.00", "2027-03-31", 12_000),  # ends next year: the whole year
        ("multi_year", "100.00", "2026-06-30", 0),
    ],
)
def test_year_value_without_cycles_stops_at_the_end_date(
    cadence: str, amount: str, ends_on: str, cents: int
) -> None:
    from app.importer.models import RewardBenefit

    b = RewardBenefit.model_validate(
        {
            "key": "x",
            "account_key": "summit-card",
            "name": "X",
            "kind": "credit",
            "cadence": cadence,
            "amount": amount,
            "ends_on": ends_on,
        }
    )
    assert rw._year_value(b, 2026) == rw._full_value(b, 2026) == cents


def test_year_value_with_cycles_and_counted_value_after_an_end_date() -> None:
    from app.importer.models import RewardBenefit

    base = {"key": "x", "account_key": "summit-card", "name": "X", "kind": "credit"}
    quarters = [
        {"start": f"2026-{m:02d}-01", "end": f"2026-{m + 2:02d}-{d}"}
        for m, d in ((1, "31"), (4, "30"), (7, "30"), (10, "31"))
    ]
    streaming = RewardBenefit.model_validate(
        {
            **base,
            "cadence": "quarterly",
            "amount": "15.00",
            "cycles": quarters,
            "ends_on": "2026-08-15",
        }
    )
    assert rw._full_value(streaming, 2026) == 4_500  # the October cycle starts after the end
    # A counted_value is the chat's figure for the year: kept as sent mid-year,
    # zero once the benefit ended before the year.
    counted = {**base, "cadence": "annual", "amount": "100.00", "counted_value": "50.00"}
    mid = RewardBenefit.model_validate({**counted, "ends_on": "2026-06-30"})
    assert rw._year_value(mid, 2026) == 5_000
    gone = RewardBenefit.model_validate({**counted, "ends_on": "2025-12-31"})
    assert rw._year_value(gone, 2026) == 0
    night = RewardBenefit.model_validate(
        {
            "key": "n",
            "account_key": "summit-card",
            "name": "N",
            "kind": "free_night",
            "program_key": "lantern-hotels",
            "counted_value": "150.00",
            "ends_on": "2025-12-31",
        }
    )
    assert rw._year_value(night, 2026) == 0


# ---- before the first 1.3 run ----


def test_no_rewards_before_a_13_run(db: sqlite3.Connection) -> None:
    out = rw.rewards_page(db)
    assert out["source_run_id"] is None and out["tiles"] is None
    assert out["programs"] == [] and out["benefits"] == [] and out["fees"]["rows"] == []
    assert rw.credits_ending(db) is None
    assert rw.checks(db) is None


# ---- API, Dashboard and Checks ----


def test_api_rewards(samples: Settings, admin: TestClient) -> None:
    r = admin.get("/api/rewards")
    assert r.status_code == 200
    body = r.json()
    assert body["source_run_id"] == "2026-10-12" and body["today"] == "2026-10-20"
    assert body["tiles"]["credits_left_count"] == 5
    assert body["highlights"][0] == "The dining credit resets on the first of each month"
    assert [p["key"] for p in body["perks"]] == ["hotel-status"]
    assert body["perks"][0]["program_name"] == "Lantern Hotels"
    summit = body["milestones"][0]
    assert summit["bills_monthly_cents"] == 4_000
    assert summit["bills_to_move"][0]["account_name"] == "Sam checking (Harbor)"


def test_api_rewards_needs_sign_in(samples: Settings, prod: TestClient) -> None:
    assert prod.get("/api/rewards").status_code == 401


def test_dashboard_credits_line(samples: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = connect(samples.db_path)
    try:
        # Dining (Oct 31) is the one. Wireless ends then too, but loads by itself.
        assert dashboard(conn)["credits_ending"] == {
            "count": 1,
            "left_cents": 1_000,
            "first_end": "2026-10-31",
        }
        on(monkeypatch, date(2026, 10, 5))  # 26 days to the end of October
        assert dashboard(conn)["credits_ending"] is None
        on(monkeypatch, date(2026, 12, 20))  # airline, streaming, travel and dining end Dec 31
        line = dashboard(conn)["credits_ending"]
        assert line["count"] == 4 and line["left_cents"] == 5_000 + 1_500 + 10_000 + 2_000
    finally:
        conn.close()


def test_checks_rewards_block(samples: Settings) -> None:
    conn = connect(samples.db_path)
    try:
        block = checks(conn)["rewards"]
    finally:
        conn.close()
    assert block["source_run_id"] == "2026-10-12"
    assert block["stale"] == [
        {
            "key": "lantern-hotels",
            "name": "Lantern Hotels",
            "what": "balance",
            "as_of": "2026-06-30",
        }
    ]
    gaps = {g["key"]: g for g in block["gaps"]}
    summit = gaps["summit-spend"]
    assert summit["kind"] == "milestone" and summit["status"] == "differs"
    assert (summit["app_cents"], summit["chat_cents"]) == (530_242, 1_465_000)
    assert summit["note"] == (
        "Summit card: the rows reach Sep 10, 2026. The chat counts to Sep 30, 2026."
    )
    fuel = gaps["fuel-bonus"]
    assert fuel["kind"] == "cap" and fuel["name"] == "Cobalt card fuel bonus"
    assert fuel["note"] == "Cobalt card: the rows start May 1, 2026, so the app counts less."
    assert "cobalt-spend" not in gaps  # no reported spend to compare


def test_checks_api_carries_rewards(samples: Settings, admin: TestClient) -> None:
    body = admin.get("/api/checks").json()
    assert body["rewards"]["stale"][0]["key"] == "lantern-hotels"
    assert admin.get("/api/dashboard").json()["credits_ending"]["count"] == 1
