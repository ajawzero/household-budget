// Dev only (/dev/gallery): every shared component with fictional numbers, so
// the look can be checked in both themes. Not part of the production build.
import { useState } from "react";
import { AreaChart, type AreaPoint } from "../../components/charts/AreaChart";
import { CashflowBars } from "../../components/charts/CashflowBars";
import { ChartCard } from "../../components/charts/ChartCard";
import { groupColor } from "../../components/charts/colors";
import { HBars } from "../../components/charts/HBars";
import { Meter } from "../../components/charts/Meter";
import { Sparkline } from "../../components/charts/Sparkline";
import { StackedBar } from "../../components/charts/StackedBar";
import { AsOf } from "../../components/money/AsOf";
import { Delta } from "../../components/money/Delta";
import { EstBadge } from "../../components/money/EstBadge";
import { Monogram } from "../../components/money/Monogram";
import { Money } from "../../components/money/Money";
import { PageHeader } from "../../components/shell/PageHeader";
import { Button } from "../../components/ui/button";
import { Card, CardHeader } from "../../components/ui/card";
import { CollapsibleGroup } from "../../components/ui/collapsible-group";
import { Drawer } from "../../components/ui/drawer";
import { RangePicker, rangeLabel } from "../../components/ui/range-picker";
import { SegmentedControl } from "../../components/ui/segmented-control";
import type { BudgetWatchItem, CashflowMonth, RangeKey } from "../../lib/api";
import { formatDate, formatMoney, formatMonth } from "../../lib/format";

const SERIES: AreaPoint[] = Array.from({ length: 120 }, (_, i) => {
  const d = new Date(Date.UTC(2026, 5, 1 + i));
  const step = Math.floor(i / 30) * 1_250_000;
  return { date: d.toISOString().slice(0, 10), value: 41_000_000 + step, partial: i > 110 };
});

const MONTHS: CashflowMonth[] = [
  ["2026-05", 1_050_000, 880_000],
  ["2026-06", 1_050_000, 1_120_000],
  ["2026-07", 1_080_000, 910_000],
  ["2026-08", 1_080_000, 940_000],
  ["2026-09", 1_100_000, 905_000],
  ["2026-10", 1_100_000, 920_000],
].map(([month, inc, sp], i) => ({
  month: month as string,
  income_cents: inc as number,
  spending_cents: sp as number,
  saved_invested_cents: 0,
  left_over_cents: (inc as number) - (sp as number),
  basis: i >= 5 ? "plan" : "actual",
  complete: i < 4,
}));

const BUDGETS: BudgetWatchItem[] = [
  { key: "g", label: "Groceries", actual_cents: 98_000, target_cents: 90_000, plan_cents: 90_000, note: null, period_basis: "card_cycle", cycle_note: "Aug 15 to Sep 14", status: "over", statements_needed: false },
  { key: "d", label: "Dining", actual_cents: 41_000, target_cents: 45_000, plan_cents: null, note: null, period_basis: "calendar", cycle_note: null, status: "on_target", statements_needed: false },
  { key: "c", label: "Clothing", actual_cents: null, target_cents: 20_000, plan_cents: null, note: "Statements needed", period_basis: "calendar", cycle_note: null, status: "no_actual", statements_needed: true },
];

