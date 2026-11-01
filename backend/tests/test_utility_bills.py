"""Format 1.2 utility_bills: samples, models, checker parity, apply, reapply, readers."""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest
from bad_cases import (
    MONTHLY,
    PRIVATE_RULES_EXAMPLE,
    copy_samples,
    read_json,
    refresh_manifest,
    write_json,
)
from conftest import SAMPLE_RUNS, SAMPLES

from app.config import Settings
from app.db.connect import read_connection
from app.db.migrate import migrate
from app.importer import models as m
from app.importer.models import load_snapshot
from app.importer.runner import IMPORTED, import_folder, reapply
from app.services import utility_bills as ub
from app.services.fingerprint import fingerprint

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


def _fp(settings: Settings) -> str:
    with read_connection(settings.db_path) as conn:
        return fingerprint(conn)


def _bills(settings: Settings, key: str | None = None) -> list[ub.UtilityBill]:
    with read_connection(settings.db_path) as conn:
        return ub.list_bills(conn, key)


def _later_run(data: Path, run_id: str, mode: str, bills: list[dict], version: str = "1.2") -> Path:
    """A small later run that carries only utility bills (plus what its mode requires)."""
    dst = data / run_id
    dst.mkdir()
    snap: dict = {"schema_version": version, "run_id": run_id, "mode": mode}
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
    snap["utility_bills"] = bills
    write_json(dst / "snapshot.json", snap)
    manifest = {
        "schema_version": version,
        "run_id": run_id,
        "mode": mode,
        "created_at": f"{run_id[:10]}T18:00:00-05:00",
        "generator": {"skill": "make_samples", "version": "1"},
        "files": [{"path": "snapshot.json", "sha256": "0" * 64, "bytes": 0}],
    }
    write_json(dst / "manifest.json", manifest)
    refresh_manifest(dst)
    return dst


def _sample_bill(period_end: str, key: str = "city-power") -> dict:
    for run in (BACKFILL, MONTHLY):
        for b in read_json(SAMPLES / run / "snapshot.json")["utility_bills"]:
            if (b["utility_key"], b["period_end"]) == (key, period_end):
                return json.loads(json.dumps(b))
    raise KeyError(period_end)


# ---- samples ----


def test_samples_cover_the_plan() -> None:
    bills = [
        b
        for run in (BACKFILL, MONTHLY)
        for b in read_json(SAMPLES / run / "snapshot.json")["utility_bills"]
    ]
    power = [b for b in bills if b["utility_key"] == "city-power"]
    water = [b for b in bills if b["utility_key"] == "town-water"]
    assert len(power) == 25 and len(water) == 4
    assert power[0]["period_end"][:7] == "2024-09" and power[-1]["period_end"][:7] == "2026-09"
    assert all(b["provider"] == "City Power & Gas" for b in power)
    assert all(29 <= b["days"] <= 33 for b in power)
    assert all([ln["service"] for ln in b["lines"]] == ["electric", "gas"] for b in power)
    assert sum(1 for b in power if b.get("estimated_read")) == 1
    assert sum(1 for b in power if b.get("credits")) >= 4
    assert all(c["amount"].startswith("-") for b in power for c in b.get("credits", []))
    assert any(b["period_end"] < "2026-01-01" for b in power)  # history before 2026
    rounding = [b for b in bills if m.UtilityBill.model_validate(b).total_rule()]
    assert len(rounding) == 1
    assert {ln["unit"] for b in water for ln in b["lines"]} == {"gallons", "none"}
    assert all(89 <= b["days"] <= 92 for b in water)
    for run, version in ((BACKFILL, "1.3"), (MONTHLY, "1.4")):
        assert read_json(SAMPLES / run / "snapshot.json")["schema_version"] == version
    assert read_json(SAMPLES / BACKFILL / "manifest.json")["mode"] == "backfill"


