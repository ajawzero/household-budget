// Fictional dashboard payload for tests. No real numbers.
import type { Dashboard } from "../../lib/api";

export const DASHBOARD: Dashboard = {
  month: "2026-10",
  updated: "2026-10-12",
  complete_through: "2026-09-30",
  location_label: "Example Town",
  net_worth: {
    value_cents: 40_000_000,
    change_cents: 500_000,
    assets_cents: 60_000_000,
    liabilities_cents: 20_000_000,
    as_of: "2026-10-21",
    balances_through: "2026-09-30",
    partial: true,
    behind: [
      { account_key: "new-card", name: "New card", reason: "no_balance", as_of: null, source: null, due: null },
      { account_key: "brokerage", name: "Sample brokerage", reason: "overdue", as_of: "2026-08-31", source: "statement", due: "2026-09-30" },
      { account_key: "home", name: "Home", reason: "overdue", as_of: "2026-06-01", source: "valuation", due: "2026-09-01" },
    ],
  },
  plan_month: {
    month: "2026-10",
    income_cents: 1_000_000,
    spending_cents: 800_000,
    saved_invested_cents: 90_000,
    left_over_cents: 200_000,
    saved_pct: "29.0",
    basis: "plan",
  },
  actual_month: null,
  cashflow: [
    { month: "2026-10", income_cents: 1_000_000, spending_cents: 800_000, saved_invested_cents: 90_000, left_over_cents: 200_000, basis: "plan", complete: false },
    { month: "2026-11", income_cents: 1_000_000, spending_cents: 1_050_000, saved_invested_cents: 90_000, left_over_cents: -50_000, basis: "plan", complete: false },
    { month: "2026-12", income_cents: 1_100_000, spending_cents: 900_000, saved_invested_cents: 90_000, left_over_cents: 200_000, basis: "plan", complete: false },
  ],
  cashflow_note: "Example plan, Oct to Dec",
  // Saved and invested is part of planned spending: the groups add up to the $8,000.
  spending_by_group: [
    { key: "lifestyle", label: "Lifestyle", cents: 300_000, note: null, kind: "spending", basis: "plan", contents: { kind: "categories", names: ["Groceries", "Dining", "Online shopping"], more: 7 } },
    { key: "housing", label: "Housing", cents: 200_000, note: null, kind: "spending", basis: "plan", contents: { kind: "bills", names: ["Mortgage", "Electric", "Internet"], more: 4 } },
    { key: "saved-invested", label: "Saved and invested", cents: 90_000, note: null, kind: "saved_invested", basis: "plan", contents: { kind: "transfers", names: ["College 529"], more: 0 } },
    { key: "insurance", label: "Insurance", cents: 40_000, note: null, kind: "spending", basis: "plan", contents: { kind: "categories", names: ["Life insurance", "Auto and home insurance"], more: 0 } },
    { key: "kids", label: "Kids", cents: 170_000, note: null, kind: "spending", basis: "plan", contents: null },
  ],
  actual_by_group: [],
  upcoming: [],
  utilities: [],
  credits_ending: null,
  next_trip: null,
  budgets: [
    { key: "dining", label: "Dining", actual_cents: 55_000, target_cents: 50_000, plan_cents: 50_000, note: null, period_basis: "calendar", cycle_note: null, status: "over", statements_needed: false },
    { key: "groceries", label: "Groceries", actual_cents: 98_000, target_cents: 100_000, plan_cents: null, note: "Two stores", period_basis: "calendar", cycle_note: null, status: "on_target", statements_needed: false },
    { key: "store-cards", label: "Store cards", actual_cents: 20_000, target_cents: 30_000, plan_cents: null, note: null, period_basis: "card_cycle", cycle_note: "Cycle closes on the 22nd", status: "under", statements_needed: true },
    { key: "fuel", label: "Fuel", actual_cents: null, target_cents: 25_000, plan_cents: null, note: null, period_basis: "calendar", cycle_note: null, status: "no_actual", statements_needed: false },
  ],
  networth_parts: [
    { key: "home-equity", label: "Home equity", cents: 30_000_000 },
    { key: "retirement", label: "Retirement", cents: 15_000_000 },
    { key: "mortgage", label: "Mortgage", cents: -5_000_000 },
  ],
  debts: [
    { account_key: "example-mortgage", name: "Mortgage", owed_cents: 19_000_000, apr_milli_pct: 4_500, payoff_date: "2049-06-01", payoff_ahead: true },
    { account_key: "example-card", name: "Blue card", owed_cents: 100_000, apr_milli_pct: 21_990, payoff_date: null, payoff_ahead: false },
  ],
  next_changes: [
    { key: "sofa-ends", effective_month: "2026-12", label: "Sofa plan ends", delta_cents: 10_500, status: "confirmed" },
    { key: "rent-up", effective_month: "2027-01", label: "Storage rent goes up", delta_cents: -5_000, status: null },
  ],
  todos: [
    { key: "car-value", title: "Check the car value", subtitle: "Quarterly", status: "open", due_date: "2026-10-31" },
    { key: "claim", title: "File the claim", subtitle: null, status: "done", due_date: null },
  ],
  review: {
    summary: "A steady month.",
    key_numbers: [{ label: "Net worth", value: 40_000_000 }],
    changes: ["The store card is closed."],
    anomalies: ["Two same charges on one day."],
    top_three: ["Pay the promo.", "Check the car value.", "Sort the odd charge."],
  },
};

