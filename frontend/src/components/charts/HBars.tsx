import type { ReactNode } from "react";
import { cn } from "../../lib/cn";
import { formatPct } from "../../lib/format";
import { Money } from "../money/Money";

export interface HBarRow {
  key: string;
  label: string;
  /** Cents. Negative values grow left from the zero line. */
  cents: number;
  /** A short line under the bar, shown on every screen (also belongs in the table view). */
  note?: string | null;
  /** A token var to override the bar color for this row. */
  color?: string;
  /** Share of its total in percent, one decimal. Shown after the value, muted. */
  share?: number | null;
}

interface HBarsProps {
  rows: HBarRow[];
  /** One series, one color. A token var. */
  color?: string;
  /** Bars for negative values (owed amounts). */
  negativeColor?: string;
  /** "$1.2M" values instead of whole dollars. */
  compact?: boolean;
  /** Extra text after a value, like a percent. */
  suffix?: (row: HBarRow) => ReactNode;
  /** What a row's share is of, for the hover text ("all assets"). */
  shareOf?: (row: HBarRow) => string;
}

/**
 * Ranked horizontal bars: label and value on one line, the bar under them.
 * Plain HTML and CSS, so it reflows on phones. 4px rounded data end, square
 * at the zero line. Mixed signs share one zero line.
 */
export function HBars({
  rows,
  color = "var(--chart-income)",
  negativeColor = "var(--chart-8)",
  compact = false,
  suffix,
  shareOf,
}: HBarsProps) {
  const lo = Math.min(0, ...rows.map((r) => r.cents));
  const hi = Math.max(0, ...rows.map((r) => r.cents));
  const span = hi - lo || 1;
  const zero = (-lo / span) * 100;
  // One row with a share gives every row the column, so the values line up.
  const showShare = rows.some((r) => r.share != null);

  return (
    <ul className="m-0 flex list-none flex-col gap-3 p-0">
      {rows.map((r) => {
        const pct = (Math.abs(r.cents) / span) * 100;
        const neg = r.cents < 0;
        const shareTip = r.share != null ? `${formatPct(r.share, true)} of ${shareOf?.(r) ?? "the total"}` : null;
        return (
          <li key={r.key} title={shareTip ?? undefined}>
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className="min-w-0 truncate text-fg">{r.label}</span>
              <span className="shrink-0 text-fg">
                <Money cents={r.cents} compact={compact} />
                {suffix?.(r)}
                {/* The space keeps the value and the share apart in copied text. */}
                {showShare && (
                  <span className="ml-2 inline-block w-14 text-right text-fg-secondary tabular">
                    {r.share == null ? "" : ` ${formatPct(r.share, true)}`}
                  </span>
                )}
              </span>
            </div>
            <div className="relative mt-1 h-2" aria-hidden="true">
              {lo < 0 && <span className="absolute inset-y-[-2px] w-px bg-line-strong" style={{ left: `${zero}%` }} />}
              <span
                className={cn("absolute inset-y-0 transition-[left,width] duration-(--dur-slow) ease-(--ease-out)", neg ? "rounded-l" : "rounded-r")}
                style={{
                  left: neg ? `${zero - pct}%` : `${zero}%`,
                  width: `${Math.max(pct, 0.5)}%`,
                  background: r.color ?? (neg ? negativeColor : color),
                }}
              />
            </div>
            {r.note && <p className="m-0 mt-1 break-words text-xs text-fg-secondary">{r.note}</p>}
          </li>
        );
      })}
    </ul>
  );
}