def test_2026_bills_match_a_tagged_payment() -> None:
    """Each 2026 bill's total is a payment with its recurring_key, 1 to 40 days after the bill."""
    import csv
    import io

    pays: dict[str, list[tuple[str, int]]] = {}
    for run in ("2026-10-06", BACKFILL, MONTHLY):
        for path in (SAMPLES / run / "transactions").glob("maple-joint-checking-*.csv"):
            for row in csv.DictReader(io.StringIO(path.read_text(), newline="")):
                if row["recurring_key"] in ("city-power", "town-water"):
                    assert row["category_key"] in ("gas-and-electric", "water-and-sewer")
                    pays.setdefault(row["recurring_key"], []).append(
                        (row["txn_date"], -m.to_cents(row["amount"]))
                    )
    for run in (BACKFILL, MONTHLY):
        for b in read_json(SAMPLES / run / "snapshot.json")["utility_bills"]:
            if b["bill_date"] < "2026-01-01":
                continue
            day = date.fromisoformat(b["bill_date"])
            window = (day.isoformat(), (day + timedelta(days=40)).isoformat())
            hits = [
                d
                for d, c in pays[b["utility_key"]]
                if c == m.to_cents(b["total"]) and window[0] <= d <= window[1]
            ]
            assert len(hits) == 1, b["period_end"]


# ---- app and checker agree ----


@pytest.mark.parametrize("run", SAMPLE_RUNS)
def test_sample_warnings_match_checker(run: str, settings: Settings) -> None:
    results = _import(settings, SAMPLES, SAMPLE_RUNS[: SAMPLE_RUNS.index(run) + 1])
    assert results[-1].status == IMPORTED
    app = {(w.rule, w.where) for w in results[-1].warnings if w.rule.startswith("utility-")}
    _, checker = cr.check_run_full(SAMPLES / run, private_rules=str(PRIVATE_RULES_EXAMPLE))
    assert app == {w for w in checker if w[0].startswith("utility-")}  # 1.3 warnings: test_trips
    if run == BACKFILL:
        assert app == {("utility-total-rounding", "snapshot.json:utility_bills[10]")}
    else:
        assert app == set()


