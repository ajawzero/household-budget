// Pure helpers for the Transactions page: day bands, labels and the activity text.

import type { CategoriesResponse } from "../../lib/api";
import type { DayTotal, TxnEvent, TxnRow } from "./api";

export interface DayBand {
  /** The date, or null for one flat band (amount sorts). */
  date: string | null;
  totalCents: number | null;
  count: number | null;
  rows: TxnRow[];
}

/**
 * Rows in server order, grouped into date bands with the server's day totals
 * (over the whole filtered set, not just the loaded page). Amount sorts are
 * one flat band, since the dates jump around.
 */
export function dayBands(rows: TxnRow[], days: DayTotal[], byDate: boolean): DayBand[] {
  if (!byDate) return rows.length ? [{ date: null, totalCents: null, count: null, rows }] : [];
  const totals = new Map(days.map((d) => [d.date, d]));
  const bands: DayBand[] = [];
  for (const row of rows) {
    const last = bands[bands.length - 1];
    if (last && last.date === row.txn_date) last.rows.push(row);
    else {
      const t = totals.get(row.txn_date);
      bands.push({ date: row.txn_date, totalCents: t?.total_cents ?? null, count: t?.count ?? null, rows: [row] });
    }
  }
  return bands;
}

/** The name a row shows: the merchant, else the statement text. */
export function displayName(row: Pick<TxnRow, "merchant" | "description">): string {
  return row.merchant ?? row.description;
}

/** "Sam" for the cardholder, else the account owner. */
export function personLabel(row: Pick<TxnRow, "cardholder_label" | "owner_label">): string {
  return row.cardholder_label ?? row.owner_label ?? "";
}

export interface CategoryInfo {
  name: string;
  emoji: string | null;
  groupKey: string;
  groupName: string;
  kind: string;
}

export function categoryIndex(categories: CategoriesResponse | undefined): Map<string, CategoryInfo> {
  const map = new Map<string, CategoryInfo>();
  for (const g of categories?.groups ?? []) {
    for (const c of g.categories) {
      map.set(c.category_key, { name: c.name, emoji: c.emoji, groupKey: g.group_key, groupName: g.name, kind: c.kind });
    }
  }
  return map;
}

/** A category option label with its emoji, for native selects. */
export function categoryOption(name: string, emoji: string | null | undefined): string {
  return emoji ? `${emoji} ${name}` : name;
}

const TOGGLE_TEXT: Record<string, [string, string, string]> = {
  // kind: [set true, set false, cleared]
  transfer_changed: ["Marked as a transfer", "Marked as not a transfer", "Transfer back to the imported value"],
  reimbursable_changed: ["Marked as reimbursable", "Marked as not reimbursable", "Reimbursable back to the imported value"],
  exclude_changed: ["Left out of the budget", "Counted in the budget", "Budget setting back to the imported value"],
  hidden_changed: ["Hidden", "Shown again", "Hidden back to the imported value"],
};

/** One line for an activity event. Note text is never in events, so notes say only what happened. */
export function eventText(e: TxnEvent, categoryName: (key: string) => string): string {
  const toggle = TOGGLE_TEXT[e.kind];
  if (toggle) {
    if (e.to_value === "true") return toggle[0];
    if (e.to_value === "false") return toggle[1];
    return toggle[2];
  }
  switch (e.kind) {
    case "imported":
      return "Imported";
    case "category_changed": {
      const to = e.to_value ? categoryName(e.to_value) : "the imported category";
      const from = e.from_value ? categoryName(e.from_value) : null;
      const lead =
        e.via === "rule"
          ? "A rule changed the category"
          : e.via === "suggestion"
            ? "Category suggestion approved"
            : "Category changed";
      return from ? `${lead} from ${from} to ${to}` : `${lead} to ${to}`;
    }
    case "merchant_changed":
      return "Merchant name changed";
    case "note_added":
      return "Note added";
    case "note_changed":
      return "Note changed";
    case "note_removed":
      return "Note removed";
    case "tags_changed":
      return "Tags changed";
    case "rekeyed":
      return "Edits moved here after a statement changed";
    case "orphaned":
      return "Edits lost this transaction after a statement changed";
    default:
      return e.kind.replace(/_/g, " ");
  }
}

/** "by Alex", "by a rule", "in a bulk edit", "by the import". */
export function eventBy(e: TxnEvent): string {
  const who = e.user ? `by ${e.user}` : "";
  if (e.via === "rule") return "by a rule";
  if (e.via === "bulk") return who ? `${who} in a bulk edit` : "in a bulk edit";
  if (e.via === "import") return e.kind === "imported" ? "" : "by the import";
  return who;
}

/** Rows in the list after an edit: the server's row replaces the old one. */
export function replaceRow(rows: TxnRow[], updated: TxnRow): TxnRow[] {
  return rows.map((r) => (r.txn_key === updated.txn_key ? updated : r));
}

/** Tags typed as "a, b" to a clean list: trimmed, no blanks, no repeats ignoring case. */
export function parseTags(text: string): string[] {
  const out: string[] = [];
  for (const raw of text.split(",")) {
    const t = raw.replace(/\s+/g, " ").trim();
    if (t && !out.some((o) => o.toLowerCase() === t.toLowerCase())) out.push(t);
  }
  return out;
}
