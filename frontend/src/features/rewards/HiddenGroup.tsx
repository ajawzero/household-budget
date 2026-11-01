import { useState } from "react";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { CollapsibleGroup } from "../../components/ui/collapsible-group";
import type { RewardHiddenBenefit } from "../../lib/api";
import { usePatchBenefit } from "./api";
import { KIND_TEXT } from "./model";
import { FormStatus } from "./parts";
import { failText } from "./ProgramNotices";

/**
 * "Hidden (n)" at the end of the Benefits tab, closed at first. Hidden
 * benefits are out of the list and every total. An admin can show one again.
 */
export function HiddenGroup({ hidden, isAdmin }: { hidden: RewardHiddenBenefit[]; isAdmin: boolean }) {
  if (hidden.length === 0) return null;
  return (
    <CollapsibleGroup title={`Hidden (${hidden.length})`} defaultOpen={false}>
      <ul className="m-0 flex list-none flex-col px-5 pb-2 md:px-6" aria-label="Hidden benefits">
        {hidden.map((h) => (
          <HiddenRow key={h.key} h={h} isAdmin={isAdmin} />
        ))}
      </ul>
    </CollapsibleGroup>
  );
}

function HiddenRow({ h, isAdmin }: { h: RewardHiddenBenefit; isAdmin: boolean }) {
  const patch = usePatchBenefit();
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <li className="border-t border-line py-3 first:border-t-0">
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
        <div className="min-w-0">
          <p className="m-0 flex flex-wrap items-center gap-2 font-medium text-fg">
            <span>{h.name}</span>
            {h.origin === "app" && <Badge>Added in the app</Badge>}
          </p>
          <p className="m-0 text-sm text-fg-secondary">
            {h.account_name ?? h.account_key} · {KIND_TEXT[h.kind]}
          </p>
        </div>
        {isAdmin && (
          <Button
            size="sm"
            disabled={patch.isPending}
            onClick={() =>
              patch.mutate({ key: h.key, body: { hidden: false } }, { onSuccess: () => setProblem(null), onError: (err) => setProblem(failText(err)) })
            }
          >
            Show again<span className="sr-only">: {h.name}</span>
          </Button>
        )}
      </div>
      <FormStatus problem={problem} saved={null} />
    </li>
  );
}
