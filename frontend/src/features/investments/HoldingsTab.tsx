import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ClockAlert, TriangleAlert } from "lucide-react";
import { AsOf } from "../../components/money/AsOf";
import { EstBadge } from "../../components/money/EstBadge";
import { Money } from "../../components/money/Money";
import { Badge } from "../../components/ui/badge";
import { Card, CardHeader } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorBanner } from "../../components/ui/error-banner";
import { SegmentedControl, type SegmentOption } from "../../components/ui/segmented-control";
import { Skeleton } from "../../components/ui/skeleton";
import { TableScroll } from "../../components/ui/table-scroll";
import { Tooltip } from "../../components/ui/tooltip";
import { cn } from "../../lib/cn";
import { formatPct, formatShortDate } from "../../lib/format";
import { gainClass, signedMoney, signedPct } from "../accounts/InvestmentPanel";
import {
  classLabel,
  formatPrice,
  formatQuantity,
  getHoldings,
  getPricesStatus,
  type GroupBy,
  type HoldingGroup,
  type HoldingRow,
  investmentKeys,
  MODE_HELP,
  MODE_LABELS,
  type PriceMode,
} from "./api";
import { PriceStatusLine } from "./PriceStatus";

const GROUP_OPTIONS: SegmentOption<GroupBy>[] = [
  { value: "account", label: "By account" },
  { value: "class", label: "By class" },
];

/** Holdings (PLAN 8.2): each account's or class's holdings with price, value and flags. */
export function HoldingsTab({ groupBy, onGroupByChange }: { groupBy: GroupBy; onGroupByChange: (g: GroupBy) => void }) {
  const q = useQuery({
    queryKey: investmentKeys.holdings(groupBy),
    queryFn: () => getHoldings(groupBy),
    placeholderData: keepPreviousData,
  });
  const status = useQuery({ queryKey: investmentKeys.pricesStatus, queryFn: getPricesStatus });
  const d = q.data;
  const rows = d?.groups.flatMap((g) => g.holdings) ?? [];

  return (
    <section aria-labelledby="holdings-title" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        {/* Grows beside the toggle, so the mode key wraps instead of pushing the toggle down. */}
        <div className="min-w-0 flex-1 basis-72">
          <h2 id="holdings-title" className="m-0 text-lg font-medium text-fg">
            Holdings
          </h2>
          {d && d.groups.length > 0 && <PriceStatusLine rows={rows} status={status.data} />}
          {d && d.groups.length > 0 && <ModeKey rows={rows} />}
        </div>
        <SegmentedControl label="Group holdings" value={groupBy} options={GROUP_OPTIONS} onChange={onGroupByChange} variant="pill" />
      </div>
      {q.isError && !d && <ErrorBanner message="Could not load holdings." onRetry={() => void q.refetch()} retrying={q.isFetching} />}
      {q.isPending && (
        <Card>
          <Skeleton className="h-40" />
        </Card>
      )}
      {d && d.groups.length === 0 && (
        <Card>
          <EmptyState title="No holdings yet.">They come in with brokerage and retirement statements.</EmptyState>
        </Card>
      )}
      <div className={cn("flex flex-col gap-4 transition-opacity duration-100", q.isPlaceholderData && "opacity-60")}>
        {d?.groups.map((g) => <GroupCard key={g.key} group={g} groupBy={d.group_by} />)}
      </div>
    </section>
  );
}

