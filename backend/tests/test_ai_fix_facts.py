"""Phase 6 review fixes: facts, tools, the monthly review and categorizing.
Fake client only (ai_fakes.py), never a key or the network."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta

import pytest
from ai_fakes import FakeClient, parsed, server_error

from app import config
from app.ai import categorize, ledger, review, tools
from app.ai import client as ai_client
from app.ai import settings as ai_settings
from app.ai.facts import ensure_facts
from app.ai.postprocess import PRIVATE_LABEL
from app.ai.safe import ai_connection
from app.config import Settings, get_settings
from app.db.connect import connect, write_connection
from app.jobs.scheduler import JobFailed

TODAY = date(2026, 10, 20)
LAST4 = "7395"
TAG = f"Ending {LAST4}"


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)


@pytest.fixture
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-canary-key-for-tests-7f3a")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9")
    get_settings.cache_clear()


@pytest.fixture
def fake() -> FakeClient:
    client = FakeClient()
    ai_client.set_client_factory(lambda _s: client)
    yield client
    ai_client.set_client_factory(None)


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def skip(_s: float) -> None:
        return None

    monkeypatch.setattr(ai_client.asyncio, "sleep", skip)


def enable(**fields) -> None:
    with write_connection(get_settings().db_path) as conn:
        ai_settings.save(conn, ai_settings.AiSettings(enabled=True, **fields), None)


def call(conn, name: str, args: dict) -> dict:
    result = tools.execute(conn, name, args)
    assert not result.is_error, result.content
    return json.loads(result.content)


@pytest.fixture
def ai(samples: Settings):
    ensure_facts(samples.db_path, None)
    with ai_connection(samples.db_path) as conn:
        yield conn


def _keys(obj) -> list[str]:
    """Every dict key at any depth."""
    if isinstance(obj, dict):
        return list(obj) + [k for v in obj.values() for k in _keys(v)]
    if isinstance(obj, list):
        return [k for v in obj for k in _keys(v)]
    return []


def good_review() -> review.ReviewOut:
    return review.ReviewOut(
        summary="A steady month.",
        changes=["Spending fell a little."],
        anomalies=[],
        actions=["Pay the store card.", "Check the van loan.", "Move $500 to savings."],
    )


# --------------------------------------------------------------------------
# Tools: category filter, month ranges, estimates, plan basis, bad sizes
# --------------------------------------------------------------------------


def test_a_category_filter_counts_that_category_only(ai, samples: Settings) -> None:
    from app.services import cashflow

    conn = connect(samples.db_path)
    app_total = {
        (k, s, e): cashflow.category_detail(conn, k, s, e)["total_cents"]
        for k, s, e in (("fitness", "2026-08", "2026-08"), ("misc", "2026-01", "2026-09"))
    }
    conn.close()
    aug = {"start_month": "2026-08", "end_month": "2026-08"}
    r = call(ai, "spending_summary", {**aug, "category": "fitness"})
    assert r["sum_amount"] == tools.usd(app_total[("fitness", "2026-08", "2026-08")])
    assert r["matched_categories"] == ["Fitness"]
    assert [row["label"] for row in r["rows"]] == ["Fitness"]
    by_month = call(ai, "spending_summary", {**aug, "category": "Fitness", "group_by": "month"})
    assert by_month["sum_amount"] == r["sum_amount"]
    r = call(
        ai,
        "spending_summary",
        {"start_month": "2026-01", "end_month": "2026-09", "category": "Misc"},
    )
    assert r["sum_amount"] == tools.usd(app_total[("misc", "2026-01", "2026-09")])
    assert r["matched_categories"] == ["Misc"]
    # A group's exact name means the whole group.
    group = call(ai, "spending_summary", {**aug, "category": "Services and fitness"})
    assert {"Fitness", "Phone", "Medical"} <= set(group["matched_categories"])
    # Search uses the same rule.
    s = call(
        ai,
        "search_transactions",
        {"category": "Fitness", "start_date": "2026-08-01", "end_date": "2026-08-31"},
    )
    assert {row["category"] for row in s["rows"]} == {"Fitness"}
    assert s["matched_categories"] == ["Fitness"]
    assert s["sum_amount"] == tools.usd(-app_total[("fitness", "2026-08", "2026-08")])
    # A part of a name still works, and nothing at all is a tool error.
    assert (
        "Fitness" in call(ai, "spending_summary", {**aug, "category": "fitn"})["matched_categories"]
    )
    bad = tools.execute(ai, "spending_summary", {**aug, "category": "Spaceships"})
    assert bad.is_error and "No category" in bad.content


def test_a_start_month_alone_runs_to_the_latest_complete_month(ai, samples: Settings) -> None:
    from app.services import cashflow

    r = call(ai, "spending_summary", {"start_month": "2026-05"})
    assert (r["from"], r["to"]) == ("2026-05", "2026-08")
    conn = connect(samples.db_path)
    app = cashflow.breakdown(conn, "2026-05", None, "category")
    conn.close()
    assert r["sum_amount"] == tools.usd(app["total_cents"])
    # A start after the latest complete month runs through the current month.
    later = call(ai, "spending_summary", {"start_month": "2026-09"})
    assert (later["from"], later["to"]) == ("2026-09", "2026-10")
    # An end month alone is still that one month.
    one = call(ai, "spending_summary", {"end_month": "2026-03"})
    assert (one["from"], one["to"]) == ("2026-03", "2026-03")


def test_an_estimated_balance_names_its_basis(samples: Settings) -> None:
    from app.market.common import Bar
    from app.market.store import upsert_bars

    conn = connect(samples.db_path)
    price, stmt = conn.execute(
        """SELECT h.price_micro, b.balance_cents FROM holdings AS h
           JOIN balances AS b ON b.account_key = h.account_key AND b.as_of = '2026-09-30'
           WHERE h.account_key = 'lakeview-brokerage' AND h.holding_key = 'broad-market'
           ORDER BY h.as_of DESC LIMIT 1"""
    ).fetchone()
    conn.close()
    bars = [
        Bar("2026-09-30", price),
        Bar("2026-10-01", price),
        Bar("2026-10-16", price + 3_000_000),
    ]
    with write_connection(samples.db_path) as w:
        upsert_bars(w, "BRDMKT", bars, "tiingo", "2026-10-20T12:00:00+00:00")
    ensure_facts(samples.db_path, None)
    with ai_connection(samples.db_path) as ai:
        r = call(ai, "account_balances", {"group": "investments"})
        everything = call(ai, "account_balances", {})
    (row,) = [a for a in r["accounts"] if a["estimated"] and a["basis"] == "estimate (price)"]
    assert row["balance"] != tools.usd(stmt)
    assert row["as_of"] == "2026-10-20" and row["estimate_through"] == "2026-10-16"
    assert row["based_on"] == {
        "as_of": "2026-09-30",
        "balance": tools.usd(stmt),
        "basis": "statement",
    }
    # A real balance keeps its own date and basis.
    real = [a for a in everything["accounts"] if not a["estimated"] and a["balance"]]
    assert real
    for a in real:
        assert not (a["basis"] or "").startswith("estimate") and "based_on" not in a


def test_cash_flow_says_planned_spending_includes_saved(ai) -> None:
    r = call(ai, "cashflow", {"start_month": "2026-10"})
    (month,) = r["months"]
    plan = month["plan"]
    assert "spending" not in plan
    assert plan["spending_incl_saved_invested"] is not None
    assert "includes saved and invested" in r["about"]
    assert "plan_vs_actual" in r["about"]


@pytest.mark.parametrize(
    "args", [{"min_amount": "inf"}, {"max_amount": 1e300}, {"max_amount": 1e17}]
)
def test_an_endless_or_huge_size_is_a_tool_error(ai, args: dict) -> None:
    result = tools.execute(ai, "search_transactions", args)
    assert result.is_error and "Bad input" in result.content


def test_account_notes_never_reach_the_ai(samples: Settings) -> None:
    """The drawer shows the chat's notes and registry names. The AI facts leave them out."""
    ensure_facts(samples.db_path, None)
    conn = connect(samples.db_path)
    texts = [
        r[0]
        for r in conn.execute(
            """SELECT notes FROM accounts WHERE notes IS NOT NULL
               UNION ALL SELECT policy_note FROM accounts WHERE policy_note IS NOT NULL
               UNION ALL SELECT advisor FROM accounts WHERE advisor IS NOT NULL
               UNION ALL SELECT custodian FROM accounts
                   WHERE custodian IS NOT NULL AND custodian != institution
               UNION ALL SELECT servicer FROM accounts
                   WHERE servicer IS NOT NULL AND servicer != institution"""
        )
    ]
    facts = [(k, json.loads(j)) for k, j in conn.execute("SELECT kind, json FROM ai_facts")]
    conn.close()
    assert len(texts) >= 4
    blob = "\n".join(json.dumps(f) for _k, f in facts)
    for text in texts:
        assert text not in blob, text
    shown = [f for k, f in facts if k in ("account", "debt")]
    assert shown
    assert not {"notes", "servicer", "advisor", "custodian", "dormant"} & set(_keys(shown))


