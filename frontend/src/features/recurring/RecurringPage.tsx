import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Clock } from "lucide-react";
import { useSearchParams } from "react-router";
import { Money } from "../../components/money/Money";
import { PageHeader } from "../../components/shell/PageHeader";
import { ErrorBanner } from "../../components/ui/error-banner";
import { LinkTabs } from "../../components/ui/link-tabs";
import { SegmentedControl } from "../../components/ui/segmented-control";
import { getPlans, getRecurring, getRecurringCalendar, recurringKeys, type RecurringCalendar, type RecurringResponse } from "../../lib/api";
import { formatMoney, formatMonth, roundToDollars } from "../../lib/format";
import { useFadeIn } from "../../lib/motion";
import { AllView, CancelledCard, EndingCard, PersonPicker, SavingsCard, SubscriptionsCard } from "./AllView";
import { CalendarView } from "./CalendarView";
import { DueCard } from "./DueList";
import {
  allTotals,
  byPerson,
  itemMap,
  parseLayout,
  parseMonth,
  parsePerson,
  parseTab,
  personOptions,
  type RecurringLayout,
  type RecurringTab,
} from "./model";
import { KpiStrip, MonthNav, SourceLine } from "./parts";
import { PlansView } from "./PlansView";

const LAYOUTS = [
  { value: "list", label: "List" },
  { value: "calendar", label: "Calendar" },
] as const;

/**
 * Recurring (PLAN 8.2): bills and subscriptions from the chat's list, with
 * what is paid this month. Monthly and All tabs, a list or a calendar, and a
 * Plans tab for installment plans. Tab, layout, month and the All tab's
 * person live in the URL.
 */
export function RecurringPage() {
  const [params, setParams] = useSearchParams();
  const tab = parseTab(params.get("tab"));
  const layout = parseLayout(params.get("view"));
  const panelRef = useFadeIn<HTMLDivElement>(tab, true);
  const month = parseMonth(params.get("month"));

  function setParam(name: string, value: string | null) {
    const next = new URLSearchParams(params);
    if (value === null) next.delete(name);
    else next.set(name, value);
    setParams(next, { replace: true });
  }

  const tabs: { key: RecurringTab; label: string }[] = [
    { key: "monthly", label: "Monthly" },
    { key: "all", label: "All" },
    { key: "plans", label: "Plans" },
  ];

  // The All view feeds the badges and notes on monthly rows too (season, cost cutting), so it always loads.
  const all = useQuery({ queryKey: recurringKeys.recurring("all"), queryFn: () => getRecurring({ view: "all" }) });
  const monthly = useQuery({
    queryKey: recurringKeys.recurring("monthly", month),
    queryFn: () => getRecurring({ view: "monthly", month }),
    enabled: tab === "monthly",
    placeholderData: keepPreviousData,
  });
  const calendar = useQuery({
    queryKey: recurringKeys.calendar(month),
    queryFn: () => getRecurringCalendar(month),
    enabled: tab === "monthly" && layout === "calendar",
    placeholderData: keepPreviousData,
  });
  const plans = useQuery({ queryKey: recurringKeys.plans, queryFn: getPlans, enabled: tab === "plans" });

  const items = itemMap(all.data?.items);
  const people = personOptions(all.data?.items);
  const person = parsePerson(params.get("person"), people);
  const shownMonth = monthly.data?.month ?? month;
  const current = all.data?.month ?? monthly.data?.month;
  const failed = tab === "plans" ? plans.isError && !plans.data : (tab === "monthly" ? monthly : all).isError;

  return (
    <>
      <PageHeader
        title="Recurring"
        tabs={
          <LinkTabs
            label="Recurring views"
            tabs={tabs.map((t) => ({ label: t.label, to: t.key === "monthly" ? "?" : `?tab=${t.key}`, active: t.key === tab }))}
          />
        }
        actions={
          tab === "monthly" ? (
            <>
              {shownMonth && current && <MonthNav month={shownMonth} current={current} onChange={(m) => setParam("month", m)} />}
              <SegmentedControl<RecurringLayout>
                label="Show as"
                value={layout}
                options={LAYOUTS}
                onChange={(v) => setParam("view", v === "list" ? null : v)}
              />
            </>
          ) : (
            tab === "all" && people.length >= 2 && <PersonPicker options={people} value={person} onChange={(p) => setParam("person", p)} />
          )
        }
      />
      <div ref={panelRef} className="flex flex-col gap-4 pb-8">
        {failed && (
          <ErrorBanner
            message="Could not load recurring bills."
            onRetry={() => void (tab === "plans" ? plans : tab === "monthly" ? monthly : all).refetch()}
          />
        )}
        {tab === "monthly" && (
          <MonthlyTab
            data={monthly.data}
            loading={monthly.isPending}
            refreshing={monthly.isPlaceholderData}
            layout={layout}
            calendarData={calendar.data}
            calendarLoading={calendar.isPending}
            items={items}
            allItems={all.data?.items}
          />
        )}
        {tab === "all" && (
          <AllTab data={all.data} loading={all.isPending} person={person} personLabel={people.find((o) => o.key === person)?.label ?? null} />
        )}
        {tab === "plans" && (
          <>
            <PlansView data={plans.data} loading={plans.isPending} error={plans.isError ? "Not loaded." : null} />
            <SourceLine runId={plans.data?.source_run_id} />
          </>
        )}
      </div>
    </>
  );
}

