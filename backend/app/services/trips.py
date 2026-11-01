"""GET /api/trips and GET /api/trips/{key} (docs/plans/phase-7.md section 4):
the Travel page, plus the Dashboard's next-trip line and the Checks trips part.

Trips are history (format 1.3): the `trips`, `trip_people`, `trip_items` and
`trip_ious` tables hold the newest run per trip key, tombstones removed. A
final trip stays without being resent, and a backfill trip needs no monthly
run. Each trip names the run it came from.

The app works out the totals, the same scope on every trip:

- Cash so far: posted and booked items paid in cash, plus the cash taxes and
  fees on points and free-night items, less points credits. Covered (someone
  else paid) and excluded items do not count. "Total actual" once complete.
- Estimated remaining: the estimate ranges of estimate items, plus a count of
  TBD items. Covered items are left out here too, and so are maybe items (1.4,
  a cost not booked that may be added), which stay in the still-open list.
- Our net: cash so far, less IOUs owed to us (open or settled), plus IOUs we
  owe (open or settled). Waived IOUs count nothing.
- Points used: points on posted and booked items, not covered or excluded,
  by program.
- Due (1.4): booked items with a due date that add cash (not covered, not a
  points credit, not $0), listed in What's still open by due date and, for
  the next 30 days, on the Dashboard. A past one stays until the charge posts.
- Tagged rows (a check only, never the source of a total): rows whose
  trip_key names the trip, whatever their date. Hidden rows and transfers are
  left out, as on the other pages. Money out is the rows below zero plus
  refund rows (a refund is money back on a purchase), so it is net of
  refunds. Deposits are the other rows above zero. Money out is compared
  with the posted items' cash, deposits with the settled IOUs owed to us.
  More than $1 apart shows both.

Scope: no year picker. The tiles, the summary and the completed list cover
trips that end in the household year or the year before, and every later
trip (planned trips next year included). Older trips are under "Show older".

Trips and rewards stay out of the AI facts in Phase 7.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from app.importer.order import run_order_key
from app.services.networth import today
from app.services.rewards_overlay import effective_rewards, read_transaction
from app.state import readers

ACTIVE = ("planning", "booked", "in_progress")
DONE = ("complete", "final")
PAID = ("posted", "booked")
CASH_TENDERS = ("cash", "points", "free_night")
MATCH_CENTS = 100  # tagged rows and items: more than $1 apart shows both
CHART_KINDS = ("flight", "lodging", "food")  # everything else is "other"


# --------------------------------------------------------------------------
# Item and trip math
# --------------------------------------------------------------------------


def item_cash(status: str, tender: str, amount: int | None) -> int:
    """What one item adds to cash so far (a points credit takes away)."""
    if status not in PAID or amount is None:
        return 0
    if tender in CASH_TENDERS:
        return amount
    if tender == "points_credit":
        return -amount
    return 0  # covered


def _counts(i: dict) -> bool:
    """Part of the household's cost: not covered, not excluded."""
    return i["tender"] != "covered" and i["status"] != "excluded"


def _is_due(i: dict) -> bool:
    """A payment still to come (1.4): booked with a due date, and it adds cash."""
    return bool(i["due_on"]) and i["cash_cents"] > 0


def trip_totals(items: list[dict], ious: list[dict]) -> dict:
    cash = posted = low = high = tbd = 0
    by_kind = dict.fromkeys((*CHART_KINDS, "other"), 0)
    points: dict[str | None, int] = {}
    for i in items:
        c = item_cash(i["status"], i["tender"], i["amount_cents"])
        cash += c
        if i["status"] == "posted":
            posted += c
        kind = i["kind"] if i["kind"] in CHART_KINDS else "other"
        by_kind[kind] += c
        if not _counts(i) or i["maybe"]:  # a maybe item is never paid, so cash is the same
            continue
        if i["status"] == "estimate":
            low += i["estimate_low_cents"] or 0
            high += i["estimate_high_cents"] or 0
        elif i["status"] == "tbd":
            tbd += 1
        if i["status"] in PAID and i["points_micro"]:
            points[i["program_key"]] = points.get(i["program_key"], 0) + i["points_micro"]
    owed_to_us = sum(
        o["amount_cents"]
        for o in ious
        if o["direction"] == "owed_to_us" and o["status"] in ("open", "settled")
    )
    we_owe = sum(
        o["amount_cents"]
        for o in ious
        if o["direction"] == "we_owe" and o["status"] in ("open", "settled")
    )
    return {
        "cash_cents": cash,
        "posted_cents": posted,
        "estimate_low_cents": low,
        "estimate_high_cents": high,
        "tbd_count": tbd,
        "owed_to_us_cents": owed_to_us,
        "we_owe_cents": we_owe,
        "our_net_cents": cash - owed_to_us + we_owe,
        "points": points,
        "by_kind": by_kind,
        "settled_to_us_cents": sum(
            o["amount_cents"]
            for o in ious
            if o["direction"] == "owed_to_us" and o["status"] == "settled"
        ),
    }


