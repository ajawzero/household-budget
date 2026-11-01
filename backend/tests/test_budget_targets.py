"""Budget target edits (docs/plans/budget-targets.md, migration 0014): the owner's group
targets laid on the chat's plan when a page is read.

Every screen shows the same effective plan (Budget, Cash Flow, the Plan tab, the
category drawer, the Dashboard, Checks), the chat's run data never changes, and
the base rule says who wins when the chat sends a new number. Later chat runs are
fake runs added straight to the database. Fake samples only. Today is 2026-10-20.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import re
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from api_helpers import ADMIN, MEMBER, MEMBER_PW, PROD_ORIGIN, add_users, login
from conftest import SAMPLE_RUNS, SAMPLES
from fastapi.testclient import TestClient
from test_cashflow_p4 import AUG_PLAN, _add_plan_run

from app import cli, config
from app.ai import facts, tools
from app.ai.facts import ensure_facts
from app.ai.safe import AuthorizerDenied, ai_connection, run
from app.config import Settings
from app.db import migrate as migrate_mod
from app.db.connect import read_connection, write_connection, write_transaction
from app.db.migrate import list_migrations, migrate, user_version
from app.importer import rescan as rs
from app.importer.models import cents_to_str, to_cents
from app.importer.runner import IMPORTED, import_folder, reapply
from app.importer.sanitize import load_private_rules
from app.services import budget_targets as bt
from app.services import cashflow as cf
from app.services import dashboard as dash
from app.services.balances import clear_cache
from app.services.budgets import budgets_page
from app.services.checks import checks
from app.services.fingerprint import fingerprint
from app.services.overrides import EditError
from app.state import readers

APP = Path(__file__).resolve().parents[1] / "app"
MIGRATION = Path(migrate_mod.MIGRATIONS_DIR) / "0014_budget_target_edits.sql"
TODAY = date(2026, 10, 20)
OCT = "2026-10"
NOW = "2026-10-20T15:00:00+00:00"
# The sample plan for October (run 2026-10-12): $8,600 of $11,000 income.
OCT_GROUPS = [
    ("housing", "3600.00"),
    ("lifestyle", "3650.00"),
    ("plan-installments", "180.00"),
    ("saved-invested", "700.00"),
    ("services", "250.00"),
    ("transportation", "220.00"),
]
INCOME = 1_100_000
CHAT_SPENDING = 860_000
AUG_INCOME = cents_to_str(1_000_000)  # the August plan in test_cashflow_p4


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    clear_cache()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _with(groups: list[tuple[str, str]], key: str, amount: str | None) -> list[tuple[str, str]]:
    """The groups with one amount changed, or the group left out (None)."""
    out = [(k, a) for k, a in groups if k != key]
    return sorted(out + ([(key, amount)] if amount is not None else []))


def _plan(month: str, groups: list[tuple[str, str]], spending: str | None = None) -> dict:
    total = sum(to_cents(a) for _, a in groups)
    return {
        "month": month,
        "planned_income": cents_to_str(INCOME),
        "planned_spending": spending or cents_to_str(total),
        "by_group": [{"key": k, "amount": a} for k, a in sorted(groups)],
    }


def _add_run(
    db: sqlite3.Connection, run_id: str, plans: list[dict], finished_at: str | None = None
) -> None:
    """A later fake monthly run for October that carries only a plan. finished_at
    says when it was imported (left out: never after a save)."""
    db.execute(
        """INSERT INTO import_runs (run_id, run_date, run_seq, mode, status, started_at,
               finished_at)
           VALUES (?, ?, 1, 'monthly', 'imported', '2026-10-20T00:00:00+00:00', ?)""",
        (run_id, run_id, finished_at),
    )
    sections = {"meta": {"updated": run_id, "this_month": OCT}, "cashflow_plan": plans}
    for name, value in sections.items():
        db.execute(
            "INSERT INTO run_sections (run_id, section, sha256, json) VALUES (?, ?, 'x', ?)",
            (run_id, name, json.dumps(value)),
        )


def _save(
    db: sqlite3.Connection,
    changes: list[tuple[str, int | None, int | None]],
    *,
    month: str = OCT,
    also_later: bool = False,
    user_id: int | None = None,
    now: str = NOW,
) -> dict[str, int]:
    with write_transaction(db):
        return bt.save(
            db,
            month=month,
            changes=[bt.Change(*c) for c in changes],
            also_later=also_later,
            user_id=user_id,
            now=now,
        )


def _insert(db: sqlite3.Connection, key: str, month: str, target: int, base: int | None) -> None:
    db.execute(
        """INSERT INTO budget_target_edits (kind, target_key, month, target_cents, base_cents,
               updated_at) VALUES ('group', ?, ?, ?, ?, ?)""",
        (key, month, target, base, NOW),
    )


def _rows(db: sqlite3.Connection) -> list[tuple]:
    return [
        tuple(r)
        for r in db.execute(
            """SELECT target_key, month, target_cents, base_cents FROM budget_target_edits
               ORDER BY target_key, month"""
        )
    ]


def _group(b: dict, key: str) -> dict:
    return next(g for g in b["groups"] if g["key"] == key)


def _admin_id(settings: Settings, db: sqlite3.Connection) -> int:
    add_users(settings)
    return db.execute("SELECT id FROM users WHERE username = ?", (ADMIN,)).fetchone()[0]


def _screens(db: sqlite3.Connection) -> dict:
    """Every screen that reads the plan, for the sample months."""
    found = checks(db)
    assert found is not None
    return {
        "budget_aug": budgets_page(db, "2026-08"),
        "budget_oct": budgets_page(db, OCT),
        "cashflow": cf.cashflow(db, "2026-01", OCT),
        "plan": cf.plan_page(db),
        "drawer": cf.category_detail(db, "dining", "2026-01", OCT),
        "dash_oct": dash.dashboard(db),
        "dash_aug": dash.dashboard(db, "2026-08"),
        "checks": {k: found[k] for k in ("plan_mapping", "plan_mapping_month")},
        "ai_budget": [f for f in facts.build(db, None) if f[0] == "budget"],
    }


def _chat_only(runs: list[readers.StateRun], month: str, edits: bt.Edits) -> bt.PlanView:
    """How the plan was read before this feature: the chat's plan, as sent."""
    found = readers.for_month(runs, "cashflow_plan", month)
    plan = found[0] if found else None
    return bt.PlanView(plan, plan, {})


def _left_overs(db: sqlite3.Connection, month: str = OCT) -> dict[str, int]:
    """Left over for `month` on every screen that shows it."""
    c = cf.cashflow(db, "2026-01", month)
    d = dash.dashboard(db)
    plan_month = next(m for m in cf.plan_page(db)["months"] if m["month"] == month)
    out = {
        "cashflow": next(m for m in c["months"] if m["month"] == month)["plan"]["left_over_cents"],
        "plan_tab": plan_month["left_over_cents"],
        "bars": next(
            x["left_over_cents"]
            for x in d["cashflow"]
            if x["month"] == month and x["basis"] == "plan"
        ),
    }
    if month == d["month"]:
        out["tiles"] = d["plan_month"]["left_over_cents"]
    return out


# --------------------------------------------------------------------------
# Migration 0014
# --------------------------------------------------------------------------

