import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { ChevronLeft, ChevronRight, Info, Pencil, TriangleAlert } from "lucide-react";
import { useSearchParams } from "react-router";
import { Money } from "../../components/money/Money";
import { PageHeader } from "../../components/shell/PageHeader";
import { Button } from "../../components/ui/button";
import { Card, CardHeader } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorBanner } from "../../components/ui/error-banner";
import { Skeleton } from "../../components/ui/skeleton";
import { useMe } from "../../lib/auth";
import { addMonths, formatMoney, formatMonth, thisMonth } from "../../lib/format";
import { isMonth } from "../cashflow/api";
import { Pill, ProgressLine, RemainingPill } from "../cashflow/parts";
import { TrackedCardsMonth } from "../cashflow/TrackedCardsCard";
import { budgetKeys, FIRST_MONTH, getBudgets, type BudgetsOut } from "./api";
import { BudgetTable } from "./BudgetTable";
import { ChatBudgetsCard } from "./ChatBudgetsCard";
import { EditTargetsDrawer } from "./EditTargetsDrawer";

/** Members see this when the server does not say why (it always should). */
const ADMIN_NOTE = "Only an admin can change targets.";

/** "The chat changed 2 targets since your edit." */
export function conflictsText(n: number, mine: boolean): string {
  return `The chat changed ${n} ${n === 1 ? "target" : "targets"} since ${mine ? "your edit" : "it was edited"}.`;
}

/**
 * Budget (PLAN 8.2, docs/plans/budget-targets.md). Opens on the latest complete
 * month. Group targets come from the chat's plan, with the owner's edits on top.
 * An admin changes them for the current month (and later months the chat
 * planned) in Edit targets. Past months keep the targets they had and offer a
 * way to this month. Members and demo see the targets and the Edited tags only.
 */
export function BudgetPage() {
  const [params, setParams] = useSearchParams();
  const raw = params.get("month");
  const asked = isMonth(raw) ? raw : undefined;
  const q = useQuery({ queryKey: budgetKeys.month(asked), queryFn: () => getBudgets(asked), placeholderData: keepPreviousData });
  const d = q.data;
  const month = d?.month ?? asked;
  const current = d?.current_month ?? thisMonth();
  const isAdmin = useMe().data?.role === "admin";

  function go(m: string | null) {
    setParams(
      (p) => {
        const next = new URLSearchParams(p);
        if (m === null) next.delete("month");
        else next.set("month", m);
        return next;
      },
      { replace: true },
    );
  }

  return (
    <>
      <PageHeader title="Budget" />
      <div className="flex flex-col gap-4 pb-8">
        <MonthHeader
          month={month}
          current={current}
          onPrev={() => month && go(addMonths(month, -1))}
          onNext={() => month && go(addMonths(month, 1))}
          onToday={() => go(current)}
          latestComplete={d?.latest_complete_month ?? null}
          onLatest={() => go(null)}
        />
        {q.isError && !d && <ErrorBanner message="Could not load the budget." onRetry={() => void q.refetch()} retrying={q.isFetching} />}
        {!d && q.isPending && (
          <Card>
            <div role="status" aria-label="Loading" className="flex flex-col gap-3">
              <Skeleton className="h-6 w-1/3" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-5/6" />
            </div>
          </Card>
        )}
        {d && <Body data={d} refreshing={q.isPlaceholderData} isAdmin={isAdmin} onGoCurrent={() => go(d.current_month)} />}
      </div>
    </>
  );
}

interface MonthHeaderProps {
  month: string | undefined;
  current: string;
  latestComplete: string | null;
  onPrev: () => void;
  onNext: () => void;
  onToday: () => void;
  onLatest: () => void;
}

function MonthHeader({ month, current, latestComplete, onPrev, onNext, onToday, onLatest }: MonthHeaderProps) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-1">
        <Button size="icon" variant="ghost" aria-label="Previous month" disabled={!month || month <= FIRST_MONTH} onClick={onPrev}>
          <ChevronLeft size={20} aria-hidden="true" />
        </Button>
        <h2 className="m-0 min-w-36 text-center text-xl font-medium text-fg" aria-live="polite">
          {month ? formatMonth(month) : "Loading…"}
        </h2>
        <Button size="icon" variant="ghost" aria-label="Next month" disabled={!month || month >= current} onClick={onNext}>
          <ChevronRight size={20} aria-hidden="true" />
        </Button>
      </div>
      <div className="flex items-center gap-2">
        {latestComplete && month !== latestComplete && (
          <Button size="sm" onClick={onLatest}>
            Latest complete
          </Button>
        )}
        <Button size="sm" onClick={onToday} disabled={month === current}>
          Today
        </Button>
      </div>
    </div>
  );
}

