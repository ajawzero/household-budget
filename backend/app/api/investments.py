"""Investments screen (PLAN 6.2, 8.2). Admin and member.

GET /api/investments/holdings?group_by=account|class   holdings valued today
GET /api/investments/allocation                         by asset class and account
GET /api/investments/history?range=YTD                  daily value, statement
                                                        points, estimated segments
GET /api/investments/flows                              money in, market, fees and
                                                        dividends this year (flows.py)
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from app import config
from app.api.deps import SIGNED_IN, Db
from app.api.market_models import (
    AllocationOut,
    HoldingsOut,
    InvestmentFlowsOut,
    InvestmentsHistoryOut,
)
from app.api.models import InvestmentRangeKey
from app.services import flows, investments
from app.services.networth import DEFAULT_RANGE

router = APIRouter(prefix="/api/investments", dependencies=SIGNED_IN)


@router.get("/holdings", response_model=HoldingsOut)
def get_holdings(
    conn: Db, group_by: Annotated[Literal["account", "class"], Query()] = "account"
) -> dict:
    return investments.holdings_page(conn, group_by)


@router.get("/allocation", response_model=AllocationOut)
def get_allocation(conn: Db) -> dict:
    return investments.allocation(conn)


@router.get("/history", response_model=InvestmentsHistoryOut)
def get_history(conn: Db, range: Annotated[InvestmentRangeKey, Query()] = DEFAULT_RANGE) -> dict:
    return investments.investments_history(conn, range)


@router.get("/flows", response_model=InvestmentFlowsOut)
def get_flows(conn: Db) -> dict:
    return flows.flows_page(conn, config.household_today())
