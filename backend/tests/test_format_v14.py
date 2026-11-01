"""Format 1.4 (docs/plans/format-14.md sections 2, 3 and 8): versions, the section,
field and value gate (app and checker), the README version lines, the goals rules, the
new state fields on flags, recurring, installment plans, cash flow events and
redemptions, with the warning recurring-no-date, and the account debt terms and trip
item fields (debt-terms, trip-due, trip-maybe). Run-level bad cases are in bad_cases.py
(test_check_run and test_import run them too). The database side of the account and
trip fields (migration 0012, the importer, the trip totals) is in test_migration_0012."""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path
from typing import get_args

import pytest
from bad_cases import (
    BAD_CASES,
    MONTHLY,
    PRIVATE_RULES_EXAMPLE,
    SAMPLES,
    _first,
    _older_form,
    _set_version,
    _v13,
    edit_snapshot,
    goals_run,
    read_json,
    since_objects,
    v13_samples,
    v14_run,
    write_json,
)
from conftest import SAMPLE_RUNS
from pydantic import ValidationError

from app.config import Settings
from app.db.connect import read_connection
from app.db.migrate import migrate
from app.importer import models as m
from app.importer import sanitize
from app.importer.models import Issue, iter_text, load_snapshot
from app.importer.runner import IMPORTED, import_folder

REPO = Path(__file__).resolve().parents[2]
SCHEMA = REPO / "schema"

# Every 1.4 field and value in the plan (format-14.md sections 3.1 to 3.7).
V14_FIELDS = (
    "accounts.original",
    "accounts.rate_changes",
    "cashflow_events.status",
    "flags.action",
    "flags.updates",
    "installment_plans.note",
    "installment_plans.paid_off_on",
    "recurring.keep",
    "recurring.tag",
    "rewards.redemptions.done_on",
    "trips.items.due_on",
    "trips.items.maybe",
)
V14_FLAG_KINDS = ("retirement", "investment", "insurance", "medical", "payroll", "home", "other")


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_run", SCHEMA / "check_run.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_run"] = mod
    spec.loader.exec_module(mod)
    return mod


cr = _load_checker()


def _app(run: Path) -> list[Issue]:
    _, issues = load_snapshot((run / "snapshot.json").read_bytes())
    return sorted(issues)


def _checker(run: Path, schema_dir: Path | None = None) -> list[tuple[str, str]]:
    return cr.check_run(run, private_rules=str(PRIVATE_RULES_EXAMPLE), schema_dir=schema_dir)


def _pairs(issues: list[Issue]) -> list[tuple[str, str]]:
    return sorted((i.rule, i.where) for i in issues)


def _rules(model: type, data: dict) -> set[str]:
    try:
        model.model_validate(data)
    except ValidationError as exc:
        return {i.rule for i in m.issues_from_error(exc)}
    return set()


def _schema() -> dict:
    return json.loads((SCHEMA / "import-v1.schema.json").read_text())


# --------------------------------------------------------------------------
# Versions and sections
# --------------------------------------------------------------------------


def test_versions_and_sections() -> None:
    assert m.CURRENT_VERSION == "1.4"
    assert m.KNOWN_VERSIONS == ("1.0", "1.1", "1.2", "1.3", "1.4")
    assert get_args(m.SchemaVersion) == m.KNOWN_VERSIONS
    assert m.version_rule("1.4") is None and m.version_rule("1.5") == "version-minor"
    assert m.REQUIRED_SECTIONS["1.4"] == m.REQUIRED_SECTIONS["1.3"]
    assert m.SECTION_SINCE["goals"] == "1.4"
    assert "goals" in m.STATE_SECTIONS and m.IDENTITY["goals"] == ("key",)
    for modes in m.REQUIRED_SECTIONS.values():
        assert all("goals" not in names for names in modes.values())  # never required
    assert m.FIELD_SINCE == dict.fromkeys(V14_FIELDS, "1.4")
    assert m.VALUE_SINCE == {"flags.kind": dict.fromkeys(V14_FLAG_KINDS, "1.4")}
    since = [*m.SECTION_SINCE.values(), *m.FIELD_SINCE.values()]
    since += [v for values in m.VALUE_SINCE.values() for v in values.values()]
    assert set(since) <= set(m.KNOWN_VERSIONS)
    assert m.RULES["section-version"] == (
        "A section, field or value needs a newer schema_version than the run declares."
    )
    schema = _schema()
    assert schema["x-schema-version"] == "1.4"
    assert set(schema["x-required-sections"]) == set(m.KNOWN_VERSIONS)
    assert schema["x-section-since"] == m.SECTION_SINCE
    assert schema["x-field-since"] == m.FIELD_SINCE
    assert schema["x-value-since"] == m.VALUE_SINCE


def test_readme_version_lines_match_the_app() -> None:
    """Exporters read the version from the README. A missed edit is costly."""
    readme = (SCHEMA / "README.md").read_text()
    v = m.CURRENT_VERSION
    assert readme.splitlines()[0] == f"# Import format v1 (schema {v})"
    assert re.findall(r"This document is ([0-9]+\.[0-9]+)\.", readme) == [v]
    accepted = re.findall(r"The app accepts ([0-9][0-9., and]*?)\. ", readme)
    assert len(accepted) == 1 and re.split(r", | and ", accepted[0]) == list(m.KNOWN_VERSIONS)
    assert re.findall(r'`"([0-9]+\.[0-9]+)"` \(new runs\)', readme) == [v, v]
    assert re.findall(r'"schema_version": "([0-9]+\.[0-9]+)"', readme) == [v]
    assert re.findall(r"Write ([0-9]+\.[0-9]+) only after the app", readme) == [v]
    assert re.findall(r"^\| ([0-9]+\.[0-9]+) \|", readme, flags=re.M) == list(m.KNOWN_VERSIONS)


