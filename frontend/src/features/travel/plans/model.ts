// Pure helpers for the trip planner screens (docs/plans/trip-planner.md).
// The plan's own prices are whole US dollars; the app's numbers are cents.
// The server works out every sum and check; these only word them.

import type {
  PlanCostKind,
  PlanDayItem,
  PlanGroundMode,
  PlanInterest,
  PlanLean,
  PlanPace,
  PlanRequestBody,
  PlanStatus,
  PlanStep,
} from "../../../lib/api";
import { formatMoney } from "../../../lib/format";
import { rangeText } from "../model";

export const INTERESTS: readonly { value: PlanInterest; label: string }[] = [
  { value: "food", label: "Food" },
  { value: "history", label: "History" },
  { value: "museums", label: "Museums" },
  { value: "art", label: "Art" },
  { value: "nature", label: "Nature" },
  { value: "hiking", label: "Hiking" },
  { value: "beaches", label: "Beaches" },
  { value: "nightlife", label: "Nightlife" },
  { value: "shopping", label: "Shopping" },
  { value: "family", label: "Family" },
  { value: "sports", label: "Sports" },
  { value: "music", label: "Music" },
  { value: "relaxing", label: "Relaxing" },
];

export const PACE_OPTIONS: readonly { value: PlanPace; label: string }[] = [
  { value: "relaxed", label: "Relaxed" },
  { value: "balanced", label: "Balanced" },
  { value: "packed", label: "Packed" },
];

export const LEAN_OPTIONS: readonly { value: PlanLean; label: string }[] = [
  { value: "value", label: "Best value" },
  { value: "points", label: "Points" },
  { value: "cash", label: "Cash" },
];

export const PLAN_STATUS_TEXT: Record<PlanStatus, string> = {
  running: "Being made",
  ready: "Ready",
  failed: "Did not finish",
};

export const PLAN_STATUS_TONE: Record<PlanStatus, "neutral" | "accent" | "good" | "warn"> = {
  running: "accent",
  ready: "good",
  failed: "warn",
};

export const STEP_TEXT: Record<PlanStep, string> = {
  searching: "Searching the web",
  writing: "Writing the plan",
};

/** The trip item kinds, worded like imported trips. */
export const COST_KIND_TEXT: Record<PlanCostKind, string> = {
  flight: "Flights",
  lodging: "Lodging",
  rental_car: "Rental car",
  gas: "Gas",
  food: "Food",
  activities: "Activities",
  tickets: "Tickets",
  shopping: "Shopping",
  insurance: "Insurance",
  parking: "Parking",
  transit: "Transit",
  cash: "Cash",
  supplies: "Supplies",
  fees: "Fees",
  other: "Other",
};

export const GROUND_TEXT: Record<PlanGroundMode, string> = {
  rental_car: "Rental car",
  transit: "Transit",
  train: "Train",
  taxi: "Taxi",
  walk: "Walking",
  drive: "Driving",
  other: "Other",
};

export const PART_TEXT: Record<PlanDayItem["part"], string> = {
  morning: "Morning",
  afternoon: "Afternoon",
  evening: "Evening",
};

export const ITEM_KIND_TEXT: Record<PlanDayItem["kind"], string> = {
  sight: "Sight",
  meal: "Meal",
  activity: "Activity",
  travel: "Travel",
  rest: "Rest",
};

export const CHECK_INTRO = "Prices and award seats change daily.";

/** The plan page. */
export function planHref(planId: string): string {
  return `/travel/plans/${encodeURIComponent(planId)}`;
}

/** Whole dollars from the plan: "$1,200". */
export function usd(dollars: number): string {
  return formatMoney(dollars * 100);
}

/** "$300 to $420", or one amount when both ends match. */
export function usdRange(low: number, high: number): string {
  return rangeText(low * 100, high * 100);
}

/** "About $1", or "About $0.40" under a dollar. */
export function estimateText(cents: number): string {
  return `About ${formatMoney(cents, { cents: cents > 0 && cents < 100 })}`;
}

/** "1.85¢ a point". Null when there is no value (a benefit, no points). */
export function valueText(cents: string | null): string | null {
  if (cents === null) return null;
  const n = Number(cents);
  if (!Number.isFinite(n)) return null;
  return `${n.toLocaleString("en-US", { maximumFractionDigits: 2 })}¢ a point`;
}

/** Points as a whole number: "60,000". */
export function pointsCount(n: number): string {
  return n.toLocaleString("en-US");
}

/**
 * Points needed against the balance: "60,000 of 84,250". A cash-back balance
 * is dollars (a text from the brief) and is never set against points.
 */
export function neededText(needed: number, have: number | string | null): string {
  if (typeof have === "number") return `${pointsCount(needed)} of ${pointsCount(have)}`;
  if (have === null) return `${pointsCount(needed)} needed, balance not known`;
  return `${pointsCount(needed)} needed, balance $${have} cash back`;
}

