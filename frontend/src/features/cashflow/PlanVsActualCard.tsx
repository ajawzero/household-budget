import { ChevronLeft, ChevronRight } from "lucide-react";
import { useState } from "react";
import { Money } from "../../components/money/Money";
import { Button } from "../../components/ui/button";
import { Card, CardHeader } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { TableScroll } from "../../components/ui/table-scroll";
import { formatMoney, formatMonth } from "../../lib/format";
import type { PlanLine, PlanVsActual } from "./api";
import { RemainingPill } from "./parts";

export const MEASURE_TEXT: Record<PlanLine["measure"], string> = {
  group: "Spending in the group",
  saved_invested: "Saved and invested",
  plan_installments: "Billed installments and plan fees",
  unmapped: "Not mapped",
  unplanned: "Not in the plan",
};

/** The month to open on: the latest complete month with a plan, else the latest one. */
export function defaultPlanMonth(items: PlanVsActual[]): string | null {
  const complete = items.filter((p) => p.complete);
  return (complete[complete.length - 1] ?? items[items.length - 1])?.month ?? null;
}

interface PlanVsActualCardProps {
  items: PlanVsActual[] | undefined;
  loading: boolean;
}

/**
 * Actual vs plan for one month, by plan group, using the mapping in PLAN 3.9:
 * each plan group maps to a spending group, saved and invested, or plan payments.
 */
export function PlanVsActualCard({ items, loading }: PlanVsActualCardProps) {
  const list = items ?? [];
  const [picked, setPicked] = useState<string | null>(null);
  const month = picked && list.some((p) => p.month === picked) ? picked : defaultPlanMonth(list);
  const index = list.findIndex((p) => p.month === month);
  const pva = index >= 0 ? list[index] : undefined;

  return (
    <Card>
      <CardHeader
        eyebrow="Actual vs plan"
        title={pva ? formatMonth(pva.month) : undefined}
        subtitle="Planned spending includes saved and invested."
        actions={
          list.length > 1 && (
            <div className="flex items-center gap-1">
              <Button
                size="icon"
                variant="ghost"
                aria-label="Earlier month"
                disabled={index <= 0}
                onClick={() => setPicked(list[index - 1]?.month ?? null)}
              >
                <ChevronLeft size={18} aria-hidden="true" />
              </Button>
              <Button
                size="icon"
                variant="ghost"
                aria-label="Later month"
                disabled={index < 0 || index >= list.length - 1}
                onClick={() => setPicked(list[index + 1]?.month ?? null)}
              >
                <ChevronRight size={18} aria-hidden="true" />
              </Button>
            </div>
          )
        }
      />
      {!pva ? (
        loading ? (
          <p className="m-0 text-sm text-fg-secondary">Loading…</p>
        ) : (
          <EmptyState title="No plan in this range.">The chat's plan for a month shows here once a monthly run brings it.</EmptyState>
        )
      ) : (
        <>
          {!pva.complete && <p className="m-0 mb-3 text-sm text-warn">Statements not in yet. Actuals will grow.</p>}
          <TableScroll label={`Actual vs plan, ${formatMonth(pva.month)}`}>
            <table className="data-table compact">
              <caption className="sr-only">Actual vs plan, {formatMonth(pva.month)}</caption>
              <thead>
                <tr>
                  <th scope="col">Plan group</th>
                  <th scope="col" className="num">
                    Plan
                  </th>
                  <th scope="col" className="num">
                    Actual
                  </th>
                  <th scope="col" className="hidden sm:table-cell">
                    Status
                  </th>
                </tr>
              </thead>
              <tbody>
                {pva.lines.map((l) => (
                  <tr key={`${l.measure}-${l.key}`}>
                    <th scope="row" className="font-normal">
                      <span className="block text-fg">{l.label}</span>
                      {MEASURE_TEXT[l.measure] !== l.label && (
                        <span className="block text-xs text-fg-secondary">{MEASURE_TEXT[l.measure]}</span>
                      )}
                    </th>
                    <td className="num">
                      <Money cents={l.plan_cents} empty="None" />
                    </td>
                    <td className="num">
                      <Money cents={l.actual_cents} empty="None" />
                      <span className="mt-1 block sm:hidden">
                        <RemainingPill
                          status={l.status}
                          remaining={l.plan_cents === null || l.actual_cents === null ? null : l.plan_cents - l.actual_cents}
                          keep={l.measure === "saved_invested"}
                          incomplete={!pva.complete}
                        />
                      </span>
                    </td>
                    <td className="hidden sm:table-cell">
                      <RemainingPill
                        status={l.status}
                        remaining={l.plan_cents === null || l.actual_cents === null ? null : l.plan_cents - l.actual_cents}
                        keep={l.measure === "saved_invested"}
                        incomplete={!pva.complete}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="font-medium">
                  <th scope="row" className="text-left">
                    Spending and saved
                  </th>
                  <td className="num">{formatMoney(pva.planned_spending_cents)}</td>
                  <td className="num">
                    {formatMoney(pva.actual_spending_cents)}
                    <span className="mt-1 block font-normal sm:hidden">
                      <RemainingPill status={pva.status} remaining={pva.planned_spending_cents - pva.actual_spending_cents} incomplete={!pva.complete} />
                    </span>
                  </td>
                  <td className="hidden sm:table-cell">
                    <RemainingPill status={pva.status} remaining={pva.planned_spending_cents - pva.actual_spending_cents} incomplete={!pva.complete} />
                  </td>
                </tr>
                <tr>
                  <th scope="row" className="text-left font-normal">
                    Income
                  </th>
                  <td className="num">{formatMoney(pva.planned_income_cents)}</td>
                  <td className="num">{formatMoney(pva.actual_income_cents)}</td>
                  <td className="hidden sm:table-cell" />
                </tr>
              </tfoot>
            </table>
          </TableScroll>
        </>
      )}
    </Card>
  );
}
