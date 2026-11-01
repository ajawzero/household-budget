"""Stable keys for imported rows (PLAN 4.5).

- statement_key = account_key + ":" + period_end
- desc_norm: uppercase, collapse whitespace, drop a `*` or `#` tail that holds
  a digit and no space, drop masked references, trim to 32 characters (the
  order in PLAN 4.5).
- occurrence_n: 1, 2, ... among rows of one statement with the same
  (txn_date, amount_cents, desc_norm), in CSV order.
- txn_key = sha256(statement_key|txn_date|amount_cents|desc_norm|occurrence_n)
- paycheck_key = sha256(person|pay_date|kind|seq)
- utility_bill_key = utility_key + ":" + period_end (format 1.2)

Post date and merchant are left out of txn_key on purpose.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence

DESC_NORM_MAX = 32

_WS = re.compile(r"\s+")
# A masked reference as its own word: 2 or more mask characters then 2 to 4
# digits (XX1234, ****1234, ••12), or 4 or more mask characters (XXXX). Words
# such as XX or XXX stay.
_MASKED = re.compile(r"(?:(?<=\s)|^)(?:[X*•#]{2,}[0-9]{2,4}|[X*•#]{4,})(?=\s|$)")


def _sha(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def statement_key(account_key: str, period_end: str) -> str:
    return f"{account_key}:{period_end}"


def utility_bill_key(utility_key: str, period_end: str) -> str:
    """One utility bill: the utility and the end of its service period."""
    return f"{utility_key}:{period_end}"


def csv_path(account_key: str, period_end: str) -> str:
    """The CSV path for a statement inside a run folder."""
    return f"transactions/{account_key}-{period_end}.csv"


_CSV_NAME = re.compile(r"^transactions/(?P<account>.+)-(?P<end>[0-9]{4}-[0-9]{2}-[0-9]{2})\.csv$")


def parse_csv_path(path: str) -> tuple[str, str] | None:
    """'transactions/card-a-2026-09-03.csv' -> ('card-a', '2026-09-03'), else None."""
    m = _CSV_NAME.match(path)
    return (m["account"], m["end"]) if m else None


def _drop_tail(s: str) -> str:
    """Drop the last `*` or `#` segment if it holds a digit and no space.

    A run of mask characters (`****5678`) is a masked reference, not a tail.
    """
    cut = max(s.rfind("*"), s.rfind("#"))
    if cut < 0 or (cut > 0 and s[cut - 1] in "*#•"):
        return s
    tail = s[cut + 1 :]
    if " " not in tail and any(ch.isdigit() for ch in tail):
        return s[:cut]
    return s


def desc_norm(description: str) -> str:
    """Normalize a description for keys. 'SQ *MAPLE CAFE #12' -> 'SQ *MAPLE CAFE'."""
    s = _WS.sub(" ", description.upper()).strip()
    s = _drop_tail(s).strip()
    s = _WS.sub(" ", _MASKED.sub(" ", s)).strip()
    return s[:DESC_NORM_MAX].rstrip()


def assign_occurrences(rows: Iterable[tuple[str, int, str]]) -> list[int]:
    """Given (txn_date, amount_cents, desc_norm) per row in CSV order, return occurrence_n."""
    seen: dict[tuple[str, int, str], int] = {}
    out = []
    for ident in rows:
        seen[ident] = seen.get(ident, 0) + 1
        out.append(seen[ident])
    return out


def txn_key(stmt_key: str, txn_date: str, amount_cents: int, norm: str, occurrence_n: int) -> str:
    return _sha(stmt_key, txn_date, amount_cents, norm, occurrence_n)


def txn_keys(stmt_key: str, rows: Sequence[tuple[str, int, str]]) -> list[str]:
    """txn_key for every row of one statement. rows: (txn_date, amount_cents, description)."""
    idents = [(d, a, desc_norm(desc)) for d, a, desc in rows]
    occ = assign_occurrences(idents)
    return [txn_key(stmt_key, d, a, n, o) for (d, a, n), o in zip(idents, occ, strict=True)]


def paycheck_key(person: str, pay_date: str, kind: str, seq: int) -> str:
    return _sha(person, pay_date, kind, seq)


def contribution_key(person: str, account_key: str, kind: str, source: str, date: str) -> str:
    return _sha(person, account_key, kind, source, date)
