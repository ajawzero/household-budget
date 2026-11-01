import { TriangleAlert } from "lucide-react";
import { ChartCard, type ChartColumn } from "../../components/charts/ChartCard";
import { Meter, statusText } from "../../components/charts/Meter";
import type { BudgetWatchItem } from "../../lib/api";
import { formatMoney } from "../../lib/format";
import { actualOverTarget } from "./format";
import { CardNote, type LoadState } from "./parts";

const PERIOD_LABEL: Record<BudgetWatchItem["period_basis"], string> = {
  calendar: "Month",
  card_cycle: "Card cycle",
};

export const BUDGET_COLUMNS: ChartColumn<BudgetWatchItem>[] = [
  { header: "Budget", cell: (b) => b.label },
  { header: "Actual", cell: (b) => (b.actual_cents === null ? "No actual yet" : formatMoney(b.actual_cents)), numeric: true },
  { header: "Target", cell: (b) => formatMoney(b.target_cents), numeric: true },
  { header: "Status", cell: (b) => statusText(b.actual_cents, b.target_cents, b.status) },
];

function summary(budgets: BudgetWatchItem[]): string {
  const parts = budgets.map((b) => `${b.label}: ${statusText(b.actual_cents, b.target_cents, b.status).toLowerCase()}`);
  return `Budget watch. ${parts.join(". ")}.`;
}

interface BudgetWatchCardProps {
  budgets: BudgetWatchItem[] | undefined;
  state: LoadState;
}

/** The chat's budgets as it reported them: a meter per budget with a target tick. */
export function BudgetWatchCard({ budgets, state }: BudgetWatchCardProps) {
  return (
    <ChartCard<BudgetWatchItem>
      title="Budget watch"
      subtitle="Latest cycle vs target, as the chat reported it"
      ariaLabel={summary(budgets ?? [])}
      data={budgets}
      columns={BUDGET_COLUMNS}
      rowKey={(b) => b.key}
      tableCaption="Budget watch"
      loading={state.loading}
      refreshing={state.refreshing}
      error={state.error}
      quietError
      emptyText="No budgets to watch yet."
      footer={<CardNote>The tick mark is the target. A bar past the tick is over budget.</CardNote>}
    >
      <ul className="m-0 flex list-none flex-col gap-5 p-0">
        {budgets?.map((b) => <BudgetRow key={b.key} budget={b} />)}
      </ul>
    </ChartCard>
  );
}

function BudgetRow({ budget: b }: { budget: BudgetWatchItem }) {
  const notes = [b.cycle_note, b.note].filter(Boolean).join(" · ");
  return (
    <li>
      <div className="mb-2 flex items-baseline justify-between gap-3 text-sm">
        <span className="min-w-0 truncate font-medium text-fg">
          {b.label}
          <span className="ml-2 text-xs font-normal text-fg-secondary">{PERIOD_LABEL[b.period_basis]}</span>
        </span>
        <span className="tabular shrink-0 text-fg">{actualOverTarget(b.actual_cents, b.target_cents)}</span>
      </div>
      <Meter actual={b.actual_cents} target={b.target_cents} status={b.status} label={b.label} />
      {notes && <p className="m-0 mt-1 text-sm text-fg-secondary">{notes}</p>}
      {b.statements_needed && (
        <p className="m-0 mt-1 flex items-center gap-1 text-sm text-warn">
          <TriangleAlert size={14} aria-hidden="true" />
          Statements needed. This number may be old.
        </p>
      )}
    </li>
  );
}
