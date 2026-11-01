import type { ReactNode } from "react";
import { deltaTone } from "../../components/money/Delta";
import { TableScroll } from "../../components/ui/table-scroll";
import type { FlowPeriod, InvestmentSummary } from "../../lib/api";
import { formatMoney, formatPct, formatShortDate, type MoneyOptions } from "../../lib/format";

// Kept here, not in lib/format.ts, so this feature does not edit a shared file.

/** "+$500", "−$1,062" and "$0". formatMoney already adds the minus. */
export function signedMoney(cents: number, opts: MoneyOptions = {}): string {
  const text = formatMoney(cents, opts);
  return cents > 0 && text !== "$0" && text !== "$0.00" ? `+${text}` : text;
}

/** "2.1" to "+2.1%", "-1.1" to "−1.1%", "0.0" to "0.0%". */
export function signedPct(pct: string): string {
  const text = formatPct(pct, true);
  return Number(pct) > 0 && text !== "0.0%" ? `+${text}` : text;
}

/** Green for a gain, red for a loss. The sign is always in the text too. */
export function gainClass(value: number | string | null | undefined): string {
  const tone = deltaTone(value === null || value === undefined ? null : Number(value), "higher_is_better");
  return tone === "good" ? "text-good" : tone === "bad" ? "text-bad" : "text-fg";
}

/** "Jul 1 to Sep 30". */
export function periodLine(p: FlowPeriod): string {
  return `${formatShortDate(p.period_start)} to ${formatShortDate(p.period_end)}`;
}

interface PanelRow {
  label: string;
  has: (p: FlowPeriod) => boolean;
  cell: (p: FlowPeriod) => ReactNode;
}

const ROWS: PanelRow[] = [
  {
    label: "Money in",
    has: (p) => p.net_flows_cents !== null,
    cell: (p) => signedMoney(p.net_flows_cents!),
  },
  {
    label: "Market change",
    has: (p) => p.market_change_cents !== null,
    cell: (p) => <span className={gainClass(p.market_change_cents)}>{signedMoney(p.market_change_cents!)}</span>,
  },
  {
    label: "Return",
    has: (p) => p.return_pct !== null,
    cell: (p) => <span className={gainClass(p.return_pct)}>{signedPct(p.return_pct!)}</span>,
  },
  {
    label: "Fees",
    has: (p) => p.fees_paid_cents !== null,
    cell: (p) => (
      <>
        {formatMoney(p.fees_paid_cents!, { cents: true })}
        {p.fee_months > 1 && p.fees_per_month_cents !== null && (
          <span className="block text-xs text-fg-secondary">{formatMoney(p.fees_per_month_cents, { cents: true })} a month</span>
        )}
      </>
    ),
  },
  {
    label: "Dividends",
    has: (p) => p.dividends_cents !== null,
    cell: (p) => formatMoney(p.dividends_cents!, { cents: true }),
  },
];

/**
 * The account drawer's money in and market (gap audit A5, A6, 54): the newest
 * statement and this year, from statements only. It ignores the range picker.
 */
/** What a break between statements means, for a note: "A statement is missing from this year". */
export function gapText(gaps: number): string {
  return gaps === 1 ? "A statement is missing from this year" : "Statements are missing from this year";
}

export function InvestmentPanel({ summary }: { summary: InvestmentSummary }) {
  const periods: [string, FlowPeriod][] = [["Last statement", summary.latest]];
  if (summary.ytd) periods.push(["This year", summary.ytd]);
  const rows = ROWS.filter((r) => periods.some(([, p]) => r.has(p)));
  if (rows.length === 0) return null;
  const shown = new Set(rows.map((r) => r.label));
  const ytd = summary.ytd;
  const feesFrom = ytd ?? summary.latest;
  const notes: string[] = [];
  if (shown.has("Return")) notes.push("Return is the market change only, not money put in.");
  if (ytd && ytd.split_statements > 0 && ytd.split_statements < ytd.statements) {
    notes.push(
      `This year uses ${ytd.split_statements} of ${ytd.statements} statements. The others do not split money in from market change.`,
    );
  }
  if (ytd && ytd.gaps > 0) notes.push(`${gapText(ytd.gaps)}, so these totals are short.`);
  if (!ytd) notes.push(`No statements from ${summary.year} yet.`);
  if (shown.has("Fees")) {
    notes.push(
      "Fees are already taken out of the numbers above." +
        (feesFrom.fee_statements > 0 && feesFrom.fee_statements < feesFrom.statements
          ? ` Fees from ${feesFrom.fee_statements} of ${feesFrom.statements} statements.`
          : ""),
    );
  }
  return (
    <section>
      <h3 className="eyebrow m-0 mb-2">Money in and market</h3>
      <TableScroll label="Money in and market">
        <table className="data-table compact">
          <thead>
            <tr>
              <th scope="col">
                <span className="sr-only">Number</span>
              </th>
              {periods.map(([label, p]) => (
                <th key={label} scope="col" className="num">
                  {label}
                  <span className="block whitespace-nowrap text-xs font-normal text-fg-secondary">{periodLine(p)}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.label}>
                <th scope="row" className="font-normal">
                  {r.label}
                </th>
                {periods.map(([label, p]) => (
                  <td key={label} className="num">
                    {r.has(p) ? r.cell(p) : <span className="text-fg-secondary">Not given</span>}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      {notes.map((n) => (
        <p key={n} className="m-0 mt-2 text-xs text-fg-secondary">
          {n}
        </p>
      ))}
    </section>
  );
}
