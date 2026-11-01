import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { ChartCard, type ChartColumn } from "../../components/charts/ChartCard";
import { Money } from "../../components/money/Money";
import { PageHeader } from "../../components/shell/PageHeader";
import { Card, CardHeader } from "../../components/ui/card";
import { ErrorBanner } from "../../components/ui/error-banner";
import { SegmentedControl, type SegmentOption } from "../../components/ui/segmented-control";
import { addMonths, formatDate, formatMoney, formatMonth, thisMonth } from "../../lib/format";
import { useFadeIn } from "../../lib/motion";
import { cashflowKeys, getBreakdown, getCashflow, STATUS_TEXT, type BreakdownBy, type CashflowOut, type MonthRange } from "./api";
import { type BreakdownPeriod, RankedCard, SpendingRingCard } from "./BreakdownCards";
import { CashflowChart, type ChartRow, toChartRows } from "./CashflowChart";
import { CategoryDrawer } from "./CategoryDrawer";
import { IncomeTab } from "./IncomeTab";
import { KpiRow } from "./KpiRow";
import { PageTabs, type TabItem } from "./PageTabs";
import { PlanTab } from "./PlanTab";
import { PlanVsActualCard } from "./PlanVsActualCard";
import { TrackedCardsCard } from "./TrackedCardsCard";
import { UtilitiesTab } from "./utilities/UtilitiesTab";

type Tab = "overview" | "plan" | "income" | "utilities";
type RangeKey = "6m" | "12m" | "ytd";

const TABS: TabItem<Tab>[] = [
  { value: "overview", label: "Overview" },
  { value: "plan", label: "Plan" },
  { value: "income", label: "Income" },
  { value: "utilities", label: "Utilities" },
];

const RANGES: SegmentOption<RangeKey>[] = [
  { value: "6m", label: "6M", ariaLabel: "6 months" },
  { value: "12m", label: "12M", ariaLabel: "12 months" },
  { value: "ytd", label: "YTD", ariaLabel: "Year to date" },
];

/** The months a range key asks for. 12 months is the API default, so it sends nothing. */
export function rangeFor(key: RangeKey, now: string = thisMonth()): MonthRange {
  if (key === "6m") return { from: addMonths(now, -5), to: now };
  if (key === "ytd") return { from: `${now.slice(0, 4)}-01`, to: now };
  return {};
}

function asTab(v: string | null): Tab {
  return v === "plan" || v === "income" || v === "utilities" ? v : "overview";
}

function asRange(v: string | null): RangeKey {
  return v === "6m" || v === "ytd" ? v : "12m";
}

/** Cash Flow (PLAN 8.2): KPIs, monthly bars, breakdowns, actual vs plan, and the Plan, Income and Utilities tabs. */
export function CashflowPage() {
  const [params, setParams] = useSearchParams();
  const tab = asTab(params.get("tab"));
  const panelRef = useFadeIn<HTMLDivElement>(tab, true);
  const rangeKey = asRange(params.get("range"));
  const range = rangeFor(rangeKey);

  function set(key: string, value: string, fallback: string) {
    setParams(
      (p) => {
        const next = new URLSearchParams(p);
        if (value === fallback) next.delete(key);
        else next.set(key, value);
        return next;
      },
      { replace: true },
    );
  }

  return (
    <>
      <PageHeader
        title="Cash Flow"
        tabs={<PageTabs label="Cash flow views" idPrefix="cashflow" value={tab} tabs={TABS} onChange={(t) => set("tab", t, "overview")} />}
        actions={
          tab !== "plan" && tab !== "utilities" && (
            <SegmentedControl label="Date range" value={rangeKey} options={RANGES} onChange={(r) => set("range", r, "12m")} />
          )
        }
      />
      <div ref={panelRef} id="cashflow-panel" role="tabpanel" aria-labelledby={`cashflow-tab-${tab}`} className="pb-8">
        {tab === "overview" && <Overview range={range} />}
        {tab === "plan" && <PlanTab />}
        {tab === "income" && <IncomeTab range={range} />}
        {tab === "utilities" && <UtilitiesTab />}
      </div>
    </>
  );
}

export const CHART_COLUMNS: ChartColumn<ChartRow>[] = [
  { header: "Month", cell: (r) => formatMonth(r.month) },
  { header: "Income", cell: (r) => formatMoney(r.income), numeric: true },
  { header: "Spending", cell: (r) => formatMoney(r.spending), numeric: true },
  { header: "Saved and invested", cell: (r) => formatMoney(r.saved), numeric: true },
  { header: "Left over", cell: (r) => formatMoney(r.leftOver), numeric: true },
  { header: "Plan income", cell: (r) => (r.planIncome === null ? "None" : formatMoney(r.planIncome)), numeric: true },
  { header: "Plan spending", cell: (r) => (r.planSpending === null ? "None" : formatMoney(r.planSpending)), numeric: true },
  { header: "Status", cell: (r) => STATUS_TEXT[r.status] },
];

