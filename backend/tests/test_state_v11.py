"""Format 1.1 state sections: samples, readers, version rules, 1.0 runs (PLAN 3.7, 4.3, 4.8)."""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import shutil
import sys
from datetime import date
from pathlib import Path

import pytest
from bad_cases import (
    MONTHLY,
    PRIVATE_RULES_EXAMPLE,
    copy_samples,
    downgrade,
    read_json,
    refresh_manifest,
    strip_newer,
    write_json,
)
from conftest import SAMPLE_RUNS, SAMPLES

from app.config import Settings
from app.db.connect import read_connection
from app.db.migrate import migrate
from app.importer import models as m
from app.importer.models import load_snapshot
from app.importer.runner import IMPORTED, import_folder
from app.services.balances import clear_cache, net_worth
from app.services.fingerprint import fingerprint
from app.state import readers

REPO = Path(__file__).resolve().parents[2]
V11 = ("recurring", "installment_plans", "flags", "pending")


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_run", REPO / "schema" / "check_run.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_run"] = mod
    spec.loader.exec_module(mod)
    return mod


cr = _load_checker()


def _import(settings: Settings, base: Path, runs) -> list[str]:
    migrate(settings=settings)
    return [import_folder(base / r, settings=settings).status for r in runs]


def _runs(settings: Settings) -> list[readers.StateRun]:
    with read_connection(settings.db_path) as conn:
        return readers.monthly_runs(conn)


def _later_monthly(data: Path, run_id: str, version: str, edit) -> Path:
    """A later monthly run made from the sample monthly run, as a run of `version`."""
    dst = data / run_id
    shutil.copytree(data / MONTHLY, dst)
    snap = read_json(dst / "snapshot.json")
    snap.update(run_id=run_id, schema_version=version)
    strip_newer(snap, version)
    snap["meta"]["updated"] = run_id
    edit(snap)
    write_json(dst / "snapshot.json", snap)
    manifest = read_json(dst / "manifest.json")
    manifest.update(run_id=run_id, schema_version=version)
    write_json(dst / "manifest.json", manifest)
    refresh_manifest(dst)
    return dst


def _drop_v11(snap: dict) -> None:
    for name in V11:
        snap.pop(name, None)


# ---- samples ----


def test_sample_versions() -> None:
    versions = {r: read_json(SAMPLES / r / "snapshot.json")["schema_version"] for r in SAMPLE_RUNS}
    assert versions == {
        "2026-10-06": "1.0",
        "2026-10-06-2": "1.3",
        "2026-10-12": "1.4",
        "2026-10-14": "1.0",
    }
    for r in SAMPLE_RUNS:
        assert read_json(SAMPLES / r / "manifest.json")["schema_version"] == versions[r]


def test_sample_monthly_covers_1_1() -> None:
    snap = read_json(SAMPLES / MONTHLY / "snapshot.json")
    rec = snap["recurring"]
    assert {r["cadence"] for r in rec} >= {
        "weekly",
        "every_n_weeks",
        "monthly",
        "quarterly",
        "semester",
        "annual",
    }
    assert {r["status"] for r in rec} >= {"active", "ending", "paused"}
    assert any(r.get("active_months") and r["cadence"] == "monthly" for r in rec)  # seasonal
    assert any(r.get("credit_offset") for r in rec)
    plans = {p["kind"]: p for p in snap["installment_plans"]}
    assert set(plans) >= {"plan_it", "apple_installment", "promo_0pct", "deferred_interest"}
    assert plans["plan_it"].get("monthly_fee")
    assert plans["promo_0pct"].get("promo_expiry")
    assert plans["deferred_interest"].get("pay_before")
    flags = snap["flags"]
    assert {f["severity"] for f in flags} == {"high", "medium", "low"}
    assert {f["status"] for f in flags} == {"open", "watching", "resolved"}
    assert {p["kind"] for p in snap["pending"]} == {"apply", "confirm"}


def _installment_rows() -> dict[str, int]:
    """Installment billing rows per plan_key, over every sample CSV."""
    count: dict[str, int] = {}
    for path in SAMPLES.glob("*/transactions/*.csv"):
        if path.parts[-3] == "2026-10-14":
            continue  # the correction run sends September checking again
        for row in csv.DictReader(io.StringIO(path.read_text(), newline="")):
            if row["plan_key"] and "INSTALLMENT" in row["description"] and row["amount"] < "0":
                count[row["plan_key"]] = count.get(row["plan_key"], 0) + 1
    return count


