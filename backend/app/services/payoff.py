"""Debt payoff projection (gap audit D1, PLAN 8.2 Debt).

Pace: what goes toward a debt's balance in a month.
- From statements, when the debt's newest gap-free run of current statements
  (each starts by the day after the one before ends) has an older statement
  that ends in an earlier month, within three months of the newest: (owed at
  the older closing - owed at the newest closing + interest charged after the
  older closing through the newest) / the months between them, counted as
  days / 30.4375, rounded. Owed is the statement balance (plan balances
  included) when set, else the closing, the same total the balance engine
  uses. Interest per statement is the account's own `interest` rows by posting
  date, hidden rows left out; a statement with no interest rows at all uses
  its own interest field. This is the payments less new charges, so escrow on
  a mortgage and new spending on a card count right. The window is that many
  months through the newest statement's month.
- Else from payments: money in from household accounts (rows whose
  counterparty is the debt, money out, hidden left out, by posting date) over
  up to three months in a row, ending with the latest month before this one
  that every paying account's statements cover in full (each payer only for
  the days it was open; an inactive one with no close date counts as closed at
  its last statement), never before the month after the debt opened, divided
  by the months in that window. None when nothing came in.

Projection: month by month, from the month of the balance (the owed balance's
own date; for a computed loan estimate, its last payment's, or its starting
balance's with none); the first payment is the month after. Each month:
interest = owed x APR / 12, rounded to the cent, then the payment. A promo
rate is 0% through the promo month, then the APR. The promo month is the last
whole month before the earliest promo_expiry or pay_before of an active 0%
promo or deferred interest plan on the account: the date's own month when the
date is the month's last day, else the month before, so a payment in that
month cannot land after it. With no such date, 0% all along. Other rates stay
as they are, and an unknown rate counts as 0%. A variable rate follows its
schedule (account rate_changes, format 1.4), used only while the stored
rate_type is variable and the APR is known. The first month at a new rate is
the effective date's month when the date is the 1st, else the month after.
Only a change whose first month is after the real balance's month counts (that
statement already shows its rate). For a computed loan estimate that is the
month of the balance it starts from, so a change that started by the last
payment it counts applies from the first projected month. Two changes that
start in the same month: the later date wins. Not paid off within 600 months (50 years)
means no payoff at this pace.

Last payment: the payment in the payoff month. Early: the one payment in the
month before that clears the debt (the owed after that month's interest),
when the payoff takes two months or more.

Dates to beat: a promo date that is today or later (by its promo month), and
the chat's payoff_date when it is today or later (by its month). For each: the
level monthly payment, to the cent, that pays the debt off by then, and
whether the pace does (None with no pace). The search for that payment takes
the highest rate in the schedule for its upper bound. When no payment month is
left before it, the whole balance is due and there is no verdict.

Cards paid in full each month (statement or full autopay) get no
projection. The Debt tab's Try a payment runs the same steps in
`features/accounts/debt/projection.ts`; both test the same cases.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from app.importer.models import InstallmentPlan, to_milli_pct
from app.services.coverage import (
    HISTORY_START,
    CoverageState,
    add_months,
    month_end,
    month_index,
    month_of,
    month_start,
)

MAX_MONTHS = 600
PACE_MONTHS = 3
PROMO_KINDS = ("promo_0pct", "deferred_interest")
DAYS_PER_MONTH = 30.4375


# --------------------------------------------------------------------------
# The steps (keep in step with projection.ts)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Terms:
    owed_cents: int
    apr_milli_pct: int | None
    promo: bool
    promo_month: str | None  # the last 0% month of a promo rate
    from_month: str  # the month of the balance; the first payment is the month after
    # (effective_on, apr_milli_pct), oldest first: a variable rate's schedule (format 1.4)
    rate_changes: tuple[tuple[str, int], ...] = ()

    def rate(self, month: str) -> int:
        if self.promo and (self.promo_month is None or month <= self.promo_month):
            return 0
        rate = self.apr_milli_pct or 0
        for first, apr in rate_steps(self.rate_changes):
            if first <= month:
                rate = apr  # in order, so the latest step that has started wins
        return rate


@dataclass(frozen=True)
class Run:
    months: int | None = None
    interest_cents: int | None = None
    last_payment_cents: int | None = None
    early_cents: int | None = None


def month_interest(owed_cents: int, apr_milli_pct: int) -> int:
    """owed x APR / 12, half up to the cent. APR is in thousandths of a percent."""
    return (owed_cents * apr_milli_pct + 600_000) // 1_200_000


def promo_last_month(day: str) -> str:
    """The last 0% month for a promo date: its own month only when the date is the
    month's last day, else the month before."""
    m = month_of(day)
    return m if day == month_end(m).isoformat() else add_months(m, -1)


