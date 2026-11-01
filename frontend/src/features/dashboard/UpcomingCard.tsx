import { Link } from "react-router";
import { Money } from "../../components/money/Money";
import { Badge } from "../../components/ui/badge";
import type { CreditsEnding, DashboardUpcoming, NextTrip, UtilitySummary } from "../../lib/api";
import { cn } from "../../lib/cn";
import { chicagoToday, formatDate, formatMoney, formatMonth, formatShortDate } from "../../lib/format";
import { weekday } from "../recurring/model";
import { travelHref } from "../travel/model";
import { DashCard, type LoadState } from "./parts";

/** How many rows the card shows before "See all". */
export const UPCOMING_LIMIT = 8;

export interface UpcomingDay {
  date: string;
  totalCents: number;
  items: DashboardUpcoming[];
}

/** Items grouped by date, in date order, each day with its total. */
export function upcomingDays(items: DashboardUpcoming[]): UpcomingDay[] {
  const days: UpcomingDay[] = [];
  for (const it of [...items].sort((a, b) => a.date.localeCompare(b.date))) {
    const last = days[days.length - 1];
    if (last && last.date === it.date) {
      last.items.push(it);
      last.totalCents += it.amount_cents;
    } else {
      days.push({ date: it.date, totalCents: it.amount_cents, items: [it] });
    }
  }
  return days;
}

/**
 * "Next bill expected Oct 6 · 12-month average $137" for one utility. Bills
 * come with the monthly imports, so a passed day alone is not late: "Next
 * bill expected Oct 6, with the next import". The server marks it late once
 * an import well after that day still has no bill: "Next bill is late,
 * expected Oct 6".
 */
export function utilityLineText(u: UtilitySummary, today: string = chicagoToday()): string {
  const parts: string[] = [];
  if (u.next_bill_date) {
    const day = formatShortDate(u.next_bill_date);
    if (u.next_bill_late) parts.push(`Next bill is late, expected ${day}`);
    else if (u.next_bill_date < today) parts.push(`Next bill expected ${day}, with the next import`);
    else parts.push(`Next bill expected ${day}`);
  }
  if (u.avg_bill_cents !== null) parts.push(`12-month average ${formatMoney(u.avg_bill_cents)}`);
  return parts.join(" · ");
}

