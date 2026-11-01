"""Phase 6 core: prices, settings, the authorizer, facts, tools, the output filter,
the caps and the call wrapper. Fake client only, never a key or the network."""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from ai_fakes import FakeClient, bad_request, message, rate_limited, server_error, text

from app import config
from app.ai import client as ai_client
from app.ai import ledger, pricing, tools
from app.ai import settings as ai_settings
from app.ai.facts import FACTS_VERSION, build, ensure_facts, signature
from app.ai.postprocess import MASK, PRIVATE_LABEL, Scrub, StreamFilter, filter_text
from app.ai.pricing import Tokens, cost_micro_usd
from app.ai.safe import SAFE_VIEWS, AuthorizerDenied, ai_connection, run
from app.config import Settings, get_settings
from app.db.connect import connect, write_connection
from app.importer.sanitize import PrivateRules, detect_sensitive

TODAY = date(2026, 10, 20)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waits: list[float] = []

    async def fake_sleep(s: float) -> None:
        waits.append(s)

    monkeypatch.setattr(ai_client.asyncio, "sleep", fake_sleep)
    return waits


def enable(settings: Settings, **fields) -> None:
    with write_connection(settings.db_path) as conn:
        ai_settings.save(conn, ai_settings.AiSettings(enabled=True, **fields), None)


@pytest.fixture
def with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-canary-key-for-tests-7f3a")
    get_settings.cache_clear()


@pytest.fixture
def fake() -> FakeClient:
    client = FakeClient()
    ai_client.set_client_factory(lambda _s: client)
    yield client
    ai_client.set_client_factory(None)


# --------------------------------------------------------------------------
# Prices
# --------------------------------------------------------------------------


def test_prices_match_the_checked_table() -> None:
    p = pricing.PRICES
    assert p["claude-sonnet-5-5"].input == Decimal("2") and p["claude-sonnet-5-5"].output == 10
    assert p["claude-sonnet-5-5"].cache_read == Decimal("0.10")
    assert p["claude-opus-5-5"].write_5m == 5 and p["claude-opus-5-5"].write_1h == 8
    assert p["claude-haiku-5-5"].write_5m == Decimal("0.125")
    assert set(pricing.MODELS) == {"claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"}


def test_cost_of_a_call() -> None:
    # Sonnet 5.5: 1,000 in, 4,000 written (5m), 10,000 read, 500 out.
    t = Tokens(input=1000, write_5m=4000, cache_read=10_000, output=500)
    assert cost_micro_usd("claude-sonnet-5-5", t) == 2000 + 10_000 + 1000 + 5000
    # 1-hour writes cost more.
    assert cost_micro_usd("claude-opus-5-5", Tokens(write_1h=1000)) == 8000
    # Haiku over 100K prompt tokens: the whole request at the higher row.
    small = Tokens(input=100_000, output=1000)
    big = Tokens(input=100_001, output=1000)
    assert cost_micro_usd("claude-haiku-5-5", small) == 10_000 + 500
    assert cost_micro_usd("claude-haiku-5-5", big) == 50_001 + 2500  # 50,000.5 rounds up
    with pytest.raises(pricing.UnknownModel):
        cost_micro_usd("claude-made-up", t)


def test_tokens_split_writes_by_ttl() -> None:
    msg = message([text("x")], input_tokens=10, write=30, read=5)
    t = pricing.tokens_of(msg.usage)
    assert (t.input, t.write_5m, t.write_1h, t.cache_read, t.output) == (10, 30, 0, 5, 200)
    assert t.prompt == 45


# --------------------------------------------------------------------------
# Settings and the off switch
# --------------------------------------------------------------------------


def test_ai_is_off_by_default(db: sqlite3.Connection, settings: Settings) -> None:
    s = ai_settings.load(db)
    assert s.enabled is False
    assert (s.ask_model, s.review_model, s.categorize_model, s.planner_model) == (
        "claude-sonnet-5-5",
        "claude-opus-5-5",
        "claude-haiku-5-5",
        "claude-opus-5-5",
    )
    assert (s.monthly_cap_cents, s.ask_reserve_cents, s.daily_cap_cents) == (2500, 200, 400)
    st = ai_settings.status(db, settings)
    assert st["available"] is False and st["reason"] == "off"


