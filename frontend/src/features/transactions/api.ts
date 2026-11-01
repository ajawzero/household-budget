// Transactions, edits and rules (PLAN 8.2, Phase 4). Types come from the
// generated schema, so a backend change shows up as a type error here.
// Search text goes in a POST body only, never in a URL.

import type { QueryClient } from "@tanstack/react-query";
import { request } from "../../lib/api/client";
import type { components } from "../../lib/api/schema";

type S = components["schemas"];

export type TxnRow = S["TxnRowOut"];
export type TxnSummary = S["TxnSummaryOut"];
export type TxnSearchOut = S["TxnSearchOut"];
export type DayTotal = S["DayTotalOut"];
/** Every search field is optional; the server fills the defaults. */
export type TxnSearchBody = Partial<S["TxnSearchIn"]>;
export type TxnPatch = S["TxnPatchIn"];
export type TxnBulkOut = S["TxnBulkOut"];
export type TxnEvent = S["TxnEventOut"];
export type TxnEvents = S["TxnEventsOut"];
export type Rule = S["RuleOut"];
export type RuleIn = S["RuleIn"];
export type RulePatch = S["RulePatchIn"];
export type RulePreview = S["RulePreviewOut"];
export type MatchKind = Rule["match_kind"];
export type OrphanedEdit = S["OrphanedEditOut"];
export type Sort = NonNullable<TxnSearchBody["sort"]>;

export function searchTransactions(body: TxnSearchBody): Promise<TxnSearchOut> {
  return request("POST", "/api/transactions/search", { body });
}

export function patchTransaction(txnKey: string, changes: TxnPatch): Promise<TxnRow> {
  return request("PATCH", `/api/transactions/${encodeURIComponent(txnKey)}`, { body: changes });
}

export function bulkEdit(txnKeys: string[], changes: TxnPatch): Promise<TxnBulkOut> {
  return request("POST", "/api/transactions/bulk", { body: { txn_keys: txnKeys, changes } });
}

export function getEvents(txnKey: string): Promise<TxnEvents> {
  return request("GET", `/api/transactions/${encodeURIComponent(txnKey)}/events`);
}

export function getOrphanedEdits(): Promise<{ rows: OrphanedEdit[] }> {
  return request("GET", "/api/transactions/orphaned-edits");
}

export function getRules(): Promise<{ rules: Rule[] }> {
  return request("GET", "/api/rules");
}

export function previewRule(body: { match_kind: MatchKind; pattern: string; category_key?: string | null }): Promise<RulePreview> {
  return request("POST", "/api/rules/preview", { body });
}

export function createRule(body: RuleIn): Promise<Rule> {
  return request("POST", "/api/rules", { body });
}

export function patchRule(id: number, changes: RulePatch): Promise<Rule> {
  return request("PATCH", `/api/rules/${id}`, { body: changes });
}

export function deleteRule(id: number): Promise<void> {
  return request("DELETE", `/api/rules/${id}`);
}

export const txnKeys = {
  all: ["transactions"] as const,
  search: (body: TxnSearchBody) => ["transactions", "search", body] as const,
  reviewCount: (kind: string) => ["transactions", "review-count", kind] as const,
  events: (txnKey: string) => ["transactions", "events", txnKey] as const,
  orphaned: ["transactions", "orphaned"] as const,
  rules: ["rules"] as const,
};

/**
 * An edit changes cash flow, budgets, checks and the dashboard too, so every
 * cached query goes stale. Only the ones on screen refetch. The signed-in user
 * and the category list do not depend on edits.
 */
export function invalidateAfterEdit(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({
    predicate: (q) => q.queryKey[0] !== "me" && q.queryKey[0] !== "categories" && q.queryKey[0] !== "meta",
  });
}
