import { ClipboardCheck, PencilLine } from "lucide-react";
import { Link } from "react-router";
import type { RewardsResponse } from "../../lib/api";
import { balancesToUpdate, countText } from "./model";

const LINK_CLASS =
  "inline-flex h-11 items-center gap-2 rounded-lg border border-line bg-card px-4 text-sm font-medium text-fg hover:bg-hover md:h-9";

/**
 * "This month" under the tiles (docs/plans/rewards-inputs.md 7): balances no
 * statement refreshes, and card credits no match word places. Each shows only
 * when there is one to do. The links open the drawers, like the Checks links.
 */
export function ThisMonth({ data }: { data: RewardsResponse }) {
  const balances = balancesToUpdate(data.programs, data.today);
  const credits = data.review_counts.to_review;
  if (balances === 0 && credits === 0) return null;
  return (
    <section aria-label="This month" className="flex flex-col gap-2 md:flex-row md:items-center md:gap-3">
      <h2 className="eyebrow m-0">This month</h2>
      <ul className="m-0 flex list-none flex-col gap-2 p-0 md:flex-row">
        {balances > 0 && (
          <li>
            <Link to="?update=balances" className={`${LINK_CLASS} w-full md:w-auto`}>
              <PencilLine size={16} aria-hidden="true" />
              Update {countText(balances, "balance", "balances")}
            </Link>
          </li>
        )}
        {credits > 0 && (
          <li>
            <Link to="?tab=benefits&review=credits" className={`${LINK_CLASS} w-full md:w-auto`}>
              <ClipboardCheck size={16} aria-hidden="true" />
              Review {countText(credits, "card credit", "card credits")}
            </Link>
          </li>
        )}
      </ul>
    </section>
  );
}
