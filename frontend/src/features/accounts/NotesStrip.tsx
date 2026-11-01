import { Info } from "lucide-react";
import type { AccountsResponse } from "../../lib/api";
import { nameMap } from "./model";

/** Plain notes about what the totals leave out or cannot vouch for. */
export function NotesStrip({ data }: { data: AccountsResponse }) {
  const names = nameMap(data);
  const list = (keys: string[]) => keys.map((k) => names.get(k) ?? k).join(", ");
  const { partial, missing, stale, tracked, excluded } = data.notes;
  const lines: string[] = [];
  if (partial) lines.push("Net worth is partial. Some balances are missing or out of date.");
  if (missing.length) lines.push(`No balance yet: ${list(missing)}.`);
  if (stale.length) lines.push(`A newer statement is overdue: ${list(stale)}.`);
  if (tracked.length) lines.push(`Tracked by payments, not in net worth: ${list(tracked)}.`);
  if (excluded.length) lines.push(`Shown but not in net worth: ${list(excluded)}.`);
  if (!lines.length) return null;

  return (
    <aside
      aria-label="Notes"
      className="flex gap-3 rounded-card border border-line px-5 py-3 text-sm text-fg-secondary md:px-6"
    >
      <Info size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
      <ul className="m-0 flex list-none flex-col gap-1 p-0">
        {lines.map((l) => (
          <li key={l} className={l.startsWith("Net worth is partial") ? "text-fg" : undefined}>
            {l}
          </li>
        ))}
      </ul>
    </aside>
  );
}
