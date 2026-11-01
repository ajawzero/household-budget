import type { AllocationSlice } from "./api";

/**
 * Asset class to color, in one place. Color follows the class, never its
 * rank, so the same class has the same color in every bar on the page.
 * Like the Cash Flow ring, the bars use the four category slots that pass
 * the palette validator on every pair (purple, amber, pink, green; light all
 * pairs pass, dark worst 6.9 protan, covered by the 2px gaps, the legend and
 * the table). Blue is left out: blue next to purple fails. Orange means
 * spending. Classes that share a color rarely sit in one household's top
 * four. A class whose color is already shown folds into "Other classes",
 * a gray hatch, never a fifth color.
 */
export const CLASS_COLORS: Record<string, string> = {
  "us-stock": "var(--chart-7)",
  stock: "var(--chart-7)",
  bond: "var(--chart-4)",
  bonds: "var(--chart-4)",
  "real-estate": "var(--chart-4)",
  "target-date": "var(--chart-5)",
  "intl-stock": "var(--chart-5)",
  "international-stock": "var(--chart-5)",
  cash: "var(--chart-6)",
  "money-market": "var(--chart-6)",
};

export const OTHER_CLASSES = "other-classes";

/** The gray hatch for "Other classes", as a CSS background. */
export const OTHER_FILL =
  "repeating-linear-gradient(135deg, var(--chart-muted) 0 2px, var(--bg-subtle) 2px 5px)";

export interface ClassColor {
  /** A CSS background: a token var, or the hatch. */
  fill: string;
  /** The legend key it belongs to: its own key, or OTHER_CLASSES. */
  legendKey: string;
}

/**
 * Picks colors for the household's classes, largest first: a class gets its
 * color when no bigger class has it yet, else it folds into "Other classes".
 */
export function classColorMap(classes: AllocationSlice[]): Map<string, ClassColor> {
  const used = new Set<string>();
  const out = new Map<string, ClassColor>();
  for (const c of [...classes].sort((a, b) => b.value_cents - a.value_cents)) {
    const color = CLASS_COLORS[c.key];
    if (color && !used.has(color)) {
      used.add(color);
      out.set(c.key, { fill: color, legendKey: c.key });
    } else {
      out.set(c.key, { fill: OTHER_FILL, legendKey: OTHER_CLASSES });
    }
  }
  return out;
}
