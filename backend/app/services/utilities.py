"""Utilities: bills paired with payments, per-day numbers and year over year (PLAN 4.12, 8.2).

Built on the readers in app/services/utility_bills.py. Everything here is
computed on request, never stored.

- Payments: live rows (not hidden) with money out whose `recurring_key` is the
  bill's `utility_key`. A payment pairs with a bill when it falls from
  PAY_EARLY_DAYS before the bill date to PAY_WINDOW_DAYS after it. One payment
  pays one bill. Pairs keep date order (an earlier bill never gets a later
  payment than a later bill). Among those pairings the app picks the one with
  the most payments of exactly the bill total, then the most pairs, then the
  smallest total gap in days. So an exact payment wins first, and a tagged
  payment of another amount pays a bill only when no exact one is left. That
  pair is marked `amount_differs`.
- Bill status: paid, amount_differs, past_due (the due date has passed, and
  the paying account's statements reach it), no_history (unpaid and before
  the payment history, see below), else not_paid. A bill with no due date
  uses the bill date plus PAY_WINDOW_DAYS. The paying account is the bill's
  paid_from, else the recurring item's account, else the account of the
  newest tagged payment. With none known, a bill is never past due.
- Payment history: checking history starts Jan 1, 2026, and payments carry
  the utility's tag only from when the chat started tagging them (rows sent
  before format 1.2 may have none). So an unpaid bill is no_history when its
  payment window opens before Jan 1, 2026, or when it is older than the
  utility's oldest paid bill, or when the utility has no paid bill yet.
- Per day: usage / days. Per degree day: gas usage / heating degree days.
- Cost per unit: charges / usage, all-in and without fixed charges, in
  thousandths of a cent (14.237 cents per kWh = 14_237).
- Year over year: the bill whose period_end is closest to one year before
  this bill's period_end, within YOY_MATCH_DAYS. The change is a percent
  string with one decimal, from exact fractions (per-day changes too).
- Bill before: the same utility's newest bill with an earlier period_end
  that ends within PREV_MATCH_DAYS of this bill's period_start, so a missing
  bill gives no false change. Change per service for usage and per day, same
  unit only.
- Next bill expected: the newest bill date plus the median gap between the
  last MEDIAN_BILLS bill dates. Bills come only with the monthly
  imports, so a passed date alone does not make the bill late. It is late
  when a monthly import dated LATE_AFTER_DAYS or more after the expected date
  still has no newer bill.
- Trailing 12 months: bills whose period_end is after the date one year
  before the newest period_end. Average bill = their total / their count.
- Checks: bills past due with no payment, tagged payments with no bill (only
  between the first bill date and the newest bill date, so a payment for a
  bill not imported yet waits), and the import warnings as they stand now.

Amounts are integer cents. Usage is integer micro-units of the line's unit.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from app.services.coverage import HISTORY_START, CoverageState, coverage_state, month_start
from app.services.networth import today
from app.services.utility_bills import UtilityBill, bill_warnings, list_bills
from app.state import readers

PAY_WINDOW_DAYS = 40
PAY_EARLY_DAYS = 5
YOY_MATCH_DAYS = 20
PREV_MATCH_DAYS = 20
MEDIAN_BILLS = 12
LATE_AFTER_DAYS = 7
HEATING_SERVICES = frozenset({"gas"})
STATUSES = ("paid", "amount_differs", "past_due", "not_paid", "no_history")
WARNING_TEXT = {
    "utility-total-rounding": "The line charges plus the credits differ from the total by "
    "1 or 2 cents.",
    "utility-days": "The days on the bill do not match the service period.",
    "utility-overlap": "The service period overlaps the bill before it.",
}


# --------------------------------------------------------------------------
# Small math helpers
# --------------------------------------------------------------------------


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _round(value: Fraction) -> int:
    """Round half away from zero to a whole number."""
    n = abs(value)
    whole = int(n)
    if n - whole >= Fraction(1, 2):
        whole += 1
    return whole if value >= 0 else -whole


def change_pct(new: Fraction | int | None, old: Fraction | int | None) -> str | None:
    """(new - old) / |old| as a percent string with one decimal. None when old is 0 or missing."""
    if new is None or old is None or old == 0:
        return None
    v = (Fraction(new) - Fraction(old)) * 100 / abs(Fraction(old))
    d = Decimal(v.numerator) / Decimal(v.denominator)
    return str(d.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def per_day(usage_micro: int, days: int) -> Fraction | None:
    return Fraction(usage_micro, days) if days > 0 else None


def per_degree_day(usage_micro: int, hdd: int | None) -> Fraction | None:
    return Fraction(usage_micro, hdd) if hdd else None


def unit_cost_millicents(cents: int | None, usage_micro: int, unit: str) -> int | None:
    """Cost per unit in thousandths of a cent. None with no usage or unit none."""
    if cents is None or usage_micro <= 0 or unit == "none":
        return None
    return _round(Fraction(cents * 1000 * 1_000_000, usage_micro))


def _int(v: Fraction | None) -> int | None:
    return None if v is None else _round(v)


def year_before(d: date) -> date:
    """The same day one year earlier. Feb 29 becomes Feb 28."""
    try:
        return d.replace(year=d.year - 1)
    except ValueError:
        return d.replace(year=d.year - 1, day=28)


# --------------------------------------------------------------------------
# Payments and pairing
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Payment:
    txn_key: str
    txn_date: str
    amount_cents: int  # positive: money paid
    account_key: str
    utility_key: str


@dataclass(frozen=True)
class Paid:
    payment: Payment
    amount_differs: bool
    gap_days: int  # payment date - bill date


def load_payments(conn: sqlite3.Connection, keys: list[str]) -> list[Payment]:
    if not keys:
        return []
    marks = ", ".join("?" for _ in keys)
    return [
        Payment(
            r["txn_key"], r["txn_date"], -r["amount_cents"], r["account_key"], r["recurring_key"]
        )
        for r in conn.execute(
            f"""SELECT txn_key, txn_date, amount_cents, account_key, recurring_key
                FROM v_transactions
                WHERE recurring_key IN ({marks}) AND amount_cents < 0 AND hidden = 0
                ORDER BY txn_date, txn_key""",
            keys,
        )
    ]


def pay_gap(bill: UtilityBill, payment: Payment) -> int | None:
    """Days from the bill date to the payment, or None outside the window."""
    gap = (_d(payment.txn_date) - _d(bill.bill_date)).days
    return gap if -PAY_EARLY_DAYS <= gap <= PAY_WINDOW_DAYS else None


def pair_payments(bills: list[UtilityBill], payments: list[Payment]) -> dict[str, Paid]:
    """bill_key -> Paid for one utility's bills and payments (see the module notes)."""
    bs = sorted(bills, key=lambda b: (b.bill_date, b.period_end))
    ps = sorted(payments, key=lambda p: (p.txn_date, p.txn_key))
    n, m = len(bs), len(ps)
    # best[i][j]: (exact pairs, pairs, -total gap) using bills i.. and payments j..
    best = [[(0, 0, 0)] * (m + 1) for _ in range(n + 1)]

    def with_pair(i: int, j: int) -> tuple[int, int, int] | None:
        gap = pay_gap(bs[i], ps[j])
        if gap is None:
            return None
        nxt = best[i + 1][j + 1]
        exact = int(ps[j].amount_cents == bs[i].total_cents)
        return (nxt[0] + exact, nxt[1] + 1, nxt[2] - abs(gap))

    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            options = [best[i + 1][j], best[i][j + 1]]
            pair = with_pair(i, j)
            if pair is not None:
                options.append(pair)
            best[i][j] = max(options)
    out: dict[str, Paid] = {}
    i = j = 0
    while i < n and j < m:
        pair = with_pair(i, j)
        if pair is not None and pair == best[i][j]:
            p = ps[j]
            out[bs[i].bill_key] = Paid(
                p, p.amount_cents != bs[i].total_cents, (_d(p.txn_date) - _d(bs[i].bill_date)).days
            )
            i, j = i + 1, j + 1
        elif best[i + 1][j] == best[i][j]:
            i += 1
        else:
            j += 1
    return out


