"""Home, vehicle and vehicle loan values: real and estimated (PLAN 3.9, 6.3, 8.2).

- GET /api/valuations: per home, vehicle and vehicle loan, the latest real
  value and its date, today's estimate with how it was made, its inputs
  (index date, mileage date, APR and payments), and equity.
- GET /api/vehicles: per vehicle, settings, odometer readings and the
  estimate's parts.
- Mileage readings (any signed-in user) and vehicle settings (admin) are
  app-owned rows that `cli reapply` keeps. Each change writes an event with
  no values.

Estimates are computed by app/valuations/estimate.py through the balance
engine. Nothing here stores a balance.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Any

from app.services.balances import History, LatestBalance, history
from app.services.networth import _equity
from app.services.overrides import EditError
from app.valuations import hpi, loans, vehicles
from app.valuations.estimate import (
    METHOD_LABELS,
    SOURCE_LABELS,
    Estimator,
    estimate_kind,
    found_for,
)

NO_VEHICLE = "No such vehicle."
NO_READING = "No such reading."
ESTIMATE_METHODS = {"home": "fhfa_hpi", "vehicle": "depreciation", "loan": "amortization"}


def _real(conn: sqlite3.Connection, key: str, b: LatestBalance) -> dict | None:
    if b.balance_cents is None or b.as_of is None or b.source in ("closed", "tracked"):
        return None
    row = conn.execute(
        """SELECT source, method, source_note FROM balances
           WHERE account_key = ? AND as_of = ?
           ORDER BY CASE source WHEN 'statement' THEN 0 WHEN 'manual' THEN 1
                                WHEN 'valuation' THEN 2 ELSE 3 END LIMIT 1""",
        (key, b.as_of),
    ).fetchone()
    source = row["source"] if row else b.source
    method = row["method"] if row else None
    label = (
        METHOD_LABELS.get(method or "", "valuation")
        if source == "valuation"
        else SOURCE_LABELS.get(source or "", "balance")
    )
    return {
        "value_cents": b.balance_cents,
        "as_of": b.as_of,
        "source": source,
        "method": method,
        "label": label,
        "source_note": row["source_note"] if row else None,
    }


def _today_history(conn: sqlite3.Connection, today: date) -> tuple[History, History]:
    return history(conn, today, today), history(conn, today, today, estimates=False)


def _item(
    conn: sqlite3.Connection, h: History, real_h: History, est: Estimator, key: str
) -> dict[str, Any]:
    acct = h.accounts[key]
    kind = est.kinds[key]
    b = h.at_end(key)
    real = _real(conn, key, real_h.at_end(key))
    found = found_for(est, h, key, len(h.days) - 1)
    estimate = None
    inputs: dict[str, Any] = {"home": None, "vehicle": None, "loan": None}
    if found is not None and b.estimate is not None:
        estimate = {
            "value_cents": found.cents,
            "method": ESTIMATE_METHODS[kind],
            "based_on": found.estimate.based_on,
            "through": found.estimate.through,
            "note": found.estimate.note,
        }
        inputs[kind] = found.detail
    note = None
    if kind == "loan" and acct["apr_milli_pct"] is None and real is not None:
        note = loans.NO_APR_NOTE
    elif kind == "home" and est.curve is None:
        note = "No index values yet, so this is the last valuation."
    elif kind == "vehicle" and not est.settings_for(key).enabled:
        note = "Estimates are off for this vehicle."
    equity = None
    if kind in ("home", "vehicle"):
        eq = _equity(h, key, b.balance_cents)
        if eq is not None:
            loan_keys = [k for k, a in h.accounts.items() if a["secured_by_account_key"] == key]
            estimated = b.estimate is not None or any(
                h.at_end(k).estimate is not None for k in loan_keys
            )
            equity = eq | {"estimated": estimated}
    return {
        "account_key": key,
        "display_name": acct["display_name"],
        "kind": kind,
        "secured_by": acct["secured_by_account_key"],
        "value_cents": b.balance_cents,
        "stale": b.stale,
        "real": real,
        "estimate": estimate,
        "inputs": inputs,
        "note": note,
        "equity": equity,
    }


def _active(acct: dict, today: date) -> bool:
    return bool(acct["active"]) and (
        acct["closed_on"] is None or acct["closed_on"] >= today.isoformat()
    )


def valuations_page(conn: sqlite3.Connection, today: date) -> dict:
    h, real_h = _today_history(conn, today)
    est = Estimator(conn, h.accounts)
    order = {"home": 0, "vehicle": 1, "loan": 2}
    keys = sorted(
        (k for k in est.kinds if _active(h.accounts[k], today)),
        key=lambda k: (order[est.kinds[k]], h.accounts[k]["display_name"], k),
    )
    return {
        "as_of": today.isoformat(),
        "index": hpi.status(conn),
        "items": [_item(conn, h, real_h, est, k) for k in keys],
    }


# --------------------------------------------------------------------------
# Vehicles
# --------------------------------------------------------------------------


def _settings_out(s: vehicles.VehicleSettings) -> dict:
    return {
        "enabled": s.enabled,
        "model_year": s.model_year,
        "depreciation_milli_pct": s.depreciation_milli_pct,
        "expected_miles_year": s.expected_miles_year,
        "mileage_milli_pct": s.mileage_milli_pct,
        "conversion_value_cents": s.conversion_value_cents,
        "conversion_value_date": s.conversion_value_date,
        "conversion_milli_pct": s.conversion_milli_pct,
        "updated_at": s.updated_at,
    }


def _effective(s: vehicles.VehicleSettings, on: date) -> dict:
    if s.depreciation_milli_pct is not None:
        source = "set"
    elif s.model_year is not None:
        source = "model_year"
    else:
        source = "default"
    return {
        "depreciation_milli_pct": s.rate(on),
        "rate_source": source,
        "expected_miles_year": s.miles_year,
        "mileage_milli_pct": s.mileage_rate,
        "conversion_milli_pct": s.conversion_rate if s.has_conversion else None,
    }


def _vehicle(
    conn: sqlite3.Connection, h: History, real_h: History, est: Estimator, key: str, today: date
) -> dict:
    item = _item(conn, h, real_h, est, key)
    s = est.settings_for(key)
    readings = est.readings.get(key, [])
    base_day = date.fromisoformat(item["real"]["as_of"]) if item["real"] else today
    loans_out = []
    for k, a in h.accounts.items():
        if a["secured_by_account_key"] == key and estimate_kind(h.accounts, k) == "loan":
            lb = h.at_end(k)
            loans_out.append(
                {
                    "account_key": k,
                    "display_name": a["display_name"],
                    "balance_cents": lb.balance_cents,
                    "estimated": lb.estimate is not None,
                }
            )
    return {
        "account_key": key,
        "display_name": item["display_name"],
        "value_cents": item["value_cents"],
        "real": item["real"],
        "estimate": item["estimate"],
        "parts": item["inputs"]["vehicle"],
        "note": item["note"],
        "settings": _settings_out(s),
        "effective": _effective(s, base_day),
        "readings": [
            {
                "id": r.id,
                "reading_date": r.reading_date,
                "miles": r.miles,
                "entered_at": r.entered_at,
            }
            for r in reversed(readings)
        ],
        "loans": loans_out,
        "equity": item["equity"],
    }


def vehicles_page(conn: sqlite3.Connection, today: date) -> dict:
    h, real_h = _today_history(conn, today)
    est = Estimator(conn, h.accounts)
    keys = sorted(
        (k for k, kind in est.kinds.items() if kind == "vehicle" and _active(h.accounts[k], today)),
        key=lambda k: (h.accounts[k]["display_name"], k),
    )
    return {
        "as_of": today.isoformat(),
        "vehicles": [_vehicle(conn, h, real_h, est, k, today) for k in keys],
    }


def vehicle(conn: sqlite3.Connection, key: str, today: date) -> dict:
    h, real_h = _today_history(conn, today)
    est = Estimator(conn, h.accounts)
    return _vehicle(conn, h, real_h, est, key, today)


def require_vehicle(conn: sqlite3.Connection, key: str) -> None:
    row = conn.execute("SELECT type FROM accounts WHERE account_key = ?", (key,)).fetchone()
    if row is None or row["type"] != "vehicle":
        raise EditError(404, NO_VEHICLE)


def _event(
    conn: sqlite3.Connection, user_id: int, now: str, kind: str, entity: str, key: str
) -> None:
    conn.execute(
        """INSERT INTO events (at, user_id, kind, entity, entity_key, detail_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (now, user_id, kind, entity, key, json.dumps({})),
    )


