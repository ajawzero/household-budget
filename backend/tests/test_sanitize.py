"""detect_sensitive, the stock plan rule and the private lists."""

from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path

import pytest
from bad_cases import PRIVATE_RULES_EXAMPLE, SAMPLES

from app.importer import sanitize
from app.importer.models import load_snapshot, parse_csv
from app.importer.sanitize import (
    PrivateRules,
    PrivateRulesError,
    detect_sensitive,
    is_stock_plan_name,
    load_private_rules,
    load_private_rules_from_env,
    scan_model,
)

REPO = Path(__file__).resolve().parents[2]

POSITIVE = [
    ("digits-run", "REF 12345"),
    ("digits-run", "ZIP 53703"),
    ("digits-split", "ACCT 12 34 56 78"),
    ("digits-split", "1234.5678.90"),
    ("digits-split", "12.34.56.78"),
    ("luhn", "CARD 4111 1111 1111 1111"),
    ("luhn", "4111-1111-1111-1111"),
    ("luhn", "4111111111111111"),
    ("phone", "CALL 555-123-4567"),
    ("phone", "(555) 123-4567"),
    ("phone", "555.123.4567"),
    ("split-account", "ACCT 1234-5678"),
    ("masked-number", "CARD XXXX1234"),
    ("masked-number", "ENDING ****1234"),
    ("masked-number", "MEMBER #-#####"),
    ("masked-number", "xx42"),
    ("email", "a.person@example.com"),
    ("street-address", "100 Pine Street"),
    ("street-address", "12 N Oak Ave"),
    ("street-address", "P.O. Box"),
    # separators and Unicode forms that must not hide a number
    ("luhn", "CARD 4111  1111  1111  1111"),
    ("luhn", "CARD 4111\u00a01111\u00a01111\u00a01111"),
    ("luhn", "CARD 4111\u20131111\u20131111\u20131111"),
    ("luhn", "CARD 4111,1111,1111,1111"),
    ("luhn", "CARD 4111/1111/1111/1111"),
    ("luhn", "CARD 4111 - 1111 - 1111 - 1111"),
    (
        "luhn",
        "CARD \uff14\uff11\uff11\uff11 \uff11\uff11\uff11\uff11 "
        "\uff11\uff11\uff11\uff11 \uff11\uff11\uff11\uff11",
    ),
    ("luhn", "CARD 4111\u200b1111\u200b1111\u200b1111"),
    ("luhn", "CARD 4111 1111 1111 1111, EXP 12/28"),
    ("digits-split", "ACCT 1234  5678"),
    ("phone", "(800)555-1234"),
    ("phone", "555/123/4567"),
    ("phone", "555\u2013123\u20134567"),
    ("masked-number", "XX-1234"),
    ("masked-number", "x-1234"),
    ("masked-number", "* * * * 1234"),
    ("masked-number", "** 1234"),
    ("street-address", "12 W 3rd St"),
]

CLEAN = [
    "SQ *MAPLE CAFE #12",
    "RIVER MKTP*AB1CD2E",
    "PAYMENT RECEIVED - THANK YOU",
    "Paid 1,234.56 on 2026-09-30",
    "Fees 12.34 56.78",
    "School year 2025-26",
    "Target Date 2050 Trust",
    "Alex 401k (Lakeview)",
    "2 WAY RADIO",
    "TAXI",
    "QUICKFUEL STATION",
    "Cycle closes on the 22nd",
    "Run 2026-10-21 and 2026-09-30",
    "1234",
    "",
    "TRIPLE XXX BAR",
    "Sep 2025-26 tuition",
    "1920 x 1080",
    "PLAN IT FEE 5.00",
    "date(2026, 10, 21)",
    "Box 12",
]


@pytest.mark.parametrize("rule,text", POSITIVE)
def test_rules_fire(rule: str, text: str) -> None:
    assert rule in detect_sensitive(text)


@pytest.mark.parametrize("text", CLEAN)
def test_clean_text(text: str) -> None:
    assert detect_sensitive(text) == []


def test_luhn_needs_a_valid_check_digit() -> None:
    assert "luhn" not in detect_sensitive("4111 1111 1111 1112")
    assert "digits-split" in detect_sensitive("4111 1111 1111 1112")


def test_rule_order_and_no_values() -> None:
    found = detect_sensitive("CARD 4111 1111 1111 1111 CALL 555-123-4567")
    assert found == ["digits-split", "luhn", "phone"]
    assert all("4111" not in r for r in found)


def test_stock_plan_names() -> None:
    for name in ("ESPP Shares", "Unvested RSUs", "Employee Stock Plan", "RSU lot"):
        assert is_stock_plan_name(name)
    for name in ("Broad Market Index Fund", "Bond Index Fund", "Trust Units"):
        assert not is_stock_plan_name(name)


# ---- private lists ----


def test_private_rules_match_loosely() -> None:
    rules = load_private_rules(PRIVATE_RULES_EXAMPLE)
    assert rules.check("Example Private Service") == ["private-subscription"]
    assert rules.check("example-private-service") == ["private-subscription"]
    assert rules.check("EXAMPLEPRIVATESERVICE MONTHLY") == ["private-subscription"]
    assert rules.check("example employer co payroll") == ["private-employer"]
    assert rules.check("Personal subscription") == []
    assert rules.check("GREEN LEAF MARKET") == []
    assert len(rules.digest) == 64


