import { useState } from "react";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Drawer } from "../../components/ui/drawer";
import { ApiError, type BalanceHistory, type RewardProgram } from "../../lib/api";
import { formatDate } from "../../lib/format";
import { ListSkeleton } from "../recurring/parts";
import { useBalanceHistory, useDeleteBalance, useDeleteProgram } from "./api";
import { BALANCE_UNIT, type BalanceUnit, balanceText, entryText, SOURCE_TEXT } from "./model";
import { FormStatus } from "./parts";

interface HistoryRow {
  key: string;
  asOf: string;
  micro: number;
  source: keyof typeof SOURCE_TEXT;
  shown: boolean;
  /** App entries only: they can be deleted. */
  id: number | null;
  /** The unit an app entry was typed in. Null for the chat's, which is in the program's unit. */
  unit: BalanceUnit | null;
}

/** The app's entries and the chat's current balance, newest first. On the same day the app's entry is first (it wins). */
export function historyRows(h: BalanceHistory): HistoryRow[] {
  const rows: HistoryRow[] = h.entries.map((e) => ({
    key: `app-${e.id}`,
    asOf: e.as_of,
    micro: e.balance_micro,
    source: "app",
    shown: e.shown,
    id: e.id,
    unit: e.unit,
  }));
  if (h.chat) {
    const chatShown = h.program !== null && h.program.source !== "app";
    rows.push({ key: "chat", asOf: h.chat.as_of, micro: h.chat.balance_micro, source: h.chat.source, shown: chatShown, id: null, unit: null });
  }
  // Stable sort: app entries stay ahead of the chat's on the same day.
  return rows.sort((a, b) => (a.asOf === b.asOf ? 0 : a.asOf > b.asOf ? -1 : 1));
}

/**
 * "Balance history" for one program: date, value and source. Each entry shows
 * in the unit it was typed in, and one in the other unit says it is not
 * counted. Anyone signed in can delete an entry made in the app. An admin
 * can delete a program added in the app (`canDeleteProgram`).
 */
export function BalanceHistoryDrawer({
  program,
  open,
  onOpenChange,
  canDeleteProgram,
}: {
  program: RewardProgram;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  canDeleteProgram: boolean;
}) {
  return (
    <Drawer open={open} onOpenChange={onOpenChange} title={`${program.name} balance history`}>
      <HistoryBody program={program} onDeleted={() => onOpenChange(false)} canDeleteProgram={canDeleteProgram} />
    </Drawer>
  );
}

function HistoryBody({ program, onDeleted, canDeleteProgram }: { program: RewardProgram; onDeleted: () => void; canDeleteProgram: boolean }) {
  const q = useBalanceHistory(program.key);
  const del = useDeleteBalance(program.key);
  const [problem, setProblem] = useState<string | null>(null);
  if (q.isPending) return <ListSkeleton rows={3} />;
  if (q.isError) return <p className="m-0 py-8 text-center text-sm text-fg-secondary">Could not load the balances.</p>;
  const rows = historyRows(q.data);
  // The chat's balance is in the program's unit. An app entry shows in the unit it was typed in.
  const valueText = (r: HistoryRow) =>
    r.unit === null ? balanceText({ kind: program.kind, balance_micro: r.micro }) : entryText(program.kind, r.unit, r.micro);
  const counted = (r: HistoryRow) => r.unit === null || r.unit === BALANCE_UNIT[program.kind];
  return (
    <div className="flex flex-col gap-4">
      {rows.length === 0 ? (
        <p className="m-0 text-sm text-fg-secondary">No balances yet.</p>
      ) : (
        <ul className="m-0 list-none p-0" aria-label="Balance history">
          {rows.map((r) => (
            <li key={r.key} className="flex items-center gap-3 border-t border-line py-2 first:border-t-0">
              <div className="min-w-0 flex-1">
                <p className="m-0 flex flex-wrap items-center gap-x-2 text-sm font-medium text-fg">
                  <span className="tabular">{valueText(r)}</span>
                  {r.shown && <Badge tone="accent">Shown</Badge>}
                </p>
                <p className="m-0 text-sm text-fg-secondary">
                  {formatDate(r.asOf)} · {SOURCE_TEXT[r.source]}
                </p>
                {!counted(r) && <p className="m-0 text-sm text-fg-secondary">Not counted: entered in another unit.</p>}
              </div>
              {r.id !== null && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={del.isPending}
                  onClick={() => {
                    const id = r.id!;
                    del.mutate(id, {
                      onSuccess: () => setProblem(null),
                      onError: (err) => setProblem(err instanceof ApiError ? err.message : "Could not delete the balance."),
                    });
                  }}
                  aria-label={`Delete the ${formatDate(r.asOf)} balance`}
                >
                  Delete
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      <FormStatus problem={problem} saved={null} />
      {canDeleteProgram && <DeleteProgram program={program} onDeleted={onDeleted} />}
    </div>
  );
}

/** Admin only, programs added in the app: an inline confirm, since its balances go too. */
function DeleteProgram({ program, onDeleted }: { program: RewardProgram; onDeleted: () => void }) {
  const del = useDeleteProgram();
  const [asking, setAsking] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <div className="border-t border-line pt-4">
      {asking ? (
        <div className="flex flex-col gap-2">
          <p className="m-0 text-sm text-fg">Delete {program.name} and the balances entered for it?</p>
          <div className="flex flex-wrap gap-2">
            <Button
              variant="primary"
              disabled={del.isPending}
              onClick={() =>
                del.mutate(program.key, {
                  onSuccess: onDeleted,
                  onError: (err) => setProblem(err instanceof ApiError ? err.message : "Could not delete the program."),
                })
              }
            >
              Delete program
            </Button>
            <Button onClick={() => setAsking(false)}>Cancel</Button>
          </div>
        </div>
      ) : (
        <Button onClick={() => setAsking(true)}>Delete this program</Button>
      )}
      <FormStatus problem={problem} saved={null} />
    </div>
  );
}