# --------------------------------------------------------------------------
# The gate on the real 1.4 paths
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", sorted({*m.FIELD_SINCE, *m.VALUE_SINCE}))
def test_app_and_checker_walk_the_same_objects(path: str) -> None:
    raw = read_json(SAMPLES / MONTHLY / "snapshot.json")
    snap = m.Snapshot.model_validate(raw)
    app = [loc for _, loc in m.since_nodes(snap, path, "snapshot.json")]
    assert app == [loc for _, loc in cr.since_nodes(raw, path)]
    assert app == [loc for _, _, loc in since_objects(raw, path)]
    assert app  # every parent on the path is in the monthly sample


def test_the_stripped_monthly_run_is_a_valid_1_3_run(tmp_path: Path) -> None:
    run = _v13(lambda snap: None)(tmp_path)
    assert _checker(run) == []
    snap, issues = load_snapshot((run / "snapshot.json").read_bytes())
    assert issues == [] and snap is not None
    assert snap.schema_version == "1.3" and snap.goals is None


@pytest.mark.parametrize("case_id", ["v14-goals-in-v13-run", "v14-goals-null-in-v13-run"])
def test_goals_in_a_1_3_run_are_gated_at_the_section(case_id: str, tmp_path: Path) -> None:
    case = next(c for c in BAD_CASES if c.case_id == case_id)
    run = case.builder(tmp_path)
    assert _pairs(_app(run)) == _checker(run) == [("section-version", "snapshot.json:goals")]


@pytest.mark.parametrize("name", sorted(m.SECTION_SINCE))
def test_each_newer_section_is_gated_even_as_null(name: str, tmp_path: Path) -> None:
    """A run one minor version older than the section, with the section written as
    null: section-version at that section only, in the app and the checker."""
    since = m.SECTION_SINCE[name]
    older = f"1.{int(since.split('.')[1]) - 1}"
    run = _older_form(older, older)(tmp_path)
    edit_snapshot(run, lambda snap: snap.update({name: None}))
    assert _pairs(_app(run)) == _checker(run) == [("section-version", f"snapshot.json:{name}")]


@pytest.mark.parametrize(
    ("case_id", "sections"),
    [
        ("v11-sections-in-v10-run", ["flags", "installment_plans", "pending", "recurring"]),
        ("v12-section-in-v11-run", ["utility_bills"]),
        ("v13-rewards-in-v12-run", ["rewards"]),
    ],
)
def test_older_section_cases_fire_only_on_their_sections(
    case_id: str, sections: list[str], tmp_path: Path
) -> None:
    run = next(c for c in BAD_CASES if c.case_id == case_id).builder(tmp_path)
    want = sorted(("section-version", f"snapshot.json:{s}") for s in sections)
    assert sorted(_pairs(_app(run))) == sorted(_checker(run)) == want


@pytest.mark.parametrize("path", sorted(m.FIELD_SINCE))
def test_each_1_4_field_is_gated_even_as_null(path: str, tmp_path: Path) -> None:
    """A 1.3 run with the field written as null on one object: section-version at that
    field."""
    name = path.rsplit(".", 1)[1]
    run = _v13(_first(path, lambda obj: obj.update({name: None})))(tmp_path)
    loc = since_objects(read_json(run / "snapshot.json"), path)[0][2] + f".{name}"
    assert _pairs(_app(run)) == _checker(run) == [("section-version", loc)]


@pytest.mark.parametrize("kind", V14_FLAG_KINDS)
def test_each_new_flag_kind_is_gated(kind: str, tmp_path: Path) -> None:
    run = _v13(_first("flags.kind", lambda flag: flag.update(kind=kind)))(tmp_path)
    assert (
        _pairs(_app(run)) == _checker(run) == [("section-version", "snapshot.json:flags[0].kind")]
    )
    assert set(V14_FLAG_KINDS) <= set(get_args(m.FlagKind))


# --------------------------------------------------------------------------
# The gate mechanism, on stand-in paths over fields that exist today
# --------------------------------------------------------------------------


def _stand_in(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fields: dict[str, str],
    values: dict[str, dict[str, str]] | None = None,
) -> Path:
    """Gate tables with stand-in paths, in the app and in a copy of the checker's
    schema folder. Returns that folder."""
    values = values or {}
    monkeypatch.setattr(m, "FIELD_SINCE", fields)
    monkeypatch.setattr(m, "VALUE_SINCE", values)
    folder = tmp_path / "schema"
    folder.mkdir()
    for name in ("redaction.json", "categories.json"):
        shutil.copyfile(SCHEMA / name, folder / name)
    schema = _schema()
    schema["x-field-since"] = fields
    schema["x-value-since"] = values
    write_json(folder / "import-v1.schema.json", schema)
    return folder


def _only_on_first(path: str, value: object) -> object:
    """Drop the field everywhere on the path, then set it on the first object only."""
    name = path.rsplit(".", 1)[1]

    def edit(snap: dict) -> None:
        objects = [obj for _, obj, _ in since_objects(snap, path)]
        for obj in objects:
            obj.pop(name, None)
        objects[0][name] = value

    return edit


def test_gate_rejects_a_field_in_an_older_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = "rewards.redemptions.note"  # an object section, then a list
    schema_dir = _stand_in(monkeypatch, tmp_path, {path: "1.4"})
    run = _v13(_only_on_first(path, "Use it before spring"))(tmp_path)
    want = [("section-version", "snapshot.json:rewards.redemptions[0].note")]
    assert _pairs(_app(run)) == _checker(run, schema_dir) == want
    # the same run saying 1.4 passes
    write_json(run / "manifest.json", read_json(run / "manifest.json") | {"schema_version": "1.4"})
    edit_snapshot(run, lambda snap: snap.update(schema_version="1.4"))
    assert _app(run) == [] and _checker(run, schema_dir) == []