def versus_estimate(cash: int, low: int | None, high: int | None) -> str | None:
    """Came in under, within or over the estimate before the trip."""
    if low is None or high is None:
        return None
    if cash < low:
        return "under"
    if cash > high:
        return "over"
    return "within"


def _end(t: sqlite3.Row | dict) -> str:
    return t["end_date"] or t["month"]


def _start(t: sqlite3.Row | dict) -> str:
    return t["start_date"] or t["month"]


def in_scope(t: sqlite3.Row | dict, year: int) -> bool:
    """Ends in the household year, the year before, or later."""
    return int(_end(t)[:4]) >= year - 1


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Names:
    people: dict[str, str]
    accounts: dict[str, str]
    programs: dict[str, str]
    runs: dict[str, str]  # run_id -> run_date

    def program(self, key: str | None) -> str | None:
        return None if key is None else self.programs.get(key, key)


def _names(conn: sqlite3.Connection) -> Names:
    with read_transaction(conn):  # names as the Rewards page shows, from one snapshot
        eff = effective_rewards(conn, readers.monthly_runs(conn))
    r = eff.rewards if eff else None
    return Names(
        people={
            x["person_key"]: x["display_name"]
            for x in conn.execute("SELECT person_key, display_name FROM people")
        },
        accounts={
            x["account_key"]: x["display_name"]
            for x in conn.execute("SELECT account_key, display_name FROM accounts")
        },
        programs={p.key: p.name for p in r.programs} if r else {},
        runs={
            x["run_id"]: x["run_date"]
            for x in conn.execute("SELECT run_id, run_date FROM import_runs")
        },
    )


def _tagged(conn: sqlite3.Connection, key: str | None = None) -> dict[str, dict]:
    """Per trip key: rows on the Transactions page (hidden left out), and the
    money out and deposits from those that are not transfers."""
    where, params = "t.trip_key IS NOT NULL AND t.hidden = 0", []
    if key is not None:
        where, params = "t.trip_key = ? AND t.hidden = 0", [key]
    rows = conn.execute(
        f"""SELECT t.trip_key, count(*) AS n,
                   COALESCE(sum(CASE WHEN COALESCE(t.is_transfer, 0) = 0
                       AND (t.amount_cents < 0 OR t.txn_type = 'refund')
                       THEN -t.amount_cents END), 0) AS out_cents,
                   COALESCE(sum(CASE WHEN COALESCE(t.is_transfer, 0) = 0
                       AND t.amount_cents > 0 AND t.txn_type <> 'refund'
                       THEN t.amount_cents END), 0) AS in_cents
            FROM v_transactions AS t WHERE {where} GROUP BY t.trip_key""",
        params,
    ).fetchall()
    return {r["trip_key"]: dict(r) for r in rows}


def _children(conn: sqlite3.Connection, table: str, order: str, key: str | None) -> dict:
    sql = f"SELECT * FROM {table}"
    params: list[str] = []
    if key is not None:
        sql += " WHERE trip_key = ?"
        params.append(key)
    out: dict[str, list[dict]] = {}
    for r in conn.execute(f"{sql} ORDER BY trip_key, {order}", params):
        out.setdefault(r["trip_key"], []).append(dict(r))
    return out


def _load(conn: sqlite3.Connection, key: str | None = None) -> list[dict]:
    names = _names(conn)
    sql, params = "SELECT * FROM trips", []
    if key is not None:
        sql, params = "SELECT * FROM trips WHERE trip_key = ?", [key]
    trips = conn.execute(sql, params).fetchall()
    people = _children(conn, "trip_people", "person_key", key)
    items = _children(conn, "trip_items", "item_key", key)
    ious = _children(conn, "trip_ious", "iou_key", key)
    tagged = _tagged(conn, key)
    return [
        trip_out(
            t,
            people.get(t["trip_key"], []),
            items.get(t["trip_key"], []),
            ious.get(t["trip_key"], []),
            tagged.get(t["trip_key"]),
            names,
        )
        for t in trips
    ]


