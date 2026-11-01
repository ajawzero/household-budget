import { useQuery } from "@tanstack/react-query";
import { getDashboard, getMeta, queryKeys } from "../../lib/api";
import { formatDate, formatMonth } from "../../lib/format";

/** "City, ST · Updated Oct 6, 2026 · Complete through Sep 2026". */
export function headerLineText(parts: {
  location?: string | null;
  updated?: string | null;
  completeThrough?: string | null;
}): string {
  const through = parts.completeThrough ? formatMonth(parts.completeThrough.slice(0, 7)) : null;
  return [
    parts.location,
    parts.updated ? `Updated ${formatDate(parts.updated.slice(0, 10))}` : null,
    through ? `Complete through ${through}` : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

/** The data freshness line under each page title. Hidden until there is data. */
export function HeaderLine() {
  const meta = useQuery({ queryKey: queryKeys.meta, queryFn: getMeta });
  // "Complete through" is the latest complete month (every needed statement is in),
  // the same month Cash Flow, Budget and Statements use. Shared cache with the Dashboard page.
  const dash = useQuery({ queryKey: queryKeys.dashboard(), queryFn: () => getDashboard(), retry: false });
  const text = headerLineText({
    location: dash.data?.location_label ?? meta.data?.location_label,
    updated: dash.data?.updated ?? meta.data?.updated,
    completeThrough: dash.data?.complete_through ?? meta.data?.latest_complete_month,
  });
  if (!text) return null;
  return <p className="m-0 text-sm text-fg-secondary">{text}</p>;
}