# --------------------------------------------------------------------------
# Loading one or all utilities
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PayHistory:
    """What one utility's tagged payments say about its payment history."""

    first_paid_bill: str | None  # bill_date of the oldest bill with a payment
    last_account: str | None  # account of the newest tagged payment


@dataclass
class Utility:
    key: str
    bills: list[UtilityBill]  # by period_end
    payments: list[Payment]
    paid: dict[str, Paid]

    @property
    def provider(self) -> str:
        return self.bills[-1].provider

    @property
    def history(self) -> PayHistory:
        paid = [b.bill_date for b in self.bills if b.bill_key in self.paid]
        newest = max(self.payments, key=lambda p: (p.txn_date, p.txn_key), default=None)
        return PayHistory(min(paid, default=None), newest.account_key if newest else None)


@dataclass
class Ctx:
    on: date
    coverage: CoverageState
    accounts: dict[str, str]  # account_key -> display name
    recurring_accounts: dict[str, str]  # recurring key -> paying account key
    last_import: str | None = None  # run date of the newest imported monthly run


def load(conn: sqlite3.Connection, utility_key: str | None = None) -> list[Utility]:
    by_key: dict[str, list[UtilityBill]] = {}
    for b in list_bills(conn, utility_key):
        by_key.setdefault(b.utility_key, []).append(b)
    payments = load_payments(conn, sorted(by_key))
    out = []
    for key, bills in sorted(by_key.items()):
        mine = [p for p in payments if p.utility_key == key]
        out.append(Utility(key, bills, mine, pair_payments(bills, mine)))
    return out


