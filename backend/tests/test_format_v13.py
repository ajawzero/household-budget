"""Format 1.3 (docs/plans/phase-7.md sections 2 and 7): versions, the rewards and
trips item rules, and the booking-code redaction rule. Snapshot-level rules
(sort order, cross references, sections per mode) are bad cases in bad_cases.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bad_cases import SAMPLES
from pydantic import ValidationError

from app.importer import models as m
from app.importer.models import iter_text, load_snapshot, parse_csv
from app.importer.sanitize import detect_sensitive, scan_model, sensitive_spans

REPO = Path(__file__).resolve().parents[2]


def _rules(model: type, data: dict) -> set[str]:
    try:
        model.model_validate(data)
    except ValidationError as exc:
        return {i.rule for i in m.issues_from_error(exc)}
    return set()


# --------------------------------------------------------------------------
# Versions and sections
# --------------------------------------------------------------------------


def test_versions_and_sections() -> None:
    assert "1.3" in m.KNOWN_VERSIONS and m.version_rule("1.3") is None
    v12, v13 = m.REQUIRED_SECTIONS["1.2"], m.REQUIRED_SECTIONS["1.3"]
    assert v13["monthly"] == (*v12["monthly"], "rewards")
    assert v13["backfill"] == v12["backfill"] and v13["correction"] == v12["correction"]
    assert m.SECTION_SINCE["rewards"] == m.SECTION_SINCE["trips"] == "1.3"
    assert "rewards" in m.STATE_SECTIONS and "trips" in m.HISTORY_SECTIONS
    for name in ("programs", "benefits", "milestones", "routing", "redemptions"):
        assert m.IDENTITY[f"rewards.{name}"] == ("key",)
    assert m.IDENTITY["trips"] == ("key",)
    schema = json.loads((REPO / "schema" / "import-v1.schema.json").read_text())
    assert schema["x-required-sections"]["1.3"]["monthly"][-1] == "rewards"
    assert schema["x-section-since"]["trips"] == "1.3"


# --------------------------------------------------------------------------
# rewards item rules
# --------------------------------------------------------------------------

PROGRAM = {"key": "summit-points", "name": "Summit Points", "kind": "points", "group": "primary"}
BALANCE = {"balance": "1200", "as_of": "2026-09-30", "source": "statement"}


@pytest.mark.parametrize(
    "extra,rule",
    [
        ({}, None),
        (BALANCE, None),
        ({"balance": "1200"}, "reward-as-of"),
        ({"balance": "1200", "as_of": "2026-09-30"}, "reward-as-of"),
        ({"as_of": "2026-09-30", "source": "manual"}, "reward-as-of"),
        ({"cpp_low": "1.1", "cpp_high": "1.6"}, None),
        ({"cpp_low": "1.1"}, "reward-value-range"),
        ({"cpp_low": "1.7", "cpp_high": "1.6"}, "reward-value-range"),
        ({"kind": "cash_back", "cpp_low": "1", "cpp_high": "1"}, "reward-value-range"),
        ({"balance": "-5", "as_of": "2026-09-30", "source": "manual"}, "money-sign"),
        ({"allocations": []}, "reward-allocation"),
        ({"best_uses": ["A use"] * 6}, "schema"),
        ({"group": "tertiary"}, "schema"),
    ],
)
def test_program_rules(extra: dict, rule: str | None) -> None:
    assert _rules(m.RewardProgram, {**PROGRAM, **extra}) == ({rule} if rule else set())


TIMESHARE = {
    "key": "vacation-club",
    "name": "Vacation Club",
    "kind": "timeshare",
    "group": "secondary",
}


@pytest.mark.parametrize(
    "allocation,rule",
    [
        ({"year": 2026, "allocated": "1000", "used": "600", "pulled_forward": "400"}, None),
        (
            {"year": 2026, "allocated": "1000", "used": "600", "pulled_forward": "401"},
            "reward-allocation",
        ),
        ({"year": 2026, "allocated": "1000", "used": "1000.5"}, "reward-allocation"),
        ({"year": "2026", "allocated": "1000", "used": "0"}, "schema"),
    ],
)
def test_timeshare_allocations(allocation: dict, rule: str | None) -> None:
    data = {**TIMESHARE, "allocations": [allocation]}
    assert _rules(m.RewardProgram, data) == ({rule} if rule else set())


CREDIT = {
    "key": "dining-credit",
    "account_key": "summit-card",
    "name": "Dining credit",
    "kind": "credit",
    "cadence": "monthly",
    "amount": "10.00",
}
CAP = {
    "key": "fuel-cap",
    "account_key": "cobalt-card",
    "name": "Fuel bonus",
    "kind": "earning_cap",
    "cap": "6000.00",
    "earn_before": "3x",
    "earn_after": "1x",
    "category_keys": ["fuel"],
}
PERK = {
    "key": "hotel-status",
    "account_key": "summit-card",
    "name": "Hotel status",
    "kind": "status_perk",
    "program_key": "lantern-hotels",
    "status_tier": "Gold",
}
FREE_NIGHT = {
    "key": "free-night",
    "account_key": "summit-card",
    "name": "Anniversary night",
    "kind": "free_night",
    "program_key": "lantern-hotels",
}
CYCLE = {"start": "2026-09-01", "end": "2026-09-30", "used": "10.00", "used_on": "2026-09-12"}


@pytest.mark.parametrize(
    "data,rule",
    [
        (CREDIT, None),
        ({**CREDIT, "cycles": [CYCLE], "auto": True, "counted_value": "60.00"}, None),
        ({k: v for k, v in CREDIT.items() if k != "cadence"}, "reward-benefit-fields"),
        ({k: v for k, v in CREDIT.items() if k != "amount"}, "reward-benefit-fields"),
        ({**CREDIT, "cap": "100.00"}, "reward-benefit-fields"),
        ({**CREDIT, "program_key": "lantern-hotels"}, "reward-benefit-fields"),
        ({**CREDIT, "cycles": [{**CYCLE, "end": "2026-08-31"}]}, "date-order"),
        (CAP, None),
        ({**CAP, "reported_spent": "1820.40", "as_of": "2026-09-30"}, None),
        ({**CAP, "reported_spent": "1820.40"}, "reward-as-of"),
        ({k: v for k, v in CAP.items() if k != "category_keys"}, "reward-benefit-fields"),
        ({**CAP, "cadence": "annual"}, "reward-benefit-fields"),
        (PERK, None),
        ({k: v for k, v in PERK.items() if k != "status_tier"}, "reward-benefit-fields"),
        (FREE_NIGHT, None),
        ({**FREE_NIGHT, "ends_on": "2027-03-31"}, None),
        ({**FREE_NIGHT, "status_tier": "Gold"}, "reward-benefit-fields"),
        ({k: v for k, v in FREE_NIGHT.items() if k != "program_key"}, "reward-benefit-fields"),
        ({**FREE_NIGHT, "kind": "other"}, "reward-benefit-fields"),
    ],
)
def test_benefit_rules(data: dict, rule: str | None) -> None:
    assert _rules(m.RewardBenefit, data) == ({rule} if rule else set())


MILESTONE = {
    "key": "summit-spend",
    "account_key": "summit-card",
    "year": 2026,
    "threshold": "9500.00",
    "reward": "A companion pass",
    "status": "open",
}


@pytest.mark.parametrize(
    "extra,rule",
    [
        ({}, None),
        ({"reported_spent": "9100.00", "as_of": "2026-09-30"}, None),
        ({"reported_spent": "9100.00"}, "reward-as-of"),
        ({"as_of": "2026-09-30"}, "reward-as-of"),
        ({"year": 1999}, "schema"),
        ({"status": "late"}, "schema"),
    ],
)
def test_milestone_rules(extra: dict, rule: str | None) -> None:
    assert _rules(m.RewardMilestone, {**MILESTONE, **extra}) == ({rule} if rule else set())


# --------------------------------------------------------------------------
# trips item rules
# --------------------------------------------------------------------------

TRIP = {"key": "ski-weekend", "name": "Ski weekend", "status": "booked", "items": []}
DATES = {"start_date": "2027-01-15", "end_date": "2027-01-18"}


@pytest.mark.parametrize(
    "extra,rule",
    [
        (DATES, None),
        ({"month": "2027-06"}, None),
        ({"month": "1998-06"}, "trip-dates"),
        ({"start_date": "1999-12-30", "end_date": "2000-01-02"}, "trip-dates"),
        ({}, "trip-dates"),
        ({"start_date": "2027-01-15"}, "trip-dates"),
        ({**DATES, "month": "2027-01"}, "trip-dates"),
        ({"start_date": "2027-01-18", "end_date": "2027-01-15"}, "date-order"),
        ({**DATES, "estimate_low": "900.00", "estimate_high": "1400.00"}, None),
        ({**DATES, "estimate_low": "900.00"}, "trip-estimate"),
        ({**DATES, "estimate_low": "1500.00", "estimate_high": "1400.00"}, "trip-estimate"),
        ({**DATES, "guests": 21}, "schema"),
        ({**DATES, "start_date": "2015-07-03", "end_date": "2015-07-05"}, None),  # old trips
    ],
)
def test_trip_rules(extra: dict, rule: str | None) -> None:
    assert _rules(m.Trip, {**TRIP, **extra}) == ({rule} if rule else set())


ITEM = {"key": "lodging", "kind": "lodging", "label": "Cabin", "status": "posted", "tender": "cash"}


@pytest.mark.parametrize(
    "extra,rule",
    [
        ({"amount": "420.00"}, None),
        ({}, "trip-item-amount"),
        ({"status": "booked"}, "trip-item-amount"),
        ({"status": "excluded"}, "trip-item-amount"),
        ({"status": "estimate", "estimate_low": "300.00", "estimate_high": "450.00"}, None),
        ({"status": "estimate", "estimate_low": "300.00"}, "trip-item-amount"),
        (
            {
                "status": "estimate",
                "amount": "1.00",
                "estimate_low": "1.00",
                "estimate_high": "2.00",
            },
            "trip-item-amount",
        ),
        ({"status": "tbd"}, None),
        ({"status": "tbd", "amount": "1.00"}, "trip-item-amount"),
        ({"status": "tbd", "estimate_low": "1.00", "estimate_high": "2.00"}, "trip-item-amount"),
        (
            {"status": "estimate", "estimate_low": "500.00", "estimate_high": "450.00"},
            "trip-estimate",
        ),
        ({"amount": "420.00", "estimate_low": "400.00"}, "trip-estimate"),
        (
            {
                "amount": "11.20",
                "tender": "points",
                "points": "5000",
                "program_key": "harbor-miles",
            },
            None,
        ),
        ({"amount": "11.20", "tender": "points", "points": "5000"}, "trip-tender"),
        ({"amount": "11.20", "tender": "points"}, "trip-tender"),
        ({"status": "tbd", "tender": "points_credit"}, "trip-tender"),
        ({"amount": "50.00", "tender": "points_credit"}, None),
        ({"amount": "-50.00", "tender": "points_credit"}, "money-sign"),
        ({"amount": "0.00", "tender": "free_night"}, None),
        ({"amount": "420.00", "kind": "boat"}, "schema"),
    ],
)
def test_trip_item_rules(extra: dict, rule: str | None) -> None:
    assert _rules(m.TripItem, {**ITEM, **extra}) == ({rule} if rule else set())


IOU = {
    "key": "cabin-share",
    "direction": "owed_to_us",
    "counterparty": "friend couple",
    "amount": "210.00",
    "status": "open",
}


@pytest.mark.parametrize(
    "extra,rule",
    [
        ({}, None),
        ({"status": "waived"}, None),
        ({"status": "settled", "settled_on": "2026-07-20"}, None),
        ({"status": "settled"}, "trip-iou-settled"),
        ({"settled_on": "2026-07-20"}, "trip-iou-settled"),
        ({"direction": "they_owe"}, "schema"),
    ],
)
def test_trip_iou_rules(extra: dict, rule: str | None) -> None:
    assert _rules(m.TripIou, {**IOU, **extra}) == ({rule} if rule else set())


def test_trip_tombstone() -> None:
    assert _rules(m.TripTombstone, {"key": "city-break", "deleted": True}) == set()
    assert _rules(m.TripTombstone, {"key": "city-break", "deleted": False}) == {"schema"}


# --------------------------------------------------------------------------
# booking-code (schema/redaction.json): rewards and trips text only
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Record locator QX7B2K",
        "Flight UA1234 at noon",
        "Confirmation 7KD93LM2",
        "code 4F9A2B.",
    ],
)
def test_booking_code_is_caught_in_rewards_and_trips(text: str) -> None:
    assert "booking-code" in detect_sensitive(text, section="trips")
    assert "booking-code" in detect_sensitive(text, section="rewards")
    assert "booking-code" not in detect_sensitive(text)  # other sections and callers
    assert "booking-code" not in detect_sensitive(text, section="budgets")


@pytest.mark.parametrize(
    "text",
    [
        "Cabin by the lake",
        "Letters only ABCDEF",
        "Short AB12",
        "Too long ABCD1234E",
        "Mixed case Ab12cD",
        "One digit ABCDE1",
        "One letter 12A345",
        "Use the 401K match",
        "Transfer to airline partners",
    ],
)
def test_booking_code_leaves_ordinary_text(text: str) -> None:
    assert "booking-code" not in detect_sensitive(text, section="trips")


def test_the_output_filter_never_runs_booking_code() -> None:
    """Sectioned rules never reach the AI output filter or log scrubbing."""
    assert sensitive_spans("Locator QX7B2K") == []


def test_scan_model_names_the_section() -> None:
    snap = json.loads((SAMPLES / "2026-10-12" / "snapshot.json").read_text())
    snap["trips"][0]["note"] = "Locator QX7B2K"
    snap["budgets"][0]["note"] = "Locator QX7B2K"
    parsed, issues = load_snapshot((json.dumps(snap, indent=2) + "\n").encode())
    assert parsed is not None and issues == []
    found = scan_model(parsed, "snapshot.json:")
    assert [(i.rule, i.where) for i in found] == [("booking-code", "snapshot.json:trips[0].note")]


@pytest.mark.parametrize(
    "section", ["trips", "rewards", "flags", "installment_plans", "goals", "budgets"]
)
def test_booking_code_field_name_is_hidden_in_its_sections(section: str) -> None:
    """An unknown field name is part of the error location. It is hidden when it
    breaks a rule of the section it sits in, and only there (flags, installment_plans
    and goals from redaction.json version 4)."""
    snap = json.loads((SAMPLES / "2026-10-12" / "snapshot.json").read_text())
    parent = snap["rewards"]["programs"][0] if section == "rewards" else snap[section][0]
    parent["QX7B2K"] = "x"
    _, issues = load_snapshot((json.dumps(snap, indent=2) + "\n").encode())
    hidden = m.Issue("schema", "snapshot.json:(hidden name)")
    if section == "budgets":
        assert hidden not in issues and any("QX7B2K" in i.where for i in issues)
    else:
        assert hidden in issues and all("QX7B2K" not in i.where for i in issues)


def test_no_sample_rewards_or_trips_text_looks_like_a_booking_code() -> None:
    """Tested against all sample text the rule covers: every text and key in every
    run's rewards and trips. Row text keeps marketplace order codes, which look
    alike, so the rule stays out of the other sections."""
    texts = []
    for run in sorted(p for p in SAMPLES.iterdir() if p.is_dir()):
        snap, issues = load_snapshot((run / "snapshot.json").read_bytes())
        assert snap is not None and issues == []
        texts += [t for loc, t in iter_text(snap) if loc.startswith(("rewards", "trips"))]
    assert len(texts) > 100
    assert [t for t in texts if "booking-code" in detect_sensitive(t, section="trips")] == []
    order_code = "RIVER MKTP*AB1CD2E"  # a sample row description
    rows, _ = parse_csv(
        (SAMPLES / "2026-10-12" / "transactions" / "summit-card-2026-09-10.csv").read_bytes(), "x"
    )
    assert any(r.description == order_code for r in rows)
    assert "booking-code" in detect_sensitive(order_code, section="trips")
    assert detect_sensitive(order_code) == []
