"""Step 6 of the import (PLAN 4.11): write one validated run to the database.

The caller holds the write lock and an open BEGIN IMMEDIATE transaction, and
has already written the run's `import_runs` row (status 'importing') and its
`run_sections`. Runs are always applied in run order: `replay` rebuilds the
imported tables from `run_sections` when a run arrives out of order, so the
result never depends on arrival order.

- Registry: upsert people and accounts. A field the run sets wins. Fields the
  run leaves out keep their value. Backfill runs only add unknown keys.
- Statements: every version is stored. The winning version (4.9) is current.
  When this run wins, its rows are upserted by txn_key (only changed columns),
  rows it dropped get removed_at, and the statement's balances and holdings
  are replaced. When it loses, it counts as superseded (same content) or drift.
  Overrides on rows the new version dropped are re-keyed to a matching new
  row or orphaned (`services.overrides.reattach_overrides`, 4.11 step 6).
- Other balances: the newest run in run order wins per key. Tombstones delete.
- Paychecks and contributions (Phase 5): the newest run in run order wins per
  identity. Tombstones delete. Runs imported before Phase 5 fill these tables
  with `cli reapply`. A contribution links to the same person's paycheck on
  its date when there is exactly one.
- Utility bills (format 1.2): the newest run in run order wins per
  utility_key and period_end. A bill is replaced whole (its lines and credits
  with it) only when something changed. Tombstones delete. Runs imported
  before migration 0007 fill these tables with `cli reapply`.
- Trips (format 1.3): the newest run in run order wins per trip key. A trip is
  replaced whole (its people, items and IOUs with it) only when something
  changed. Tombstones delete. Rewards has no table: it stays in run_sections.
- Format 1.4 (migration 0012): account `original` and `rate_changes` (registry
  fields: one the run leaves out keeps its value, and `[]` clears the schedule),
  and trip item `due_on` and `maybe`. Runs imported before 0012 fill them with
  `cli reapply`. Every other 1.4 field stays run JSON in run_sections.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.importer.keys import (
    assign_occurrences,
    contribution_key,
    desc_norm,
    paycheck_key,
    statement_key,
    txn_key,
    utility_bill_key,
)
from app.importer.models import (
    Account,
    BalanceTombstone,
    Contribution,
    ContributionTombstone,
    CsvRow,
    Holding,
    Issue,
    NewAccount,
    Paycheck,
    PaycheckTombstone,
    Person,
    RateChange,
    Snapshot,
    Statement,
    Trip,
    TripTombstone,
    UtilityBill,
    UtilityBillTombstone,
    csv_bool,
    to_cents,
    to_micro,
    to_milli_pct,
)
from app.importer.order import run_order_key, winning_run
from app.importer.validate import HISTORY_START, ValidRun
from app.services.overrides import reattach_overrides

ROWS_PREFIX = "rows:"
_RUN_FIELDS = ("schema_version", "run_id", "mode")
# Imported tables, in the order they are cleared (children first).
IMPORTED_TABLES = (
    "trip_ious",
    "trip_items",
    "trip_people",
    "trips",
    "utility_bill_credits",
    "utility_bill_lines",
    "utility_bills",
    "contributions",
    "paychecks",
    "holdings",
    "balances",
    "transactions",
    "statements",
    "accounts",
    "people",
)
IN_CASHFLOW_TYPES = frozenset({"checking", "credit_card", "store_card"})


@dataclass
class ApplyStats:
    statements: int = 0  # statements in the run
    applied: int = 0  # versions that won and were written
    replaced: int = 0  # won over an earlier current version
    superseded: int = 0  # lost, same content as the current version
    drift: int = 0  # lost, different content (extraction drift)
    rows: int = 0  # CSV rows in the run
    rekeyed: int = 0  # overrides moved to a changed row's new key (4.11 step 6)
    orphaned: int = 0  # overrides whose row is gone, with no unique match
    warnings: list[Issue] = field(default_factory=list)


# --------------------------------------------------------------------------
# Canonical JSON and run_sections
# --------------------------------------------------------------------------


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_sections(run: ValidRun) -> dict[str, str]:
    """Section name -> canonical JSON. Statement rows are 'rows:<statement_key>'.

    Fields left out of the export stay left out (exclude_unset), so a replay
    sees exactly what the run set.
    """
    dumped = run.snapshot.model_dump(mode="json", exclude_unset=True)
    out = {name: canonical(value) for name, value in dumped.items() if name not in _RUN_FIELDS}
    for skey, rows in run.rows.items():
        out[ROWS_PREFIX + skey] = canonical([r.model_dump(mode="json") for r in rows])
    return out


def store_sections(conn: sqlite3.Connection, run: ValidRun) -> None:
    conn.execute("DELETE FROM run_sections WHERE run_id = ?", (run.run_id,))
    conn.executemany(
        "INSERT INTO run_sections (run_id, section, sha256, json) VALUES (?, ?, ?, ?)",
        [(run.run_id, name, sha256_text(text), text) for name, text in run_sections(run).items()],
    )


def load_stored_run(conn: sqlite3.Connection, run_id: str) -> ValidRun:
    """Rebuild a validated run from import_runs and run_sections."""
    rec = conn.execute(
        "SELECT mode, schema_version, manifest_sha256 FROM import_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    snap: dict[str, Any] = {
        "schema_version": rec["schema_version"],
        "run_id": run_id,
        "mode": rec["mode"],
    }
    rows: dict[str, list[CsvRow]] = {}
    for r in conn.execute("SELECT section, json FROM run_sections WHERE run_id = ?", (run_id,)):
        value = json.loads(r["json"])
        if r["section"].startswith(ROWS_PREFIX):
            rows[r["section"][len(ROWS_PREFIX) :]] = [CsvRow.model_validate(x) for x in value]
        else:
            snap[r["section"]] = value
    return ValidRun(
        run_id=run_id,
        mode=rec["mode"],
        schema_version=rec["schema_version"],
        manifest_sha256=rec["manifest_sha256"],
        snapshot=Snapshot.model_validate(snap),
        rows=rows,
    )


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


def _money(v: str | None) -> int | None:
    return to_cents(v) if v is not None else None


def _flag(v: bool | None) -> int | None:
    return int(v) if v is not None else None


def _rate_changes(v: list[RateChange] | None) -> str | None:
    """1.4: the schedule as canonical JSON in the run's own shape (apr as the string).
    [] and null clear it (NULL)."""
    return canonical([rc.model_dump(mode="json") for rc in v]) if v else None


# Account model field -> (column, converter). The key is handled separately.
_ACCOUNT_FIELDS: dict[str, tuple[str, Any]] = {
    "display_name": ("display_name", None),
    "institution": ("institution", None),
    "type": ("type", None),
    "owner": ("owner_person_key", None),
    "ownership": ("ownership", None),
    "group": ("account_group", None),
    "is_asset": ("is_asset", _flag),
    "include_in_net_worth": ("include_in_net_worth", _flag),
    "statement_cadence": ("statement_cadence", None),
    "nw_group": ("nw_group", None),
    "nw_group_label": ("nw_group_label", None),
    "subtype": ("subtype", None),
    "custodian": ("custodian", None),
    "advisor": ("advisor", None),
    "in_cashflow": ("in_cashflow", _flag),
    "tracked_by": ("tracked_by", None),
    "stale_after_days": ("stale_after_days", None),
    "last4": ("last4", None),
    "opened_on": ("opened_on", None),
    "closed_on": ("closed_on", None),
    "active": ("active", _flag),
    "dormant": ("dormant", _flag),
    "display_order": ("display_order", None),
    "apr": ("apr_milli_pct", lambda v: to_milli_pct(v) if v is not None else None),
    "rate_type": ("rate_type", None),
    "credit_limit": ("credit_limit_cents", _money),
    "min_payment": ("min_payment_cents", _money),
    "autopay": ("autopay", None),
    "cycle_close_day": ("cycle_close_day", None),
    "servicer": ("servicer", None),
    "payoff_date": ("payoff_date", None),
    "priority": ("priority", None),
    "policy_note": ("policy_note", None),
    "secured_by": ("secured_by_account_key", None),
    "annual_fee": ("annual_fee_cents", _money),
    "fee_renews_on": ("fee_renews_on", None),
    "notes": ("notes", None),
    "original": ("original_cents", _money),  # 1.4
    "rate_changes": ("rate_changes_json", _rate_changes),  # 1.4
}


def _account_values(a: Account, fields: Iterable[str], acct_type: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in fields:
        if name not in _ACCOUNT_FIELDS:
            continue
        column, conv = _ACCOUNT_FIELDS[name]
        value = getattr(a, name)
        value = conv(value) if conv else value
        if value is None and column == "in_cashflow":
            value = int(acct_type in IN_CASHFLOW_TYPES)
        elif value is None and column == "active":
            value = 1
        elif value is None and column == "dormant":
            value = 0
        elif value is None and column == "tracked_by":
            value = "statements"
        out[column] = value
    return out


def _person_values(p: Person, fields: Iterable[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in fields:
        if name == "display_name":
            out["display_name"] = p.display_name
        elif name == "aliases":
            out["aliases_json"] = json.dumps(p.aliases) if p.aliases is not None else None
        elif name == "role":
            out["role"] = p.role
    return out


def _upsert(
    conn: sqlite3.Connection,
    table: str,
    key_col: str,
    key: str,
    insert_values: dict[str, Any],
    update_values: dict[str, Any],
    run_id: str,
    add_only: bool,
) -> None:
    old = conn.execute(f"SELECT * FROM {table} WHERE {key_col} = ?", (key,)).fetchone()
    if old is None:
        cols = [key_col, *insert_values, "first_run_id", "run_id"]
        vals = [key, *insert_values.values(), run_id, run_id]
        marks = ", ".join("?" for _ in cols)
        conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({marks})", vals)
        return
    if add_only:
        return
    changed = {c: v for c, v in update_values.items() if old[c] != v}
    if changed:
        sets = ", ".join(f"{c} = ?" for c in changed)
        conn.execute(
            f"UPDATE {table} SET {sets}, run_id = ? WHERE {key_col} = ?",
            [*changed.values(), run_id, key],
        )


def apply_registry(
    conn: sqlite3.Connection,
    run_id: str,
    mode: str,
    people: list[Person] | None,
    accounts: list[Account] | None,
    new_accounts: list[NewAccount] | None,
) -> None:
    add_only = mode == "backfill"
    for p in people or []:
        _upsert(
            conn,
            "people",
            "person_key",
            p.key,
            _person_values(p, ("display_name", "aliases", "role")),
            _person_values(p, p.model_fields_set),
            run_id,
            add_only,
        )
    for a in accounts or []:
        _upsert(
            conn,
            "accounts",
            "account_key",
            a.key,
            _account_values(a, _ACCOUNT_FIELDS, a.type),
            _account_values(a, a.model_fields_set, a.type),
            run_id,
            add_only,
        )
    for na in new_accounts or []:
        if na.opened_on is not None:
            conn.execute(
                "UPDATE accounts SET opened_on = ? WHERE account_key = ? AND opened_on IS NULL",
                (na.opened_on, na.key),
            )


# --------------------------------------------------------------------------
# Statements, rows, balances, holdings
# --------------------------------------------------------------------------


def _statement_values(st: Statement) -> dict[str, Any]:
    return {
        "account_key": st.account_key,
        "period_start": st.period_start,
        "period_end": st.period_end,
        "statement_date": st.statement_date,
        "due_date": st.due_date,
        "opening_cents": _money(st.opening),
        "closing_cents": to_cents(st.closing),
        "balance_cents": _money(st.balance),
        "credits_cents": _money(st.credits),
        "debits_cents": _money(st.debits),
        "fees_cents": _money(st.fees),
        "interest_cents": _money(st.interest),
        "min_due_cents": _money(st.min_due),
        "credit_limit_cents": _money(st.credit_limit),
        "net_flows_cents": _money(st.net_flows),
        "market_change_cents": _money(st.market_change),
        "txn_count": st.txn_count,
        "txn_sum_cents": _money(st.txn_sum),
        "reconciled": int(st.reconciled),
        "reconcile_note": st.reconcile_note,
        "ocr": int(bool(st.ocr)),
        "has_holdings": int(bool(st.has_holdings)),
        "source_sha256": st.source_sha256,
        "flags_text": st.flags_text,
    }


def statement_content_sha(st: Statement, rows: list[CsvRow]) -> str:
    """Hash of one statement version: its fields plus its rows in CSV order."""
    return sha256_text(
        canonical(
            {
                "statement": st.model_dump(mode="json"),
                "rows": [r.model_dump(mode="json") for r in rows],
            }
        )
    )


def _opt(cell: str) -> str | None:
    return cell or None


def _row_values(st: Statement, skey: str, r: CsvRow, norm: str, occ: int) -> dict[str, Any]:
    return {
        "statement_key": skey,
        "account_key": st.account_key,
        "txn_date": r.txn_date,
        "post_date": _opt(r.post_date),
        "amount_cents": r.amount_cents,
        "description": r.description,
        "desc_norm": norm,
        "merchant": _opt(r.merchant),
        "category_key": r.category_key,
        "cardholder_person_key": _opt(r.cardholder),
        "txn_type": r.txn_type,
        "is_transfer": _flag(csv_bool(r.is_transfer)),
        "counterparty_account_key": _opt(r.counterparty_account_key),
        "is_reimbursable": _flag(csv_bool(r.is_reimbursable)),
        "exclude_from_budget": _flag(csv_bool(r.exclude_from_budget)),
        "plan_key": _opt(r.plan_key),
        "recurring_key": _opt(r.recurring_key),
        "trip_key": _opt(r.trip_key),
        "hsa_eligible": _flag(csv_bool(r.hsa_eligible)),
        "needs_review": _flag(csv_bool(r.needs_review)),
        "confidence_pct": to_cents(r.confidence) if r.confidence else None,
        "notes": _opt(r.notes),
        "occurrence_n": occ,
        "pre_history": int(r.txn_date < HISTORY_START),
        "ocr": int(bool(st.ocr)),
    }


def apply_rows(
    conn: sqlite3.Connection, st: Statement, skey: str, rows: list[CsvRow], run_id: str, now: str
) -> tuple[int, int]:
    """Upsert the winning version's rows by txn_key. Rows it dropped get removed_at.

    Overrides on dropped rows are moved to a new row or orphaned (4.11 step 6).
    Returns (rekeyed, orphaned).
    """
    idents = [(r.txn_date, r.amount_cents, desc_norm(r.description)) for r in rows]
    new: dict[str, dict[str, Any]] = {}
    for r, (d, a, norm), occ in zip(rows, idents, assign_occurrences(idents), strict=True):
        new[txn_key(skey, d, a, norm, occ)] = _row_values(st, skey, r, norm, occ)
    existing = {
        row["txn_key"]: row
        for row in conn.execute("SELECT * FROM transactions WHERE statement_key = ?", (skey,))
    }
    for key, vals in new.items():
        old = existing.get(key)
        if old is None:
            cols = ["txn_key", *vals, "run_id"]
            marks = ", ".join("?" for _ in cols)
            conn.execute(
                f"INSERT INTO transactions ({', '.join(cols)}) VALUES ({marks})",
                [key, *vals.values(), run_id],
            )
            continue
        changed = {c: v for c, v in vals.items() if old[c] != v}
        if not changed and old["removed_at"] is None:
            continue
        sets = [f"{c} = ?" for c in changed] + ["removed_at = NULL"]
        params = list(changed.values())
        if changed:
            sets.append("run_id = ?")
            params.append(run_id)
        conn.execute(f"UPDATE transactions SET {', '.join(sets)} WHERE txn_key = ?", [*params, key])
    live_before = {k for k, old in existing.items() if old["removed_at"] is None}
    dropped = {k: existing[k] for k in live_before if k not in new}
    for key in dropped:
        conn.execute("UPDATE transactions SET removed_at = ? WHERE txn_key = ?", (now, key))
    fresh = {k: v for k, v in new.items() if k not in live_before}
    return reattach_overrides(conn, dropped, fresh, now)


def _insert_holding(conn: sqlite3.Connection, skey: str, h: Holding, run_id: str) -> None:
    qty = to_micro(h.quantity) if h.quantity is not None else None
    value = to_cents(h.value)
    if value == 0 and not qty:
        return  # dropped only when both quantity and value are zero
    conn.execute(
        """INSERT INTO holdings (statement_key, account_key, as_of, holding_key, holding_name,
               symbol, cusip, quantity_micro, price_micro, value_cents, cost_basis_cents,
               asset_class, run_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            skey,
            h.account_key,
            h.period_end,
            h.key,
            h.name,
            h.symbol,
            h.cusip,
            qty,
            to_micro(h.price) if h.price is not None else None,
            value,
            _money(h.cost_basis),
            h.asset_class,
            run_id,
        ),
    )


