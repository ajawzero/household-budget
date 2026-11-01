import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { AreaChart, type AreaPoint, ChartCard, type ChartColumn } from "../../components/charts";
import { ESTIMATE_SWATCH_STYLE } from "../../components/charts/AreaChart";
import { AsOf } from "../../components/money/AsOf";
import { Delta } from "../../components/money/Delta";
import { Money } from "../../components/money/Money";
import { Monogram } from "../../components/money/Monogram";
import { Drawer } from "../../components/ui/drawer";
import { ErrorBanner } from "../../components/ui/error-banner";
import { RangePicker, rangeLabel } from "../../components/ui/range-picker";
import { TableScroll } from "../../components/ui/table-scroll";
import {
  getAccount,
  queryKeys,
  type AccountAbout,
  type AccountDetail,
  type AccountTerms,
  type Holding,
  type RangeKey,
  type StatementSummary,
  type Transaction,
} from "../../lib/api";
import { cn } from "../../lib/cn";
import { formatDate, formatMoney, formatShortDate } from "../../lib/format";
import { EquityLine, RowEstBadge } from "./AccountRowItem";
import { InvestmentPanel, signedMoney } from "./InvestmentPanel";
import { balanceState, isOwedGroup, metaLine, monogramName, otherName, shownCents } from "./model";
import { useEquityNote } from "./vehicles/EquityNotes";
import { HomePanel } from "./vehicles/HomePanel";
import { VehiclePanel } from "./vehicles/VehiclePanel";

interface AccountDrawerProps {
  accountKey: string | null;
  /** The page's range, used as the starting range. */
  range: RangeKey;
  onClose: () => void;
}

/** Account detail: balance chart, statements, recent transactions, holdings and terms. */
export function AccountDrawer({ accountKey, range, onClose }: AccountDrawerProps) {
  return (
    <Drawer
      open={accountKey !== null}
      onOpenChange={(open) => !open && onClose()}
      title={<DrawerTitle accountKey={accountKey} range={range} />}
      widthClass="md:w-[560px]"
    >
      {accountKey && <DrawerBody key={accountKey} accountKey={accountKey} initialRange={range} />}
    </Drawer>
  );
}

function useAccount(accountKey: string | null, range: RangeKey) {
  return useQuery({
    queryKey: queryKeys.account(accountKey ?? "", range),
    queryFn: () => getAccount(accountKey!, range),
    enabled: accountKey !== null,
    placeholderData: keepPreviousData,
  });
}

function DrawerTitle({ accountKey, range }: { accountKey: string | null; range: RangeKey }) {
  const q = useAccount(accountKey, range);
  return <>{q.data?.account.display_name ?? "Account"}</>;
}

function DrawerBody({ accountKey, initialRange }: { accountKey: string; initialRange: RangeKey }) {
  const [range, setRange] = useState<RangeKey>(initialRange);
  const q = useAccount(accountKey, range);
  if (q.isPending) return <p className="text-fg-secondary">Loading…</p>;
  if (q.isError) {
    return <ErrorBanner message="Could not load this account." onRetry={() => void q.refetch()} retrying={q.isFetching} />;
  }
  const d = q.data;
  return (
    <div className="flex flex-col gap-6">
      <Header detail={d} range={range} />
      {d.account.group === "vehicles" && <VehiclePanel accountKey={d.account.account_key} />}
      {d.account.type === "property" && <HomePanel accountKey={d.account.account_key} />}
      <BalanceChart detail={d} range={range} onRangeChange={setRange} refreshing={q.isPlaceholderData} />
      {d.investment && <InvestmentPanel summary={d.investment} />}
      <Statements detail={d} />
      <RecentTransactions rows={d.recent_transactions} />
      {d.holdings.length > 0 && <Holdings rows={d.holdings} />}
      <Terms terms={d.terms} isAsset={d.account.is_asset} />
      <About about={d.about} institution={d.account.institution} />
    </div>
  );
}

