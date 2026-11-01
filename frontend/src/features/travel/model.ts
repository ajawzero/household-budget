// Pure helpers for the Travel page. Money is integer cents; points are
// integer micro-units. The server works out every total; these only word them.

import type { Trip, TripItem, TripIou, TripPoints, TripStatus, TripTender } from "../../lib/api";
import { formatDate, formatMoney, formatMonth, formatShortDate } from "../../lib/format";
import { formatPoints } from "../rewards/model";

export const STATUS_TEXT: Record<TripStatus, string> = {
  planning: "Planning",
  booked: "Booked",
  in_progress: "In progress",
  complete: "Complete",
  final: "Final",
};

export const STATUS_TONE: Record<TripStatus, "neutral" | "accent" | "good" | "warn"> = {
  planning: "neutral",
  booked: "accent",
  in_progress: "warn",
  complete: "good",
  final: "good",
};

export const TENDER_TEXT: Record<TripTender, string> = {
  cash: "Cash",
  points: "Points",
  free_night: "Free night",
  points_credit: "Points credit",
  covered: "Not ours",
};

/**
 * The four chart groups and their fixed color slots, in stack order. Blue and
 * purple are kept apart: side by side they fail the color-blind check
 * (dataviz validate_palette.js, both themes). This order passes.
 */
export const KIND_GROUPS = [
  { key: "flight", label: "Flights", color: "var(--chart-income)" },
  { key: "food", label: "Food", color: "var(--chart-4)" },
  { key: "lodging", label: "Lodging", color: "var(--chart-7)" },
  { key: "other", label: "Everything else", color: "var(--chart-5)" },
] as const;

/** "Jan 15 to 18, 2027", "Dec 30, 2024 to Jan 2, 2025", or "Jun 2027" for a month alone. Never a made-up day. */
export function tripDates(t: { start_date: string | null; end_date: string | null; month: string | null }): string {
  if (t.start_date && t.end_date) {
    if (t.start_date === t.end_date) return formatDate(t.start_date);
    const sameYear = t.start_date.slice(0, 4) === t.end_date.slice(0, 4);
    const sameMonth = sameYear && t.start_date.slice(0, 7) === t.end_date.slice(0, 7);
    if (sameMonth) return `${formatShortDate(t.start_date)} to ${Number(t.end_date.slice(8, 10))}, ${t.end_date.slice(0, 4)}`;
    if (sameYear) return `${formatShortDate(t.start_date)} to ${formatDate(t.end_date)}`;
    return `${formatDate(t.start_date)} to ${formatDate(t.end_date)}`;
  }
  return t.month ? formatMonth(t.month) : "No dates yet";
}

/** "Alex, Sam +2 guests". */
export function whoText(t: { people: { name: string }[]; guests: number | null }): string {
  const names = t.people.map((p) => p.name).join(", ");
  const g = t.guests ? `+${t.guests} ${t.guests === 1 ? "guest" : "guests"}` : "";
  return [names, g].filter(Boolean).join(" ") || "Not set";
}

/** "$300 to $420", or one amount when both ends match. */
export function rangeText(low: number, high: number, cents = false): string {
  const f = (c: number) => formatMoney(c, { cents });
  return low === high ? f(low) : `${f(low)} to ${f(high)}`;
}

/** "$3,000 to $4,200 + 1 TBD", "1 TBD", or "None". */
export function remainingText(low: number, high: number, tbd: number): string {
  const parts: string[] = [];
  if (low > 0 || high > 0) parts.push(rangeText(low, high));
  if (tbd > 0) parts.push(`${tbd} TBD`);
  return parts.join(" + ") || "None";
}

/** "50,000 Harbor Miles · 12,000 other-air". */
export function pointsText(points: TripPoints[]): string {
  return points.map((p) => `${formatPoints(p.points_micro)} ${p.program_name ?? "points"}`).join(" · ") || "None";
}

/** What an item row shows on the right: its cash, an estimate range, or TBD ("Not booked" for a maybe TBD item). */
export function itemAmountText(i: TripItem): string {
  if (i.status === "tbd") return i.maybe ? "Not booked" : "TBD";
  if (i.status === "estimate") return i.estimate_low_cents !== null && i.estimate_high_cents !== null ? rangeText(i.estimate_low_cents, i.estimate_high_cents, true) : "Estimate";
  if (i.amount_cents === null) return "";
  return i.tender === "points_credit" ? formatMoney(-i.amount_cents, { cents: true }) : formatMoney(i.amount_cents, { cents: true });
}

