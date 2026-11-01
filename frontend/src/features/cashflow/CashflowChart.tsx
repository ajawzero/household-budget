import { useId, useState } from "react";
import { wash } from "../../components/charts/CashflowBars";
import {
  Bar,
  type BarShapeProps,
  CartesianGrid,
  ComposedChart,
  Line,
  Rectangle,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  useYAxisScale,
} from "recharts";
import { AXIS_TICK, CHART_ANIMATION_MS, ChartTooltipBox } from "../../components/charts/ChartTooltip";
import { niceScale } from "../../components/charts/scale";
import { LegendItem, seriesFade } from "../../components/charts/SeriesLegend";
import { formatCompact, formatMoney, formatMonth, formatMonthShort } from "../../lib/format";
import { useReducedMotion } from "../../lib/media";
import { useResolvedTheme } from "../../lib/theme";
import type { CashflowMonthOut, MonthStatus } from "./api";

export const INCOME = "var(--chart-income)";
export const SPENDING = "var(--chart-spending)";
export const LEFTOVER = "var(--chart-leftover)";

/** actual: a complete month. partial: statements not all in. plan: this month or later, drawn from the plan. */
export type Basis = "actual" | "partial" | "plan";

/** One month as the chart and its table draw it. Cents. */
export interface ChartRow {
  month: string;
  status: MonthStatus;
  basis: Basis;
  income: number;
  /** Leaves out saved and invested, like the KPI. */
  spending: number;
  saved: number;
  leftOver: number;
  /** The plan's income, when the month has a plan. */
  planIncome: number | null;
  /** The plan's spending without its saved and invested part, so it compares with the bar. */
  planSpending: number | null;
  planSaved: number | null;
  planLeftOver: number | null;
}

/**
 * Months from GET /api/cashflow to chart rows. This month and later show the
 * plan (labeled plan) when there is one. Past months show actuals.
 */
export function toChartRows(months: CashflowMonthOut[]): ChartRow[] {
  return months.map((m) => {
    const p = m.plan;
    const planPart = {
      planIncome: p ? p.income_cents : null,
      planSpending: p ? p.spending_cents - p.saved_invested_cents : null,
      planSaved: p ? p.saved_invested_cents : null,
      planLeftOver: p ? p.left_over_cents : null,
    };
    if ((m.status === "current" || m.status === "future") && p) {
      return {
        month: m.month,
        status: m.status,
        basis: "plan",
        income: p.income_cents,
        spending: p.spending_cents - p.saved_invested_cents,
        saved: p.saved_invested_cents,
        leftOver: p.left_over_cents,
        ...planPart,
      };
    }
    return {
      month: m.month,
      status: m.status,
      basis: m.complete ? "actual" : "partial",
      income: m.income_cents,
      spending: m.spending_cents,
      saved: m.saved_invested_cents,
      leftOver: m.left_over_cents,
      ...planPart,
    };
  });
}

/** Plan ticks are drawn on actual and partial months that have a plan. */
function hasTick(r: ChartRow): boolean {
  return r.basis !== "plan" && r.planIncome !== null;
}

interface CashflowChartProps {
  rows: ChartRow[];
  height?: number;
  /** Fixed width (tests). Leave out to fill the card. */
  width?: number;
}

/**
 * Income and spending bars per month with a left over line. One y axis.
 * Plan months are hatched. Partial months are a pale wash with an outline.
 * A dark tick on a bar is the plan for that month.
 */
export function CashflowChart({ rows, height = 280, width }: CashflowChartProps) {
  const ids = useId().replace(/:/g, "");
  const reduced = useReducedMotion();
  // The legend item under the mouse; the other series step back.
  const [focus, setFocus] = useState<string | null>(null);
  const theme = useResolvedTheme();
  const { domain, ticks } = niceScale(
    rows.flatMap((r) => [r.income, r.spending, r.leftOver, r.planIncome ?? 0, r.planSpending ?? 0]),
    { includeZero: true },
  );
  const showPlan = rows.some((r) => r.basis === "plan");
  const showPartial = rows.some((r) => r.basis === "partial");
  const showTicks = rows.some(hasTick);

  const chart = (
    <ComposedChart
      key={theme}
      accessibilityLayer={false}
      data={rows}
      width={width}
      height={width ? height : undefined}
      barGap={2}
      barCategoryGap="28%"
      margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
    >
      <defs>
        <Hatch id={`${ids}-income`} color={INCOME} />
        <Hatch id={`${ids}-spending`} color={SPENDING} />
      </defs>
      <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
      <XAxis dataKey="month" tickFormatter={formatMonthShort} tick={AXIS_TICK} axisLine={false} tickLine={false} tickMargin={8} minTickGap={8} />
      <YAxis domain={domain} tickFormatter={formatCompact} tick={AXIS_TICK} axisLine={false} tickLine={false} width={56} ticks={ticks} interval={0} />
      <ReferenceLine y={0} stroke="var(--chart-axis)" />
      <Tooltip cursor={{ fill: "var(--bg-hover)" }} content={({ active, payload }) => <RowTip row={active ? (payload?.[0]?.payload as ChartRow | undefined) : undefined} />} />
      <Bar
        dataKey="income"
        className={seriesFade(focus, "income")}
        name="Income"
        maxBarSize={24}
        isAnimationActive={!reduced}
        animationDuration={CHART_ANIMATION_MS}
        shape={(p: BarShapeProps) => <SeriesBar {...p} color={INCOME} hatch={`url(#${ids}-income)`} planKey="planIncome" />}
      />
      <Bar
        dataKey="spending"
        className={seriesFade(focus, "spending")}
        name="Spending"
        maxBarSize={24}
        isAnimationActive={!reduced}
        animationDuration={CHART_ANIMATION_MS}
        shape={(p: BarShapeProps) => <SeriesBar {...p} color={SPENDING} hatch={`url(#${ids}-spending)`} planKey="planSpending" />}
      />
      <Line
        dataKey="leftOver"
        className={seriesFade(focus, "leftover")}
        name="Left over"
        type="linear"
        stroke={LEFTOVER}
        strokeWidth={2}
        dot={{ r: 4, fill: LEFTOVER, stroke: "var(--bg-card)", strokeWidth: 2 }}
        activeDot={{ r: 5, fill: LEFTOVER, stroke: "var(--bg-card)", strokeWidth: 2 }}
        isAnimationActive={!reduced}
        animationDuration={CHART_ANIMATION_MS}
      />
    </ComposedChart>
  );

  return (
    <div>
      {width ? chart : <ResponsiveContainer width="100%" height={height}>{chart}</ResponsiveContainer>}
      <Legend showPlan={showPlan} showPartial={showPartial} showTicks={showTicks} hatchId={`${ids}-income`} onFocus={setFocus} />
    </div>
  );
}

