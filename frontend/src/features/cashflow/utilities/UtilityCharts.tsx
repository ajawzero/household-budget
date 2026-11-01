import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AXIS_TICK, CHART_ANIMATION_MS, ChartTooltipBox } from "../../../components/charts/ChartTooltip";
import { ChartCard, type ChartColumn } from "../../../components/charts/ChartCard";
import { niceScale } from "../../../components/charts/scale";
import type { UtilityDetail, UtilityService, UtilityUnit } from "../../../lib/api";
import { formatDate, formatMoney, formatShortDate } from "../../../lib/format";
import { useMediaQuery, useReducedMotion } from "../../../lib/media";
import { useResolvedTheme } from "../../../lib/theme";
import {
  billTick,
  CONTEXT_COLOR,
  changeText,
  costRows,
  costScale,
  type CostRow,
  CREDITS_COLOR,
  creditRows,
  type CreditRow,
  formatCost,
  formatCostTick,
  formatUsage,
  moneyTickFormatter,
  perDayName,
  periodText,
  PLAIN_HEADERS,
  SERVICE_COLORS,
  SERVICE_NAMES,
  signedPct,
  unitName,
  type UsageMode,
  usageRows,
  type UsageRow,
  usageTickFormatter,
} from "./model";

const HEIGHT = 240;
const MARGIN = { top: 8, right: 8, bottom: 0, left: 0 };

/** "Therms by bill", but "kWh by bill" keeps its small k. */
function capitalize(s: string): string {
  return s.startsWith("kWh") ? s : s.charAt(0).toUpperCase() + s.slice(1);
}

function spansYears(ends: string[]): boolean {
  return ends.length > 0 && ends[0]!.slice(0, 4) !== ends[ends.length - 1]!.slice(0, 4);
}

function Swatch({ color, line = false }: { color: string; line?: boolean }) {
  return <span aria-hidden="true" className={line ? "h-0.5 w-3 rounded-full" : "h-2.5 w-2.5 rounded-sm"} style={{ background: color }} />;
}

function Legend({ items }: { items: { key: string; label: string; color: string; line?: boolean }[] }) {
  return (
    <ul className="m-0 mt-3 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-sm text-fg-secondary">
      {items.map((i) => (
        <li key={i.key} className="flex items-center gap-1.5">
          <Swatch color={i.color} line={i.line} />
          {i.label}
        </li>
      ))}
    </ul>
  );
}

// ---- Usage by bill ----

/** Show the numbers columns for a usage chart, in the same unit as the bars. */
export function usageColumns(unitLabel: string): ChartColumn<UsageRow>[] {
  return [
    { header: "Bill period", cell: (r) => periodText(r.periodStart, r.periodEnd) },
    { header: "Days", cell: (r) => r.days, numeric: true },
    { header: `This bill (${unitLabel})`, cell: (r) => formatUsage(r.valueMicro), numeric: true },
    {
      header: "Last year period",
      cell: (r) => (r.lastYearStart && r.lastYearEnd ? periodText(r.lastYearStart, r.lastYearEnd) : "No bill"),
    },
    { header: `Last year (${unitLabel})`, cell: (r) => formatUsage(r.lastYearMicro), numeric: true },
    { header: "Change from last year", cell: (r) => signedPct(r.changePct), numeric: true },
    { header: "Change from bill before", cell: (r) => signedPct(r.prevChangePct), numeric: true },
  ];
}

/** "up 51.4% from the bill before", or null with no bill before to compare with. */
function prevClause(r: UsageRow): string | null {
  const change = changeText(r.prevChangePct);
  if (!change) return null;
  return change.dir ? `${change.dir} ${change.text} from the bill before` : "the same as the bill before";
}

/** "Newest bill 719 kWh, up 51.4% from the bill before." Null with no bill before. */
export function prevSentence(rows: UsageRow[], unitLabel: string): string | null {
  const last = rows[rows.length - 1];
  const clause = last ? prevClause(last) : null;
  return last && clause ? `Newest bill ${formatUsage(last.valueMicro)} ${unitLabel}, ${clause}.` : null;
}

export function usageSummaryText(service: UtilityService, unitLabel: string, rows: UsageRow[]): string {
  const name = SERVICE_NAMES[service];
  if (rows.length === 0) return `${name} use by bill.`;
  const first = rows[0]!;
  const last = rows[rows.length - 1]!;
  const head = `${name} use by bill in ${unitLabel}, ${formatDate(first.periodEnd)} to ${formatDate(last.periodEnd)}, next to the same period last year.`;
  const change = changeText(last.changePct);
  const tail =
    change && change.dir
      ? ` Newest bill ${formatUsage(last.valueMicro)}, ${change.dir} ${change.text} from last year.`
      : ` Newest bill ${formatUsage(last.valueMicro)}.`;
  const prev = prevClause(last);
  return head + tail + (prev ? ` ${prev.charAt(0).toUpperCase()}${prev.slice(1)}.` : "");
}

