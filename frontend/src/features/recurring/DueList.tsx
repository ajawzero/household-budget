import { CircleAlert, CircleCheck, CircleDashed, CircleQuestionMark, Clock } from "lucide-react";
import { Money } from "../../components/money/Money";
import { Badge, type BadgeTone } from "../../components/ui/badge";
import { Card, CardHeader } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import type { RecurringDue, RecurringDueState, RecurringItem } from "../../lib/api";
import { cn } from "../../lib/cn";
import { formatDate, formatMoney, formatMonth, formatShortDate } from "../../lib/format";
import { costCut, dueKey, keptText, moveTo, reviewText, seasonText, STATE_HINT, STATE_TEXT, weekday } from "./model";
import { ChangeBadge, ListSkeleton } from "./parts";

const STATE_TONE: Record<RecurringDueState, BadgeTone> = {
  paid: "good",
  upcoming: "neutral",
  waiting: "warn",
  no_history: "neutral",
  missed: "bad",
};

/** One shape per due state, so a state never shows by color alone. */
export function StateIcon({ state, size = 12 }: { state: RecurringDueState; size?: number }) {
  const props = { size, "aria-hidden": true } as const;
  if (state === "paid") return <CircleCheck {...props} />;
  if (state === "missed") return <CircleAlert {...props} />;
  if (state === "waiting") return <Clock {...props} />;
  if (state === "no_history") return <CircleQuestionMark {...props} />;
  return <CircleDashed {...props} />;
}

export function StateBadge({ state }: { state: RecurringDueState }) {
  return (
    <Badge tone={STATE_TONE[state]} icon={<StateIcon state={state} />} title={STATE_HINT[state]}>
      {STATE_TEXT[state]}
    </Badge>
  );
}

/**
 * Badges from the item itself: new or a rate change since the update before,
 * a better card to pay from, season, cost cutting, end, kept or review, and
 * renewal dates, varies. A kept bill's badge carries its review date, so the
 * date never shows twice.
 */
export function ItemBadges({ item, status }: { item: RecurringItem | undefined; status: RecurringDue["status"] }) {
  const season = seasonText(item?.active_months);
  const cut = item ? costCut(item) : null;
  const review = item ? reviewText(item) : null;
  const kept = item ? keptText(item) : null;
  const move = item ? moveTo(item) : null;
  // The same date as "Bills that end": the earlier of end_date and active_through.
  const ends = item?.ends_on ?? item?.active_through ?? item?.end_date;
  return (
    <>
      <ChangeBadge change={item?.change} wasCents={item?.previous_monthly_equivalent_cents} />
      {move && (
        <Badge tone="warn" className="max-w-full" title={item?.account_name ? `Paid from ${item.account_name} now` : undefined}>
          <span className="min-w-0 truncate">Move to {move}</span>
        </Badge>
      )}
      {season && (
        <Badge tone="accent" title="Seasonal: only these months">
          {season}
        </Badge>
      )}
      {cut?.open && (
        <Badge tone="warn" title={cut.reason}>
          Cost cutting
        </Badge>
      )}
      {status === "active" && item?.ends_on && <Badge title={`Last date ${formatDate(item.ends_on)}`}>Ends {formatMonth(item.ends_on.slice(0, 7))}</Badge>}
      {status === "ending" && <Badge title={ends ? `Last payment by ${formatDate(ends)}` : undefined}>Ending{ends ? ` ${formatShortDate(ends)}` : ""}</Badge>}
      {status === "paused" && <Badge>Paused</Badge>}
      {kept ? <Badge title="You chose to keep this bill.">{kept}</Badge> : review && <Badge>{review}</Badge>}
      {item?.renews_on && <Badge>Renews {formatDate(item.renews_on)}</Badge>}
      {item?.variable && <Badge title="The amount changes each time. The expected amount is typical.">Varies</Badge>}
    </>
  );
}

