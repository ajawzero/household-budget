// Budget API calls, types and query key. Kept in the feature so the shared
// lib/api module stays untouched while other pages are built.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError, query, request } from "../../lib/api/client";
import type { components } from "../../lib/api/schema";
import { invalidateAfterEdit } from "../transactions/api";

type S = components["schemas"];

export type BudgetsOut = S["BudgetsOut"];
export type BudgetGroup = S["BudgetGroup"];
export type BudgetCategory = S["BudgetCategory"];
export type ChatBudget = S["ChatBudget"];
export type BudgetTargetsIn = S["BudgetTargetsIn"];
export type TargetChangeIn = S["TargetChangeIn"];

/** One month's budget. Leave out the month for the latest complete month. */
export function getBudgets(month?: string): Promise<BudgetsOut> {
  return request("GET", `/api/budgets${query({ month })}`);
}

/** Change group targets for a month (admin only). Answers with the month's page. */
export function putTargets(body: BudgetTargetsIn): Promise<BudgetsOut> {
  return request("PUT", "/api/budgets/targets", { body });
}

/**
 * Save target changes. A target feeds Cash Flow, the Dashboard, Checks and Ask
 * too, so every cached query goes stale, as after a transaction edit. The
 * refetch finishes before the caller's onSuccess, so the form never shows the
 * old numbers. A 409 means an import changed the chat's plan: the page refetches
 * so the form shows the chat's new numbers, and what was typed stays.
 */
export function useSaveTargets() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: putTargets,
    onSuccess: () => invalidateAfterEdit(qc),
    onError: (err) => {
      if (err instanceof ApiError && err.status === 409) void invalidateAfterEdit(qc);
    },
  });
}

export const budgetKeys = {
  month: (month?: string) => ["budgets", month ?? null] as const,
};

/** The first month with data. Cash flow numbers start in Jan 2026. */
export const FIRST_MONTH = "2026-01";
