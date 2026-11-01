// API types. Response shapes come from schema.ts, which scripts/gen_api_types.sh
// generates from the backend's OpenAPI. This file only gives them stable names,
// adds the constants screens need, and keeps a few hand-written types for
// routes the backend does not type yet (imports) and for request inputs.
// Money is integer cents, signed from the net worth view (assets positive,
// owed negative) unless a field says positive. Dates are ISO strings
// ("2026-09-30"), months are "2026-09".

import type { components } from "./schema";

type S = components["schemas"];

/** Fails to compile unless A and B are the same type. */
type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;
type Expect<T extends true> = T;

// ---------- Auth and meta ----------

export type Role = "admin" | "member";
export type User = S["UserOut"];
/** `/api/auth/me`: the user, and in demo the pinned "today". */
export type Me = S["MeOut"];
export type LoginOut = S["LoginOut"];
export type Meta = S["MetaOut"];

// ---------- Imports (no response models in the backend yet) ----------

export interface RunError {
  loc: string;
  rule: string;
  message?: string;
}

export interface ImportRun {
  run_id: string;
  mode: string;
  schema_version: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  counts: Record<string, unknown>;
  warning_count: number;
  error: RunError | null;
}

export interface RunWarning {
  loc: string;
  rule: string;
  message: string;
}

export interface RunStatement {
  statement_key: string;
  account_key: string;
  period_end: string;
  is_current: boolean;
  txn_count: number | null;
  reconciled: boolean | null;
}

export interface ImportRunDetail extends ImportRun {
  warnings: RunWarning[];
  statements: RunStatement[];
}

// ---------- Transactions and categories ----------

export type Transaction = S["TransactionRow"];
export type TransactionsPage = S["TransactionsOut"];

export interface TransactionFilters {
  account?: string;
  category?: string;
  month?: string;
  owner?: string;
  cursor?: string | null;
  limit?: number;
}

export type Category = S["CategoryOut"];
export type CategoryGroup = S["CategoryGroupOut"];
export type CategoriesResponse = S["CategoriesOut"];

// ---------- Ranges and series ----------

export const RANGE_KEYS = ["1M", "3M", "6M", "YTD", "1Y", "3Y", "ALL"] as const;
export type RangeKey = (typeof RANGE_KEYS)[number];
/** The Investments value chart also takes 1D and 7D (priced holdings move every weekday). */
export const INVESTMENT_RANGE_KEYS = ["1D", "7D", ...RANGE_KEYS] as const;
export type InvestmentRangeKey = (typeof INVESTMENT_RANGE_KEYS)[number];
export const DEFAULT_RANGE: RangeKey = "YTD";
export type DateRange = S["RangeOut"];

export const ACCOUNT_GROUPS = [
  "cash",
  "credit_cards",
  "investments",
  "retirement",
  "real_estate",
  "vehicles",
  "loans",
  "other",
] as const;
export type AccountGroupKey = (typeof ACCOUNT_GROUPS)[number];

export const SERIES_KEYS = ["networth", ...ACCOUNT_GROUPS.filter((g) => g !== "other")] as const;
export type SeriesKey = S["SeriesOut"]["series"];

// The constants above must list exactly what the backend accepts.
export type ConstantsMatchSchema = [
  Expect<Same<RangeKey, DateRange["key"]>>,
  Expect<Same<AccountGroupKey, S["AccountGroup"]["group"]>>,
  Expect<Same<SeriesKey, "networth" | Exclude<AccountGroupKey, "other">>>,
];

export type BalanceSource = NonNullable<S["AccountRow"]["source"]>;
export type BalancePoint = S["BalancePoint"];
export type Equity = S["Equity"];

// ---------- GET /api/accounts ----------

export type AccountRow = S["AccountRow"];
export type AccountGroup = S["AccountGroup"];
export type SummaryPart = S["SummaryPart"];
export type AccountsSummary = S["AccountsSummary"];
export type AccountsNotes = S["AccountsNotes"];
export type AccountsResponse = S["AccountsOut"];

// ---------- GET /api/networth/series ----------

export type SeriesPoint = S["SeriesPoint"];
export type NetworthSeries = S["SeriesOut"];

// ---------- GET /api/accounts/{key} ----------

export type StatementSummary = S["StatementOut"];
export type Holding = S["HoldingOut"];
export type AccountTerms = S["Terms"];
export type AccountAbout = S["AccountAbout"];
export type AccountDetail = S["AccountDetailOut"];
export type InvestmentSummary = S["InvestmentSummary"];
export type FlowPeriod = S["FlowPeriod"];

// ---------- GET /api/dashboard ----------

/** Phase 2 sends only "plan". Charts also accept "actual", which comes later. */
export type Basis = "plan" | "actual";

export type DashboardNetWorth = S["DashboardNetWorth"];
export type NetWorthBehind = S["NetWorthBehind"];
export type PlanMonth = S["PlanMonth"];
export type CashflowMonth = Omit<S["CashflowMonth"], "basis"> & { basis: Basis };
export type SpendingGroup = Omit<S["SpendingGroup"], "basis"> & { basis: Basis };
export type GroupContents = S["GroupContents"];
export type BudgetWatchItem = S["BudgetOut"];
export type BudgetStatus = BudgetWatchItem["status"];
export type NetworthPart = S["NetWorthPart"];
export type DebtItem = S["Debt"];
export type NextChange = S["NextChange"];
export type Todo = S["TodoOut"];
/** value is integer cents. */
export type KeyNumber = S["KeyNumberOut"];
export type Review = S["ReviewOut"];
export type Dashboard = S["DashboardOut"];

// ---------- GET /api/checks ----------

export type CheckStatus = S["CheckLine"]["status"];
export type CheckLine = S["CheckLine"];
export type CheckTotal = S["CheckTotal"];
export type CheckPart = S["CheckPart"];
export type CashflowCheck = S["CashflowCheck"];
export type Checks = S["ChecksOut"];

export type CheckStatusesMatch = [
  Expect<Same<CheckStatus, CheckTotal["status"]>>,
  Expect<Same<CheckStatus, CheckPart["status"]>>,
];
