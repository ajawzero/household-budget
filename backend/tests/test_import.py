"""Importer end to end: good samples, idempotency, order, bad cases, the poll loop."""

from __future__ import annotations

import itertools
import json
import logging
import shutil
import sqlite3
from pathlib import Path

import pytest
from bad_cases import (
    BAD_CASES,
    INJECTED_VALUES,
    BadCase,
    edit_csv,
    edit_snapshot,
    original_run,
    read_json,
    refresh_manifest,
    write_json,
)
from conftest import SAMPLE_RUNS, SAMPLES, copy_run

from app import cli
from app.config import Settings
from app.db.connect import read_connection
from app.db.migrate import migrate
from app.importer.models import RULES
from app.importer.runner import (
    FAILED,
    IMPORTED,
    UNCHANGED,
    WAITING,
    import_folder,
    import_path,
    reapply,
    retry_run,
)
from app.importer.scan import NOT_READY, ImportPoller, candidate_folders
from app.importer.validate import rule_message
from app.services.fingerprint import fingerprint

MAPLE = "maple-joint-checking:2026-09-30"


def fp(settings: Settings) -> str:
    with read_connection(settings.db_path) as conn:
        return fingerprint(conn)


def import_runs(settings: Settings, runs=SAMPLE_RUNS, base: Path = SAMPLES) -> list:
    migrate(settings=settings)
    return [import_folder(base / r, settings=settings) for r in runs]


@pytest.fixture
def forward_fp(tmp_path_factory, monkeypatch) -> str:
    """Fingerprint after importing every sample in run order into a fresh DB."""
    d = tmp_path_factory.mktemp("forward")
    s = Settings(db_path=d / "f.db", import_dir=d / "imp", backup_dir=d / "bk")
    results = import_runs(s)
    assert [r.status for r in results] == [IMPORTED] * 4
    return fp(s)


def db_dump(settings: Settings) -> str:
    conn = sqlite3.connect(settings.db_path)
    try:
        return "\n".join(conn.iterdump())
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Good samples and idempotency
# --------------------------------------------------------------------------


def test_every_sample_imports_and_reimport_is_zero_changes(settings, capsys) -> None:
    assert cli.main(["import", str(SAMPLES)]) == 0
    first = capsys.readouterr().out
    for run in SAMPLE_RUNS:
        assert f"{run}  imported" in first
    fp1 = first.strip().splitlines()[-1]
    assert fp1.startswith("fingerprint ")

    assert cli.main(["import", str(SAMPLES)]) == 0
    second = capsys.readouterr().out
    assert "4 runs, 0 changes" in second
    assert second.strip().splitlines()[-1] == fp1
    for run in SAMPLE_RUNS:
        assert f"{run}  unchanged  0 changes" in second


def test_import_runs_rows_and_counts(settings) -> None:
    results = import_runs(settings)
    with read_connection() as conn:
        rows = {r["run_id"]: r for r in conn.execute("SELECT * FROM import_runs")}
        assert {r["status"] for r in rows.values()} == {"imported"}
        counts = json.loads(rows["2026-10-14"]["counts_json"])
        # The correction changes one description (new key) and one category (same key).
        assert (counts["inserted"], counts["updated"], counts["removed"]) == (1, 1, 1)
        assert counts["replaced"] == 1 and counts["drift"] == 0
        assert rows["2026-10-12"]["mode"] == "monthly"
        assert rows["2026-10-06-2"]["run_seq"] == 2
        sections = {
            r[0]
            for r in conn.execute("SELECT section FROM run_sections WHERE run_id = '2026-10-12'")
        }
        assert {"meta", "accounts", "statements", "review"} <= sections
        assert f"rows:{MAPLE}" in sections
    assert results[0].changes > 0


def test_correction_wins_and_rows_follow(settings) -> None:
    import_runs(settings)
    with read_connection() as conn:
        versions = dict(
            conn.execute(
                "SELECT run_id, is_current FROM statements WHERE statement_key = ?", (MAPLE,)
            )
        )
        assert versions == {"2026-10-12": 0, "2026-10-14": 1}
        rows = conn.execute(
            "SELECT description, category_key, removed_at FROM transactions "
            "WHERE statement_key = ? AND txn_date IN ('2026-09-14', '2026-09-16')",
            (MAPLE,),
        ).fetchall()
        live = {(r[0], r[1]) for r in rows if r[2] is None}
        gone = {(r[0], r[1]) for r in rows if r[2] is not None}
        assert live == {("FIBERNET INTERNET", "internet"), ("GREEN LEAF MARKET", "groceries")}
        assert gone == {("FIBERNET INTERNT", "internet")}
        # removed rows stay out of the view
        assert (
            conn.execute(
                "SELECT count(*) FROM v_transactions WHERE description = ?", ("FIBERNET INTERNT",)
            ).fetchone()[0]
            == 0
        )


