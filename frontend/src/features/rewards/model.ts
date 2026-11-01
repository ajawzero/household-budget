// Pure helpers for the Rewards page. Points are integer micro-units from the
// API (1 point = 1,000,000). Money is integer cents.
import type {
  BalanceEntry,
  BenefitField,
  BenefitFileBody,
  FileChange,
  FileMode,
  NeedsLook,
  ReviewGuess,
  RewardBenefit,
  RewardCycle,
  RewardCycleSource,
  RewardCycleState,
  RewardMilestone,
  RewardProgram,
  RewardsResponse,
  RewardsReview,
} from "../../lib/api";
import { formatDate, formatMoney, formatMonthShort, formatShortDate } from "../../lib/format";
import { parseDollarsCents } from "../settings/vehicles/parse";

export const MICRO = 1_000_000;

export type RewardsTab = "points" | "benefits" | "milestones" | "routing" | "fees";

export const TABS: { key: RewardsTab; label: string }[] = [
  { key: "points", label: "Points" },
  { key: "benefits", label: "Benefits" },
  { key: "milestones", label: "Milestones" },
  { key: "routing", label: "Routing" },
  { key: "fees", label: "Fees" },
];

export function parseTab(value: string | null): RewardsTab {
  return TABS.some((t) => t.key === value) ? (value as RewardsTab) : "points";
}

const whole = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const two = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

/** "84,250" points, or "37.42" when there are decimals. */
export function formatPoints(micro: number | null | undefined): string {
  if (micro === null || micro === undefined) return "None";
  const n = micro / MICRO;
  return Number.isInteger(n) ? whole.format(n) : two.format(n);
}

const UNIT: Record<RewardProgram["kind"], [string, string]> = {
  points: ["point", "points"],
  miles: ["mile", "miles"],
  cash_back: ["", ""],
  timeshare: ["point", "points"],
};

/** "84,250 points", "42,000 miles", "$37.42" for cash back. */
export function balanceText(p: Pick<RewardProgram, "kind" | "balance_micro">): string {
  if (p.balance_micro === null) return "No balance";
  if (p.kind === "cash_back") return formatMoney(Math.round(p.balance_micro / 10_000), { cents: true });
  const [one, many] = UNIT[p.kind];
  return `${formatPoints(p.balance_micro)} ${p.balance_micro === MICRO ? one : many}`;
}

/** Ready and Consider: "needed 50,000, have 42,000 miles", "needed $25.00, have $37.42", or "needed 50,000 points, no balance". */
export function redemptionText(kind: RewardProgram["kind"], needed: number, have: number | null): string {
  if (have === null) return `needed ${balanceText({ kind, balance_micro: needed })}, no balance`;
  const need = kind === "cash_back" ? balanceText({ kind, balance_micro: needed }) : formatPoints(needed);
  return `needed ${need}, have ${balanceText({ kind, balance_micro: have })}`;
}

/** Done: "Used 50,000 miles", "Used $25.00". A done redemption's points_needed is what it used.
 * With its day (1.4): "Used 20,000 points on Apr 18, 2026", or "Used on Apr 18, 2026" with no points. */
export function usedText(kind: RewardProgram["kind"], micro: number | null, doneOn?: string | null): string {
  const what = micro === null ? "Used" : `Used ${balanceText({ kind, balance_micro: micro })}`;
  return doneOn ? `${what} on ${formatDate(doneOn)}` : what;
}

/** "42,000" points or miles with no unit, "$37.42" for cash back. */
export function amountText(kind: RewardProgram["kind"], micro: number): string {
  return kind === "cash_back" ? balanceText({ kind, balance_micro: micro }) : formatPoints(micro);
}

/** Where the shown balance came from. "manual" is a number the owner gave the chat. */
export const SOURCE_TEXT: Record<NonNullable<RewardProgram["source"]>, string> = {
  statement: "From a statement",
  manual: "Given to the chat",
  app: "Entered in the app",
};

/** AsOf's hover text on a stale balance that no statement will refresh. */
export const STALE_ENTER = "Older than 90 days. Enter a new balance.";

