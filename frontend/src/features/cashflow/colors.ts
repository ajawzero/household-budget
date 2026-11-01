import type { BreakdownItem } from "./api";

/**
 * Spending group to ring color, in one place. Color follows the group, never
 * its rank. The ring uses four category slots (purple, amber, pink, green):
 * those four pass the palette validator on every pair in light mode, and
 * reach 6.9 in dark mode, which the 2px gaps, the legend and the table cover.
 * Ten groups share four colors, so groups that rarely both rank high share.
 * A group whose color is already in the ring folds into "Everything else".
 */
export const GROUP_RING_COLORS: Record<string, string> = {
  housing: "var(--chart-7)",
  debt: "var(--chart-7)",
  lifestyle: "var(--chart-4)",
  insurance: "var(--chart-4)",
  kids: "var(--chart-5)",
  fees: "var(--chart-5)",
  "card-no-statement": "var(--chart-5)",
  discretionary: "var(--chart-6)",
  services: "var(--chart-6)",
  transportation: "var(--chart-6)",
};

export const OTHER_KEY = "everything-else";
export const MAX_SLICES = 4;

export interface Slice {
  key: string;
  label: string;
  cents: number;
  share_pct: string | null;
  /** A token var. Null for "Everything else", which is drawn with a texture. */
  color: string | null;
  /** Groups folded into "Everything else". */
  members: string[];
}

/** Up to four colored slices, largest first, then "Everything else". Values are positive cents. */
export function ringSlices(groups: BreakdownItem[], totalCents: number): Slice[] {
  const used = new Set<string>();
  const slices: Slice[] = [];
  const other: Slice = { key: OTHER_KEY, label: "Everything else", cents: 0, share_pct: null, color: null, members: [] };
  for (const g of [...groups].sort((a, b) => b.cents - a.cents)) {
    if (g.cents <= 0) continue;
    const color = GROUP_RING_COLORS[g.key];
    if (color && !used.has(color) && slices.length < MAX_SLICES) {
      used.add(color);
      slices.push({ key: g.key, label: g.label, cents: g.cents, share_pct: g.share_pct, color, members: [] });
    } else {
      other.cents += g.cents;
      other.members.push(g.label);
    }
  }
  if (other.cents > 0) {
    other.share_pct = totalCents > 0 ? ((other.cents / totalCents) * 100).toFixed(1) : null;
    slices.push(other);
  }
  return slices;
}
