// Display helpers. Money is integer cents and is formatted with integer
// math only, so no float rounding can creep in.

export const MINUS = "−";

const grouping = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

export interface MoneyOptions {
  /** Show cents (transactions). The default is whole dollars (summaries). */
  cents?: boolean;
}

/**
 * Formats cents as US dollars with a real minus sign.
 * Whole dollars round half away from zero. A value that rounds to zero
 * shows no sign.
 */
export function formatMoney(cents: number, opts: MoneyOptions = {}): string {
  if (!Number.isSafeInteger(cents)) return "";
  const abs = Math.abs(cents);
  let body: string;
  let isZero: boolean;
  if (opts.cents) {
    const dollars = Math.floor(abs / 100);
    const rest = abs % 100;
    body = `${grouping.format(dollars)}.${String(rest).padStart(2, "0")}`;
    isZero = abs === 0;
  } else {
    const dollars = Math.floor((abs + 50) / 100);
    body = grouping.format(dollars);
    isZero = dollars === 0;
  }
  const sign = cents < 0 && !isZero ? MINUS : "";
  return `${sign}$${body}`;
}

/** A run id ("2026-10-12" or "2026-10-12-2") as its update date: "Oct 12, 2026". */
export function formatRunDate(runId: string): string {
  return formatDate(runId.slice(0, 10));
}

/** Cents rounded to whole dollars the way formatMoney shows them (half up, away from zero). */
export function roundToDollars(cents: number): number {
  const dollars = Math.floor((Math.abs(cents) + 50) / 100) * 100;
  return cents < 0 ? -dollars : dollars;
}

/**
 * The gap between two amounts shown in whole dollars, worked out from the
 * rounded figures, so "$213 of $300" sits next to "$87", not "$88".
 */
export function formatMoneyGap(a: number, b: number): string {
  return formatMoney(roundToDollars(a) - roundToDollars(b));
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-09-30" to "Sep 30, 2026". Parsed by hand so time zones cannot shift the day. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  const month = MONTHS[Number(m[2]) - 1];
  return month ? `${month} ${Number(m[3])}, ${m[1]}` : iso;
}

/** "2026-09" to "Sep 2026". */
export function formatMonth(ym: string | null | undefined): string {
  if (!ym) return "";
  const m = /^(\d{4})-(\d{2})$/.exec(ym);
  if (!m) return ym;
  const month = MONTHS[Number(m[2]) - 1];
  return month ? `${month} ${m[1]}` : ym;
}

/** A date, a month or anything else, shown as plainly as possible. */
export function formatDay(value: string | null | undefined): string {
  if (!value) return "";
  if (/^\d{4}-\d{2}$/.test(value)) return formatMonth(value);
  return formatDate(value);
}

const timestamp = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/Chicago",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

/** A UTC timestamp shown in household time. */
export function formatTimestamp(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : timestamp.format(d);
}

const compact = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});

/** Chart axes and tight spots: "$1.2M", "$45K", "$950", with a real minus sign. */
export function formatCompact(cents: number): string {
  if (!Number.isFinite(cents)) return "";
  const dollars = Math.round(Math.abs(cents) / 100);
  const body = compact.format(dollars);
  const sign = cents < 0 && dollars !== 0 ? MINUS : "";
  return `${sign}$${body}`;
}

/** "2026-09-30" to "Sep 30". */
export function formatShortDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) return iso;
  const month = MONTHS[Number(m[2]) - 1];
  return month ? `${month} ${Number(m[3])}` : iso;
}

/** "2026-09" to "Sep". Pass withYear for "Sep 2026". */
export function formatMonthShort(ym: string | null | undefined): string {
  if (!ym) return "";
  const m = /^(\d{4})-(\d{2})/.exec(ym);
  return (m && MONTHS[Number(m[2]) - 1]) || ym;
}

/**
 * "4.2" or 4.2 to "4.2%", with a real minus sign. Null shows nothing.
 * A whole number drops its ".0" unless keepDecimal is set, for lists where
 * every value should line up ("17.0%" next to "51.8%").
 */
export function formatPct(value: string | number | null | undefined, keepDecimal = false): string {
  if (value === null || value === undefined || value === "") return "";
  const n = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(n)) return "";
  const fixed = Math.abs(n).toFixed(1);
  const abs = keepDecimal ? fixed : fixed.replace(/\.0$/, "");
  return `${n < 0 && abs !== "0" ? MINUS : ""}${abs}%`;
}

/** "2026-10" plus n months (n may be negative). Anything that is not a month comes back as it is. */
export function addMonths(month: string, n: number): string {
  const m = /^(\d{4})-(\d{2})$/.exec(month);
  if (!m) return month;
  const index = Number(m[1]) * 12 + Number(m[2]) - 1 + n;
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, "0")}`;
}

// Demo mode pins "today" to the samples' date (/api/auth/me sends it). Set
// before any page renders; a call that passes `now` ignores it.
let pinnedToday: string | null = null;

/** Pins "today" ("YYYY-MM-DD"), or null for the real clock. */
export function setPinnedToday(day: string | null): void {
  pinnedToday = day;
}

/** Today in household time, as "YYYY-MM-DD". */
export function chicagoToday(now?: Date): string {
  if (now === undefined && pinnedToday) return pinnedToday;
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Chicago", year: "numeric", month: "2-digit", day: "2-digit" }).format(
    now ?? new Date(),
  );
}

/** This month in household time (Chicago), as "YYYY-MM". */
export function thisMonth(now?: Date): string {
  if (now === undefined && pinnedToday) return pinnedToday.slice(0, 7);
  now ??= new Date();
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/Chicago", year: "numeric", month: "2-digit" }).formatToParts(now);
  const year = parts.find((p) => p.type === "year")?.value ?? "1970";
  const month = parts.find((p) => p.type === "month")?.value ?? "01";
  return `${year}-${month}`;
}