def context(conn: sqlite3.Connection) -> Ctx:
    items = readers.recurring(readers.monthly_runs(conn))
    return Ctx(
        on=today(),
        coverage=coverage_state(conn),
        accounts={
            r["account_key"]: r["display_name"]
            for r in conn.execute("SELECT account_key, display_name FROM accounts")
        },
        recurring_accounts={i.key: i.account_key for i in items if i.account_key},
        last_import=conn.execute(
            "SELECT max(run_date) FROM import_runs WHERE status = 'imported' AND mode = 'monthly'"
        ).fetchone()[0],
    )


def effective_due(bill: UtilityBill) -> str:
    if bill.due_date:
        return bill.due_date
    return (_d(bill.bill_date) + timedelta(days=PAY_WINDOW_DAYS)).isoformat()


def paying_account(bill: UtilityBill, ctx: Ctx, hist: PayHistory) -> str | None:
    return bill.paid_from or ctx.recurring_accounts.get(bill.utility_key) or hist.last_account


def before_history(bill: UtilityBill, hist: PayHistory) -> bool:
    """True when no tagged payment can be expected for this bill (see the module notes)."""
    opens = (_d(bill.bill_date) - timedelta(days=PAY_EARLY_DAYS)).isoformat()
    if opens < month_start(HISTORY_START).isoformat():
        return True
    return hist.first_paid_bill is None or bill.bill_date < hist.first_paid_bill


def bill_status(bill: UtilityBill, paid: Paid | None, ctx: Ctx, hist: PayHistory) -> str:
    if paid is not None:
        return "amount_differs" if paid.amount_differs else "paid"
    if before_history(bill, hist):
        return "no_history"
    due = effective_due(bill)
    if due < ctx.on.isoformat():
        account = paying_account(bill, ctx, hist)
        through = ctx.coverage.covered_through(account) if account else None
        if through is not None and through >= due:
            return "past_due"
    return "not_paid"


# --------------------------------------------------------------------------
# Summaries
# --------------------------------------------------------------------------


def next_bill_date(bills: list[UtilityBill]) -> str | None:
    dates = sorted(_d(b.bill_date) for b in bills)[-MEDIAN_BILLS:]
    gaps = sorted((b - a).days for a, b in zip(dates, dates[1:], strict=False))
    if not gaps:
        return None
    k = len(gaps) // 2
    median = Fraction(gaps[k]) if len(gaps) % 2 else Fraction(gaps[k - 1] + gaps[k], 2)
    return (dates[-1] + timedelta(days=_round(median))).isoformat()


def next_bill_late(next_date: str | None, last_import: str | None) -> bool:
    """True when a monthly import came LATE_AFTER_DAYS or more after the expected date."""
    if next_date is None or last_import is None:
        return False
    return _d(last_import) >= _d(next_date) + timedelta(days=LATE_AFTER_DAYS)


def trailing_year(bills: list[UtilityBill]) -> list[UtilityBill]:
    if not bills:
        return []
    newest = max(b.period_end for b in bills)
    start = year_before(_d(newest)).isoformat()
    return [b for b in bills if b.period_end > start]