def test_sample_plans_match_the_statements() -> None:
    plans = read_json(SAMPLES / MONTHLY / "snapshot.json")["installment_plans"]
    billed = _installment_rows()
    for p in plans:
        if "payments_total" in p:
            assert p["payments_total"] - p["payments_left"] == billed[p["key"]], p["key"]
            per = m.to_cents(p["monthly_payment"])
            assert m.to_cents(p["remaining"]) == per * p["payments_left"]
        if p.get("components"):
            assert sum(m.to_cents(c["amount"]) for c in p["components"]) == m.to_cents(
                p["original"]
            )
    plan = read_json(SAMPLES / MONTHLY / "snapshot.json")["cashflow_plan"][0]
    groups = {g["key"]: m.to_cents(g["amount"]) for g in plan["by_group"]}
    in_plan = sum(
        m.to_cents(p["monthly_payment"]) + m.to_cents(p.get("monthly_fee", "0.00"))
        for p in plans
        if p.get("included_in_spending_plan")
    )
    assert groups["plan-installments"] == in_plan


def test_recurring_keys_match_csv_rows() -> None:
    keys = {r["key"] for r in read_json(SAMPLES / MONTHLY / "snapshot.json")["recurring"]}
    used = {
        row["recurring_key"]
        for path in SAMPLES.glob("*/transactions/*.csv")
        for row in csv.DictReader(io.StringIO(path.read_text(), newline=""))
        if row["recurring_key"]
    }
    assert used - {"alex-pay", "sam-pay"} <= keys  # pay is an income stream


# ---- versions ----


def test_known_versions() -> None:
    assert m.KNOWN_VERSIONS == ("1.0", "1.1", "1.2", "1.3", "1.4") and m.CURRENT_VERSION == "1.4"
    assert set(m.REQUIRED_SECTIONS) == {"1.0", "1.1", "1.2", "1.3", "1.4"}
    assert m.REQUIRED_SECTIONS["1.2"] == m.REQUIRED_SECTIONS["1.1"]  # utility_bills is optional
    assert m.REQUIRED_SECTIONS["1.1"]["monthly"][-4:] == V11
    assert not set(V11) & set(m.REQUIRED_SECTIONS["1.0"]["monthly"])
    for mode in ("backfill", "correction"):
        assert m.REQUIRED_SECTIONS["1.1"][mode] == m.REQUIRED_SECTIONS["1.0"][mode]
    assert set(V11) <= set(m.STATE_SECTIONS)
    schema = json.loads((REPO / "schema" / "import-v1.schema.json").read_text())
    assert schema["x-schema-version"] == "1.4"
    assert schema["x-section-since"] == dict.fromkeys(V11, "1.1") | {
        "utility_bills": "1.2",
        "rewards": "1.3",
        "trips": "1.3",
        "goals": "1.4",
    }


def test_1_0_monthly_run_is_still_accepted(tmp_path: Path, settings: Settings) -> None:
    """A 1.0 monthly run simply lacks the 1.1 sections."""
    data = copy_samples(tmp_path)
    run = downgrade(data / MONTHLY, "1.0")
    assert not set(V11) & set(read_json(run / "snapshot.json"))
    assert cr.check_run(run, private_rules=str(PRIVATE_RULES_EXAMPLE)) == []
    _, issues = load_snapshot((run / "snapshot.json").read_bytes())
    assert issues == []
    assert _import(settings, data, SAMPLE_RUNS) == [IMPORTED] * 4
    runs = _runs(settings)
    assert readers.recurring(runs) == readers.flags(runs) == []
    assert readers.installment_plans(runs) == readers.pending(runs) == []
    assert readers.section_source(runs, "flags") is None


def test_1_1_monthly_run_needs_every_new_section(tmp_path: Path) -> None:
    data = copy_samples(tmp_path)
    run = data / MONTHLY
    snap = read_json(run / "snapshot.json")
    _drop_v11(snap)
    write_json(run / "snapshot.json", snap)
    refresh_manifest(run)
    expected = {("section-missing", f"snapshot.json:{n}") for n in V11}
    assert set(cr.check_run(run)) == expected
    _, issues = load_snapshot((run / "snapshot.json").read_bytes())
    assert {(i.rule, i.where) for i in issues} == expected


def test_backfill_may_say_1_1(tmp_path: Path) -> None:
    data = copy_samples(tmp_path)
    run = data / "2026-10-06"
    for name in ("snapshot.json", "manifest.json"):
        obj = read_json(run / name)
        obj["schema_version"] = "1.1"
        write_json(run / name, obj)
    refresh_manifest(run)
    assert cr.check_run(run) == []


@pytest.mark.parametrize("bad", ["-0.01", "-10.99", "10.9", "1,000.00"])
def test_amount_type(bad: str) -> None:
    item = {"merchant": "Shop", "amount": bad}
    with pytest.raises(m.ValidationError) as exc:
        m.PlanComponent.model_validate(item)
    rule = "money-sign" if bad.startswith("-") and "." in bad[-3:] else "money-format"
    assert {i.rule for i in m.issues_from_error(exc.value)} == {rule}
    m.PlanComponent.model_validate({"merchant": "Shop", "amount": "0.00"})


