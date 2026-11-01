"""GET /api/rewards (docs/plans/phase-7.md section 4): the Rewards page, plus
the Dashboard line and the Checks block.

The chat owns the data (`rewards`, format 1.3; the newest monthly run wins
whole). the owner's app inputs lie on top (`rewards_overlay.effective_rewards`):
the page, Fees, the Dashboard line and Checks read that effective view. The
app works out, on household today:

- Cycle state of a credit, from dates and amounts: done (used reaches the
  cycle's amount), expired (past `end` and not done), partial (some used) or
  open. Expired is shown in gray: a past deadline never shows red again.
- A cycle's used comes from one source (the overlay, plan 3.3): a mark set in
  the app, the statement credits matched to it, or the chat's. Each cycle says
  which, the chat's number, whether the statements cover it, how far matched
  rows go over the amount (the cycle is then done with 0 left), and the rows.
- This cycle: the cycle that holds today, else the next one to start
  (upcoming). A benefit's end date shortens its cycles and enrollment deadline.
  Cycles that start after it ended are left out. Past usage stays in history.
  A credit whose cycles all ended is "ended".
- Use-or-lose order: open and partial credits and free nights by deadline,
  then auto-loads, then used this cycle, then upcoming, then the rest.
- Credits left this cycle: what is not used yet of every credit whose cycle
  holds today. Auto-loads count. The Dashboard line counts the ones that are
  not auto and end within 14 days.
- Next deadline: the soonest date, today or later, among an open cycle's end
  (not auto), an enroll-by date (not enrolled), a free night's expiry, a
  timeshare year's book-by and expiry dates (points left) and a program's
  expiry.
- Value: balance x cents per point, low and high, rounded half up to the
  cent. Cash back counts at face value. Programs with no balance or no cpp,
  and timeshares, are "not valued".
- A program's balance is the overlay's winner (the chat's, or a newer app
  entry). The value range, the redemption "have" line (not on done ones),
  stale and Checks stale all read it. `source` says "app" when an app entry wins, from the overlay's
  meta (the model itself says "manual").
- Benefits are the overlay's too: the owner's edits, ends and app items are in the
  model, and hidden ones are out of it, so the Benefits list, Credits left,
  Next deadline, the Dashboard line, Fees and the Checks cap lines skip them.
  Each benefit row says where it came from, which fields are edited and the
  overlay's notes on it. The page lists the hidden ones apart.
- Card credits to review (plan 4.9): rows no match word or choice claims, the
  ambiguous ones ("Matches more than one benefit. Pick one."), the returns and
  trip groups, each with the overlay's best guess. "Needs a look" adds the
  choices that lost their row, rows no cycle holds and covered cycles the chat
  says are used with no row. Rows a hidden benefit claims never flag, and its
  cycles are left out. The counts feed the page, the Checks lines and the
  review list alike.
- Card spend (milestones and earning caps): purchases minus refunds on the
  card in the calendar year, every cardholder. Rows with txn_type purchase or
  refund, not a transfer, not hidden in the app. Plan It purchases are
  purchase rows, so they count. Fees, interest, plan fees, payments,
  transfers and the Plan It adjustments do not. A cap also needs the row's
  category in its categories, and splits by cardholder. Rows reach the end
  of the card's newest statement (`rows_through`).
- Pace: projection = spend / days so far x days in the year. Need a month =
  what is left / months left after `rows_through`.
- The chat's `reported_spent` is compared with the rows up to its `as_of`.
  More than $25 apart shows both.
- Bills on a milestone card (gap audit C7): the counted recurring items (not
  paused or ended, not fees or transfers) on the card, each at its net monthly
  equivalent, largest first, with their sum. Set to move here: counted items whose preferred
  account is the card and that sit on another account now.
- Fees: recurring items in `annual-fees` (any cadence) and `memberships`
  with cadence annual, active or ending. An open card with a registry
  `annual_fee` and no `annual-fees` item gets a row from the registry. Never
  both. A card's credits for the year go on its largest fee row only, so a
  per-person fee (authorized user) stands alone. A paused or ended
  `annual-fees` item still stops the registry row. A credit counts its
  `counted_value`, else this year's cycles, else amount x cycles a year
  (multi-year and one-time credits with no cycles count nothing). A benefit's
  end date cuts the cycles that start after it. One that ended before the year
  counts nothing. Other kinds count only their `counted_value`. The chat's
  `counted_value` is its own figure for the year, so the chat's end inside the
  year does not cut it. An end the owner set (an edit, or an app benefit's) does:
  the credit then counts at most its cycles up to that end. A benefit a file
  added from a later year (its own start) counts nothing, of any kind.

Trips and rewards stay out of the AI facts in Phase 7.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction

from app.importer.models import (
    REWARD_STALE_DAYS,
    Recurring,
    RewardBenefit,
    RewardMilestone,
    RewardProgram,
    Rewards,
    to_cents,
    to_micro,
)
from app.services.checks_p4 import date_label
from app.services.networth import today
from app.services.recurring import INACTIVE, _round, monthly_equivalent, occurrences_per_year
from app.services.rewards_match import ReviewItem, RowFlag
from app.services.rewards_overlay import (
    Effective,
    OverlayMeta,
    effective_rewards,
    read_transaction,
)
from app.services.trips import trip_names
from app.state import readers

SOON_DAYS = 14  # the Dashboard's "credits end soon" window
REPORTED_MATCH_CENTS = 2500  # the chat's spend and the rows: more than $25 apart shows both
MICRO = 1_000_000
CARD_TYPES = ("credit_card", "store_card")
FEE_CATEGORY = "annual-fees"
MEMBERSHIP_CATEGORY = "memberships"
CYCLES_A_YEAR = {"monthly": 12, "quarterly": 4, "semi_annual": 2, "annual": 1}
GROUP_ORDER = {"open": 0, "auto": 1, "used": 2, "upcoming": 3, "other": 4, "ended": 5}
DEADLINE_ORDER = {"enroll_by": 0, "cycle_end": 1, "book_by": 2, "expires": 3}
REDEMPTION_ORDER = {"ready": 0, "consider": 1, "done": 2}
REVIEW_GROUPS = {"review": "credits", "returns": "returns", "trips": "trips"}
AMBIGUOUS_TEXT = "Matches more than one benefit. Pick one."
LOST_TEXT = "A credit choice lost its row."
NO_CYCLE_TEXT = "Matches a benefit, but no cycle holds its date."
CHAT_ONLY_TEXT = "The chat says used, no credit row matches."
CHOICE_OFF_TEXT = "Your choice counts it for a credit that takes no statement rows."
FLAG_KINDS = {"no_cycle": ("no_cycle", NO_CYCLE_TEXT), "off": ("choice_off", CHOICE_OFF_TEXT)}
COUNT_KEYS = (
    "to_review",
    "ambiguous",
    "returns",
    "trips",
    "over",
    "chat_says_used",
    "needs_a_look",
    "benefit_notes",
    "program_notes",
)


# --------------------------------------------------------------------------
# Context: names from the registry
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Ctx:
    today: date
    accounts: dict[str, sqlite3.Row]
    people: dict[str, str]
    categories: dict[str, str]

    def account(self, key: str | None) -> str | None:
        if key is None:
            return None
        a = self.accounts.get(key)
        return a["display_name"] if a else key

    def person(self, key: str | None) -> str | None:
        return None if key is None else self.people.get(key, key)


def _ctx(conn: sqlite3.Connection) -> Ctx:
    return Ctx(
        today=today(),
        accounts={
            r["account_key"]: r
            for r in conn.execute(
                """SELECT account_key, display_name, type, owner_person_key, active, closed_on,
                          annual_fee_cents, fee_renews_on FROM accounts"""
            )
        },
        people={
            r["person_key"]: r["display_name"]
            for r in conn.execute("SELECT person_key, display_name FROM people")
        },
        categories={
            r["category_key"]: r["name"]
            for r in conn.execute("SELECT category_key, name FROM categories")
        },
    )


def _days(a: date, b: date) -> int:
    return (b - a).days


# --------------------------------------------------------------------------
# Credit cycles
# --------------------------------------------------------------------------


def cycle_state(amount: int, used: int, end: str, on: date) -> str:
    """done, expired, partial or open (docs/plans/phase-7.md 2.1)."""
    if used >= amount:
        return "done"
    if end < on.isoformat():
        return "expired"
    return "partial" if used > 0 else "open"


def _benefit_deadline(b: RewardBenefit, deadline: str) -> str:
    return min(deadline, b.ends_on) if b.ends_on is not None else deadline


def _cycle_rows(meta: OverlayMeta, at: tuple[str, str]) -> list[dict]:
    out = []
    for key in meta.cycle_rows.get(at, ()):
        r = meta.credit_rows[key]
        out.append(
            {
                "txn_key": key,
                "date": r.txn_date,
                "merchant": r.merchant,
                "description": r.description,
                "cents": r.cents,
                "earlier_start": meta.earlier.get(key),
            }
        )
    return out


def _cycle_out(b: RewardBenefit, c, on: date, meta: OverlayMeta) -> dict:
    """One cycle. used and used_on are the overlay's one source (a mark, the
    matched rows or the chat's); the rest says where they came from."""
    amount = to_cents(c.amount or b.amount or "0.00")
    used = to_cents(c.used or "0.00")
    end = _benefit_deadline(b, c.end)
    state = cycle_state(amount, used, end, on)
    at = (b.key, c.start)
    return {
        "start": c.start,
        "end": end,
        "amount_cents": amount,
        "used_cents": used,
        "left_cents": max(amount - used, 0) if state != "expired" else 0,
        "used_on": c.used_on,
        "state": state,
        "days_left": _days(on, date.fromisoformat(end)) if end >= on.isoformat() else None,
        "source": meta.cycle_sources.get(at),
        "chat_used_cents": meta.chat_used.get(at),
        "covered": at in meta.covered,
        "over_cents": meta.over.get(at, 0),
        "rows": _cycle_rows(meta, at),
    }


