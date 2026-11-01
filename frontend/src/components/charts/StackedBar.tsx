import { formatPct } from "../../lib/format";
import { Money } from "../money/Money";

export interface StackSegment {
  key: string;
  label: string;
  /** Cents. Zero or negative parts cannot be drawn; they get a legend line only. */
  cents: number;
  /** A token var, from the fixed group-to-slot map. */
  color: string;
}

interface StackedBarProps {
  segments: StackSegment[];
  /** "Totals" shows money, "percent" shows each part's share. */
  mode?: "totals" | "percent";
  /** Screen reader name, like "Assets". */
  label: string;
}

/**
 * Each part's share in percent, rounded to one decimal so the shown values
 * add up to exactly 100 (largest remainder). All zeros when the total is 0.
 */
export function sharePercents(cents: number[]): number[] {
  const total = cents.reduce((sum, c) => sum + c, 0);
  if (total <= 0) return cents.map(() => 0);
  const raw = cents.map((c) => (c / total) * 1000);
  const tenths = raw.map(Math.floor);
  let left = 1000 - tenths.reduce((sum, t) => sum + t, 0);
  const byRemainder = raw.map((r, i) => [r - tenths[i]!, i] as const).sort((a, b) => b[0] - a[0]);
  for (const [, i] of byRemainder) {
    if (left <= 0) break;
    tenths[i]! += 1;
    left -= 1;
  }
  return tenths.map((t) => t / 10);
}

/**
 * One full-width bar split into parts, largest first, with 2px gaps, and a
 * legend list under it. The legend carries every value as text. Parts at zero
 * or below (a card with a credit balance) are listed after the bar's parts
 * with their signed value, so the legend still adds up to the total.
 */
export function StackedBar({ segments, mode = "totals", label }: StackedBarProps) {
  const parts = segments.filter((s) => s.cents > 0).sort((a, b) => b.cents - a.cents);
  const others = segments.filter((s) => s.cents < 0).sort((a, b) => a.cents - b.cents);
  const pcts = sharePercents(parts.map((s) => s.cents));
  const share = (i: number) => pcts[i] ?? 0;

  return (
    <div>
      {parts.length > 0 && (
        <div className="flex h-3 gap-[2px] overflow-hidden rounded-full" role="img" aria-label={`${label} by part`}>
          {parts.map((s, i) => (
            <span
              key={s.key}
              title={`${s.label}: ${formatPct(share(i), true)}`}
              // Parts ease to new shares (Totals or Percent, By type or By owner).
              className="transition-[flex-grow] duration-(--dur-slow) ease-(--ease-out)"
              style={{ flexGrow: s.cents, flexBasis: 0, background: s.color }}
            />
          ))}
        </div>
      )}
      <ul className="m-0 mt-4 flex list-none flex-col gap-3 p-0 text-sm">
        {parts.map((s, i) => (
          <li key={s.key} className="flex items-center gap-2">
            <span aria-hidden="true" className="h-2 w-2 shrink-0 rounded-full" style={{ background: s.color }} />
            <span className="min-w-0 flex-1 truncate text-fg">{s.label}</span>
            <span className="tabular text-fg">
              {mode === "percent" ? formatPct(share(i), true) : <Money cents={s.cents} />}
            </span>
          </li>
        ))}
        {others.map((s) => (
          <li key={s.key} className="flex items-center gap-2">
            <span aria-hidden="true" className="h-2 w-2 shrink-0 rounded-full border" style={{ borderColor: s.color }} />
            <span className="min-w-0 flex-1 truncate text-fg">{s.label}</span>
            <span className="tabular text-fg">
              <Money cents={s.cents} />
              <span className="sr-only"> (not drawn in the bar)</span>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
