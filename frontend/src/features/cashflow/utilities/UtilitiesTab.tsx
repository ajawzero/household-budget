import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { Card } from "../../../components/ui/card";
import { EmptyState } from "../../../components/ui/empty-state";
import { ErrorBanner } from "../../../components/ui/error-banner";
import { SegmentedControl, type SegmentOption } from "../../../components/ui/segmented-control";
import { getUtilities, getUtility, utilityKeys } from "../../../lib/api";
import { chicagoToday } from "../../../lib/format";
import { CostCard, CreditsCard, UsageCard } from "./UtilityCharts";
import { BillsCard, SummaryCard, YoyCard } from "./UtilityTables";
import { meteredServices, servicesInRange, type Span, spanFrom, type UsageMode } from "./model";

const SPANS: SegmentOption<Span>[] = [
  { value: "1y", label: "1Y", ariaLabel: "1 year" },
  { value: "2y", label: "2Y", ariaLabel: "2 years" },
  { value: "all", label: "All", ariaLabel: "All bills" },
];

const MODES: SegmentOption<UsageMode>[] = [
  { value: "total", label: "Total" },
  { value: "day", label: "Per day" },
];

function asSpan(v: string | null): Span {
  return v === "2y" || v === "all" ? v : "1y";
}

function asMode(v: string | null): UsageMode {
  return v === "day" ? "day" : "total";
}

/**
 * Cash Flow > Utilities (format 1.2): a utility picker, use by bill next to
 * last year, cost per unit, year over year, credits and the bills with their
 * paid status. The picker and span sit in one row above everything they
 * change. Total / Per day sits above the use charts, the only thing it
 * changes. All three live in the URL.
 */
export function UtilitiesTab({ today = chicagoToday() }: { today?: string }) {
  const [params, setParams] = useSearchParams();
  const list = useQuery({ queryKey: utilityKeys.list, queryFn: getUtilities });
  const utilities = list.data?.utilities ?? [];
  const picked = utilities.find((u) => u.utility_key === params.get("utility")) ?? utilities[0];
  const span = asSpan(params.get("span"));
  const mode = asMode(params.get("usage"));
  const from = spanFrom(span, picked?.last_period_end);
  const key = picked?.utility_key ?? "";
  const detail = useQuery({
    queryKey: utilityKeys.detail(key, from),
    queryFn: () => getUtility(key, { from }),
    enabled: !!picked,
    placeholderData: keepPreviousData,
  });

  function set(name: string, value: string, fallback: string) {
    setParams(
      (p) => {
        const next = new URLSearchParams(p);
        if (value === fallback) next.delete(name);
        else next.set(name, value);
        return next;
      },
      { replace: true },
    );
  }

  if (list.isError && !list.data) {
    return <ErrorBanner message="Could not load utilities." onRetry={() => void list.refetch()} retrying={list.isFetching} />;
  }
  if (list.isPending) {
    return (
      <Card>
        <div role="status" aria-label="Loading" className="h-24 animate-pulse rounded-lg bg-subtle" />
      </Card>
    );
  }
  if (!picked) {
    return (
      <Card>
        <EmptyState title="No utility bills yet.">Bills come in with the monthly runs once the chat sends them.</EmptyState>
      </Card>
    );
  }

  const d = detail.data?.utility_key === picked.utility_key ? detail.data : undefined;
  const loading = detail.isPending || (!d && detail.isFetching);
  const refreshing = detail.isPlaceholderData;
  // Only services with bills in this range, in their newest unit. While loading, every service holds its place.
  const metered = d ? servicesInRange(d.bills) : meteredServices(picked.services);
  const options: SegmentOption<string>[] = utilities.map((u) => ({ value: u.utility_key, label: u.provider }));

  const controls = (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      {utilities.length === 1 && <h2 className="m-0 text-lg font-medium text-fg">{picked.provider}</h2>}
      {utilities.length > 1 && (
        <SegmentedControl label="Utility" value={picked.utility_key} options={options} onChange={(v) => set("utility", v, utilities[0]!.utility_key)} />
      )}
      <div className="flex flex-wrap items-center gap-2 sm:ml-auto">
        <SegmentedControl label="Bills shown" value={span} options={SPANS} onChange={(v) => set("span", v, "1y")} />
      </div>
    </div>
  );

  // With no bills to show, the cards would each say "No bills". Only the error is true.
  if (detail.isError && !d) {
    return (
      <div className="flex flex-col gap-4">
        {controls}
        <ErrorBanner message="Could not load these bills." onRetry={() => void detail.refetch()} retrying={detail.isFetching} />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {controls}
      <SummaryCard detail={d} today={today} loading={loading} />
      {metered.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="m-0 text-base font-medium text-fg">Use by bill</h2>
          <SegmentedControl label="Use shown as" value={mode} options={MODES} onChange={(v) => set("usage", v, "total")} />
        </div>
      )}
      <div className="grid items-start gap-4 min-[1200px]:grid-cols-2">
        {metered.map((s) => (
          <UsageCard key={s.service} detail={d} service={s.service} unit={s.unit} mode={mode} loading={loading} refreshing={refreshing} />
        ))}
      </div>
      <div className="grid items-start gap-4 min-[1200px]:grid-cols-2">
        {metered.map((s) => (
          <CostCard key={s.service} detail={d} service={s.service} unit={s.unit} loading={loading} refreshing={refreshing} />
        ))}
      </div>
      <YoyCard detail={d} loading={loading} />
      <div className="grid items-start gap-4 min-[1200px]:grid-cols-[2fr_3fr]">
        <CreditsCard detail={d} loading={loading} refreshing={refreshing} />
        <BillsCard detail={d} today={today} loading={loading} />
      </div>
    </div>
  );
}
