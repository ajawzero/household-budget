// Pure helpers for the Recurring page. No React here, so tests stay simple.

import type { RecurringCadence, RecurringDue, RecurringDueState, RecurringItem, RecurringResponse } from "../../lib/api";
import { addMonths, formatDate, formatMoney, formatMonth, roundToDollars } from "../../lib/format";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export type RecurringTab = "monthly" | "all" | "plans";
export type RecurringLayout = "list" | "calendar";

export function parseTab(value: string | null): RecurringTab {
  return value === "all" || value === "plans" ? value : "monthly";
}

export function parseLayout(value: string | null): RecurringLayout {
  return value === "calendar" ? "calendar" : "list";
}

/** "2026-10" or undefined. Anything else is ignored, so a bad URL still loads this month. */
export function parseMonth(value: string | null): string | undefined {
  return value && /^\d{4}-(0[1-9]|1[0-2])$/.test(value) ? value : undefined;
}

/** "Thu" for "2026-10-01". Computed in UTC so the time zone cannot shift the day. */
export function weekday(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number) as [number, number, number];
  return WEEKDAYS[new Date(Date.UTC(y, m - 1, d)).getUTCDay()] ?? "";
}

const CADENCE: Record<RecurringCadence, string> = {
  weekly: "Weekly",
  biweekly: "Every 2 weeks",
  every_n_weeks: "Every few weeks",
  semi_monthly: "Twice a month",
  monthly: "Monthly",
  quarterly: "Quarterly",
  semester: "Each semester",
  semi_annual: "Twice a year",
  annual: "Yearly",
};

/** "Monthly", "Every 3 weeks", "Yearly". */
export function cadenceText(cadence: RecurringCadence, intervalN?: number | null): string {
  if (cadence === "every_n_weeks" && intervalN) return `Every ${intervalN} weeks`;
  return CADENCE[cadence];
}

/**
 * The months a seasonal item runs, in words. Null when it runs all year.
 * A run of months reads "Apr to Oct"; scattered months read "Mar and Aug".
 */
