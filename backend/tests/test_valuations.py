"""Phase 5 home, vehicle and vehicle loan estimates (PLAN 3.9, 6.3).

No test reaches FHFA: the index comes from a fake line source with rows in
the real file's layout (conftest blocks the real download too).
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import UTC, date, datetime, timedelta

import pytest
from api_helpers import MEMBER, MEMBER_PW, PROD_ORIGIN, login
from fastapi.testclient import TestClient

from app import config
from app.config import Settings
from app.db.connect import connect, write_transaction
from app.importer.runner import reapply
from app.jobs.scheduler import JobFailed
from app.services.balances import History, clear_cache
from app.valuations import hpi, loans, vehicles
from app.valuations.estimate import estimate_kind
from app.valuations.hpi import urllib_lines as real_urllib_lines  # before conftest blocks it

TODAY = date(2026, 10, 20)
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
H = {"Origin": PROD_ORIGIN}

# Real FHFA layout (header, quoting, a stray tab in note, blank SA values).
HEADER = (
    "hpi_type,hpi_flavor,frequency,level,place_name,place_id,yr,period,"
    "index_nsa,index_sa,rstderr,note\r\n"
)
MASTER = [
    HEADER,
    'traditional,all-transactions,quarterly,MSA,"Austin, TX",12420,2026,1,364.52,,,\r\n',
    'traditional,all-transactions,quarterly,MSA,"Austin, TX",12420,2026,2,376.08,,,\r\n',
    'traditional,expanded-data,quarterly,MSA,"Austin, TX",12420,2025,1,504.05,504.39,1.25,\t\r\n',
    'traditional,expanded-data,quarterly,MSA,"Austin, TX",12420,2025,2,520.69,507.70,1.16,\t\r\n',
    'traditional,expanded-data,quarterly,MSA,"Austin, TX",12420,2025,3,522.34,515.17,1.17,\t\r\n',
    'traditional,expanded-data,quarterly,MSA,"Austin, TX",12420,2025,4,513.77,516.00,1.20,\t\r\n',
    'traditional,expanded-data,quarterly,MSA,"Austin, TX",12420,2026,1,514.45,514.84,1.28,\t\r\n',
    'traditional,expanded-data,quarterly,MSA,"Austin, TX",12420,2026,2,540.69,527.20,1.18,\t\r\n',
    'traditional,expanded-data,quarterly,MSA,"Boise City, ID",14260,2026,2,400.00,401.00,1.10,\r\n',
    "traditional,purchase-only,quarterly,State,Texas,TX,2026,1,467.13,468.94,,\r\n",
    "traditional,purchase-only,quarterly,State,Texas,TX,2026,2,483.73,,,\r\n",
    "traditional,purchase-only,monthly,USA or Census Division,West South Central Division,"
    "DV_WSC,2026,7,392.02,383.39,,\r\n",
    'developmental,expanded-data,quarterly,MSA,"Austin, TX",12420,2026,2,1.00,1.00,,\r\n',
]


def source_of(lines: list[str], calls: list | None = None):
    def source(url: str, headers: dict, timeout: float):
        if calls is not None:
            calls.append((url, dict(headers), timeout))
        return iter(lines)

    return source


def failing(code: str):
    def source(url: str, headers: dict, timeout: float):
        raise hpi.FetchError(code)

    return source


@pytest.fixture(autouse=True)
def _today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "household_today", lambda: TODAY)
    clear_cache()


@pytest.fixture
def conn(samples: Settings):
    c = connect(samples.db_path)
    yield c
    c.close()


def load_index(conn: sqlite3.Connection) -> dict:
    return hpi.run_hpi(conn, NOW, source=source_of(MASTER))


def at(conn: sqlite3.Connection, key: str, day: date, *, estimates: bool = True):
    return History(conn, day, day, estimates=estimates).at_end(key)


# --------------------------------------------------------------------------
# FHFA file
# --------------------------------------------------------------------------


def test_parse_master_keeps_only_the_four_series() -> None:
    parsed = hpi.parse_master(MASTER)
    assert set(parsed) == {
        "fhfa:at:austin:nsa",
        "fhfa:exp:austin:sa",
        "fhfa:po:tx:sa",
        "fhfa:po:wsc:sa:m",
    }
    exp = parsed["fhfa:exp:austin:sa"]
    assert exp["2026-04-01"] == ("q", 527_200_000)  # index_sa, not nsa
    assert exp["2025-01-01"] == ("q", 504_390_000)
    assert len(exp) == 6  # the developmental row and other metros are left out
    assert parsed["fhfa:at:austin:nsa"]["2026-04-01"] == ("q", 376_080_000)  # nsa column
    assert "2026-04-01" not in parsed["fhfa:po:tx:sa"]  # blank (suppressed) value skipped
    assert parsed["fhfa:po:wsc:sa:m"] == {"2026-07-01": ("m", 383_390_000)}


def test_parse_master_rejects_a_bad_header() -> None:
    with pytest.raises(hpi.FetchError) as e:
        hpi.parse_master(["a,b,c\n", "1,2,3\n"])
    assert e.value.code == "bad_response"


def test_run_hpi_sends_nothing_about_the_household(conn: sqlite3.Connection) -> None:
    calls: list = []
    summary = hpi.run_hpi(conn, NOW, source=source_of(MASTER, calls))
    assert calls == [(hpi.MASTER_URL, {"User-Agent": hpi.BROWSER_UA, "Accept": "text/csv"}, 60.0)]
    assert summary["series"]["fhfa:exp:austin:sa"] == "2026-04-01"
    assert summary["rows"] == 10
    row = conn.execute(
        "SELECT last_period, last_fetch_at FROM index_series WHERE series_id = ?",
        ("fhfa:exp:austin:sa",),
    ).fetchone()
    assert tuple(row) == ("2026-04-01", "2026-10-21T12:00:00+00:00")
    # A rerun changes nothing. A changed value is updated in place.
    assert hpi.run_hpi(conn, NOW, source=source_of(MASTER))["rows"] == 0
    revised = [x.replace("540.69,527.20", "540.69,528.00") for x in MASTER]
    assert hpi.run_hpi(conn, NOW, source=source_of(revised))["rows"] == 1


def test_failed_fetch_keeps_the_last_values(conn: sqlite3.Connection) -> None:
    load_index(conn)
    for code in ("timeout", "server", "not_found"):
        with pytest.raises(JobFailed) as e:
            hpi.run_hpi(conn, NOW, source=failing(code))
        assert e.value.summary == {"error": code}
    with pytest.raises(JobFailed) as e:
        hpi.run_hpi(conn, NOW, source=source_of([HEADER]))
    assert e.value.summary == {"error": "no_data"}
    assert conn.execute("SELECT count(*) FROM index_values").fetchone()[0] == 10


def test_status_codes_and_urllib_lines_errors() -> None:
    assert hpi._status_code(429) == "rate_limited"
    assert hpi._status_code(503) == "server"
    assert hpi._status_code(404) == "not_found"
    assert hpi._status_code(403) == "bad_response"


def test_index_status_reports_the_last_run(conn: sqlite3.Connection, samples: Settings) -> None:
    from app.jobs.scheduler import record_run

    assert hpi.status(conn)["series_id"] is None
    load_index(conn)
    record_run(samples.db_path, hpi.JOB_NAME, NOW, NOW, "ok", {"rows": 11})
    record_run(samples.db_path, hpi.JOB_NAME, NOW, NOW, "failed", {"error": "timeout"})
    s = hpi.status(conn)
    assert (s["series_id"], s["through_label"], s["error"]) == (
        "fhfa:exp:austin:sa",
        "2026 Q2",
        "timeout",
    )
    assert s["last_run_status"] == "failed" and s["last_success_at"] == NOW.isoformat()
    assert "Not endorsed by FHFA" in s["attribution"]


# --------------------------------------------------------------------------
# Curve: quarterly values to days
# --------------------------------------------------------------------------


def _curve() -> hpi.Curve:
    rows = [
        ("2025-01-01", "q", 100_000_000),
        ("2025-04-01", "q", 102_000_000),
        ("2025-07-01", "q", 104_000_000),
        ("2025-10-01", "q", 104_000_000),
        ("2026-01-01", "q", 104_000_000),
        ("2026-04-01", "q", 106_000_000),
    ]
    c = hpi.make_curve("fhfa:exp:austin:sa", rows)
    assert c is not None
    return c


def test_curve_anchors_mid_quarter_and_interpolates_log_linear() -> None:
    c = _curve()
    assert [a[0] for a in c.anchors[:3]] == [
        date(2025, 2, 15),
        date(2025, 5, 15),
        date(2025, 8, 15),
    ]
    assert c.at(date(2025, 2, 15)) == pytest.approx(100.0)
    assert c.at(date(2025, 5, 15)) == pytest.approx(102.0)
    assert c.at(date(2025, 1, 1)) == pytest.approx(100.0)  # flat before the first anchor
    # Log-linear by day between Feb 15 and May 15 (89 days).
    d = date(2025, 4, 1)
    expected = 100 * (102 / 100) ** ((d - date(2025, 2, 15)).days / 89)
    assert c.at(d) == pytest.approx(expected)
    assert c.through_label == "2026 Q2"


def test_curve_holds_flat_after_the_last_anchor() -> None:
    """the owner chose to hold steady: no guess past FHFA's newest quarter."""
    c = _curve()
    last = date(2026, 5, 15)
    g = math.log(106 / c.at(last - timedelta(days=365)))
    assert c.trend == pytest.approx(g) and 0.03 < g < 0.04  # still shown, for information
    later = date(2026, 11, 15)
    assert c.at(later) == pytest.approx(106.0)
    assert not c.projected(later) and not c.projected(last)