def test_status_reasons(with_key, settings: Settings, db: sqlite3.Connection) -> None:
    s = get_settings()
    assert ai_settings.status(db, s)["reason"] == "off"
    with db:
        pass
    enable(settings)
    assert ai_settings.status(db, s)["available"] is True
    now = datetime(2026, 10, 20, 15, tzinfo=UTC)
    db.execute("BEGIN")
    block = ai_settings.set_block(db, "workspace_limit", now)
    db.execute("COMMIT")
    assert block.until == "2026-11-01T00:00:00+00:00"
    st = ai_settings.status(db, s, now)
    assert (st["available"], st["reason"]) == (False, "spend_limit")
    assert "workspace" in st["blocked_reason"]
    # After the 1st of next month the block is over.
    assert ai_settings.status(db, s, datetime(2026, 11, 1, 0, 1, tzinfo=UTC))["available"]
    assert ai_settings.next_month_utc(datetime(2026, 12, 9, tzinfo=UTC)).isoformat() == (
        "2027-01-01T00:00:00+00:00"
    )


def test_no_key_means_no_ai(settings: Settings, db: sqlite3.Connection) -> None:
    enable(settings)
    st = ai_settings.status(db, settings)
    assert (st["available"], st["reason"], st["key_present"]) == (False, "no_key", False)


# --------------------------------------------------------------------------
# The AI connection and its authorizer (PLAN 5.7)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM transactions",
        "SELECT txn_key FROM transactions",
        "SELECT * FROM v_transactions",
        "SELECT * FROM v_cashflow_rows",
        "SELECT last4 FROM accounts",
        "SELECT notes FROM transaction_overrides",
        "SELECT * FROM users",
        "SELECT * FROM sessions",
        "SELECT * FROM ai_messages",
        "SELECT * FROM settings",
        "SELECT * FROM sqlite_master",
        "PRAGMA table_info(accounts)",
        "SELECT hex(txn_key) FROM transactions_safe",
        "SELECT * FROM accounts_safe, accounts",
        "SELECT (SELECT description FROM transactions LIMIT 1) FROM accounts_safe",
        "SELECT count(1) FROM v_cashflow_rows",
        'SELECT count(a.account_key) FROM accounts_safe AS a JOIN "accounts" AS b ON 1',
        # the rewards inputs (migration 0010): rewards stay out of the AI
        "SELECT * FROM reward_balances",
        "SELECT * FROM reward_app_programs",
        "SELECT * FROM reward_benefit_edits",
        "SELECT * FROM reward_match_terms",
        "SELECT * FROM reward_credit_decisions",
        "SELECT * FROM reward_cycle_marks",
        # the budget target edits (migration 0014)
        "SELECT * FROM budget_target_edits",
    ],
)
def test_authorizer_denies_everything_but_safe_views(samples: Settings, sql: str) -> None:
    with ai_connection(samples.db_path) as conn, pytest.raises(AuthorizerDenied):
        run(conn, sql)


@pytest.mark.parametrize(
    "sql",
    [
        "WITH v_transactions AS (SELECT * FROM transactions) SELECT 1",
        "/*x*/with t AS (SELECT 1) SELECT * FROM t",
        "SELECT count(1) FROM (SELECT 'x' AS last4) AS t NATURAL JOIN accounts_safe",
    ],
)
def test_no_cte_or_natural_join(samples: Settings, sql: str) -> None:
    with ai_connection(samples.db_path) as conn, pytest.raises(ValueError):
        run(conn, sql)


def test_the_authorizer_alone_stops_a_cte_posing_as_a_safe_view(samples: Settings) -> None:
    # Without run()'s checks: a CTE named like a safe view may not read another column.
    with ai_connection(samples.db_path) as conn:
        raw = conn._conn
        with pytest.raises(sqlite3.DatabaseError):
            raw.execute(
                "WITH transactions_safe AS (SELECT last4 FROM accounts) "
                "SELECT last4 FROM transactions_safe"
            ).fetchall()
        with pytest.raises(sqlite3.DatabaseError):
            raw.execute("WITH base AS (SELECT notes FROM accounts) SELECT * FROM base").fetchall()
        with pytest.raises(TypeError):
            run(raw, "SELECT account_key FROM accounts_safe")  # type: ignore[arg-type]


def test_the_connection_cannot_write(samples: Settings) -> None:
    with ai_connection(samples.db_path) as conn:
        assert not hasattr(conn, "execute")
        with pytest.raises(sqlite3.DatabaseError):
            conn._conn.execute("DELETE FROM ai_facts")
        with pytest.raises(sqlite3.DatabaseError):
            conn._conn.execute("ATTACH ':memory:' AS x")


