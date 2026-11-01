// Fictional numbers for Budget tests. Not real data.
import type { BudgetGroup, BudgetsOut } from "./api";

export const BUDGET_AUG: BudgetsOut = {
  month: "2026-08",
  latest_complete_month: "2026-08",
  current_month: "2026-10",
  complete: true,
  note: null,
  plan_source: "plan",
  planned_income_cents: 1_000_000,
  planned_spending_cents: 500_000,
  chat_planned_spending_cents: 500_000,
  actual_income_cents: 980_000,
  totals: { plan_cents: 500_000, chat_plan_cents: 500_000, actual_cents: 520_000, remaining_cents: -20_000 },
  groups: [
    {
      key: "housing",
      label: "Housing and utilities",
      kind: "spending",
      plan_cents: 200_000,
      actual_cents: 230_000,
      remaining_cents: -30_000,
      status: "over",
      chat_plan_cents: 200_000,
      source: "chat",
      editable: false,
      edit: null,
      categories: [
        { category_key: "mortgage", name: "Mortgage", emoji: "🏠", actual_cents: 200_000, target_cents: null, remaining_cents: null, status: "no_plan" },
        { category_key: "internet", name: "Internet", emoji: "🌐", actual_cents: 30_000, target_cents: null, remaining_cents: null, status: "no_plan" },
      ],
    },
    {
      key: "lifestyle",
      label: "Lifestyle",
      kind: "spending",
      plan_cents: 200_000,
      actual_cents: 150_000,
      remaining_cents: 50_000,
      status: "under",
      chat_plan_cents: 200_000,
      source: "chat",
      editable: false,
      edit: null,
      categories: [
        { category_key: "dining", name: "Dining", emoji: "🍽️", actual_cents: 45_000, target_cents: 40_000, remaining_cents: -5_000, status: "over" },
      ],
    },
    {
      key: "saved-invested",
      label: "Saved and invested",
      kind: "saved_invested",
      plan_cents: 100_000,
      actual_cents: 120_000,
      remaining_cents: -20_000,
      status: "over",
      chat_plan_cents: 100_000,
      source: "chat",
      editable: false,
      edit: null,
      categories: [],
    },
    {
      key: "discretionary",
      label: "Other discretionary",
      kind: "spending",
      plan_cents: null,
      actual_cents: 20_000,
      remaining_cents: null,
      status: "no_plan",
      chat_plan_cents: null,
      source: "chat",
      editable: false,
      edit: null,
      categories: [],
    },
  ],
  tracked_cards: {
    label: "Sam's cards",
    basis: "payment basis",
    cards: [
      {
        account_key: "bramble-store-card",
        name: "Bramble store card",
        months: [{ month: "2026-08", cents: 7_700, payments_cents: 4_400, statement_cents: 3_300 }],
        total_cents: 7_700,
        avg_cents: 7_700,
        last_payment_date: "2026-08-24",
        last_payment_cents: 4_400,
      },
    ],
    months: [{ month: "2026-08", cents: 7_700 }],
    total_cents: 7_700,
    avg_cents: 7_700,
    avg_months: 1,
  },
  chat_budgets: [
    {
      key: "dining",
      label: "Dining",
      period_basis: "calendar",
      cycle_note: null,
      category_key: "dining",
      scope_owner: null,
      scope_cards: [],
      target_cents: 40_000,
      plan_cents: 40_000,
      reported_actual_cents: 45_000,
      actual_source: "statement",
      status: "over",
      statements_needed: [],
      note: null,
    },
    {
      key: "sams-cards",
      label: "Sam's cards",
      period_basis: "card_cycle",
      cycle_note: "Cycle closes on the 22nd",
      category_key: null,
      scope_owner: "sam",
      scope_cards: ["Cobalt card"],
      target_cents: 30_000,
      plan_cents: null,
      reported_actual_cents: 21_000,
      actual_source: "statement",
      status: "under",
      statements_needed: ["Cobalt card"],
      note: null,
    },
  ],
  statements_needed: ["Cobalt card"],
  can_edit: false,
  edit_note: "Past months keep the targets they had.",
  later_months: [],
  addable_groups: [],
  conflicts: 0,
  edits_notice: null,
};

export const BUDGET_OCT: BudgetsOut = {
  ...BUDGET_AUG,
  month: "2026-10",
  complete: false,
  note: "Statements not in yet",
  actual_income_cents: 0,
  totals: { plan_cents: 500_000, chat_plan_cents: 500_000, actual_cents: 0, remaining_cents: 500_000 },
  groups: [],
  chat_budgets: [],
  statements_needed: [],
  tracked_cards: null,
  can_edit: true,
  edit_note: null,
};

function group(key: string, label: string, kind: BudgetGroup["kind"], plan: number, actual: number, editable = true): BudgetGroup {
  return {
    key,
    label,
    kind,
    plan_cents: plan,
    actual_cents: actual,
    remaining_cents: plan - actual,
    status: "under",
    categories: [],
    chat_plan_cents: plan,
    source: "chat",
    editable,
    edit: null,
  };
}

/** This month, open for an admin's edits: $8,600 planned of $11,000 income. */
export const BUDGET_OCT_EDIT: BudgetsOut = {
  ...BUDGET_OCT,
  planned_income_cents: 1_100_000,
  planned_spending_cents: 860_000,
  chat_planned_spending_cents: 860_000,
  totals: { plan_cents: 860_000, chat_plan_cents: 860_000, actual_cents: 230_000, remaining_cents: 630_000 },
  groups: [
    group("housing", "Housing and utilities", "spending", 360_000, 120_000),
    group("lifestyle", "Lifestyle", "spending", 365_000, 80_000),
    group("plan-installments", "Plan It installments", "plan_installments", 18_000, 9_000, false),
    group("saved-invested", "Saved and invested", "saved_invested", 70_000, 0),
    group("services", "Services", "spending", 25_000, 12_000),
    group("transportation", "Transportation", "spending", 22_000, 9_000),
  ],
  can_edit: true,
  edit_note: null,
  later_months: ["2026-11", "2026-12"],
  addable_groups: [{ key: "kids", label: "Kids and education", kind: "spending" }],
};

/** BUDGET_OCT_EDIT with Lifestyle set to $3,400 in the app. Pass the chat's number now for a conflict. */
export function withLifestyleEdit(page: BudgetsOut, chatNow = 365_000): BudgetsOut {
  const conflict = chatNow !== 365_000;
  const groups = page.groups.map((g) =>
    g.key === "lifestyle"
      ? {
          ...g,
          plan_cents: 340_000,
          remaining_cents: 340_000 - g.actual_cents,
          chat_plan_cents: chatNow,
          source: "edited" as const,
          edit: { by: "Sam Example", at: "2026-10-20", conflict },
        }
      : g,
  );
  const chatTotal = 860_000 - 365_000 + chatNow;
  return {
    ...page,
    groups,
    planned_spending_cents: 835_000,
    chat_planned_spending_cents: chatTotal,
    totals: { ...page.totals, plan_cents: 835_000, chat_plan_cents: chatTotal, remaining_cents: 835_000 - page.totals.actual_cents },
    conflicts: conflict ? 1 : 0,
  };
}
