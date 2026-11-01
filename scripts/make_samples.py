#!/usr/bin/env python3
"""Write the fake sample runs to samples/data/<run-id>/ (deterministic).

A fictional household (Alex, Sam, two kids) with made-up institutions and
numbers. Fixed seed and fixed created_at, so two runs give byte-identical files.

    cd backend && uv run python ../scripts/make_samples.py [--out DIR]

Runs:
  2026-10-06    backfill    statements ending Jan to Apr 2026
  2026-10-06-2  backfill    May to Aug (adds a new card, closes the store card), plus
                            utility bill history from Sep 2024 (format 1.2) and two
                            trips (format 1.3)
  2026-10-12    monthly     September, with state and check sections, the
                            September utility bills, rewards, trips and goals
                            (format 1.4)
  2026-10-14    correction  September checking: one category fix, one changed row

Edge cases covered: identical same-day charges, identical charges on either side
of a cycle close, two descriptions that normalize alike, one source document
holding two accounts, quarterly statements, statements with no CSV, a card split
by cardholder, a Plan It purchase with installments and a fee, an Apple-style
installment, a 0% promo, a placeholder personal subscription, holdings that go
to zero, value-only holdings, an OCR-flagged statement, a Dec to Jan crossover,
card and loan payments between two closes, a balance tombstone, two paychecks
of one kind on one day, a refund, a reimbursable charge and its reimbursement,
a store card tracked by payments (payment-basis rows from checking, plus one
cycle that uses a statement with its payment marked as a transfer), and two
payments to the same issuer where only one belongs to the tracked card.

Investments (gap audit A5 to A8): the brokerage and the 401k give net_flows and
market_change on every statement. The 401k gives fees (money out, negative)
inside its market change; the brokerage gives none. Brokerage funds carry a
cost basis (the January value plus buys and reinvested dividends); the 401k
trust has none. "Kids 529 (Bluestem)" (kids-529) has quarterly statements with
no rows, a holding with a cost basis, and 529 contributions: $200 on the 5th
of each month plus two gifts. None of this uses the RNG, so no other value moves.

The monthly run is format 1.1 and carries recurring items (weekly, every few
weeks, monthly, quarterly, semester, annual, seasonal active months, card
credit offsets, one ending and one paused item), installment plans (Plan It
with a fee, an Apple-style installment, a 0% promo, deferred interest with
pay_before and components), flags (every severity and status) and pending
items. The first backfill and the correction run stay 1.0.

Utility bills (format 1.2), from their own RNG so nothing else moves:
"City Power & Gas" (city-power) has 25 monthly bills, service periods ending
Sep 2024 to Sep 2026, each 29 to 33 days, with electric and gas lines, made-up
heating degree days, small thermostat credits in summer, one estimated read
and one bill off by one cent (a warning). Each 2026 bill's total equals its
payment in Maple joint checking (recurring_key city-power), so they pair.
"Town Water" (town-water) has 4 quarterly bills with water, sewer and
stormwater (unit none) lines; the 2026 ones equal a monthly payment in their
end month. Bills ending in September go in the monthly run, the rest in the
second backfill run.

Rewards and trips (format 1.3, docs/plans/phase-7.md section 7). The monthly
run carries rewards: five programs (two primary, a manual balance that is
stale on purpose with best uses and a points expiry on a secondary program,
cash back, a timeshare with a year pulled forward), card
benefits (monthly, quarterly, semi-annual, auto and not-enrolled credits, a
fuel earning cap, a status perk, a free night), two milestones, six routing
rows and two redemptions. The second backfill run sends "lake-weekend" (final:
posted items that match two tagged card rows, a covered item, an excluded item
and a waived IOU) and "city-break", which the monthly run removes with a
tombstone. The monthly run sends "ski-weekend" (booked: points, a free night,
a points credit, a plan purchase, an estimate, a TBD item, an open IOU) and
"beach-week" (planning, estimates only). One September card row names a trip
no run defines, on purpose (warning trip-unknown).

Format 1.4 (docs/plans/format-14.md section 8), the monthly run only, no RNG, added
to the 1.3 monthly run without changing a 1.3 value (format_v14): an action on most
flags (two left out read as act), dated updates (two on one day, four on one flag),
seven new flags (one per new kind, an info one with a past date), tags on four bills
and keep on two (one with review_by, one without; never on the personal
subscription), a plan note and paid_off_on, a status on each cash flow event (two new
events for target and suggested), original on the van loan and the mortgage, a rate
change on the Orchard card, a booked rental car with due_on, a maybe snorkel tour (an
estimate the trip totals leave out), a done redemption with done_on, and two goals
(college, save up). The new whole items are listed in backend/tests/bad_cases.py
V14_SAMPLE_ITEMS: strip_newer(snap, "1.3") gives the 1.3 monthly run back byte for
byte.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import random
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.importer.keys import csv_path  # noqa: E402
from app.importer.models import (  # noqa: E402
    CSV_HEADER,
    cents_to_str,
    csv_sort_key,
    cusip_valid,
    micro_to_str,
)
from app.importer.order import is_run_id  # noqa: E402

SEED = 20261020
GENERATOR = {"skill": "make_samples", "version": "1"}
RUN_A, RUN_B, RUN_M, RUN_C = "2026-10-06", "2026-10-06-2", "2026-10-12", "2026-10-14"
RUN_MODES = {RUN_A: "backfill", RUN_B: "backfill", RUN_M: "monthly", RUN_C: "correction"}
RUN_MONTHS = {RUN_A: (1, 4), RUN_B: (5, 8), RUN_M: (9, 9)}
RUN_VERSIONS = {RUN_A: "1.0", RUN_B: "1.3", RUN_M: "1.4", RUN_C: "1.0"}
# Cards tracked by payments (PLAN 3.2): no balances, never in net worth.
TRACKED_BY_PAYMENTS = frozenset({"bramble-store-card"})
YEAR = 2026
LAST_MONTH = 9
PRIVATE_RULES_EXAMPLE = {
    "employer_terms": ["EXAMPLE EMPLOYER CO"],
    "personal_subscriptions": ["EXAMPLE PRIVATE SERVICE"],
}

D = date.fromisoformat


def money(c: int) -> str:
    return cents_to_str(c)


def month_end(m: int, y: int = YEAR) -> date:
    return (date(y + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)


def day(m: int, d: int) -> date:
    return date(YEAR, m, min(d, month_end(m).day))


def fake_sha(label: str) -> str:
    return hashlib.sha256(f"sample-source:{label}".encode()).hexdigest()


# --------------------------------------------------------------------------
# Rows and statements
# --------------------------------------------------------------------------


@dataclass
class Row:
    txn_date: date
    amount: int  # cents, signed per the format
    description: str
    category: str
    txn_type: str = "purchase"
    post_date: date | None = None
    merchant: str = ""
    cardholder: str = ""
    is_transfer: str = "false"
    counterparty: str = ""
    is_reimbursable: str = ""
    exclude: str = ""
    plan_key: str = ""
    recurring_key: str = ""
    trip_key: str = ""
    hsa: str = ""
    needs_review: str = "false"
    confidence: str = "0.95"
    notes: str = ""

    def cells(self) -> dict[str, str]:
        return {
            "txn_date": self.txn_date.isoformat(),
            "post_date": self.post_date.isoformat() if self.post_date else "",
            "amount": money(self.amount),
            "description": self.description,
            "merchant": self.merchant,
            "category_key": self.category,
            "cardholder": self.cardholder,
            "txn_type": self.txn_type,
            "is_transfer": self.is_transfer,
            "counterparty_account_key": self.counterparty,
            "is_reimbursable": self.is_reimbursable,
            "exclude_from_budget": self.exclude,
            "plan_key": self.plan_key,
            "recurring_key": self.recurring_key,
            "trip_key": self.trip_key,
            "hsa_eligible": self.hsa,
            "needs_review": self.needs_review,
            "confidence": self.confidence,
            "notes": self.notes,
        }


@dataclass
class Stmt:
    account: str
    start: date
    end: date
    opening: int
    rows: list[Row] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    closing_override: int | None = None
    holdings: list[dict] = field(default_factory=list)

    @property
    def closing(self) -> int:
        if self.closing_override is not None:
            return self.closing_override
        return self.opening + sum(r.amount for r in self.rows)


def in_period(rows: list[Row], start: date, end: date) -> list[Row]:
    return [r for r in rows if start <= r.txn_date <= end]


def card_periods(first_start: date, close_day: int, last_month: int = LAST_MONTH) -> list:
    """Periods ending on close_day each month from Jan to last_month."""
    out, start = [], first_start
    for m in range(1, last_month + 1):
        end = day(m, close_day)
        if end < first_start:
            continue
        out.append((start, end))
        start = end + timedelta(days=1)
    return out


def calendar_periods(first_month: int = 1, last_month: int = LAST_MONTH) -> list:
    return [(date(YEAR, m, 1), month_end(m)) for m in range(first_month, last_month + 1)]


# --------------------------------------------------------------------------
# The household
# --------------------------------------------------------------------------

PEOPLE = [
    {"key": "alex", "display_name": "Alex", "role": "adult"},
    {"key": "jamie", "display_name": "Jamie", "aliases": ["James"], "role": "child"},
    {"key": "joint", "display_name": "Joint", "role": "joint"},
    {"key": "riley", "display_name": "Riley", "role": "child"},
    {"key": "sam", "display_name": "Sam", "role": "adult"},
]


def accounts(run: str) -> list[dict]:
    """The registry as each run reports it."""
    a = {
        "bramble-store-card": {
            "display_name": "Bramble store card",
            "institution": "Cobalt Card",
            "type": "store_card",
            "owner": "sam",
            "ownership": "individual",
            "group": "credit_cards",
            "is_asset": False,
            "include_in_net_worth": False,
            "statement_cadence": "none",
            "tracked_by": "payments",
            "in_cashflow": True,
            "last4": "4471",
            "autopay": "minimum",
            "opened_on": "2023-04-10",
            "display_order": 15,
        },
        "alex-401k": {
            "display_name": "Alex 401k (Lakeview)",
            "institution": "Lakeview Investments",
            "custodian": "Granite Trust Bank",
            "type": "retirement",
            "owner": "alex",
            "ownership": "individual",
            "group": "retirement",
            "nw_group": "alex-retirement",
            "nw_group_label": "Alex retirement",
            "is_asset": True,
            "include_in_net_worth": True,
            "statement_cadence": "quarterly",
            "opened_on": "2018-04-02",
            "notes": "New pre-tax and Roth money goes to the target date fund.",
            "display_order": 40,
        },
        "family-car": {
            "display_name": "Family car",
            "institution": "Valuation",
            "type": "vehicle",
            "owner": "joint",
            "ownership": "joint",
            "group": "vehicles",
            "nw_group": "vehicle-equity",
            "nw_group_label": "Vehicle equity",
            "is_asset": True,
            "include_in_net_worth": True,
            "statement_cadence": "none",
            "stale_after_days": 120,
            "display_order": 70,
        },
        "family-home": {
            "display_name": "Family home",
            "institution": "Valuation",
            "type": "property",
            "owner": "joint",
            "ownership": "joint",
            "group": "real_estate",
            "nw_group": "home-equity",
            "nw_group_label": "Home equity",
            "is_asset": True,
            "include_in_net_worth": True,
            "statement_cadence": "none",
            "stale_after_days": 120,
            "display_order": 60,
        },
        "harbor-checking": {
            "display_name": "Sam checking (Harbor)",
            "institution": "Harbor Credit Union",
            "type": "checking",
            "owner": "sam",
            "ownership": "individual",
            "group": "cash",
            "nw_group": "cash-and-misc",
            "nw_group_label": "Cash and misc",
            "is_asset": True,
            "include_in_net_worth": True,
            "statement_cadence": "monthly",
            "last4": "7702",
            "in_cashflow": True,
            "opened_on": "2015-08-14",
            "display_order": 2,
        },
        "kids-529": {
            "display_name": "Kids 529 (Bluestem)",
            "institution": "Bluestem College Savings",
            "type": "529",
            "owner": "alex",
            "ownership": "individual",
            "group": "investments",
            "nw_group": "college-savings",
            "nw_group_label": "College savings",
            "is_asset": True,
            "include_in_net_worth": True,
            "statement_cadence": "quarterly",
            "opened_on": "2021-05-03",
            "display_order": 35,
        },
        "harbor-savings": {
            "display_name": "Sam savings (Harbor)",
            "institution": "Harbor Credit Union",
            "type": "savings",
            "owner": "sam",
            "ownership": "individual",
            "group": "cash",
            "nw_group": "cash-and-misc",
            "nw_group_label": "Cash and misc",
            "is_asset": True,
            "include_in_net_worth": True,
            "statement_cadence": "quarterly",
            "last4": "7719",
            "in_cashflow": False,
            "apr": "4.1",
            "rate_type": "variable",
            "opened_on": "2015-08-14",
            "display_order": 3,
        },
        "lakeview-brokerage": {
            "display_name": "Alex brokerage (Lakeview)",
            "institution": "Lakeview Investments",
            "custodian": "Lakeview Investments",
            "advisor": "Birch Lane Advisors",
            "type": "brokerage",
            "owner": "alex",
            "ownership": "individual",
            "group": "investments",
            "nw_group": "alex-brokerage",
            "nw_group_label": "Alex brokerage",
            "is_asset": True,
            "include_in_net_worth": True,
            "statement_cadence": "monthly",
            "opened_on": "2019-02-11",
            "notes": "Most of it sits in one fund. A diversification plan is needed.",
            "display_order": 30,
        },
        "maple-joint-checking": {
            "display_name": "Joint checking (Maple)",
            "institution": "Maple Bank",
            "type": "checking",
            "owner": "joint",
            "ownership": "joint",
            "group": "cash",
            "nw_group": "cash-and-misc",
            "nw_group_label": "Cash and misc",
            "is_asset": True,
            "include_in_net_worth": True,
            "statement_cadence": "monthly",
            "last4": "5521",
            "in_cashflow": True,
            "opened_on": "2012-06-01",
            "display_order": 1,
        },
        "old-store-card": {
            "display_name": "Towne store card",
            "institution": "Towne Outfitters",
            "type": "store_card",
            "owner": "sam",
            "ownership": "individual",
            "group": "credit_cards",
            "nw_group": "credit-cards",
            "nw_group_label": "Credit cards",
            "is_asset": False,
            "include_in_net_worth": True,
            "statement_cadence": "monthly",
            "last4": "9031",
            "in_cashflow": True,
            "cycle_close_day": 15,
            "autopay": "none",
            "opened_on": "2021-11-20",
            "display_order": 14,
        },
        "orchard-card": {
            "display_name": "Orchard card",
            "institution": "Orchard Card",
            "type": "credit_card",
            "owner": "alex",
            "ownership": "individual",
            "group": "credit_cards",
            "nw_group": "credit-cards",
            "nw_group_label": "Credit cards",
            "is_asset": False,
            "include_in_net_worth": True,
            "statement_cadence": "monthly",
            "last4": "2290",
            "in_cashflow": True,
            "apr": "19.24",
            "rate_type": "variable",
            "credit_limit": "6000.00",
            "autopay": "statement",
            "cycle_close_day": 31,
            "opened_on": "2020-03-09",
            "display_order": 12,
        },
        "pinecrest-mortgage": {
            "display_name": "Mortgage (Pinecrest)",
            "institution": "Pinecrest Mortgage",
            "type": "mortgage",
            "owner": "joint",
            "ownership": "joint",
            "group": "loans",
            "nw_group": "mortgage",
            "nw_group_label": "Mortgage",
            "is_asset": False,
            "include_in_net_worth": True,
            "statement_cadence": "monthly",
            "last4": "0618",
            "apr": "4.5",
            "rate_type": "fixed",
            "servicer": "Pinecrest Mortgage",
            "payoff_date": "2049-06-01",
            "policy_note": "Do not prepay",
            "secured_by": "family-home",
            "min_payment": "1500.00",
            "autopay": "full",
            "opened_on": "2019-06-01",
            "notes": "Fixed rate for the full term. Extra cash goes to savings first.",
            "display_order": 50,
        },
        "summit-card": {
            "display_name": "Summit card",
            "institution": "Summit Card Services",
            "type": "credit_card",
            "owner": "joint",
            "ownership": "joint",
            "group": "credit_cards",
            "nw_group": "credit-cards",
            "nw_group_label": "Credit cards",
            "is_asset": False,
            "include_in_net_worth": True,
            "statement_cadence": "monthly",
            "last4": "8812",
            "in_cashflow": True,
            "apr": "24.99",
            "rate_type": "variable",
            "credit_limit": "15000.00",
            "autopay": "full",
            "cycle_close_day": 10,
            "annual_fee": "250.00",
            "fee_renews_on": "2027-03-01",
            "opened_on": "2017-09-15",
            "display_order": 10,
        },
    }
    cobalt = {
        "display_name": "Cobalt card",
        "institution": "Cobalt Card",
        "type": "credit_card",
        "owner": "sam",
        "ownership": "individual",
        "group": "credit_cards",
        "nw_group": "credit-cards",
        "nw_group_label": "Credit cards",
        "is_asset": False,
        "include_in_net_worth": True,
        "statement_cadence": "monthly",
        "last4": "3318",
        "in_cashflow": True,
        "apr": "21.99",
        "rate_type": "promo",
        "credit_limit": "5000.00",
        "autopay": "minimum",
        "priority": 1,
        "cycle_close_day": 22,
        "opened_on": "2026-05-01",
        "notes": "0% promo on the mattress until March 2027",
        "display_order": 11,
    }
    if run != RUN_A:
        a["cobalt-card"] = cobalt
        a["old-store-card"] |= {"closed_on": "2026-07-20", "active": False}
        a |= van_accounts()
    for v in a.values():
        v.setdefault("active", True)
    return [{"key": k, **a[k]} for k in sorted(a)]


def van_accounts() -> dict[str, dict]:
    """A camper van bought Aug 1 with a loan that has no statements (Phase 5 estimates)."""
    return {
        "camper-van": {
            "display_name": "Camper van",
            "institution": "Valuation",
            "type": "vehicle",
            "owner": "joint",
            "ownership": "joint",
            "group": "vehicles",
            "nw_group": "vehicle-equity",
            "nw_group_label": "Vehicle equity",
            "is_asset": True,
            "include_in_net_worth": True,
            "statement_cadence": "none",
            "stale_after_days": 120,
            "opened_on": "2026-08-01",
            "display_order": 71,
        },
        "van-loan": {
            "display_name": "Van loan (Prairie)",
            "institution": "Prairie Lending",
            "type": "loan",
            "owner": "joint",
            "ownership": "joint",
            "group": "loans",
            "nw_group": "vehicle-loans",
            "nw_group_label": "Vehicle loans",
            "is_asset": False,
            "include_in_net_worth": True,
            "statement_cadence": "none",
            "stale_after_days": 120,
            "apr": "6.49",
            "rate_type": "fixed",
            "servicer": "Meadow Loan Servicing",
            "secured_by": "camper-van",
            "min_payment": "650.00",
            "autopay": "full",
            "opened_on": "2026-08-01",
            "display_order": 52,
        },
    }


def new_accounts(run: str) -> list[dict] | None:
    if run == RUN_A:
        return [
            {"key": a["key"], **({"opened_on": a["opened_on"]} if "opened_on" in a else {})}
            for a in accounts(RUN_A)
        ]
    if run == RUN_B:
        return [
            {"key": "camper-van", "opened_on": "2026-08-01"},
            {"key": "cobalt-card", "opened_on": "2026-05-01"},
            {"key": "van-loan", "opened_on": "2026-08-01"},
        ]
    return None


# --------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------


VAN_LOAN_PAYMENTS = (("2026-09-01", 65000), ("2026-09-20", 100000))
VAN_LOAN_APR_MILLI_PCT = 6490


def van_loan_owed(owed: int, start: str, end: str) -> int:
    """Simple daily interest at the van loan's APR, less the payments (app/valuations/loans.py)."""
    last = D(start)
    for when, cents in VAN_LOAN_PAYMENTS:
        days = (D(when) - last).days
        owed = owed + round(owed * VAN_LOAN_APR_MILLI_PCT * days / (100000 * 365)) - cents
        last = D(when)
    days = (D(end) - last).days
    return owed + round(owed * VAN_LOAN_APR_MILLI_PCT * days / (100000 * 365))


