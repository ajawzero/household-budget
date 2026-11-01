// Cash Flow API calls, types and query keys. Kept in the feature so the
// shared lib/api module stays untouched while other pages are built.
// Response types come from the generated schema.ts.

import { query, request } from "../../lib/api/client";
import type { components } from "../../lib/api/schema";

type S = components["schemas"];

export type CashflowOut = S["CashflowOut"];
export type CashflowMonthOut = S["CashflowMonthOut"];
export type KpiOut = S["KpiOut"];
export type PlanValues = S["PlanValues"];
export type PlanVsActual = S["PlanVsActual"];
export type PlanLine = S["PlanLine"];
export type TrackedCards = S["TrackedCards"];
export type WaitingReimbursement = S["WaitingReimbursement"];
export type BreakdownOut = S["BreakdownOut"];
export type BreakdownItem = S["BreakdownItem"];
export type BreakdownBy = BreakdownOut["by"];
export type CategoryDetailOut = S["CategoryDetailOut"];
export type CategoryMonth = S["CategoryMonth"];
export type CashflowPlanOut = S["CashflowPlanOut"];
export type PlanMonthOut = S["PlanMonthOut"];
export type PlanEvent = S["EventOut"];
export type PlanPhase = S["PhaseOut"];
export type IncomeOut = S["IncomeOut"];
export type IncomeMonth = S["IncomeMonth"];
export type IncomeStream = S["StreamOut"];
export type Paycheck = S["PaycheckOut"];
export type MonthStatus = CashflowMonthOut["status"];

export interface MonthRange {
  from?: string;
  to?: string;
}

export function getCashflow(range: MonthRange = {}): Promise<CashflowOut> {
  return request("GET", `/api/cashflow${query({ from: range.from, to: range.to })}`);
}

export function getBreakdown(opts: MonthRange & { by?: BreakdownBy } = {}): Promise<BreakdownOut> {
  return request("GET", `/api/cashflow/breakdown${query({ from: opts.from, to: opts.to, by: opts.by })}`);
}

export function getCategoryDetail(key: string, range: MonthRange = {}): Promise<CategoryDetailOut> {
  return request("GET", `/api/cashflow/category/${encodeURIComponent(key)}${query({ from: range.from, to: range.to })}`);
}

export function getCashflowPlan(): Promise<CashflowPlanOut> {
  return request("GET", "/api/cashflow/plan");
}

export function getIncome(range: MonthRange = {}): Promise<IncomeOut> {
  return request("GET", `/api/income${query({ from: range.from, to: range.to })}`);
}

export const cashflowKeys = {
  cashflow: (r: MonthRange) => ["cashflow", r.from ?? null, r.to ?? null] as const,
  breakdown: (r: MonthRange, by: BreakdownBy) => ["cashflow-breakdown", r.from ?? null, r.to ?? null, by] as const,
  category: (key: string, r: MonthRange) => ["cashflow-category", key, r.from ?? null, r.to ?? null] as const,
  plan: ["cashflow-plan"] as const,
  income: (r: MonthRange) => ["income", r.from ?? null, r.to ?? null] as const,
};

// ---------- Months ----------

const MONTH_RE = /^(\d{4})-(\d{2})$/;

export function isMonth(value: string | null | undefined): value is string {
  const m = value ? MONTH_RE.exec(value) : null;
  return !!m && Number(m[2]) >= 1 && Number(m[2]) <= 12;
}

/** Status words for a month, for tables and tooltips. */
export const STATUS_TEXT: Record<MonthStatus, string> = {
  complete: "Complete",
  partial: "Partial, statements not all in",
  current: "This month, plan",
  future: "Plan",
};
