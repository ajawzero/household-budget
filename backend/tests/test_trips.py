"""Format 1.3 trips (history): migration 0009, apply (newest run per key, the whole
trip with its items and IOUs), tombstones, reapply, the fingerprint, and the two
1.3 warnings in the app and the checker (docs/plans/phase-7.md sections 2.2, 3, 7)."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

from bad_cases import (
    MONTHLY,
    PRIVATE_RULES_EXAMPLE,
    SAMPLES,
    copy_samples,
    read_json,
    refresh_manifest,
    write_json,
)
from conftest import SAMPLE_RUNS

from app.config import Settings
from app.db.connect import read_connection
from app.db.migrate import migrate
from app.importer.apply import IMPORTED_TABLES
from app.importer.models import CURRENT_VERSION
from app.importer.runner import IMPORTED, import_folder, reapply
from app.services.fingerprint import TABLES, fingerprint

REPO = Path(__file__).resolve().parents[2]
BACKFILL = "2026-10-06-2"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_run", REPO / "schema" / "check_run.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_run"] = mod
    spec.loader.exec_module(mod)
    return mod


cr = _load_checker()


def _import(settings: Settings, base: Path, runs) -> list:
    migrate(settings=settings)
    return [import_folder(base / r, settings=settings) for r in runs]


def _q(settings: Settings, sql: str, *args) -> list[sqlite3.Row]:
    with read_connection(settings.db_path) as conn:
        return conn.execute(sql, args).fetchall()


def _fp(settings: Settings) -> str:
    with read_connection(settings.db_path) as conn:
        return fingerprint(conn)


def _trips(settings: Settings) -> dict[str, dict]:
    """Every trip with its people, items and IOUs, run_id left out."""
    out = {}
    for t in _q(settings, "SELECT * FROM trips ORDER BY trip_key"):
        key = t["trip_key"]
        row = {k: t[k] for k in t.keys() if k != "run_id"}
        row["people"] = [
            r["person_key"]
            for r in _q(
                settings, "SELECT person_key FROM trip_people WHERE trip_key = ? ORDER BY 1", key
            )
        ]
        row["items"] = [
            dict(r)
            for r in _q(
                settings, "SELECT * FROM trip_items WHERE trip_key = ? ORDER BY item_key", key
            )
        ]
        row["ious"] = [
            dict(r)
            for r in _q(
                settings, "SELECT * FROM trip_ious WHERE trip_key = ? ORDER BY iou_key", key
            )
        ]
        out[key] = row
    return out


def _later_run(data: Path, run_id: str, mode: str, trips: list[dict]) -> Path:
    """A small later run that carries only trips (plus what its mode requires). The
    current version: the sample trips carry 1.4 fields (due_on, maybe)."""
    dst = data / run_id
    dst.mkdir()
    snap: dict = {"schema_version": CURRENT_VERSION, "run_id": run_id, "mode": mode}
    if mode == "backfill":
        snap |= {
            "people": [],
            "accounts": [],
            "statements": [],
            "balances": [],
            "holdings": [],
            "income": {"paychecks": []},
        }
    else:
        snap["statements"] = []
    snap["trips"] = trips
    write_json(dst / "snapshot.json", snap)
    manifest = {
        "schema_version": CURRENT_VERSION,
        "run_id": run_id,
        "mode": mode,
        "created_at": f"{run_id[:10]}T18:00:00-05:00",
        "generator": {"skill": "make_samples", "version": "1"},
        "files": [{"path": "snapshot.json", "sha256": "0" * 64, "bytes": 0}],
    }
    write_json(dst / "manifest.json", manifest)
    refresh_manifest(dst)
    return dst


def _sample_trip(key: str) -> dict:
    for run in (BACKFILL, MONTHLY):
        for t in read_json(SAMPLES / run / "snapshot.json").get("trips", []):
            if t["key"] == key and not t.get("deleted"):
                return json.loads(json.dumps(t))
    raise KeyError(key)


# ---- migration and tables ----


def test_migration_adds_the_trip_tables(db: sqlite3.Connection) -> None:
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")}
    assert {"trips", "trip_people", "trip_items", "trip_ious"} <= tables
    for t in ("trips", "trip_people", "trip_items", "trip_ious"):
        sql = db.execute("SELECT sql FROM sqlite_schema WHERE name = ?", (t,)).fetchone()[0]
        assert sql.rstrip().endswith("STRICT")
    indexes = {
        r[0]: r[1]
        for r in db.execute("SELECT name, tbl_name FROM sqlite_schema WHERE type = 'index'")
    }
    assert indexes.get("transactions_trip_key") == "transactions"
    assert IMPORTED_TABLES[:4] == ("trip_ious", "trip_items", "trip_people", "trips")
    assert {"trips", "trip_people", "trip_items", "trip_ious"} <= set(TABLES)


# ---- samples ----


def test_samples_cover_the_plan() -> None:
    back = read_json(SAMPLES / BACKFILL / "snapshot.json")
    monthly = read_json(SAMPLES / MONTHLY / "snapshot.json")
    assert (back["schema_version"], monthly["schema_version"]) == ("1.3", "1.4")
    sent = {t["key"]: t for t in back["trips"]}
    assert set(sent) == {"city-break", "lake-weekend"}
    assert {t["key"]: t.get("deleted", False) for t in monthly["trips"]} == {
        "beach-week": False,
        "city-break": True,  # a tombstone for a trip the backfill run sent
        "ski-weekend": False,
    }
    lake = sent["lake-weekend"]
    assert lake["status"] == "final"
    assert {i["status"] for i in lake["items"]} >= {"posted", "excluded"}
    assert [i["status"] for i in lake["ious"]] == ["waived"]
    ski = _sample_trip("ski-weekend")
    tenders = {i["tender"] for i in ski["items"]}
    assert {"points", "free_night", "points_credit", "cash"} <= tenders
    assert {"estimate", "tbd", "booked"} <= {i["status"] for i in ski["items"]}
    assert [i["status"] for i in ski["ious"]] == ["open"]
    beach = _sample_trip("beach-week")
    assert beach["status"] == "planning" and "month" in beach
    assert {i["status"] for i in beach["items"]} == {"estimate"}


def test_lake_weekend_rows_match_its_posted_cash() -> None:
    """The final trip's tagged card rows equal its posted cash items, item by item."""
    lake = _sample_trip("lake-weekend")
    cash = sorted(
        i["amount"] for i in lake["items"] if i["status"] == "posted" and i["tender"] == "cash"
    )
    tagged = []
    for path in sorted((SAMPLES / BACKFILL / "transactions").glob("*.csv")):
        for line in path.read_text().splitlines()[1:]:
            if ",lake-weekend," in line:
                tagged.append(line.split(",")[2].lstrip("-"))
    assert len(cash) >= 2 and sorted(tagged) == cash


