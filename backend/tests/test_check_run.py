"""schema/check_run.py: good samples pass, every bad case fails with its rule id,
no value is ever printed, and make_samples is deterministic."""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from bad_cases import (
    APP_ONLY_CASES,
    BAD_CASES,
    INJECTED_VALUES,
    PRIVATE_RULES_EXAMPLE,
    SAMPLES,
    copy_samples,
)

from app.importer import models, sanitize

REPO = Path(__file__).resolve().parents[2]
CHECK_RUN = REPO / "schema" / "check_run.py"
RUNS = sorted(p for p in SAMPLES.iterdir() if p.is_dir())


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses look the module up by name
    spec.loader.exec_module(mod)
    return mod


cr = _load(CHECK_RUN, "check_run")


def _check(run: Path, **kw) -> list[tuple[str, str]]:
    return cr.check_run(run, private_rules=str(PRIVATE_RULES_EXAMPLE), **kw)


# ---- good runs ----


@pytest.mark.parametrize("run", RUNS, ids=lambda p: p.name)
def test_good_samples_pass(run: Path) -> None:
    assert _check(run) == []


@pytest.mark.parametrize("run", RUNS, ids=lambda p: p.name)
def test_pre_mode_without_manifest(run: Path, tmp_path: Path) -> None:
    data = copy_samples(tmp_path)
    (data / run.name / "manifest.json").unlink()
    assert _check(data / run.name, pre=True) == []
    assert ("manifest-missing", "manifest.json") in _check(data / run.name)


def test_pre_mode_still_checks_paths(tmp_path: Path) -> None:
    run = copy_samples(tmp_path) / "2026-10-12"
    (run / "manifest.json").unlink()
    (run / "notes.txt").write_text("x\n")
    assert _check(run, pre=True) == [("path-not-allowed", "notes.txt")]


# ---- bad runs ----


@pytest.mark.parametrize("case", BAD_CASES, ids=lambda c: c.case_id)
def test_bad_cases(case, tmp_path: Path) -> None:
    run = case.builder(tmp_path)
    found = {rule for rule, _ in _check(run)}
    if case.case_id in APP_ONLY_CASES:
        assert found == set()  # only the app, with its import history, can reject it
    else:
        assert case.expected_rule_id in found, sorted(found)