# --------------------------------------------------------------------------
# The monthly review
# --------------------------------------------------------------------------


def test_review_inputs_are_all_in_dollars(ai, samples: Settings) -> None:
    month = "2026-08"
    with write_connection(samples.db_path) as w:
        (text,) = w.execute(
            "SELECT json FROM ai_facts WHERE kind = 'plan_vs_actual' AND key = '2026-10'"
        ).fetchone()
        fact = {**json.loads(text), "month": month}
        w.execute("DELETE FROM ai_facts WHERE kind = 'plan_vs_actual' AND key = ?", (month,))
        w.execute(
            "INSERT INTO ai_facts (kind, key, json) VALUES ('plan_vs_actual', ?, ?)",
            (month, json.dumps(fact)),
        )
    data = review.inputs(ai)
    assert data["month"] == month and data["plan_vs_actual"] is not None
    keys = _keys(data)
    assert not [k for k in keys if k.endswith("_cents") or k.endswith("_milli_pct")]
    line = fact["lines"][0]
    sent = data["plan_vs_actual"]["lines"][0]
    assert sent["label"] == line["label"] and sent["plan"] == tools.usd(line["plan_cents"])


def _reviews() -> list[tuple]:
    conn = connect(get_settings().db_path)
    rows = conn.execute("SELECT month, status FROM ai_reviews").fetchall()
    conn.close()
    return [tuple(r) for r in rows]


