import type { Vehicle } from "./api";

// Fictional vehicle and numbers only.

export const VEHICLE: Vehicle = {
  account_key: "example-van",
  display_name: "Example van",
  value_cents: 4_800_000,
  real: { value_cents: 5_000_000, as_of: "2026-07-15", source: "valuation", method: "kbb", label: "KBB value", source_note: "KBB value" },
  estimate: {
    value_cents: 4_800_000,
    method: "depreciation",
    based_on: "2026-07-15",
    through: "2026-10-01",
    note: "Estimated from the Jul 15 KBB value. Chassis loses 10% a year, conversion 8% a year, 1,500 miles over pace.",
  },
  parts: {
    step: "2026-09-30",
    chassis_cents: 3_000_000,
    conversion_cents: 1_800_000,
    chassis_rate_milli_pct: 10_000,
    conversion_rate_milli_pct: 8_000,
    mileage_factor_milli: 993,
    excess_miles: 1_500,
    mileage_date: "2026-10-01",
    chassis_floored: false,
  },
  note: null,
  settings: {
    enabled: true,
    model_year: 2020,
    depreciation_milli_pct: null,
    expected_miles_year: 8_000,
    mileage_milli_pct: null,
    conversion_value_cents: 2_000_000,
    conversion_value_date: "2026-07-15",
    conversion_milli_pct: 8_000,
    updated_at: "2026-10-01T12:00:00+00:00",
  },
  effective: {
    depreciation_milli_pct: 10_000,
    rate_source: "model_year",
    expected_miles_year: 8_000,
    mileage_milli_pct: 500,
    conversion_milli_pct: 8_000,
  },
  readings: [{ id: 7, reading_date: "2026-10-01", miles: 30_000, entered_at: "2026-10-01T12:00:00+00:00" }],
  loans: [{ account_key: "example-van-loan", display_name: "Example van loan", balance_cents: -2_000_000, estimated: true }],
  equity: { value_cents: 4_800_000, loan_cents: -2_000_000, equity_cents: 2_800_000, underwater: false, estimated: true },
};