export function UsageCard({
  detail,
  service,
  unit,
  mode,
  loading,
  refreshing,
}: {
  detail: UtilityDetail | undefined;
  service: UtilityService;
  unit: UtilityUnit;
  mode: UsageMode;
  loading: boolean;
  refreshing: boolean;
}) {
  const reduced = useReducedMotion();
  const theme = useResolvedTheme();
  const phone = !useMediaQuery("(min-width: 768px)");
  const rows = detail ? usageRows(detail.yoy, service, mode, unit) : undefined;
  const unitLabel = mode === "day" ? perDayName(unit) : unitName(unit);
  const color = SERVICE_COLORS[service];
  const values = (rows ?? []).flatMap((r) => [r.value, r.lastYear]).filter((v): v is number => v !== null);
  const { domain, ticks } = niceScale(values, { includeZero: true });
  const years = spansYears((rows ?? []).map((r) => r.periodEnd));
  const anyLastYear = (rows ?? []).some((r) => r.lastYear !== null);
  const prev = prevSentence(rows ?? [], unitLabel);
  return (
    <ChartCard<UsageRow>
      eyebrow={`${SERVICE_NAMES[service]} use`}
      title={capitalize(mode === "day" ? `${unitName(unit)} per day, by bill` : `${unitName(unit)} by bill`)}
      subtitle={
        !anyLastYear
          ? "No bills from last year to compare with yet."
          : mode === "day"
            ? "Per day evens out bills with more or fewer days."
            : "Each bill next to the bill from the same period last year."
      }
      ariaLabel={usageSummaryText(service, unitLabel, rows ?? [])}
      data={rows}
      columns={usageColumns(unitLabel)}
      rowKey={(r) => r.billKey}
      tableCaption={`${SERVICE_NAMES[service]} use by bill, in ${unitLabel}`}
      tableClassName={PLAIN_HEADERS}
      loading={loading}
      refreshing={refreshing}
      emptyText="No bills in this range."
      height={HEIGHT}
      footer={
        <>
          {/* No good or bad tone here: bill to bill use swings with the season,
              so only the same period last year says better or worse. */}
          {prev && <p className="m-0 mt-3 text-sm text-fg-secondary">{prev}</p>}
          <Legend
            items={[
              // In bar order: last year's bar sits left of this bill's.
              ...(anyLastYear ? [{ key: "last", label: "Same period last year", color: CONTEXT_COLOR }] : []),
              { key: "this", label: "This bill", color },
            ]}
          />
        </>
      }
    >
      <ResponsiveContainer width="100%" height={HEIGHT}>
        <BarChart key={theme} accessibilityLayer={false} data={rows} margin={MARGIN} barGap={2} barCategoryGap="24%">
          <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
          <XAxis
            dataKey="periodEnd"
            tickFormatter={(v: string) => billTick(v, years)}
            tick={AXIS_TICK}
            axisLine={false}
            tickLine={false}
            tickMargin={8}
            minTickGap={phone ? 16 : 8}
          />
          <YAxis
            domain={domain}
            ticks={ticks}
            interval={0}
            tickFormatter={usageTickFormatter(ticks)}
            tick={AXIS_TICK}
            axisLine={false}
            tickLine={false}
            width={phone ? 44 : 56}
          />
          <Tooltip
            cursor={{ fill: "var(--bg-hover)" }}
            content={({ active, payload }) => {
              const r = active ? (payload?.[0]?.payload as UsageRow | undefined) : undefined;
              if (!r) return null;
              const change = changeText(r.changePct);
              const prevChange = changeText(r.prevChangePct);
              return (
                <ChartTooltipBox
                  title={`${periodText(r.periodStart, r.periodEnd)} · ${r.days} days`}
                  rows={[
                    { key: "this", label: `${unitLabel} this bill`, value: formatUsage(r.valueMicro), color },
                    {
                      key: "last",
                      label: r.lastYearEnd ? `last year, to ${formatShortDate(r.lastYearEnd)}` : "No bill last year",
                      value: formatUsage(r.lastYearMicro),
                      color: CONTEXT_COLOR,
                    },
                    ...(change ? [{ key: "chg", label: "vs last year", value: change.dir ? `${change.arrow} ${change.text}` : change.text }] : []),
                    ...(prevChange
                      ? [{ key: "prev", label: "vs bill before", value: prevChange.dir ? `${prevChange.arrow} ${prevChange.text}` : prevChange.text }]
                      : []),
                  ]}
                />
              );
            }}
          />
          <Bar dataKey="lastYear" name="Same period last year" fill={CONTEXT_COLOR} radius={[4, 4, 0, 0]} maxBarSize={24} isAnimationActive={!reduced} animationDuration={CHART_ANIMATION_MS} />
          <Bar dataKey="value" name="This bill" fill={color} radius={[4, 4, 0, 0]} maxBarSize={24} isAnimationActive={!reduced} animationDuration={CHART_ANIMATION_MS} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

// ---- Cost per unit ----

/**
 * A line with dot={false} draws nothing for a point with no neighbor (one
 * bill, or a value between two gaps). Those points get a dot.
 */
function loneDot(rows: CostRow[], key: "allIn" | "exFixed", color: string) {
  function LoneDot(props: { cx?: number; cy?: number; index?: number }) {
    const { cx, cy, index = 0 } = props;
    const lone = rows[index]?.[key] !== null && (rows[index - 1]?.[key] ?? null) === null && (rows[index + 1]?.[key] ?? null) === null;
    if (!lone || cx === undefined || cy === undefined) return <g key={`${key}-${index}`} />;
    return <circle key={`${key}-${index}`} cx={cx} cy={cy} r={4} fill={color} stroke="var(--bg-card)" strokeWidth={2} />;
  }
  return LoneDot;
}

export function costColumns(unit: UtilityUnit): ChartColumn<CostRow>[] {
  const per = costScale(unit).label;
  return [
    { header: "Period end", cell: (r) => formatDate(r.periodEnd) },
    { header: `All in (${per})`, cell: (r) => formatCost(r.allIn, unit), numeric: true },
    { header: `Without fixed charges (${per})`, cell: (r) => formatCost(r.exFixed, unit), numeric: true },
  ];
}

export function CostCard({
  detail,
  service,
  unit,
  loading,
  refreshing,
}: {
  detail: UtilityDetail | undefined;
  service: UtilityService;
  unit: UtilityUnit;
  loading: boolean;
  refreshing: boolean;
}) {
  const reduced = useReducedMotion();
  const theme = useResolvedTheme();
  const phone = !useMediaQuery("(min-width: 768px)");
  const rows = detail ? costRows(detail.cost, service, unit) : undefined;
  const per = costScale(unit).label;
  const color = SERVICE_COLORS[service];
  const values = (rows ?? []).flatMap((r) => [r.allIn, r.exFixed]).filter((v): v is number => v !== null);
  const { domain, ticks } = niceScale(values, { includeZero: true });
  const years = spansYears((rows ?? []).map((r) => r.periodEnd));
  const hasEx = (rows ?? []).some((r) => r.exFixed !== null);
  const last = rows?.[rows.length - 1];
  return (
    <ChartCard<CostRow>
      eyebrow={`${SERVICE_NAMES[service]} cost`}
      title={`Cost ${per}`}
      subtitle={hasEx ? "All in, and without the fixed charges." : "All in. The bills do not split out fixed charges."}
      ariaLabel={`${SERVICE_NAMES[service]} cost ${per} by bill.${last ? ` Newest bill ${formatCost(last.allIn, unit)} all in.` : ""}`}
      data={rows}
      columns={costColumns(unit)}
      rowKey={(r) => r.billKey}
      tableCaption={`${SERVICE_NAMES[service]} cost ${per} by bill`}
      tableClassName={PLAIN_HEADERS}
      loading={loading}
      refreshing={refreshing}
      emptyText="No bills in this range."
      height={HEIGHT}
      footer={
        <Legend
          items={[
            { key: "all", label: "All in", color, line: true },
            ...(hasEx ? [{ key: "ex", label: "Without fixed charges", color: CONTEXT_COLOR, line: true }] : []),
          ]}
        />
      }
    >
      <ResponsiveContainer width="100%" height={HEIGHT}>
        <LineChart key={theme} accessibilityLayer={false} data={rows} margin={MARGIN}>
          <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
          <XAxis
            dataKey="periodEnd"
            tickFormatter={(v: string) => billTick(v, years)}
            tick={AXIS_TICK}
            axisLine={false}
            tickLine={false}
            tickMargin={8}
            minTickGap={phone ? 16 : 8}
          />
          <YAxis
            domain={domain}
            ticks={ticks}
            interval={0}
            tickFormatter={(v: number) => formatCostTick(v, unit)}
            tick={AXIS_TICK}
            axisLine={false}
            tickLine={false}
            width={phone ? 44 : 56}
          />
          <Tooltip
            cursor={{ stroke: "var(--chart-axis)" }}
            content={({ active, payload }) => {
              const r = active ? (payload?.[0]?.payload as CostRow | undefined) : undefined;
              if (!r) return null;
              return (
                <ChartTooltipBox
                  title={`Period ending ${formatDate(r.periodEnd)}`}
                  rows={[
                    { key: "all", label: "all in", value: formatCost(r.allIn, unit), color },
                    ...(r.exFixed !== null ? [{ key: "ex", label: "without fixed charges", value: formatCost(r.exFixed, unit), color: CONTEXT_COLOR }] : []),
                  ]}
                />
              );
            }}
          />
          {hasEx && (
            <Line
              dataKey="exFixed"
              name="Without fixed charges"
              stroke={CONTEXT_COLOR}
              strokeWidth={2}
              dot={loneDot(rows ?? [], "exFixed", CONTEXT_COLOR)}
              activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--bg-card)", fill: CONTEXT_COLOR }}
              connectNulls={false}
              isAnimationActive={!reduced}
              animationDuration={CHART_ANIMATION_MS}
            />
          )}
          <Line
            dataKey="allIn"
            name="All in"
            stroke={color}
            strokeWidth={2}
            dot={loneDot(rows ?? [], "allIn", color)}
            activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--bg-card)", fill: color }}
            connectNulls={false}
            isAnimationActive={!reduced}
            animationDuration={CHART_ANIMATION_MS}
          />
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

// ---- Credits running total ----

export const CREDIT_COLUMNS: ChartColumn<CreditRow>[] = [
  { header: "Bill date", cell: (r) => formatDate(r.billDate) },
  { header: "Credits", cell: (r) => r.label },
  { header: "Amount", cell: (r) => formatMoney(r.amountCents, { cents: true }), numeric: true },
  { header: "Credits so far", cell: (r) => formatMoney(r.soFarCents, { cents: true }), numeric: true },
];

export function CreditsCard({ detail, loading, refreshing }: { detail: UtilityDetail | undefined; loading: boolean; refreshing: boolean }) {
  const reduced = useReducedMotion();
  const theme = useResolvedTheme();
  const phone = !useMediaQuery("(min-width: 768px)");
  const rows = detail ? creditRows(detail.credits) : undefined;
  const total = detail ? -detail.credits_total_cents : null;
  const { domain, ticks } = niceScale((rows ?? []).map((r) => r.soFarCents), { includeZero: true });
  const years = spansYears((rows ?? []).map((r) => r.billDate));
  return (
    <ChartCard<CreditRow>
      eyebrow="Credits"
      title={total === null ? undefined : `${formatMoney(total, { cents: true })} in credits`}
      subtitle="Credits on the bills in this range, added up."
      ariaLabel={`Bill credits added up over time.${total !== null ? ` ${formatMoney(total, { cents: true })} in all.` : ""}`}
      data={rows}
      columns={CREDIT_COLUMNS}
      rowKey={(r) => r.billKey}
      tableCaption="Bill credits and the running total"
      loading={loading}
      refreshing={refreshing}
      emptyText="No credits in this range."
      height={180}
    >
      <ResponsiveContainer width="100%" height={180}>
        <LineChart key={theme} accessibilityLayer={false} data={rows} margin={MARGIN}>
          <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
          <XAxis
            dataKey="billDate"
            tickFormatter={(v: string) => billTick(v, years)}
            tick={AXIS_TICK}
            axisLine={false}
            tickLine={false}
            tickMargin={8}
            minTickGap={phone ? 24 : 16}
          />
          <YAxis
            domain={domain}
            ticks={ticks}
            interval={0}
            tickFormatter={moneyTickFormatter(ticks)}
            tick={AXIS_TICK}
            axisLine={false}
            tickLine={false}
            width={phone ? 44 : 56}
          />
          <Tooltip
            cursor={{ stroke: "var(--chart-axis)" }}
            content={({ active, payload }) => {
              const r = active ? (payload?.[0]?.payload as CreditRow | undefined) : undefined;
              if (!r) return null;
              return (
                <ChartTooltipBox
                  title={`${r.label}, billed ${formatDate(r.billDate)}`}
                  rows={[
                    { key: "amt", label: "this bill", value: formatMoney(r.amountCents, { cents: true }) },
                    { key: "sum", label: "so far", value: formatMoney(r.soFarCents, { cents: true }), color: CREDITS_COLOR },
                  ]}
                />
              );
            }}
          />
          <Line
            type="stepAfter"
            dataKey="soFarCents"
            name="Credits so far"
            stroke={CREDITS_COLOR}
            strokeWidth={2}
            dot={{ r: 4, strokeWidth: 2, stroke: "var(--bg-card)", fill: CREDITS_COLOR }}
            activeDot={{ r: 5, strokeWidth: 2, stroke: "var(--bg-card)", fill: CREDITS_COLOR }}
            isAnimationActive={!reduced}
            animationDuration={CHART_ANIMATION_MS}
          />
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}