def test_warnings_import_and_print(tmp_path: Path, settings: Settings, capsys) -> None:
    """Days off and an overlapping period are warnings: the run still imports."""
    data = copy_samples(tmp_path)
    late = _sample_bill("2026-09-03")
    late["days"] = 31  # the period is 30 days
    early = _sample_bill("2026-08-04")
    early["period_end"] = "2026-08-10"  # ends after the next bill starts
    early["days"] = 36
    run = _later_run(data, "2026-10-15", "correction", [early, late])
    expected = {
        ("utility-days", "snapshot.json:utility_bills[1]"),
        ("utility-overlap", "snapshot.json:utility_bills[1]"),
    }
    issues, warnings = cr.check_run_full(run)
    assert issues == [] and set(warnings) == expected
    assert cr.main([str(run)]) == 0
    out = capsys.readouterr().out
    assert "utility-overlap  snapshot.json:utility_bills[1]  (warning)" in out
    assert out.strip().endswith("OK")
    assert cr.main([str(run), "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    where = "snapshot.json:utility_bills[1]"
    assert {"level": "warning", "rule": "utility-days", "where": where} in rows

    results = _import(settings, data, (*SAMPLE_RUNS, "2026-10-15"))
    assert [r.status for r in results] == [IMPORTED] * 5
    app = {(w.rule, w.where) for w in results[-1].warnings}
    assert app == set(warnings)
    with read_connection(settings.db_path) as conn:
        found = {(w.bill_key, w.rule) for w in ub.bill_warnings(ub.list_bills(conn))}
    assert ("city-power:2026-09-03", "utility-overlap") in found
    assert ("city-power:2026-09-03", "utility-days") in found
    assert ("city-power:2025-07-05", "utility-total-rounding") in found


def test_pre_2026_bills_allowed_in_backfill(tmp_path: Path, settings: Settings) -> None:
    data = copy_samples(tmp_path)
    old = _sample_bill("2024-09-02")
    old |= {
        "period_start": "2021-08-31",
        "period_end": "2021-09-30",
        "bill_date": "2021-10-03",
        "due_date": "2021-10-23",
        "days": 30,
    }
    run = _later_run(data, "2026-10-15", "backfill", [old])
    assert cr.check_run(run) == []
    results = _import(settings, data, (*SAMPLE_RUNS, "2026-10-15"))
    assert results[-1].status == IMPORTED
    assert _bills(settings, "city-power")[0].period_end == "2021-09-30"


def test_v11_run_cannot_carry_bills(tmp_path: Path) -> None:
    data = copy_samples(tmp_path)
    run = _later_run(data, "2026-10-15", "correction", [_sample_bill("2026-09-03")], "1.1")
    assert ("section-version", "snapshot.json:utility_bills") in cr.check_run(run)
    _, issues = load_snapshot((run / "snapshot.json").read_bytes())
    assert m.Issue("section-version", "snapshot.json:utility_bills") in issues


def test_units_match_checker() -> None:
    assert {k: set(v) for k, v in m.UTILITY_UNITS.items()} == cr.UTILITY_UNITS
    assert m.UTILITY_EARLIEST == cr.UTILITY_EARLIEST
    assert m.UTILITY_ROUNDING_CENTS == cr.UTILITY_ROUNDING_CENTS
    assert cr.IDENTITY["utility_bills"] == ("utility_key", "period_end")


def test_text_fields_are_scanned() -> None:
    snap, _ = load_snapshot((SAMPLES / MONTHLY / "snapshot.json").read_bytes())
    locs = dict(m.iter_text(snap))
    for loc in (
        "utility_bills[0].utility_key",
        "utility_bills[0].provider",
        "utility_bills[0].paid_from",
        "utility_bills[0].credits[0].label",
        "utility_bills[0].lines[0].service",
    ):
        assert loc in locs, loc
    assert not any(
        loc.startswith("utility_bills")
        and loc.endswith((".usage", ".charges", ".fixed_charges", ".total", ".amount", "_date"))
        for loc in locs
    )
    bill = _sample_bill("2025-02-03")
    item = m.UtilityBill.model_validate(bill)
    assert dict(m.iter_text(item))["note"] == bill["note"]


@pytest.mark.parametrize(
    "service,unit,ok",
    [
        ("electric", "kwh", True),
        ("electric", "therms", False),
        ("gas", "therms", True),
        ("gas", "none", False),
        ("water", "ccf", True),
        ("sewer", "gallons", True),
        ("sewer", "kwh", False),
        ("stormwater", "none", True),
        ("other", "kwh", True),
    ],
)
def test_unit_fits_service(service: str, unit: str, ok: bool) -> None:
    line = {"service": service, "usage": "1", "unit": unit, "charges": "1.00"}
    if ok:
        m.UtilityLine.model_validate(line)
        return
    with pytest.raises(m.ValidationError) as exc:
        m.UtilityLine.model_validate(line)
    assert {i.rule for i in m.issues_from_error(exc.value)} == {"utility-unit"}


@pytest.mark.parametrize(
    "off,rule",
    [
        (0, None),
        (1, "utility-total-rounding"),
        (-2, "utility-total-rounding"),
        (3, "utility-total"),
        (-100, "utility-total"),
    ],
)
def test_total_rule(off: int, rule: str | None) -> None:
    assert m.utility_total_rule(10_000, -219, 9_781 + off) == rule


# ---- apply, newest run wins, tombstones, reapply ----


def test_samples_fill_the_tables(samples: Settings) -> None:
    with read_connection(samples.db_path) as conn:
        bills = ub.list_bills(conn)
        info = {u.utility_key: u for u in ub.list_utilities(conn)}
        n_lines = conn.execute("SELECT count(*) FROM utility_bill_lines").fetchone()[0]
        n_credits = conn.execute("SELECT count(*) FROM utility_bill_credits").fetchone()[0]
    assert len(bills) == 29 and n_lines == 25 * 2 + 4 * 3
    assert n_credits == sum(len(b.credits) for b in bills) >= 5
    power = info["city-power"]
    assert (power.bill_count, power.provider, power.services) == (
        25,
        "City Power & Gas",
        ("electric", "gas"),
    )
    assert power.first_period_end < "2025-01-01" < power.last_period_end
    assert info["town-water"].services == ("sewer", "stormwater", "water")
    sep = next(b for b in bills if b.bill_key == "city-power:2026-09-03")
    assert [c.label for c in sep.credits] == ["Energy savings credit", "Smart thermostat credit"]
    assert sep.charges_cents + sep.credits_cents == sep.total_cents
    assert sep.line("electric").unit == "kwh" and sep.line("electric").usage_micro > 0
    assert sep.paid_from == "maple-joint-checking" and sep.heating_degree_days is not None
    est = [b for b in bills if b.estimated_read]
    assert [b.bill_key for b in est] == ["city-power:2025-02-03"] and est[0].note
    storm = next(b for b in bills if b.utility_key == "town-water").line("stormwater")
    assert (storm.unit, storm.usage_micro, storm.fixed_charges_cents) == ("none", 0, 600)


def test_newest_run_wins_and_tombstones(tmp_path: Path, settings: Settings) -> None:
    data = copy_samples(tmp_path)
    changed = _sample_bill("2026-09-03")
    changed["lines"][0]["usage"] = "700"
    changed["credits"] = changed["credits"][1:]
    changed["total"] = m.cents_to_str(m.to_cents(changed["total"]) + 500)
    _later_run(data, "2026-10-15", "correction", [changed])
    tomb = {"utility_key": "town-water", "period_end": "2025-12-01", "deleted": True}
    _later_run(data, "2026-10-16", "backfill", [tomb])
    # an older run that arrives later, with a different version of the same bill
    older = _sample_bill("2026-09-03")
    older["note"] = "An older run"
    _later_run(data, "2026-10-12-2", "correction", [older])

    runs = (*SAMPLE_RUNS, "2026-10-15", "2026-10-16", "2026-10-12-2")
    assert [r.status for r in _import(settings, data, runs)] == [IMPORTED] * 7
    bills = {b.bill_key: b for b in _bills(settings)}
    sep = bills["city-power:2026-09-03"]
    assert sep.line("electric").usage_micro == 700_000_000
    assert [c.label for c in sep.credits] == ["Smart thermostat credit"]
    assert sep.note is None  # the newer run in run order wins, whatever arrived last
    assert "town-water:2025-12-01" not in bills
    with read_connection(settings.db_path) as conn:
        orphans = conn.execute(
            "SELECT count(*) FROM utility_bill_lines WHERE bill_key = 'town-water:2025-12-01'"
        ).fetchone()[0]
    assert orphans == 0
    fp_mixed = _fp(settings)

    # the same runs in run order into a fresh database give the same fingerprint
    s2 = Settings(db_path=tmp_path / "b" / "b.db", import_dir=tmp_path / "imp", backup_dir=tmp_path)
    ordered = sorted(runs, key=lambda r: (r[:10], int(r[11:] or 1)))
    assert [r.status for r in _import(s2, data, ordered)] == [IMPORTED] * 7
    assert _fp(s2) == fp_mixed


def test_reimport_is_zero_changes_and_reapply_rebuilds(settings: Settings) -> None:
    assert [r.status for r in _import(settings, SAMPLES, SAMPLE_RUNS)] == [IMPORTED] * 4
    fp1 = _fp(settings)
    with read_connection(settings.db_path) as conn:
        counts = json.loads(
            conn.execute(
                "SELECT counts_json FROM import_runs WHERE run_id = ?", (BACKFILL,)
            ).fetchone()[0]
        )
    assert counts["inserted"] > 0
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("DELETE FROM utility_bills")  # a database from before migration 0007
        conn.commit()
        assert conn.execute("SELECT count(*) FROM utility_bill_lines").fetchone()[0] == 0
    finally:
        conn.close()
    assert _fp(settings) != fp1
    out = reapply(settings)
    assert out["runs"] == 4
    assert _fp(settings) == fp1
    assert len(_bills(settings)) == 29


def test_tables_reject_bad_rows(db: sqlite3.Connection) -> None:
    db.execute(
        """INSERT INTO utility_bills (bill_key, utility_key, provider, bill_date, period_start,
               period_end, days, total_cents, run_id)
           VALUES ('x:2026-01-02', 'x', 'X', '2026-01-05', '2025-12-01', '2026-01-02', 32,
                   100, 'r')"""
    )
    bad = [
        "INSERT INTO utility_bill_credits VALUES ('x:2026-01-02', 1, 'Credit', 5)",
        "INSERT INTO utility_bill_lines VALUES ('x:2026-01-02', 'heat', 1, 'kwh', 1, NULL)",
        "INSERT INTO utility_bill_lines VALUES ('x:2026-01-02', 'gas', 1, 'therms', 1, 2)",
        "INSERT INTO utility_bill_lines VALUES ('y:2026-01-02', 'gas', 1, 'therms', 1, NULL)",
    ]
    for sql in bad:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(sql)
    db.rollback()


def test_utility_bills_are_never_statements() -> None:
    for run in SAMPLE_RUNS:
        snap = read_json(SAMPLES / run / "snapshot.json")
        keys = {b["utility_key"] for b in snap.get("utility_bills", [])}
        assert not keys & {s["account_key"] for s in snap.get("statements", [])}
    assert "utility_bills" in m.HISTORY_SECTIONS and "utility_bills" not in m.STATE_SECTIONS
    assert m.SECTION_SINCE["utility_bills"] == "1.2"
    for mode in ("monthly", "backfill", "correction"):
        assert "utility_bills" not in m.REQUIRED_SECTIONS["1.2"][mode]