def _tagged_out(tag: dict | None, totals: dict) -> dict:
    n = tag["n"] if tag else 0
    out = tag["out_cents"] if tag else 0
    deposits = tag["in_cents"] if tag else 0
    posted = totals["posted_cents"]
    settled = totals["settled_to_us_cents"]
    out_differs = abs(out - posted) > MATCH_CENTS
    in_differs = abs(deposits - settled) > MATCH_CENTS
    return {
        "row_count": n,
        "out_cents": out,
        "posted_cents": posted,
        "out_differs": out_differs,
        "deposits_cents": deposits,
        "settled_to_us_cents": settled,
        "deposits_differ": in_differs,
    }


def trip_out(
    t: sqlite3.Row,
    people: list[dict],
    items: list[dict],
    ious: list[dict],
    tag: dict | None,
    names: Names,
) -> dict:
    totals = trip_totals(items, ious)
    labels = {i["item_key"]: i["label"] for i in items}
    done = t["status"] in DONE
    low, high = t["estimate_low_cents"], t["estimate_high_cents"]
    return {
        "key": t["trip_key"],
        "name": t["name"],
        "status": t["status"],
        "start_date": t["start_date"],
        "end_date": t["end_date"],
        "month": t["month"],
        "people": [
            {"key": p["person_key"], "name": names.people.get(p["person_key"], p["person_key"])}
            for p in people
        ],
        "guests": t["guests"],
        "vehicle": t["vehicle"],
        "note": t["note"],
        "estimate_low_cents": low,
        "estimate_high_cents": high,
        "versus_estimate": versus_estimate(totals["cash_cents"], low, high) if done else None,
        "source_run_id": t["run_id"],
        "source_run_date": names.runs.get(t["run_id"]),
        "items": [
            {
                "key": i["item_key"],
                "kind": i["kind"],
                "label": i["label"],
                "status": i["status"],
                "tender": i["tender"],
                "amount_cents": i["amount_cents"],
                "estimate_low_cents": i["estimate_low_cents"],
                "estimate_high_cents": i["estimate_high_cents"],
                "points_micro": i["points_micro"],
                "program_key": i["program_key"],
                "program_name": names.program(i["program_key"]),
                "account_name": (
                    names.accounts.get(i["account_key"], i["account_key"])
                    if i["account_key"]
                    else None
                ),
                "person_name": (
                    names.people.get(i["person_key"], i["person_key"]) if i["person_key"] else None
                ),
                "note": i["note"],
                "due_on": i["due_on"],
                "maybe": bool(i["maybe"]),
                "cash_cents": item_cash(i["status"], i["tender"], i["amount_cents"]),
            }
            for i in items
        ],
        "ious": [
            {
                "key": o["iou_key"],
                "direction": o["direction"],
                "counterparty": o["counterparty"],
                "amount_cents": o["amount_cents"],
                "status": o["status"],
                "settled_on": o["settled_on"],
                "item_key": o["item_key"],
                "item_label": labels.get(o["item_key"]) if o["item_key"] else None,
                "note": o["note"],
            }
            for o in ious
        ],
        "totals": {
            "cash_cents": totals["cash_cents"],
            "estimate_low_cents": totals["estimate_low_cents"],
            "estimate_high_cents": totals["estimate_high_cents"],
            "tbd_count": totals["tbd_count"],
            "owed_to_us_cents": totals["owed_to_us_cents"],
            "we_owe_cents": totals["we_owe_cents"],
            "our_net_cents": totals["our_net_cents"],
            "points": _points(totals["points"], names),
            "by_kind": totals["by_kind"],
        },
        "tagged": _tagged_out(tag, totals),
    }


def _points(points: dict[str | None, int], names: Names) -> list[dict]:
    rows = [
        {"program_key": k, "program_name": names.program(k), "points_micro": v}
        for k, v in points.items()
    ]
    return sorted(rows, key=lambda r: (r["program_name"] or "", r["program_key"] or ""))


def _sum_points(trips: list[dict]) -> list[dict]:
    by: dict[str | None, dict] = {}
    for t in trips:
        for p in t["totals"]["points"]:
            row = by.setdefault(p["program_key"], {**p, "points_micro": 0})
            row["points_micro"] += p["points_micro"]
    return sorted(by.values(), key=lambda r: (r["program_name"] or "", r["program_key"] or ""))


# --------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------


def _by_start(trips: list[dict], newest_first: bool = False) -> list[dict]:
    return sorted(trips, key=lambda t: (_start(t), t["key"]), reverse=newest_first)