GOOD = {
    "kind": "group",
    "target_key": "lifestyle",
    "month": OCT,
    "target_cents": 340_000,
    "base_cents": 365_000,
    "updated_at": NOW,
}


def _ins(db: sqlite3.Connection, row: dict) -> None:
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    db.execute(f"INSERT INTO budget_target_edits ({cols}) VALUES ({marks})", tuple(row.values()))


def test_migration_0014_adds_one_strict_table(db: sqlite3.Connection) -> None:
    assert list_migrations()[13].path == MIGRATION
    assert user_version(db) == len(list_migrations()) >= 14
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "—" not in sql and not re.search(r"[0-9]{5,}", sql)
    code = "\n".join(line for line in sql.splitlines() if not line.startswith("--"))
    assert code.count("CREATE TABLE") == 1
    assert "ALTER" not in code and "DROP" not in code and "INSERT" not in code
    row = db.execute("SELECT sql FROM sqlite_schema WHERE name = 'budget_target_edits'").fetchone()
    assert row[0].rstrip().endswith("STRICT")
    # App-owned: no foreign key to imported tables, so reapply never drops an edit.
    refs = {r["table"] for r in db.execute("PRAGMA foreign_key_list(budget_target_edits)")}
    assert refs == {"users"}
    _ins(db, GOOD)
    _ins(db, {**GOOD, "target_key": "kids", "target_cents": 0, "base_cents": None})
    assert len(_rows(db)) == 2


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("kind", "category"),  # CHECK kind IN ('group')
        ("target_key", ""),  # CHECK target_key <> ''
        ("month", "2026-1"),  # CHECK month GLOB
        ("month", "2026-20"),
        ("month", "2026-10-01"),
        ("target_cents", -1),  # CHECK target_cents >= 0
        ("base_cents", -1),  # CHECK base_cents IS NULL OR >= 0
        ("target_cents", 1.5),  # STRICT
        ("target_cents", None),  # NOT NULL
        ("updated_at", None),
    ],
)
def test_migration_0014_checks_refuse(db: sqlite3.Connection, column: str, value: object) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        _ins(db, {**GOOD, column: value})
    assert _rows(db) == []


def test_migration_0014_one_row_per_kind_key_month(db: sqlite3.Connection) -> None:
    _ins(db, GOOD)
    with pytest.raises(sqlite3.IntegrityError):
        _ins(db, {**GOOD, "target_cents": 1})
    _ins(db, {**GOOD, "month": "2026-11"})
    assert [r[1] for r in _rows(db)] == [OCT, "2026-11"]


def test_0014_upgrades_a_populated_v13_database(samples: Settings) -> None:
    """The copy is written first, the table starts empty, and the data is the same."""
    with read_connection(samples.db_path) as conn:
        before = fingerprint(conn)
    with write_connection(samples.db_path) as w:
        w.execute("DROP TABLE budget_target_edits")
        w.execute("PRAGMA user_version = 13")
    assert migrate(settings=samples) == len(list_migrations()) >= 14
    backup = samples.backup_dir / "pre-migrate-0014.db"
    assert backup.exists() and backup.stat().st_mode & 0o777 == 0o600
    old = sqlite3.connect(backup)
    try:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 13
        names = {r[0] for r in old.execute("SELECT name FROM sqlite_schema")}
        assert "budget_target_edits" not in names
    finally:
        old.close()
    with read_connection(samples.db_path) as conn:
        assert conn.execute("SELECT count(*) FROM budget_target_edits").fetchone()[0] == 0
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert fingerprint(conn) == before
    assert migrate(settings=samples) == len(list_migrations())


# --------------------------------------------------------------------------
# One reader of the plan
# --------------------------------------------------------------------------


class _PlanReads(ast.NodeVisitor):
    """Every "cashflow_plan" constant: (function, call it is an argument of)."""

    def __init__(self) -> None:
        self.stack: list[str] = ["<module>"]
        self.calls: list[str | None] = [None]
        self.found: list[tuple[str, str | None]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        f = node.func
        self.calls.append(f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None))
        self.generic_visit(node)
        self.calls.pop()

    def visit_Constant(self, node: ast.Constant) -> None:
        if node.value == "cashflow_plan":
            self.found.append((self.stack[-1], self.calls[-1]))


def test_only_cashflow_reads_the_chat_plan() -> None:
    """Under backend/app, only cashflow.plan_view (behind plan_for) reads the
    cashflow_plan section, so no screen can skip the edits. plan_page names it
    once more for the run id only. The importer and readers.py stay chat only."""
    found = set()
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP.parent).as_posix()
        if rel.startswith("app/importer/") or rel == "app/state/readers.py":
            continue
        visitor = _PlanReads()
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        found |= {(rel, fn, call) for fn, call in visitor.found}
    assert found == {
        ("app/services/cashflow.py", "plan_view", "for_month"),
        ("app/services/cashflow.py", "plan_page", "section_source"),
    }


# --------------------------------------------------------------------------
# No edits: every screen as before
# --------------------------------------------------------------------------