type SeriesBarProps = BarShapeProps & { color: string; hatch: string; planKey: "planIncome" | "planSpending" };

/** One bar, filled by its month's basis, with the plan tick on top when there is one. */
function SeriesBar({ x, y, width, height, payload, color, hatch, planKey }: SeriesBarProps) {
  const yScale = useYAxisScale();
  const row = payload as ChartRow | undefined;
  if (!row) return null;
  const basis = row.basis;
  const fill = basis === "plan" ? hatch : basis === "partial" ? wash(color) : color;
  const plan = hasTick(row) ? row[planKey] : null;
  const tickY = plan !== null && yScale ? yScale(plan) : undefined;
  return (
    <g>
      <Rectangle
        x={x}
        y={y}
        width={width}
        height={height}
        radius={[4, 4, 0, 0]}
        fill={fill}
        stroke={basis === "partial" ? color : undefined}
        strokeWidth={basis === "partial" ? 1.5 : undefined}
      />
      {tickY !== undefined && (
        <line
          x1={x - 3}
          x2={x + width + 3}
          y1={tickY}
          y2={tickY}
          stroke="var(--chart-target)"
          strokeWidth={2}
          strokeLinecap="round"
        />
      )}
    </g>
  );
}


function RowTip({ row }: { row: ChartRow | undefined }) {
  if (!row) return null;
  const suffix = row.basis === "plan" ? " (plan)" : row.basis === "partial" ? " (partial)" : "";
  const rows = [
    { key: "i", label: "Income", value: formatMoney(row.income), color: INCOME },
    { key: "s", label: "Spending", value: formatMoney(row.spending), color: SPENDING },
    { key: "v", label: "Saved and invested", value: formatMoney(row.saved) },
    { key: "l", label: "Left over", value: formatMoney(row.leftOver), color: LEFTOVER },
  ];
  if (hasTick(row)) {
    rows.push(
      { key: "pi", label: "Plan income", value: formatMoney(row.planIncome ?? 0) },
      { key: "ps", label: "Plan spending", value: formatMoney(row.planSpending ?? 0) },
    );
  }
  return (
    <ChartTooltipBox
      title={`${formatMonth(row.month)}${suffix}`}
      rows={rows}
      note={row.basis === "partial" ? "Statements not all in yet." : undefined}
    />
  );
}

/** 45 degree lines over a pale wash of the series color. Marks plan months. */
function Hatch({ id, color }: { id: string; color: string }) {
  return (
    <pattern id={id} patternUnits="userSpaceOnUse" width={6} height={6} patternTransform="rotate(45)">
      <rect width={6} height={6} fill={color} opacity={0.3} />
      <line x1={0} y1={0} x2={0} y2={6} stroke={color} strokeWidth={3} />
    </pattern>
  );
}

function Legend({
  showPlan,
  showPartial,
  showTicks,
  hatchId,
  onFocus,
}: {
  showPlan: boolean;
  showPartial: boolean;
  showTicks: boolean;
  hatchId: string;
  onFocus: (id: string | null) => void;
}) {
  return (
    <ul className="m-0 mt-3 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-sm text-fg-secondary">
      <LegendItem id="income" onFocus={onFocus}>
        <span aria-hidden="true" className="h-2.5 w-2.5 rounded-sm" style={{ background: INCOME }} />
        Income
      </LegendItem>
      <LegendItem id="spending" onFocus={onFocus}>
        <span aria-hidden="true" className="h-2.5 w-2.5 rounded-sm" style={{ background: SPENDING }} />
        Spending
      </LegendItem>
      <LegendItem id="leftover" onFocus={onFocus}>
        <span aria-hidden="true" className="h-0.5 w-3.5 rounded-full" style={{ background: LEFTOVER }} />
        Left over
      </LegendItem>
      {showPlan && (
        <li className="flex items-center gap-1.5">
          <svg aria-hidden="true" width={10} height={10}>
            <rect width={10} height={10} rx={2} fill={`url(#${hatchId})`} />
          </svg>
          Plan
        </li>
      )}
      {showPartial && (
        <li className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="h-2.5 w-2.5 rounded-sm"
            style={{ background: wash(INCOME), boxShadow: `inset 0 0 0 1.5px ${INCOME}` }}
          />
          Partial month
        </li>
      )}
      {showTicks && (
        <li className="flex items-center gap-1.5">
          <span aria-hidden="true" className="h-0.5 w-3.5 rounded-full bg-chart-target" />
          Plan target
        </li>
      )}
    </ul>
  );
}
