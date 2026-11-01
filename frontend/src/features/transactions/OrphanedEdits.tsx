import { useQuery } from "@tanstack/react-query";
import { Money } from "../../components/money/Money";
import { Card, CardHeader } from "../../components/ui/card";
import { TableScroll } from "../../components/ui/table-scroll";
import { getCategories, queryKeys } from "../../lib/api";
import { formatDate, formatTimestamp } from "../../lib/format";
import { getOrphanedEdits, type OrphanedEdit, txnKeys } from "./api";
import { categoryIndex } from "./model";

/** "Category Dining · Transfer · Note · 2 tags": what the lost edit had set. Never the note text. */
export function editSummary(o: OrphanedEdit["override"], categoryName: (key: string) => string): string {
  const parts: string[] = [];
  if (o.category_key) parts.push(`Category ${categoryName(o.category_key)}`);
  if (o.merchant) parts.push(`Merchant ${o.merchant}`);
  const flag = (v: boolean | null, yes: string, no: string) => {
    if (v !== null) parts.push(v ? yes : no);
  };
  flag(o.is_transfer, "Transfer", "Not a transfer");
  flag(o.is_reimbursable, "Reimbursable", "Not reimbursable");
  flag(o.exclude_from_budget, "Out of budget", "In budget");
  flag(o.hidden, "Hidden", "Shown");
  if (o.notes) parts.push("Note");
  if (o.tags.length) parts.push(`${o.tags.length} tag${o.tags.length === 1 ? "" : "s"}`);
  return parts.join(" · ") || "No fields";
}

/**
 * Statements page: "Edits that lost their transaction" (PLAN 4.11). A
 * statement version dropped the row and no single new row matched it, so
 * the edit waits here. Read only for now.
 */
export function OrphanedEditsCard() {
  const orphans = useQuery({ queryKey: txnKeys.orphaned, queryFn: getOrphanedEdits });
  const categories = useQuery({ queryKey: queryKeys.categories, queryFn: getCategories });
  const index = categoryIndex(categories.data);
  const name = (k: string) => index.get(k)?.name ?? k;
  const rows = orphans.data?.rows ?? [];

  return (
    <Card aria-labelledby="orphaned-title">
      <CardHeader
        titleId="orphaned-title"
        title="Edits that lost their transaction"
        subtitle="A newer statement version dropped these rows, and no single new row matched. The edits are kept here."
      />
      {orphans.isPending && <p className="m-0 text-fg-secondary">Loading…</p>}
      {orphans.isError && <p className="m-0 text-bad">{orphans.error.message}</p>}
      {orphans.isSuccess && rows.length === 0 && <p className="m-0 text-fg-secondary">None. Every edit is on its transaction.</p>}
      {rows.length > 0 && (
        <TableScroll label="Edits that lost their transaction">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Date</th>
                <th scope="col">Statement text</th>
                <th scope="col">Account</th>
                <th scope="col" className="num">
                  Amount
                </th>
                <th scope="col">The edit</th>
                <th scope="col">Lost</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.txn_key}>
                  <td className="whitespace-nowrap">{formatDate(r.txn_date)}</td>
                  <td>{r.imported_merchant ?? r.desc_norm ?? ""}</td>
                  <td>{r.account_name ?? r.account_key ?? ""}</td>
                  <td className="num">
                    <Money cents={r.amount_cents} withCents flow empty="" />
                  </td>
                  <td>
                    {editSummary(r.override, name)}
                    {r.updated_by && <span className="block text-xs text-fg-secondary">By {r.updated_by}</span>}
                  </td>
                  <td className="whitespace-nowrap">{formatTimestamp(r.orphaned_at ?? r.removed_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
    </Card>
  );
}
