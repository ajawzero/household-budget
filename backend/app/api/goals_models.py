"""Response models for the Goals page (format 1.4 goals, docs/plans/format-14.md section 7).

Money is integer cents, signed like net worth (assets positive). Dates are YYYY-MM-DD.
"""

from __future__ import annotations

from pydantic import Field

from app.api.models import EstimateOut, Out
from app.importer.models import GoalKind


class GoalAccountOut(Out):
    account_key: str
    display_name: str
    balance_cents: int | None = Field(
        description="Today's balance, estimates included. None: no balance yet, or a card "
        "tracked by payments."
    )
    as_of: str | None
    stale: bool
    missing: bool = Field(description="No balance yet. Now leaves it out.")
    estimate: EstimateOut | None = Field(
        description="Set when balance_cents is a computed estimate (est. badge). Never stored."
    )


class GoalOut(Out):
    key: str
    name: str
    kind: GoalKind
    person_name: str | None = Field(description="College goals: the child's display name.")
    start_year: int | None = Field(
        description="College goals only. Shown on the Goals page only: never on the "
        "Dashboard or in the AI facts."
    )
    target_date: str | None = Field(description="Save up goals only, when the chat gives one.")
    note: str | None
    target_cents: int = Field(
        description="The round target, or the total college cost in today's dollars."
    )
    now_cents: int | None = Field(
        description="Sum of the accounts' balances today. None when no account has a balance."
    )
    to_go_cents: int | None = Field(
        description="target - now, never below 0. None when now is None."
    )
    reached: bool = Field(description="now is at or above the target.")
    as_of: str | None = Field(description="The oldest balance date among the accounts with one.")
    stale: bool = Field(description="A balance in now is past its next expected statement.")
    accounts: list[GoalAccountOut] = Field(description="In the chat's order.")


class GoalsOut(Out):
    goals: list[GoalOut] = Field(description="By key, from the newest monthly run that has goals.")
    source_run_id: str | None = Field(description="That run. None before the first one.")