def _current(
    b: RewardBenefit, on: date, meta: OverlayMeta | None = None
) -> tuple[dict | None, list[dict], str]:
    """(this cycle, past cycles, group) for a credit."""
    day = on.isoformat()
    meta = meta or OverlayMeta()
    cycles = [
        _cycle_out(b, c, on, meta)
        for c in b.cycles or []
        if b.ends_on is None or c.start <= b.ends_on
    ]
    past = [c for c in cycles if c["end"] < day]
    cur = next((c for c in cycles if c["start"] <= day <= c["end"]), None)
    if cur is not None:
        if cur["state"] == "done":
            group = "used"
        elif b.auto:
            group = "auto"
        else:
            group = "open"
        return cur, past, group
    nxt = next((c for c in cycles if c["start"] > day), None)
    if nxt is not None:
        return nxt, past, "upcoming"
    ended = bool(cycles) or (b.ends_on is not None and b.ends_on < day)
    return None, past, "ended" if ended else "other"


def _origin(meta: OverlayMeta, key: str) -> str:
    return "app" if meta.provenance.get(("benefit", key)) == "app" else "chat"


def _benefit_notices(meta: OverlayMeta, key: str) -> list[dict]:
    return [
        {"kind": n.kind, "fields": list(n.fields)}
        for n in meta.notices
        if n.key == key and n.kind.startswith("benefit_")
    ]


def _benefit_out(
    b: RewardBenefit, ctx: Ctx, meta: OverlayMeta, programs: dict[str, str], hidden: bool = False
) -> dict:
    on = ctx.today
    enroll_by = _benefit_deadline(b, b.enroll_by) if b.enroll_by is not None else None
    cycle, past, group = (None, [], "other")
    if b.kind == "credit":
        cycle, past, group = _current(b, on, meta)
    elif b.ends_on is not None:  # a free night or other benefit with an expiry
        group = "open" if b.ends_on >= on.isoformat() else "ended"
    deadline = cycle["end"] if cycle and group in ("open", "auto") else None
    if b.kind != "credit" and group == "open":
        deadline = b.ends_on
    return {
        "key": b.key,
        "name": b.name,
        "kind": b.kind,
        "account_key": b.account_key,
        "account_name": ctx.account(b.account_key),
        "cadence": b.cadence,
        "amount_cents": to_cents(b.amount) if b.amount is not None else None,
        "counted_value_cents": to_cents(b.counted_value) if b.counted_value else None,
        "auto": b.auto,
        "enrolled": b.enrolled,
        "enroll_by": enroll_by,
        "enroll_flag": b.enrolled is False
        and enroll_by is not None
        and enroll_by >= on.isoformat(),
        "rolls_over": b.rolls_over,
        "ends_on": b.ends_on,
        "program_name": programs.get(b.program_key) if b.program_key else None,
        "how_to_use": b.how_to_use,
        "note": b.note,
        "group": group,
        "deadline": deadline,
        "days_left": _days(on, date.fromisoformat(deadline)) if deadline else None,
        "cycle": cycle,
        "past_cycles": past,
        "origin": _origin(meta, b.key),
        "edited_fields": list(meta.edited_fields.get(b.key, ())),
        "notices": _benefit_notices(meta, b.key),
        "hidden": hidden,
        "match_off": b.key in meta.match_off,
        "terms_count": meta.terms_count.get(b.key, 0),
        "amount_from": meta.amount_from.get(b.key),
    }


