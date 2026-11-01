import { Sparkline } from "../../components/charts";
import { AsOf } from "../../components/money/AsOf";
import { Delta } from "../../components/money/Delta";
import { EstBadge } from "../../components/money/EstBadge";
import { Money } from "../../components/money/Money";
import { Monogram } from "../../components/money/Monogram";
import { rangeLabel } from "../../components/ui/range-picker";
import type { AccountRow, Equity, RangeKey } from "../../lib/api";
import { balanceState, isOwedGroup, metaLine, monogramName, shownCents } from "./model";
import { useEquityNote } from "./vehicles/EquityNotes";

interface AccountRowItemProps {
  row: AccountRow;
  range: RangeKey;
  onOpen: (accountKey: string) => void;
}

/** One account in a group card. The whole row is a button that opens the detail drawer. */
export function AccountRowItem({ row, range, onOpen }: AccountRowItemProps) {
  const owed = isOwedGroup(row.group);
  const equityNote = useEquityNote(row.account_key);
  return (
    <li className="border-b border-line last:border-b-0">
      <button
        type="button"
        onClick={() => onOpen(row.account_key)}
        className="flex min-h-[76px] w-full items-center gap-3 px-4 py-3 text-left hover:bg-hover md:min-h-20 md:gap-4 md:px-6 md:py-4"
      >
        <Monogram name={monogramName(row)} size={48} />
        {/* One line each, like Monarch. The full text is in the title and the drawer. */}
        <span className="min-w-0 flex-1">
          <span className="block truncate text-base text-fg" title={row.display_name}>
            {row.display_name}
          </span>
          <span className="block truncate text-sm text-fg-secondary" title={metaLine(row)}>
            {metaLine(row)}
          </span>
          {row.equity && <EquityLine equity={row.equity} note={equityNote} />}
        </span>
        <span className="hidden w-28 shrink-0 text-right lg:block">
          {row.change_cents !== null && (
            <>
              <Delta cents={row.change_cents} display={owed ? "owed" : "signed"} className="justify-end" />
              <span className="sr-only"> {rangeLabel(range).toLowerCase()} change</span>
            </>
          )}
        </span>
        <span className="hidden w-[120px] shrink-0 md:block">
          {/* Owed rows draw the owed amount, so the line moves the way the shown number does. */}
          <Sparkline
            points={row.sparkline.map((p) => ({ date: p.date, balance_cents: shownCents(p.balance_cents, row.group) }))}
            label={`${row.display_name} ${owed ? "owed" : "balance"} trend`}
          />
        </span>
        <BalanceCell row={row} />
      </button>
    </li>
  );
}

function BalanceCell({ row }: { row: AccountRow }) {
  const state = balanceState(row);
  let main;
  let sub = null;
  if (state === "tracked") {
    // "Tracked" on phones so the name keeps the room; the full words from the tablet size up.
    main = (
      <span className="text-sm text-fg-secondary">
        <span aria-hidden="true" className="md:hidden">
          Tracked
        </span>
        <span className="sr-only md:not-sr-only">Tracked by payments</span>
      </span>
    );
  } else if (state === "missing") {
    main = <Money cents={null} className="text-sm" />;
  } else {
    const cents = shownCents(row.balance_cents ?? 0, row.group);
    main = (
      <span className="inline-flex items-center gap-1.5 text-base text-fg">
        <RowEstBadge row={row} />
        <Money cents={cents} />
      </span>
    );
    // An estimate's date is the real value it starts from: "from Jun 30".
    const asOfLabel = row.estimate && row.estimate.based_on === row.as_of ? "from" : undefined;
    sub =
      state === "closed" ? (
        <span className="text-sm text-fg-secondary">Closed</span>
      ) : (
        <AsOf date={row.as_of} stale={row.stale} label={asOfLabel} />
      );
    if (!row.include_in_net_worth && state === "balance") {
      sub = (
        <>
          {sub}
          <span className="text-sm text-fg-secondary">Not in net worth</span>
        </>
      );
    }
  }
  // The chat marks it not in use. Closed says more, and payments-tracked cards have no balance line.
  if (row.dormant && (state === "balance" || state === "missing")) {
    sub = (
      <>
        {sub}
        <span className="text-sm text-fg-secondary">Dormant</span>
      </>
    );
  }
  // At least 96px on phones, wider for an est. badge and a big balance; the name gives up the room.
  return (
    <span className="flex min-w-24 shrink-0 flex-col items-end text-right md:w-36">
      {main}
      {sub}
    </span>
  );
}

/**
 * The est. badge for a computed balance: prices, the home index, vehicle
 * depreciation or a loan schedule (the server's note says which and from
 * what date), or the monthly run's own estimate.
 */
export function RowEstBadge({
  row,
  className,
}: {
  row: Pick<AccountRow, "estimate" | "source" | "as_of">;
  className?: string;
}) {
  if (row.estimate) return <EstBadge note={row.estimate.note} className={className} />;
  if (row.source === "estimate") return <EstBadge kind="monthly-run" statementDate={row.as_of} className={className} />;
  return null;
}

/**
 * Home and vehicle rows: value minus the loan. Underwater says so in words and color.
 * A note marks it est. when the value or a loan in it is an estimate.
 */
export function EquityLine({ equity, note }: { equity: Equity; note?: string | null }) {
  const badge = note ? <EstBadge note={note} className="ml-1.5 align-middle" /> : null;
  if (equity.underwater) {
    return (
      <span className="block text-sm text-bad">
        Underwater by <Money cents={-equity.equity_cents} />
        {badge}
      </span>
    );
  }
  return (
    <span className="block text-sm text-fg-secondary">
      Equity <Money cents={equity.equity_cents} />
      {badge}
    </span>
  );
}