def _upsert_balance(conn: sqlite3.Connection, b: Any, skey: str | None, run_id: str) -> None:
    conn.execute(
        """INSERT INTO balances (account_key, as_of, source, balance_cents, statement_key,
               method, source_note, run_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (account_key, as_of, source) DO UPDATE SET
             balance_cents = excluded.balance_cents, statement_key = excluded.statement_key,
             method = excluded.method, source_note = excluded.source_note,
             run_id = excluded.run_id
           WHERE balance_cents IS NOT excluded.balance_cents
              OR statement_key IS NOT excluded.statement_key
              OR method IS NOT excluded.method
              OR source_note IS NOT excluded.source_note""",
        (
            b.account_key,
            b.as_of,
            b.source,
            to_cents(b.balance),
            skey,
            b.method,
            b.source_note,
            run_id,
        ),
    )


def apply_run(conn: sqlite3.Connection, run: ValidRun, now: str) -> ApplyStats:
    """Apply one validated run. The caller holds the write transaction."""
    snap = run.snapshot
    stats = ApplyStats(statements=len(snap.statements or []))
    apply_registry(conn, run.run_id, run.mode, snap.people, snap.accounts, snap.new_accounts)

    holdings: dict[tuple[str, str], list[Holding]] = {}
    for h in snap.holdings or []:
        holdings.setdefault((h.account_key, h.period_end), []).append(h)

    won: set[tuple[str, str]] = set()
    for i, st in enumerate(snap.statements or []):
        skey = statement_key(st.account_key, st.period_end)
        rows = run.rows.get(skey, [])
        stats.rows += len(rows)
        content = statement_content_sha(st, rows)
        vals = _statement_values(st)
        cols = ["statement_key", "run_id", "content_sha256", *vals]
        conn.execute(
            f"INSERT INTO statements ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            [skey, run.run_id, content, *vals.values()],
        )
        versions = conn.execute(
            """SELECT s.run_id, r.mode, s.is_current, s.content_sha256 FROM statements s
               JOIN import_runs r ON r.run_id = s.run_id WHERE s.statement_key = ?""",
            (skey,),
        ).fetchall()
        winner = winning_run((v["run_id"], v["mode"]) for v in versions)
        current = next((v for v in versions if v["is_current"]), None)
        if winner != run.run_id:
            if current is not None and current["content_sha256"] == content:
                stats.superseded += 1
            else:
                stats.drift += 1
                stats.warnings.append(Issue("statement-drift", f"snapshot.json:statements[{i}]"))
            continue
        stats.applied += 1
        if current is not None:
            stats.replaced += 1
        conn.execute("UPDATE statements SET is_current = 0 WHERE statement_key = ?", (skey,))
        conn.execute(
            "UPDATE statements SET is_current = 1 WHERE statement_key = ? AND run_id = ?",
            (skey, run.run_id),
        )
        rekeyed, orphaned = apply_rows(conn, st, skey, rows, run.run_id, now)
        stats.rekeyed += rekeyed
        stats.orphaned += orphaned
        conn.execute("DELETE FROM holdings WHERE statement_key = ?", (skey,))
        for h in holdings.get((st.account_key, st.period_end), []):
            _insert_holding(conn, skey, h, run.run_id)
        conn.execute(
            "DELETE FROM balances WHERE statement_key = ? AND source = 'statement'", (skey,)
        )
        won.add((st.account_key, st.period_end))

    for b in snap.balances or []:
        if isinstance(b, BalanceTombstone):
            conn.execute(
                "DELETE FROM balances WHERE account_key = ? AND as_of = ? AND source = ?",
                (b.account_key, b.as_of, b.source),
            )
        elif b.source == "statement":
            if (b.account_key, b.as_of) in won:
                _upsert_balance(conn, b, statement_key(b.account_key, b.as_of), run.run_id)
        else:
            _upsert_balance(conn, b, None, run.run_id)
    apply_income(conn, snap.income.paychecks if snap.income else [], run.run_id)
    apply_contributions(conn, snap.contributions or [], run.run_id)
    apply_utility_bills(conn, snap.utility_bills or [], run.run_id)
    apply_trips(conn, snap.trips or [], run.run_id)
    return stats