def add_reading(
    conn: sqlite3.Connection,
    key: str,
    *,
    reading_date: date,
    miles: int,
    today: date,
    user_id: int,
    now: str,
) -> None:
    """Add or replace the reading for that day. Call inside a write transaction."""
    require_vehicle(conn, key)
    if reading_date > today:
        raise EditError(422, "The date cannot be in the future.")
    day = reading_date.isoformat()
    earlier = conn.execute(
        """SELECT reading_date, miles FROM vehicle_readings
           WHERE account_key = ? AND reading_date < ? ORDER BY reading_date DESC LIMIT 1""",
        (key, day),
    ).fetchone()
    later = conn.execute(
        """SELECT reading_date, miles FROM vehicle_readings
           WHERE account_key = ? AND reading_date > ? ORDER BY reading_date LIMIT 1""",
        (key, day),
    ).fetchone()
    if earlier is not None and miles < earlier["miles"]:
        raise EditError(
            422, f"Mileage cannot go down. The {_label(earlier['reading_date'])} reading is higher."
        )
    if later is not None and miles > later["miles"]:
        raise EditError(
            422, f"Mileage cannot go down. The {_label(later['reading_date'])} reading is lower."
        )
    conn.execute(
        """INSERT INTO vehicle_readings (account_key, reading_date, miles, entered_by, entered_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (account_key, reading_date) DO UPDATE SET
             miles = excluded.miles, entered_by = excluded.entered_by,
             entered_at = excluded.entered_at""",
        (key, day, miles, user_id, now),
    )
    _event(conn, user_id, now, "mileage_added", "vehicle_readings", key)


