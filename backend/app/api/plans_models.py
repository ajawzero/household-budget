"""Request and response models for the trip planner (docs/plans/trip-planner.md).

Money in the app's own numbers is integer cents. The plan's own prices are
whole US dollars, as Claude gave them. Links come only from the research
call's search results (`links`); the plan names them by source id.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.ai.planner import PlanOut
from app.api.rewards_models import In, Out

PlanStatus = Literal["running", "ready", "failed"]
PlanStep = Literal["searching", "writing"]
CheckKind = Literal[
    "points_not_yours",
    "free_night_expires",
    "credit_use_by",
    "not_enough_points",
    "no_lodging",
    "no_way_home",
    "no_travel",
]


class FollowUpIn(In):
    text: str = Field(min_length=1, max_length=1000, description="What to change.")


class TripLinkIn(In):
    trip_key: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_-]{0,63}$",
        description="An imported trip, or null to unlink.",
    )


class Person(Out):
    key: str
    name: str


class PlanStarted(Out):
    plan_id: str
    status: Literal["running"]


class PlanSummary(Out):
    plan_id: str
    title: str
    status: PlanStatus
    start_date: str | None
    end_date: str | None
    month: str | None
    nights: int
    updated_at: str
    created_by_name: str | None
    trip_key: str | None
    trip_name: str | None
    versions: int
    low_cents: int | None = Field(description="The newest version's cost range.")
    high_cents: int | None


class TripPlansOut(Out):
    plans: list[PlanSummary]
    running_plan_id: str | None = Field(description="The plan being made now, if any.")
    available: bool = Field(description="AI is on, with a key, and not paused.")
    estimate_cents: int = Field(description="About what a new plan costs, before it starts.")
    follow_up_limit: int
    model: str
    people: list[Person] = Field(description="Who can go: the household's adults and children.")


class RequestOut(Out):
    where: str
    start_date: str | None
    end_date: str | None
    month: str | None
    nights: int
    people: list[Person]
    guests: int
    home_airport: str | None
    interests: list[str]
    note: str
    pace: str
    lean: str


class PointsValue(Out):
    value_cents_per_point: str | None = Field(
        description="(cash price less taxes) / points, in cents. Null for a benefit."
    )
    range: Literal["below", "within", "above"] | None = Field(
        description="Against the program's usual value per point."
    )
    yours: bool = Field(description="The program is one of the household's.")


class Balance(Out):
    program: str
    needed: int
    have: int | str | None
    enough: bool | None


class Night(Out):
    night: int
    date: str | None
    lodging: str | None


class Check(Out):
    kind: CheckKind
    text: str


class KindCost(Out):
    kind: str
    low_cents: int
    high_cents: int


class Costs(Out):
    by_kind: list[KindCost]
    low_cents: int
    high_cents: int


class Derived(Out):
    points: list[PointsValue] = Field(description="One per points_plan entry, in order.")
    balances: list[Balance]
    nights: list[Night]
    checks: list[Check]
    costs: Costs


class Link(Out):
    id: str
    title: str
    url: str
    site: str


class Version(Out):
    version: int
    created_at: str
    follow_up: str | None = Field(description="The change asked for. Null for the first.")
    plan: PlanOut
    derived: Derived
    links: list[Link]


class PlanView(Out):
    plan_id: str
    title: str
    status: PlanStatus
    step: PlanStep | None = Field(description="While running: searching the web or writing.")
    created_at: str
    updated_at: str
    created_by_name: str | None
    model: str
    request: RequestOut
    trip_key: str | None
    trip_name: str | None
    versions: list[Version]
    notice: str | None = Field(description="Why the last turn ended without a plan.")
    follow_ups_left: int
    cost_cents: int
    web_searches: int
