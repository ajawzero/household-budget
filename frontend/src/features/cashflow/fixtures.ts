// Fictional numbers for Cash Flow tests. Not real data.
import type { BreakdownOut, CashflowOut, CashflowPlanOut, CategoryDetailOut, IncomeOut } from "./api";

const zero = {
  payment_basis_cents: 0,
  card_no_statement_cents: 0,
  uncategorized_cents: 0,
  reimbursable_cents: 0,
};

export const CASHFLOW: CashflowOut = {
  from: "2026-07",
  to: "2026-10",
  current_month: "2026-10",
  latest_complete_month: "2026-08",
  kpi: {
    month: "2026-08",
    income_cents: 1_000_000,
    spending_cents: 400_000,
    saved_invested_cents: 100_000,
    left_over_cents: 500_000,
    savings_rate: "60.0",
    count: 40,
    ...zero,
    payment_basis_cents: 5_000,
  },
  months: [
    {
      month: "2026-07",
      status: "complete",
      complete: true,
      income_cents: 900_000,
      spending_cents: 350_000,
      saved_invested_cents: 100_000,
      left_over_cents: 450_000,
      savings_rate: "61.1",
      count: 38,
      ...zero,
      plan: { income_cents: 950_000, spending_cents: 500_000, saved_invested_cents: 100_000, left_over_cents: 450_000, saved_pct: "57.9" },
    },
    {
      month: "2026-08",
      status: "complete",
      complete: true,
      income_cents: 1_000_000,
      spending_cents: 400_000,
      saved_invested_cents: 100_000,
      left_over_cents: 500_000,
      savings_rate: "60.0",
      count: 40,
      ...zero,
      plan: null,
    },
    {
      month: "2026-09",
      status: "partial",
      complete: false,
      income_cents: 800_000,
      spending_cents: 200_000,
      saved_invested_cents: 50_000,
      left_over_cents: 550_000,
      savings_rate: "75.0",
      count: 20,
      ...zero,
      plan: null,
    },
    {
      month: "2026-10",
      status: "current",
      complete: false,
      income_cents: 0,
      spending_cents: 0,
      saved_invested_cents: 0,
      left_over_cents: 0,
      savings_rate: null,
      count: 0,
      ...zero,
      plan: { income_cents: 1_100_000, spending_cents: 860_000, saved_invested_cents: 70_000, left_over_cents: 240_000, saved_pct: "28.2" },
    },
  ],
  totals: { months: 2, income_cents: 1_900_000, spending_cents: 750_000, saved_invested_cents: 200_000, left_over_cents: 950_000, savings_rate: "60.5" },
  averages: { months: 2, income_cents: 950_000, spending_cents: 375_000, saved_invested_cents: 100_000, left_over_cents: 475_000 },
  plan_vs_actual: [
    {
      month: "2026-07",
      complete: true,
      planned_income_cents: 950_000,
      actual_income_cents: 900_000,
      planned_spending_cents: 500_000,
      actual_spending_cents: 450_000,
      diff_cents: -50_000,
      status: "on_target",
      lines: [
        { key: "housing", label: "Housing and utilities", measure: "group", plan_cents: 200_000, actual_cents: 230_000, diff_cents: 30_000, status: "over" },
        { key: "lifestyle", label: "Lifestyle", measure: "group", plan_cents: 200_000, actual_cents: 120_000, diff_cents: -80_000, status: "under" },
        { key: "saved-invested", label: "Saved and invested", measure: "saved_invested", plan_cents: 100_000, actual_cents: 100_000, diff_cents: 0, status: "on_target" },
        { key: "travel", label: "Other discretionary", measure: "unplanned", plan_cents: null, actual_cents: 0, diff_cents: null, status: "no_plan" },
      ],
    },
    {
      month: "2026-10",
      complete: false,
      planned_income_cents: 1_100_000,
      actual_income_cents: 0,
      planned_spending_cents: 860_000,
      actual_spending_cents: 18_000,
      diff_cents: -842_000,
      status: "under",
      lines: [
        { key: "plan-installments", label: "Plan It installments", measure: "plan_installments", plan_cents: 18_000, actual_cents: 18_000, diff_cents: 0, status: "on_target" },
      ],
    },
  ],
  tracked_cards: {
    label: "Sam's cards",
    basis: "payment basis",
    cards: [
      {
        account_key: "bramble-store-card",
        name: "Bramble store card",
        months: [
          { month: "2026-07", cents: 4_000, payments_cents: 4_000, statement_cents: 0 },
          { month: "2026-08", cents: 7_700, payments_cents: 4_400, statement_cents: 3_300 },
          { month: "2026-09", cents: 2_100, payments_cents: 2_100, statement_cents: 0 },
          { month: "2026-10", cents: 0, payments_cents: 0, statement_cents: 0 },
        ],
        total_cents: 13_800,
        avg_cents: 5_850,
        last_payment_date: "2026-09-24",
        last_payment_cents: 2_100,
      },
      {
        account_key: "lark-store-card",
        name: "Lark store card",
        months: [
          { month: "2026-07", cents: 1_000, payments_cents: 1_000, statement_cents: 0 },
          { month: "2026-08", cents: 0, payments_cents: 0, statement_cents: 0 },
          { month: "2026-09", cents: 500, payments_cents: 500, statement_cents: 0 },
          { month: "2026-10", cents: 0, payments_cents: 0, statement_cents: 0 },
        ],
        total_cents: 1_500,
        avg_cents: 500,
        last_payment_date: "2026-09-12",
        last_payment_cents: 500,
      },
    ],
    months: [
      { month: "2026-07", cents: 5_000 },
      { month: "2026-08", cents: 7_700 },
      { month: "2026-09", cents: 2_600 },
      { month: "2026-10", cents: 0 },
    ],
    total_cents: 15_300,
    // Jul and Aug are the complete months in the fixture.
    avg_cents: 6_350,
    avg_months: 2,
  },
  waiting_reimbursement: [
    { txn_key: "a".repeat(64), txn_date: "2026-08-14", merchant: "Pine Clinic", account_name: "Cobalt card", amount_cents: -12_000 },
  ],
  waiting_reimbursement_cents: 12_000,
};