@pytest.mark.parametrize("case", BAD_CASES, ids=lambda c: c.case_id)
def test_output_never_holds_values(case, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    run = case.builder(tmp_path)
    args = [str(run), "--private-rules", str(PRIVATE_RULES_EXAMPLE)]
    code = cr.main(args)
    code_json = cr.main([*args, "--json"])
    out = capsys.readouterr().out
    assert (code, code_json) == ((0, 0) if case.case_id in APP_ONLY_CASES else (1, 1))
    for value in INJECTED_VALUES:
        assert value.lower() not in out.lower(), value
    for line in out.splitlines():
        if line and not line.startswith(("FAILED", "OK", "[", "]", " ", "{", "}")):
            assert line.split()[0] in models.RULES


def test_sensitive_location_is_hidden(tmp_path: Path) -> None:
    run = copy_samples(tmp_path) / "2026-10-12"
    (run / "notes-4111111111111111.txt").write_text("x\n")
    issues = _check(run)
    assert issues == [("file-unlisted", "(hidden name)")]


def test_today_override_and_future(tmp_path: Path) -> None:
    run = copy_samples(tmp_path) / "2026-10-12"
    assert ("run-id-future", "(folder name)") in _check(run, today=cr.date(2026, 10, 10))
    assert _check(run, today=cr.date(2026, 10, 11)) == []


def test_cli_exit_codes(tmp_path: Path) -> None:
    good = subprocess.run(
        [sys.executable, str(CHECK_RUN), str(RUNS[0])], capture_output=True, text=True, check=False
    )
    assert good.returncode == 0 and good.stdout.strip() == "OK"
    run = copy_samples(tmp_path) / "2026-10-12"
    (run / "manifest.json").unlink()
    bad = subprocess.run(
        [sys.executable, str(CHECK_RUN), str(run), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode == 1
    assert {"rule": "manifest-missing", "where": "manifest.json"} in json.loads(bad.stdout)


# ---- the checker stays in step with the backend ----


def test_checker_runs_on_python_310_syntax() -> None:
    ast.parse(CHECK_RUN.read_text(), feature_version=(3, 10))
    imports = {
        n.names[0].name.split(".")[0]
        for n in ast.walk(ast.parse(CHECK_RUN.read_text()))
        if isinstance(n, ast.Import)
    }
    imports |= {
        n.module.split(".")[0]
        for n in ast.walk(ast.parse(CHECK_RUN.read_text()))
        if isinstance(n, ast.ImportFrom) and n.module
    }
    assert imports <= set(sys.stdlib_module_names) | {"__future__"}


def test_checker_rule_ids_are_known() -> None:
    src = CHECK_RUN.read_text()
    used = set(re.findall(r'self\.(?:add|warn)\("([a-z0-9-]+)"', src))
    used |= {r["id"] for r in json.loads((REPO / "schema" / "redaction.json").read_text())["rules"]}
    assert used and used <= set(models.RULES)


def test_checker_tables_match_models() -> None:
    assert cr.IDENTITY == models.IDENTITY
    assert cr.STATE_SECTIONS == models.STATE_SECTIONS
    assert cr.CASH_LIKE_TYPES == set(models.CASH_LIKE_TYPES)
    assert cr.INVESTMENT_TYPES == set(models.INVESTMENT_TYPES)
    assert cr.COLLEGE_ACCOUNT_TYPES == set(models.COLLEGE_ACCOUNT_TYPES)
    assert cr.DEBT_ORIGINAL_TYPES == set(models.DEBT_ORIGINAL_TYPES)
    assert cr.RECURRING_DATE_FIELDS == models.RECURRING_DATE_FIELDS
    assert cr.PERSONAL_SUBSCRIPTION == models.PERSONAL_SUBSCRIPTION == sanitize.SUBSCRIPTION_TEXT


# ---- sample generation ----


def test_make_samples_is_deterministic(tmp_path: Path) -> None:
    ms = _load(REPO / "scripts" / "make_samples.py", "make_samples")
    a, b = tmp_path / "a" / "data", tmp_path / "b" / "data"
    ms.generate(a)
    ms.generate(b)
    files_a = sorted(p.relative_to(a.parent) for p in a.parent.rglob("*") if p.is_file())
    files_b = sorted(p.relative_to(b.parent) for p in b.parent.rglob("*") if p.is_file())
    assert files_a == files_b
    for rel in files_a:
        assert (a.parent / rel).read_bytes() == (b.parent / rel).read_bytes(), rel
    # the committed samples match a fresh generation
    for rel in files_a:
        assert (REPO / "samples" / rel).read_bytes() == (a.parent / rel).read_bytes(), rel
    committed = sorted(p.relative_to(SAMPLES.parent) for p in SAMPLES.rglob("*") if p.is_file())
    assert committed == [r for r in files_a if r.parts[0] == "data"]


def test_samples_are_marked_as_samples() -> None:
    for run in RUNS:
        manifest = json.loads((run / "manifest.json").read_text())
        assert manifest["generator"]["skill"] == "make_samples"


def test_samples_cover_every_mode_and_edge(tmp_path: Path) -> None:
    modes = [json.loads((r / "manifest.json").read_text())["mode"] for r in RUNS]
    assert sorted(modes) == ["backfill", "backfill", "correction", "monthly"]
    assert any(r.name.endswith("-2") for r in RUNS)
    snaps = {r.name: json.loads((r / "snapshot.json").read_text()) for r in RUNS}
    statements = [s for snap in snaps.values() for s in snap.get("statements", [])]
    assert any(s.get("ocr") for s in statements)
    assert any(s["txn_count"] == 0 for s in statements)
    assert any(s["period_start"] < "2026-01-01" <= s["period_end"] for s in statements)
    shas = [s["source_sha256"] for s in statements if s.get("source_sha256")]
    assert len(shas) != len(set(shas))  # one source document holds two accounts
    holdings = [h for snap in snaps.values() for h in snap.get("holdings", [])]
    assert any("quantity" not in h for h in holdings)
    keys_by_month = {h["period_end"]: h["key"] for h in holdings if h["key"] == "bond-index"}
    assert max(keys_by_month) < "2026-06-30"  # the bond fund goes to zero
    closed = [a for a in snaps["2026-10-06-2"]["accounts"] if a.get("closed_on")]
    assert closed and closed[0]["closed_on"] < "2026-10-12"
    tomb = [b for b in snaps["2026-10-12"]["balances"] if b.get("deleted")]
    assert tomb
    csv_text = "".join(p.read_text() for p in SAMPLES.rglob("*.csv"))
    for needle in (
        "Personal subscription",
        "plan-sofa",
        "device-plan",
        "promo-mattress",
        "PLAN IT FEE",
        ",alex,",
        ",sam,",
        "SQ *MAPLE CAFE #34",
        "refund",
    ):
        assert needle in csv_text, needle
    shutil.rmtree(tmp_path, ignore_errors=True)