export function chartSummary(d: CashflowOut): string {
  const span = `${formatMonth(d.from)} to ${formatMonth(d.to)}`;
  const t = d.totals;
  if (t.months === 0) return `Income, spending and left over by month, ${span}. No complete months yet.`;
  return `Income, spending and left over by month, ${span}. Over ${t.months} complete months: income ${formatMoney(t.income_cents)}, spending ${formatMoney(t.spending_cents)}, left over ${formatMoney(t.left_over_cents)}.`;
}

function Overview({ range }: { range: MonthRange }) {
  const cf = useQuery({ queryKey: cashflowKeys.cashflow(range), queryFn: () => getCashflow(range), placeholderData: keepPreviousData });
  const [period, setPeriod] = useState<BreakdownPeriod>("month");
  const [by, setBy] = useState<BreakdownBy>("category");
  const [category, setCategory] = useState<string | null>(null);
  const d = cf.data;
  const bRange: MonthRange = period === "range" && d ? { from: d.from, to: d.to } : {};
  const bEnabled = period === "month" || !!d;
  const ring = useQuery({
    queryKey: cashflowKeys.breakdown(bRange, "group"),
    queryFn: () => getBreakdown({ ...bRange, by: "group" }),
    enabled: bEnabled,
    placeholderData: keepPreviousData,
  });
  const ranked = useQuery({
    queryKey: cashflowKeys.breakdown(bRange, by),
    queryFn: () => getBreakdown({ ...bRange, by }),
    enabled: bEnabled,
    placeholderData: keepPreviousData,
  });

  const rows = d ? toChartRows(d.months) : undefined;
  const loadError = cf.isError && !d ? "Not loaded." : null;
  const complete = new Set((d?.months ?? []).filter((m) => m.complete).map((m) => m.month));

  return (
    <div className="flex flex-col gap-4">
      {loadError && <ErrorBanner message="Could not load cash flow." onRetry={() => void cf.refetch()} retrying={cf.isFetching} />}
      <KpiRow data={d} loading={cf.isPending} />
      <ChartCard<ChartRow>
        eyebrow="Cash flow"
        title={d ? `${formatMonth(d.from)} to ${formatMonth(d.to)}` : undefined}
        subtitle="Spending leaves out saved and invested. Left over is what is left after both."
        ariaLabel={d ? chartSummary(d) : "Cash flow by month"}
        data={rows}
        columns={CHART_COLUMNS}
        rowKey={(r) => r.month}
        tableCaption="Cash flow by month. Plan spending leaves out the planned saved and invested."
        loading={cf.isPending}
        refreshing={cf.isPlaceholderData}
        error={loadError}
        quietError
        emptyText="No months in this range."
        height={280}
      >
        <CashflowChart rows={rows ?? []} />
      </ChartCard>
      <SpendingRingCard
        data={ring.data}
        period={period}
        onPeriodChange={setPeriod}
        loading={ring.isPending}
        refreshing={ring.isPlaceholderData}
        error={ring.isError && !ring.data ? "Not loaded." : null}
      />
      <div className="grid items-start gap-4 min-[960px]:grid-cols-2">
        <RankedCard
          data={ranked.data}
          by={by}
          onByChange={setBy}
          onCategory={setCategory}
          loading={ranked.isPending}
          refreshing={ranked.isPlaceholderData}
          error={ranked.isError && !ranked.data ? "Not loaded." : null}
        />
        <div className="flex min-w-0 flex-col gap-4">
          <PlanVsActualCard items={d?.plan_vs_actual} loading={cf.isPending} />
          {d?.tracked_cards && <TrackedCardsCard tracked={d.tracked_cards} completeMonths={complete} />}
          {d && d.waiting_reimbursement.length > 0 && <WaitingCard data={d} />}
        </div>
      </div>
      <CategoryDrawer categoryKey={category} onClose={() => setCategory(null)} />
    </div>
  );
}

function WaitingCard({ data }: { data: CashflowOut }) {
  return (
    <Card>
      <CardHeader
        title="Waiting on reimbursement"
        subtitle={`${formatMoney(data.waiting_reimbursement_cents)} left out of spending until it is paid back.`}
      />
      <ul className="m-0 flex list-none flex-col gap-2 p-0">
        {data.waiting_reimbursement.map((w) => (
          <li key={w.txn_key} className="flex items-baseline justify-between gap-3 text-sm">
            <span className="min-w-0">
              <span className="block truncate text-fg">{w.merchant}</span>
              <span className="block text-xs text-fg-secondary">
                {formatDate(w.txn_date)} · {w.account_name}
              </span>
            </span>
            <Money cents={w.amount_cents} withCents flow className="text-fg" />
          </li>
        ))}
      </ul>
    </Card>
  );
}