def test_curve_can_still_project_when_switched_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hpi, "PROJECT_AFTER_LAST", True)
    c = _curve()
    last = date(2026, 5, 15)
    g = math.log(106 / c.at(last - timedelta(days=365)))
    later = date(2026, 11, 15)
    assert c.at(later) == pytest.approx(106 * math.exp(g * (later - last).days / 365.25))
    assert c.projected(later) and not c.projected(last)


def test_curve_trend_is_capped_and_unknown_without_a_year() -> None:
    steep = hpi.make_curve(
        "x", [("2025-01-01", "q", 100_000_000), ("2026-04-01", "q", 200_000_000)]
    )
    assert steep is not None and steep.trend == hpi.MAX_TREND
    short = hpi.make_curve(
        "x", [("2026-01-01", "q", 100_000_000), ("2026-04-01", "q", 101_000_000)]
    )
    assert short is not None and short.trend is None
    assert short.at(date(2026, 12, 31)) == pytest.approx(101.0)  # flat
    assert hpi.make_curve("x", [("2026-01-01", "q", 100_000_000)]) is None


# --------------------------------------------------------------------------
# Schedule
# --------------------------------------------------------------------------


def test_schedule_weekly_wednesday_and_startup(samples: Settings) -> None:
    from app.jobs.scheduler import record_run

    sched = hpi.HpiSchedule(samples.db_path)
    tue = datetime(2026, 10, 20, 15, 0, tzinfo=UTC)
    assert sched(tue) is True  # never succeeded: run at startup
    record_run(samples.db_path, hpi.JOB_NAME, tue, tue, "ok", {"rows": 1})
    fresh = hpi.HpiSchedule(samples.db_path)
    assert fresh(tue) is False  # a recent success: no startup run
    assert fresh(tue + timedelta(hours=12)) is False
    # Wednesday 06:45 Chicago is 11:45 UTC in October.
    assert fresh(datetime(2026, 10, 21, 11, 44, tzinfo=UTC)) is False
    assert fresh(datetime(2026, 10, 21, 11, 46, tzinfo=UTC)) is True
    assert fresh(datetime(2026, 10, 21, 12, 0, tzinfo=UTC)) is False
    assert hpi.last_slot(datetime(2026, 10, 27, 0, 0, tzinfo=UTC)) == datetime(
        2026, 10, 21, 11, 45, tzinfo=UTC
    )


