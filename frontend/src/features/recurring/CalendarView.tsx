import { useState } from "react";
import { Money } from "../../components/money/Money";
import { Card, CardHeader } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import type { RecurringCalendar, RecurringDueState, RecurringItem } from "../../lib/api";
import { cn } from "../../lib/cn";
import { formatDate, formatMoney, formatMonth } from "../../lib/format";
import { ItemBadges, ItemNote, StateBadge, StateIcon } from "./DueList";
import { dueKey, STATE_ORDER, STATE_TEXT, stateCountText } from "./model";
import { ListSkeleton } from "./parts";

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** Icon colors per due state. Each state also has its own icon shape, its word in the legend and the day list. */
export const STATE_FILL: Record<RecurringDueState, string> = {
  paid: "var(--fill-good)",
  upcoming: "var(--chart-muted)",
  waiting: "var(--fill-warn)",
  no_history: "var(--chart-muted)",
  missed: "var(--fill-bad)",
};

function isoDay(month: string, day: number): string {
  return `${month}-${String(day).padStart(2, "0")}`;
}

/**
 * Month grid of due dates. Each day with bills is a button; the day's bills
 * show in a list under the grid, so phones get the details too.
 */
export function CalendarView({
  data,
  loading,
  items,
}: {
  data: RecurringCalendar | undefined;
  loading: boolean;
  items: Map<string, RecurringItem>;
}) {
  const [picked, setPicked] = useState<string | null>(null);
  if (!data) {
    return <Card>{loading ? <ListSkeleton rows={6} /> : <EmptyState title="No calendar for this month." />}</Card>;
  }
  const byDate = new Map(data.days.map((d) => [d.date, d]));
  const inMonth = data.today.startsWith(data.month);
  const firstDue = data.days[0]?.date ?? null;
  const selected = picked && picked.startsWith(data.month) ? picked : inMonth && byDate.has(data.today) ? data.today : firstDue;
  const day = selected ? byDate.get(selected) : undefined;
  const cells: (number | null)[] = [
    ...Array.from({ length: data.first_weekday }, () => null),
    ...Array.from({ length: data.days_in_month }, (_, i) => i + 1),
  ];
  while (cells.length % 7 !== 0) cells.push(null);

  return (
    <Card>
      <CardHeader title={formatMonth(data.month)} subtitle="Pick a day to see what is due." />
      {/* Phones: the grid runs to the card edges, so each day is wide enough to tap (44px). */}
      <div
        role="group"
        aria-label={`Bills due in ${formatMonth(data.month)}`}
        className="-mx-5 grid grid-cols-7 gap-px overflow-hidden border-y border-line bg-line md:mx-0 md:rounded-lg md:border-x"
      >
        <div aria-hidden="true" className="contents">
          {WEEKDAYS.map((w) => (
            <div key={w} className="bg-subtle py-1 text-center text-xs font-medium uppercase text-fg-secondary">
              {w}
            </div>
          ))}
        </div>
        {Array.from({ length: cells.length / 7 }, (_, week) => (
          <div className="contents" key={week}>
            {cells.slice(week * 7, week * 7 + 7).map((n, i) => {
              if (n === null) return <div key={`b${week}-${i}`} aria-hidden="true" className="min-h-14 bg-card md:min-h-24" />;
              const iso = isoDay(data.month, n);
              const d = byDate.get(iso);
              const isToday = iso === data.today;
              const label = d
                ? `${formatDate(iso)}: ${d.items.length} due, ${formatMoney(d.expected_cents)}. ${stateCountText(d.items)}.`
                : `${formatDate(iso)}: nothing due`;
              return (
                <div key={iso} className="bg-card">
                  <button
                    type="button"
                    aria-label={label}
                    aria-pressed={selected === iso}
                    disabled={!d}
                    onClick={() => setPicked(iso)}
                    className={cn(
                      "flex h-full min-h-14 w-full flex-col items-stretch gap-1 p-1 text-left md:min-h-24 md:p-2",
                      d ? "hover:bg-hover" : "cursor-default",
                      selected === iso && "bg-active ring-2 ring-accent ring-inset hover:bg-active",
                    )}
                  >
                    <span
                      className={cn(
                        "inline-flex h-6 w-6 items-center justify-center self-start rounded-full text-xs",
                        isToday ? "bg-primary font-semibold text-on-primary" : d ? "font-medium text-fg" : "text-fg-secondary",
                      )}
                    >
                      {n}
                    </span>
                    {d && (
                      <>
                        {/* Phones: one state icon per bill. */}
                        <span className="flex flex-wrap gap-0.5 md:hidden" aria-hidden="true">
                          {d.items.map((it) => (
                            <span key={dueKey(it)} className="inline-flex" style={{ color: STATE_FILL[it.state] }}>
                              <StateIcon state={it.state} size={10} />
                            </span>
                          ))}
                        </span>
                        {/* Wider: names, two at most. */}
                        <span className="hidden flex-col gap-0.5 md:flex" aria-hidden="true">
                          {d.items.slice(0, 2).map((it) => (
                            <span key={dueKey(it)} className="flex items-center gap-1 text-xs text-fg">
                              <span className="inline-flex shrink-0" style={{ color: STATE_FILL[it.state] }}>
                                <StateIcon state={it.state} size={10} />
                              </span>
                              <span className="truncate">{it.name}</span>
                            </span>
                          ))}
                          {d.items.length > 2 && <span className="text-xs text-fg-secondary">{d.items.length - 2} more</span>}
                        </span>
                      </>
                    )}
                  </button>
                </div>
              );
            })}
          </div>
        ))}
      </div>
      <ul className="m-0 mt-3 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-sm text-fg-secondary" aria-label="Bill states">
        {STATE_ORDER.map((s) => (
          <li key={s} className="flex items-center gap-1.5">
            <span className="inline-flex" style={{ color: STATE_FILL[s] }}>
              <StateIcon state={s} size={12} />
            </span>
            {STATE_TEXT[s]}
          </li>
        ))}
      </ul>
      <section aria-label={selected ? `Due ${formatDate(selected)}` : "Due"} className="mt-4 border-t border-line pt-4">
        {day ? (
          <>
            <h3 className="m-0 mb-2 flex items-baseline justify-between text-sm font-medium text-fg">
              {formatDate(day.date)}
              <span className="text-fg-secondary">
                <Money cents={day.expected_cents} withCents /> due
              </span>
            </h3>
            <ul className="m-0 flex list-none flex-col gap-2 p-0">
              {day.items.map((it) => (
                <li key={dueKey(it)} className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="m-0 truncate font-medium text-fg">{it.name}</p>
                    <p className="m-0 truncate text-sm text-fg-secondary">
                      {it.category_emoji && <span aria-hidden="true">{it.category_emoji} </span>}
                      {it.category_name ?? it.category_key}
                      {it.account_name && ` · ${it.account_name}`}
                    </p>
                    <ItemNote item={items.get(it.key)} />
                    <div className="mt-1 flex flex-wrap gap-1 empty:hidden">
                      {/* On phones the state sits here, so the name keeps the width (as in the list). */}
                      <span className="sm:hidden">
                        <StateBadge state={it.state} />
                      </span>
                      <ItemBadges item={items.get(it.key)} status={it.status} />
                    </div>
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1">
                    <Money cents={it.state === "paid" && it.paid_cents !== null ? it.paid_cents : it.expected_cents} withCents />
                    <span className="hidden sm:inline-flex">
                      <StateBadge state={it.state} />
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="m-0 text-sm text-fg-secondary">Nothing is due this month.</p>
        )}
      </section>
    </Card>
  );
}
