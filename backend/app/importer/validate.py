"""Step 5 of the import (PLAN 4.11): validate a verified run.

- Strict models for snapshot.json and every CSV (schema version first).
- Run id, mode and version agree between manifest.json and snapshot.json.
- `detect_sensitive` plus the private lists on every text field and key.
- Registry: account and person keys must come from runs earlier in run order,
  or from this run's new_accounts (accounts) or people (people).
- Statements: CSV links, txn_count and txn_sum, reconciliation per account
  type, statement balances equal closing (or `balance`), holdings point at a
  statement with has_holdings, no stock plan holdings. Every statement has its
  statement balance in the run (except cards tracked by payments, which have
  none), and a statement with has_holdings needs the holdings section.
- 1.1 state: recurring, installment plan and flag references point at known
  categories, accounts and people (item rules live in the models).
- 1.2 utility bills: `paid_from` is a known account. Warnings (not rejects):
  a 1 or 2 cent total difference, days that do not match the period, and an
  overlapping period (`models.utility_warnings`). Bill dates may be before
  2026 (history back to 2021); the item rules live in the models.
- 1.3 rewards and trips: cards, people and categories they name are known
  (item rules and references inside the section live in the models).
  Warnings: `reward-stale` (`models.reward_warnings`) and `trip-unknown`, a
  trip_key on a row, a plan component, a flag or a redemption that no run so
  far defines (this run's trips, or an earlier run's, tombstones left out).
- 1.4 goals: the accounts and the person a goal names are known (the item
  rules and the college checks live in the models). Warning: `recurring-no-date`,
  an active recurring item with no date (`models.recurring_warnings`).
- Rows: date windows, known categories, no refunds in income categories,
  known counterparties and cardholders. A row to a card tracked by payments
  with is_transfer not true is a payment with a spending category.

`schema/check_run.py` (Checker.semantic and reconcile) is the reference. Both
use the same rule ids. Results are rule ids and locations, never values.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.importer.keys import parse_csv_path, statement_key
from app.importer.models import (
    CASH_LIKE_TYPES,
    INVESTMENT_TYPES,
    RULES,
    CsvRow,
    Issue,
    Snapshot,
    Statement,
    Trip,
    defined_trips,
    load_snapshot,
    parse_csv,
    recurring_warnings,
    reward_warnings,
    to_cents,
    trip_references,
    utility_warnings,
)
from app.importer.order import run_order_key
from app.importer.sanitize import PrivateRules, is_stock_plan_name, load_redaction, scan_model
from app.importer.verify import MANIFEST, SNAPSHOT, VerifiedRun, safe_where

HISTORY_START = "2026-01-01"
TXN_DAYS_BEFORE = 60
POST_DAYS_AFTER = 5
S = f"{SNAPSHOT}:"

# Rule ids the app adds on top of the format rules (models.RULES).
APP_RULES: dict[str, str] = {
    "statement-unreconciled": "Statement is marked not reconciled. Its months are never complete.",
    "plan-groups-sum": "The plan's groups do not add up to its planned spending "
    "(which includes saved and invested).",
    "statement-drift": "A later run sent this statement again with different content. "
    "The earlier version stays.",
    "internal": "The import stopped on an unexpected error. Nothing was changed.",
    "waiting-earlier-runs": "It uses account or person keys that no earlier imported run has. "
    "It is tried again after each import, in case an earlier run adds them. "
    "If no earlier run is missing, fix the keys in a new run.",
}

# Problems that an earlier run, not yet imported, may fix.
DEPENDENCY_RULES = frozenset({"account-unknown", "person-unknown"})


def rule_message(rule: str) -> str:
    """Plain message for any rule id. Never includes a value."""
    return RULES.get(rule) or APP_RULES.get(rule) or RULES["schema"]


@dataclass
class Registry:
    """What the database knows from imported runs earlier in run order."""

    accounts: dict[str, str | None] = field(default_factory=dict)  # key -> type
    tracked: dict[str, str] = field(default_factory=dict)  # key -> tracked_by, when set
    people: set[str] = field(default_factory=set)
    categories: dict[str, str] = field(default_factory=dict)  # key -> kind (aliases resolved)
    trips: set[str] = field(default_factory=set)  # trip keys earlier runs sent (1.3)


@dataclass
class ValidRun:
    run_id: str
    mode: str
    schema_version: str
    manifest_sha256: str
    snapshot: Snapshot = field(repr=False)
    rows: dict[str, list[CsvRow]] = field(repr=False)  # statement_key -> rows in CSV order
    warnings: list[Issue] = field(default_factory=list)


def load_categories(conn: sqlite3.Connection) -> dict[str, str]:
    cats = {
        r["category_key"]: r["kind"]
        for r in conn.execute("SELECT category_key, kind FROM categories")
    }
    for r in conn.execute("SELECT old_key, new_key FROM category_aliases"):
        if r["new_key"] in cats:
            cats.setdefault(r["old_key"], cats[r["new_key"]])
    return cats


def load_registry(conn: sqlite3.Connection, run_id: str) -> Registry:
    """Accounts, people and trips from imported runs that come before `run_id` in run order."""
    reg = Registry(categories=load_categories(conn))
    me = run_order_key(run_id)
    rows = conn.execute(
        """SELECT s.run_id, s.section, s.json FROM run_sections s
           JOIN import_runs r ON r.run_id = s.run_id
           WHERE r.status IN ('imported', 'importing')
             AND s.section IN ('accounts', 'new_accounts', 'people', 'trips')"""
    ).fetchall()
    for r in sorted(rows, key=lambda r: run_order_key(r["run_id"])):
        if run_order_key(r["run_id"]) >= me:
            continue
        items = json.loads(r["json"])
        if r["section"] == "people":
            reg.people.update(p["key"] for p in items)
        elif r["section"] == "trips":
            reg.trips.update(t["key"] for t in items if not t.get("deleted"))
        elif r["section"] == "accounts":
            reg.accounts.update({a["key"]: a["type"] for a in items})
            reg.tracked.update(
                {a["key"]: a["tracked_by"] or "statements" for a in items if "tracked_by" in a}
            )
        else:
            for a in items:
                reg.accounts.setdefault(a["key"], None)
    return reg


def _add_days(d: str, n: int) -> str:
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()


class _Checker:
    def __init__(self, run: VerifiedRun, registry: Registry, private: PrivateRules | None):
        self.run = run
        self.registry = registry
        self.private = private
        self.issues: list[Issue] = []
        self.warnings: list[Issue] = []

    def add(self, rule: str, where: str) -> None:
        self.issues.append(Issue(rule, safe_where(where, self.private)))

    def extend(self, issues: list[Issue]) -> None:
        for i in issues:
            self.add(i.rule, i.where)

    # ---- files ----

    def snapshot(self) -> Snapshot | None:
        snap, problems = load_snapshot(self.run.files[SNAPSHOT])
        self.extend(problems)
        if snap is None:
            return None
        m = self.run.manifest
        if snap.run_id != self.run.run_id:
            self.add("run-id-mismatch", f"{S}run_id")
        if snap.mode != m.mode:
            self.add("mode-mismatch", f"{S}mode")
        if snap.schema_version != m.schema_version:
            self.add("schema", f"{S}schema_version")
        self.extend(scan_model(m, f"{MANIFEST}:", self.private))
        self.extend(scan_model(snap, S, self.private))
        return snap

    def csvs(self) -> dict[str, list[CsvRow]]:
        out = {}
        for rel, data in self.run.files.items():
            if not rel.endswith(".csv"):
                continue
            rows, problems = parse_csv(data, rel)
            self.extend(problems)
            for n, row in enumerate(rows, start=1):
                self.extend(scan_model(row, f"{rel}:row {n}.", self.private))
            if not problems:
                out[rel] = rows
        return out

    # ---- cross-file checks ----

    def semantic(self, snap: Snapshot, csvs: dict[str, list[CsvRow]]) -> dict[str, list[CsvRow]]:
        reg = self.registry
        new_keys = {a.key for a in snap.new_accounts or []}
        this_accounts = {a.key: a.type for a in snap.accounts or []}
        known = set(reg.accounts) | new_keys
        types = dict(reg.accounts)
        types.update(this_accounts)
        people = reg.people | {p.key for p in snap.people or []}
        self.known, self.people = known, people
        tracked = dict(reg.tracked)
        for a in snap.accounts or []:
            if "tracked_by" in a.model_fields_set:
                tracked[a.key] = a.tracked_by or "statements"
        self.by_payments = {k for k, v in tracked.items() if v == "payments"}

        for i, a in enumerate(snap.accounts or []):
            if a.key not in known:
                self.add("account-unknown", f"{S}accounts[{i}].key")
        for i, a in enumerate(snap.new_accounts or []):
            if a.key not in this_accounts:
                self.add("new-account-details", f"{S}new_accounts[{i}]")
        for i, a in enumerate(snap.accounts or []):
            self.need_account(a.secured_by, f"{S}accounts[{i}].secured_by")
            self.need_person(a.owner, f"{S}accounts[{i}].owner")
        for name in ("statements", "balances", "holdings", "contributions"):
            for i, it in enumerate(getattr(snap, name) or []):
                self.need_account(it.account_key, f"{S}{name}[{i}].account_key")
        for i, c in enumerate(snap.contributions or []):
            self.need_person(c.person, f"{S}contributions[{i}].person")
        if snap.income:
            for i, p in enumerate(snap.income.paychecks):
                self.need_person(p.person, f"{S}income.paychecks[{i}].person")
            for i, p in enumerate(snap.income.income_streams or []):
                self.need_person(p.person, f"{S}income.income_streams[{i}].person")
        for i, r in enumerate(snap.recurring or []):
            where = f"{S}recurring[{i}]"
            if r.category_key not in reg.categories:
                self.add("category-unknown", f"{where}.category_key")
            self.need_account(r.account_key, f"{where}.account_key")
            self.need_account(r.preferred_account_key, f"{where}.preferred_account_key")
            self.need_person(r.owner, f"{where}.owner")
        for i, p in enumerate(snap.installment_plans or []):
            self.need_account(p.account_key, f"{S}installment_plans[{i}].account_key")
        for i, f in enumerate(snap.flags or []):
            where = f"{S}flags[{i}].related_key"
            if f.related_kind == "account":
                self.need_account(f.related_key, where)
            elif f.related_kind == "person":
                self.need_person(f.related_key, where)
            elif f.related_kind == "category" and f.related_key not in reg.categories:
                self.add("category-unknown", where)
        for i, b in enumerate(snap.budgets or []):
            if b.category_key and b.category_key not in reg.categories:
                self.add("category-unknown", f"{S}budgets[{i}].category_key")
            if b.scope:
                self.need_person(b.scope.owner, f"{S}budgets[{i}].scope.owner")
                for j, card in enumerate(b.scope.cards or []):
                    self.need_account(card, f"{S}budgets[{i}].scope.cards[{j}]")
        for i, g in enumerate(snap.goals or []):
            for j, key in enumerate(g.accounts):
                self.need_account(key, f"{S}goals[{i}].accounts[{j}]")
            self.need_person(g.person, f"{S}goals[{i}].person")

        for i, u in enumerate(snap.utility_bills or []):
            self.need_account(getattr(u, "paid_from", None), f"{S}utility_bills[{i}].paid_from")
        self.warnings += utility_warnings(snap, SNAPSHOT)
        self.warnings += recurring_warnings(snap, SNAPSHOT)
        self.rewards_and_trips(snap, csvs)

        for i, p in enumerate(snap.cashflow_plan or []):
            if sum(to_cents(g.amount) for g in p.by_group) != to_cents(p.planned_spending):
                self.warnings.append(Issue("plan-groups-sum", f"{S}cashflow_plan[{i}].by_group"))

        statements = {
            (st.account_key, st.period_end): (i, st) for i, st in enumerate(snap.statements or [])
        }
        csv_by_stmt: dict[tuple[str, str], tuple[str, list[CsvRow]]] = {}
        for rel in self.run.files:
            if not rel.endswith(".csv"):
                continue
            ident = parse_csv_path(rel)
            if ident not in statements:
                self.add("csv-orphan", rel)
            elif rel in csvs:
                csv_by_stmt[ident] = (rel, csvs[rel])
        stmt_balances = {
            (b.account_key, b.as_of)
            for b in snap.balances or []
            if not getattr(b, "deleted", False) and b.source == "statement"
        }
        rows_out: dict[str, list[CsvRow]] = {}
        for ident, (i, st) in statements.items():
            where = f"{S}statements[{i}]"
            if not st.reconciled:
                self.warnings.append(Issue("statement-unreconciled", where))
            if st.account_key not in self.by_payments and ident not in stmt_balances:
                self.add("statement-balance-missing", where)
            if st.has_holdings and snap.holdings is None:
                self.add("holdings-missing", where)
            rel, rows = csv_by_stmt.get(ident, ("", None))
            if rows is None:
                csv_listed = any(parse_csv_path(p) == ident for p in self.run.files)
                if st.txn_count > 0 and not csv_listed:
                    self.add("csv-missing", where)
                if csv_listed:
                    continue  # the CSV itself failed and is already reported
                rows = []
            if len(rows) != st.txn_count:
                self.add("txn-count", where)
            amounts = [r.amount_cents for r in rows]
            if st.txn_sum is not None and rows and to_cents(st.txn_sum) != sum(amounts):
                self.add("txn-sum", where)
            self.reconcile(st, types.get(st.account_key), amounts, where)
            for n, r in enumerate(rows, start=1):
                self.row_checks(st, r, f"{rel}:row {n}")
            rows_out[statement_key(*ident)] = rows

        for i, b in enumerate(snap.balances or []):
            if getattr(b, "deleted", False) or b.source != "statement":
                continue
            if b.account_key in self.by_payments:
                self.add("tracked-balance", f"{S}balances[{i}]")
                continue
            hit = statements.get((b.account_key, b.as_of))
            if hit is None:
                self.add("balance-statement", f"{S}balances[{i}]")
                continue
            st = hit[1]
            expected = st.balance if st.balance is not None else st.closing
            if to_cents(b.balance) != to_cents(expected):
                self.add("balance-closing", f"{S}balances[{i}].balance")
        stock_plan_id = load_redaction().stock_plan_id
        for i, h in enumerate(snap.holdings or []):
            hit = statements.get((h.account_key, h.period_end))
            if hit is None or hit[1].has_holdings is not True:
                self.add("holding-statement", f"{S}holdings[{i}]")
            if is_stock_plan_name(h.name):
                self.add(stock_plan_id, f"{S}holdings[{i}].name")
        return rows_out

    def rewards_and_trips(self, snap: Snapshot, csvs: dict[str, list[CsvRow]]) -> None:
        """Format 1.3: references to cards, people and categories, and the warnings."""
        if snap.rewards is not None:
            for i, p in enumerate(snap.rewards.programs):
                where = f"{S}rewards.programs[{i}]"
                self.need_person(p.owner, f"{where}.owner")
                for j, card in enumerate(p.earned_on or []):
                    self.need_account(card, f"{where}.earned_on[{j}]")
                self.need_account(p.status_via, f"{where}.status_via")
            for i, b in enumerate(snap.rewards.benefits):
                where = f"{S}rewards.benefits[{i}]"
                self.need_account(b.account_key, f"{where}.account_key")
                for j, key in enumerate(b.category_keys or []):
                    if key not in self.registry.categories:
                        self.add("category-unknown", f"{where}.category_keys[{j}]")
            for name in ("milestones", "routing"):
                for i, item in enumerate(getattr(snap.rewards, name)):
                    self.need_account(item.account_key, f"{S}rewards.{name}[{i}].account_key")
        for i, t in enumerate(snap.trips or []):
            if not isinstance(t, Trip):
                continue
            for j, person in enumerate(t.people or []):
                self.need_person(person, f"{S}trips[{i}].people[{j}]")
            for j, item in enumerate(t.items):
                self.need_account(item.account_key, f"{S}trips[{i}].items[{j}].account_key")
                self.need_person(item.person, f"{S}trips[{i}].items[{j}].person")
        self.warnings += reward_warnings(snap, SNAPSHOT)
        known = self.registry.trips | defined_trips(snap)
        refs = list(trip_references(snap, SNAPSHOT))
        for rel, rows in csvs.items():
            refs += [(r.trip_key, f"{rel}:row {n}.trip_key") for n, r in enumerate(rows, start=1)]
        for key, where in refs:
            if key and key not in known:
                self.warnings.append(Issue("trip-unknown", where))

    def need_account(self, key: str | None, where: str) -> None:
        if key and key not in self.known:
            self.add("account-unknown", where)

    def need_person(self, key: str | None, where: str) -> None:
        if key and key not in self.people:
            self.add("person-unknown", where)

    def reconcile(
        self, st: Statement, acct_type: str | None, amounts: list[int], where: str
    ) -> None:
        if not st.reconciled:
            return
        if st.opening is None:
            if amounts:
                self.add("statement-opening", where)
            return
        opening, closing = to_cents(st.opening), to_cents(st.closing)

        def c(value: str | None) -> int | None:
            return to_cents(value) if value is not None else None

        credits, debits = c(st.credits), c(st.debits)
        if acct_type in CASH_LIKE_TYPES:
            if amounts:
                if opening + sum(amounts) != closing:
                    self.add("statement-unbalanced", where)
                if credits is not None and credits != sum(a for a in amounts if a > 0):
                    self.add("statement-credits", where)
                if debits is not None and debits != sum(a for a in amounts if a < 0):
                    self.add("statement-debits", where)
            elif opening + (credits or 0) + (debits or 0) != closing:
                self.add("statement-unbalanced", where)
        elif acct_type in INVESTMENT_TYPES:
            flows, market = c(st.net_flows), c(st.market_change)
            if flows is None or market is None or opening + flows + market != closing:
                self.add("statement-unbalanced", where)

    def row_checks(self, st: Statement, r: CsvRow, where: str) -> None:
        start, end = st.period_start, st.period_end
        if not r.plan_key and not (_add_days(start, -TXN_DAYS_BEFORE) <= r.txn_date <= end):
            self.add("date-window", f"{where}.txn_date")
        if r.post_date and not (start <= r.post_date <= _add_days(end, POST_DAYS_AFTER)):
            self.add("date-window", f"{where}.post_date")
        kind = self.registry.categories.get(r.category_key)
        if kind is None:
            self.add("category-unknown", f"{where}.category_key")
        elif r.txn_type == "refund" and kind == "income":
            self.add("refund-income", f"{where}.category_key")
        self.need_account(r.counterparty_account_key, f"{where}.counterparty_account_key")
        self.need_person(r.cardholder, f"{where}.cardholder")
        if r.counterparty_account_key in self.by_payments and r.is_transfer != "true":
            if r.txn_type != "payment" or kind != "spending":
                self.add("tracked-payment", f"{where}.is_transfer")


def validate_run(
    run: VerifiedRun, registry: Registry, private: PrivateRules | None = None
) -> tuple[ValidRun | None, list[Issue]]:
    """Run step 5. Returns the parsed run, or None and the issues found."""
    ck = _Checker(run, registry, private)
    snap = ck.snapshot()
    csvs = ck.csvs()
    rows: dict[str, list[CsvRow]] = {}
    if snap is not None:
        rows = ck.semantic(snap, csvs)
    if ck.issues or snap is None:
        return None, sorted(set(ck.issues))
    return (
        ValidRun(
            run_id=run.run_id,
            mode=snap.mode,
            schema_version=snap.schema_version,
            manifest_sha256=run.manifest_sha256,
            snapshot=snap,
            rows=rows,
            warnings=sorted(set(ck.warnings)),
        ),
        [],
    )
