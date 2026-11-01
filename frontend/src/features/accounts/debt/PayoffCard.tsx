import { ChevronDown, CircleAlert, CircleCheck } from "lucide-react";
import { useId, useState } from "react";
import { Button } from "../../../components/ui/button";
import { Card, CardHeader } from "../../../components/ui/card";
import { EmptyState } from "../../../components/ui/empty-state";
import type { DebtLedgerRow, DebtRateChange, DebtResponse } from "../../../lib/api";
import { cn } from "../../../lib/cn";
import { addMonths, formatDate, formatMoney, formatMonth, formatMonthShort } from "../../../lib/format";
import { aprText } from "../../dashboard/format";
import { INPUT_CLASS } from "../vehicles/fields";
import { parseDollarsCents } from "../../settings/vehicles/parse";
import { monthInterest, monthlyToFinish, rateFor, simulate, termsOf } from "./projection";

type Projection = NonNullable<DebtLedgerRow["projection"]>;
type Target = Projection["targets"][number];

/** The last payment line shows only for a payoff within this many months. */
const NEAR_MONTHS = 12;
/** On a card the pace is payments less new charges, so amounts read "pay it down". */
const CARD_TYPES = ["credit_card", "store_card"];

function monthIndex(ym: string): number {
  return Number(ym.slice(0, 4)) * 12 + Number(ym.slice(5, 7)) - 1;
}

/** "Sep 2026", "Jul to Sep 2026", or "Nov 2026 to Jan 2027". */
export function windowText(from: string, through: string): string {
  if (from === through) return formatMonth(through);
  if (from.slice(0, 4) === through.slice(0, 4)) return `${formatMonthShort(from)} to ${formatMonth(through)}`;
  return `${formatMonth(from)} to ${formatMonth(through)}`;
}

/** "1 month", "9 months", "21 years, 2 months". */
export function durationText(months: number): string {
  if (months < 24) return `${months} ${months === 1 ? "month" : "months"}`;
  const years = Math.floor(months / 12);
  const rest = months % 12;
  return `${years} years${rest ? `, ${rest} ${rest === 1 ? "month" : "months"}` : ""}`;
}

/** What goes toward the balance a month, against the next month's interest, and where the number comes from. */
export function paceText(row: DebtLedgerRow): string {
  const p = row.projection!;
  const window = p.pace_from && p.pace_through ? windowText(p.pace_from, p.pace_through) : null;
  const from = window ? (p.pace_basis === "statements" ? ` (${window} statements)` : ` (payments in ${window})`) : "";
  if (p.pace_cents === null) {
    return p.pace_basis === "payments" && window ? `No payments in ${window}` : "No statements or payments to go on yet";
  }
  const terms = termsOf(row)!;
  const up = monthInterest(terms.owedCents, rateFor(terms, addMonths(terms.fromMonth, 1))) - p.pace_cents;
  if (up < 0) return `Pays down about ${formatMoney(p.pace_cents)} a month${from}`;
  if (p.pace_cents <= 0) return up === 0 ? `Not going down${from}` : `Not going down: up about ${formatMoney(up)} a month${from}`;
  const toward = `About ${formatMoney(p.pace_cents)} a month goes toward it`;
  return up === 0 ? `${toward}, all of it interest${from}` : `${toward}, less than the interest: up about ${formatMoney(up)} a month${from}`;
}

/** The promo line: "0% until Mar 31, 2027, then 21.99%". Null when there is no promo ahead. */
export function promoText(row: DebtLedgerRow, asOf: string): string | null {
  const p = row.projection;
  if (!p || row.rate_type !== "promo") return null;
  if (!p.promo_until) return "0% promo with no end date here, so it counts as 0% all along.";
  if (p.promo_until < asOf) return null;
  const after = row.apr_milli_pct === null ? "the card's rate" : aprText(row.apr_milli_pct);
  return `0% until ${formatDate(p.promo_until)}, then ${after}.`;
}

