import { useQuery } from "@tanstack/react-query";
import { ChevronDown } from "lucide-react";
import { useState } from "react";
import { ChartCard, type ChartColumn } from "../../components/charts/ChartCard";
import { Money } from "../../components/money/Money";
import { Card, CardHeader } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorBanner } from "../../components/ui/error-banner";
import { TableScroll } from "../../components/ui/table-scroll";
import { cn } from "../../lib/cn";
import { addMonths, formatMoney, formatMonth, formatPct, formatRunDate } from "../../lib/format";
import { cashflowKeys, getCashflowPlan, STATUS_TEXT, type PlanEvent, type PlanMonthOut, type PlanPhase } from "./api";
import { LEFTOVER, SPENDING } from "./CashflowChart";
import { ChangeBadge } from "../recurring/parts";
import { contentsText, EventStatusBadge, Pill } from "./parts";

/** Plan tab: the chat's plan by month, upcoming changes and the cash flow relief timeline. */
export function PlanTab() {
  const q = useQuery({ queryKey: cashflowKeys.plan, queryFn: getCashflowPlan });
  if (q.isError && !q.data) {
    return <ErrorBanner message="Could not load the plan." onRetry={() => void q.refetch()} retrying={q.isFetching} />;
  }
  const d = q.data;
  const loading = q.isPending;
  const focus = d?.months.find((m) => m.status === "current") ?? d?.months[0];
  return (
    <div className="flex flex-col gap-4">
      <PlanMonthsCard months={d?.months} loading={loading} source={d?.source_run_id ?? null} />
      <div className="grid items-start gap-4 lg:grid-cols-2">
        <EventsCard events={d?.events} loading={loading} />
        <PlanGroupsCard month={focus} loading={loading} />
      </div>
      <ReliefCard phases={d?.phases} loading={loading} />
    </div>
  );
}

const MONTH_COLUMNS: ChartColumn<PlanMonthOut>[] = [
  { header: "Month", cell: (m) => formatMonth(m.month) },
  { header: "Income", cell: (m) => formatMoney(m.income_cents), numeric: true },
  { header: "Spending", cell: (m) => formatMoney(m.spending_cents), numeric: true },
  { header: "Saved and invested", cell: (m) => formatMoney(m.saved_invested_cents), numeric: true },
  { header: "Left over", cell: (m) => formatMoney(m.left_over_cents), numeric: true },
  { header: "% saved", cell: (m) => (m.saved_pct === null ? "None" : formatPct(m.saved_pct)), numeric: true },
];