/** "The chat has 42,000 for the same day.", when the app's entry wins and the two differ. */
export function sameDayText(p: Pick<RewardProgram, "kind" | "chat_balance_micro">): string | null {
  if (p.chat_balance_micro === null) return null;
  return `The chat has ${amountText(p.kind, p.chat_balance_micro)} for the same day.`;
}

/** The last value as a field placeholder: "52,000 as of Aug 2". */
export function lastValueText(p: Pick<RewardProgram, "kind" | "balance_micro" | "as_of">): string {
  if (p.balance_micro === null) return "No balance yet";
  const value = amountText(p.kind, p.balance_micro);
  return p.as_of ? `${value} as of ${formatShortDate(p.as_of)}` : value;
}

/** Whole points or miles: up to 10 digits, like the API. */
const WHOLE_BALANCE = /^\d{1,10}$/;

/**
 * A balance field to the API's text: "5,200" to "5200", "$37.4" to "37.40".
 * Points and miles are whole. Null for an empty field (skip the row),
 * undefined when the text is not valid for the kind.
 */
export function parseBalance(kind: RewardProgram["kind"], text: string): string | null | undefined {
  if (kind === "cash_back") {
    const cents = parseDollarsCents(text);
    if (cents === null || cents === undefined) return cents;
    return `${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, "0")}`;
  }
  const t = text.trim().replace(/,/g, "");
  if (t === "") return null;
  return WHOLE_BALANCE.test(t) ? String(Number(t)) : undefined;
}

/** The unit a balance is typed in: whole (points and miles) or cash (cash back). */
export type BalanceUnit = BalanceEntry["unit"];

/** A program's balance unit. Timeshares have none: they use yearly points. */
export const BALANCE_UNIT: Record<RewardProgram["kind"], BalanceUnit | null> = {
  points: "whole",
  miles: "whole",
  cash_back: "cash",
  timeshare: null,
};

/** Balances move only within one unit: points and miles together, cash back alone. */
export function sameUnit(kind: RewardProgram["kind"], unit: BalanceUnit | null): boolean {
  return unit !== null && BALANCE_UNIT[kind] === unit;
}

/** A unit in a sentence: "points or miles", "cash back". */
export const UNIT_TEXT: Record<BalanceUnit, string> = {
  whole: "points or miles",
  cash: "cash back",
};

/**
 * An entry in the unit it was typed in: "52,000 miles", "$37.42". A whole
 * entry on a program that no longer counts points or miles says "52,000
 * points or miles".
 */
export function entryText(kind: RewardProgram["kind"], unit: BalanceUnit, micro: number): string {
  if (unit === "cash") return balanceText({ kind: "cash_back", balance_micro: micro });
  if (BALANCE_UNIT[kind] === "whole") return balanceText({ kind, balance_micro: micro });
  return `${formatPoints(micro)} ${micro === MICRO ? "point or mile" : UNIT_TEXT.whole}`;
}

/** "1.2" or "1.2¢" cents per point to the API's text. Null when empty, undefined when not valid. */
export function parseCpp(text: string): string | null | undefined {
  const t = text.trim().replace(/¢$/, "").trim();
  if (t === "") return null;
  if (!/^\d{1,2}(\.\d{1,4})?$/.test(t)) return undefined;
  return t;
}

/** "$1,011 to $1,517", or one amount when low and high are the same. */
export function rangeText(low: number | null, high: number | null): string {
  if (low === null || high === null) return "Not valued";
  return low === high ? formatMoney(low) : `${formatMoney(low)} to ${formatMoney(high)}`;
}

/** "1.2 to 1.8¢ a point". */
export function cppText(low: string | null, high: string | null): string | null {
  if (low === null || high === null) return null;
  return low === high ? `${low}¢ a point` : `${low} to ${high}¢ a point`;
}

/** "Ends today", "1 day left", "11 days left". */
export function daysLeftText(days: number | null): string | null {
  if (days === null) return null;
  if (days <= 0) return "Ends today";
  return `${days} ${days === 1 ? "day" : "days"} left`;
}

export const CYCLE_TEXT: Record<RewardCycleState, string> = {
  done: "Used",
  partial: "Partly used",
  open: "Not used yet",
  expired: "Expired",
};

