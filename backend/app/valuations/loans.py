"""Loan balance estimate between real balances (PLAN 3.9, 6.3).

From the last real balance (owed L0 on d0), the APR and the payments from
household accounts whose counterparty is the loan, dated after d0:

    for each payment p on day t, in date order:
        L = L * (1 + APR / 365 * days since the last step) - p, floored at 0
    then add the interest from the last payment to the day.

Simple daily interest, rounded to the cent at each step. With no APR there is
no estimate: the last real balance shows, with "Set the APR to estimate."
The app never guesses an APR.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date

NO_APR_NOTE = "No APR on file, so this is the last real balance. Set the APR to estimate."


@dataclass(frozen=True)
class Payment:
    day: str
    cents: int  # positive: money paid toward the loan


@dataclass(frozen=True)
class LoanEstimate:
    owed_cents: int  # positive
    payments: int
    paid_cents: int
    interest_cents: int
    last_payment: str | None


def _interest(owed: int, apr_milli_pct: int, days: int) -> int:
    if owed <= 0 or days <= 0:
        return 0
    return round(owed * apr_milli_pct * days / (100_000 * 365))


def amortize(
    owed0: int, d0: date, day: date, apr_milli_pct: int, payments: list[Payment]
) -> LoanEstimate:
    """The amount owed on `day`. Only payments after d0 and on or before `day` count."""
    owed, last = owed0, d0
    paid = interest = n = 0
    last_payment = None
    for p in sorted(payments, key=lambda x: x.day):
        t = date.fromisoformat(p.day)
        if t <= d0 or t > day:
            continue
        i = _interest(owed, apr_milli_pct, (t - last).days)
        interest += i
        owed = max(owed + i - p.cents, 0)
        paid += p.cents
        n += 1
        last, last_payment = t, p.day
    i = _interest(owed, apr_milli_pct, (day - last).days)
    return LoanEstimate(owed + i, n, paid, interest + i, last_payment)


def load_payments(conn: sqlite3.Connection, loan_keys: list[str]) -> dict[str, list[Payment]]:
    """Money out of other household accounts with the loan as counterparty.

    Live rows only. The loan's own statement rows are left out (they are in
    its statement balances already).
    """
    if not loan_keys:
        return {}
    marks = ", ".join("?" for _ in loan_keys)
    out: dict[str, list[Payment]] = {}
    for r in conn.execute(
        f"""SELECT counterparty_account_key AS loan, txn_date, amount_cents
            FROM transactions
            WHERE removed_at IS NULL AND amount_cents < 0
              AND counterparty_account_key IN ({marks})
              AND account_key <> counterparty_account_key
            ORDER BY txn_date""",
        loan_keys,
    ):
        out.setdefault(r["loan"], []).append(Payment(r["txn_date"], -r["amount_cents"]))
    return out