function Header({ detail, range }: { detail: AccountDetail; range: RangeKey }) {
  const a = detail.account;
  const owed = isOwedGroup(a.group);
  const state = balanceState(a);
  const equityNote = useEquityNote(a.account_key);
  return (
    <div className="flex items-start gap-3">
      <Monogram name={monogramName(a)} />
      <div className="min-w-0 flex-1">
        <p className="m-0 text-sm text-fg-secondary">
          {metaLine(a)}
        </p>
        <p className="m-0 mt-1 text-3xl font-semibold text-fg">
          {state === "tracked" ? (
            <span className="text-base font-normal text-fg-secondary">Tracked by payments</span>
          ) : (
            <>
              <Money cents={a.balance_cents === null ? null : shownCents(a.balance_cents, a.group)} proportional />
              {owed && a.balance_cents !== null && <span className="ml-2 text-base font-normal text-fg-secondary">owed</span>}
              {a.balance_cents !== null && <RowEstBadge row={a} className="ml-2 align-middle" />}
            </>
          )}
        </p>
        <div className="mt-1 flex flex-wrap items-baseline gap-x-3">
          {state === "balance" && (
            <AsOf
              date={a.as_of}
              stale={a.stale}
              label={a.estimate && a.estimate.based_on === a.as_of ? "from" : undefined}
              staleText={a.source === "estimate" ? "The monthly run's estimate is out of date." : undefined}
              showStaleText
            />
          )}
          {state === "closed" && <span className="text-sm text-fg-secondary">Closed</span>}
          {a.dormant && state !== "closed" && <span className="text-sm text-fg-secondary">Dormant</span>}
          {a.change_cents !== null && (
            <Delta cents={a.change_cents} display={owed ? "owed" : "signed"} period={`${rangeLabel(range)} change`} />
          )}
        </div>
        {/* The tooltips do not open on a tap, so the drawer says them in words too. */}
        {a.estimate && a.group !== "vehicles" && a.type !== "property" && <p className="m-0 mt-2 text-sm text-fg-secondary">{a.estimate.note}</p>}
        {!a.estimate && a.source === "estimate" && a.balance_cents !== null && (
          <p className="m-0 mt-2 text-sm text-fg-secondary">Estimated by the monthly run, not from a statement.</p>
        )}
        {a.equity && (
          <div className="mt-2">
            <EquityLine equity={a.equity} note={equityNote} />
            {/* The home panel gives the value's note but not the loan's. Vehicles list both in their panel. */}
            {a.type === "property" && equityNote && <p className="m-0 mt-1 text-sm text-fg-secondary">{equityNote}</p>}
          </div>
        )}
        {/* The chat's own status text, as is. */}
        {detail.about.notes && <p className="m-0 mt-2 break-words text-sm text-fg">{detail.about.notes}</p>}
      </div>
    </div>
  );
}

const POINT_COLUMNS: ChartColumn<AreaPoint>[] = [
  { header: "Date", cell: (p) => formatDate(p.date) },
  { header: "Balance", cell: (p) => formatMoney(p.value), numeric: true },
  { header: "Basis", cell: (p) => (p.estimated ? (p.real ? "Real value plus an estimated part" : "Estimate") : p.real ? "Real value" : "") },
];