def test_every_safe_view_reads(samples: Settings) -> None:
    with ai_connection(samples.db_path) as conn:
        for view in sorted(SAFE_VIEWS):
            cols = conn._conn.execute(f"SELECT * FROM {view} LIMIT 1").description
            names = {c[0] for c in cols}
            assert not names & {"last4", "notes", "description", "tags_json", "import_notes"}


def test_safe_views_hide_hidden_rows_and_last4(samples: Settings) -> None:
    with write_connection(samples.db_path) as conn:
        key = conn.execute("SELECT txn_key FROM transactions LIMIT 1").fetchone()[0]
        conn.execute(
            """INSERT INTO transaction_overrides (txn_key, hidden, notes, guard_json, updated_at)
               VALUES (?, 1, 'secret note', '{}', '2026-10-20T00:00:00+00:00')""",
            (key,),
        )
    with ai_connection(samples.db_path) as conn:
        assert run(conn, "SELECT txn_key FROM transactions_safe WHERE txn_key = ?", [key]) == []
        assert run(conn, "SELECT txn_key FROM cashflow_rows_safe WHERE txn_key = ?", [key]) == []


# --------------------------------------------------------------------------
# Facts
# --------------------------------------------------------------------------


def test_facts_are_built_once_per_signature(samples: Settings) -> None:
    sig = ensure_facts(samples.db_path, None)
    conn = connect(samples.db_path)
    built = conn.execute("SELECT built_at FROM ai_facts_state").fetchone()[0]
    assert ensure_facts(samples.db_path, None) == sig
    assert conn.execute("SELECT built_at FROM ai_facts_state").fetchone()[0] == built
    kinds = {r[0] for r in conn.execute("SELECT DISTINCT kind FROM ai_facts")}
    assert kinds >= {
        "account",
        "budget",
        "cashflow_month",
        "debt",
        "holding",
        "meta",
        "networth",
        "networth_summary",
        "plans",
        "recurring",
    }
    # An edit changes the signature, and the facts are rebuilt.
    with write_connection(samples.db_path) as w:
        w.execute(
            """INSERT INTO transaction_events (txn_key, at, kind, via)
               VALUES ('x', '2026-10-20T00:00:00+00:00', 'category_changed', 'edit')"""
        )
    assert signature(conn) != sig
    assert ensure_facts(samples.db_path, None) != sig
    conn.close()
    assert FACTS_VERSION >= 1


def test_a_new_private_list_rebuilds_the_facts(samples: Settings, private_rules) -> None:
    conn = connect(samples.db_path)
    try:
        assert signature(conn, None) != signature(conn, private_rules)
    finally:
        conn.close()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE categories SET name = 'Gasoline' WHERE category_key = 'fuel'",
        "UPDATE categories SET group_key = 'lifestyle' WHERE category_key = 'fuel'",
        "UPDATE category_groups SET name = 'Getting around' WHERE group_key = 'transportation'",
        "UPDATE category_groups SET counts_in_spending = 0 WHERE group_key = 'transportation'",
    ],
)
def test_category_changes_invalidate_facts_signature(db, sql) -> None:
    before = signature(db)
    assert signature(db) == before
    db.execute(sql)
    assert signature(db) != before


def test_category_regrouping_rebuilds_cached_facts(samples: Settings) -> None:
    before = ensure_facts(samples.db_path, None)
    conn = connect(samples.db_path)
    try:

        def groups() -> dict[str, int]:
            raw = conn.execute(
                "SELECT json FROM ai_facts WHERE kind = 'budget' AND key = '2026-07'"
            ).fetchone()[0]
            return {g["key"]: g["actual_cents"] for g in json.loads(raw)["groups"]}

        old = groups()
        assert old["transportation"] > 0
        with write_connection(samples.db_path) as w:
            w.execute("UPDATE categories SET group_key = 'lifestyle' WHERE category_key = 'fuel'")
        after = ensure_facts(samples.db_path, None)
        assert after != before
        new = groups()
        assert new.get("transportation", 0) == 0
        assert new["lifestyle"] == old["lifestyle"] + old["transportation"]
        assert ensure_facts(samples.db_path, None) == after
    finally:
        conn.close()


def test_chat_text_with_an_accounts_last_four_is_hidden() -> None:
    from app.ai.facts import HIDDEN, _Cleaner

    c = _Cleaner(None, frozenset({"4321"}))
    assert c.text("Pay the card ending 4321 first") == HIDDEN
    assert c.text("Statement closes on the 21st") == "Statement closes on the 21st"
    assert c.text("2026-09-30") == "2026-09-30"


