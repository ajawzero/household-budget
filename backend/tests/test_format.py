"""Format v1 models: types, strictness, sections, CSV parsing, schema drift, categories."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest
from bad_cases import BAD_CASES, CHECKING_CSV, SAMPLES

from app.importer import models as m
from app.importer.models import (
    Issue,
    load_manifest,
    load_snapshot,
    parse_csv,
    version_rule,
)

REPO = Path(__file__).resolve().parents[2]
RUNS = sorted(p for p in SAMPLES.iterdir() if p.is_dir())


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _snap(run: str = "2026-10-12") -> dict:
    return json.loads((SAMPLES / run / "snapshot.json").read_text())


def _bytes(obj: object) -> bytes:
    return (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode()


def _rules(issues: list[Issue]) -> set[str]:
    return {i.rule for i in issues}


# ---- number helpers ----


@pytest.mark.parametrize(
    "text,cents",
    [("0.00", 0), ("-0.05", -5), ("12.34", 1234), ("-12.34", -1234), ("100000.01", 10000001)],
)
def test_money_round_trip(text: str, cents: int) -> None:
    assert m.to_cents(text) == cents
    assert m.cents_to_str(cents) == text.replace("-0.00", "0.00")


def test_quantity_and_rate_units() -> None:
    assert m.to_micro("1.5") == 1_500_000
    assert m.to_micro("-0.000001") == -1
    assert m.to_micro("12") == 12_000_000
    assert m.micro_to_str(1_500_000) == "1.5"
    assert m.micro_to_str(12_000_000) == "12.0"
    assert m.to_milli_pct("24.99") == 24_990
    assert m.to_milli_pct("4.5") == 4_500
    for n in (0, 1, -1, 99, 123_456_789, -987_654_321):
        assert m.to_cents(m.cents_to_str(n)) == n
        assert m.to_micro(m.micro_to_str(n)) == n


# ---- keys ----

GOOD_KEYS = ["card-gold", "alex-401k", "a-529", "kid-403b", "ab12", "a1-b2", "x", "a" * 40]
BAD_KEYS = [
    "card-123",
    "x-401kx",
    "ab123",
    "529-plan",
    "A",
    "a--b",
    "-a",
    "a-",
    "a_b",
    "a" * 41,
    "card-0042",
    "acct-2026",
]


@pytest.mark.parametrize("key", GOOD_KEYS)
def test_good_keys(key: str) -> None:
    m.NewAccount.model_validate({"key": key})


@pytest.mark.parametrize("key", BAD_KEYS)
def test_bad_keys(key: str) -> None:
    with pytest.raises(m.ValidationError) as exc:
        m.NewAccount.model_validate({"key": key})
    assert _rules(m.issues_from_error(exc.value)) == {"key-format"}


# ---- versions ----


@pytest.mark.parametrize(
    "value,rule",
    [
        ("1.0", None),
        ("1.1", None),
        ("1.2", None),
        ("1.3", None),
        ("1.4", None),
        ("1.5", "version-minor"),
        ("1.10", "version-minor"),
        ("2.0", "version-major"),
        ("0.9", "version-major"),
        ("1", "version-format"),
        (1.0, "version-format"),
        (None, "version-format"),
    ],
)
def test_version_rule(value: object, rule: str | None) -> None:
    assert version_rule(value) == rule


def test_version_messages_are_clear() -> None:
    assert "newer" in m.version_message("1.5")
    assert "1.0, 1.1, 1.2, 1.3, 1.4" in m.version_message("1.5")
    assert "major" in m.version_message("2.0")


def test_version_checked_before_strict_model() -> None:
    snap = _snap()
    snap["schema_version"] = "1.5"
    snap["loyalty"] = []  # a 1.5 section: must not show up as an unknown field
    _, issues = load_snapshot(_bytes(snap))
    assert issues == [Issue("version-minor", "snapshot.json:schema_version")]


# ---- samples round trip ----


@pytest.mark.parametrize("run", RUNS, ids=lambda p: p.name)
def test_samples_validate_and_round_trip(run: Path) -> None:
    raw = (run / "snapshot.json").read_bytes()
    snap, issues = load_snapshot(raw)
    assert issues == []
    assert snap.model_dump(mode="json", exclude_unset=True) == json.loads(raw)
    manifest, issues = load_manifest((run / "manifest.json").read_bytes())
    assert issues == [] and manifest.generator.skill == "make_samples"
    for f in manifest.files:
        if f.path.endswith(".csv"):
            rows, issues = parse_csv((run / f.path).read_bytes(), f.path)
            assert issues == [] and len(rows) == f.rows


# ---- strictness ----


def test_json_numbers_rejected_for_money() -> None:
    snap = _snap()
    snap["statements"][0]["closing"] = 12.34
    _, issues = load_snapshot(_bytes(snap))
    assert Issue("schema", "snapshot.json:statements[0].closing") in issues


def test_money_needs_two_places() -> None:
    snap = _snap()
    snap["statements"][0]["closing"] = "12.3"
    _, issues = load_snapshot(_bytes(snap))
    assert Issue("money-format", "snapshot.json:statements[0].closing") in issues


def test_unknown_field_rejected() -> None:
    snap = _snap()
    snap["accounts"][0]["nickname"] = "x"
    _, issues = load_snapshot(_bytes(snap))
    assert Issue("schema", "snapshot.json:accounts[0].nickname") in issues


def test_sensitive_unknown_field_name_is_hidden() -> None:
    snap = _snap()
    snap["accounts"][0]["acct_4111111111111111"] = "x"
    _, issues = load_snapshot(_bytes(snap))
    assert issues and all("4111" not in i.where for i in issues)
    assert Issue("schema", "snapshot.json:(hidden name)") in issues


def test_bad_date_values() -> None:
    snap = _snap()
    snap["statements"][0]["period_end"] = "2026-02-30"
    _, issues = load_snapshot(_bytes(snap))
    assert Issue("date-format", "snapshot.json:statements[0].period_end") in issues


def test_strict_bool_and_int() -> None:
    snap = _snap()
    snap["statements"][0]["reconciled"] = "true"
    snap["statements"][1]["txn_count"] = "3"
    _, issues = load_snapshot(_bytes(snap))
    assert {i.where for i in issues} >= {
        "snapshot.json:statements[0].reconciled",
        "snapshot.json:statements[1].txn_count",
    }


def test_errors_never_carry_values() -> None:
    snap = _snap()
    snap["accounts"][0]["last4"] = "98765"
    _, issues = load_snapshot(_bytes(snap))
    assert issues == [Issue("last4-format", "snapshot.json:accounts[0].last4")]
    assert "98765" not in repr(issues)


def test_model_rules() -> None:
    snap = _snap()
    snap["statements"][0]["reconciled"] = False
    acct = next(a for a in snap["accounts"] if a["include_in_net_worth"])
    del acct["nw_group"]
    _, issues = load_snapshot(_bytes(snap))
    assert _rules(issues) == {"reconcile-note", "nw-group"}
    snap = _snap()
    snap["statements"][0]["period_start"] = "2026-12-31"
    _, issues = load_snapshot(_bytes(snap))
    assert _rules(issues) == {"date-order"}


def test_manifest_rows_rule() -> None:
    raw = json.loads((SAMPLES / "2026-10-12" / "manifest.json").read_text())
    del raw["files"][1]["rows"]
    raw["files"][0]["rows"] = 1
    _, issues = load_manifest(_bytes(raw))
    assert issues == [
        Issue("manifest-rows", "manifest.json:files[0]"),
        Issue("manifest-rows", "manifest.json:files[1]"),
    ]


def test_manifest_bad_path() -> None:
    raw = json.loads((SAMPLES / "2026-10-12" / "manifest.json").read_text())
    raw["files"][1]["path"] = "transactions/../x.csv"
    _, issues = load_manifest(_bytes(raw))
    assert issues == [Issue("path-not-allowed", "manifest.json:files[1].path")]


# ---- tombstones ----


def test_tombstones() -> None:
    snap, _ = load_snapshot((SAMPLES / "2026-10-12" / "snapshot.json").read_bytes())
    tombs = [b for b in snap.balances if isinstance(b, m.BalanceTombstone)]
    assert len(tombs) == 1 and tombs[0].deleted is True
    raw = _snap()
    raw["balances"].append(
        {
            "account_key": "family-car",
            "as_of": "2026-07-31",
            "source": "valuation",
            "deleted": True,
            "balance": "1.00",
        }
    )
    _, issues = load_snapshot(_bytes(raw))
    assert "schema" in _rules(issues)
    raw = _snap()
    raw["balances"][0]["deleted"] = False
    _, issues = load_snapshot(_bytes(raw))
    assert "schema" in _rules(issues)


# ---- sections ----


def test_required_sections_per_mode() -> None:
    snap = _snap()
    del snap["budgets"]
    del snap["review"]
    _, issues = load_snapshot(_bytes(snap))
    assert issues == [
        Issue("section-missing", "snapshot.json:budgets"),
        Issue("section-missing", "snapshot.json:review"),
    ]
    corr = {"schema_version": "1.0", "run_id": "2026-10-14", "mode": "correction"}
    _, issues = load_snapshot(_bytes(corr))
    assert issues == [Issue("section-missing", "snapshot.json:statements")]


def test_state_not_allowed_in_backfill() -> None:
    snap = _snap("2026-10-06")
    snap["meta"] = {"updated": "2026-10-06", "this_month": "2026-10"}
    snap["income"]["income_streams"] = []
    _, issues = load_snapshot(_bytes(snap))
    assert issues == [
        Issue("section-not-allowed", "snapshot.json:meta"),
        Issue("section-not-allowed", "snapshot.json:income.income_streams"),
    ]


def test_duplicates_and_sort_order() -> None:
    snap = _snap()
    snap["accounts"] = list(reversed(snap["accounts"]))
    snap["todos"].append(dict(snap["todos"][-1]))
    snap["net_worth"]["parts"].reverse()
    _, issues = load_snapshot(_bytes(snap))
    assert set(issues) == {
        Issue("sort-order", "snapshot.json:accounts[1]"),
        Issue("key-duplicate", "snapshot.json:todos[2]"),
        Issue("sort-order", "snapshot.json:net_worth.parts"),
    }


def test_paycheck_seq() -> None:
    snap = _snap("2026-10-06-2")
    pays = [p for p in snap["income"]["paychecks"] if p["pay_date"] == "2026-06-12"]
    assert [p["seq"] for p in pays] == [1, 2]
    pays[0]["seq"], pays[1]["seq"] = 2, 1
    snap["income"]["paychecks"].sort(
        key=lambda p: (p["person"], p["pay_date"], p["kind"], p["seq"])
    )
    _, issues = load_snapshot(_bytes(snap))
    assert issues == [Issue("paycheck-seq", "snapshot.json:income.paychecks")]


# ---- CSV ----


def _csv() -> bytes:
    return (SAMPLES / "2026-10-12" / CHECKING_CSV).read_bytes()


def test_csv_header_and_endings() -> None:
    data = _csv()
    assert parse_csv(data.replace(b"txn_date,post_date", b"post_date,txn_date", 1), "f")[1] == [
        Issue("csv-header", "f")
    ]
    assert _rules(parse_csv(data.replace(b"\n", b"\r\n"), "f")[1]) == {"line-endings"}
    assert parse_csv(b"\xef\xbb\xbf" + data, "f")[1] == [Issue("encoding", "f")]
    assert parse_csv(data.split(b"\n", 1)[0] + b"\n", "f")[1] == [Issue("csv-empty", "f")]


def test_csv_row_errors() -> None:
    lines = _csv().decode().split("\n")
    cells = lines[1].split(",")
    cells[2] = "12"  # amount
    cells[8] = "yes"  # is_transfer
    lines[1] = ",".join(cells)
    lines[2] = lines[2] + ",extra"
    rows, issues = parse_csv("\n".join(lines).encode(), "f")
    assert set(issues) == {
        Issue("money-format", "f:row 1.amount"),
        Issue("bool-format", "f:row 1.is_transfer"),
        Issue("csv-format", "f:row 2"),
    }


def test_csv_sort_order() -> None:
    lines = _csv().decode().split("\n")
    lines[1], lines[2] = lines[2], lines[1]
    _, issues = parse_csv("\n".join(lines).encode(), "f")
    assert issues == [Issue("csv-sort", "f:row 2")]


def test_csv_bool_helper() -> None:
    assert (m.csv_bool("true"), m.csv_bool("false"), m.csv_bool("")) == (True, False, None)


# ---- text walk ----


def test_iter_text_skips_formatted_fields() -> None:
    snap, _ = load_snapshot((SAMPLES / "2026-10-12" / "snapshot.json").read_bytes())
    locs = dict(m.iter_text(snap))
    assert "accounts[0].key" in locs
    assert "accounts[0].display_name" in locs
    assert not any(
        loc.endswith((".last4", ".closing", ".period_end", ".cusip", "run_id")) for loc in locs
    )
    assert not any(re.fullmatch(m.MONEY_PATTERN, t) for t in locs.values())
    row = m.CsvRow.model_validate(
        dict(zip(m.CSV_HEADER, _csv().decode().split("\n")[1].split(","), strict=True))
    )
    assert {loc for loc, _ in m.iter_text(row)} <= {
        "description",
        "merchant",
        "category_key",
        "cardholder",
        "counterparty_account_key",
        "plan_key",
        "recurring_key",
        "trip_key",
        "notes",
        "txn_type",
    }


# ---- bad cases through the models alone ----

MODEL_CASES = {
    "bad-last4",
    "key-with-digits",
    "missing-section",
    "schema-major-2",
    "unknown-minor",
    # format 1.1 rules that the models enforce on their own
    "v11-sections-in-v10-run",
    "missing-flags",
    "correction-with-flags",
    "recurring-negative-amount",
    "plan-negative-remaining",
    "recurring-interval-missing",
    "recurring-interval-not-allowed",
    "active-months-unsorted",
    "active-month-13",
    "recurring-bad-cadence",
    "recurring-end-before-start",
    "promo-without-expiry",
    "deferred-without-pay-before",
    "plan-payments-left",
    "plan-components-sum",
    "plan-money-as-number",
    "flag-resolved-without-date",
    "flag-related-half",
    "flag-bad-severity",
    "flag-resolved-before-created",
    "pending-updated-before-added",
    "pending-unknown-field",
    # format 1.2 utility bill rules that the models enforce on their own
    "v12-section-in-v11-run",
    "utility-total-off",
    "utility-total-off-3-cents",
    "utility-period-empty",
    "utility-date-too-early",
    "utility-gas-in-kwh",
    "utility-water-none",
    "utility-duplicate-service",
    "utility-lines-unsorted",
    "utility-credit-positive",
    "utility-fixed-over-charges",
    "utility-negative-usage",
    "utility-unknown-unit",
    "utility-days-as-text",
    "utility-no-lines",
    # format 1.3 rewards and trips rules that the models enforce on their own
    "v13-rewards-in-v12-run",
    "v13-trips-in-v12-run",
    "rewards-in-backfill",
    "monthly-13-without-rewards",
    "reward-balance-without-as-of",
    "reward-cpp-reversed",
    "reward-cash-back-cpp",
    "reward-credit-without-cadence",
    "reward-cap-field-on-credit",
    "reward-unknown-program",
    "reward-perk-unknown-program",
    "reward-over-allocated",
    "reward-allocations-on-points",
    "reward-programs-unsorted",
    "reward-routing-duplicate",
    "reward-cycles-unsorted",
    "trip-month-and-dates",
    "trip-before-2000",
    "trip-end-before-start",
    "trip-estimate-reversed",
    "trip-posted-without-amount",
    "trip-points-without-points",
    "trip-iou-settled-without-date",
    "trip-iou-unknown-item",
    "trip-items-unsorted",
    # format 1.4 gate and goals rules that the models enforce on their own
    "v14-goals-in-v13-run",
    "goals-in-backfill",
    "goal-college-without-person",
    "goal-college-without-start-year",
    "goal-target-date-on-college",
    "goal-start-year-on-save-up",
    "goal-college-adult",
    "goal-college-brokerage",
    "goal-accounts-unsorted",
    "goal-accounts-duplicate",
    "goal-no-accounts",
    "goal-start-year-as-text",
    "goal-bad-kind",
    "goal-negative-target",
    "goals-unsorted",
    # format 1.4 state fields: the gate and the rules the models enforce on their own
    "v14-flags-kind-in-v13-run",
    "v14-flags-action-in-v13-run",
    "v14-flags-action-null-in-v13-run",
    "v14-flags-updates-in-v13-run",
    "v14-recurring-keep-in-v13-run",
    "v14-recurring-tag-in-v13-run",
    "v14-installment-plans-note-in-v13-run",
    "v14-installment-plans-paid-off-on-in-v13-run",
    "v14-cashflow-events-status-in-v13-run",
    "v14-rewards-redemptions-done-on-in-v13-run",
    "flag-kind-reimbursement",
    "flag-bad-action",
    "flag-updates-empty",
    "flag-too-many-updates",
    "flag-update-without-text",
    "flag-update-two-lines",
    "flag-updates-unsorted",
    "flag-updates-same-day-unsorted",
    "flag-updates-duplicate",
    "flag-update-before-created",
    "flag-update-after-run",
    "recurring-keep-on-ended",
    "recurring-keep-on-ending",
    "recurring-keep-false",
    "recurring-keep-as-number",
    "recurring-bad-tag",
    "recurring-tag-private",
    "plan-paid-off-on-active",
    "plan-paid-off-before-created",
    "cashflow-event-bad-status",
    "reward-done-on-not-done",
    # format 1.4 account debt terms and trip items
    "v14-accounts-original-in-v13-run",
    "v14-accounts-rate-changes-in-v13-run",
    "v14-trips-items-due-on-in-v13-run",
    "v14-trips-items-maybe-in-v13-run",
    "account-original-on-card",
    "account-original-zero",
    "account-original-negative",
    "account-rate-changes-on-fixed",
    "account-rate-changes-without-apr",
    "account-rate-changes-without-rate-type",
    "account-rate-changes-unsorted",
    "account-rate-changes-repeat",
    "account-rate-changes-over-four",
    "account-rate-change-bad-apr",
    "trip-due-on-posted",
    "trip-due-on-estimate",
    "trip-maybe-on-booked",
    "trip-maybe-false",
    "trip-maybe-as-number",
}


@pytest.mark.parametrize(
    "case", [c for c in BAD_CASES if c.case_id in MODEL_CASES], ids=lambda c: c.case_id
)
def test_bad_cases_rejected_by_models(case, tmp_path: Path) -> None:
    run = case.builder(tmp_path)
    _, issues = load_snapshot((run / "snapshot.json").read_bytes())
    assert case.expected_rule_id in _rules(issues)


# ---- schema drift and categories ----


def test_schema_has_not_drifted() -> None:
    gen = _load_script("gen_schema")
    assert gen.build_schema() == (REPO / "schema" / "import-v1.schema.json").read_text()


def test_categories() -> None:
    cats = json.loads((REPO / "schema" / "categories.json").read_text())
    groups = {g["key"]: g for g in cats["groups"]}
    keys = [c["key"] for c in cats["categories"]]
    assert len(keys) == len(set(keys)) == 53
    assert len({c["emoji"] for c in cats["categories"]}) == len(keys)  # each emoji once
    key_rx = re.compile(m.KEY_PATTERN)
    for c in cats["categories"]:
        assert key_rx.fullmatch(c["key"]) and c["group"] in groups and c["emoji"] and c["name"]
        assert c["kind"] in {"spending", "income", "transfer", "reimbursement", "uncategorized"}
        assert groups[c["group"]]["counts_in_spending"] == (c["kind"] == "spending")
    for g in groups:
        assert key_rx.fullmatch(g)
    for needed in (
        "uncategorized",
        "transfer",
        "reimbursement",
        "plan-fees",
        "subscriptions",
        "paycheck",
        "mortgage",
        "home-equity-loan",
        "fitness",
    ):
        assert needed in keys
    assert len({c["sort"] for c in cats["categories"]}) == len(keys)
    # Phase 7: the Transportation group, after Housing and utilities.
    assert groups["transportation"] == {
        "counts_in_spending": True,
        "key": "transportation",
        "name": "Transportation",
        "sort": 25,
    }
    transport = [
        (c["key"], c["name"], c["emoji"], c["sort"], c["kind"])
        for c in cats["categories"]
        if c["group"] == "transportation"
    ]
    assert transport == [  # docs/plans/phase-7.md section 5
        ("fuel", "Fuel", "⛽", 251, "spending"),
        ("vehicle-maintenance", "Maintenance and repairs", "🛠️", 252, "spending"),
        ("parking", "Parking", "🅿️", 253, "spending"),
        ("tolls", "Tolls", "🛣️", 254, "spending"),
        ("rideshare-and-transit", "Rideshare and transit", "🚕", 255, "spending"),
        ("vehicle-registration", "Vehicle registration", "🪪", 256, "spending"),
    ]
    by_key = {c["key"]: c for c in cats["categories"]}
    assert by_key["auto-and-home-insurance"]["group"] == "insurance"
    assert by_key["vehicle-loans"]["group"] == "debt"
    assert by_key["travel"]["group"] == "discretionary"
    assert cats["version"] == 1


def test_readme_documents_every_field() -> None:
    readme = (REPO / "schema" / "README.md").read_text()
    assert "—" not in readme  # no em dashes
    schema = json.loads((REPO / "schema" / "import-v1.schema.json").read_text())
    missing = {
        f"{name}.{field}"
        for name, d in schema["$defs"].items()
        for field in d.get("properties", {})
        if not re.search(rf'[`.]{field}`|"{field}"', readme)
    }
    assert missing == set()
    for rule in m.RULES:
        assert f"`{rule}`" in readme, rule