export const BREAKDOWN_GROUP: BreakdownOut = {
  from: "2026-08",
  to: "2026-08",
  by: "group",
  flow: "spending",
  complete: true,
  partial_months: [],
  total_cents: 400_000,
  saved_invested_cents: 100_000,
  items: [
    { key: "housing", label: "Housing and utilities", emoji: null, group_key: "housing", group_label: "Housing and utilities", cents: 200_000, count: 4, payment_basis_cents: 0, share_pct: "50.0" },
    { key: "lifestyle", label: "Lifestyle", emoji: null, group_key: "lifestyle", group_label: "Lifestyle", cents: 100_000, count: 20, payment_basis_cents: 5_000, share_pct: "25.0" },
    // Shares purple with housing, so it folds into "Everything else".
    { key: "debt", label: "Debt payments", emoji: null, group_key: "debt", group_label: "Debt payments", cents: 50_000, count: 1, payment_basis_cents: 0, share_pct: "12.5" },
    { key: "kids", label: "Kids and education", emoji: null, group_key: "kids", group_label: "Kids and education", cents: 30_000, count: 2, payment_basis_cents: 0, share_pct: "7.5" },
    { key: "services", label: "Services and fitness", emoji: null, group_key: "services", group_label: "Services and fitness", cents: 20_000, count: 2, payment_basis_cents: 0, share_pct: "5.0" },
  ],
};

export const BREAKDOWN_CATEGORY: BreakdownOut = {
  ...BREAKDOWN_GROUP,
  by: "category",
  items: [
    { key: "mortgage", label: "Mortgage", emoji: "🏠", group_key: "housing", group_label: "Housing and utilities", cents: 150_000, count: 1, payment_basis_cents: 0, share_pct: "37.5" },
    { key: "groceries", label: "Groceries", emoji: "🛒", group_key: "lifestyle", group_label: "Lifestyle", cents: 60_000, count: 8, payment_basis_cents: 0, share_pct: "15.0" },
    { key: "clothing", label: "Clothing", emoji: "👕", group_key: "lifestyle", group_label: "Lifestyle", cents: 40_000, count: 4, payment_basis_cents: 5_000, share_pct: "10.0" },
  ],
};

export const BREAKDOWN_MERCHANT: BreakdownOut = {
  ...BREAKDOWN_GROUP,
  by: "merchant",
  items: [
    { key: "green leaf market", label: "Green Leaf Market", emoji: null, group_key: null, group_label: null, cents: 50_000, count: 6, payment_basis_cents: 0, share_pct: "12.5" },
  ],
};

export const CATEGORY_GROCERIES: CategoryDetailOut = {
  category_key: "groceries",
  name: "Groceries",
  emoji: "🛒",
  kind: "spending",
  group_key: "lifestyle",
  group_label: "Lifestyle",
  from: "2026-07",
  to: "2026-09",
  months: [
    { month: "2026-07", cents: 55_000, count: 7, complete: true, status: "complete", budget_cents: 60_000, group_plan_cents: 200_000 },
    { month: "2026-08", cents: 60_000, count: 8, complete: true, status: "complete", budget_cents: 60_000, group_plan_cents: null },
    { month: "2026-09", cents: 20_000, count: 3, complete: false, status: "partial", budget_cents: null, group_plan_cents: null },
  ],
  total_cents: 135_000,
  average_cents: 57_500,
  top_merchants: [
    { label: "Green Leaf Market", cents: 100_000, count: 12 },
    { label: "Corner Grocery", cents: 35_000, count: 6 },
  ],
};