# --------------------------------------------------------------------------
# Paychecks and contributions (Phase 5)
# --------------------------------------------------------------------------


def _paycheck_values(p: Paycheck) -> dict[str, Any]:
    return {
        "person_key": p.person,
        "pay_date": p.pay_date,
        "period_start": p.period_start,
        "period_end": p.period_end,
        "kind": p.kind,
        "seq": p.seq,
        "gross_cents": to_cents(p.gross),
        "net_cents": to_cents(p.net),
        "k401_cents": _money(p.k401),
        "k401_milli_pct": to_milli_pct(p.k401_pct) if p.k401_pct is not None else None,
        "hsa_employee_cents": _money(p.hsa_employee),
        "hsa_employer_cents": _money(p.hsa_employer),
        "pretax_health_cents": _money(p.pretax_health),
        "ytd_k401_cents": _money(p.ytd_k401),
        "ytd_hsa_employee_cents": _money(p.ytd_hsa_employee),
        "ytd_hsa_employer_cents": _money(p.ytd_hsa_employer),
    }


def _replace_row(conn: sqlite3.Connection, table: str, key_col: str, key: str, vals: dict) -> None:
    """Insert or replace one row by key, touching it only when a value changed."""
    old = conn.execute(f"SELECT * FROM {table} WHERE {key_col} = ?", (key,)).fetchone()
    if old is not None and all(old[c] == v for c, v in vals.items() if c != "run_id"):
        return
    conn.execute(f"DELETE FROM {table} WHERE {key_col} = ?", (key,))
    cols = [key_col, *vals]
    conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
        [key, *vals.values()],
    )