/** "19.99% from Nov 2026": the effective date's month, as the lender gives it. */
export function rateChangeText(c: DebtRateChange): string {
  return `${aprText(c.apr_milli_pct)} from ${formatMonth(c.effective_on.slice(0, 7))}`;
}

/** "Uses the scheduled rate: 19.99% from Nov 2026." Null with no schedule. */
export function scheduleText(row: DebtLedgerRow): string | null {
  if (row.rate_type !== "variable" || row.rate_changes.length === 0) return null;
  const list = row.rate_changes.map(rateChangeText);
  return `Uses the scheduled ${list.length === 1 ? "rate" : "rates"}: ${names(list)}.`;
}

/**
 * "Last payment about $203 in Jan 2027. Or pay about $1,625 in Dec 2026 to finish a month sooner."
 * Cards say "pay it down". Only for a payoff from this month to a year out.
 */
export function lastPaymentText(row: DebtLedgerRow, now: string): string | null {
  const p = row.projection!;
  if (p.months === null || !p.payoff_month || p.last_payment_cents === null) return null;
  const ahead = monthIndex(p.payoff_month) - monthIndex(now);
  if (ahead < 0 || ahead > NEAR_MONTHS) return null;
  const card = CARD_TYPES.includes(row.type);
  const amount = formatMoney(p.last_payment_cents);
  const month = formatMonth(p.payoff_month);
  const last = card ? `Pay it down about ${amount} in ${month} to finish.` : `Last payment about ${amount} in ${month}.`;
  const early = addMonths(p.payoff_month, -1);
  const small = p.pace_cents !== null && p.last_payment_cents * 2 < p.pace_cents;
  if (!small || p.early_cents === null || early < now) return last;
  return `${last} Or ${card ? "pay it down" : "pay"} about ${formatMoney(p.early_cents)} in ${formatMonth(early)} to finish a month sooner.`;
}

/** No payment month is left before the date: the whole balance is due by then. */
function isDue(t: Target, p: Projection): boolean {
  return t.by_month <= p.from_month;
}

/** A date to beat: whether the pace makes it, and what a month does. */
export function targetText(t: Target, p: Projection): string {
  const amount = formatMoney(t.monthly_cents);
  const need = `pay it down about ${amount} a month`;
  if (t.kind === "promo") {
    const ends = `the promo ends ${formatDate(t.date)}`;
    if (isDue(t, p)) return `Pay off the ${amount} before ${ends}.`;
    if (t.on_track === null) return `To clear it before ${ends}, ${need}.`;
    return t.on_track ? `Paid off before ${ends}.` : `Not paid off before ${ends}. P${need.slice(1)} to clear it in time.`;
  }
  const when = formatMonth(t.date.slice(0, 7));
  if (isDue(t, p)) return `Pay off the ${amount} by the chat's payoff date, ${when}.`;
  if (t.on_track === null) return `To make the chat's payoff date, ${when}, ${need}.`;
  if (!t.on_track) return `Behind the chat's payoff date, ${when}. P${need.slice(1)} to make it.`;
  const ahead = p.payoff_month ? monthIndex(t.date.slice(0, 7)) - monthIndex(p.payoff_month) : 0;
  return `On pace for the chat's payoff date, ${when}${ahead > 0 ? ` (about ${durationText(ahead)} sooner)` : ""}.`;
}

/** "A, B and C". */
function names(list: string[]): string {
  return list.length > 1 ? `${list.slice(0, -1).join(", ")} and ${list[list.length - 1]}` : (list[0] ?? "");
}

/** "in 9 months" from this month, "this month", or null for a month gone by. */
export function fromNowText(month: string, now: string): string | null {
  const ahead = monthIndex(month) - monthIndex(now);
  if (ahead < 0) return null;
  return ahead === 0 ? "this month" : `in ${durationText(ahead)}`;
}

