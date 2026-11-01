import { ChevronDown } from "lucide-react";
import { Link } from "react-router";
import { Money } from "../../components/money/Money";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardHeader } from "../../components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";
import { EmptyState } from "../../components/ui/empty-state";
import { TableScroll } from "../../components/ui/table-scroll";
import type { RecurringItem } from "../../lib/api";
import { cn } from "../../lib/cn";
import { chicagoToday, formatMoney, formatMonth, formatShortDate, formatDate } from "../../lib/format";
import { ItemBadges, ItemNote } from "./DueList";
import {
  billSections,
  cadenceText,
  cancelledRows,
  endingRows,
  lastPaidText,
  railText,
  savingsCard,
  seasonMonthlyText,
  seasons,
  keptText,
  seasonTotalText,
  sectionName,
  sectionSubtitle,
  subscriptionRows,
  subscriptionTotalText,
  type BillSection,
  type PersonOption,
  type RecurringSection,
} from "./model";
import { ListSkeleton } from "./parts";

const EVERYONE = "";

/** "Everyone" or one person. The All tab shows it only when items have two or more owners. */
export function PersonPicker({
  options,
  value,
  onChange,
}: {
  options: PersonOption[];
  value: string | null;
  onChange: (person: string | null) => void;
}) {
  const label = options.find((o) => o.key === value)?.label ?? "Everyone";
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="sm" aria-label={`Person: ${label}`}>
          {label}
          <ChevronDown size={16} aria-hidden="true" className="text-fg-secondary" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuRadioGroup value={value ?? EVERYONE} onValueChange={(v) => onChange(v === EVERYONE ? null : v)}>
          <DropdownMenuRadioItem value={EVERYONE}>Everyone</DropdownMenuRadioItem>
          {options.map((o) => (
            <DropdownMenuRadioItem key={o.key} value={o.key}>
              {o.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function Name({ item }: { item: RecurringItem }) {
  return (
    <div className="min-w-0">
      <p className="m-0 truncate font-medium text-fg">{item.name}</p>
      <p className="m-0 truncate text-sm text-fg-secondary">
        {item.category_emoji && <span aria-hidden="true">{item.category_emoji} </span>}
        {item.category_name ?? item.category_key}
        {item.owner_label && ` · ${item.owner_label}`}
      </p>
      <ItemNote item={item} />
      <div className="mt-1 flex flex-wrap gap-1 empty:hidden">
        <ItemBadges item={item} status={item.status} />
        {!item.counted && <Badge title="Paused or ended items are left out of the totals.">Not counted</Badge>}
      </div>
    </div>
  );
}

function MonthlyEquivalent({ item }: { item: RecurringItem }) {
  const net = item.net_monthly_equivalent_cents !== item.monthly_equivalent_cents;
  return (
    <>
      <Money cents={item.monthly_equivalent_cents} withCents />
      {net && (
        <span className="block text-xs text-fg-secondary">
          {formatMoney(item.net_monthly_equivalent_cents, { cents: true })} after credits
        </span>
      )}
    </>
  );
}

function PaidFrom({ item }: { item: RecurringItem }) {
  return (
    <>
      {item.account_name ?? "Not set"}
      {item.payment_rail && <span className="block text-xs text-fg-secondary">{railText(item.payment_rail)}</span>}
    </>
  );
}

/** A section's items: a table from the tablet size up, a list on phones. */
function ItemRows({ items, label }: { items: RecurringItem[]; label: string }) {
  return (
    <>
      <TableScroll className="hidden md:block" label={label}>
        {/* compact: no 640px floor, so the table fits at 768px as before. */}
        <table className="data-table compact">
          <caption className="sr-only">{label}</caption>
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">How often</th>
              <th scope="col" className="num">
                Each time
              </th>
              <th scope="col" className="num">
                A month
              </th>
              <th scope="col">Next due</th>
              <th scope="col">Paid from</th>
            </tr>
          </thead>
          <tbody>
            {items.map((i) => {
              const lastPaid = lastPaidText(i);
              return (
                <tr key={i.key} className={cn(!i.counted && "text-fg-secondary")}>
                  <th scope="row" className="font-normal">
                    <Name item={i} />
                  </th>
                  <td>{cadenceText(i.cadence, i.interval_n)}</td>
                  <td className="num">
                    <Money cents={i.amount_cents} withCents />
                    {i.credit_offset_cents ? (
                      <span className="block text-xs text-fg-secondary">
                        {formatMoney(i.credit_offset_cents, { cents: true })} credit
                        {i.credit_note && <span className="block whitespace-normal">{i.credit_note}</span>}
                      </span>
                    ) : null}
                  </td>
                  <td className="num">
                    <MonthlyEquivalent item={i} />
                  </td>
                  <td className="whitespace-nowrap">
                    {i.next_due ? formatDate(i.next_due) : "None"}
                    {lastPaid && <span className="block whitespace-normal text-xs text-fg-secondary">{lastPaid}</span>}
                  </td>
                  <td>
                    <PaidFrom item={i} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableScroll>
      <ul className="m-0 flex list-none flex-col p-0 md:hidden" aria-label={label}>
        {items.map((i) => {
          const lastPaid = lastPaidText(i);
          return (
            <li key={i.key} className={cn("flex items-start gap-3 border-t border-line py-3 first:border-t-0", !i.counted && "opacity-80")}>
              <div className="min-w-0 flex-1">
                <Name item={i} />
                <p className="m-0 mt-1 text-sm text-fg-secondary">
                  {cadenceText(i.cadence, i.interval_n)} · {formatMoney(i.amount_cents, { cents: true })} each
                  {i.next_due && ` · next ${formatShortDate(i.next_due)}`}
                </p>
                {lastPaid && <p className="m-0 text-xs text-fg-secondary">{lastPaid}</p>}
                <p className="m-0 text-xs text-fg-secondary">
                  {i.account_name ?? "Account not set"}
                  {i.payment_rail && ` · ${railText(i.payment_rail)}`}
                </p>
                {i.credit_offset_cents ? (
                  <p className="m-0 text-xs text-fg-secondary">
                    {formatMoney(i.credit_offset_cents, { cents: true })} credit{i.credit_note ? ` · ${i.credit_note}` : ""}
                  </p>
                ) : null}
              </div>
              <div className="shrink-0 text-right">
                <MonthlyEquivalent item={i} />
                <span className="block text-xs text-fg-secondary">a month</span>
              </div>
            </li>
          );
        })}
      </ul>
    </>
  );
}

/** A tag card names its plan group, so two Hobbies cards read apart: "In Kids and education · 1 bill · ...". */
function tagSubtitle(group: BillSection): string {
  const { kind, group_label } = group.section;
  return kind === "tag" && group_label ? `In ${group_label} · ${sectionSubtitle(group)}` : sectionSubtitle(group);
}

/**
 * One plan group's bills, or one tag's bills in a plan group, with its count,
 * monthly subtotal and, for seasonal bills, the season costs.
 */
function SectionCard({ group }: { group: BillSection }) {
  const s = seasons(group.items);
  const monthly = seasonMonthlyText(s);
  const name = sectionName(group.section);
  // Kids' activities, and their tag sections, also say what each season costs in all, like the old tab.
  const total = (group.section.group_key ?? group.section.key) === "kids" ? seasonTotalText(s) : null;
  return (
    <Card>
      <CardHeader title={group.section.label} subtitle={tagSubtitle(group)} />
      {(monthly || total) && (
        <div className="mb-3 flex flex-col gap-0.5 text-sm text-fg-secondary">
          {monthly && <p className="m-0">{monthly}</p>}
          {total && <p className="m-0">{total}</p>}
        </div>
      )}
      <ItemRows items={group.items} label={name} />
    </Card>
  );
}

/**
 * The bills, one card per plan group in the server's order, each followed by
 * a card per tag (format 1.4). Savings and transfers and ended items have
 * their own cards. Paused items stay, not counted.
 */
export function AllView({
  items,
  sections,
  loading,
  empty,
}: {
  /** The items to show (after the person filter). */
  items: RecurringItem[] | undefined;
  sections: RecurringSection[] | undefined;
  loading: boolean;
  /** Text when the items have no bills, like "No bills for Sam." */
  empty: string;
}) {
  if (!items || items.length === 0) {
    return (
      <Card>
        <CardHeader title="All recurring" />
        {!items ? (
          loading ? <ListSkeleton rows={6} /> : <EmptyState title="No recurring items." />
        ) : (
          <EmptyState title="No recurring items yet.">They come with the monthly run.</EmptyState>
        )}
      </Card>
    );
  }
  const groups = billSections(items, sections);
  if (groups.length === 0) {
    return (
      <Card>
        <EmptyState title={empty} />
      </Card>
    );
  }
  return (
    <>
      <p className="m-0 text-sm text-fg-secondary">Each bill as a monthly amount: amount × times a year ÷ 12, counting only the months it runs.</p>
      {groups.map((g) => (
        <SectionCard key={g.section.key} group={g} />
      ))}
    </>
  );
}

/** Net monthly cost on the right of a card row, with "a month" under it. */
function MonthlyCost({ cents }: { cents: number }) {
  return (
    <span className="shrink-0 text-right text-sm text-fg">
      <Money cents={cents} withCents />
      <span className="block text-xs text-fg-secondary">a month</span>
    </span>
  );
}

const ROW = "flex items-start justify-between gap-3 border-t border-line py-3 first:border-t-0 first:pt-0";
const FOOTER = "m-0 mt-3 border-t border-line pt-3 text-sm text-fg-secondary";

/**
 * Savings and transfers apart from the bills: where each one goes when the
 * paying rows say, and a subtotal for each place two or more go to.
 */
export function SavingsCard({ items, sections }: { items: RecurringItem[] | undefined; sections: RecurringSection[] | undefined }) {
  const section = sections?.find((s) => s.kind === "savings");
  const card = savingsCard(items ?? [], sections);
  if (!items || !section || card.rows.length === 0) return null;
  return (
    <Card>
      <CardHeader title={section.label} subtitle="Money set aside or moved each month. Not in the bills totals." />
      <ul className="m-0 flex list-none flex-col p-0" aria-label={section.label}>
        {card.rows.map(({ item, where }) => (
          <li key={item.key} className={cn(ROW, !item.counted && "text-fg-secondary opacity-80")}>
            <div className="min-w-0">
              <p className="m-0 truncate font-medium text-fg">{item.name}</p>
              {where && (
                <p className="m-0 truncate text-sm text-fg-secondary">
                  {where}
                  {item.payment_rail && ` · ${railText(item.payment_rail)}`}
                </p>
              )}
              <ItemNote item={item} />
              <p className="m-0 text-sm text-fg-secondary">
                {cadenceText(item.cadence, item.interval_n)} · {formatMoney(item.amount_cents, { cents: true })} each
                {item.next_due && ` · next ${formatShortDate(item.next_due)}`}
              </p>
              {lastPaidText(item) && <p className="m-0 text-xs text-fg-secondary">{lastPaidText(item)}</p>}
              <div className="mt-1 flex flex-wrap gap-1 empty:hidden">
                <ItemBadges item={item} status={item.status} />
                {!item.counted && <Badge title="Paused items are left out of the totals.">Not counted</Badge>}
              </div>
            </div>
            <MonthlyCost cents={item.net_monthly_equivalent_cents} />
          </li>
        ))}
      </ul>
      <div className={FOOTER}>
        {card.subtotals.map((t) => (
          <p key={t.key} className="m-0">
            To {t.name}: about {formatMoney(t.cents)} a month
          </p>
        ))}
        <p className={cn("m-0", card.subtotals.length > 0 && "mt-1 font-medium text-fg")}>Together about {formatMoney(card.total)} a month.</p>
      </div>
    </Card>
  );
}

/** Cancelled (ending) and ended items, with what each one saves a year. */
export function CancelledCard({ items, sections }: { items: RecurringItem[] | undefined; sections: RecurringSection[] | undefined }) {
  const rows = cancelledRows(items ?? [], sections, chicagoToday());
  const bills = rows.filter((r) => !r.transfer);
  const total = bills.reduce((sum, r) => sum + r.yearly, 0);
  return (
    <Card>
      <CardHeader title="Cancelled and ended" subtitle="What each one saves a year, after credits." />
      {!items ? (
        <ListSkeleton rows={2} />
      ) : rows.length === 0 ? (
        <p className="m-0 text-sm text-fg-secondary">Nothing cancelled or ended.</p>
      ) : (
        <>
          <ul className="m-0 flex list-none flex-col p-0" aria-label="Cancelled and ended">
            {rows.map(({ item, badge, when, yearly, from, transfer }) => (
              <li key={item.key} className={ROW}>
                <div className="min-w-0">
                  <p className="m-0 truncate font-medium text-fg">{item.name}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
                    <Badge>{badge}</Badge>
                    {when && <span className="text-sm text-fg-secondary">{when}</span>}
                  </div>
                </div>
                <span className="max-w-[45%] shrink-0 text-right text-sm text-fg">
                  {transfer ? `No longer moves about ${formatMoney(yearly)} a year` : `Saves about ${formatMoney(yearly)} a year`}
                  {from && <span className="block text-xs text-fg-secondary">{from}</span>}
                </span>
              </li>
            ))}
          </ul>
          {bills.length > 0 && <p className={FOOTER}>Together the bills save about {formatMoney(total)} a year.</p>}
        </>
      )}
    </Card>
  );
}

/**
 * Paid subscriptions and memberships (Monarch's subscription list), plus any
 * active bill the chat flagged in a cost cutting flag. A flag marks the row;
 * the card never says to cancel anything. Bills the household chose to keep
 * go last, with a Kept badge, and the footer says how much of the total they are.
 */
export function SubscriptionsCard({ items }: { items: RecurringItem[] | undefined }) {
  const rows = subscriptionRows(items ?? []);
  return (
    <Card>
      <CardHeader title="Subscriptions and memberships" subtitle="Paid subscriptions and memberships after credits, and any bill the chat flagged to cut." />
      {!items ? (
        <ListSkeleton rows={2} />
      ) : (
        <>
          {rows.length === 0 ? (
            <p className="m-0 text-sm text-fg-secondary">No paid subscriptions or memberships.</p>
          ) : (
            <>
              <ul className="m-0 flex list-none flex-col p-0" aria-label="Subscriptions and memberships">
                {rows.map(({ item, cut, kept }) => (
                  <li key={item.key} className={ROW}>
                    <div className="min-w-0">
                      <p className="m-0 truncate font-medium text-fg">{item.name}</p>
                      <p className="m-0 truncate text-sm text-fg-secondary">
                        {item.category_emoji && <span aria-hidden="true">{item.category_emoji} </span>}
                        {item.category_name ?? item.category_key}
                      </p>
                      {(cut || kept) && (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {cut && <Badge tone={cut.open ? "warn" : "neutral"}>{cut.open ? "Cost cutting" : "Watching"}</Badge>}
                          {kept && <Badge title="You chose to keep this bill.">{keptText(item)}</Badge>}
                        </div>
                      )}
                      {cut && <p className="m-0 mt-0.5 break-words text-sm text-fg-secondary">{cut.reason}</p>}
                    </div>
                    <MonthlyCost cents={item.net_monthly_equivalent_cents} />
                  </li>
                ))}
              </ul>
              <p className={FOOTER}>{subscriptionTotalText(rows)}</p>
            </>
          )}
          <Link to="/flags" className="mt-3 inline-flex min-h-11 items-center text-sm font-medium md:min-h-0">
            See all flags
          </Link>
        </>
      )}
    </Card>
  );
}

/** Counted bills with an end date, soonest first, and how much the monthly bills drop by the last one. */
export function EndingCard({ items, sections }: { items: RecurringItem[] | undefined; sections?: RecurringSection[] }) {
  const rows = endingRows(items ?? [], sections);
  const total = rows.reduce((sum, r) => sum + r.item.net_monthly_equivalent_cents, 0);
  const last = rows[rows.length - 1]?.item.ends_on;
  return (
    <Card>
      <CardHeader title="Bills that end" subtitle="Soonest first, with what each one costs a month." />
      {!items ? (
        <ListSkeleton rows={2} />
      ) : rows.length === 0 ? (
        <p className="m-0 text-sm text-fg-secondary">No bills with an end date.</p>
      ) : (
        <>
          <ul className="m-0 flex list-none flex-col p-0" aria-label="Bills that end">
            {rows.map(({ item, when }) => (
              <li key={item.key} className={ROW}>
                <div className="min-w-0">
                  <p className="m-0 truncate font-medium text-fg">{item.name}</p>
                  <p className="m-0 text-sm text-fg-secondary">{when}</p>
                </div>
                <MonthlyCost cents={item.net_monthly_equivalent_cents} />
              </li>
            ))}
          </ul>
          {last && (
            <p className={FOOTER}>
              Bills drop by about {formatMoney(total)} a month by {formatMonth(last.slice(0, 7))}.
            </p>
          )}
        </>
      )}
    </Card>
  );
}