def test_schedule_retries_a_day_after_a_failure(samples: Settings) -> None:
    from app.jobs.scheduler import record_run

    sched = hpi.HpiSchedule(samples.db_path)
    t0 = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)
    assert sched(t0) is True
    record_run(samples.db_path, hpi.JOB_NAME, t0, t0, "failed", {"error": "timeout"})
    assert sched(t0 + timedelta(hours=23)) is False
    assert sched(t0 + timedelta(hours=24)) is True


# --------------------------------------------------------------------------
# Math
# --------------------------------------------------------------------------


def test_amortization_walks_payments_with_daily_interest() -> None:
    pays = [loans.Payment("2026-09-01", 65000), loans.Payment("2026-09-20", 100000)]
    r = loans.amortize(4_200_000, date(2026, 8, 1), date(2026, 9, 30), 6490, pays)
    i1 = round(4_200_000 * 6490 * 31 / (100_000 * 365))  # Aug 1 to Sep 1
    owed = 4_200_000 + i1 - 65000
    i2 = round(owed * 6490 * 19 / (100_000 * 365))  # to Sep 20
    owed = owed + i2 - 100000
    i3 = round(owed * 6490 * 10 / (100_000 * 365))  # to Sep 30
    assert r.owed_cents == owed + i3 == 4_079_440  # the sample's own Sep 30 estimate
    assert (r.payments, r.paid_cents, r.last_payment) == (2, 165000, "2026-09-20")
    assert r.interest_cents == i1 + i2 + i3
    # Payments on or before d0 are in the real balance already. Later ones wait.
    assert (
        loans.amortize(100000, date(2026, 9, 1), date(2026, 9, 1), 6490, pays).owed_cents == 100000
    )
    paid_off = loans.amortize(50000, date(2026, 8, 31), date(2026, 9, 2), 0, pays)
    assert paid_off.owed_cents == 0  # floored at 0


def test_month_step() -> None:
    assert vehicles.month_step(date(2026, 11, 15), date(2026, 9, 30)) == date(2026, 10, 31)
    assert vehicles.month_step(date(2026, 11, 30), date(2026, 9, 30)) == date(2026, 11, 30)
    assert vehicles.month_step(date(2026, 10, 20), date(2026, 9, 30)) == date(2026, 9, 30)
    assert vehicles.month_step(date(2026, 10, 20), date(2026, 10, 5)) == date(2026, 10, 5)