def test_backfill_does_not_change_known_accounts_and_tombstones_apply(settings) -> None:
    import_runs(settings, SAMPLE_RUNS[:2])
    with read_connection() as conn:
        row = conn.execute(
            "SELECT closed_on, active FROM accounts WHERE account_key = 'old-store-card'"
        ).fetchone()
        assert tuple(row) == (None, 1)  # the second backfill may not change a known account
        assert (
            conn.execute(
                "SELECT count(*) FROM balances WHERE account_key = 'family-car' "
                "AND as_of = '2026-06-30'"
            ).fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT opened_on FROM accounts WHERE account_key = 'cobalt-card'"
            ).fetchone()[0]
            == "2026-05-01"
        )
    import_folder(SAMPLES / "2026-10-12", settings=settings)
    with read_connection() as conn:
        row = conn.execute(
            "SELECT closed_on, active FROM accounts WHERE account_key = 'old-store-card'"
        ).fetchone()
        assert tuple(row) == ("2026-07-20", 0)
        assert (
            conn.execute(
                "SELECT count(*) FROM balances WHERE account_key = 'family-car' "
                "AND as_of = '2026-06-30'"
            ).fetchone()[0]
            == 0
        )


def test_reverse_arrival_through_poll_matches_forward(settings, forward_fp) -> None:
    migrate(settings=settings)
    poller = ImportPoller(settings)
    statuses = []
    for run in reversed(SAMPLE_RUNS):
        copy_run(run, settings.import_dir)
        statuses.append({r.run_id: r.status for r in poller.poll_once()})
    assert statuses[0] == {"2026-10-14": WAITING}
    assert statuses[-1] == {r: IMPORTED for r in SAMPLE_RUNS}
    assert fp(settings) == forward_fp
    assert poller.poll_once() == []


def test_correction_before_original_matches_forward(settings, forward_fp) -> None:
    results = import_runs(settings, ("2026-10-06", "2026-10-06-2", "2026-10-14", "2026-10-12"))
    assert [r.status for r in results] == [IMPORTED] * 4
    assert fp(settings) == forward_fp
    with read_connection() as conn:
        current = conn.execute(
            "SELECT run_id FROM statements WHERE statement_key = ? AND is_current = 1", (MAPLE,)
        ).fetchone()[0]
    assert current == "2026-10-14"


@pytest.mark.parametrize("order", list(itertools.permutations(range(4))))
def test_any_order_one_folder_at_a_time(settings, forward_fp, order) -> None:
    """Every arrival order, one run folder per call: waiting runs are found next to
    the given folder and retried, so the last call reaches the forward fingerprint."""
    migrate(settings=settings)
    for i in order:
        import_path(SAMPLES / SAMPLE_RUNS[i], settings=settings)
    with read_connection() as conn:
        statuses = {r[0] for r in conn.execute("SELECT status FROM import_runs")}
    assert statuses == {IMPORTED}
    assert fp(settings) == forward_fp


def test_cli_reverse_order_matches_forward(settings, forward_fp, capsys) -> None:
    """The Phase 1 done-when check: `cli import <run>` newest first."""
    migrate(settings=settings)
    codes = [cli.main(["import", str(SAMPLES / run)]) for run in reversed(SAMPLE_RUNS)]
    out = capsys.readouterr().out
    assert codes == [1, 1, 1, 0]  # the first three wait for the earliest run
    assert "waiting  not imported." in out
    assert fp(settings) == forward_fp
    assert out.strip().endswith(forward_fp)
    for run in SAMPLE_RUNS:
        assert f"{run}  imported" in out


def test_reapply_matches_live(settings, capsys) -> None:
    import_runs(settings)
    live = fp(settings)
    counts = reapply(settings)
    assert counts == {"runs": 4, "changes": 0, "tables": {}}
    assert fp(settings) == live
    assert cli.main(["reapply"]) == 0
    assert capsys.readouterr().out.strip().endswith(live)
    assert cli.main(["fingerprint"]) == 0
    assert capsys.readouterr().out.strip() == live
    with pytest.raises(SystemExit):  # --display-name is required
        cli.main(["create-user", "someone"])