def rate_change_month(day: str) -> str:
    """The first month at a new rate: the date's own month when it is the 1st, else the
    month after (a payment in that month is charged part at the old rate)."""
    m = month_of(day)
    return m if day[8:] == "01" else add_months(m, 1)


def rate_steps(changes: tuple[tuple[str, int], ...]) -> list[tuple[str, int]]:
    """(first month, APR) for each change, oldest first. The list is already the one that
    counts (scheduled_rates); a step that started by from_month applies from month one."""
    return [(rate_change_month(day), apr) for day, apr in changes]


def scheduled_rates(account: dict | sqlite3.Row, balance_month: str) -> list[dict]:
    """The rate changes that count for a debt, oldest first: [{effective_on, apr_milli_pct}].
    Only while the stored rate_type is variable and the APR is known (a debt that turns
    fixed never keeps an old schedule), and only those after `balance_month`: the real
    balance's month, not a computed estimate's."""
    raw = account["rate_changes_json"]
    if not raw or account["rate_type"] != "variable" or account["apr_milli_pct"] is None:
        return []
    return [
        {"effective_on": rc["effective_on"], "apr_milli_pct": to_milli_pct(rc["apr"])}
        for rc in json.loads(raw)  # the run's own shape: {"apr": "19.99", "effective_on": ...}
        if rate_change_month(rc["effective_on"]) > balance_month
    ]


def simulate(t: Terms, monthly_cents: int, limit: int = MAX_MONTHS) -> Run:
    """Pay `monthly_cents` each month until the debt is gone, up to `limit` months."""
    if t.owed_cents <= 0:
        return Run(0, 0, 0, None)
    if monthly_cents <= 0:
        return Run()
    owed, total, before = t.owed_cents, 0, None
    for n in range(1, limit + 1):
        charge = month_interest(owed, t.rate(add_months(t.from_month, n)))
        owed += charge
        total += charge
        if owed <= monthly_cents:
            return Run(n, total, owed, before)
        before = owed
        owed -= monthly_cents
    return Run()


def monthly_to_finish(t: Terms, by_month: str) -> int:
    """The smallest level monthly payment that pays the debt off by `by_month`."""
    months = max(1, month_index(by_month) - month_index(t.from_month))
    lo = 1
    # Paying the owed plus a month's interest at the highest rate clears it in month one.
    top = max([t.apr_milli_pct or 0, *(apr for _, apr in t.rate_changes)])
    hi = max(1, t.owed_cents + month_interest(t.owed_cents, top))
    while lo < hi:
        mid = (lo + hi) // 2
        if simulate(t, mid, months).months is None:
            lo = mid + 1
        else:
            hi = mid
    return lo


# --------------------------------------------------------------------------
# Pace
# --------------------------------------------------------------------------


def _per_month(total: int, months: int) -> int:
    """total / months, half away from zero."""
    q = (abs(total) * 2 + months) // (months * 2)
    return q if total >= 0 else -q