def test_vehicle_depreciation_monthly_with_mileage() -> None:
    s = vehicles.VehicleSettings("car", depreciation_milli_pct=12000, expected_miles_year=12000)
    d0 = date(2026, 3, 31)
    # Moves only at month ends.
    assert vehicles.estimate(2_000_000, d0, date(2026, 4, 29), s, []) is None
    e = vehicles.estimate(2_000_000, d0, date(2026, 6, 30), s, [])
    assert e is not None and e.step == "2026-06-30" and e.mileage is None
    assert e.value_cents == round(2_000_000 * 0.88 ** (91 / 365.25))
    # 6,000 miles in 91 days is about 3,010 over the 12,000 a year pace.
    readings = [
        vehicles.Reading(1, "2026-03-31", 40000, "x"),
        vehicles.Reading(2, "2026-06-30", 46000, "x"),
    ]
    m = vehicles.estimate(2_000_000, d0, date(2026, 6, 30), s, readings)
    assert m is not None and m.mileage is not None
    excess = 6000 - 12000 * 91 / 365.25
    assert m.mileage.excess_miles == round(excess)
    factor = 1 - 0.005 * excess / 1000
    assert m.value_cents == round(2_000_000 * 0.88 ** (91 / 365.25) * factor)
    assert "3,010 miles over pace" in vehicles.note(m, "Mar 31", "KBB value")
    # A new reading moves the value before the next month end.
    early = vehicles.estimate(
        2_000_000,
        d0,
        date(2026, 4, 10),
        s,
        [
            vehicles.Reading(1, "2026-03-31", 40000, "x"),
            vehicles.Reading(2, "2026-04-10", 40100, "x"),
        ],
    )
    assert early is not None and early.value_cents > 2_000_000  # under pace, no months yet


def test_mileage_factor_is_clamped_and_ignores_old_readings() -> None:
    s = vehicles.VehicleSettings("car")
    d0 = date(2026, 3, 31)
    wild = [
        vehicles.Reading(1, "2026-03-31", 10000, "x"),
        vehicles.Reading(2, "2026-04-30", 90000, "x"),
    ]
    e = vehicles.estimate(1_000_000, d0, date(2026, 4, 30), s, wild)
    assert e is not None and e.mileage is not None and e.mileage.factor == 0.80
    old = [
        vehicles.Reading(1, "2026-01-01", 10000, "x"),
        vehicles.Reading(2, "2026-03-01", 12000, "x"),
    ]
    e = vehicles.estimate(1_000_000, d0, date(2026, 4, 30), s, old)
    assert e is not None and e.mileage is None  # every reading is older than the valuation


def test_mileage_before_the_valuation_never_counts_twice() -> None:
    """The odometer on d0 is interpolated, so miles driven before the KBB check
    (already in its value) are left out (review, Oct 21)."""
    s = vehicles.VehicleSettings("car", expected_miles_year=12000)
    readings = [
        vehicles.Reading(1, "2026-01-01", 10000, "x"),
        vehicles.Reading(2, "2026-10-20", 30000, "x"),
    ]
    d0 = date(2026, 9, 30)
    m = vehicles.mileage(readings, d0, date(2026, 10, 20), s)
    assert m is not None and m.base_date == "2026-09-30"
    per_day = 20000 / 292
    on_d0 = 10000 + per_day * 272
    assert m.base_miles == round(on_d0)
    assert m.excess_miles == round((30000 - on_d0) - 12000 * 20 / 365.25)
    assert m.factor > 0.99  # about 1,100 miles over pace in 20 days, not 10,400
    # No reading before d0: the first one after it is the base.
    later = [vehicles.Reading(1, "2026-10-01", 30000, "x"), readings[1]]
    first = vehicles.mileage(later, d0, date(2026, 10, 20), s)
    assert first is not None and first.base_date == "2026-10-01"


def test_conversion_is_added_to_the_kbb_value() -> None:
    """A van's KBB value does not count the conversion. The app
    adds it on top, losing value at its own rate, from the valuation day on."""
    s = vehicles.VehicleSettings(
        "van",
        depreciation_milli_pct=10000,
        conversion_value_cents=2_000_000,
        conversion_value_date="2026-08-01",
        conversion_milli_pct=8000,
    )
    d0, day = date(2026, 9, 30), date(2026, 12, 31)
    e = vehicles.estimate(5_390_000, d0, day, s, [])
    assert e is not None
    c1 = 2_000_000 * 0.92 ** (152 / 365.25)
    b1 = 5_390_000 * 0.90 ** (92 / 365.25)
    assert (e.conversion_cents, e.chassis_cents) == (round(c1), round(b1))
    assert e.value_cents == round(b1) + round(c1)
    assert vehicles.note(e, "Sep 30", "KBB value") == (
        "Estimated from the Sep 30 KBB value plus the conversion. Base vehicle loses "
        "10% a year, conversion 8% a year, no new mileage."
    )
    # On the valuation day itself: KBB plus the conversion on that day.
    on_d0 = vehicles.estimate(5_390_000, d0, d0, s, [])
    c0 = 2_000_000 * 0.92 ** (60 / 365.25)
    assert on_d0 is not None and on_d0.value_cents == 5_390_000 + round(c0)
    assert vehicles.estimate(5_390_000, d0, date(2026, 9, 29), s, []) is None
    # A conversion dated after the day keeps its full value.
    late = vehicles.VehicleSettings(
        "van", conversion_value_cents=2_000_000, conversion_value_date="2026-10-15"
    )
    f = vehicles.estimate(5_390_000, d0, d0, late, [])
    assert f is not None and f.conversion_cents == 2_000_000