def test_scrub_is_the_last_pass_on_what_is_sent(private_rules) -> None:
    from app.ai.postprocess import Scrub

    scrub = Scrub(private_rules, {"4321"})
    out = scrub.json(
        {
            "rows": [{"merchant": "EXAMPLE PRIVATE SERVICE", "amount": "-4321.00"}],
            "note": "card ending 4321",
            "date": "2026-09-30",
            "count": 4321,
            "label": "Sep 4321.5 units",
        }
    )
    assert out["rows"][0] == {"merchant": PRIVATE_LABEL, "amount": "-4321.00"}
    assert out["note"] == f"card ending {MASK}"
    assert (out["date"], out["count"], out["label"]) == ("2026-09-30", 4321, "Sep 4321.5 units")


def test_tool_results_go_through_the_scrub(samples: Settings, private_rules) -> None:
    from app.ai.postprocess import Scrub

    with write_connection(samples.db_path) as w:
        # A name put on the private list after the row was imported.
        w.execute(
            "UPDATE transactions SET merchant = 'Example Private Service' WHERE rowid IN "
            "(SELECT rowid FROM transactions WHERE merchant IS NOT NULL LIMIT 3)"
        )
    ensure_facts(samples.db_path, None)
    with ai_connection(samples.db_path) as conn:
        args = {"limit": 100, "text": "example private"}
        raw = tools.execute(conn, "search_transactions", args).content
        clean = tools.execute(conn, "search_transactions", args, Scrub(private_rules)).content
    assert "Example Private Service" in raw
    assert "Example Private" not in clean and PRIVATE_LABEL in clean


def test_facts_hold_no_last4_notes_or_flagged_text(samples: Settings) -> None:
    conn = connect(samples.db_path)
    with write_connection(samples.db_path) as w:
        w.execute("UPDATE accounts SET last4 = '4321', notes = 'private note 99'")
    ensure_facts(samples.db_path, None)
    blob = " ".join(r[0] for r in conn.execute("SELECT json FROM ai_facts"))
    assert "4321" not in blob and "private note" not in blob
    for (j,) in conn.execute("SELECT json FROM ai_facts"):
        for value in _strings(json.loads(j)):
            assert detect_sensitive(value) == [], value
    conn.close()


def _strings(obj) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _strings(v)]
    return []


def test_facts_match_the_screens(samples: Settings) -> None:
    from app.services import cashflow, networth

    conn = connect(samples.db_path)
    facts = {(k, key): v for k, key, v in build(conn, None)}
    page = networth.accounts_page(conn, "1M")
    assert (
        facts[("networth_summary", "latest")]["net_worth_cents"]
        == (page["summary"]["net_worth_cents"])
    )
    cf = cashflow.cashflow(conn, None, None)
    for m in cf["months"]:
        f = facts[("cashflow_month", m["month"])]
        assert (f["income_cents"], f["spending_cents"]) == (m["income_cents"], m["spending_cents"])
    conn.close()


# --------------------------------------------------------------------------
# Tools
# --------------------------------------------------------------------------


@pytest.fixture
def ai(samples: Settings):
    ensure_facts(samples.db_path, None)
    with ai_connection(samples.db_path) as conn:
        yield conn


def test_tool_definitions_are_sorted_and_stable() -> None:
    defs = tools.definitions()
    names = [d["name"] for d in defs]
    assert names == sorted(names) and len(names) == 8
    assert names == [
        "account_balances",
        "budget_status",
        "cashflow",
        "holdings",
        "net_worth_history",
        "plans_and_recurring",
        "search_transactions",
        "spending_summary",
    ]
    assert json.dumps(defs, sort_keys=True) == json.dumps(tools.definitions(), sort_keys=True)
    for d in defs:
        assert d["input_schema"]["additionalProperties"] is False


def test_every_tool_runs(ai: sqlite3.Connection) -> None:
    for t in tools.TOOLS:
        result = tools.execute(ai, t.name, {})
        assert not result.is_error, (t.name, result.content)
        assert len(result.content) <= tools.RESULT_CHARS
        json.loads(result.content)