/** Expired is gray: a past deadline never shows red again. */
export const CYCLE_TONE: Record<RewardCycleState, "good" | "warn" | "neutral"> = {
  done: "good",
  partial: "warn",
  open: "neutral",
  expired: "neutral",
};

export const GROUP_TITLE: Record<RewardBenefit["group"], string> = {
  open: "Open this cycle",
  auto: "Loads by itself",
  used: "Used this cycle",
  upcoming: "Starts later",
  other: "Other benefits",
  ended: "Ended",
};

/** Benefits by group in the API's order. Ended ones go under "Show past cycles". */
export function benefitGroups(benefits: RewardBenefit[]): { group: RewardBenefit["group"]; items: RewardBenefit[] }[] {
  const out: { group: RewardBenefit["group"]; items: RewardBenefit[] }[] = [];
  for (const b of benefits) {
    if (b.group === "ended") continue;
    const last = out[out.length - 1];
    if (last && last.group === b.group) last.items.push(b);
    else out.push({ group: b.group, items: [b] });
  }
  return out;
}

/** A benefit's main line: "$10.00 left of $10.00 · 11 days left", or a free night's expiry. */
export function benefitLine(b: RewardBenefit): string {
  const parts: string[] = [];
  const c = b.cycle;
  if (c) {
    if (b.group === "upcoming") parts.push(`Starts ${formatShortDate(c.start)}`);
    else if (c.state === "done") parts.push(`${formatMoney(c.used_cents, { cents: true })} used`);
    else if (c.state === "partial") parts.push(`${formatMoney(c.left_cents, { cents: true })} left of ${formatMoney(c.amount_cents, { cents: true })}`);
    else parts.push(`${formatMoney(c.amount_cents, { cents: true })} to use`);
    if (b.group !== "upcoming") parts.push(`ends ${formatShortDate(c.end)}`);
  } else if (b.deadline) {
    parts.push(`Expires ${formatShortDate(b.deadline)}`);
  }
  const days = b.group === "open" || b.group === "auto" ? daysLeftText(b.days_left) : null;
  if (days) parts.push(days);
  return parts.join(" · ");
}

export const KIND_TEXT: Record<RewardBenefit["kind"], string> = {
  credit: "Credit",
  earning_cap: "Earning cap",
  status_perk: "Status perk",
  free_night: "Free night",
  other: "Other",
};

export const CADENCE_TEXT: Record<NonNullable<RewardBenefit["cadence"]>, string> = {
  monthly: "Monthly",
  quarterly: "Quarterly",
  semi_annual: "Twice a year",
  annual: "Yearly",
  multi_year: "Every few years",
  one_time: "One time",
};

/** Cadences whose cycles the app cannot work out: they list their own. */
export const STORED_CYCLES: NonNullable<RewardBenefit["cadence"]>[] = ["multi_year", "one_time"];

/** A benefit field in a sentence: "Use the chat's for the amount". */
export const FIELD_NAME: Record<BenefitField, string> = {
  name: "name",
  amount: "amount",
  counted_value: "yearly value",
  auto: "loads by itself",
  enrolled: "enrolled",
  rolls_over: "rolls over",
  enroll_by: "enroll-by date",
  ends_on: "end date",
  how_to_use: "how to use it",
  note: "note",
  cap: "cap",
  earn_before: "earn rate up to the cap",
  earn_after: "earn rate after the cap",
  category_keys: "categories",
  reported_spent: "spend the chat counts",
  as_of: "as of date",
  program_key: "program",
  status_tier: "status tier",
};

export const MONEY_RULE = "Use dollars and cents, like 12.00.";

/** The API's money limit: 7 digits before the point. */
const MONEY_LIMIT_CENTS = 10_000_000 * 100;

/** Cents to the API's money text: 1_250 to "12.50". */
export function moneyText(cents: number): string {
  return `${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, "0")}`;
}

/** "$1,250" or "12.5" to "1250.00" or "12.50". Null when empty, undefined when not valid. */
export function parseMoney(text: string): string | null | undefined {
  const cents = parseDollarsCents(text);
  if (cents === null || cents === undefined) return cents;
  return cents < MONEY_LIMIT_CENTS ? moneyText(cents) : undefined;
}