def benefits_list(r: Rewards, ctx: Ctx, meta: OverlayMeta | None = None) -> list[dict]:
    """Credits, free nights and other benefits in use-or-lose order."""
    programs = {p.key: p.name for p in r.programs}
    meta = meta or OverlayMeta()
    out = [
        _benefit_out(b, ctx, meta, programs)
        for b in r.benefits
        if b.kind not in ("earning_cap", "status_perk")
    ]
    out.sort(
        key=lambda x: (
            GROUP_ORDER[x["group"]],
            x["deadline"] or (x["cycle"] or {}).get("end") or "9999-12-31",
            x["name"].casefold(),
            x["key"],
        )
    )
    return out


def credits_left(r: Rewards, on: date) -> tuple[int, int]:
    """(cents, count) not used yet on credits whose cycle holds today."""
    cents = count = 0
    for b in r.benefits:
        if b.kind != "credit":
            continue
        cur, _, group = _current(b, on)
        if group in ("open", "auto") and cur is not None and cur["left_cents"] > 0:
            cents += cur["left_cents"]
            count += 1
    return cents, count


def _effective(conn: sqlite3.Connection) -> tuple[list[readers.StateRun], Effective | None]:
    """The runs and the effective rewards, read in one snapshot."""
    with read_transaction(conn):
        runs = readers.monthly_runs(conn)
        return runs, effective_rewards(conn, runs)


def credits_ending(conn: sqlite3.Connection) -> dict | None:
    """The Dashboard line: credits not auto, not used, ending within 14 days."""
    _, eff = _effective(conn)
    if eff is None:
        return None
    r = eff.rewards
    on = today()
    hits = []
    for b in r.benefits:
        if b.kind != "credit":
            continue
        cur, _, group = _current(b, on)
        if group == "open" and cur is not None and cur["days_left"] is not None:
            if cur["days_left"] <= SOON_DAYS:
                hits.append(cur)
    if not hits:
        return None
    return {
        "count": len(hits),
        "left_cents": sum(c["left_cents"] for c in hits),
        "first_end": min(c["end"] for c in hits),
    }


def next_deadline(r: Rewards, on: date) -> dict | None:
    day = on.isoformat()
    found: list[tuple[str, int, str, str, str]] = []  # date, order, label, kind, key
    for b in r.benefits:
        enroll_by = _benefit_deadline(b, b.enroll_by) if b.enroll_by is not None else None
        if b.enrolled is False and enroll_by and enroll_by >= day:
            found.append((enroll_by, 0, f"Enroll in {b.name}", "enroll_by", b.key))
        if b.kind == "credit":
            cur, _, group = _current(b, on)
            if group == "open" and cur is not None:
                found.append((cur["end"], 1, f"{b.name} ends", "cycle_end", b.key))
        elif b.kind == "free_night" and b.ends_on and b.ends_on >= day:
            found.append((b.ends_on, 3, f"{b.name} expires", "expires", b.key))
    for p in r.programs:
        if p.expires_on and p.expires_on >= day:
            found.append((p.expires_on, 3, f"{p.name} points expire", "expires", p.key))
        for a in p.allocations or []:
            left = to_micro(a.allocated) - to_micro(a.used) - to_micro(a.pulled_forward or "0")
            if left <= 0:
                continue
            if a.book_by and a.book_by >= day:
                found.append((a.book_by, 2, f"Book {p.name} {a.year}", "book_by", p.key))
            if a.expires_on and a.expires_on >= day:
                found.append(
                    (a.expires_on, 3, f"{p.name} {a.year} points expire", "expires", p.key)
                )
    if not found:
        return None
    when, _, label, kind, key = min(found)
    return {
        "date": when,
        "label": label,
        "kind": kind,
        "key": key,
        "days_left": _days(on, date.fromisoformat(when)),
    }


# --------------------------------------------------------------------------
# Points and value
# --------------------------------------------------------------------------


def _value_cents(balance: str, cpp: str) -> int:
    """balance x cents per point, rounded half up to the cent."""
    return _round(Fraction(to_micro(balance) * to_micro(cpp), MICRO * MICRO))