def _next_day(day: str) -> str:
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def statement_pace(
    statements: list[sqlite3.Row], interest_rows: list[tuple[str, int, int]]
) -> tuple[int, str, str] | None:
    """(pace, from month, through month) from the statements, or None.

    `interest_rows` are (posting day, cents, hidden)."""
    if len(statements) < 2:
        return None
    # The newest gap-free run.
    run = [statements[-1]]
    for s in reversed(statements[:-1]):
        if run[0]["period_start"] > _next_day(s["period_end"]):
            break
        run.insert(0, s)
    newest = run[-1]
    through = month_of(newest["period_end"])
    older = [
        i
        for i, s in enumerate(run[:-1])
        if month_of(s["period_end"]) < through
        and month_index(month_of(s["period_end"])) >= month_index(through) - PACE_MONTHS
    ]
    if not older:
        return None
    old = run[older[0]]
    days = (date.fromisoformat(newest["period_end"]) - date.fromisoformat(old["period_end"])).days
    months = max(1, round(days / DAYS_PER_MONTH))
    interest, prev = 0, old["period_end"]
    for s in run[older[0] + 1 :]:
        rows = [(c, hidden) for day, c, hidden in interest_rows if prev < day <= s["period_end"]]
        if rows:
            interest -= sum(c for c, hidden in rows if not hidden)
        else:
            interest -= s["interest_cents"] or 0
        prev = s["period_end"]
    drop = newest["balance_cents"] - old["balance_cents"]  # negative when owed
    return _per_month(drop + interest, months), add_months(through, 1 - months), through


def _covers(stmts: list[sqlite3.Row], start: str, end: str) -> bool:
    """True when one gap-free run of the statements spans start through end."""
    run_start = run_end = None
    for s in stmts:
        if run_end is None or s["period_start"] > _next_day(run_end):
            run_start, run_end = s["period_start"], s["period_end"]
        else:
            run_end = max(run_end, s["period_end"])
        if run_start <= start and run_end >= end:
            return True
    return False


def _payer_covers(cov: CoverageState, key: str, month: str) -> bool:
    """True when a paying account's statements cover the days of `month` it was open."""
    a = cov.accounts.get(key)
    if a is None or a["tracked_by"] != "statements":
        return True
    stmts = cov.statements.get(key, [])
    start, end = month_start(month).isoformat(), month_end(month).isoformat()
    opened, closed = a["opened_on"], a["closed_on"]
    if (not a["active"] or a["dormant"]) and not closed:
        if not stmts:
            return True
        closed = stmts[-1]["period_end"]
    if (opened and opened > end) or (closed and closed < start):
        return True
    return _covers(stmts, max(start, opened or start), min(end, closed or end))


def payments_window(
    payers: set[str], cov: CoverageState, opened_on: str | None, this_month: str
) -> tuple[str, str] | None:
    """(from, through) months for the payments pace, or None."""
    first = HISTORY_START
    if opened_on:
        first = max(first, add_months(month_of(opened_on), 1))
    months: list[str] = []
    m = add_months(this_month, -1)
    while m >= first and len(months) < PACE_MONTHS:
        if all(_payer_covers(cov, p, m) for p in payers):
            months.append(m)
        elif months:
            break
        m = add_months(m, -1)
    return (months[-1], months[0]) if months else None


# --------------------------------------------------------------------------
# The Debt tab's projections
# --------------------------------------------------------------------------


def date_to_beat(t: Terms, run: Run | None, kind: str, when: str, by: str) -> dict:
    """A date to beat, paid off by month `by`. `run` is None when there is no pace."""
    if by <= t.from_month:
        # No payment month is left before the date: the whole balance is due by then.
        return {
            "kind": kind,
            "date": when,
            "by_month": by,
            "monthly_cents": t.owed_cents,
            "on_track": None,
        }
    on_track = None
    if run is not None:
        on_track = run.months is not None and add_months(t.from_month, run.months) <= by
    return {
        "kind": kind,
        "date": when,
        "by_month": by,
        "monthly_cents": monthly_to_finish(t, by),
        "on_track": on_track,
    }