function Body({
  data: d,
  refreshing,
  isAdmin,
  onGoCurrent,
}: {
  data: BudgetsOut;
  refreshing: boolean;
  isAdmin: boolean;
  onGoCurrent: () => void;
}) {
  const [editing, setEditing] = useState<{ open: boolean; focus: string | null }>({ open: false, focus: null });
  const canEdit = d.can_edit && isAdmin && (d.groups.some((g) => g.editable) || d.addable_groups.length > 0);
  const openAt = (focus: string | null) => setEditing({ open: true, focus });
  const note = canEdit ? null : (d.edit_note ?? (isAdmin ? null : ADMIN_NOTE));
  const firstConflict = d.groups.find((g) => g.edit?.conflict)?.key ?? null;
  return (
    <div className={refreshing ? "opacity-60 transition-opacity duration-100" : undefined}>
      <div className="flex flex-col gap-4">
        {d.note && (
          <p role="status" className="m-0 flex items-start gap-2 rounded-card border border-line bg-card px-4 py-3 text-sm text-fg shadow-card">
            <Info size={18} aria-hidden="true" className="mt-px shrink-0 text-warn" />
            <span>
              <span className="font-medium">{d.note}.</span>{" "}
              {d.month === d.current_month ? "This month is still going, so actuals are low." : "Actuals will grow as statements arrive."}
              {d.latest_complete_month ? ` ${formatMonth(d.latest_complete_month)} is the latest complete month.` : ""}
            </span>
          </p>
        )}
        {d.statements_needed.length > 0 && (
          <p className="m-0 flex items-start gap-2 text-sm text-warn">
            <TriangleAlert size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
            Waiting on statements: {d.statements_needed.join(", ")}.
          </p>
        )}
        <div className="grid items-start gap-4 min-[1200px]:grid-cols-[minmax(0,1fr)_360px]">
          <div className="flex min-w-0 flex-col gap-4">
            <Summary data={d} />
            <Card>
              <CardHeader
                title={
                  <>
                    By group
                    {d.plan_source === "plan" && <Pill>plan</Pill>}
                  </>
                }
                subtitle={canEdit ? "Targets come from the chat's plan. You can change this month's." : "Targets come from the chat's plan."}
                actions={
                  canEdit && (
                    <Button size="sm" disabled={refreshing} onClick={() => openAt(null)}>
                      <Pencil size={16} aria-hidden="true" />
                      Edit targets
                    </Button>
                  )
                }
              />
              {note && (
                <div className="-mt-2 mb-4 flex flex-wrap items-center gap-x-3 gap-y-2">
                  <p className="m-0 text-sm text-fg-secondary">{note}</p>
                  {d.month < d.current_month && (
                    <Button size="sm" onClick={onGoCurrent}>
                      Go to {formatMonth(d.current_month)}
                    </Button>
                  )}
                </div>
              )}
              {d.edits_notice && (
                <p role="status" className="m-0 mb-4 flex items-start gap-2 text-sm text-warn">
                  <Info size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
                  {d.edits_notice}
                </p>
              )}
              {d.conflicts > 0 && (
                <div className="-mt-1 mb-4 flex flex-wrap items-center gap-x-3 gap-y-2">
                  <p className="m-0 flex items-start gap-2 text-sm text-warn">
                    <TriangleAlert size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
                    {conflictsText(d.conflicts, isAdmin)}
                  </p>
                  {canEdit && (
                    <Button size="sm" disabled={refreshing} onClick={() => openAt(firstConflict)}>
                      Review
                    </Button>
                  )}
                </div>
              )}
              {d.groups.length === 0 ? (
                <EmptyState title="Nothing spent or planned this month." />
              ) : (
                <BudgetTable data={d} onEdit={canEdit && !refreshing ? openAt : undefined} />
              )}
            </Card>
            {/*
              Stays mounted while open, even when a refetch turns editing off
              (a 409 for a month with no plan), so the form can say why.
            */}
            {(canEdit || editing.open) && (
              <EditTargetsDrawer
                open={editing.open}
                onOpenChange={(open) => setEditing((e) => ({ ...e, open }))}
                data={d}
                focusKey={editing.focus}
              />
            )}
          </div>
          <div className="flex min-w-0 flex-col gap-4">
            <ChatBudgetsCard budgets={d.chat_budgets} />
            {d.tracked_cards && <TrackedCardsMonth tracked={d.tracked_cards} month={d.month} />}
          </div>
        </div>
      </div>
    </div>
  );
}

function Summary({ data: d }: { data: BudgetsOut }) {
  const t = d.totals;
  const status = t.plan_cents === null ? "no_plan" : t.actual_cents > t.plan_cents ? "over" : "under";
  return (
    <Card>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <Stat label="Planned spending" plan={t.plan_cents !== null}>
          <Money cents={t.plan_cents} empty="No plan" proportional />
        </Stat>
        <Stat label="Actual">
          <Money cents={t.actual_cents} proportional />
        </Stat>
        <div className="col-span-2 sm:col-span-1">
          <p className="m-0 text-sm font-medium text-fg-secondary">Remaining</p>
          <div className="mt-1.5">
            <RemainingPill status={status} remaining={t.remaining_cents} />
          </div>
        </div>
      </div>
      <div className="mt-4">
        <ProgressLine
          actual={t.actual_cents}
          plan={t.plan_cents}
          color={status === "over" ? "var(--fill-bad)" : "var(--fill-good)"}
          label="Spending plus saved and invested"
        />
      </div>
      <p className="m-0 mt-3 text-sm text-fg-secondary">
        Spending plus saved and invested. Income {formatMoney(d.actual_income_cents)}
        {d.planned_income_cents !== null ? ` of ${formatMoney(d.planned_income_cents)} planned` : ""}. Purchases on a plan in the
        spending plan count by what they billed this month, like the plan.
      </p>
    </Card>
  );
}

function Stat({ label, plan = false, children }: { label: string; plan?: boolean; children: ReactNode }) {
  return (
    <div>
      <p className="m-0 text-sm font-medium text-fg-secondary">
        {label}
        {plan && <Pill>plan</Pill>}
      </p>
      <p className="m-0 mt-1 text-2xl font-semibold text-fg">{children}</p>
    </div>
  );
}