def apply_income(
    conn: sqlite3.Connection, paychecks: list[Paycheck | PaycheckTombstone], run_id: str
) -> None:
    for p in paychecks:
        key = paycheck_key(p.person, p.pay_date, p.kind, p.seq)
        if isinstance(p, PaycheckTombstone):
            conn.execute("DELETE FROM paychecks WHERE paycheck_key = ?", (key,))
            conn.execute(
                "UPDATE contributions SET paycheck_key = NULL WHERE paycheck_key = ?", (key,)
            )
            continue
        _replace_row(
            conn, "paychecks", "paycheck_key", key, _paycheck_values(p) | {"run_id": run_id}
        )


def _paycheck_for(conn: sqlite3.Connection, person: str, day: str) -> str | None:
    """The person's paycheck on that date, when there is exactly one."""
    rows = conn.execute(
        "SELECT paycheck_key FROM paychecks WHERE person_key = ? AND pay_date = ?", (person, day)
    ).fetchall()
    return rows[0]["paycheck_key"] if len(rows) == 1 else None


def apply_contributions(
    conn: sqlite3.Connection,
    items: list[Contribution | ContributionTombstone],
    run_id: str,
) -> None:
    for c in items:
        key = contribution_key(c.person, c.account_key, c.kind, c.source, c.date)
        if isinstance(c, ContributionTombstone):
            conn.execute("DELETE FROM contributions WHERE contribution_key = ?", (key,))
            continue
        vals = {
            "person_key": c.person,
            "account_key": c.account_key,
            "kind": c.kind,
            "source": c.source,
            "date": c.date,
            "amount_cents": to_cents(c.amount),
            "paycheck_key": _paycheck_for(conn, c.person, c.date)
            if c.source == "current_employer"
            else None,
            "run_id": run_id,
        }
        _replace_row(conn, "contributions", "contribution_key", key, vals)