def services_of(bills: list[UtilityBill]) -> list[dict]:
    """Each service seen, with the unit on its newest line, sorted by service."""
    units: dict[str, str] = {}
    for b in sorted(bills, key=lambda b: b.period_end):
        for ln in b.lines:
            units[ln.service] = ln.unit
    return [{"service": s, "unit": u} for s, u in sorted(units.items())]


def summary(u: Utility, ctx: Ctx) -> dict:
    year = trailing_year(u.bills)
    total = sum(b.total_cents for b in year)
    last = u.bills[-1]
    expected = next_bill_date(u.bills)
    return {
        "utility_key": u.key,
        "provider": u.provider,
        "bill_count": len(u.bills),
        "first_period_end": u.bills[0].period_end,
        "last_period_end": last.period_end,
        "last_bill_date": max(b.bill_date for b in u.bills),
        "last_total_cents": last.total_cents,
        "last_status": bill_status(last, u.paid.get(last.bill_key), ctx, u.history),
        "services": services_of(u.bills),
        "next_bill_date": expected,
        "next_bill_late": next_bill_late(expected, ctx.last_import),
        "last_import_date": ctx.last_import,
        "avg_bill_cents": _round(Fraction(total, len(year))) if year else None,
        "bills_in_avg": len(year),
        "total_12m_cents": total,
    }


def summaries(conn: sqlite3.Connection) -> list[dict]:
    """One summary per utility, by utility_key. Used by GET /api/utilities and the Dashboard."""
    utils = load(conn)
    if not utils:
        return []
    ctx = context(conn)
    return [summary(u, ctx) for u in utils]


# --------------------------------------------------------------------------
# Year over year
# --------------------------------------------------------------------------


def last_year_bill(bill: UtilityBill, bills: list[UtilityBill]) -> UtilityBill | None:
    """The same utility's bill whose period_end is closest to one year earlier (20 days)."""
    target = year_before(_d(bill.period_end))
    best: tuple[int, str, UtilityBill] | None = None
    for b in bills:
        if b.utility_key != bill.utility_key or b.bill_key == bill.bill_key:
            continue
        gap = abs((_d(b.period_end) - target).days)
        if gap <= YOY_MATCH_DAYS and (best is None or (gap, b.period_end) < best[:2]):
            best = (gap, b.period_end, b)
    return best[2] if best else None


def previous_bill(bill: UtilityBill, bills: list[UtilityBill]) -> UtilityBill | None:
    """The same utility's newest bill that ends before this one, if it ends
    within PREV_MATCH_DAYS of this bill's period_start (so a gap gives none)."""
    earlier = [
        b for b in bills if b.utility_key == bill.utility_key and b.period_end < bill.period_end
    ]
    if not earlier:
        return None
    prev = max(earlier, key=lambda b: (b.period_end, b.bill_key))
    gap = abs((_d(prev.period_end) - _d(bill.period_start)).days)
    return prev if gap <= PREV_MATCH_DAYS else None


def _pv(value: int | Fraction | None, prev: int | Fraction | None) -> dict:
    """The bill before's value and the change. The change uses the exact fractions."""
    return {
        "previous": _int(prev) if isinstance(prev, Fraction) else prev,
        "change_pct": change_pct(value, prev),
    }


def _yv(value: int | Fraction | None, last: int | Fraction | None) -> dict:
    """A value, last year's value and the change. Fractions round to whole
    numbers for display, and the change uses the exact fractions."""
    return {
        "value": _int(value) if isinstance(value, Fraction) else value,
        "last_year": _int(last) if isinstance(last, Fraction) else last,
        "change_pct": change_pct(value, last),
    }


