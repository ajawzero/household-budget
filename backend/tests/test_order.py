"""Run ids, run order and statement version precedence (PLAN 4.9)."""

from __future__ import annotations

from datetime import date

import pytest

from app.importer.order import (
    RunIdError,
    is_future,
    is_run_id,
    parse_run_id,
    run_order_key,
    sort_runs,
    winning_run,
)


def test_parse() -> None:
    r = parse_run_id("2026-10-21")
    assert (r.day, r.seq, str(r)) == (date(2026, 10, 21), 1, "2026-10-21")
    assert parse_run_id("2026-10-21-2").seq == 2
    assert parse_run_id("2026-10-21-10").seq == 10


@pytest.mark.parametrize(
    "bad",
    [
        "2026-10-21-1",
        "2026-10-21-0",
        "2026-10-21-02",
        "2026-13-01",
        "2026-02-30",
        "20261021",
        "2026-10-21-x",
        "2026-10-21-",
        " 2026-10-21",
        "",
        "2026-10-21.partial",
    ],
)
def test_bad_run_ids(bad: str) -> None:
    with pytest.raises(RunIdError):
        parse_run_id(bad)
    assert not is_run_id(bad)


def test_order_is_numeric() -> None:
    ids = ["2026-10-21-10", "2026-10-21", "2026-10-21-2", "2026-09-30", "2026-10-21-3"]
    assert sort_runs(ids) == [
        "2026-09-30",
        "2026-10-21",
        "2026-10-21-2",
        "2026-10-21-3",
        "2026-10-21-10",
    ]
    assert run_order_key("2026-10-21-10") == (2026, 10, 21, 10)
    assert parse_run_id("2026-10-21-10") > parse_run_id("2026-10-21-2")


def test_future() -> None:
    today = date(2026, 10, 20)
    assert not is_future("2026-10-21", today)
    assert not is_future("2026-10-21-5", today)
    assert is_future("2026-10-22", today)


@pytest.mark.parametrize(
    "runs,winner",
    [
        ([("2026-10-06", "backfill")], "2026-10-06"),
        ([("2026-10-12", "monthly"), ("2026-10-06", "backfill")], "2026-10-06"),
        ([("2026-11-12", "monthly"), ("2026-10-12", "monthly")], "2026-10-12"),
        ([("2026-10-12", "monthly"), ("2026-10-14", "correction")], "2026-10-14"),
        # a correction that arrives before the original still wins
        ([("2026-10-14", "correction"), ("2026-10-12", "monthly")], "2026-10-14"),
        (
            [
                ("2026-10-14", "correction"),
                ("2026-10-14-2", "correction"),
                ("2026-11-12", "monthly"),
            ],
            "2026-10-14-2",
        ),
        ([("2026-10-14-10", "correction"), ("2026-10-14-2", "correction")], "2026-10-14-10"),
    ],
)
def test_winning_run(runs: list[tuple[str, str]], winner: str) -> None:
    assert winning_run(runs) == winner
    assert winning_run(reversed(runs)) == winner


def test_winning_run_needs_runs() -> None:
    with pytest.raises(ValueError):
        winning_run([])