function GroupCard({ group, groupBy }: { group: HoldingGroup; groupBy: GroupBy }) {
  const byClass = groupBy === "class";
  const title = byClass ? classLabel(group.key, group.label) : group.label;
  const count = group.holdings.length;
  const subtitle = byClass ? (
    `${count} ${count === 1 ? "holding" : "holdings"}`
  ) : (
    <>
      <AsOf date={group.statement_date} />
      {group.dormant && <span className="text-sm text-fg-secondary"> · Dormant</span>}
    </>
  );
  const estimatedHolding = group.holdings.find((h) => h.estimated);
  const concentrated = group.holdings.some((h) => h.concentrated);
  return (
    <Card>
      <CardHeader
        className="mb-2"
        title={title}
        subtitle={subtitle}
        actions={
          <span className="inline-flex items-center gap-2 text-lg font-semibold text-fg">
            {group.estimated && (
              <EstBadge priceDate={estimatedHolding?.price_date} statementDate={estimatedHolding?.statement_date ?? group.statement_date} />
            )}
            <Money cents={group.total_cents} />
          </span>
        }
      />
      {!byClass && <GainLine group={group} />}
      {group.notes && <p className="m-0 mb-3 break-words text-sm text-fg-secondary">{group.notes}</p>}
      <TableScroll className="-mx-3" label={`Holdings in ${title}`}>
        <table className="data-table compact table-fixed">
          <caption className="sr-only">Holdings in {title}</caption>
          <colgroup>
            <col />
            <col className="hidden w-32 md:table-column" />
            <col className="hidden w-36 md:table-column" />
            <col className="w-36 md:w-44" />
          </colgroup>
          <thead>
            <tr>
              <th scope="col">Holding</th>
              <th scope="col" className="num hidden md:table-cell">
                Quantity
              </th>
              <th scope="col" className="num hidden md:table-cell">
                Price
              </th>
              <th scope="col" className="num">
                Value
              </th>
            </tr>
          </thead>
          <tbody>
            {group.holdings.map((h) => (
              <HoldingRowItem key={`${h.account_key}:${h.holding_key}`} h={h} showAccount={byClass} />
            ))}
          </tbody>
        </table>
      </TableScroll>
      {concentrated && (
        <p className="m-0 mt-2 text-xs text-fg-secondary">Over 20% marks a holding worth more than 20% of its account. Cash never gets it.</p>
      )}
    </Card>
  );
}

/** "Gain on cost: +$795 (+2.3%)." for an account, with what it leaves out. */
function GainLine({ group }: { group: HoldingGroup }) {
  if (group.gain_cents === null) return null;
  const partial = group.basis_holdings > 0 && group.no_basis_holdings > 0;
  return (
    <p className="m-0 mb-2 text-sm text-fg-secondary">
      Gain on cost:{" "}
      <span className={cn("font-medium tabular", gainClass(group.gain_cents))}>
        {signedMoney(group.gain_cents)}
        {group.gain_pct !== null && ` (${signedPct(group.gain_pct)})`}
      </span>
      .
      {partial &&
        ` For ${group.basis_holdings} of ${group.basis_holdings + group.no_basis_holdings} holdings. The others have no cost basis.`}
      {group.account_type === "529" && group.gain_share_pct !== null && ` Gains are ${formatPct(group.gain_share_pct, true)} of the account.`}
    </p>
  );
}

/** "Oct 20" never splits across lines on a phone. */
function noBreak(text: string): string {
  return text.replace(/ /g, "\u00a0");
}

/** "price as of Jun 12" in the warning color with a clock when stale, else the price date. */
function PriceDate({ h }: { h: HoldingRow }) {
  if (h.price_missing) {
    return <span className="text-warn">{noBreak("No price yet")}</span>;
  }
  if (h.mode === "constant") return <span className="text-fg-secondary">Fixed</span>;
  // A daily price needs a share count. Statements for trusts often print none.
  if (h.mode === "api" && h.quantity_micro === null) {
    return <span className="text-warn">No share count, so it keeps the statement value</span>;
  }
  if (!h.price_date) return null;
  if (h.mode === "statement") return <span className="text-fg-secondary">{noBreak(formatShortDate(h.price_date))} statement</span>;
  if (h.price_stale) {
    return (
      <span className="inline-flex items-center gap-1 text-warn">
        <ClockAlert size={12} aria-hidden="true" className="shrink-0" />
        price as of {noBreak(formatShortDate(h.price_date))}
        <span className="sr-only">, stale</span>
      </span>
    );
  }
  return <span className="text-fg-secondary">{noBreak(formatShortDate(h.price_date))}</span>;
}