def test_default_rates_by_age() -> None:
    on = date(2026, 9, 30)
    assert vehicles.age_rate(None, on) == 12000
    assert vehicles.age_rate(2026, on) == 16000
    assert vehicles.age_rate(2022, on) == 13000
    assert vehicles.age_rate(2018, on) == 10000
    assert vehicles.age_rate(2010, on) == 7000
    assert vehicles.pct_label(6490) == "6.49" and vehicles.pct_label(12000) == "12"


# --------------------------------------------------------------------------
# In the balance engine (samples)
# --------------------------------------------------------------------------


def test_estimate_kinds(conn: sqlite3.Connection) -> None:
    accts = {r["account_key"]: dict(r) for r in conn.execute("SELECT * FROM accounts")}
    kinds = {k: estimate_kind(accts, k) for k in accts}
    assert {k: v for k, v in kinds.items() if v} == {
        "family-home": "home",
        "family-car": "vehicle",
        "camper-van": "vehicle",
        "van-loan": "loan",
    }  # the mortgage is not a vehicle loan


def test_loan_estimate_between_real_balances(conn: sqlite3.Connection) -> None:
    sep10 = at(conn, "van-loan", date(2026, 9, 10))
    expected = loans.amortize(
        4_200_000, date(2026, 8, 1), date(2026, 9, 10), 6490, [loans.Payment("2026-09-01", 65000)]
    )
    assert sep10.balance_cents == -expected.owed_cents
    assert sep10.estimate is not None and sep10.estimate.kind == "loan"
    assert sep10.estimate.based_on == "2026-08-01" and sep10.estimate.through == "2026-09-01"
    assert sep10.estimate.note == (
        "Estimated from the Aug 1 balance, 6.49% APR and 1 payment since. Payments count "
        "once the checking statement is in, so this can run high until then."
    )
    # A real balance on the day always wins: the loan papers and the chat's Sep 30 estimate.
    for day, cents in ((date(2026, 8, 1), -4_200_000), (date(2026, 9, 30), -4_079_440)):
        b = at(conn, "van-loan", day)
        assert (b.balance_cents, b.estimate) == (cents, None)
    # After Sep 30: interest only, from the chat's estimate.
    oct20 = at(conn, "van-loan", TODAY)
    assert (
        oct20.balance_cents
        == -loans.amortize(4_079_440, date(2026, 9, 30), TODAY, 6490, []).owed_cents
    )
    # Statement basis (Checks) never sees an estimate.
    real = at(conn, "van-loan", date(2026, 9, 10), estimates=False)
    assert (real.balance_cents, real.estimate) == (-4_200_000, None)


def test_no_apr_means_no_loan_estimate(conn: sqlite3.Connection) -> None:
    with write_transaction(conn):
        conn.execute("UPDATE accounts SET apr_milli_pct = NULL WHERE account_key = 'van-loan'")
    clear_cache()
    b = at(conn, "van-loan", date(2026, 9, 10))
    assert (b.balance_cents, b.estimate) == (-4_200_000, None)
    from app.services.valuations import valuations_page

    item = next(i for i in valuations_page(conn, TODAY)["items"] if i["account_key"] == "van-loan")
    assert item["estimate"] is None and item["note"] == loans.NO_APR_NOTE


def test_home_estimate_moves_with_the_index(conn: sqlite3.Connection) -> None:
    # Nothing before index values exist.
    assert at(conn, "family-home", date(2026, 11, 30)).estimate is None
    load_index(conn)
    clear_cache()
    curve = hpi.load_curve(conn)
    assert curve is not None and curve.series_id == "fhfa:exp:austin:sa"
    real = at(conn, "family-home", date(2026, 9, 30))
    assert real.estimate is None  # the valuation day itself
    assert at(conn, "family-home", TODAY).estimate is None  # no month end since Sep 30
    # The valuation is newer than FHFA's newest quarter (Q2), so the value holds.
    nov = at(conn, "family-home", date(2026, 11, 30))
    assert curve.at(date(2026, 9, 30)) == curve.at(date(2026, 11, 30))
    assert (nov.balance_cents, nov.estimate) == (real.balance_cents, None)
    # Inside the index range: Apr 30 from the Mar 31 valuation, between two anchors.
    mar = at(conn, "family-home", date(2026, 3, 31))
    apr = at(conn, "family-home", date(2026, 4, 30))
    ratio = curve.at(date(2026, 4, 30)) / curve.at(date(2026, 3, 31))
    assert apr.balance_cents == round(mar.balance_cents * ratio)
    assert not curve.projected(date(2026, 4, 30))
    # Checks basis: still the valuation.
    assert at(conn, "family-home", date(2026, 11, 30), estimates=False).estimate is None