/** "Dining credit · Summit card", plus "(added in the app)" when asked, so two alike stay apart. */
export function benefitLabel(name: string, card: string | null, app = false): string {
  return `${card ? `${name} · ${card}` : name}${app ? " (added in the app)" : ""}`;
}

/**
 * When a new amount starts, by default: the day an earlier change started
 * (the API keeps one start, so a later day is refused), else the start of the
 * cycle that holds today, else today (like the API's default).
 */
export function amountStartDefault(b: Pick<RewardBenefit, "cycle" | "amount_from">, today: string): string {
  if (b.amount_from) return b.amount_from;
  const c = b.cycle;
  return c && c.start <= today && today <= c.end ? c.start : today;
}

/** The kinds the page's benefit list holds. Earning caps and status perks have their own cards. */
const LIST_KINDS: RewardBenefit["kind"][] = ["credit", "free_night", "other"];

/**
 * The page with a saved benefit in place, until the refetch lands: a hidden
 * one moves to Hidden (an earning cap or status perk leaves its card too), one
 * shown again leaves it. A benefit keeps its place in the list. One shown
 * again goes after the last one in its group, so no group shows twice, until
 * the refetch sorts it.
 */
export function withBenefit(page: RewardsResponse, out: RewardBenefit): RewardsResponse {
  const at = page.benefits.findIndex((b) => b.key === out.key);
  const benefits = page.benefits.filter((b) => b.key !== out.key);
  const hidden = page.hidden.filter((h) => h.key !== out.key);
  if (out.hidden) {
    const { key, name, kind, account_key, account_name, origin } = out;
    hidden.push({ key, name, kind, account_key, account_name, origin });
    hidden.sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
  } else if (at >= 0) {
    benefits.splice(at, 0, out);
  } else if (LIST_KINDS.includes(out.kind)) {
    const last = benefits.map((b) => b.group).lastIndexOf(out.group);
    benefits.splice(last === -1 ? benefits.length : last + 1, 0, out);
  }
  const caps = out.hidden ? page.caps.filter((c) => c.key !== out.key) : page.caps;
  const perks = out.hidden ? page.perks.filter((p) => p.key !== out.key) : page.perks;
  return { ...page, benefits, hidden, caps, perks };
}

/** Where a cycle's use comes from (docs/plans/rewards-inputs.md 3.3). One source, never added. */
export const CYCLE_SOURCE_TEXT: Record<RewardCycleSource, string> = {
  set: "Set in the app",
  rows: "From your statements",
  chat: "From the chat",
};

/** A credit whose rows the app matches: it has match words and matching is on. */
export function matches(b: Pick<RewardBenefit, "terms_count" | "match_off">): boolean {
  return b.terms_count > 0 && !b.match_off;
}

/**
 * The notes on a cycle's line: its source, "The chat says $6.00" when the
 * rows win and the chat's number differs, "Waiting for rows" on a matched
 * credit whose statements have not come in and nothing else says used, and
 * "More than the credit by $5.00".
 */
export function cycleNotes(c: RewardCycle, matched: boolean): string[] {
  const out: string[] = [];
  if (c.source) out.push(CYCLE_SOURCE_TEXT[c.source]);
  if (c.source === "rows" && c.chat_used_cents !== null && c.chat_used_cents !== c.used_cents) {
    out.push(`The chat says ${formatMoney(c.chat_used_cents, { cents: true })}`);
  }
  if (matched && !c.covered && c.source === null) out.push("Waiting for rows");
  if (c.over_cents > 0) out.push(`More than the credit by ${formatMoney(c.over_cents, { cents: true })}`);
  return out;
}

/** A credit's cycles that have started, newest first: this one, then the past ones. */
export function startedCycles(b: Pick<RewardBenefit, "cycle" | "past_cycles">, today: string): RewardCycle[] {
  const now = b.cycle && b.cycle.start <= today ? [b.cycle] : [];
  const past = b.past_cycles.filter((c) => !now.some((n) => n.start === c.start));
  return [...now, ...[...past].reverse()];
}

