"""Phase 6 follow-ups (PLAN 7.1 item 2, 7.4): Ask sees utility bills and
contributions through plans_and_recurring, and a review is written again when
a later import changes the month's inputs. Fake client only (ai_fakes.py)."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, date, datetime, timedelta

import pytest
from ai_fakes import FakeClient, parsed, server_error
from test_ai_features import H
from test_ai_fix4_review import _age, _job_runs
from test_ai_fix_facts import _keys, _reviews, _spend, call, enable, good_review

from app import config
from app.ai import ask, context, review, tools
from app.ai import client as ai_client
from app.ai import settings as ai_settings
from app.ai.facts import HIDDEN, ensure_facts
from app.ai.postprocess import PRIVATE_LABEL, Scrub
from app.ai.safe import ai_connection
from app.config import Settings, get_settings
from app.db.connect import connect, write_connection
from app.importer.sanitize import PrivateRules
from app.jobs.scheduler import Job, Scheduler, ai_review_job
from app.services.balances import clear_cache

TODAY = date(2026, 10, 20)


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


def _ask(db_path, args: dict) -> dict:
    """Rebuild the facts when the data changed, then run plans_and_recurring."""
    ensure_facts(db_path, None)
    with ai_connection(db_path) as conn:
        return call(conn, "plans_and_recurring", args)


def _micro(v: int | None) -> str | None:
    return tools._micro(v)


def _utility(r: dict, key: str) -> dict:
    """One utility from a utilities result, with its own bills and past-due bills."""
    found = [x for x in r["utilities"]["utilities"] if x["utility_key"] == key]
    assert len(found) == 1, key
    return found[0]


# --------------------------------------------------------------------------
# Utilities
# --------------------------------------------------------------------------


def test_ask_sees_utility_bills_like_the_utilities_page(samples: Settings, admin) -> None:
    page = {x["utility_key"]: x for x in admin.get("/api/utilities").json()["utilities"]}
    assert set(page) == {"city-power", "town-water"}
    for k, s in page.items():
        # Named by part of the provider, any case: the last 12 bills.
        r = _ask(samples.db_path, {"show": "utilities", "utility": s["provider"][-5:].upper()})
        assert set(r) == {"utilities"}
        assert [x["utility_key"] for x in r["utilities"]["utilities"]] == [k]
        u = _utility(r, k)
        assert u["provider"] == s["provider"]
        assert u["services"] == s["services"]
        assert u["bill_count"] == s["bill_count"]
        assert u["last_bill_date"] == s["last_bill_date"]
        assert u["last_total"] == tools.usd(s["last_total_cents"])
        assert u["last_status"] == s["last_status"]
        assert u["next_bill_date"] == s["next_bill_date"]
        assert u["next_bill_late"] == s["next_bill_late"]
        # The 12-month average.
        assert u["avg_bill_12m"] == tools.usd(s["avg_bill_cents"])
        assert u["bills_in_avg"] == s["bills_in_avg"]
        assert u["total_12m"] == tools.usd(s["total_12m_cents"])

        d = admin.get(f"/api/utilities/{k}").json()
        bills = list(reversed(d["bills"]))  # newest first, like the tool
        yoy = {y["bill_key"]: y for y in d["yoy"]}
        assert len(u["bills"]) == min(len(bills), 12)
        assert u["past_due"] == [
            {
                "bill_date": b["bill_date"],
                "due_by": b["due_by"],
                "total": tools.usd(b["total_cents"]),
            }
            for b in d["bills"]
            if b["status"] == "past_due"
        ]
        for mine, b in zip(u["bills"], bills, strict=False):
            y = yoy[b["bill_key"]]
            assert mine["bill_date"] == b["bill_date"]
            assert mine["period_end"] == b["period_end"]
            assert mine["days"] == b["days"]
            assert mine["total"] == tools.usd(b["total_cents"])
            assert mine["due_date"] == b["due_date"]
            assert mine["due_by"] == b["due_by"]
            assert mine["status"] == b["status"]
            paid = b["payment"]
            assert mine["paid_date"] == (paid["txn_date"] if paid else None)
            assert mine["paid_amount"] == (tools.usd(paid["amount_cents"]) if paid else None)
            assert mine["total_per_day"] == tools.usd(y["total_per_day_cents"]["value"])
            assert mine["last_year_period_end"] == y["last_year_period_end"]
            assert mine["total_last_year"] == tools.usd(y["total_cents"]["last_year"])
            assert mine["total_change_pct"] == y["total_cents"]["change_pct"]
            assert len(mine["services"]) == len(y["services"])
            for svc, ys in zip(mine["services"], y["services"], strict=True):
                line = next(ln for ln in b["lines"] if ln["service"] == ys["service"])
                assert (svc["service"], svc["unit"]) == (ys["service"], ys["unit"])
                assert svc["usage"] == _micro(line["usage_micro"])
                assert svc["usage_last_year"] == _micro(ys["usage_micro"]["last_year"])
                assert svc["usage_change_pct"] == ys["usage_micro"]["change_pct"]
                assert svc["per_day"] == _micro(ys["per_day_micro"]["value"])
                assert svc["per_day_last_year"] == _micro(ys["per_day_micro"]["last_year"])
                assert svc["per_day_change_pct"] == ys["per_day_micro"]["change_pct"]
                assert svc["charges"] == tools.usd(line["charges_cents"])
        if k == "city-power":
            # A bill with a year-ago bill to compare with.
            assert u["bills"][0]["last_year_period_end"] is not None
            assert u["bills"][0]["total_change_pct"] is not None

    # Unnamed: every utility, with its newest 3 bills.
    every = _ask(samples.db_path, {"show": "utilities"})
    assert [x["utility_key"] for x in every["utilities"]["utilities"]] == list(page)
    for k in page:
        named = _ask(samples.db_path, {"show": "utilities", "utility": k})
        assert _utility(every, k)["bills"] == _utility(named, k)["bills"][:3]


@pytest.mark.parametrize(
    "args",
    [
        {"show": "utilities", "utility": "no such provider"},
        {"show": "utilities", "utility": " "},
        {"show": "plans", "utility": "power"},
    ],
)
def test_a_bad_utility_name_is_a_tool_error(samples: Settings, args: dict) -> None:
    ensure_facts(samples.db_path, None)
    with ai_connection(samples.db_path) as conn:
        assert tools.execute(conn, "plans_and_recurring", args).is_error


def test_gas_per_degree_day_shows_like_the_utilities_page(samples: Settings, admin) -> None:
    with write_connection(samples.db_path) as w:
        w.execute(
            "UPDATE utility_bills SET heating_degree_days = 300 + days WHERE utility_key = ?",
            ("city-power",),
        )
    named = _ask(samples.db_path, {"show": "utilities", "utility": "power"})
    bills = _utility(named, "city-power")["bills"]
    d = admin.get("/api/utilities/city-power").json()
    yoy = {y["period_end"]: y for y in d["yoy"]}
    checked = 0
    for b in bills:
        for svc in b["services"]:
            ys = next(x for x in yoy[b["period_end"]]["services"] if x["service"] == svc["service"])
            if svc["service"] == "gas":
                assert svc["per_degree_day"] == _micro(ys["per_degree_day_micro"]["value"])
                assert svc["per_degree_day_change_pct"] == ys["per_degree_day_micro"]["change_pct"]
                checked += 1
            else:
                assert "per_degree_day" not in svc
    assert checked == len(bills)
    ensure_facts(samples.db_path, None)
    with ai_connection(samples.db_path) as conn:
        out = tools.execute(conn, "plans_and_recurring", {"show": "utilities", "utility": "power"})
    assert len(out.content) <= tools.RESULT_CHARS and "bills_note" not in out.content


def test_a_result_too_large_cuts_old_bills_never_a_utility(
    samples: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a small limit, each utility keeps its newest bills, and no utility goes."""
    monkeypatch.setattr(tools, "RESULT_CHARS", 4_000)
    ensure_facts(samples.db_path, None)
    with ai_connection(samples.db_path) as conn:
        full = {
            k: call(conn, "plans_and_recurring", {"show": "utilities", "utility": k})
            for k in ("city-power", "town-water")
        }
        for args in ({"show": "utilities"}, {"show": "utilities", "utility": "power"}):
            out = tools.execute(conn, "plans_and_recurring", args)
            assert not out.is_error and len(out.content) <= 4_000, args
            got = json.loads(out.content)["utilities"]["utilities"]
            assert "utilities_note" not in out.content  # no utility was cut
            for u in got:
                bills = _utility(full[u["utility_key"]], u["utility_key"])["bills"]
                assert u["bills"] == bills[: len(u["bills"])]  # the newest, in order
            power = next(u for u in got if u["utility_key"] == "city-power")
            assert power["bills_note"].startswith(f"Showing the newest {len(power['bills'])} of")


