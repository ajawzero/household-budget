import type { ReactNode } from "react";
import { Money } from "../../components/money/Money";
import { Card, CardHeader } from "../../components/ui/card";
import { Skeleton } from "../../components/ui/skeleton";
import { formatDate, formatShortDate } from "../../lib/format";
import type { TxnSummary } from "./api";

/** "Sep 3 to Sep 30, 2026", or one date. */
export function rangeText(first: string | null, last: string | null): string {
  if (!first || !last) return "";
  if (first === last) return formatDate(first);
  const sameYear = first.slice(0, 4) === last.slice(0, 4);
  return `${sameYear ? formatShortDate(first) : formatDate(first)} to ${formatDate(last)}`;
}

/**
 * The Summary panel (Monarch's right column): count, largest, average, and
 * total spending and income for everything the filters match, not just the
 * loaded rows. Cents, like the list.
 */
export function SummaryPanel({ summary, loading }: { summary: TxnSummary | null | undefined; loading: boolean }) {
  return (
    <Card aria-labelledby="txn-summary-title">
      <CardHeader title="Summary" titleId="txn-summary-title" subtitle={summary ? rangeText(summary.first_date, summary.last_date) : undefined} />
      {loading && !summary && (
        <div className="flex flex-col gap-3" aria-hidden="true">
          <Skeleton className="h-5" />
          <Skeleton className="h-5" />
          <Skeleton className="h-5" />
        </div>
      )}
      {summary && (
        <dl className="m-0 grid grid-cols-[1fr_auto] gap-x-4 gap-y-3 text-sm">
          <Item label="Total transactions">
            <span className="tabular">{summary.count.toLocaleString("en-US")}</span>
          </Item>
          <Item
            label="Largest transaction"
            note={
              summary.largest
                ? `${summary.largest.merchant ?? summary.largest.description}, ${formatShortDate(summary.largest.txn_date)}`
                : undefined
            }
          >
            {summary.largest ? (
              <Money cents={summary.largest.amount_cents} withCents flow />
            ) : (
              <span className="text-fg-secondary">None</span>
            )}
          </Item>
          <Item label="Average transaction">
            <Money cents={summary.average_cents} withCents flow empty="None" />
          </Item>
          <Item label="Total spending">
            <Money cents={summary.spending_cents} withCents />
          </Item>
          <Item label="Total income">
            <Money cents={summary.income_cents} withCents />
          </Item>
          <Item label="Net of all transactions">
            <Money cents={summary.total_cents} withCents flow />
          </Item>
        </dl>
      )}
      {summary && (
        <p className="m-0 mt-4 border-t border-line pt-3 text-xs text-fg-secondary">
          Spending and income leave out transfers. Refunds lower spending. Net adds every row, transfers too.
        </p>
      )}
    </Card>
  );
}

function Item({ label, note, children }: { label: string; note?: string; children: ReactNode }) {
  return (
    <div className="contents">
      <dt className="whitespace-nowrap text-fg-secondary">{label}</dt>
      <dd className="m-0 text-right font-medium text-fg">{children}</dd>
      {note && <dd className="col-span-2 -mt-2 m-0 truncate text-right text-xs text-fg-secondary">{note}</dd>}
    </div>
  );
}

/** Phones and tablets: the summary as one short line above the list. */
export function SummaryStrip({ summary }: { summary: TxnSummary }) {
  return (
    <dl className="m-0 flex basis-full flex-wrap gap-x-4 gap-y-1 text-sm lg:hidden" aria-label="Summary">
      <div className="flex gap-1">
        <dt className="text-fg-secondary">Transactions</dt>
        <dd className="tabular m-0 font-medium">{summary.count.toLocaleString("en-US")}</dd>
      </div>
      <div className="flex gap-1">
        <dt className="text-fg-secondary">Spending</dt>
        <dd className="m-0 font-medium">
          <Money cents={summary.spending_cents} withCents />
        </dd>
      </div>
      <div className="flex gap-1">
        <dt className="text-fg-secondary">Income</dt>
        <dd className="m-0 font-medium">
          <Money cents={summary.income_cents} withCents />
        </dd>
      </div>
    </dl>
  );
}