def test_reapply_rebuilds_after_tables_are_damaged(settings) -> None:
    import_runs(settings)
    live = fp(settings)
    with read_connection() as conn:
        conn.execute("DELETE FROM holdings")
        conn.execute("UPDATE transactions SET category_key = 'misc'")
    assert fp(settings) != live
    reapply(settings)
    assert fp(settings) == live


def test_dry_run_changes_nothing(settings, capsys) -> None:
    migrate(settings=settings)
    empty = fp(settings)
    assert cli.main(["import", str(SAMPLES), "--dry-run"]) == 1  # later runs wait on earlier
    out = capsys.readouterr().out
    assert "2026-10-06  dry-run" in out
    assert fp(settings) == empty
    with read_connection() as conn:
        assert conn.execute("SELECT count(*) FROM import_runs").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM events").fetchone()[0] == 0


def _relabel_run(src: Path, dst: Path, run_id: str) -> Path:
    shutil.copytree(src, dst / run_id)
    run = dst / run_id
    for name in ("snapshot.json", "manifest.json"):
        obj = read_json(run / name)
        obj["run_id"] = run_id
        write_json(run / name, obj)
    refresh_manifest(run)
    return run


def test_later_backfill_resending_statements_counts_drift(settings, tmp_path, forward_fp) -> None:
    import_runs(settings)
    run = _relabel_run(SAMPLES / "2026-10-06", tmp_path, "2026-10-16")

    def recategorize(rows: list[dict]) -> None:
        rows[0]["category_key"] = "misc" if rows[0]["category_key"] != "misc" else "gifts"

    edit_csv(run, "transactions/harbor-checking-2026-01-31.csv", recategorize)
    r = import_folder(run, settings=settings)
    assert r.status == IMPORTED
    assert r.counts["drift"] == 1
    assert r.counts["superseded"] == r.counts["statements"] - 1
    assert r.counts["changes"] == 0
    assert [w.rule for w in r.warnings] == ["statement-drift"]
    assert fp(settings) == forward_fp


def test_waiting_run_imports_after_earlier_runs(settings) -> None:
    migrate(settings=settings)
    r = import_folder(SAMPLES / "2026-10-12", settings=settings)
    assert r.status == WAITING
    assert {i.rule for i in r.issues} <= {"account-unknown", "person-unknown"}
    results = {x.run_id: x.status for x in import_path(SAMPLES, settings=settings)}
    assert results == {run: IMPORTED for run in SAMPLE_RUNS}


# --------------------------------------------------------------------------
# Bad cases
# --------------------------------------------------------------------------


@pytest.mark.parametrize("case", BAD_CASES, ids=[c.case_id for c in BAD_CASES])
def test_bad_case_rejected_without_leaking(
    case: BadCase, use_private_rules: Settings, tmp_path: Path, capsys, caplog
) -> None:
    settings = use_private_rules
    caplog.set_level(logging.DEBUG)
    import_runs(settings, SAMPLE_RUNS[:2])
    run_dir = case.builder(tmp_path)
    if case.case_id == "run-changed-after-import":
        assert import_folder(original_run(run_dir), settings=settings).status == IMPORTED
    capsys.readouterr()

    assert cli.main(["import", str(run_dir)]) == 1
    out = capsys.readouterr()
    text = out.out + out.err
    assert case.expected_rule_id in text

    with read_connection() as conn:
        row = conn.execute("SELECT * FROM import_runs WHERE run_id = ?", (run_dir.name,)).fetchone()
    if case.case_id == "run-changed-after-import":
        assert row["status"] == "imported"  # the imported run stays as it was
    else:
        # unknown keys may be fixed by an earlier run that has not arrived yet
        waits = case.expected_rule_id in ("account-unknown", "person-unknown")
        assert row["status"] == (WAITING if waits else FAILED)
        rules = {e["rule"] for e in json.loads(row["error_json"])}
        assert case.expected_rule_id in rules
    dump = db_dump(settings)
    for rule in RULES:  # fixed messages may show an example form, like 1234-5678
        text = text.replace(rule_message(rule), "")
    for value in INJECTED_VALUES:
        assert value not in text, value
        assert value not in caplog.text, value
        assert value not in dump, value


