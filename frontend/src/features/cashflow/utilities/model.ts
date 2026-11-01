// Pure helpers for the Utilities tab. Usage is integer micro-units from the
// API, cost per unit is thousandths of a cent, percent changes are strings.
// Nothing here does money math beyond formatting: the server computes it.

import { deltaTone, type Tone } from "../../../components/money/Delta";
import type {
  UtilityBill,
  UtilityBillBeforeValue,
  UtilityCostPoint,
  UtilityCreditPoint,
  UtilityService,
  UtilityUnit,
  UtilityYoyRow,
  UtilityYoyValue,
} from "../../../lib/api";
import { addMonths, formatDate, formatMoney, formatShortDate, MINUS } from "../../../lib/format";

export type Span = "1y" | "2y" | "all";
export type UsageMode = "total" | "day";

/**
 * Service to chart color, in one place. Each service is drawn next to a gray
 * last-year bar or line, so only slots that pass the palette validator against
 * `--chart-muted` in both themes are used: amber, purple and green. Pink and
 * red fail next to gray. Gas and sewer share purple, since one utility never
 * bills both in our data (the same rule as the Cash Flow ring).
 */
export const SERVICE_COLORS: Record<UtilityService, string> = {
  electric: "var(--chart-4)",
  gas: "var(--chart-7)",
  water: "var(--chart-6)",
  sewer: "var(--chart-7)",
  stormwater: "var(--chart-4)",
  other: "var(--chart-4)",
};

/** Table headers that hold units keep their case: "kWh", not "KWH". */
export const PLAIN_HEADERS = "[&_thead_th]:normal-case [&_thead_th]:tracking-normal";

/** Last year's bars and the "without fixed charges" line. */
export const CONTEXT_COLOR = "var(--chart-muted)";

/** Credits are money kept, so they wear the left over green. */
export const CREDITS_COLOR = "var(--chart-leftover)";

export const SERVICE_NAMES: Record<UtilityService, string> = {
  electric: "Electric",
  gas: "Gas",
  water: "Water",
  sewer: "Sewer",
  stormwater: "Stormwater",
  other: "Other",
};

const UNIT_NAMES: Record<UtilityUnit, string> = {
  kwh: "kWh",
  therms: "therms",
  gallons: "gallons",
  ccf: "ccf",
  none: "",
};

export function unitName(unit: UtilityUnit): string {
  return UNIT_NAMES[unit];
}

/** "kWh per day", "therms per day". */
export function perDayName(unit: UtilityUnit): string {
  return `${unitName(unit)} per day`;
}

const SERVICE_ORDER: UtilityService[] = ["electric", "gas", "water", "sewer", "stormwater", "other"];

/** Services with a metered unit, in a fixed order: electric, gas, water, sewer, then the rest. */
export function meteredServices<T extends { service: UtilityService; unit: UtilityUnit }>(services: T[]): T[] {
  return services.filter((s) => s.unit !== "none").sort((a, b) => SERVICE_ORDER.indexOf(a.service) - SERVICE_ORDER.indexOf(b.service));
}

/**
 * The metered services on these bills, each with the unit on its newest
 * line. Charts and the year over year table use this unit, and leave out
 * older lines in another unit, so gallons never plot as ccf.
 */
export function servicesInRange(bills: UtilityBill[]): { service: UtilityService; unit: UtilityUnit }[] {
  const units = new Map<UtilityService, UtilityUnit>();
  for (const b of [...bills].sort((x, y) => x.period_end.localeCompare(y.period_end))) {
    for (const l of b.lines) units.set(l.service, l.unit);
  }
  return meteredServices([...units].map(([service, unit]) => ({ service, unit })));
}

const MICRO = 1_000_000;

/** Micro-units as a plain number, for chart scales. */
export function fromMicro(micro: number | null | undefined): number | null {
  return micro === null || micro === undefined ? null : micro / MICRO;
}

