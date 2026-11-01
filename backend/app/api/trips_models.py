"""Response models for GET /api/trips and GET /api/trips/{key}
(docs/plans/phase-7.md section 4), the Dashboard's next-trip line and the
Checks trips part.

Money is integer cents. Points are integer micro-units (1 point = 1,000,000).
Dates are ISO `YYYY-MM-DD`; a trip has a start and end date, or a month
(`YYYY-MM`) alone. The app never fills in a missing date.

Format 1.4: an item can carry `due_on` (a booked payment still to come, on its
card) and `maybe` (a cost not booked, in no total). What's still open lists the
due payments.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.api.rewards_models import Out

TripStatus = Literal["planning", "booked", "in_progress", "complete", "final"]
ItemStatus = Literal["posted", "booked", "estimate", "tbd", "excluded"]
Tender = Literal["cash", "points", "free_night", "points_credit", "covered"]
ItemKind = Literal[
    "flight",
    "lodging",
    "rental_car",
    "gas",
    "food",
    "activities",
    "tickets",
    "shopping",
    "insurance",
    "parking",
    "transit",
    "cash",
    "supplies",
    "fees",
    "other",
]


class TripPerson(Out):
    key: str
    name: str


class TripItemOut(Out):
    key: str
    kind: ItemKind
    label: str
    status: ItemStatus
    tender: Tender
    amount_cents: int | None = Field(
        description="Cash for the whole household. For points and free nights the taxes and "
        "fees; for a points credit the size of the credit."
    )
    estimate_low_cents: int | None
    estimate_high_cents: int | None
    points_micro: int | None
    program_key: str | None
    program_name: str | None = Field(description="From Rewards when it has the program.")
    account_name: str | None
    person_name: str | None
    note: str | None
    due_on: str | None = Field(
        description="1.4: the day a booked amount is charged or due, on the item's card "
        "(account_name). Booked items only; gone once the charge posts."
    )
    maybe: bool = Field(
        description="1.4: a cost not booked that may be added. Estimate and TBD items only. "
        "Never in a total or a tile; still open."
    )
    cash_cents: int = Field(
        description="What this item adds to cash so far: posted or booked cash and cash fees, "
        "minus a points credit. 0 for covered, excluded, estimate and TBD items."
    )


class TripIouOut(Out):
    key: str
    direction: Literal["owed_to_us", "we_owe"]
    counterparty: str = Field(description="A role, like relative. Never a name.")
    amount_cents: int
    status: Literal["open", "settled", "waived"]
    settled_on: str | None
    item_key: str | None
    item_label: str | None
    note: str | None


class PointsUsed(Out):
    program_key: str | None
    program_name: str | None
    points_micro: int


class KindCents(Out):
    flight: int
    lodging: int
    food: int
    other: int


class TripTotals(Out):
    cash_cents: int = Field(
        description="Posted and booked cash and cash fees, less points credits. Covered and "
        "excluded items do not count."
    )
    estimate_low_cents: int = Field(description="Estimate items, covered ones left out.")
    estimate_high_cents: int
    tbd_count: int
    owed_to_us_cents: int = Field(description="IOUs owed to us, open or settled.")
    we_owe_cents: int = Field(description="IOUs we owe, open or settled.")
    our_net_cents: int = Field(description="cash - owed to us + we owe. Waived counts nothing.")
    points: list[PointsUsed] = Field(description="Points on posted and booked items.")
    by_kind: KindCents = Field(description="cash_cents split for the chart. Adds up to it.")


class TaggedCheck(Out):
    row_count: int = Field(description="Tagged rows on the Transactions page (hidden left out).")
    out_cents: int = Field(
        description="Tagged money out, net of refunds. Transfers and hidden rows left out."
    )
    posted_cents: int = Field(description="Posted items' cash, less posted points credits.")
    out_differs: bool = Field(description="More than $1 apart.")
    deposits_cents: int = Field(description="Tagged deposits (above zero, not refunds).")
    settled_to_us_cents: int = Field(description="Settled IOUs owed to us.")
    deposits_differ: bool = Field(description="More than $1 apart.")


class TripOut(Out):
    key: str
    name: str
    status: TripStatus
    start_date: str | None
    end_date: str | None
    month: str | None = Field(description="YYYY-MM when the trip has no dates.")
    people: list[TripPerson]
    guests: int | None = Field(description="People outside the household. A count, no names.")
    vehicle: str | None
    note: str | None
    estimate_low_cents: int | None = Field(description='The estimate before the trip ("est. was").')
    estimate_high_cents: int | None
    versus_estimate: Literal["under", "within", "over"] | None = Field(
        description="Complete and final trips with an estimate: total actual against it."
    )
    source_run_id: str = Field(description="The run this trip came from.")
    source_run_date: str | None
    items: list[TripItemOut]
    ious: list[TripIouOut]
    totals: TripTotals
    tagged: TaggedCheck


class OpenIou(TripIouOut):
    trip_key: str
    trip_name: str


class OpenItem(TripItemOut):
    trip_key: str
    trip_name: str


class OpenItems(Out):
    collect: list[OpenIou] = Field(description="Open IOUs owed to us.")
    pay: list[OpenIou] = Field(description="Open IOUs we owe.")
    due: list[OpenItem] = Field(
        description="1.4: booked items with a due date that add cash (not covered, not a "
        "points credit, not $0), by due date. A past one stays until the charge posts."
    )
    items: list[OpenItem] = Field(
        description="Estimate and TBD items, maybe ones included, covered ones left out."
    )


class TripTiles(Out):
    booked_cents: int = Field(description="Cash so far, every trip in scope.")
    estimate_low_cents: int
    estimate_high_cents: int
    tbd_count: int
    points: list[PointsUsed]
    collect_cents: int
    collect_count: int
    pay_cents: int
    pay_count: int


class SummaryRow(Out):
    key: str
    name: str
    status: TripStatus
    start_date: str | None
    end_date: str | None
    month: str | None
    people: list[TripPerson]
    guests: int | None
    total_cents: int = Field(description="Cash so far (total actual once complete).")
    estimate_low_cents: int
    estimate_high_cents: int
    tbd_count: int
    flight_cents: int
    lodging_cents: int
    food_cents: int
    other_cents: int = Field(description="Everything else. The four add up to total_cents.")


class TripSummary(Out):
    rows: list[SummaryRow] = Field(description="Trips in scope, by start.")
    booked_cents: int
    estimate_low_cents: int
    estimate_high_cents: int
    tbd_count: int


class TripsOut(Out):
    today: str
    from_year: int = Field(
        description="Tiles, summary and completed trips cover trips ending in this year or later."
    )
    source_run_id: str | None = Field(description="The newest run among the trips.")
    source_run_date: str | None
    trip_count: int = Field(description="Every trip in the app, older ones included.")
    tiles: TripTiles
    active: list[TripOut] = Field(description="Planning, booked and in progress, by start.")
    open_items: OpenItems
    summary: TripSummary
    completed: list[TripOut] = Field(description="Complete and final, newest first.")
    older: list[TripOut] = Field(description="Trips ending before from_year, newest first.")


class NextTrip(Out):
    key: str
    name: str
    status: Literal["planning", "booked", "in_progress"]
    start_date: str | None
    month: str | None
    cash_cents: int
    estimate_low_cents: int
    estimate_high_cents: int
    tbd_count: int


class UnknownTrip(Out):
    key: str
    status: Literal["removed", "not_sent"] = Field(
        description="removed: a run sent it and a later run removed it."
    )
    rows: int = Field(description="Imported rows with this trip_key.")
    plans: int
    flags: int
    redemptions: int


class TaggedLine(TaggedCheck):
    key: str
    name: str


class TripsCheck(Out):
    trip_count: int
    unknown: list[UnknownTrip] = Field(description="Trip keys the app has no trip for.")
    tagged: list[TaggedLine] = Field(
        description="Trips with tagged rows, posted items or settled IOUs owed to us."
    )