def test_search_totals_come_from_sql(ai: sqlite3.Connection, samples: Settings) -> None:
    r = json.loads(
        tools.execute(ai, "search_transactions", {"limit": 5, "direction": "out"}).content
    )
    conn = connect(samples.db_path)
    n, total = conn.execute(
        """SELECT count(*), sum(amount_cents) FROM v_transactions
           WHERE hidden = 0 AND pre_history = 0 AND amount_cents < 0"""
    ).fetchone()
    conn.close()
    assert (r["returned"], r["total_matching"], r["truncated"]) == (5, n, True)
    assert r["sum_amount"] == tools.usd(total) and r["next_offset"] == 5


def test_limits_are_clamped_and_results_fit(ai: sqlite3.Connection) -> None:
    r = json.loads(tools.execute(ai, "search_transactions", {"limit": 100}).content)
    assert r["returned"] <= 100
    big = tools.execute(ai, "search_transactions", {"limit": 1000})
    assert big.is_error is False  # clamped, not refused
    assert json.loads(big.content)["returned"] <= 100


def test_spending_summary_matches_cash_flow(ai: sqlite3.Connection, samples: Settings) -> None:
    from app.services import cashflow

    r = json.loads(
        tools.execute(ai, "spending_summary", {"start_month": "2026-08", "limit": 100}).content
    )
    conn = connect(samples.db_path)
    month = next(
        m for m in cashflow.cashflow(conn, None, None)["months"] if m["month"] == "2026-08"
    )
    conn.close()
    assert r["sum_amount"] == tools.usd(month["spending_cents"])


def test_spending_summary_ranges_and_paging(ai) -> None:
    one = json.loads(tools.execute(ai, "spending_summary", {"end_month": "2026-03"}).content)
    assert (one["from"], one["to"]) == ("2026-03", "2026-03") and one["sum_amount"] != "0.00"
    bad = tools.execute(ai, "spending_summary", {"start_month": "2026-08", "end_month": "2026-03"})
    assert bad.is_error and "after" in bad.content
    args = {"start_month": "2026-01", "end_month": "2026-08", "limit": 3}
    first = json.loads(tools.execute(ai, "spending_summary", args).content)
    assert first["truncated"] and first["next_offset"] == 3
    page2 = tools.execute(ai, "spending_summary", {**args, "offset": 3})
    assert not page2.is_error
    labels = {r["label"] for r in first["rows"]}
    assert labels.isdisjoint(r["label"] for r in json.loads(page2.content)["rows"])


def test_shrinking_a_later_page_keeps_the_offset(monkeypatch) -> None:
    monkeypatch.setattr(tools, "RESULT_CHARS", 2_000)
    rows = [{"merchant": "x" * 40, "n": i} for i in range(80)]
    out = json.loads(tools._shrink(tools._page(rows, 465, 385, "1.00")))
    assert out["next_offset"] == 385 + out["returned"] and out["truncated"]
    assert out["total_matching"] == 465 and out["sum_amount"] == "1.00"


def test_shrinking_reaches_nested_lists_and_keeps_the_newest(monkeypatch) -> None:
    monkeypatch.setattr(tools, "RESULT_CHARS", 1_500)
    months = [{"month": f"2026-{m:02d}", "note": "y" * 200} for m in range(1, 13)]
    out = json.loads(tools._shrink({"plans": {"months": months}}))
    kept = [m["month"] for m in out["plans"]["months"]]
    assert kept[-1] == "2026-12" and kept[0] > "2026-01"
    assert "newest" in out["plans"]["months_note"] and out["plans"]["months_total"] == 12


@pytest.mark.parametrize("term", ["Showing", "Less", "Newest"])
def test_shrink_notes_use_the_tool_scrub(ai, monkeypatch, term: str) -> None:
    full = json.loads(tools.execute(ai, "net_worth_history", {}).content)
    monkeypatch.setattr(tools, "RESULT_CHARS", len(tools.dump(full)) - 1)
    scrub = Scrub(PrivateRules(subscriptions=(term,)))
    result = tools.execute(ai, "net_worth_history", {}, scrub)
    out = json.loads(result.content)
    assert not result.is_error and len(result.content) <= tools.RESULT_CHARS
    assert term.casefold() not in result.content.casefold()
    assert PRIVATE_LABEL in out["points_note"]
    assert out["points_total"] == len(full["points"])
    assert out["points"][-1] == full["points"][-1]


