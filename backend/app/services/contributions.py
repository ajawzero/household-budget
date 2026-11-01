"""401k and HSA contributions against the IRS limits (PLAN 3.3, 3.9, 8.2).

Per person and year:
- Year to date: the newest pay stub's YTD columns (401k: employee deferral;
  HSA: employee plus employer), plus prior-employer and personal rows from
  `contributions`. With no stub YTD, the per-check amounts are summed.
- Cross-check: the sum of per-check amounts up to that stub, beside the stub's
  YTD (also on Checks).
- Limit: 401k employee deferral plus the person's catch-up (50 and over, or
  60 to 63, set in Settings since birth years are not stored). HSA: self-only
  or family by the person's coverage, plus 1,000 at 55 and over. The family
  limit is shared by both spouses. Partial-year coverage is not prorated.
- Projection: the pay schedule is read from the person's regular stubs
  (weekly, every two weeks, twice a month or monthly). Pay dates after the
  newest stub through Dec 31 count at the newest regular check's amount.
  Bonus checks are left out of the projection.
- Election change: the first pay date after today that is at least the
  person's lead time away (default one pay period). The amount per check from
  then on that lands the year exactly at the limit, and for 401k the percent
  of gross pay.
- 415(c): employee (up to the base limit, so catch-up is left out) plus
  employer, shown only when employer rows exist.

Limits are app-owned rows seeded from IRS figures, unconfirmed until an
admin confirms them. Changes write an event with no amounts.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from app.services.overrides import EditError

LIMIT_KINDS = (
    "k401_deferral",
    "k401_catchup_50",
    "k401_catchup_60_63",
    "k415c_total",
    "hsa_self",
    "hsa_family",
    "hsa_catchup_55",
)
LIMIT_LABELS = {
    "k401_deferral": "401k employee limit",
    "k401_catchup_50": "401k catch-up, age 50 and over",
    "k401_catchup_60_63": "401k catch-up, age 60 to 63",
    "k415c_total": "401k total, employee and employer",
    "hsa_self": "HSA, self-only coverage",
    "hsa_family": "HSA, family coverage",
    "hsa_catchup_55": "HSA catch-up, age 55 and over",
}
INCLUDES_EMPLOYER = frozenset({"k415c_total", "hsa_self", "hsa_family"})
CATCH_UP_KIND = {"50": "k401_catchup_50", "60-63": "k401_catchup_60_63"}
MAX_LIMIT_CENTS = 100_000_00  # $100,000, well above any of these limits
NO_PERSON = "No such person."
NO_YEAR_NOTE = "No IRS limits for this year yet. Add them in Settings."
HSA_FAMILY_NOTE = "The family limit is shared. Both spouses' HSA money counts toward it."
PRORATE_NOTE = "Full-year limit. It is lower if HDHP coverage was not the whole year."


# --------------------------------------------------------------------------
# Pay schedule
# --------------------------------------------------------------------------


def _month_end(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def _add_month(d: date, day: int) -> date:
    nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return nxt.replace(day=min(day, _month_end(nxt).day))


SHIFT_DAYS = 3  # a payroll moves a weekend or holiday pay date up to 3 days earlier
GAP_SLACK = 2  # days a weekly or biweekly date may move for a holiday


@dataclass(frozen=True)
class Schedule:
    cadence: str  # weekly, biweekly, semimonthly or monthly
    day: int | None = None  # monthly: the day of the month, 31 for the month end


def _nominal_half(d: date) -> date | None:
    """The 15th or month end a shifted pay date stands for, or None."""
    if 15 - SHIFT_DAYS <= d.day <= 15:
        return d.replace(day=15)
    end = _month_end(d)
    if (end - d).days <= SHIFT_DAYS:
        return end
    return None


def _halves_apart(a: date, b: date) -> int:
    """How many 15th/month-end steps from nominal date a to b."""

    def index(d: date) -> int:
        return (d.year * 12 + d.month) * 2 + (0 if d.day == 15 else 1)

    return index(b) - index(a)


def _steady(gaps: list[int], step: int) -> bool:
    """Every gap is a whole number of steps (a missed stub is fine), give or take
    a holiday shift, and at least one gap is a single step."""
    counts = [round(g / step) for g in gaps]
    return (
        all(n >= 1 and abs(g - n * step) <= GAP_SLACK for g, n in zip(gaps, counts, strict=True))
        and 1 in counts
    )


def infer_schedule(dates: list[date]) -> Schedule | None:
    """The pay schedule from regular pay dates. Allows weekend and holiday shifts
    and a missing stub."""
    recent = sorted(set(dates))[-6:]
    if len(recent) < 2:
        return None
    halves = [_nominal_half(d) for d in recent]
    if all(h is not None for h in halves):
        nominal = [h for h in halves if h is not None]
        steps = [_halves_apart(a, b) for a, b in zip(nominal, nominal[1:], strict=False)]
        kinds = {h.day == 15 for h in nominal}
        if len(kinds) == 2 and all(1 <= n <= 2 for n in steps) and 1 in steps:
            return Schedule("semimonthly")
        if kinds == {False} and all(n in (2, 4) for n in steps):
            return Schedule("monthly", 31)
    gaps = [(b - a).days for a, b in zip(recent, recent[1:], strict=False)]
    weekdays = [d.weekday() for d in recent]
    same_weekday = max(weekdays.count(w) for w in weekdays) * 2 > len(recent)
    if same_weekday and _steady(gaps, 7):
        return Schedule("weekly")
    if same_weekday and _steady(gaps, 14):
        return Schedule("biweekly")
    days = sorted(d.day for d in recent)
    day = days[-1]  # the latest day: earlier ones were moved up for a weekend
    if all(day - SHIFT_DAYS <= x <= day for x in days) and all(
        28 - SHIFT_DAYS <= g <= 31 + SHIFT_DAYS or 59 - SHIFT_DAYS <= g <= 62 + SHIFT_DAYS
        for g in gaps
    ):
        return Schedule("monthly", day)
    return None


def infer_cadence(dates: list[date]) -> str | None:
    """weekly, biweekly, semimonthly or monthly from pay dates (oldest first)."""
    s = infer_schedule(dates)
    return s.cadence if s else None


def _semimonthly_after(d: date) -> date:
    if d.day < 15:
        return d.replace(day=15)
    if d < _month_end(d):
        return _month_end(d)
    return _add_month(d, 15)


def _business_day(d: date) -> date:
    """A Saturday or Sunday pay date moves to the Friday before."""
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def schedule_dates(last: date, cadence: str, through: date, day: int | None = None) -> list[date]:
    """Pay dates after `last` through `through`. Semimonthly and monthly dates
    count from the nominal date `last` stands for, and a weekend date moves to
    the Friday before. `day` is the monthly pay day (31 for the month end)."""
    out: list[date] = []
    if cadence == "semimonthly":
        d = _nominal_half(last) or last
    elif cadence == "monthly":
        day = day or last.day
        d = last
    else:
        d = last
    while True:
        if cadence == "weekly":
            d += timedelta(days=7)
        elif cadence == "biweekly":
            d += timedelta(days=14)
        elif cadence == "semimonthly":
            d = _semimonthly_after(d)
        elif cadence == "monthly":
            d = _add_month(d, day or 1)
        else:
            return out
        paid = d if cadence in ("weekly", "biweekly") else _business_day(d)
        if paid > through:
            return out
        if paid > last:
            out.append(paid)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def limits_for(conn: sqlite3.Connection, year: int) -> dict[str, sqlite3.Row]:
    return {
        r["kind"]: r
        for r in conn.execute("SELECT * FROM contribution_limits WHERE year = ?", (year,))
    }


def limit_row(r: sqlite3.Row) -> dict:
    return {
        "year": r["year"],
        "kind": r["kind"],
        "label": LIMIT_LABELS[r["kind"]],
        "limit_cents": r["limit_cents"],
        "includes_employer": bool(r["includes_employer"]),
        "source_url": r["source_url"],
        "confirmed": r["confirmed_at"] is not None,
        "confirmed_at": r["confirmed_at"],
        "updated_at": r["updated_at"],
    }


@dataclass(frozen=True)
class PersonSettings:
    person_key: str
    k401_catch_up: str = "none"
    hsa_coverage: str | None = None
    hsa_catch_up: bool = False
    election_lead_checks: int = 1
    updated_at: str | None = None

    def out(self) -> dict:
        return {
            "k401_catch_up": self.k401_catch_up,
            "hsa_coverage": self.hsa_coverage,
            "hsa_catch_up": self.hsa_catch_up,
            "election_lead_checks": self.election_lead_checks,
            "updated_at": self.updated_at,
        }


def people_settings(conn: sqlite3.Connection) -> dict[str, PersonSettings]:
    return {
        r["person_key"]: PersonSettings(
            r["person_key"],
            r["k401_catch_up"],
            r["hsa_coverage"],
            bool(r["hsa_catch_up"]),
            r["election_lead_checks"],
            r["updated_at"],
        )
        for r in conn.execute("SELECT * FROM contribution_people")
    }


# --------------------------------------------------------------------------
# Per person and plan
# --------------------------------------------------------------------------


def pct1(numer: int, denom: int) -> str | None:
    """numer / denom as a percent with one decimal, half up. None when denom is 0."""
    if denom <= 0:
        return None
    tenths = (numer * 1000 * 2 + denom) // (2 * denom)
    return f"{tenths // 10}.{tenths % 10}"


def milli_pct_str(milli: int | None) -> str | None:
    if milli is None:
        return None
    tenths = (milli + 50) // 100
    return f"{tenths // 10}.{tenths % 10}"


@dataclass
class PlanInputs:
    kind: str  # k401 or hsa
    stub_ytd: int | None  # newest stub YTD (HSA: employee plus employer)
    stub_ytd_employee: int | None
    stub_date: str | None
    sum_of_checks: int  # per-check amounts up to the stub (or all, without one)
    checks_counted: int
    prior_employer: int
    personal: int
    employer_ytd: int  # 401k employer rows (415c); HSA employer from the stub
    has_employer_rows: bool
    per_check: int | None  # newest regular check; HSA: employee plus employer
    per_check_employer: int  # HSA employer part of per_check
    per_check_pct: str | None
    gross: int | None
    has_data: bool


def _plan_inputs(
    kind: str, stubs: list[sqlite3.Row], contribs: list[sqlite3.Row], year: int
) -> PlanInputs:
    in_year = [s for s in stubs if s["pay_date"].startswith(f"{year}-")]
    if kind == "k401":

        def ytd(s: sqlite3.Row) -> int | None:
            return s["ytd_k401_cents"]

        def per(s: sqlite3.Row) -> int | None:
            return s["k401_cents"]

        employee_kinds, employer_kinds = ("401k_employee",), ("401k_employer",)
    else:

        def ytd(s: sqlite3.Row) -> int | None:
            e, r = s["ytd_hsa_employee_cents"], s["ytd_hsa_employer_cents"]
            return None if e is None and r is None else (e or 0) + (r or 0)

        def per(s: sqlite3.Row) -> int | None:
            e, r = s["hsa_employee_cents"], s["hsa_employer_cents"]
            return None if e is None and r is None else (e or 0) + (r or 0)

        employee_kinds, employer_kinds = ("hsa_employee", "hsa_employer"), ()
    with_ytd = [s for s in in_year if ytd(s) is not None]
    stub = max(with_ytd, key=lambda s: (s["pay_date"], ytd(s)), default=None)
    stub_ytd = ytd(stub) if stub else None
    upto = stub["pay_date"] if stub else None
    counted = [s for s in in_year if per(s) is not None and (upto is None or s["pay_date"] <= upto)]
    sum_checks = sum(per(s) or 0 for s in counted)
    c_year = [c for c in contribs if c["date"].startswith(f"{year}-")]
    prior = sum(
        c["amount_cents"]
        for c in c_year
        if c["kind"] in employee_kinds + employer_kinds and c["source"] == "prior_employer"
    )
    personal = sum(
        c["amount_cents"]
        for c in c_year
        if c["kind"] in employee_kinds and c["source"] in ("personal", "gift")
    )
    current = [c for c in c_year if c["source"] == "current_employer"]
    employer_rows = [c for c in current if c["kind"] in employer_kinds]
    if kind == "hsa":
        stub_e = stub["ytd_hsa_employer_cents"] if stub else None
        employer_ytd = stub_e or 0
        stub_employee = (stub["ytd_hsa_employee_cents"] or 0) if stub else None
    else:
        employer_ytd = sum(c["amount_cents"] for c in employer_rows)
        stub_employee = stub_ytd
    if stub is None and not counted:
        # No stubs this year: current-employer contribution rows instead.
        rows = [c for c in current if c["kind"] in employee_kinds + employer_kinds]
        rows = rows if kind == "hsa" else [c for c in rows if c["kind"] in employee_kinds]
        sum_checks = sum(c["amount_cents"] for c in rows)
    regular = [s for s in stubs if s["kind"] == "regular"]
    latest = max(regular, key=lambda s: (s["pay_date"], s["seq"]), default=None)
    per_check = per_check_employer = None
    if latest is not None:
        per_check = per(latest) or 0
        per_check_employer = (latest["hsa_employer_cents"] or 0) if kind == "hsa" else 0
    has_data = bool(
        stub is not None
        or counted
        or prior
        or personal
        or employer_rows
        or any(c["kind"] in employee_kinds for c in current)
        or (latest is not None and per(latest))
    )
    return PlanInputs(
        kind=kind,
        stub_ytd=stub_ytd,
        stub_ytd_employee=stub_employee,
        stub_date=upto,
        sum_of_checks=sum_checks,
        checks_counted=len(counted),
        prior_employer=prior,
        personal=personal,
        employer_ytd=employer_ytd,
        has_employer_rows=bool(employer_rows),
        per_check=per_check,
        per_check_employer=per_check_employer or 0,
        per_check_pct=milli_pct_str(latest["k401_milli_pct"])
        if kind == "k401" and latest is not None
        else None,
        gross=latest["gross_cents"] if latest is not None else None,
        has_data=has_data,
    )


def _limit(
    kind: str, s: PersonSettings, limits: dict[str, sqlite3.Row]
) -> tuple[int | None, int, int, bool]:
    """(limit, base, catch-up, every part confirmed). limit None when unknown."""
    if kind == "k401":
        base_kind = "k401_deferral"
        catch_kind = CATCH_UP_KIND.get(s.k401_catch_up)
    else:
        if s.hsa_coverage not in ("self", "family"):
            return None, 0, 0, False
        base_kind = "hsa_self" if s.hsa_coverage == "self" else "hsa_family"
        catch_kind = "hsa_catchup_55" if s.hsa_catch_up else None
    base = limits.get(base_kind)
    catch = limits.get(catch_kind) if catch_kind else None
    if base is None or (catch_kind and catch is None):
        return None, 0, 0, False
    confirmed = base["confirmed_at"] is not None and (catch is None or catch["confirmed_at"])
    base_c = base["limit_cents"]
    catch_c = catch["limit_cents"] if catch else 0
    return base_c + catch_c, base_c, catch_c, bool(confirmed)


def plan_row(
    p: PlanInputs,
    s: PersonSettings,
    limits: dict[str, sqlite3.Row],
    schedule: Schedule | None,
    last_regular: date | None,
    year: int,
    today: date,
) -> dict:
    stub_part = p.stub_ytd if p.stub_ytd is not None else p.sum_of_checks
    ytd = stub_part + p.prior_employer + p.personal
    limit, base, catch, confirmed = _limit(p.kind, s, limits)
    year_end = date(year, 12, 31)
    remaining: list[date] = []
    if schedule and last_regular is not None:
        start = max(last_regular, date.fromisoformat(p.stub_date) if p.stub_date else last_regular)
        dates = schedule_dates(last_regular, schedule.cadence, year_end, schedule.day)
        remaining = [d for d in dates if d > start and d.year == year]
    projectable = schedule is not None and p.per_check is not None
    checks_left = len(remaining) if projectable else None
    projected = ytd + len(remaining) * (p.per_check or 0) if projectable else None
    future = [d for d in remaining if d > today]
    lead = s.election_lead_checks
    change_on = future[lead] if projectable and lead < len(future) else None
    before = sum(1 for d in remaining if change_on and d < change_on)
    after = sum(1 for d in remaining if change_on and d >= change_on)
    needed = needed_pct = None
    if limit is not None and change_on is not None and after:
        total = max(limit - ytd - before * (p.per_check or 0), 0) // after
        needed = max(total - p.per_check_employer, 0)  # the employee's part
        if p.kind == "k401" and p.gross:
            needed_pct = pct1(needed, p.gross)
    if limit is None:
        status = "no_limit"
    elif not p.has_data:
        status = "no_data"
    elif projected is None:
        status = "no_schedule"
    elif projected > limit:
        status = "over"
    elif limit - projected < max(p.per_check or 0, 1):
        status = "at_limit"
    else:
        status = "under"
    k415c = None
    if p.kind == "k401" and p.has_employer_rows and "k415c_total" in limits:
        # 415(c) is per unrelated employer: deferrals at a prior employer
        # count toward 402(g) above, not toward this plan's 415(c) room.
        this_plan = stub_part + p.personal
        counted = min(this_plan, base) if base else this_plan
        cap = limits["k415c_total"]["limit_cents"]
        k415c = {
            "employee_cents": counted,
            "employer_cents": p.employer_ytd,
            "total_cents": counted + p.employer_ytd,
            "limit_cents": cap,
            "room_cents": cap - counted - p.employer_ytd,
        }
    cross = None
    if p.stub_ytd is not None and p.checks_counted:
        cross = p.stub_ytd - p.sum_of_checks
    notes = []
    if p.kind == "hsa" and limit is not None:
        notes.append(PRORATE_NOTE)
        if s.hsa_coverage == "family":
            notes.append(HSA_FAMILY_NOTE)
    if p.kind == "hsa" and s.hsa_coverage is None:
        notes.append("Set HSA coverage in Settings to see the limit.")
    if limit is None and p.kind == "k401":
        notes.append(NO_YEAR_NOTE)
    if needed is not None and p.gross and needed > p.gross:
        notes.append("Reaching the limit would take more than the gross pay per check.")
    return {
        "kind": p.kind,
        "label": "401k" if p.kind == "k401" else "HSA",
        "ytd_cents": ytd,
        "ytd_as_of": p.stub_date,
        "ytd_parts": {
            "stubs_cents": stub_part,
            "employer_cents": p.employer_ytd if p.kind == "hsa" else None,
            "prior_employer_cents": p.prior_employer,
            "personal_cents": p.personal,
        },
        "sum_of_checks_cents": p.sum_of_checks,
        "checks_counted": p.checks_counted,
        "cross_check_diff_cents": cross,
        "limit_cents": limit,
        "limit_base_cents": base if limit is not None else None,
        "limit_catch_up_cents": catch if limit is not None else None,
        "limit_confirmed": confirmed,
        "room_cents": limit - ytd if limit is not None else None,
        "per_check_cents": p.per_check,
        "per_check_employer_cents": p.per_check_employer if p.kind == "hsa" else None,
        "per_check_pct": p.per_check_pct,
        "checks_left": checks_left,
        "remaining_dates": [d.isoformat() for d in remaining],
        "projected_cents": projected,
        "projected_room_cents": limit - projected
        if limit is not None and projected is not None
        else None,
        "status": status,
        "change_on": change_on.isoformat() if change_on else None,
        "checks_before_change": before if change_on else None,
        "checks_from_change": after if change_on else None,
        "needed_per_check_cents": needed,
        "needed_pct": needed_pct,
        "shared": p.kind == "hsa" and s.hsa_coverage == "family",
        "k415c": k415c,
        "notes": notes,
    }


def contributions_page(conn: sqlite3.Connection, year: int, today: date) -> dict:
    limits = limits_for(conn, year)
    settings = people_settings(conn)
    names = {r["person_key"]: r["display_name"] for r in conn.execute("SELECT * FROM people")}
    stubs: dict[str, list[sqlite3.Row]] = {}
    for r in conn.execute("SELECT * FROM paychecks ORDER BY person_key, pay_date, kind, seq"):
        stubs.setdefault(r["person_key"], []).append(r)
    contribs: dict[str, list[sqlite3.Row]] = {}
    for r in conn.execute("SELECT * FROM contributions ORDER BY person_key, date"):
        contribs.setdefault(r["person_key"], []).append(r)
    people = []
    family_rows = []
    for person in sorted(set(stubs) | set(contribs) | set(settings)):
        s = settings.get(person) or PersonSettings(person)
        mine = stubs.get(person, [])
        regular_dates = [date.fromisoformat(x["pay_date"]) for x in mine if x["kind"] == "regular"]
        schedule = infer_schedule(regular_dates)
        cadence = schedule.cadence if schedule else None
        last_regular = max(regular_dates) if regular_dates else None
        plans = []
        for kind in ("k401", "hsa"):
            p = _plan_inputs(kind, mine, contribs.get(person, []), year)
            wanted = p.has_data or (kind == "hsa" and s.hsa_coverage in ("self", "family"))
            if not wanted:
                continue
            row = plan_row(p, s, limits, schedule, last_regular, year, today)
            plans.append(row)
            if row["shared"]:
                family_rows.append(row)
        if not plans:
            continue
        people.append(
            {
                "person_key": person,
                "display_name": names.get(person, person),
                "settings": s.out(),
                "settings_set": person in settings,
                "schedule": {
                    "cadence": cadence,
                    "last_pay_date": last_regular.isoformat() if last_regular else None,
                },
                "plans": plans,
            }
        )
    family = None
    if len(family_rows) > 1:
        limit = max(r["limit_cents"] or 0 for r in family_rows)
        ytd = sum(r["ytd_cents"] for r in family_rows)
        proj = [r["projected_cents"] for r in family_rows]
        family = {
            "ytd_cents": ytd,
            "projected_cents": sum(proj) if all(x is not None for x in proj) else None,
            "limit_cents": limit,
            "room_cents": limit - ytd,
        }
    return {
        "year": year,
        "as_of": today.isoformat(),
        "limits": [limit_row(limits[k]) for k in LIMIT_KINDS if k in limits],
        "limits_found": bool(limits),
        "people": people,
        "hsa_family": family,
        "other": other_contributions(conn, year),
    }


OTHER_LABELS = {"ira": "IRA", "529": "529"}


def other_contributions(conn: sqlite3.Connection, year: int) -> list[dict]:
    """IRA and 529 money put in during `year`, per person, account and kind.
    The app has no limits for these (Ask's facts, PLAN 7.4, and Investments >
    Contributions). gift_cents is the part with source 'gift'. last_cents adds
    up every row on last_date, so a gift and a deposit on one day count as two."""
    groups: dict[tuple[str, str, str], dict] = {}
    for r in conn.execute(
        """SELECT c.person_key, coalesce(p.display_name, c.person_key) AS person,
                  c.account_key, coalesce(a.display_name, c.account_key) AS account_name,
                  c.kind, c.source, c.date, c.amount_cents
           FROM contributions AS c
           LEFT JOIN people AS p ON p.person_key = c.person_key
           LEFT JOIN accounts AS a ON a.account_key = c.account_key
           WHERE c.kind IN ('ira', '529') AND c.date BETWEEN ? AND ?
           ORDER BY c.person_key, c.kind, c.account_key, c.date""",
        (f"{year}-01-01", f"{year}-12-31"),
    ):
        g = groups.setdefault(
            (r["person_key"], r["kind"], r["account_key"]),
            {
                "person_key": r["person_key"],
                "person": r["person"],
                "account_key": r["account_key"],
                "account_name": r["account_name"],
                "kind": r["kind"],
                "label": OTHER_LABELS[r["kind"]],
                "ytd_cents": 0,
                "gift_cents": 0,
                "count": 0,
                "last_date": r["date"],
                "last_cents": 0,
            },
        )
        g["ytd_cents"] += r["amount_cents"]
        g["count"] += 1
        if r["source"] == "gift":
            g["gift_cents"] += r["amount_cents"]
        if r["date"] > g["last_date"]:
            g["last_date"], g["last_cents"] = r["date"], 0
        g["last_cents"] += r["amount_cents"]
    return [groups[k] for k in sorted(groups)]


# --------------------------------------------------------------------------
# Changes (admin)
# --------------------------------------------------------------------------


def _event(conn: sqlite3.Connection, user_id: int, now: str, entity: str, key: str, detail: dict):
    conn.execute(
        """INSERT INTO events (at, user_id, kind, entity, entity_key, detail_json)
           VALUES (?, ?, 'setting_changed', ?, ?, ?)""",
        (now, user_id, entity, key, json.dumps(detail, sort_keys=True)),
    )


def set_limit(
    conn: sqlite3.Connection,
    *,
    year: int,
    kind: str,
    limit_cents: int,
    confirm: bool,
    user_id: int,
    now: str,
) -> dict:
    """Add, change or confirm one limit. Call inside a write transaction."""
    if kind not in LIMIT_KINDS:
        raise EditError(422, "Unknown limit.")
    if not 0 <= limit_cents <= MAX_LIMIT_CENTS:
        raise EditError(422, "That limit is out of range.")
    conn.execute(
        """INSERT INTO contribution_limits (year, kind, limit_cents, includes_employer,
               confirmed_at, confirmed_by, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (year, kind) DO UPDATE SET
             limit_cents = excluded.limit_cents, confirmed_at = excluded.confirmed_at,
             confirmed_by = excluded.confirmed_by,
             updated_at = CASE WHEN limit_cents = excluded.limit_cents THEN updated_at
                               ELSE excluded.updated_at END""",
        (
            year,
            kind,
            limit_cents,
            int(kind in INCLUDES_EMPLOYER),
            now if confirm else None,
            user_id if confirm else None,
            now,
        ),
    )
    _event(conn, user_id, now, "contribution_limits", f"{year}:{kind}", {"confirmed": confirm})
    row = conn.execute(
        "SELECT * FROM contribution_limits WHERE year = ? AND kind = ?", (year, kind)
    ).fetchone()
    return limit_row(row)


def set_person(
    conn: sqlite3.Connection,
    person_key: str,
    *,
    k401_catch_up: str,
    hsa_coverage: str | None,
    hsa_catch_up: bool,
    election_lead_checks: int,
    user_id: int,
    now: str,
) -> PersonSettings:
    if conn.execute("SELECT 1 FROM people WHERE person_key = ?", (person_key,)).fetchone() is None:
        raise EditError(404, NO_PERSON)
    conn.execute(
        """INSERT INTO contribution_people (person_key, k401_catch_up, hsa_coverage, hsa_catch_up,
               election_lead_checks, updated_by, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (person_key) DO UPDATE SET
             k401_catch_up = excluded.k401_catch_up, hsa_coverage = excluded.hsa_coverage,
             hsa_catch_up = excluded.hsa_catch_up,
             election_lead_checks = excluded.election_lead_checks,
             updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
        (
            person_key,
            k401_catch_up,
            hsa_coverage,
            int(hsa_catch_up),
            election_lead_checks,
            user_id,
            now,
        ),
    )
    _event(conn, user_id, now, "contribution_people", person_key, {})
    return people_settings(conn)[person_key]
