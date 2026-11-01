"""Asset class picks (Settings > Prices): the class an admin chose for a holding.

A pick wins over the class on the statement for grouping only: Allocation,
Holdings by class, the account drawer and Ask, and the cash and fund-of-funds
rules that follow the class. The price mode keeps the statement's class
(modes.py), so a pick never changes a value. Picks are per (account, holding),
like price overrides, and survive `cli reapply` and new statements.
"""

from __future__ import annotations

import sqlite3

# The classes an admin can pick, in the order the picker lists them.
CLASS_CHOICES = (
    "us-stock",
    "intl-stock",
    "bond",
    "cash",
    "target-date",
    "balanced",
    "real-estate",
    "other",
)


def class_picks(conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
    """(account_key, holding_key) -> the picked class."""
    return {
        (r["account_key"], r["holding_key"]): r["asset_class"]
        for r in conn.execute("SELECT account_key, holding_key, asset_class FROM holding_classes")
    }
