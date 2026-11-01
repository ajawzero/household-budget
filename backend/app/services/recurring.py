"""Recurring bills and subscriptions (PLAN 3.7, 3.9, 8.2 Recurring).

The chat owns the list (`recurring`, format 1.1). The app works out:

- Monthly equivalent: amount per occurrence x occurrences per year / 12.
  Occurrences per year: weekly 52, biweekly 26, every_n_weeks 52 / interval_n,
  semi_monthly 24, monthly 12, quarterly 4, semester 2, semi_annual 2,
  annual 1. With `active_months`, weekly to monthly cadences count only those
  months (a monthly bill with 7 active months is 7 a year), semester bills
  count one charge per listed month, and quarterly, semi_annual and annual
  bills count the steps from the anchor date that land in a listed month (the
  same dates the calendar shows). Net = amount minus
  credit_offset. Rounded half up to the cent.
- Due dates: stepped from an anchor date (next_date, else start_date, else
  renewal_date, else the newest paid row). Month cadences keep the anchor's
  day (the 31st becomes the month's last day). Semester bills with
  `active_months` fall in those months. Seasonal `active_months` filter every
  cadence. Paused and ended items have no due dates. An ending item stops
  after `active_through`. start_date and end_date bound the dates.
- Paid: a live row with the item's recurring_key and money out whose date is
  within the cadence's tolerance of a due date (weekly 3 days, biweekly and
  every_n_weeks 5, semi_monthly 4, monthly 7, longer cadences 10). Each row
  pays one due date, the closest first.
- State of a due date: paid; upcoming (due today or later); waiting (past,
  but the paying account's statements do not reach past the window yet);
  no_history (past, the statements cover the window, no row found, and no
  tagged payment can be expected: the item has no tagged payment yet, the
  date is before its oldest one, or the window opens before the payment
  history starts on Jan 1, 2026, as for utilities); missed (the same, for an
  item with payment history).
- Ends on: end_date, or active_through for an ending item, whichever is
  earlier. None for paused and ended items, and once the date has passed.
- Cost cut: the chat's unresolved cost cutting flag tied to the item
  (related_kind recurring, related_key = the item key, not archived, not
  info). Open beats watching, then the newest.
- Section (gap audit C1, C3): the plan group of the item's category (its
  category group), except that transfers and allowances form "Savings and
  transfers", like the old dashboard's Recurring tab. A bill with a tag
  (format 1.4) has its own section right after its plan group,
  `<group>.<tag>` (Streaming, Hobbies, Apps and software). Totals, plan lines
  and the Dashboard still use the plan group. The personal subscription never
  shows a tag.
- By month: the yearly net cost spread evenly over the months it runs
  (active_months, else all 12). A monthly bill with 9 active months is its
  amount in each of them; an annual fee is a twelfth in every month.
- Change (C6): against the recurring list of the monthly run before the
  newest one that has it. New when the key is not there; rate up or down when
  the gross monthly equivalent moved (not for variable bills). Nothing for
  paused and ended items, or with no earlier run. Plan lines (C2) carry the
  change too, with the earlier run's by-month amount for that month, only when
  it moved the same way (E7f).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from app.importer.models import Flag, Recurring, is_personal_subscription, to_cents
from app.services.coverage import (
    HISTORY_START,
    CoverageState,
    coverage_state,
    month_end,
    month_start,
)
from app.services.networth import today
from app.state import readers

PER_YEAR = {
    "weekly": Fraction(52),
    "biweekly": Fraction(26),
    "semi_monthly": Fraction(24),
    "monthly": Fraction(12),
    "quarterly": Fraction(4),
    "semester": Fraction(2),
    "semi_annual": Fraction(2),
    "annual": Fraction(1),
}
DAY_STEPS = {"weekly": 7, "biweekly": 14}
MONTH_STEPS = {"monthly": 1, "quarterly": 3, "semester": 6, "semi_annual": 6, "annual": 12}
FREQUENT = frozenset({"weekly", "biweekly", "every_n_weeks", "semi_monthly", "monthly"})
TOLERANCE_DAYS = {
    "weekly": 3,
    "biweekly": 5,
    "every_n_weeks": 5,
    "semi_monthly": 4,
    "monthly": 7,
}
LONG_TOLERANCE = 10
MATCH_MARGIN_DAYS = 45  # extra days loaded around a month so edge dates pair right
INACTIVE = frozenset({"paused", "ended"})
SAVINGS = "savings"
SAVINGS_LABEL = "Savings and transfers"
SAVINGS_CATEGORIES = frozenset({"allowances"})  # money moved to the kids' card
# Tag sections (format 1.4), in this order right after their plan group. Key
# "<group>.<tag>": a group key never has a dot, so the two cannot clash.
TAG_LABELS = {"streaming": "Streaming", "hobbies": "Hobbies", "software": "Apps and software"}


def _round(value: Fraction) -> int:
    """Round half away from zero to a whole number of cents."""
    n = abs(value)
    whole = int(n)
    if n - whole >= Fraction(1, 2):
        whole += 1
    return whole if value >= 0 else -whole


def occurrences_per_year(item: Recurring) -> Fraction:
    if item.cadence == "every_n_weeks":
        base = Fraction(52, item.interval_n or 1)
    else:
        base = PER_YEAR[item.cadence]
    if item.active_months:
        n = len(item.active_months)
        if item.cadence in FREQUENT:
            return base * n / 12
        if item.cadence == "semester":
            return Fraction(n)
        # active_months filters the stepped dates, as in due_dates().
        anchor = anchor_date(item, None)
        if anchor is None:
            return min(base, Fraction(n))
        step = MONTH_STEPS[item.cadence]
        months = {(anchor.month - 1 + k * step) % 12 + 1 for k in range(12 // step)}
        return Fraction(len(months & set(item.active_months)))
    return base


def per_year_text(value: Fraction) -> str:
    """Like "12", "7" or "17.33"."""
    d = (Decimal(value.numerator) / Decimal(value.denominator)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return str(d.quantize(Decimal(1))) if d == d.to_integral() else str(d)


def monthly_equivalent(item: Recurring) -> tuple[int, int]:
    """(gross, net) monthly equivalent in cents."""
    per = occurrences_per_year(item) / 12
    gross = to_cents(item.amount)
    net = gross - to_cents(item.credit_offset or "0.00")
    return _round(per * gross), _round(per * net)


def tolerance(item: Recurring) -> int:
    return TOLERANCE_DAYS.get(item.cadence, LONG_TOLERANCE)


def anchor_date(item: Recurring, last_paid: str | None) -> date | None:
    for value in (item.next_date, item.start_date, item.renewal_date, last_paid):
        if value:
            return date.fromisoformat(value)
    return None


def _on_day(year: int, month: int, day: int) -> date:
    last = month_end(f"{year:04d}-{month:02d}")
    return last if day >= last.day else date(year, month, day)


def _month_dates(item: Recurring, anchor: date, start: date, end: date) -> list[date]:
    out = []
    m = start.year * 12 + start.month - 1
    stop = end.year * 12 + end.month - 1
    a = anchor.year * 12 + anchor.month - 1
    while m <= stop:
        y, mo = divmod(m, 12)
        mo += 1
        if item.cadence == "semi_monthly":
            d1 = anchor.day if anchor.day <= 15 else anchor.day - 15
            out += [_on_day(y, mo, d1), _on_day(y, mo, d1 + 15)]
        elif item.cadence == "semester" and item.active_months:
            if mo in item.active_months:
                out.append(_on_day(y, mo, anchor.day))
        elif (m - a) % MONTH_STEPS[item.cadence] == 0:
            out.append(_on_day(y, mo, anchor.day))
        m += 1
    return sorted({d for d in out if start <= d <= end})


def due_dates(item: Recurring, start: date, end: date, last_paid: str | None = None) -> list[date]:
    """Due dates from start to end (both included). See the module rules."""
    if item.status in INACTIVE:
        return []
    anchor = anchor_date(item, last_paid)
    if anchor is None:
        return []
    if item.cadence in DAY_STEPS or item.cadence == "every_n_weeks":
        step = DAY_STEPS.get(item.cadence) or 7 * (item.interval_n or 1)
        k = -((anchor - start).days // step)  # first k with anchor + k*step >= start
        d = anchor + timedelta(days=k * step)
        dates = []
        while d <= end:
            dates.append(d)
            d += timedelta(days=step)
    else:
        dates = _month_dates(item, anchor, start, end)
    out = []
    for d in dates:
        iso = d.isoformat()
        if item.active_months and d.month not in item.active_months:
            continue
        if item.start_date and iso < item.start_date:
            continue
        if item.end_date and iso > item.end_date:
            continue
        if item.status == "ending" and item.active_through and iso > item.active_through:
            continue
        out.append(d)
    return out


def ends_on(item: Recurring, on: date) -> str | None:
    """The last date the item runs, or None. See the module rules."""
    if item.status in INACTIVE:
        return None
    candidates = [item.end_date] if item.end_date else []
    if item.status == "ending" and item.active_through:
        candidates.append(item.active_through)
    if not candidates:
        return None
    end = min(candidates)
    return end if end >= on.isoformat() else None


def renews_on(item: Recurring, on: date) -> str | None:
    """The renewal date when it is still to come and the item runs until then.
    None for a paused or ended item, and for one that ends before it renews."""
    r = item.renewal_date
    if not r or r < on.isoformat() or item.status in INACTIVE:
        return None
    ends = [item.end_date] if item.end_date else []
    if item.status == "ending" and item.active_through:
        ends.append(item.active_through)
    return None if ends and min(ends) < r else r


def cost_cuts(flags: list[Flag]) -> dict[str, Flag]:
    """Item key -> the open or watching cost cutting flag on it. Open first, then newest.
    An info flag (nothing to do) is never one."""
    out: dict[str, Flag] = {}
    for f in flags:
        if f.kind != "cost_cutting" or f.related_kind != "recurring" or not f.related_key:
            continue
        if f.status not in ("open", "watching") or f.archived or f.action == "info":
            continue
        best = out.get(f.related_key)
        rank = (f.status == "open", f.created_on, f.key)
        if best is None or rank > (best.status == "open", best.created_on, best.key):
            out[f.related_key] = f
    return out


def by_month(item: Recurring) -> list[int]:
    """Net cents in each month, January to December. See the module rules."""
    net = to_cents(item.amount) - to_cents(item.credit_offset or "0.00")
    months = set(item.active_months or range(1, 13))
    per = _round(occurrences_per_year(item) * net / len(months))
    return [per if m in months else 0 for m in range(1, 13)]


def runs_in(item: Recurring, month: str) -> bool:
    """Whether the item runs at some point in the month (dates, not season). A paused
    item never does; an ended one only up to its end_date."""
    if item.status == "paused" or (item.status == "ended" and not item.end_date):
        return False
    if item.start_date and item.start_date > month_end(month).isoformat():
        return False
    stops = [item.end_date] if item.end_date else []
    if item.status == "ending" and item.active_through:
        stops.append(item.active_through)
    return not (stops and min(stops) < month_start(month).isoformat())


def changes(items: list[Recurring], before: list[Recurring] | None) -> dict[str, tuple]:
    """Item key -> (change, previous gross monthly equivalent). See the module rules."""
    if before is None:
        return {}
    old = {i.key: monthly_equivalent(i)[0] for i in before}
    out: dict[str, tuple] = {}
    for item in items:
        if item.status in INACTIVE:
            continue
        if item.key not in old:
            out[item.key] = ("new", None)
            continue
        now = monthly_equivalent(item)[0]
        if not item.variable and now != old[item.key]:
            out[item.key] = ("rate_up" if now > old[item.key] else "rate_down", old[item.key])
    return out


# --------------------------------------------------------------------------
# Paid matching
# --------------------------------------------------------------------------


@dataclass
class Due:
    item: Recurring
    date: date
    expected: int
    txn: sqlite3.Row | None = None
    first_paid: str | None = None  # the date of the item's oldest tagged payment


def _rows_for(conn: sqlite3.Connection, keys: list[str], start: date, end: date) -> list:
    if not keys:
        return []
    marks = ", ".join("?" for _ in keys)
    return conn.execute(
        f"""SELECT txn_key, txn_date, amount_cents, account_key, recurring_key
            FROM v_transactions
            WHERE recurring_key IN ({marks}) AND amount_cents < 0 AND hidden = 0
              AND txn_date BETWEEN ? AND ?
            ORDER BY txn_date, txn_key""",
        (*keys, start.isoformat(), end.isoformat()),
    ).fetchall()


def last_paid(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    out = {}
    for r in conn.execute(
        """SELECT recurring_key, txn_date, amount_cents, txn_key, counterparty_account_key
           FROM v_transactions
           WHERE recurring_key IS NOT NULL AND amount_cents < 0 AND hidden = 0
           ORDER BY txn_date, txn_key"""
    ):
        out[r["recurring_key"]] = r
    return out


def first_paid(conn: sqlite3.Connection) -> dict[str, str]:
    """The date of each item's oldest tagged payment."""
    return {
        r[0]: r[1]
        for r in conn.execute(
            """SELECT recurring_key, min(txn_date) FROM v_transactions
               WHERE recurring_key IS NOT NULL AND amount_cents < 0 AND hidden = 0
               GROUP BY recurring_key"""
        )
    }