def projections(
    conn: sqlite3.Connection,
    ledger: list[dict],
    accounts: dict,
    cov: CoverageState,
    plans: list[InstallmentPlan],
    on: date,
    paid_in_full,
    balance_months: dict[str, str],
) -> dict[str, dict | None]:
    """{account_key: projection, or None for a card paid in full}.

    `balance_months` is each debt's balance month (see Projection above)."""
    keys = [d["account_key"] for d in ledger]
    if not keys:
        return {}
    marks = ",".join("?" * len(keys))
    statements: dict[str, list[sqlite3.Row]] = {}
    for r in conn.execute(
        f"""SELECT account_key, period_start, period_end, interest_cents,
                   COALESCE(balance_cents, closing_cents) AS balance_cents
            FROM statements
            WHERE is_current = 1 AND account_key IN ({marks})
            ORDER BY account_key, period_start, period_end""",
        keys,
    ):
        statements.setdefault(r["account_key"], []).append(r)
    interest: dict[str, list[tuple[str, int, int]]] = {}
    for r in conn.execute(
        f"""SELECT account_key, COALESCE(post_date, txn_date) AS day, amount_cents, hidden
            FROM v_transactions
            WHERE txn_type = 'interest' AND account_key IN ({marks})""",
        keys,
    ):
        interest.setdefault(r["account_key"], []).append((r["day"], r["amount_cents"], r["hidden"]))
    paid: dict[str, list[tuple[str, str, int]]] = {}
    for r in conn.execute(
        f"""SELECT counterparty_account_key AS k, account_key,
                   COALESCE(post_date, txn_date) AS day, amount_cents
            FROM v_transactions
            WHERE amount_cents < 0 AND hidden = 0 AND counterparty_account_key IN ({marks})""",
        keys,
    ):
        paid.setdefault(r["k"], []).append((r["account_key"], r["day"], -r["amount_cents"]))
    promo_dates: dict[str, list[str]] = {}
    for p in plans:
        when = p.promo_expiry or p.pay_before
        if p.status == "active" and p.kind in PROMO_KINDS and when:
            promo_dates.setdefault(p.account_key, []).append(str(when))

    this_month = month_of(on)
    out: dict[str, dict | None] = {}
    for d in ledger:
        key = d["account_key"]
        if paid_in_full(d):
            out[key] = None
            continue
        pace = basis = start = through = None
        sp = statement_pace(statements.get(key, []), interest.get(key, []))
        if sp:
            pace, start, through = sp
            basis = "statements"
        else:
            rows = paid.get(key, [])
            window = payments_window(
                {a for a, _, _ in rows}, cov, accounts[key]["opened_on"], this_month
            )
            if window:
                start, through = window
                total = sum(c for _, day, c in rows if start <= month_of(day) <= through)
                months = month_index(through) - month_index(start) + 1
                basis = "payments"
                pace = _per_month(total, months) if total else None
        from_month = balance_months.get(key) or add_months(this_month, -1)
        dates = sorted(promo_dates.get(key, []))
        promo = d["rate_type"] == "promo"
        promo_month = promo_last_month(dates[0]) if promo and dates else None
        t = Terms(
            owed_cents=d["owed_cents"],
            apr_milli_pct=d["apr_milli_pct"],
            promo=promo,
            promo_month=promo_month,
            from_month=from_month,
            rate_changes=tuple((c["effective_on"], c["apr_milli_pct"]) for c in d["rate_changes"]),
        )
        run = simulate(t, pace) if pace is not None else None
        targets = []
        ahead = [x for x in dates if x >= on.isoformat()]
        if ahead:
            targets.append(date_to_beat(t, run, "promo", ahead[0], promo_last_month(ahead[0])))
        if d["payoff_ahead"]:
            when = d["payoff_date"]
            targets.append(date_to_beat(t, run, "payoff_date", when, month_of(when)))
        run = run or Run()
        out[key] = {
            "from_month": from_month,
            "pace_cents": pace,
            "pace_basis": basis,
            "pace_from": start,
            "pace_through": through,
            "promo_until": dates[0] if promo and dates else None,
            "promo_month": promo_month,
            "months": run.months,
            "payoff_month": add_months(from_month, run.months) if run.months else None,
            "interest_to_go_cents": run.interest_cents,
            "last_payment_cents": run.last_payment_cents,
            "early_cents": run.early_cents,
            "targets": targets,
        }
    return out