def _open_items(trips: list[dict]) -> dict:
    collect, pay, due, items = [], [], [], []
    for t in _by_start(trips):
        for o in t["ious"]:
            if o["status"] != "open":
                continue
            line = {"trip_key": t["key"], "trip_name": t["name"], **o}
            (collect if o["direction"] == "owed_to_us" else pay).append(line)
        for i in t["items"]:
            if _is_due(i):
                due.append({"trip_key": t["key"], "trip_name": t["name"], **i})
            if i["status"] in ("estimate", "tbd") and _counts(i):
                items.append({"trip_key": t["key"], "trip_name": t["name"], **i})
    due.sort(key=lambda x: x["due_on"])  # stable: trip start order breaks a tie
    return {"collect": collect, "pay": pay, "due": due, "items": items}


def _summary(trips: list[dict]) -> dict:
    rows = [
        {
            "key": t["key"],
            "name": t["name"],
            "status": t["status"],
            "start_date": t["start_date"],
            "end_date": t["end_date"],
            "month": t["month"],
            "people": t["people"],
            "guests": t["guests"],
            "total_cents": t["totals"]["cash_cents"],
            "estimate_low_cents": t["totals"]["estimate_low_cents"],
            "estimate_high_cents": t["totals"]["estimate_high_cents"],
            "tbd_count": t["totals"]["tbd_count"],
            "flight_cents": t["totals"]["by_kind"]["flight"],
            "lodging_cents": t["totals"]["by_kind"]["lodging"],
            "food_cents": t["totals"]["by_kind"]["food"],
            "other_cents": t["totals"]["by_kind"]["other"],
        }
        for t in _by_start(trips)
    ]
    return {
        "rows": rows,
        "booked_cents": sum(r["total_cents"] for r in rows),
        "estimate_low_cents": sum(r["estimate_low_cents"] for r in rows),
        "estimate_high_cents": sum(r["estimate_high_cents"] for r in rows),
        "tbd_count": sum(r["tbd_count"] for r in rows),
    }


def _newest_run(trips: list[dict]) -> dict | None:
    if not trips:
        return None
    t = max(trips, key=lambda t: run_order_key(t["source_run_id"]))
    return {"run_id": t["source_run_id"], "run_date": t["source_run_date"]}


def trips_page(conn: sqlite3.Connection) -> dict:
    on = today()
    trips = _load(conn)
    shown = [t for t in trips if in_scope(t, on.year)]
    older = [t for t in trips if not in_scope(t, on.year)]
    open_ = _open_items(shown)
    summary = _summary(shown)
    newest = _newest_run(trips)
    return {
        "today": on.isoformat(),
        "from_year": on.year - 1,
        "source_run_id": newest["run_id"] if newest else None,
        "source_run_date": newest["run_date"] if newest else None,
        "trip_count": len(trips),
        "tiles": {
            "booked_cents": summary["booked_cents"],
            "estimate_low_cents": summary["estimate_low_cents"],
            "estimate_high_cents": summary["estimate_high_cents"],
            "tbd_count": summary["tbd_count"],
            "points": _sum_points(shown),
            "collect_cents": sum(o["amount_cents"] for o in open_["collect"]),
            "collect_count": len(open_["collect"]),
            "pay_cents": sum(o["amount_cents"] for o in open_["pay"]),
            "pay_count": len(open_["pay"]),
        },
        "active": _by_start([t for t in shown if t["status"] in ACTIVE]),
        "open_items": open_,
        "summary": summary,
        "completed": _by_start([t for t in shown if t["status"] in DONE], newest_first=True),
        "older": _by_start(older, newest_first=True),
    }


def trip_detail(conn: sqlite3.Connection, key: str) -> dict | None:
    found = _load(conn, key)
    return found[0] if found else None


def trip_names(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["trip_key"]: r["name"] for r in conn.execute("SELECT trip_key, name FROM trips")}


# --------------------------------------------------------------------------
# Dashboard: the next trip
# --------------------------------------------------------------------------