def match(conn: sqlite3.Connection, items: list[Recurring], start: date, end: date) -> list[Due]:
    """Due dates from start to end, each paired with the row that paid it, if any."""
    lo = start - timedelta(days=MATCH_MARGIN_DAYS)
    hi = end + timedelta(days=MATCH_MARGIN_DAYS)
    paid = last_paid(conn)
    first = first_paid(conn)
    rows = _rows_for(conn, [i.key for i in items], lo, hi)
    by_key: dict[str, list] = {}
    for r in rows:
        by_key.setdefault(r["recurring_key"], []).append(r)
    out: list[Due] = []
    for item in items:
        last = paid[item.key]["txn_date"] if item.key in paid else None
        dues = [
            Due(item, d, to_cents(item.amount), first_paid=first.get(item.key))
            for d in due_dates(item, lo, hi, last)
        ]
        tol = tolerance(item)
        pairs = []
        for i, due in enumerate(dues):
            for r in by_key.get(item.key, []):
                gap = abs((date.fromisoformat(r["txn_date"]) - due.date).days)
                if gap <= tol:
                    pairs.append((gap, due.date, r["txn_date"], i, r))
        used_rows: set[str] = set()
        for _gap, _, _, i, r in sorted(pairs, key=lambda p: p[:3]):
            if dues[i].txn is None and r["txn_key"] not in used_rows:
                dues[i].txn = r
                used_rows.add(r["txn_key"])
        out += [d for d in dues if start <= d.date <= end]
    out.sort(key=lambda d: (d.date, d.item.name.casefold(), d.item.key))
    return out