export const PLAN: CashflowPlanOut = {
  current_month: "2026-10",
  source_run_id: "2026-10-12",
  months: [
    {
      month: "2026-10",
      status: "current",
      income_cents: 1_100_000,
      spending_cents: 860_000,
      saved_invested_cents: 70_000,
      left_over_cents: 240_000,
      saved_pct: "28.2",
      note: "Fictional plan",
      by_group: [
        {
          key: "housing",
          label: "Housing and utilities",
          cents: 360_000,
          kind: "spending",
          lines: [
            { key: "mortgage", name: "Pinecrest Mortgage", cents: 150_000, change: null, previous_cents: null },
            { key: "city-power", name: "City Power", cents: 15_000, change: null, previous_cents: null },
            { key: "lawn-care", name: "Lawn care", cents: 8_500, change: "rate_up", previous_cents: 8_000 },
          ],
          other_cents: 186_500,
          contents: { kind: "bills", names: ["Pinecrest Mortgage", "City Power", "Lawn care"], more: 0 },
        },
        { key: "plan-installments", label: "Plan It installments", cents: 18_000, kind: "spending", lines: [], other_cents: 18_000, contents: null },
        {
          key: "saved-invested",
          label: "Saved and invested",
          cents: 70_000,
          kind: "saved_invested",
          lines: [
            { key: "college-529", name: "College 529", cents: 60_000, change: "new", previous_cents: null },
            { key: "savings-transfer", name: "Monthly savings", cents: 20_000, change: null, previous_cents: null },
          ],
          other_cents: -10_000,
          contents: { kind: "transfers", names: ["College 529", "Monthly savings"], more: 0 },
        },
      ],
      mapping: [
        { key: "housing", label: "Housing and utilities", measure: "group", maps_to: "Spending in Housing and utilities" },
        { key: "plan-installments", label: "Plan It installments", measure: "plan_installments", maps_to: "Installments and plan fees billed" },
        { key: "saved-invested", label: "Saved and invested", measure: "saved_invested", maps_to: "Actual saved and invested" },
      ],
      vs_actual: null,
    },
  ],
  events: [
    { key: "sofa-ends", effective_month: "2026-12", delta_cents: 10_500, label: "Sofa plan ends", reason: "Last payment in November", linked_kind: "plan", linked_key: "plan-sofa", upcoming: true, status: "confirmed" },
    { key: "rent-up", effective_month: "2026-06", delta_cents: -5_000, label: "Storage went up", reason: null, linked_kind: null, linked_key: null, upcoming: false, status: null },
  ],
  phases: [
    { key: "base", label: "Base", start_month: "2026-10", end_month: "2026-12", surplus_cents: 40_000, reason: "Plans still running", note: null, current: true },
    { key: "plans-done", label: "Plans done", start_month: "2027-01", end_month: null, surplus_cents: 58_000, reason: null, note: null, current: false },
  ],
};

export const INCOME: IncomeOut = {
  from: "2026-08",
  to: "2026-09",
  source_run_id: "2026-10-12",
  streams: [
    { key: "alex-pay", person: "alex", person_label: "Alex", label: "Alex pay", kind: "paycheck", schedule: "Every other Friday", expected_net_cents: 290_000, start_month: "2026-01", end_month: null, note: null, active: true },
    { key: "sam-pay", person: "sam", person_label: "Sam", label: "Sam pay", kind: "paycheck", schedule: "15th and last day", expected_net_cents: 220_000, start_month: "2026-01", end_month: null, note: null, active: true },
  ],
  months: [
    {
      month: "2026-08",
      status: "complete",
      complete: true,
      income_cents: 1_020_100,
      other_income_cents: 100,
      streams: [
        { key: "alex-pay", deposits_cents: 580_000, deposit_count: 2 },
        { key: "sam-pay", deposits_cents: 440_000, deposit_count: 2 },
      ],
      paychecks: [
        { person: "alex", person_label: "Alex", count: 2, gross_cents: 800_000, net_cents: 580_000 },
        { person: "sam", person_label: "Sam", count: 2, gross_cents: 600_000, net_cents: 440_000 },
      ],
    },
    {
      month: "2026-09",
      status: "partial",
      complete: false,
      income_cents: 290_000,
      other_income_cents: 0,
      streams: [{ key: "alex-pay", deposits_cents: 290_000, deposit_count: 1 }],
      paychecks: [],
    },
  ],
  paychecks: [
    { person: "alex", person_label: "Alex", pay_date: "2026-08-07", kind: "regular", seq: 1, gross_cents: 400_000, net_cents: 290_000 },
    { person: "sam", person_label: "Sam", pay_date: "2026-08-14", kind: "regular", seq: 1, gross_cents: 300_000, net_cents: 220_000 },
  ],
};