/** The due day has passed (household today) and the charge has not posted yet. On the day itself it is not past. */
export function isPastDue(i: Pick<TripItem, "due_on">, today: string): boolean {
  return !!i.due_on && i.due_on < today;
}

/** "Due Nov 10, 2026 on Summit card", or "Was due Nov 10, 2026 on Summit card" once past.
 * "Due Nov 10, 2026" with no card, or withCard false. Null with no due date. */
export function dueText(i: Pick<TripItem, "due_on" | "account_name">, today: string, withCard = true): string | null {
  if (!i.due_on) return null;
  const card = withCard && i.account_name ? ` on ${i.account_name}` : "";
  return `${isPastDue(i, today) ? "Was due" : "Due"} ${formatDate(i.due_on)}${card}`;
}

/** The small line under an item: points, card, person, note. */
export function itemDetail(i: TripItem): string {
  const parts: string[] = [];
  if (i.points_micro !== null) parts.push(`${formatPoints(i.points_micro)} ${i.program_name ?? "points"}`);
  else if (i.tender === "free_night" && i.program_name) parts.push(i.program_name);
  if (i.tender === "points" || i.tender === "free_night") {
    if (i.amount_cents !== null && i.amount_cents > 0) parts.push(`${formatMoney(i.amount_cents, { cents: true })} in cash fees`);
  }
  if (i.account_name) parts.push(i.account_name);
  if (i.person_name) parts.push(i.person_name);
  if (i.note) parts.push(i.note);
  return parts.join(" · ");
}

/** Open: "Collect $150.00 from friend couple" or "Pay relative $40.00". Settled: "Collected ..." or "Paid ...".
 * Waived: "$25.00 from relative", with the status saying it was called a wash. */
export function iouText(o: TripIou): string {
  const amount = formatMoney(o.amount_cents, { cents: true });
  const toUs = o.direction === "owed_to_us";
  if (o.status === "settled") return toUs ? `Collected ${amount} from ${o.counterparty}` : `Paid ${o.counterparty} ${amount}`;
  if (o.status === "waived") return toUs ? `${amount} from ${o.counterparty}` : `${amount} to ${o.counterparty}`;
  return toUs ? `Collect ${amount} from ${o.counterparty}` : `Pay ${o.counterparty} ${amount}`;
}

export const IOU_STATUS_TEXT: Record<TripIou["status"], string> = {
  open: "Open",
  settled: "Settled",
  waived: "Called it a wash",
};

/** "Came in under the estimate" and so on, for completed trips. */
export function versusText(v: Trip["versus_estimate"]): string | null {
  if (v === "under") return "Came in under";
  if (v === "over") return "Came in over";
  if (v === "within") return "Within the estimate";
  return null;
}

/** The note under "Transactions for this trip" when tagged rows and items are more than $1 apart. */
export function taggedNotes(t: Trip): string[] {
  const g = t.tagged;
  const out: string[] = [];
  if (g.out_differs) {
    out.push(
      `Tagged rows show ${formatMoney(g.out_cents, { cents: true })} out, net of refunds. Posted items add up to ${formatMoney(g.posted_cents, { cents: true })}.`,
    );
  }
  if (g.deposits_differ) {
    out.push(
      `Tagged deposits show ${formatMoney(g.deposits_cents, { cents: true })}. Settled IOUs owed to us add up to ${formatMoney(g.settled_to_us_cents, { cents: true })}.`,
    );
  }
  return out;
}

export function isDone(s: TripStatus): boolean {
  return s === "complete" || s === "final";
}

/** The Transactions link for a trip. */
export function tripTransactionsHref(key: string): string {
  return `/transactions?trip=${encodeURIComponent(key)}`;
}

/** The Travel link that opens one trip. */
export function travelHref(key: string): string {
  return `/travel?trip=${encodeURIComponent(key)}`;
}

export function excludedItems(t: Trip): TripItem[] {
  return t.items.filter((i) => i.status === "excluded");
}