@pytest.mark.parametrize("terms", [("Total",), ("Note",), ("Total", "Note")])
def test_shrink_keeps_totals_when_the_scrub_renames_metadata(ai, monkeypatch, terms) -> None:
    full = json.loads(tools.execute(ai, "net_worth_history", {}).content)
    monkeypatch.setattr(tools, "RESULT_CHARS", 440)
    scrub = Scrub(PrivateRules(subscriptions=terms))
    result = tools.execute(ai, "net_worth_history", {}, scrub)
    out = json.loads(result.content)
    total = len(full["points"])
    assert not result.is_error and len(result.content) <= tools.RESULT_CHARS
    assert 0 < len(out["points"]) < total // 2  # more than one cut
    assert all(term.casefold() not in result.content.casefold() for term in terms)
    total_key, note_key = scrub.text("points_total"), scrub.text("points_note")
    if note_key == total_key:
        note_key += "_2"
    assert out[total_key] == total
    assert f"of {total}." in out[note_key]
    assert scrub.json(out) == out  # a follow-up's scrub keeps both fields


def test_shrink_measures_the_note_after_scrubbing(ai, monkeypatch) -> None:
    full = json.loads(tools.execute(ai, "net_worth_history", {}).content)
    total = len(full["points"])
    keep = total // 2
    half = {
        **full,
        "points": full["points"][-keep:],
        "points_total": total,
        "points_note": f"Showing the newest {keep} of {total}. Ask for less.",
    }
    # The plain note fits exactly. Replacing "Showing" with the private label
    # makes it longer, so the scrub must run before the size check.
    monkeypatch.setattr(tools, "RESULT_CHARS", len(tools.dump(half)))
    scrub = Scrub(PrivateRules(subscriptions=("Showing",)))
    result = tools.execute(ai, "net_worth_history", {}, scrub)
    out = json.loads(result.content)
    assert not result.is_error and len(result.content) <= tools.RESULT_CHARS
    assert 0 < len(out["points"]) < keep
    assert "showing" not in result.content.casefold() and PRIVATE_LABEL in out["points_note"]
    assert out["points_total"] == total


def test_shrink_fallback_uses_the_tool_scrub(ai, monkeypatch) -> None:
    monkeypatch.setattr(tools, "RESULT_CHARS", 200)
    scrub = Scrub(PrivateRules(subscriptions=("Narrow", "Error")))
    result = tools.execute(ai, "net_worth_history", {}, scrub)
    assert not result.is_error and len(result.content) <= tools.RESULT_CHARS
    assert json.loads(result.content) == scrub.json(
        {"error": "The result is too large. Narrow the question."}
    )


def test_apr_reaches_the_model_as_a_percent() -> None:
    assert tools._money({"apr_milli_pct": 24_990, "owed_cents": 100}) == {
        "apr_pct": "24.99",
        "owed": "1.00",
    }


def test_bad_tool_input_is_a_tool_error(ai: sqlite3.Connection) -> None:
    r = tools.execute(ai, "cashflow", {"start_month": "August"})
    assert r.is_error and "start_month" in r.content
    r = tools.execute(ai, "budget_status", {"month": "2020-01"})
    assert r.is_error and "No budget" in r.content
    assert tools.execute(ai, "drop_tables", {}).is_error
    # A name that differs only in case is accepted.
    assert not tools.execute(ai, "CASHFLOW", {}).is_error


# --------------------------------------------------------------------------
# The output filter (PLAN 7.7)
# --------------------------------------------------------------------------


# Test numbers are built at runtime, so the repo scanner never sees a full one.
CARD = " ".join(["4111"] + ["1111"] * 3)


def test_filter_rules(private_rules) -> None:
    assert filter_text("Up 3% — good.") == "Up 3%, good."
    assert filter_text("The 2025–26 plan") == "The 2025-26 plan"
    assert filter_text(f"Card {CARD} was used.") == f"Card {MASK} was used."
    phone = "(608) " + "555-" + "1234"
    assert filter_text(f"Call {phone}.") == f"Call {MASK}."
    assert filter_text("You spent $1,234.56 on 2026-08-31.") == "You spent $1,234.56 on 2026-08-31."
    out = filter_text("You paid Example Private Service $9.99.", private_rules)
    assert out == f"You paid {PRIVATE_LABEL} $9.99."
    assert "EMPLOYER" not in filter_text("At example employer co today.", private_rules).upper()
    # A Cyrillic look-alike letter does not hide the name.
    look_alike = "Ex\u0430mple Priv\u0430te Service"
    assert filter_text(f"You paid {look_alike}.", private_rules) == f"You paid {PRIVATE_LABEL}."


