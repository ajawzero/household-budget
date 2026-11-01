// The payoff steps of backend/app/services/payoff.py, for Try a payment on the
// Debt tab. Keep the two in step: both run payoff-cases.json. A variable rate
// follows its schedule (format 1.4): the first month at a new rate is the
// effective date's month when the date is the 1st, else the month after, and
// only a change whose first month is after the balance's month counts.
import type { DebtLedgerRow } from "../../../lib/api";
import { addMonths } from "../../../lib/format";

export const MAX_MONTHS = 600;

export interface RateChange {
  effectiveOn: string;
  aprMilliPct: number;
}

export interface Terms {
  owedCents: number;
  aprMilliPct: number | null;
  promo: boolean;
  promoMonth: string | null;
  fromMonth: string;
  /** A variable rate's schedule, oldest first (format 1.4). */
  rateChanges: RateChange[];
}

export interface Run {
  months: number | null;
  interestCents: number | null;
  lastPaymentCents: number | null;
  earlyCents: number | null;
}

/** owed x APR / 12, half up to the cent. APR is in thousandths of a percent. */
export function monthInterest(owedCents: number, aprMilliPct: number): number {
  return Math.floor((owedCents * aprMilliPct + 600_000) / 1_200_000);
}

/** The first month at a new rate: the date's own month when it is the 1st, else the month after. */
export function rateChangeMonth(effectiveOn: string): string {
  const month = effectiveOn.slice(0, 7);
  return effectiveOn.slice(8) === "01" ? month : addMonths(month, 1);
}

/**
 * (first month, APR) for each change, oldest first. The server already sends only the
 * changes that count; a step that started by fromMonth applies from month one.
 */
export function rateSteps(t: Pick<Terms, "rateChanges">): { month: string; aprMilliPct: number }[] {
  return t.rateChanges.map((c) => ({ month: rateChangeMonth(c.effectiveOn), aprMilliPct: c.aprMilliPct }));
}

/**
 * 0 for a promo rate through promoMonth (always when it is null), else the APR or the
 * scheduled rate (unknown counts as 0). Two changes that start in the same month: the later wins.
 */
export function rateFor(t: Terms, month: string): number {
  if (t.promo && (t.promoMonth === null || month <= t.promoMonth)) return 0;
  let rate = t.aprMilliPct ?? 0;
  for (const s of rateSteps(t)) if (s.month <= month) rate = s.aprMilliPct;
  return rate;
}

/** Pay the balance month by month, up to the month limit. */
export function simulate(t: Terms, monthlyCents: number, limit = MAX_MONTHS): Run {
  if (t.owedCents <= 0) {
    return { months: 0, interestCents: 0, lastPaymentCents: 0, earlyCents: null };
  }
  if (monthlyCents <= 0) {
    return { months: null, interestCents: null, lastPaymentCents: null, earlyCents: null };
  }

  let owed = t.owedCents;
  let total = 0;
  let before: number | null = null;
  for (let n = 1; n <= limit; n += 1) {
    const charge = monthInterest(owed, rateFor(t, addMonths(t.fromMonth, n)));
    owed += charge;
    total += charge;
    if (owed <= monthlyCents) {
      return { months: n, interestCents: total, lastPaymentCents: owed, earlyCents: before };
    }
    before = owed;
    owed -= monthlyCents;
  }
  return { months: null, interestCents: null, lastPaymentCents: null, earlyCents: null };
}

/** Find the smallest level payment that clears the balance by the given month. */
export function monthlyToFinish(t: Terms, byMonth: string): number {
  const months = Math.max(1, monthIndex(byMonth) - monthIndex(t.fromMonth));
  let lo = 1;
  // Paying the owed plus a month's interest at the highest rate clears it in month one.
  const top = Math.max(t.aprMilliPct ?? 0, ...t.rateChanges.map((c) => c.aprMilliPct));
  let hi = Math.max(1, t.owedCents + monthInterest(t.owedCents, top));
  while (lo < hi) {
    const mid = Math.floor((lo + hi) / 2);
    if (simulate(t, mid, months).months === null) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

/** Return projection terms for a debt row, or null when it has no projection. */
export function termsOf(row: DebtLedgerRow): Terms | null {
  if (row.projection === null) return null;
  const promo = row.rate_type === "promo";
  return {
    owedCents: row.owed_cents,
    aprMilliPct: row.apr_milli_pct,
    promo,
    promoMonth: promo ? row.projection.promo_month : null,
    fromMonth: row.projection.from_month,
    rateChanges: row.rate_changes.map((c) => ({ effectiveOn: c.effective_on, aprMilliPct: c.apr_milli_pct })),
  };
}

function monthIndex(month: string): number {
  const year = Number(month.slice(0, 4));
  const value = Number(month.slice(5, 7));
  return year * 12 + value - 1;
}
