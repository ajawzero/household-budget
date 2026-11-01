import { TriangleAlert } from "lucide-react";
import { Meter } from "../../components/charts/Meter";
import { Card, CardHeader } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { formatMoney } from "../../lib/format";
import type { ChatBudget } from "./api";

/** "Calendar month" or "Card cycle, closes on the 22nd". */
export function cycleLabel(b: Pick<ChatBudget, "period_basis" | "cycle_note">): string {
  if (b.period_basis === "calendar") return "Calendar month";
  return b.cycle_note ? `Card cycle · ${b.cycle_note}` : "Card cycle";
}

const SOURCE_TEXT: Record<NonNullable<ChatBudget["actual_source"]>, string> = {
  statement: "from statements",
  baseline: "from the baseline",
};

/**
 * The chat's own budgets for the month, as it reported them (Q4.1 default).
 * Card-cycle budgets are labeled with their cycle. Read only: the chat sets
 * these. Group targets (By group) can change in the app; these cannot yet.
 */
export function ChatBudgetsCard({ budgets }: { budgets: ChatBudget[] }) {
  return (
    <Card>
      <CardHeader title="The chat's budgets" subtitle="As the chat reported them. Some follow a card cycle, not the calendar month." />
      {budgets.length === 0 ? (
        <EmptyState title="No budgets from the chat for this month." />
      ) : (
        <ul className="m-0 flex list-none flex-col gap-5 p-0">
          {budgets.map((b) => (
            <li key={b.key}>
              <div className="flex items-baseline justify-between gap-3 text-sm">
                <span className="min-w-0 font-medium text-fg">{b.label}</span>
                <span className="shrink-0 text-fg-secondary tabular">
                  {b.reported_actual_cents === null
                    ? `No spending reported yet. Target ${formatMoney(b.target_cents)}`
                    : `${formatMoney(b.reported_actual_cents)} of ${formatMoney(b.target_cents)}`}
                </span>
              </div>
              <p className="m-0 mb-2 text-xs text-fg-secondary">
                {cycleLabel(b)}
                {b.scope_cards.length > 0 ? ` · ${b.scope_cards.join(", ")}` : ""}
                {b.actual_source ? ` · Actual ${SOURCE_TEXT[b.actual_source]}` : ""}
              </p>
              <Meter actual={b.reported_actual_cents} target={b.target_cents} status={b.status} label={b.label} />
              {b.statements_needed.length > 0 && (
                <p className="m-0 mt-1.5 flex items-start gap-1 text-sm text-warn">
                  <TriangleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
                  Needs {b.statements_needed.length === 1 ? "the statement" : "statements"} for {b.statements_needed.join(", ")}
                </p>
              )}
              {b.note && <p className="m-0 mt-1 text-sm text-fg-secondary">{b.note}</p>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