# --------------------------------------------------------------------------
# Utility bills (format 1.2)
# --------------------------------------------------------------------------

_BILL_COLS = (
    "utility_key",
    "provider",
    "bill_date",
    "period_start",
    "period_end",
    "days",
    "total_cents",
    "due_date",
    "paid_from_account_key",
    "heating_degree_days",
    "estimated_read",
    "note",
)
_LINE_COLS = ("service", "usage_micro", "unit", "charges_cents", "fixed_charges_cents")
_CREDIT_COLS = ("seq", "label", "amount_cents")


def _bill_rows(b: UtilityBill) -> tuple[tuple, list[tuple], list[tuple]]:
    """(bill values, line values, credit values) in column order."""
    bill = (
        b.utility_key,
        b.provider,
        b.bill_date,
        b.period_start,
        b.period_end,
        b.days,
        to_cents(b.total),
        b.due_date,
        b.paid_from,
        b.heating_degree_days,
        _flag(b.estimated_read),
        b.note,
    )
    lines = [
        (ln.service, to_micro(ln.usage), ln.unit, to_cents(ln.charges), _money(ln.fixed_charges))
        for ln in b.lines
    ]
    credits = [(n, c.label, to_cents(c.amount)) for n, c in enumerate(b.credits or [], start=1)]
    return bill, lines, credits


