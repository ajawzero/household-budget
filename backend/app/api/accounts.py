"""Accounts page and account detail (PLAN 8.2).

GET /api/accounts?range=YTD           groups, rows, summary and notes
GET /api/accounts/{account_key}?range=  one account: daily balances, statements,
                                        recent rows, holdings and terms
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import SIGNED_IN, Db
from app.api.models import AccountDetailOut, AccountsOut, RangeKey
from app.services.networth import DEFAULT_RANGE, account_detail, accounts_page

router = APIRouter(prefix="/api", dependencies=SIGNED_IN)

NO_ACCOUNT = "No such account."
RangeQuery = Annotated[RangeKey, Query()]


@router.get("/accounts", response_model=AccountsOut)
def get_accounts(conn: Db, range: RangeQuery = DEFAULT_RANGE) -> dict:
    return accounts_page(conn, range)


@router.get("/accounts/{account_key}", response_model=AccountDetailOut)
def get_account(account_key: str, conn: Db, range: RangeQuery = DEFAULT_RANGE) -> dict:
    try:
        return account_detail(conn, account_key, range)
    except KeyError:
        raise HTTPException(status_code=404, detail=NO_ACCOUNT) from None