function PlanMonthsCard({ months, loading, source }: { months: PlanMonthOut[] | undefined; loading: boolean; source: string | null }) {
  return (
    <Card>
      <CardHeader
        eyebrow="The plan"
        title="Plan by month"
        subtitle={`Planned spending includes saved and invested. Left over is income minus all of it.${source ? ` From the ${formatRunDate(source)} update.` : ""}`}
      />
      {loading ? (
        <p className="m-0 text-sm text-fg-secondary">Loading…</p>
      ) : !months || months.length === 0 ? (
        <EmptyState title="No plan yet." />
      ) : (
        <TableScroll label="Plan by month">
          <table className="data-table">
            <caption className="sr-only">Plan by month</caption>
            <thead>
              <tr>
                {MONTH_COLUMNS.map((c) => (
                  <th key={c.header} scope="col" className={c.numeric ? "num" : undefined}>
                    {c.header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {months.map((m) => (
                <tr key={m.month}>
                  <th scope="row" className="font-normal">
                    <span className="block text-fg">
                      {formatMonth(m.month)}
                      {m.status === "current" && <Pill>this month</Pill>}
                    </span>
                    {m.note && <span className="block text-xs text-fg-secondary">{m.note}</span>}
                    {m.status !== "current" && m.status !== "future" && (
                      <span className="block text-xs text-fg-secondary">{STATUS_TEXT[m.status]}</span>
                    )}
                  </th>
                  {MONTH_COLUMNS.slice(1).map((c) => (
                    <td key={c.header} className="num">
                      {c.cell(m)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
    </Card>
  );
}

function EventsCard({ events, loading }: { events: PlanEvent[] | undefined; loading: boolean }) {
  const upcoming = (events ?? []).filter((e) => e.upcoming);
  const past = (events ?? []).filter((e) => !e.upcoming);
  return (
    <Card>
      <CardHeader eyebrow="Coming up" title="Upcoming cash flow changes" subtitle="Each change is a monthly amount from that month on." />
      {loading ? (
        <p className="m-0 text-sm text-fg-secondary">Loading…</p>
      ) : !events || events.length === 0 ? (
        <EmptyState title="No changes planned." />
      ) : (
        <>
          <EventList events={upcoming} />
          {past.length > 0 && (
            <>
              <h3 className="eyebrow m-0 mb-2 mt-5">Earlier</h3>
              <EventList events={past} muted />
            </>
          )}
        </>
      )}
    </Card>
  );
}

function EventList({ events, muted = false }: { events: PlanEvent[]; muted?: boolean }) {
  if (events.length === 0) return <p className="m-0 text-sm text-fg-secondary">Nothing upcoming.</p>;
  return (
    <ul className="m-0 flex list-none flex-col p-0">
      {events.map((e) => (
        <li key={e.key} className={cn("flex items-start justify-between gap-3 border-b border-line py-3 last:border-b-0", muted && "opacity-80")}>
          <div className="min-w-0">
            <p className="m-0 text-xs font-medium text-fg-secondary">{formatMonth(e.effective_month)}</p>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <p className="m-0 text-sm text-fg">{e.label}</p>
              <EventStatusBadge status={e.status} />
            </div>
            {e.reason && <p className="m-0 text-sm text-fg-secondary">{e.reason}</p>}
          </div>
          <MonthlyChange cents={e.delta_cents} />
        </li>
      ))}
    </ul>
  );
}

/** A change to monthly left over. More left over is good. */
function MonthlyChange({ cents }: { cents: number }) {
  const up = cents > 0;
  const tone = cents === 0 ? "text-fg-secondary" : up ? "text-good" : "text-bad";
  return (
    <span className={cn("shrink-0 whitespace-nowrap text-sm font-medium tabular", tone)}>
      {cents !== 0 && <span aria-hidden="true">{up ? "↑ " : "↓ "}</span>}
      {up ? "+" : ""}
      {formatMoney(cents)}
      <span className="font-normal text-fg-secondary"> a month</span>
      {cents !== 0 && <span className="sr-only">{up ? ", more left over" : ", less left over"}</span>}
    </span>
  );
}

type GroupRow = PlanMonthOut["by_group"][number] & { maps_to: string | null };

function PlanGroupsCard({ month, loading }: { month: PlanMonthOut | undefined; loading: boolean }) {
  const rows: GroupRow[] | undefined = month?.by_group.map((g) => ({
    ...g,
    maps_to: month.mapping.find((m) => m.key === g.key)?.maps_to ?? null,
  }));
  const max = Math.max(1, ...(rows ?? []).map((r) => r.cents));
  const withLines = (rows ?? []).filter((r) => r.lines.length > 0);
  const columns: ChartColumn<GroupRow>[] = [
    { header: "Plan group", cell: (r) => r.label },
    { header: "Plan", cell: (r) => formatMoney(r.cents), numeric: true },
    { header: "Compared with", cell: (r) => r.maps_to ?? "Not mapped" },
    { header: "What is in it", cell: (r) => contentsText(r.contents) ?? "" },
  ];
  return (
    <ChartCard<GroupRow>
      eyebrow="Plan groups"
      title={month ? formatMonth(month.month) : undefined}
      subtitle="Each group is compared with one measure on Actual vs plan."
      ariaLabel={`Plan groups for ${month ? formatMonth(month.month) : "this month"}. ${(rows ?? []).map((r) => `${r.label} ${formatMoney(r.cents)}`).join(", ")}.`}
      data={rows}
      columns={columns}
      rowKey={(r) => r.key}
      tableCaption="Plan groups and what each is compared with"
      loading={loading}
      emptyText="No plan groups."
      footer={
        <>
          <p className="m-0 mt-4 flex items-center gap-1.5 text-sm text-fg-secondary">
            <span aria-hidden="true" className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: LEFTOVER }} />
            Saved and invested is money kept, so it is not drawn in the spending color.
          </p>
          {/* Outside the chart's role="img" summary, so screen readers reach the lines. */}
          {withLines.length > 0 && (
            <div className="mt-4 border-t border-line pt-3">
              <p className="eyebrow m-0">What is in each group</p>
              <ul className="m-0 mt-1 flex list-none flex-col p-0">
                {withLines.map((r) => (
                  <li key={r.key}>
                    <GroupBills group={r} />
                  </li>
                ))}
              </ul>
              <p className="m-0 mt-2 text-xs text-fg-secondary">
                A bill not charged every month shows its yearly cost spread over the months it runs.
              </p>
            </div>
          )}
        </>
      }
    >
      <ul className="m-0 flex list-none flex-col gap-3 p-0">
        {rows?.map((r) => (
          <li key={r.key}>
            <div className="flex items-baseline justify-between gap-3 text-sm">
              <span className="min-w-0 truncate text-fg">{r.label}</span>
              <Money cents={r.cents} className="text-fg" />
            </div>
            <div aria-hidden="true" className="mt-1 h-2">
              <div
                className="h-2 rounded-r"
                style={{ width: `${Math.max((r.cents / max) * 100, 0.5)}%`, background: r.kind === "saved_invested" ? LEFTOVER : SPENDING }}
              />
            </div>
            {contentsText(r.contents) && <p className="m-0 mt-1 break-words text-xs text-fg-secondary">{contentsText(r.contents)}</p>}
            {r.maps_to && <p className="m-0 mt-1 text-xs text-fg-secondary">Compared with: {r.maps_to}</p>}
          </li>
        ))}
      </ul>
    </ChartCard>
  );
}

/**
 * The recurring bills in one plan group that month, then everything else
 * (gap audit C2). Saved and invested holds transfers, so it says transfers.
 */
function GroupBills({ group }: { group: GroupRow }) {
  const [open, setOpen] = useState(false);
  const n = group.lines.length;
  const saved = group.kind === "saved_invested";
  const noun = saved ? (n === 1 ? "transfer" : "transfers") : n === 1 ? "bill" : "bills";
  const Noun = saved ? "Transfers" : "Bills";
  return (
    <>
      <button
        type="button"
        className="-ml-1 inline-flex min-h-11 items-center gap-1 rounded-lg px-1 text-left text-sm font-medium text-accent-text hover:underline md:min-h-8"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {group.label}: {n} {noun}
        <ChevronDown aria-hidden="true" size={16} className={cn("shrink-0 transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="mb-2 mt-1 rounded-lg bg-subtle p-3 text-sm">
          <ul className="m-0 flex list-none flex-col gap-1 p-0" aria-label={`${Noun} in ${group.label}`}>
            {group.lines.map((b) => (
              <li key={b.key} className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 break-words text-fg">
                  {b.name}
                  {b.change && (
                    <>
                      {" "}
                      <ChangeBadge change={b.change} wasCents={b.previous_cents} />
                    </>
                  )}
                </span>
                <Money cents={b.cents} withCents className="shrink-0 text-fg" />
              </li>
            ))}
            {group.other_cents >= 0 && (
              <li className="flex items-baseline justify-between gap-3 border-t border-line pt-1">
                <span className="min-w-0 text-fg-secondary">Everything else</span>
                <Money cents={group.other_cents} withCents className="shrink-0 text-fg" />
              </li>
            )}
          </ul>
          {group.other_cents < 0 && (
            <p className="m-0 mt-2 border-t border-line pt-2 text-fg-secondary">
              {Noun} are {formatMoney(-group.other_cents, { cents: true })} more than this group's plan.
            </p>
          )}
        </div>
      )}
    </>
  );
}

/** Months a phase covers on the timeline. Open-ended phases show 6 months. */
function phaseMonths(p: PlanPhase): number {
  if (!p.end_month) return 6;
  const [y1, m1] = p.start_month.split("-").map(Number);
  const [y2, m2] = p.end_month.split("-").map(Number);
  return Math.max(1, (y2! - y1!) * 12 + (m2! - m1!) + 1);
}

function phaseSpan(p: PlanPhase): string {
  return p.end_month ? `${formatMonth(p.start_month)} to ${formatMonth(p.end_month)}` : `From ${formatMonth(p.start_month)}`;
}

const PHASE_COLUMNS: ChartColumn<PlanPhase>[] = [
  { header: "Phase", cell: (p) => p.label },
  { header: "Months", cell: phaseSpan },
  { header: "Surplus a month", cell: (p) => formatMoney(p.surplus_cents), numeric: true },
  { header: "Why", cell: (p) => [p.reason, p.note].filter(Boolean).join(". ") },
];

/** The cash flow relief timeline: each phase as a step, its height the monthly left over. */
function ReliefCard({ phases, loading }: { phases: PlanPhase[] | undefined; loading: boolean }) {
  const list = phases ?? [];
  const max = Math.max(1, ...list.map((p) => p.surplus_cents));
  const last = list[list.length - 1];
  return (
    <ChartCard<PlanPhase>
      eyebrow="Cash flow relief"
      title="Surplus by phase"
      subtitle="The chat's monthly surplus after its buffers, as plans and loans end. It is smaller than the plan's left over above."
      ariaLabel={`Cash flow relief timeline. ${list.map((p) => `${p.label}, ${phaseSpan(p)}, ${formatMoney(p.surplus_cents)} a month`).join(". ")}.`}
      data={phases}
      columns={PHASE_COLUMNS}
      rowKey={(p) => p.key}
      tableCaption="Cash flow phases"
      loading={loading}
      emptyText="No phases from the chat yet."
      height={160}
      footer={
        <ol className="m-0 mt-4 flex list-none flex-col gap-3 border-t border-line p-0 pt-4">
          {list.map((p) => (
            <li key={p.key} className="text-sm">
              <p className="m-0 flex flex-wrap items-baseline justify-between gap-x-3">
                <span className="text-fg">
                  {p.label}
                  {p.current && <Pill>now</Pill>}
                </span>
                <span className="text-fg">
                  <Money cents={p.surplus_cents} /> <span className="text-fg-secondary">a month</span>
                </span>
              </p>
              <p className="m-0 text-fg-secondary">
                {phaseSpan(p)}
                {p.reason ? `. ${p.reason}` : ""}
                {p.note ? `. ${p.note}` : ""}
              </p>
            </li>
          ))}
        </ol>
      }
    >
      <div className="flex h-40 items-end gap-0.5">
        {list.map((p) => (
          <div key={p.key} className="flex h-full min-w-0 flex-col justify-end" style={{ flexGrow: phaseMonths(p), flexBasis: 0 }}>
            <p className="m-0 truncate text-xs text-fg-secondary">
              <Money cents={p.surplus_cents} className="text-fg" />
            </p>
            <div
              aria-hidden="true"
              className={cn("mt-1 rounded-t", !p.end_month && "rounded-tr-none")}
              style={{
                height: `${Math.max((Math.max(0, p.surplus_cents) / max) * 100, 2)}px`,
                background: p.current ? LEFTOVER : `color-mix(in oklab, ${LEFTOVER} 55%, var(--bg-card))`,
              }}
            />
            <p className="m-0 mt-1 truncate text-xs text-fg-secondary">{formatMonth(p.start_month)}</p>
          </div>
        ))}
      </div>
      {last && !last.end_month && (
        <p className="m-0 mt-2 text-xs text-fg-secondary">
          The last phase has no end date. Shown through {formatMonth(addMonths(last.start_month, 5))}.
        </p>
      )}
    </ChartCard>
  );
}