function Result({ p, now }: { p: Projection; now: string }) {
  if (p.months !== null && p.payoff_month) {
    const when = fromNowText(p.payoff_month, now);
    return (
      <span className="flex shrink-0 flex-col items-end text-right">
        <span className="font-medium text-fg">
          <span className="sr-only">Paid off about </span>
          {formatMonth(p.payoff_month)}
        </span>
        {when && <span className="text-xs text-fg-secondary">{when}</span>}
      </span>
    );
  }
  const known = p.pace_cents !== null;
  return (
    <span className={cn("inline-flex shrink-0 items-center gap-1 text-sm", known ? "text-warn" : "text-fg-secondary")}>
      {known && <CircleAlert size={14} aria-hidden="true" />}
      {known ? "Not at this pace" : "Not known"}
    </span>
  );
}

function TargetLine({ t, p }: { t: Target; p: Projection }) {
  // No verdict without a pace, unless the whole balance is due.
  const warn = t.on_track === false || isDue(t, p);
  const good = t.on_track === true && !warn;
  return (
    <p className={cn("m-0 mt-1 flex items-start gap-1.5 text-sm", good ? "text-good" : warn ? "text-warn" : "text-fg-secondary")}>
      {good && <CircleCheck size={14} className="mt-0.5 shrink-0" aria-hidden="true" />}
      {warn && <CircleAlert size={14} className="mt-0.5 shrink-0" aria-hidden="true" />}
      <span className="min-w-0 break-words">{targetText(t, p)}</span>
    </p>
  );
}

/** The result for a typed monthly amount. */
export function tryAmountText(row: DebtLedgerRow, text: string, now: string): string | null {
  const terms = termsOf(row);
  const cents = parseDollarsCents(text);
  if (!terms || cents === null) return null;
  if (cents === undefined || cents === 0) return "Type an amount, like 1,500.";
  const run = simulate(terms, cents);
  if (run.months !== null) {
    const interest = run.interestCents ? `, about ${formatMoney(run.interestCents)} interest` : "";
    const payoff = addMonths(terms.fromMonth, run.months);
    const when = fromNowText(payoff, now);
    return `Paid off about ${formatMonth(payoff)}${when ? `, ${when}` : ""}${interest}.`;
  }
  const first = monthInterest(terms.owedCents, rateFor(terms, addMonths(terms.fromMonth, 1)));
  return cents <= first
    ? `Not paid off at that amount: a month's interest is about ${formatMoney(first)}.`
    : "Not paid off within 50 years at that amount.";
}

/** The first month Try a payment can finish by: after the balance's month, and not gone by. */
function firstMonth(p: Projection, now: string): string {
  const next = addMonths(p.from_month, 1);
  return next > now ? next : now;
}

/** The payment a picked month needs. */
export function tryMonthText(row: DebtLedgerRow, month: string, now: string): string | null {
  const terms = termsOf(row);
  if (!terms || month === "") return null;
  if (!/^\d{4}-\d{2}$/.test(month)) return "Pick a month, like 2027-12.";
  const first = firstMonth(row.projection!, now);
  if (month < first) return `Pick ${formatMonth(first)} or later.`;
  return `Pay it down about ${formatMoney(monthlyToFinish(terms, month))} a month to finish by ${formatMonth(month)}.`;
}

