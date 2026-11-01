"""Format 1.3 rewards (state): the reader, newest monthly run wins whole, the
fingerprint, and the samples (docs/plans/phase-7.md sections 2.1 and 7)."""

from __future__ import annotations

import json
import sqlite3

from bad_cases import MONTHLY, SAMPLES, read_json

from app.config import Settings
from app.db.connect import connect, write_connection
from app.importer.models import Rewards
from app.services.fingerprint import fingerprint
from app.state import readers


def _runs(settings: Settings) -> list[readers.StateRun]:
    conn = connect(settings.db_path)
    try:
        return readers.monthly_runs(conn)
    finally:
        conn.close()


def _add_run(conn: sqlite3.Connection, run_id: str, mode: str, **sections: object) -> None:
    """A stored run with only the given sections (like test_dashboard's helper)."""
    conn.execute(
        """INSERT INTO import_runs (run_id, run_date, run_seq, mode, status, started_at,
               schema_version)
           VALUES (?, ?, 1, ?, 'imported', '2026-10-20T00:00:00+00:00', '1.3')""",
        (run_id, run_id[:10], mode),
    )
    for name, value in sections.items():
        conn.execute(
            "INSERT INTO run_sections (run_id, section, sha256, json) VALUES (?, ?, ?, ?)",
            (run_id, name, f"{run_id}-{name}".ljust(64, "0")[:64], json.dumps(value)),
        )


# ---- samples ----


def test_the_monthly_sample_carries_rewards() -> None:
    snap = read_json(SAMPLES / MONTHLY / "snapshot.json")
    assert snap["schema_version"] == "1.4"
    r = Rewards.model_validate(snap["rewards"])
    programs = {p.key: p for p in r.programs}
    assert set(programs) == {
        "harbor-miles",
        "lantern-hotels",
        "pine-cash-back",
        "shared-vacation-club",
        "summit-points",
    }
    assert {p.group for p in r.programs} == {"primary", "secondary"}
    assert programs["summit-points"].source == "statement"
    assert programs["harbor-miles"].source == "manual"
    assert (
        programs["pine-cash-back"].kind == "cash_back"
        and programs["pine-cash-back"].cpp_low is None
    )
    club = programs["shared-vacation-club"]
    assert club.kind == "timeshare" and len(club.allocations or []) == 2
    assert any(a.pulled_forward for a in club.allocations or [])
    kinds = sorted(b.kind for b in r.benefits)
    assert kinds.count("credit") >= 5 and {"earning_cap", "status_perk", "free_night"} <= set(kinds)
    cadences = {b.cadence for b in r.benefits if b.kind == "credit"}
    assert {"monthly", "quarterly", "semi_annual"} <= cadences
    monthly = next(b for b in r.benefits if b.cadence == "monthly" and b.cycles and not b.auto)
    assert any(c.used for c in monthly.cycles or [])
    december = [c for c in monthly.cycles or [] if c.start.endswith("-12-01")]
    assert december and december[0].amount is not None  # a bigger December cycle
    assert any(b.auto for b in r.benefits)
    assert any(b.enrolled is False and b.enroll_by for b in r.benefits)
    assert any(
        b.kind == "credit" and b.ends_on and b.ends_on.startswith("2026") for b in r.benefits
    )
    fuel = next(b for b in r.benefits if b.kind == "earning_cap")
    assert fuel.category_keys == ["fuel"]
    assert sorted(ms.status for ms in r.milestones) == ["met", "open"]
    assert len(r.routing) == 6
    ready = [x for x in r.redemptions if x.status == "ready"]
    assert [x.trip_key for x in ready] == ["ski-weekend"]
    assert r.highlights


def test_backfill_and_correction_samples_carry_no_rewards() -> None:
    for run in sorted(p for p in SAMPLES.iterdir() if p.is_dir()):
        snap = read_json(run / "snapshot.json")
        assert ("rewards" in snap) == (snap["mode"] == "monthly"), run.name


# ---- the reader ----


def test_the_reader_returns_the_monthly_runs_rewards(samples: Settings) -> None:
    runs = _runs(samples)
    r = readers.rewards(runs)
    assert r is not None
    assert readers.section_source(runs, "rewards") == MONTHLY
    assert r == Rewards.model_validate(read_json(SAMPLES / MONTHLY / "snapshot.json")["rewards"])


def test_no_rewards_before_a_13_run(db: sqlite3.Connection) -> None:
    assert readers.rewards(readers.monthly_runs(db)) is None


def test_the_newest_monthly_run_wins_whole(samples: Settings) -> None:
    """Items left out of the newest run count as ended: the reader never merges."""
    later = {
        "programs": [
            {"key": "summit-points", "name": "Summit Points", "kind": "points", "group": "primary"}
        ],
        "benefits": [],
        "milestones": [],
        "routing": [],
        "redemptions": [],
    }
    with write_connection(samples.db_path) as w:
        _add_run(w, "2026-10-20", "monthly", rewards=later)
        _add_run(w, "2026-10-21", "backfill", rewards={**later, "programs": []})  # ignored
    runs = _runs(samples)
    r = readers.rewards(runs)
    assert r is not None and [p.key for p in r.programs] == ["summit-points"]
    assert r.benefits == [] and r.highlights is None
    assert readers.section_source(runs, "rewards") == "2026-10-20"


def test_the_fingerprint_follows_the_newest_rewards(samples: Settings) -> None:
    conn = connect(samples.db_path)
    before = fingerprint(conn)
    conn.close()
    empty = {"programs": [], "benefits": [], "milestones": [], "routing": [], "redemptions": []}
    with write_connection(samples.db_path) as w:
        _add_run(w, "2026-10-20", "monthly", rewards=empty)
    conn = connect(samples.db_path)
    after = fingerprint(conn)
    conn.close()
    assert after != before
