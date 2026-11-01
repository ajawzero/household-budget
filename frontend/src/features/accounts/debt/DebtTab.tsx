import { useQuery } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, TriangleAlert } from "lucide-react";
import { Money } from "../../../components/money/Money";
import { Monogram } from "../../../components/money/Monogram";
import { Badge } from "../../../components/ui/badge";
import { Card, CardHeader } from "../../../components/ui/card";
import { EmptyState } from "../../../components/ui/empty-state";
import { ErrorBanner } from "../../../components/ui/error-banner";
import { TableScroll } from "../../../components/ui/table-scroll";
import { type DebtLedgerRow, type DebtPick, type DebtRateChange, type DebtResponse, getDebt, recurringKeys } from "../../../lib/api";
import { cn } from "../../../lib/cn";
import { formatMoney, formatMonth, formatPct, formatShortDate } from "../../../lib/format";
import { aprText } from "../../dashboard/format";
import { otherName } from "../model";
import { KpiStrip, ListSkeleton, SourceLine } from "../../recurring/parts";
import { PayoffCard, rateChangeText } from "./PayoffCard";

const TYPE_TEXT: Record<string, string> = {
  credit_card: "Credit card",
  store_card: "Store card",
  loan: "Loan",
  mortgage: "Mortgage",
  heloc: "HELOC",
};

const AUTOPAY_TEXT: Record<string, string> = {
  minimum: "Autopay: minimum",
  statement: "Autopay: statement balance",
  full: "Autopay: in full",
  none: "No autopay",
};

type RateRow = { apr_milli_pct: number | null; rate_type?: string | null; rate_changes?: DebtRateChange[] };

/** "24.99% variable", or "Rate not known". */
function baseRateText(row: RateRow): string {
  if (row.apr_milli_pct === null) return "Rate not known";
  return `${aprText(row.apr_milli_pct)}${row.rate_type ? ` ${row.rate_type}` : ""}`;
}

/** "19.99% from Nov 2026": the next scheduled rate on a variable rate with a known APR, or null. */
export function scheduledRateText(row: RateRow): string | null {
  const next = row.rate_type === "variable" && row.apr_milli_pct !== null ? row.rate_changes?.[0] : undefined;
  return next ? rateChangeText(next) : null;
}

/** "24.99% variable · 19.99% from Nov 2026", "24.99% variable", or "Rate not known". */
export function rateText(row: RateRow): string {
  const next = scheduledRateText(row);
  return next ? `${baseRateText(row)} · ${next}` : baseRateText(row);
}

/** The desktop rate cell: the same text as rateText, but it can wrap at the dot. */
function RateCell({ row }: { row: RateRow }) {
  const next = scheduledRateText(row);
  return (
    <>
      <span className="whitespace-nowrap">{baseRateText(row)}</span>
      {next && (
        <>
          {" · "}
          <span className="whitespace-nowrap">{next}</span>
        </>
      )}
    </>
  );
}

/** "18.4% paid down of $300,000", or "Owes more than the $300,000 original". Null with no original. */
export function paidDownText(row: Pick<DebtLedgerRow, "original_cents" | "owed_cents" | "paid_down_pct">): string | null {
  if (row.original_cents === null) return null;
  if (row.owed_cents > row.original_cents) return `Owes more than the ${formatMoney(row.original_cents)} original`;
  return `${formatPct(row.paid_down_pct)} paid down of ${formatMoney(row.original_cents)}`;
}

/** Paying vs required: good when it covers the required payment. */
export function payingTone(row: DebtLedgerRow): "good" | "bad" | null {
  if (row.paying_cents === null || row.required_cents === null) return null;
  return row.paying_cents >= row.required_cents ? "good" : "bad";
}

/** Utilization fill by how much of the limit is used. The text says the percent and the level too. */
function utilFill(pct: number): string {
  if (pct >= 50) return "var(--fill-bad)";
  if (pct >= 30) return "var(--fill-warn)";
  return "var(--fill-good)";
}

/** The level word for the warn and bad fills, so the bar's color is never the only signal. */
export function utilLevel(pct: number): "High" | "Very high" | null {
  if (pct >= 50) return "Very high";
  if (pct >= 30) return "High";
  return null;
}