def test_failed_run_is_skipped_until_retry(settings, tmp_path) -> None:
    import_runs(settings, SAMPLE_RUNS[:2])
    run = copy_run("2026-10-12", settings.import_dir)
    path = run / "transactions/maple-joint-checking-2026-09-30.csv"
    good = path.read_bytes()
    path.write_bytes(good.replace(b"GREEN LEAF MARKET", b"GREEN LEAF MARKEX", 1))
    assert import_folder(run, settings=settings).status == FAILED
    assert import_folder(run, settings=settings).status == "skipped"
    path.write_bytes(good)
    assert retry_run("2026-10-12", settings=settings).status == IMPORTED


# --------------------------------------------------------------------------
# The poll loop
# --------------------------------------------------------------------------


def test_skipped_names_are_ignored(settings) -> None:
    migrate(settings=settings)
    copy_run("2026-10-06", settings.import_dir, "2026-10-06.partial")
    copy_run("2026-10-06", settings.import_dir, "_schema")
    copy_run("2026-10-06", settings.import_dir, ".2026-10-06")
    (settings.import_dir / "notes-folder").mkdir()
    assert candidate_folders(settings.import_dir) == []
    poller = ImportPoller(settings)
    assert poller.poll_once() == []
    (settings.import_dir / "2026-10-06.partial").rename(settings.import_dir / "2026-10-06")
    assert [(r.run_id, r.status) for r in poller.poll_once()] == [("2026-10-06", IMPORTED)]


