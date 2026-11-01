import { wash } from "../../components/charts/CashflowBars";
import { ChartCard, type ChartColumn } from "../../components/charts/ChartCard";
import { Money } from "../../components/money/Money";
import { Card, CardHeader } from "../../components/ui/card";
import { formatDate, formatMoney, formatMonth, formatMonthShort } from "../../lib/format";
import type { TrackedCards } from "./api";
import { SPENDING } from "./CashflowChart";
import { Pill } from "./parts";

const NOTE = "Payments from checking count as spending in the month they were paid. When the chat used a card's statement, its rows count instead.";

type TrackedCard = TrackedCards["cards"][number];

type MonthRow = { month: string; cents: number; byCard: Record<string, number> };

function monthRows(t: TrackedCards): MonthRow[] {
  return t.months.map((m) => ({
    month: m.month,
    cents: m.cents,
    byCard: Object.fromEntries(t.cards.map((c) => [c.account_key, c.months.find((x) => x.month === m.month)?.cents ?? 0])),
  }));
}

/**
 * "Avg $59 a month · Last paid $21 on Sep 24, 2026". A card with no payment
 * in range says when it was last paid. Dates compare as plain ISO strings.
 */
export function cardNote(c: TrackedCard, firstMonth: string | undefined): string {
  const parts: string[] = [];
  if (c.avg_cents !== null) parts.push(`Avg ${formatMoney(c.avg_cents)} a month`);
  const day = c.last_payment_date;
  if (day === null) parts.push("No payments yet");
  else if (firstMonth && day < `${firstMonth}-01`) parts.push(`No payment since ${formatDate(day)}`);
  else if (c.last_payment_cents !== null) parts.push(`Last paid ${formatMoney(c.last_payment_cents)} on ${formatDate(day)}`);
  return parts.join(" · ");
}

/** "Averages use the 2 complete months in range." */
export function averagesNote(months: number): string {
  if (months === 0) return "No complete month to average yet.";
  return `Averages use the ${months} complete ${months === 1 ? "month" : "months"} in range.`;
}

/**
 * The "Sam's cards" line (PLAN 3.9): store cards tracked by their payments,
 * per month and per card, labeled "payment basis". The label comes from the API.
 */