function Progress({ row }: { row: DebtLedgerRow }) {
  if (row.utilization_pct !== null && row.credit_limit_cents) {
    const pct = Number(row.utilization_pct);
    const level = utilLevel(pct);
    return (
      <div className="min-w-28">
        <div className="h-1.5 overflow-hidden rounded-full bg-subtle" aria-hidden="true">
          <span className="block h-full rounded-full" style={{ width: `${Math.min(100, pct)}%`, background: utilFill(pct) }} />
        </div>
        <p className="m-0 mt-1 text-xs text-fg-secondary">
          {formatPct(row.utilization_pct)} of {formatMoney(row.credit_limit_cents)} limit
          {level && (
            <>
              {" · "}
              <span className={cn("inline-flex items-center gap-1", pct >= 50 ? "text-bad" : "text-warn")}>
                <TriangleAlert size={12} aria-hidden="true" />
                {level}
              </span>
            </>
          )}
        </p>
      </div>
    );
  }
  if (row.original_cents !== null && !row.credit_limit_cents) {
    // Progress toward paying off the original loan, not a level: the accent fill, like the Plans bar.
    const over = row.owed_cents > row.original_cents;
    const pct = over ? 0 : Math.min(100, Math.max(0, Number(row.paid_down_pct ?? 0)));
    return (
      <div className="min-w-28">
        <div className="h-1.5 overflow-hidden rounded-full bg-subtle" aria-hidden="true">
          <span className="block h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
        </div>
        <p className={cn("m-0 mt-1 text-xs", over ? "inline-flex items-center gap-1 text-warn" : "text-fg-secondary")}>
          {over && <TriangleAlert size={12} aria-hidden="true" />}
          {paidDownText(row)}
        </p>
        {row.payoff_date && <p className="m-0 mt-0.5 text-xs text-fg-secondary">{payoffText(row)}</p>}
      </div>
    );
  }
  if (row.payoff_date) return <span className="text-sm text-fg-secondary">{payoffText(row)}</span>;
  return <span className="text-sm text-fg-secondary">No limit or end date</span>;
}

/** "Payoff Jun 2049", or "Payoff was Mar 2026" when the date is past but money is still owed. */
export function payoffText(row: Pick<DebtLedgerRow, "payoff_date" | "payoff_ahead">): string {
  if (!row.payoff_date) return "";
  const month = formatMonth(row.payoff_date.slice(0, 7));
  return row.payoff_ahead ? `Payoff ${month}` : `Payoff was ${month}`;
}

/** The phone line: "Interest $922.56 in Aug 2026 · $8,360.86 this year". Null when there is none to show. */
export function interestLine(row: DebtLedgerRow, month: string | null): string | null {
  const parts: string[] = [];
  if (row.interest_cents !== null && month) parts.push(`${formatMoney(row.interest_cents, { cents: true })} in ${formatMonth(month)}`);
  if (row.interest_ytd_cents !== null) parts.push(`${formatMoney(row.interest_ytd_cents, { cents: true })} this year`);
  const any = (row.interest_cents ?? 0) !== 0 || (row.interest_ytd_cents ?? 0) !== 0;
  return any && parts.length ? `Interest ${parts.join(" · ")}` : null;
}

function InterestLine({ row, month }: { row: DebtLedgerRow; month: string | null }) {
  const text = interestLine(row, month);
  return text ? <p className="m-0 mt-2 text-xs text-fg-secondary">{text}</p> : null;
}

function Interest({ row, month }: { row: DebtLedgerRow; month: string | null }) {
  const known = month ? row.interest_cents : null;
  const ytd = row.interest_ytd_cents;
  if (known === null && ytd === null) return <span className="text-fg-secondary">Not known</span>;
  if ((known ?? 0) === 0 && (ytd ?? 0) === 0) return <span className="text-fg-secondary">None</span>;
  return (
    <span className="inline-flex flex-col items-end">
      <Money cents={known} withCents empty="Not known" />
      {month && known !== null && <span className="sr-only"> in {formatMonth(month)}</span>}
      {ytd !== null && <span className="whitespace-nowrap text-xs text-fg-secondary">{formatMoney(ytd, { cents: true })} this year</span>}
    </span>
  );
}

const CARD_TYPES = new Set(["credit_card", "store_card"]);

/** "Statement $432.41 · due Oct 5" for a card, or "was due" once the date is past. Null when nothing is owed on it. */
export function statementText(row: DebtLedgerRow, asOf: string): string | null {
  if (!CARD_TYPES.has(row.type) || !row.statement_due_date || !row.statement_owed_cents || row.statement_owed_cents <= 0) return null;
  const due = row.statement_due_date < asOf ? "was due" : "due";
  return `Statement ${formatMoney(row.statement_owed_cents, { cents: true })} · ${due} ${formatShortDate(row.statement_due_date)}`;
}

function Paying({ row, month }: { row: DebtLedgerRow; month: string | null }) {
  const tone = payingTone(row);
  return (
    <span className="inline-flex flex-col items-end">
      <Money cents={row.paying_cents} withCents empty="None" />
      {tone && (
        <span className={cn("inline-flex items-center gap-1 text-xs", tone === "good" ? "text-good" : "text-bad")}>
          {tone === "good" ? <CircleCheck size={12} aria-hidden="true" /> : <CircleAlert size={12} aria-hidden="true" />}
          {tone === "good" ? "Covers it" : "Below required"}
        </span>
      )}
      {month && <span className="sr-only"> in {formatMonth(month)}</span>}
    </span>
  );
}

