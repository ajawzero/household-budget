"""Privacy rescan and purge (PLAN 5.8, docs/plans/privacy-rescan.md).

Fake samples and a fake private list, written per test: STARLIGHT CINEMA is a
"personal subscription", BLUE DOOR an employer term, STATEMENT CREDIT (the
Summit card's +25.00 on 2026-08-18) a subscription for the credit choice
tests, and WAVE MOBILE a subscription that is also a recurring key.
"""

from __future__ import annotations

import copy
import json
import logging
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from bad_cases import MONTHLY, PRIVATE_RULES_EXAMPLE, SAMPLE_GOALS, strip_newer, v14_run
from conftest import SAMPLE_RUNS
from fastapi.testclient import TestClient
from test_rewards_choices import TRAVEL, choose, lost, review, statement_credit
from test_rewards_match import key_of, q

from app import config
from app.cli import main as cli_main
from app.config import Settings, get_settings
from app.db.connect import connect, read_connection, write_connection
from app.db.migrate import migrate
from app.importer import rescan as rs
from app.importer.apply import load_stored_run
from app.importer.models import snapshot_issues
from app.importer.runner import IMPORTED, import_folder, reapply
from app.importer.sanitize import SUBSCRIPTION_TEXT, PrivateRules, load_private_rules
from app.jobs import privacy
from app.services import overrides as ov
from app.services import rewards_match as rm
from app.services.balances import clear_cache
from app.services.fingerprint import fingerprint
from app.state import readers

TODAY = date(2026, 10, 20)
NOW = "2026-10-20T12:00:00+00:00"
LEAKS = ("starlight", "blue door", "bluedoor", "statement credit", "statementcredit")


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    clear_cache()


def rules_file(tmp_path: Path, subs: list[str], employer: list[str], name: str = "p.json") -> Path:
    path = tmp_path / name
    path.write_text(
        json.dumps({"personal_subscriptions": subs, "employer_terms": employer}), encoding="utf-8"
    )
    return path


def use_rules(monkeypatch: pytest.MonkeyPatch, path: Path) -> Settings:
    monkeypatch.setenv("BUDGET_PRIVATE_RULES", str(path))
    get_settings.cache_clear()
    return get_settings()


def private_for(tmp_path: Path) -> PrivateRules:
    return load_private_rules(
        rules_file(tmp_path, ["Starlight Cinema", "Statement Credit"], ["Blue Door"])
    )


def dump(settings: Settings, *, skip: tuple[str, ...] = ()) -> str:
    """Every row of every table, as SQL text."""
    conn = connect(settings.db_path)
    try:
        lines = [
            line
            for line in conn.iterdump()
            if not any(line.startswith(f'INSERT INTO "{t}"') for t in skip)
        ]
    finally:
        conn.close()
    return "\n".join(lines)


def leaks(text: str) -> list[str]:
    low = text.lower()
    return [w for w in LEAKS if w in low]


def key_for(settings: Settings, description: str, day: str) -> str:
    [(key,)] = q(
        settings,
        "SELECT txn_key FROM transactions WHERE description = ? AND txn_date = ? "
        "AND removed_at IS NULL",
        (description, day),
    )
    return key


def first_row(settings: Settings, description: str) -> sqlite3.Row:
    return q(
        settings,
        """SELECT txn_key, txn_date, amount_cents FROM transactions
           WHERE description = ? AND removed_at IS NULL ORDER BY txn_date LIMIT 1""",
        (description,),
    )[0]


def edit(settings: Settings, key: str, **changes: object) -> None:
    with write_connection(settings.db_path) as conn:
        ov.apply_edit(conn, [key], changes, user_id=1, via="edit", now=NOW)


def add_thread(settings: Settings, thread_id: str, title: str, text: str) -> None:
    with write_connection(settings.db_path) as conn:
        conn.execute(
            """INSERT INTO ai_threads (thread_id, user_id, created_at, updated_at, model,
                 effort, title) VALUES (?, 1, ?, ?, 'm', 'low', ?)""",
            (thread_id, NOW, NOW, title),
        )
        conn.execute(
            """INSERT INTO ai_messages (thread_id, seq, role, kind, content_json, created_at)
               VALUES (?, 1, 'user', 'question', ?, ?)""",
            (thread_id, json.dumps({"text": text}), NOW),
        )


