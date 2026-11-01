import { keepPreviousData, useQuery } from "@tanstack/react-query";
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
import { AXIS_TICK, CHART_ANIMATION_MS, ChartTooltipBox } from "../../components/charts/ChartTooltip";
import { ChartCard, type ChartColumn } from "../../components/charts/ChartCard";
import { niceScale } from "../../components/charts/scale";
import { Money } from "../../components/money/Money";
import { Drawer } from "../../components/ui/drawer";
import { ErrorBanner } from "../../components/ui/error-banner";
import { formatCompact, formatMoney, formatMonth, formatMonthShort } from "../../lib/format";
import { useReducedMotion } from "../../lib/media";
import { useResolvedTheme } from "../../lib/theme";
import { cashflowKeys, getCategoryDetail, STATUS_TEXT, type CategoryDetailOut, type CategoryMonth } from "./api";
import { INCOME, SPENDING } from "./CashflowChart";

interface CategoryDrawerProps {
  categoryKey: string | null;
  onClose: () => void;
}

function useCategory(key: string | null) {
  return useQuery({
    queryKey: cashflowKeys.category(key ?? "", {}),
    queryFn: () => getCategoryDetail(key!),
    enabled: key !== null,
    placeholderData: keepPreviousData,
  });
}

/** One category over 12 months: bars, the chat's budget line, the average and the top merchants. */
export function CategoryDrawer({ categoryKey, onClose }: CategoryDrawerProps) {
  const q = useCategory(categoryKey);
  const title = q.data && q.data.category_key === categoryKey ? q.data.name : "Category";
  return (
    <Drawer
      open={categoryKey !== null}
      onOpenChange={(open) => !open && onClose()}
      title={
        <span className="inline-flex items-baseline gap-2">
          {q.data?.emoji && q.data.category_key === categoryKey && <span aria-hidden="true">{q.data.emoji}</span>}
          {title}
        </span>
      }
      widthClass="md:w-[560px]"
    >
      {categoryKey && <Body categoryKey={categoryKey} />}
    </Drawer>
  );
}

function Body({ categoryKey }: { categoryKey: string }) {
  const q = useCategory(categoryKey);
  if (q.isPending || (q.data && q.data.category_key !== categoryKey)) return <p className="text-fg-secondary">Loading…</p>;
  if (q.isError) {
    return <ErrorBanner message="Could not load this category." onRetry={() => void q.refetch()} retrying={q.isFetching} />;
  }
  const d = q.data;
  return (
    <div className="flex flex-col gap-6">
      <Header d={d} />
      <MonthsChart d={d} />
      <TopMerchants d={d} />
      <PlanContext d={d} />
    </div>
  );
}

function Header({ d }: { d: CategoryDetailOut }) {
  const span = d.from === d.to ? formatMonth(d.from) : `${formatMonth(d.from)} to ${formatMonth(d.to)}`;
  return (
    <div>
      <p className="m-0 text-sm text-fg-secondary">
        {d.group_label} · {span}
      </p>
      <p className="m-0 mt-1 text-3xl font-semibold text-fg">
        <Money cents={d.total_cents} proportional />
      </p>
      <p className="m-0 mt-1 text-sm text-fg-secondary">
        {d.average_cents === null ? "No complete month to average yet." : `Average ${formatMoney(d.average_cents)} a month over complete months.`}
      </p>
    </div>
  );
}

export const CATEGORY_MONTH_COLUMNS: ChartColumn<CategoryMonth>[] = [
  { header: "Month", cell: (m) => formatMonth(m.month) },
  { header: "Amount", cell: (m) => formatMoney(m.cents), numeric: true },
  { header: "Budget", cell: (m) => (m.budget_cents === null ? "None" : formatMoney(m.budget_cents)), numeric: true },
  { header: "Rows", cell: (m) => String(m.count), numeric: true },
  { header: "Status", cell: (m) => STATUS_TEXT[m.status] },
];

