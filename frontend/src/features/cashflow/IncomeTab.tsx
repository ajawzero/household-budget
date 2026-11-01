import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Bar, BarChart, type BarShapeProps, CartesianGrid, Rectangle, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AXIS_TICK, CHART_ANIMATION_MS, ChartTooltipBox } from "../../components/charts/ChartTooltip";
import { ChartCard, type ChartColumn } from "../../components/charts/ChartCard";
import { niceScale } from "../../components/charts/scale";
import { Money } from "../../components/money/Money";
import { Card, CardHeader } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorBanner } from "../../components/ui/error-banner";
import { TableScroll } from "../../components/ui/table-scroll";
import { formatCompact, formatDate, formatMoney, formatMonth, formatMonthShort } from "../../lib/format";
import { useReducedMotion } from "../../lib/media";
import { useResolvedTheme } from "../../lib/theme";
import { cashflowKeys, getIncome, STATUS_TEXT, type IncomeMonth, type IncomeOut, type IncomeStream, type MonthRange } from "./api";
import { Pill } from "./parts";

/**
 * Stream colors by the stream's place in the chat's list, so a range change
 * never repaints a stream. Blue first (income), then amber, pink and green:
 * the four pass the palette validator on every pair (blue and purple do not).
 */
const STREAM_COLORS = ["var(--chart-income)", "var(--chart-4)", "var(--chart-5)", "var(--chart-6)"];
const OTHER = "var(--chart-muted)";

export function streamColor(index: number): string {
  return STREAM_COLORS[index] ?? OTHER;
}

const KIND_TEXT: Record<IncomeStream["kind"], string> = {
  paycheck: "Paycheck",
  benefit: "Benefit",
  draw: "Draw",
  bonus: "Bonus",
};

/** Income tab: the chat's income streams, deposits by month and paychecks by month. */
export function IncomeTab({ range }: { range: MonthRange }) {
  const q = useQuery({ queryKey: cashflowKeys.income(range), queryFn: () => getIncome(range), placeholderData: keepPreviousData });
  if (q.isError && !q.data) {
    return <ErrorBanner message="Could not load income." onRetry={() => void q.refetch()} retrying={q.isFetching} />;
  }
  const d = q.data;
  return (
    <div className="flex flex-col gap-4">
      <StreamsCard data={d} loading={q.isPending} />
      <IncomeByMonthCard data={d} loading={q.isPending} refreshing={q.isPlaceholderData} />
      <PaychecksCard data={d} loading={q.isPending} />
    </div>
  );
}