def before_history(due: Due) -> bool:
    """True when no tagged payment can be expected for this due date (see the module notes)."""
    if due.date - timedelta(days=tolerance(due.item)) < month_start(HISTORY_START):
        return True
    return due.first_paid is None or due.date.isoformat() < due.first_paid


def due_state(due: Due, on: date, cov: CoverageState) -> str:
    if due.txn is not None:
        return "paid"
    if due.date >= on:
        return "upcoming"
    window_end = (due.date + timedelta(days=tolerance(due.item))).isoformat()
    through = cov.covered_through(due.item.account_key) if due.item.account_key else None
    if through is not None and through >= window_end:
        return "no_history" if before_history(due) else "missed"
    return "waiting"


# --------------------------------------------------------------------------
# Payloads
# --------------------------------------------------------------------------


@dataclass
class Ctx:
    categories: dict[str, sqlite3.Row]
    accounts: dict[str, str]
    people: dict[str, str]
    groups: dict[str, sqlite3.Row]


def _ctx(conn: sqlite3.Connection) -> Ctx:
    return Ctx(
        {r["category_key"]: r for r in conn.execute("SELECT * FROM categories")},
        {r["account_key"]: r["display_name"] for r in conn.execute("SELECT * FROM accounts")},
        {r["person_key"]: r["display_name"] for r in conn.execute("SELECT * FROM people")},
        {r["group_key"]: r for r in conn.execute("SELECT * FROM category_groups")},
    )