function MonthsChart({ d }: { d: CategoryDetailOut }) {
  const reduced = useReducedMotion();
  const theme = useResolvedTheme();
  const color = d.kind === "income" ? INCOME : SPENDING;
  const hasBudget = d.months.some((m) => m.budget_cents !== null);
  const { domain, ticks } = niceScale(
    d.months.flatMap((m) => [m.cents, m.budget_cents ?? 0, d.average_cents ?? 0]),
    { includeZero: true },
  );
  const partial = d.months.some((m) => !m.complete);
  const aria = `${d.name} by month, ${formatMonth(d.from)} to ${formatMonth(d.to)}. Total ${formatMoney(d.total_cents)}.${
    d.average_cents !== null ? ` Average ${formatMoney(d.average_cents)}.` : ""
  }`;
  return (
    <ChartCard<CategoryMonth>
      eyebrow="By month"
      ariaLabel={aria}
      data={d.months}
      columns={CATEGORY_MONTH_COLUMNS}
      rowKey={(m) => m.month}
      tableCaption={`${d.name} by month`}
      emptyText="No months in range."
      height={200}
      className="p-0! shadow-none!"
      footer={
        <ul className="m-0 mt-3 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-sm text-fg-secondary">
          <li className="flex items-center gap-1.5">
            <span aria-hidden="true" className="h-2.5 w-2.5 rounded-sm" style={{ background: color }} />
            {d.kind === "income" ? "Income" : "Spent"}
          </li>
          {hasBudget && (
            <li className="flex items-center gap-1.5">
              <span aria-hidden="true" className="h-0.5 w-3.5 rounded-full bg-chart-target" />
              Budget (the chat’s)
            </li>
          )}
          {d.average_cents !== null && (
            <li className="flex items-center gap-1.5">
              <span aria-hidden="true" className="w-3.5 border-t-2 border-dashed border-chart-muted" />
              Average
            </li>
          )}
          {partial && (
            <li className="flex items-center gap-1.5">
              <span
                aria-hidden="true"
                className="h-2.5 w-2.5 rounded-sm"
                style={{ background: `color-mix(in oklab, ${color} 35%, var(--bg-card))`, boxShadow: `inset 0 0 0 1.5px ${color}` }}
              />
              Partial month
            </li>
          )}
        </ul>
      }
    >
      <ResponsiveContainer width="100%" height={200}>
        <ComposedChart key={theme} accessibilityLayer={false} data={d.months} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
          <XAxis dataKey="month" tickFormatter={formatMonthShort} tick={AXIS_TICK} axisLine={false} tickLine={false} tickMargin={8} minTickGap={4} />
          <YAxis domain={domain} ticks={ticks} interval={0} tickFormatter={formatCompact} tick={AXIS_TICK} axisLine={false} tickLine={false} width={48} />
          <ReferenceLine y={0} stroke="var(--chart-axis)" />
          {d.average_cents !== null && (
            <ReferenceLine y={d.average_cents} stroke="var(--chart-muted)" strokeDasharray="4 4" strokeWidth={1.5} />
          )}
          <Tooltip
            cursor={{ fill: "var(--bg-hover)" }}
            content={({ active, payload }) => {
              const m = active ? (payload?.[0]?.payload as CategoryMonth | undefined) : undefined;
              if (!m) return null;
              const rows = [{ key: "c", label: d.kind === "income" ? "income" : "spent", value: formatMoney(m.cents), color }];
              if (m.budget_cents !== null) rows.push({ key: "b", label: "budget", value: formatMoney(m.budget_cents), color: "var(--chart-target)" });
              return (
                <ChartTooltipBox
                  title={`${formatMonth(m.month)} · ${m.count} ${m.count === 1 ? "transaction" : "transactions"}`}
                  rows={rows}
                  note={m.complete ? undefined : STATUS_TEXT[m.status]}
                />
              );
            }}
          />
          <Bar dataKey="cents" maxBarSize={24} radius={[4, 4, 0, 0]} isAnimationActive={!reduced} animationDuration={CHART_ANIMATION_MS}>
            {d.months.map((m) => (
              <Cell
                key={m.month}
                fill={m.complete ? color : `color-mix(in oklab, ${color} 35%, var(--bg-card))`}
                stroke={m.complete ? undefined : color}
                strokeWidth={m.complete ? undefined : 1.5}
              />
            ))}
          </Bar>
          {hasBudget && (
            <Line
              dataKey="budget_cents"
              type="stepAfter"
              stroke="var(--chart-target)"
              strokeWidth={2}
              dot={false}
              connectNulls={false}
              isAnimationActive={!reduced}
              animationDuration={CHART_ANIMATION_MS}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

function TopMerchants({ d }: { d: CategoryDetailOut }) {
  const max = Math.max(1, ...d.top_merchants.map((m) => m.cents));
  const color = d.kind === "income" ? INCOME : SPENDING;
  return (
    <section>
      <h3 className="eyebrow m-0 mb-2">Top merchants</h3>
      {d.top_merchants.length === 0 ? (
        <p className="m-0 text-sm text-fg-secondary">No rows in this range.</p>
      ) : (
        <ul className="m-0 flex list-none flex-col gap-3 p-0">
          {d.top_merchants.map((m) => (
            <li key={m.label}>
              <div className="flex items-baseline justify-between gap-3 text-sm">
                <span className="min-w-0 truncate text-fg">{m.label}</span>
                <span className="shrink-0 text-fg-secondary">
                  <Money cents={m.cents} className="text-fg" /> · {m.count} {m.count === 1 ? "transaction" : "transactions"}
                </span>
              </div>
              <div aria-hidden="true" className="mt-1 h-2">
                <div className="h-2 rounded-r" style={{ width: `${Math.max((Math.max(0, m.cents) / max) * 100, 0.5)}%`, background: color }} />
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** The plan sets targets by group. Show the group's plan for the months that have one. */
function PlanContext({ d }: { d: CategoryDetailOut }) {
  const planned = d.months.filter((m) => m.group_plan_cents !== null);
  const latest = planned[planned.length - 1];
  return (
    <section>
      <h3 className="eyebrow m-0 mb-2">Plan</h3>
      <p className="m-0 text-sm text-fg-secondary">
        {latest && latest.group_plan_cents !== null
          ? `The plan sets ${d.group_label} at ${formatMoney(latest.group_plan_cents)} for ${formatMonth(latest.month)}. It has no target for ${d.name} alone.`
          : `No plan for ${d.group_label} in these months.`}
        {d.months.some((m) => m.budget_cents !== null) ? " The line is the chat’s budget for this category." : ""}
      </p>
    </section>
  );
}
