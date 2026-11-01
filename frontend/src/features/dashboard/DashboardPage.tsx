import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { PageHeader } from "../../components/shell/PageHeader";
import { ErrorBanner } from "../../components/ui/error-banner";
import { getDashboard, queryKeys } from "../../lib/api";
import { cn } from "../../lib/cn";
import { useMe } from "../../lib/auth";
import { DebtsCard, NetWorthPartsCard, SpendingCard } from "./BarCards";
import { BudgetWatchCard } from "./BudgetWatchCard";
import { CashflowCard } from "./CashflowCard";
import { greetingTitle } from "./format";
import { NextChangesCard, ReviewCard, TodoCard } from "./ListCards";
import type { LoadState } from "./parts";
import { StatTiles } from "./StatTiles";
import { UpcomingCard } from "./UpcomingCard";

/** True once the Dashboard has been shown in this page load. */
let entered = false;

/**
 * The household at a glance (PLAN 8.2). Tiles show the latest complete month's
 * actuals with the plan beside them; with no complete month yet they show the
 * chat's plan, labeled "plan". Upcoming lists bills for the next 30 days.
 */
export function DashboardPage() {
  const dash = useQuery({ queryKey: queryKeys.dashboard(), queryFn: () => getDashboard() });
  const d = dash.data;
  const me = useMe();
  const state: LoadState = {
    loading: dash.isPending,
    refreshing: dash.isFetching && !dash.isPending,
    error: dash.isError && !d ? "Not loaded." : null,
  };
  // Cards rise in one after another on the first visit only, never on a refetch or a return.
  const [stagger] = useState(() => !entered);
  useEffect(() => {
    entered = true;
  }, []);

  return (
    <>
      <PageHeader title={greetingTitle(me.data?.display_name)} documentTitle="Dashboard" showTitleOnPhone />
      <div className="flex flex-col gap-4 pb-8">
        {state.error && (
          <ErrorBanner
            message="Could not load the dashboard."
            onRetry={() => void dash.refetch()}
            retrying={dash.isFetching}
          />
        )}
        <div className={cn(stagger && "animate-rise-in")}>
          <StatTiles netWorth={d?.net_worth} plan={d?.plan_month} actual={d?.actual_month} state={state} />
        </div>
        {/* Two stacks on wide screens, set by hand to about even height. Phones read them in order. */}
        <div className="grid items-start gap-4 min-[960px]:grid-cols-2">
          <div className={cn("flex min-w-0 flex-col gap-4", stagger && "stagger-in")}>
            <CashflowCard months={d?.cashflow} note={d?.cashflow_note} state={state} />
            <BudgetWatchCard budgets={d?.budgets} state={state} />
            <SpendingCard
              groups={d?.spending_by_group}
              month={d?.month}
              planSpendingCents={d?.plan_month?.spending_cents}
              actualGroups={d?.actual_by_group}
              actualMonth={d?.actual_month?.month}
              state={state}
            />
            <TodoCard todos={d?.todos} state={state} />
          </div>
          <div className={cn("flex min-w-0 flex-col gap-4", stagger && "stagger-in")}>
            <UpcomingCard items={d?.upcoming} utilities={d?.utilities} creditsEnding={d?.credits_ending} nextTrip={d?.next_trip} state={state} />
            <NextChangesCard changes={d?.next_changes} state={state} />
            <NetWorthPartsCard parts={d?.networth_parts} netWorthCents={d?.net_worth.value_cents} state={state} />
            <DebtsCard debts={d?.debts} state={state} />
            <ReviewCard review={d?.review} aiReview={d?.ai_review} state={state} />
          </div>
        </div>
      </div>
    </>
  );
}