def test_the_cut_falls_back_when_the_scrub_renames_bills(
    samples: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A private term "Bills" renames the "bills" key in the scrub. The cut cannot
    find its shape, so the general cut takes over, and nothing raises."""
    monkeypatch.setattr(tools, "RESULT_CHARS", 5_000)
    ensure_facts(samples.db_path, None)
    scrub = Scrub(PrivateRules(subscriptions=("Bills",)), ())
    with ai_connection(samples.db_path) as conn:
        for args in ({"show": "utilities"}, {"show": "utilities", "utility": "power"}):
            out = tools.execute(conn, "plans_and_recurring", args, scrub)
            assert not out.is_error and len(out.content) <= 5_000, args
            json.loads(out.content)
            assert "bills" not in out.content.casefold()


def test_the_cut_note_goes_through_the_scrub(
    samples: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cut's note is added after the scrub, so it is scrubbed itself: a
    private term "Newest" does not come back in "Showing the newest 6 of 12"."""
    monkeypatch.setattr(tools, "RESULT_CHARS", 6_000)
    ensure_facts(samples.db_path, None)
    scrub = Scrub(PrivateRules(subscriptions=("Newest",)), ())
    with ai_connection(samples.db_path) as conn:
        out = tools.execute(
            conn, "plans_and_recurring", {"show": "utilities", "utility": "power"}, scrub
        )
    power = _utility(json.loads(out.content), "city-power")
    assert power["bills_total"] == 12 and len(power["bills"]) < 12  # the cut ran
    assert "newest" not in out.content.casefold()
    assert PRIVATE_LABEL in power["bills_note"]


def _bills_of(admin, keys: list[str]) -> dict[str, list[tuple]]:
    """Each utility's newest 3 bills from the Utilities API, as (date, total)."""
    return {
        k: [
            (b["bill_date"], tools.usd(b["total_cents"]))
            for b in reversed(admin.get(f"/api/utilities/{k}").json()["bills"])
        ][:3]
        for k in keys
    }


def test_two_utilities_made_private_later_keep_their_own_bills(samples: Settings, admin) -> None:
    """Both names join the private list after the facts were built: the last
    scrub makes both utilities "personal subscription", and each still carries
    its own bills, since nothing links them by name."""
    keys = [x["utility_key"] for x in admin.get("/api/utilities").json()["utilities"]]
    real = _bills_of(admin, keys)
    ensure_facts(samples.db_path, None)
    later = Scrub(PrivateRules(subscriptions=("City Power", "Town Water")), ())
    with ai_connection(samples.db_path) as conn:
        out = tools.execute(conn, "plans_and_recurring", {"show": "utilities"}, later)
    got = json.loads(out.content)["utilities"]["utilities"]
    assert {u["utility_key"] for u in got} == {PRIVATE_LABEL}
    assert "city" not in out.content.casefold() and "town" not in out.content.casefold()
    assert [[(b["bill_date"], b["total"]) for b in u["bills"]] for u in got] == [
        real[k] for k in keys
    ]


def test_two_private_utilities_keep_their_own_bills(
    use_private_rules, samples: Settings, admin
) -> None:
    """Both utilities private in the data: names and keys are hidden alike in the
    facts, and each utility still carries its own bills."""
    from app.importer.runner import private_rules_for

    keys = [x["utility_key"] for x in admin.get("/api/utilities").json()["utilities"]]
    real = _bills_of(admin, keys)
    with write_connection(samples.db_path) as w:
        w.execute(
            """UPDATE utility_bills SET provider = 'Example Private Service',
                   utility_key = 'example-private-service-' || utility_key"""
        )
    private = private_rules_for(get_settings())
    ensure_facts(samples.db_path, private)
    with ai_connection(samples.db_path) as conn:
        out = tools.execute(conn, "plans_and_recurring", {"show": "utilities"}, Scrub(private))
    got = json.loads(out.content)["utilities"]["utilities"]
    assert [(u["provider"], u["utility_key"]) for u in got] == [(HIDDEN, HIDDEN)] * 2
    assert "example private" not in out.content.casefold()
    assert [[(b["bill_date"], b["total"]) for b in u["bills"]] for u in got] == [
        real[k] for k in keys
    ]


def test_a_past_due_utility_bill_shows(samples: Settings, admin) -> None:
    """The Utilities checks test's past-due case: September's payment is missing."""
    with write_connection(samples.db_path) as w:
        w.execute(
            "DELETE FROM transactions WHERE recurring_key = ? AND txn_date = ?",
            ("city-power", "2026-09-18"),
        )
    clear_cache()
    checks = admin.get("/api/checks").json()["utility_past_due"]
    assert [c["bill_key"] for c in checks] == ["city-power:2026-09-03"]
    r = _ask(samples.db_path, {"show": "utilities"})
    power = _utility(r, "city-power")
    assert power["past_due_count"] == 1
    assert [(p["bill_date"], p["due_by"], p["total"]) for p in power["past_due"]] == [
        (c["bill_date"], c["due_date"], tools.usd(c["total_cents"])) for c in checks
    ]
    assert power["last_status"] == "past_due"


def test_a_private_provider_name_is_hidden(use_private_rules, samples: Settings) -> None:
    from app.importer.runner import private_rules_for

    with write_connection(samples.db_path) as w:
        w.execute("UPDATE utility_bills SET provider = 'Example Private Service'")
    ensure_facts(samples.db_path, private_rules_for(get_settings()))
    with ai_connection(samples.db_path) as conn:
        r = call(conn, "plans_and_recurring", {"show": "utilities"})
        text = context.build(conn)
    assert {u["provider"] for u in r["utilities"]["utilities"]} == {HIDDEN}
    assert "example private" not in json.dumps(r).casefold()
    assert "example private" not in text.casefold()


def test_the_utilities_result_fits_and_keeps_the_newest_bills(samples: Settings) -> None:
    ensure_facts(samples.db_path, None)
    with ai_connection(samples.db_path) as conn:
        for args in ({"show": "utilities"}, {"show": "utilities", "utility": "power"}):
            out = tools.execute(conn, "plans_and_recurring", args)
            assert not out.is_error and len(out.content) <= tools.RESULT_CHARS
            power = _utility(json.loads(out.content), "city-power")
            dates = [b["bill_date"] for b in power["bills"]]
            assert dates == sorted(dates, reverse=True) and dates[0] == power["last_bill_date"]
            assert "_note" not in out.content  # it fits whole


# --------------------------------------------------------------------------
# Contributions
# --------------------------------------------------------------------------

PLAN_FIELDS = {
    "ytd": "ytd_cents",
    "limit": "limit_cents",
    "room": "room_cents",
    "per_check": "per_check_cents",
    "projected": "projected_cents",
    "projected_room": "projected_room_cents",
    "needed_per_check": "needed_per_check_cents",
}
SAME_FIELDS = (
    "ytd_as_of",
    "limit_confirmed",
    "per_check_pct",
    "checks_left",
    "status",
    "change_on",
    "needed_pct",
    "shared",
    "notes",
)


def test_ask_sees_contributions_like_the_contributions_page(samples: Settings, admin) -> None:
    r = _ask(samples.db_path, {"show": "contributions"})
    assert set(r) == {"contributions"}
    c = r["contributions"]
    page = admin.get("/api/contributions").json()
    assert (c["year"], c["as_of"]) == (page["year"], page["as_of"]) == (2026, "2026-10-20")
    assert [p["person"] for p in c["people"]] == [p["display_name"] for p in page["people"]]
    assert page["people"], "the samples have contributions"
    for mine, theirs in zip(c["people"], page["people"], strict=True):
        assert [x["plan"] for x in mine["plans"]] == [x["label"] for x in theirs["plans"]]
        for m, t in zip(mine["plans"], theirs["plans"], strict=True):
            for out, src in PLAN_FIELDS.items():
                assert m[out] == tools.usd(t[src]), out
            for f in SAME_FIELDS:
                assert m[f] == t[f], f
            assert m["ytd_parts"] == tools._money(t["ytd_parts"])
    assert c["hsa_family"] == tools._money(page["hsa_family"])
    # IRA and 529 money: the same rows as the page's IRA and 529 card.
    assert page["other"], "the samples have 529 money"
    assert [
        (o["kind"], o["account"], o["ytd"], o["count"], o["last_date"]) for o in c["other"]
    ] == [
        (o["label"], o["account_name"], tools.usd(o["ytd_cents"]), o["count"], o["last_date"])
        for o in page["other"]
    ]


def test_ira_money_this_year_shows_per_person_and_account(samples: Settings) -> None:
    with write_connection(samples.db_path) as w:
        person, account = w.execute(
            "SELECT person_key, account_key FROM contributions LIMIT 1"
        ).fetchone()
        for d, cents in (("2026-02-03", 100_000), ("2026-05-04", 50_000), ("2025-12-30", 70_000)):
            w.execute(
                """INSERT INTO contributions (contribution_key, person_key, account_key, kind,
                       source, date, amount_cents, run_id)
                   VALUES (?, ?, ?, 'ira', 'personal', ?, ?, '2026-10-14')""",
                (f"test-ira-{d}", person, account, d, cents),
            )
        name = w.execute("SELECT display_name FROM accounts WHERE account_key = ?", (account,))
        account_name = name.fetchone()[0]
    c = _ask(samples.db_path, {"show": "contributions"})["contributions"]
    assert [
        (o["kind"], o["account"], o["ytd"], o["count"], o["last_date"])
        for o in c["other"]
        if o["kind"] == "IRA"  # the samples also have the kids 529
    ] == [("IRA", account_name, "1500.00", 2, "2026-05-04")]


def test_a_limit_change_rebuilds_the_facts(samples: Settings, admin) -> None:
    before = _ask(samples.db_path, {"show": "contributions"})["contributions"]
    plan = before["people"][0]["plans"][0]
    assert plan["plan"] == "401k"
    person = admin.get("/api/contributions").json()["people"][0]["person_key"]
    body = {
        "k401_catch_up": "50",
        "hsa_coverage": None,
        "hsa_catch_up": False,
        "election_lead_checks": 1,
    }
    assert admin.put(f"/api/contributions/people/{person}", json=body, headers=H).status_code == 200
    page = admin.get("/api/contributions").json()["people"][0]["plans"][0]
    after = _ask(samples.db_path, {"show": "contributions"})["contributions"]
    assert after["people"][0]["plans"][0]["limit"] == tools.usd(page["limit_cents"])
    assert after["people"][0]["plans"][0]["limit"] != plan["limit"]


def test_confirming_a_limit_rebuilds_the_facts(samples: Settings, admin) -> None:
    plan = _ask(samples.db_path, {"show": "contributions"})["contributions"]["people"][0]
    assert plan["plans"][0]["limit_confirmed"] is False
    limit = next(x for x in admin.get("/api/contributions").json()["limits"])
    body = {"year": 2026, "kind": "k401_deferral", "limit_cents": 2_450_000, "confirm": True}
    assert limit["kind"] == "k401_deferral"
    assert admin.put("/api/contribution-limits", json=body, headers=H).status_code == 200
    after = _ask(samples.db_path, {"show": "contributions"})["contributions"]["people"][0]
    assert after["plans"][0]["limit_confirmed"] is True


# --------------------------------------------------------------------------
# The tool list, "all" and the context
# --------------------------------------------------------------------------


def test_still_eight_tools_and_all_is_unchanged(samples: Settings) -> None:
    assert len(tools.TOOLS) == 8
    schema = next(d for d in tools.definitions() if d["name"] == "plans_and_recurring")
    assert schema["input_schema"]["properties"]["show"]["enum"] == [
        "all",
        "plans",
        "recurring",
        "debt",
        "utilities",
        "contributions",
    ]
    r = _ask(samples.db_path, {})
    assert set(r) == {"plans", "recurring", "debt"}


def test_the_context_names_utilities_and_contributions(samples: Settings) -> None:
    ensure_facts(samples.db_path, None)
    with ai_connection(samples.db_path) as conn:
        text = context.build(conn)
    assert "Utility bills:" in text and "City Power" in text
    assert "Contributions 2026:" in text and "401k" in text


# --------------------------------------------------------------------------
# The review is written again after a correction
# --------------------------------------------------------------------------


def _tick(job: Job) -> None:
    job.last_started = None
    asyncio.run(Scheduler([job], get_settings().db_path).tick())
    job.thread.join(10)


def _correction(run_id: str = "2026-10-20", change: bool = True, month: str = "2026-08") -> None:
    """A later import. With `change`, it corrects a spending row in `month`."""
    with write_connection(get_settings().db_path) as w:
        w.execute(
            """INSERT INTO import_runs (run_id, run_date, run_seq, mode, status, started_at)
               VALUES (?, ?, 1, 'correction', 'imported', '2026-10-20T12:00:00+00:00')""",
            (run_id, run_id[:10]),
        )
        if change:
            w.execute(
                """UPDATE transactions SET amount_cents = amount_cents - 4000
                   WHERE txn_key = (SELECT txn_key FROM v_cashflow_rows
                                    WHERE month = ? AND flow = 'spending'
                                    ORDER BY out_cents DESC, txn_key LIMIT 1)""",
                (month,),
            )


def _review_row() -> tuple[str, str, str]:
    conn = connect(get_settings().db_path)
    row = conn.execute("SELECT run_id, status, summary FROM ai_reviews").fetchone()
    conn.close()
    return row["run_id"], row["status"], row["summary"]


def _second() -> review.ReviewOut:
    out = good_review()
    out.summary = "Written again after a correction."
    return out


def test_a_correction_to_the_month_before_writes_the_review_again(key, samples, fake) -> None:
    """The review compares August with July, so a July correction counts too."""
    enable()
    job = ai_review_job(get_settings())
    fake.script = [parsed(good_review())]
    _tick(job)
    _correction(month="2026-07")
    fake.script = [parsed(_second())]
    _tick(job)
    assert len(fake.requests) == 2
    assert _review_row() == ("2026-10-20", "ready", "Written again after a correction.")


def _age_rewrite_failure(minutes: int) -> None:
    """Move the last failed rewrite try back in time."""
    when = ai_settings.iso(datetime.now(UTC) - timedelta(minutes=minutes))
    with write_connection(get_settings().db_path) as w:
        row = w.execute("SELECT value_json FROM settings WHERE key = ?", (review.INPUTS_KEY,))
        seen = json.loads(row.fetchone()[0])
        seen["2026-08"]["failed"]["at"] = when
        w.execute(
            "UPDATE settings SET value_json = ? WHERE key = ?",
            (json.dumps(seen), review.INPUTS_KEY),
        )


def _failed_note(admin) -> dict | None:
    return admin.get("/api/ai/status", headers=H).json()["review_failed"]


def _dashboard_review(admin) -> str | None:
    found = admin.get("/api/dashboard", headers=H).json()["ai_review"]
    return found["summary"] if found else None


def test_a_rewrite_that_fails_for_a_passing_reason_is_tried_again(
    key, samples, fake, no_sleep, admin
) -> None:
    """The review from before the correction stays while the job tries again,
    an hour apart, up to 3 times. Then Settings says so."""
    enable()
    job = ai_review_job(get_settings())
    fake.script = [parsed(good_review())]
    _tick(job)
    _age(5)
    _correction()
    for tries in (1, 2, 3):
        fake.script = [server_error(), server_error()]
        _tick(job)
        assert len(fake.requests) == 1 + 2 * tries
        assert _review_row() == ("2026-10-14", "ready", "A steady month.")
        assert _dashboard_review(admin) == "A steady month."
        _tick(job)  # less than an hour later: no call
        assert len(fake.requests) == 1 + 2 * tries
        if tries < 3:
            assert _failed_note(admin) is None  # the job tries again
            _age_rewrite_failure(61)
    note = {"month": "2026-08", "reason": "transient", "tries": 3, "rewrite": True}
    assert _failed_note(admin) == note
    assert _job_runs()[-1] == ("failed", note)
    _age_rewrite_failure(61)
    _tick(job)
    assert len(fake.requests) == 7  # the job stopped
    # A later import that changes the month again starts over.
    _correction("2026-10-21")
    fake.script = [parsed(_second())]
    _tick(job)
    assert _review_row() == ("2026-10-21", "ready", "Written again after a correction.")
    assert _failed_note(admin) is None


def test_a_correction_to_the_month_writes_the_review_again(key, samples, fake) -> None:
    enable()
    job = ai_review_job(get_settings())
    fake.script = [parsed(good_review())]
    _tick(job)
    assert _review_row() == ("2026-10-14", "ready", "A steady month.")
    _tick(job)
    assert len(fake.requests) == 1  # nothing changed
    _correction()
    fake.script = [parsed(_second())]
    _tick(job)
    assert len(fake.requests) == 2
    assert _review_row() == ("2026-10-20", "ready", "Written again after a correction.")
    _tick(job)
    assert len(fake.requests) == 2  # once per change


def test_an_import_that_leaves_the_month_alone_writes_nothing(
    key, samples, fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable()
    job = ai_review_job(get_settings())
    fake.script = [parsed(good_review())]
    _tick(job)
    # A new day and an import: today's net worth, plans and notes move, the month does not.
    monkeypatch.setattr(config, "household_today", lambda: date(2026, 10, 21))
    _correction("2026-10-21", change=False)
    _tick(job)
    assert len(fake.requests) == 1
    assert _review_row()[1:] == ("ready", "A steady month.")


def test_a_failed_rewrite_keeps_the_old_review(key, samples, fake, admin) -> None:
    """A correction run carries no chat review, so the chat's review is as old as
    this one. The review stays on the Dashboard, and Settings says it was not
    updated, so an admin can write it now."""
    enable()
    job = ai_review_job(get_settings())
    fake.script = [parsed(good_review())]
    _tick(job)
    _age(5)  # written minutes before the correction, not in the same second
    _correction()
    fake.script = [parsed(good_review().model_copy(update={"actions": ["One."]}))]
    _tick(job)
    assert len(fake.requests) == 2
    assert _review_row() == ("2026-10-14", "ready", "A steady month.")
    assert _dashboard_review(admin) == "A steady month."
    note = {"month": "2026-08", "reason": "schema", "tries": None, "rewrite": True}
    assert _failed_note(admin) == note
    assert _job_runs()[-1] == ("failed", note)
    _tick(job)
    assert len(fake.requests) == 2  # final: the job stops
    fake.script = [parsed(_second())]
    assert admin.post("/api/ai/review/run", headers=H).json() == {"status": "ready"}
    assert _review_row() == ("2026-10-20", "ready", "Written again after a correction.")
    assert _failed_note(admin) is None
    _tick(job)
    assert len(fake.requests) == 3  # the admin's review has the new hash


def test_a_failed_admin_run_keeps_the_review(key, samples, fake, admin) -> None:
    """Write the review now, and it fails: the ready review stays, with no note."""
    enable()
    job = ai_review_job(get_settings())
    fake.script = [parsed(good_review())]
    _tick(job)
    _age(5)
    fake.script = [parsed(good_review().model_copy(update={"actions": ["One."]}))]
    assert admin.post("/api/ai/review/run", headers=H).json()["status"] == "failed"
    assert _review_row() == ("2026-10-14", "ready", "A steady month.")
    assert _failed_note(admin) is None
    _tick(job)
    assert len(fake.requests) == 2  # nothing changed: the job does not call


def test_an_edit_without_an_import_writes_nothing(key, samples, fake) -> None:
    enable()
    job = ai_review_job(get_settings())
    fake.script = [parsed(good_review())]
    _tick(job)
    with write_connection(get_settings().db_path) as w:
        # A category edit by hand on August's largest spending row.
        w.execute(
            """INSERT INTO transaction_overrides (txn_key, category_key, updated_at)
               SELECT txn_key, 'misc', '2026-10-20T12:00:00+00:00' FROM v_cashflow_rows
               WHERE month = '2026-08' AND flow = 'spending' AND category_key <> 'misc'
               ORDER BY out_cents DESC, txn_key LIMIT 1"""
        )
    _tick(job)
    assert len(fake.requests) == 1


def test_an_edit_after_a_quiet_import_waits_for_the_next_import(key, samples, fake) -> None:
    enable()
    job = ai_review_job(get_settings())
    fake.script = [parsed(good_review())]
    _tick(job)
    _correction("2026-10-20", change=False)
    _tick(job)  # seen: this import left the month alone
    with write_connection(get_settings().db_path) as w:
        w.execute(
            """INSERT INTO transaction_overrides (txn_key, category_key, updated_at)
               SELECT txn_key, 'misc', '2026-10-20T13:00:00+00:00' FROM v_cashflow_rows
               WHERE month = '2026-08' AND flow = 'spending' AND category_key <> 'misc'
               ORDER BY out_cents DESC, txn_key LIMIT 1"""
        )
    _tick(job)
    assert len(fake.requests) == 1  # an edit alone
    _correction("2026-10-21", change=False)
    fake.script = [parsed(_second())]
    _tick(job)
    assert len(fake.requests) == 2  # the next import brings it in
    assert _review_row() == ("2026-10-21", "ready", "Written again after a correction.")


def test_a_review_from_before_the_hash_is_kept_until_the_next_change(key, samples, fake) -> None:
    enable()
    job = ai_review_job(get_settings())
    fake.script = [parsed(good_review())]
    _tick(job)
    with write_connection(get_settings().db_path) as w:
        w.execute("DELETE FROM settings WHERE key = ?", (review.INPUTS_KEY,))
    _correction("2026-10-20", change=True)
    _tick(job)  # no hash: this becomes the baseline, no call
    assert len(fake.requests) == 1
    _correction("2026-10-21", change=True)
    fake.script = [parsed(_second())]
    _tick(job)
    assert len(fake.requests) == 2
    assert _review_row()[:2] == ("2026-10-21", "ready")


def test_a_rewrite_waits_for_the_cap(key, samples, fake) -> None:
    enable(monthly_cap_cents=2500, daily_cap_cents=100_000)
    job = ai_review_job(get_settings())
    fake.script = [parsed(good_review())]
    _tick(job)
    _correction()
    _spend(2500 * 10_000 - 10_000)  # $24.99 of $25: the review's estimate does not fit
    _tick(job)
    assert len(fake.requests) == 1
    assert _review_row() == ("2026-10-14", "ready", "A steady month.")
    assert _reviews() == [("2026-08", "ready")]
    # The cap goes up: the next check writes it.
    enable(monthly_cap_cents=100_000, daily_cap_cents=100_000)
    fake.script = [parsed(_second())]
    _tick(job)
    assert len(fake.requests) == 2
    assert _review_row() == ("2026-10-20", "ready", "Written again after a correction.")


def test_a_rewrite_waits_for_a_review_being_written(key, samples, fake) -> None:
    enable()
    job = ai_review_job(get_settings())
    fake.script = [parsed(good_review())]
    _tick(job)
    _correction()
    assert review.GUARD.acquire(blocking=False)
    try:
        r = asyncio.run(review.write_review(get_settings()))
    finally:
        review.GUARD.release()
    assert r["status"] == "running" and len(fake.requests) == 1


# --------------------------------------------------------------------------
# Codex review (Oct 24): no name in a key, and the guard reads fresh inputs
# --------------------------------------------------------------------------

TOOL_ARGS = [
    ("account_balances", {}),
    ("account_balances", {"include_closed": True}),
    ("budget_status", {}),
    ("cashflow", {}),
    ("holdings", {}),
    *[("net_worth_history", {"series": s}) for s in ("networth", "cash", "investments")],
    *[
        ("plans_and_recurring", {"show": s})
        for s in ("all", "plans", "recurring", "debt", "utilities", "contributions")
    ],
    ("plans_and_recurring", {"show": "utilities", "utility": "power"}),
    ("search_transactions", {"limit": 100}),
    *[
        ("spending_summary", {"group_by": g, "flow": f})
        for g in ("category", "group", "merchant", "month", "account")
        for f in ("spending", "income", "saved_invested")
    ],
]
FIELD_NAME = re.compile(r"[a-z][a-z0-9_]*", re.ASCII)
# Fixed field names that are also a category, group or other name in the samples.
FIELDS_LIKE_DATA = frozenset({"debt", "income", "services", "transfer", "uncategorized"})


def _data_values(db_path) -> set[str]:
    """Keys and names from the data, as they might show up in a JSON key."""
    conn = connect(db_path)
    found: set[str] = set()
    for sql in (
        "SELECT utility_key, provider FROM utility_bills",
        "SELECT account_key, display_name FROM accounts",
        "SELECT person_key, display_name FROM people",
        "SELECT category_key, name FROM categories",
        "SELECT group_key, name FROM category_groups",
        "SELECT DISTINCT merchant, merchant FROM transactions WHERE merchant IS NOT NULL",
    ):
        for a, b in conn.execute(sql):
            found |= {str(a).casefold(), str(b).casefold()}
    conn.close()
    return found


def test_no_tool_result_carries_data_in_a_key(samples: Settings) -> None:
    """Ask re-scrubs stored tool results value by value (ask._result), so a key
    must never hold a name or a key from the data: only field names."""
    ensure_facts(samples.db_path, None)
    data = _data_values(samples.db_path)
    with ai_connection(samples.db_path) as conn:
        for name, args in TOOL_ARGS:
            out = tools.execute(conn, name, args)
            assert not out.is_error, (name, args, out.content)
            for k in _keys(json.loads(out.content)):
                assert FIELD_NAME.fullmatch(k), (name, args, k)
                assert k.casefold() not in data or k in FIELDS_LIKE_DATA, (name, args, k)


def test_a_name_put_on_the_private_list_later_is_not_sent_again(samples: Settings) -> None:
    """A tool result stored in a thread, sent again after "City Power" joins the
    private list: nothing of the name goes, in a key or a value."""
    ensure_facts(samples.db_path, None)
    with ai_connection(samples.db_path) as conn:
        stored = [
            tools.execute(conn, "plans_and_recurring", args).content
            for args in ({"show": "utilities"}, {"show": "utilities", "utility": "power"})
        ]
    assert all("city" in s.casefold() for s in stored)
    later = Scrub(PrivateRules(subscriptions=("City Power",)), ())
    for content in stored:
        sent = ask._result(content, later)
        assert "city" not in sent.casefold()
        assert PRIVATE_LABEL in sent


def test_the_scrub_cleans_keys_too() -> None:
    scrub = Scrub(PrivateRules(subscriptions=("City Power",)), ())
    out = scrub.json({"city-power": {"provider": "City Power & Gas"}, "rows": [{"CITY POWER": 1}]})
    assert "city" not in json.dumps(out).casefold()


def test_the_job_reads_the_inputs_again_once_it_holds_guard(
    key, samples, fake, admin, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The job sees a correction (due), and before it takes GUARD a second
    correction comes and an admin writes the review from it. Under GUARD the job
    reads the inputs again: nothing is due, so it makes no call and the admin's
    review stays."""
    enable()
    s = get_settings()
    fake.script = [parsed(good_review())]
    assert review.review_job_run(s)["status"] == "ready"
    _age(5)
    _correction("2026-10-20")
    real = review._reviewed
    seen = []

    def check_then_correct(db_path, month: str, *rest) -> bool:
        found = real(db_path, month, *rest)
        if not seen:
            seen.append(found)
            _correction("2026-10-21")
            fake.script = [parsed(_second())]
            assert admin.post("/api/ai/review/run", headers=H).json() == {"status": "ready"}
        return found

    monkeypatch.setattr(review, "_reviewed", check_then_correct)
    assert review.review_job_run(s) is None  # nothing left to do
    assert seen == [False]  # it was due when the job first looked
    assert len(fake.requests) == 2 and fake.script == []
    assert _review_row() == ("2026-10-21", "ready", "Written again after a correction.")
    assert not review.GUARD.locked()
