// Utility bills (format 1.2): GET /api/utilities and GET /api/utilities/{key}.
// Read-only. Types come from the generated schema.ts. index.ts re-exports this file.
// Usage is integer micro-units of the line's unit. Cost per unit is in
// thousandths of a cent. Percent changes are strings with one decimal.

import { query, request } from "./client";
import type { components } from "./schema";

type S = components["schemas"];

export type UtilitiesResponse = S["UtilitiesOut"];
export type UtilitySummary = S["UtilitySummary"];
export type UtilityService = S["ServiceUnit"]["service"];
export type UtilityUnit = S["ServiceUnit"]["unit"];
export type UtilityDetail = S["UtilityDetailOut"];
export type UtilityBill = S["BillOut"];
export type UtilityBillStatus = UtilityBill["status"];
export type UtilityBillLine = S["LineOut"];
export type UtilityBillPayment = S["BillPayment"];
export type UtilityYoyRow = S["YoyRow"];
export type UtilityYoyService = S["YoyService"];
export type UtilityYoyValue = S["YoyValue"];
export type UtilityBillBeforeValue = S["BillBeforeValue"];
export type UtilityCostPoint = S["CostPoint"];
export type UtilityCostService = S["CostService"];
export type UtilityCreditPoint = S["CreditPoint"];
export type UtilityWarning = S["UtilityWarning"];
export type UtilityPastDue = S["UtilityPastDue"];
export type UtilityPaymentNoBill = S["UtilityPaymentNoBill"];

export function getUtilities(): Promise<UtilitiesResponse> {
  return request("GET", "/api/utilities");
}

/** from and to are YYYY-MM months on the bill's period end. Both optional. */
export function getUtility(key: string, opts: { from?: string; to?: string } = {}): Promise<UtilityDetail> {
  return request("GET", `/api/utilities/${encodeURIComponent(key)}${query({ from: opts.from, to: opts.to })}`);
}

/** Query keys for the calls above. */
export const utilityKeys = {
  list: ["utilities"] as const,
  detail: (key: string, from?: string, to?: string) => ["utility", key, from ?? null, to ?? null] as const,
};
