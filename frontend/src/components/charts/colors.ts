import type { AccountGroupKey } from "../../lib/api";

/**
 * Fixed color slot per account group, in one place (PLAN 8.3). Color follows
 * the group, never its rank, so a filter never repaints the others.
 * Slots 1 to 3 are income, spending and left over elsewhere, so groups start
 * at the category slots and only reuse 1 to 3 where those meanings cannot
 * appear in the same chart.
 */
export const GROUP_COLORS: Record<AccountGroupKey, string> = {
  cash: "var(--chart-leftover)",
  investments: "var(--chart-income)",
  retirement: "var(--chart-7)",
  real_estate: "var(--chart-5)",
  vehicles: "var(--chart-4)",
  other: "var(--chart-6)",
  credit_cards: "var(--chart-8)",
  // Loans share purple with retirement: they never sit in the same bar, and
  // amber next to the card red fails the normal-vision check in dark mode.
  loans: "var(--chart-7)",
};

export function groupColor(group: string): string {
  return GROUP_COLORS[group as AccountGroupKey] ?? "var(--chart-muted)";
}