export function GalleryPage() {
  const [range, setRange] = useState<RangeKey>("YTD");
  const [mode, setMode] = useState<"totals" | "percent">("totals");
  const [drawer, setDrawer] = useState(false);
  const last = SERIES[SERIES.length - 1]!.value;
  const first = SERIES[0]!.value;

  return (
    <>
      <PageHeader title="Component gallery" actions={<Button variant="primary">Primary</Button>} />
      <div className="flex flex-col gap-4">
        <ChartCard
          eyebrow="Net worth"
          title={<Money cents={last} proportional className="text-3xl font-semibold" />}
          subtitle={<Delta cents={last - first} pct="9.1" period={`${rangeLabel(range)} change`} />}
          controls={<RangePicker value={range} onChange={setRange} />}
          ariaLabel="Net worth up 9.1% year to date"
          data={SERIES}
          columns={[
            { header: "Date", cell: (p) => formatDate(p.date) },
            { header: "Net worth", cell: (p) => formatMoney(p.value), numeric: true },
          ]}
          rowKey={(p) => p.date}
        >
          <AreaChart points={SERIES} label="Net worth" />
        </ChartCard>

        <div className="grid gap-4 lg:grid-cols-2">
          <ChartCard
            title="Income vs spending (plan)"
            ariaLabel="Income and spending by month"
            data={MONTHS}
            columns={[
              { header: "Month", cell: (m) => formatMonth(m.month) },
              { header: "Income", cell: (m) => formatMoney(m.income_cents), numeric: true },
              { header: "Spending", cell: (m) => formatMoney(m.spending_cents), numeric: true },
              { header: "Left over", cell: (m) => formatMoney(m.left_over_cents), numeric: true },
            ]}
            rowKey={(m) => m.month}
          >
            <CashflowBars months={MONTHS} />
          </ChartCard>

          <Card>
            <CardHeader title="Budget watch" subtitle="Latest cycle vs target" />
            <div className="flex flex-col gap-5">
              {BUDGETS.map((b) => (
                <div key={b.key}>
                  <div className="mb-1.5 flex justify-between text-sm">
                    <span>{b.label}</span>
                    <span className="tabular text-fg-secondary">
                      <Money cents={b.actual_cents} empty="No actual" /> / <Money cents={b.target_cents} />
                    </span>
                  </div>
                  <Meter actual={b.actual_cents} target={b.target_cents} status={b.status} label={b.label} />
                </div>
              ))}
            </div>
          </Card>

          <Card>
            <CardHeader title="Where the net worth is" />
            <HBars
              color="var(--accent)"
              rows={[
                { key: "r", label: "Retirement", cents: 21_000_000 },
                { key: "h", label: "Home equity", cents: 14_000_000 },
                { key: "b", label: "Brokerage", cents: 6_500_000 },
                { key: "c", label: "Cash and misc", cents: 1_800_000 },
                { key: "s", label: "Student loan", cents: -2_400_000 },
              ]}
            />
          </Card>

          <Card>
            <CardHeader
              title="Summary"
              actions={
                <SegmentedControl
                  label="Show as"
                  value={mode}
                  onChange={setMode}
                  options={[
                    { value: "totals", label: "Totals" },
                    { value: "percent", label: "Percent" },
                  ]}
                />
              }
            />
            <StackedBar
              label="Assets"
              mode={mode}
              segments={[
                { key: "investments", label: "Investments", cents: 6_500_000, color: groupColor("investments") },
                { key: "retirement", label: "Retirement", cents: 21_000_000, color: groupColor("retirement") },
                { key: "real_estate", label: "Real estate", cents: 52_000_000, color: groupColor("real_estate") },
                { key: "cash", label: "Cash", cents: 1_800_000, color: groupColor("cash") },
                { key: "vehicles", label: "Vehicles", cents: 3_900_000, color: groupColor("vehicles") },
              ]}
            />
          </Card>
        </div>

        <CollapsibleGroup
          title="Credit cards"
          summary={
            <>
              <Delta cents={30_000} display="owed" pct="12.4" />
              <Money cents={212_000} className="text-lg font-medium" />
            </>
          }
        >
          {[
            { name: "Example Card", inst: "Example Bank", stale: false, est: false },
            { name: "Example Brokerage", inst: "Sample Investments", stale: true, est: true },
          ].map((r) => (
            <button
              key={r.name}
              type="button"
              onClick={() => setDrawer(true)}
              className="flex w-full items-center gap-3 border-b border-line px-5 py-3 text-left last:border-b-0 hover:bg-hover md:px-6"
            >
              <Monogram name={r.inst} />
              <span className="min-w-0 flex-1">
                <span className="block text-base text-fg">{r.name}</span>
                <span className="block text-sm text-fg-secondary">{r.inst} · Alex</span>
              </span>
              <span className="hidden md:block">
                <Sparkline
                  points={[
                    { date: "2026-06-30", balance_cents: 150_000 },
                    { date: "2026-07-31", balance_cents: 230_000 },
                    { date: "2026-08-31", balance_cents: 180_000 },
                    { date: "2026-09-30", balance_cents: 212_000 },
                  ]}
                />
              </span>
              <span className="flex flex-col items-end">
                <span className="flex items-center gap-1.5">
                  {r.est && <EstBadge priceDate="2026-10-20" statementDate="2026-09-30" />}
                  <Money cents={212_000} className="text-base" />
                </span>
                <AsOf date="2026-09-30" stale={r.stale} />
              </span>
            </button>
          ))}
        </CollapsibleGroup>

        <Card>
          <CardHeader title="Money and deltas" />
          <ul className="m-0 flex list-none flex-col gap-2 p-0 text-sm">
            <li>
              Flow in <Money cents={225_000} withCents flow /> · out <Money cents={-4_250} withCents flow /> · compact{" "}
              <Money cents={123_000_000} compact /> · missing <Money cents={null} />
            </li>
            <li>
              Spending up <Delta cents={8_000} polarity="lower_is_better" pct="4.2" arrows="diagonal" period="vs last month" />
            </li>
            <li>
              Card owed down <Delta cents={30_000} display="owed" period="1 month change" />
            </li>
            <li>
              Net worth down <Delta cents={-120_000} pct="-0.4" />
            </li>
          </ul>
        </Card>
      </div>

      <Drawer open={drawer} onOpenChange={setDrawer} title="Example Card" description="Example Bank · Credit card">
        <p className="text-fg-secondary">Account detail goes here.</p>
      </Drawer>
    </>
  );
}