def test_no_edits_every_screen_shows_the_chat_plan(
    samples: Settings, db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_plan_run(db, "2026-08", AUG_PLAN, AUG_INCOME)
    real = _screens(db)
    with monkeypatch.context() as mp:
        mp.setattr(cf, "plan_view", _chat_only)
        assert _screens(db) == real
    # A row for a month none of them shows changes nothing.
    _insert(db, "lifestyle", "2026-12", 100_000, None)
    assert _screens(db) == real

    for b in (real["budget_aug"], real["budget_oct"]):
        assert b["edits_notice"] is None and b["conflicts"] == 0
        assert b["planned_spending_cents"] == b["chat_planned_spending_cents"]
        assert b["totals"]["plan_cents"] == b["totals"]["chat_plan_cents"]
        for g in b["groups"]:
            assert (g["source"], g["edit"]) == ("chat", None)
            assert g["chat_plan_cents"] == g["plan_cents"]
    aug = budgets_page(db, "2026-08", is_admin=True)
    oct_ = budgets_page(db, OCT, is_admin=True)
    assert (aug["can_edit"], aug["edit_note"]) == (False, bt.PAST_TEXT)
    assert not any(g["editable"] for g in aug["groups"])
    assert (oct_["can_edit"], oct_["edit_note"]) == (True, None)
    # A member sees the same numbers, and no edits.
    member = real["budget_oct"]
    assert (member["can_edit"], member["edit_note"]) == (False, bt.ADMIN_TEXT)
    assert not any(g["editable"] for g in member["groups"])
    role_only = ("can_edit", "edit_note", "groups", "addable_groups")
    assert {k: v for k, v in member.items() if k not in role_only} == {
        k: v for k, v in oct_.items() if k not in role_only
    }
    assert member["addable_groups"] == [] and oct_["addable_groups"] != []
    assert oct_["planned_spending_cents"] == CHAT_SPENDING
    assert {g["key"]: g["editable"] for g in oct_["groups"]} == {
        "housing": True,
        "lifestyle": True,
        "plan-installments": False,
        "saved-invested": True,
        "services": True,
        "transportation": True,
    }


def test_a_month_with_no_chat_plan_takes_no_edits(samples: Settings, db: sqlite3.Connection):
    b = budgets_page(db, "2026-11")
    assert (b["can_edit"], b["edit_note"], b["plan_source"]) == (False, bt.NO_PLAN_TEXT, None)
    assert b["planned_spending_cents"] is None and b["totals"]["chat_plan_cents"] is None


def test_an_edit_equal_to_the_chat_moves_no_number(samples: Settings, db: sqlite3.Connection):
    before = budgets_page(db, OCT)
    _insert(db, "lifestyle", OCT, 365_000, 300_000)  # the chat caught up
    after = budgets_page(db, OCT)
    assert after == before


# --------------------------------------------------------------------------
# One edit: every screen agrees
# --------------------------------------------------------------------------


def test_one_edit_every_screen_agrees(samples: Settings, db: sqlite3.Connection) -> None:
    left_before = _left_overs(db)
    assert set(left_before.values()) == {INCOME - CHAT_SPENDING}
    other_before = {
        g["key"]: g["other_cents"]
        for g in next(m for m in cf.plan_page(db)["months"] if m["month"] == OCT)["by_group"]
    }
    uid = _admin_id(samples, db)

    counts = _save(db, [("lifestyle", 340_000, 365_000)], user_id=uid)
    assert counts == {"set": 1, "kept": 0, "cleared": 0, "also_later": 0}
    assert _rows(db) == [("lifestyle", OCT, 340_000, 365_000)]

    b = budgets_page(db, OCT)
    life = _group(b, "lifestyle")
    assert (life["plan_cents"], life["chat_plan_cents"], life["source"]) == (
        340_000,
        365_000,
        "edited",
    )
    assert life["edit"] == {"by": "Alex Example", "at": "2026-10-20", "conflict": False}
    assert life["remaining_cents"] == 340_000 - life["actual_cents"]
    assert (b["totals"]["plan_cents"], b["totals"]["chat_plan_cents"]) == (835_000, CHAT_SPENDING)
    assert (b["planned_spending_cents"], b["chat_planned_spending_cents"]) == (
        835_000,
        CHAT_SPENDING,
    )
    assert b["planned_income_cents"] == INCOME and b["conflicts"] == 0
    assert _group(b, "housing")["source"] == "chat"

    # Budget total == Cash Flow planned spending == the Dashboard spending plan.
    c = cf.cashflow(db, "2026-01", OCT)
    [pva] = [p for p in c["plan_vs_actual"] if p["month"] == OCT]
    oct_month = next(m for m in c["months"] if m["month"] == OCT)
    d = dash.dashboard(db)
    assert (
        b["totals"]["plan_cents"]
        == pva["planned_spending_cents"]
        == oct_month["plan"]["spending_cents"]
        == d["plan_month"]["spending_cents"]
        == 835_000
    )
    assert {x["key"]: x["plan_cents"] for x in pva["lines"]}["lifestyle"] == 340_000
    # Left over moves by the same 250.00 everywhere.
    assert _left_overs(db) == {k: v + 25_000 for k, v in left_before.items()}
    # The group: the Dashboard bar, the Plan tab row and the category drawer.
    spending = {g["key"]: g["cents"] for g in d["spending_by_group"]}
    plan_month = next(m for m in cf.plan_page(db)["months"] if m["month"] == OCT)
    rows = {g["key"]: g for g in plan_month["by_group"]}
    drawer = cf.category_detail(db, "dining", OCT, OCT)
    assert drawer is not None
    assert (
        spending["lifestyle"]
        == rows["lifestyle"]["cents"]
        == drawer["months"][-1]["group_plan_cents"]
        == 340_000
    )
    assert rows["lifestyle"]["other_cents"] == other_before["lifestyle"] - 25_000
    # The chat's run data never changed.
    runs, _ = bt.runs_and_edits(db)
    assert cf.plan_for(runs, OCT, bt.NO_EDITS).planned_spending == "8600.00"


def test_the_gap_in_a_plan_that_does_not_add_up_stays(
    samples: Settings, db: sqlite3.Connection
) -> None:
    """The chat's groups add up to 8600.00 but it plans 9000.00: after an edit the
    groups and planned spending still differ by 400.00 (the delta rule)."""
    _add_run(db, "2026-10-20", [_plan(OCT, OCT_GROUPS, spending="9000.00")])
    gap = 40_000
    _save(db, [("lifestyle", 340_000, 365_000)])
    b = budgets_page(db, OCT)
    assert b["planned_spending_cents"] == 875_000
    assert b["planned_spending_cents"] - b["totals"]["plan_cents"] == gap
    assert b["chat_planned_spending_cents"] - b["totals"]["chat_plan_cents"] == gap
    # The Dashboard note's two figures (BarCards): the groups and planned spending.
    d = dash.dashboard(db)
    groups = sum(g["cents"] for g in d["spending_by_group"])
    assert d["plan_month"]["spending_cents"] - groups == gap
    [pva] = [p for p in cf.cashflow(db, "2026-01", OCT)["plan_vs_actual"] if p["month"] == OCT]
    assert pva["planned_spending_cents"] == 875_000


def test_saved_invested_and_an_app_added_group(samples: Settings, db: sqlite3.Connection) -> None:
    before = dash.dashboard(db)["plan_month"]
    _save(db, [("saved-invested", 90_000, 70_000)])
    after = dash.dashboard(db)["plan_month"]
    assert after["saved_invested_cents"] == 90_000
    assert after["spending_cents"] == before["spending_cents"] + 20_000
    assert after["left_over_cents"] == before["left_over_cents"] - 20_000
    assert after["saved_pct"] == before["saved_pct"]  # spending and saved move together

    # Kids: a spending group the chat left out. The edit adds it to the plan.
    _save(db, [("kids", 15_000, None)])
    b = budgets_page(db, OCT)
    assert [g["key"] for g in b["groups"]] == [
        "housing",
        "kids",
        "lifestyle",
        "plan-installments",
        "saved-invested",
        "services",
        "transportation",
    ]
    kids = _group(b, "kids")
    assert (kids["plan_cents"], kids["chat_plan_cents"], kids["source"]) == (15_000, None, "edited")
    assert kids["edit"]["conflict"] is False and kids["status"] == "under"
    assert b["planned_spending_cents"] == CHAT_SPENDING + 20_000 + 15_000
    assert b["totals"]["plan_cents"] == b["planned_spending_cents"]
    [pva] = [p for p in cf.cashflow(db, "2026-01", OCT)["plan_vs_actual"] if p["month"] == OCT]
    line = next(x for x in pva["lines"] if x["key"] == "kids")
    assert (line["measure"], line["plan_cents"]) == ("group", 15_000)
    mapping = checks(db)["plan_mapping"]
    assert next(m for m in mapping if m["key"] == "kids")["measure"] == "group"


def test_a_past_month_keeps_its_edits_read_only(samples: Settings, db: sqlite3.Connection):
    _add_plan_run(db, "2026-08", AUG_PLAN, AUG_INCOME)
    _insert(db, "lifestyle", "2026-08", 80_000, 60_000)  # the chat sent 700.00 since
    b = budgets_page(db, "2026-08")
    life = _group(b, "lifestyle")
    assert (life["plan_cents"], life["chat_plan_cents"], life["source"]) == (
        80_000,
        70_000,
        "edited",
    )
    assert life["edit"]["conflict"] is True and life["editable"] is False
    assert (b["can_edit"], b["edit_note"], b["conflicts"]) == (False, bt.PAST_TEXT, 0)
    assert b["later_months"] == []
    with pytest.raises(EditError) as exc:
        _save(db, [("lifestyle", None, 70_000)], month="2026-08")
    assert (exc.value.status, exc.value.message) == (422, bt.PAST_TEXT)


# --------------------------------------------------------------------------
# Who wins: the base rule, with later chat runs
# --------------------------------------------------------------------------


def test_who_wins_when_the_chat_sends_a_new_number(
    samples: Settings, db: sqlite3.Connection
) -> None:
    _save(db, [("lifestyle", 340_000, 365_000)])

    # 1. The chat still sends the base: the owner's number, tagged edited.
    life = _group(b := budgets_page(db, OCT), "lifestyle")
    assert (life["source"], life["edit"]["conflict"], b["conflicts"]) == ("edited", False, 0)

    # 2. The chat now sends the owner's number: the chat's, no tag. The row stays.
    _add_run(db, "2026-10-20", [_plan(OCT, _with(OCT_GROUPS, "lifestyle", "3400.00"))])
    life = _group(b := budgets_page(db, OCT), "lifestyle")
    assert (life["plan_cents"], life["chat_plan_cents"], life["source"], life["edit"]) == (
        340_000,
        340_000,
        "chat",
        None,
    )
    assert b["conflicts"] == 0 and b["planned_spending_cents"] == 835_000
    assert _rows(db) == [("lifestyle", OCT, 340_000, 365_000)]

    # 3. The chat sends a third number: the owner's stays, with a conflict.
    _add_run(db, "2026-10-21", [_plan(OCT, _with(OCT_GROUPS, "lifestyle", "3800.00"))])
    life = _group(b := budgets_page(db, OCT), "lifestyle")
    assert (life["plan_cents"], life["chat_plan_cents"], life["source"]) == (
        340_000,
        380_000,
        "edited",
    )
    assert life["edit"]["conflict"] is True and b["conflicts"] == 1
    assert (b["chat_planned_spending_cents"], b["planned_spending_cents"]) == (875_000, 835_000)
    # A save that saw the old number is refused: the chat's plan changed.
    with pytest.raises(EditError) as exc:
        _save(db, [("lifestyle", 330_000, 365_000)])
    assert (exc.value.status, exc.value.message) == (409, bt.STALE_TEXT)

    # Keep mine: the same target again re-bases the row on the chat's number.
    assert _save(db, [("lifestyle", 340_000, 380_000)])["kept"] == 1
    assert _rows(db) == [("lifestyle", OCT, 340_000, 380_000)]
    life = _group(b := budgets_page(db, OCT), "lifestyle")
    assert (life["source"], life["edit"]["conflict"], b["conflicts"]) == ("edited", False, 0)

    # 4. The chat drops the group: the owner's number stays, with a conflict.
    _add_run(db, "2026-10-22", [_plan(OCT, _with(OCT_GROUPS, "lifestyle", None))])
    life = _group(b := budgets_page(db, OCT), "lifestyle")
    assert (life["plan_cents"], life["chat_plan_cents"], life["source"]) == (
        340_000,
        None,
        "edited",
    )
    assert life["edit"]["conflict"] is True and b["conflicts"] == 1

    # Use the chat's: the row goes, and the chat's plan shows.
    assert _save(db, [("lifestyle", None, None)])["cleared"] == 1
    assert _rows(db) == []
    b = budgets_page(db, OCT)
    assert "lifestyle" not in {g["key"] for g in b["groups"]}  # no plan, nothing spent yet
    assert b["planned_spending_cents"] == b["chat_planned_spending_cents"] == 495_000


def test_a_chat_that_caught_up_and_went_back_is_a_conflict(
    samples: Settings, db: sqlite3.Connection
) -> None:
    """The chat sends the owner's number after the save, then its old number again.
    That is a change by the chat, so it shows as a conflict, never quietly. A run
    from before the save that sent that number never counts."""
    before = [
        ("2026-10-15", "3400.00", "2026-10-15T16:00:00+00:00"),
        ("2026-10-16", "3650.00", "2026-10-16T16:00:00+00:00"),
    ]
    for run_id, amount, at in before:
        _add_run(db, run_id, [_plan(OCT, _with(OCT_GROUPS, "lifestyle", amount))], at)
    _save(db, [("lifestyle", 340_000, 365_000)])  # at NOW, Oct 20 15:00 UTC
    life = _group(b := budgets_page(db, OCT), "lifestyle")
    assert (life["source"], life["edit"]["conflict"], b["conflicts"]) == ("edited", False, 0)

    # The chat catches up after the save: the chat's, no tag.
    _add_run(
        db,
        "2026-10-20",
        [_plan(OCT, _with(OCT_GROUPS, "lifestyle", "3400.00"))],
        "2026-10-20T16:00:00+00:00",
    )
    life = _group(b := budgets_page(db, OCT), "lifestyle")
    assert (life["source"], life["edit"], b["conflicts"]) == ("chat", None, 0)

    # Then goes back to its old number: the owner's stays, with a conflict.
    _add_run(db, "2026-10-21", [_plan(OCT, OCT_GROUPS)], "2026-10-21T16:00:00+00:00")
    life = _group(b := budgets_page(db, OCT), "lifestyle")
    assert (life["plan_cents"], life["chat_plan_cents"], life["source"]) == (
        340_000,
        365_000,
        "edited",
    )
    assert life["edit"]["conflict"] is True and b["conflicts"] == 1
    assert b["planned_spending_cents"] == 835_000
    runs, edits = bt.runs_and_edits(db)
    assert cf.plan_for(runs, OCT, edits) == cf.plan_view(runs, OCT, edits).plan

    # Keep mine re-bases the row and dates it now: quiet again.
    later = "2026-10-21T17:00:00+00:00"
    assert _save(db, [("lifestyle", 340_000, 365_000)], now=later)["kept"] == 1
    life = _group(b := budgets_page(db, OCT), "lifestyle")
    assert (life["source"], life["edit"]["conflict"], b["conflicts"]) == ("edited", False, 0)
    # Use the chat's still clears it.
    assert _save(db, [("lifestyle", None, 365_000)], now=later)["cleared"] == 1
    assert _rows(db) == []


def test_a_chat_number_below_zero_takes_no_edit(
    samples: Settings, db: sqlite3.Connection, admin: TestClient
) -> None:
    """The import format allows a group amount below $0, but a target and its
    base never are. The save says so in place (422), never a 500, and saves
    nothing. Use the chat's still works."""
    _add_run(
        db,
        "2026-10-20",
        [
            _plan(OCT, _with(OCT_GROUPS, "services", "-50.00")),
            _plan("2026-11", _with(OCT_GROUPS, "saved-invested", "-200.00")),
        ],
    )
    assert _group(budgets_page(db, OCT), "services")["chat_plan_cents"] == -5_000
    with pytest.raises(EditError) as exc:
        _save(db, [("services", 10_000, -5_000)])
    assert (exc.value.status, exc.value.message) == (422, bt.NEGATIVE_TEXT)
    # A later month below $0 refuses the whole save. This month alone saves.
    with pytest.raises(EditError) as exc:
        _save(db, [("saved-invested", 50_000, 70_000)], also_later=True)
    assert (exc.value.status, exc.value.message) == (422, bt.NEGATIVE_LATER_TEXT)
    r = _put(
        admin, _body({"group_key": "services", "target_cents": 10_000, "seen_chat_cents": -5_000})
    )
    assert (r.status_code, r.json()["detail"]) == (422, bt.NEGATIVE_TEXT)
    assert _rows(db) == [] and _edit_events(db) == []

    _save(db, [("saved-invested", 50_000, 70_000)])
    assert _rows(db) == [("saved-invested", OCT, 50_000, 70_000)]
    # A row the chat later answers with a number below $0: Use the chat's clears it.
    _insert(db, "services", "2026-11", 30_000, 25_000)
    _add_run(
        db,
        "2026-10-21",
        [_plan("2026-11", _with(OCT_GROUPS, "services", "-10.00"))],
    )
    assert _save(db, [("services", None, -1_000)], month="2026-11")["cleared"] == 1
    assert _rows(db) == [("saved-invested", OCT, 50_000, 70_000)]


def test_a_chat_number_too_large_to_store_takes_no_edit(
    samples: Settings, db: sqlite3.Connection, admin: TestClient
) -> None:
    """A chat number above SQLite's INTEGER range cannot be a base. The save
    says so in place (422), never a 500, and saves nothing."""
    huge = cents_to_str(bt.SQLITE_MAX + 1)
    _add_run(
        db,
        "2026-10-20",
        [
            _plan(OCT, _with(OCT_GROUPS, "services", huge)),
            _plan("2026-11", _with(OCT_GROUPS, "lifestyle", huge)),
        ],
    )
    with pytest.raises(EditError) as exc:
        _save(db, [("services", 10_000, bt.SQLITE_MAX + 1)])
    assert (exc.value.status, exc.value.message) == (422, bt.TOO_LARGE_TEXT)
    with pytest.raises(EditError) as exc:
        _save(db, [("lifestyle", 340_000, 365_000)], also_later=True)
    assert (exc.value.status, exc.value.message) == (422, bt.TOO_LARGE_LATER_TEXT)
    r = _put(admin, _body({**LIFE}, also_later=True))
    assert (r.status_code, r.json()["detail"]) == (422, bt.TOO_LARGE_LATER_TEXT)
    assert _rows(db) == [] and _edit_events(db) == []
    # This month alone saves.
    _save(db, [("lifestyle", 340_000, 365_000)])
    assert _rows(db) == [("lifestyle", OCT, 340_000, 365_000)]


def test_a_group_the_chat_left_out_can_get_a_first_target(
    samples: Settings, db: sqlite3.Connection, admin: TestClient
) -> None:
    """The drawer offers every editable group, also those with no row on the
    page (no chat target and nothing spent): addable_groups."""
    _add_run(db, "2026-10-20", [_plan(OCT, _with(OCT_GROUPS, "saved-invested", None))])
    b = admin.get("/api/budgets", params={"month": OCT}).json()
    shown = {g["key"] for g in b["groups"]}
    added = [g["key"] for g in b["addable_groups"]]
    assert set(added) == bt.editable_keys(db) - shown
    assert "kids" in added and added[-1] == "saved-invested"
    assert next(g for g in b["addable_groups"] if g["key"] == "saved-invested")["kind"] == (
        "saved_invested"
    )
    assert not shown & set(added)

    r = _put(admin, _body({"group_key": "kids", "target_cents": 15_000, "seen_chat_cents": None}))
    assert r.status_code == 200
    kids = _group(r.json(), "kids")
    assert (kids["plan_cents"], kids["chat_plan_cents"], kids["source"]) == (15_000, None, "edited")
    assert "kids" not in [g["key"] for g in r.json()["addable_groups"]]
    r = _put(
        admin,
        _body({"group_key": "saved-invested", "target_cents": 50_000, "seen_chat_cents": None}),
    )
    assert _group(r.json(), "saved-invested")["plan_cents"] == 50_000

    # Nothing to add for a member, on a past month, or with no chat plan.
    assert budgets_page(db, OCT)["addable_groups"] == []
    assert budgets_page(db, "2026-08", is_admin=True)["addable_groups"] == []
    assert budgets_page(db, "2027-03", is_admin=True)["addable_groups"] == []


def test_setting_the_chat_number_deletes_the_row(samples: Settings, db: sqlite3.Connection):
    _save(db, [("lifestyle", 340_000, 365_000)])
    counts = _save(db, [("lifestyle", 365_000, 365_000)])
    assert counts == {"set": 0, "kept": 0, "cleared": 1, "also_later": 0}
    assert _rows(db) == []
    # Nothing to clear: nothing written but the events row.
    assert _save(db, [("lifestyle", None, 365_000)])["cleared"] == 0


def test_also_later_writes_one_row_per_later_planned_month(
    samples: Settings, db: sqlite3.Connection
) -> None:
    _add_run(
        db,
        "2026-10-20",
        [
            _plan(OCT, OCT_GROUPS),
            _plan("2026-11", _with(OCT_GROUPS, "lifestyle", "3700.00")),
            _plan("2027-01", _with(OCT_GROUPS, "lifestyle", "3900.00")),
        ],
    )
    runs, edits = bt.runs_and_edits(db)
    nov = cf.plan_for(runs, "2026-11", edits)

    # One month: the months after stay exactly the chat's plan.
    _save(db, [("lifestyle", 340_000, 365_000)])
    assert _rows(db) == [("lifestyle", OCT, 340_000, 365_000)]
    runs, edits = bt.runs_and_edits(db)
    assert cf.plan_for(runs, "2026-11", edits) == nov
    assert cf.plan_for(runs, "2026-09", edits) is None

    # Also later: each later month the chat planned, each with its own base. December
    # has no chat plan, so nothing.
    counts = _save(db, [("lifestyle", 330_000, 365_000)], also_later=True)
    assert counts == {"set": 3, "kept": 0, "cleared": 0, "also_later": 2}
    assert _rows(db) == [
        ("lifestyle", OCT, 330_000, 365_000),
        ("lifestyle", "2026-11", 330_000, 370_000),
        ("lifestyle", "2027-01", 330_000, 390_000),
    ]
    runs, edits = bt.runs_and_edits(db)
    assert cf.plan_for(runs, "2026-12", edits) is None
    # A later month shows the edit on the Plan tab and the Dashboard bars.
    assert _left_overs(db, "2026-11")["plan_tab"] == INCOME - (CHAT_SPENDING + 5_000 - 40_000)
    assert len(set(_left_overs(db, "2026-11").values())) == 1

    # Use the chat's with also later clears every month.
    counts = _save(db, [("lifestyle", None, 365_000)], also_later=True)
    assert counts == {"set": 0, "kept": 0, "cleared": 3, "also_later": 2}
    assert _rows(db) == []


# --------------------------------------------------------------------------
# Saves: checks, atomic, the events row, the write transaction
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "status", "message"),
    [
        ({"month": "2026-09"}, 422, bt.PAST_TEXT),
        ({"changes": [("plan-installments", 20_000, 18_000)]}, 422, bt.INSTALLMENTS_TEXT),
        ({"changes": [("travel-fund", 1_000, None)]}, 422, bt.UNKNOWN_TEXT),
        ({"changes": [("income", 1_000, None)]}, 422, bt.UNKNOWN_TEXT),
        ({"changes": [("not-spending", 1_000, None)]}, 422, bt.UNKNOWN_TEXT),
        (
            {"changes": [("lifestyle", 1_000, 365_000), ("lifestyle", 2_000, 365_000)]},
            422,
            bt.DUPLICATE_TEXT,
        ),
        ({"changes": [("lifestyle", -1, 365_000)]}, 422, bt.AMOUNT_TEXT),
        ({"changes": [("lifestyle", bt.MAX_CENTS + 1, 365_000)]}, 422, bt.AMOUNT_TEXT),
        ({"changes": []}, 422, bt.COUNT_TEXT),
        ({"changes": [("lifestyle", 1_000, 365_000)] * 41}, 422, bt.COUNT_TEXT),
        ({"changes": [("lifestyle", 340_000, 360_000)]}, 409, bt.STALE_TEXT),
        ({"month": "2026-11"}, 409, bt.NO_PLAN_TEXT),
        # One bad change saves nothing.
        (
            {"changes": [("lifestyle", 340_000, 365_000), ("housing", 300_000, None)]},
            409,
            bt.STALE_TEXT,
        ),
        (
            {"changes": [("lifestyle", 340_000, 365_000), ("plan-installments", None, 18_000)]},
            422,
            bt.INSTALLMENTS_TEXT,
        ),
    ],
)
def test_a_bad_save_is_refused_and_saves_nothing(
    samples: Settings, db: sqlite3.Connection, kwargs: dict, status: int, message: str
) -> None:
    args = {"changes": [("lifestyle", 340_000, 365_000)], **kwargs}
    changes = args.pop("changes")
    with pytest.raises(EditError) as exc:
        _save(db, changes, **args)
    assert (exc.value.status, exc.value.message) == (status, message)
    assert _rows(db) == []
    assert (
        db.execute("SELECT count(*) FROM events WHERE kind = ?", (bt.EVENT_KIND,)).fetchone()[0]
        == 0
    )