def test_private_rules_match_full_width_letters() -> None:
    rules = load_private_rules(PRIVATE_RULES_EXAMPLE)
    wide = "".join(chr(ord(c) + 0xFEE0) if c != " " else " " for c in "EXAMPLE PRIVATE SERVICE")
    assert rules.check(wide) == ["private-subscription"]


def test_private_rules_never_show_values(caplog: pytest.LogCaptureFixture) -> None:
    rules = load_private_rules(PRIVATE_RULES_EXAMPLE)
    with caplog.at_level(logging.DEBUG):
        text = repr(rules) + str(rules) + repr(rules.check("EXAMPLE PRIVATE SERVICE"))
    assert "EXAMPLE" not in text.upper()
    assert "EXAMPLE" not in caplog.text.upper()


def test_private_rules_file_errors(tmp_path: Path) -> None:
    bad = tmp_path / "rules.json"
    bad.write_text(json.dumps({"names": ["SECRET VALUE"]}))
    with pytest.raises(PrivateRulesError) as exc:
        load_private_rules(bad)
    assert "SECRET" not in str(exc.value)
    bad.write_text(json.dumps({"employer_terms": ["", "x"]}))
    with pytest.raises(PrivateRulesError):
        load_private_rules(bad)
    with pytest.raises(PrivateRulesError):
        load_private_rules(tmp_path / "missing.json")


def test_private_rules_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUDGET_PRIVATE_RULES", raising=False)
    assert not load_private_rules_from_env()
    monkeypatch.setenv("BUDGET_PRIVATE_RULES", str(PRIVATE_RULES_EXAMPLE))
    assert load_private_rules_from_env().check("example private service")


def test_empty_rules_match_nothing() -> None:
    assert PrivateRules().check("EXAMPLE PRIVATE SERVICE") == []


# ---- scanning the samples ----


def test_samples_scan_clean() -> None:
    rules = load_private_rules(PRIVATE_RULES_EXAMPLE)
    for run in sorted(p for p in SAMPLES.iterdir() if p.is_dir()):
        snap, issues = load_snapshot((run / "snapshot.json").read_bytes())
        assert issues == []
        assert scan_model(snap, "snapshot.json:", rules) == []
        for f in sorted(run.glob("transactions/*.csv")):
            rows, issues = parse_csv(f.read_bytes(), f.name)
            assert issues == []
            for row in rows:
                assert scan_model(row, f.name, rules) == []


def test_scan_model_reports_location_only() -> None:
    raw = json.loads((SAMPLES / "2026-10-12" / "snapshot.json").read_text())
    raw["review"]["summary"] = "Call 555-123-4567"
    raw["accounts"][0]["notes"] = "EXAMPLE PRIVATE SERVICE"
    snap, _ = load_snapshot((json.dumps(raw, indent=2, sort_keys=True) + "\n").encode())
    issues = scan_model(snap, "snapshot.json:", load_private_rules(PRIVATE_RULES_EXAMPLE))
    assert {(i.rule, i.where) for i in issues} == {
        ("digits-split", "snapshot.json:review.summary"),
        ("phone", "snapshot.json:review.summary"),
        ("private-subscription", "snapshot.json:accounts[0].notes"),
    }
    assert "555" not in repr(issues) and "EXAMPLE" not in repr(issues)


# ---- parity with schema/check_run.py ----


def _check_run_module():
    spec = importlib.util.spec_from_file_location("check_run", REPO / "schema" / "check_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_checker_uses_the_same_algorithm() -> None:
    cr = _check_run_module()
    private = json.loads(PRIVATE_RULES_EXAMPLE.read_text())
    red = cr.Redaction(REPO / "schema" / "redaction.json", private)
    rules = load_private_rules(PRIVATE_RULES_EXAMPLE)
    corpus = (
        [t for _, t in POSITIVE]
        + CLEAN
        + [
            "4111 1111 1111 1112",
            "EXAMPLE PRIVATE SERVICE",
            "example-employer-co",
            "1,234,567.89",
            "2026-01-15-1234-5678",
            "ref 12-34-56-78-90",
            "•••• 1234",
            "Record locator QX7B2K",
            "Flight UA1234",
        ]
    )
    for text in corpus:
        assert red.scan(text) == detect_sensitive(text) + rules.check(text), text
        # booking-code runs in five sections (redaction.json version 4), not budgets
        for section in ("trips", "rewards", "flags", "installment_plans", "goals", "budgets"):
            expected = detect_sensitive(text, section) + rules.check(text)
            assert red.scan(text, section) == expected, (text, section)
    for name in ("ESPP Shares", "Bond Index Fund"):
        assert bool(red.stock_plan.search(name)) == is_stock_plan_name(name)


def test_redaction_file_is_loaded_once() -> None:
    assert sanitize.load_redaction() is sanitize.load_redaction()