def delete_reading(
    conn: sqlite3.Connection, key: str, reading_id: int, *, user_id: int, now: str
) -> None:
    require_vehicle(conn, key)
    cur = conn.execute(
        "DELETE FROM vehicle_readings WHERE id = ? AND account_key = ?", (reading_id, key)
    )
    if cur.rowcount == 0:
        raise EditError(404, NO_READING)
    _event(conn, user_id, now, "mileage_deleted", "vehicle_readings", key)


def set_settings(
    conn: sqlite3.Connection,
    key: str,
    values: dict[str, Any],
    *,
    today: date,
    user_id: int,
    now: str,
) -> None:
    """Replace one vehicle's settings. None fields mean "use the default"."""
    require_vehicle(conn, key)
    has_value = values["conversion_value_cents"] is not None
    has_date = values["conversion_value_date"] is not None
    if has_value != has_date:
        raise EditError(422, "Enter the conversion value and the date it applies to together.")
    if has_date and values["conversion_value_date"] > today:
        raise EditError(422, "The conversion date cannot be in the future.")
    if values["conversion_value_date"] is not None:
        values = values | {"conversion_value_date": values["conversion_value_date"].isoformat()}
    cols = [
        "enabled",
        "model_year",
        "depreciation_milli_pct",
        "expected_miles_year",
        "mileage_milli_pct",
        "conversion_value_cents",
        "conversion_value_date",
        "conversion_milli_pct",
    ]
    params = [int(values["enabled"]) if c == "enabled" else values[c] for c in cols]
    conn.execute(
        f"""INSERT INTO vehicle_settings (account_key, {", ".join(cols)}, updated_by, updated_at)
            VALUES (?, {", ".join("?" for _ in cols)}, ?, ?)
            ON CONFLICT (account_key) DO UPDATE SET
              {", ".join(f"{c} = excluded.{c}" for c in cols)},
              updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
        [key, *params, user_id, now],
    )
    _event(conn, user_id, now, "setting_changed", "vehicle_settings", key)


def _label(day: str) -> str:
    d = date.fromisoformat(day)
    return f"{d.strftime('%b')} {d.day}"