/** One line per utility under the bills: next bill expected and the 12-month average bill. Each opens Cash Flow > Utilities. */
function UtilityLines({ utilities }: { utilities: UtilitySummary[] }) {
  const today = chicagoToday();
  return (
    <section aria-label="Utilities" className="-mx-5 md:-mx-6">
      <h3 className="m-0 bg-subtle px-5 py-1.5 text-sm font-medium text-fg-secondary md:px-6">Utilities</h3>
      <ul className="m-0 list-none p-0">
        {utilities.map((u) => (
          <li key={u.utility_key}>
            <Link
              to={`/cash-flow?tab=utilities&utility=${encodeURIComponent(u.utility_key)}`}
              className="block px-5 py-2.5 no-underline hover:bg-hover md:px-6"
            >
              <span className="block truncate text-sm font-medium text-fg">{u.provider}</span>
              <span className={cn("block text-sm", u.next_bill_late ? "text-warn" : "text-fg-secondary")}>{utilityLineText(u, today)}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** "2 credits end soon": card credits not used yet that end within 14 days. Opens Rewards, Benefits. */
export function creditsEndingText(c: CreditsEnding): string {
  return `${c.count} ${c.count === 1 ? "credit ends" : "credits end"} soon`;
}

/** "$35.00 not used yet, the first ends Oct 31", or "... ends Oct 31" for one credit. */
export function creditsEndingDetail(c: CreditsEnding): string {
  const left = formatMoney(c.left_cents, { cents: true });
  return `${left} not used yet, ${c.count === 1 ? "ends" : "the first ends"} ${formatShortDate(c.first_end)}`;
}

function CreditsLine({ credits }: { credits: CreditsEnding }) {
  return (
    <section aria-label="Card credits" className="-mx-5 md:-mx-6">
      <h3 className="m-0 bg-subtle px-5 py-1.5 text-sm font-medium text-fg-secondary md:px-6">Card credits</h3>
      <Link to="/rewards?tab=benefits" className="block px-5 py-2.5 no-underline hover:bg-hover md:px-6">
        <span className="block text-sm font-medium text-warn">{creditsEndingText(credits)}</span>
        <span className="block text-sm text-fg-secondary">{creditsEndingDetail(credits)}</span>
      </Link>
    </section>
  );
}

/** "Ski weekend · Jan 15, 2027", or the month alone when the trip has no dates yet. */
export function nextTripText(t: NextTrip): string {
  return `${t.name} · ${t.start_date ? formatDate(t.start_date) : formatMonth(t.month)}`;
}

/** "$451 so far · $300 to $420 still to spend + 1 TBD". */
export function nextTripDetail(t: NextTrip): string {
  const parts = [`${formatMoney(t.cash_cents)} so far`];
  const low = t.estimate_low_cents;
  const high = t.estimate_high_cents;
  if (low > 0 || high > 0) parts.push(`${low === high ? formatMoney(low) : `${formatMoney(low)} to ${formatMoney(high)}`} still to spend`);
  if (t.tbd_count > 0) parts.push(`${t.tbd_count} TBD`);
  return parts.join(" · ");
}

/** One upcoming row's content: the name with its badge, a second line, the amount on the right. */
function UpcomingRow({ item, detail, badge }: { item: DashboardUpcoming; detail: string | null; badge: string | null }) {
  return (
    <>
      <div className="min-w-0">
        <p className="m-0 flex items-center gap-2 text-sm font-medium text-fg">
          <span className="truncate">{item.name}</span>
          {badge && <Badge>{badge}</Badge>}
        </p>
        {detail && <p className="m-0 truncate text-sm text-fg-secondary">{detail}</p>}
      </div>
      <span className="shrink-0 text-sm text-fg">
        <Money cents={item.amount_cents} withCents />
      </span>
    </>
  );
}

function TripLine({ trip }: { trip: NextTrip }) {
  return (
    <section aria-label="Next trip" className="-mx-5 md:-mx-6">
      <h3 className="m-0 bg-subtle px-5 py-1.5 text-sm font-medium text-fg-secondary md:px-6">Next trip</h3>
      <Link to={`/travel?trip=${encodeURIComponent(trip.key)}`} className="block px-5 py-2.5 no-underline hover:bg-hover md:px-6">
        <span className="block truncate text-sm font-medium text-fg">{nextTripText(trip)}</span>
        <span className="block text-sm text-fg-secondary">{nextTripDetail(trip)}</span>
      </Link>
    </section>
  );
}

/** Bills, plan payments and trip payments (format 1.4) due in the next 30 days, in date
 * bands like a transaction list. A trip payment links to its trip. Under them, one line
 * per utility with its next bill and 12-month average. */
export function UpcomingCard({
  items,
  utilities = [],
  creditsEnding = null,
  nextTrip = null,
  state,
}: {
  items: DashboardUpcoming[] | undefined;
  utilities?: UtilitySummary[];
  creditsEnding?: CreditsEnding | null;
  nextTrip?: NextTrip | null;
  state: LoadState;
}) {
  const all = items ?? [];
  const total = all.reduce((sum, i) => sum + i.amount_cents, 0);
  // Day totals count every bill that day, even ones cut by the limit.
  let left = UPCOMING_LIMIT;
  const days = upcomingDays(all).flatMap((d) => {
    if (left <= 0) return [];
    const shown = d.items.slice(0, left);
    left -= shown.length;
    return [{ ...d, items: shown }];
  });
  const more = all.length - UPCOMING_LIMIT;
  return (
    <DashCard
      title="Upcoming bills"
      subtitle={all.length ? `${all.length} due in the next 30 days, ${formatMoney(total)} in all` : "Bills, plan and trip payments in the next 30 days"}
      state={state}
      empty={all.length === 0 && utilities.length === 0 && !creditsEnding && !nextTrip}
      emptyText="Nothing due in the next 30 days."
      footer={
        <Link to="/recurring" className="mt-3 inline-flex min-h-11 items-center text-sm font-medium md:min-h-0">
          {more > 0 ? `See ${more} more on Recurring` : "See all on Recurring"}
        </Link>
      }
    >
      <div className="-mx-5 md:-mx-6">
        {days.map((d) => (
          <section key={d.date} aria-label={`Due ${formatShortDate(d.date)}`}>
            <h3 className="m-0 flex items-baseline justify-between bg-subtle px-5 py-1.5 text-sm font-medium text-fg-secondary md:px-6">
              <span>
                {weekday(d.date)}, {formatShortDate(d.date)}
              </span>
              <Money cents={d.totalCents} withCents />
            </h3>
            <ul className="m-0 list-none p-0">
              {d.items.map((it) =>
                it.kind === "trip" && it.trip_key ? (
                  // Trip item keys repeat across trips, so the key has the trip too.
                  <li key={`${it.kind}-${it.trip_key}-${it.key}-${it.date}`}>
                    <Link to={travelHref(it.trip_key)} className="flex items-center justify-between gap-3 px-5 py-2.5 no-underline hover:bg-hover md:px-6">
                      <UpcomingRow item={it} detail={[it.trip_name, it.account_name].filter(Boolean).join(" · ")} badge="Trip payment" />
                    </Link>
                  </li>
                ) : (
                  <li key={`${it.kind}-${it.trip_key ?? ""}-${it.key}-${it.date}`} className="flex items-center justify-between gap-3 px-5 py-2.5 md:px-6">
                    <UpcomingRow item={it} detail={it.account_name} badge={it.kind === "plan" ? "Plan payment" : null} />
                  </li>
                ),
              )}
            </ul>
          </section>
        ))}
        {all.length === 0 && <p className="m-0 px-5 pb-3 text-sm text-fg-secondary md:px-6">Nothing due in the next 30 days.</p>}
      </div>
      {utilities.length > 0 && <UtilityLines utilities={utilities} />}
      {creditsEnding && <CreditsLine credits={creditsEnding} />}
      {nextTrip && <TripLine trip={nextTrip} />}
    </DashCard>
  );
}