function NameCell({ row, asOf, next }: { row: DebtLedgerRow; asOf: string; next: boolean }) {
  const servicer = otherName(row.servicer, row.institution);
  const statement = statementText(row, asOf);
  return (
    <div className="flex min-w-0 items-start gap-3">
      <Monogram name={row.institution} size={40} />
      <div className="min-w-0">
        <p className="m-0 truncate font-medium text-fg">{row.name}</p>
        <p className="m-0 truncate text-sm text-fg-secondary">
          {TYPE_TEXT[row.type] ?? row.type} · {row.institution}
        </p>
        {servicer && <p className="m-0 truncate text-sm text-fg-secondary">Serviced by {servicer}</p>}
        <div className="mt-1 flex flex-wrap gap-1 empty:hidden">
          {row.priority !== null && <Badge tone={next ? "accent" : "neutral"}>{next ? "Next priority" : `Priority ${row.priority}`}</Badge>}
          {/* Free text from the chat: looks like a warn badge, but a long note wraps instead of pushing the row wide. */}
          {row.policy_note && (
            <span className="max-w-full break-words rounded-[10px] bg-tint-warn px-2 py-0.5 text-xs font-medium text-warn">
              {row.policy_note}
            </span>
          )}
          {row.autopay && <Badge>{AUTOPAY_TEXT[row.autopay] ?? `Autopay: ${row.autopay}`}</Badge>}
          {row.plans_remaining_cents ? <Badge>{formatMoney(row.plans_remaining_cents)} in installment plans</Badge> : null}
        </div>
        {statement && <p className="m-0 mt-1 text-xs text-fg-secondary">{statement}</p>}
        {row.notes && <p className="m-0 mt-1 max-w-md break-words text-sm text-fg-secondary">{row.notes}</p>}
      </div>
    </div>
  );
}

/** The lowest priority number set on any debt (ties share it), or null. */
export function nextPriority(ledger: DebtLedgerRow[]): number | null {
  const set = ledger.map((r) => r.priority).filter((p): p is number => p !== null);
  return set.length ? Math.min(...set) : null;
}

