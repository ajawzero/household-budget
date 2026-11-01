// Small pure helpers for the Accounts page. No React here, so tests stay simple.

import { groupColor } from "../../components/charts/colors";
import {
  type AccountGroup,
  DEFAULT_RANGE,
  RANGE_KEYS,
  SERIES_KEYS,
  type AccountRow,
  type AccountsResponse,
  type RangeKey,
  type SeriesKey,
} from "../../lib/api";

/** Groups whose balances are owed. Rows and totals show them as positive numbers. */
const OWED_GROUPS = new Set<string>(["credit_cards", "loans"]);

export function isOwedGroup(group: string): boolean {
  return OWED_GROUPS.has(group);
}

/** The number to show: owed amounts as positive, everything else as signed. */
export function shownCents(cents: number, group: string): number {
  return isOwedGroup(group) ? -cents : cents;
}

export const SERIES_LABELS: Record<SeriesKey, string> = {
  networth: "Net worth",
  cash: "Cash",
  credit_cards: "Credit cards",
  investments: "Investments",
  retirement: "Retirement",
  real_estate: "Real estate",
  vehicles: "Vehicles",
  loans: "Loans",
};

export function parseRange(value: string | null): RangeKey {
  return (RANGE_KEYS as readonly string[]).includes(value ?? "") ? (value as RangeKey) : DEFAULT_RANGE;
}

export function parseSeries(value: string | null): SeriesKey {
  return (SERIES_KEYS as readonly string[]).includes(value ?? "") ? (value as SeriesKey) : "networth";
}

const TYPE_LABELS: Record<string, string> = {
  checking: "Checking",
  savings: "Savings",
  credit_card: "Credit card",
  store_card: "Store card",
  loan: "Loan",
  mortgage: "Mortgage",
  heloc: "HELOC",
  brokerage: "Brokerage",
  retirement: "Retirement",
  "529": "529 plan",
  hsa: "HSA",
  custodial: "Custodial",
  nqdc: "Deferred comp",
  property: "Property",
  vehicle: "Vehicle",
  other: "Other",
};

/** "Checking", or the subtype the export gave. */
export function typeLabel(row: Pick<AccountRow, "type" | "subtype">): string {
  return row.subtype || TYPE_LABELS[row.type] || row.type;
}

/** Homes and vehicles: valued, not held at a bank, so no institution to show. */
export function isValuedAsset(row: Pick<AccountRow, "type">): boolean {
  return row.type === "property" || row.type === "vehicle";
}

/**
 * The gray line on rows and in the drawer: "Checking · Sam · Harbor Credit Union",
 * or "Property · Joint" for valued assets. Owner before institution, so a cut
 * off line still says whose it is.
 */
export function metaLine(row: Pick<AccountRow, "type" | "subtype" | "institution" | "owner_label">): string {
  return [typeLabel(row), row.owner_label, isValuedAsset(row) ? null : row.institution].filter(Boolean).join(" · ");
}

/** Monogram letters come from the institution, or the account name for valued assets. */
export function monogramName(row: Pick<AccountRow, "type" | "institution" | "display_name">): string {
  return isValuedAsset(row) ? row.display_name : row.institution;
}

/**
 * A custodian or servicer worth showing: null when it is empty or is the
 * institution again, apart from case and spaces.
 */
export function otherName(name: string | null, institution: string): string | null {
  const n = name?.trim();
  return n && n.toLowerCase() !== institution.trim().toLowerCase() ? n : null;
}

/** What the balance cell says when there is no number to show. */
export type BalanceState = "tracked" | "missing" | "closed" | "balance";

export function balanceState(row: AccountRow): BalanceState {
  if (row.tracked_by === "payments" || row.source === "tracked") return "tracked";
  if (row.balance_cents === null || row.missing) return "missing";
  if (!row.active && row.source === null) return "closed";
  return "balance";
}

/** Account keys to display names, for the notes strip. */
export function nameMap(data: AccountsResponse): Map<string, string> {
  const map = new Map<string, string>();
  for (const g of data.groups) for (const a of g.accounts) map.set(a.account_key, a.display_name);
  return map;
}

/**
 * The account group each nw_group mostly holds, so By owner can borrow the
 * By type color. The largest asset in net worth decides (a netted equity
 * group is its home or vehicle); a group with no assets uses its largest
 * owed account.
 */
export function ownerGroupTypes(groups: AccountGroup[]): Map<string, string> {
  const best = new Map<string, { asset: boolean; size: number; group: string }>();
  for (const g of groups) {
    for (const a of g.accounts) {
      if (!a.nw_group || !a.include_in_net_worth) continue;
      const next = { asset: a.is_asset, size: Math.abs(a.balance_cents ?? 0), group: a.group };
      const cur = best.get(a.nw_group);
      if (!cur || (next.asset && !cur.asset) || (next.asset === cur.asset && next.size > cur.size)) {
        best.set(a.nw_group, next);
      }
    }
  }
  return new Map([...best].map(([k, v]) => [k, v.group]));
}

/** Free slots for owner groups whose type color is taken. No spending orange. */
const SPARE_SLOTS = [
  "var(--chart-leftover)",
  "var(--chart-income)",
  "var(--chart-7)",
  "var(--chart-5)",
  "var(--chart-4)",
  "var(--chart-6)",
  "var(--chart-8)",
] as const;

/**
 * Colors for nw_group keys, one map for both sides. Each key takes the color
 * of its account group (By type), so Credit cards is red in both views. When
 * two keys share a group, the first by name keeps the color and the other
 * takes the first free slot. Never by size or rank.
 */
export function ownerColors(keys: string[], types: Map<string, string>): Map<string, string> {
  const map = new Map<string, string>();
  const used = new Set<string>();
  const sorted = [...new Set(keys)].sort();
  for (const k of sorted) {
    const t = types.get(k);
    const color = t ? groupColor(t) : null;
    if (color && color !== "var(--chart-muted)" && !used.has(color)) {
      map.set(k, color);
      used.add(color);
    }
  }
  for (const k of sorted) {
    if (map.has(k)) continue;
    const color = SPARE_SLOTS.find((c) => !used.has(c)) ?? "var(--chart-muted)";
    map.set(k, color);
    used.add(color);
  }
  return map;
}
