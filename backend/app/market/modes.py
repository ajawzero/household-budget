"""Price mode per holding (PLAN 6.2): api, constant or statement.

Rules, first match wins:
1. An override in `symbol_map` for (account_key, holding_key). Overrides are
   per account, so a collective trust is never priced as a public fund just
   because another account holds a fund with the same name.
2. `constant` (price 1.00): a core, sweep or money market position. That is a
   symbol on the fixed list below, an asset class of cash or money market, or
   a holding key like `cash`, `core` or `sweep`.
3. `api`: the statement printed a symbol (a symbol column, or a
   ticker in parentheses). The chat never infers a ticker from a fund name.
4. `statement`: no printed symbol. That covers 401k collective trusts and the
   private fund. The value moves only with the next statement.

Constant and statement holdings add no change to a price estimate.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

MODES = ("api", "constant", "statement")

# Core, sweep and money market funds that hold 1.00 a share.
CONSTANT_SYMBOLS = frozenset(
    {
        # Core and money market funds
        "SPAXX",
        "FDRXX",
        "FZFXX",
        "SPRXX",
        "FZDXX",
        "FDLXX",
        "FCASH",
        "CORE",
        # Another broker's funds
        "SWVXX",
        "SNVXX",
        "SNOXX",
        "SNSXX",
        "SWGXX",
        "SNAXX",
        "SWTXX",
        # Vanguard
        "VMFXX",
        "VMRXX",
        "VUSXX",
        "VYFXX",
        # Others
        "TTTXX",
        "QACDS",
        "CASH",
    }
)
CONSTANT_CLASSES = frozenset({"cash", "money-market", "sweep", "core"})
CONSTANT_KEYS = frozenset(
    {"cash", "core", "core-position", "sweep", "bank-sweep", "money-market", "cash-sweep"}
)


@dataclass(frozen=True)
class Mode:
    mode: str  # api, constant or statement
    symbol: str | None  # the ticker to price with (api only)
    source: str  # rule or override


def rule_mode(holding_key: str, symbol: str | None, asset_class: str | None) -> Mode:
    """The default mode from the rules (no override)."""
    if (
        (symbol is not None and symbol in CONSTANT_SYMBOLS)
        or (asset_class is not None and asset_class in CONSTANT_CLASSES)
        or holding_key in CONSTANT_KEYS
    ):
        return Mode("constant", None, "rule")
    if symbol:
        return Mode("api", symbol, "rule")
    return Mode("statement", None, "rule")


def overrides(conn: sqlite3.Connection) -> dict[tuple[str, str], sqlite3.Row]:
    return {
        (r["account_key"], r["holding_key"]): r for r in conn.execute("SELECT * FROM symbol_map")
    }


def resolve(
    holding_key: str,
    symbol: str | None,
    asset_class: str | None,
    override: sqlite3.Row | dict | None,
) -> Mode:
    """The mode in use: the override when there is one, else the rules.

    An api override with no symbol of its own uses the printed symbol. With
    neither, the holding falls back to statement mode.
    """
    if override is None:
        return rule_mode(holding_key, symbol, asset_class)
    mode = override["price_mode"]
    if mode == "api":
        ticker = override["symbol"] or symbol
        return Mode("api", ticker, "override") if ticker else Mode("statement", None, "override")
    return Mode(mode, None, "override")


def mode_map(conn: sqlite3.Connection) -> dict[tuple[str, str], Mode]:
    """(account_key, holding_key) -> Mode for every holding in a current statement.

    A holding's printed symbol and class come from its newest statement.
    """
    over = overrides(conn)
    out: dict[tuple[str, str], Mode] = {}
    for r in conn.execute(
        """SELECT h.account_key, h.holding_key, h.symbol, h.asset_class
           FROM holdings h JOIN statements s
             ON s.statement_key = h.statement_key AND s.is_current = 1
           ORDER BY h.as_of"""
    ):
        k = (r["account_key"], r["holding_key"])
        out[k] = resolve(r["holding_key"], r["symbol"], r["asset_class"], over.get(k))
    return out


def api_symbols(conn: sqlite3.Connection) -> dict[str, str]:
    """Ticker -> the earliest statement date it is needed from, over api holdings."""
    modes = mode_map(conn)
    out: dict[str, str] = {}
    for r in conn.execute(
        """SELECT h.account_key, h.holding_key, min(h.as_of) AS first
           FROM holdings h JOIN statements s
             ON s.statement_key = h.statement_key AND s.is_current = 1
           GROUP BY h.account_key, h.holding_key"""
    ):
        m = modes.get((r["account_key"], r["holding_key"]))
        if m is None or m.mode != "api" or m.symbol is None:
            continue
        if m.symbol not in out or r["first"] < out[m.symbol]:
            out[m.symbol] = r["first"]
    return dict(sorted(out.items()))