export function TrackedCardsCard({
  tracked,
  completeMonths,
}: {
  tracked: TrackedCards;
  /** Months whose statements are all in. Others are drawn pale. */
  completeMonths: Set<string>;
}) {
  const rows = monthRows(tracked);
  const max = Math.max(1, ...rows.map((r) => r.cents));
  const columns: ChartColumn<MonthRow>[] = [
    { header: "Month", cell: (r) => `${formatMonth(r.month)}${completeMonths.has(r.month) ? "" : " (partial)"}` },
    ...tracked.cards.map((c) => ({ header: c.name, cell: (r: MonthRow) => formatMoney(r.byCard[c.account_key] ?? 0), numeric: true })),
    ...(tracked.cards.length > 1 ? [{ header: "Total", cell: (r: MonthRow) => formatMoney(r.cents), numeric: true }] : []),
  ];
  return (
    <ChartCard<MonthRow>
      title={
        <>
          {tracked.label}
          <Pill>{tracked.basis}</Pill>
        </>
      }
      subtitle={NOTE}
      ariaLabel={`${tracked.label}, payment basis, by month. Total ${formatMoney(tracked.total_cents)}.`}
      data={rows}
      columns={columns}
      rowKey={(r) => r.month}
      tableCaption={`${tracked.label} by month, payment basis`}
      emptyText="No months in range."
      height={96}
      footer={
        <>
          {rows.some((r) => !completeMonths.has(r.month)) && (
            <p className="m-0 mt-3 flex items-center gap-1.5 text-sm text-fg-secondary">
              <span aria-hidden="true" className="h-2.5 w-2.5 rounded-sm" style={{ background: wash(SPENDING), boxShadow: `inset 0 0 0 1.5px ${SPENDING}` }} />
              Partial month
            </p>
          )}
          <ul className="m-0 mt-4 flex list-none flex-col gap-2 border-t border-line p-0 pt-4">
            {tracked.cards.map((c) => {
              const note = cardNote(c, tracked.months[0]?.month);
              return (
                <li key={c.account_key} className="text-sm">
                  <div className="flex items-baseline justify-between gap-3">
                    <span className="min-w-0 truncate text-fg">{c.name}</span>
                    <Money cents={c.total_cents} className="text-fg" />
                  </div>
                  {note && <p className="m-0 text-xs text-fg-secondary">{note}</p>}
                </li>
              );
            })}
            {tracked.cards.length > 1 && (
              <li className="text-sm">
                <div className="flex items-baseline justify-between gap-3 font-medium">
                  <span className="text-fg">Total</span>
                  <Money cents={tracked.total_cents} className="text-fg" />
                </div>
                {tracked.avg_cents !== null && <p className="m-0 text-xs text-fg-secondary">Avg {formatMoney(tracked.avg_cents)} a month</p>}
              </li>
            )}
          </ul>
          <p className="m-0 mt-3 text-xs text-fg-secondary">{averagesNote(tracked.avg_months)}</p>
        </>
      }
    >
      <ol className="m-0 flex h-24 list-none items-end gap-1 p-0">
        {rows.map((r, i) => {
          const complete = completeMonths.has(r.month);
          // Every month up to 8, else every other one, ending on the latest.
          const labeled = rows.length <= 8 || (rows.length - 1 - i) % 2 === 0;
          return (
            <li key={r.month} className="flex h-full min-w-0 flex-1 flex-col items-center justify-end gap-1" title={`${formatMonth(r.month)}: ${formatMoney(r.cents)}${complete ? "" : ", partial"}`}>
              <span
                aria-hidden="true"
                className="block w-full max-w-6 rounded-t"
                style={{
                  height: `${Math.max((r.cents / max) * 72, r.cents > 0 ? 2 : 0)}px`,
                  background: complete ? SPENDING : wash(SPENDING),
                  boxShadow: complete ? undefined : `inset 0 0 0 1.5px ${SPENDING}`,
                }}
              />
              <span aria-hidden="true" className="h-4 text-xs text-fg-secondary">
                {labeled ? formatMonthShort(r.month) : ""}
              </span>
            </li>
          );
        })}
      </ol>
    </ChartCard>
  );
}

/** One month of the tracked cards, for the Budget page's side panel. */
export function TrackedCardsMonth({ tracked, month }: { tracked: TrackedCards; month: string }) {
  return (
    <Card>
      <CardHeader
        title={
          <>
            {tracked.label}
            <Pill>{tracked.basis}</Pill>
          </>
        }
        subtitle={formatMonth(month)}
      />
      <ul className="m-0 flex list-none flex-col gap-3 p-0">
        {tracked.cards.map((c) => {
          const m = c.months.find((x) => x.month === month);
          return (
            <li key={c.account_key} className="text-sm">
              <div className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 truncate text-fg">{c.name}</span>
                <Money cents={m?.cents ?? 0} className="text-fg" />
              </div>
              {m && m.statement_cents !== 0 && (
                <p className="m-0 text-xs text-fg-secondary">
                  {formatMoney(m.payments_cents)} in payments, {formatMoney(m.statement_cents)} from a statement
                </p>
              )}
            </li>
          );
        })}
        {tracked.cards.length > 1 && (
          <li className="flex items-baseline justify-between gap-3 border-t border-line pt-3 text-sm font-medium">
            <span className="text-fg">Total</span>
            <Money cents={tracked.months.find((x) => x.month === month)?.cents ?? 0} className="text-fg" />
          </li>
        )}
      </ul>
      <p className="m-0 mt-4 text-sm text-fg-secondary">{NOTE}</p>
    </Card>
  );
}
