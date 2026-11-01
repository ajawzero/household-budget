"""Format 1.4 goals (state): the reader (newest monthly run wins whole, [] clears),
the fingerprint, a 1.4 run that imports, and stored runs that are not gated again
(docs/plans/format-14.md sections 2 and 3.8). The 1.4 monthly sample: every 1.4 field,
its 1.3 form (the 1.3 sample byte for byte), and a 1.3 run that reads the same as
before format 1.4 (section 8)."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from bad_cases import (
    MONTHLY,
    PRIVATE_RULES_EXAMPLE,
    SAMPLE_GOALS,
    SAMPLES,
    SCHEMA_FILE,
    V14_SAMPLE_ITEMS,
    edit_snapshot,
    goals_run,
    read_json,
    since_objects,
    strip_newer,
    v13_samples,
)
from conftest import SAMPLE_RUNS
from fastapi.testclient import TestClient
from goldens import E14_FIELDS, digest, older
from pydantic import TypeAdapter
from test_check_run import cr

from app import config
from app.config import Settings
from app.db.connect import connect, read_connection, write_connection
from app.db.migrate import migrate
from app.importer import models as m
from app.importer.models import Goal, load_snapshot
from app.importer.runner import IMPORTED, import_folder, reapply
from app.importer.sanitize import load_private_rules
from app.jobs import privacy
from app.services.balances import clear_cache
from app.services.fingerprint import fingerprint
from app.state import readers

GOALS = TypeAdapter(list[Goal]).validate_python(SAMPLE_GOALS)
TODAY = date(2026, 10, 20)

# The 1.3 monthly sample (last regenerated at 562fa53), captured at a19d2df before E-a.
V13_SNAPSHOT_SHA = "1fc28db7d517c506475b075b8b9c2e738c561a34846cb25b494eec66a741178a"
V13_MANIFEST_SHA = "2e44e8452bcae958603be1beeb6870f70fc5c630bad479209c9baa19654a9d85"
# API outputs on the 1.3 samples, captured at a19d2df: household today 2026-10-20, admin,
# prod mode. sha256 of the canonical JSON (sorted keys, no spaces), as in R1. E-b takes
# its new fields out before hashing, like R1, rather than recapturing.
V13_OUTPUTS = {
    "/api/flags": "150b42bafe1cf8596bf4b7a8a8dc7367655b1425dda1ee05909f908c855e5ff3",
    "/api/flags?include_archived=true": (
        "b5787d96e80b4874fc74e3f05f0c9529f7b7b6d6ebe46ad7be741e7485b9ba88"
    ),
    "/api/recurring": "e9cb43b3bdc25617e981c445a759d513a1aefd6185a7e01c9e51daadd85cf951",
    "/api/recurring?view=all": "dfb359eb01f97568e4ba22b0a1970584613e538e9cfd59f774500247a666bfad",
    "/api/recurring/calendar": "5448c74acc42e3f879cd0f7aea296233311acd39ec265f13c9c7a77b2f5a9c94",
    "/api/plans": "1afed07d9611b6c42c50a14c700490a8edbf391e8f3dfbaa9d8e8e83934b5098",
    "/api/cashflow": "bbb863e63e38bc76b6887cc0ad31645b2e4352490fe491adfa3db363a00f4d40",
    "/api/cashflow/plan": "f4ba23cc4a129be8d80e88d5af97498d5c1381ce63a27542aba850999814607d",
    "/api/rewards": "9077ce1ce2c43a2393de096cf8990b0cb35c11db976044ba240fb31ed95373b2",
    "/api/debt": "c08b69d09e865430a72a72bf43b444beacc6bbb617886d869176e14b7c3c5e0a",
    "/api/dashboard": "ecf65574cc0e5cf5f447c963ab55094d94d2d6f0952b90a4870c8f58d796a237",
    "/api/trips": "33bf9a3dea849eb30ab98931dae6ae6223115bd540da1249f4dee2c530059a7a",
}
NEW_FLAG_KINDS = {"retirement", "investment", "insurance", "medical", "payroll", "home", "other"}


def _by_key(items: list[dict]) -> dict[str, dict]:
    return {i["key"]: i for i in items if "key" in i}


def _runs(settings: Settings) -> list[readers.StateRun]:
    with read_connection(settings.db_path) as conn:
        return readers.monthly_runs(conn)


def _add_run(conn: sqlite3.Connection, run_id: str, mode: str, **sections: object) -> None:
    """A stored run with only the given sections (like test_state_v13's helper)."""
    conn.execute(
        """INSERT INTO import_runs (run_id, run_date, run_seq, mode, status, started_at,
               schema_version)
           VALUES (?, ?, 1, ?, 'imported', '2026-10-20T00:00:00+00:00', '1.4')""",
        (run_id, run_id[:10], mode),
    )
    for name, value in sections.items():
        conn.execute(
            "INSERT INTO run_sections (run_id, section, sha256, json) VALUES (?, ?, ?, ?)",
            (run_id, name, f"{run_id}-{name}".ljust(64, "0")[:64], json.dumps(value)),
        )


@pytest.fixture
def with_goals(settings: Settings, tmp_path: Path) -> Settings:
    """The sample runs imported, with the monthly run as a 1.4 run carrying goals."""
    run = goals_run()(tmp_path)
    migrate(settings=settings)
    statuses = [import_folder(run.parent / r, settings=settings).status for r in SAMPLE_RUNS]
    assert statuses == [IMPORTED] * len(SAMPLE_RUNS)
    return settings


def test_a_1_4_run_with_goals_imports_and_reads_back(with_goals: Settings) -> None:
    runs = _runs(with_goals)
    assert readers.goals(runs) == GOALS
    assert readers.section_source(runs, "goals") == MONTHLY


def test_no_goals_before_a_run_has_them(db: sqlite3.Connection) -> None:
    assert readers.goals(readers.monthly_runs(db)) == []


def test_the_newest_monthly_run_wins_whole(with_goals: Settings) -> None:
    """Goals left out of the newest run ended: the reader never merges."""
    with write_connection(with_goals.db_path) as w:
        _add_run(w, "2026-10-20", "monthly", goals=[SAMPLE_GOALS[1]])
        _add_run(w, "2026-10-21", "backfill", goals=[])  # not monthly: ignored
    runs = _runs(with_goals)
    assert readers.goals(runs) == GOALS[1:]
    assert readers.section_source(runs, "goals") == "2026-10-20"


def test_an_empty_list_clears_goals(with_goals: Settings) -> None:
    with write_connection(with_goals.db_path) as w:
        _add_run(w, "2026-10-20", "monthly", goals=[])
    runs = _runs(with_goals)
    assert readers.goals(runs) == []
    assert readers.section_source(runs, "goals") == "2026-10-20"


def test_a_monthly_run_without_goals_keeps_them(with_goals: Settings) -> None:
    meta = {"updated": "2026-10-20", "this_month": "2026-10"}
    with write_connection(with_goals.db_path) as w:
        _add_run(w, "2026-10-20", "monthly", meta=meta)
    runs = _runs(with_goals)
    assert readers.goals(runs) == GOALS
    assert readers.section_source(runs, "goals") == MONTHLY


def test_the_fingerprint_follows_the_newest_goals(with_goals: Settings) -> None:
    def fp() -> str:
        conn = connect(with_goals.db_path)
        try:
            return fingerprint(conn)
        finally:
            conn.close()

    before = fp()
    with write_connection(with_goals.db_path) as w:
        _add_run(w, "2026-10-21", "backfill", goals=[])
    assert fp() == before  # state comes from monthly runs only
    with write_connection(with_goals.db_path) as w:
        _add_run(w, "2026-10-20", "monthly", goals=[])
    assert fp() != before


def test_stored_runs_are_not_gated_again(
    with_goals: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Replay, reapply, the privacy rescan and the readers never call section_issues
    (format-14.md section 2): only the import gates a run."""

    def gate(*args: object, **kwargs: object) -> list:
        raise AssertionError("section_issues ran on a stored run")

    monkeypatch.setattr(m, "section_issues", gate)
    reapply(with_goals)
    rules = tmp_path / "private_rules.json"
    rules.write_text(
        json.dumps({"personal_subscriptions": ["Nothing Matches Here"], "employer_terms": []})
    )
    privacy.run_rescan(with_goals, load_private_rules(rules))
    runs = _runs(with_goals)
    assert readers.goals(runs) == GOALS
    assert readers.flags(runs) and readers.recurring(runs) and readers.rewards(runs)


# --------------------------------------------------------------------------
# The 1.4 monthly sample (format-14.md section 8)
# --------------------------------------------------------------------------


def test_the_monthly_sample_carries_every_14_field() -> None:
    raw = (SAMPLES / MONTHLY / "snapshot.json").read_bytes()
    _, issues = load_snapshot(raw)
    assert issues == []
    snap = json.loads(raw)
    assert (
        snap["schema_version"]
        == read_json(SAMPLES / MONTHLY / "manifest.json")["schema_version"]
        == m.CURRENT_VERSION
        == "1.4"
    )
    for path in read_json(SCHEMA_FILE)["x-field-since"]:
        name = path.rsplit(".", 1)[1]
        assert any(name in obj for _, obj, _ in since_objects(snap, path)), path

    flags = _by_key(snap["flags"])
    new = {k for k, f in flags.items() if f["kind"] in NEW_FLAG_KINDS}
    assert new == V14_SAMPLE_ITEMS["flags"]  # one flag per new kind, only on new flags
    assert sorted(flags[k]["kind"] for k in new) == sorted(NEW_FLAG_KINDS)
    assert {f.get("action") for f in flags.values()} == {None, "act", "ask", "reimburse", "info"}
    assert all(k > "gym-ending" for k in new)  # the 1.4 cases' flags[3] stays gym-ending
    days = [[u["on"] for u in f["updates"]] for f in flags.values() if "updates" in f]
    assert any(len(d) == 2 and d[0] == d[1] for d in days)  # two updates on one day
    assert any(len(d) > 3 for d in days)  # the Flags page shows "Show 1 earlier"
    info_dates = [f.get("due_date") for f in flags.values() if f.get("action") == "info"]
    assert any(d and d < str(TODAY) for d in info_dates)  # an info flag's past date

    bills = _by_key(snap["recurring"])
    kept = {k: r for k, r in bills.items() if r.get("keep")}
    assert set(kept) == {"music-lessons", "summit-annual-fee"}
    assert {r["status"] for r in kept.values()} <= {"active", "paused"}
    assert {"review_by" in r for r in kept.values()} == {True, False}
    tags = {k: r["tag"] for k, r in bills.items() if "tag" in r}
    assert tags == {
        "cloudtunes": "streaming",
        "gym": "hobbies",
        "music-lessons": "hobbies",
        "reelhouse": "streaming",
    }
    assert not {"tag", "keep"} & set(bills["personal-subscription"])

    plans = _by_key(snap["installment_plans"])
    assert plans["towne-coats"]["status"] == "paid_off" and plans["towne-coats"]["paid_off_on"]
    assert {k for k, p in plans.items() if "note" in p} == {"promo-mattress", "towne-coats"}
    events = _by_key(snap["cashflow_events"])
    statuses = {e["status"] for e in events.values()}
    assert statuses == {"confirmed", "expected", "target", "suggested"}
    assert set(events) - V14_SAMPLE_ITEMS["cashflow_events"] == {
        "device-plan-ends",
        "plan-sofa-ends",
    }

    accounts = _by_key(snap["accounts"])
    assert {k for k, a in accounts.items() if "original" in a} == {"pinecrest-mortgage", "van-loan"}
    assert {k for k, a in accounts.items() if "rate_changes" in a} == {"orchard-card"}
    assert accounts["orchard-card"]["rate_type"] == "variable"

    trips = _by_key([t for t in snap["trips"] if not t.get("deleted")])
    ski = _by_key(trips["ski-weekend"]["items"])
    assert (ski["rental-car"]["status"], ski["rental-car"]["due_on"]) == ("booked", "2026-11-10")
    maybe = [(t, i["key"], i["status"]) for t in trips for i in trips[t]["items"] if i.get("maybe")]
    assert maybe == [("beach-week", "snorkel-tour", "estimate")]

    done = [x for x in snap["rewards"]["redemptions"] if x["status"] == "done"]
    assert [(x["key"], x["done_on"]) for x in done] == [("spring-stay", "2026-04-18")]
    assert "trip_key" not in done[0]

    people = {p["key"]: p["role"] for p in snap["people"]}
    goals = _by_key(snap["goals"])
    assert {g["kind"] for g in goals.values()} == {"college", "save_up"}
    for g in goals.values():
        if g["kind"] == "college":
            assert people[g["person"]] == "child" and isinstance(g["start_year"], int)
            assert g["accounts"] == ["kids-529"]
        else:
            assert g["target_date"] and "start_year" not in g


@pytest.mark.parametrize("run", ["2026-10-06", "2026-10-06-2", "2026-10-14"])
def test_older_sample_runs_carry_nothing_newer(run: str) -> None:
    """The backfill runs and the correction stay at their version (1.0, 1.3, 1.0)."""
    snap = read_json(SAMPLES / run / "snapshot.json")
    assert snap["schema_version"] == {"2026-10-06-2": "1.3"}.get(run, "1.0")
    same = json.loads(json.dumps(snap))
    strip_newer(same, snap["schema_version"])
    assert same == snap


def test_the_13_form_is_the_13_sample_byte_for_byte(tmp_path: Path) -> None:
    """make_samples only adds to the 1.3 monthly run, and strip_newer takes exactly that
    out again: GENERATOR, created_at and every 1.3 value are unchanged."""
    run = v13_samples(tmp_path) / MONTHLY
    sha = {
        n: hashlib.sha256((run / n).read_bytes()).hexdigest()
        for n in ("snapshot.json", "manifest.json")
    }
    assert sha == {"snapshot.json": V13_SNAPSHOT_SHA, "manifest.json": V13_MANIFEST_SHA}


def test_the_13_form_passes_and_imports(tmp_path: Path, settings: Settings) -> None:
    data = v13_samples(tmp_path)
    run = data / MONTHLY
    assert cr.check_run(run, private_rules=str(PRIVATE_RULES_EXAMPLE)) == []
    _, issues = load_snapshot((run / "snapshot.json").read_bytes())
    assert issues == []
    migrate(settings=settings)
    statuses = [import_folder(data / r, settings=settings).status for r in SAMPLE_RUNS]
    assert statuses == [IMPORTED] * len(SAMPLE_RUNS)
    assert readers.goals(_runs(settings)) == []


def test_a_13_monthly_run_reads_the_same(
    samples_v13: Settings, admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Flags (open and watching counts), Recurring, Plans, Cash Flow, Rewards, Debt,
    Dashboard and Trips give what they gave before format 1.4 on the 1.3 samples."""
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    clear_cache()
    got = {}
    for url in V13_OUTPUTS:
        r = admin.get(url)
        assert r.status_code == 200, url
        got[url] = digest(older(r.json(), E14_FIELDS.get(url.split("?")[0], {})))
    assert got == V13_OUTPUTS


def test_every_e14_path_is_a_golden_url() -> None:
    """A typo in a URL would leave its fields in the body unseen by any golden."""
    goldens = {u.split("?")[0] for u in V13_OUTPUTS}
    assert set(E14_FIELDS) <= goldens


def test_digest_is_the_canonical_json() -> None:
    want = hashlib.sha256(b'{"a":1,"b":[2,{"c":null}]}').hexdigest()
    assert digest({"b": [2, {"c": None}], "a": 1}) == want


def test_older_takes_new_fields_out_at_their_old_value() -> None:
    old = {
        "counts": {"open": 2},
        "cards": [{"plans": [{"key": "a"}, {"key": "b"}]}, {"plans": []}, {"other": 1}],
    }
    new = copy.deepcopy(old)
    new["counts"]["info"] = 0
    for plan in new["cards"][0]["plans"]:
        plan |= {"note": None, "updates": []}
    fields = {"counts.info": 0, "cards.plans.note": None, "cards.plans.updates": []}
    kept = copy.deepcopy(new)
    assert older(new, fields) == old
    assert digest(older(new, fields)) == digest(old)
    assert new == kept  # a copy: the body itself is left alone
    assert older(old, {}) == old and older(old, {}) is not old


def test_older_fails_a_new_value_on_old_data() -> None:
    body = {"flags": [{"key": "a", "action": "act"}, {"key": "b", "action": "ask"}]}
    with pytest.raises(AssertionError, match="on old data"):
        older(body, {"flags.action": "act"})


@pytest.mark.parametrize("path", ["flags.actoin", "flgs.action", "counts.inf"])
def test_older_fails_a_path_not_in_the_body(path: str) -> None:
    body = {"flags": [{"key": "a", "action": "act"}], "counts": {"info": 0}}
    with pytest.raises(AssertionError, match="not in the body"):
        older(body, {path: "act"})


def test_older_passes_when_no_object_holds_the_path() -> None:
    body = {"items": [], "trip": None, "older": [{"items": []}]}
    assert older(body, {"items.tag": None, "trip.due": [], "older.items.due_on": None}) == body


def test_older_on_a_13_body(
    samples_v13: Settings, admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real body with a field added on every nested trip item strips back to its golden."""
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    clear_cache()
    body = older(admin.get("/api/trips").json(), E14_FIELDS.get("/api/trips", {}))
    added = copy.deepcopy(body)
    paths = [s for s in ("active", "completed", "older") if added[s]]
    assert paths  # the 1.3 sample has trips
    for s in paths:
        for trip in added[s]:
            for item in trip["items"]:
                item["probe"] = None
    fields = {f"{s}.items.probe": None for s in paths}
    assert digest(older(added, fields)) == V13_OUTPUTS["/api/trips"]
    with pytest.raises(AssertionError, match="on old data"):
        older(added, {f"{paths[0]}.items.probe": False})


def test_the_14_sample_fills_the_new_columns(samples: Settings) -> None:
    sample = _by_key(read_json(SAMPLES / MONTHLY / "snapshot.json")["accounts"])
    with read_connection(samples.db_path) as conn:
        original = dict(
            conn.execute(
                "SELECT account_key, original_cents FROM accounts WHERE original_cents IS NOT NULL"
            ).fetchall()
        )
        schedules = dict(
            conn.execute(
                """SELECT account_key, rate_changes_json FROM accounts
                   WHERE rate_changes_json IS NOT NULL"""
            ).fetchall()
        )
        due = conn.execute(
            "SELECT trip_key, item_key, due_on FROM trip_items WHERE due_on IS NOT NULL"
        ).fetchall()
        maybe = conn.execute(
            "SELECT trip_key, item_key, maybe FROM trip_items WHERE maybe IS NOT NULL"
        ).fetchall()
    assert original == {"pinecrest-mortgage": 300_000_00, "van-loan": 42_000_00}
    assert {k: json.loads(v) for k, v in schedules.items()} == {
        "orchard-card": sample["orchard-card"]["rate_changes"]
    }
    assert [tuple(r) for r in due] == [("ski-weekend", "rental-car", "2026-11-10")]
    assert [tuple(r) for r in maybe] == [("beach-week", "snorkel-tour", 1)]


def test_the_sample_goals_read_back(samples: Settings) -> None:
    runs = _runs(samples)
    assert readers.goals(runs) == GOALS
    assert [g.key for g in GOALS] == ["college-jamie", "emergency-fund"]
    assert readers.section_source(runs, "goals") == MONTHLY


def test_a_monthly_run_with_goals_null_reads_as_no_goals(
    settings: Settings, tmp_path: Path
) -> None:
    """Null is stored as sent; the reader takes it as no goals, like [], not an error."""
    data = goals_run()(tmp_path).parent
    edit_snapshot(data / MONTHLY, lambda snap: snap.update(goals=None))
    migrate(settings=settings)
    statuses = [import_folder(data / run, settings=settings).status for run in SAMPLE_RUNS]
    assert statuses == [IMPORTED] * len(SAMPLE_RUNS)
    with read_connection(settings.db_path) as conn:
        [(raw,)] = conn.execute(
            "SELECT json FROM run_sections WHERE run_id = ? AND section = 'goals'", (MONTHLY,)
        ).fetchall()
        assert raw == "null"
        assert readers.goals(readers.monthly_runs(conn)) == []