def group_key(item: Recurring, ctx: Ctx) -> str | None:
    """The plan group: the category's group, or saved-invested for a transfer."""
    cat = ctx.categories.get(item.category_key)
    if cat is None:
        return None
    return "saved-invested" if cat["kind"] == "transfer" else cat["group_key"]


def shown_tag(item: Recurring) -> str | None:
    """The bill's tag. Never one on the personal subscription (format 1.4 section 3.2):
    import rejects it, and a stored run is not checked again."""
    return None if is_personal_subscription(item.name) else item.tag


def section_key(item: Recurring, ctx: Ctx) -> str:
    """The plan group, `<group>.<tag>` for a tagged bill, or savings. Savings items
    never get a tag section: the savings check runs first."""
    group = group_key(item, ctx)
    if group == "saved-invested" or item.category_key in SAVINGS_CATEGORIES:
        return SAVINGS
    tag = shown_tag(item)
    base = group or "other"
    return f"{base}.{tag}" if tag else base


def _sections(keys: set[str], ctx: Ctx) -> list[dict]:
    """Bills sections in category group order, each followed by its tag sections
    (TAG_LABELS order), then savings. A tag section keeps its group's place even
    when the group has no untagged bill."""

    def name(group: str) -> str:
        return ctx.groups[group]["name"] if group in ctx.groups else "Other"

    def place(group: str) -> tuple[int, str]:
        return (ctx.groups[group]["sort"] if group in ctx.groups else 10_000, group)

    def section(key: str, label: str, kind: str, group: str | None = None) -> dict:
        """A tag section names the plan group it follows. The others have no group."""
        group_label = name(group) if group else None
        return dict(key=key, label=label, kind=kind, group_key=group, group_label=group_label)

    out: list[dict] = []
    for g in sorted({k.split(".", 1)[0] for k in keys if k != SAVINGS}, key=place):
        if g in keys:
            out.append(section(g, name(g), "bills"))
        out += [
            section(f"{g}.{tag}", label, "tag", g)
            for tag, label in TAG_LABELS.items()
            if f"{g}.{tag}" in keys
        ]
    if SAVINGS in keys:
        out.append(section(SAVINGS, SAVINGS_LABEL, "savings"))
    return out