function BalanceChart({
  detail,
  range,
  onRangeChange,
  refreshing,
}: {
  detail: AccountDetail;
  range: RangeKey;
  onRangeChange: (r: RangeKey) => void;
  refreshing: boolean;
}) {
  const a = detail.account;
  const owed = isOwedGroup(a.group);
  const points: AreaPoint[] = detail.points.map((p) => ({
    date: p.date,
    value: shownCents(p.balance_cents, a.group),
    estimated: p.estimated,
    real: p.real,
  }));
  const anyEstimate = points.some((p) => p.estimated);
  // Cash accounts have a statement nearly every day of a period: no dots there.
  const dots = anyEstimate || a.group === "real_estate" || a.group === "vehicles";
  const first = points[0];
  const last = points[points.length - 1];
  const label = owed ? "Balance owed" : "Balance";
  const aria =
    first && last
      ? `${label} from ${formatMoney(first.value)} on ${formatShortDate(first.date)} to ${formatMoney(last.value)} on ${formatShortDate(last.date)}`
      : label;
  return (
    <ChartCard
      eyebrow={label}
      // Full width on phones, like the net worth card.
      controls={<RangePicker value={range} onChange={onRangeChange} className="w-full md:w-auto *:flex-1 md:*:flex-none" />}
      controlsClassName="w-full md:w-auto"
      ariaLabel={aria}
      data={points}
      columns={POINT_COLUMNS}
      rowKey={(p) => p.date}
      tableCaption={`${a.display_name}, ${label.toLowerCase()} by day`}
      refreshing={refreshing}
      emptyText="No balances in this range."
      height={180}
      className="p-0! shadow-none!"
      footer={
        anyEstimate ? (
          <p className="m-0 mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-fg-secondary">
            <span className="inline-flex items-center gap-2">
              <span aria-hidden="true" className="h-2 w-2 rounded-full bg-accent" />
              Real values
            </span>
            <span className="inline-flex items-center gap-2">
              <span aria-hidden="true" className="inline-block h-3 w-4 rounded-sm border border-line-strong" style={ESTIMATE_SWATCH_STYLE} />
              Estimate
            </span>
          </p>
        ) : undefined
      }
    >
      <AreaChart points={points} label={label} height={180} showYAxis estimateBands="all" realDots={dots} />
    </ChartCard>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h3 className="eyebrow m-0 mb-2">{title}</h3>
      {children}
    </section>
  );
}