def test_vehicle_estimate_and_mileage_from_the_api(conn: sqlite3.Connection, admin) -> None:
    # Oct 20: no month end since the Sep 30 KBB value, no readings: the real value.
    b = at(conn, "family-car", TODAY)
    assert b.estimate is None and b.balance_cents == 2_238_590
    r = admin.post(
        "/api/vehicles/family-car/mileage",
        json={"miles": 30000, "reading_date": "2026-09-30"},
        headers=H,
    )
    assert r.status_code == 201, r.text
    r = admin.post("/api/vehicles/family-car/mileage", json={"miles": 32100}, headers=H)
    assert r.status_code == 201, r.text
    car = r.json()
    assert [x["reading_date"] for x in car["readings"]] == ["2026-10-20", "2026-09-30"]
    excess = 2100 - 12000 * 20 / 365.25
    factor = 1 - 0.005 * excess / 1000
    assert car["value_cents"] == round(2_238_590 * factor)
    assert car["parts"]["excess_miles"] == round(excess)
    assert car["parts"]["mileage_factor_milli"] == round(factor * 1000)
    assert car["estimate"]["note"] == (
        "Estimated from the Sep 30 KBB value. Loses 12% a year, 1,443 miles over pace."
    )
    assert car["estimate"]["based_on"] == "2026-09-30"
    assert car["estimate"]["through"] == "2026-10-20"
    assert car["effective"] == {
        "depreciation_milli_pct": 12000,
        "rate_source": "default",
        "expected_miles_year": 12000,
        "mileage_milli_pct": 500,
        "conversion_milli_pct": None,
    }
    # The Checks page (statement basis) is unchanged.
    real = at(conn, "family-car", TODAY, estimates=False)
    assert (real.balance_cents, real.estimate) == (2_238_590, None)


def test_estimates_never_reach_checks(conn: sqlite3.Connection, admin) -> None:
    before = admin.get("/api/checks").json()
    load_index(conn)
    admin.post("/api/vehicles/camper-van/mileage", json={"miles": 9000}, headers=H)
    admin.post(
        "/api/vehicles/camper-van/mileage",
        json={"miles": 1000, "reading_date": "2026-09-30"},
        headers=H,
    )
    clear_cache()
    assert at(conn, "camper-van", TODAY).estimate is not None
    assert admin.get("/api/checks").json() == before


def test_conversion_not_added_to_a_whole_vehicle_value(conn: sqlite3.Connection, admin) -> None:
    """A manual value already holds the build-out, so the conversion is not added twice."""
    body = {
        "enabled": True,
        "model_year": 2022,
        "depreciation_milli_pct": 10000,
        "expected_miles_year": 8000,
        "mileage_milli_pct": None,
        "conversion_value_cents": 2_000_000,
        "conversion_value_date": "2026-08-01",
        "conversion_milli_pct": 8000,
    }
    assert admin.put("/api/vehicles/camper-van/settings", json=body, headers=H).status_code == 200
    with write_transaction(conn):
        conn.execute(
            "INSERT INTO balances (account_key, as_of, source, balance_cents, method, run_id) "
            "VALUES ('camper-van', '2026-10-05', 'valuation', 7400000, 'manual', 'test')"
        )
    clear_cache()
    b = at(conn, "camper-van", date(2026, 10, 5))
    assert (b.balance_cents, b.estimate) == (7_400_000, None)  # the whole value, nothing added
    later = at(conn, "camper-van", date(2026, 11, 30))
    assert later.estimate is not None and "conversion" not in later.estimate.note
    assert later.balance_cents < 7_400_000  # it only loses value from the whole amount