# ---- apply ----


def test_import_fills_the_trip_tables(settings: Settings) -> None:
    results = _import(settings, SAMPLES, SAMPLE_RUNS)
    assert [r.status for r in results] == [IMPORTED] * len(SAMPLE_RUNS)
    trips = _trips(settings)
    assert set(trips) == {"beach-week", "lake-weekend", "ski-weekend"}  # city-break is gone
    runs = {r["trip_key"]: r["run_id"] for r in _q(settings, "SELECT trip_key, run_id FROM trips")}
    assert runs == {"beach-week": MONTHLY, "lake-weekend": BACKFILL, "ski-weekend": MONTHLY}
    lake, ski = trips["lake-weekend"], trips["ski-weekend"]
    assert (lake["start_date"], lake["end_date"], lake["month"]) == (
        "2026-07-03",
        "2026-07-06",
        None,
    )
    assert lake["people"] == ["alex", "jamie", "riley", "sam"] and lake["guests"] == 2
    lodging = next(i for i in lake["items"] if i["item_key"] == "lodging")
    assert (lodging["amount_cents"], lodging["status"], lodging["tender"]) == (
        21_000,
        "posted",
        "cash",
    )
    assert lake["ious"][0]["status"] == "waived" and lake["ious"][0]["item_key"] == "gas"
    flights = next(i for i in ski["items"] if i["tender"] == "points")
    assert flights["points_micro"] == 50_000_000_000 and flights["program_key"] == "harbor-miles"
    tbd = next(i for i in ski["items"] if i["status"] == "tbd")
    assert tbd["amount_cents"] is None and tbd["estimate_low_cents"] is None
    beach = trips["beach-week"]
    assert (beach["start_date"], beach["month"]) == (None, "2027-06")


def test_the_newest_run_replaces_a_trip_whole(settings: Settings, tmp_path: Path) -> None:
    data = copy_samples(tmp_path)
    ski = _sample_trip("ski-weekend")
    ski["status"] = "in_progress"
    ski["items"] = [i for i in ski["items"] if i["key"] != "parking"]
    ski.pop("ious")
    _later_run(data, "2026-10-20", "correction", [ski])
    _import(settings, data, [*SAMPLE_RUNS, "2026-10-20"])
    got = _trips(settings)["ski-weekend"]
    assert got["status"] == "in_progress"
    assert "parking" not in {i["item_key"] for i in got["items"]} and got["ious"] == []
    run = _q(settings, "SELECT run_id FROM trips WHERE trip_key = 'ski-weekend'")[0][0]
    assert run == "2026-10-20"


def test_an_unchanged_trip_keeps_its_run(settings: Settings, tmp_path: Path) -> None:
    data = copy_samples(tmp_path)
    _later_run(data, "2026-10-20", "backfill", [_sample_trip("lake-weekend")])
    _import(settings, data, [*SAMPLE_RUNS, "2026-10-20"])
    run = _q(settings, "SELECT run_id FROM trips WHERE trip_key = 'lake-weekend'")[0][0]
    assert run == BACKFILL