/** The chat's note on an item, in full. Notes wrap and are never cut short. */
export function ItemNote({ item }: { item: RecurringItem | undefined }) {
  if (!item?.notes) return null;
  return <p className="m-0 mt-0.5 break-words text-sm text-fg-secondary">{item.notes}</p>;
}

function DueRow({ due, item }: { due: RecurringDue; item: RecurringItem | undefined }) {
  const paid = due.state === "paid" && due.paid_cents !== null;
  const shownCents = paid ? due.paid_cents! : due.expected_cents;
  const differs = paid && due.paid_cents !== due.expected_cents;
  return (
    <li className="flex items-start gap-3 border-t border-line py-3 first:border-t-0 md:items-center">
      <div className="w-12 shrink-0 text-center" aria-hidden="true">
        <p className="m-0 text-xs uppercase text-fg-secondary">{weekday(due.due_date)}</p>
        <p className="m-0 text-sm font-medium text-fg">{formatShortDate(due.due_date)}</p>
      </div>
      <div className="min-w-0 flex-1">
        <p className="m-0 line-clamp-2 break-words text-base font-medium text-fg">
          <span className="sr-only">Due {formatDate(due.due_date)}: </span>
          {due.name}
        </p>
        <p className="m-0 line-clamp-2 break-words text-sm text-fg-secondary">
          {due.category_emoji && <span aria-hidden="true">{due.category_emoji} </span>}
          {due.category_name ?? due.category_key}
          {due.account_name && ` · ${due.account_name}`}
        </p>
        <ItemNote item={item} />
        <div className="mt-1 flex flex-wrap gap-1 empty:hidden">
          {/* On phones the state sits here, so the name keeps the width. */}
          <span className="sm:hidden">
            <StateBadge state={due.state} />
          </span>
          <ItemBadges item={item} status={due.status} />
        </div>
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1 text-right">
        <span className="text-base text-fg">
          <Money cents={shownCents} withCents />
        </span>
        <span className="hidden sm:inline-flex">
          <StateBadge state={due.state} />
        </span>
        {paid && due.paid_date && <span className="text-xs text-fg-secondary">Paid {formatShortDate(due.paid_date)}</span>}
        {differs && <span className="text-xs text-fg-secondary">Expected {formatMoney(due.expected_cents, { cents: true })}</span>}
        {due.credit_offset_cents ? (
          <span className="text-xs text-fg-secondary">Credit back {formatMoney(due.credit_offset_cents, { cents: true })}</span>
        ) : null}
      </div>
    </li>
  );
}

interface DueCardProps {
  title: string;
  rows: RecurringDue[] | undefined;
  items: Map<string, RecurringItem>;
  loading: boolean;
  emptyText: string;
  /** Total of the rows, shown on the right of the header. */
  totalLabel?: string;
  /** Show the total in whole dollars, like the summary tiles beside it. */
  totalWholeDollars?: boolean;
}

/** "Upcoming" or "Complete": one row per due date, by date. */
export function DueCard({ title, rows, items, loading, emptyText, totalLabel, totalWholeDollars }: DueCardProps) {
  const total = (rows ?? []).reduce((sum, r) => sum + (r.state === "paid" && r.paid_cents !== null ? r.paid_cents : r.expected_cents), 0);
  return (
    <Card>
      <CardHeader
        title={
          <>
            {title}
            {rows && <span className="ml-2 text-base font-normal text-fg-secondary">{rows.length}</span>}
          </>
        }
        actions={
          rows && rows.length > 0 ? (
            <span className="text-sm text-fg-secondary">
              {totalLabel ?? "Total"} <Money cents={total} withCents={!totalWholeDollars} className="font-medium text-fg" />
            </span>
          ) : undefined
        }
      />
      {loading && !rows ? (
        <ListSkeleton />
      ) : !rows || rows.length === 0 ? (
        <EmptyState title={emptyText} />
      ) : (
        <ul className={cn("m-0 list-none p-0")} aria-label={title}>
          {rows.map((d) => (
            <DueRow key={dueKey(d)} due={d} item={items.get(d.key)} />
          ))}
        </ul>
      )}
    </Card>
  );
}
