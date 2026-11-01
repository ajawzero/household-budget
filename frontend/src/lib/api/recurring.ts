// Phase 4 reads for Recurring, Plans, Debt, Flags & To do, Pending and the
// statement coverage grid. All GET, all read-only: the chat owns this state
// in v1. Types come from the generated schema.ts. index.ts re-exports this file.

import { query, request } from "./client";
import type { components } from "./schema";

type S = components["schemas"];

// ---------- GET /api/recurring ----------

export type RecurringView = "monthly" | "all";
export type RecurringResponse = S["RecurringOut"];
/** One due date of an item in a month, with its paid state. */
export type RecurringDue = S["DueOut"];
export type RecurringDueState = RecurringDue["state"];
/** One item with its monthly equivalent (the All tab). */
export type RecurringItem = S["RecurringItem"];
export type RecurringCadence = RecurringItem["cadence"];
export type RecurringStatus = RecurringItem["status"];
/** The chat's open or watching cost cutting flag on an item. */
export type RecurringCostCut = S["RecurringCostCut"];
export type RecurringSummary = S["RecurringSummary"];
export type RecurringTotals = S["RecurringTotals"];
export type RecurringCalendar = S["RecurringCalendarOut"];
export type RecurringCalendarDay = S["CalendarDay"];

// ---------- GET /api/plans ----------

export type PlansResponse = S["PlansOut"];
export type InstallmentPlan = S["InstallmentPlanOut"];
export type PlanCard = S["PlanCard"];
export type PlanStepDown = S["StepDown"];
export type PlanWarningRow = S["PlanWarningRow"];
export type PlanBrief = S["PlanBrief"];

// ---------- GET /api/debt ----------

export type DebtResponse = S["DebtOut"];
export type DebtLedgerRow = S["DebtRow"];
export type DebtRateChange = S["DebtRateChange"];
export type DebtPick = S["DebtPick"];
export type PayoffStep = S["PayoffStepOut"];
export type TrackedDebt = S["TrackedDebt"];

// ---------- Flags, to do and pending ----------

export type FlagsResponse = S["FlagsOut"];
export type FlagItem = S["FlagOut"];
export type FlagSeverity = FlagItem["severity"];
export type FlagStatus = FlagItem["status"];
export type FlagAction = FlagItem["action"];
export type FlagUpdate = FlagItem["updates"][number];
export type TodosResponse = S["TodosOut"];
export type TodoListItem = S["TodoItem"];
export type PendingResponse = S["PendingListOut"];
export type PendingItem = S["PendingOut"];

// ---------- GET /api/statements/coverage ----------

export type CoverageResponse = S["CoverageOut"];
export type CoverageAccount = S["CoverageAccount"];
export type CoverageCell = S["CoverageCell"];
export type CoverageStatus = CoverageCell["status"];

// ---------- Dashboard and Checks, Phase 4 parts ----------

export type DashboardActualMonth = S["ActualMonth"];
export type DashboardUpcoming = S["UpcomingItem"];
export type UnpairedTransfer = S["UnpairedTransfer"];
export type CardPaymentNoStatement = S["CardNoStatement"];
export type MissingStatement = S["MissingStatement"];
export type PaymentBasisWatch = S["WatchItem"];
export type PlanMapRow = S["PlanMap"];

// ---------- Calls ----------

export function getRecurring(opts: { view?: RecurringView; month?: string } = {}): Promise<RecurringResponse> {
  return request("GET", `/api/recurring${query({ view: opts.view, month: opts.month })}`);
}

export function getRecurringCalendar(month?: string): Promise<RecurringCalendar> {
  return request("GET", `/api/recurring/calendar${query({ month })}`);
}

export function getPlans(): Promise<PlansResponse> {
  return request("GET", "/api/plans");
}

export function getDebt(): Promise<DebtResponse> {
  return request("GET", "/api/debt");
}

export function getFlags(includeArchived = false): Promise<FlagsResponse> {
  return request("GET", `/api/flags${query({ include_archived: includeArchived ? "true" : undefined })}`);
}

export function getTodos(): Promise<TodosResponse> {
  return request("GET", "/api/todos");
}

export function getPending(): Promise<PendingResponse> {
  return request("GET", "/api/pending");
}

export function getCoverage(opts: { from?: string; to?: string } = {}): Promise<CoverageResponse> {
  return request("GET", `/api/statements/coverage${query({ from: opts.from, to: opts.to })}`);
}

/** Query keys for the calls above. */
export const recurringKeys = {
  recurring: (view: RecurringView, month?: string) => ["recurring", view, month ?? null] as const,
  calendar: (month?: string) => ["recurring-calendar", month ?? null] as const,
  plans: ["plans"] as const,
  debt: ["debt"] as const,
  flags: (includeArchived: boolean) => ["flags", includeArchived] as const,
  todos: ["todos"] as const,
  pending: ["pending"] as const,
  coverage: (from?: string, to?: string) => ["statements-coverage", from ?? null, to ?? null] as const,
};