def seed(settings: Settings, admin: TestClient) -> dict[str, sqlite3.Row | str]:
    """Edits, a credit choice, Ask threads, a match word and a merchant rule
    that hold the fake private terms. Returns the rows and keys it used."""
    star = first_row(settings, "STARLIGHT CINEMA")
    blue = first_row(settings, "BLUE DOOR BISTRO")
    credit = statement_credit(settings)
    edit(settings, star["txn_key"], notes="Starlight Cinema yearly", category_key="entertainment")
    edit(settings, blue["txn_key"], merchant="Blue Door place", tags=["blue door"])
    edit(settings, credit, notes="kept")
    assert choose(admin, credit, benefit_key=TRAVEL).status_code == 200
    add_thread(settings, "t-leak", "About starlight cinema", "How much at Starlight Cinema?")
    add_thread(settings, "t-clean", "Groceries", "How much at Green Leaf Market?")
    with write_connection(settings.db_path) as conn:
        conn.execute(
            """INSERT INTO reward_match_terms (benefit_key, term, updated_at)
               VALUES (?, 'STATEMENT CREDIT', ?), (?, 'TRAVEL', ?)""",
            (TRAVEL, NOW, TRAVEL, NOW),
        )
        conn.execute(
            """INSERT INTO merchant_rules (match_kind, pattern, category_key, created_from_txn_key,
                 created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "description_contains",
                "STARLIGHT CINEMA",
                "entertainment",
                star["txn_key"],
                NOW,
                NOW,
            ),
        )
    return {"star": star, "blue": blue, "credit": credit}


# --------------------------------------------------------------------------
# Masking
# --------------------------------------------------------------------------


def test_masking_rules(tmp_path: Path) -> None:
    private = private_for(tmp_path)
    assert private.mask("Green Leaf Market") is None
    assert private.mask("STARLIGHT CINEMA #4") == "Personal subscription"
    assert private.mask("starlight-cinema gift") == "Personal subscription"
    assert private.mask("BLUE DOOR BISTRO") == "BISTRO"
    assert private.mask("Blue  Door") == "Employer"
    assert private.mask("BLUEDOOR") == "Employer"  # squashed match, nothing left


def test_rescan_cleans_everything_and_keeps_edits(
    samples: Settings, admin: TestClient, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    keys = seed(samples, admin)
    with read_connection(samples.db_path) as conn:
        assert ov.orphaned_edits(conn) == []
    old_sig = rm.row_sig("STATEMENT CREDIT", "Statement Credit")
    assert old_sig in dump(samples)
    caplog.set_level(logging.INFO)

    report = privacy.run_rescan(samples, private_for(tmp_path))

    s = report.summary()
    assert s["masked"]["runs:rows.description"] > 0 and s["masked"]["runs:rows.merchant"] > 0
    assert s["masked"]["transaction_overrides.notes"] == 1
    assert s["deleted"] == {"ai_threads": 1, "merchant_rules": 1, "reward_match_terms": 1}
    assert s["listed"] == {} and s["purge"] == [] and s["lost"] == 0
    assert s["rekeyed"] > 0 and s["runs"] and s["since"]
    text = dump(samples)
    assert leaks(text) == []
    assert old_sig not in text  # no hash of the leaked text is left
    for old in (keys["star"]["txn_key"], keys["blue"]["txn_key"], keys["credit"]):
        assert old not in text  # the old keys are hashes of the leaked text

    # The rows, their edits and the choice moved to the masked rows' keys.
    star = key_for(samples, "Personal subscription", keys["star"]["txn_date"])
    blue = key_for(samples, "BISTRO", keys["blue"]["txn_date"])
    credit = key_for(samples, "Personal subscription", "2026-08-18")
    [o] = q(samples, "SELECT * FROM transaction_overrides WHERE txn_key = ?", (star,))
    assert o["notes"] == "Personal subscription" and o["category_key"] == "entertainment"
    assert json.loads(o["guard_json"])["desc_norm"] == "PERSONAL SUBSCRIPTION"
    [b] = q(samples, "SELECT * FROM transaction_overrides WHERE txn_key = ?", (blue,))
    assert b["merchant"] == "place" and json.loads(b["tags_json"]) == ["Employer"]
    assert json.loads(b["guard_json"])["desc_norm"] == "BISTRO"
    assert q(samples, "SELECT merchant FROM transactions WHERE txn_key = ?", (blue,))[0][0] == (
        "Bistro"
    )
    [d] = q(samples, "SELECT * FROM reward_credit_decisions")
    assert d["txn_key"] == credit and d["benefit_key"] == TRAVEL
    guard = json.loads(d["guard_json"])
    new_sig = rm.row_sig("Personal subscription", "Personal subscription")
    assert guard["row_sig"] == new_sig and guard["near"][credit] == new_sig
    assert old_sig not in guard["near"].values()
    assert lost(review(admin)) == []
    assert [r[0] for r in q(samples, "SELECT thread_id FROM ai_threads")] == ["t-clean"]
    assert q(samples, "SELECT count(*) FROM ai_messages WHERE thread_id = 't-leak'")[0][0] == 0
    assert [r[0] for r in q(samples, "SELECT term FROM reward_match_terms")] == ["TRAVEL"]

    # reapply gives the same fingerprint and no edit loses its row.
    with read_connection(samples.db_path) as conn:
        before = fingerprint(conn)
        assert ov.orphaned_edits(conn) == []
    reapply(samples)
    with read_connection(samples.db_path) as conn:
        assert fingerprint(conn) == before
        assert ov.orphaned_edits(conn) == []
    assert lost(review(admin)) == []

    # One job_runs row; neither it nor the logs hold a term.
    [job] = q(samples, "SELECT * FROM job_runs WHERE job = 'privacy_rescan'")
    assert job["status"] == "ok" and json.loads(job["summary_json"])["mode"] == "rescan"
    assert leaks(caplog.text) == []


def test_a_matching_key_is_listed_never_masked(
    samples: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """WAVE MOBILE rows carry the recurring key wave-mobile: the text is masked,
    the key is listed with its runs, and no output shows it."""
    use_rules(monkeypatch, rules_file(tmp_path, ["Wave Mobile"], []))
    assert cli_main(["privacy-rescan"]) == 0
    out = capsys.readouterr()
    assert "wave" not in (out.out + out.err).lower()
    [listed] = [line for line in out.out.splitlines() if line.startswith("Listed keys")]
    assert "runs:recurring.key 1" in listed and "runs:rows.recurring_key" in listed
    assert "Purge these runs: " in out.out
    assert q(samples, "SELECT count(*) FROM transactions WHERE recurring_key = 'wave-mobile'")[0][0]
    assert not q(samples, "SELECT 1 FROM transactions WHERE description LIKE '%WAVE%'")
    [job] = q(samples, "SELECT summary_json FROM job_runs WHERE job = 'privacy_rescan'")
    summary = json.loads(job[0])
    assert summary["purge"] and "wave" not in job[0].lower()


def test_dry_run_changes_nothing(
    samples: Settings, admin: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(samples, admin)
    use_rules(monkeypatch, rules_file(tmp_path, ["Starlight Cinema"], ["Blue Door"]))
    before = dump(samples, skip=("job_runs",))
    assert cli_main(["privacy-rescan", "--dry-run"]) == 0
    assert dump(samples, skip=("job_runs",)) == before
    with read_connection(samples.db_path) as conn:
        assert rs.stored_hash(conn) is None


def test_no_rules_set_does_nothing(samples: Settings) -> None:
    assert cli_main(["privacy-rescan"]) == 1
    assert privacy.startup_check(samples) == "off"


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------


def test_startup_lists_first_then_cleans_on_a_new_file(
    samples: Settings, admin: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(samples, admin)
    first = use_rules(monkeypatch, rules_file(tmp_path, ["Starlight Cinema"], [], "a.json"))
    before = dump(samples, skip=("job_runs", "settings"))
    assert privacy.startup_check(first) == "listed"
    assert dump(samples, skip=("job_runs", "settings")) == before
    [job] = q(samples, "SELECT summary_json FROM job_runs WHERE job = 'privacy_rescan'")
    assert json.loads(job[0])["mode"] == "list" and json.loads(job[0])["masked"]
    assert privacy.startup_check(first) == "same"

    second = use_rules(monkeypatch, rules_file(tmp_path, ["Starlight Cinema"], ["Blue Door"]))
    assert privacy.startup_check(second) == "cleaned"
    assert leaks(dump(samples)) == ["statement credit"]  # not on this list
    assert privacy.startup_check(second) == "same"
    with read_connection(samples.db_path) as conn:
        assert rs.stored_hash(conn) == load_private_rules(second.private_rules).digest


def test_the_app_starts_when_the_rescan_fails(
    samples: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = use_rules(monkeypatch, rules_file(tmp_path, ["Green Leaf"], [], "a.json"))
    assert privacy.startup_check(first) == "listed"
    second = use_rules(monkeypatch, rules_file(tmp_path, ["Starlight Cinema"], []))

    def boom(*args: object) -> None:
        raise rs.RescanError("still-matches")

    monkeypatch.setattr(rs, "replay", boom)
    before = dump(samples, skip=("job_runs",))
    assert privacy.startup_check(second) == "failed"
    assert dump(samples, skip=("job_runs",)) == before  # rolled back, old hash kept
    statuses = [
        r[0] for r in q(samples, "SELECT status FROM job_runs WHERE job = ?", (rs.JOB_NAME,))
    ]
    assert statuses == ["ok", "failed"]


# --------------------------------------------------------------------------
# Purge
# --------------------------------------------------------------------------


def test_purge_run(
    samples: Settings, admin: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    """The Towne Outfitters return (2026-06-29) is in one stored run only
    (2026-10-06-2). Purging that run drops the choice's row_sig, so the choice
    shows as lost."""
    credit = key_of(samples, "TOWNE OUTFITTERS RETURN", "2026-06-29")
    assert choose(admin, credit, benefit_key=TRAVEL).status_code == 200
    [(raw,)] = q(samples, "SELECT guard_json FROM reward_credit_decisions")
    gone_sig = json.loads(raw)["row_sig"]
    runs = q(
        samples,
        """SELECT DISTINCT run_id FROM run_sections WHERE section =
             'rows:' || (SELECT statement_key FROM transactions WHERE txn_key = ?)""",
        (credit,),
    )
    assert [r[0] for r in runs] == ["2026-10-06-2"]
    run = runs[0][0]
    before = dump(samples, skip=("job_runs",))
    assert cli_main(["purge-run", run]) == 0
    assert "Would purge" in capsys.readouterr().out
    assert dump(samples, skip=("job_runs",)) == before

    assert cli_main(["purge-run", run, "--yes"]) == 0
    assert "Purged run 2026-10-06-2" in capsys.readouterr().out
    assert q(samples, "SELECT status FROM import_runs WHERE run_id = ?", (run,))[0][0] == "purged"
    assert not q(samples, "SELECT 1 FROM run_sections WHERE run_id = ?", (run,))
    assert not q(samples, "SELECT 1 FROM transactions WHERE txn_key = ?", (credit,))
    [d] = q(samples, "SELECT guard_json FROM reward_credit_decisions")
    guard = json.loads(d[0])
    assert "row_sig" not in guard and credit not in guard["near"]
    assert gone_sig not in dump(samples)
    assert lost(review(admin)) == [credit]
    [job] = q(samples, "SELECT status FROM job_runs WHERE job = ?", (rs.PURGE_JOB,))
    assert job[0] == "ok"
    assert cli_main(["purge-run", run, "--yes"]) == 1  # already purged


def test_a_purged_rows_hash_is_dropped_by_the_rescan(
    samples: Settings, admin: TestClient, tmp_path: Path
) -> None:
    """A run purged before this release left its rows' hashes in a guard. The
    rescan cannot recompute them, so it drops them and the choice is lost."""
    credit = key_of(samples, "TOWNE OUTFITTERS RETURN", "2026-06-29")
    assert choose(admin, credit, benefit_key=TRAVEL).status_code == 200
    with write_connection(samples.db_path) as conn:  # an old-style purge: guards untouched
        conn.execute("DELETE FROM run_sections WHERE run_id = '2026-10-06-2'")
        conn.execute("UPDATE import_runs SET status = 'purged' WHERE run_id = '2026-10-06-2'")
    reapply(samples)
    privacy.run_rescan(samples, private_for(tmp_path))
    [(raw,)] = q(samples, "SELECT guard_json FROM reward_credit_decisions")
    guard = json.loads(raw)
    assert "row_sig" not in guard and credit not in guard["near"]
    assert lost(review(admin)) == [credit]


def test_key_moves_follow_chains_and_stop_on_a_clash(db: sqlite3.Connection) -> None:
    """a -> b while b -> c: both edits move. Two keys onto one: the rescan stops."""
    a, b, c = ("a" * 64, "b" * 64, "c" * 64)
    db.executemany(
        "INSERT INTO transaction_overrides (txn_key, notes, updated_at) VALUES (?, ?, ?)",
        [(a, "from a", NOW), (b, "from b", NOW)],
    )
    rs._move_keys(db, rs._Moves(keys={a: b, b: c}))
    rows = dict(db.execute("SELECT txn_key, notes FROM transaction_overrides").fetchall())
    assert rows == {b: "from a", c: "from b"}
    with pytest.raises(rs.RescanError, match="key-clash"):
        rs._move_keys(db, rs._Moves(keys={b: a, c: a}))


# --------------------------------------------------------------------------
# Review fixes (Codex adversarial review, Oct 27)
# --------------------------------------------------------------------------


def test_masking_that_merges_two_rows_under_an_edit_stops(
    samples: Settings, tmp_path: Path
) -> None:
    """The Maple statement for September is stored twice: 2026-10-12 has
    FIBERNET INTERNT, the 2026-10-14 correction FIBERNET INTERNET, same day
    and amount. Masked, both get one key. With nothing pointing at the old
    row that is safe; with an orphaned edit on it the rescan cannot tell which
    row the edit meant, so it stops and lists both runs."""
    private = load_private_rules(rules_file(tmp_path, ["Fibernet"], []))
    assert privacy.run_rescan(samples, private, dry_run=True).rekeyed > 0  # no edit: fine
    [old] = q(
        samples,
        "SELECT txn_key FROM transactions WHERE description = 'FIBERNET INTERNT'"
        " AND removed_at IS NOT NULL",
    )
    with write_connection(samples.db_path) as conn:
        conn.execute(
            "INSERT INTO transaction_overrides (txn_key, notes, updated_at) VALUES (?, 'x', ?)",
            (old[0], NOW),
        )
    before = dump(samples, skip=("job_runs",))
    with pytest.raises(rs.RescanError) as caught:
        privacy.run_rescan(samples, private)
    assert caught.value.reason == "key-clash"
    assert {"2026-10-12", "2026-10-14"} <= set(caught.value.summary["purge"])
    assert dump(samples, skip=("job_runs",)) == before


def test_a_warm_server_cache_never_serves_masked_text(
    samples: Settings, admin: TestClient, tmp_path: Path
) -> None:
    """The balance history cache keys on the rescan's stamp, so a rescan from
    the CLI (another process, no cache clear here) is seen by the server."""
    assert "Credit Union" in admin.get("/api/accounts").text
    privacy.run_rescan(samples, load_private_rules(rules_file(tmp_path, [], ["Credit Union"])))
    body = admin.get("/api/accounts").text
    assert "Credit Union" not in body and "Harbor" in body


def test_startup_survives_a_database_that_cannot_record(
    samples: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = use_rules(monkeypatch, rules_file(tmp_path, ["Green Leaf"], [], "a.json"))
    assert privacy.startup_check(first) == "listed"
    second = use_rules(monkeypatch, rules_file(tmp_path, ["Starlight Cinema"], []))

    def broken(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(rs, "rescan", broken)
    monkeypatch.setattr(privacy, "record_run", broken)
    assert privacy.startup_check(second) == "failed"
    with read_connection(samples.db_path) as conn:
        assert rs.stored_hash(conn) == load_private_rules(first.private_rules).digest


# --------------------------------------------------------------------------
# Format 1.4 (docs/plans/format-14.md section 5)
# --------------------------------------------------------------------------

# On the sample private list (samples/private_rules.example.json), in no sample run.
PRIVATE = "EXAMPLE PRIVATE SERVICE"
# Riverside Music Lessons: no row and no key holds its name, so no row is rekeyed.
MUSIC = "music-lessons"


def _private_14(snap: dict) -> None:
    """The monthly run's 1.4 text with the private name in one flag update (of two), a
    plan note and a goal note, and a tag and keep on the music lessons bill."""
    strip_newer(snap, "1.3")
    flag = next(f for f in snap["flags"] if f["key"] == "gym-ending")  # created 2026-10-01
    flag["updates"] = [
        {"on": "2026-10-03", "text": "Called about the fee"},
        {"on": "2026-10-08", "text": f"Asked {PRIVATE} about a bundle"},
    ]
    next(p for p in snap["installment_plans"] if p["key"] == "plan-sofa")["note"] = (
        f"Paid with the {PRIVATE} card"
    )
    bills = {r["key"]: r for r in snap["recurring"]}
    bills[MUSIC].update(tag="hobbies", keep=True)
    bills["cloudtunes"]["tag"] = "streaming"
    goals = copy.deepcopy(SAMPLE_GOALS)
    next(g for g in goals if g["key"] == "emergency-fund")["note"] = f"Gift from {PRIVATE}"
    snap["goals"] = goals


@pytest.fixture
def v14_private(settings: Settings, tmp_path: Path) -> Settings:
    """The samples imported with that 1.4 monthly run, before its text went on the
    private list (the import runs with no private rules)."""
    data = v14_run(_private_14)(tmp_path).parent
    migrate(settings=settings)
    statuses = [import_folder(data / run, settings=settings).status for run in SAMPLE_RUNS]
    assert statuses == [IMPORTED] * len(SAMPLE_RUNS)
    return settings


def stored_recurring(settings: Settings) -> dict[str, dict]:
    [(raw,)] = q(
        settings,
        "SELECT json FROM run_sections WHERE run_id = ? AND section = 'recurring'",
        (MONTHLY,),
    )
    return {r["key"]: r for r in json.loads(raw)}


def test_new_14_text_is_masked_one_item_at_a_time(v14_private: Settings) -> None:
    settings = v14_private
    s = privacy.run_rescan(settings, load_private_rules(PRIVATE_RULES_EXAMPLE)).summary()
    assert s["masked"] == {
        "runs:flags.updates.text": 1,
        "runs:goals.note": 1,
        "runs:installment_plans.note": 1,
    }
    assert s["listed"] == {} and s["purge"] == [] and s["rekeyed"] == 0
    assert s["runs"] == [MONTHLY]
    with read_connection(settings.db_path) as conn:
        runs = readers.monthly_runs(conn)
        flag = next(f for f in readers.flags(runs) if f.key == "gym-ending")
        plans = {p.key: p.note for p in readers.installment_plans(runs)}
        goals = {g.key: g.note for g in readers.goals(runs)}
    # Only the matching update changes; the other one and its dates stay.
    assert [(u.on, u.text) for u in flag.updates or []] == [
        ("2026-10-03", "Called about the fee"),
        ("2026-10-08", SUBSCRIPTION_TEXT),
    ]
    assert plans["plan-sofa"] == SUBSCRIPTION_TEXT
    assert {k: v for k, v in plans.items() if k != "plan-sofa"} == dict.fromkeys(
        set(plans) - {"plan-sofa"}
    )
    assert goals == {"college-jamie": None, "emergency-fund": SUBSCRIPTION_TEXT}
    assert "example private service" not in dump(settings).lower()


def test_a_tagged_bill_that_turns_private_loses_its_tag_and_keep(
    v14_private: Settings, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = v14_private
    before = stored_recurring(settings)
    assert (before[MUSIC]["tag"], before[MUSIC]["keep"]) == ("hobbies", True)
    others = {k: (r.get("tag"), r.get("keep")) for k, r in before.items() if k != MUSIC}
    assert others["cloudtunes"] == ("streaming", None)
    rules = load_private_rules(rules_file(tmp_path, [before[MUSIC]["name"]], []))
    caplog.set_level(logging.INFO)

    s = privacy.run_rescan(settings, rules).summary()

    assert s["masked"] == {
        "runs:recurring.keep": 1,
        "runs:recurring.name": 1,
        "runs:recurring.tag": 1,
    }
    assert s["listed"] == {} and s["purge"] == [] and s["rekeyed"] == 0
    after = stored_recurring(settings)
    assert after[MUSIC]["name"] == SUBSCRIPTION_TEXT
    assert "tag" not in after[MUSIC] and "keep" not in after[MUSIC]
    assert {k: (r.get("tag"), r.get("keep")) for k, r in after.items() if k != MUSIC} == others
    with read_connection(settings.db_path) as conn:
        # The cleaned run passes the import's run checks again (recurring-tag-private).
        assert snapshot_issues(load_stored_run(conn, MONTHLY).snapshot) == []
        bill = next(r for r in readers.recurring(readers.monthly_runs(conn)) if r.key == MUSIC)
        assert (bill.name, bill.tag, bill.keep) == (SUBSCRIPTION_TEXT, None, None)
        assert rs.stored_hash(conn) == rules.digest
        [(raw,)] = conn.execute("SELECT json FROM ai_facts WHERE kind = 'recurring'").fetchall()
    # The rebuilt facts show no tag or keep on either Personal subscription item
    # (keep and tag reach the facts since E-b).
    items = [it for it in json.loads(raw)["items"] if it["name"] == SUBSCRIPTION_TEXT]
    assert len(items) == 2
    assert all(it.get("tag") is None and it.get("keep") is None for it in items)
    assert "riverside" not in dump(settings).lower()
    [job] = q(settings, "SELECT status, summary_json FROM job_runs WHERE job = 'privacy_rescan'")
    assert job["status"] == "ok" and "riverside" not in job["summary_json"].lower()
    assert "riverside" not in caplog.text.lower()


def test_an_employer_cut_keeps_the_tag_and_keep(v14_private: Settings, tmp_path: Path) -> None:
    """Only a name masked to Personal subscription drops its tag and keep."""
    settings = v14_private
    rules = load_private_rules(rules_file(tmp_path, [], ["Riverside"]))
    s = privacy.run_rescan(settings, rules).summary()
    assert s["masked"] == {"runs:recurring.name": 1}
    bill = stored_recurring(settings)[MUSIC]
    assert (bill["name"], bill["tag"], bill["keep"]) == ("Music Lessons", "hobbies", True)


def test_masked_flag_updates_stay_sorted_with_no_repeats(
    settings: Settings, tmp_path: Path
) -> None:
    """Two updates on one day that mask to the same text: one is dropped, and the rest
    are sorted again, so the cleaned run passes the import's order rule."""

    def edit(snap: dict) -> None:
        _private_14(snap)
        flag = next(f for f in snap["flags"] if f["key"] == "gym-ending")
        flag["updates"] = [
            {"on": "2026-10-08", "text": f"Asked {PRIVATE} about a bundle"},
            {"on": "2026-10-08", "text": "Called the bank back"},
            {"on": "2026-10-08", "text": f"Emailed {PRIVATE} again"},
        ]

    data = v14_run(edit)(tmp_path).parent
    migrate(settings=settings)
    statuses = [import_folder(data / run, settings=settings).status for run in SAMPLE_RUNS]
    assert statuses == [IMPORTED] * len(SAMPLE_RUNS)

    s = privacy.run_rescan(settings, load_private_rules(PRIVATE_RULES_EXAMPLE)).summary()

    assert s["masked"]["runs:flags.updates.text"] == 2
    assert s["deleted"] == {"runs:flags.updates": 1}
    with read_connection(settings.db_path) as conn:
        assert snapshot_issues(load_stored_run(conn, MONTHLY).snapshot) == []
        flag = next(f for f in readers.flags(readers.monthly_runs(conn)) if f.key == "gym-ending")
    assert [(u.on, u.text) for u in flag.updates or []] == [
        ("2026-10-08", "Called the bank back"),
        ("2026-10-08", SUBSCRIPTION_TEXT),
    ]