/** "Mark used" counts the cycle's amount on its last day, or today while it runs. Never a future day. */
export function markUsedOn(c: Pick<RewardCycle, "end">, today: string): string {
  return c.end < today ? c.end : today;
}

/** "Looks like Dining credit, Sep": the benefit and the month its cycle starts. */
export function guessText(g: ReviewGuess): string {
  return `Looks like ${g.benefit_name}, ${formatMonthShort(g.cycle_start)}`;
}

/** "1 card credit", "5 card credits". */
export function countText(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/**
 * Programs to update this month: a balance no statement refreshes (given to
 * the chat or entered in the app) that is not from this month, a program with
 * no balance yet, and any stale balance. Timeshares use their yearly points.
 */
export function balancesToUpdate(programs: RewardProgram[], today: string): number {
  const month = today.slice(0, 7);
  return programs.filter((p) => {
    if (p.kind === "timeshare") return false;
    if (p.stale || p.balance_micro === null || p.as_of === null) return true;
    return p.source !== "statement" && p.as_of.slice(0, 7) < month;
  }).length;
}

/** Credit benefits on a card that a row can count for: shown, with matching on. */
export function cardCredits(benefits: RewardBenefit[], accountKey: string): RewardBenefit[] {
  return benefits.filter((b) => b.kind === "credit" && b.account_key === accountKey && !b.match_off);
}

/** The review without one row, until the refetch lands. Its counts go down with it. */
export function withoutRow(review: RewardsReview, txnKey: string): RewardsReview {
  const credit = review.credits.find((r) => r.txn_key === txnKey);
  const inReturns = review.returns.some((r) => r.txn_key === txnKey);
  const inTrips = review.trips.some((r) => r.txn_key === txnKey);
  const flagged = review.needs_a_look.some((n) => n.kind !== "chat_only" && n.txn_key === txnKey);
  const c = review.counts;
  return {
    ...review,
    credits: review.credits.filter((r) => r.txn_key !== txnKey),
    returns: review.returns.filter((r) => r.txn_key !== txnKey),
    trips: review.trips.filter((r) => r.txn_key !== txnKey),
    needs_a_look: review.needs_a_look.filter((n) => n.kind === "chat_only" || n.txn_key !== txnKey),
    counts: {
      ...c,
      to_review: c.to_review - (credit ? 1 : 0),
      ambiguous: c.ambiguous - (credit && credit.options.length > 0 ? 1 : 0),
      returns: c.returns - (inReturns ? 1 : 0),
      trips: c.trips - (inTrips ? 1 : 0),
      needs_a_look: c.needs_a_look - (flagged ? 1 : 0),
    },
  };
}

/** The review without a chat-only cycle once it is marked, until the refetch lands. */
export function withoutChatOnly(review: RewardsReview, key: string, start: string): RewardsReview {
  const gone = (n: NeedsLook) => n.kind === "chat_only" && n.benefit_key === key && n.cycle_start === start;
  const found = review.needs_a_look.some(gone);
  return {
    ...review,
    needs_a_look: review.needs_a_look.filter((n) => !gone(n)),
    counts: { ...review.counts, chat_says_used: review.counts.chat_says_used - (found ? 1 : 0) },
  };
}

/** A match word: 3 to 40 plain characters, like the API. The API also refuses words like CREDIT alone. */
export const TERM_RULE = "A match word is 3 to 40 plain letters, digits, spaces or marks.";

/** A typed word as the API keeps it: trimmed, inner spaces made one. */
export function termText(text: string): string {
  return text.trim().replace(/ +/g, " ");
}

/** Null when the word can go to the API (or nothing is typed yet), else the rule it breaks. */
export function termProblem(text: string): string | null {
  if (!/^[\x20-\x7e]*$/.test(text)) return TERM_RULE;
  const t = termText(text);
  if (t === "") return null;
  return t.length < 3 || t.length > 40 ? TERM_RULE : null;
}

/** A benefit file is at most 256 KB, like the API. The browser checks before it reads one. */
export const FILE_MAX_BYTES = 256 * 1024;

export const FILE_TOO_BIG = "This file is too big.";

export const OTHER_CARD = "This file is for another card. Pick that card, or fix account_key in the file.";

/**
 * The line a JSON.parse error points to, when the message says. Browsers
 * give a line, a position or neither. Only the number is used: the message
 * itself can quote the file.
 */
export function jsonErrorLine(message: string, text: string): number | null {
  const line = /\bline (\d+)/i.exec(message);
  if (line) return Number(line[1]);
  const at = /\bposition (\d+)/i.exec(message);
  return at ? text.slice(0, Number(at[1])).split("\n").length : null;
}

/**
 * A file's text to the API body, or the problem to show. The drawer's card
 * goes on top of the file's. Its mode and start date do too once set there:
 * null (or an empty date) keeps the file's, and the API's defaults (merge,
 * today) when the file has none. A file that names another card is refused,
 * so a list never lands on the wrong card.
 */
export function fileBody(text: string, card: string, mode: FileMode | null, startsOn: string | null): BenefitFileBody | string {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch (err) {
    const line = jsonErrorLine(err instanceof Error ? err.message : "", text);
    return line === null ? "This is not valid JSON." : `This is not valid JSON. Look near line ${line}.`;
  }
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return "This is not a benefits file. Start from this card's list.";
  }
  const file = value as Record<string, unknown>;
  if (file.account_key !== undefined && file.account_key !== card) return OTHER_CARD;
  // A mode the file gets wrong goes as it is, so the check names it.
  const body: BenefitFileBody = { ...file, account_key: card, mode: mode ?? ((file.mode as FileMode | undefined) ?? "merge") };
  if (startsOn) body.starts_on = startsOn;
  return body;
}

