import { ChevronRight, Pencil } from "lucide-react";
import { Money } from "../../components/money/Money";
import { cn } from "../../lib/cn";
import { formatDate, formatShortDate } from "../../lib/format";
import type { TxnRow } from "./api";
import { type DayBand, displayName, personLabel } from "./model";

interface TxnListProps {
  bands: DayBand[];
  onOpen: (row: TxnRow) => void;
  /** Edit multiple mode: rows get checkboxes. */
  selecting: boolean;
  selected: Set<string>;
  onToggle: (key: string) => void;
}

// Merchant, category, account, cardholder, amount, marker. Phones stack the middle columns.
const COLS = "md:grid md:grid-cols-[minmax(0,1.4fr)_minmax(0,1.2fr)_minmax(0,1.1fr)_minmax(0,0.6fr)_7.5rem_2.5rem]";

/**
 * Date bands with day totals, then one row per transaction (Monarch's
 * transactions list). Each row is a button that opens the detail drawer.
 */
export function TxnList({ bands, onOpen, selecting, selected, onToggle }: TxnListProps) {
  return (
    <div>
      <div
        aria-hidden="true"
        className={cn("hidden border-b border-line px-5 py-2 text-xs font-medium uppercase tracking-wider text-fg-secondary md:px-6", COLS, selecting && "pl-14 md:pl-14")}
      >
        <span>Merchant</span>
        <span>Category</span>
        <span>Account</span>
        <span>Cardholder</span>
        <span className="text-right">Amount</span>
        <span />
      </div>
      {bands.map((band) => (
        <section key={band.date ?? "all"} aria-label={band.date ? formatDate(band.date) : "Transactions"}>
          {band.date && (
            <h3 className="m-0 flex items-baseline justify-between gap-3 bg-subtle px-4 py-2 text-sm font-medium text-fg-secondary md:px-6">
              <span>{formatDate(band.date)}</span>
              {band.totalCents !== null && (
                <span>
                  <span className="sr-only">Day total </span>
                  <Money cents={band.totalCents} withCents flow className={band.totalCents > 0 ? undefined : "text-fg-secondary"} />
                </span>
              )}
            </h3>
          )}
          <ul className="m-0 list-none p-0">
            {band.rows.map((row) => (
              <li key={row.txn_key} className="flex items-stretch border-b border-line last:border-b-0">
                {selecting && (
                  <label className="flex w-12 shrink-0 cursor-pointer items-center justify-center pl-2">
                    <input
                      type="checkbox"
                      checked={selected.has(row.txn_key)}
                      onChange={() => onToggle(row.txn_key)}
                      className="h-4 w-4 accent-[var(--primary)]"
                      aria-label={`Select ${displayName(row)}, ${formatDate(row.txn_date)}`}
                    />
                  </label>
                )}
                <button
                  type="button"
                  onClick={() => (selecting ? onToggle(row.txn_key) : onOpen(row))}
                  className={cn(
                    "flex min-h-14 w-full min-w-0 items-center gap-3 px-4 py-2 text-left text-sm text-fg hover:bg-hover md:min-h-12 md:items-center md:gap-0 md:px-6",
                    COLS,
                    selecting && "pl-1 md:pl-1",
                    row.hidden && "opacity-60",
                  )}
                  tabIndex={selecting ? -1 : undefined}
                >
                  <TxnCells row={row} showDate={!band.date} />
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function TxnCells({ row, showDate }: { row: TxnRow; showDate: boolean }) {
  const name = displayName(row);
  const person = personLabel(row);
  const category = row.category_name ?? row.category_key;
  return (
    <>
      {/* Merchant, with the phone's second line under it. */}
      <span className="min-w-0 flex-1 md:pr-3">
        <span className="flex min-w-0 items-center">
          <span className="truncate font-medium md:font-normal">
            {name}
            {row.hidden && <span className="text-fg-secondary"> (hidden)</span>}
          </span>
          {row.needs_review && <ReviewBadge />}
        </span>
        <span className="block truncate text-xs text-fg-secondary md:hidden">
          {[
            showDate ? formatShortDate(row.txn_date) : null,
            `${row.category_emoji ?? ""} ${category}`.trim(),
            row.account_name,
            row.has_override ? "Edited" : null,
          ]
            .filter(Boolean)
            .join(" · ")}
        </span>
        {showDate && <span className="hidden text-xs text-fg-secondary md:block">{formatDate(row.txn_date)}</span>}
      </span>
      <span className="hidden min-w-0 pr-3 md:block">
        <span className="flex min-w-0 items-center gap-1.5">
          <span aria-hidden="true" className="inline-block w-5 shrink-0 text-center">
            {row.category_emoji ?? ""}
          </span>
          <span className="truncate">{category}</span>
        </span>
      </span>
      <span className="hidden min-w-0 truncate pr-3 text-fg-secondary md:block">{row.account_name}</span>
      <span className="hidden min-w-0 truncate pr-3 text-fg-secondary md:block">{person}</span>
      <span className="shrink-0 text-right">
        <Money cents={row.amount_cents} withCents flow />
        {row.is_transfer && row.category_kind !== "transfer" && <span className="block text-xs text-fg-secondary">Transfer</span>}
      </span>
      <span className={cn("shrink-0 items-center justify-end gap-1 text-fg-secondary md:flex", row.has_override ? "flex" : "hidden")}>
        {row.has_override ? (
          <span title="Edited">
            <Pencil size={14} aria-hidden="true" />
            {/* Phones say "Edited" in the line under the merchant. */}
            <span className="sr-only hidden md:inline">Edited</span>
          </span>
        ) : null}
        <ChevronRight size={16} aria-hidden="true" className="hidden md:block" />
      </span>
    </>
  );
}

export function ReviewBadge() {
  return <span className="ml-1 shrink-0 rounded-full bg-tint-warn px-2 py-0.5 text-xs font-medium text-warn">Needs review</span>;
}
