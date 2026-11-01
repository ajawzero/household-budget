import { useId, useState } from "react";
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { CashflowMonth } from "../../lib/api";
import { formatCompact, formatMoney, formatMonth, formatMonthShort } from "../../lib/format";
import { useReducedMotion } from "../../lib/media";
import { useResolvedTheme } from "../../lib/theme";
import { AXIS_TICK, CHART_ANIMATION_MS, ChartTooltipBox } from "./ChartTooltip";
import { niceScale } from "./scale";
import { LegendItem, seriesFade } from "./SeriesLegend";

const INCOME = "var(--chart-income)";
const SPENDING = "var(--chart-spending)";
const LEFTOVER = "var(--chart-leftover)";

interface CashflowBarsProps {
  months: CashflowMonth[];
  height?: number;
  /** Fixed width (tests). Leave out to fill the card. */
  width?: number;
  /** Name of the spending series ("Spending", or "Spending and saved" when it counts saved and invested). */
  spendingLabel?: string;
}

/** A pale wash of a series color. Marks partial months (statements not all in). */
export function wash(color: string): string {
  return `color-mix(in oklab, ${color} 35%, var(--bg-card))`;
}

/**
 * Income and spending bars per month with a left over line. One y axis.
 * Plan months are hatched when the chart mixes plan and actual months. When
 * every month is plan, the bars stay solid and the card title says "plan".
 * Partial actual months (statements not all in) are a pale wash with an
 * outline, the same as on Cash Flow.
 */
export function CashflowBars({ months, height = 260, width, spendingLabel = "Spending" }: CashflowBarsProps) {
  const ids = useId().replace(/:/g, "");
  const reduced = useReducedMotion();
  // The legend item under the mouse; the other series step back.
  const [focus, setFocus] = useState<string | null>(null);
  const theme = useResolvedTheme();
  const mixed = months.some((m) => m.basis === "plan") && months.some((m) => m.basis !== "plan");
  const hatched = (m: CashflowMonth) => mixed && m.basis === "plan";
  const isPartial = (m: CashflowMonth) => m.basis === "actual" && m.complete === false;
  const showPartial = months.some(isPartial);
  const fillFor = (m: CashflowMonth, color: string, hatch: string) =>
    hatched(m) ? `url(#${hatch})` : isPartial(m) ? wash(color) : color;
  const { domain, ticks } = niceScale(
    months.flatMap((m) => [m.income_cents, m.spending_cents, m.left_over_cents]),
    { includeZero: true },
  );

  const chart = (
    <ComposedChart
      key={theme}
      accessibilityLayer={false}
      data={months}
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
      <XAxis dataKey="month" tickFormatter={formatMonthShort} tick={AXIS_TICK} axisLine={false} tickLine={false} tickMargin={8} />
      <YAxis domain={domain} tickFormatter={formatCompact} tick={AXIS_TICK} axisLine={false} tickLine={false} width={56} ticks={ticks} interval={0} />
      <ReferenceLine y={0} stroke="var(--chart-axis)" />
      <Tooltip
        cursor={{ fill: "var(--bg-hover)" }}
        content={({ active, payload }) => {
          const m = active ? (payload?.[0]?.payload as CashflowMonth | undefined) : undefined;
          if (!m) return null;
          return (
            <ChartTooltipBox
              title={`${formatMonth(m.month)}${m.basis === "plan" ? " (plan)" : isPartial(m) ? " (partial)" : ""}`}
              rows={[
                { key: "i", label: "Income", value: formatMoney(m.income_cents), color: INCOME },
                { key: "s", label: spendingLabel, value: formatMoney(m.spending_cents), color: SPENDING },
                { key: "l", label: "Left over", value: formatMoney(m.left_over_cents), color: LEFTOVER },
              ]}
            />
          );
        }}
      />
      <Bar dataKey="income_cents" name="Income" className={seriesFade(focus, "income")} maxBarSize={24} radius={[4, 4, 0, 0]} isAnimationActive={!reduced} animationDuration={CHART_ANIMATION_MS}>
        {months.map((m) => (
          <Cell
            key={m.month}
            fill={fillFor(m, INCOME, `${ids}-income`)}
            stroke={isPartial(m) ? INCOME : undefined}
            strokeWidth={isPartial(m) ? 1.5 : undefined}
          />
        ))}
      </Bar>
      <Bar dataKey="spending_cents" name={spendingLabel} className={seriesFade(focus, "spending")} maxBarSize={24} radius={[4, 4, 0, 0]} isAnimationActive={!reduced} animationDuration={CHART_ANIMATION_MS}>
        {months.map((m) => (
          <Cell
            key={m.month}
            fill={fillFor(m, SPENDING, `${ids}-spending`)}
            stroke={isPartial(m) ? SPENDING : undefined}
            strokeWidth={isPartial(m) ? 1.5 : undefined}
          />
        ))}
      </Bar>
      <Line
        dataKey="left_over_cents"
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
      <CashflowLegend showPlan={mixed} showPartial={showPartial} spendingLabel={spendingLabel} hatchId={`${ids}-income`} onFocus={setFocus} />
    </div>
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

function CashflowLegend({
  showPlan,
  showPartial,
  spendingLabel,
  hatchId,
  onFocus,
}: {
  showPlan: boolean;
  showPartial: boolean;
  spendingLabel: string;
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
        {spendingLabel}
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
    </ul>
  );
}