def test_a_transient_failure_is_tried_again_up_to_three_times(
    key, samples, fake, no_sleep, monkeypatch
) -> None:
    monkeypatch.setattr(review, "RETRY_AFTER", timedelta(0))  # spacing: test_ai_fix3_review
    enable()
    s = get_settings()
    for tries in (1, 2, 3):
        fake.script = [server_error(), server_error()]
        if tries < 3:
            assert review.review_job_run(s) == {"status": "failed", "reason": "transient"}
        else:  # the last try is a failed job run (test_ai_fix4_review)
            with pytest.raises(JobFailed):
                review.review_job_run(s)
        assert fake.script == [], tries
        conn = connect(s.db_path)
        (summary,) = conn.execute("SELECT summary FROM ai_reviews").fetchone()
        conn.close()
        assert json.loads(summary) == {"reason": "transient", "tries": tries}
    # Three tries for the month: the job stops.
    fake.script = [parsed(good_review())]
    calls = len(fake.requests)
    assert review.review_job_run(s) is None
    assert len(fake.requests) == calls and _reviews() == [("2026-08", "failed")]
    # An admin can still write it.
    assert asyncio.run(review.write_review(s, force=True))["status"] == "ready"


def test_a_transient_failure_then_success(key, samples, fake, no_sleep, monkeypatch) -> None:
    monkeypatch.setattr(review, "RETRY_AFTER", timedelta(0))
    enable()
    s = get_settings()
    fake.script = [server_error(), server_error()]
    assert review.review_job_run(s)["reason"] == "transient"
    fake.script = [parsed(good_review())]
    assert review.review_job_run(s)["status"] == "ready"
    assert _reviews() == [("2026-08", "ready")]
    assert review.review_job_run(s) is None