def _base(item: Recurring, ctx: Ctx) -> dict:
    cat = ctx.categories.get(item.category_key)
    return {
        "key": item.key,
        "name": item.name,
        "category_key": item.category_key,
        "category_name": cat["name"] if cat else None,
        "category_emoji": cat["emoji"] if cat else None,
        "account_key": item.account_key,
        "account_name": ctx.accounts.get(item.account_key) if item.account_key else None,
        "cadence": item.cadence,
        "status": item.status,
        "variable": bool(item.variable),
    }


def due_out(due: Due, on: date, cov: CoverageState, ctx: Ctx) -> dict:
    credit = to_cents(due.item.credit_offset or "0.00")
    return {
        **_base(due.item, ctx),
        "due_date": due.date.isoformat(),
        "expected_cents": due.expected,
        "credit_offset_cents": credit or None,
        "state": due_state(due, on, cov),
        "paid_cents": -due.txn["amount_cents"] if due.txn is not None else None,
        "paid_date": due.txn["txn_date"] if due.txn is not None else None,
        "txn_key": due.txn["txn_key"] if due.txn is not None else None,
    }


def month_view(conn: sqlite3.Connection, month: str) -> dict:
    runs = readers.monthly_runs(conn)
    items = readers.recurring(runs)
    ctx = _ctx(conn)
    cov = coverage_state(conn)
    on = today()
    found = match(conn, items, month_start(month), month_end(month))
    dues = [due_out(d, on, cov, ctx) for d in found]
    upcoming = [d for d in dues if d["state"] != "paid"]
    complete = [d for d in dues if d["state"] == "paid"]
    return {
        "view": "monthly",
        "month": month,
        "source_run_id": readers.section_source(runs, "recurring"),
        "summary": {
            "expected_cents": sum(d["expected_cents"] for d in dues),
            "paid_cents": sum(d["paid_cents"] or 0 for d in complete),
            "remaining_cents": sum(d["expected_cents"] for d in upcoming),
            "count": len(dues),
            "paid_count": len(complete),
        },
        "upcoming": upcoming,
        "complete": complete,
        "items": [],
        "totals": None,
    }


