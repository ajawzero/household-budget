import { CashflowBars } from "../../components/charts/CashflowBars";
import { ChartCard, type ChartColumn } from "../../components/charts/ChartCard";
import type { CashflowMonth } from "../../lib/api";
import { formatMoney, formatMonth } from "../../lib/format";
import { Title, type LoadState } from "./parts";

/** "Actual", "Actual, partial" or "Plan". */
export function basisText(m: CashflowMonth): string {
  if (m.basis === "plan") return "Plan";
  return m.complete ? "Actual" : "Actual, partial";
}

/** True when the bars mix actual and plan months (Phase 4). */
export function isMixed(months: CashflowMonth[]): boolean {
  return months.some((m) => m.basis === "actual");
}

export const CASHFLOW_COLUMNS: ChartColumn<CashflowMonth>[] = [
  { header: "Month", cell: (m) => formatMonth(m.month) },
  { header: "Income", cell: (m) => formatMoney(m.income_cents), numeric: true },
  { header: "Spending and saved", cell: (m) => formatMoney(m.spending_cents), numeric: true },
  { header: "Saved and invested", cell: (m) => formatMoney(m.saved_invested_cents), numeric: true },
  { header: "Left over", cell: (m) => formatMoney(m.left_over_cents), numeric: true },
];

export const MIXED_COLUMNS: ChartColumn<CashflowMonth>[] = [
  ...CASHFLOW_COLUMNS,
  { header: "Basis", cell: basisText },
];

export function cashflowSummary(months: CashflowMonth[]): string {
  const first = months[0];
  const last = months[months.length - 1];
  if (!first || !last) return "No cash flow plan yet.";
  if (isMixed(months)) {
    const actual = months.filter((m) => m.basis === "actual");
    const partial = actual.filter((m) => !m.complete).map((m) => formatMonth(m.month));
    const plan = months.filter((m) => m.basis === "plan");
    return `Income, spending and saved, and left over by month, ${formatMonth(first.month)} to ${formatMonth(last.month)}. ${actual.length} actual months${partial.length ? ` (${partial.join(", ")} partial)` : ""}, then ${plan.length} plan months, hatched.`;
  }
  const span = first === last ? formatMonth(first.month) : `${formatMonth(first.month)} to ${formatMonth(last.month)}`;
  return `Planned income, spending and left over by month, ${span}. ${formatMonth(first.month)}: income ${formatMoney(first.income_cents)}, spending ${formatMoney(first.spending_cents)}, left over ${formatMoney(first.left_over_cents)}.`;
}

interface CashflowCardProps {
  months: CashflowMonth[] | undefined;
  note: string | null | undefined;
  state: LoadState;
}

/** Income and spending bars per plan month with a left over line. */
export function CashflowCard({ months, note, state }: CashflowCardProps) {
  const mixed = isMixed(months ?? []);
  const partial = (months ?? []).filter((m) => m.basis === "actual" && !m.complete);
  return (
    <ChartCard<CashflowMonth>
      title={<Title plan={!mixed}>Cash flow</Title>}
      subtitle={
        mixed
          ? `Actual months, then the plan (hatched). Spending counts saved and invested, like the plan.${partial.length ? ` ${partial.map((m) => formatMonth(m.month)).join(", ")} ${partial.length === 1 ? "is" : "are"} partial (pale): statements not all in.` : ""}`
          : (note ?? "Income vs spending, month over month")
      }
      ariaLabel={cashflowSummary(months ?? [])}
      data={months}
      columns={mixed ? MIXED_COLUMNS : CASHFLOW_COLUMNS}
      rowKey={(m) => m.month}
      tableCaption={
        mixed
          ? "Cash flow by month, actual then plan. Spending includes saved and invested."
          : "Cash flow plan by month. Spending includes saved and invested."
      }
      loading={state.loading}
      refreshing={state.refreshing}
      error={state.error}
      quietError
      emptyText="No cash flow plan yet."
      height={260}
    >
      <CashflowBars months={months ?? []} spendingLabel="Spending and saved" />
    </ChartCard>
  );
}