function Statements({ detail }: { detail: AccountDetail }) {
  const group = detail.account.group;
  const rows: StatementSummary[] = detail.statements;
  // Investment statements split money in from market change. Wide screens only.
  const split = rows.some((s) => s.market_change_cents !== null);
  return (
    <Section title="Statements">
      {rows.length === 0 ? (
        <p className="m-0 text-sm text-fg-secondary">No statements yet.</p>
      ) : (
        <TableScroll label="Statements">
          <table className="data-table compact">
            <thead>
              <tr>
                <th scope="col">Statement</th>
                <th scope="col" className="num">
                  {isOwedGroup(group) ? "Owed" : "Balance"}
                </th>
                {split && (
                  <>
                    <th scope="col" className="num hidden md:table-cell">
                      Money in
                    </th>
                    <th scope="col" className="num hidden md:table-cell">
                      Market
                    </th>
                  </>
                )}
                <th scope="col" className="num hidden md:table-cell">
                  Rows
                </th>
                <th scope="col">Check</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.period_end}>
                  <th scope="row" className="font-normal">
                    <span className="whitespace-nowrap">{formatDate(s.period_end)}</span>
                    <span className="block whitespace-nowrap text-xs text-fg-secondary">from {formatShortDate(s.period_start)}</span>
                  </th>
                  <td className="num">{formatMoney(shownCents(s.balance_cents ?? s.closing_cents, group), { cents: true })}</td>
                  {split && (
                    <>
                      <td className="num hidden md:table-cell">{flowCell(s.net_flows_cents)}</td>
                      <td className="num hidden md:table-cell">{flowCell(s.market_change_cents)}</td>
                    </>
                  )}
                  <td className="num hidden md:table-cell">{s.txn_count}</td>
                  <td className={cn("whitespace-nowrap", s.reconciled ? "text-fg-secondary" : "text-warn")}>
                    {s.reconciled ? "Adds up" : "Does not add up"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
    </Section>
  );
}

function flowCell(cents: number | null): ReactNode {
  return cents === null ? <span className="text-fg-secondary">Not given</span> : signedMoney(cents, { cents: true });
}

function RecentTransactions({ rows }: { rows: Transaction[] }) {
  return (
    <Section title="Recent transactions">
      {rows.length === 0 ? (
        <p className="m-0 text-sm text-fg-secondary">No transactions yet.</p>
      ) : (
        <ul className="m-0 list-none p-0">
          {rows.map((t) => (
            <li key={t.txn_key} className="flex items-center gap-3 border-b border-line py-2 last:border-b-0">
              <span className="w-14 shrink-0 text-sm text-fg-secondary">{formatShortDate(t.txn_date)}</span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm text-fg">{t.merchant || t.description}</span>
                <span className="block truncate text-xs text-fg-secondary">{t.category_name ?? "Uncategorized"}</span>
              </span>
              <Money cents={t.amount_cents} withCents flow className="text-sm" />
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function Holdings({ rows }: { rows: Holding[] }) {
  return (
    <Section title="Holdings">
      <TableScroll label="Holdings">
        <table className="data-table compact">
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col" className="num">
                Quantity
              </th>
              <th scope="col" className="num">
                Price
              </th>
              <th scope="col" className="num">
                Value
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((h) => (
              <tr key={h.key}>
                <th scope="row" className="font-normal">
                  {h.name}
                  {h.symbol && <span className="block text-xs text-fg-secondary">{h.symbol}</span>}
                </th>
                <td className="num">{h.quantity ?? ""}</td>
                <td className="num">{h.price ? formatPrice(h.price) : ""}</td>
                <td className="num">{formatMoney(h.value_cents, { cents: true })}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </Section>
  );
}

const AUTOPAY: Record<string, string> = {
  minimum: "Minimum payment",
  statement: "Statement balance",
  full: "Full balance",
  none: "Off",
};

/** APR from apr_milli_pct (thousandths of a percent) to a string like "24.99%". */
export function formatApr(milli: number): string {
  return `${(milli / 1000).toFixed(3).replace(/\.?0+$/, "")}%`;
}

/** Deposit accounts earn an APY. Debts charge an APR. The export keeps both in apr. */
function Terms({ terms, isAsset }: { terms: AccountTerms; isAsset: boolean }) {
  const items: [string, string][] = [];
  if (terms.apr_milli_pct !== null) {
    items.push([isAsset ? "APY" : "APR", formatApr(terms.apr_milli_pct) + (terms.rate_type ? `, ${terms.rate_type}` : "")]);
  }
  if (terms.original_cents !== null) items.push(["Original amount", formatMoney(terms.original_cents)]);
  if (terms.credit_limit_cents !== null) items.push(["Credit limit", formatMoney(terms.credit_limit_cents)]);
  if (terms.min_payment_cents !== null) items.push(["Minimum payment", formatMoney(terms.min_payment_cents, { cents: true })]);
  if (terms.autopay) items.push(["Autopay", AUTOPAY[terms.autopay] ?? terms.autopay]);
  if (terms.payoff_date) items.push(["Payoff date", formatDate(terms.payoff_date)]);
  if (terms.priority !== null) items.push(["Payoff priority", String(terms.priority)]);
  if (terms.policy_note) items.push(["Note", terms.policy_note]);
  if (!items.length) return null;
  return (
    <Section title="Terms">
      <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
        {items.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-fg-secondary">{k}</dt>
            <dd className="m-0 text-fg">{v}</dd>
          </div>
        ))}
      </dl>
    </Section>
  );
}

/** Registry facts from the chat. A custodian or servicer that is the institution again is left out. */
function About({ about, institution }: { about: AccountAbout; institution: string }) {
  const rows: [string, string | null][] = [
    ["Custodian", otherName(about.custodian, institution)],
    ["Managed by", about.advisor?.trim() || null],
    ["Servicer", otherName(about.servicer, institution)],
    ["Opened", about.opened_on ? formatDate(about.opened_on) : null],
    ["Closed", about.closed_on ? formatDate(about.closed_on) : null],
  ];
  const items = rows.filter((r): r is [string, string] => r[1] !== null);
  if (!items.length) return null;
  return (
    <Section title="About">
      <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
        {items.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-fg-secondary">{k}</dt>
            <dd className="m-0 min-w-0 break-words text-fg">{v}</dd>
          </div>
        ))}
      </dl>
    </Section>
  );
}

const PRICE = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 4 });

/** A decimal price string, for display only. */
function formatPrice(price: string): string {
  const n = Number(price);
  return Number.isFinite(n) ? PRICE.format(n) : price;
}