export function seasonText(activeMonths: number[] | null | undefined): string | null {
  if (!activeMonths || activeMonths.length === 0 || activeMonths.length >= 12) return null;
  const sorted = [...activeMonths].sort((a, b) => a - b);
  const names = sorted.map((m) => MONTHS[m - 1] ?? String(m));
  const breaks = sorted.flatMap((m, i) => (i > 0 && m !== sorted[i - 1]! + 1 ? [i] : []));
  if (breaks.length === 0 && sorted.length >= 3) return `${names[0]} to ${names[names.length - 1]}`;
  // One run across the new year, like a school year: Sep to May.
  const b = breaks[0]!;
  if (breaks.length === 1 && sorted[0] === 1 && sorted[sorted.length - 1] === 12 && sorted.length >= 3)
    return `${names[b]} to ${names[b - 1]}`;
  if (names.length === 1) return `${names[0]} only`;
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** Categories that are paid by choice (Monarch's subscription list). */
export const SUBSCRIPTION_CATEGORIES = new Set(["subscriptions", "memberships", "fitness"]);

/**
 * An active subscription, membership or gym that still costs money after
 * credits. A credit that pays it back in full makes it free, so it is left out.
 */
export function isPaidSubscription(item: RecurringItem): boolean {
  return item.status === "active" && SUBSCRIPTION_CATEGORIES.has(item.category_key) && item.net_monthly_equivalent_cents > 0;
}

export interface CostCut {
  key: string;
  /** The flag's title, as the chat wrote it. */
  reason: string;
  /** Open, or only being watched. */
  open: boolean;
}

/**
 * The chat's cost cutting flag on an active item, or null. Paused, ending and
 * ended items are already decided, so their flags do not show here.
 */
export function costCut(item: RecurringItem): CostCut | null {
  if (item.status !== "active" || !item.cost_cut) return null;
  return { key: item.cost_cut.key, reason: item.cost_cut.title, open: item.cost_cut.status === "open" };
}

/** "Review by Feb 1, 2027" for an active item with a review date, else null. */
export function reviewText(item: RecurringItem): string | null {
  if (item.status !== "active" || !item.review_by) return null;
  return `Review by ${formatDate(item.review_by)}`;
}

/** "Kept", or "Kept · Review by Feb 1, 2027" when an active kept bill has a review date. Null when not kept. */
export function keptText(item: RecurringItem): string | null {
  if (!item.keep) return null;
  const review = reviewText(item);
  return review ? `Kept · ${review}` : "Kept";
}

export interface SubscriptionRow {
  item: RecurringItem;
  cut: CostCut | null;
  /** The household chose to keep it (format 1.4). */
  kept: boolean;
}

/** Paid subscriptions and memberships, plus any active bill the chat flagged to cut. In list order, kept bills last. */
export function subscriptionRows(items: RecurringItem[]): SubscriptionRow[] {
  const rows = items.flatMap((item) => {
    const cut = costCut(item);
    return cut || isPaidSubscription(item) ? [{ item, cut, kept: item.keep === true }] : [];
  });
  return [...rows.filter((r) => !r.kept), ...rows.filter((r) => r.kept)];
}

/** "Together about $62 a month. $12 of it you chose to keep." with a kept row, else "Together they cost about $62 a month." */
export function subscriptionTotalText(rows: SubscriptionRow[]): string {
  const sum = (rs: SubscriptionRow[]) => rs.reduce((total, r) => total + r.item.net_monthly_equivalent_cents, 0);
  const kept = rows.filter((r) => r.kept);
  return kept.length
    ? `Together about ${formatMoney(sum(rows))} a month. ${formatMoney(sum(kept))} of it you chose to keep.`
    : `Together they cost about ${formatMoney(sum(rows))} a month.`;
}

export interface EndingRow {
  item: RecurringItem;
  /** "Ends Jun 1, 2049", or "Cancelled, runs through Dec 31, 2026" for an ending item. */
  when: string;
}

/** Counted bills with an end date, soonest first, then by name. */
export function endingRows(items: RecurringItem[], sections?: RecurringSection[]): EndingRow[] {
  const savings = savingsKeys(sections);
  return items
    .filter((i) => i.counted && i.ends_on && !savings.has(i.section_key))
    .sort((a, b) => a.ends_on!.localeCompare(b.ends_on!) || a.name.localeCompare(b.name))
    .map((item) => ({
      item,
      when: item.status === "ending" ? `Cancelled, runs through ${formatDate(item.ends_on)}` : `Ends ${formatDate(item.ends_on)}`,
    }));
}

const RAIL_TEXT: Record<string, string> = {
  card: "By card",
  ach: "By ACH",
  bill_pay: "By bill pay",
  check: "By check",
  cash: "In cash",
  payroll: "From payroll",
};

/** "By ACH", "By card". An unknown rail shows its own words. */
export function railText(rail: string): string {
  return RAIL_TEXT[rail] ?? rail.replace(/_/g, " ");
}

/**
 * "Last paid Sep 1, 2026", plus the amount when it differs from the usual one.
 * "No tagged payment yet" for a counted item with none. Null for the rest.
 */
export function lastPaidText(item: RecurringItem): string | null {
  if (item.last_paid_date) {
    const differs = item.last_paid_cents !== null && item.last_paid_cents !== item.amount_cents;
    return `Last paid ${formatDate(item.last_paid_date)}${differs ? `: ${formatMoney(item.last_paid_cents!, { cents: true })}` : ""}`;
  }
  return item.counted ? "No tagged payment yet" : null;
}

export const STATE_TEXT: Record<RecurringDueState, string> = {
  paid: "Paid",
  upcoming: "Upcoming",
  waiting: "Statement not in",
  no_history: "No payment history yet",
  missed: "Missed",
};

/** Due states in legend order. */
export const STATE_ORDER: RecurringDueState[] = ["paid", "upcoming", "waiting", "no_history", "missed"];

/** Count words where the badge text does not read well after a number. */
const COUNT_TEXT: Partial<Record<RecurringDueState, string>> = { no_history: "with no payment history yet" };

/** "1 paid, 1 missed": how many of a day's bills are in each state, for a screen reader. */
export function stateCountText(items: Pick<RecurringDue, "state">[]): string {
  return STATE_ORDER.flatMap((s) => {
    const n = items.filter((it) => it.state === s).length;
    return n ? [`${n} ${COUNT_TEXT[s] ?? STATE_TEXT[s].toLowerCase()}`] : [];
  }).join(", ");
}

/** Hover text for each due state. */
export const STATE_HINT: Record<RecurringDueState, string> = {
  paid: "A matching transaction posted in the due window.",
  upcoming: "Not due yet.",
  waiting: "Past due, but the paying account's statements do not reach this date yet.",
  no_history: "Past due, but no tagged payment goes back this far, so the app cannot check it.",
  missed: "No matching transaction in the due window.",
};

/** Row key: weekly items show up more than once in a month. */
export function dueKey(d: RecurringDue): string {
  return `${d.key}:${d.due_date}`;
}

/** Item details by key, for badges on the monthly rows. */
export function itemMap(items: RecurringItem[] | undefined): Map<string, RecurringItem> {
  return new Map((items ?? []).map((i) => [i.key, i]));
}

// ---------- All tab: person filter, sections, savings, cancelled ----------

export type RecurringSection = NonNullable<RecurringResponse["sections"]>[number];

export interface PersonOption {
  /** The owner key, as in the `person` URL param. */
  key: string;
  label: string;
}

/** Each distinct item owner, by label. Items with no owner are not a person. */
export function personOptions(items: RecurringItem[] | undefined): PersonOption[] {
  const by = new Map<string, string>();
  for (const i of items ?? []) if (i.owner) by.set(i.owner, i.owner_label ?? i.owner);
  return [...by].map(([key, label]) => ({ key, label })).sort((a, b) => a.label.localeCompare(b.label) || a.key.localeCompare(b.key));
}

/**
 * The person to filter by: an owner from the options, and only when there are
 * two or more. Anything else (no param, an unknown key) means everyone.
 */
export function parsePerson(value: string | null, options: PersonOption[]): string | null {
  return options.length >= 2 && options.some((o) => o.key === value) ? value : null;
}

/** One person's items, or every item for everyone. */
export function byPerson(items: RecurringItem[], person: string | null): RecurringItem[] {
  return person === null ? items : items.filter((i) => i.owner === person);
}

/** Section keys that hold savings and transfers, not bills. */
function savingsKeys(sections: RecurringSection[] | undefined): Set<string> {
  return new Set(sections ? sections.filter((s) => s.kind === "savings").map((s) => s.key) : ["savings"]);
}

export interface AllTotals {
  /** Counted bills: gross and net monthly equivalents. */
  gross: number;
  net: number;
  count: number;
  /** Counted savings and transfers, net a month. */
  savings: number;
  savingsCount: number;
}

/** The All tab's tiles: counted bills apart from counted savings and transfers. */
export function allTotals(items: RecurringItem[], sections: RecurringSection[] | undefined): AllTotals {
  const savings = savingsKeys(sections);
  const out: AllTotals = { gross: 0, net: 0, count: 0, savings: 0, savingsCount: 0 };
  for (const i of items) {
    if (!i.counted) continue;
    if (savings.has(i.section_key)) {
      out.savings += i.net_monthly_equivalent_cents;
      out.savingsCount += 1;
    } else {
      out.gross += i.monthly_equivalent_cents;
      out.net += i.net_monthly_equivalent_cents;
      out.count += 1;
    }
  }
  return out;
}

export interface BillSection {
  section: RecurringSection;
  /** In list order. Ended items are left out: they go to the cancelled card. */
  items: RecurringItem[];
  /** Counted items and their net monthly total. */
  count: number;
  net: number;
  /** A counted item has a card credit, so the total is after credits. */
  credits: boolean;
}

/** Bill and tag sections in the server's order, each with its items. Empty sections are left out. */
export function billSections(items: RecurringItem[], sections: RecurringSection[] | undefined): BillSection[] {
  return (sections ?? []).flatMap((section) => {
    if (section.kind === "savings") return [];
    const rows = items.filter((i) => i.section_key === section.key && i.status !== "ended");
    if (rows.length === 0) return [];
    const counted = rows.filter((i) => i.counted);
    return [
      {
        section,
        items: rows,
        count: counted.length,
        net: counted.reduce((sum, i) => sum + i.net_monthly_equivalent_cents, 0),
        credits: counted.some((i) => i.net_monthly_equivalent_cents !== i.monthly_equivalent_cents),
      },
    ];
  });
}

/** A section's name for screen readers. A tag section also names its plan group, since two groups can each have "Hobbies". */
export function sectionName(section: RecurringSection): string {
  return section.kind === "tag" && section.group_label ? `${section.label} in ${section.group_label}` : section.label;
}

/** "4 bills · 1 paused · about $210 a month after credits". Paused bills are listed but not counted. */
export function sectionSubtitle(s: BillSection): string {
  const paused = s.items.length - s.count;
  const pausedText = paused ? ` · ${paused} paused` : "";
  if (s.count === 0) return `${paused} paused · not counted`;
  return `${s.count} ${s.count === 1 ? "bill" : "bills"}${pausedText} · about ${formatMoney(s.net)} a month${s.credits ? " after credits" : ""}`;
}

/** Sep to May. Month numbers, January = 1. */
export const SCHOOL_MONTHS = [9, 10, 11, 12, 1, 2, 3, 4, 5];
/** Jun to Aug. */
export const SUMMER_MONTHS = [6, 7, 8];

export interface Seasons {
  /** Average cents a month in each season. Not rounded. */
  schoolMonthly: number;
  summerMonthly: number;
  /** Cents over the whole season. */
  schoolTotal: number;
  summerTotal: number;
}

/**
 * School year and summer costs of the counted items, from their by-month
 * amounts. Null when none of them is seasonal, so a flat section says nothing.
 */
export function seasons(items: RecurringItem[]): Seasons | null {
  const counted = items.filter((i) => i.counted);
  if (!counted.some((i) => seasonText(i.active_months) !== null)) return null;
  const month = (m: number) => counted.reduce((sum, i) => sum + (i.by_month_cents[m - 1] ?? 0), 0);
  const schoolTotal = SCHOOL_MONTHS.reduce((sum, m) => sum + month(m), 0);
  const summerTotal = SUMMER_MONTHS.reduce((sum, m) => sum + month(m), 0);
  return {
    schoolMonthly: schoolTotal / SCHOOL_MONTHS.length,
    summerMonthly: summerTotal / SUMMER_MONTHS.length,
    schoolTotal,
    summerTotal,
  };
}

/** "School year about $X a month · summer about $Y a month", when the two differ in whole dollars. */
export function seasonMonthlyText(s: Seasons | null): string | null {
  if (!s) return null;
  const school = roundToDollars(s.schoolMonthly);
  const summer = roundToDollars(s.summerMonthly);
  if (school === summer) return null;
  return `School year about ${formatMoney(school)} a month · summer about ${formatMoney(summer)} a month`;
}

/** "About $X over the school year and $Y over the summer", leaving out a zero part. */
export function seasonTotalText(s: Seasons | null): string | null {
  if (!s) return null;
  const parts = [
    roundToDollars(s.schoolTotal) ? `${formatMoney(s.schoolTotal)} over the school year` : null,
    roundToDollars(s.summerTotal) ? `${formatMoney(s.summerTotal)} over the summer` : null,
  ].filter((p) => p !== null);
  return parts.length ? `About ${parts.join(" and ")}` : null;
}

export interface SavingsRow {
  item: RecurringItem;
  /** "To Example Savings", or "From Example checking" when the destination is not known. */
  where: string | null;
}

export interface SavingsSubtotal {
  key: string;
  name: string;
  cents: number;
}

export interface Savings {
  rows: SavingsRow[];
  /** Counted rows, net a month. */
  total: number;
  /** A line per known destination that two or more counted rows go to. */
  subtotals: SavingsSubtotal[];
}

/** Savings and transfers, apart from the bills. Ended rows go to the cancelled card. */
export function savingsCard(items: RecurringItem[], sections: RecurringSection[] | undefined): Savings {
  const keys = savingsKeys(sections);
  const list = items.filter((i) => keys.has(i.section_key) && i.status !== "ended");
  const counted = list.filter((i) => i.counted);
  const to = new Map<string, SavingsSubtotal & { n: number }>();
  for (const i of counted) {
    if (!i.to_account_key) continue;
    const t = to.get(i.to_account_key) ?? { key: i.to_account_key, name: i.to_account_name ?? i.to_account_key, cents: 0, n: 0 };
    t.cents += i.net_monthly_equivalent_cents;
    t.n += 1;
    to.set(i.to_account_key, t);
  }
  return {
    rows: list.map((item) => ({
      item,
      where: item.to_account_name ? `To ${item.to_account_name}` : item.account_name ? `From ${item.account_name}` : null,
    })),
    total: counted.reduce((sum, i) => sum + i.net_monthly_equivalent_cents, 0),
    subtotals: [...to.values()].filter((t) => t.n >= 2).map(({ key, name, cents }) => ({ key, name, cents })),
  };
}

export interface CancelledRow {
  item: RecurringItem;
  /** "Cancelled" for an ending item, "Ended" for an ended one. */
  badge: string;
  /** "Runs to Dec 31, 2026", "Stopped Oct 15, 2026" or "Ended Jun 30, 2026". Null when no date is known. */
  when: string | null;
  /** Net monthly amount x 12, in cents. */
  yearly: number;
  /** "from Jan 2027": the month after it stops, while that is still ahead. */
  from: string | null;
  /** A savings transfer or allowance: stopping it saves nothing, so it is not in the total. */
  transfer: boolean;
}

/** The earliest of end_date and, for an ending item, active_through. Past dates too. */
function stopDate(item: RecurringItem): string | null {
  const dates = [item.end_date, item.status === "ending" ? item.active_through : null].filter((d): d is string => !!d);
  return dates.length ? dates.reduce((a, b) => (a < b ? a : b)) : null;
}

/**
 * Ending (cancelled) and ended items, with what each saves a year. In list
 * order. `today` is the household date (YYYY-MM-DD).
 */
export function cancelledRows(items: RecurringItem[], sections: RecurringSection[] | undefined, today: string): CancelledRow[] {
  const savings = savingsKeys(sections);
  return items.flatMap((item) => {
    if (item.status !== "ending" && item.status !== "ended") return [];
    const stops = stopDate(item);
    const ahead = stops !== null && stops >= today;
    const ending = item.status === "ending";
    return [
      {
        item,
        badge: ending ? "Cancelled" : "Ended",
        when: stops ? `${ending && ahead ? "Runs to" : ending ? "Stopped" : "Ended"} ${formatDate(stops)}` : null,
        yearly: item.net_monthly_equivalent_cents * 12,
        from: ending && ahead ? `from ${formatMonth(addMonths(stops.slice(0, 7), 1))}` : null,
        transfer: savings.has(item.section_key),
      },
    ];
  });
}

/** The better account to pay from, when the chat names one that is not the current one. Counted items only. */
export function moveTo(item: RecurringItem): string | null {
  if (!item.counted || !item.preferred_account_key || item.preferred_account_key === item.account_key) return null;
  return item.preferred_account_name ?? item.preferred_account_key;
}