function StreamsCard({ data, loading }: { data: IncomeOut | undefined; loading: boolean }) {
  const streams = data?.streams ?? [];
  return (
    <Card>
      <CardHeader eyebrow="Income streams" title="Expected pay" subtitle="From the chat's latest monthly run. Net is per deposit." />
      {loading ? (
        <p className="m-0 text-sm text-fg-secondary">Loading…</p>
      ) : streams.length === 0 ? (
        <EmptyState title="No income streams yet." />
      ) : (
        <ul className="m-0 grid list-none gap-4 p-0 sm:grid-cols-2 xl:grid-cols-3">
          {streams.map((s, i) => (
            <li key={s.key} className="flex min-w-0 items-start gap-3 rounded-lg border border-line p-4">
              <span aria-hidden="true" className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: streamColor(i) }} />
              <div className="min-w-0 flex-1">
                <p className="m-0 text-sm font-medium text-fg">
                  {s.label}
                  {!s.active && <Pill>ended</Pill>}
                </p>
                <p className="m-0 text-sm text-fg-secondary">
                  {s.person_label} · {KIND_TEXT[s.kind]} · {s.schedule}
                </p>
                <p className="m-0 mt-2 text-lg font-medium text-fg">
                  <Money cents={s.expected_net_cents} /> <span className="text-sm font-normal text-fg-secondary">net</span>
                </p>
                <p className="m-0 text-xs text-fg-secondary">
                  {s.start_month ? `From ${formatMonth(s.start_month)}` : "Start not set"}
                  {s.end_month ? ` to ${formatMonth(s.end_month)}` : ""}
                  {s.note ? `. ${s.note}` : ""}
                </p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

type IncomeRow = { month: string; status: IncomeMonth["status"]; complete: boolean; total: number; other: number } & Record<
  `s_${string}`,
  number
>;

export function incomeRows(d: IncomeOut): IncomeRow[] {
  return d.months.map((m) => {
    const row = { month: m.month, status: m.status, complete: m.complete, total: m.income_cents, other: m.other_income_cents } as IncomeRow;
    for (const s of d.streams) row[`s_${s.key}`] = m.streams.find((x) => x.key === s.key)?.deposits_cents ?? 0;
    return row;
  });
}

function IncomeByMonthCard({ data, loading, refreshing }: { data: IncomeOut | undefined; loading: boolean; refreshing: boolean }) {
  const reduced = useReducedMotion();
  const theme = useResolvedTheme();
  const rows = data ? incomeRows(data) : undefined;
  const streams = data?.streams ?? [];
  const { domain, ticks } = niceScale((rows ?? []).map((r) => r.total), { includeZero: true });
  const columns: ChartColumn<IncomeRow>[] = [
    { header: "Month", cell: (r) => formatMonth(r.month) },
    ...streams.map((s) => ({ header: s.label, cell: (r: IncomeRow) => formatMoney(r[`s_${s.key}`] ?? 0), numeric: true })),
    { header: "Other income", cell: (r) => formatMoney(r.other), numeric: true },
    { header: "Total", cell: (r) => formatMoney(r.total), numeric: true },
    { header: "Status", cell: (r) => STATUS_TEXT[r.status] },
  ];
  const partial = rows?.some((r) => !r.complete) ?? false;
  return (
    <ChartCard<IncomeRow>
      eyebrow="Deposits"
      title="Income by month"
      subtitle="Deposits tied to each stream, plus other income."
      ariaLabel={`Income by month by stream${rows?.length ? `, ${formatMonth(rows[0]!.month)} to ${formatMonth(rows[rows.length - 1]!.month)}` : ""}.`}
      data={rows}
      columns={columns}
      rowKey={(r) => r.month}
      tableCaption="Income by month and stream"
      loading={loading}
      refreshing={refreshing}
      emptyText="No income in this range."
      height={240}
      footer={
        <>
          <ul className="m-0 mt-3 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-sm text-fg-secondary">
            {streams.map((s, i) => (
              <li key={s.key} className="flex items-center gap-1.5">
                <span aria-hidden="true" className="h-2.5 w-2.5 rounded-sm" style={{ background: streamColor(i) }} />
                {s.label}
              </li>
            ))}
            <li className="flex items-center gap-1.5">
              <span aria-hidden="true" className="h-2.5 w-2.5 rounded-sm" style={{ background: OTHER }} />
              Other income
            </li>
          </ul>
          {partial && <p className="m-0 mt-2 text-sm text-fg-secondary">Pale bars are months that are not complete. They may grow as statements come in.</p>}
        </>
      }
    >
      <ResponsiveContainer width="100%" height={240}>
        <BarChart key={theme} accessibilityLayer={false} data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }} barCategoryGap="28%">
          <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
          <XAxis dataKey="month" tickFormatter={formatMonthShort} tick={AXIS_TICK} axisLine={false} tickLine={false} tickMargin={8} minTickGap={8} />
          <YAxis domain={domain} ticks={ticks} interval={0} tickFormatter={formatCompact} tick={AXIS_TICK} axisLine={false} tickLine={false} width={56} />
          <Tooltip
            cursor={{ fill: "var(--bg-hover)" }}
            content={({ active, payload }) => {
              const r = active ? (payload?.[0]?.payload as IncomeRow | undefined) : undefined;
              if (!r) return null;
              return (
                <ChartTooltipBox
                  title={`${formatMonth(r.month)}${r.complete ? "" : ` (${r.status === "partial" ? "partial" : "so far"})`}`}
                  rows={[
                    ...streams.map((s, i) => ({ key: s.key, label: s.label, value: formatMoney(r[`s_${s.key}`] ?? 0), color: streamColor(i) })),
                    { key: "o", label: "Other income", value: formatMoney(r.other), color: OTHER },
                    { key: "t", label: "Total", value: formatMoney(r.total) },
                  ]}
                />
              );
            }}
          />
          {[...streams.map((s, i) => ({ key: `s_${s.key}`, color: streamColor(i) })), { key: "other", color: OTHER }].map((seg, _i, all) => (
            <Bar
              key={seg.key}
              dataKey={seg.key}
              stackId="income"
              maxBarSize={24}
              isAnimationActive={!reduced}
              animationDuration={CHART_ANIMATION_MS}
              shape={(p: BarShapeProps) => <Segment {...p} color={seg.color} segKey={seg.key} stack={all.map((x) => x.key)} />}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

type SegmentProps = BarShapeProps & { color: string; segKey: string; stack: string[] };

/**
 * One stacked segment. The top segment of each month gets the 4px rounded end,
 * a 2px card-colored gap separates segments, and months that are not complete
 * are a pale wash with an outline.
 */
function Segment({ x, y, width, height, payload, color, segKey, stack }: SegmentProps) {
  const row = payload as IncomeRow | undefined;
  if (!row || !height) return null;
  const top = [...stack].reverse().find((k) => ((row as Record<string, unknown>)[k] as number) > 0);
  const partial = !row.complete;
  const isTop = top === segKey;
  return (
    <Rectangle
      x={x}
      y={isTop ? y : y + 2}
      width={width}
      height={isTop ? height : Math.max(0, height - 2)}
      radius={isTop ? [4, 4, 0, 0] : 0}
      fill={partial ? `color-mix(in oklab, ${color} 35%, var(--bg-card))` : color}
      stroke={partial ? color : undefined}
      strokeWidth={partial ? 1.5 : undefined}
    />
  );
}

function PaychecksCard({ data, loading }: { data: IncomeOut | undefined; loading: boolean }) {
  const [showEach, setShowEach] = useState(false);
  const months = (data?.months ?? []).filter((m) => m.paychecks.length > 0);
  const paychecks = data?.paychecks ?? [];
  return (
    <Card>
      <CardHeader eyebrow="Paychecks" title="Paychecks by month" subtitle="Gross and net from each pay stub." />
      {loading ? (
        <p className="m-0 text-sm text-fg-secondary">Loading…</p>
      ) : months.length === 0 ? (
        <EmptyState title="No paychecks in this range." />
      ) : (
        <>
          <TableScroll label="Paychecks by month">
            <table className="data-table compact">
              <caption className="sr-only">Paychecks by month and person</caption>
              <thead>
                <tr>
                  <th scope="col">Month</th>
                  <th scope="col">Person</th>
                  <th scope="col" className="num hidden sm:table-cell">
                    Checks
                  </th>
                  <th scope="col" className="num">
                    Gross
                  </th>
                  <th scope="col" className="num">
                    Net
                  </th>
                </tr>
              </thead>
              <tbody>
                {months.map((m) => (
                  <Fragment key={m.month}>
                    {m.paychecks.map((p, i) => (
                      <tr key={`${m.month}-${p.person}`}>
                        {i === 0 ? (
                          <th scope="rowgroup" rowSpan={m.paychecks.length} className="font-normal">
                            {formatMonth(m.month)}
                          </th>
                        ) : null}
                        <td>{p.person_label}</td>
                        <td className="num hidden sm:table-cell">{p.count}</td>
                        <td className="num">{formatMoney(p.gross_cents)}</td>
                        <td className="num">{formatMoney(p.net_cents)}</td>
                      </tr>
                    ))}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </TableScroll>
          <button
            type="button"
            className="mt-3 inline-flex h-11 items-center rounded-lg px-1 text-sm font-medium text-accent-text hover:underline md:h-8"
            aria-expanded={showEach}
            onClick={() => setShowEach((v) => !v)}
          >
            {showEach ? "Hide each paycheck" : `Show each paycheck (${paychecks.length})`}
          </button>
          {showEach && (
            <TableScroll className="mt-2" label="Each paycheck">
              <table className="data-table compact">
                <caption className="sr-only">Each paycheck</caption>
                <thead>
                  <tr>
                    <th scope="col">Pay date</th>
                    <th scope="col">Person</th>
                    <th scope="col">Kind</th>
                    <th scope="col" className="num">
                      Gross
                    </th>
                    <th scope="col" className="num">
                      Net
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {paychecks.map((p) => (
                    <tr key={`${p.person}-${p.pay_date}-${p.seq}`}>
                      <th scope="row" className="font-normal whitespace-nowrap">
                        {formatDate(p.pay_date)}
                      </th>
                      <td>{p.person_label}</td>
                      <td>{p.kind === "regular" ? "Regular" : p.kind === "bonus" ? "Bonus" : "Draw"}</td>
                      <td className="num">{formatMoney(p.gross_cents)}</td>
                      <td className="num">{formatMoney(p.net_cents)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
          )}
        </>
      )}
    </Card>
  );
}