/** The file fields a benefit edit does not have, in a sentence. */
const FILE_FIELD_NAME: Record<string, string> = {
  cycles: "cycles",
};

/** A changed field in a sentence: "amount", "how to use it", "cycles". */
export function fileFieldName(field: string): string {
  return FIELD_NAME[field as BenefitField] ?? FILE_FIELD_NAME[field] ?? field.replace(/_/g, " ");
}

/** "Amount from Jan 1, 2027 · note · match words": what a file changes on one benefit. */
export function fileChangeText(c: FileChange): string {
  const parts = c.fields.map((f) => (f === "amount" && c.amount_from ? `amount from ${formatDate(c.amount_from)}` : fileFieldName(f)));
  if (c.terms) parts.push("match words");
  const text = parts.join(" · ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** "Saved. 1 change." or "Saved. 5 changes." */
export function fileSavedText(n: number): string {
  return `Saved. ${countText(n, "change", "changes")}.`;
}

/** "1 benefit stays as it is." or "3 benefits stay as they are." */
export function unchangedText(n: number): string {
  return n === 1 ? "1 benefit stays as it is." : `${n} benefits stay as they are.`;
}

export const PACE_TEXT: Record<NonNullable<RewardMilestone["pace"]>, string> = {
  met: "Met",
  missed: "Missed",
  on_pace: "On pace",
  behind: "Behind pace",
};

export const PACE_TONE: Record<NonNullable<RewardMilestone["pace"]>, "good" | "warn" | "neutral"> = {
  met: "good",
  missed: "neutral",
  on_pace: "good",
  behind: "warn",
};

/** Share of the bar, 0 to 100. */
export function pctOf(part: number, whole: number): number {
  if (whole <= 0) return 0;
  return Math.min(100, Math.max(0, (part / whole) * 100));
}

/** Fixed slots for cardholders, by person key, never by size. No cardholder is gray. */
const HOLDER_SLOTS = ["var(--chart-4)", "var(--chart-7)", "var(--chart-6)", "var(--chart-5)", "var(--chart-8)"];

export function holderColors(keys: (string | null)[]): Map<string | null, string> {
  const named = [...new Set(keys.filter((k): k is string => k !== null))].sort();
  const out = new Map<string | null, string>(named.map((k, i) => [k, HOLDER_SLOTS[i % HOLDER_SLOTS.length]!]));
  out.set(null, "var(--chart-muted)");
  return out;
}