def _stored_bill(conn: sqlite3.Connection, key: str) -> tuple | None:
    row = conn.execute(
        f"SELECT {', '.join(_BILL_COLS)} FROM utility_bills WHERE bill_key = ?", (key,)
    ).fetchone()
    if row is None:
        return None
    lines = conn.execute(
        f"SELECT {', '.join(_LINE_COLS)} FROM utility_bill_lines WHERE bill_key = ? "
        "ORDER BY service",
        (key,),
    ).fetchall()
    credits = conn.execute(
        f"SELECT {', '.join(_CREDIT_COLS)} FROM utility_bill_credits WHERE bill_key = ? "
        "ORDER BY seq",
        (key,),
    ).fetchall()
    return tuple(row), [tuple(r) for r in lines], [tuple(r) for r in credits]


def apply_utility_bills(
    conn: sqlite3.Connection,
    items: list[UtilityBill | UtilityBillTombstone],
    run_id: str,
) -> None:
    """Runs arrive in run order, so the last write per bill is the newest run's."""
    for b in items:
        key = utility_bill_key(b.utility_key, b.period_end)
        if isinstance(b, UtilityBillTombstone):
            conn.execute("DELETE FROM utility_bills WHERE bill_key = ?", (key,))
            continue
        new = _bill_rows(b)
        if _stored_bill(conn, key) == new:
            continue
        bill, lines, credits = new
        conn.execute("DELETE FROM utility_bills WHERE bill_key = ?", (key,))  # cascades
        cols = ("bill_key", *_BILL_COLS, "run_id")
        conn.execute(
            f"INSERT INTO utility_bills ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})",
            (key, *bill, run_id),
        )
        conn.executemany(
            f"INSERT INTO utility_bill_lines (bill_key, {', '.join(_LINE_COLS)}) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(key, *ln) for ln in lines],
        )
        conn.executemany(
            f"INSERT INTO utility_bill_credits (bill_key, {', '.join(_CREDIT_COLS)}) "
            "VALUES (?, ?, ?, ?)",
            [(key, *c) for c in credits],
        )


