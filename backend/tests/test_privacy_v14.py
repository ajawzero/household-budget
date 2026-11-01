"""Format 1.4 privacy and AI (docs/plans/format-14.md sections 5 and 6).

booking-code covers flags, installment_plans and goals from redaction.json version 4,
in runs of every version. Every new free-text field is a plain Text, so the import
scan, the checker and the privacy rescan all cover it. Nothing new from 1.4 reaches
Ask, the monthly review or the trip planner. The rescan side (masking, the dropped
tag and keep) is in test_privacy_rescan; the bad cases are in bad_cases.py.

The 1.4 runs here are the monthly sample stripped to 1.3 (strip_newer) with known
fake 1.4 values put back, so each test knows every 1.4 value in the run.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
from bad_cases import (
    BAD_CASES,
    MONTHLY,
    PRIVATE_RULES_EXAMPLE,
    SAMPLE_GOALS,
    SAMPLES,
    strip_newer,
    v14_run,
)
from conftest import SAMPLE_RUNS
from test_check_run import cr

from app.ai.facts import (
    ACCOUNT_FIELDS,
    DEBT_FIELDS,
    PLAN_FIELDS,
    RECURRING_FIELDS,
    ensure_facts,
)
from app.ai.planner import rewards_brief
from app.ai.safe import SAFE_VIEWS
from app.config import Settings
from app.db.connect import read_connection
from app.db.migrate import migrate
from app.importer.models import PERSONAL_SUBSCRIPTION, iter_text, load_snapshot, text_paths
from app.importer.runner import IMPORTED, import_folder
from app.importer.sanitize import (
    detect_sensitive,
    load_private_rules,
    scan_model,
    sensitive_spans,
)

REPO = Path(__file__).resolve().parents[2]
SCHEMA = REPO / "schema"
NEW_SECTIONS = ("flags", "goals", "installment_plans")

# The 1.4 free text the plan names (section 5), as shapes with no indexes.
NEW_TEXT = {"flags.updates.text", "installment_plans.note", "goals.name", "goals.note"}


def _by_key(items: list[dict]) -> dict[str, dict]:
    return {i["key"]: i for i in items if "key" in i}


# Fake 1.4 text, in no sample run. None of it may reach the AI.
UPDATES = [
    {"on": "2026-10-03", "text": "Asked the gym about the fee"},
    {"on": "2026-10-08", "text": "Called the gym again"},
]
PLAN_NOTES = {"plan-sofa": "Pay the sofa off early", "towne-coats": "Paid before the promo ended"}
GOAL_NOTES = {
    "college-jamie": "Grandparents add a gift each year",
    "emergency-fund": "Three months of spending",
}


def _every_14_field(snap: dict) -> None:
    """Every 1.4 field and value on the monthly run, fake values only."""
    strip_newer(snap, "1.3")
    accounts = _by_key(snap["accounts"])
    accounts["van-loan"]["original"] = "9000.00"
    accounts["summit-card"]["rate_changes"] = [{"apr": "23.99", "effective_on": "2026-11-01"}]
    trips = _by_key([t for t in snap["trips"] if not t.get("deleted")])
    _by_key(trips["ski-weekend"]["items"])["rental-car"]["due_on"] = "2027-01-15"
    _by_key(trips["beach-week"]["items"])["flights"]["maybe"] = True  # an estimate
    flags = _by_key(snap["flags"])
    flags["gym-ending"].update(action="ask", updates=copy.deepcopy(UPDATES))
    flags["hsa-claim-paid"].update(kind="medical", action="info")
    bills = _by_key(snap["recurring"])
    bills["cloudtunes"]["tag"] = "streaming"
    bills["music-lessons"].update(tag="hobbies", keep=True)
    plans = _by_key(snap["installment_plans"])
    for key, note in PLAN_NOTES.items():
        plans[key]["note"] = note
    plans["towne-coats"]["paid_off_on"] = "2026-06-30"
    for status, event in zip(("confirmed", "suggested"), snap["cashflow_events"], strict=True):
        event["status"] = status
    _by_key(snap["rewards"]["redemptions"])["ski-flights"].update(
        status="done", done_on="2026-10-01"
    )
    goals = copy.deepcopy(SAMPLE_GOALS)
    for g in goals:
        g["note"] = GOAL_NOTES[g["key"]]
    snap["goals"] = goals


def _v14_snapshot() -> dict:
    snap = json.loads((SAMPLES / MONTHLY / "snapshot.json").read_text())
    snap["schema_version"] = "1.4"
    _every_14_field(snap)
    return snap


def _parse(snap: dict):
    parsed, issues = load_snapshot((json.dumps(snap, indent=2) + "\n").encode())
    assert parsed is not None and issues == []
    return parsed


def _shape(loc: str) -> str:
    """'snapshot.json:flags[3].updates[1].text' -> 'flags.updates.text'."""
    return re.sub(r"\[[0-9]+\]", "", loc.split(":", 1)[-1])


# --------------------------------------------------------------------------
# booking-code: flags, installment_plans and goals (redaction.json version 4)
# --------------------------------------------------------------------------


def test_booking_code_covers_the_new_sections() -> None:
    red = json.loads((SCHEMA / "redaction.json").read_text())
    assert red["version"] == 4
    [rule] = [r for r in red["rules"] if r["id"] == "booking-code"]
    assert rule["sections"] == ["flags", "goals", "installment_plans", "rewards", "trips"]
    for section in NEW_SECTIONS:
        assert detect_sensitive("Ticket QX7B2K opened", section=section) == ["booking-code"]
    assert detect_sensitive("Ticket QX7B2K opened", section="budgets") == []
    assert detect_sensitive("Ticket QX7B2K opened") == []
    assert sensitive_spans("Ticket QX7B2K opened") == []  # never the AI output filter


def test_no_sample_text_in_a_booking_code_section_looks_like_one() -> None:
    """Every sample text in the rule's sections passes, the new ones included, so no
    stored or sample run starts failing."""
    red = json.loads((SCHEMA / "redaction.json").read_text())
    [sections] = [set(r["sections"]) for r in red["rules"] if r["id"] == "booking-code"]
    texts = []
    for run in sorted(p for p in SAMPLES.iterdir() if p.is_dir()):
        snap, issues = load_snapshot((run / "snapshot.json").read_bytes())
        assert snap is not None and issues == []
        texts += [(loc, t) for loc, t in iter_text(snap) if _shape(loc).split(".")[0] in sections]
    for loc, t in iter_text(_parse(_v14_snapshot())):
        if _shape(loc).split(".")[0] in sections:
            texts.append((loc, t))
    assert {_shape(loc).split(".")[0] for loc, _ in texts} >= set(NEW_SECTIONS)
    hits = [loc for loc, t in texts if detect_sensitive(t, _shape(loc).split(".")[0])]
    assert hits == []


# Each new privacy bad case: the app and the checker give one issue, the same rule at
# the same spot. (section, key) finds the item, so the index follows the samples.
PRIVACY_CASES = {
    "flag-update-private-name": ("flags", "gym-ending", "updates[1].text"),
    "plan-note-private-name": ("installment_plans", "plan-sofa", "note"),
    "goal-note-private-name": ("goals", "emergency-fund", "note"),
    "flag-update-booking-code": ("flags", "gym-ending", "updates[0].text"),
    "plan-note-booking-code": ("installment_plans", "plan-sofa", "note"),
    "goal-note-booking-code": ("goals", "emergency-fund", "note"),
    "flag-body-booking-code-in-v13-run": ("flags", "gym-ending", "body"),
    "plan-component-booking-code-in-v13-run": (
        "installment_plans",
        "plan-sofa",
        "components[0].merchant",
    ),
}


@pytest.mark.parametrize("case_id", sorted(PRIVACY_CASES))
def test_new_privacy_cases_point_at_the_same_spot(case_id: str, tmp_path: Path) -> None:
    case = next(c for c in BAD_CASES if c.case_id == case_id)
    run = case.builder(tmp_path)
    raw = json.loads((run / "snapshot.json").read_text())
    section, key, field = PRIVACY_CASES[case_id]
    i = [item["key"] for item in raw[section]].index(key)
    want = [(case.expected_rule_id, f"snapshot.json:{section}[{i}].{field}")]
    snap, issues = load_snapshot((run / "snapshot.json").read_bytes())
    assert snap is not None and issues == []
    private = load_private_rules(PRIVATE_RULES_EXAMPLE)
    app = sorted((x.rule, x.where) for x in scan_model(snap, "snapshot.json:", private))
    checker = cr.check_run(run, private_rules=str(PRIVATE_RULES_EXAMPLE))
    assert app == checker == want
    if case_id.endswith("-in-v13-run"):
        assert raw["schema_version"] == "1.3"  # the rule goes by section, not version


# --------------------------------------------------------------------------
# Every new free-text field is scanned (app and checker) and maskable (rescan)
# --------------------------------------------------------------------------


def test_every_new_14_text_field_is_scanned_and_maskable() -> None:
    snap = _v14_snapshot()
    parsed = _parse(snap)
    app = {_shape(loc) for loc, _ in iter_text(parsed)}
    maskable = {
        ".".join(p for p in path if isinstance(p, str))
        for path, _, is_text in text_paths(parsed)
        if is_text
    }
    schema = json.loads((SCHEMA / "import-v1.schema.json").read_text())
    errors, texts = cr.SchemaWalker(schema).run(snap, "Snapshot", "snapshot.json:")
    assert errors == []
    checker = {_shape(loc) for loc, _ in texts}
    assert app & maskable & checker >= NEW_TEXT
    # Fixed words: scanned and listed by the rescan, never masked.
    assert {"recurring.tag", "flags.action"} <= (app & checker) - maskable
    # Dates and the yes/no marks are checked by format, not scanned.
    assert not {"flags.updates.on", "recurring.keep", "trips.items.maybe"} & (app | checker)


@pytest.mark.parametrize("shape", sorted(NEW_TEXT))
def test_a_private_name_in_new_text_is_found_where_it_is(shape: str) -> None:
    """One item at a time: only the text that holds the name is flagged, by the app
    and by the checker."""
    snap = _v14_snapshot()
    value = "Asked EXAMPLE PRIVATE SERVICE"
    if shape == "flags.updates.text":
        i = [f["key"] for f in snap["flags"]].index("gym-ending")
        snap["flags"][i]["updates"][1]["text"] = value
        loc = f"flags[{i}].updates[1].text"
    elif shape == "installment_plans.note":
        i = [p["key"] for p in snap["installment_plans"]].index("plan-sofa")
        snap["installment_plans"][i]["note"] = value
        loc = f"installment_plans[{i}].note"
    else:
        field = shape.split(".")[1]
        snap["goals"][1][field] = value
        loc = f"goals[1].{field}"
    private = load_private_rules(PRIVATE_RULES_EXAMPLE)
    app = [(x.rule, x.where) for x in scan_model(_parse(snap), "snapshot.json:", private)]
    assert app == [("private-subscription", f"snapshot.json:{loc}")]
    schema = json.loads((SCHEMA / "import-v1.schema.json").read_text())
    red = cr.Redaction(SCHEMA / "redaction.json", cr.load_private(str(PRIVATE_RULES_EXAMPLE)))
    errors, texts = cr.SchemaWalker(schema).run(snap, "Snapshot", "snapshot.json:")
    assert errors == []
    assert sorted((r, w) for w, t in texts for r in red.scan(t, cr.section_of(w))) == app


# --------------------------------------------------------------------------
# AI: nothing new from 1.4 reaches Ask, the monthly review or the trip planner
# --------------------------------------------------------------------------

# Keys of 1.4 fields that never reach the AI. Not "note", "action" or "status": facts
# carry those keys today for other things. keep and tag reach Ask since E-b
# (RECURRING_FIELDS, format-14 section 6), so they are checked by value below.
NEW_14_KEYS = frozenset(
    {
        "updates",
        "paid_off_on",
        "paid_off_early",
        "refund_cents",
        "original",
        "original_cents",
        "paid_down_pct",
        "rate_changes",
        "rate_changes_json",
        "effective_on",
        "due_on",
        "maybe",
        "done_on",
        "last_redeemed_on",
        "goals",
        "start_year",
        "target_date",
    }
)
NEW_14_COLUMNS = frozenset({"original_cents", "rate_changes_json", "due_on", "maybe"})
NOT_FACT_KINDS = frozenset(
    {
        "flag",
        "flags",
        "goal",
        "goals",
        "trip",
        "trips",
        "cashflow_event",
        "cashflow_events",
        "rewards",
        "redemptions",
    }
)


def _keys(obj: object) -> set[str]:
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in _keys(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in _keys(v)}
    return set()


@pytest.fixture
def v14_db(settings: Settings, tmp_path: Path) -> Settings:
    """The samples imported with every 1.4 field on the monthly run."""
    data = v14_run(_every_14_field)(tmp_path).parent
    migrate(settings=settings)
    results = [import_folder(data / run, settings=settings) for run in SAMPLE_RUNS]
    assert [r.status for r in results] == [IMPORTED] * len(SAMPLE_RUNS), [
        (r.run_id, [(i.rule, i.where) for i in r.issues]) for r in results
    ]
    return settings


def test_nothing_from_14_reaches_the_ai(v14_db: Settings) -> None:
    for fields in (ACCOUNT_FIELDS, DEBT_FIELDS, PLAN_FIELDS, RECURRING_FIELDS):
        assert not NEW_14_KEYS & set(fields)
    ensure_facts(v14_db.db_path, None)
    with read_connection(v14_db.db_path) as conn:
        facts = {
            (r["kind"], r["key"]): json.loads(r["json"])
            for r in conn.execute("SELECT kind, key, json FROM ai_facts")
        }
        brief = rewards_brief(conn)
        columns = {c["name"] for v in SAFE_VIEWS for c in conn.execute(f"PRAGMA table_info({v})")}
        assert conn.execute("SELECT count(*) FROM trip_items WHERE maybe = 1").fetchone()[0]
        assert conn.execute(
            "SELECT count(*) FROM accounts WHERE rate_changes_json IS NOT NULL"
        ).fetchone()[0]
    kinds = {kind for kind, _ in facts}
    assert {"account", "plans", "debt", "recurring"} <= kinds
    assert not NOT_FACT_KINDS & kinds
    assert not NEW_14_KEYS & _keys(list(facts.values()))
    assert not (NEW_14_KEYS | {"redemptions"}) & _keys(brief)
    assert not NEW_14_COLUMNS & columns
    plans = facts[("plans", "all")]["plans"]
    assert plans and all(p.get("note") is None for p in plans)
    # Recurring facts pick fields by name. keep and tag go in as set (E-b), and the
    # personal subscription never shows a tag.
    items = facts[("recurring", "all")]["items"]
    assert items and all(set(it) <= set(RECURRING_FIELDS) for it in items)
    assert {"keep", "tag"} <= set(RECURRING_FIELDS)
    assert {(it["name"], it["tag"], it["keep"]) for it in items if it["tag"] or it["keep"]} == {
        ("Cloudtunes", "streaming", None),
        ("Riverside Music Lessons", "hobbies", True),
    }
    private = [it for it in items if it["name"] == PERSONAL_SUBSCRIPTION]
    assert private and all(it.get("tag") is None for it in private)
    blob = json.dumps(list(facts.values())) + json.dumps(brief)
    texts = [u["text"] for u in UPDATES] + list(PLAN_NOTES.values()) + list(GOAL_NOTES.values())
    texts += [g["name"] for g in SAMPLE_GOALS]
    assert all(texts) and [t for t in texts if t in blob] == []