function priceMicro(h: HoldingRow): number | null {
  if (h.mode === "constant") return h.price_micro ?? 1_000_000;
  return h.price_micro;
}

export function HoldingRowItem({ h, showAccount }: { h: HoldingRow; showAccount: boolean }) {
  const price = priceMicro(h);
  const qty = formatQuantity(h.quantity_micro);
  return (
    <tr>
      <th scope="row" className="font-normal">
        <span className="block font-medium text-fg">{h.name}</span>
        <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-fg-secondary">
          {h.symbol && <span className="font-medium tabular text-fg-secondary">{h.symbol}</span>}
          <ModeBadge h={h} />
          {h.concentrated && (
            <Badge tone="warn" icon={<TriangleAlert size={12} aria-hidden="true" />}>
              Over 20%
            </Badge>
          )}
          {showAccount && <span>{h.account_name}</span>}
          {/* By class, no subtitle gives the statement date an estimate starts from. */}
          {showAccount && h.estimated && h.statement_date && (
            <span>estimated since the {noBreak(formatShortDate(h.statement_date))} statement</span>
          )}
        </span>
        {/* Phones: quantity and price under the name, since those columns are hidden. */}
        {(qty || price !== null || h.mode === "api") && (
          <span className="mt-1 block text-xs text-fg-secondary md:hidden">
            {qty && `${qty} sh`}
            {qty && price !== null && " at "}
            {price !== null && formatPrice(price)}
            {h.mode === "api" && (
              <>
                {(qty || price !== null) && " · "}
                <PriceDate h={h} />
              </>
            )}
          </span>
        )}
      </th>
      <td className="num hidden md:table-cell">{qty || <span className="sr-only">None</span>}</td>
      <td className="num hidden md:table-cell">
        {price !== null ? formatPrice(price) : <span className="sr-only">None</span>}
        <span className="block text-xs">
          <PriceDate h={h} />
        </span>
      </td>
      <td className="num">
        <span className="inline-flex items-center gap-1.5">
          {h.estimated && <EstBadge priceDate={h.price_date} statementDate={h.statement_date} />}
          <Money cents={h.value_cents} />
        </span>
        {h.share_of_account_pct !== null && (
          <span className={cn("block text-xs", h.concentrated ? "text-warn" : "text-fg-secondary")}>
            {formatPct(h.share_of_account_pct, true)} of account
          </span>
        )}
        {h.gain_cents !== null && (
          <span className={cn("block text-xs", gainClass(h.gain_cents))}>
            {signedMoney(h.gain_cents)}
            {h.gain_pct !== null && <span className="hidden md:inline"> ({signedPct(h.gain_pct)})</span>} on cost
          </span>
        )}
      </td>
    </tr>
  );
}

const MODE_ORDER: PriceMode[] = ["api", "constant", "statement"];

/** What each price mode on screen means, in words (phones have no hover). */
function ModeKey({ rows }: { rows: HoldingRow[] }) {
  const modes = MODE_ORDER.filter((m) => rows.some((h) => h.mode === m));
  const set = rows.some((h) => h.mode_source === "override");
  return (
    <p className="m-0 mt-1 text-xs text-fg-secondary" data-testid="mode-key">
      {modes.map((m) => `${MODE_LABELS[m]}: ${MODE_HELP[m]}`).join(" ")}
      {set && " Set by hand: changed on Settings, Prices."}
    </p>
  );
}

/** The price mode, and "set by hand" in words when an override set it. The tooltip is extra. */
function ModeBadge({ h }: { h: HoldingRow }) {
  const set = h.mode_source === "override";
  const tip = `${MODE_HELP[h.mode]}${set ? " Set by hand on Settings, Prices." : ""}`;
  return (
    <>
      <Tooltip content={tip}>
        <span tabIndex={0} className="inline-flex rounded-full">
          <Badge tone={set ? "accent" : "neutral"}>
            {MODE_LABELS[h.mode]}
            <span className="sr-only">. {tip}</span>
          </Badge>
        </span>
      </Tooltip>
      {set && <span>set by hand</span>}
    </>
  );
}