# --------------------------------------------------------------------------
# Trips (format 1.3)
# --------------------------------------------------------------------------

_TRIP_COLS = (
    "name",
    "status",
    "start_date",
    "end_date",
    "month",
    "guests",
    "vehicle",
    "estimate_low_cents",
    "estimate_high_cents",
    "note",
)
_TRIP_ITEM_COLS = (
    "item_key",
    "kind",
    "label",
    "status",
    "tender",
    "amount_cents",
    "estimate_low_cents",
    "estimate_high_cents",
    "points_micro",
    "program_key",
    "account_key",
    "person_key",
    "note",
    "due_on",  # 1.4
    "maybe",  # 1.4
)
_TRIP_IOU_COLS = (
    "iou_key",
    "direction",
    "counterparty",
    "amount_cents",
    "status",
    "settled_on",
    "item_key",
    "note",
)


def _trip_rows(t: Trip) -> tuple[tuple, list[tuple], list[tuple], list[tuple]]:
    """(trip values, people, items, IOUs) in column order, each list in key order."""
    trip = (
        t.name,
        t.status,
        t.start_date,
        t.end_date,
        t.month,
        t.guests,
        t.vehicle,
        _money(t.estimate_low),
        _money(t.estimate_high),
        t.note,
    )
    people = [(p,) for p in t.people or []]
    items = [
        (
            i.key,
            i.kind,
            i.label,
            i.status,
            i.tender,
            _money(i.amount),
            _money(i.estimate_low),
            _money(i.estimate_high),
            to_micro(i.points) if i.points is not None else None,
            i.program_key,
            i.account_key,
            i.person,
            i.note,
            i.due_on,
            _flag(i.maybe),
        )
        for i in t.items
    ]
    ious = [
        (
            o.key,
            o.direction,
            o.counterparty,
            to_cents(o.amount),
            o.status,
            o.settled_on,
            o.item_key,
            o.note,
        )
        for o in t.ious or []
    ]
    return trip, people, items, ious


