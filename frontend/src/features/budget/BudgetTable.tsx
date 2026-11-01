import { ChevronDown, ChevronRight } from "lucide-react";
import { Fragment, useState } from "react";
import { Money } from "../../components/money/Money";
import { TableScroll } from "../../components/ui/table-scroll";
import { formatMoney } from "../../lib/format";
import { ProgressLine, RemainingPill } from "../cashflow/parts";
import { EditedBadge } from "../transactions/controls";
import type { BudgetGroup, BudgetsOut } from "./api";

/** Fill for a group's progress line. Saved and invested is money kept, so it keeps the left over green. */
export function progressColor(g: Pick<BudgetGroup, "kind" | "status">): string {
  if (g.kind === "saved_invested") return "var(--chart-leftover)";
  if (g.status === "over") return "var(--fill-bad)";
  return "var(--fill-good)";
}

const KIND_NOTE: Record<BudgetGroup["kind"], string | null> = {
  spending: null,
  saved_invested: "Money kept, not spent",
  plan_installments: "Billed this month, with plan fees",
};

/**
 * On phones the Plan column is hidden, so the target shows as "of $X" under
 * the actual, with the Edited tag. When the group can be edited, the line is a
 * button (a 44px hit area) that opens Edit targets at that group.
 */
function PhonePlanLine({ group: g, onEdit }: { group: BudgetGroup; onEdit?: (key: string) => void }) {
  if (g.plan_cents === null) return null;
  const body = (
    <>
      <span>of {formatMoney(g.plan_cents)}</span>
      {/* On its own line, so the tag never widens the column past a phone. */}
      {g.source === "edited" && (
        <>
          {" "}
          <EditedBadge className="mt-1" />
        </>
      )}
    </>
  );
  if (onEdit && g.editable) {
    return (
      <button
        type="button"
        onClick={() => onEdit(g.key)}
        className="tap-target flex w-full flex-col items-end justify-center text-xs text-fg-secondary underline decoration-dotted underline-offset-2 sm:hidden"
      >
        {body}
        <span className="sr-only">. Edit the {g.label} target.</span>
      </button>
    );
  }
  return <span className="flex flex-col items-end text-xs text-fg-secondary sm:hidden">{body}</span>;
}

/**
 * Budget by group (PLAN 8.2): the target (the chat's plan with any edits on
 * top, tagged Edited as text), the actual, what is left (a tinted pill with
 * words) and a thin progress line with a target tick. Category rows show
 * actuals, with a target only when the chat sent one. onEdit, for an admin on
 * a month that takes edits, makes each editable group's phone "of $X" line
 * open Edit targets.
 */
export function BudgetTable({ data, onEdit }: { data: BudgetsOut; onEdit?: (key: string) => void }) {
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const toggle = (key: string) =>
    setClosed((s) => {
      const next = new Set(s);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const t = data.totals;

  return (
    <TableScroll className="-mx-5 md:-mx-6" label="Budget by group">
      <table className="data-table compact">
        <caption className="sr-only">
          Budget by group for the month. Targets come from the chat's plan. An admin can change this month's, and those are
          marked Edited.
        </caption>
        <thead>
          <tr>
            <th scope="col" className="pl-5 md:pl-6">
              Group
            </th>
            <th scope="col" className="num hidden sm:table-cell">
              Plan
            </th>
            <th scope="col" className="num">
              Actual
            </th>
            <th scope="col" className="pr-5 text-right md:pr-6">
              Remaining
            </th>
          </tr>
        </thead>
        <tbody>
          {data.groups.map((g) => {
            const open = !closed.has(g.key);
            const hasRows = g.categories.length > 0;
            return (
              <Fragment key={g.key}>
                <tr>
                  <th scope="row" className="pl-5 font-medium md:pl-6">
                    <span className="flex items-center gap-1">
                      {hasRows ? (
                        <button
                          type="button"
                          onClick={() => toggle(g.key)}
                          aria-expanded={open}
                          aria-label={`${open ? "Hide" : "Show"} categories in ${g.label}`}
                          className="tap-target -ml-2 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-fg-secondary hover:bg-active"
                        >
                          {open ? <ChevronDown size={16} aria-hidden="true" /> : <ChevronRight size={16} aria-hidden="true" />}
                        </button>
                      ) : (
                        <span aria-hidden="true" className="-ml-2 inline-block w-8 shrink-0" />
                      )}
                      <span className="min-w-0 text-fg">{g.label}</span>
                    </span>
                    {KIND_NOTE[g.kind] && <span className="ml-7 block text-xs font-normal text-fg-secondary">{KIND_NOTE[g.kind]}</span>}
                    {g.plan_cents !== null && g.plan_cents > 0 && (
                      <div className="ml-7 mt-2 max-w-md">
                        <ProgressLine actual={g.actual_cents} plan={g.plan_cents} color={progressColor(g)} label={g.label} />
                      </div>
                    )}
                  </th>
                  <td className="num hidden sm:table-cell">
                    <Money cents={g.plan_cents} empty="No plan" />
                    {g.source === "edited" && (
                      <span className="mt-1 block">
                        <EditedBadge />
                      </span>
                    )}
                  </td>
                  <td className="num">
                    <Money cents={g.actual_cents} />
                    <PhonePlanLine group={g} onEdit={onEdit} />
                  </td>
                  <td className="pr-5 text-right md:pr-6">
                    <RemainingPill status={g.status} remaining={g.remaining_cents} keep={g.kind === "saved_invested"} />
                  </td>
                </tr>
                {open &&
                  g.categories.map((c) => (
                    <tr key={`${g.key}-${c.category_key}`} className="text-fg-secondary">
                      <th scope="row" className="pl-5 font-normal md:pl-6">
                        <span className="ml-7 flex items-baseline gap-1.5">
                          <span aria-hidden="true" className="inline-block w-5 shrink-0 text-center">
                            {c.emoji ?? ""}
                          </span>
                          <span className="text-fg">{c.name}</span>
                        </span>
                      </th>
                      <td className="num hidden sm:table-cell">
                        {c.target_cents === null ? "" : <Money cents={c.target_cents} />}
                      </td>
                      <td className="num text-fg">
                        <Money cents={c.actual_cents} />
                      </td>
                      <td className="pr-5 text-right md:pr-6">
                        {c.target_cents !== null && <RemainingPill status={c.status} remaining={c.remaining_cents} />}
                      </td>
                    </tr>
                  ))}
              </Fragment>
            );
          })}
        </tbody>
        <tfoot>
          <tr className="font-medium">
            <th scope="row" className="pl-5 text-left md:pl-6">
              <span className="ml-7 block">Total</span>
              <span className="ml-7 block text-xs font-normal text-fg-secondary">Spending plus saved and invested</span>
            </th>
            <td className="num hidden sm:table-cell">
              <Money cents={t.plan_cents} empty="No plan" />
            </td>
            <td className="num">
              <Money cents={t.actual_cents} />
            </td>
            <td className="pr-5 text-right md:pr-6">
              <RemainingPill
                status={t.plan_cents === null ? "no_plan" : t.actual_cents > t.plan_cents ? "over" : "under"}
                remaining={t.remaining_cents}
              />
            </td>
          </tr>
        </tfoot>
      </table>
    </TableScroll>
  );
}
