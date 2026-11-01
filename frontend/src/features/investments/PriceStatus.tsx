import { ClockAlert } from "lucide-react";
import { Link } from "react-router";
import { formatDate, formatShortDate } from "../../lib/format";
import type { HoldingRow, PricesStatus } from "./api";

export interface PriceLine {
  /** The newest close used by any daily-priced holding. */
  asOf: string | null;
  /** Daily-priced holdings with an old close or none, as "BNDIDX price as of Jun 12". */
  stale: string[];
  /** Every daily-priced holding is stale or has no price. */
  allStale: boolean;
  /** No holding is priced daily. */
  none: boolean;
}

/** What the price status line says, from the holdings on screen. */
export function priceLine(rows: HoldingRow[]): PriceLine {
  const api = rows.filter((h) => h.mode === "api");
  let asOf: string | null = null;
  const stale: string[] = [];
  const seen = new Set<string>();
  for (const h of api) {
    if (h.price_date && !h.price_missing && (asOf === null || h.price_date > asOf)) asOf = h.price_date;
    const name = h.symbol ?? h.name;
    if (seen.has(name)) continue;
    if (h.price_missing) {
      stale.push(`${name} has no price yet`);
      seen.add(name);
    } else if (h.price_stale && h.price_date) {
      stale.push(`${name} price as of ${formatShortDate(h.price_date)}`);
      seen.add(name);
    }
  }
  const allStale = api.length > 0 && api.every((h) => h.price_stale || h.price_missing);
  return { asOf, stale, allStale, none: api.length === 0 };
}

/**
 * "Prices as of Oct 20" above the holdings. The warning color when a close
 * is old or missing, or when the daily price run cannot run (no Tiingo key).
 */
export function PriceStatusLine({ rows, status }: { rows: HoldingRow[]; status: PricesStatus | undefined }) {
  const line = priceLine(rows);
  const keyMissing = status ? !status.tiingo_key_set : false;
  const failed = status?.last_run?.status === "failed";
  const warn = keyMissing || failed || line.stale.length > 0;

  return (
    <p className="m-0 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm" data-testid="price-status">
      {warn && <ClockAlert size={14} aria-hidden="true" className="shrink-0 text-warn" />}
      {line.none ? (
        <span className="text-fg-secondary">No holdings are priced daily. Values are from statements.</span>
      ) : line.asOf ? (
        <span className={line.allStale ? "text-warn" : "text-fg-secondary"} title={formatDate(line.asOf)}>
          Prices as of {formatShortDate(line.asOf)}
        </span>
      ) : null}
      {line.stale.length > 0 && <span className="text-warn">{line.stale.join(". ")}.</span>}
      {keyMissing && <span className="text-warn">Prices are not updating: the Tiingo key is not set.</span>}
      {!keyMissing && failed && <span className="text-warn">The last price run failed.</span>}
      <Link to="/settings/prices" className="inline-flex min-h-11 items-center md:min-h-0">
        Price settings
      </Link>
    </p>
  );
}