def test_the_largest_and_smallest_amounts_save(samples: Settings, db: sqlite3.Connection):
    _save(db, [("lifestyle", bt.MAX_CENTS, 365_000), ("housing", 0, 360_000)])
    assert _rows(db) == [("housing", OCT, 0, 360_000), ("lifestyle", OCT, bt.MAX_CENTS, 365_000)]
    b = budgets_page(db, OCT)
    assert _group(b, "housing")["status"] == "on_target"  # nothing spent of $0


def test_a_save_writes_one_events_row_with_counts_only(
    samples: Settings, db: sqlite3.Connection
) -> None:
    uid = _admin_id(samples, db)
    counts = _save(db, [("lifestyle", 340_000, 365_000), ("kids", 12_300, None)], user_id=uid)
    [ev] = db.execute("SELECT * FROM events WHERE kind = ?", (bt.EVENT_KIND,)).fetchall()
    assert (ev["entity"], ev["entity_key"], ev["user_id"], ev["at"]) == (
        "budget_target_edits",
        OCT,
        uid,
        NOW,
    )
    assert (
        json.loads(ev["detail_json"])
        == counts
        == {
            "set": 2,
            "kept": 0,
            "cleared": 0,
            "also_later": 0,
        }
    )
    assert not re.search(r"[0-9]{3,}", ev["detail_json"])  # never an amount


