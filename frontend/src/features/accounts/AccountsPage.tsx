import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { PageHeader } from "../../components/shell/PageHeader";
import { Card } from "../../components/ui/card";
import { LinkTabs } from "../../components/ui/link-tabs";
import { ErrorBanner } from "../../components/ui/error-banner";
import { Skeleton } from "../../components/ui/skeleton";
import { DEFAULT_RANGE, getAccounts, queryKeys, type RangeKey, type SeriesKey } from "../../lib/api";
import { cn } from "../../lib/cn";
import { AccountDrawer } from "./AccountDrawer";
import { DebtTab } from "./debt/DebtTab";
import { GroupCard } from "./GroupCard";
import { parseRange, parseSeries } from "./model";
import { NetWorthCard } from "./NetWorthCard";
import { NotesStrip } from "./NotesStrip";
import { SummaryCard } from "./SummaryCard";
import { EquityNotesProvider } from "./vehicles/EquityNotes";

/**
 * Accounts (PLAN 8.2): net worth card on top, then group cards on the left
 * and the Summary on the right. Range, series and the open account live in
 * the URL, so a view can be bookmarked or shared.
 */
export function AccountsPage() {
  const [params, setParams] = useSearchParams();
  const range = parseRange(params.get("range"));
  const series = parseSeries(params.get("series"));
  const openKey = params.get("account");
  const debtTab = params.get("tab") === "debt";

  const accounts = useQuery({
    queryKey: queryKeys.accounts(range),
    queryFn: () => getAccounts(range),
    placeholderData: keepPreviousData,
    enabled: !debtTab,
  });

  function setParam(name: string, value: string | null, fallback?: string) {
    const next = new URLSearchParams(params);
    if (value === null || value === fallback) next.delete(name);
    else next.set(name, value);
    setParams(next, { replace: name !== "account" });
  }

  const data = accounts.data;
  const tabs = (
    <LinkTabs
      label="Accounts views"
      tabs={[
        { label: "Overview", to: "?", active: !debtTab },
        { label: "Debt", to: "?tab=debt", active: debtTab },
      ]}
    />
  );
  if (debtTab) {
    return (
      <>
        <PageHeader title="Accounts" tabs={tabs} />
        <DebtTab />
      </>
    );
  }
  return (
    <EquityNotesProvider>
      <PageHeader title="Accounts" tabs={tabs} />
      <div className="flex flex-col gap-4">
        <NetWorthCard
          range={range}
          series={series}
          onRangeChange={(r: RangeKey) => setParam("range", r, DEFAULT_RANGE)}
          onSeriesChange={(s: SeriesKey) => setParam("series", s, "networth")}
        />
        {data && <NotesStrip data={data} />}
        {accounts.isError && !data && (
          <ErrorBanner message="Could not load accounts." onRetry={() => void accounts.refetch()} retrying={accounts.isFetching} />
        )}
        {!data && accounts.isPending && <LoadingCards />}
        {data && (
          <div className="grid gap-4 lg:grid-cols-3 lg:items-start">
            <div
              className={cn(
                "flex min-w-0 flex-col gap-4 transition-opacity duration-100 lg:col-span-2",
                accounts.isPlaceholderData && "opacity-60",
              )}
            >
              {data.groups.map((g) => (
                <GroupCard key={g.group} group={g} range={range} onOpen={(k) => setParam("account", k)} />
              ))}
              {data.groups.length === 0 && (
                <Card>
                  <p className="m-0 text-fg-secondary">No accounts yet. They appear after the first import.</p>
                </Card>
              )}
            </div>
            <div className="min-w-0 lg:sticky lg:top-4">
              <SummaryCard summary={data.summary} groups={data.groups} />
            </div>
          </div>
        )}
      </div>
      <AccountDrawer accountKey={openKey} range={range} onClose={() => setParam("account", null)} />
    </EquityNotesProvider>
  );
}

function LoadingCards() {
  return (
    <div role="status" aria-label="Loading accounts" className="grid gap-4 lg:grid-cols-3">
      <div className="flex flex-col gap-4 lg:col-span-2">
        <Skeleton className="h-40 rounded-card" />
        <Skeleton className="h-40 rounded-card" />
      </div>
      <Skeleton className="h-64 rounded-card" />
    </div>
  );
}