function TryPayment({ row, now }: { row: DebtLedgerRow; now: string }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [amount, setAmount] = useState("");
  const [month, setMonth] = useState("");
  const p = row.projection!;
  const amountResult = tryAmountText(row, amount, now);
  const monthResult = tryMonthText(row, month, now);
  return (
    <div className="mt-2">
      <Button size="sm" variant="ghost" className="-ml-3" aria-expanded={open} aria-controls={`${id}-try`} onClick={() => setOpen((v) => !v)}>
        Try a payment <span className="sr-only">{`for ${row.name}`}</span>
        <ChevronDown size={14} className={cn("transition-transform", open && "rotate-180")} aria-hidden="true" />
      </Button>
      {open && (
        <div id={`${id}-try`} className="mt-2 grid gap-3 sm:grid-cols-2">
          <div className="flex min-w-0 flex-col gap-1">
            <label htmlFor={`${id}-amount`} className="text-sm text-fg-secondary">
              Pay down a month<span className="sr-only">{`, ${row.name}`}</span>
            </label>
            <div className="relative">
              <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-secondary" aria-hidden="true">
                $
              </span>
              <input
                id={`${id}-amount`}
                className={`${INPUT_CLASS} pl-6 tabular-nums`}
                inputMode="decimal"
                autoComplete="off"
                placeholder={p.pace_cents && p.pace_cents > 0 ? formatMoney(p.pace_cents).replace("$", "") : "1,500"}
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
              />
            </div>
            <p className="m-0 min-h-5 text-sm text-fg" aria-live="polite">
              {amountResult}
            </p>
          </div>
          <div className="flex min-w-0 flex-col gap-1">
            <label htmlFor={`${id}-month`} className="text-sm text-fg-secondary">
              Or finish by<span className="sr-only">{`, ${row.name}`}</span>
            </label>
            <input
              id={`${id}-month`}
              type="month"
              className={INPUT_CLASS}
              min={firstMonth(p, now)}
              placeholder="2027-12"
              value={month}
              onChange={(e) => setMonth(e.target.value)}
            />
            <p className="m-0 min-h-5 text-sm text-fg" aria-live="polite">
              {monthResult}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

function PayoffRow({ row, asOf }: { row: DebtLedgerRow; asOf: string }) {
  const p = row.projection!;
  const now = asOf.slice(0, 7);
  const promo = promoText(row, asOf);
  const schedule = scheduleText(row);
  const last = lastPaymentText(row, now);
  const old = p.from_month < addMonths(now, -1) ? `Going by the balance from ${formatMonth(p.from_month)}, the newest here.` : null;
  const interest =
    p.months !== null && p.interest_to_go_cents ? `About ${formatMoney(p.interest_to_go_cents)} interest to go.` : null;
  return (
    <li className="border-t border-line py-3 first:border-t-0 first:pt-0">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="m-0 break-words font-medium text-fg">{row.name}</p>
          <p className="m-0 break-words text-sm text-fg-secondary">{paceText(row)}</p>
        </div>
        <Result p={p} now={now} />
      </div>
      {old && <p className="m-0 mt-1 text-sm text-fg-secondary">{old}</p>}
      {interest && <p className="m-0 mt-1 text-sm text-fg-secondary">{interest}</p>}
      {row.apr_milli_pct === null && <p className="m-0 mt-1 text-sm text-fg-secondary">Rate not known, so interest is left out.</p>}
      {schedule && <p className="m-0 mt-1 text-sm text-fg-secondary">{schedule}</p>}
      {promo && <p className="m-0 mt-1 text-sm text-fg-secondary">{promo}</p>}
      {last && <p className="m-0 mt-1 text-sm text-fg-secondary">{last}</p>}
      {p.targets.map((t) => (
        <TargetLine key={t.kind} t={t} p={p} />
      ))}
      <TryPayment row={row} now={now} />
    </li>
  );
}

/**
 * Debt tab, Payoff at this pace (gap audit D1): for each debt that is not a
 * card paid in full, the pace, the payoff month, the dates to beat and Try a
 * payment. The numbers are the server's (services/payoff.py); Try a payment
 * runs the same steps here (projection.ts).
 */
export function PayoffCard({ data }: { data: DebtResponse }) {
  const rows = data.ledger.filter((r) => r.projection);
  const inFull = data.ledger.filter((r) => !r.projection).map((r) => r.name);
  return (
    <Card>
      <CardHeader
        title="Payoff at this pace"
        subtitle="The pace is what went toward each balance a month lately: payments less new charges, with interest. Rates are assumed to stay the same unless a change is scheduled."
      />
      {rows.length === 0 ? (
        <EmptyState title="No balances to project." />
      ) : (
        <ul className="m-0 flex list-none flex-col p-0" aria-label="Payoff at this pace">
          {rows.map((r) => (
            <PayoffRow key={r.account_key} row={r} asOf={data.as_of} />
          ))}
        </ul>
      )}
      {inFull.length > 0 && (
        <p className="m-0 mt-4 border-t border-line pt-3 text-sm text-fg-secondary">Paid in full each month, so nothing to project: {names(inFull)}.</p>
      )}
    </Card>
  );
}
