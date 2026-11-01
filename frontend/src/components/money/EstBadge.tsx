import { formatDate, formatShortDate } from "../../lib/format";
import { cn } from "../../lib/cn";
import { Tooltip } from "../ui/tooltip";

interface EstBadgeProps {
  /** Who made the estimate: market prices (default) or the monthly run. */
  kind?: "prices" | "monthly-run";
  /** Date of the prices used. */
  priceDate?: string | null;
  /** Date of the last statement the estimate starts from. */
  statementDate?: string | null;
  /** The server's own words on how it was made and from what date. Replaces the built text. */
  note?: string | null;
  className?: string;
}

/** Tooltip text for a total that holds estimates: names each account and its base date. */
export function estimatesNote(accounts: { display_name: string; based_on: string }[]): string {
  const names = accounts.map((a) => `${a.display_name} (from ${formatShortDate(a.based_on)})`);
  return `Includes estimates: ${names.join(", ")}. Each account's own est. note says how it was made.`;
}

/** Small "est." pill for a value estimated (prices, an index, depreciation or a loan schedule), not a statement.
 * The tooltip is extra (a touch never opens it): every screen says the note in words nearby. */
export function EstBadge({ kind = "prices", priceDate, statementDate, note, className }: EstBadgeProps) {
  const parts: string[] = note
    ? [note]
    : kind === "monthly-run"
      ? ["Estimated by the monthly run. There is no statement for this account."]
      : ["Estimated from market prices."];
  if (!note && priceDate) parts.push(`Prices from ${formatDate(priceDate)}.`);
  if (!note && statementDate) {
    parts.push(kind === "monthly-run" ? `As of ${formatDate(statementDate)}.` : `Last statement ${formatDate(statementDate)}.`);
  }
  return (
    <Tooltip content={parts.join(" ")}>
      <span
        className={cn(
          "inline-flex h-5 items-center rounded-full bg-tint-warn px-1.5 text-xs font-medium text-warn",
          className,
        )}
      >
        <span aria-hidden="true">est.</span>
        <span className="sr-only">estimated. {parts.join(" ")}</span>
      </span>
    </Tooltip>
  );
}
