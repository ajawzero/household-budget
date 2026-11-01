import { formatMoney } from "../../../lib/format";

/**
 * The paychecks still to come: green stripes with a green outline, so the
 * segment stands apart from the empty track (3:1) in both themes. Solid green
 * is what is already in.
 */
const PROJECTED = {
  background:
    "repeating-linear-gradient(135deg, var(--chart-leftover) 0 2px, color-mix(in oklab, var(--chart-leftover) 30%, var(--bg-card)) 2px 5px)",
  boxShadow: "inset 0 0 0 1px var(--chart-leftover)",
} as const;

interface ContributionMeterProps {
  /** Cents paid in so far this year. */
  ytd: number;
  /** Cents by Dec 31 at the current election. Null without a pay schedule. */
  projected: number | null;
  /** Cents. Null when there is no limit to compare to. */
  limit: number | null;
  /** Screen reader name, like "Alex, 401k". */
  label: string;
}

/**
 * Year-to-date against the IRS limit. The solid bar is what is in, the pale
 * part is what the paychecks left add by Dec 31, the dark tick is the limit.
 * Saved money, so the bar uses the left over green, never a status color.
 * The status line beside it says over or under in words.
 */
export function ContributionMeter({ ytd, projected, limit, label }: ContributionMeterProps) {
  const top = Math.max(limit ?? 0, projected ?? 0, ytd, 1);
  const pct = (c: number) => `${Math.min(Math.max(c / top, 0), 1) * 100}%`;
  const ytdWidth = pct(ytd);
  const restWidth = projected !== null && projected > ytd ? pct(projected - ytd) : "0%";
  const aria = [
    `${label}: ${formatMoney(ytd)} so far`,
    projected !== null ? `${formatMoney(projected)} projected by Dec 31` : null,
    limit !== null ? `limit ${formatMoney(limit)}` : "no limit set",
  ]
    .filter(Boolean)
    .join(", ");
  return (
    <div
      role="img"
      aria-label={aria}
      className="relative h-2.5 rounded-full"
      style={{ background: "var(--bg-subtle)", boxShadow: "inset 0 0 0 1px var(--border)" }}
    >
      <div className="absolute inset-0 flex gap-0.5 overflow-hidden rounded-full">
        <span className="h-full shrink-0" style={{ width: ytdWidth, background: "var(--chart-leftover)" }} />
        {restWidth !== "0%" && (
          <span
            className="h-full shrink-0"
            style={{ width: `calc(${restWidth} - 2px)`, ...PROJECTED }}
          />
        )}
      </div>
      {limit !== null && (
        <span
          aria-hidden="true"
          className="absolute -top-1 h-[18px] w-0.5 rounded-full bg-chart-target"
          style={{ left: `calc(${pct(limit)} - 1px)` }}
        />
      )}
    </div>
  );
}

/** The legend under the meter: swatch, words and the amount in text color. */
export function MeterLegend({ ytd, projected, limit }: { ytd: number; projected: number | null; limit: number | null }) {
  return (
    <ul className="m-0 mt-2 flex list-none flex-wrap gap-x-5 gap-y-1 p-0 text-sm text-fg-secondary">
      <li className="inline-flex items-center gap-1.5">
        <span aria-hidden="true" className="h-2.5 w-2.5 rounded-full" style={{ background: "var(--chart-leftover)" }} />
        So far <span className="tabular-nums text-fg">{formatMoney(ytd)}</span>
      </li>
      {projected !== null && (
        <li className="inline-flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="h-2.5 w-2.5 rounded-full"
            style={PROJECTED}
          />
          By Dec 31 <span className="tabular-nums text-fg">{formatMoney(projected)}</span>
        </li>
      )}
      {limit !== null && (
        <li className="inline-flex items-center gap-1.5">
          <span aria-hidden="true" className="h-3 w-0.5 rounded-full bg-chart-target" />
          Limit <span className="tabular-nums text-fg">{formatMoney(limit)}</span>
        </li>
      )}
    </ul>
  );
}