function MonthlyTab({
  data,
  loading,
  refreshing,
  layout,
  calendarData,
  calendarLoading,
  items,
  allItems,
}: {
  data: RecurringResponse | undefined;
  loading: boolean;
  refreshing: boolean;
  layout: RecurringLayout;
  calendarData: RecurringCalendar | undefined;
  calendarLoading: boolean;
  items: ReturnType<typeof itemMap>;
  allItems: RecurringResponse["items"] | undefined;
}) {
  // List and Calendar crossfade when the view changes.
  const viewRef = useFadeIn<HTMLDivElement>(layout, true);
  const s = data?.summary;
  const waiting = data?.upcoming.filter((d) => d.state === "waiting").length ?? 0;
  const noHistory = data?.upcoming.filter((d) => d.state === "no_history").length ?? 0;
  const missed = data?.upcoming.filter((d) => d.state === "missed").length ?? 0;
  return (
    <>
      <KpiStrip
        // Transfers due this month are in these sums too, so the label names both.
        label={`Bills and transfers in ${formatMonth(data?.month)}`}
        loading={loading && !data}
        items={[
          { key: "expected", label: "Expected", value: <Money cents={s?.expected_cents} />, sub: s ? `${s.count} ${s.count === 1 ? "payment" : "payments"}` : undefined },
          { key: "paid", label: "Paid", value: <Money cents={s?.paid_cents} />, sub: s ? `${s.paid_count} of ${s.count} paid` : undefined },
          {
            key: "remaining",
            label: "Remaining",
            value: <Money cents={s?.remaining_cents} />,
            sub: s ? `${s.count - s.paid_count} not paid yet` : undefined,
          },
        ]}
      />
      {(waiting > 0 || noHistory > 0 || missed > 0) && (
        <p className="m-0 flex items-start gap-2 text-sm text-fg-secondary">
          <Clock size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
          <span>
            {waiting > 0 && `${waiting} past due ${waiting === 1 ? "bill shows" : "bills show"} "Statement not in": the paying account's statement does not reach that date yet. `}
            {noHistory > 0 &&
              `${noHistory} past due ${noHistory === 1 ? "bill shows" : "bills show"} "No payment history yet": no tagged payment goes back that far. `}
            {missed > 0 && `${missed} ${missed === 1 ? "bill has" : "bills have"} no matching payment in its window. `}
            A bill counts as paid when a transaction tagged with it posts within a few days of the due date.
          </span>
        </p>
      )}
      <div className="grid items-start gap-4 lg:grid-cols-3">
        <div ref={viewRef} className={`flex min-w-0 flex-col gap-4 lg:col-span-2 ${refreshing ? "opacity-60 transition-opacity duration-100" : ""}`}>
          {layout === "calendar" ? (
            <CalendarView data={calendarData} loading={calendarLoading} items={items} />
          ) : (
            <>
              <DueCard title="Upcoming" rows={data?.upcoming} items={items} loading={loading} emptyText="Everything is paid this month." totalLabel="Still due" totalWholeDollars />
              <DueCard title="Complete" rows={data?.complete} items={items} loading={loading} emptyText="Nothing paid yet this month." totalLabel="Paid" totalWholeDollars />
            </>
          )}
        </div>
        <div className="flex min-w-0 flex-col gap-4">
          <SubscriptionsCard items={allItems} />
          <SourceLine runId={data?.source_run_id} />
        </div>
      </div>
    </>
  );
}

function AllTab({
  data,
  loading,
  person,
  personLabel,
}: {
  data: RecurringResponse | undefined;
  loading: boolean;
  /** Owner key to filter by, or null for everyone. */
  person: string | null;
  personLabel: string | null;
}) {
  // The person filter narrows every tile and card on this tab.
  const items = data ? byPerson(data.items, person) : undefined;
  const t = items ? allTotals(items, data?.sections) : undefined;
  // From the rounded figures, so the tiles add up as shown.
  const credits = t ? roundToDollars(t.gross) - roundToDollars(t.net) : 0;
  return (
    <>
      <KpiStrip
        label="Recurring totals"
        loading={loading && !data}
        items={[
          { key: "monthly", label: "A month", value: <Money cents={t?.gross} />, sub: t ? `${t.count} ${t.count === 1 ? "bill" : "bills"} counted` : undefined },
          {
            key: "net",
            label: "After credits",
            value: <Money cents={t?.net} />,
            sub: t ? (credits ? `${formatMoney(credits)} a month in card credits` : "No card credits") : undefined,
          },
          { key: "year", label: "A year", value: <Money cents={t ? t.net * 12 : undefined} />, sub: "After credits" },
          {
            key: "savings",
            label: "Savings and transfers",
            value: <Money cents={t?.savings} />,
            sub: t ? (t.savingsCount ? `A month · ${t.savingsCount} ${t.savingsCount === 1 ? "transfer" : "transfers"}` : "None counted") : undefined,
          },
        ]}
      />
      <AllView items={items} sections={data?.sections} loading={loading} empty={personLabel ? `No bills for ${personLabel}.` : "No bills."} />
      <SavingsCard items={items} sections={data?.sections} />
      <div className="grid items-start gap-4 lg:grid-cols-2">
        <SubscriptionsCard items={items} />
        <EndingCard items={items} sections={data?.sections} />
        <CancelledCard items={items} sections={data?.sections} />
      </div>
      <SourceLine runId={data?.source_run_id} />
    </>
  );
}