def all_view(conn: sqlite3.Connection, month: str) -> dict:
    runs = readers.monthly_runs(conn)
    items = readers.recurring(runs)
    ctx = _ctx(conn)
    on = today()
    paid = last_paid(conn)
    cuts = cost_cuts(readers.flags(runs))
    moved = changes(items, readers.previous_recurring(runs))
    out = []
    gross_total = net_total = 0
    count = 0
    for item in sorted(items, key=lambda i: (i.status in INACTIVE, i.name.casefold(), i.key)):
        gross, net = monthly_equivalent(item)
        counted = item.status not in INACTIVE
        section = section_key(item, ctx)
        if counted and section != SAVINGS:
            gross_total += gross
            net_total += net
            count += 1
        last = paid.get(item.key)
        upcoming = due_dates(item, on, on + timedelta(days=400), last["txn_date"] if last else None)
        cut = cuts.get(item.key)
        to_key = last["counterparty_account_key"] if last and section == SAVINGS else None
        change, before = moved.get(item.key, (None, None))
        out.append(
            {
                **_base(item, ctx),
                "amount_cents": to_cents(item.amount),
                "credit_offset_cents": to_cents(item.credit_offset) if item.credit_offset else None,
                "credit_note": item.credit_note,
                "interval_n": item.interval_n,
                "active_months": list(item.active_months) if item.active_months else None,
                "occurrences_per_year": per_year_text(occurrences_per_year(item)),
                "monthly_equivalent_cents": gross,
                "net_monthly_equivalent_cents": net,
                "counted": counted,
                "next_due": upcoming[0].isoformat() if upcoming else None,
                "next_date": item.next_date,
                "start_date": item.start_date,
                "end_date": item.end_date,
                "renewal_date": item.renewal_date,
                "renews_on": renews_on(item, on),
                "ends_on": ends_on(item, on),
                "active_through": item.active_through,
                "review_by": item.review_by,
                "payment_rail": item.payment_rail,
                "preferred_account_key": item.preferred_account_key,
                "preferred_account_name": ctx.accounts.get(item.preferred_account_key)
                if item.preferred_account_key
                else None,
                "owner": item.owner,
                "owner_label": ctx.people.get(item.owner) if item.owner else None,
                "notes": item.notes,
                "keep": item.keep,
                "tag": shown_tag(item),
                "last_paid_date": last["txn_date"] if last else None,
                "last_paid_cents": -last["amount_cents"] if last else None,
                "cost_cut": {
                    "key": cut.key,
                    "title": cut.title,
                    "status": cut.status,
                    "due_date": cut.due_date,
                }
                if cut
                else None,
                "group_key": group_key(item, ctx),
                "section_key": section,
                "by_month_cents": by_month(item),
                "change": change,
                "previous_monthly_equivalent_cents": before,
                "to_account_key": to_key,
                "to_account_name": ctx.accounts.get(to_key) if to_key else None,
            }
        )
    return {
        "view": "all",
        "month": month,
        "source_run_id": readers.section_source(runs, "recurring"),
        "summary": None,
        "upcoming": [],
        "complete": [],
        "items": out,
        "totals": {
            "monthly_equivalent_cents": gross_total,
            "net_monthly_equivalent_cents": net_total,
            "count": count,
        },
        "sections": _sections({i["section_key"] for i in out}, ctx),
    }