const whole = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const one = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const two = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** A usage number with as many decimals as its size needs: "5,642", "19", "24.0", "0.63". */
export function formatUsage(micro: number | null | undefined): string {
  if (micro === null || micro === undefined) return "None";
  const n = micro / MICRO;
  const abs = Math.abs(n);
  if (abs >= 100 || Number.isInteger(n)) return whole.format(n);
  if (abs >= 10) return one.format(n);
  return two.format(n);
}

/** Axis ticks for usage: no decimals past 10. */
export function formatUsageTick(n: number): string {
  return Math.abs(n) >= 10 || Number.isInteger(n) ? whole.format(n) : one.format(n);
}

/** The fewest decimals (up to 4) that show every tick exactly: 0.25 steps need 2. */
export function tickDecimals(ticks: number[]): number {
  for (let d = 0; d < 4; d++) {
    const f = 10 ** d;
    if (ticks.every((t) => Math.abs(t * f - Math.round(t * f)) < 1e-6)) return d;
  }
  return 4;
}

/** A usage tick formatter with as many decimals as the step needs, so ticks never round unevenly. */
export function usageTickFormatter(ticks: number[]): (n: number) => string {
  const d = tickDecimals(ticks);
  const f = new Intl.NumberFormat("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  return (n) => f.format(n);
}

/** A money tick formatter for cents: whole dollars when every tick is one, else cents ("$2.50"). */
export function moneyTickFormatter(ticksCents: number[]): (cents: number) => string {
  const wholeDollars = ticksCents.every((t) => Math.round(t) % 100 === 0);
  return (cents) => (wholeDollars ? formatMoney(Math.round(cents)) : formatMoney(Math.round(cents), { cents: true }));
}

const sig3 = new Intl.NumberFormat("en-US", { maximumSignificantDigits: 3 });

/** Use per heating degree day, to 3 significant digits ("0.0414"), so small numbers keep their change. */
export function formatPerDegreeDay(micro: number | null | undefined): string {
  if (micro === null || micro === undefined) return "None";
  return sig3.format(micro / MICRO);
}

/**
 * Cost per unit as people say it. kWh in cents ("20.7¢"), therms and ccf in
 * dollars ("$1.72"), gallons per 1,000 gallons ("$5.92"), since a cent per
 * gallon is too small to read.
 */
export function costScale(unit: UtilityUnit): { label: string; perMillicents: number } {
  if (unit === "kwh") return { label: "per kWh", perMillicents: 1 };
  if (unit === "gallons") return { label: "per 1,000 gallons", perMillicents: 1000 };
  return { label: `per ${unitName(unit).replace(/s$/, "")}`, perMillicents: 1 };
}

/** Millicents per unit as a display number: cents for kWh, dollars otherwise. */
export function costValue(millicents: number | null, unit: UtilityUnit): number | null {
  if (millicents === null) return null;
  const scaled = millicents * costScale(unit).perMillicents;
  return unit === "kwh" ? scaled / 1000 : scaled / 100_000;
}

export function formatCost(value: number | null, unit: UtilityUnit): string {
  if (value === null) return "None";
  return unit === "kwh" ? `${one.format(value)}¢` : `$${two.format(value)}`;
}

export function formatCostTick(value: number, unit: UtilityUnit): string {
  return unit === "kwh" ? `${whole.format(value)}¢` : `$${two.format(value)}`;
}

/** The `from` month for a span, counted back from the newest period end. Undefined means all bills. */
export function spanFrom(span: Span, lastPeriodEnd: string | null | undefined): string | undefined {
  if (span === "all" || !lastPeriodEnd) return undefined;
  return addMonths(lastPeriodEnd.slice(0, 7), span === "1y" ? -11 : -23);
}

/** "Aug 4 to Sep 3, 2026". */
export function periodText(start: string, end: string): string {
  return `${formatShortDate(start)} to ${formatDate(end)}`;
}

/** Short bill label on the x axis: "Sep 3". Adds the year when bills span years: "Sep '26". */
export function billTick(periodEnd: string, spansYears: boolean): string {
  if (!spansYears) return formatShortDate(periodEnd);
  return `${formatShortDate(periodEnd).split(" ")[0]} '${periodEnd.slice(2, 4)}`;
}

// ---- Usage chart rows ----

export interface UsageRow {
  billKey: string;
  periodStart: string;
  periodEnd: string;
  days: number;
  lastYearStart: string | null;
  lastYearEnd: string | null;
  lastYearDays: number | null;
  /** This bill, in units (or units per day). Null when the line is missing. */
  value: number | null;
  lastYear: number | null;
  /** Micro-units, for exact text. */
  valueMicro: number | null;
  lastYearMicro: number | null;
  changePct: string | null;
  /** The bill before (null with none in 20 days of this bill's start). */
  prevEnd: string | null;
  /** The bill before's value, micro-units. Null with no bill before or another unit. */
  prevMicro: number | null;
  prevChangePct: string | null;
}

/**
 * One row per bill for one service, from the year over year rows (oldest
 * first). With a unit, a line in another unit counts as missing.
 */
export function usageRows(yoy: UtilityYoyRow[], service: UtilityService, mode: UsageMode, unit?: UtilityUnit): UsageRow[] {
  return yoy.map((r) => {
    const s = r.services.find((x) => x.service === service && (unit === undefined || x.unit === unit));
    const v: UtilityYoyValue | undefined = s ? (mode === "day" ? s.per_day_micro : s.usage_micro) : undefined;
    const pv: UtilityBillBeforeValue | undefined = s ? (mode === "day" ? s.per_day_vs_prev : s.usage_vs_prev) : undefined;
    return {
      billKey: r.bill_key,
      periodStart: r.period_start,
      periodEnd: r.period_end,
      days: r.days.value ?? 0,
      lastYearStart: r.last_year_period_start,
      lastYearEnd: r.last_year_period_end,
      lastYearDays: r.days.last_year,
      value: fromMicro(v?.value),
      lastYear: fromMicro(v?.last_year),
      valueMicro: v?.value ?? null,
      lastYearMicro: v?.last_year ?? null,
      changePct: v?.change_pct ?? null,
      prevEnd: r.prev_period_end,
      prevMicro: pv?.previous ?? null,
      prevChangePct: pv?.change_pct ?? null,
    };
  });
}

// ---- Cost chart rows ----

export interface CostRow {
  billKey: string;
  periodEnd: string;
  allIn: number | null;
  exFixed: number | null;
}

export function costRows(cost: UtilityCostPoint[], service: UtilityService, unit: UtilityUnit): CostRow[] {
  return cost.map((c) => {
    const s = c.services.find((x) => x.service === service && x.unit === unit);
    return {
      billKey: c.bill_key,
      periodEnd: c.period_end,
      allIn: costValue(s?.cost_per_unit_millicents ?? null, unit),
      exFixed: costValue(s?.cost_per_unit_ex_fixed_millicents ?? null, unit),
    };
  });
}

// ---- Credits ----

export interface CreditRow {
  billKey: string;
  billDate: string;
  periodEnd: string;
  /** The bill's credit labels, joined. */
  label: string;
  /** The bill's credits added up (negative). */
  amountCents: number;
  /** Credits so far in the range, as a positive amount. */
  soFarCents: number;
}

/** One point per bill: its credits added up, and the running total after it (from the server). */
export function creditRows(credits: UtilityCreditPoint[]): CreditRow[] {
  const rows: CreditRow[] = [];
  for (const c of credits) {
    const last = rows[rows.length - 1];
    if (last && last.billKey === c.bill_key) {
      last.label = `${last.label}, ${c.label}`;
      last.amountCents += c.amount_cents;
      last.soFarCents = -c.running_cents;
    } else {
      rows.push({
        billKey: c.bill_key,
        billDate: c.bill_date,
        periodEnd: c.period_end,
        label: c.label,
        amountCents: c.amount_cents,
        soFarCents: -c.running_cents,
      });
    }
  }
  return rows;
}

// ---- Year over year ----

/**
 * Lower use and lower cost are better. Days and heating degree days are
 * weather and the calendar, so they get no tone.
 */
export function yoyTone(change: string | null, polarity: "lower_is_better" | "none"): Tone {
  if (change === null || polarity === "none") return "neutral";
  return deltaTone(Number(change), "lower_is_better");
}

/** "↑ 24.0%" style text parts. Null when there is no change to show. */
export function changeText(change: string | null): { arrow: string; dir: "up" | "down" | null; text: string } | null {
  if (change === null) return null;
  const n = Number(change);
  if (!Number.isFinite(n)) return null;
  const abs = Math.abs(n).toFixed(1);
  if (abs === "0.0") return { arrow: "", dir: null, text: "No change" };
  return { arrow: n > 0 ? "↑" : "↓", dir: n > 0 ? "up" : "down", text: `${abs}%` };
}

/** Signed percent for tables: "+24.0%", "−1.8%". */
export function signedPct(change: string | null): string {
  if (change === null) return "None";
  const n = Number(change);
  const abs = Math.abs(n).toFixed(1);
  if (abs === "0.0") return "0.0%";
  return `${n > 0 ? "+" : MINUS}${abs}%`;
}

// ---- Bills ----

export interface StatusView {
  tone: "good" | "bad" | "warn" | "neutral";
  text: string;
}

/**
 * Paid status in words. The color always has these words with it. `today`
 * tells a bill not yet due from one whose due date passed before the
 * paying account's statements reached it.
 */
export function billStatus(b: UtilityBill, today?: string): StatusView {
  const p = b.payment;
  const due = formatShortDate(b.due_by);
  if (b.status === "paid" && p) return { tone: "good", text: `Paid ${formatShortDate(p.txn_date)} from ${p.account_name}` };
  if (b.status === "amount_differs" && p) {
    return {
      tone: "warn",
      text: `Paid ${formatMoney(p.amount_cents, { cents: true })} on ${formatShortDate(p.txn_date)} from ${p.account_name}. Not the bill total.`,
    };
  }
  if (b.status === "past_due") {
    return { tone: "bad", text: b.due_date ? `Past due since ${due}` : `No payment by ${due}, 40 days after the bill` };
  }
  if (b.status === "no_history") return { tone: "neutral", text: "Before our payment history" };
  if (today && b.due_by < today) {
    return { tone: "neutral", text: b.due_date ? `No payment in yet. Was due ${due}` : `No payment in yet. Expected by ${due}` };
  }
  return { tone: "neutral", text: b.due_date ? `Not paid yet. Due ${due}` : "Not paid yet" };
}

/**
 * "719 kWh · 19 therms" for a bill's metered lines. Lines that share a unit
 * say their service: "Water 5,642 gallons · Sewer 5,642 gallons". A number
 * and its unit never break apart.
 */
export function usageSummary(b: UtilityBill): string {
  const lines = meteredServices(b.lines);
  return lines
    .map((l) => {
      const shared = lines.filter((x) => x.unit === l.unit).length > 1;
      return `${shared ? `${SERVICE_NAMES[l.service]} ` : ""}${formatUsage(l.usage_micro)}\u00a0${unitName(l.unit)}`;
    })
    .join(" · ");
}

/**
 * "Electric $83.90 · Gas $30.00": dollars per service on a bill, metered or
 * not, in the fixed service order. A name and its amount never break apart.
 */
export function chargesSummary(b: UtilityBill): string {
  return [...b.lines]
    .sort((x, y) => SERVICE_ORDER.indexOf(x.service) - SERVICE_ORDER.indexOf(y.service))
    .map((l) => `${SERVICE_NAMES[l.service]}\u00a0${formatMoney(l.charges_cents, { cents: true })}`)
    .join(" · ");
}

/** The per-service line repeats the total on a single-line bill with no credits. */
export function showCharges(b: UtilityBill): boolean {
  return b.lines.length > 1 || b.credits.length > 0;
}