def test_stability_wait_then_import(settings) -> None:
    migrate(settings=settings)
    run = copy_run("2026-10-06", settings.import_dir)
    path = run / "transactions/harbor-checking-2026-01-31.csv"
    good = path.read_bytes()
    path.write_bytes(good[: len(good) // 2])  # still copying
    poller = ImportPoller(settings)
    assert [r.status for r in poller.poll_once()] == [NOT_READY]
    path.write_bytes(good)  # copy finished
    assert [r.status for r in poller.poll_once()] == [IMPORTED]


def test_stability_wait_then_fail_when_unchanged(settings) -> None:
    migrate(settings=settings)
    run = copy_run("2026-10-06", settings.import_dir)
    path = run / "transactions/harbor-checking-2026-01-31.csv"
    path.write_bytes(path.read_bytes().replace(b"\n2026", b"\n2026", 1) + b"")
    path.write_bytes(path.read_bytes()[:-10] + b"0000000000")
    poller = ImportPoller(settings)
    assert [r.status for r in poller.poll_once()] == [NOT_READY]
    with read_connection() as conn:
        assert conn.execute("SELECT count(*) FROM import_runs").fetchone()[0] == 0
    results = poller.poll_once()
    assert [r.status for r in results] == [FAILED]
    assert "hash-mismatch" in {i.rule for i in results[0].issues}
    assert poller.poll_once() == []  # same manifest: not tried again


def test_missing_manifest_waits_then_fails(settings) -> None:
    migrate(settings=settings)
    run = copy_run("2026-10-06", settings.import_dir)
    (run / "manifest.json").unlink()
    poller = ImportPoller(settings)
    assert [r.status for r in poller.poll_once()] == [NOT_READY]
    results = poller.poll_once()
    assert [i.rule for i in results[0].issues] == ["manifest-missing"]


def test_changed_manifest_rejected_once(settings) -> None:
    migrate(settings=settings)
    run = copy_run("2026-10-06", settings.import_dir)
    poller = ImportPoller(settings)
    assert [r.status for r in poller.poll_once()] == [IMPORTED]
    before = fp(settings)

    def tweak(rows: list[dict]) -> None:
        rows[0]["merchant"] = "Changed"

    edit_csv(run, "transactions/harbor-checking-2026-01-31.csv", tweak)
    results = poller.poll_once()
    assert [(r.status, [i.rule for i in r.issues]) for r in results] == [(FAILED, ["run-changed"])]
    assert poller.poll_once() == []
    assert ImportPoller(settings).poll_once()[0].issues[0].rule == "run-changed"
    with read_connection() as conn:
        assert conn.execute("SELECT status FROM import_runs").fetchone()[0] == "imported"
        assert (
            conn.execute("SELECT count(*) FROM events WHERE kind = 'import_rejected'").fetchone()[0]
            == 1
        )
    assert fp(settings) == before
    assert import_folder(SAMPLES / "2026-10-06", settings=settings).status == UNCHANGED


def test_import_logs_hold_no_values(settings, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    import_runs(settings)
    reapply(settings)
    for value in (
        "GREEN LEAF",
        "Green Leaf",
        "-42.97",
        "4297",
        "maple-joint-checking",
        "Sample Town",
    ):
        assert value not in caplog.text
    assert "2026-10-12" in caplog.text  # run ids are fine


def test_cashflow_view_classifies_sample_rows(settings) -> None:
    import_runs(settings)
    with read_connection() as conn:
        flows = dict(
            conn.execute(
                "SELECT description, flow FROM v_cashflow_rows WHERE month = '2026-09' "
                "AND description IN ('PINECREST MORTGAGE PAYMENT', 'COBALT CARD PAYMENT', "
                "'PAYMENT RECEIVED - THANK YOU', 'TRANSFER TO SAVINGS', "
                "'TRANSFER TO LAKEVIEW BROKERAGE', 'INTEREST PAID', 'GREEN LEAF MARKET')"
            )
        )
        all_flows = {r[0] for r in conn.execute("SELECT DISTINCT flow FROM v_cashflow_rows")}
    assert flows["PINECREST MORTGAGE PAYMENT"] == "spending"
    assert flows["COBALT CARD PAYMENT"] == "transfer"
    assert flows["PAYMENT RECEIVED - THANK YOU"] == "transfer"
    assert flows["TRANSFER TO SAVINGS"] == "saved"
    assert flows["TRANSFER TO LAKEVIEW BROKERAGE"] == "saved"
    assert flows["INTEREST PAID"] == "income"
    assert flows["GREEN LEAF MARKET"] == "spending"
    assert all_flows <= {
        "spending",
        "card_no_stmt",
        "income",
        "saved",
        "transfer",
        "reimbursable",
        "reimbursement",
    }


def test_cashflow_view_payment_basis(settings) -> None:
    """Payments to a card tracked by payments are spending ("payment basis"). The
    cycle that uses a statement counts its rows, and its payment is a transfer.
    Payments to the other card of the same issuer stay transfers."""
    import_runs(settings)
    with read_connection() as conn:
        rows = conn.execute(
            """SELECT account_key, txn_date, counterparty_account_key, flow, payment_basis,
                      category_key, out_cents
               FROM v_cashflow_rows
               WHERE counterparty_account_key IN ('bramble-store-card', 'cobalt-card')
                  OR account_key = 'bramble-store-card'"""
        ).fetchall()
    to_store = [r for r in rows if r["counterparty_account_key"] == "bramble-store-card"]
    basis = [r for r in to_store if r["payment_basis"]]
    assert len(basis) == 8 and {r["flow"] for r in basis} == {"spending"}
    assert {r["category_key"] for r in basis} == {"clothing"}
    statement_paid = [r for r in to_store if not r["payment_basis"]]
    assert [(r["txn_date"], r["flow"]) for r in statement_paid] == [("2026-09-24", "transfer")]
    other_card = [r for r in rows if r["counterparty_account_key"] == "cobalt-card"]
    assert other_card and {r["flow"] for r in other_card} == {"transfer"}
    store_rows = {
        (r["txn_date"], r["flow"]) for r in rows if r["account_key"] == "bramble-store-card"
    }
    assert store_rows == {
        ("2026-08-24", "transfer"),
        ("2026-08-30", "spending"),
        ("2026-09-06", "spending"),
    }


def test_plan_groups_must_add_up_to_planned_spending(settings, tmp_path) -> None:
    """Planned spending includes saved and invested. A plan whose groups miss it warns."""
    migrate(settings=settings)
    for run in ("2026-10-06", "2026-10-06-2"):
        assert import_folder(SAMPLES / run, settings=settings).status == IMPORTED
    run = copy_run("2026-10-12", tmp_path)
    good = import_folder(SAMPLES / "2026-10-12", settings=settings, dry_run=True)
    assert "plan-groups-sum" not in {w.rule for w in good.warnings}

    def drop_saved(snap: dict) -> None:
        plan = snap["cashflow_plan"][0]
        plan["by_group"] = [g for g in plan["by_group"] if g["key"] != "saved-invested"]

    edit_snapshot(run, drop_saved)
    r = import_folder(run, settings=settings)
    assert r.status == IMPORTED
    assert [(w.rule, w.where) for w in r.warnings if w.rule == "plan-groups-sum"] == [
        ("plan-groups-sum", "snapshot.json:cashflow_plan[0].by_group")
    ]