def _stored_trip(conn: sqlite3.Connection, key: str) -> tuple | None:
    row = conn.execute(
        f"SELECT {', '.join(_TRIP_COLS)} FROM trips WHERE trip_key = ?", (key,)
    ).fetchone()
    if row is None:
        return None

    def rows(table: str, cols: tuple[str, ...]) -> list[tuple]:
        sql = f"SELECT {', '.join(cols)} FROM {table} WHERE trip_key = ? ORDER BY {cols[0]}"
        return [tuple(r) for r in conn.execute(sql, (key,))]

    return (
        tuple(row),
        rows("trip_people", ("person_key",)),
        rows("trip_items", _TRIP_ITEM_COLS),
        rows("trip_ious", _TRIP_IOU_COLS),
    )


def apply_trips(conn: sqlite3.Connection, items: list[Trip | TripTombstone], run_id: str) -> None:
    """Runs arrive in run order, so the last write per trip is the newest run's."""
    for t in items:
        if isinstance(t, TripTombstone):
            conn.execute("DELETE FROM trips WHERE trip_key = ?", (t.key,))  # cascades
            continue
        new = _trip_rows(t)
        if _stored_trip(conn, t.key) == new:
            continue
        trip, people, trip_items, ious = new
        conn.execute("DELETE FROM trips WHERE trip_key = ?", (t.key,))  # cascades
        cols = ("trip_key", *_TRIP_COLS, "run_id")
        conn.execute(
            f"INSERT INTO trips ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
            (t.key, *trip, run_id),
        )
        conn.executemany(
            "INSERT INTO trip_people (trip_key, person_key) VALUES (?, ?)",
            [(t.key, *p) for p in people],
        )
        conn.executemany(
            f"INSERT INTO trip_items (trip_key, {', '.join(_TRIP_ITEM_COLS)}) "
            f"VALUES (?, {', '.join('?' for _ in _TRIP_ITEM_COLS)})",
            [(t.key, *i) for i in trip_items],
        )
        conn.executemany(
            f"INSERT INTO trip_ious (trip_key, {', '.join(_TRIP_IOU_COLS)}) "
            f"VALUES (?, {', '.join('?' for _ in _TRIP_IOU_COLS)})",
            [(t.key, *o) for o in ious],
        )


# --------------------------------------------------------------------------
# Replay
# --------------------------------------------------------------------------


def applied_runs(conn: sqlite3.Connection) -> list[str]:
    """Runs whose data is in the imported tables, in run order."""
    rows = conn.execute(
        "SELECT run_id FROM import_runs WHERE status IN ('imported', 'importing')"
    ).fetchall()
    return sorted((r["run_id"] for r in rows), key=run_order_key)


def clear_imported(conn: sqlite3.Connection) -> None:
    """Empty the imported tables. Overrides, users, settings and run_sections stay."""
    for table in IMPORTED_TABLES:
        conn.execute(f"DELETE FROM {table}")


def replay(conn: sqlite3.Connection, now: str) -> dict[str, ApplyStats]:
    """Rebuild the imported tables from run_sections, every run in run order."""
    clear_imported(conn)
    return {
        run_id: apply_run(conn, load_stored_run(conn, run_id), now) for run_id in applied_runs(conn)
    }