def value_range(p: RewardProgram) -> tuple[int, int] | None:
    """(low, high) cents, or None when the program is not valued."""
    if p.balance is None:
        return None
    if p.kind == "cash_back":
        cents = _round(Fraction(to_micro(p.balance), MICRO // 100))
        return cents, cents
    if p.cpp_low is None or p.cpp_high is None or p.kind == "timeshare":
        return None
    return _value_cents(p.balance, p.cpp_low), _value_cents(p.balance, p.cpp_high)


def _micro(value: str | None) -> int | None:
    return to_micro(value) if value is not None else None


def last_redeemed(r: Rewards) -> dict[str, str]:
    """Program key -> the newest done_on among its done redemptions (1.4)."""
    out: dict[str, str] = {}
    for x in r.redemptions:
        if x.status == "done" and x.done_on and x.done_on > out.get(x.program_key, ""):
            out[x.program_key] = x.done_on
    return out


def _program_out(p: RewardProgram, ctx: Ctx, meta: OverlayMeta, last: Mapping[str, str]) -> dict:
    vr = value_range(p)
    stale_before = (ctx.today - timedelta(days=REWARD_STALE_DAYS)).isoformat()
    from_app = meta.provenance.get(("balance", p.key)) == "app"
    return {
        "key": p.key,
        "name": p.name,
        "kind": p.kind,
        "group": p.group,
        "balance_micro": _micro(p.balance),
        "as_of": p.as_of,
        "source": "app" if from_app else p.source,
        "origin": "app" if ("program", p.key) in meta.provenance else "chat",
        "chat_balance_micro": meta.chat_balance.get(p.key),
        "stale": p.as_of is not None and p.as_of < stale_before,
        "owner_key": p.owner,
        "owner_name": ctx.person(p.owner),
        "earned_on": [{"account_key": k, "name": ctx.account(k)} for k in p.earned_on or []],
        "status_tier": p.status_tier,
        "status_via_name": ctx.account(p.status_via),
        "cpp_low": p.cpp_low,
        "cpp_high": p.cpp_high,
        "value_low_cents": vr[0] if vr else None,
        "value_high_cents": vr[1] if vr else None,
        "expires_on": p.expires_on,
        "last_redeemed_on": last.get(p.key),
        "best_uses": list(p.best_uses or []),
        "avoid": list(p.avoid or []),
        "note": p.note,
        "allocations": [
            {
                "year": a.year,
                "allocated_micro": to_micro(a.allocated),
                "used_micro": to_micro(a.used),
                "pulled_forward_micro": _micro(a.pulled_forward),
                "left_micro": to_micro(a.allocated)
                - to_micro(a.used)
                - to_micro(a.pulled_forward or "0"),
                "expires_on": a.expires_on,
                "book_by": a.book_by,
            }
            for a in p.allocations or []
        ],
    }


def value_table(programs: list[dict]) -> dict:
    """The "Estimated total portfolio value" table: valued programs first."""
    rows = []
    for p in programs:
        valued = p["value_low_cents"] is not None
        rows.append(
            {
                "key": p["key"],
                "name": p["name"],
                "kind": p["kind"],
                "balance_micro": p["balance_micro"],
                "cpp_low": p["cpp_low"],
                "cpp_high": p["cpp_high"],
                "low_cents": p["value_low_cents"],
                "high_cents": p["value_high_cents"],
                "basis": ("face" if p["kind"] == "cash_back" else "cpp")
                if valued
                else "not_valued",
            }
        )
    rows.sort(key=lambda x: (x["basis"] == "not_valued", -(x["high_cents"] or 0), x["key"]))
    return {
        "rows": rows,
        "low_cents": sum(x["low_cents"] or 0 for x in rows),
        "high_cents": sum(x["high_cents"] or 0 for x in rows),
    }


def _redemptions(r: Rewards, trips: dict[str, str]) -> list[dict]:
    """A done redemption's points_needed is the points it used, so it is never short."""
    progs = {p.key: p for p in r.programs}
    out = []
    for x in r.redemptions:
        p = progs.get(x.program_key)
        have = _micro(p.balance) if p else None
        need = _micro(x.points_needed)
        done = x.status == "done"
        out.append(
            {
                "key": x.key,
                "title": x.title,
                "status": x.status,
                "program_key": x.program_key,
                "program_name": p.name if p else x.program_key,
                "points_needed_micro": need,
                "have_micro": have,
                "enough": None if done or need is None or have is None else have >= need,
                "trip_key": x.trip_key,
                "trip_name": trips.get(x.trip_key) if x.trip_key else None,
                "note": x.note,
                "done_on": x.done_on,
            }
        )
    out.sort(key=_redemption_order)
    return out


def _redemption_order(x: dict) -> tuple:
    """Ready, consider, done. Done ones newest first by done_on (1.4), undated ones last,
    then by title. Ready and consider never carry done_on."""
    d = x["done_on"]
    newest = -date.fromisoformat(d).toordinal() if d else 0
    return (REDEMPTION_ORDER[x["status"]], d is None, newest, x["title"].casefold(), x["key"])


# --------------------------------------------------------------------------
# Card spend from rows
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Spend:
    cents: int
    rows_from: str | None  # the first day the card's statements cover in the year
    rows_through: str | None  # the last day, capped at Dec 31
    by_month: dict[str, int]  # YYYY-MM -> cents in that month
    by_cardholder: dict[str | None, int]
    at: dict[str, int]  # requested day -> cents up to and including it


def card_spend(
    conn: sqlite3.Connection,
    account_key: str,
    year: int,
    categories: list[str] | None = None,
    at: tuple[str, ...] = (),
) -> Spend:
    """Purchases minus refunds on the card in `year` (positive = spent)."""
    first, last = f"{year}-01-01", f"{year}-12-31"
    cov = conn.execute(
        """SELECT min(period_start) AS lo, max(period_end) AS hi FROM statements
           WHERE account_key = ? AND is_current = 1 AND period_end >= ? AND period_start <= ?""",
        (account_key, first, last),
    ).fetchone()
    params: list[object] = [account_key, first, last]
    cat_sql = ""
    if categories is not None:
        cat_sql = f"AND category_key IN ({', '.join('?' for _ in categories)})"
        params += categories
    rows = conn.execute(
        f"""SELECT txn_date, amount_cents, cardholder_person_key FROM v_transactions
            WHERE account_key = ? AND txn_date BETWEEN ? AND ?
              AND txn_type IN ('purchase', 'refund') AND COALESCE(is_transfer, 0) = 0
              AND hidden = 0 {cat_sql}""",
        params,
    ).fetchall()
    by_month: dict[str, int] = {}
    by_holder: dict[str | None, int] = {}
    at_cents = dict.fromkeys(at, 0)
    total = 0
    for r in rows:
        cents = -r["amount_cents"]
        total += cents
        by_month[r["txn_date"][:7]] = by_month.get(r["txn_date"][:7], 0) + cents
        holder = r["cardholder_person_key"]
        by_holder[holder] = by_holder.get(holder, 0) + cents
        for day in at_cents:
            if r["txn_date"] <= day:
                at_cents[day] += cents
    lo, hi = (cov["lo"], cov["hi"]) if cov and cov["lo"] else (None, None)
    return Spend(
        cents=total,
        rows_from=max(lo, first) if lo else None,
        rows_through=min(hi, last) if hi else None,
        by_month=by_month,
        by_cardholder=by_holder,
        at=at_cents,
    )


def _reported(
    reported: str | None, as_of: str | None, s: Spend, year: int
) -> tuple[int | None, int | None, bool]:
    """(chat cents, app cents up to as_of, more than $25 apart). The app side is
    None when as_of falls in another year."""
    if reported is None or as_of is None:
        return None, None, False
    chat = to_cents(reported)
    if as_of[:4] != str(year):
        return chat, None, False
    app = s.at[as_of]
    return chat, app, abs(app - chat) > REPORTED_MATCH_CENTS


def pace(spent: int, threshold: int, year: int, through: str | None) -> dict:
    """Projection, need a month and the pace word for a spend target."""
    if through is None:
        return {"projection_cents": None, "need_per_month_cents": None}
    start, end = date(year, 1, 1), date(year, 12, 31)
    upto = date.fromisoformat(through)
    days_in_year = _days(start, end) + 1
    elapsed = _days(start, upto) + 1
    left_days = _days(upto, end)
    projection = spent if left_days <= 0 else _round(Fraction(spent * days_in_year, elapsed))
    to_go = threshold - spent
    need = None
    if to_go > 0 and left_days > 0:
        need = _round(Fraction(to_go * days_in_year, 12 * left_days))
    return {"projection_cents": projection, "need_per_month_cents": need}


def _months(year: int, s: Spend) -> list[dict]:
    """Cumulative spend at each month's end, from the rows' first month to their
    last. Months before the card's statements start have no rows, not zero."""
    if s.rows_from is None or s.rows_through is None:
        return []
    out, running = [], 0
    for m in range(int(s.rows_from[5:7]), int(s.rows_through[5:7]) + 1):
        month = f"{year}-{m:02d}"
        running += s.by_month.get(month, 0)
        out.append({"month": month, "cumulative_cents": running})
    return out


def _bill(item: Recurring) -> dict:
    return {"key": item.key, "name": item.name, "monthly_cents": monthly_equivalent(item)[1]}


def _largest_first(b: dict) -> tuple:
    return (-b["monthly_cents"], b["name"].casefold(), b["key"])


# Bills that never count toward a spend milestone: fees post as fee rows, and
# transfers are not purchases (card_spend counts purchases and refunds only).
NOT_SPEND_CATEGORIES = frozenset(
    {"annual-fees", "card-fees", "card-interest", "plan-fees", "bank-fees", "transfer"}
)


def card_bills(items: list[Recurring], card: str, ctx: Ctx) -> dict:
    """The counted bills on the card and the ones set to move to it (gap audit C7).
    Fees and transfers are left out: they never count toward the milestone."""
    counted = [
        i for i in items if i.status not in INACTIVE and i.category_key not in NOT_SPEND_CATEGORIES
    ]
    on = sorted((_bill(i) for i in counted if i.account_key == card), key=_largest_first)
    to_move = sorted(
        (
            {**_bill(i), "account_name": ctx.account(i.account_key)}
            for i in counted
            if i.preferred_account_key == card and i.account_key != card
        ),
        key=_largest_first,
    )
    return {
        "bills": on,
        "bills_monthly_cents": sum(b["monthly_cents"] for b in on),
        "bills_to_move": to_move,
    }


def _milestone_out(
    conn: sqlite3.Connection, m: RewardMilestone, ctx: Ctx, items: list[Recurring]
) -> dict:
    s = card_spend(conn, m.account_key, m.year, at=(m.as_of,) if m.as_of else ())
    threshold = to_cents(m.threshold)
    p = pace(s.cents, threshold, m.year, s.rows_through)
    chat, app_at, differs = _reported(m.reported_spent, m.as_of, s, m.year)
    if m.status == "met" or s.cents >= threshold:
        word = "met"
    elif m.status == "missed" or (s.rows_through or "") >= f"{m.year}-12-31":
        word = "missed"
    elif p["projection_cents"] is None:
        word = None
    else:
        word = "on_pace" if p["projection_cents"] >= threshold else "behind"
    if word in ("met", "missed"):
        p["need_per_month_cents"] = None  # nothing left to plan
    return {
        "key": m.key,
        "account_key": m.account_key,
        "account_name": ctx.account(m.account_key),
        "year": m.year,
        "threshold_cents": threshold,
        "reward": m.reward,
        "status": m.status,
        "met_on": m.met_on,
        "plan": m.plan,
        "note": m.note,
        "spent_cents": s.cents,
        "to_go_cents": 0 if word == "met" else max(threshold - s.cents, 0),
        **p,
        "pace": word,
        "rows_from": s.rows_from,
        "rows_through": s.rows_through,
        "months": _months(m.year, s),
        "reported_spent_cents": chat,
        "reported_as_of": m.as_of,
        "app_at_reported_cents": app_at,
        "reported_differs": differs,
        **card_bills(items, m.account_key, ctx),
    }


def _cap_out(conn: sqlite3.Connection, b: RewardBenefit, ctx: Ctx, meta: OverlayMeta) -> dict:
    year = ctx.today.year
    cats = list(b.category_keys or [])
    s = card_spend(conn, b.account_key, year, cats, at=(b.as_of,) if b.as_of else ())
    cap = to_cents(b.cap or "0.00")
    chat, app_at, differs = _reported(b.reported_spent, b.as_of, s, year)
    holders = sorted(s.by_cardholder.items(), key=lambda kv: (-kv[1], kv[0] or "~"))
    return {
        "key": b.key,
        "name": b.name,
        "account_key": b.account_key,
        "account_name": ctx.account(b.account_key),
        "year": year,
        "cap_cents": cap,
        "earn_before": b.earn_before,
        "earn_after": b.earn_after,
        "categories": [{"key": k, "name": ctx.categories.get(k, k)} for k in cats],
        "spent_cents": s.cents,
        "left_cents": max(cap - s.cents, 0),
        "by_cardholder": [
            {"person_key": k, "name": ctx.person(k), "cents": v} for k, v in holders if v
        ],
        "rows_from": s.rows_from,
        "rows_through": s.rows_through,
        "reported_spent_cents": chat,
        "reported_as_of": b.as_of,
        "app_at_reported_cents": app_at,
        "reported_differs": differs,
        "note": b.note,
        "origin": _origin(meta, b.key),
    }


# --------------------------------------------------------------------------
# Fees
# --------------------------------------------------------------------------


def _ended_before(b: RewardBenefit, year: int) -> bool:
    return b.ends_on is not None and b.ends_on < f"{year}-01-01"


def _starts_after(b: RewardBenefit, year: int) -> bool:
    """Every cycle starts after the year: a credit a benefit file adds from a
    later year. The chat sends this year's cycles only."""
    return bool(b.cycles) and min(c.start for c in b.cycles) > f"{year}-12-31"


def _year_value(
    b: RewardBenefit, year: int, ended_by_you: bool = False, starts_on: str | None = None
) -> int:
    """A benefit's value for the year: counted_value, else its full value. A
    counted_value is the chat's own figure for the year, so the chat's end date
    inside the year does not cut it. An end the owner set on a credit does: it counts
    at most its cycles up to the end. A benefit whose cycles all start after the
    year counts nothing, its counted_value too. So does one whose own start
    (`starts_on`, an app benefit a file added) is after the year, of any kind."""
    if _ended_before(b, year) or _starts_after(b, year):
        return 0
    if starts_on is not None and starts_on > f"{year}-12-31":
        return 0
    if b.counted_value is not None:
        counted = to_cents(b.counted_value)
        if ended_by_you and b.kind == "credit" and b.ends_on is not None:
            return min(counted, _full_value(b, year))
        return counted
    return _full_value(b, year)


def _full_value(b: RewardBenefit, year: int) -> int:
    """This year's cycles, else amount x cycles a year. Multi-year and one-time
    credits with no cycles count nothing. Cycles that start after the benefit
    ends count nothing, like on the Benefits tab. A benefit that ended before
    the year counts nothing."""
    if _ended_before(b, year):
        return 0
    if b.cycles:
        return sum(
            to_cents(c.amount or b.amount or "0.00")
            for c in b.cycles
            if c.start[:4] == str(year) and (b.ends_on is None or c.start <= b.ends_on)
        )
    per_year = CYCLES_A_YEAR.get(b.cadence or "", 0)
    if per_year and b.ends_on is not None and b.ends_on[:4] == str(year):
        # Calendar periods that start on or before the end date.
        per_year = (int(b.ends_on[5:7]) - 1) // (12 // per_year) + 1
    return to_cents(b.amount or "0.00") * per_year


def fees(
    runs: list[readers.StateRun],
    r: Rewards | None,
    ctx: Ctx,
    ended_by_you: frozenset[str] = frozenset(),
    starts: Mapping[str, str] | None = None,
) -> dict:
    """The Fees table. `ended_by_you`: benefit keys whose end the owner set.
    `starts`: benefit key -> an app benefit's own first day."""
    year = ctx.today.year
    rows: list[dict] = []
    fee_cards: set[str] = set()
    for item in readers.recurring(runs):
        is_fee = item.category_key == FEE_CATEGORY
        if is_fee and item.account_key:
            fee_cards.add(item.account_key)  # paused or ended too: no registry row
        if item.status in INACTIVE:
            continue
        if not is_fee and not (
            item.category_key == MEMBERSHIP_CATEGORY and item.cadence == "annual"
        ):
            continue
        rows.append(
            {
                "key": item.key,
                "name": item.name,
                "kind": "card_fee" if is_fee else "membership",
                "source": "recurring",
                "account_key": item.account_key,
                "account_name": ctx.account(item.account_key),
                "owner_name": ctx.person(item.owner),
                "gross_cents": _round(occurrences_per_year(item) * to_cents(item.amount)),
                "renews_on": item.renewal_date or item.next_date,
            }
        )
    for key, a in sorted(ctx.accounts.items()):
        if (
            a["type"] in CARD_TYPES
            and a["annual_fee_cents"]
            and a["active"]
            and a["closed_on"] is None
            and key not in fee_cards
        ):
            rows.append(
                {
                    "key": f"card:{key}",
                    "name": a["display_name"],
                    "kind": "card_fee",
                    "source": "registry",
                    "account_key": key,
                    "account_name": a["display_name"],
                    "owner_name": ctx.person(a["owner_person_key"]),
                    "gross_cents": a["annual_fee_cents"],
                    "renews_on": a["fee_renews_on"],
                }
            )
    # A card's credits go on its largest fee row only. Other kinds count only
    # with a counted_value, and have no full value apart from it.
    credits: dict[str, list[dict]] = {}
    for b in r.benefits if r else []:
        if b.kind == "credit" or b.counted_value is not None:
            cents = _year_value(b, year, b.key in ended_by_you, (starts or {}).get(b.key))
            credits.setdefault(b.account_key, []).append(
                {
                    "key": b.key,
                    "name": b.name,
                    "cents": cents,
                    "full_cents": _full_value(b, year) if b.kind == "credit" else cents,
                    "counted": b.counted_value is not None,
                }
            )
    holder: dict[str, str] = {}
    for row in sorted(rows, key=lambda x: (-x["gross_cents"], x["key"])):
        if row["kind"] == "card_fee" and row["account_key"] not in holder:
            holder[row["account_key"]] = row["key"]
    for row in rows:
        mine = holder.get(row["account_key"]) == row["key"]
        row["credits"] = credits.get(row["account_key"], []) if mine else []
        row["credits_cents"] = sum(c["cents"] for c in row["credits"])
        row["net_cents"] = row["gross_cents"] - row["credits_cents"]
        row["net_positive"] = row["credits_cents"] > row["gross_cents"]
    rows.sort(
        key=lambda x: (
            x["kind"] != "card_fee",
            (x["account_name"] or x["name"]).casefold(),
            -x["gross_cents"],
            x["key"],
        )
    )
    return {
        "year": year,
        "rows": rows,
        "gross_cents": sum(x["gross_cents"] for x in rows),
        "credits_cents": sum(x["credits_cents"] for x in rows),
        "net_cents": sum(x["net_cents"] for x in rows),
    }


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------


def _notices(meta: OverlayMeta) -> list[dict]:
    return [
        {
            "kind": n.kind,
            "text": n.text,
            "key": n.key,
            "other_key": n.other,
            "fields": list(n.fields),
        }
        for n in meta.notices
    ]


def _orphans(meta: OverlayMeta) -> list[dict]:
    return [
        {"kind": o.kind, "key": o.key, "holds": list(o.holds), "reason": o.reason, "unit": o.unit}
        for o in meta.orphans
    ]


def _hidden(meta: OverlayMeta, ctx: Ctx) -> list[dict]:
    return [
        {
            "key": b.key,
            "name": b.name,
            "kind": b.kind,
            "account_key": b.account_key,
            "account_name": ctx.account(b.account_key),
            "origin": _origin(meta, b.key),
        }
        for b in meta.hidden
    ]


def _ended_by_you(r: Rewards, meta: OverlayMeta) -> frozenset[str]:
    """Benefits whose end the owner set: an edited ends_on, or an app benefit's."""
    return frozenset(
        b.key
        for b in r.benefits
        if b.ends_on is not None
        and ("ends_on" in meta.edited_fields.get(b.key, ()) or _origin(meta, b.key) == "app")
    )


def benefit_views(conn: sqlite3.Connection, keys: set[str]) -> dict[str, dict]:
    """Benefit rows for these keys, from the effective view, hidden ones and
    every kind included. Keys that are not shown are left out."""
    _, eff = _effective(conn)
    if eff is None:
        return {}
    ctx = _ctx(conn)
    programs = {p.key: p.name for p in eff.rewards.programs}
    rows = [(b, False) for b in eff.rewards.benefits] + [(b, True) for b in eff.meta.hidden]
    return {
        b.key: _benefit_out(b, ctx, eff.meta, programs, hidden)
        for b, hidden in rows
        if b.key in keys
    }


def program_views(conn: sqlite3.Connection, keys: set[str]) -> dict[str, dict]:
    """The page's program rows for these keys, from the effective view. Keys that
    are not shown are left out."""
    _, eff = _effective(conn)
    if eff is None:
        return {}
    ctx = _ctx(conn)
    last = last_redeemed(eff.rewards)
    return {
        p.key: _program_out(p, ctx, eff.meta, last) for p in eff.rewards.programs if p.key in keys
    }


def rewards_page(conn: sqlite3.Connection) -> dict:
    runs, eff = _effective(conn)
    r = eff.rewards if eff else None
    ctx = _ctx(conn)
    fee_table = fees(
        runs,
        r,
        ctx,
        _ended_by_you(r, eff.meta) if eff and r else frozenset(),
        eff.meta.starts_on if eff else None,
    )
    if eff is None or r is None:
        return {
            "source_run_id": None,
            "today": ctx.today.isoformat(),
            "tiles": None,
            "highlights": [],
            "programs": [],
            "value_table": {"rows": [], "low_cents": 0, "high_cents": 0},
            "redemptions": [],
            "benefits": [],
            "caps": [],
            "perks": [],
            "milestones": [],
            "routing": [],
            "fees": fee_table,
            "notices": [],
            "orphans": [],
            "hidden": [],
            "review_counts": review_counts(None),
        }
    last = last_redeemed(r)
    programs = [_program_out(p, ctx, eff.meta, last) for p in r.programs]
    programs.sort(key=lambda p: (p["group"] != "primary", p["name"].casefold(), p["key"]))
    table = value_table(programs)
    left_cents, left_count = credits_left(r, ctx.today)
    names = {p.key: p.name for p in r.programs}
    items = readers.recurring(runs)
    return {
        "source_run_id": readers.section_source(runs, "rewards"),
        "today": ctx.today.isoformat(),
        "tiles": {
            "value_low_cents": table["low_cents"],
            "value_high_cents": table["high_cents"],
            "valued_count": sum(x["basis"] != "not_valued" for x in table["rows"]),
            "credits_left_cents": left_cents,
            "credits_left_count": left_count,
            "next_deadline": next_deadline(r, ctx.today),
        },
        "highlights": list(r.highlights or []),
        "programs": programs,
        "value_table": table,
        "redemptions": _redemptions(r, trip_names(conn)),
        "benefits": benefits_list(r, ctx, eff.meta),
        "caps": [_cap_out(conn, b, ctx, eff.meta) for b in r.benefits if b.kind == "earning_cap"],
        "perks": [
            {
                "key": b.key,
                "name": b.name,
                "account_name": ctx.account(b.account_key),
                "program_name": names.get(b.program_key or "", b.program_key),
                "status_tier": b.status_tier,
                "note": b.note,
                "origin": _origin(eff.meta, b.key),
            }
            for b in r.benefits
            if b.kind == "status_perk"
        ],
        "milestones": sorted(
            (_milestone_out(conn, m, ctx, items) for m in r.milestones),
            key=lambda m: (m["status"] != "open", -m["year"], m["account_name"] or "", m["key"]),
        ),
        "routing": [
            {
                "key": x.key,
                "spend": x.spend,
                "account_key": x.account_key,
                "account_name": ctx.account(x.account_key),
                "earn": x.earn,
                "note": x.note,
                "display_order": x.display_order,
            }
            for x in sorted(r.routing, key=lambda x: (x.display_order, x.key))
        ],
        "fees": fee_table,
        "notices": _notices(eff.meta),
        "orphans": _orphans(eff.meta),
        "hidden": _hidden(eff.meta, ctx),
        "review_counts": review_counts(eff.meta),
    }


# --------------------------------------------------------------------------
# Card credits to review and what needs a look (plan 4.9)
# --------------------------------------------------------------------------


def _shown_flags(meta: OverlayMeta) -> list[RowFlag]:
    """Lost choices, rows no cycle holds, and choices on a credit that takes no
    rows. Rows a hidden benefit claims never flag."""
    hidden = {b.key for b in meta.hidden}
    return [f for f in meta.flags if f.kind == "lost" or f.benefit_key not in hidden]


def _shown_cycles(meta: OverlayMeta, found: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """(benefit key, cycle start) pairs, hidden benefits left out."""
    hidden = {b.key for b in meta.hidden}
    return sorted(at for at in found if at[0] not in hidden)


def review_counts(meta: OverlayMeta | None) -> dict:
    """The counts behind "Review N card credits", "Needs a look" and the Checks
    lines, split by where each item is: the review (needs_a_look), the
    Benefits tab, or the Points tab and the page (a failed overlay). None
    (before the first 1.3 run) counts nothing."""
    if meta is None:
        return dict.fromkeys(COUNT_KEYS, 0)
    groups = [x.group for x in meta.review]
    notes = len(meta.notices) + len(meta.orphans)
    on_benefits = sum(1 for n in meta.notices if n.kind.startswith("benefit_"))
    on_benefits += sum(1 for o in meta.orphans if o.kind == "benefit")
    return {
        "to_review": groups.count("review"),
        "ambiguous": sum(1 for x in meta.review if x.options),
        "returns": groups.count("returns"),
        "trips": groups.count("trips"),
        "over": len(_shown_cycles(meta, meta.over)),
        "chat_says_used": len(_shown_cycles(meta, meta.chat_only)),
        "needs_a_look": len(_shown_flags(meta)),
        "benefit_notes": on_benefits,
        "program_notes": notes - on_benefits,
    }


def _review_row(x: ReviewItem, eff: Effective, ctx: Ctx, names: dict[str, str]) -> dict:
    r = eff.meta.credit_rows[x.txn_key]
    guess = None
    if x.guess is not None and x.guess[0] in names:
        key, start = x.guess
        guess = {"benefit_key": key, "benefit_name": names[key], "cycle_start": start}
    return {
        "txn_key": r.txn_key,
        "date": r.txn_date,
        "account_key": r.account_key,
        "account_name": ctx.account(r.account_key),
        "merchant": r.merchant,
        "description": r.description,
        "cents": r.cents,
        "trip_key": r.trip_key,
        "options": list(x.options),
        "note": AMBIGUOUS_TEXT if x.options else None,
        "guess": guess,
    }


def _guard_day_cents(guard_json: str | None) -> tuple[str | None, int | None]:
    """The date and amount a choice's guard holds, when they are well formed."""
    try:
        guard = json.loads(guard_json or "{}")
    except ValueError:
        return None, None
    if not isinstance(guard, dict):
        return None, None
    day, cents = guard.get("txn_date"), guard.get("amount_cents")
    return (
        day if isinstance(day, str) else None,
        cents if isinstance(cents, int) and not isinstance(cents, bool) else None,
    )


def _needs_a_look(eff: Effective, names: dict[str, str], guards: dict[str, str]) -> list[dict]:
    meta = eff.meta
    out = []
    for f in _shown_flags(meta):
        if f.kind == "lost":
            day, cents = _guard_day_cents(guards.get(f.txn_key))
            kind, text = "lost_choice", LOST_TEXT
        else:
            r = meta.credit_rows[f.txn_key]
            day, cents, (kind, text) = r.txn_date, r.cents, FLAG_KINDS[f.kind]
        out.append(
            {
                "kind": kind,
                "text": text,
                "benefit_key": f.benefit_key,
                "benefit_name": names.get(f.benefit_key or ""),
                "cycle_start": None,
                "txn_key": f.txn_key,
                "date": day,
                "cents": cents,
            }
        )
    used_on = {(b.key, c.start): c.used_on for b in eff.chat.benefits for c in b.cycles or []}
    for at in _shown_cycles(meta, meta.chat_only):
        out.append(
            {
                "kind": "chat_only",
                "text": CHAT_ONLY_TEXT,
                "benefit_key": at[0],
                "benefit_name": names.get(at[0]),
                "cycle_start": at[1],
                "txn_key": None,
                "date": used_on.get(at),
                "cents": meta.chat_used.get(at),
            }
        )
    return out


def review_page(conn: sqlite3.Connection) -> dict:
    """GET /api/rewards/review: Card credits to review, the returns and trip
    groups, and what needs a look that is not already a page notice."""
    with read_transaction(conn):
        runs, eff = _effective(conn)
        guards = {
            r["txn_key"]: r["guard_json"]
            for r in conn.execute("SELECT txn_key, guard_json FROM reward_credit_decisions")
        }
    out: dict = {"source_run_id": None, "needs_a_look": [], "counts": review_counts(None)}
    out |= {name: [] for name in REVIEW_GROUPS.values()}
    if eff is None:
        return out
    ctx = _ctx(conn)
    names = {b.key: b.name for b in [*eff.rewards.benefits, *eff.meta.hidden]}
    for x in eff.meta.review:
        out[REVIEW_GROUPS[x.group]].append(_review_row(x, eff, ctx, names))
    out |= {
        "source_run_id": readers.section_source(runs, "rewards"),
        "needs_a_look": _needs_a_look(eff, names, guards),
        "counts": review_counts(eff.meta),
    }
    return out


# --------------------------------------------------------------------------
# Checks: the "Rewards and travel" block (rewards part)
# --------------------------------------------------------------------------


def _gap_line(app: int, chat: int) -> dict:
    diff = app - chat
    return {
        "app_cents": app,
        "chat_cents": chat,
        "diff_cents": diff,
        "status": "match" if abs(diff) <= REPORTED_MATCH_CENTS else "differs",
    }


def _rows_note(
    name: str | None, rows_from: str | None, through: str | None, year: int, as_of: str
) -> str | None:
    if through is None:
        return f"{name} has no statement rows in {year}."
    if rows_from and rows_from > f"{year}-01-01":
        return f"{name}: the rows start {date_label(rows_from)}, so the app counts less."
    if through < as_of:
        return (
            f"{name}: the rows reach {date_label(through)}. The chat counts to {date_label(as_of)}."
        )
    return None


def checks(conn: sqlite3.Connection) -> dict | None:
    """Stale balances and the milestone and cap gaps against the chat's numbers."""
    runs, eff = _effective(conn)
    if eff is None:
        return None
    r = eff.rewards
    ctx = _ctx(conn)
    limit = (ctx.today - timedelta(days=REWARD_STALE_DAYS)).isoformat()
    stale = [
        {"key": p.key, "name": p.name, "what": "balance", "as_of": p.as_of}
        for p in r.programs
        if p.as_of and p.as_of < limit
    ]
    stale += [
        {"key": b.key, "name": b.name, "what": "reported_spent", "as_of": b.as_of}
        for b in r.benefits
        if b.as_of and b.as_of < limit
    ]
    stale += [
        {
            "key": m.key,
            "name": f"{ctx.account(m.account_key)} spend milestone",
            "what": "reported_spent",
            "as_of": m.as_of,
        }
        for m in r.milestones
        if m.as_of and m.as_of < limit
    ]
    stale.sort(key=lambda x: (x["as_of"], x["key"]))
    gaps = []
    for m in r.milestones:
        if m.reported_spent is None or m.as_of is None:
            continue
        s = card_spend(conn, m.account_key, m.year, at=(m.as_of,))
        name = ctx.account(m.account_key)
        gaps.append(
            {
                "kind": "milestone",
                "key": m.key,
                "name": f"{name} spend milestone",
                "as_of": m.as_of,
                **_gap_line(s.at[m.as_of], to_cents(m.reported_spent)),
                "note": _rows_note(name, s.rows_from, s.rows_through, m.year, m.as_of),
            }
        )
    for b in r.benefits:
        if b.kind != "earning_cap" or b.reported_spent is None or b.as_of is None:
            continue
        year = int(b.as_of[:4])
        s = card_spend(conn, b.account_key, year, list(b.category_keys or []), at=(b.as_of,))
        name = ctx.account(b.account_key)
        gaps.append(
            {
                "kind": "cap",
                "key": b.key,
                "name": f"{name} {b.name.lower()}" if name else b.name,
                "as_of": b.as_of,
                **_gap_line(s.at[b.as_of], to_cents(b.reported_spent)),
                "note": _rows_note(name, s.rows_from, s.rows_through, year, b.as_of),
            }
        )
    return {
        "source_run_id": readers.section_source(runs, "rewards"),
        "stale": stale,
        "gaps": gaps,
        "counts": review_counts(eff.meta),
    }