def shown(sf: StreamFilter, pieces: list[str]) -> list[str]:
    """What the browser shows after each event (text appends, replace swaps)."""
    states, now = [], ""
    for piece in [*pieces, None]:
        ev = sf.finish() if piece is None else sf.feed(piece)
        if ev:
            now = now + ev[1] if ev[0] == "text" else ev[1]
            states.append(now)
    return states


def test_stream_filter_never_sends_a_number_half_masked(private_rules) -> None:
    sf = StreamFilter(private_rules)
    head, tail = CARD[:10], CARD[10:]
    states = shown(
        sf, ["You spent $1,2", f"34.56 on dining. Card {head}", f"{tail} was used", ". Done"]
    )
    assert all("4111" not in s for s in states)
    assert states[-1] == sf.text == f"You spent $1,234.56 on dining. Card {MASK} was used. Done"


def test_stream_filter_replaces_text_when_a_number_spans_sentences(private_rules) -> None:
    # Each group ends a "sentence", so each is sent before the next arrives.
    groups = CARD.split(" ")
    sf = StreamFilter(private_rules)
    states = shown(sf, ["Ref ", *(f"{g}. " for g in groups), "Done."])
    assert len(states) > 2
    for state in states:  # every state on screen is the filter's own output
        assert not detect_sensitive(state)
        assert "".join(groups) not in re.sub(r"\D", "", state)
    assert states[-1] == sf.text


def test_stream_filter_replaces_a_private_name_split_by_a_sentence_end(private_rules) -> None:
    sf = StreamFilter(private_rules)
    states = shown(sf, ["You paid Example. ", "Private Service $9.99. ", "Done."])
    assert not any(private_rules.check(s) for s in states)
    assert PRIVATE_LABEL in states[-1] and states[-1] == sf.text


# --------------------------------------------------------------------------
# Caps and the ledger
# --------------------------------------------------------------------------


def _usage(conn: sqlite3.Connection, cost: int, at: datetime, feature: str = "ask") -> None:
    with conn:
        ledger.record(
            conn, ledger.UsageRow(feature, "claude-sonnet-5-5", "medium", cost_micro_usd=cost), at
        )


def test_caps(db: sqlite3.Connection) -> None:
    now = datetime(2026, 10, 20, 18, tzinfo=UTC)
    s = ai_settings.AiSettings(enabled=True)
    ledger.check_caps(db, s, "ask", 10_000, now)  # $0.01 is fine
    # $22.99 spent this month: Ask stops at $23, the review may still run.
    _usage(db, 2299 * 10_000, now - timedelta(days=3))
    with pytest.raises(ledger.CapReached) as e:
        ledger.check_caps(db, s, "ask", 2 * 10_000, now)
    assert e.value.cap == "monthly_ask" and "Nov 1" in e.value.message
    ledger.check_caps(db, s, "review", 50 * 10_000, now)
    with pytest.raises(ledger.CapReached) as e:
        ledger.check_caps(db, s, "review", 202 * 10_000, now)
    assert e.value.cap == "monthly"
    # A daily cap of $0.01 blocks the next question (PLAN 10 Phase 6 "done when").
    low = ai_settings.AiSettings(enabled=True, daily_cap_cents=1, monthly_cap_cents=10_000)
    with pytest.raises(ledger.CapReached) as e:
        ledger.check_caps(db, low, "ask", 20_000, now + timedelta(days=20))
    assert e.value.cap == "daily"


def test_daily_question_count(db: sqlite3.Connection) -> None:
    now = datetime.now(UTC)
    s = ai_settings.AiSettings(enabled=True, daily_questions=1)
    ledger.check_caps(db, s, "ask", 0, now, new_question=True)
    with db:
        db.execute(
            """INSERT INTO ai_threads (thread_id, user_id, created_at, updated_at, model, effort,
                   title) VALUES ('t', NULL, ?, ?, 'claude-sonnet-5-5', 'medium', 'q')""",
            (ai_settings.iso(now), ai_settings.iso(now)),
        )
        db.execute(
            """INSERT INTO ai_messages (thread_id, seq, role, kind, content_json, created_at)
               VALUES ('t', 1, 'user', 'question', '[]', ?)""",
            (ai_settings.iso(now),),
        )
    with pytest.raises(ledger.CapReached) as e:
        ledger.check_caps(db, s, "ask", 0, now, new_question=True)
    assert e.value.cap == "daily_questions"