def test_camper_van_conversion_settings(conn: sqlite3.Connection, admin) -> None:
    body = {
        "enabled": True,
        "model_year": 2022,
        "depreciation_milli_pct": 10000,
        "expected_miles_year": 8000,
        "mileage_milli_pct": None,
        "conversion_value_cents": 2_000_000,
        "conversion_value_date": "2026-08-01",
        "conversion_milli_pct": 8000,
    }
    r = admin.put("/api/vehicles/camper-van/settings", json=body, headers=H)
    assert r.status_code == 200, r.text
    van = r.json()
    assert van["settings"]["conversion_value_cents"] == 2_000_000
    assert van["effective"]["rate_source"] == "set"
    # Oct 20: no month end since Sep 30, but the conversion is added to the KBB value.
    c_oct = round(2_000_000 * 0.92 ** (60 / 365.25))
    assert van["real"]["value_cents"] == 5_390_000 and van["real"]["label"] == "KBB value"
    assert van["value_cents"] == van["estimate"]["value_cents"] == 5_390_000 + c_oct
    assert van["parts"]["chassis_cents"] + van["parts"]["conversion_cents"] == van["value_cents"]
    sep30 = at(conn, "camper-van", date(2026, 9, 30))
    assert sep30.balance_cents == 5_390_000 + c_oct and sep30.estimate is not None
    checks = at(conn, "camper-van", date(2026, 9, 30), estimates=False)
    assert (checks.balance_cents, checks.estimate) == (5_390_000, None)  # Checks: KBB only
    nov = vehicles.estimate(
        5_390_000,
        date(2026, 9, 30),
        date(2026, 11, 30),
        vehicles.load_settings(conn)["camper-van"],
        [],
    )
    b = at(conn, "camper-van", date(2026, 11, 30))
    assert nov is not None and b.balance_cents == nov.value_cents
    assert b.estimate is not None and "conversion 8% a year" in b.estimate.note
    # Equity uses the estimates of the van and its loan.
    page = admin.get("/api/vehicles").json()
    assert [v["account_key"] for v in page["vehicles"]] == ["camper-van", "family-car"]
    v = page["vehicles"][0]
    assert v["loans"][0]["account_key"] == "van-loan" and v["loans"][0]["estimated"] is True
    assert v["equity"]["loan_cents"] == v["loans"][0]["balance_cents"]
    assert v["equity"]["equity_cents"] == v["value_cents"] + v["loans"][0]["balance_cents"]
    assert v["equity"]["estimated"] is True
    # Value and date go together. No future dates.
    bad = admin.put(
        "/api/vehicles/camper-van/settings",
        json={**body, "conversion_value_date": None},
        headers=H,
    )
    assert bad.status_code == 422
    future = admin.put(
        "/api/vehicles/camper-van/settings",
        json={**body, "conversion_value_date": "2026-12-01"},
        headers=H,
    )
    assert future.status_code == 422
    # Events hold no values.
    detail = conn.execute(
        "SELECT detail_json FROM events WHERE entity = 'vehicle_settings'"
    ).fetchone()[0]
    assert json.loads(detail) == {}


def test_vehicle_api_rules(conn: sqlite3.Connection, prod: TestClient) -> None:
    assert login(prod, MEMBER, MEMBER_PW, PROD_ORIGIN).status_code == 200
    # Any signed-in user can add mileage. Settings are admin only.
    r = prod.post("/api/vehicles/family-car/mileage", json={"miles": 30000}, headers=H)
    assert r.status_code == 201
    rid = r.json()["readings"][0]["id"]
    assert prod.put("/api/vehicles/family-car/settings", json={}, headers=H).status_code == 403
    # Origin rules apply.
    assert prod.post("/api/vehicles/family-car/mileage", json={"miles": 30001}).status_code == 403
    # Not a vehicle, a future date, mileage going down, bad numbers.
    assert (
        prod.post("/api/vehicles/van-loan/mileage", json={"miles": 1}, headers=H).status_code == 404
    )
    future = prod.post(
        "/api/vehicles/family-car/mileage",
        json={"miles": 30500, "reading_date": "2026-10-21"},
        headers=H,
    )
    assert future.status_code == 422
    down = prod.post(
        "/api/vehicles/family-car/mileage",
        json={"miles": 29000, "reading_date": "2026-10-20"},
        headers=H,
    )
    assert down.status_code == 200 or down.status_code == 201  # same day replaces
    lower_later = prod.post(
        "/api/vehicles/family-car/mileage",
        json={"miles": 29500, "reading_date": "2026-10-01"},
        headers=H,
    )
    assert lower_later.status_code == 422
    assert "Mileage cannot go down" in lower_later.json()["detail"]
    assert (
        prod.post("/api/vehicles/family-car/mileage", json={"miles": -1}, headers=H).status_code
        == 422
    )
    # Delete a reading.
    gone = prod.delete(f"/api/vehicles/family-car/mileage/{rid}", headers=H)
    assert gone.status_code == 200 and gone.json()["readings"] == []
    again = prod.delete(f"/api/vehicles/family-car/mileage/{rid}", headers=H)
    assert again.status_code == 404
    kinds = [
        r[0] for r in conn.execute("SELECT kind FROM events WHERE entity = 'vehicle_readings'")
    ]
    assert kinds == ["mileage_added", "mileage_added", "mileage_deleted"]
    assert prod.get("/api/vehicles").status_code == 200