export const EMPTY_DASHBOARD: Dashboard = {
  ...DASHBOARD,
  plan_month: null,
  cashflow: [],
  cashflow_note: null,
  spending_by_group: [],
  budgets: [],
  networth_parts: [],
  debts: [],
  next_changes: [],
  todos: [],
  review: null,
};

/** Phase 4: an actual month, actual bars before the plan months, actual groups and upcoming bills. */
export const DASHBOARD_P4: Dashboard = {
  ...DASHBOARD,
  actual_month: {
    month: "2026-08",
    income_cents: 1_050_000,
    spending_cents: 600_000,
    saved_invested_cents: 90_000,
    left_over_cents: 360_000,
    savings_rate: "42.9",
    basis: "actual",
  },
  cashflow: [
    { month: "2026-08", income_cents: 1_050_000, spending_cents: 690_000, saved_invested_cents: 90_000, left_over_cents: 360_000, basis: "actual", complete: true },
    { month: "2026-09", income_cents: 1_040_000, spending_cents: 500_000, saved_invested_cents: 90_000, left_over_cents: 540_000, basis: "actual", complete: false },
    ...DASHBOARD.cashflow,
  ],
  actual_by_group: [
    { key: "housing", label: "Housing", cents: 400_000, note: null, kind: "spending", basis: "actual", contents: { kind: "bills", names: ["Mortgage", "Electric"], more: 0 } },
    { key: "saved-invested", label: "Saved and invested", cents: 90_000, note: null, kind: "saved_invested", basis: "actual", contents: null },
    { key: "lifestyle", label: "Lifestyle", cents: 200_000, note: null, kind: "spending", basis: "actual", contents: { kind: "categories", names: ["Groceries", "Dining", "Online shopping"], more: 7 } },
  ],
  upcoming: [
    { date: "2026-10-22", kind: "bill", key: "water", name: "Example Water", amount_cents: 6_000, account_name: "Example checking", trip_key: null, trip_name: null },
    { date: "2026-10-22", kind: "plan", key: "sofa", name: "Example sofa", amount_cents: 10_500, account_name: "Example card", trip_key: null, trip_name: null },
    { date: "2026-10-23", kind: "bill", key: "allowance", name: "Allowance", amount_cents: 1_000, account_name: null, trip_key: null, trip_name: null },
    ...Array.from({ length: 8 }, (_, i) => ({
      date: `2026-11-${String(i + 1).padStart(2, "0")}`,
      kind: "bill" as const,
      key: `bill-${i}`,
      name: `Example bill ${i + 1}`,
      amount_cents: 1_000,
      account_name: "Example checking",
      trip_key: null,
      trip_name: null,
    })),
  ],
  credits_ending: { count: 2, left_cents: 3_500, first_end: "2026-10-31" },
  next_trip: {
    key: "example-trip",
    name: "Example trip",
    status: "booked",
    start_date: "2027-01-15",
    month: null,
    cash_cents: 45_120,
    estimate_low_cents: 30_000,
    estimate_high_cents: 42_000,
    tbd_count: 1,
  },
  utilities: [
    {
      utility_key: "example-power",
      provider: "Example Power",
      bill_count: 25,
      first_period_end: "2024-09-02",
      last_period_end: "2026-09-03",
      last_bill_date: "2026-09-06",
      last_total_cents: 17_425,
      last_status: "paid",
      services: [
        { service: "electric", unit: "kwh" },
        { service: "gas", unit: "therms" },
      ],
      next_bill_date: "2026-10-06",
      next_bill_late: false,
      last_import_date: "2026-10-12",
      avg_bill_cents: 13_744,
      bills_in_avg: 12,
      total_12m_cents: 164_928,
    },
  ],
};