def test_gate_catches_an_explicit_null(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = "trips.items.note"
    schema_dir = _stand_in(monkeypatch, tmp_path, {path: "1.4"})
    run = _v13(_only_on_first(path, None))(tmp_path)
    want = [("section-version", "snapshot.json:trips[0].items[0].note")]
    assert _pairs(_app(run)) == _checker(run, schema_dir) == want


def test_gate_walks_lists_in_lists_and_skips_tombstones(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = "trips.items.label"  # required on every item
    schema_dir = _stand_in(monkeypatch, tmp_path, {path: "1.4"})
    run = _v13(lambda snap: None)(tmp_path)
    raw = read_json(run / "snapshot.json")
    tombstones = [i for i, t in enumerate(raw["trips"]) if t.get("deleted")]
    want = [("section-version", f"{loc}.label") for _, _, loc in since_objects(raw, path)]
    assert tombstones and len(want) > len(raw["trips"])
    assert not any(f"trips[{i}]" in loc for i in tombstones for _, loc in want)
    assert _pairs(_app(run)) == _checker(run, schema_dir) == sorted(want)


def test_gate_rejects_a_newer_value_at_the_field(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    schema_dir = _stand_in(monkeypatch, tmp_path, {}, {"flags.kind": {"milestone": "1.4"}})

    def edit(snap: dict) -> None:
        for flag in snap["flags"]:
            if flag["kind"] == "milestone":
                flag["kind"] = "budget"
        snap["flags"][0]["kind"] = "milestone"

    run = _v13(edit)(tmp_path)
    want = [("section-version", "snapshot.json:flags[0].kind")]
    assert _pairs(_app(run)) == _checker(run, schema_dir) == want  # never the value


def test_paths_with_no_field_match_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A path whose field does not exist (yet) is never present: nothing fires."""
    fields = dict.fromkeys(
        ("flags.no_such_field", "no_section.items.x", "rewards.no_list.x", "meta.updated.x"),
        "1.4",
    )
    schema_dir = _stand_in(monkeypatch, tmp_path, fields, {"flags.no_such_field": {"x": "1.4"}})
    run = _v13(lambda snap: None)(tmp_path)
    assert _app(run) == [] and _checker(run, schema_dir) == []


# --------------------------------------------------------------------------
# goals: the item rules
# --------------------------------------------------------------------------

COLLEGE = {
    "key": "college-jamie",
    "name": "College, older child",
    "kind": "college",
    "accounts": ["kids-529"],
    "target": "9000.00",
    "person": "jamie",
    "start_year": 2034,
}
SAVE = {
    "key": "emergency-fund",
    "name": "Emergency fund",
    "kind": "save_up",
    "accounts": ["harbor-savings"],
    "target": "3000.00",
}


def _without(data: dict, field: str) -> dict:
    return {k: v for k, v in data.items() if k != field}


@pytest.mark.parametrize(
    "data,rules",
    [
        (COLLEGE, set()),
        (COLLEGE | {"note": "Counts the custodial account too"}, set()),
        (_without(COLLEGE, "person"), {"goal-fields"}),
        (_without(COLLEGE, "start_year"), {"goal-fields"}),
        (COLLEGE | {"target_date": "2034-08-15"}, {"goal-fields"}),
        (SAVE, set()),
        (SAVE | {"target_date": "2027-12-31"}, set()),
        (SAVE | {"person": "alex"}, set()),  # any role on save_up
        (SAVE | {"target": "0.00"}, set()),
        (SAVE | {"start_year": 2030}, {"goal-fields"}),
        (SAVE | {"accounts": []}, {"schema"}),
        (SAVE | {"accounts": [f"account-{chr(97 + n)}" for n in range(11)]}, {"schema"}),
        (COLLEGE | {"start_year": 1999}, {"schema"}),
        (COLLEGE | {"start_year": 2101}, {"schema"}),
        (COLLEGE | {"start_year": "2034"}, {"schema"}),
        (COLLEGE | {"start_year": True}, {"schema"}),
        (SAVE | {"target": "-1.00"}, {"money-sign"}),
        (SAVE | {"target": "12"}, {"money-format"}),
        (SAVE | {"target_date": "2027-02-30"}, {"date-format"}),
        (SAVE | {"kind": "pay_down"}, {"schema"}),
        (SAVE | {"note": "Two\nlines"}, {"text-format"}),
        (SAVE | {"owner": "alex"}, {"schema"}),
        (_without(SAVE, "target"), {"schema"}),
    ],
)
def test_goal_rules(data: dict, rules: set[str]) -> None:
    assert _rules(m.Goal, data) == rules


# --------------------------------------------------------------------------
# goals: the run-level rules (app and checker)
# --------------------------------------------------------------------------


def test_a_1_4_monthly_run_with_goals_passes(tmp_path: Path) -> None:
    run = goals_run()(tmp_path)
    assert _app(run) == [] and _checker(run) == []


@pytest.mark.parametrize(
    "case_id,loc",
    [
        ("goal-college-without-person", "goals[0]"),
        ("goal-college-without-start-year", "goals[0]"),
        ("goal-target-date-on-college", "goals[0]"),
        ("goal-start-year-on-save-up", "goals[1]"),
        ("goal-college-adult", "goals[0]"),
        ("goal-college-brokerage", "goals[0]"),
        ("goal-accounts-unsorted", "goals[1].accounts"),
        ("goal-accounts-duplicate", "goals[1].accounts"),
        ("goals-unsorted", "goals[1]"),
        ("goal-unknown-account", "goals[1].accounts[0]"),
        ("goal-unknown-person", "goals[1].person"),
    ],
)
def test_goal_cases_point_at_the_same_spot(case_id: str, loc: str, tmp_path: Path) -> None:
    """The app and the checker give one issue, at the same location. Unknown keys are
    validate.py's check, so the app part there is the import (test_import runs it)."""
    case = next(c for c in BAD_CASES if c.case_id == case_id)
    run = case.builder(tmp_path)
    want = [(case.expected_rule_id, f"snapshot.json:{loc}")]
    assert _checker(run) == want
    if case.expected_rule_id not in ("account-unknown", "person-unknown"):
        assert _pairs(_app(run)) == want


def test_a_college_goal_may_hold_a_custodial_account(tmp_path: Path) -> None:
    run = goals_run()(tmp_path)

    def custodial(snap: dict) -> None:
        next(a for a in snap["accounts"] if a["key"] == "kids-529").update(type="custodial")

    edit_snapshot(run, custodial)
    assert _app(run) == [] and _checker(run) == []


def test_a_college_account_must_be_in_this_runs_accounts(tmp_path: Path) -> None:
    """Account types come from this run's accounts (the full registry in a monthly run).
    An account only known from an earlier run fails goal-fields."""
    run = goals_run()(tmp_path)

    def drop_529(snap: dict) -> None:
        snap["accounts"] = [a for a in snap["accounts"] if a["key"] != "kids-529"]

    edit_snapshot(run, drop_529)
    want = [("goal-fields", "snapshot.json:goals[0]")]
    assert _pairs(_app(run)) == want
    assert [p for p in _checker(run) if p[0] == "goal-fields"] == want


def test_a_college_person_must_be_a_child_in_this_runs_people(tmp_path: Path) -> None:
    run = goals_run()(tmp_path)

    def drop_jamie(snap: dict) -> None:
        snap["people"] = [p for p in snap["people"] if p["key"] != "jamie"]

    edit_snapshot(run, drop_jamie)
    want = [("goal-fields", "snapshot.json:goals[0]")]
    assert _pairs(_app(run)) == want
    assert [p for p in _checker(run) if p[0] == "goal-fields"] == want


def test_goal_text_is_scanned(tmp_path: Path) -> None:
    run = goals_run(lambda goals: goals[1].update(note="Six months of bills"))(tmp_path)
    snap, issues = load_snapshot((run / "snapshot.json").read_bytes())
    assert issues == [] and snap is not None
    locs = {loc for loc, _ in iter_text(snap) if loc.startswith("goals")}
    assert {"goals[0].name", "goals[1].name", "goals[1].note"} <= locs
    assert {"goals[0].person", "goals[0].accounts[0]"} <= locs  # keys are scanned too
    assert not any(loc.endswith((".target", ".target_date", ".start_year")) for loc in locs)


def test_goals_are_monthly_only(tmp_path: Path) -> None:
    case = next(c for c in BAD_CASES if c.case_id == "goals-in-backfill")
    run = case.builder(tmp_path)
    want = [("section-not-allowed", "snapshot.json:goals")]
    assert _pairs(_app(run)) == _checker(run) == want


def test_a_1_4_monthly_run_needs_no_goals(tmp_path: Path) -> None:
    """1.4 adds nothing required: the monthly sample saying 1.4 is valid as it is."""
    run = _set_version("1.4")(tmp_path)
    assert _app(run) == [] and _checker(run) == []


# --------------------------------------------------------------------------
# State fields (format-14.md sections 3.1 to 3.4 and 3.7): the item rules
# --------------------------------------------------------------------------

FLAG = {
    "key": "gym-ending",
    "severity": "medium",
    "status": "open",
    "kind": "cost_cutting",
    "title": "Gym ends",
    "created_on": "2026-10-01",
}


def _ups(n: int) -> list[dict]:
    return [{"on": "2026-10-03", "text": f"Update {chr(97 + i)}"} for i in range(n)]


@pytest.mark.parametrize(
    "data,rules",
    [
        *[(FLAG | {"kind": kind}, set()) for kind in V14_FLAG_KINDS],
        (FLAG | {"kind": "reimbursement"}, {"schema"}),  # an action, not a kind
        *[(FLAG | {"action": a}, set()) for a in ("act", "ask", "reimburse", "info")],
        (FLAG | {"action": "later"}, {"schema"}),
        (FLAG | {"action": None, "updates": None}, set()),  # null is absent to the model
        (FLAG | {"updates": [{"on": "2026-10-03", "text": "Called"}]}, set()),
        (FLAG | {"updates": _ups(20)}, set()),
        (FLAG | {"updates": _ups(21)}, {"schema"}),
        (FLAG | {"updates": []}, {"schema"}),
        (FLAG | {"updates": [{"on": "2026-10-03"}]}, {"schema"}),
        (FLAG | {"updates": [{"text": "Called"}]}, {"schema"}),
        (FLAG | {"updates": [{"on": "2026-10-03", "text": "Called", "by": "alex"}]}, {"schema"}),
        (FLAG | {"updates": [{"on": "2026-10-03", "text": "Two\nlines"}]}, {"text-format"}),
        (FLAG | {"updates": [{"on": "2026-10-03", "text": "x" * 501}]}, {"text-format"}),
        (FLAG | {"updates": [{"on": "2026-02-30", "text": "Called"}]}, {"date-format"}),
        # order and the date window need meta: run-level (test_flag_update_bounds)
        (
            FLAG
            | {"updates": [{"on": "2026-12-01", "text": "B"}, {"on": "2026-01-01", "text": "A"}]},
            set(),
        ),
    ],
)
def test_flag_rules(data: dict, rules: set[str]) -> None:
    assert _rules(m.Flag, data) == rules


RECURRING = {
    "key": "gym",
    "name": "Ironworks Gym",
    "category_key": "fitness",
    "amount": "45.00",
    "cadence": "monthly",
    "status": "active",
}


@pytest.mark.parametrize(
    "data,rules",
    [
        (RECURRING | {"keep": True}, set()),
        (RECURRING | {"keep": True, "status": "paused"}, set()),
        (RECURRING | {"keep": True, "status": "ending"}, {"recurring-keep"}),
        (RECURRING | {"keep": True, "status": "ended"}, {"recurring-keep"}),
        (RECURRING | {"keep": None, "status": "ended"}, set()),
        (RECURRING | {"keep": False}, {"schema"}),  # only true
        (RECURRING | {"keep": 1}, {"schema"}),  # the checker rejects 1 too (parity)
        (RECURRING | {"keep": 1.0}, {"schema"}),
        (RECURRING | {"keep": "true"}, {"schema"}),
        *[(RECURRING | {"tag": t}, set()) for t in ("streaming", "hobbies", "software")],
        (RECURRING | {"tag": "games"}, {"schema"}),
        (RECURRING | {"tag": "Streaming"}, {"schema"}),
        # the model alone takes a tag on the personal subscription: the rule is run-level,
        # so the privacy rescan and replay can re-validate stored runs
        (RECURRING | {"name": "Personal subscription", "tag": "streaming"}, set()),
    ],
)
def test_recurring_rules(data: dict, rules: set[str]) -> None:
    assert _rules(m.Recurring, data) == rules


def test_only_true_schema_matches_literal_true() -> None:
    keep = _schema()["$defs"]["Recurring"]["properties"]["keep"]
    assert keep["anyOf"] == [{"const": True, "type": "boolean"}, {"type": "null"}]


PLAN = {
    "key": "plan-sofa",
    "account_key": "cobalt-card",
    "kind": "plan_it",
    "name": "Sofa",
    "created_on": "2026-04-22",
    "original": "600.00",
    "status": "paid_off",
}


@pytest.mark.parametrize(
    "data,rules",
    [
        (PLAN | {"paid_off_on": "2026-09-30"}, set()),
        (PLAN | {"paid_off_on": "2026-04-22"}, set()),  # the same day
        (PLAN | {"paid_off_on": "2026-09-30", "status": "closed"}, set()),
        (PLAN | {"status": "active"}, set()),
        (PLAN | {"paid_off_on": None, "status": "active"}, set()),
        (PLAN | {"paid_off_on": "2026-09-30", "status": "active"}, {"plan-dates"}),
        (PLAN | {"paid_off_on": "2026-04-21"}, {"plan-dates"}),
        (PLAN | {"paid_off_on": "2026-09-31"}, {"date-format"}),
        (PLAN | {"note": "Paid off early from the bonus"}, set()),
        (PLAN | {"note": "Two\nlines"}, {"text-format"}),
        (PLAN | {"note": ""}, {"text-format"}),
    ],
)
def test_plan_rules(data: dict, rules: set[str]) -> None:
    assert _rules(m.InstallmentPlan, data) == rules


EVENT = {
    "key": "plan-sofa-ends",
    "effective_month": "2026-11",
    "delta": "50.00",
    "label": "Sofa plan ends",
}


@pytest.mark.parametrize(
    "data,rules",
    [
        *[
            (EVENT | {"status": st}, set())
            for st in ("confirmed", "expected", "target", "suggested")
        ],
        (EVENT | {"status": None}, set()),
        (EVENT | {"status": "assumed"}, {"schema"}),
    ],
)
def test_cashflow_event_status(data: dict, rules: set[str]) -> None:
    assert _rules(m.CashflowEvent, data) == rules


REDEMPTION = {
    "key": "ski-flights",
    "program_key": "summit-points",
    "title": "Ski flights",
    "status": "done",
}


@pytest.mark.parametrize(
    "data,rules",
    [
        (REDEMPTION | {"done_on": "2026-10-01"}, set()),
        (REDEMPTION, set()),  # older runs have done redemptions with no date
        (REDEMPTION | {"done_on": "2026-10-01", "points_needed": "4000"}, set()),
        (REDEMPTION | {"done_on": "2026-10-01", "status": "ready"}, {"reward-done-on"}),
        (REDEMPTION | {"done_on": "2026-10-01", "status": "consider"}, {"reward-done-on"}),
        (REDEMPTION | {"done_on": None, "status": "ready"}, set()),
        (REDEMPTION | {"done_on": "2026-02-30"}, {"date-format"}),
    ],
)
def test_redemption_rules(data: dict, rules: set[str]) -> None:
    assert _rules(m.RewardRedemption, data) == rules


# --------------------------------------------------------------------------
# State fields: the run-level rules and the gate (app and checker, same spot)
# --------------------------------------------------------------------------


def test_a_1_4_monthly_run_with_every_new_state_field_passes(tmp_path: Path) -> None:
    def edit(snap: dict) -> None:
        def item(section: str, key: str) -> dict:
            return next(i for i in snap[section] if i["key"] == key)

        item("flags", "gym-ending").update(
            action="ask",
            updates=[
                {"on": "2026-10-01", "text": "Asked about the fee"},  # on created_on
                {"on": "2026-10-12", "text": "A second call"},  # on meta.updated
                {"on": "2026-10-12", "text": "B same day"},  # same day, sorted by text
            ],
        )
        item("flags", "hsa-claim-paid").update(kind="medical", action="info")
        item("recurring", "cloudtunes").update(tag="streaming")
        item("recurring", "music-lessons").update(tag="hobbies", keep=True)
        item("recurring", "house-cleaning").update(keep=True)  # paused
        item("recurring", "personal-subscription").update(keep=True)  # keep is fine
        item("installment_plans", "towne-coats").update(
            note="Paid before the promo ended", paid_off_on="2026-06-30"
        )
        item("cashflow_events", "plan-sofa-ends")["status"] = "confirmed"
        item("cashflow_events", "device-plan-ends")["status"] = "suggested"
        x = next(x for x in snap["rewards"]["redemptions"] if x["key"] == "ski-flights")
        x.update(status="done", done_on="2026-10-01")

    run = v14_run(edit)(tmp_path)
    assert _app(run) == [] and _checker(run) == []
    snap, _ = load_snapshot((run / "snapshot.json").read_bytes())
    assert snap is not None
    locs = {loc for loc, _ in iter_text(snap)}
    assert {"flags[3].updates[0].text", "installment_plans[4].note"} <= locs  # scanned
    assert not any(loc.endswith((".on", ".paid_off_on", ".done_on", ".keep")) for loc in locs)


@pytest.mark.parametrize(
    "case_id,loc",
    [
        ("v14-flags-kind-in-v13-run", "flags[0].kind"),
        ("v14-flags-action-in-v13-run", "flags[0].action"),
        ("v14-flags-action-null-in-v13-run", "flags[0].action"),
        ("v14-flags-updates-in-v13-run", "flags[0].updates"),
        ("v14-recurring-keep-in-v13-run", "recurring[0].keep"),
        ("v14-recurring-tag-in-v13-run", "recurring[0].tag"),
        ("v14-installment-plans-note-in-v13-run", "installment_plans[0].note"),
        ("v14-installment-plans-paid-off-on-in-v13-run", "installment_plans[4].paid_off_on"),
        ("v14-cashflow-events-status-in-v13-run", "cashflow_events[0].status"),
        ("v14-rewards-redemptions-done-on-in-v13-run", "rewards.redemptions[0].done_on"),
        ("flag-kind-reimbursement", "flags[3].kind"),
        ("flag-bad-action", "flags[3].action"),
        ("flag-updates-empty", "flags[3].updates"),
        ("flag-too-many-updates", "flags[3].updates"),
        ("flag-update-without-text", "flags[3].updates[0].text"),
        ("flag-update-two-lines", "flags[3].updates[0].text"),
        ("flag-updates-unsorted", "flags[3].updates"),
        ("flag-updates-same-day-unsorted", "flags[3].updates"),
        ("flag-updates-duplicate", "flags[3].updates"),
        ("flag-update-before-created", "flags[3].updates[0].on"),
        ("flag-update-after-run", "flags[3].updates[0].on"),
        ("recurring-keep-on-ended", "recurring[11]"),
        ("recurring-keep-on-ending", "recurring[3]"),
        ("recurring-keep-false", "recurring[1].keep"),
        ("recurring-keep-as-number", "recurring[1].keep"),
        ("recurring-bad-tag", "recurring[1].tag"),
        ("recurring-tag-private", "recurring[9].tag"),
        ("plan-paid-off-on-active", "installment_plans[2]"),
        ("plan-paid-off-before-created", "installment_plans[4]"),
        ("cashflow-event-bad-status", "cashflow_events[1].status"),
        ("reward-done-on-not-done", "rewards.redemptions[1]"),
    ],
)
def test_state_field_cases_point_at_the_same_spot(case_id: str, loc: str, tmp_path: Path) -> None:
    """The app and the checker give one issue each, the same rule at the same spot."""
    case = next(c for c in BAD_CASES if c.case_id == case_id)
    run = case.builder(tmp_path)
    want = [(case.expected_rule_id, f"snapshot.json:{loc}")]
    assert _pairs(_app(run)) == _checker(run) == want


@pytest.mark.parametrize(
    "case_id,loc",
    [
        ("flag-update-phone", "flags[3].updates[0].text"),
        ("plan-note-phone", "installment_plans[2].note"),
    ],
)
def test_new_text_is_scanned_by_both(case_id: str, loc: str, tmp_path: Path) -> None:
    case = next(c for c in BAD_CASES if c.case_id == case_id)
    run = case.builder(tmp_path)
    snap, issues = load_snapshot((run / "snapshot.json").read_bytes())
    assert issues == [] and snap is not None
    app = _pairs(sanitize.scan_model(snap, "snapshot.json:", None))
    assert app == _checker(run)
    assert ("phone", f"snapshot.json:{loc}") in app
    assert {where for _, where in app} == {f"snapshot.json:{loc}"}


@pytest.mark.parametrize(
    "on,ok",
    [
        ("2026-09-30", False),  # the day before created_on
        ("2026-10-01", True),  # created_on
        ("2026-10-12", True),  # meta.updated
        ("2026-10-13", False),  # the day after
    ],
)
def test_flag_update_bounds(on: str, ok: bool, tmp_path: Path) -> None:
    def edit(snap: dict) -> None:
        flag = next(f for f in snap["flags"] if f["key"] == "gym-ending")
        flag["updates"] = [{"on": on, "text": "Called"}]

    run = v14_run(edit)(tmp_path)
    want = [] if ok else [("date-order", "snapshot.json:flags[3].updates[0].on")]
    assert _pairs(_app(run)) == _checker(run) == want


@pytest.mark.parametrize(
    "name,private",
    [
        ("Personal subscription", True),
        ("personal  Subscription", True),
        (" PERSONAL SUBSCRIPTION ", True),
        ("Personal\u00a0subscription", True),
        ("Personal subscriptions", False),
        ("Personal-subscription", False),
        ("Streaming subscription", False),
    ],
)
def test_personal_subscription_match(name: str, private: bool) -> None:
    assert m.is_personal_subscription(name) is private
    assert cr.is_personal_subscription(name) is private


def test_a_tagged_personal_subscription_fails_import_but_stays_readable(tmp_path: Path) -> None:
    """recurring-tag-private is run-level: load_snapshot (import) rejects the run, while
    Snapshot.model_validate (replay, the rescan, the readers) still reads it."""

    def edit(snap: dict) -> None:
        item = next(r for r in snap["recurring"] if r["key"] == "personal-subscription")
        item.update(name="personal  Subscription", tag="hobbies")

    run = v14_run(edit)(tmp_path)
    want = [("recurring-tag-private", "snapshot.json:recurring[9].tag")]
    assert _pairs(_app(run)) == _checker(run) == want
    raw = read_json(run / "snapshot.json")
    assert m.Snapshot.model_validate(raw).recurring[9].tag == "hobbies"


# --------------------------------------------------------------------------
# The warning recurring-no-date (app and checker)
# --------------------------------------------------------------------------


def _undated(data: Path, keys: dict[str, str]) -> Path:
    """Drop one date field (by item key) from recurring items in the monthly sample."""
    run = data / MONTHLY

    def edit(snap: dict) -> None:
        for r in snap["recurring"]:
            if r["key"] in keys:
                r.pop(keys[r["key"]])

    edit_snapshot(run, edit)
    return run


def test_recurring_no_date_warns_in_app_and_checker(
    settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """An active item with no date warns. A paused or ended one does not. The run still
    imports, in a 1.3 run too: the warning is not version gated."""
    data = v13_samples(tmp_path)  # the monthly run in its 1.3 form
    run = _undated(
        data, {"cloudtunes": "next_date", "house-cleaning": "start_date", "lawn-care": "next_date"}
    )
    next_snap = read_json(run / "snapshot.json")
    assert next_snap["schema_version"] == "1.3"
    paused = next(r for r in next_snap["recurring"] if r["key"] == "house-cleaning")
    assert paused["status"] == "paused" and not any(paused.get(k) for k in m.RECURRING_DATE_FIELDS)
    want = {
        ("recurring-no-date", "snapshot.json:recurring[1]"),
        ("recurring-no-date", "snapshot.json:recurring[6]"),
    }  # reelhouse (ended) and house-cleaning (paused) have no date and no warning

    problems, warnings = cr.check_run_full(run, private_rules=str(PRIVATE_RULES_EXAMPLE))
    assert problems == [] and {w for w in warnings if w[0] == "recurring-no-date"} == want
    assert cr.main([str(run)]) == 0
    out = capsys.readouterr().out
    assert "recurring-no-date  snapshot.json:recurring[1]  (warning)" in out
    assert out.strip().endswith("OK")
    assert cr.main([str(run), "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    where = "snapshot.json:recurring[6]"
    assert {"level": "warning", "rule": "recurring-no-date", "where": where} in rows

    migrate(settings=settings)
    results = [import_folder(data / r, settings=settings) for r in SAMPLE_RUNS]
    assert [r.status for r in results] == [IMPORTED] * len(SAMPLE_RUNS)
    monthly = results[SAMPLE_RUNS.index(MONTHLY)]
    assert {(w.rule, w.where) for w in monthly.warnings if w.rule == "recurring-no-date"} == want


def test_no_sample_run_warns_recurring_no_date(samples: Settings) -> None:
    with read_connection(samples.db_path) as conn:
        stored = [r["warnings_json"] or "[]" for r in conn.execute("SELECT * FROM import_runs")]
    assert len(stored) == len(SAMPLE_RUNS)
    assert not any(w["rule"] == "recurring-no-date" for text in stored for w in json.loads(text))
    for run in SAMPLE_RUNS:
        _, warnings = cr.check_run_full(SAMPLES / run, private_rules=str(PRIVATE_RULES_EXAMPLE))
        assert not any(rule == "recurring-no-date" for rule, _ in warnings), run


# --------------------------------------------------------------------------
# Accounts (3.5) and trip items (3.6): debt-terms, trip-due, trip-maybe
# --------------------------------------------------------------------------

LOAN = {
    "key": "van-loan",
    "display_name": "Van loan",
    "institution": "Prairie Lending",
    "type": "loan",
    "owner": "joint",
    "ownership": "joint",
    "group": "loans",
    "is_asset": False,
    "include_in_net_worth": False,
    "statement_cadence": "none",
}
VARIABLE = {"apr": "6.49", "rate_type": "variable"}
RC = {"effective_on": "2026-11-01", "apr": "5.99"}


def _schedule(n: int) -> list[dict]:
    return [RC | {"effective_on": f"2027-0{i + 1}-01"} for i in range(n)]


@pytest.mark.parametrize(
    "data,rules",
    [
        (LOAN, set()),
        (LOAN | {"original": "9000.00"}, set()),
        (LOAN | {"original": "0.01"}, set()),
        (LOAN | {"type": "mortgage", "original": "9000.00"}, set()),
        (LOAN | {"type": "heloc", "original": None}, set()),  # null is absent to the model
        (LOAN | {"type": "heloc", "original": "9000.00"}, {"debt-terms"}),  # uses credit_limit
        (
            LOAN | {"type": "credit_card", "group": "credit_cards", "original": "9000.00"},
            {"debt-terms"},
        ),
        (LOAN | {"original": "0.00"}, {"debt-terms"}),  # the column CHECK refuses 0 too
        (LOAN | {"original": "-1.00"}, {"money-sign"}),
        (LOAN | {"original": "9000"}, {"money-format"}),
        (LOAN | {"rate_changes": []}, set()),  # [] clears the schedule, fine anywhere
        (LOAN | {"type": "checking", "group": "cash", "rate_changes": []}, set()),
        (LOAN | {"rate_changes": None}, set()),
        (LOAN | VARIABLE | {"rate_changes": [RC]}, set()),
        (LOAN | VARIABLE | {"rate_changes": _schedule(4)}, set()),
        (LOAN | VARIABLE | {"rate_changes": _schedule(5)}, {"schema"}),
        (LOAN | VARIABLE | {"type": "savings", "group": "cash", "rate_changes": [RC]}, set()),
        (LOAN | VARIABLE | {"rate_type": "fixed", "rate_changes": [RC]}, {"debt-terms"}),
        (LOAN | VARIABLE | {"rate_type": "promo", "rate_changes": [RC]}, {"debt-terms"}),
        (LOAN | {"rate_type": "variable", "rate_changes": [RC]}, {"debt-terms"}),  # no apr
        (LOAN | {"apr": "6.49", "rate_changes": [RC]}, {"debt-terms"}),  # no rate_type
        # order and repeats are run-level (the bad cases)
        (LOAN | VARIABLE | {"rate_changes": [RC, RC]}, set()),
        (
            LOAN | VARIABLE | {"rate_changes": [RC | {"effective_on": "2026-11-31"}]},
            {"date-format"},
        ),
        (LOAN | VARIABLE | {"rate_changes": [RC | {"apr": "5.99%"}]}, {"rate-format"}),
        (LOAN | VARIABLE | {"rate_changes": [{"effective_on": "2026-11-01"}]}, {"schema"}),
        (LOAN | VARIABLE | {"rate_changes": [RC | {"note": "Rate notice"}]}, {"schema"}),
    ],
)
def test_account_debt_terms(data: dict, rules: set[str]) -> None:
    assert _rules(m.Account, data) == rules


BOOKED = {
    "key": "rental-car",
    "kind": "rental_car",
    "label": "Rental car",
    "status": "booked",
    "tender": "cash",
    "amount": "300.00",
}
TBD = {k: v for k, v in BOOKED.items() if k != "amount"} | {"status": "tbd"}
ESTIMATE = TBD | {"status": "estimate", "estimate_low": "150.00", "estimate_high": "250.00"}
DUE = {"due_on": "2027-01-15"}


@pytest.mark.parametrize(
    "data,rules",
    [
        (BOOKED | DUE, set()),
        (BOOKED | {"due_on": "2025-01-15"}, set()),  # no date floor
        (BOOKED | {"status": "posted"} | DUE, {"trip-due"}),
        (BOOKED | {"status": "excluded"} | DUE, {"trip-due"}),
        (TBD | DUE, {"trip-due"}),
        (ESTIMATE | DUE, {"trip-due"}),
        (BOOKED | {"status": "posted", "due_on": None}, set()),  # the 1.3 gate catches it
        (BOOKED | {"due_on": "2027-02-30"}, {"date-format"}),
        (TBD | {"maybe": True}, set()),
        (ESTIMATE | {"maybe": True}, set()),
        (BOOKED | {"maybe": True}, {"trip-maybe"}),
        (BOOKED | {"status": "posted", "maybe": True}, {"trip-maybe"}),
        (BOOKED | {"status": "excluded", "maybe": True}, {"trip-maybe"}),
        (BOOKED | {"maybe": None}, set()),  # the 1.3 gate catches it
        (TBD | {"maybe": False}, {"schema"}),  # only true
        (TBD | {"maybe": 1}, {"schema"}),  # the checker rejects 1 too (parity)
        (TBD | {"maybe": "true"}, {"schema"}),
        (ESTIMATE | {"maybe": True} | DUE, {"trip-due"}),
    ],
)
def test_trip_item_due_and_maybe(data: dict, rules: set[str]) -> None:
    assert _rules(m.TripItem, data) == rules


def test_maybe_schema_matches_keep() -> None:
    defs = _schema()["$defs"]
    maybe, keep = defs["TripItem"]["properties"]["maybe"], defs["Recurring"]["properties"]["keep"]
    assert maybe["anyOf"] == keep["anyOf"] == [{"const": True, "type": "boolean"}, {"type": "null"}]


def test_a_1_4_monthly_run_with_the_account_and_trip_fields_passes(tmp_path: Path) -> None:
    def edit(snap: dict) -> None:
        accounts = {a["key"]: a for a in snap["accounts"]}
        accounts["van-loan"]["original"] = "9000.00"
        accounts["pinecrest-mortgage"]["original"] = "8000.00"
        accounts["summit-card"]["rate_changes"] = [
            {"apr": "23.99", "effective_on": "2026-11-01"},
            {"apr": "22.99", "effective_on": "2027-02-01"},
        ]
        accounts["harbor-savings"]["rate_changes"] = [{"apr": "3.9", "effective_on": "2026-11-01"}]
        accounts["orchard-card"]["rate_changes"] = []
        accounts["family-car"]["rate_changes"] = []
        trips = {t["key"]: t for t in snap["trips"]}
        ski = {i["key"]: i for i in trips["ski-weekend"]["items"]}
        ski["rental-car"]["due_on"] = "2027-01-15"
        ski["gear"]["due_on"] = "2026-11-05"
        ski["parking"]["maybe"] = True  # tbd
        beach = trips["beach-week"]
        beach["items"] = [i for i in beach["items"] if i["key"] != "snorkel-tour"]  # the sample's
        beach["items"].append(
            {
                "key": "snorkel-tour",
                "kind": "activities",
                "label": "Snorkel tour",
                "status": "estimate",
                "tender": "cash",
                "estimate_low": "150.00",
                "estimate_high": "250.00",
                "maybe": True,
            }
        )

    run = v14_run(edit)(tmp_path)
    assert _app(run) == [] and _checker(run) == []
    snap, _ = load_snapshot((run / "snapshot.json").read_bytes())
    assert snap is not None
    locs = {loc for loc, _ in iter_text(snap)}
    assert not any(".rate_changes" in loc or loc.endswith((".due_on", ".maybe")) for loc in locs)


@pytest.mark.parametrize(
    "case_id,loc",
    [
        ("v14-accounts-original-in-v13-run", "accounts[15].original"),
        ("v14-accounts-rate-changes-in-v13-run", "accounts[0].rate_changes"),
        ("v14-trips-items-due-on-in-v13-run", "trips[2].items[6].due_on"),
        ("v14-trips-items-maybe-in-v13-run", "trips[0].items[0].maybe"),
        ("account-original-on-card", "accounts[14]"),
        ("account-original-zero", "accounts[15]"),
        ("account-original-negative", "accounts[15].original"),
        ("account-rate-changes-on-fixed", "accounts[13]"),
        ("account-rate-changes-without-apr", "accounts[14]"),
        ("account-rate-changes-without-rate-type", "accounts[14]"),
        ("account-rate-changes-unsorted", "accounts[14].rate_changes"),
        ("account-rate-changes-repeat", "accounts[14].rate_changes"),
        ("account-rate-changes-over-four", "accounts[14].rate_changes"),
        ("account-rate-change-bad-apr", "accounts[14].rate_changes[0].apr"),
        ("trip-due-on-posted", "trips[2].items[5]"),
        ("trip-due-on-estimate", "trips[0].items[0]"),
        ("trip-maybe-on-booked", "trips[2].items[6]"),
        ("trip-maybe-false", "trips[0].items[0].maybe"),
        ("trip-maybe-as-number", "trips[0].items[0].maybe"),
    ],
)
def test_account_and_trip_cases_point_at_the_same_spot(
    case_id: str, loc: str, tmp_path: Path
) -> None:
    """The app and the checker give one issue each, the same rule at the same spot."""
    case = next(c for c in BAD_CASES if c.case_id == case_id)
    run = case.builder(tmp_path)
    want = [(case.expected_rule_id, f"snapshot.json:{loc}")]
    assert _pairs(_app(run)) == _checker(run) == want
