import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartCard, type ChartColumn } from "../../components/charts/ChartCard";
import { AXIS_TICK, CHART_ANIMATION_MS, ChartTooltipBox } from "../../components/charts/ChartTooltip";
import { niceScale } from "../../components/charts/scale";
import type { PlanStepDown } from "../../lib/api";
import { formatCompact, formatMoney, formatMonth, formatMonthShort } from "../../lib/format";
import { useReducedMotion } from "../../lib/media";
import { useResolvedTheme } from "../../lib/theme";

/** Plan payments are spending, so the bars take the spending color. One series, no legend. */
const COLOR = "var(--chart-spending)";

export interface StepRow extends PlanStepDown {
  /** Names of the plans whose last payment was the month before. */
  endedNames: string[];
}

export function stepRows(steps: PlanStepDown[], names: Map<string, string>): StepRow[] {
  return steps.map((s) => ({ ...s, endedNames: s.ended.map((k) => names.get(k) ?? k) }));
}

export const STEP_COLUMNS: ChartColumn<StepRow>[] = [
  { header: "Month", cell: (r) => formatMonth(r.month) },
  { header: "Plan payments", cell: (r) => formatMoney(r.monthly_cents), numeric: true },
  { header: "Drop", cell: (r) => (r.drop_cents ? formatMoney(-r.drop_cents) : ""), numeric: true },
  { header: "Plans ended", cell: (r) => r.endedNames.join(", ") },
  { header: "Plans left", cell: (r) => String(r.plan_count), numeric: true },
];

/** "Plan payments fall from $370 to $0 by Apr 2027, in 3 steps." */
export function stepSummary(rows: StepRow[]): string {
  const first = rows[0];
  const last = rows[rows.length - 1];
  if (!first || !last) return "No plan payments.";
  const steps = rows.filter((r) => r.drop_cents > 0).length;
  return `Monthly plan payments fall from ${formatMoney(first.monthly_cents)} in ${formatMonth(first.month)} to ${formatMoney(last.monthly_cents)} by ${formatMonth(last.month)}, in ${steps} ${steps === 1 ? "step" : "steps"}.`;
}

/** How the monthly plan payments step down as each plan ends. */
export function StepDownChart({ rows, loading, error }: { rows: StepRow[] | undefined; loading: boolean; error: string | null }) {
  const reduced = useReducedMotion();
  const theme = useResolvedTheme();
  const { domain, ticks } = niceScale((rows ?? []).map((r) => r.monthly_cents), { includeZero: true });
  return (
    <ChartCard<StepRow>
      title="Step-downs"
      subtitle="Monthly plan payments as each plan ends"
      ariaLabel={stepSummary(rows ?? [])}
      data={rows}
      columns={STEP_COLUMNS}
      rowKey={(r) => r.month}
      tableCaption="Monthly plan payments by month"
      loading={loading}
      error={error}
      quietError
      emptyText="No plans to step down."
      height={220}
      footer={
        rows && (
          <ul className="m-0 mt-3 flex list-none flex-col gap-1 p-0 text-sm text-fg-secondary">
            {rows
              .filter((r) => r.drop_cents > 0)
              .map((r) => (
                <li key={r.month}>
                  <span className="font-medium text-fg">{formatMonth(r.month)}</span>: {formatMoney(r.drop_cents)} less a month.{" "}
                  {r.endedNames.join(" and ")} {r.endedNames.length === 1 ? "is" : "are"} paid off.
                </li>
              ))}
          </ul>
        )
      }
    >
      <ResponsiveContainer width="100%" height={220}>
        <BarChart key={theme} accessibilityLayer={false} data={rows} barCategoryGap="28%" margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
          <XAxis
            dataKey="month"
            tickFormatter={formatMonthShort}
            tick={AXIS_TICK}
            axisLine={{ stroke: "var(--chart-axis)" }}
            tickLine={false}
            tickMargin={8}
          />
          <YAxis domain={domain} ticks={ticks} interval={0} tickFormatter={formatCompact} tick={AXIS_TICK} axisLine={false} tickLine={false} width={48} />
          <Tooltip
            cursor={{ fill: "var(--bg-hover)" }}
            content={({ active, payload }) => {
              const r = active ? (payload?.[0]?.payload as StepRow | undefined) : undefined;
              if (!r) return null;
              return (
                <ChartTooltipBox
                  title={formatMonth(r.month)}
                  rows={[{ key: "m", label: `a month, ${r.plan_count} ${r.plan_count === 1 ? "plan" : "plans"}`, value: formatMoney(r.monthly_cents), color: COLOR }]}
                />
              );
            }}
          />
          <Bar dataKey="monthly_cents" name="Plan payments" fill={COLOR} maxBarSize={24} radius={[4, 4, 0, 0]} isAnimationActive={!reduced} animationDuration={CHART_ANIMATION_MS} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}
