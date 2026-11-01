"""Run ids, run order and statement version precedence (PLAN 4.1, 4.9).

A run id is YYYY-MM-DD for the first run of a day, then YYYY-MM-DD-2, -3, ...
Run order is (date, seq) as integers, so -10 sorts after -2.

Which version of a statement wins:
1. the latest correction run, in run order, that includes it;
2. if no correction run includes it, the earliest run, in run order, that includes it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta

MAX_DAYS_AHEAD = 1
_RUN_ID = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2})(?:-([1-9][0-9]*))?")


class RunIdError(ValueError):
    """Raised for a malformed run id. rule_id is 'run-id'."""

    rule_id = "run-id"


@dataclass(frozen=True, order=True)
class RunId:
    day: date
    seq: int
    text: str = field(compare=False)

    def __str__(self) -> str:
        return self.text


def parse_run_id(text: str) -> RunId:
    """Parse 'YYYY-MM-DD' (seq 1) or 'YYYY-MM-DD-N' with N >= 2 and no leading zero."""
    m = _RUN_ID.fullmatch(text) if isinstance(text, str) else None
    if m is None:
        raise RunIdError("Run id must be YYYY-MM-DD or YYYY-MM-DD-N with N of 2 or more.")
    try:
        day = date.fromisoformat(m[1])
    except ValueError:
        raise RunIdError("Run id must start with a real date.") from None
    seq = int(m[2]) if m[2] else 1
    if m[2] is not None and seq < 2:
        raise RunIdError("A same-day run number starts at 2.")
    return RunId(day, seq, text)


def is_run_id(text: str) -> bool:
    try:
        parse_run_id(text)
    except RunIdError:
        return False
    return True


def run_order_key(run_id: str) -> tuple[int, int, int, int]:
    """Sort key (year, month, day, seq) as integers."""
    r = parse_run_id(run_id)
    return (r.day.year, r.day.month, r.day.day, r.seq)


def sort_runs(run_ids: Iterable[str]) -> list[str]:
    return sorted(run_ids, key=run_order_key)


def is_future(run_id: str, today: date, max_days_ahead: int = MAX_DAYS_AHEAD) -> bool:
    """True when the run date is more than max_days_ahead after today."""
    return parse_run_id(run_id).day > today + timedelta(days=max_days_ahead)


def winning_run(runs: Iterable[tuple[str, str]]) -> str:
    """Pick the run whose version of one statement wins.

    runs: (run_id, mode) for every run that includes the statement.
    Depends only on run ids and modes, so arrival order never matters.
    """
    items = list(runs)
    if not items:
        raise ValueError("No runs include this statement.")
    corrections = [r for r, mode in items if mode == "correction"]
    if corrections:
        return max(corrections, key=run_order_key)
    return min((r for r, _ in items), key=run_order_key)