/** "3 travelers": the household people plus guests. */
export function travelersText(people: number, guests: number): string {
  const n = people + guests;
  return `${n} ${n === 1 ? "traveler" : "travelers"}`;
}

/** "5 nights". */
export function nightsText(n: number): string {
  return `${n} ${n === 1 ? "night" : "nights"}`;
}

const MAX_URL = 500;
const HOST = /^[a-z0-9.-]+$/;
// Printable ASCII only: the URL parser turns a non-ASCII host into punycode,
// which would then pass the host check.
const PLAIN = /^[\x21-\x7e]+$/;

/**
 * The link to open, or null. The server already keeps only search results
 * with plain web links (planner.safe_url); this checks again before a link
 * is drawn: http or https, no user name or password, a plain ASCII host.
 */
export function safeHref(url: string): string | null {
  if (url.length > MAX_URL || !PLAIN.test(url) || url.includes("\\")) return null;
  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return null;
  }
  if (u.protocol !== "https:" && u.protocol !== "http:") return null;
  if (u.username || u.password || !HOST.test(u.hostname)) return null;
  return u.href;
}

// ---------- The form ----------

export interface PlanForm {
  where: string;
  when: "dates" | "month";
  start: string;
  end: string;
  month: string;
  nights: string;
  people: string[];
  guests: string;
  airport: string;
  interests: PlanInterest[];
  note: string;
  pace: PlanPace;
  lean: PlanLean;
}

export const EMPTY_FORM: PlanForm = {
  where: "",
  when: "dates",
  start: "",
  end: "",
  month: "",
  nights: "",
  people: [],
  guests: "0",
  airport: "",
  interests: [],
  note: "",
  pace: "balanced",
  lean: "value",
};

export const WHERE_MAX = 120;
export const NOTE_MAX = 300;
export const FOLLOW_UP_MAX = 1000;
export const MAX_NIGHTS = 30;
export const MAX_GUESTS = 20;

/** Nights between two YYYY-MM-DD dates. */
export function nightsBetween(start: string, end: string): number {
  return Math.round((Date.parse(`${end}T00:00:00Z`) - Date.parse(`${start}T00:00:00Z`)) / 86_400_000);
}

const WHOLE = /^\d{1,3}$/;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const ISO_MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;

/**
 * The request to send, or the first problem in words. The server checks
 * again (two-year bound, private names, known people).
 */
export function buildRequest(f: PlanForm, today: string): { body: PlanRequestBody } | { problem: string } {
  const where = f.where.trim();
  if (!where) return { problem: "Type where you want to go." };
  if (where.length > WHERE_MAX) return { problem: `Keep the place under ${WHERE_MAX} characters.` };
  const body: PlanRequestBody = {
    where,
    people: f.people,
    guests: 0,
    interests: f.interests,
    note: f.note.trim(),
    pace: f.pace,
    lean: f.lean,
  };
  if (f.when === "dates") {
    if (!ISO_DATE.test(f.start) || !ISO_DATE.test(f.end)) return { problem: "Pick a start and an end date." };
    if (f.start < today) return { problem: "Pick dates from today on." };
    const n = nightsBetween(f.start, f.end);
    if (!(n >= 1 && n <= MAX_NIGHTS)) return { problem: `A trip is 1 to ${MAX_NIGHTS} nights. The end comes after the start.` };
    body.start_date = f.start;
    body.end_date = f.end;
  } else {
    if (!ISO_MONTH.test(f.month)) return { problem: "Pick a month." };
    if (f.month < today.slice(0, 7)) return { problem: "Pick a month from this one on." };
    const n = WHOLE.test(f.nights.trim()) ? Number(f.nights.trim()) : NaN;
    if (!(n >= 1 && n <= MAX_NIGHTS)) return { problem: `Enter the nights, 1 to ${MAX_NIGHTS}.` };
    body.month = f.month;
    body.nights = n;
  }
  const guests = f.guests.trim() === "" ? 0 : WHOLE.test(f.guests.trim()) ? Number(f.guests.trim()) : NaN;
  if (!(guests >= 0 && guests <= MAX_GUESTS)) return { problem: `Guests: a whole number up to ${MAX_GUESTS}.` };
  body.guests = guests;
  if (f.people.length === 0 && guests === 0) return { problem: "Pick who is going." };
  const airport = f.airport.trim().toUpperCase();
  if (airport) {
    if (!/^[A-Z]{3}$/.test(airport)) return { problem: "Use a 3-letter airport code, like MSP." };
    body.home_airport = airport;
  }
  if (body.note.length > NOTE_MAX) return { problem: `Keep the note under ${NOTE_MAX} characters.` };
  return { body };
}