def test_save_refuses_to_run_outside_a_write_transaction(
    samples: Settings, db: sqlite3.Connection
) -> None:
    assert not db.in_transaction
    with pytest.raises(RuntimeError):
        bt.save(
            db,
            month=OCT,
            changes=[bt.Change("lifestyle", 340_000, 365_000)],
            also_later=False,
            user_id=None,
            now=NOW,
        )
    assert _rows(db) == []


# --------------------------------------------------------------------------
# Fail safe
# --------------------------------------------------------------------------


def test_a_failed_overlay_shows_the_chat_plan(
    samples: Settings,
    db: sqlite3.Connection,
    admin: TestClient,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rebuilt plan that does not pass (here a key the import format refuses,
    slipped in) fails the rebuild: every screen shows the chat's plan, the page
    says so, never a 500."""
    _insert(db, "lifestyle", OCT, 340_000, 365_000)
    _insert(db, "kids", OCT, 123_456, None)
    real = bt.CashflowPlan

    class Broken:
        @staticmethod
        def model_validate(data: dict) -> object:
            bad = [*data["by_group"], {"key": "Bad Key", "amount": "1.00"}]
            return real.model_validate({**data, "by_group": bad})

    monkeypatch.setattr(bt, "CashflowPlan", Broken)
    with caplog.at_level(logging.WARNING, logger="app.budgets"):
        r = admin.get("/api/budgets", params={"month": OCT})
    assert r.status_code == 200
    b = r.json()
    assert b["edits_notice"] == bt.FAILED_TEXT
    assert _group(b, "lifestyle")["plan_cents"] == 365_000
    assert {g["source"] for g in b["groups"]} == {"chat"}
    assert b["planned_spending_cents"] == b["chat_planned_spending_cents"] == CHAT_SPENDING
    assert b["totals"]["plan_cents"] == CHAT_SPENDING
    [rec] = [x for x in caplog.records if x.name == "app.budgets"]
    assert rec.error == "ValidationError"  # type: ignore[attr-defined]
    assert "Bad Key" not in caplog.text
    assert str(123_456) not in caplog.text and str(340_000) not in caplog.text
    assert dash.dashboard(db)["plan_month"]["spending_cents"] == CHAT_SPENDING
    assert set(_left_overs(db).values()) == {INCOME - CHAT_SPENDING}


def test_a_row_for_a_group_that_stopped_counting_is_left_out(
    samples: Settings, db: sqlite3.Connection
) -> None:
    """A group that no longer counts in spending cannot take an edit, so its row
    never moves a plan nobody can change back. The row stays, and comes back if
    the group counts again. A key that is no group at all is left out too."""
    _insert(db, "debt", OCT, 50_000, None)
    _insert(db, "Bad Key", OCT, 123_456, None)
    b = budgets_page(db, OCT)
    assert (_group(b, "debt")["plan_cents"], _group(b, "debt")["source"]) == (50_000, "edited")
    assert b["planned_spending_cents"] == CHAT_SPENDING + 50_000 and b["edits_notice"] is None

    db.execute("UPDATE category_groups SET counts_in_spending = 0 WHERE group_key = 'debt'")
    b = budgets_page(db, OCT)
    debt = next((g for g in b["groups"] if g["key"] == "debt"), None)
    assert debt is None or (debt["plan_cents"], debt["source"]) == (None, "chat")
    assert b["planned_spending_cents"] == b["chat_planned_spending_cents"] == CHAT_SPENDING
    assert set(_left_overs(db).values()) == {INCOME - CHAT_SPENDING}
    assert ("debt", OCT, 50_000, None) in _rows(db)

    db.execute("UPDATE category_groups SET counts_in_spending = 1 WHERE group_key = 'debt'")
    assert _group(budgets_page(db, OCT), "debt")["plan_cents"] == 50_000


# --------------------------------------------------------------------------
# Imports, reapply, the fingerprint and the privacy rescan
# --------------------------------------------------------------------------


def test_edits_survive_an_import_and_reapply(settings: Settings, capsys) -> None:
    migrate(settings=settings)
    for run_id in SAMPLE_RUNS[:-1]:
        assert import_folder(SAMPLES / run_id, settings=settings).status == IMPORTED
    with read_connection(settings.db_path) as conn:
        empty = fingerprint(conn)
        assert fingerprint(conn) == empty  # stable with no edit
    with write_connection(settings.db_path) as w:
        bt.save(
            w,
            month=OCT,
            changes=[bt.Change("lifestyle", 340_000, 365_000)],
            also_later=False,
            user_id=None,
            now=NOW,
        )
        before = _rows(w)
    with read_connection(settings.db_path) as conn:
        assert fingerprint(conn) != empty  # the fingerprint sees the edit

    result = import_folder(SAMPLES / SAMPLE_RUNS[-1], settings=settings)
    assert result.status == IMPORTED
    assert "budget_target_edits" not in result.counts["tables"]
    with read_connection(settings.db_path) as conn:
        assert _rows(conn) == before
        live = fingerprint(conn)
    assert reapply(settings) == {"runs": len(SAMPLE_RUNS), "changes": 0, "tables": {}}
    assert cli.main(["reapply"]) == 0
    assert capsys.readouterr().out.strip().endswith(live)
    with read_connection(settings.db_path) as conn:
        assert _rows(conn) == before
        assert fingerprint(conn) == live
        assert _group(budgets_page(conn, OCT), "lifestyle")["plan_cents"] == 340_000


def test_the_privacy_rescan_lists_the_key_and_never_masks_it(
    samples: Settings, tmp_path: Path
) -> None:
    with write_connection(samples.db_path) as w:
        _insert(w, "zephyr-fund", OCT, 10_000, None)
        before = _rows(w)
    rules = tmp_path / "p.json"
    rules.write_text(
        json.dumps({"personal_subscriptions": ["Zephyr Fund"], "employer_terms": []}),
        encoding="utf-8",
    )
    with write_connection(samples.db_path) as w:
        report = rs.rescan(w, load_private_rules(rules), NOW)
    assert report.listed["budget_target_edits.target_key"] == 1
    assert not [k for k in report.masked if k.startswith("budget_target_edits")]
    with read_connection(samples.db_path) as conn:
        assert _rows(conn) == before


# --------------------------------------------------------------------------
# PUT /api/budgets/targets
# --------------------------------------------------------------------------

H = {"Origin": PROD_ORIGIN}
ROUTE = "/api/budgets/targets"
LIFE = {"group_key": "lifestyle", "target_cents": 340_000, "seen_chat_cents": 365_000}


def _put(client: TestClient, body: dict):
    return client.put(ROUTE, json=body, headers=H)


def _body(*changes: dict, month: str = OCT, **extra: object) -> dict:
    return {"month": month, "changes": list(changes) or [LIFE], **extra}


def _edit_events(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute("SELECT * FROM events WHERE kind = ?", (bt.EVENT_KIND,)).fetchall()


def test_put_saves_and_returns_the_page(
    samples: Settings, db: sqlite3.Connection, admin: TestClient
) -> None:
    _add_run(
        db,
        "2026-10-20",
        [_plan(OCT, OCT_GROUPS), _plan("2026-11", _with(OCT_GROUPS, "lifestyle", "3700.00"))],
    )
    before = admin.get("/api/budgets", params={"month": OCT}).json()
    assert (before["can_edit"], before["edit_note"]) == (True, None)
    # The drawer's "Also for Nov" checkbox. November has its own month page too.
    assert before["later_months"] == ["2026-11"]
    nov = admin.get("/api/budgets", params={"month": "2026-11"}).json()
    assert (nov["can_edit"], nov["later_months"]) == (True, [])
    assert _group(before, "lifestyle")["editable"] is True

    r = _put(admin, _body(also_later=True))
    assert r.status_code == 200, r.text
    b = r.json()
    assert b == admin.get("/api/budgets", params={"month": OCT}).json()
    life = _group(b, "lifestyle")
    assert (life["plan_cents"], life["chat_plan_cents"], life["source"]) == (
        340_000,
        365_000,
        "edited",
    )
    assert life["edit"]["by"] == "Alex Example" and life["edit"]["conflict"] is False
    assert re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", life["edit"]["at"])
    assert b["totals"]["plan_cents"] == b["planned_spending_cents"] == CHAT_SPENDING - 25_000
    assert b["chat_planned_spending_cents"] == CHAT_SPENDING and b["can_edit"] is True
    assert _rows(db) == [
        ("lifestyle", OCT, 340_000, 365_000),
        ("lifestyle", "2026-11", 340_000, 370_000),
    ]
    uid = db.execute("SELECT id FROM users WHERE username = ?", (ADMIN,)).fetchone()[0]
    assert [r["updated_by"] for r in db.execute("SELECT updated_by FROM budget_target_edits")] == [
        uid,
        uid,
    ]
    # One events row, counts only: no cents, no names.
    [ev] = _edit_events(db)
    assert (ev["user_id"], ev["entity"], ev["entity_key"]) == (uid, bt.ENTITY, OCT)
    assert json.loads(ev["detail_json"]) == {"set": 2, "kept": 0, "cleared": 0, "also_later": 1}
    assert not re.search(r"[0-9]{3,}", ev["detail_json"])
    assert "Alex" not in ev["detail_json"] and "lifestyle" not in ev["detail_json"]

    # Use the chat's: null clears every month it was saved for.
    use_chat = {**LIFE, "target_cents": None}
    r = _put(admin, _body(use_chat, also_later=True))
    assert r.status_code == 200 and _group(r.json(), "lifestyle")["source"] == "chat"
    assert _rows(db) == []


@pytest.mark.parametrize(
    ("body", "status", "detail"),
    [
        # The chat's plan.
        (_body({**LIFE, "seen_chat_cents": 360_000}), 409, bt.STALE_TEXT),
        (_body({**LIFE, "seen_chat_cents": None}), 409, bt.STALE_TEXT),
        (_body(month="2026-11"), 409, bt.NO_PLAN_TEXT),
        (_body(month="2027-03"), 409, bt.NO_PLAN_TEXT),
        # The service's checks.
        (_body(month="2026-09"), 422, bt.PAST_TEXT),
        (_body(month="2025-12"), 422, bt.PAST_TEXT),
        (
            _body({"group_key": "plan-installments", "target_cents": 0, "seen_chat_cents": 18_000}),
            422,
            bt.INSTALLMENTS_TEXT,
        ),
        (_body({**LIFE, "group_key": "travel-fund"}), 422, bt.UNKNOWN_TEXT),
        (_body({**LIFE, "group_key": "income"}), 422, bt.UNKNOWN_TEXT),
        (_body({**LIFE, "group_key": "not-spending"}), 422, bt.UNKNOWN_TEXT),
        (_body(LIFE, {**LIFE, "target_cents": 1_000}), 422, bt.DUPLICATE_TEXT),
        # The body (Pydantic): amounts, counts, fields.
        (_body({**LIFE, "target_cents": -1}), 422, "Invalid request."),
        (_body({**LIFE, "target_cents": bt.MAX_CENTS + 1}), 422, "Invalid request."),
        (_body({**LIFE, "target_cents": 12.5}), 422, "Invalid request."),
        (_body({**LIFE, "target_cents": "lots"}), 422, "Invalid request."),
        ({"month": OCT, "changes": []}, 422, "Invalid request."),
        (_body(*[LIFE] * (bt.MAX_CHANGES + 1)), 422, "Invalid request."),
        (_body(month="2026-13"), 422, "Invalid request."),
        (_body(month="2026-1"), 422, "Invalid request."),
        (_body({**LIFE, "group_key": "Bad Key"}), 422, "Invalid request."),
        (_body({"group_key": "lifestyle", "target_cents": 340_000}), 422, "Invalid request."),
        (_body({**LIFE, "note": "x"}), 422, "Invalid request."),
        (_body(extra=True), 422, "Invalid request."),
        ({"changes": [LIFE]}, 422, "Invalid request."),
        # One bad change saves nothing: the good one goes too.
        (
            _body(LIFE, {"group_key": "housing", "target_cents": 300_000, "seen_chat_cents": 1}),
            409,
            bt.STALE_TEXT,
        ),
        (
            _body(
                LIFE,
                {"group_key": "plan-installments", "target_cents": None, "seen_chat_cents": 18_000},
            ),
            422,
            bt.INSTALLMENTS_TEXT,
        ),
        (
            _body(LIFE, {**LIFE, "group_key": "housing", "target_cents": -5}),
            422,
            "Invalid request.",
        ),
    ],
)
def test_put_refuses_and_saves_nothing(
    samples: Settings,
    db: sqlite3.Connection,
    admin: TestClient,
    body: dict,
    status: int,
    detail: str,
) -> None:
    page = admin.get("/api/budgets", params={"month": OCT}).json()
    r = _put(admin, body)
    assert (r.status_code, r.json()["detail"]) == (status, detail)
    assert _rows(db) == [] and _edit_events(db) == []
    assert admin.get("/api/budgets", params={"month": OCT}).json() == page


def test_put_keep_mine_re_bases_on_the_chat_number(
    samples: Settings, db: sqlite3.Connection, admin: TestClient
) -> None:
    assert _put(admin, _body()).status_code == 200
    _add_run(db, "2026-10-21", [_plan(OCT, _with(OCT_GROUPS, "lifestyle", "3800.00"))])
    b = admin.get("/api/budgets", params={"month": OCT}).json()
    assert (b["conflicts"], _group(b, "lifestyle")["edit"]["conflict"]) == (1, True)
    # The page saw 3650.00 before the chat's run: stale.
    assert _put(admin, _body()).status_code == 409
    # Keep mine: the same target, with the chat's number the page shows now.
    r = _put(admin, _body({**LIFE, "seen_chat_cents": 380_000}))
    assert r.status_code == 200
    assert (r.json()["conflicts"], _group(r.json(), "lifestyle")["edit"]["conflict"]) == (0, False)
    assert _rows(db) == [("lifestyle", OCT, 340_000, 380_000)]


def test_a_member_sees_the_targets_and_cannot_change_them(
    samples: Settings, db: sqlite3.Connection, prod: TestClient
) -> None:
    _insert(db, "lifestyle", OCT, 340_000, 365_000)
    assert login(prod, MEMBER, MEMBER_PW, PROD_ORIGIN).status_code == 200
    b = prod.get("/api/budgets", params={"month": OCT}).json()
    assert (b["can_edit"], b["edit_note"], b["later_months"]) == (False, bt.ADMIN_TEXT, [])
    assert not any(g["editable"] for g in b["groups"])
    life = _group(b, "lifestyle")
    assert (life["plan_cents"], life["source"]) == (340_000, "edited")  # the tag shows

    r = _put(prod, _body({**LIFE, "target_cents": 300_000}))
    assert (r.status_code, r.json()) == (403, {"detail": "Only an admin can do this."})
    # The role is checked before the body.
    assert _put(prod, {"month": "nope"}).status_code == 403
    assert _rows(db) == [("lifestyle", OCT, 340_000, 365_000)] and _edit_events(db) == []


def test_signed_out_gets_401(samples: Settings, db: sqlite3.Connection, prod: TestClient) -> None:
    r = _put(prod, _body())
    assert (r.status_code, r.json()) == (401, {"detail": "Not signed in."})
    assert prod.get("/api/budgets").status_code == 401
    assert _rows(db) == [] and _edit_events(db) == []


def test_demo_refuses_the_put_and_the_data_stays(
    samples: Settings, db: sqlite3.Connection, admin: TestClient, prod_app
) -> None:
    from app.auth import web

    assert ROUTE not in web.DEMO_POSTS
    assert admin.post("/api/demo/start", headers=H).status_code == 204
    demo_file = prod_app.state.demo.db_file
    demo_sha = hashlib.sha256(Path(demo_file).read_bytes()).hexdigest()
    seen = admin.get("/api/budgets", params={"month": OCT}).json()
    # The demo user is a member: the edit controls do not show.
    assert (seen["can_edit"], seen["edit_note"]) == (False, bt.ADMIN_TEXT)
    assert not any(g["editable"] for g in seen["groups"])
    r = _put(admin, _body())
    assert (r.status_code, r.json()) == (403, {"detail": "Changes are off in demo."})
    assert hashlib.sha256(Path(demo_file).read_bytes()).hexdigest() == demo_sha
    conn = sqlite3.connect(f"file:{demo_file}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT count(*) FROM budget_target_edits").fetchone()[0] == 0
    finally:
        conn.close()
    assert admin.get("/api/budgets", params={"month": OCT}).json() == seen
    assert _rows(db) == [] and _edit_events(db) == []


# --------------------------------------------------------------------------
# The AI
# --------------------------------------------------------------------------


def test_the_ai_sees_the_edit(samples: Settings, db: sqlite3.Connection) -> None:
    assert facts.FACTS_VERSION == 7
    before = facts.signature(db)
    assert facts.signature(db) == before  # stable without a change
    uid = _admin_id(samples, db)
    _save(db, [("lifestyle", 340_000, 365_000)], user_id=uid)
    after = facts.signature(db)
    assert after != before
    _save(db, [("lifestyle", 330_000, 365_000)], user_id=uid)
    assert facts.signature(db) not in (before, after)

    [(_, _, fact)] = [f for f in facts.build(db, None) if f[:2] == ("budget", OCT)]
    groups = {g["key"]: g for g in fact["groups"]}
    assert (
        groups["lifestyle"]["plan_cents"],
        groups["lifestyle"]["chat_plan_cents"],
        groups["lifestyle"]["source"],
    ) == (330_000, 365_000, "edited")
    assert (groups["housing"]["plan_cents"], groups["housing"]["source"]) == (360_000, "chat")
    assert groups["housing"]["chat_plan_cents"] == 360_000
    assert fact["totals"]["plan_cents"] == CHAT_SPENDING - 35_000
    # Never who made the edit.
    assert "edit" not in groups["lifestyle"] and "Alex" not in json.dumps(fact)

    # Ask's budget tool names both numbers, with the plain line that says which is which.
    ensure_facts(samples.db_path, None)
    with ai_connection(samples.db_path) as ai:
        r = tools.execute(ai, "budget_status", {"month": OCT})
    assert not r.is_error, r.content
    out = json.loads(r.content)
    assert out["about"] == tools.BUDGET_ABOUT
    life = next(g for g in out["groups"] if g["key"] == "lifestyle")
    assert (life["plan"], life["chat_plan"], life["source"]) == ("3300.00", "3650.00", "edited")


def test_the_sandbox_denies_the_edits(samples: Settings) -> None:
    with ai_connection(samples.db_path) as ai, pytest.raises(AuthorizerDenied):
        run(ai, "SELECT * FROM budget_target_edits")
