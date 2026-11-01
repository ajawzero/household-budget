import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useId } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ChartCard, type ChartColumn } from "../../components/charts/ChartCard";
import { AXIS_TICK, CHART_ANIMATION_MS, ChartTooltipBox } from "../../components/charts/ChartTooltip";
import { monthStartTicks } from "../../components/charts/AreaChart";
import { crossesZero, niceScale } from "../../components/charts/scale";
import { Delta } from "../../components/money/Delta";
import { EstBadge } from "../../components/money/EstBadge";
import { Money } from "../../components/money/Money";
import { RangePicker, rangeLabel } from "../../components/ui/range-picker";
import { INVESTMENT_RANGE_KEYS, type InvestmentRangeKey } from "../../lib/api/types";
import { formatCompact, formatDate, formatMoney, formatMonthShort, formatShortDate } from "../../lib/format";
import { useMediaQuery, useReducedMotion } from "../../lib/media";
import { useResolvedTheme } from "../../lib/theme";
import { getHistory, type HistoryOut, investmentKeys } from "./api";

/** One day on the chart, with the statements that landed on it. */
export interface HistoryRow {
  date: string;
  value: number;
  estimated: boolean;
  partial: boolean;
  /** "Alex brokerage (Lakeview) $56,591" for each statement value that day. */
  statements: string[];
}

export function historyRows(d: HistoryOut): HistoryRow[] {
  const names = new Map(d.accounts.map((a) => [a.account_key, a.display_name]));
  const byDate = new Map<string, string[]>();
  for (const s of d.statement_points) {
    const list = byDate.get(s.date) ?? [];
    list.push(`${names.get(s.account_key) ?? s.account_key} ${formatMoney(s.value_cents)}`);
    byDate.set(s.date, list);
  }
  return d.points.map((p) => ({
    date: p.date,
    value: p.value_cents,
    estimated: p.estimated,
    partial: p.partial,
    statements: byDate.get(p.date) ?? [],
  }));
}

/**
 * The estimate still running today: the segments that reach the end of the
 * range. Older segments sit between statements and are covered by the note
 * under the chart.
 */
export function currentEstimate(d: HistoryOut): { start: string; end: string; basedOn: string } | null {
  const open = d.estimated_segments.filter((s) => s.end === d.range.end);
  if (open.length === 0) return null;
  const start = open.reduce((a, s) => (s.start < a ? s.start : a), open[0]!.start);
  const basedOn = open.reduce((a, s) => (s.based_on < a ? s.based_on : a), open[0]!.based_on);
  return { start, end: d.range.end, basedOn };
}

function basisText(r: HistoryRow): string {
  if (r.statements.length > 0) return r.estimated ? "Statement, others estimated" : "Statement";
  if (r.estimated) return "Estimated from prices";
  return "Last statement";
}

const COLUMNS: ChartColumn<HistoryRow>[] = [
  { header: "Date", cell: (r) => formatDate(r.date) },
  { header: "Value", cell: (r) => formatMoney(r.value), numeric: true },
  { header: "Basis", cell: (r) => basisText(r) + (r.partial ? ", partial" : "") },
  { header: "Statements that day", cell: (r) => r.statements.join("; ") },
];

/**
 * Investments and retirement value by day (PLAN 8.2): statement values as dots,
 * the price estimate as the line between them, and the estimate still running
 * today as a tinted band.
 */
export function HistoryCard({ range, onRangeChange }: { range: InvestmentRangeKey; onRangeChange: (r: InvestmentRangeKey) => void }) {
  const wide = useMediaQuery("(min-width: 768px)");
  const q = useQuery({
    queryKey: investmentKeys.history(range),
    queryFn: () => getHistory(range),
    placeholderData: keepPreviousData,
  });
  const d = q.data;
  const rows = d ? historyRows(d) : undefined;
  const first = rows?.[0];
  const last = rows?.[rows.length - 1];
  const change = first && last ? last.value - first.value : null;
  const pct = first && last && first.value > 0 ? (((last.value - first.value) / first.value) * 100).toFixed(1) : null;
  const est = d ? currentEstimate(d) : null;
  const period = `${rangeLabel(range)} change`;
  const height = wide ? 280 : 180;

  const ariaLabel =
    d && last && change !== null
      ? `Investments and retirement value, ${rangeLabel(range).toLowerCase()}: ${change >= 0 ? "up" : "down"} ${formatMoney(Math.abs(change))}, now ${formatMoney(last.value)}. ` +
        `${d.statement_points.length} statement values.${est ? ` Estimated from prices since ${formatDate(est.basedOn)}.` : ""}`
      : "Investments and retirement value by day";

  const hero =
    last && change !== null ? (
      <span className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="inline-flex items-baseline gap-2 text-3xl font-semibold text-fg">
          <Money cents={last.value} proportional />
          {last.estimated && <EstBadge priceDate={d?.latest_price_date} statementDate={est?.basedOn} className="self-center" />}
        </span>
        <Delta cents={change} pct={pct} period={period} />
      </span>
    ) : null;

  return (
    <ChartCard<HistoryRow>
      eyebrow="Investments and retirement"
      subtitle={hero}
      controls={<RangePicker value={range} onChange={onRangeChange} keys={INVESTMENT_RANGE_KEYS} className="w-full md:w-auto *:flex-1 md:*:flex-none" />}
      controlsClassName="w-full md:w-auto"
      ariaLabel={ariaLabel}
      data={rows}
      columns={COLUMNS}
      rowKey={(r) => r.date}
      tableCaption="Value of investment and retirement accounts by day, with the statement values that landed on each day."
      loading={q.isPending}
      refreshing={q.isPlaceholderData}
      error={q.isError ? "Could not load the chart." : null}
      onRetry={() => void q.refetch()}
      emptyText="No investment balances in this range yet."
      height={height}
      footer={<HistoryLegend estimateSince={est?.basedOn ?? null} hasStatements={!!d?.statement_points.length} />}
    >
      {rows && (
        <HistoryChart
          rows={rows}
          estimate={est}
          height={height}
          yAxisWidth={wide ? 56 : 44}
          monthTicks={range === "YTD" || range === "1Y" || range === "3Y" || range === "ALL"}
          maxTicks={wide ? 8 : 4}
        />
      )}
    </ChartCard>
  );
}

