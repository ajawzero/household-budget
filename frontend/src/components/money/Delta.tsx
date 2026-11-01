import { formatCompact, formatMoney, formatPct } from "../../lib/format";
import { cn } from "../../lib/cn";

/**
 * higher_is_better: signed balances and net worth. A card balance is negative,
 * so paying it down is a positive change and reads as good.
 * lower_is_better: spending, fees, interest and budget actuals.
 */
export type Polarity = "higher_is_better" | "lower_is_better";
export type Tone = "good" | "bad" | "neutral";

/** Good, bad or neutral for a change, by what "better" means for the value. */
export function deltaTone(delta: number | null | undefined, polarity: Polarity): Tone {
  if (delta === null || delta === undefined || !Number.isFinite(delta) || delta === 0) return "neutral";
  const up = delta > 0;
  return up === (polarity === "higher_is_better") ? "good" : "bad";
}

const TONE_CLASS: Record<Tone, string> = {
  good: "text-good",
  bad: "text-bad",
  neutral: "text-fg-secondary",
};

interface DeltaProps {
  /** The signed change, from the net worth view (owed amounts are negative). */
  cents: number | null | undefined;
  polarity?: Polarity;
  /**
   * "owed" when the row shows owed amounts as positive numbers (cards, loans).
   * The arrow then follows the shown value: owed going down shows "↓".
   */
  display?: "signed" | "owed";
  /** Percent change as the API sends it ("4.2"). */
  pct?: string | number | null;
  /** Gray label after the change, like "Year to date change". */
  period?: string;
  /** Vertical arrows on Accounts, diagonal on the Dashboard. */
  arrows?: "vertical" | "diagonal";
  withCents?: boolean;
  compact?: boolean;
  className?: string;
}

export function Delta({
  cents,
  polarity = "higher_is_better",
  display = "signed",
  pct,
  period,
  arrows = "vertical",
  withCents = false,
  compact = false,
  className,
}: DeltaProps) {
  if (cents === null || cents === undefined || !Number.isFinite(cents)) return null;
  const tone = deltaTone(cents, polarity);
  const shown = display === "owed" ? -cents : cents;
  const abs = Math.abs(shown);
  const amount = compact ? formatCompact(abs) : formatMoney(abs, { cents: withCents });
  const isZero = amount === "$0" || amount === "$0.00";
  const dir = isZero ? null : shown > 0 ? "up" : "down";
  const arrow = dir === null ? "" : arrows === "vertical" ? (dir === "up" ? "↑" : "↓") : dir === "up" ? "↗" : "↘";
  const pctText = pct === null || pct === undefined ? "" : formatPct(typeof pct === "string" ? pct.replace(/^-/, "") : Math.abs(pct));

  return (
    <span className={cn("inline-flex flex-wrap items-baseline gap-x-1.5 text-sm", className)}>
      <span className={cn("tabular whitespace-nowrap font-medium", TONE_CLASS[tone])}>
        {arrow && <span aria-hidden="true">{arrow} </span>}
        {dir && <span className="sr-only">{dir} </span>}
        {isZero ? "No change" : amount}
        {pctText && !isZero && ` (${pctText})`}
        {tone !== "neutral" && <span className="sr-only">{tone === "good" ? ", better" : ", worse"}</span>}
      </span>
      {period && <span className="text-fg-secondary">{period}</span>}
    </span>
  );
}