function Ledger({ data }: { data: DebtResponse }) {
  const paying = data.paying_month ? formatMonth(data.paying_month) : null;
  const next = nextPriority(data.ledger);
  const isNext = (r: DebtLedgerRow) => r.priority !== null && r.priority === next;
  return (
    <Card>
      <CardHeader
        title="Debts by priority"
        subtitle={`Owed as of the latest statements. Paying is what went in from household accounts${paying ? ` in ${paying}` : ""}, and interest is what was charged. Rate tiles leave out cards paid in full and promo rates.`}
      />
      {data.ledger.length === 0 ? (
        <EmptyState title="No debts." />
      ) : (
        <>
          <TableScroll className="hidden md:block" label="Debts by priority">
            <table className="data-table">
              <caption className="sr-only">Debts by priority</caption>
              <thead>
                <tr>
                  <th scope="col">Debt</th>
                  <th scope="col" className="num">
                    Owed
                  </th>
                  <th scope="col">Rate</th>
                  <th scope="col" className="num">
                    Required
                  </th>
                  <th scope="col" className="num">
                    Paying
                  </th>
                  <th scope="col" className="num">
                    Interest
                  </th>
                  <th scope="col">Progress</th>
                </tr>
              </thead>
              <tbody>
                {data.ledger.map((r) => (
                  <tr key={r.account_key}>
                    <th scope="row" className="font-normal">
                      <NameCell row={r} asOf={data.as_of} next={isNext(r)} />
                    </th>
                    <td className="num">
                      <Money cents={r.owed_cents} withCents />
                    </td>
                    <td>
                      <RateCell row={r} />
                    </td>
                    <td className="num">
                      <Money cents={r.required_cents} withCents empty="None" />
                    </td>
                    <td className="num">
                      <Paying row={r} month={data.paying_month} />
                    </td>
                    <td className="num">
                      <Interest row={r} month={data.paying_month} />
                    </td>
                    <td>
                      <Progress row={r} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
          <ul className="m-0 flex list-none flex-col p-0 md:hidden" aria-label="Debts by priority">
            {data.ledger.map((r) => (
              <li key={r.account_key} className="border-t border-line py-3 first:border-t-0 first:pt-0">
                <div className="flex items-start justify-between gap-3">
                  <NameCell row={r} asOf={data.as_of} next={isNext(r)} />
                  <span className="shrink-0 font-medium text-fg">
                    <Money cents={r.owed_cents} withCents />
                  </span>
                </div>
                <dl className="m-0 mt-2 grid grid-cols-3 gap-2 text-sm">
                  <div className="min-w-0">
                    <dt className="text-xs text-fg-secondary">Rate</dt>
                    <dd className="m-0 truncate text-fg">{r.apr_milli_pct === null ? "Not known" : aprText(r.apr_milli_pct)}</dd>
                  </div>
                  <div className="min-w-0">
                    <dt className="text-xs text-fg-secondary">Required</dt>
                    <dd className="m-0 text-fg">
                      <Money cents={r.required_cents} withCents empty="None" />
                    </dd>
                  </div>
                  <div className="min-w-0">
                    <dt className="text-xs text-fg-secondary">Paying</dt>
                    <dd className={cn("m-0", payingTone(r) === "bad" ? "text-bad" : "text-fg")}>
                      <Money cents={r.paying_cents} withCents empty="None" />
                      {payingTone(r) === "bad" && (
                        <span className="flex items-center gap-1 text-xs text-bad">
                          <CircleAlert size={12} aria-hidden="true" />
                          Below required
                        </span>
                      )}
                    </dd>
                  </div>
                </dl>
                {scheduledRateText(r) && <p className="m-0 mt-2 text-xs text-fg-secondary">Rate {rateText(r)}</p>}
                <InterestLine row={r} month={data.paying_month} />
                <div className="mt-2">
                  <Progress row={r} />
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
      {data.tracked_cards.length > 0 && (
        <p className="m-0 mt-4 border-t border-line pt-3 text-sm text-fg-secondary">
          Tracked by payments, so no balance here:{" "}
          {data.tracked_cards.map((c) => `${c.name}${c.autopay ? ` (${(AUTOPAY_TEXT[c.autopay] ?? c.autopay).toLowerCase()})` : ""}`).join(", ")}.
        </p>
      )}
    </Card>
  );
}

function pickText(p: DebtPick | null | undefined): { value: string; sub?: string } {
  if (!p) return { value: "None" };
  return { value: p.apr_milli_pct === null ? "Not known" : aprText(p.apr_milli_pct), sub: `${p.name}, ${formatMoney(p.owed_cents)} owed` };
}

/**
 * Accounts, Debt tab (PLAN 8.2): total debt, the highest and cheapest rate,
 * a ledger by priority, payoff at this pace and the chat's payoff sequence.
 */
export function DebtTab() {
  const debt = useQuery({ queryKey: recurringKeys.debt, queryFn: getDebt });
  const d = debt.data;
  const high = pickText(d?.highest_rate);
  const low = pickText(d?.cheapest);
  const required = d?.ledger.reduce((s, r) => s + (r.required_cents ?? 0), 0) ?? 0;
  const paying = d?.ledger.reduce((s, r) => s + (r.paying_cents ?? 0), 0) ?? 0;
  return (
    <div className="flex flex-col gap-4 pb-8">
      {debt.isError && !d && <ErrorBanner message="Could not load debts." onRetry={() => void debt.refetch()} retrying={debt.isFetching} />}
      <KpiStrip
        label="Debt totals"
        loading={debt.isPending}
        items={[
          { key: "total", label: "Total debt", value: <Money cents={d?.total_debt_cents} />, sub: d ? `${d.ledger.length} debts` : undefined },
          { key: "high", label: "Highest rate", value: high.value, sub: high.sub },
          { key: "low", label: "Cheapest debt", value: low.value, sub: low.sub },
          {
            key: "paying",
            label: d?.paying_month ? `Paid in ${formatMonth(d.paying_month)}` : "Paid last month",
            value: <Money cents={d ? paying : undefined} />,
            sub: d ? `${formatMoney(required)} required` : undefined,
          },
        ]}
      />
      {d ? <Ledger data={d} /> : <Card>{debt.isPending ? <ListSkeleton /> : <EmptyState title="Debts did not load." />}</Card>}
      {d && <PayoffCard data={d} />}
      <div className="flex min-w-0 flex-col gap-4">
        <div className="flex min-w-0 flex-col gap-4">
          <Card>
            <CardHeader title="Payoff sequence" subtitle="The chat's order for paying things off" />
            {!d ? (
              <ListSkeleton rows={2} />
            ) : d.payoff_sequence.length === 0 ? (
              <EmptyState title="No payoff steps yet." />
            ) : (
              <ol className="m-0 flex list-none flex-col p-0">
                {d.payoff_sequence.map((s, i) => (
                  <li key={s.key} className="flex gap-3 border-t border-line py-3 first:border-t-0 first:pt-0">
                    <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-subtle text-xs font-semibold text-fg">
                      {i + 1}
                    </span>
                    <div className="min-w-0">
                      <p className="m-0 font-medium text-fg">{s.action}</p>
                      <p className="m-0 text-sm text-fg-secondary">{s.when_label}</p>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </Card>
          <SourceLine runId={d?.source_run_id} />
        </div>
      </div>
    </div>
  );
}