def test_a_tombstone_removes_a_trip_and_its_rows(settings: Settings, tmp_path: Path) -> None:
    data = copy_samples(tmp_path)
    _later_run(data, "2026-10-20", "correction", [{"deleted": True, "key": "ski-weekend"}])
    _import(settings, data, [*SAMPLE_RUNS, "2026-10-20"])
    assert "ski-weekend" not in _trips(settings)
    for table in ("trip_people", "trip_items", "trip_ious"):
        assert _q(settings, f"SELECT 1 FROM {table} WHERE trip_key = 'ski-weekend'") == []


def test_reapply_and_any_arrival_order_give_the_same_trips(settings: Settings) -> None:
    _import(settings, SAMPLES, SAMPLE_RUNS)
    trips, fp = _trips(settings), _fp(settings)
    reapply(settings=settings)
    assert _trips(settings) == trips and _fp(settings) == fp


def test_the_fingerprint_covers_trips(settings: Settings, tmp_path: Path) -> None:
    _import(settings, SAMPLES, SAMPLE_RUNS)
    before = _fp(settings)
    data = copy_samples(tmp_path)
    beach = _sample_trip("beach-week")
    beach["items"][0]["estimate_high"] = "1400.00"
    _later_run(data, "2026-10-20", "correction", [beach])
    assert import_folder(data / "2026-10-20", settings=settings).status == IMPORTED
    assert _fp(settings) != before


# ---- 1.3 warnings, the app and the checker alike ----


def _tagged_row(run: str, trip_key: str) -> str:
    """'<csv>:row <n>.trip_key' of the one row in a sample run tagged with trip_key."""
    found = []
    for path in sorted((SAMPLES / run / "transactions").glob("*.csv")):
        for n, line in enumerate(path.read_text().splitlines()[1:], start=1):
            if f",{trip_key}," in line:
                found.append(f"transactions/{path.name}:row {n}.trip_key")
    assert len(found) == 1, found
    return found[0]


def test_the_samples_warn_on_purpose(settings: Settings) -> None:
    """A stale manual balance, and one row tagged with a trip no run defines."""
    expected = {
        MONTHLY: {
            ("reward-stale", "snapshot.json:rewards.programs[1].as_of"),  # lantern-hotels
            ("trip-unknown", _tagged_row(MONTHLY, "day-trip-sep26")),
        },
    }
    results = {r.run_id: r for r in _import(settings, SAMPLES, SAMPLE_RUNS)}
    for run in SAMPLE_RUNS:
        found = {(w.rule, w.where) for w in results[run].warnings}
        found = {w for w in found if w[0] in ("reward-stale", "trip-unknown")}
        assert found == expected.get(run, set()), run
        problems, warnings = cr.check_run_full(
            SAMPLES / run, private_rules=str(PRIVATE_RULES_EXAMPLE)
        )
        assert problems == []
        assert {w for w in warnings if w[0] in ("reward-stale", "trip-unknown")} == found, run


def test_every_trip_reference_warns_when_undefined(settings: Settings, tmp_path: Path) -> None:
    """A plan component, a flag and a redemption that name an undefined trip warn
    (never reject): backfill and old rows may name trips the chat never sent."""
    data = copy_samples(tmp_path)
    run = data / MONTHLY
    snap = read_json(run / "snapshot.json")
    plan = next(p for p in snap["installment_plans"] if p["key"] == "bramble-deferred")
    plan["components"][1]["trip_key"] = "no-such-trip"
    flag = next(f for f in snap["flags"] if f.get("related_kind") == "trip")
    flag["related_key"] = "no-such-trip"
    snap["rewards"]["redemptions"][0]["trip_key"] = "no-such-trip"
    write_json(run / "snapshot.json", snap)
    refresh_manifest(run)
    problems, warnings = cr.check_run_full(run, private_rules=str(PRIVATE_RULES_EXAMPLE))
    assert problems == []
    unknown = {w for w in warnings if w[0] == "trip-unknown"}
    flag_i = snap["flags"].index(flag)
    plan_i = snap["installment_plans"].index(plan)
    assert {
        ("trip-unknown", f"snapshot.json:installment_plans[{plan_i}].components[1].trip_key"),
        ("trip-unknown", f"snapshot.json:flags[{flag_i}].related_key"),
        ("trip-unknown", "snapshot.json:rewards.redemptions[0].trip_key"),
    } <= unknown
    results = {r.run_id: r for r in _import(settings, data, SAMPLE_RUNS)}
    assert results[MONTHLY].status == IMPORTED
    app = {(w.rule, w.where) for w in results[MONTHLY].warnings if w.rule == "trip-unknown"}
    assert app == unknown
