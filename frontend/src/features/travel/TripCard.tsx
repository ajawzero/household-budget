import { Check } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { Money } from "../../components/money/Money";
import { Badge } from "../../components/ui/badge";
import { CollapsibleGroup } from "../../components/ui/collapsible-group";
import type { PlanSummary, Trip, TripItem } from "../../lib/api";
import { cn } from "../../lib/cn";
import { chicagoToday, formatDate, formatMoney, formatRunDate } from "../../lib/format";
import {
  dueText,
  excludedItems,
  IOU_STATUS_TEXT,
  iouText,
  isDone,
  isPastDue,
  itemAmountText,
  itemDetail,
  pointsText,
  rangeText,
  remainingText,
  STATUS_TEXT,
  STATUS_TONE,
  taggedNotes,
  TENDER_TEXT,
  tripDates,
  tripTransactionsHref,
  versusText,
  whoText,
} from "./model";
import { planHref } from "./plans/model";

/**
 * One trip as a Monarch group card: chevron, name, total on the right,
 * collapsible. Open trips start open; completed ones start closed and dimmed.
 * Excluded items sit under "Excluded, verify" and never count. A linked trip
 * plan shows its estimate ("plan est.") next to the actual cost. Format 1.4: a
 * booked item with a due date says "Due <date>" ("Was due" once past, before
 * `today`, the household day), and a maybe item is muted with a chip.
 */
