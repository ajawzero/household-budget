// Small pieces shared by Recurring, Debt, Flags and Cash Flow.
import { ChevronLeft, ChevronRight } from "lucide-react";
import type { ReactNode } from "react";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card } from "../../components/ui/card";
import { Skeleton } from "../../components/ui/skeleton";
import { cn } from "../../lib/cn";
import type { RecurringItem } from "../../lib/api";
import { addMonths, formatMoney, formatMonth, formatRunDate } from "../../lib/format";

/**
 * C6: new since the update before, or a rate change. The old amount is on the
 * badge itself, not only in a hover title, so phones see it.
 */
export function ChangeBadge({ change, wasCents }: { change: RecurringItem["change"] | undefined; wasCents?: number | null }) {
  if (!change) return null;
  if (change === "new") {
    return (
      <Badge tone="accent" title="Not in the update before">
        New
      </Badge>
    );
  }
  const was = wasCents === null || wasCents === undefined ? null : formatMoney(wasCents, { cents: true });
  return (
    <Badge tone={change === "rate_up" ? "warn" : "good"} title={was ? `Was ${was} a month` : undefined}>
      {change === "rate_up" ? "Rate up" : "Rate down"}
      {was ? ` from ${was}` : ""}
    </Badge>
  );
}


export interface Kpi {
  key: string;
  label: string;
  value: ReactNode;
  /** One gray line under the label. */
  sub?: ReactNode;
  /** Text tone for the value. Status words go in `sub` too, so tone is never the only signal. */
  tone?: "good" | "bad" | "warn";
}

/**
 * A row of KPI tiles, like Monarch's report tiles: the value centered, a
 * small caps label under it. Two per row on phones when there are four.
 */
export function KpiStrip({ items, loading = false, label }: { items: Kpi[]; loading?: boolean; label: string }) {
  const cols = items.length >= 4 ? "grid-cols-2 md:grid-cols-4" : items.length === 3 ? "grid-cols-3" : "grid-cols-2";
  return (
    <ul aria-label={label} className={cn("m-0 grid list-none gap-3 p-0 md:gap-4", cols)}>
      {items.map((k) => (
        <li key={k.key} className="min-w-0">
          <Card className="flex h-full flex-col items-center justify-center px-2 py-4 text-center md:p-5">
            {loading ? (
              <div role="status" aria-label="Loading" className="flex flex-col items-center gap-2">
                <Skeleton className="h-7 w-20" />
                <Skeleton className="h-3 w-16" />
              </div>
            ) : (
              <>
                <p
                  className={cn(
                    "m-0 max-w-full truncate text-xl font-medium md:text-2xl",
                    k.tone === "good" ? "text-good" : k.tone === "bad" ? "text-bad" : k.tone === "warn" ? "text-warn" : "text-fg",
                  )}
                >
                  {k.value}
                </p>
                <p className="eyebrow m-0 mt-1">{k.label}</p>
                {k.sub && <p className="m-0 mt-1 text-xs text-fg-secondary md:text-sm">{k.sub}</p>}
              </>
            )}
          </Card>
        </li>
      ))}
    </ul>
  );
}

/** Previous and next month arrows around the month name, plus "This month". */
export function MonthNav({
  month,
  current,
  onChange,
}: {
  month: string;
  /** The server's current month. */
  current: string;
  onChange: (month: string | null) => void;
}) {
  return (
    <div className="flex items-center gap-1">
      <Button size="icon" variant="ghost" aria-label="Previous month" onClick={() => onChange(addMonths(month, -1))}>
        <ChevronLeft size={18} aria-hidden="true" />
      </Button>
      <span className="min-w-20 text-center text-sm font-medium text-fg" aria-live="polite">
        {formatMonth(month)}
      </span>
      <Button size="icon" variant="ghost" aria-label="Next month" onClick={() => onChange(addMonths(month, 1))}>
        <ChevronRight size={18} aria-hidden="true" />
      </Button>
      {month !== current && (
        <Button size="sm" onClick={() => onChange(null)}>
          This month
        </Button>
      )}
    </div>
  );
}

/** A card's list while loading. */
export function ListSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div role="status" aria-label="Loading" className="flex flex-col gap-3">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
  );
}

/** A gray "as of" line naming the run the chat's data came from. `note` follows the date. */
export function SourceLine({
  runId,
  note = "Read only: the statement chat keeps this list.",
}: {
  runId: string | null | undefined;
  note?: string;
}) {
  if (!runId) return null;
  return (
    <p className="m-0 text-sm text-fg-secondary">
      From the {formatRunDate(runId)} update. {note}
    </p>
  );
}