def next_trip(conn: sqlite3.Connection) -> dict | None:
    """The first planning, booked or in-progress trip that has not ended yet."""
    on = today().isoformat()
    month = on[:7]
    rows = conn.execute(
        f"""SELECT trip_key, start_date, end_date, month FROM trips
            WHERE status IN ({", ".join("?" for _ in ACTIVE)})""",
        ACTIVE,
    ).fetchall()
    ahead = [r for r in rows if (r["end_date"] >= on if r["end_date"] else r["month"] >= month)]
    if not ahead:
        return None
    first = min(ahead, key=lambda r: (_start(r), r["trip_key"]))
    t = trip_detail(conn, first["trip_key"])
    assert t is not None
    return {
        "key": t["key"],
        "name": t["name"],
        "status": t["status"],
        "start_date": t["start_date"],
        "month": t["month"],
        "cash_cents": t["totals"]["cash_cents"],
        "estimate_low_cents": t["totals"]["estimate_low_cents"],
        "estimate_high_cents": t["totals"]["estimate_high_cents"],
        "tbd_count": t["totals"]["tbd_count"],
    }


# --------------------------------------------------------------------------
# Dashboard: trip payments due (1.4)
# --------------------------------------------------------------------------


def upcoming_payments(conn: sqlite3.Connection, start: date, end: date) -> list[dict]:
    """Booked trip items due from start to end (both days in) that add cash: the
    Dashboard's trip payments. The same rule and cash as the Travel Due list."""
    rows = conn.execute(
        """SELECT i.trip_key, i.item_key, i.label, i.status, i.tender, i.amount_cents,
                  i.due_on, i.account_key, t.name AS trip_name,
                  a.display_name AS account_name
           FROM trip_items AS i JOIN trips AS t ON t.trip_key = i.trip_key
           LEFT JOIN accounts AS a ON a.account_key = i.account_key
           WHERE i.due_on BETWEEN ? AND ?""",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    out = []
    for r in rows:
        cash = item_cash(r["status"], r["tender"], r["amount_cents"])
        if cash <= 0:
            continue
        out.append(
            {
                "date": r["due_on"],
                "kind": "trip",
                "key": r["item_key"],
                "name": r["label"],
                "amount_cents": cash,
                "account_name": r["account_name"] or r["account_key"],  # as trip_out
                "trip_key": r["trip_key"],
                "trip_name": r["trip_name"],
            }
        )
    return out


# --------------------------------------------------------------------------
# Checks: the trips part of "Rewards and travel"
# --------------------------------------------------------------------------


def _sent_trips(conn: sqlite3.Connection) -> set[str]:
    """Trip keys any imported run sent as a trip (not a tombstone)."""
    out: set[str] = set()
    for r in conn.execute(
        """SELECT s.json FROM run_sections s JOIN import_runs r ON r.run_id = s.run_id
           WHERE r.status = 'imported' AND s.section = 'trips'"""
    ):
        out.update(t["key"] for t in json.loads(r["json"]) if not t.get("deleted"))
    return out


def unknown_trips(conn: sqlite3.Connection) -> list[dict]:
    """Trip keys named on rows, plan components, trip flags or redemptions that
    the app has no trip for. "removed" when a run sent it and a later one
    removed it, else "not_sent"."""
    known = set(trip_names(conn))
    refs: dict[str, dict[str, int]] = {}

    def add(key: str | None, where: str, n: int = 1) -> None:
        if key and key not in known:
            spot = refs.setdefault(key, {})
            spot[where] = spot.get(where, 0) + n

    for r in conn.execute(
        """SELECT trip_key, count(*) AS n FROM transactions
           WHERE trip_key IS NOT NULL GROUP BY trip_key"""
    ):
        add(r["trip_key"], "rows", r["n"])
    runs = readers.monthly_runs(conn)
    for p in readers.installment_plans(runs):
        for c in p.components or []:
            add(c.trip_key, "plans")
    for f in readers.flags(runs):
        if f.related_kind == "trip":
            add(f.related_key, "flags")
    r = readers.rewards(runs)  # the chat's: nothing edits redemptions
    for x in r.redemptions if r else []:
        add(x.trip_key, "redemptions")
    sent = _sent_trips(conn)
    return [
        {
            "key": key,
            "status": "removed" if key in sent else "not_sent",
            "rows": where.get("rows", 0),
            "plans": where.get("plans", 0),
            "flags": where.get("flags", 0),
            "redemptions": where.get("redemptions", 0),
        }
        for key, where in sorted(refs.items())
    ]


def checks(conn: sqlite3.Connection) -> dict:
    """Unknown trip keys, and each trip's tagged rows next to its items."""
    trips = _load(conn)
    tagged = []
    for t in _by_start(trips):
        g = t["tagged"]
        if not (g["row_count"] or g["posted_cents"] or g["settled_to_us_cents"]):
            continue
        tagged.append({"key": t["key"], "name": t["name"], **g})
    return {"trip_count": len(trips), "unknown": unknown_trips(conn), "tagged": tagged}
