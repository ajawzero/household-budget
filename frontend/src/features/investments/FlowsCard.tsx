import { useQuery } from "@tanstack/react-query";
import { Card, CardHeader } from "../../components/ui/card";
import { ErrorBanner } from "../../components/ui/error-banner";
import { Skeleton } from "../../components/ui/skeleton";
import { TableScroll } from "../../components/ui/table-scroll";
import { formatMoney } from "../../lib/format";
import { gainClass, gapText, periodLine, signedMoney, signedPct } from "../accounts/InvestmentPanel";
import { type AccountFlows, getFlows, investmentKeys } from "./api";

const NONE = <span className="sr-only">None</span>;

/**
 * Investments > Holdings: this year's money in, market change, return, fees
 * and dividends per account, from statements (gap audit A5, A6, 54). No totals
 * row: the accounts cover different periods, so a total would mislead.
 */
export function FlowsCard() {
  const q = useQuery({ queryKey: investmentKeys.flows, queryFn: getFlows });
  if (q.isPending) {
    return (
      <Card>
        <Skeleton className="h-32" />
      </Card>
    );
  }
  if (q.isError) {
    return <ErrorBanner message="Could not load this year by account." onRetry={() => void q.refetch()} retrying={q.isFetching} />;
  }
  const rows = q.data.accounts.filter((a) => a.ytd !== null);
  if (rows.length === 0) return null;
  return (
    <Card>
      <CardHeader title="This year by account" subtitle="From statements. Return is the market change only, not money put in." />
      <TableScroll className="-mx-3" label="This year by account">
        <table className="data-table compact">
          <caption className="sr-only">This year by account</caption>
          <thead>
            <tr>
              <th scope="col">Account</th>
              <th scope="col" className="num">
                Money in
              </th>
              <th scope="col" className="num">
                Market
              </th>
              <th scope="col" className="num">
                Return
              </th>
              <th scope="col" className="num hidden md:table-cell">
                Fees
              </th>
              <th scope="col" className="num hidden md:table-cell">
                Dividends
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => (
              <FlowRow key={a.account_key} a={a} />
            ))}
          </tbody>
        </table>
      </TableScroll>
    </Card>
  );
}

function FlowRow({ a }: { a: AccountFlows }) {
  const p = a.ytd!;
  return (
    <tr>
      <th scope="row" className="font-normal">
        <span className="block font-medium text-fg">{a.display_name}</span>
        <span className="block whitespace-nowrap text-xs text-fg-secondary">{periodLine(p)}</span>
        {a.closed && <span className="block text-xs text-fg-secondary">Closed</span>}
        {p.split_statements < p.statements && (
          <span className="block text-xs text-fg-secondary">
            From {p.split_statements} of {p.statements} statements
          </span>
        )}
        {p.gaps > 0 && <span className="block text-xs text-fg-secondary">{gapText(p.gaps)}</span>}
      </th>
      <td className="num">{p.net_flows_cents !== null ? signedMoney(p.net_flows_cents) : NONE}</td>
      <td className="num">
        {p.market_change_cents !== null ? <span className={gainClass(p.market_change_cents)}>{signedMoney(p.market_change_cents)}</span> : NONE}
      </td>
      <td className="num">{p.return_pct !== null ? <span className={gainClass(p.return_pct)}>{signedPct(p.return_pct)}</span> : NONE}</td>
      <td className="num hidden md:table-cell">
        {p.fees_paid_cents !== null ? (
          <>
            {formatMoney(p.fees_paid_cents, { cents: true })}
            {p.fee_months > 1 && p.fees_per_month_cents !== null && (
              <span className="block text-xs text-fg-secondary">{formatMoney(p.fees_per_month_cents, { cents: true })} a month</span>
            )}
            {p.fee_statements < p.statements && (
              <span className="block text-xs text-fg-secondary">
                Fees from {p.fee_statements} of {p.statements} statements
              </span>
            )}
          </>
        ) : (
          NONE
        )}
      </td>
      <td className="num hidden md:table-cell">{p.dividends_cents !== null ? formatMoney(p.dividends_cents, { cents: true }) : NONE}</td>
    </tr>
  );
}
