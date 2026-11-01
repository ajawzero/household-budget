// Phase 4 parts of the Checks page: the plan mapping, the payment-basis watch,
// unpaired transfer legs, card payments with no statement, missing statements,
// utility bills (format 1.2) and rewards (format 1.3).
import { CircleAlert, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { Badge } from "../../components/ui/badge";
import { Card, CardHeader } from "../../components/ui/card";
import { TableScroll } from "../../components/ui/table-scroll";
import type { Checks, PlanMapRow } from "../../lib/api";
import { formatDate, formatMoney, formatMonth, formatShortDate } from "../../lib/format";

export function moneyLine(cents: number): string {
  return formatMoney(cents, { cents: true });
}

const MEASURE_TEXT: Record<PlanMapRow["measure"], string> = {
  group: "Spending group",
  saved_invested: "Saved and invested",
  plan_installments: "Plan payments",
  unmapped: "Not mapped",
};

interface Column<T> {
  header: string;
  cell: (row: T) => ReactNode;
  numeric?: boolean;
}

/** A small table that scrolls inside its card on phones. The first column is the row header. */
function SimpleTable<T>({ rows, columns, caption, rowKey }: { rows: T[]; columns: Column<T>[]; caption: string; rowKey: (r: T) => string }) {
  return (
    <TableScroll label={caption}>
      <table className="data-table compact">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.header} scope="col" className={c.numeric ? "num" : undefined}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={rowKey(r)}>
              {columns.map((c, i) =>
                i === 0 ? (
                  <th key={c.header} scope="row" className="font-normal text-fg">
                    {c.cell(r)}
                  </th>
                ) : (
                  <td key={c.header} className={c.numeric ? "num" : undefined}>
                    {c.cell(r)}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}

function Section({
  title,
  subtitle,
  empty,
  footer,
  children,
}: {
  title: string;
  subtitle: string;
  empty: string | null;
  footer?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Card>
      <CardHeader title={title} subtitle={subtitle} />
      {empty ? <p className="m-0 text-sm text-fg-secondary">{empty}</p> : children}
      {footer}
    </Card>
  );
}

export function P4Checks({ data }: { data: Checks }) {
  const watch = data.payment_basis_watch;
  return (
    <>
      <Section
        title="Payment-basis watch"
        subtitle="Cards tracked by their payments have no statements, so these rules watch for trouble."
        empty={watch.length === 0 ? "Nothing to watch. Every rule passes." : null}
      >
        <ul className="m-0 flex list-none flex-col p-0" aria-label="Payment-basis watch">
          {watch.map((w) => (
            <li key={`${w.rule}-${w.account_key}-${w.month ?? ""}`} className="flex gap-3 border-t border-line py-3 first:border-t-0 first:pt-0">
              {w.severity === "high" ? (
                <CircleAlert size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-bad" />
              ) : (
                <TriangleAlert size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-warn" />
              )}
              <div className="min-w-0">
                <p className="m-0 font-medium text-fg">
                  {w.account_name}
                  {w.month && <span className="font-normal text-fg-secondary"> · {formatMonth(w.month)}</span>}
                </p>
                <p className="m-0 text-sm text-fg">{w.message}</p>
              </div>
              <Badge tone={w.severity === "high" ? "bad" : "warn"} className="ml-auto">
                {w.severity === "high" ? "High" : "Medium"}
              </Badge>
            </li>
          ))}
        </ul>
      </Section>

      <Section
        title="Unpaired transfer legs"
        subtitle="Transfers between household accounts where only one side is in the data."
        empty={data.unpaired_transfers.length === 0 ? "None. Every transfer has both legs." : null}
      >
        <SimpleTable
          rows={data.unpaired_transfers}
          caption="Unpaired transfer legs"
          rowKey={(r) => r.txn_key}
          columns={[
            { header: "Date", cell: (r) => <span className="whitespace-nowrap">{formatDate(r.txn_date)}</span> },
            { header: "Account", cell: (r) => r.account_name },
            { header: "Other side", cell: (r) => r.counterparty_name },
            { header: "Amount", cell: (r) => moneyLine(r.amount_cents), numeric: true },
          ]}
        />
      </Section>

      <Section
        title="Card payments with no statement"
        subtitle="Payments to a statement card whose statement for that time is not in. They count as card spending until it is."
        empty={data.card_payments_no_statement.length === 0 ? "None." : null}
      >
        <SimpleTable
          rows={data.card_payments_no_statement}
          caption="Card payments with no statement"
          rowKey={(r) => `${r.account_key}-${r.month}`}
          columns={[
            { header: "Card", cell: (r) => r.account_name },
            { header: "Month", cell: (r) => formatMonth(r.month) },
            { header: "Payments", cell: (r) => String(r.count), numeric: true },
            { header: "Amount", cell: (r) => moneyLine(r.cents), numeric: true },
          ]}
        />
      </Section>

      <Section
        title="Missing statements"
        subtitle="Statements the account's cadence expects that are more than 15 days late."
        empty={data.missing_statements.length === 0 ? "None. Every expected statement is in." : null}
      >
        <SimpleTable
          rows={data.missing_statements}
          caption="Missing statements"
          rowKey={(r) => `${r.account_key}-${r.month}`}
          columns={[
            { header: "Account", cell: (r) => r.name },
            { header: "Month", cell: (r) => formatMonth(r.month) },
          ]}
        />
      </Section>

      <UtilityChecks data={data} />

      <RewardChecks data={data} />

      <Section
        title="Plan mapping"
        subtitle={`How each plan group is compared with actuals${data.plan_mapping_month ? `, from the ${formatMonth(data.plan_mapping_month)} plan` : ""}.`}
        empty={data.plan_mapping.length === 0 ? "No plan to map yet." : null}
      >
        <SimpleTable
          rows={data.plan_mapping}
          caption="Plan mapping"
          rowKey={(r) => r.key}
          columns={[
            { header: "Plan group", cell: (r) => r.label },
            { header: "Measure", cell: (r) => (r.measure === "unmapped" ? <Badge tone="warn">Not mapped</Badge> : MEASURE_TEXT[r.measure]) },
            { header: "Compared with", cell: (r) => r.maps_to },
          ]}
        />
      </Section>
    </>
  );
}

/** Utility bills: past due with no payment, tagged payments with no bill, import warnings. */
function UtilityChecks({ data }: { data: Checks }) {
  const pastDue = data.utility_past_due;
  const noBill = data.utility_payments_no_bill;
  const warnings = data.utility_warnings;
  const none = pastDue.length === 0 && noBill.length === 0 && warnings.length === 0;
  return (
    <Section
      title="Utility bills"
      subtitle="Each bill pairs with a payment tagged with its utility, within 40 days of the bill date."
      empty={none ? (data.utility_bill_count === 0 ? "No utility bills yet." : "Nothing to check. Every bill has its payment.") : null}
      footer={
        <Link to="/cash-flow?tab=utilities" className="mt-3 inline-flex min-h-11 items-center text-sm font-medium md:min-h-0">
          See the bills on Cash Flow
        </Link>
      }
    >
      <div className="flex flex-col gap-5">
        {pastDue.length > 0 && (
          <div>
            <h3 className="m-0 mb-2 text-sm font-medium text-fg">Past due with no payment</h3>
            <SimpleTable
              rows={pastDue}
              caption="Utility bills past due with no payment"
              rowKey={(r) => r.bill_key}
              columns={[
                { header: "Utility", cell: (r) => r.provider },
                { header: "Due", cell: (r) => <span className="whitespace-nowrap">{formatDate(r.due_date)}</span> },
                { header: "Paid from", cell: (r) => r.account_name ?? "Not known" },
                { header: "Total", cell: (r) => moneyLine(r.total_cents), numeric: true },
              ]}
            />
          </div>
        )}
        {noBill.length > 0 && (
          <div>
            <h3 className="m-0 mb-2 text-sm font-medium text-fg">Payments with no bill</h3>
            <SimpleTable
              rows={noBill}
              caption="Utility payments with no bill"
              rowKey={(r) => r.txn_key}
              columns={[
                { header: "Date", cell: (r) => <span className="whitespace-nowrap">{formatDate(r.txn_date)}</span> },
                { header: "Utility", cell: (r) => r.provider },
                { header: "Account", cell: (r) => r.account_name },
                { header: "Amount", cell: (r) => moneyLine(r.amount_cents), numeric: true },
              ]}
            />
          </div>
        )}
        {warnings.length > 0 && (
          <div>
            <h3 className="m-0 mb-2 text-sm font-medium text-fg">Import warnings</h3>
            <ul className="m-0 flex list-none flex-col p-0" aria-label="Utility bill warnings">
              {warnings.map((w) => (
                <li key={`${w.bill_key}-${w.rule}`} className="flex gap-3 border-t border-line py-3 first:border-t-0 first:pt-0">
                  <TriangleAlert size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-warn" />
                  <div className="min-w-0">
                    <p className="m-0 font-medium text-fg">
                      {w.provider}
                      <span className="font-normal text-fg-secondary"> · period ending {formatDate(w.period_end)}</span>
                    </p>
                    <p className="m-0 text-sm text-fg">{w.message}</p>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Section>
  );
}

/** Where the card credit lines link: the review drawer on the Benefits tab. */
export const CREDITS_REVIEW = "/rewards?tab=benefits&review=credits";
/** Benefit notes sit in the Needs a look card on the Benefits tab, program notes on the Points tab. */
export const BENEFIT_NOTES = "/rewards?tab=benefits";
export const PROGRAM_NOTES = "/rewards";

type RewardCounts = NonNullable<Checks["rewards"]>["counts"];

export interface CountLine {
  text: string;
  to: string;
}

/**
 * The card credit lines (docs/plans/rewards-inputs.md 7), each only when its
 * count is above 0, each linking to where its items are: "5 card credits to
 * review (1 matches more than one benefit)", "1 cycle has more credits than
 * its amount", "1 cycle the chat says is used has no credit row" and "2 card
 * credits need a look" open the review; "1 benefit note needs a look" opens
 * the Benefits tab; "1 program note needs a look" opens the Points tab.
 */
export function creditCountLines(c: RewardCounts): CountLine[] {
  const n = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;
  const out: CountLine[] = [];
  const review = (text: string) => out.push({ text, to: CREDITS_REVIEW });
  if (c.to_review > 0) {
    const tie = c.ambiguous > 0 ? ` (${n(c.ambiguous, "matches", "match")} more than one benefit)` : "";
    review(`${n(c.to_review, "card credit", "card credits")} to review${tie}`);
  }
  if (c.over > 0) review(c.over === 1 ? "1 cycle has more credits than its amount" : `${c.over} cycles have more credits than their amount`);
  if (c.chat_says_used > 0) review(`${n(c.chat_says_used, "cycle the chat says is used has", "cycles the chat says are used have")} no credit row`);
  if (c.needs_a_look > 0) review(`${n(c.needs_a_look, "card credit needs", "card credits need")} a look`);
  if (c.benefit_notes > 0) out.push({ text: `${n(c.benefit_notes, "benefit note needs", "benefit notes need")} a look`, to: BENEFIT_NOTES });
  if (c.program_notes > 0) out.push({ text: `${n(c.program_notes, "program note needs", "program notes need")} a look`, to: PROGRAM_NOTES });
  return out;
}

/** Rewards and travel: stale balances, card credits to review, the milestone and cap spend from rows
 * against the chat's numbers, trip keys the app has no trip for, and tagged rows next to trip items. */
function RewardChecks({ data }: { data: Checks }) {
  const r = data.rewards;
  const t = data.trips;
  const credits = r ? creditCountLines(r.counts) : [];
  const tripsNone = t.unknown.length === 0 && t.tagged.every((x) => !x.out_differs && !x.deposits_differ);
  const none = (r === null || (r.stale.length === 0 && r.gaps.length === 0 && credits.length === 0)) && tripsNone && t.tagged.length === 0;
  return (
    <Section
      title="Rewards and travel"
      subtitle="Balances older than 90 days, card credits to review, card spend from rows next to the chat's numbers (within $25 matches), trip tags, and tagged rows next to trip items (within $1 matches)."
      empty={r === null && t.trip_count === 0 && t.unknown.length === 0 ? "No rewards or trip data yet." : none ? "Nothing to check. Every balance is recent." : null}
      footer={
        <div className="mt-3 flex flex-wrap gap-x-4">
          <Link to="/rewards" className="inline-flex min-h-11 items-center text-sm font-medium md:min-h-0">
            See Rewards
          </Link>
          <Link to="/travel" className="inline-flex min-h-11 items-center text-sm font-medium md:min-h-0">
            See Travel
          </Link>
        </div>
      }
    >
      {r && (
        <div className="flex flex-col gap-5">
          {r.stale.length > 0 && (
            <div>
              <h3 className="m-0 mb-2 text-sm font-medium text-fg">Stale numbers</h3>
              <ul className="m-0 flex list-none flex-col p-0" aria-label="Stale reward numbers">
                {r.stale.map((x) => (
                  <li key={`${x.what}-${x.key}`} className="flex gap-3 border-t border-line py-3 first:border-t-0 first:pt-0">
                    <TriangleAlert size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-warn" />
                    <p className="m-0 text-sm text-fg">
                      <span className="font-medium">{x.name}</span>: the {x.what === "balance" ? "balance" : "reported spend"} is from{" "}
                      {formatDate(x.as_of)}, more than 90 days old.
                    </p>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {credits.length > 0 && (
            <div>
              <h3 className="m-0 mb-2 text-sm font-medium text-fg">Card credits</h3>
              <ul className="m-0 flex list-none flex-col p-0" aria-label="Card credits">
                {credits.map((line) => (
                  <li key={line.text} className="flex items-center gap-3 border-t border-line py-0.5 first:border-t-0 first:pt-0 md:py-2">
                    <TriangleAlert size={18} aria-hidden="true" className="shrink-0 text-warn" />
                    <Link to={line.to} className="inline-flex min-h-11 items-center text-sm md:min-h-0">
                      {line.text}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {r.gaps.length > 0 && (
            <div>
              <h3 className="m-0 mb-2 text-sm font-medium text-fg">Milestones and caps</h3>
              <SimpleTable
                rows={r.gaps}
                caption="Card spend from rows next to the chat's numbers"
                rowKey={(g) => `${g.kind}-${g.key}`}
                columns={[
                  { header: "Item", cell: (g) => g.name },
                  { header: "As of", cell: (g) => <span className="whitespace-nowrap">{formatShortDate(g.as_of)}</span> },
                  { header: "App", cell: (g) => moneyLine(g.app_cents), numeric: true },
                  { header: "Chat", cell: (g) => moneyLine(g.chat_cents), numeric: true },
                  {
                    header: "Status",
                    cell: (g) => <Badge tone={g.status === "match" ? "good" : "warn"}>{g.status === "match" ? "Match" : "Differs"}</Badge>,
                  },
                ]}
              />
              <ul className="m-0 mt-2 flex list-none flex-col gap-1 p-0 text-sm text-fg-secondary">
                {r.gaps
                  .filter((g) => g.note)
                  .map((g) => (
                    <li key={`${g.kind}-${g.key}`}>{g.note}</li>
                  ))}
              </ul>
            </div>
          )}
        </div>
      )}
      <TripChecks trips={t} />
    </Section>
  );
}

function TripChecks({ trips }: { trips: Checks["trips"] }) {
  if (trips.unknown.length === 0 && trips.tagged.length === 0) return null;
  return (
    <div className="mt-5 flex flex-col gap-5">
      {trips.unknown.length > 0 && (
        <div>
          <h3 className="m-0 mb-2 text-sm font-medium text-fg">Unknown trips</h3>
          <ul className="m-0 flex list-none flex-col p-0" aria-label="Trip keys the app has no trip for">
            {trips.unknown.map((u) => (
              <li key={u.key} className="flex gap-3 border-t border-line py-3 first:border-t-0 first:pt-0">
                <TriangleAlert size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-warn" />
                <p className="m-0 min-w-0 flex-1 text-sm text-fg">
                  <span className="font-mono break-all">{u.key}</span>: {unknownWhere(u)}{" "}
                  {u.rows + u.plans + u.flags + u.redemptions === 1 ? "names" : "name"}{" "}
                  {u.status === "removed" ? "a trip a later update removed." : "a trip no update has sent."}
                  {u.rows > 0 && (
                    <>
                      {" "}
                      <Link to={`/transactions?trip=${encodeURIComponent(u.key)}`}>See the rows</Link>
                    </>
                  )}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}
      {trips.tagged.length > 0 && (
        <div>
          <h3 className="m-0 mb-2 text-sm font-medium text-fg">Tagged rows and trip items</h3>
          <SimpleTable
            rows={trips.tagged}
            caption="Tagged rows next to posted trip items and settled IOUs"
            rowKey={(x) => x.key}
            columns={[
              {
                header: "Trip",
                cell: (x) => (
                  <Link to={`/travel?trip=${encodeURIComponent(x.key)}`} className="inline-flex min-h-11 items-center md:min-h-0">
                    {x.name}
                  </Link>
                ),
              },
              { header: "Rows out", cell: (x) => moneyLine(x.out_cents), numeric: true },
              { header: "Posted items", cell: (x) => moneyLine(x.posted_cents), numeric: true },
              { header: "Deposits", cell: (x) => moneyLine(x.deposits_cents), numeric: true },
              { header: "Settled to us", cell: (x) => moneyLine(x.settled_to_us_cents), numeric: true },
              {
                header: "Status",
                cell: (x) => {
                  const ok = !x.out_differs && !x.deposits_differ;
                  return <Badge tone={ok ? "good" : "warn"}>{ok ? "Match" : "Differs"}</Badge>;
                },
              },
            ]}
          />
          <p className="m-0 mt-2 text-sm text-fg-secondary">
            Trip items carry the totals. Tagged rows are a check only. Hidden rows and transfers are left out.
          </p>
        </div>
      )}
    </div>
  );
}

/** "1 row and 1 plan purchase". */
export function unknownWhere(u: Checks["trips"]["unknown"][number]): string {
  const n = (count: number, one: string, many: string) => (count > 0 ? [`${count} ${count === 1 ? one : many}`] : []);
  const parts = [
    ...n(u.rows, "row", "rows"),
    ...n(u.plans, "plan purchase", "plan purchases"),
    ...n(u.flags, "flag", "flags"),
    ...n(u.redemptions, "redemption", "redemptions"),
  ];
  return parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : (parts[0] ?? "");
}
