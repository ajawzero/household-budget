"""Build every bad run from PLAN Phase 1 task 5 in a temp folder.

Each builder copies all good sample runs to `<tmp>/data/`, breaks one run
(the monthly run, or the correction run for the correction cases), and
returns that run's folder. Earlier runs stay next to it, so registry
checks see the same history as a real import folder.

    for case in BAD_CASES:
        run_dir = case.builder(tmp_path)
        ...expect case.expected_rule_id...

Cases in APP_ONLY_CASES pass check_run.py (it has no import history). Only the
app can reject them. For "run-changed-after-import", import the good sample run
with the same id first: `original_run(run_dir)`.

Cases in NEEDS_PRIVATE_RULES need PRIVATE_RULES_EXAMPLE passed as the private rules.
Nothing here is ever written to the repo.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import io
import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

REPO = Path(__file__).resolve().parents[2]
SAMPLES = REPO / "samples" / "data"
SCHEMA_FILE = REPO / "schema" / "import-v1.schema.json"
PRIVATE_RULES_EXAMPLE = REPO / "samples" / "private_rules.example.json"

MONTHLY = "2026-10-12"
CORRECTION = "2026-10-14"
CHECKING_CSV = "transactions/maple-joint-checking-2026-09-30.csv"
HARBOR_CSV = "transactions/harbor-checking-2026-09-30.csv"
TRACKED_CARD = "bramble-store-card"

# Values injected by the builders. None of them may appear in any output.
INJECTED_VALUES = (
    "INVOICE 12345",
    "1234-5678",
    "4111 1111 1111 1111",
    "4111/1111/1111/1111",
    "４１１１ １１１１ １１１１ １１１１",
    "(555)123-4567",
    "555-123-4567",
    "#-#####",
    "EXAMPLE PRIVATE SERVICE",
    "EXAMPLE EMPLOYER CO",
    "ESPP Shares",
    "check-car-123",
    "mystery-account",
    "Meter 12345678",
    "42 Elm Street",
    "QX7B2K",
)


class BadCase(NamedTuple):
    case_id: str
    builder: Callable[[Path], Path]
    expected_rule_id: str


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def copy_samples(tmp: Path) -> Path:
    data = tmp / "data"
    shutil.copytree(SAMPLES, data)
    return data


def original_run(run_dir: Path) -> Path:
    """The good sample run with the same id as a bad run."""
    return SAMPLES / run_dir.name


def _json_bytes(obj: object) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: object) -> None:
    path.write_bytes(_json_bytes(obj))


def refresh_manifest(run_dir: Path) -> None:
    """Recompute sha256, bytes and rows for every listed file."""
    mpath = run_dir / "manifest.json"
    manifest = read_json(mpath)
    for f in manifest["files"]:
        data = (run_dir / f["path"]).read_bytes()
        f["sha256"] = hashlib.sha256(data).hexdigest()
        f["bytes"] = len(data)
        if f["path"].endswith(".csv"):
            f["rows"] = len(list(csv.reader(io.StringIO(data.decode(), newline="")))) - 1
    write_json(mpath, manifest)


def edit_snapshot(run_dir: Path, fn: Callable[[dict], None]) -> None:
    path = run_dir / "snapshot.json"
    snap = read_json(path)
    fn(snap)
    write_json(path, snap)
    refresh_manifest(run_dir)


def _sort_key(row: dict) -> tuple:
    rest = tuple(v for k, v in row.items() if k not in ("txn_date", "amount", "description"))
    neg = row["amount"].startswith("-")
    whole, _, frac = row["amount"].lstrip("-").partition(".")
    cents = int(whole) * 100 + int(frac)
    return (row["txn_date"], -cents if neg else cents, row["description"], *rest)


def edit_csv(run_dir: Path, rel: str, fn: Callable[[list[dict]], None]) -> None:
    path = run_dir / rel
    reader = csv.DictReader(io.StringIO(path.read_text(encoding="utf-8"), newline=""))
    header = list(reader.fieldnames or [])
    rows = list(reader)
    fn(rows)
    rows.sort(key=_sort_key)
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=header, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    path.write_bytes(buf.getvalue().encode())
    refresh_manifest(run_dir)


def _monthly(tmp: Path) -> Path:
    return copy_samples(tmp) / MONTHLY


def _set_first_description(text: str, match: str = "GREEN LEAF MARKET") -> Callable:
    def fn(rows: list[dict]) -> None:
        next(r for r in rows if r["description"] == match)["description"] = text

    return fn


def _statement(snap: dict, account: str, end: str) -> dict:
    return next(
        s for s in snap["statements"] if s["account_key"] == account and s["period_end"] == end
    )


def _balance(snap: dict, account: str, end: str) -> dict:
    return next(
        b
        for b in snap["balances"]
        if b["account_key"] == account and b["as_of"] == end and b["source"] == "statement"
    )


def _add_cents(value: str, cents: int) -> str:
    neg = value.startswith("-")
    whole, _, frac = value.lstrip("-").partition(".")
    n = (int(whole) * 100 + int(frac)) * (-1 if neg else 1) + cents
    sign = "-" if n < 0 else ""
    return f"{sign}{abs(n) // 100}.{abs(n) % 100:02d}"


def _rename_run(src: Path, new_id: str, *, update_files: bool) -> Path:
    dst = src.parent / new_id
    src.rename(dst)
    if update_files:
        for name in ("snapshot.json", "manifest.json"):
            obj = read_json(dst / name)
            obj["run_id"] = new_id
            write_json(dst / name, obj)
        refresh_manifest(dst)
    return dst


# --------------------------------------------------------------------------
# Builders
# --------------------------------------------------------------------------


def missing_manifest(tmp: Path) -> Path:
    run = _monthly(tmp)
    (run / "manifest.json").unlink()
    return run


def hash_mismatch(tmp: Path) -> Path:
    run = _monthly(tmp)
    path = run / CHECKING_CSV
    path.write_bytes(path.read_bytes().replace(b"GREEN LEAF MARKET", b"GREEN LEAF MARKEX", 1))
    return run


def extra_file(tmp: Path) -> Path:
    run = _monthly(tmp)
    (run / "notes.txt").write_text("extra\n")
    return run


def dotdot_path(tmp: Path) -> Path:
    run = _monthly(tmp)
    manifest = read_json(run / "manifest.json")
    manifest["files"][1]["path"] = "transactions/../../escape.csv"
    write_json(run / "manifest.json", manifest)
    return run


def symlink(tmp: Path) -> Path:
    run = _monthly(tmp)
    target = run / CHECKING_CSV
    outside = tmp / "outside.csv"
    shutil.copyfile(target, outside)
    target.unlink()
    os.symlink(outside, target)
    return run


def future_run_id(tmp: Path) -> Path:
    return _rename_run(_monthly(tmp), "2099-01-01", update_files=True)


def run_id_not_folder(tmp: Path) -> Path:
    return _rename_run(_monthly(tmp), "2026-10-13", update_files=False)


def five_digit_run(tmp: Path) -> Path:
    run = _monthly(tmp)
    edit_csv(run, CHECKING_CSV, _set_first_description("INVOICE 12345"))
    return run


def split_number(tmp: Path) -> Path:
    run = _monthly(tmp)
    edit_csv(run, CHECKING_CSV, _set_first_description("TRANSFER ACCT 1234-5678"))
    return run


def spaced_luhn(tmp: Path) -> Path:
    run = _monthly(tmp)
    edit_csv(run, CHECKING_CSV, _set_first_description("CARD 4111 1111 1111 1111"))
    return run


def dashed_phone(tmp: Path) -> Path:
    run = _monthly(tmp)
    edit_csv(run, CHECKING_CSV, _set_first_description("CALL 555-123-4567"))
    return run


def luhn_slashes(tmp: Path) -> Path:
    run = _monthly(tmp)
    edit_csv(run, CHECKING_CSV, _set_first_description("CARD 4111/1111/1111/1111"))
    return run


def luhn_full_width(tmp: Path) -> Path:
    run = _monthly(tmp)
    edit_csv(run, CHECKING_CSV, _set_first_description("CARD ４１１１ １１１１ １１１１ １１１１"))
    return run


def phone_no_space(tmp: Path) -> Path:
    run = _monthly(tmp)
    edit_csv(run, CHECKING_CSV, _set_first_description("CALL (555)123-4567"))
    return run


def hash_dash_form(tmp: Path) -> Path:
    run = _monthly(tmp)
    edit_csv(run, CHECKING_CSV, _set_first_description("MEMBER #-#####"))
    return run


def bad_last4(tmp: Path) -> Path:
    run = _monthly(tmp)

    def fn(snap: dict) -> None:
        next(a for a in snap["accounts"] if a.get("last4"))["last4"] = "12345"

    edit_snapshot(run, fn)
    return run


def private_name(tmp: Path) -> Path:
    run = _monthly(tmp)

    def fn(rows: list[dict]) -> None:
        row = next(r for r in rows if r["description"] == "GREEN LEAF MARKET")
        row["description"] = "EXAMPLE PRIVATE SERVICE"
        row["merchant"] = "EXAMPLE PRIVATE SERVICE"

    edit_csv(run, CHECKING_CSV, fn)
    return run


def employer_term(tmp: Path) -> Path:
    run = _monthly(tmp)
    edit_csv(
        run,
        CHECKING_CSV,
        _set_first_description("EXAMPLE EMPLOYER CO PAYROLL", match="PAYROLL DEPOSIT"),
    )
    return run


def stock_plan_holding(tmp: Path) -> Path:
    run = _monthly(tmp)

    def fn(snap: dict) -> None:
        next(h for h in snap["holdings"] if h["key"] == "broad-market")["name"] = "ESPP Shares"

    edit_snapshot(run, fn)
    return run


def key_with_digits(tmp: Path) -> Path:
    run = _monthly(tmp)

    def fn(snap: dict) -> None:
        snap["todos"][0]["key"] = "check-car-123"

    edit_snapshot(run, fn)
    return run


def unbalanced_statement(tmp: Path) -> Path:
    run = _monthly(tmp)

    def fn(snap: dict) -> None:
        st = _statement(snap, "maple-joint-checking", "2026-09-30")
        st["closing"] = _add_cents(st["closing"], 100)
        bal = _balance(snap, "maple-joint-checking", "2026-09-30")
        bal["balance"] = st["closing"]

    edit_snapshot(run, fn)
    return run


def balance_not_closing(tmp: Path) -> Path:
    run = _monthly(tmp)

    def fn(snap: dict) -> None:
        bal = _balance(snap, "maple-joint-checking", "2026-09-30")
        bal["balance"] = _add_cents(bal["balance"], 100)

    edit_snapshot(run, fn)
    return run


def row_outside_window(tmp: Path) -> Path:
    run = _monthly(tmp)

    def fn(rows: list[dict]) -> None:
        next(r for r in rows if r["description"] == "GREEN LEAF MARKET")["txn_date"] = "2026-05-15"

    edit_csv(run, CHECKING_CSV, fn)
    return run


def refund_in_income(tmp: Path) -> Path:
    run = _monthly(tmp)

    def fn(rows: list[dict]) -> None:
        row = next(r for r in rows if r["description"] == "INTEREST PAID")
        row["txn_type"] = "refund"
        row["category_key"] = "paycheck"

    edit_csv(run, CHECKING_CSV, fn)
    return run


def unknown_account(tmp: Path) -> Path:
    run = _monthly(tmp)

    def fn(snap: dict) -> None:
        snap["balances"].append(
            {
                "account_key": "mystery-account",
                "as_of": "2026-09-30",
                "source": "manual",
                "balance": "10.00",
            }
        )
        snap["balances"].sort(key=lambda b: (b["account_key"], b["as_of"], b["source"]))

    edit_snapshot(run, fn)
    return run


def missing_section(tmp: Path) -> Path:
    run = _monthly(tmp)
    edit_snapshot(run, lambda snap: snap.pop("budgets"))
    return run


def _set_version(version: str) -> Callable[[Path], Path]:
    def build(tmp: Path) -> Path:
        run = _monthly(tmp)
        for name in ("snapshot.json", "manifest.json"):
            obj = read_json(run / name)
            obj["schema_version"] = version
            write_json(run / name, obj)
        refresh_manifest(run)
        return run

    return build


def correction_with_state(tmp: Path) -> Path:
    """A correction run that carries a state section (state is monthly only)."""
    run = copy_samples(tmp) / CORRECTION
    meta = read_json(run.parent / MONTHLY / "snapshot.json")["meta"]
    edit_snapshot(run, lambda snap: snap.update(meta=meta))
    return run


def _brokerage_correction(tmp: Path, *, balance: bool) -> Path:
    """A correction of the September brokerage statement that leaves out its holdings
    (and its balance row, unless `balance`). It must not wipe them."""
    data = copy_samples(tmp)
    run, monthly = data / CORRECTION, data / MONTHLY
    src = read_json(monthly / "snapshot.json")
    ident = ("lakeview-brokerage", "2026-09-30")
    stmt = next(s for s in src["statements"] if (s["account_key"], s["period_end"]) == ident)
    bal = [b for b in src["balances"] if (b["account_key"], b["as_of"]) == ident]

    def fn(snap: dict) -> None:
        snap["statements"] = [stmt]
        snap["balances"] = bal if balance else []

    for old in (run / "transactions").iterdir():
        old.unlink()
    rel = "transactions/lakeview-brokerage-2026-09-30.csv"
    shutil.copyfile(monthly / rel, run / rel)
    manifest = read_json(run / "manifest.json")
    manifest["files"] = [f for f in manifest["files"] if f["path"] == "snapshot.json"]
    manifest["files"].append({"path": rel, "sha256": "0" * 64, "bytes": 0, "rows": 0})
    write_json(run / "manifest.json", manifest)
    edit_snapshot(run, fn)
    return run


def correction_without_balance(tmp: Path) -> Path:
    return _brokerage_correction(tmp, balance=False)


def correction_without_holdings(tmp: Path) -> Path:
    return _brokerage_correction(tmp, balance=True)


def tracked_payment_not_spending(tmp: Path) -> Path:
    """A payment to a card tracked by payments with is_transfer false and a transfer category."""
    run = _monthly(tmp)

    def fn(rows: list[dict]) -> None:
        row = next(r for r in rows if r["counterparty_account_key"] == TRACKED_CARD)
        row["is_transfer"] = "false"
        row["category_key"] = "transfer"

    edit_csv(run, HARBOR_CSV, fn)
    return run


def tracked_statement_balance(tmp: Path) -> Path:
    run = _monthly(tmp)

    def fn(snap: dict) -> None:
        st = next(s for s in snap["statements"] if s["account_key"] == TRACKED_CARD)
        snap["balances"].append(
            {
                "account_key": TRACKED_CARD,
                "as_of": st["period_end"],
                "source": "statement",
                "balance": st["closing"],
            }
        )
        snap["balances"].sort(key=lambda b: (b["account_key"], b["as_of"], b["source"]))

    edit_snapshot(run, fn)
    return run


def tracked_in_net_worth(tmp: Path) -> Path:
    run = _monthly(tmp)

    def fn(snap: dict) -> None:
        acct = next(a for a in snap["accounts"] if a["key"] == TRACKED_CARD)
        acct.update(include_in_net_worth=True, nw_group="credit-cards")

    edit_snapshot(run, fn)
    return run


# ---- format 1.1 state sections ----


def _state_item(section: str, key: str, fn: Callable[[dict], None]) -> Callable[[Path], Path]:
    """Edit one item (by key) of a 1.1 state section in the monthly run."""

    def build(tmp: Path) -> Path:
        run = _monthly(tmp)
        edit_snapshot(run, lambda snap: fn(next(i for i in snap[section] if i["key"] == key)))
        return run

    return build


def _drop(*fields: str) -> Callable[[dict], None]:
    def fn(item: dict) -> None:
        for f in fields:
            item.pop(f)

    return fn


def _set(**values: object) -> Callable[[dict], None]:
    return lambda item: item.update(values)


def _older_form(form: str, version: str) -> Callable[[Path], Path]:
    """The monthly run stripped to `form` (strip_newer), then labeled `version`, so only
    the sections newer than `version` and no older than `form` are out of place."""

    def build(tmp: Path) -> Path:
        run = _set_version(version)(tmp)
        edit_snapshot(run, lambda snap: strip_newer(snap, form))
        return run

    return build


def v11_sections_in_v10_run(tmp: Path) -> Path:
    """The monthly run says 1.0 but carries the 1.1 sections."""
    return _older_form("1.1", "1.0")(tmp)


def missing_flags(tmp: Path) -> Path:
    run = _monthly(tmp)
    edit_snapshot(run, lambda snap: snap.pop("flags"))
    return run


def correction_with_flags(tmp: Path) -> Path:
    run = copy_samples(tmp) / CORRECTION
    flags = read_json(run.parent / MONTHLY / "snapshot.json")["flags"]
    edit_snapshot(run, lambda snap: snap.update(flags=flags))
    return run


def _component_sum(item: dict) -> None:
    item["components"][0]["amount"] = "299.00"


# ---- format 1.2 utility bills (the monthly run carries September's bills) ----

POWER_BILL = ("city-power", "2026-09-03")
WATER_BILL = ("town-water", "2026-09-01")


def _bill(ident: tuple[str, str], fn: Callable[[dict], None]) -> Callable[[Path], Path]:
    """Edit one utility bill (by utility_key and period_end) in the monthly run."""

    def build(tmp: Path) -> Path:
        run = _monthly(tmp)

        def edit(snap: dict) -> None:
            bills = snap["utility_bills"]
            fn(next(b for b in bills if (b["utility_key"], b["period_end"]) == ident))

        edit_snapshot(run, edit)
        return run

    return build


def _line(service: str, **values: object) -> Callable[[dict], None]:
    return lambda bill: next(ln for ln in bill["lines"] if ln["service"] == service).update(values)


def _add_total(cents: int) -> Callable[[dict], None]:
    return lambda bill: bill.update(total=_add_cents(bill["total"], cents))


def _duplicate_line(bill: dict) -> None:
    """A second electric line with no charges, so the total still adds up."""
    extra = {"service": "electric", "usage": "0", "unit": "kwh", "charges": "0.00"}
    bill["lines"].insert(0, extra)


def _swap_lines(bill: dict) -> None:
    bill["lines"].reverse()


def _positive_credit(bill: dict) -> None:
    bill["credits"][0]["amount"] = "5.00"


def v12_section_in_v11_run(tmp: Path) -> Path:
    """The monthly run says 1.1 but carries utility_bills (a 1.2 section)."""
    return _older_form("1.2", "1.1")(tmp)


# ---- format 1.3 rewards (monthly state) and trips (history) ----

BACKFILL_B = "2026-10-06-2"


def _rewards(fn: Callable[[dict], None]) -> Callable[[Path], Path]:
    """Edit the rewards section of the monthly run."""

    def build(tmp: Path) -> Path:
        run = _monthly(tmp)
        edit_snapshot(run, lambda snap: fn(snap["rewards"]))
        return run

    return build


def _reward_item(name: str, key: str, fn: Callable[[dict], None]) -> Callable[[Path], Path]:
    return _rewards(lambda r: fn(next(i for i in r[name] if i["key"] == key)))


def _trip(key: str, fn: Callable[[dict], None], run_id: str = MONTHLY) -> Callable[[Path], Path]:
    """Edit one trip (by key) in a sample run."""

    def build(tmp: Path) -> Path:
        run = copy_samples(tmp) / run_id
        edit_snapshot(run, lambda snap: fn(next(t for t in snap["trips"] if t["key"] == key)))
        return run

    return build


def _trip_item(key: str, item: str, fn: Callable[[dict], None]) -> Callable[[Path], Path]:
    return _trip(key, lambda t: fn(next(i for i in t["items"] if i["key"] == item)))


def _swap_first_two(name: str) -> Callable[[dict], None]:
    def fn(parent: dict) -> None:
        parent[name][0], parent[name][1] = parent[name][1], parent[name][0]

    return fn


def _repeat_first(name: str) -> Callable[[dict], None]:
    return lambda parent: parent[name].insert(1, dict(parent[name][0]))


def rewards_in_v12_run(tmp: Path) -> Path:
    """The monthly run says 1.2 but carries rewards (its trips are dropped)."""
    run = _older_form("1.3", "1.2")(tmp)
    edit_snapshot(run, lambda snap: snap.pop("trips"))
    return run


EXTRA_RUN = "2026-10-15"  # after every sample run, so earlier imports never change


def extra_backfill(tmp: Path, version: str, **sections: object) -> Path:
    """A new small backfill run after the samples, carrying `sections`."""
    data = copy_samples(tmp)
    run = data / EXTRA_RUN
    run.mkdir()
    snap: dict = {"schema_version": version, "run_id": EXTRA_RUN, "mode": "backfill"}
    snap |= {
        "people": [],
        "accounts": [],
        "statements": [],
        "balances": [],
        "holdings": [],
        "income": {"paychecks": []},
    }
    write_json(run / "snapshot.json", snap | sections)
    manifest = {
        "schema_version": version,
        "run_id": EXTRA_RUN,
        "mode": "backfill",
        "created_at": "2026-10-15T18:00:00-05:00",
        "generator": {"skill": "make_samples", "version": "1"},
        "files": [{"path": "snapshot.json", "sha256": "0" * 64, "bytes": 0}],
    }
    write_json(run / "manifest.json", manifest)
    refresh_manifest(run)
    return run


def _sample_section(run_id: str, name: str) -> object:
    return read_json(SAMPLES / run_id / "snapshot.json")[name]


def trips_in_v12_run(tmp: Path) -> Path:
    """A backfill run says 1.2 but carries trips."""
    return extra_backfill(tmp, "1.2", trips=_sample_section(BACKFILL_B, "trips"))


def rewards_in_backfill(tmp: Path) -> Path:
    return extra_backfill(tmp, "1.3", rewards=_sample_section(MONTHLY, "rewards"))


def monthly_without_rewards(tmp: Path) -> Path:
    run = _monthly(tmp)
    edit_snapshot(run, lambda snap: snap.pop("rewards"))
    return run


def _over_allocated(program: dict) -> None:
    program["allocations"][0]["used"] = program["allocations"][0]["allocated"] + ".5"


# ---- format 1.4: the section, field and value gate, and goals (monthly state) ----


def _version(text: str) -> tuple[int, int]:
    major, minor = text.split(".")
    return int(major), int(minor)


def since_objects(snap: dict, path: str) -> list[tuple[list | None, dict, str]]:
    """(holding list, object, location) for each object a since path ends in, on the
    raw JSON (the walk of check_run.since_nodes). Tombstones drop out."""
    nodes: list[tuple[list | None, dict, str]] = [(None, snap, "snapshot.json:")]
    for part in path.split(".")[:-1]:
        found: list[tuple[list | None, dict, str]] = []
        for _, obj, loc in nodes:
            value = obj.get(part)
            here = loc + part if loc.endswith(":") else f"{loc}.{part}"
            if isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, dict):
                        found.append((value, item, f"{here}[{i}]"))
            elif isinstance(value, dict):
                found.append((None, value, here))
        nodes = found
    return nodes


# Whole items the 1.4 monthly sample adds that carry no 1.4 field or value of their own
# (make_samples.format_v14), by since path, then key. Fields and values go by the schema.
# Their keys sort after the 1.3 items the 1.4 cases edit (gym-ending, plan-sofa-ends,
# ski-flights), so those keep their index in a 1.4 run.
V14_SAMPLE_ITEMS: dict[str, frozenset[str]] = {
    "cashflow_events": frozenset({"switch-phone-plan", "trim-dining"}),
    "flags": frozenset(
        {
            "health-claim",
            "home-insurance-renewal",
            "kids-college-gift",
            "library-fees",
            "old-plan-rollover",
            "roof-repair",
            "withholding-check",
        }
    ),
    "rewards.redemptions": frozenset({"spring-stay"}),
    "trips.items": frozenset({"snorkel-tour"}),
}


def strip_newer(snap: dict, version: str) -> None:
    """Make a sample snapshot a valid run of `version`: drop the newer sections and
    fields, every list item that carries a newer value, and the whole items the 1.4
    sample adds (V14_SAMPLE_ITEMS; format-14.md section 8). Driven by x-section-since,
    x-field-since and x-value-since in the generated schema. The 1.4 monthly sample
    stripped to 1.3 is the 1.3 monthly sample byte for byte (test_state_v14)."""
    schema = read_json(SCHEMA_FILE)
    old = _version(version)
    if old < (1, 4):
        for path, keys in V14_SAMPLE_ITEMS.items():
            for holder, obj, _ in since_objects(snap, f"{path}.key"):
                if obj.get("key") in keys and holder is not None:
                    holder[:] = [x for x in holder if x is not obj]
    for name, since in schema["x-section-since"].items():
        if old < _version(since):
            snap.pop(name, None)
    for path, since in schema["x-field-since"].items():
        if old < _version(since):
            name = path.rsplit(".", 1)[1]
            for _, obj, _ in since_objects(snap, path):
                obj.pop(name, None)
    for path, values in schema["x-value-since"].items():
        name = path.rsplit(".", 1)[1]
        for holder, obj, _ in since_objects(snap, path):
            value = obj.get(name)
            since = values.get(value) if isinstance(value, str) else None
            if since and old < _version(since) and holder is not None:
                holder[:] = [x for x in holder if x is not obj]


def _v13(fn: Callable[[dict], None]) -> Callable[[Path], Path]:
    """The monthly run as a valid 1.3 run (strip_newer), then edited by fn."""

    def build(tmp: Path) -> Path:
        run = _set_version("1.3")(tmp)

        def edit(snap: dict) -> None:
            strip_newer(snap, "1.3")
            fn(snap)

        edit_snapshot(run, edit)
        return run

    return build


def downgrade(run_dir: Path, version: str) -> Path:
    """Rewrite a copied sample run as a run of `version` (strip_newer), manifest too."""
    for name in ("snapshot.json", "manifest.json"):
        obj = read_json(run_dir / name)
        obj["schema_version"] = version
        if name == "snapshot.json":
            strip_newer(obj, version)
        write_json(run_dir / name, obj)
    refresh_manifest(run_dir)
    return run_dir


def v13_samples(tmp: Path) -> Path:
    """Every sample run copied to <tmp>/data, the monthly run in its 1.3 form."""
    data = copy_samples(tmp)
    downgrade(data / MONTHLY, "1.3")
    return data


def _first(
    path: str, fn: Callable[[dict], None], pick: Callable[[dict], bool] | None = None
) -> Callable[[dict], None]:
    """fn on the first object of a since path that pick accepts."""

    def edit(snap: dict) -> None:
        fn(next(o for _, o, _ in since_objects(snap, path) if pick is None or pick(o)))

    return edit


# The monthly sample's goals: college-jamie (jamie is a child, kids-529 a 529 account)
# and emergency-fund (save_up on harbor-savings).
SAMPLE_GOALS: list[dict] = read_json(SAMPLES / MONTHLY / "snapshot.json")["goals"]


def goals_run(fn: Callable[[list[dict]], None] | None = None) -> Callable[[Path], Path]:
    """The monthly run (1.4) with SAMPLE_GOALS, edited by fn."""

    def build(tmp: Path) -> Path:
        run = _set_version("1.4")(tmp)

        def edit(snap: dict) -> None:
            snap["goals"] = copy.deepcopy(SAMPLE_GOALS)
            if fn is not None:
                fn(snap["goals"])

        edit_snapshot(run, edit)
        return run

    return build


def _goal(key: str, fn: Callable[[dict], None]) -> Callable[[Path], Path]:
    return goals_run(lambda goals: fn(next(g for g in goals if g["key"] == key)))


def goals_in_v13_run(tmp: Path) -> Path:
    return _v13(lambda snap: snap.update(goals=copy.deepcopy(SAMPLE_GOALS)))(tmp)


def goals_null_in_v13_run(tmp: Path) -> Path:
    """A section written as null counts as sent (format-14.md section 8)."""
    return _v13(lambda snap: snap.update(goals=None))(tmp)


def goals_in_backfill(tmp: Path) -> Path:
    return extra_backfill(tmp, "1.4", goals=[copy.deepcopy(SAMPLE_GOALS[1])])


# ---- format 1.4: state fields on flags, recurring, plans, cash flow events, redemptions ----


def v14_run(fn: Callable[[dict], None] | None = None) -> Callable[[Path], Path]:
    """The monthly run as a 1.4 run (the sample is one), edited by fn."""

    def build(tmp: Path) -> Path:
        run = _set_version("1.4")(tmp)
        if fn is not None:
            edit_snapshot(run, fn)
        return run

    return build


def _v14_item(section: str, key: str, fn: Callable[[dict], None]) -> Callable[[Path], Path]:
    """Edit one item (by key) of a state section in the monthly run as a 1.4 run."""
    return v14_run(lambda snap: fn(next(i for i in snap[section] if i["key"] == key)))


def _v14_redemption(key: str, fn: Callable[[dict], None]) -> Callable[[Path], Path]:
    def edit(snap: dict) -> None:
        fn(next(x for x in snap["rewards"]["redemptions"] if x["key"] == key))

    return v14_run(edit)


def v14_in_v13(path: str, key: str | None = None, **values: object) -> BadCase:
    """BadCase `v14-<path>-in-v13-run`: a valid 1.3 run whose first object on a since
    path (or the one with this key) carries 1.4 values. Rejected with section-version
    at that field. Only the field on the path is new: other values set here are 1.3."""
    pick = None if key is None else (lambda obj: obj.get("key") == key)
    case_id = "v14-" + path.replace(".", "-").replace("_", "-") + "-in-v13-run"
    return BadCase(case_id, _v13(_first(path, _set(**values), pick)), "section-version")


def _updates(*pairs: tuple[str, str]) -> Callable[[dict], None]:
    return _set(updates=[{"on": on, "text": text} for on, text in pairs])


# gym-ending is created 2026-10-01; the monthly run's meta.updated is 2026-10-12.
_GYM_FLAG = ("flags", "gym-ending")


# ---- format 1.4: account debt terms (registry) and trip item due_on and maybe ----


def _v14_trip_item(trip: str, key: str, fn: Callable[[dict], None]) -> Callable[[Path], Path]:
    """Edit one item of a trip (by keys) in the monthly run as a 1.4 run."""

    def edit(snap: dict) -> None:
        items = next(t for t in snap["trips"] if t["key"] == trip)["items"]
        fn(next(i for i in items if i["key"] == key))

    return v14_run(edit)


def _rate_changes(*days: str) -> Callable[[dict], None]:
    return _set(rate_changes=[{"apr": "22.99", "effective_on": day} for day in days])


def _and(*fns: Callable[[dict], None]) -> Callable[[dict], None]:
    def fn(item: dict) -> None:
        for f in fns:
            f(item)

    return fn


def run_changed_after_import(tmp: Path) -> Path:
    """Same run id as a good sample, valid on its own, but different bytes."""
    run = _monthly(tmp)
    edit_csv(run, CHECKING_CSV, _set_first_description("GREEN LEAF GROCER"))
    return run


BAD_CASES: list[BadCase] = [
    BadCase("missing-manifest", missing_manifest, "manifest-missing"),
    BadCase("hash-mismatch", hash_mismatch, "hash-mismatch"),
    BadCase("extra-file", extra_file, "file-unlisted"),
    BadCase("dotdot-path", dotdot_path, "path-not-allowed"),
    BadCase("symlink", symlink, "symlink"),
    BadCase("future-run-id", future_run_id, "run-id-future"),
    BadCase("run-id-not-folder", run_id_not_folder, "run-id-mismatch"),
    BadCase("five-digit-run", five_digit_run, "digits-run"),
    BadCase("split-number", split_number, "split-account"),
    BadCase("spaced-luhn-number", spaced_luhn, "luhn"),
    BadCase("dashed-phone", dashed_phone, "phone"),
    BadCase("luhn-slashes", luhn_slashes, "luhn"),
    BadCase("luhn-full-width", luhn_full_width, "luhn"),
    BadCase("phone-no-space", phone_no_space, "phone"),
    BadCase("hash-dash-form", hash_dash_form, "masked-number"),
    BadCase("bad-last4", bad_last4, "last4-format"),
    BadCase("private-list-name", private_name, "private-subscription"),
    BadCase("employer-term", employer_term, "private-employer"),
    BadCase("stock-plan-holding", stock_plan_holding, "stock-plan"),
    BadCase("key-with-digits", key_with_digits, "key-format"),
    BadCase("unbalanced-statement", unbalanced_statement, "statement-unbalanced"),
    BadCase("balance-not-closing", balance_not_closing, "balance-closing"),
    BadCase("row-outside-window", row_outside_window, "date-window"),
    BadCase("refund-in-income", refund_in_income, "refund-income"),
    BadCase("unknown-account", unknown_account, "account-unknown"),
    BadCase("missing-section", missing_section, "section-missing"),
    BadCase("correction-with-state", correction_with_state, "section-not-allowed"),
    BadCase("correction-without-balance", correction_without_balance, "statement-balance-missing"),
    BadCase("correction-without-holdings", correction_without_holdings, "holdings-missing"),
    BadCase("tracked-payment-not-spending", tracked_payment_not_spending, "tracked-payment"),
    BadCase("tracked-statement-balance", tracked_statement_balance, "tracked-balance"),
    BadCase("tracked-in-net-worth", tracked_in_net_worth, "tracked-net-worth"),
    BadCase("schema-major-2", _set_version("2.0"), "version-major"),
    BadCase("unknown-minor", _set_version("1.5"), "version-minor"),
    BadCase("run-changed-after-import", run_changed_after_import, "run-changed"),
    # format 1.1
    BadCase("v11-sections-in-v10-run", v11_sections_in_v10_run, "section-version"),
    BadCase("missing-flags", missing_flags, "section-missing"),
    BadCase("correction-with-flags", correction_with_flags, "section-not-allowed"),
    BadCase(
        "recurring-negative-amount",
        _state_item("recurring", "gym", _set(amount="-45.00")),
        "money-sign",
    ),
    BadCase(
        "plan-negative-remaining",
        _state_item("installment_plans", "device-plan", _set(remaining="-300.00")),
        "money-sign",
    ),
    BadCase(
        "recurring-interval-missing",
        _state_item("recurring", "house-cleaning", _drop("interval_n")),
        "recurring-interval",
    ),
    BadCase(
        "recurring-interval-not-allowed",
        _state_item("recurring", "gym", _set(interval_n=2)),
        "recurring-interval",
    ),
    BadCase(
        "active-months-unsorted",
        _state_item("recurring", "youth-soccer", _set(active_months=[8, 3])),
        "active-months",
    ),
    BadCase(
        "active-month-13",
        _state_item("recurring", "lawn-care", _set(active_months=[4, 13])),
        "schema",
    ),
    BadCase(
        "recurring-bad-cadence",
        _state_item("recurring", "gym", _set(cadence="fortnightly")),
        "schema",
    ),
    BadCase(
        "recurring-end-before-start",
        _state_item("recurring", "pest-control", _set(end_date="2025-01-01")),
        "date-order",
    ),
    BadCase(
        "recurring-unknown-category",
        _state_item("recurring", "gym", _set(category_key="gym-fees")),
        "category-unknown",
    ),
    BadCase(
        "recurring-unknown-account",
        _state_item("recurring", "gym", _set(preferred_account_key="mystery-account")),
        "account-unknown",
    ),
    BadCase(
        "recurring-unknown-owner",
        _state_item("recurring", "gym", _set(owner="casey")),
        "person-unknown",
    ),
    BadCase(
        "recurring-private-name",
        _state_item("recurring", "personal-subscription", _set(name="EXAMPLE PRIVATE SERVICE")),
        "private-subscription",
    ),
    BadCase(
        "promo-without-expiry",
        _state_item("installment_plans", "promo-mattress", _drop("promo_expiry")),
        "plan-dates",
    ),
    BadCase(
        "deferred-without-pay-before",
        _state_item("installment_plans", "bramble-deferred", _drop("pay_before")),
        "plan-dates",
    ),
    BadCase(
        "plan-payments-left",
        _state_item("installment_plans", "device-plan", _set(payments_left=13)),
        "plan-payments",
    ),
    BadCase(
        "plan-components-sum",
        _state_item("installment_plans", "plan-sofa", _component_sum),
        "plan-components",
    ),
    BadCase(
        "plan-unknown-account",
        _state_item("installment_plans", "plan-sofa", _set(account_key="mystery-account")),
        "account-unknown",
    ),
    BadCase(
        "plan-money-as-number",
        _state_item("installment_plans", "plan-sofa", _set(original=600)),
        "schema",
    ),
    BadCase(
        "flag-resolved-without-date",
        _state_item("flags", "gym-ending", _set(status="resolved")),
        "flag-resolved",
    ),
    BadCase(
        "flag-related-half",
        _state_item("flags", "gym-ending", _drop("related_key")),
        "flag-related",
    ),
    BadCase(
        "flag-bad-severity",
        _state_item("flags", "gym-ending", _set(severity="urgent")),
        "schema",
    ),
    BadCase(
        "flag-resolved-before-created",
        _state_item("flags", "hsa-claim-paid", _set(resolved_on="2026-01-02")),
        "date-order",
    ),
    BadCase(
        "flag-unknown-account",
        _state_item("flags", "summit-fee-review", _set(related_key="mystery-account")),
        "account-unknown",
    ),
    BadCase(
        "flag-phone-in-body",
        _state_item("flags", "gym-ending", _set(body="Call them at 555-123-4567")),
        "phone",
    ),
    BadCase(
        "pending-updated-before-added",
        _state_item("pending", "apply-gym-cancel", _set(updated_on="2026-09-01")),
        "date-order",
    ),
    BadCase(
        "pending-unknown-field",
        _state_item("pending", "apply-gym-cancel", _set(owner="alex")),
        "schema",
    ),
]

BAD_CASES += [
    BadCase("v12-section-in-v11-run", v12_section_in_v11_run, "section-version"),
    BadCase("utility-total-off", _bill(POWER_BILL, _add_total(100)), "utility-total"),
    BadCase("utility-total-off-3-cents", _bill(POWER_BILL, _add_total(3)), "utility-total"),
    BadCase(
        "utility-period-empty",
        _bill(POWER_BILL, lambda b: b.update(period_start=b["period_end"])),
        "utility-period",
    ),
    BadCase(
        "utility-date-too-early",
        _bill(WATER_BILL, _set(period_start="1999-12-01")),
        "utility-date",
    ),
    BadCase("utility-gas-in-kwh", _bill(POWER_BILL, _line("gas", unit="kwh")), "utility-unit"),
    BadCase("utility-water-none", _bill(WATER_BILL, _line("water", unit="none")), "utility-unit"),
    BadCase("utility-duplicate-service", _bill(POWER_BILL, _duplicate_line), "key-duplicate"),
    BadCase("utility-lines-unsorted", _bill(WATER_BILL, _swap_lines), "sort-order"),
    BadCase("utility-credit-positive", _bill(POWER_BILL, _positive_credit), "utility-credit-sign"),
    BadCase(
        "utility-fixed-over-charges",
        _bill(POWER_BILL, _line("electric", fixed_charges="999.00")),
        "utility-fixed",
    ),
    BadCase(
        "utility-negative-usage", _bill(POWER_BILL, _line("electric", usage="-5")), "money-sign"
    ),
    BadCase("utility-unknown-unit", _bill(POWER_BILL, _line("gas", unit="ccm")), "schema"),
    BadCase("utility-days-as-text", _bill(POWER_BILL, _set(days="30")), "schema"),
    BadCase("utility-no-lines", _bill(WATER_BILL, _set(lines=[])), "schema"),
    BadCase(
        "utility-unknown-paid-from",
        _bill(POWER_BILL, _set(paid_from="mystery-account")),
        "account-unknown",
    ),
    BadCase(
        "utility-meter-number",
        _bill(POWER_BILL, _set(note="Meter 12345678 was read")),
        "digits-run",
    ),
    BadCase(
        "utility-service-address",
        _bill(WATER_BILL, _set(note="Service at 42 Elm Street")),
        "street-address",
    ),
]

BAD_CASES += [
    BadCase("v13-rewards-in-v12-run", rewards_in_v12_run, "section-version"),
    BadCase("v13-trips-in-v12-run", trips_in_v12_run, "section-version"),
    BadCase("rewards-in-backfill", rewards_in_backfill, "section-not-allowed"),
    BadCase("monthly-13-without-rewards", monthly_without_rewards, "section-missing"),
    BadCase(
        "reward-balance-without-as-of",
        _reward_item("programs", "summit-points", _drop("as_of")),
        "reward-as-of",
    ),
    BadCase(
        "reward-cpp-reversed",
        _reward_item("programs", "summit-points", _set(cpp_low="2.5")),
        "reward-value-range",
    ),
    BadCase(
        "reward-cash-back-cpp",
        _reward_item("programs", "pine-cash-back", _set(cpp_low="1", cpp_high="1")),
        "reward-value-range",
    ),
    BadCase(
        "reward-credit-without-cadence",
        _reward_item("benefits", "dining-credit", _drop("cadence")),
        "reward-benefit-fields",
    ),
    BadCase(
        "reward-cap-field-on-credit",
        _reward_item("benefits", "dining-credit", _set(cap="100.00")),
        "reward-benefit-fields",
    ),
    BadCase(
        "reward-unknown-program",
        _reward_item("redemptions", "ski-flights", _set(program_key="no-such-program")),
        "reward-program-unknown",
    ),
    BadCase(
        "reward-perk-unknown-program",
        _reward_item("benefits", "hotel-status", _set(program_key="no-such-program")),
        "reward-program-unknown",
    ),
    BadCase(
        "reward-over-allocated",
        _reward_item("programs", "shared-vacation-club", _over_allocated),
        "reward-allocation",
    ),
    BadCase(
        "reward-allocations-on-points",
        _reward_item("programs", "summit-points", _set(allocations=[])),
        "reward-allocation",
    ),
    BadCase("reward-programs-unsorted", _rewards(_swap_first_two("programs")), "sort-order"),
    BadCase("reward-routing-duplicate", _rewards(_repeat_first("routing")), "key-duplicate"),
    BadCase(
        "reward-cycles-unsorted",
        _reward_item("benefits", "dining-credit", _swap_first_two("cycles")),
        "sort-order",
    ),
    BadCase(
        "reward-unknown-card",
        _reward_item("benefits", "dining-credit", _set(account_key="mystery-account")),
        "account-unknown",
    ),
    BadCase(
        "reward-unknown-owner",
        _reward_item("programs", "harbor-miles", _set(owner="casey")),
        "person-unknown",
    ),
    BadCase(
        "reward-cap-unknown-category",
        _reward_item("benefits", "fuel-bonus", _set(category_keys=["gas-stations"])),
        "category-unknown",
    ),
    BadCase(
        "reward-highlight-phone",
        _rewards(lambda r: r["highlights"].append("Call 555-123-4567 to enroll")),
        "phone",
    ),
    BadCase("trip-month-and-dates", _trip("ski-weekend", _set(month="2027-01")), "trip-dates"),
    BadCase(
        "trip-before-2000",
        _trip("beach-week", _set(month="1999-06")),
        "trip-dates",
    ),
    BadCase(
        "trip-end-before-start",
        _trip("ski-weekend", _set(end_date="2027-01-01")),
        "date-order",
    ),
    BadCase(
        "trip-estimate-reversed",
        _trip("ski-weekend", _set(estimate_low="9000.00")),
        "trip-estimate",
    ),
    BadCase(
        "trip-posted-without-amount",
        _trip_item("ski-weekend", "rental-car", _drop("amount")),
        "trip-item-amount",
    ),
    BadCase(
        "trip-points-without-points",
        _trip_item("ski-weekend", "flights", _drop("points")),
        "trip-tender",
    ),
    BadCase(
        "trip-iou-settled-without-date",
        _trip("ski-weekend", lambda t: t["ious"][0].update(status="settled")),
        "trip-iou-settled",
    ),
    BadCase(
        "trip-iou-unknown-item",
        _trip("ski-weekend", lambda t: t["ious"][0].update(item_key="no-such-item")),
        "trip-item-unknown",
    ),
    BadCase("trip-items-unsorted", _trip("ski-weekend", _swap_first_two("items")), "sort-order"),
    BadCase(
        "trip-unknown-person",
        _trip("ski-weekend", _set(people=["alex", "casey"])),
        "person-unknown",
    ),
    BadCase(
        "trip-unknown-card",
        _trip_item("ski-weekend", "rental-car", _set(account_key="mystery-account")),
        "account-unknown",
    ),
    BadCase(
        "trip-booking-code",
        _trip("ski-weekend", _set(note="Record locator QX7B2K")),
        "booking-code",
    ),
    BadCase(
        "trip-item-booking-code",
        _trip_item("ski-weekend", "flights", _set(note="Flight UA1234 out")),
        "booking-code",
    ),
    # an unknown field named like a booking code: the location must hide it
    BadCase("trip-booking-code-field-name", _trip("ski-weekend", _set(QX7B2K="x")), "schema"),
    BadCase(
        "reward-booking-code-field-name",
        _reward_item("programs", "summit-points", _set(QX7B2K="x")),
        "schema",
    ),
    BadCase(
        "trip-guest-name-address",
        _trip("ski-weekend", _set(note="Meet at 42 Elm Street")),
        "street-address",
    ),
]

BAD_CASES += [
    BadCase("v14-goals-in-v13-run", goals_in_v13_run, "section-version"),
    BadCase("v14-goals-null-in-v13-run", goals_null_in_v13_run, "section-version"),
    BadCase("goals-in-backfill", goals_in_backfill, "section-not-allowed"),
    BadCase("goal-college-without-person", _goal("college-jamie", _drop("person")), "goal-fields"),
    BadCase(
        "goal-college-without-start-year",
        _goal("college-jamie", _drop("start_year")),
        "goal-fields",
    ),
    BadCase(
        "goal-target-date-on-college",
        _goal("college-jamie", _set(target_date="2034-08-15")),
        "goal-fields",
    ),
    BadCase(
        "goal-start-year-on-save-up",
        _goal("emergency-fund", _set(start_year=2030)),
        "goal-fields",
    ),
    BadCase("goal-college-adult", _goal("college-jamie", _set(person="alex")), "goal-fields"),
    BadCase(
        "goal-college-brokerage",
        _goal("college-jamie", _set(accounts=["kids-529", "lakeview-brokerage"])),
        "goal-fields",
    ),
    BadCase(
        "goal-accounts-unsorted",
        _goal("emergency-fund", _set(accounts=["lakeview-brokerage", "harbor-savings"])),
        "sort-order",
    ),
    BadCase(
        "goal-accounts-duplicate",
        _goal("emergency-fund", _set(accounts=["harbor-savings", "harbor-savings"])),
        "key-duplicate",
    ),
    BadCase("goal-no-accounts", _goal("emergency-fund", _set(accounts=[])), "schema"),
    BadCase("goal-start-year-as-text", _goal("college-jamie", _set(start_year="2034")), "schema"),
    BadCase("goal-bad-kind", _goal("emergency-fund", _set(kind="pay_down")), "schema"),
    BadCase("goal-negative-target", _goal("emergency-fund", _set(target="-5.00")), "money-sign"),
    BadCase(
        "goal-unknown-account",
        _goal("emergency-fund", _set(accounts=["mystery-account"])),
        "account-unknown",
    ),
    BadCase("goal-unknown-person", _goal("emergency-fund", _set(person="casey")), "person-unknown"),
    BadCase(
        "goal-note-phone",
        _goal("emergency-fund", _set(note="Call 555-123-4567")),
        "phone",
    ),
    BadCase("goals-unsorted", goals_run(lambda goals: goals.reverse()), "sort-order"),
]

# format 1.4 state fields (the 1.4 monthly sample, one value changed)
BAD_CASES += [
    v14_in_v13("flags.kind", kind="retirement"),
    v14_in_v13("flags.action", action="ask"),
    BadCase(  # an explicit null counts as sent (format-14.md section 8)
        "v14-flags-action-null-in-v13-run",
        _v13(_first("flags.action", _set(action=None))),
        "section-version",
    ),
    v14_in_v13("flags.updates", updates=[{"on": "2026-09-20", "text": "Called the bank"}]),
    v14_in_v13("recurring.keep", keep=True),
    v14_in_v13("recurring.tag", tag="streaming"),
    v14_in_v13("installment_plans.note", note="Pay it off early"),
    v14_in_v13("installment_plans.paid_off_on", "towne-coats", paid_off_on="2026-06-30"),
    v14_in_v13("cashflow_events.status", status="confirmed"),
    v14_in_v13("rewards.redemptions.done_on", status="done", done_on="2026-10-01"),
    BadCase("flag-kind-reimbursement", _v14_item(*_GYM_FLAG, _set(kind="reimbursement")), "schema"),
    BadCase("flag-bad-action", _v14_item(*_GYM_FLAG, _set(action="later")), "schema"),
    BadCase("flag-updates-empty", _v14_item(*_GYM_FLAG, _set(updates=[])), "schema"),
    BadCase(
        "flag-too-many-updates",
        _v14_item(
            *_GYM_FLAG, _updates(*[("2026-10-03", f"Update {chr(97 + n)}") for n in range(21)])
        ),
        "schema",
    ),
    BadCase(
        "flag-update-without-text",
        _v14_item(*_GYM_FLAG, _set(updates=[{"on": "2026-10-03"}])),
        "schema",
    ),
    BadCase(
        "flag-update-two-lines",
        _v14_item(*_GYM_FLAG, _updates(("2026-10-03", "Called\nagain"))),
        "text-format",
    ),
    BadCase(
        "flag-updates-unsorted",
        _v14_item(
            *_GYM_FLAG, _updates(("2026-10-08", "Second call"), ("2026-10-03", "First call"))
        ),
        "sort-order",
    ),
    BadCase(
        "flag-updates-same-day-unsorted",
        _v14_item(*_GYM_FLAG, _updates(("2026-10-03", "Second"), ("2026-10-03", "First"))),
        "sort-order",
    ),
    BadCase(
        "flag-updates-duplicate",
        _v14_item(*_GYM_FLAG, _updates(("2026-10-03", "Called"), ("2026-10-03", "Called"))),
        "key-duplicate",
    ),
    BadCase(
        "flag-update-before-created",
        _v14_item(*_GYM_FLAG, _updates(("2026-09-30", "Too early"))),
        "date-order",
    ),
    BadCase(
        "flag-update-after-run",
        _v14_item(*_GYM_FLAG, _updates(("2026-10-13", "Too late"))),
        "date-order",
    ),
    BadCase(
        "flag-update-phone",
        _v14_item(*_GYM_FLAG, _updates(("2026-10-03", "Call them at 555-123-4567"))),
        "phone",
    ),
    BadCase(
        "recurring-keep-on-ended",
        _v14_item("recurring", "reelhouse", _set(keep=True)),
        "recurring-keep",
    ),
    BadCase(
        "recurring-keep-on-ending", _v14_item("recurring", "gym", _set(keep=True)), "recurring-keep"
    ),
    BadCase(
        "recurring-keep-false", _v14_item("recurring", "cloudtunes", _set(keep=False)), "schema"
    ),
    BadCase(
        "recurring-keep-as-number", _v14_item("recurring", "cloudtunes", _set(keep=1)), "schema"
    ),
    BadCase("recurring-bad-tag", _v14_item("recurring", "cloudtunes", _set(tag="games")), "schema"),
    BadCase(
        "recurring-tag-private",
        _v14_item("recurring", "personal-subscription", _set(tag="streaming")),
        "recurring-tag-private",
    ),
    BadCase(
        "plan-paid-off-on-active",
        _v14_item("installment_plans", "plan-sofa", _set(paid_off_on="2026-09-30")),
        "plan-dates",
    ),
    BadCase(
        "plan-paid-off-before-created",  # towne-coats is created 2025-12-20
        _v14_item("installment_plans", "towne-coats", _set(paid_off_on="2025-12-19")),
        "plan-dates",
    ),
    BadCase(
        "plan-note-phone",
        _v14_item("installment_plans", "plan-sofa", _set(note="Call 555-123-4567")),
        "phone",
    ),
    BadCase(
        "cashflow-event-bad-status",
        _v14_item("cashflow_events", "plan-sofa-ends", _set(status="assumed")),
        "schema",
    ),
    BadCase(
        "reward-done-on-not-done",
        _v14_redemption("ski-flights", _set(done_on="2026-10-01")),
        "reward-done-on",
    ),
]

# format 1.4 privacy (format-14.md section 5): every new text is scanned, and booking-code
# covers flags, installment_plans and goals (redaction.json version 4) in runs of every
# version, plan component merchants included.
BAD_CASES += [
    BadCase(
        "flag-update-private-name",  # only the second update holds the name
        _v14_item(
            *_GYM_FLAG,
            _updates(
                ("2026-10-03", "Called about the fee"),
                ("2026-10-08", "Asked EXAMPLE PRIVATE SERVICE about a bundle"),
            ),
        ),
        "private-subscription",
    ),
    BadCase(
        "plan-note-private-name",
        _v14_item(
            "installment_plans",
            "plan-sofa",
            _set(note="Paid with the EXAMPLE PRIVATE SERVICE card"),
        ),
        "private-subscription",
    ),
    BadCase(
        "goal-note-private-name",
        _goal("emergency-fund", _set(note="Gift from EXAMPLE PRIVATE SERVICE")),
        "private-subscription",
    ),
    BadCase(
        "flag-update-booking-code",
        _v14_item(*_GYM_FLAG, _updates(("2026-10-03", "Ticket QX7B2K opened"))),
        "booking-code",
    ),
    BadCase(
        "plan-note-booking-code",
        _v14_item("installment_plans", "plan-sofa", _set(note="Order QX7B2K")),
        "booking-code",
    ),
    BadCase(
        "goal-note-booking-code",
        _goal("emergency-fund", _set(note="Claim QX7B2K")),
        "booking-code",
    ),
    BadCase(
        "flag-body-booking-code-in-v13-run",
        _v13(
            _first(
                "flags.body",
                _set(body="Ticket QX7B2K opened"),
                lambda f: f.get("key") == "gym-ending",
            )
        ),
        "booking-code",
    ),
    BadCase(
        "plan-component-booking-code-in-v13-run",
        _v13(
            _first(
                "installment_plans.components.merchant",
                _set(merchant="Oak and Pine QX7B2K"),
                lambda c: c.get("merchant") == "Oak and Pine Furniture",
            )
        ),
        "booking-code",
    ),
]

# format 1.4 account debt terms and trip items (the 1.4 monthly sample, one value changed).
# summit-card is variable (apr 24.99), pinecrest-mortgage fixed, van-loan a fixed loan.
BAD_CASES += [
    v14_in_v13("accounts.original", "van-loan", original="9000.00"),
    v14_in_v13("accounts.rate_changes", rate_changes=[]),  # present counts, [] too
    v14_in_v13("trips.items.due_on", "rental-car", due_on="2027-01-15"),  # booked
    v14_in_v13("trips.items.maybe", maybe=True),  # beach-week flights, an estimate
    BadCase(
        "account-original-on-card",
        _v14_item("accounts", "summit-card", _set(original="5000.00")),
        "debt-terms",
    ),
    BadCase(
        "account-original-zero",
        _v14_item("accounts", "van-loan", _set(original="0.00")),
        "debt-terms",
    ),
    BadCase(
        "account-original-negative",
        _v14_item("accounts", "van-loan", _set(original="-1.00")),
        "money-sign",
    ),
    BadCase(
        "account-rate-changes-on-fixed",
        _v14_item("accounts", "pinecrest-mortgage", _rate_changes("2027-06-01")),
        "debt-terms",
    ),
    BadCase(
        "account-rate-changes-without-apr",
        _v14_item("accounts", "summit-card", _and(_rate_changes("2026-11-01"), _drop("apr"))),
        "debt-terms",
    ),
    BadCase(
        "account-rate-changes-without-rate-type",
        _v14_item("accounts", "summit-card", _and(_rate_changes("2026-11-01"), _drop("rate_type"))),
        "debt-terms",
    ),
    BadCase(
        "account-rate-changes-unsorted",
        _v14_item("accounts", "summit-card", _rate_changes("2027-02-01", "2026-11-01")),
        "sort-order",
    ),
    BadCase(
        "account-rate-changes-repeat",
        _v14_item("accounts", "summit-card", _rate_changes("2026-11-01", "2026-11-01")),
        "key-duplicate",
    ),
    BadCase(
        "account-rate-changes-over-four",
        _v14_item(
            "accounts",
            "summit-card",
            _rate_changes(*(f"2027-0{n}-01" for n in range(1, 6))),
        ),
        "schema",
    ),
    BadCase(
        "account-rate-change-bad-apr",
        _v14_item(
            "accounts",
            "summit-card",
            _set(rate_changes=[{"apr": "22.99%", "effective_on": "2026-11-01"}]),
        ),
        "rate-format",
    ),
    BadCase(
        "trip-due-on-posted",
        _v14_trip_item("ski-weekend", "points-credit", _set(due_on="2027-01-10")),
        "trip-due",
    ),
    BadCase(
        "trip-due-on-estimate",
        _v14_trip_item("beach-week", "flights", _set(due_on="2027-01-10")),
        "trip-due",
    ),
    BadCase(
        "trip-maybe-on-booked",
        _v14_trip_item("ski-weekend", "rental-car", _set(maybe=True)),
        "trip-maybe",
    ),
    BadCase(
        "trip-maybe-false",
        _v14_trip_item("beach-week", "flights", _set(maybe=False)),
        "schema",
    ),
    BadCase(
        "trip-maybe-as-number",
        _v14_trip_item("beach-week", "flights", _set(maybe=1)),
        "schema",
    ),
]

APP_ONLY_CASES = frozenset({"run-changed-after-import"})
NEEDS_PRIVATE_RULES = frozenset(
    {
        "private-list-name",
        "employer-term",
        "recurring-private-name",
        "flag-update-private-name",
        "plan-note-private-name",
        "goal-note-private-name",
    }
)
