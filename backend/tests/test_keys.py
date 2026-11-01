"""desc_norm, occurrence_n and the stable keys (PLAN 4.5)."""

from __future__ import annotations

import hashlib

import pytest
from bad_cases import CHECKING_CSV, SAMPLES

from app.importer import keys
from app.importer.models import parse_csv


@pytest.mark.parametrize(
    "raw,norm",
    [
        ("SQ *BLUE BOTTLE", "SQ *BLUE BOTTLE"),
        ("SQ *PANERA", "SQ *PANERA"),
        ("SQ *MAPLE CAFE #12", "SQ *MAPLE CAFE"),
        ("SQ *MAPLE CAFE #34", "SQ *MAPLE CAFE"),
        ("River Mktp*AB1CD2E", "RIVER MKTP"),
        ("  corner   grocery\t", "CORNER GROCERY"),
        ("STORE #12 MAIN", "STORE #12 MAIN"),
        ("PAYMENT XXXX1234 THANK YOU", "PAYMENT THANK YOU"),
        ("CARD ****5678", "CARD"),
        ("TRIPLE XXX BAR", "TRIPLE XXX BAR"),  # real words made of X stay
        ("XX LOUNGE", "XX LOUNGE"),
        ("PAYPAL *AB12 XXXX1234", "PAYPAL *AB12"),  # tail first (has a space, stays), then mask
        ("STORE ••12 XXXX", "STORE"),
        ("A VERY LONG MERCHANT DESCRIPTION THAT GOES ON", "A VERY LONG MERCHANT DESCRIPTION"),
        ("ABCDEFGHIJKLMNOPQRSTUVWXYZABCDE FGH", "ABCDEFGHIJKLMNOPQRSTUVWXYZABCDE"),
    ],
)
def test_desc_norm(raw: str, norm: str) -> None:
    assert keys.desc_norm(raw) == norm
    assert len(keys.desc_norm(raw)) <= keys.DESC_NORM_MAX


def test_statement_key_and_csv_path() -> None:
    assert keys.statement_key("summit-card", "2026-09-10") == "summit-card:2026-09-10"
    assert keys.csv_path("alex-401k", "2026-09-30") == "transactions/alex-401k-2026-09-30.csv"
    assert keys.parse_csv_path("transactions/alex-401k-2026-09-30.csv") == (
        "alex-401k",
        "2026-09-30",
    )
    assert keys.parse_csv_path("transactions/x.csv") is None


def test_occurrences() -> None:
    rows = [
        ("2026-07-18", -2345, "CORNER GROCERY"),
        ("2026-07-18", -2345, "CORNER GROCERY"),
        ("2026-07-18", -2346, "CORNER GROCERY"),
        ("2026-07-19", -2345, "CORNER GROCERY"),
        ("2026-07-18", -2345, "CORNER GROCERY"),
    ]
    assert keys.assign_occurrences(rows) == [1, 2, 1, 1, 3]


def test_txn_key_formula() -> None:
    raw = b"summit-card:2026-08-10|2026-08-02|-650|SQ *MAPLE CAFE|2"
    expected = hashlib.sha256(raw).hexdigest()
    got = keys.txn_key("summit-card:2026-08-10", "2026-08-02", -650, "SQ *MAPLE CAFE", 2)
    assert got == expected
    rows = [("2026-08-02", -650, "SQ *MAPLE CAFE #12"), ("2026-08-02", -650, "SQ *MAPLE CAFE #34")]
    k1, k2 = keys.txn_keys("summit-card:2026-08-10", rows)
    assert k2 == expected and k1 != k2


def test_paycheck_and_contribution_keys() -> None:
    assert (
        keys.paycheck_key("alex", "2026-06-12", "regular", 2)
        == hashlib.sha256(b"alex|2026-06-12|regular|2").hexdigest()
    )
    assert keys.paycheck_key("alex", "2026-06-12", "regular", 1) != keys.paycheck_key(
        "alex", "2026-06-12", "regular", 2
    )
    assert (
        len(
            keys.contribution_key(
                "alex", "alex-401k", "401k_employee", "current_employer", "2026-06-12"
            )
        )
        == 64
    )


def _keys_for(run: str, rel: str) -> list[str]:
    rows, issues = parse_csv((SAMPLES / run / rel).read_bytes(), rel)
    assert issues == []
    account, end = keys.parse_csv_path(rel)
    return keys.txn_keys(
        keys.statement_key(account, end),
        [(r.txn_date, r.amount_cents, r.description) for r in rows],
    )


def test_sample_keys_unique_per_statement() -> None:
    for run in sorted(p for p in SAMPLES.iterdir() if p.is_dir()):
        for f in sorted(run.glob("transactions/*.csv")):
            got = _keys_for(run.name, f"transactions/{f.name}")
            assert len(got) == len(set(got))


def test_correction_keeps_attribute_fix_and_rekeys_changed_row() -> None:
    monthly = set(_keys_for("2026-10-12", CHECKING_CSV))
    correction = set(_keys_for("2026-10-14", CHECKING_CSV))
    assert len(monthly - correction) == 1  # the row whose description changed
    assert len(correction - monthly) == 1
    assert len(monthly & correction) == len(monthly) - 1  # the category fix keeps its key


def test_edge_rows_in_samples() -> None:
    got = _keys_for("2026-10-06-2", "transactions/summit-card-2026-08-10.csv")
    assert len(got) == len(set(got))
    june = _keys_for("2026-10-06-2", "transactions/summit-card-2026-06-10.csv")
    july = _keys_for("2026-10-06-2", "transactions/summit-card-2026-07-10.csv")
    assert not set(june) & set(july)
