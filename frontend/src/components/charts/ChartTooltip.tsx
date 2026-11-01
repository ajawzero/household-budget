import type { ReactNode } from "react";

export interface TooltipRow {
  key: string;
  label: string;
  value: ReactNode;
  /** Series color, shown as a short line key. A token var, like "var(--chart-income)". */
  color?: string;
}

/** The hover box for Recharts charts. Value first, series name second. */
export function ChartTooltipBox({ title, rows, note }: { title: ReactNode; rows: TooltipRow[]; note?: ReactNode }) {
  return (
    <div className="min-w-40 rounded-lg bg-card px-3 py-2 text-sm text-fg shadow-pop">
      <p className="m-0 mb-1 text-xs text-fg-secondary">{title}</p>
      {rows.map((r) => (
        <p key={r.key} className="m-0 flex items-center gap-2">
          {r.color && <span aria-hidden="true" className="h-0.5 w-3 rounded-full" style={{ background: r.color }} />}
          <span className="tabular font-semibold">{r.value}</span>
          <span className="text-fg-secondary">{r.label}</span>
        </p>
      ))}
      {note && <p className="m-0 mt-1 text-xs text-warn">{note}</p>}
    </div>
  );
}

/** Shared axis text style. Text tokens, never series colors. */
export const AXIS_TICK = { fill: "var(--text-secondary)", fontSize: 12 } as const;

/**
 * Series animation: short, like the rest of the app's motion. Recharts' default
 * is 1.5 s. Each series still turns it off under reduced motion.
 */
export const CHART_ANIMATION_MS = 500;