def yoy_row(bill: UtilityBill, last: UtilityBill | None, prev: UtilityBill | None = None) -> dict:
    services = []
    for ln in bill.lines:
        same = last.line(ln.service) if last else None
        # Usage in another unit (ccf vs gallons) is not comparable. Charges still are.
        old = same if same is not None and same.unit == ln.unit else None
        pd = per_day(ln.usage_micro, bill.days)
        pd_old = per_day(old.usage_micro, last.days) if old and last else None
        same_p = prev.line(ln.service) if prev else None
        p_old = same_p if same_p is not None and same_p.unit == ln.unit else None
        pd_prev = per_day(p_old.usage_micro, prev.days) if p_old and prev else None
        heat = ln.service in HEATING_SERVICES
        pdd = per_degree_day(ln.usage_micro, bill.heating_degree_days) if heat else None
        pdd_old = (
            per_degree_day(old.usage_micro, last.heating_degree_days)
            if heat and old and last
            else None
        )
        services.append(
            {
                "service": ln.service,
                "unit": ln.unit,
                "usage_micro": _yv(ln.usage_micro, old.usage_micro if old else None),
                "per_day_micro": _yv(pd, pd_old),
                "per_degree_day_micro": _yv(pdd, pdd_old) if heat else None,
                "charges_cents": _yv(ln.charges_cents, same.charges_cents if same else None),
                "usage_vs_prev": _pv(ln.usage_micro, p_old.usage_micro if p_old else None),
                "per_day_vs_prev": _pv(pd, pd_prev),
            }
        )
    return {
        "bill_key": bill.bill_key,
        "period_start": bill.period_start,
        "period_end": bill.period_end,
        "last_year_bill_key": last.bill_key if last else None,
        "last_year_period_start": last.period_start if last else None,
        "last_year_period_end": last.period_end if last else None,
        "prev_bill_key": prev.bill_key if prev else None,
        "prev_period_start": prev.period_start if prev else None,
        "prev_period_end": prev.period_end if prev else None,
        "days": _yv(bill.days, last.days if last else None),
        "heating_degree_days": _yv(
            bill.heating_degree_days, last.heating_degree_days if last else None
        ),
        "total_cents": _yv(bill.total_cents, last.total_cents if last else None),
        "total_per_day_cents": _yv(
            per_day(bill.total_cents, bill.days),
            per_day(last.total_cents, last.days) if last else None,
        ),
        "services": services,
    }


# --------------------------------------------------------------------------
# GET /api/utilities/{key}
# --------------------------------------------------------------------------


def _in_range(b: UtilityBill, start: str | None, end: str | None) -> bool:
    month = b.period_end[:7]
    return (start is None or month >= start) and (end is None or month <= end)


def bill_out(b: UtilityBill, paid: Paid | None, ctx: Ctx, hist: PayHistory) -> dict:
    payment = None
    if paid is not None:
        p = paid.payment
        payment = {
            "txn_key": p.txn_key,
            "txn_date": p.txn_date,
            "amount_cents": p.amount_cents,
            "account_key": p.account_key,
            "account_name": ctx.accounts.get(p.account_key, p.account_key),
            "amount_differs": paid.amount_differs,
            "days_after_bill": paid.gap_days,
        }
    return {
        "bill_key": b.bill_key,
        "provider": b.provider,
        "bill_date": b.bill_date,
        "period_start": b.period_start,
        "period_end": b.period_end,
        "days": b.days,
        "total_cents": b.total_cents,
        "due_date": b.due_date,
        "due_by": effective_due(b),
        "paid_from": b.paid_from,
        "paid_from_name": ctx.accounts.get(b.paid_from) if b.paid_from else None,
        "heating_degree_days": b.heating_degree_days,
        "estimated_read": b.estimated_read,
        "note": b.note,
        "lines": [
            {
                "service": ln.service,
                "unit": ln.unit,
                "usage_micro": ln.usage_micro,
                "charges_cents": ln.charges_cents,
                "fixed_charges_cents": ln.fixed_charges_cents,
            }
            for ln in b.lines
        ],
        "credits": [{"label": c.label, "amount_cents": c.amount_cents} for c in b.credits],
        "charges_cents": b.charges_cents,
        "credits_cents": b.credits_cents,
        "status": bill_status(b, paid, ctx, hist),
        "payment": payment,
    }


def cost_point(b: UtilityBill) -> dict:
    return {
        "bill_key": b.bill_key,
        "period_end": b.period_end,
        "services": [
            {
                "service": ln.service,
                "unit": ln.unit,
                "usage_micro": ln.usage_micro,
                "charges_cents": ln.charges_cents,
                "fixed_charges_cents": ln.fixed_charges_cents,
                "cost_per_unit_millicents": unit_cost_millicents(
                    ln.charges_cents, ln.usage_micro, ln.unit
                ),
                "cost_per_unit_ex_fixed_millicents": unit_cost_millicents(
                    None
                    if ln.fixed_charges_cents is None
                    else ln.charges_cents - ln.fixed_charges_cents,
                    ln.usage_micro,
                    ln.unit,
                ),
            }
            for ln in b.lines
        ],
    }


