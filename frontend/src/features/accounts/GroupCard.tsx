import { Delta } from "../../components/money/Delta";
import { EstBadge, estimatesNote } from "../../components/money/EstBadge";
import { Money } from "../../components/money/Money";
import { CollapsibleGroup } from "../../components/ui/collapsible-group";
import { rangeLabel } from "../../components/ui/range-picker";
import type { AccountGroup, RangeKey } from "../../lib/api";
import { AccountRowItem } from "./AccountRowItem";
import { shownCents } from "./model";

interface GroupCardProps {
  group: AccountGroup;
  range: RangeKey;
  onOpen: (accountKey: string) => void;
}

/** One card per account group: name, period change, total, then its rows. */
export function GroupCard({ group, range, onOpen }: GroupCardProps) {
  const owed = group.is_liability;
  const estimated = group.accounts.flatMap((a) =>
    a.estimate && a.include_in_net_worth ? [{ display_name: a.display_name, based_on: a.estimate.based_on }] : [],
  );
  return (
    <CollapsibleGroup
      title={
        <span className="flex flex-col gap-0.5 md:flex-row md:items-baseline md:gap-3">
          <span>{group.label}</span>
          <Delta
            cents={group.change_cents}
            pct={group.change_pct}
            display={owed ? "owed" : "signed"}
            period={`${rangeLabel(range)} change`}
            className="font-normal"
          />
        </span>
      }
      summary={
        <span className="text-lg font-medium text-fg">
          <Money cents={shownCents(group.total_cents, group.group)} />
          {owed && <span className="sr-only"> owed</span>}
          {group.estimated && estimated.length > 0 && (
            <EstBadge note={estimatesNote(estimated)} className="ml-2 align-middle" />
          )}
        </span>
      }
    >
      <ul className="m-0 list-none p-0">
        {group.accounts.map((row) => (
          <AccountRowItem key={row.account_key} row={row} range={range} onOpen={onOpen} />
        ))}
      </ul>
    </CollapsibleGroup>
  );
}