@pytest.mark.parametrize(
    "reply,reason",
    [
        (lambda: parsed(good_review().model_copy(update={"actions": ["One."]})), "schema"),
        (lambda: parsed(good_review(), stop_reason="max_tokens"), "max_tokens"),
        (lambda: parsed(good_review(), stop_reason="refusal"), "refusal"),
    ],
)
def test_other_failures_are_final(key, samples, fake, reply, reason) -> None:
    enable()
    s = get_settings()
    fake.script = [reply()]
    with pytest.raises(JobFailed) as exc:
        review.review_job_run(s)
    assert exc.value.summary["reason"] == reason
    fake.script = [parsed(good_review())]
    assert review.review_job_run(s) is None
    assert len(fake.requests) == 1


def _spend(micro: int) -> None:
    with write_connection(get_settings().db_path) as conn:
        ledger.record(
            conn,
            ledger.UsageRow("ask", "claude-sonnet-5-5", "medium", cost_micro_usd=micro),
            datetime.now(UTC),
        )


def test_the_review_estimate_is_checked_against_the_cap(key, samples, fake) -> None:
    # $24.99 of $25 spent. The review's estimate (whole input, longest reply on
    # Opus) is far more than the cent left, so nothing is sent.
    enable(monthly_cap_cents=2500, daily_cap_cents=100_000)
    _spend(2500 * 10_000 - 10_000)
    fake.script = [parsed(good_review())]
    r = asyncio.run(review.write_review(get_settings()))
    assert (r["status"], r["reason"]) == ("skipped", "cap")
    assert fake.requests == []


def _privatize(db_path) -> None:
    """A name put on the private list after import, and a last four in an account name."""
    with write_connection(db_path) as w:
        w.execute("UPDATE transactions SET merchant = 'Example Private Service'")
        w.execute(f"UPDATE accounts SET last4 = '{LAST4}', display_name = display_name || ' {TAG}'")


def test_the_review_payload_is_scrubbed(key, use_private_rules, samples, fake) -> None:
    enable()
    _privatize(get_settings().db_path)
    fake.script = [parsed(good_review())]
    assert asyncio.run(review.write_review(get_settings()))["status"] == "ready"
    content = fake.requests[0][1]["messages"][0]["content"]
    assert "example private" not in content.casefold() and PRIVATE_LABEL in content
    assert LAST4 not in content


# --------------------------------------------------------------------------
# Categorizing
# --------------------------------------------------------------------------


def test_categorizing_sends_the_category_enum(key, samples, fake) -> None:
    enable()
    fake.script = [parsed(categorize.SuggestionsOut(suggestions=[]), model="claude-haiku-5-5")]
    asyncio.run(categorize.suggest(get_settings(), None))
    kind, params = fake.requests[0]
    assert kind == "create"  # like the review: the reply is parsed after the call
    schema = params["output_config"]["format"]["schema"]
    enum = schema["$defs"]["Suggestion"]["properties"]["category_key"]["enum"]
    payload = json.loads(params["messages"][0]["content"])
    assert enum == [c["key"] for c in payload["categories"]]
    assert {"dining", "uncategorized"} <= set(enum)


def test_the_categorize_estimate_is_checked_against_the_cap(key, samples, fake) -> None:
    # A tenth of a cent left. One Haiku chunk estimates more than that.
    enable(monthly_cap_cents=2500, daily_cap_cents=100_000)
    _spend(2500 * 10_000 - 1_000)
    fake.script = [parsed(categorize.SuggestionsOut(suggestions=[]), model="claude-haiku-5-5")]
    with pytest.raises(ai_client.AiError) as err:
        asyncio.run(categorize.suggest(get_settings(), None))
    assert err.value.kind == "cap"
    assert fake.requests == []


def test_the_categorize_payload_is_scrubbed(key, use_private_rules, samples, fake) -> None:
    enable()
    _privatize(get_settings().db_path)
    fake.script = [
        parsed(categorize.SuggestionsOut(suggestions=[]), model="claude-haiku-5-5")
    ] * categorize.MAX_CHUNKS
    asyncio.run(categorize.suggest(get_settings(), None))
    content = fake.requests[0][1]["messages"][0]["content"]
    assert "example private" not in content.casefold() and PRIVATE_LABEL in content
    assert LAST4 not in content
