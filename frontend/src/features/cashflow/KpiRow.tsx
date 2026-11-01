import type { ReactNode } from "react";
import { Money } from "../../components/money/Money";
import { Card } from "../../components/ui/card";
import { Skeleton } from "../../components/ui/skeleton";
import { cn } from "../../lib/cn";
import { formatMoney, formatMonth, formatPct } from "../../lib/format";
import type { CashflowOut } from "./api";

interface KpiRowProps {
  data: CashflowOut | undefined;
  loading: boolean;
}

/**
 * Monarch-style KPI tiles for the latest complete month: the value centered,
 * a small caps label under it, and the range average under that.
 */
export function KpiRow({ data, loading }: KpiRowProps) {
  const k = data?.kpi ?? null;
  const avg = data?.averages ?? null;
  const avgText = (cents: number | undefined) => (avg && cents !== undefined ? `Avg ${formatMoney(cents)} a month` : undefined);
  const avgRate =
    data && data.totals.months > 0 && data.totals.savings_rate !== null ? `${formatPct(data.totals.savings_rate)} over the range` : undefined;

  if (!k) {
    return (
      <Card className="p-4 md:p-5">
        {loading ? (
          <div role="status" aria-label="Loading" className="grid grid-cols-2 gap-4 lg:grid-cols-5">
            {[0, 1, 2, 3, 4].map((i) => (
              <Skeleton key={i} className="h-14" />
            ))}
          </div>
        ) : (
          <p className="m-0 text-center text-sm text-fg-secondary">No complete month yet. Totals show once every statement for a month is in.</p>
        )}
      </Card>
    );
  }

  return (
    <section aria-labelledby="kpi-title">
      <h2 id="kpi-title" className="sr-only">
        {formatMonth(k.month)} totals
      </h2>
      {/* Five tiles fill every row: 2+2+1 (wide) on phones, 3+2 on tablets, 5 from lg. */}
      <div className="grid grid-cols-2 gap-4 [&>*:last-child]:col-span-2 sm:grid-cols-6 sm:[&>*]:col-span-2 sm:[&>*:nth-child(n+4)]:col-span-3 lg:grid-cols-5 lg:[&>*]:col-span-1 lg:[&>*:nth-child(n+4)]:col-span-1">
        <Tile month={k.month} label="Income" sub={avgText(avg?.income_cents)}>
          <Money cents={k.income_cents} proportional className="text-good" />
        </Tile>
        <Tile month={k.month} label="Spending" sub={avgText(avg?.spending_cents)}>
          <Money cents={k.spending_cents} proportional />
        </Tile>
        <Tile month={k.month} label="Saved and invested" sub={avgText(avg?.saved_invested_cents)}>
          <Money cents={k.saved_invested_cents} proportional />
        </Tile>
        <Tile month={k.month} label="Left over" sub={avgText(avg?.left_over_cents)}>
          <Money cents={k.left_over_cents} proportional className={k.left_over_cents < 0 ? "text-bad" : undefined} />
        </Tile>
        <Tile month={k.month} label="Savings rate" sub={avgRate} className="col-span-2 sm:col-span-1">
          <span>{k.savings_rate === null ? "None" : formatPct(k.savings_rate)}</span>
        </Tile>
      </div>
      <p className="m-0 mt-2 text-sm text-fg-secondary">
        {formatMonth(k.month)}, the latest complete month.
        {avg ? ` Averages use the ${avg.months} complete ${avg.months === 1 ? "month" : "months"} in range.` : ""}
      </p>
    </section>
  );
}

/** The value and the average line fade in when they change (a new month or range). */
function Tile({
  month,
  label,
  sub,
  className,
  children,
}: {
  month: string;
  label: string;
  sub?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <Card className={cn("flex flex-col items-center justify-center p-4 text-center md:p-5", className)}>
      {/* The label comes first for screen readers; the value shows above it. */}
      <h3 className="eyebrow order-2 m-0 mt-1">{label}</h3>
      <p key={month} className="order-1 m-0 animate-fade-in text-2xl font-semibold text-fg">
        {children}
      </p>
      {sub && (
        <p key={sub} className="order-3 m-0 mt-1 animate-fade-in text-xs text-fg-secondary">
          {sub}
        </p>
      )}
    </Card>
  );
}