def calendar(conn: sqlite3.Connection, month: str) -> dict:
    runs = readers.monthly_runs(conn)
    items = readers.recurring(runs)
    ctx = _ctx(conn)
    cov = coverage_state(conn)
    on = today()
    days: dict[str, list[dict]] = {}
    for d in match(conn, items, month_start(month), month_end(month)):
        days.setdefault(d.date.isoformat(), []).append(due_out(d, on, cov, ctx))
    return {
        "month": month,
        "today": on.isoformat(),
        "first_weekday": month_start(month).isoweekday() % 7,  # 0 = Sunday
        "days_in_month": month_end(month).day,
        "days": [
            {
                "date": k,
                "items": v,
                "expected_cents": sum(i["expected_cents"] for i in v),
            }
            for k, v in sorted(days.items())
        ],
    }


def plan_lines(conn: sqlite3.Connection, month: str) -> dict[str, list[dict]]:
    """Plan group -> the bills in it for the month (gap audit C2), largest first.
    Each is its by-month amount for that month; bills that do not run that month
    (dates or season) are left out. A transfer counts as saved and invested only
    when its newest paying row is saved in cash flow (or it has no row yet), like
    the plan's saved-invested group; a move between cash flow accounts has none."""
    ctx = _ctx(conn)
    m = int(month[5:7])
    flows = {
        r["recurring_key"]: r["flow"]
        for r in conn.execute(
            """SELECT recurring_key, flow FROM v_cashflow_rows
               WHERE recurring_key IS NOT NULL AND amount_cents < 0 AND hidden = 0
               ORDER BY txn_date, txn_key"""
        )
    }
    runs = readers.monthly_runs(conn)
    items = readers.recurring(runs)
    before = readers.previous_recurring(runs)
    moved = changes(items, before)
    old = {i.key: i for i in before or []}
    out: dict[str, list[dict]] = {}
    for item in items:
        group = group_key(item, ctx)
        cents = by_month(item)[m - 1]
        if group is None or not cents or not runs_in(item, month):
            continue
        if group == "saved-invested" and flows.get(item.key, "saved") != "saved":
            continue
        change = moved.get(item.key, (None, None))[0]
        out.setdefault(group, []).append(
            {
                "key": item.key,
                "name": item.name,
                "cents": cents,
                "change": change,
                "previous_cents": _previous_cents(change, old.get(item.key), m, cents),
            }
        )
    for lines in out.values():
        lines.sort(key=lambda r: (-r["cents"], r["name"].casefold(), r["key"]))
    return out


def _previous_cents(change: str | None, old: Recurring | None, m: int, cents: int) -> int | None:
    """The earlier run's by-month cents for the month, when it moved the way the change says.
    None when that run had nothing in the month (it did not run then)."""
    if old is None or change not in ("rate_up", "rate_down"):
        return None
    was = by_month(old)[m - 1]
    return was if was and (was < cents if change == "rate_up" else was > cents) else None


def upcoming_bills(conn: sqlite3.Connection, start: date, end: date) -> list[dict]:
    """Unpaid due dates from start to end, for the Dashboard."""
    runs = readers.monthly_runs(conn)
    items = readers.recurring(runs)
    ctx = _ctx(conn)
    cov = coverage_state(conn)
    return [due_out(d, start, cov, ctx) for d in match(conn, items, start, end) if d.txn is None]