/** The legend swatch: the same 12% amber wash as the band behind the line. */
const ESTIMATE_SWATCH = { background: "color-mix(in srgb, var(--fill-warn) 12%, transparent)" };

function HistoryLegend({ estimateSince, hasStatements }: { estimateSince: string | null; hasStatements: boolean }) {
  return (
    <div className="mt-3 flex flex-col gap-1 text-sm text-fg-secondary">
      <p className="m-0 flex flex-wrap items-center gap-x-4 gap-y-1">
        {hasStatements && (
          <span className="inline-flex items-center gap-2">
            <span aria-hidden="true" className="h-2 w-2 rounded-full bg-accent" />
            Statement value
          </span>
        )}
        {estimateSince && (
          <span className="inline-flex items-center gap-2">
            <span aria-hidden="true" className="inline-block h-3 w-4 rounded-sm border border-line-strong" style={ESTIMATE_SWATCH} />
            Estimated since the {formatShortDate(estimateSince)} statement
          </span>
        )}
      </p>
      <p className="m-0">Between statements the line moves with market prices. Funds with no market price hold their statement value.</p>
    </div>
  );
}

interface HistoryChartProps {
  rows: HistoryRow[];
  estimate: { start: string; end: string } | null;
  height: number;
  yAxisWidth: number;
  monthTicks: boolean;
  maxTicks: number;
  /** Fixed width (tests). Leave out to fill the card. */
  width?: number;
}

export function HistoryChart({ rows, estimate, height, yAxisWidth, monthTicks, maxTicks, width }: HistoryChartProps) {
  const gradientId = useId().replace(/:/g, "");
  const reduced = useReducedMotion();
  const theme = useResolvedTheme();
  const { domain, ticks } = niceScale(rows.map((r) => r.value));
  const dates = rows.map((r) => r.date);
  const xTicks = monthTicks ? monthStartTicks(dates, maxTicks) : undefined;
  const multiYear = !!dates.length && dates[0]!.slice(0, 4) !== dates[dates.length - 1]!.slice(0, 4);
  const monthTick = (v: string) => (multiYear ? `${formatMonthShort(v)} ${v.slice(0, 4)}` : formatMonthShort(v));
  const color = "var(--accent)";

  const chart = (
    <AreaChart
      key={theme}
      accessibilityLayer={false}
      data={rows}
      width={width}
      height={width ? height : undefined}
      margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} style={{ stopOpacity: "var(--area-fill-top)" }} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      {estimate && (
        <ReferenceArea
          x1={estimate.start}
          x2={estimate.end}
          fill="var(--fill-warn)"
          fillOpacity={0.12}
          stroke="none"
          ifOverflow="hidden"
          zIndex={-150}
        />
      )}
      <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
      <XAxis
        dataKey="date"
        tickFormatter={monthTicks ? monthTick : formatShortDate}
        ticks={xTicks}
        interval={monthTicks ? 0 : "preserveEnd"}
        tick={AXIS_TICK}
        axisLine={false}
        tickLine={false}
        minTickGap={32}
        tickMargin={8}
      />
      <YAxis
        domain={domain}
        tickFormatter={formatCompact}
        tick={AXIS_TICK}
        axisLine={false}
        tickLine={false}
        width={yAxisWidth}
        ticks={ticks}
        interval={0}
      />
      {crossesZero(domain) && <ReferenceLine y={0} stroke="var(--chart-axis)" />}
      <Tooltip
        cursor={{ stroke: "var(--border-strong)", strokeWidth: 1 }}
        content={({ active, payload }) => {
          const r = active ? (payload?.[0]?.payload as HistoryRow | undefined) : undefined;
          if (!r) return null;
          return (
            <ChartTooltipBox
              title={formatDate(r.date)}
              rows={[
                { key: "v", label: "Total", value: formatMoney(r.value), color },
                ...r.statements.map((s, i) => ({ key: `s${i}`, label: "statement", value: s })),
              ]}
              note={r.estimated ? "Estimated from market prices" : r.partial ? "Partial: an account has no balance yet" : undefined}
            />
          );
        }}
      />
      <Area
        type="linear"
        dataKey="value"
        stroke={color}
        strokeWidth={2}
        fill={`url(#${gradientId})`}
        isAnimationActive={!reduced}
        animationDuration={CHART_ANIMATION_MS}
        dot={(props: { cx?: number; cy?: number; index?: number; payload?: HistoryRow }) => {
          const key = `dot-${props.index ?? 0}`;
          if (!props.payload?.statements.length || props.cx === undefined || props.cy === undefined) return <g key={key} />;
          return <circle key={key} cx={props.cx} cy={props.cy} r={4} fill={color} stroke="var(--bg-card)" strokeWidth={2} />;
        }}
        activeDot={{ r: 4, stroke: "var(--bg-card)", strokeWidth: 2, fill: color }}
      />
    </AreaChart>
  );

  if (width) return chart;
  return (
    <ResponsiveContainer width="100%" height={height}>
      {chart}
    </ResponsiveContainer>
  );
}