def test_new_text_fields_are_scanned() -> None:
    snap, _ = load_snapshot((SAMPLES / MONTHLY / "snapshot.json").read_bytes())
    locs = dict(m.iter_text(snap))
    assert "recurring[0].name" in locs and "flags[0].title" in locs
    assert "installment_plans[0].components[0].merchant" in locs
    assert "pending[0].subject" in locs
    assert not any(
        loc.startswith(("recurring", "installment_plans", "flags", "pending"))
        and loc.endswith(
            (".amount", ".original", ".remaining", "_date", "_on", ".active_months", ".end_month")
        )
        for loc in locs
    )


# ---- readers ----


def test_readers_return_typed_items(settings: Settings) -> None:
    assert _import(settings, SAMPLES, SAMPLE_RUNS) == [IMPORTED] * 4
    runs = _runs(settings)
    rec = readers.recurring(runs)
    assert all(isinstance(r, m.Recurring) for r in rec)
    assert [r.key for r in rec] == sorted(r.key for r in rec)
    lawn = next(r for r in rec if r.key == "lawn-care")
    assert lawn.active_months == [4, 5, 6, 7, 8, 9, 10] and lawn.cadence == "monthly"
    cleaning = next(r for r in rec if r.key == "house-cleaning")
    assert (cleaning.cadence, cleaning.interval_n, cleaning.status) == (
        "every_n_weeks",
        3,
        "paused",
    )
    plans = {p.key: p for p in readers.installment_plans(runs)}
    sofa = plans["plan-sofa"]
    assert isinstance(sofa, m.InstallmentPlan)
    assert (sofa.kind, sofa.monthly_fee, sofa.payments_left, sofa.end_month) == (
        "plan_it",
        "5.00",
        2,
        "2026-11",
    )
    assert plans["bramble-deferred"].components[1].trip_key == "ski-weekend"
    flags = readers.flags(runs)
    assert all(isinstance(f, m.Flag) for f in flags)
    resolved = [f for f in flags if f.status == "resolved"]
    assert resolved and all(f.resolved_on for f in resolved)
    pend = readers.pending(runs)
    assert all(isinstance(p, m.PendingItem) for p in pend)
    for name in V11:
        assert readers.section_source(runs, name) == MONTHLY


def test_newest_monthly_run_wins_per_section(tmp_path: Path, settings: Settings) -> None:
    data = copy_samples(tmp_path)

    def newer_1_1(snap: dict) -> None:
        snap["flags"] = [f for f in snap["flags"] if f["key"] != "gym-ending"]
        snap["pending"] = []

    _later_monthly(data, "2026-10-15", "1.1", newer_1_1)
    _later_monthly(data, "2026-10-16", "1.0", _drop_v11)
    runs_all = (*SAMPLE_RUNS, "2026-10-15", "2026-10-16")

    assert _import(settings, data, SAMPLE_RUNS) == [IMPORTED] * 4
    with read_connection(settings.db_path) as conn:
        fp_before = fingerprint(conn)
    v11 = read_json(data / MONTHLY / "snapshot.json")
    strip_newer(v11, "1.1")  # the 1.1 form: the 1.4 sample adds flags a 1.1 run never has
    flags_before = {f["key"] for f in v11["flags"]}
    assert _import(settings, data, runs_all[4:]) == [IMPORTED] * 2
    runs = _runs(settings)
    # the 1.0 run is newest for meta, the 1.1 run for the 1.1 sections
    assert readers.section_source(runs, "meta") == "2026-10-16"
    assert readers.section_source(runs, "flags") == "2026-10-15"
    # an item left out of the newest run is gone (resolved); an empty list wins too
    assert {f.key for f in readers.flags(runs)} == flags_before - {"gym-ending"}
    assert readers.pending(runs) == []
    assert len(readers.recurring(runs)) == 17
    with read_connection(settings.db_path) as conn:
        assert fingerprint(conn) != fp_before


def test_remaining_is_never_in_net_worth(samples: Settings) -> None:
    """Plan balances are already in the card's statement balance. `remaining` adds nothing."""
    snap = read_json(SAMPLES / MONTHLY / "snapshot.json")
    remaining = sum(m.to_cents(p.get("remaining", "0.00")) for p in snap["installment_plans"])
    assert remaining > 0
    clear_cache()
    with read_connection(samples.db_path) as conn:
        nw = net_worth(conn, date(2026, 9, 30))
    assert nw.value_cents == m.to_cents(snap["net_worth"]["value"])