def credits_running(bills: list[UtilityBill]) -> list[dict]:
    out = []
    running = 0
    for b in bills:
        for c in b.credits:
            running += c.amount_cents
            out.append(
                {
                    "bill_key": b.bill_key,
                    "bill_date": b.bill_date,
                    "period_end": b.period_end,
                    "label": c.label,
                    "amount_cents": c.amount_cents,
                    "running_cents": running,
                }
            )
    return out


def warning_out(u: Utility, bill_key: str, rule: str) -> dict:
    bill = next(b for b in u.bills if b.bill_key == bill_key)
    return {
        "utility_key": u.key,
        "provider": u.provider,
        "bill_key": bill_key,
        "period_end": bill.period_end,
        "rule": rule,
        "message": WARNING_TEXT[rule],
    }


def detail(
    conn: sqlite3.Connection, utility_key: str, start: str | None = None, end: str | None = None
) -> dict | None:
    """The Utilities view for one utility, bills whose period_end month is in [start, end]."""
    found = load(conn, utility_key)
    if not found:
        return None
    u = found[0]
    ctx = context(conn)
    shown = [b for b in u.bills if _in_range(b, start, end)]
    keys = {b.bill_key for b in shown}
    hist = u.history
    return {
        "utility_key": u.key,
        "provider": u.provider,
        "from": start,
        "to": end,
        "summary": summary(u, ctx),
        "bills": [bill_out(b, u.paid.get(b.bill_key), ctx, hist) for b in shown],
        "yoy": [yoy_row(b, last_year_bill(b, u.bills), previous_bill(b, u.bills)) for b in shown],
        "cost": [cost_point(b) for b in shown],
        "credits": credits_running(shown),
        "credits_total_cents": sum(b.credits_cents for b in shown),
        "warnings": [
            warning_out(u, w.bill_key, w.rule) for w in bill_warnings(u.bills) if w.bill_key in keys
        ],
    }


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------


def checks(conn: sqlite3.Connection) -> dict:
    """The three Utilities lists on the Checks page."""
    utils = load(conn)
    out: dict = {"past_due": [], "payments_no_bill": [], "warnings": [], "bill_count": 0}
    if not utils:
        return out
    out["bill_count"] = sum(len(u.bills) for u in utils)
    ctx = context(conn)
    for u in utils:
        hist = u.history
        for b in u.bills:
            if bill_status(b, u.paid.get(b.bill_key), ctx, hist) == "past_due":
                account = paying_account(b, ctx, hist)
                out["past_due"].append(
                    {
                        "utility_key": u.key,
                        "provider": u.provider,
                        "bill_key": b.bill_key,
                        "bill_date": b.bill_date,
                        "period_end": b.period_end,
                        "due_date": effective_due(b),
                        "total_cents": b.total_cents,
                        "account_name": ctx.accounts.get(account) if account else None,
                    }
                )
        used = {p.payment.txn_key for p in u.paid.values()}
        first = (_d(min(b.bill_date for b in u.bills)) - timedelta(days=PAY_EARLY_DAYS)).isoformat()
        newest = max(b.bill_date for b in u.bills)
        for p in u.payments:
            if p.txn_key in used or not first <= p.txn_date <= newest:
                continue
            out["payments_no_bill"].append(
                {
                    "utility_key": u.key,
                    "provider": u.provider,
                    "txn_key": p.txn_key,
                    "txn_date": p.txn_date,
                    "account_key": p.account_key,
                    "account_name": ctx.accounts.get(p.account_key, p.account_key),
                    "amount_cents": p.amount_cents,
                }
            )
        out["warnings"] += [warning_out(u, w.bill_key, w.rule) for w in bill_warnings(u.bills)]
    out["past_due"].sort(key=lambda r: (r["due_date"], r["utility_key"]))
    out["payments_no_bill"].sort(key=lambda r: (r["txn_date"], r["utility_key"], r["txn_key"]))
    return out
