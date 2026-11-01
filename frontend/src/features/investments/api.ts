// Investments and prices API calls, types and query keys (PLAN 6.2, 8.2).
// Kept in the feature so the shared lib/api module stays untouched while
// other Phase 5 pages are built. Types come from the generated schema.ts.
// Money is integer cents. Quantities and prices are integer micro-units.

import { query, request } from "../../lib/api/client";
import type { components } from "../../lib/api/schema";
import type { InvestmentRangeKey } from "../../lib/api/types";
import { formatMoney } from "../../lib/format";

type S = components["schemas"];

export type HoldingsOut = S["HoldingsOut"];
export type HoldingGroup = S["HoldingGroup"];
export type HoldingRow = S["HoldingRow"];
export type GroupBy = HoldingsOut["group_by"];
export type PriceMode = HoldingRow["mode"];
export type AllocationOut = S["AllocationOut"];
export type AllocationSlice = S["AllocationSlice"];
export type AllocationAccount = S["AllocationAccount"];
export type HistoryOut = S["InvestmentsHistoryOut"];
export type HistoryPoint = S["HistoryPoint"];
export type StatementPoint = S["StatementPoint"];
export type EstimatedSegment = S["EstimatedSegment"];
export type PricesStatus = S["PricesStatusOut"];
export type PriceRun = S["PriceRun"];
export type SymbolStatus = S["SymbolStatus"];
export type SanityFlag = S["SanityFlag"];
export type SymbolsOut = S["SymbolsOut"];
export type SymbolRow = S["SymbolRow"];
export type SymbolIn = S["SymbolIn"];
export type ClassIn = S["ClassIn"];
export type ClassChoice = NonNullable<ClassIn["asset_class"]>;
export type InvestmentFlowsOut = S["InvestmentFlowsOut"];
export type AccountFlows = S["AccountFlows"];

export function getHoldings(groupBy: GroupBy): Promise<HoldingsOut> {
  return request("GET", `/api/investments/holdings${query({ group_by: groupBy })}`);
}

export function getAllocation(): Promise<AllocationOut> {
  return request("GET", "/api/investments/allocation");
}

export function getHistory(range: InvestmentRangeKey): Promise<HistoryOut> {
  return request("GET", `/api/investments/history${query({ range })}`);
}

export function getFlows(): Promise<InvestmentFlowsOut> {
  return request("GET", "/api/investments/flows");
}

export function getPricesStatus(): Promise<PricesStatus> {
  return request("GET", "/api/prices/status");
}

export function getSymbols(): Promise<SymbolsOut> {
  return request("GET", "/api/symbols");
}

/** Admin only. price_mode null removes the override, so the rule applies again. */
export function putSymbol(body: SymbolIn): Promise<SymbolRow> {
  return request("PUT", "/api/symbols", { body });
}

/** Admin only. asset_class null removes the pick, so the statement's class applies again. */
export function putClass(body: ClassIn): Promise<SymbolRow> {
  return request("PUT", "/api/symbols/class", { body });
}

export const investmentKeys = {
  all: ["investments"] as const,
  holdings: (groupBy: GroupBy) => ["investments", "holdings", groupBy] as const,
  allocation: ["investments", "allocation"] as const,
  history: (range: InvestmentRangeKey) => ["investments", "history", range] as const,
  flows: ["investments", "flows"] as const,
  pricesStatus: ["investments", "prices-status"] as const,
  symbols: ["investments", "symbols"] as const,
};

// ---------- Labels ----------

/** Short words for each price mode, never the internal names. */
export const MODE_LABELS: Record<PriceMode, string> = {
  api: "Daily price",
  constant: "Fixed $1",
  statement: "Statement",
};

/** What each mode means, for tooltips and Settings. */
export const MODE_HELP: Record<PriceMode, string> = {
  api: "Priced each weekday from market closes. The value moves between statements.",
  constant: "Cash or a money market fund. Always $1.00 a share.",
  statement: "No market price. The value is the one on the latest statement.",
};

// The backend's fallback label capitalizes the first letter only ("Us stock").
const CLASS_LABELS: Record<string, string> = {
  "us-stock": "US stock",
  "intl-stock": "International stock",
  "international-stock": "International stock",
  bond: "Bonds",
  bonds: "Bonds",
  cash: "Cash",
  "money-market": "Money market",
  "target-date": "Target date",
  "real-estate": "Real estate",
  balanced: "Balanced",
  other: "Other",
  unclassified: "No class",
};

/** The classes an admin can pick (backend market/classes.py), in its order. */
export const CLASS_CHOICES: readonly ClassChoice[] = [
  "us-stock",
  "intl-stock",
  "bond",
  "cash",
  "target-date",
  "balanced",
  "real-estate",
  "other",
];

/** A readable name for an asset class key. */
export function classLabel(key: string | null | undefined, fallback?: string): string {
  if (!key) return CLASS_LABELS.unclassified!;
  return CLASS_LABELS[key] ?? fallback ?? key.replace(/-/g, " ").replace(/^./, (c) => c.toUpperCase());
}

// ---------- Numbers ----------

const MICRO = 1_000_000;

/** Shares from micro-units: up to 3 decimals, trailing zeros dropped ("138.288"). */
export function formatQuantity(micro: number | null | undefined): string {
  if (micro === null || micro === undefined) return "";
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 3 }).format(micro / MICRO);
}

/** A price in micro-units as dollars and cents ("$262.65"). Integer math, half up. */
export function formatPrice(micro: number | null | undefined): string {
  if (micro === null || micro === undefined) return "";
  const cents = Math.sign(micro) * Math.floor((Math.abs(micro) + 5_000) / 10_000);
  return formatMoney(cents, { cents: true });
}

/** "1.50" to "+1.50%", "-1.25" to "−1.25%". */
export function formatGap(pct: string): string {
  const n = Number(pct);
  if (!Number.isFinite(n)) return pct;
  return `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(2)}%`;
}