def test_usage_page(db: sqlite3.Connection) -> None:
    now = datetime(2026, 10, 20, 18, tzinfo=UTC)
    with db:
        ledger.record(
            db,
            ledger.UsageRow(
                "ask",
                "claude-sonnet-5-5",
                "medium",
                tokens=Tokens(input=100, write_5m=900, cache_read=9000, output=50),
                cost_micro_usd=13_000_000,
            ),
            now,
        )
    page = ledger.usage_page(db, ai_settings.AiSettings(), now)
    assert page["month_cost_micro_usd"] == 13_000_000
    assert page["month_used_pct"] == "52.0" and page["warning_pct"] == 50
    assert page["cache_hit_pct"] == "90.0"
    assert page["calls"][0]["feature"] == "ask"


# --------------------------------------------------------------------------
# The call wrapper: spend limits, retries, usage rows (PLAN 7.6)
# --------------------------------------------------------------------------


def _call(settings: Settings, feature: str = "review"):
    info = ai_client.CallInfo(feature, "claude-opus-5-5", "high")

    async def request(client):
        return await client.messages.create(model="claude-opus-5-5", max_tokens=10, messages=[])

    return asyncio.run(ai_client.call(settings, info, request))


def _rows(settings: Settings) -> list[sqlite3.Row]:
    conn = connect(settings.db_path)
    rows = conn.execute("SELECT * FROM ai_usage ORDER BY id").fetchall()
    conn.close()
    return rows


def test_a_call_writes_its_usage(with_key, settings, db, fake, no_sleep) -> None:
    enable(settings)
    s = get_settings()
    fake.script = [
        message([text("ok")], model="claude-opus-5-5", input_tokens=100, output_tokens=10)
    ]
    _call(s)
    (row,) = _rows(s)
    assert (row["feature"], row["input_tokens"], row["output_tokens"]) == ("review", 100, 10)
    assert row["cost_micro_usd"] == 100 * 4 + 10 * 20 and row["error"] is None


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (
            bad_request("You have reached your specified workspace API usage limits. ..."),
            "workspace_limit",
        ),
        (bad_request("You have reached your specified API usage limits. ..."), "org_limit"),
        (rate_limited(code="enforced_spend_limit_reached"), "tier_cap"),
    ],
)
def test_spend_limits_turn_ai_off(with_key, settings, db, fake, no_sleep, error, reason) -> None:
    enable(settings)
    s = get_settings()
    fake.script = [error]
    with pytest.raises(ai_client.AiError) as e:
        _call(s)
    assert e.value.kind == "spend_limit" and no_sleep == []
    block = ai_settings.load_block(db)
    assert block is not None and block.reason == reason
    assert ai_settings.status(db, s)["reason"] == "spend_limit"
    # No more calls until the block ends.
    with pytest.raises(ai_client.AiError) as e:
        _call(s)
    assert e.value.kind == "unavailable"
    assert [r["error"] for r in _rows(s)] == [type(error).__name__]


def test_rate_limit_retries_once_after_retry_after(with_key, settings, db, fake, no_sleep) -> None:
    enable(settings)
    s = get_settings()
    fake.script = [rate_limited("7"), message([text("ok")], model="claude-opus-5-5")]
    _call(s)
    assert no_sleep == [7.0]
    assert [r["error"] for r in _rows(s)] == ["RateLimitError", None]
    fake.script = [rate_limited(), message([text("ok")])]
    with pytest.raises(ai_client.AiError):  # no retry-after: no retry
        _call(s)


def test_server_errors_retry_once_then_fail(with_key, settings, db, fake, no_sleep) -> None:
    enable(settings)
    s = get_settings()
    fake.script = [server_error(), server_error()]
    with pytest.raises(ai_client.AiError) as e:
        _call(s)
    assert e.value.kind == "failed" and e.value.message == ai_client.FAILED_TEXT
    assert no_sleep == [ai_client.RETRY_PAUSE]
    assert len(_rows(s)) == 2


def test_nothing_is_called_when_off_or_capped(with_key, settings, db, fake) -> None:
    s = get_settings()
    with pytest.raises(ai_client.AiError) as e:
        _call(s)
    assert e.value.kind == "unavailable"
    enable(settings, monthly_cap_cents=0)
    with pytest.raises(ai_client.AiError) as e:
        asyncio.run(
            ai_client.call(
                s,
                ai_client.CallInfo("review", "claude-opus-5-5", "high", estimate_micro=1),
                lambda c: c.messages.create(),
            )
        )
    assert e.value.kind == "cap"
    assert fake.requests == []