def test_valuations_page(conn: sqlite3.Connection, admin) -> None:
    load_index(conn)
    page = admin.get("/api/valuations").json()
    assert page["as_of"] == "2026-10-20"
    assert page["index"]["series_id"] == "fhfa:exp:austin:sa"
    assert page["index"]["through_label"] == "2026 Q2"
    items = {i["account_key"]: i for i in page["items"]}
    assert [i["kind"] for i in page["items"]] == ["home", "vehicle", "vehicle", "loan"]
    home = items["family-home"]
    assert home["real"]["as_of"] == "2026-09-30" and home["real"]["label"] == "valuation"
    assert home["real"]["source_note"] == "Three nearby sales"
    assert home["estimate"] is None and home["equity"]["loan_cents"] < 0
    loan = items["van-loan"]
    assert loan["real"] == {
        "value_cents": -4_079_440,
        "as_of": "2026-09-30",
        "source": "estimate",
        "method": None,
        "label": "budget chat's estimate",
        "source_note": "Amortized from the loan papers",
    }
    assert loan["estimate"]["method"] == "amortization"
    assert loan["inputs"]["loan"]["apr_milli_pct"] == 6490
    assert loan["value_cents"] == loan["estimate"]["value_cents"] < -4_079_440
    assert items["camper-van"]["equity"]["estimated"] is True


def test_reapply_keeps_app_owned_rows(conn: sqlite3.Connection, samples: Settings, admin) -> None:
    load_index(conn)
    admin.post("/api/vehicles/family-car/mileage", json={"miles": 30000}, headers=H)
    counts = reapply(samples)
    assert counts["changes"] == 0
    for table in ("vehicle_readings", "index_values", "contribution_limits"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] > 0, table


def test_urllib_lines_streams_and_maps_errors() -> None:
    """Against a local server only: lines stream through, statuses become codes."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            seen.append(self.headers.get("User-Agent", ""))
            if self.path == "/ok":
                body = "".join(MASTER).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(503 if self.path == "/down" else 404)
                self.send_header("Content-Length", "0")
                self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        lines = list(real_urllib_lines(f"{base}/ok", {"User-Agent": "ua-test"}, 5.0))
        assert hpi.parse_master(lines)["fhfa:exp:austin:sa"]["2026-04-01"] == ("q", 527_200_000)
        for path, code in (("/down", "server"), ("/missing", "not_found")):
            with pytest.raises(hpi.FetchError) as e:
                list(real_urllib_lines(f"{base}{path}", {}, 5.0))
            assert e.value.code == code
        assert seen[0] == "ua-test"
    finally:
        server.shutdown()
        server.server_close()


def test_urllib_lines_connect_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    from app.market import http

    class Slow:
        def open(self, req, timeout=None):  # noqa: ANN001, ANN201
            raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(http, "_OPENER", Slow())
    with pytest.raises(hpi.FetchError) as e:
        list(real_urllib_lines("https://example.invalid/", {}, 1.0))
    assert e.value.code == "timeout"


def test_settings_saved_in_the_same_second_clear_the_cache(conn: sqlite3.Connection, admin) -> None:
    """The History cache key holds the settings values, not just updated_at."""
    body = {
        "enabled": True,
        "model_year": None,
        "depreciation_milli_pct": 10000,
        "expected_miles_year": None,
        "mileage_milli_pct": None,
        "conversion_value_cents": 2_000_000,
        "conversion_value_date": "2026-08-01",
        "conversion_milli_pct": 8000,
    }
    for rate in (8000, 40000):
        r = admin.put(
            "/api/vehicles/camper-van/settings",
            json={**body, "conversion_milli_pct": rate},
            headers=H,
        )
        assert r.status_code == 200, r.text
    van = admin.get("/api/vehicles").json()["vehicles"][0]
    assert van["value_cents"] == van["estimate"]["value_cents"]
    assert van["parts"]["conversion_rate_milli_pct"] == 40000


def test_accounts_page_marks_estimates_and_new_accounts(conn: sqlite3.Connection) -> None:
    """Review, Oct 21: net worth says it holds estimates, and a van bought in
    August shows how its value moved, not its whole value as a gain."""
    from app.services import networth

    page = networth.accounts_page(conn, "YTD")
    groups = {g["group"]: g for g in page["groups"]}
    rows = {r["account_key"]: r for g in page["groups"] for r in g["accounts"]}
    van = rows["camper-van"]
    detail = networth.account_detail(conn, "camper-van", "YTD")
    first = detail["points"][0]
    assert first["date"] == "2026-08-01" and first["real"] is True
    assert van["change_cents"] == van["balance_cents"] - first["balance_cents"]
    # Oct 20: no month end since the Sep 30 KBB values, so only the loan is estimated.
    assert groups["loans"]["estimated"] is True and groups["vehicles"]["estimated"] is False
    estimated = {e["account_key"]: e for e in page["notes"]["estimated"]}
    assert "van-loan" in estimated and "camper-van" not in estimated
    assert estimated["van-loan"]["kind"] == "loan" and estimated["van-loan"]["note"]
    series = networth.networth_series(conn, "YTD")
    assert series["points"][-1]["estimated"] is True
    assert {e["account_key"] for e in series["estimated_accounts"]} == set(estimated)
    loan = networth.account_detail(conn, "van-loan", "YTD")["points"]
    by_day = {p["date"]: p for p in loan}
    assert by_day["2026-08-01"] == {**by_day["2026-08-01"], "real": True, "estimated": False}
    assert by_day["2026-09-10"]["estimated"] is True and not by_day["2026-09-10"]["real"]