class Household:
    def __init__(self) -> None:
        self.rng = random.Random(SEED)
        self.payer_rows: dict[str, list[Row]] = {}
        self.statements: dict[str, list[Stmt]] = {}
        self.paychecks: list[dict] = []
        self.contributions: list[dict] = []
        self.valuations: list[dict] = []

    def amt(self, lo: float, hi: float) -> int:
        return self.rng.randint(int(lo * 100), int(hi * 100))

    def pay(self, payer: str, card: str, when: date, cents: int, label: str) -> Row:
        """A card or loan payment: the payer's row is stored, the card's row returned."""
        self.payer_rows.setdefault(payer, []).append(
            Row(
                when,
                -cents,
                f"{label} PAYMENT",
                "transfer",
                "payment",
                is_transfer="true",
                counterparty=card,
                confidence="1.00",
            )
        )
        return Row(
            when,
            cents,
            "PAYMENT RECEIVED - THANK YOU",
            "transfer",
            "payment",
            post_date=when,
            is_transfer="true",
            counterparty=payer,
            confidence="1.00",
        )

    def purchases(
        self, start: date, end: date, merchants: list, n: int, holders: list[str] | None = None
    ) -> list[Row]:
        rows = []
        span = (end - start).days
        for _ in range(n):
            desc, merchant, cat, lo, hi = self.rng.choice(merchants)
            when = start + timedelta(days=self.rng.randint(0, span))
            post = min(when + timedelta(days=1), end)
            rows.append(
                Row(
                    when,
                    -self.amt(lo, hi),
                    desc,
                    cat,
                    post_date=post,
                    merchant=merchant,
                    cardholder=self.rng.choice(holders) if holders else "",
                )
            )
        return rows

    # ---- income ----

    def income(self) -> None:
        ytd_k401 = ytd_hsa_e = ytd_hsa_r = 0
        pay_days = []
        d = D("2026-01-09")
        while d <= month_end(LAST_MONTH):
            pay_days.append(d)
            d += timedelta(days=14)
        advices = [(p, "regular", 420000, 293120, 25200) for p in pay_days]
        advices.append((D("2026-03-13"), "bonus", 500000, 312000, 30000))
        advices.append((D("2026-06-12"), "regular", 5000, 3825, 0))  # a small adjustment advice
        advices.sort(key=lambda a: (a[0], a[1], a[2]))
        seqs: dict[tuple, int] = {}
        for when, kind, gross, net, k401 in advices:
            seq = seqs[(when, kind)] = seqs.get((when, kind), 0) + 1
            hsa_e, hsa_r = (5000, 2500) if kind == "regular" and gross > 100000 else (0, 0)
            ytd_k401 += k401
            ytd_hsa_e += hsa_e
            ytd_hsa_r += hsa_r
            p = {
                "person": "alex",
                "pay_date": when.isoformat(),
                "kind": kind,
                "seq": seq,
                "gross": money(gross),
                "net": money(net),
                "ytd_k401": money(ytd_k401),
                "ytd_hsa_employee": money(ytd_hsa_e),
                "ytd_hsa_employer": money(ytd_hsa_r),
            }
            if kind == "regular" and gross > 100000:
                p |= {
                    "period_start": (when - timedelta(days=20)).isoformat(),
                    "period_end": (when - timedelta(days=7)).isoformat(),
                    "pretax_health": "120.00",
                    "hsa_employee": money(hsa_e),
                    "hsa_employer": money(hsa_r),
                }
            if k401:
                p |= {"k401": money(k401), "k401_pct": "6"}
                for kind_c, amount in (("401k_employee", k401), ("401k_employer", k401 // 2)):
                    self.contributions.append(
                        {
                            "person": "alex",
                            "account_key": "alex-401k",
                            "kind": kind_c,
                            "source": "current_employer",
                            "date": when.isoformat(),
                            "amount": money(amount),
                        }
                    )
            self.paychecks.append(p)
            self.payer_rows.setdefault("maple-joint-checking", []).append(
                Row(
                    when,
                    net,
                    "PAYROLL DEPOSIT",
                    "paycheck",
                    "deposit",
                    confidence="1.00",
                    recurring_key="alex-pay",
                )
            )
        for m in range(1, LAST_MONTH + 1):
            for when in (day(m, 15), month_end(m)):
                self.paychecks.append(
                    {
                        "person": "sam",
                        "pay_date": when.isoformat(),
                        "kind": "regular",
                        "seq": 1,
                        "gross": "3000.00",
                        "net": "2250.00",
                    }
                )
                self.payer_rows.setdefault("harbor-checking", []).append(
                    Row(
                        when,
                        225000,
                        "PAYROLL DEPOSIT",
                        "paycheck",
                        "deposit",
                        confidence="1.00",
                        recurring_key="sam-pay",
                    )
                )

    # ---- cards ----

    @staticmethod
    def credit_pair(
        when: date,
        cents: int,
        description: str,
        category: str,
        merchant: str,
        cardholder: str = "",
    ) -> list[Row]:
        """A benefit refund and a same-day purchase, with no effect on any net total."""
        common = dict(post_date=when, cardholder=cardholder)
        return [
            Row(
                when,
                cents,
                description,
                category,
                "refund",
                merchant=description.title(),
                **common,
            ),
            Row(when, -cents, merchant.upper(), category, merchant=merchant, **common),
        ]

    def summit(self) -> None:
        merchants = [
            ("CORNER GROCERY", "Corner Grocery", "groceries", 20, 140),
            ("SQ *MAPLE CAFE #12", "Maple Cafe", "dining", 5, 15),
            ("BLUE DOOR BISTRO", "Blue Door Bistro", "dining", 30, 90),
            ("QUICKFUEL STATION", "Quickfuel", "fuel", 30, 60),
            ("RIVER MKTP*AB1CD2E", "River Marketplace", "online-shopping", 10, 80),
            ("BULK BARN CLUB", "Bulk Barn Club", "groceries", 80, 200),
            ("PET SUPPLY BARN", "Pet Supply Barn", "pets", 20, 60),
            ("BLOOM HOME AND BATH", "Bloom Home and Bath", "household-and-personal-care", 15, 50),
        ]
        out, opening, remaining = [], -123456, 0
        prev = None
        for start, end in card_periods(D("2025-12-11"), 10):
            rows = self.purchases(start, end, merchants, 10, ["alex", "sam"])
            pay_on = (prev.end if prev else D("2025-12-10")) + timedelta(days=15)
            owed = -(prev.closing if prev else opening)
            payer = "maple-joint-checking"
            rows.append(self.pay(payer, "summit-card", pay_on, owed, "SUMMIT CARD"))
            m = end.month
            if m == 3:
                rows.append(
                    Row(
                        D("2026-03-02"),
                        -25000,
                        "ANNUAL MEMBERSHIP FEE",
                        "annual-fees",
                        "fee",
                        post_date=D("2026-03-02"),
                        recurring_key="summit-annual-fee",
                        confidence="1.00",
                    )
                )
            if m == 5:  # Plan It: purchase, plan creation
                rows += [
                    Row(
                        D("2026-04-20"),
                        -60000,
                        "OAK AND PINE FURNITURE",
                        "home-upkeep",
                        post_date=D("2026-04-21"),
                        merchant="Oak and Pine Furniture",
                        cardholder="alex",
                        plan_key="plan-sofa",
                    ),
                    Row(
                        D("2026-04-22"),
                        60000,
                        "PLAN IT CREATED",
                        "transfer",
                        "adjustment",
                        post_date=D("2026-04-22"),
                        is_transfer="true",
                        plan_key="plan-sofa",
                        confidence="1.00",
                    ),
                ]
                remaining = 60000
            if m >= 6 and remaining:  # monthly installment and plan fee
                rows += [
                    Row(
                        end,
                        -10000,
                        "PLAN IT INSTALLMENT",
                        "transfer",
                        "adjustment",
                        post_date=end,
                        is_transfer="true",
                        plan_key="plan-sofa",
                        confidence="1.00",
                    ),
                    Row(
                        end,
                        -500,
                        "PLAN IT FEE",
                        "plan-fees",
                        "plan_fee",
                        post_date=end,
                        plan_key="plan-sofa",
                        confidence="1.00",
                    ),
                ]
                remaining -= 10000
            if m == 6:  # identical charge on the close day (the twin is in July)
                rows.append(
                    Row(
                        D("2026-06-10"),
                        -1500,
                        "CITY PARKING",
                        "misc",
                        post_date=end,
                        merchant="City Parking",
                        cardholder="sam",
                    )
                )
            if m == 7:
                rows += [
                    Row(
                        D("2026-06-11"),
                        -1500,
                        "CITY PARKING",
                        "misc",
                        post_date=D("2026-06-12"),
                        merchant="City Parking",
                        cardholder="sam",
                    ),
                    Row(
                        D("2026-06-20"),
                        -4500,
                        "TOWNE OUTFITTERS",
                        "clothing",
                        post_date=D("2026-06-21"),
                        merchant="Towne Outfitters",
                        cardholder="sam",
                    ),
                    Row(
                        D("2026-06-29"),
                        4500,
                        "TOWNE OUTFITTERS RETURN",
                        "clothing",
                        "refund",
                        post_date=D("2026-06-30"),
                        merchant="Towne Outfitters",
                        cardholder="sam",
                    ),
                    Row(
                        D("2026-07-03"),
                        -21000,
                        "LAKESIDE LODGE",
                        "travel",
                        post_date=D("2026-07-04"),
                        merchant="Lakeside Lodge",
                        cardholder="alex",
                        trip_key="lake-weekend",
                    ),
                ]
            if m == 8:  # identical same-day charges, and two descriptions that normalize alike
                twin = dict(merchant="Corner Grocery", cardholder="alex")
                rows += [
                    Row(
                        D("2026-07-18"),
                        -2345,
                        "CORNER GROCERY",
                        "groceries",
                        post_date=D("2026-07-19"),
                        **twin,
                    ),
                    Row(
                        D("2026-07-18"),
                        -2345,
                        "CORNER GROCERY",
                        "groceries",
                        post_date=D("2026-07-19"),
                        **twin,
                    ),
                    Row(
                        D("2026-08-02"),
                        -650,
                        "SQ *MAPLE CAFE #12",
                        "dining",
                        post_date=D("2026-08-03"),
                        merchant="Maple Cafe",
                        cardholder="sam",
                    ),
                    Row(
                        D("2026-08-02"),
                        -650,
                        "SQ *MAPLE CAFE #34",
                        "dining",
                        post_date=D("2026-08-03"),
                        merchant="Maple Cafe",
                        cardholder="sam",
                    ),
                    Row(
                        D("2026-07-25"),
                        -3999,
                        "PAPER AND RIBBON",
                        "gifts",
                        post_date=D("2026-07-26"),
                        merchant="Paper and Ribbon",
                        cardholder="sam",
                        exclude="true",
                        notes="Birthday gift",
                    ),
                    Row(
                        D("2026-07-29"),
                        -1875,
                        "MISC VENDOR",
                        "uncategorized",
                        post_date=D("2026-07-30"),
                        needs_review="true",
                        confidence="0.40",
                    ),
                ]
            for month in range(1, 9):
                when = day(month, 12)
                if start <= when <= end:
                    rows += self.credit_pair(
                        when, 1000, "DINING CREDIT", "dining", "Cedar Cafe", "alex"
                    )
            if start <= D("2026-08-18") <= end:
                rows += self.credit_pair(
                    D("2026-08-18"), 2500, "STATEMENT CREDIT", "groceries", "Meadow Market", "alex"
                )
            st = Stmt("summit-card", start, end, opening, rows)
            st.extra = self.card_extra(st, 1500000)
            if remaining:
                st.extra["balance"] = money(st.closing - remaining)
            out.append(st)
            opening, prev = st.closing, st
        self.statements["summit-card"] = out

    def orchard(self) -> None:
        merchants = [
            ("RIVER MKTP*ZX9Y8W7", "River Marketplace", "online-shopping", 12, 70),
            ("TACO CART", "Taco Cart", "dining", 8, 25),
            ("GREEN LEAF MARKET", "Green Leaf Market", "groceries", 15, 90),
        ]
        out, opening, remaining, prev = [], -31025, 0, None
        for start, end in calendar_periods():
            m = end.month
            rows = self.purchases(start, end, merchants, 4)
            rows += self.credit_pair(day(m, 5), 500, "WIRELESS CREDIT", "phone", "Pine Wireless")
            rows.append(
                Row(
                    day(m, 7),
                    -1099,
                    "CLOUDTUNES MUSIC",
                    "subscriptions",
                    post_date=day(m, 8),
                    merchant="Cloudtunes",
                    recurring_key="cloudtunes",
                )
            )
            owed = -(prev.closing if prev else opening)
            rows.append(
                self.pay("maple-joint-checking", "orchard-card", day(m, 20), owed, "ORCHARD CARD")
            )
            if m == 2:  # installment purchase from the Installments section, moved to a plan
                rows += [
                    Row(
                        D("2026-02-14"),
                        -90000,
                        "ORCHARD STORE DEVICE",
                        "shopping-misc",
                        post_date=D("2026-02-14"),
                        merchant="Orchard Store",
                        plan_key="device-plan",
                    ),
                    Row(
                        D("2026-02-14"),
                        90000,
                        "INSTALLMENT BALANCE MOVE",
                        "transfer",
                        "adjustment",
                        post_date=D("2026-02-14"),
                        is_transfer="true",
                        plan_key="device-plan",
                        confidence="1.00",
                    ),
                ]
                remaining = 90000
            if remaining and m >= 2:
                rows.append(
                    Row(
                        end,
                        -7500,
                        "MONTHLY INSTALLMENT",
                        "transfer",
                        "adjustment",
                        post_date=end,
                        is_transfer="true",
                        plan_key="device-plan",
                        confidence="1.00",
                    )
                )
                remaining -= 7500
            st = Stmt("orchard-card", start, end, opening, rows)
            st.extra = self.card_extra(st, 600000)
            if remaining:
                st.extra["balance"] = money(st.closing - remaining)
            out.append(st)
            opening, prev = st.closing, st
        self.statements["orchard-card"] = out

    def cobalt(self) -> None:
        merchants = [
            ("TOWNE OUTFITTERS", "Towne Outfitters", "clothing", 20, 80),
            ("GREEN LEAF MARKET", "Green Leaf Market", "groceries", 15, 90),
            ("STARLIGHT CINEMA", "Starlight Cinema", "entertainment", 12, 40),
        ]
        out, opening, prev = [], 0, None
        for start, end in card_periods(D("2026-05-01"), 22):
            rows = self.purchases(start, end, merchants, 3)
            if prev:
                due = min(25000, -prev.closing)
                pay_on = prev.end + timedelta(days=18)
                rows.append(self.pay("harbor-checking", "cobalt-card", pay_on, due, "COBALT CARD"))
            if end.month == 5:
                rows.append(
                    Row(
                        D("2026-05-10"),
                        -120000,
                        "LAKESIDE MATTRESS",
                        "home-upkeep",
                        post_date=D("2026-05-11"),
                        merchant="Lakeside Mattress",
                        plan_key="promo-mattress",
                    )
                )
            if end.month == 9:  # Transportation (Phase 7): parking and a toll
                rows += [
                    Row(
                        D("2026-09-05"),
                        -1800,
                        "CITYPARK GARAGE",
                        "parking",
                        post_date=D("2026-09-06"),
                        merchant="Citypark Garage",
                    ),
                    Row(
                        D("2026-09-12"),
                        -650,
                        "STATE TOLLWAY",
                        "tolls",
                        post_date=D("2026-09-13"),
                        merchant="State Tollway",
                    ),
                ]
            if end.month >= 6:
                sub = day(end.month, 3)
                rows.append(
                    Row(
                        sub,
                        -1499,
                        "Personal subscription",
                        "subscriptions",
                        post_date=sub,
                        merchant="Personal subscription",
                        recurring_key="personal-subscription",
                    )
                )
            for when in (D("2026-05-04"), D("2026-08-03")):
                if start <= when <= end:
                    rows += self.credit_pair(
                        when, 1500, "STREAMING CREDIT", "subscriptions", "Brook Streaming"
                    )
            st = Stmt("cobalt-card", start, end, opening, rows)
            st.extra = self.card_extra(st, 500000)
            out.append(st)
            opening, prev = st.closing, st
        self.statements["cobalt-card"] = out

    def old_store(self) -> None:
        merchants = [("TOWNE OUTFITTERS", "Towne Outfitters", "clothing", 20, 70)]
        out, opening, prev = [], -8540, None
        for start, end in card_periods(D("2025-12-16"), 15, last_month=7):
            rows = [] if end.month == 7 else self.purchases(start, end, merchants, 2)
            owed = -(prev.closing if prev else opening)
            pay_on = (prev.end if prev else D("2025-12-15")) + timedelta(days=21)
            if owed:
                rows.append(
                    self.pay("harbor-checking", "old-store-card", pay_on, owed, "TOWNE CARD")
                )
            st = Stmt("old-store-card", start, end, opening, rows)
            st.extra = self.card_extra(st, 150000)
            if end.month == 3:
                st.extra |= {"ocr": True, "flags_text": "Scanned statement, totals checked by hand"}
            out.append(st)
            opening, prev = st.closing, st
        self.statements["old-store-card"] = out

    @staticmethod
    def card_extra(st: Stmt, limit: int) -> dict:
        owed = -st.closing
        return {
            "statement_date": st.end.isoformat(),
            "due_date": (st.end + timedelta(days=25)).isoformat(),
            "credit_limit": money(limit),
            "min_due": money(min(owed, 3500) if owed > 0 else 0),
        }

    # ---- loans, investments, savings ----

    def mortgage(self) -> None:
        out, opening = [], -25000000
        for start, end in card_periods(D("2025-12-16"), 15):
            pay_day = date(end.year, end.month, 1)
            rows = [
                self.pay(
                    "maple-joint-checking",
                    "pinecrest-mortgage",
                    pay_day,
                    150000,
                    "PINECREST MORTGAGE",
                )
            ]
            rows[0].post_date = None
            interest = -opening * 375 // 100000
            rows.append(
                Row(end, -interest, "INTEREST CHARGED", "mortgage", "interest", confidence="1.00")
            )
            st = Stmt("pinecrest-mortgage", start, end, opening, rows)
            st.extra = {
                "statement_date": end.isoformat(),
                "due_date": (end + timedelta(days=17)).isoformat(),
                "min_due": "1500.00",
                "interest": money(-interest),
            }
            out.append(st)
            opening = st.closing
        # the checking side is spending in the Mortgage category
        for r in self.payer_rows["maple-joint-checking"]:
            if r.counterparty == "pinecrest-mortgage":
                r.category, r.recurring_key = "mortgage", "mortgage"
        self.statements["pinecrest-mortgage"] = out

    def brokerage(self) -> None:
        rng = self.rng
        price = {"broad-market": 250_000_000, "bond-index": 100_000_000}
        qty = {"broad-market": 120_000_000, "bond-index": 200_000_000}
        cash = 100000

        def value(k: str) -> int:
            return qty[k] * price[k] // 10**10

        def total() -> int:
            return sum(value(k) for k in qty) + cash

        out, opening = [], total()
        # Cost basis per fund: the January value, plus buys and reinvested dividends.
        cost = {k: value(k) for k in qty}
        for start, end in calendar_periods():
            m = end.month
            rows = []
            dep = Row(
                day(m, 5),
                50000,
                "DEPOSIT FROM CHECKING",
                "transfer",
                "contribution",
                is_transfer="true",
                counterparty="maple-joint-checking",
                confidence="1.00",
            )
            self.payer_rows.setdefault("maple-joint-checking", []).append(
                Row(
                    day(m, 5),
                    -50000,
                    "TRANSFER TO LAKEVIEW BROKERAGE",
                    "transfer",
                    "transfer",
                    is_transfer="true",
                    counterparty="lakeview-brokerage",
                    confidence="1.00",
                )
            )
            buy = Row(
                day(m, 6), -50000, "BUY BROAD MARKET FUND", "transfer", "buy", confidence="1.00"
            )
            qty["broad-market"] += 50000 * 10**10 // price["broad-market"]
            cost["broad-market"] += 50000
            rows += [dep, buy]
            if m in (3, 6, 9):
                div = self.amt(40, 90)
                rows += [
                    Row(
                        day(m, 15),
                        div,
                        "DIVIDEND BROAD MARKET FUND",
                        "other-income",
                        "dividend",
                        confidence="1.00",
                    ),
                    Row(
                        day(m, 15),
                        -div,
                        "REINVEST BROAD MARKET FUND",
                        "transfer",
                        "reinvest",
                        confidence="1.00",
                    ),
                ]
                qty["broad-market"] += div * 10**10 // price["broad-market"]
                cost["broad-market"] += div
            if m == 6:  # sell the whole bond fund: it goes to zero from June on
                proceeds = value("bond-index")
                rows.append(
                    Row(
                        day(m, 20),
                        proceeds,
                        "SELL BOND INDEX FUND",
                        "transfer",
                        "sell",
                        confidence="1.00",
                    )
                )
                cash += proceeds
                del qty["bond-index"]
                del cost["bond-index"]
            for k in price:
                price[k] = price[k] * (1000 + rng.randint(-30, 35)) // 1000
            st = Stmt("lakeview-brokerage", start, end, opening, rows, closing_override=total())
            st.extra = {
                "statement_date": end.isoformat(),
                "has_holdings": True,
                "net_flows": "500.00",
                "market_change": money(total() - opening - 50000),
            }
            names = {
                "broad-market": ("Broad Market Index Fund", "BRDMKT", "us-stock"),
                "bond-index": ("Bond Index Fund", "BNDIDX", "bond"),
            }
            for k in sorted(qty):
                name, sym, cls = names[k]
                h = {
                    "key": k,
                    "name": name,
                    "symbol": sym,
                    "quantity": micro_to_str(qty[k]),
                    "price": micro_to_str(price[k]),
                    "value": money(value(k)),
                    "cost_basis": money(cost[k]),
                    "asset_class": cls,
                }
                if k == "bond-index":
                    h["cusip"] = SAMPLE_CUSIP
                st.holdings.append(h)
            st.holdings.append(
                {"key": "cash", "name": "Cash", "value": money(cash), "asset_class": "cash"}
            )
            st.holdings.sort(key=lambda h: h["key"])
            out.append(st)
            opening = st.closing
        self.statements["lakeview-brokerage"] = out

    def retirement(self) -> None:
        out, opening = [], 8500000
        for q in (1, 2, 3):
            start, end = date(YEAR, 3 * q - 2, 1), month_end(3 * q)
            flows = sum(
                int(c["amount"].replace(".", ""))
                for c in self.contributions
                if start.isoformat() <= c["date"] <= end.isoformat()
            )
            market = opening * self.rng.randint(-30, 45) // 1000
            closing = opening + flows + market
            st = Stmt("alex-401k", start, end, opening, closing_override=closing)
            st.extra = {
                "statement_date": end.isoformat(),
                "has_holdings": True,
                "net_flows": money(flows),
                "market_change": money(market),
                # Fees are money out (negative) and already sit inside market_change.
                "fees": money(-(closing // 4000)),
            }
            st.holdings = [
                {
                    "key": "target-date-trust",
                    "name": "Target Date 2050 Trust",
                    "value": money(closing),
                    "asset_class": "target-date",
                }
            ]
            out.append(st)
            opening = closing
        self.statements["alex-401k"] = out

    def college(self) -> None:
        """Kids 529: $200 on the 5th of each month and two gifts, quarterly statements
        with no rows. No RNG, so no other value moves. Runs after retirement(), which
        adds up every contribution in its quarters."""
        deposits = [(day(m, 5), 20000, "personal") for m in range(1, LAST_MONTH + 1)]
        deposits += [(D("2026-03-14"), 50000, "gift"), (D("2026-07-04"), 25000, "gift")]
        for when, cents, source in deposits:
            self.contributions.append(
                {
                    "person": "alex",
                    "account_key": "kids-529",
                    "kind": "529",
                    "source": source,
                    "date": when.isoformat(),
                    "amount": money(cents),
                }
            )
        out, opening, basis = [], 1850000, 1500000
        for q, per_mille in zip((1, 2, 3), (21, -12, 34), strict=True):
            start, end = date(YEAR, 3 * q - 2, 1), month_end(3 * q)
            flows = sum(c for when, c, _ in deposits if start <= when <= end)
            market = opening * per_mille // 1000
            closing = opening + flows + market
            basis += flows
            st = Stmt("kids-529", start, end, opening, closing_override=closing)
            st.extra = {
                "statement_date": end.isoformat(),
                "has_holdings": True,
                "net_flows": money(flows),
                "market_change": money(market),
            }
            st.holdings = [
                {
                    "key": "age-based-portfolio",
                    "name": "Age-Based Portfolio",
                    "value": money(closing),
                    "cost_basis": money(basis),
                    "asset_class": "target-date",
                }
            ]
            out.append(st)
            opening = closing
        self.statements["kids-529"] = out

    def savings(self) -> None:
        out, opening = [], 1200000
        transfers = [
            r for r in self.payer_rows["harbor-checking"] if r.counterparty == "harbor-savings"
        ]
        for q in (1, 2, 3):
            start, end = date(YEAR, 3 * q - 2, 1), month_end(3 * q)
            moved = -sum(r.amount for r in transfers if start <= r.txn_date <= end)
            interest = self.amt(12, 20)
            credits = moved + interest
            st = Stmt("harbor-savings", start, end, opening, closing_override=opening + credits)
            st.extra = {
                "statement_date": end.isoformat(),
                "credits": money(credits),
                "debits": "0.00",
                "interest": money(interest),
                "source_sha256": fake_sha(f"harbor-{end}"),
            }
            out.append(st)
            opening = st.closing
        self.statements["harbor-savings"] = out

    # ---- checking ----

    def maple(self) -> None:
        own: list[Row] = []
        for m in range(1, LAST_MONTH + 1):
            own += [
                Row(day(m, 9), -10000, "ATM WITHDRAWAL", "cash-withdrawal", "withdrawal"),
                Row(
                    day(m, 14),
                    -7999,
                    "FIBERNET INTERNET",
                    "internet",
                    merchant="Fibernet",
                    recurring_key="fibernet-internet",
                ),
                Row(
                    day(m, 18),
                    -self.amt(110, 190),
                    "CITY POWER AND GAS",
                    "gas-and-electric",
                    merchant="City Power and Gas",
                    recurring_key="city-power",
                ),
                Row(
                    day(m, 22),
                    -self.amt(45, 70),
                    "TOWN WATER UTILITY",
                    "water-and-sewer",
                    merchant="Town Water",
                    recurring_key="town-water",
                ),
                Row(
                    month_end(m),
                    self.amt(0.2, 0.6),
                    "INTEREST PAID",
                    "interest-income",
                    "interest",
                    confidence="1.00",
                ),
            ]
            for _ in range(2):
                own.append(
                    Row(
                        day(m, self.rng.randint(1, 28)),
                        -self.amt(30, 120),
                        "GREEN LEAF MARKET",
                        "groceries",
                        merchant="Green Leaf Market",
                    )
                )
        own += [
            Row(
                D("2026-04-08"),
                -18000,
                "FAMILY CLINIC",
                "medical",
                merchant="Family Clinic",
                is_reimbursable="true",
                hsa="true",
            ),
            Row(
                D("2026-05-03"),
                18000,
                "HSA REIMBURSEMENT",
                "reimbursement",
                "deposit",
                confidence="1.00",
            ),
            Row(
                D("2026-09-16"),
                -6420,
                "GREEN LEAF MARKET",
                "groceries",
                merchant="Green Leaf Market",
                notes="Weekly shop",
            ),
        ]
        self.checking("maple-joint-checking", 850000, own + self.payer_rows["maple-joint-checking"])

    def harbor(self) -> None:
        own: list[Row] = []
        for m in range(1, LAST_MONTH + 1):
            own += [
                Row(
                    day(m, 2),
                    -4500,
                    "IRONWORKS GYM",
                    "fitness",
                    merchant="Ironworks Gym",
                    recurring_key="gym",
                ),
                Row(
                    day(m, 12),
                    -6500,
                    "WAVE MOBILE",
                    "phone",
                    merchant="Wave Mobile",
                    recurring_key="wave-mobile",
                ),
                Row(
                    day(m, 28),
                    -20000,
                    "TRANSFER TO SAVINGS",
                    "transfer",
                    "transfer",
                    is_transfer="true",
                    counterparty="harbor-savings",
                    confidence="1.00",
                    recurring_key="savings-transfer",
                ),
                Row(
                    day(m, self.rng.randint(1, 27)),
                    -self.amt(12, 40),
                    "STARLIGHT CINEMA",
                    "entertainment",
                    merchant="Starlight Cinema",
                ),
            ]
        own += [
            Row(
                D("2026-03-03"),
                -12000,
                "YOUTH SOCCER CLUB",
                "kids-activities",
                merchant="Youth Soccer Club",
                recurring_key="youth-soccer",
            ),
            Row(
                D("2026-08-04"),
                -12000,
                "YOUTH SOCCER CLUB",
                "kids-activities",
                merchant="Youth Soccer Club",
                recurring_key="youth-soccer",
            ),
        ]
        self.payer_rows.setdefault("harbor-checking", []).extend(own)

    def bramble(self) -> None:
        """A store card tracked by payments from Sam's checking (no RNG, so other
        sample numbers do not move). Its payments use the same issuer text as the
        Cobalt card's payments, which come from the same account.

        - Jan to Aug: payment basis. The checking row is the spending
          (`payment`, is_transfer false, clothing).
        - The Aug 16 to Sep 15 cycle uses its statement. That cycle's payment
          (Sep 24) is a normal card payment (is_transfer true).
        """
        key, payee = "bramble-store-card", "COBALT CARD PAYMENT"
        amounts = [4210, 5575, 3890, 6120, 4745, 5230, 3980, 4415]
        for m, cents in enumerate(amounts, start=1):
            self.payer_rows.setdefault("harbor-checking", []).append(
                Row(
                    day(m, 24),
                    -cents,
                    payee,
                    "clothing",
                    "payment",
                    counterparty=key,
                    confidence="1.00",
                )
            )
        rows = [
            Row(
                D("2026-08-24"),
                4415,
                "PAYMENT RECEIVED - THANK YOU",
                "transfer",
                "payment",
                post_date=D("2026-08-24"),
                is_transfer="true",
                counterparty="harbor-checking",
                confidence="1.00",
            ),
            Row(
                D("2026-08-30"),
                -3299,
                "BRAMBLE OUTFITTERS",
                "clothing",
                post_date=D("2026-08-31"),
                merchant="Bramble Outfitters",
            ),
            Row(
                D("2026-09-06"),
                -2150,
                "BRAMBLE OUTFITTERS",
                "clothing",
                post_date=D("2026-09-07"),
                merchant="Bramble Outfitters",
            ),
        ]
        st = Stmt(key, D("2026-08-16"), D("2026-09-15"), -4415, rows)
        st.extra = {"statement_date": "2026-09-15", "due_date": "2026-10-10"}
        self.payer_rows["harbor-checking"].append(
            Row(
                D("2026-09-24"),
                st.closing,
                payee,
                "transfer",
                "payment",
                is_transfer="true",
                counterparty=key,
                confidence="1.00",
            )
        )
        self.statements[key] = [st]

    def van(self) -> None:
        """Van loan payments from Sam's checking: the monthly one and an extra one.

        The loan has no statements. The chat sends the Aug 1 balance from the
        loan papers and its own Sep 30 estimate. Between them (and after Sep 30)
        the app estimates from the APR and these rows. Fixed amounts, no random
        draws, so the other sample numbers do not move.
        """
        for when, cents in VAN_LOAN_PAYMENTS:
            self.pay("harbor-checking", "van-loan", D(when), cents, "PRAIRIE LENDING")
        for r in self.payer_rows["harbor-checking"]:
            if r.counterparty == "van-loan":
                r.category, r.recurring_key = "vehicle-loans", ""
        self.valuations += [
            {
                "account_key": "camper-van",
                "as_of": "2026-08-01",
                "source": "valuation",
                "balance": money(5450000),
                "method": "kbb",
                "source_note": "KBB value",
            },
            {
                "account_key": "camper-van",
                "as_of": "2026-09-30",
                "source": "valuation",
                "balance": money(5390000),
                "method": "kbb",
                "source_note": "KBB value",
            },
            {
                "account_key": "van-loan",
                "as_of": "2026-08-01",
                "source": "manual",
                "balance": money(-4200000),
                "source_note": "Loan papers",
            },
            {
                # The monthly run's own estimate for a loan with no statement.
                "account_key": "van-loan",
                "as_of": "2026-09-30",
                "source": "estimate",
                "balance": money(-van_loan_owed(4200000, "2026-08-01", "2026-09-30")),
                "source_note": "Amortized from the loan papers",
            },
        ]

    def harbor_statements(self) -> None:
        self.checking("harbor-checking", 320000, self.payer_rows["harbor-checking"])
        for st in self.statements["harbor-checking"]:
            if st.end.month in (3, 6, 9):  # one PDF holds checking and savings
                st.extra["source_sha256"] = fake_sha(f"harbor-{st.end}")

    def checking(self, key: str, opening: int, rows: list[Row]) -> None:
        out = []
        for start, end in calendar_periods():
            got = in_period(rows, start, end)
            st = Stmt(key, start, end, opening, got)
            st.extra = {"statement_date": end.isoformat()}
            out.append(st)
            opening = st.closing
        self.statements[key] = out

    def valuations_list(self) -> None:
        home, car = 52000000, 2400000
        # A January starting value, so the home and car count from the start of the year.
        self.valuations += [
            {
                "account_key": "family-car",
                "as_of": f"{YEAR}-01-01",
                "source": "valuation",
                "balance": money(car),
                "method": "kbb",
                "source_note": "Private party value",
            },
            {
                "account_key": "family-home",
                "as_of": f"{YEAR}-01-01",
                "source": "valuation",
                "balance": money(home),
                "method": "comps",
                "source_note": "Three nearby sales",
            },
        ]
        for m in (3, 6, 9):
            home += self.amt(-1500, 4000)
            car -= self.amt(300, 700)
            as_of = month_end(m).isoformat()
            self.valuations += [
                {
                    "account_key": "family-car",
                    "as_of": as_of,
                    "source": "valuation",
                    "balance": money(car),
                    "method": "kbb",
                    "source_note": "Private party value",
                },
                {
                    "account_key": "family-home",
                    "as_of": as_of,
                    "source": "valuation",
                    "balance": money(home),
                    "method": "comps",
                    "source_note": "Three nearby sales",
                },
            ]

    def build(self) -> None:
        self.income()
        self.summit()
        self.orchard()
        self.cobalt()
        self.old_store()
        self.mortgage()
        self.brokerage()
        self.harbor()
        self.bramble()
        self.retirement()
        self.college()
        self.savings()
        self.maple()
        self.van()
        self.harbor_statements()
        self.valuations_list()
        # A rollover deferral from a prior job. Added after retirement() so the
        # 401k statements stay as they were.
        self.contributions.append(
            {
                "person": "alex",
                "account_key": "alex-401k",
                "kind": "401k_employee",
                "source": "prior_employer",
                "date": "2026-01-05",
                "amount": "1200.00",
            }
        )
        self.tag_trips()

    def tag_trips(self) -> None:
        """Format 1.3: tag the lake weekend's fuel on the way home (the lodge row is
        tagged where it is made), and one September cafe row with a trip no run
        defines (warning trip-unknown). Only trip_key changes, so no total moves."""
        for desc, when, key in TRIP_TAGS:
            hits = [
                r
                for st in self.statements["summit-card"]
                for r in st.rows
                if r.description == desc and r.txn_date == D(when)
            ]
            if len(hits) != 1:
                raise RuntimeError(f"expected one summit-card row for {desc} on {when}")
            hits[0].trip_key = key

    def summit_row(self, desc: str, when: str) -> Row:
        return next(
            r
            for st in self.statements["summit-card"]
            for r in st.rows
            if r.description == desc and r.txn_date == D(when)
        )


def _cusip(base: str) -> str:
    for d in "0123456789":
        if cusip_valid(base + d):
            return base + d
    raise ValueError("no check digit")


SAMPLE_CUSIP = _cusip("SMPLBND0")


# --------------------------------------------------------------------------
# Runs
# --------------------------------------------------------------------------


def stmt_json(st: Stmt) -> dict:
    rows = st.rows
    d = {
        "account_key": st.account,
        "period_start": st.start.isoformat(),
        "period_end": st.end.isoformat(),
        "opening": money(st.opening),
        "closing": money(st.closing),
        "reconciled": True,
        "txn_count": len(rows),
    }
    if rows and st.account not in ("lakeview-brokerage",):
        d |= {
            "credits": money(sum(r.amount for r in rows if r.amount > 0)),
            "debits": money(sum(r.amount for r in rows if r.amount < 0)),
            "txn_sum": money(sum(r.amount for r in rows)),
        }
    d |= st.extra
    return d


def in_run(run: str, when: date) -> bool:
    lo, hi = RUN_MONTHS[run]
    return when.year == YEAR and lo <= when.month <= hi


def csv_bytes(rows: list[Row]) -> tuple[bytes, int]:
    cells = sorted((r.cells() for r in rows), key=csv_sort_key)
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_HEADER)
    for c in cells:
        w.writerow([c[f] for f in CSV_HEADER])
    return buf.getvalue().encode("utf-8"), len(cells)


def json_bytes(obj: object) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def balances_for(stmts: list[Stmt]) -> list[dict]:
    out = []
    for st in stmts:
        if st.account in TRACKED_BY_PAYMENTS:
            continue  # cards tracked by payments carry no statement balances
        bal = st.extra.get("balance", money(st.closing))
        out.append(
            {
                "account_key": st.account,
                "as_of": st.end.isoformat(),
                "source": "statement",
                "balance": bal,
            }
        )
    return out


def holdings_for(stmts: list[Stmt]) -> list[dict]:
    return [
        {"account_key": st.account, "period_end": st.end.isoformat(), **h}
        for st in stmts
        for h in st.holdings
    ]


def ident_sort(items: list[dict], fields: tuple[str, ...]) -> list[dict]:
    return sorted(items, key=lambda it: tuple(it.get(f) for f in fields))


# --------------------------------------------------------------------------
# Format 1.3: rewards (monthly state) and trips (history)
# --------------------------------------------------------------------------

LAKE_GAS = ("QUICKFUEL STATION", "2026-07-06")
TRIP_TAGS = (
    (*LAKE_GAS, "lake-weekend"),
    ("SQ *MAPLE CAFE #12", "2026-09-08", "day-trip-sep26"),  # no run defines it
)
ALL_FOUR = ["alex", "jamie", "riley", "sam"]


def _cycles(cadence: str, used: dict[int, tuple[str, str]], **extra: dict) -> list[dict]:
    """This year's cycles. `used`: cycle number -> (used, used_on). `extra`:
    cycle number (as text) -> more fields, like a bigger December amount."""
    step = {"monthly": 1, "quarterly": 3, "semi_annual": 6, "annual": 12}[cadence]
    out = []
    for n, first in enumerate(range(1, 13, step), start=1):
        end = month_end(first + step - 1)
        cycle: dict = {"start": f"{YEAR}-{first:02d}-01", "end": end.isoformat()}
        if n in used:
            cycle["used"], cycle["used_on"] = used[n]
        cycle |= extra.get(str(n), {})
        out.append(cycle)
    return out


def rewards_section() -> dict:
    """The monthly run's rewards. Every list sorted by key. No numbers in text."""
    programs = [
        {
            "key": "harbor-miles",
            "name": "Harbor Miles",
            "kind": "miles",
            "group": "primary",
            "balance": "42000",
            "as_of": "2026-10-01",
            "source": "manual",
            "owner": "sam",
            "earned_on": ["cobalt-card"],
            "status_tier": "Silver",
            "status_via": "cobalt-card",
            "cpp_low": "1.0",
            "cpp_high": "1.4",
            "best_uses": ["Award flights booked early"],
            "avoid": ["Seat upgrades"],
        },
        {
            "key": "lantern-hotels",
            "name": "Lantern Hotels",
            "kind": "points",
            "group": "secondary",
            "balance": "12000",
            "as_of": "2026-06-30",  # stale on purpose (warning reward-stale)
            "source": "manual",
            "owner": "alex",
            "status_tier": "Gold",
            "status_via": "summit-card",
            "cpp_low": "0.5",
            "cpp_high": "0.7",
            "best_uses": ["Free nights in the off season"],
            "avoid": ["Points plus cash stays"],
            "expires_on": "2027-09-30",  # after the free night, so it is not the next deadline
        },
        {
            "key": "pine-cash-back",
            "name": "Pine Cash Back",
            "kind": "cash_back",
            "group": "secondary",
            "balance": "37.42",
            "as_of": "2026-09-30",
            "source": "statement",
            "owner": "alex",
            "earned_on": ["orchard-card"],
        },
        {
            "key": "shared-vacation-club",
            "name": "Shared Vacation Club",
            "kind": "timeshare",
            "group": "secondary",
            "allocations": [
                {
                    "year": 2026,
                    "allocated": "1000",
                    "used": "800",
                    "expires_on": "2026-12-31",
                    "book_by": "2026-11-30",
                },
                {
                    "year": 2027,
                    "allocated": "1000",
                    "used": "0",
                    "pulled_forward": "200",
                    "expires_on": "2027-12-31",
                    "book_by": "2027-11-30",
                },
            ],
            "note": "Book the summer week before the deadline",
        },
        {
            "key": "summit-points",
            "name": "Summit Points",
            "kind": "points",
            "group": "primary",
            "balance": "84250",
            "as_of": "2026-09-30",
            "source": "statement",
            "earned_on": ["summit-card"],
            "cpp_low": "1.2",
            "cpp_high": "1.8",
            "best_uses": ["Transfer to airline partners", "Hotel stays at peak times"],
            "avoid": ["Gift cards", "Shopping with points"],
        },
    ]
    benefits = [
        {
            "key": "airline-fee-credit",
            "account_key": "summit-card",
            "name": "Airline fee credit",
            "kind": "credit",
            "cadence": "semi_annual",
            "amount": "50.00",
            "cycles": _cycles("semi_annual", {1: ("50.00", "2026-03-14")}),
            "ends_on": "2026-12-31",
            "how_to_use": "Pay seat or bag fees with this card",
            "note": "This credit goes away after this year",
        },
        {
            "key": "anniversary-night",
            "account_key": "summit-card",
            "name": "Anniversary free night",
            "kind": "free_night",
            "program_key": "lantern-hotels",
            "ends_on": "2027-03-31",
            "how_to_use": "Book a standard room on points",
        },
        {
            "key": "dining-credit",
            "account_key": "summit-card",
            "name": "Dining credit",
            "kind": "credit",
            "cadence": "monthly",
            "amount": "10.00",
            "cycles": _cycles(
                "monthly",
                {m: ("10.00", f"2026-{m:02d}-12") for m in range(1, 9)}
                | {9: ("6.00", "2026-09-14")},
                **{"12": {"amount": "20.00"}},
            ),
            "counted_value": "100.00",
            "how_to_use": "Order from the partner delivery app",
        },
        {
            "key": "fuel-bonus",
            "account_key": "cobalt-card",
            "name": "Fuel bonus",
            "kind": "earning_cap",
            "cap": "6000.00",
            "earn_before": "3x",
            "earn_after": "1x",
            "category_keys": ["fuel"],
            "reported_spent": "1820.40",
            "as_of": "2026-09-30",
        },
        {
            "key": "hotel-status",
            "account_key": "summit-card",
            "name": "Hotel status",
            "kind": "status_perk",
            "program_key": "lantern-hotels",
            "status_tier": "Gold",
        },
        {
            "key": "streaming-credit",
            "account_key": "cobalt-card",
            "name": "Streaming credit",
            "kind": "credit",
            "cadence": "quarterly",
            "amount": "15.00",
            "cycles": _cycles(
                "quarterly",
                {
                    1: ("15.00", "2026-02-03"),
                    2: ("15.00", "2026-05-04"),
                    3: ("15.00", "2026-08-03"),
                },
            ),
        },
        {
            "key": "travel-credit",
            "account_key": "summit-card",
            "name": "Yearly travel credit",
            "kind": "credit",
            "cadence": "annual",
            "amount": "100.00",
            "cycles": _cycles("annual", {}),
            "enrolled": False,
            "enroll_by": "2026-10-31",
            "how_to_use": "Enroll on the card's benefits page first",
        },
        {
            "key": "wireless-credit",
            "account_key": "orchard-card",
            "name": "Wireless credit",
            "kind": "credit",
            "cadence": "monthly",
            "amount": "5.00",
            "cycles": _cycles("monthly", {m: ("5.00", f"2026-{m:02d}-05") for m in range(1, 10)}),
            "auto": True,
        },
    ]
    milestones = [
        {
            "key": "cobalt-spend",
            "account_key": "cobalt-card",
            "year": 2026,
            "threshold": "4000.00",
            "reward": "Bonus miles",
            "status": "met",
            "met_on": "2026-08-14",
        },
        {
            "key": "summit-spend",
            "account_key": "summit-card",
            "year": 2026,
            "threshold": "30000.00",
            "reward": "A companion pass",
            "status": "open",
            "reported_spent": "14650.00",
            "as_of": "2026-09-30",
            "plan": "Put dining and groceries on this card through December",
            "note": "Behind pace",
        },
    ]
    routing = [
        ("dining", "Dining and groceries", "summit-card", 1, "4x", None),
        ("everything-else", "Everything else", "orchard-card", 6, "1.5%", None),
        ("fuel", "Fuel", "cobalt-card", 2, "3x", None),
        ("store", "Bramble store purchases", "bramble-store-card", 5, "5%", "Pay in full"),
        ("streaming", "Streaming", "cobalt-card", 4, "2x", None),
        ("travel", "Flights and hotels", "summit-card", 3, "3x", None),
    ]
    return {
        "programs": programs,
        "benefits": benefits,
        "milestones": milestones,
        "routing": [
            {"key": k, "spend": sp, "account_key": a, "display_order": o, "earn": e}
            | ({"note": n} if n else {})
            for k, sp, a, o, e, n in routing
        ],
        "redemptions": [
            {
                "key": "beach-hotel",
                "program_key": "summit-points",
                "title": "Hotel for the beach week",
                "status": "consider",
                "points_needed": "60000",
                "trip_key": "beach-week",
            },
            {
                "key": "ski-flights",
                "program_key": "harbor-miles",
                "title": "Flights for the ski weekend",
                "status": "ready",
                "points_needed": "50000",
                "trip_key": "ski-weekend",
            },
        ],
        "highlights": [
            "The dining credit resets on the first of each month",
            "Summit Points go furthest as airline transfers",
        ],
    }


def lake_weekend(hh: Household) -> dict:
    gas = hh.summit_row(*LAKE_GAS)
    return {
        "key": "lake-weekend",
        "name": "Lake weekend",
        "status": "final",
        "items": [
            {
                "key": "boat",
                "kind": "activities",
                "label": "Boat rental",
                "status": "posted",
                "tender": "covered",
                "amount": "120.00",
                "note": "The relatives paid",
            },
            {
                "key": "gas",
                "kind": "gas",
                "label": "Gas on the way home",
                "status": "posted",
                "tender": "cash",
                "amount": money(-gas.amount),
                "account_key": "summit-card",
                "person": gas.cardholder,
            },
            {
                "key": "lodging",
                "kind": "lodging",
                "label": "Lakeside Lodge",
                "status": "posted",
                "tender": "cash",
                "amount": "210.00",
                "account_key": "summit-card",
                "person": "alex",
            },
            {
                "key": "souvenirs",
                "kind": "shopping",
                "label": "Souvenirs for a friend",
                "status": "excluded",
                "tender": "cash",
                "amount": "35.00",
            },
        ],
        "start_date": "2026-07-03",
        "end_date": "2026-07-06",
        "people": ALL_FOUR,
        "guests": 2,
        "vehicle": "Family car",
        "estimate_low": "450.00",
        "estimate_high": "600.00",
        "ious": [
            {
                "key": "gas-share",
                "direction": "owed_to_us",
                "counterparty": "relative",
                "amount": "25.00",
                "status": "waived",
                "item_key": "gas",
                "note": "Called it a wash",
            }
        ],
        "note": "All costs are on the card statements",
    }


def ski_weekend() -> dict:
    def est(lo: str, hi: str) -> dict:
        return {"status": "estimate", "estimate_low": lo, "estimate_high": hi}

    return {
        "key": "ski-weekend",
        "name": "Ski weekend",
        "status": "booked",
        "items": [
            {
                "key": "flights",
                "kind": "flight",
                "label": "Flights",
                "status": "booked",
                "tender": "points",
                "amount": "11.20",
                "points": "50000",
                "program_key": "harbor-miles",
                "account_key": "cobalt-card",
                "person": "sam",
            },
            {
                "key": "gear",
                "kind": "shopping",
                "label": "Winter gear on the store plan",
                "status": "booked",
                "tender": "cash",
                "amount": "180.00",
                "account_key": "bramble-store-card",
                "person": "sam",
            },
            {
                "key": "hotel",
                "kind": "lodging",
                "label": "Mountain inn",
                "status": "booked",
                "tender": "free_night",
                "amount": "0.00",
                "program_key": "lantern-hotels",
            },
            {
                "key": "lift-tickets",
                "kind": "tickets",
                "label": "Lift tickets",
                "tender": "cash",
                **est("300.00", "420.00"),
            },
            {
                "key": "parking",
                "kind": "parking",
                "label": "Resort parking",
                "status": "tbd",
                "tender": "cash",
            },
            {
                "key": "points-credit",
                "kind": "other",
                "label": "Points toward the inn fees",
                "status": "posted",
                "tender": "points_credit",
                "amount": "40.00",
                "program_key": "summit-points",
            },
            {
                "key": "rental-car",
                "kind": "rental_car",
                "label": "Rental car",
                "status": "booked",
                "tender": "cash",
                "amount": "300.00",
                "account_key": "summit-card",
                "person": "alex",
            },
        ],
        "start_date": "2027-01-15",
        "end_date": "2027-01-18",
        "people": ["alex", "sam"],
        "estimate_low": "1100.00",
        "estimate_high": "1600.00",
        "ious": [
            {
                "key": "car-share",
                "direction": "owed_to_us",
                "counterparty": "friend couple",
                "amount": "150.00",
                "status": "open",
                "item_key": "rental-car",
                "note": "Their half of the rental car",
            }
        ],
        "note": "Book the lift tickets in December",
    }


def beach_week() -> dict:
    def est(key: str, kind: str, label: str, lo: str, hi: str) -> dict:
        return {
            "key": key,
            "kind": kind,
            "label": label,
            "status": "estimate",
            "tender": "cash",
            "estimate_low": lo,
            "estimate_high": hi,
        }

    return {
        "key": "beach-week",
        "name": "Beach week",
        "status": "planning",
        "items": [
            est("flights", "flight", "Flights", "900.00", "1300.00"),
            est("rental", "lodging", "Beach rental", "1200.00", "1800.00"),
        ],
        "month": "2027-06",
        "people": ALL_FOUR,
        "estimate_low": "2400.00",
        "estimate_high": "3600.00",
    }


CITY_BREAK = {
    "key": "city-break",
    "name": "City break",
    "status": "planning",
    "items": [],
    "month": "2026-11",
    "estimate_low": "600.00",
    "estimate_high": "900.00",
}


def trips_for(hh: Household, run: str) -> list[dict]:
    """The second backfill run sends the lake weekend (final) and a city break.
    The monthly run sends two more and removes the city break."""
    if run == RUN_B:
        return [CITY_BREAK, lake_weekend(hh)]
    return [beach_week(), {"deleted": True, "key": "city-break"}, ski_weekend()]


def run_snapshot(hh: Household, run: str) -> tuple[dict, dict[str, list[Row]]]:
    mode = RUN_MODES[run]
    snap: dict = {"schema_version": RUN_VERSIONS[run], "run_id": run, "mode": mode}
    stmts = [
        st for key in sorted(hh.statements) for st in hh.statements[key] if in_run(run, st.end)
    ]
    csvs = {csv_path(st.account, st.end.isoformat()): st.rows for st in stmts if st.rows}
    snap["people"] = PEOPLE
    snap["accounts"] = accounts(run)
    if na := new_accounts(run):
        snap["new_accounts"] = na
    snap["statements"] = ident_sort([stmt_json(st) for st in stmts], ("account_key", "period_end"))
    vals = [v for v in hh.valuations if in_run(run, D(v["as_of"]))]
    balances = balances_for(stmts) + vals
    if run == RUN_M:  # retract the June car valuation sent in the second backfill run
        balances.append(
            {
                "account_key": "family-car",
                "as_of": "2026-06-30",
                "source": "valuation",
                "deleted": True,
            }
        )
    snap["balances"] = ident_sort(balances, ("account_key", "as_of", "source"))
    snap["holdings"] = ident_sort(holdings_for(stmts), ("account_key", "period_end", "key"))
    snap["income"] = {
        "paychecks": ident_sort(
            [p for p in hh.paychecks if in_run(run, D(p["pay_date"]))],
            ("person", "pay_date", "kind", "seq"),
        )
    }
    snap["contributions"] = ident_sort(
        [c for c in hh.contributions if in_run(run, D(c["date"]))],
        ("person", "account_key", "kind", "source", "date"),
    )
    if run in (RUN_B, RUN_M):
        snap["utility_bills"] = utility_bills(hh, run)
        snap["trips"] = trips_for(hh, run)
    if run == RUN_M:
        snap |= state_sections(hh, stmts)
        snap |= state_sections_v11()
        snap["rewards"] = rewards_section()
        snap["income"]["income_streams"] = [
            {
                "key": "alex-pay",
                "person": "alex",
                "label": "Alex pay",
                "kind": "paycheck",
                "schedule": "Every other Friday",
                "expected_net": "2931.20",
                "start_month": "2026-01",
            },
            {
                "key": "sam-pay",
                "person": "sam",
                "label": "Sam pay",
                "kind": "paycheck",
                "schedule": "15th and last day",
                "expected_net": "2250.00",
                "start_month": "2026-01",
            },
        ]
        format_v14(snap)
        # The monthly export has two mistakes in September checking. The correction fixes them.
        csvs = {
            k: [_monthly_mistake(r) for r in v] if "maple-joint-checking" in k else v
            for k, v in csvs.items()
        }
    return snap, csvs


def _monthly_mistake(r: Row) -> Row:
    if r.description == "GREEN LEAF MARKET" and r.txn_date == D("2026-09-16"):
        return Row(**{**r.__dict__, "category": "dining"})  # attribute-only change
    if r.description == "FIBERNET INTERNET" and r.txn_date.month == 9:
        return Row(**{**r.__dict__, "description": "FIBERNET INTERNT"})  # changed row
    return r


def correction_snapshot(hh: Household) -> tuple[dict, dict[str, list[Row]]]:
    st = next(s for s in hh.statements["maple-joint-checking"] if s.end.month == 9)
    snap = {
        "schema_version": RUN_VERSIONS[RUN_C],
        "run_id": RUN_C,
        "mode": "correction",
        "statements": [stmt_json(st)],
        "balances": balances_for([st]),
    }
    return snap, {csv_path(st.account, st.end.isoformat()): st.rows}


def latest_balances(hh: Household, as_of: date) -> dict[str, int]:
    out: dict[str, tuple[date, int]] = {}
    for key, stmts in hh.statements.items():
        if key in TRACKED_BY_PAYMENTS:
            continue
        for st in stmts:
            if st.end <= as_of:
                bal = st.closing
                if "balance" in st.extra:
                    bal = int(st.extra["balance"].replace(".", ""))
                out[key] = (st.end, bal)
    for v in hh.valuations:
        if D(v["as_of"]) <= as_of:
            out[v["account_key"]] = (D(v["as_of"]), int(v["balance"].replace(".", "")))
    return {k: v[1] for k, v in out.items() if k != "old-store-card"}


def state_sections(hh: Household, stmts: list[Stmt]) -> dict:
    accts = {a["key"]: a for a in accounts(RUN_M)}
    bal = latest_balances(hh, D("2026-09-30"))

    def part_of(k: str) -> str:
        """A loan secured by an asset in net worth counts in that asset's group (net equity)."""
        a = accts[k]
        home = accts.get(a.get("secured_by") or "")
        if (
            a["type"] in ("loan", "mortgage", "heloc")
            and home
            and home["is_asset"]
            and home["include_in_net_worth"]
        ):
            return home["nw_group"]
        return a["nw_group"]

    parts: dict[str, int] = {}
    for k, v in bal.items():
        parts[part_of(k)] = parts.get(part_of(k), 0) + v
    assets = sum(v for k, v in bal.items() if accts[k]["is_asset"])
    liabilities = -sum(v for k, v in bal.items() if not accts[k]["is_asset"])
    cats = {
        c["key"]: c
        for c in json.loads((ROOT / "schema" / "categories.json").read_text(encoding="utf-8"))[
            "categories"
        ]
    }

    def actual(month: int) -> dict:
        income = spending = 0
        groups: dict[str, int] = {}
        for key in (
            "maple-joint-checking",
            "harbor-checking",
            "summit-card",
            "orchard-card",
            "cobalt-card",
            "bramble-store-card",
        ):
            for st in hh.statements.get(key, []):
                for r in st.rows:
                    if r.txn_date.month != month or r.txn_date.year != YEAR:
                        continue
                    c = cats[r.category]
                    if c["kind"] == "income":
                        income += r.amount
                    elif c["kind"] == "spending":
                        spending -= r.amount
                        groups[c["group"]] = groups.get(c["group"], 0) - r.amount
        return {
            "month": f"{YEAR}-{month:02d}",
            "income": money(income),
            "spending": money(spending),
            "saved_invested": "700.00",
            "by_group": [{"key": g, "amount": money(groups[g])} for g in sorted(groups)],
        }

    net = assets - liabilities
    plan_groups = [
        {"key": "housing", "amount": "3600.00"},
        {"key": "lifestyle", "amount": "3650.00"},
        {"key": "plan-installments", "amount": "180.00"},
        {"key": "saved-invested", "amount": "700.00"},
        {"key": "services", "amount": "250.00"},
        # Fuel moved here from Lifestyle in Phase 7: the old 3870.00 split in two.
        {"key": "transportation", "amount": "220.00"},
    ]
    return {
        "meta": {
            "updated": "2026-10-12",
            "this_month": "2026-10",
            "complete_as_of": "2026-09-30",
            "location_label": "Sample Town",
        },
        "net_worth": {
            "as_of": "2026-09-30",
            "value": money(net),
            "assets": money(assets),
            "liabilities": money(liabilities),
            "parts": [{"key": g, "amount": money(parts[g])} for g in sorted(parts)],
        },
        "cashflow_actual": [actual(8), actual(9)],
        "cashflow_plan": [
            {
                "month": "2026-10",
                "planned_income": "11000.00",
                # Planned spending includes the saved and invested moves: by_group adds up to it.
                "planned_spending": money(
                    sum(int(g["amount"].replace(".", "")) for g in plan_groups)
                ),
                "by_group": plan_groups,
                "note": "Sample plan for October",
            }
        ],
        "cashflow_events": [
            {
                "key": "device-plan-ends",
                "effective_month": "2027-02",
                "delta": "75.00",
                "label": "Device installments end",
                "linked_kind": "plan",
                "linked_key": "device-plan",
            },
            {
                "key": "plan-sofa-ends",
                "effective_month": "2026-12",
                "delta": "105.00",
                "label": "Sofa plan ends",
                "reason": "Last Plan It installment in November",
                "linked_kind": "plan",
                "linked_key": "plan-sofa",
            },
        ],
        "cashflow_phases": [
            {
                "key": "base",
                "label": "Base",
                "start_month": "2026-10",
                "end_month": "2026-12",
                "surplus": "400.00",
                "reason": "Plans still running",
            },
            {
                "key": "plans-done",
                "label": "Plans done",
                "start_month": "2027-01",
                "surplus": "580.00",
            },
        ],
        "budgets": [
            {
                "key": "dining",
                "month": "2026-10",
                "label": "Dining",
                "category_key": "dining",
                "target": "400.00",
                "period_basis": "calendar",
                "plan": "400.00",
            },
            {
                "key": "sams-cards",
                "month": "2026-10",
                "label": "Sam's cards",
                "scope": {"owner": "sam", "cards": ["cobalt-card"]},
                "period_basis": "card_cycle",
                "cycle_note": "Cycle closes on the 22nd",
                "target": "300.00",
                "reported_actual": "212.50",
                "actual_source": "statement",
                "statements_needed": ["cobalt-card"],
            },
        ],
        "payoff_sequence": [
            {
                "key": "promo-mattress",
                "month": "2027-03",
                "when_label": "By March 2027",
                "action": "Pay off the 0% promo before it ends",
            },
        ],
        "todos": [
            {
                "key": "check-car-value",
                "title": "Check the car value",
                "status": "open",
                "due_date": "2026-10-31",
                "subtitle": "Quarterly valuation",
            },
            {"key": "file-clinic-claim", "title": "File the clinic claim", "status": "done"},
        ],
        "review": {
            "summary": "A steady month. Spending was close to plan.",
            "key_numbers": [{"label": "Net worth", "value": money(net)}],
            "changes": ["The store card is closed and paid off."],
            "anomalies": ["Two identical grocery charges on one day in July."],
            "top_three": [
                "Keep paying down the 0% promo.",
                "Check the car value.",
                "Review the uncategorized charge.",
            ],
        },
    }


# --------------------------------------------------------------------------
# Utility bills (format 1.2). Own RNG, so the rest of the samples never move.
# --------------------------------------------------------------------------

UTILITY_SEED = SEED + 12
PAID_FROM = "maple-joint-checking"
# By the month the period mostly covers (15 days before it ends).
GAS_SHARE_PCT = {
    1: 62,
    2: 60,
    3: 50,
    4: 35,
    5: 22,
    6: 18,
    7: 18,
    8: 18,
    9: 20,
    10: 30,
    11: 45,
    12: 58,
}
HEATING_DD = {1: 1250, 2: 1050, 3: 850, 4: 500, 5: 220, 6: 40, 7: 5, 8: 10, 9: 90, 10: 400, 11: 800}
HEATING_DD[12] = 1150


@dataclass(frozen=True)
class PowerRates:
    electric_fixed: int  # cents
    per_kwh: int  # milli-cents
    gas_fixed: int  # cents
    per_therm: int  # milli-cents


POWER_RATES = {
    2024: PowerRates(electric_fixed=1900, per_kwh=16400, gas_fixed=1200, per_therm=95000),
    2025: PowerRates(electric_fixed=1900, per_kwh=17100, gas_fixed=1200, per_therm=102000),
    2026: PowerRates(electric_fixed=2000, per_kwh=17900, gas_fixed=1250, per_therm=108000),
}


def _per_unit(cents: int, milli_cents: int) -> str:
    """Whole units bought by `cents` at `milli_cents` each (rounded)."""
    return str((cents * 1000 + milli_cents // 2) // milli_cents)


def _payments(hh: Household, key: str) -> dict[int, int]:
    """2026 month -> payment cents for one recurring_key in Maple joint checking."""
    return {
        r.txn_date.month: -r.amount
        for st in hh.statements["maple-joint-checking"]
        for r in st.rows
        if r.recurring_key == key
    }


def city_power_bills(hh: Household, rng: random.Random) -> list[dict]:
    paid = _payments(hh, "city-power")
    out = []
    prev_end = D("2024-08-02")
    months = [(2024, m) for m in range(9, 13)] + [(2025, m) for m in range(1, 13)]
    months += [(2026, m) for m in range(1, 10)]
    for y, m in months:
        options = [
            date(y, m, d) for d in range(1, 6) if 29 <= (date(y, m, d) - prev_end).days <= 33
        ]
        end = rng.choice(options) if options else date(y, m, 5)
        start, prev_end = prev_end, end
        use_month = (end - timedelta(days=15)).month
        if y == YEAR:
            total = paid[m]
        elif use_month in (12, 1, 2):
            total = rng.randint(16000, 19000)
        elif use_month in (7, 8):
            total = rng.randint(14000, 17000)
        else:
            total = rng.randint(11000, 15000)
        credits = []
        if use_month in (6, 7, 8):
            credits.append({"label": "Smart thermostat credit", "amount": "-2.19"})
        if (y, m) == (2026, 9):
            credits.insert(0, {"label": "Energy savings credit", "amount": "-5.00"})
        gross = total - sum(int(c["amount"].replace(".", "")) for c in credits)
        gas = gross * GAS_SHARE_PCT[use_month] // 100
        electric = gross - gas
        if (y, m) == (2025, 7):
            electric -= 1  # one bill off by a cent: a warning, not a reject
        rates = POWER_RATES[y]
        fixed_e, rate_e = rates.electric_fixed, rates.per_kwh
        fixed_g, rate_g = rates.gas_fixed, rates.per_therm
        bill_date = end + timedelta(days=3)
        bill = {
            "utility_key": "city-power",
            "provider": "City Power & Gas",
            "bill_date": bill_date.isoformat(),
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "days": (end - start).days,
            "total": money(total),
            "due_date": (bill_date + timedelta(days=20)).isoformat(),
            "paid_from": PAID_FROM,
            "heating_degree_days": max(0, HEATING_DD[use_month] + rng.randint(-60, 60)),
            "lines": [
                {
                    "service": "electric",
                    "usage": _per_unit(electric - fixed_e, rate_e),
                    "unit": "kwh",
                    "charges": money(electric),
                    "fixed_charges": money(fixed_e),
                },
                {
                    "service": "gas",
                    "usage": _per_unit(gas - fixed_g, rate_g),
                    "unit": "therms",
                    "charges": money(gas),
                    "fixed_charges": money(fixed_g),
                },
            ],
        }
        if credits:
            bill["credits"] = credits
        if (y, m) == (2025, 2):
            bill["estimated_read"] = True
            bill["note"] = "Estimated read. Snow covered the meter."
        out.append(bill)
    return out


def town_water_bills(hh: Household, rng: random.Random) -> list[dict]:
    paid = _payments(hh, "town-water")
    ends = ["2025-09-01", "2025-12-01", "2026-03-01", "2026-06-01", "2026-09-01"]
    out = []
    for start, end in zip(ends, ends[1:], strict=False):
        e = D(end)
        total = paid[e.month] if e.year == YEAR else rng.randint(4500, 7000)
        storm = 600
        water = (total - storm) * 55 // 100
        sewer = total - storm - water
        gallons = _per_unit(water - 800, 450)
        bill_date = e + timedelta(days=5)
        out.append(
            {
                "utility_key": "town-water",
                "provider": "Town Water",
                "bill_date": bill_date.isoformat(),
                "period_start": start,
                "period_end": end,
                "days": (e - D(start)).days,
                "total": money(total),
                "due_date": (bill_date + timedelta(days=25)).isoformat(),
                "paid_from": PAID_FROM,
                "lines": [
                    {
                        "service": "sewer",
                        "usage": gallons,
                        "unit": "gallons",
                        "charges": money(sewer),
                        "fixed_charges": money(700),
                    },
                    {
                        "service": "stormwater",
                        "usage": "0",
                        "unit": "none",
                        "charges": money(storm),
                        "fixed_charges": money(storm),
                    },
                    {
                        "service": "water",
                        "usage": gallons,
                        "unit": "gallons",
                        "charges": money(water),
                        "fixed_charges": money(800),
                    },
                ],
            }
        )
    return out


def utility_bills(hh: Household, run: str) -> list[dict]:
    """The bills a run carries: September 2026 in the monthly run, the rest in RUN_B."""
    rng = random.Random(UTILITY_SEED)
    bills = city_power_bills(hh, rng) + town_water_bills(hh, rng)
    sep = [b for b in bills if b["period_end"] >= "2026-09-01"]
    mine = sep if run == RUN_M else [b for b in bills if b not in sep]
    return ident_sort(mine, ("utility_key", "period_end"))


def state_sections_v11() -> dict:
    """The format 1.1 state sections of the monthly run (no RNG, so nothing else moves).

    Plans match the CSV rows: device-plan has 8 of 12 installments billed
    (Feb to Sep), plan-sofa 4 of 6 (Jun to Sep statements). Their monthly
    payments plus the sofa fee make the plan's `plan-installments` group (180.00).
    """

    def rec(key: str, name: str, category: str, amount: str, cadence: str, **kw) -> dict:
        return {
            "key": key,
            "name": name,
            "category_key": category,
            "amount": amount,
            "cadence": cadence,
            "status": kw.pop("status", "active"),
            **kw,
        }

    recurring = [
        rec(
            "city-power",
            "City Power and Gas",
            "gas-and-electric",
            "150.00",
            "monthly",
            variable=True,
            next_date="2026-10-18",
            account_key="maple-joint-checking",
            payment_rail="ach",
            owner="joint",
        ),
        rec(
            "cloudtunes",
            "Cloudtunes",
            "subscriptions",
            "10.99",
            "monthly",
            next_date="2026-10-07",
            account_key="orchard-card",
            payment_rail="card",
            owner="alex",
            credit_offset="10.99",
            credit_note="A separate Cloudtunes promotion pays it back",
        ),
        rec(
            "fibernet-internet",
            "Fibernet",
            "internet",
            "79.99",
            "monthly",
            next_date="2026-10-14",
            account_key="maple-joint-checking",
            payment_rail="ach",
            owner="joint",
        ),
        rec(
            "gym",
            "Ironworks Gym",
            "fitness",
            "45.00",
            "monthly",
            status="ending",
            next_date="2026-11-02",
            active_through="2026-12-31",
            review_by="2026-11-15",
            account_key="harbor-checking",
            payment_rail="ach",
            owner="sam",
            notes="Cancel notice sent. December is the last month.",
        ),
        rec(
            "house-cleaning",
            "Sparkle Home Cleaning",
            "cleaning",
            "140.00",
            "every_n_weeks",
            interval_n=3,
            status="paused",
            start_date="2025-06-03",
            account_key="maple-joint-checking",
            payment_rail="check",
            owner="joint",
            notes="Paused for the fall",
        ),
        rec(
            "kids-allowance",
            "Kids allowance",
            "allowances",
            "10.00",
            "weekly",
            next_date="2026-10-16",
            account_key="maple-joint-checking",
            payment_rail="ach",
            owner="joint",
        ),
        rec(
            "lawn-care",
            "Green Thumb Lawn Care",
            "home-upkeep",
            "85.00",
            "monthly",
            active_months=[4, 5, 6, 7, 8, 9, 10],
            next_date="2026-10-15",
            account_key="maple-joint-checking",
            payment_rail="bill_pay",
            owner="joint",
            notes="Season runs April to October",
        ),
        rec(
            "mortgage",
            "Pinecrest Mortgage",
            "mortgage",
            "1500.00",
            "monthly",
            next_date="2026-11-01",
            account_key="maple-joint-checking",
            payment_rail="ach",
            owner="joint",
            end_date="2049-06-01",
        ),
        rec(
            "music-lessons",
            "Riverside Music Lessons",
            "kids-activities",
            "80.00",
            "monthly",
            active_months=[1, 2, 3, 4, 5, 9, 10, 11, 12],
            start_date="2026-10-30",
            next_date="2026-10-30",
            account_key="harbor-checking",
            payment_rail="ach",
            owner="riley",
            notes="School year only, September to May",
        ),
        rec(
            "personal-subscription",
            "Personal subscription",
            "subscriptions",
            "14.99",
            "monthly",
            next_date="2026-10-03",
            account_key="cobalt-card",
            payment_rail="card",
            owner="sam",
        ),
        rec(
            "pest-control",
            "Shield Pest Control",
            "pest-control",
            "120.00",
            "quarterly",
            start_date="2025-02-05",
            next_date="2026-11-05",
            account_key="summit-card",
            payment_rail="card",
            owner="joint",
        ),
        rec(
            "reelhouse",
            "Reelhouse Streaming",
            "subscriptions",
            "15.49",
            "monthly",
            status="ended",
            end_date="2026-09-12",
            account_key="orchard-card",
            payment_rail="card",
            owner="alex",
            notes="Cancelled in September",
        ),
        rec(
            "savings-transfer",
            "Transfer to Harbor Savings",
            "transfer",
            "200.00",
            "monthly",
            next_date="2026-10-28",
            account_key="harbor-checking",
            payment_rail="ach",
            owner="sam",
        ),
        rec(
            "summit-annual-fee",
            "Summit card annual fee",
            "annual-fees",
            "250.00",
            "annual",
            next_date="2027-03-01",
            renewal_date="2027-03-01",
            review_by="2027-02-01",
            account_key="summit-card",
            payment_rail="card",
            owner="joint",
            credit_offset="100.00",
            credit_note="The yearly travel credit offsets part of it",
        ),
        rec(
            "town-water",
            "Town Water",
            "water-and-sewer",
            "60.00",
            "monthly",
            variable=True,
            next_date="2026-10-22",
            account_key="maple-joint-checking",
            payment_rail="ach",
            owner="joint",
        ),
        rec(
            "wave-mobile",
            "Wave Mobile",
            "phone",
            "65.00",
            "monthly",
            next_date="2026-10-12",
            account_key="harbor-checking",
            payment_rail="ach",
            owner="sam",
            preferred_account_key="summit-card",
            notes="Paying with the Summit card adds phone protection",
        ),
        rec(
            "youth-soccer",
            "Youth Soccer Club",
            "kids-activities",
            "120.00",
            "semester",
            active_months=[3, 8],
            next_date="2027-03-03",
            account_key="harbor-checking",
            payment_rail="card",
            owner="sam",
        ),
    ]
    plans = [
        {
            "key": "bramble-deferred",
            "account_key": "bramble-store-card",
            "kind": "deferred_interest",
            "name": "Bramble winter gear",
            "created_on": "2025-11-28",
            "original": "480.00",
            "status": "active",
            "monthly_payment": "40.00",
            "remaining": "120.00",
            "pay_before": "2026-11-28",
            "end_month": "2026-11",
            "included_in_spending_plan": False,
            "components": [
                {"merchant": "Bramble Outfitters", "amount": "300.00"},
                {"merchant": "Bramble Outfitters", "amount": "180.00", "trip_key": "ski-weekend"},
            ],
        },
        {
            "key": "device-plan",
            "account_key": "orchard-card",
            "kind": "apple_installment",
            "name": "Orchard Store device",
            "created_on": "2026-02-14",
            "original": "900.00",
            "status": "active",
            "monthly_payment": "75.00",
            "payments_total": 12,
            "payments_left": 4,
            "remaining": "300.00",
            "end_month": "2027-01",
            "included_in_spending_plan": True,
        },
        {
            "key": "plan-sofa",
            "account_key": "summit-card",
            "kind": "plan_it",
            "name": "Oak and Pine sofa",
            "created_on": "2026-04-22",
            "original": "600.00",
            "status": "active",
            "monthly_payment": "100.00",
            "monthly_fee": "5.00",
            "total_fees": "30.00",
            "payments_total": 6,
            "payments_left": 2,
            "remaining": "200.00",
            "end_month": "2026-11",
            "included_in_spending_plan": True,
            "components": [{"merchant": "Oak and Pine Furniture", "amount": "600.00"}],
        },
        {
            "key": "promo-mattress",
            "account_key": "cobalt-card",
            "kind": "promo_0pct",
            "name": "Lakeside mattress",
            "created_on": "2026-05-10",
            "original": "1200.00",
            "status": "active",
            "monthly_payment": "150.00",
            "promo_expiry": "2027-03-31",
            "end_month": "2027-03",
            "included_in_spending_plan": False,
        },
        {
            "key": "towne-coats",
            "account_key": "old-store-card",
            "kind": "deferred_interest",
            "name": "Towne winter coats",
            "created_on": "2025-12-20",
            "original": "240.00",
            "status": "paid_off",
            "monthly_payment": "40.00",
            "remaining": "0.00",
            "pay_before": "2026-06-20",
            "end_month": "2026-06",
            "included_in_spending_plan": False,
        },
    ]
    flags = [
        {
            "key": "bramble-promo-ends",
            "severity": "high",
            "status": "open",
            "kind": "debt",
            "title": "Pay off the Bramble promo before it ends",
            "body": "Deferred interest applies to the whole purchase if any balance is left.",
            "related_kind": "plan",
            "related_key": "bramble-deferred",
            "due_date": "2026-11-20",
            "created_on": "2026-09-12",
        },
        {
            "key": "cloudtunes-credit",
            "severity": "low",
            "status": "watching",
            "kind": "subscription",
            "title": "Check that the streaming credit posts each month",
            "related_kind": "recurring",
            "related_key": "cloudtunes",
            "created_on": "2026-08-10",
        },
        {
            "key": "fibernet-rate",
            "severity": "low",
            "status": "open",
            "kind": "cost_cutting",
            "title": "Ask Fibernet for a lower rate",
            "related_kind": "recurring",
            "related_key": "fibernet-internet",
            "created_on": "2026-10-05",
        },
        {
            "key": "gym-ending",
            "severity": "medium",
            "status": "open",
            "kind": "cost_cutting",
            "title": "Gym membership ends in December",
            "related_kind": "recurring",
            "related_key": "gym",
            "created_on": "2026-10-01",
        },
        {
            "key": "hsa-claim-paid",
            "severity": "low",
            "status": "resolved",
            "kind": "tax",
            "title": "Clinic visit paid back from the HSA",
            "related_kind": "account",
            "related_key": "maple-joint-checking",
            "created_on": "2026-04-10",
            "resolved_on": "2026-05-03",
            "archived": True,
        },
        {
            "key": "lake-weekend-costs",
            "severity": "low",
            "status": "resolved",
            "kind": "travel",
            "title": "Lake weekend costs are all posted",
            "related_kind": "trip",
            "related_key": "lake-weekend",
            "created_on": "2026-07-05",
            "resolved_on": "2026-07-12",
        },
        {
            "key": "promo-mattress-ends",
            "severity": "medium",
            "status": "open",
            "kind": "debt",
            "title": "The mattress promo ends in March",
            "related_kind": "plan",
            "related_key": "promo-mattress",
            "due_date": "2027-03-31",
            "created_on": "2026-05-12",
        },
        {
            "key": "savings-milestone",
            "severity": "low",
            "status": "open",
            "kind": "milestone",
            "title": "Savings passed three months of bills",
            "related_kind": "account",
            "related_key": "harbor-savings",
            "created_on": "2026-09-30",
        },
        {
            "key": "summit-fee-review",
            "severity": "medium",
            "status": "open",
            "kind": "rewards",
            "title": "Decide on the Summit card before the fee renews",
            "related_kind": "account",
            "related_key": "summit-card",
            "due_date": "2027-02-01",
            "created_on": "2026-09-30",
        },
        {
            "key": "unknown-vendor",
            "severity": "high",
            "status": "watching",
            "kind": "fraud",
            "title": "Review the charge from an unknown vendor",
            "related_kind": "category",
            "related_key": "uncategorized",
            "created_on": "2026-07-30",
        },
    ]
    pending = [
        {
            "key": "apply-gym-cancel",
            "kind": "apply",
            "status": "waiting",
            "subject": "Take the gym out of the plan after December",
            "detail": "Waiting for the last charge to post.",
            "added_on": "2026-10-01",
            "updated_on": "2026-10-12",
        },
        {
            "key": "apply-water-budget",
            "kind": "apply",
            "status": "done",
            "subject": "Raise the water budget for the summer months",
            "added_on": "2026-08-15",
            "updated_on": "2026-09-20",
        },
        {
            "key": "confirm-bramble-payment",
            "kind": "confirm",
            "status": "open",
            "subject": "Confirm the Bramble payment for October",
            "detail": "Autopay is set to the minimum.",
            "added_on": "2026-10-12",
        },
    ]
    return {"recurring": recurring, "installment_plans": plans, "flags": flags, "pending": pending}


# --------------------------------------------------------------------------
# Format 1.4: the monthly run only (docs/plans/format-14.md section 8)
# --------------------------------------------------------------------------

# Only added to the 1.3 monthly run, never changing a 1.3 value. New whole items have
# keys that sort after the 1.3 items the 1.4 test cases edit (gym-ending, plan-sofa-ends,
# ski-flights), and are listed in backend/tests/bad_cases.py V14_SAMPLE_ITEMS.
V14_FLAG_ACTIONS = {
    "bramble-promo-ends": "act",
    "fibernet-rate": "ask",
    "hsa-claim-paid": "reimburse",
    "lake-weekend-costs": "info",
    "promo-mattress-ends": "act",
    "savings-milestone": "info",
    "summit-fee-review": "act",
    "unknown-vendor": "act",
}  # cloudtunes-credit and gym-ending send none: they read as act
V14_FLAG_UPDATES = {
    "bramble-promo-ends": [
        {"on": "2026-09-20", "text": "Checked the promo end date on the statement"},
        {"on": "2026-10-10", "text": "Three payments left before the deadline"},
    ],
    "unknown-vendor": [  # two on one day, sorted by text
        {"on": "2026-08-02", "text": "Asked the card issuer about the charge"},
        {"on": "2026-08-02", "text": "The issuer opened a review"},
    ],
}
V14_FLAGS = [  # one per new kind, nothing references them
    {
        "key": "health-claim",
        "severity": "low",
        "status": "open",
        "kind": "medical",
        "action": "reimburse",
        "title": "Claim the dentist bill from the health account",
        "due_date": "2026-11-30",
        "created_on": "2026-09-22",
    },
    {
        "key": "home-insurance-renewal",
        "severity": "medium",
        "status": "open",
        "kind": "insurance",
        "action": "ask",
        "title": "Ask for a new home insurance quote before it renews",
        "due_date": "2026-12-15",
        "created_on": "2026-10-02",
    },
    {
        "key": "kids-college-gift",
        "severity": "low",
        "status": "open",
        "kind": "investment",
        "action": "info",
        "title": "A family gift went into the college fund",
        "due_date": "2026-10-01",  # an info flag's date, already past
        "created_on": "2026-09-08",
    },
    {
        "key": "library-fees",
        "severity": "low",
        "status": "resolved",
        "kind": "other",
        "action": "info",
        "title": "The library fees are paid",
        "created_on": "2026-09-02",
        "resolved_on": "2026-09-10",
    },
    {
        "key": "old-plan-rollover",
        "severity": "medium",
        "status": "open",
        "kind": "retirement",
        "action": "act",
        "title": "Roll the old workplace plan into an IRA",
        "body": "The forms are in. The check goes to the new account.",
        "created_on": "2026-08-20",
        "updates": [  # four: the Flags page shows the newest three
            {"on": "2026-08-25", "text": "Called the old plan about a rollover"},
            {"on": "2026-09-01", "text": "Opened the IRA at the new broker"},
            {"on": "2026-09-15", "text": "Asked for the rollover forms"},
            {"on": "2026-10-10", "text": "Forms sent, waiting on the check"},
        ],
    },
    {
        "key": "roof-repair",
        "severity": "medium",
        "status": "watching",
        "kind": "home",
        "action": "act",
        "title": "Get two quotes for the roof repair",
        "created_on": "2026-09-01",
    },
    {
        "key": "withholding-check",
        "severity": "low",
        "status": "open",
        "kind": "payroll",
        "action": "act",
        "title": "Check the tax withholding after the raise",
        "created_on": "2026-10-05",
    },
]
V14_RECURRING = {
    "cloudtunes": {"tag": "streaming"},
    "gym": {"tag": "hobbies"},
    "music-lessons": {"tag": "hobbies", "keep": True},  # kept, no review date
    "reelhouse": {"tag": "streaming"},
    "summit-annual-fee": {"keep": True},  # it has a review_by: kept for now
}  # never a tag or keep on personal-subscription
V14_PLANS = {
    "promo-mattress": {"note": "Pay it off before the promo ends"},
    "towne-coats": {"note": "Paid off a month early", "paid_off_on": "2026-05-18"},
}
V14_EVENT_STATUS = {"device-plan-ends": "expected", "plan-sofa-ends": "confirmed"}
V14_EVENTS = [
    {
        "key": "switch-phone-plan",
        "effective_month": "2027-05",
        "delta": "20.00",
        "label": "Move to a cheaper phone plan",
        "reason": "An idea from the review, not decided",
        "status": "suggested",
    },
    {
        "key": "trim-dining",
        "effective_month": "2027-03",
        "delta": "50.00",
        "label": "Dining back to the budget",
        "status": "target",
    },
]
V14_ACCOUNTS = {
    "orchard-card": {"rate_changes": [{"apr": "19.99", "effective_on": "2026-11-01"}]},
    "pinecrest-mortgage": {"original": money(30000000)},
    "van-loan": {"original": money(4200000)},  # the Aug 1 balance from the loan papers
}
V14_SNORKEL = {  # a maybe item: listed as open, left out of the trip totals
    "key": "snorkel-tour",
    "kind": "activities",
    "label": "Snorkel tour",
    "status": "estimate",
    "tender": "cash",
    "estimate_low": "150.00",
    "estimate_high": "250.00",
    "maybe": True,
}
V14_REDEMPTION = {  # points_needed: the points it used
    "key": "spring-stay",
    "program_key": "lantern-hotels",
    "title": "Spring weekend stay",
    "status": "done",
    "points_needed": "20000",
    "done_on": "2026-04-18",
}
V14_GOALS = [  # sorted by key
    {
        "key": "college-jamie",
        "name": "College, older child",
        "kind": "college",
        "accounts": ["kids-529"],
        "target": money(12000000),
        "person": "jamie",
        "start_year": 2034,
    },
    {
        "key": "emergency-fund",
        "name": "Emergency fund",
        "kind": "save_up",
        "accounts": ["harbor-savings"],
        "target": "30000.00",
        "target_date": "2027-12-31",
        "note": "Six months of bills",
    },
]


def _by_key(items: list[dict]) -> dict[str, dict]:
    return {i["key"]: i for i in items if "key" in i}


def format_v14(snap: dict) -> None:
    """The 1.4 fields, values and items on the monthly run (fake values, no RNG)."""
    flags = _by_key(snap["flags"])
    for key, action in V14_FLAG_ACTIONS.items():
        flags[key]["action"] = action
    for key, updates in V14_FLAG_UPDATES.items():
        flags[key]["updates"] = updates
    snap["flags"] = ident_sort(snap["flags"] + V14_FLAGS, ("key",))
    bills = _by_key(snap["recurring"])
    for key, fields in V14_RECURRING.items():
        bills[key] |= fields
    plans = _by_key(snap["installment_plans"])
    for key, fields in V14_PLANS.items():
        plans[key] |= fields
    for e in snap["cashflow_events"]:
        e["status"] = V14_EVENT_STATUS[e["key"]]
    snap["cashflow_events"] = ident_sort(snap["cashflow_events"] + V14_EVENTS, ("key",))
    # accounts(run) is shared with the 1.3 second backfill run: copy before adding.
    snap["accounts"] = [a | V14_ACCOUNTS.get(a["key"], {}) for a in snap["accounts"]]
    trips = _by_key(snap["trips"])
    _by_key(trips["ski-weekend"]["items"])["rental-car"]["due_on"] = "2026-11-10"
    beach = trips["beach-week"]
    beach["items"] = ident_sort([*beach["items"], V14_SNORKEL], ("key",))
    r = snap["rewards"]
    r["redemptions"] = ident_sort([*r["redemptions"], V14_REDEMPTION], ("key",))
    snap["goals"] = V14_GOALS


def write_run(out: Path, run: str, snap: dict, csvs: dict[str, list[Row]]) -> None:
    folder = out / run
    if folder.exists():
        shutil.rmtree(folder)
    (folder / "transactions").mkdir(parents=True)
    files = []
    data = json_bytes(snap)
    (folder / "snapshot.json").write_bytes(data)
    files.append(
        {"path": "snapshot.json", "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    )
    for rel in sorted(csvs):
        data, n = csv_bytes(csvs[rel])
        (folder / rel).write_bytes(data)
        files.append(
            {"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "rows": n}
        )
    if not csvs:
        (folder / "transactions").rmdir()
    manifest = {
        "schema_version": RUN_VERSIONS[run],
        "run_id": run,
        "mode": RUN_MODES[run],
        "created_at": f"{run[:10]}T18:00:00-05:00",
        "generator": GENERATOR,
        "files": sorted(files, key=lambda f: f["path"]),
    }
    (folder / "manifest.json").write_bytes(json_bytes(manifest))


def generate(out: Path) -> list[str]:
    """Write every sample run under `out` and the private rules example next to it."""
    out.mkdir(parents=True, exist_ok=True)
    for old in out.iterdir():
        if old.is_dir() and is_run_id(old.name):
            shutil.rmtree(old)
    hh = Household()
    hh.build()
    for run in (RUN_A, RUN_B, RUN_M):
        write_run(out, run, *run_snapshot(hh, run))
    write_run(out, RUN_C, *correction_snapshot(hh))
    (out.parent / "private_rules.example.json").write_bytes(json_bytes(PRIVATE_RULES_EXAMPLE))
    return [RUN_A, RUN_B, RUN_M, RUN_C]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Write the fake sample runs.")
    ap.add_argument("--out", default=str(ROOT / "samples" / "data"))
    args = ap.parse_args(argv)
    runs = generate(Path(args.out))
    print(f"wrote {len(runs)} runs to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