export function TripCard({ trip, plan, defaultOpen, today = chicagoToday() }: { trip: Trip; plan?: PlanSummary; defaultOpen?: boolean; today?: string }) {
  const done = isDone(trip.status);
  const counted = trip.items.filter((i) => i.status !== "excluded");
  const excluded = excludedItems(trip);
  const notes = taggedNotes(trip);
  const t = trip.totals;
  return (
    <div id={`trip-${trip.key}`} className="scroll-mt-4">
      <CollapsibleGroup
        defaultOpen={defaultOpen ?? !done}
        title={
          <span className={cn("block", done && "text-fg-secondary")}>
            {trip.name}
            <span className="block text-sm font-normal text-fg-secondary">{tripDates(trip)}</span>
          </span>
        }
        summary={
          <>
            <span className="hidden sm:inline-flex">
              <Badge tone={STATUS_TONE[trip.status]}>
                {trip.status === "final" && <Check size={12} aria-hidden="true" />}
                {STATUS_TEXT[trip.status]}
              </Badge>
            </span>
            <span className="text-right">
              <span className="block text-base font-medium text-fg">
                <Money cents={t.cash_cents} />
              </span>
              <span className="block text-xs text-fg-secondary">{done ? "total actual" : "cash so far"}</span>
            </span>
          </>
        }
      >
        <div className="flex flex-col gap-4 px-5 py-4 md:px-6">
          <dl className="m-0 grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
            <Meta label="Status">
              <span className="sm:hidden">
                <Badge tone={STATUS_TONE[trip.status]}>{STATUS_TEXT[trip.status]}</Badge>
              </span>
              <span className="hidden sm:inline">{STATUS_TEXT[trip.status]}</span>
            </Meta>
            <Meta label="Who">{whoText(trip)}</Meta>
            {trip.vehicle && <Meta label="Vehicle">{trip.vehicle}</Meta>}
          </dl>

          {counted.length > 0 ? (
            <ul aria-label={`${trip.name} items`} className="m-0 list-none p-0">
              {counted.map((i) => (
                <ItemRow key={i.key} item={i} today={today} />
              ))}
            </ul>
          ) : (
            <p className="m-0 text-sm text-fg-secondary">No items yet.</p>
          )}

          <dl className="m-0 grid grid-cols-2 gap-3 rounded-lg bg-subtle p-3 text-sm md:grid-cols-4">
            <Total label={done ? "Total actual" : "Cash so far"}>{formatMoney(t.cash_cents)}</Total>
            <Total label="Estimated remaining">{remainingText(t.estimate_low_cents, t.estimate_high_cents, t.tbd_count)}</Total>
            <Total label="Our net">{formatMoney(t.our_net_cents)}</Total>
            <Total label="Points used">{pointsText(t.points)}</Total>
          </dl>
          {plan && plan.low_cents !== null && plan.high_cents !== null && (
            <p className="m-0 -mt-2 text-sm text-fg-secondary">
              <Link to={planHref(plan.plan_id)} className="inline-flex min-h-11 items-center md:min-h-0">
                plan est. {rangeText(plan.low_cents, plan.high_cents)}
              </Link>
            </p>
          )}
          {(t.owed_to_us_cents > 0 || t.we_owe_cents > 0) && (
            <p className="m-0 -mt-2 text-xs text-fg-secondary">
              Our net is cash so far
              {t.owed_to_us_cents > 0 && `, less ${formatMoney(t.owed_to_us_cents)} owed to us`}
              {t.we_owe_cents > 0 && `, plus ${formatMoney(t.we_owe_cents)} we owe`}.
            </p>
          )}
          {trip.items.some((i) => i.maybe) && <p className="m-0 -mt-2 text-xs text-fg-secondary">Maybe items are not in the totals.</p>}

          {done && (
            <p className="m-0 text-sm text-fg-secondary">
              {trip.estimate_low_cents !== null && trip.estimate_high_cents !== null && (
                <>est. was {rangeText(trip.estimate_low_cents, trip.estimate_high_cents)}</>
              )}
              {versusText(trip.versus_estimate) && <> · {versusText(trip.versus_estimate)}</>}
              {trip.status === "final" && <> · final ✓</>}
            </p>
          )}

          {trip.ious.length > 0 && (
            <div>
              <h3 className="m-0 mb-1 text-sm font-medium text-fg">Who owes what</h3>
              <ul className="m-0 list-none p-0 text-sm">
                {trip.ious.map((o) => (
                  <li key={o.key} className="flex flex-wrap items-baseline justify-between gap-x-3 border-t border-line py-2 first:border-t-0">
                    <span className={cn("min-w-0", o.status === "waived" ? "text-fg-secondary" : "text-fg")}>
                      {iouText(o)}
                      {o.item_label && <span className="text-fg-secondary"> · {o.item_label}</span>}
                    </span>
                    <span className="text-xs text-fg-secondary">
                      {IOU_STATUS_TEXT[o.status]}
                      {o.settled_on && ` ${formatDate(o.settled_on)}`}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {excluded.length > 0 && (
            <div>
              <h3 className="m-0 mb-1 text-sm font-medium text-fg">Excluded, verify</h3>
              <p className="m-0 mb-1 text-xs text-fg-secondary">Not counted in any total.</p>
              <ul className="m-0 list-none p-0">
                {excluded.map((i) => (
                  <ItemRow key={i.key} item={i} today={today} muted />
                ))}
              </ul>
            </div>
          )}

          {trip.note && <p className="m-0 text-sm text-fg-secondary">{trip.note}</p>}

          <div className="flex flex-col gap-1 text-sm">
            <Link to={tripTransactionsHref(trip.key)} className="inline-flex min-h-11 items-center font-medium md:min-h-0">
              Transactions for this trip ({trip.tagged.row_count})
            </Link>
            {notes.map((n) => (
              <p key={n} className="m-0 text-warn">
                {n}
              </p>
            ))}
            <p className="m-0 text-xs text-fg-secondary">From the {formatRunDate(trip.source_run_id)} update.</p>
          </div>
        </div>
      </CollapsibleGroup>
    </div>
  );
}

function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-2 sm:block">
      <dt className="text-fg-secondary">{label}</dt>
      <dd className="m-0 text-fg">{children}</dd>
    </div>
  );
}

function Total({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-fg-secondary">{label}</dt>
      <dd className="m-0 break-words font-medium text-fg">{children}</dd>
    </div>
  );
}

/** One item on two lines: ✓ when posted, label and tender chip, amount on the right; then points, card, person, note.
 * A booked item with a due date shows "Due <date>" (warn once past) in place of "Booked"; the card is on the line under it.
 * A maybe item (1.4) is muted with a Maybe chip in place of "Estimate". */
function ItemRow({ item, today, muted = false }: { item: TripItem; today: string; muted?: boolean }) {
  const detail = itemDetail(item);
  const posted = item.status === "posted";
  const quiet = muted || item.maybe || item.tender === "covered";
  const due = item.status === "booked" ? dueText(item, today, false) : null;
  return (
    <li className="flex items-start gap-2 border-t border-line py-2 first:border-t-0">
      <span className="mt-0.5 w-4 shrink-0 text-good">
        {posted && <Check size={16} aria-label="Posted" />}
      </span>
      <div className="min-w-0 flex-1">
        <p className="m-0 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
          <span className={cn("min-w-0", quiet ? "text-fg-secondary" : "text-fg")}>{item.label}</span>
          {item.tender !== "cash" && <Badge tone={item.tender === "covered" ? "neutral" : "accent"}>{TENDER_TEXT[item.tender]}</Badge>}
          {item.maybe && <Badge>Maybe</Badge>}
          {due ? (
            <span className={cn("text-xs", isPastDue(item, today) ? "text-warn" : "text-fg-secondary")}>{due}</span>
          ) : (
            item.status === "booked" && <span className="text-xs text-fg-secondary">Booked</span>
          )}
          {item.status === "estimate" && !item.maybe && <span className="text-xs text-fg-secondary">Estimate</span>}
        </p>
        {detail && <p className="m-0 text-xs text-fg-secondary">{detail}</p>}
      </div>
      <span className={cn("shrink-0 text-right text-sm tabular", item.status === "tbd" && !item.maybe ? "text-warn" : "text-fg", quiet && "text-fg-secondary")}>
        {itemAmountText(item)}
      </span>
    </li>
  );
}
