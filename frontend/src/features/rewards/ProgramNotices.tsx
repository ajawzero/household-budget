import { useId, useState } from "react";
import { Button } from "../../components/ui/button";
import { Card, CardHeader } from "../../components/ui/card";
import { ApiError, type RewardNotice, type RewardOrphan, type RewardProgram } from "../../lib/api";
import { INPUT_CLASS } from "../accounts/vehicles/fields";
import { useDeleteBalances, useDeleteProgram, useKeepBoth, useMoveBalances } from "./api";
import { balancePrograms } from "./BalancesDrawer";
import { ConfirmAction } from "../../components/ui/confirm-action";
import { BALANCE_UNIT, sameUnit, UNIT_TEXT } from "./model";
import { FormStatus, MemberHint } from "./parts";

/** Who is looking: null while the user loads, so no hint or button flashes. */
export type Role = "admin" | "member" | null;

const PROGRAM_NOTICES: RewardNotice["kind"][] = ["program_app_key", "program_duplicate"];

export function failText(err: unknown): string {
  return err instanceof ApiError ? err.message : "Could not save. Try again.";
}

/** "Harbor Miles · Alex", plus "(added in the app)", so programs with one name stay apart. */
export function programLabel(p: RewardProgram): string {
  const owner = p.owner_name ? ` · ${p.owner_name}` : "";
  return `${p.name}${owner}${p.origin === "app" ? " (added in the app)" : ""}`;
}

/**
 * "Needs a look" on Points (docs/plans/rewards-inputs.md 3.1): an app program
 * the chat now sends, two programs with the same name, and balances kept but
 * not shown (orphans). Anyone sees them. Only an admin acts.
 */
export function ProgramNotices({
  notices,
  orphans,
  programs,
  role,
}: {
  notices: RewardNotice[];
  orphans: RewardOrphan[];
  programs: RewardProgram[];
  role: Role;
}) {
  const items = notices.filter((n) => PROGRAM_NOTICES.includes(n.kind) && n.key !== null);
  const lost = orphans.filter((o) => o.kind === "program");
  if (items.length + lost.length === 0) return null;
  const find = (key: string | null) => programs.find((p) => p.key === key);
  const nameOf = (key: string) => find(key)?.name ?? key;
  const isAdmin = role === "admin";
  return (
    <Card>
      <CardHeader title="Needs a look" />
      <ul className="m-0 flex list-none flex-col p-0" aria-label="Needs a look">
        {items.map((n) => (
          <li key={`${n.kind}:${n.key}:${n.other_key ?? ""}`} className="border-t border-line py-3 first:border-t-0 first:pt-0">
            {n.kind === "program_duplicate" ? (
              <DuplicateItem appKey={n.key!} chatKey={n.other_key} name={nameOf(n.key!)} chat={find(n.other_key)} isAdmin={isAdmin} />
            ) : (
              <AppKeyItem appKey={n.key!} name={nameOf(n.key!)} app={find(n.key)} otherUnit={n.fields.includes("unit")} isAdmin={isAdmin} />
            )}
          </li>
        ))}
        {lost.map((o) => (
          <li key={`orphan:${o.key}:${o.unit ?? ""}`} className="border-t border-line py-3 first:border-t-0 first:pt-0">
            <OrphanItem orphan={o} programs={programs} isAdmin={isAdmin} />
          </li>
        ))}
      </ul>
      {role === "member" && (
        <div className="mt-3">
          <MemberHint>Only an admin can change programs.</MemberHint>
        </div>
      )}
    </Card>
  );
}

/**
 * The chat now sends a key the app made. The app's program is shown. When the
 * chat counts the key in another unit (`otherUnit`), Use the chat's keeps the
 * app's balances but does not show them, and the notice says so.
 */
function AppKeyItem({
  appKey,
  name,
  app,
  otherUnit,
  isAdmin,
}: {
  appKey: string;
  name: string;
  app: RewardProgram | undefined;
  otherUnit: boolean;
  isAdmin: boolean;
}) {
  const del = useDeleteProgram();
  const [problem, setProblem] = useState<string | null>(null);
  const unit = app ? BALANCE_UNIT[app.kind] : null;
  const yours = unit ? `your ${UNIT_TEXT[unit]} balances` : "your balances";
  return (
    <>
      <p className="m-0 text-sm font-medium text-fg">The chat now sends {name} too. Yours is shown.</p>
      <p className="m-0 text-sm text-fg-secondary">
        {otherUnit ? `The chat counts it in another unit. Use the chat's keeps ${yours}, but does not show them.` : "Use the chat's keeps your balances."}
      </p>
      {isAdmin && (
        <div className="mt-2 flex flex-wrap gap-2">
          <Button
            size="sm"
            disabled={del.isPending}
            onClick={() => del.mutate(appKey, { onSuccess: () => setProblem(null), onError: (err) => setProblem(failText(err)) })}
          >
            Use the chat's{" "}<span className="sr-only">for {name}</span>
          </Button>
        </div>
      )}
      <FormStatus problem={problem} saved={null} />
    </>
  );
}

/**
 * An app program with the same name as one the chat sends. The chat can send
 * one per owner, so the chat's program is named with its owner.
 */
function DuplicateItem({
  appKey,
  chatKey,
  name,
  chat,
  isAdmin,
}: {
  appKey: string;
  chatKey: string | null;
  name: string;
  chat: RewardProgram | undefined;
  isAdmin: boolean;
}) {
  const move = useMoveBalances();
  const keep = useKeepBoth();
  const [problem, setProblem] = useState<string | null>(null);
  const busy = move.isPending || keep.isPending;
  const done = { onSuccess: () => setProblem(null), onError: (err: unknown) => setProblem(failText(err)) };
  const theirs = chat ? programLabel(chat) : name;
  return (
    <>
      <p className="m-0 text-sm font-medium text-fg">Two programs named {name}.</p>
      <p className="m-0 text-sm text-fg-secondary">
        One is yours. The other is the chat's {theirs}. Use the chat's moves your balances to it and deletes yours.
      </p>
      {isAdmin && chatKey && (
        <div className="mt-2 flex flex-wrap gap-2">
          <Button size="sm" disabled={busy} onClick={() => move.mutate({ key: appKey, toKey: chatKey }, done)}>
            Use the chat's{" "}<span className="sr-only">{theirs}</span>
          </Button>
          <Button size="sm" disabled={busy} onClick={() => keep.mutate({ key: appKey, otherKey: chatKey }, done)}>
            Keep both{" "}<span className="sr-only">(yours and the chat's {theirs})</span>
          </Button>
        </div>
      )}
      <FormStatus problem={problem} saved={null} />
    </>
  );
}

/** What the chat counts a program in now: "points", "miles" or "cash back". */
const KIND_UNIT_TEXT: Record<RewardProgram["kind"], string> = {
  points: "points",
  miles: "miles",
  cash_back: "cash back",
  timeshare: "yearly points",
};

/**
 * Balances kept, not shown, on a key the chat no longer sends, on a program
 * that is now a timeshare, or typed in the other unit than the one the chat
 * now counts the program in. They move only to a program in their unit. A key
 * with both units is listed once per unit, so every line and button names the
 * unit, and Delete balances sends it: only the balances in that unit go.
 */
function OrphanItem({ orphan, programs, isAdmin }: { orphan: RewardOrphan; programs: RewardProgram[]; isAdmin: boolean }) {
  const id = useId();
  const orphanKey = orphan.key;
  const unit = orphan.unit ?? "whole"; // program orphans always carry one
  const shown = programs.find((p) => p.key === orphanKey);
  const label = shown?.name ?? orphanKey;
  const targets = balancePrograms(programs).filter((p) => p.key !== orphanKey && sameUnit(p.kind, unit));
  const [to, setTo] = useState("");
  // Worked out on each render: a program in the unit that shows up later can
  // be picked, and one that is gone is never sent.
  const pick = targets.some((p) => p.key === to) ? to : (targets[0]?.key ?? "");
  const [problem, setProblem] = useState<string | null>(null);
  const move = useMoveBalances();
  const del = useDeleteBalances();
  const busy = move.isPending || del.isPending;
  const done = { onSuccess: () => setProblem(null), onError: (err: unknown) => setProblem(failText(err)) };
  const was = UNIT_TEXT[unit];
  const these = `the ${label} balances entered as ${was}`;
  // Unit orphans only: what the chat counts the program in now.
  const now = orphan.reason === "unit" && shown ? KIND_UNIT_TEXT[shown.kind] : null;
  const title = now
    ? `${label}: your earlier balances were entered as ${was}. The chat now counts it in ${now}.`
    : orphan.reason === "timeshare"
      ? `${label} now uses yearly points.`
      : `The chat no longer sends ${orphanKey}.`;
  return (
    <>
      <p className="m-0 text-sm font-medium text-fg">{title}</p>
      <p className="m-0 text-sm text-fg-secondary">
        {now ? "Those balances are kept, not shown." : `Its balances entered as ${was} are kept, not shown.`}
      </p>
      {isAdmin && targets.length === 0 && (
        <p className="m-0 text-sm text-fg-secondary">No {was} program to move them to. Add one under Other programs.</p>
      )}
      {isAdmin && (
        <div className="mt-2 flex flex-wrap items-end gap-2">
          {targets.length > 0 && (
            <>
              <span className="flex min-w-0 flex-col gap-1">
                <label htmlFor={`${id}-to`} className="text-sm text-fg-secondary">
                  Move to{" "}<span className="sr-only">({these})</span>
                </label>
                <select id={`${id}-to`} className={INPUT_CLASS.replace("w-full", "w-auto")} value={pick} onChange={(e) => setTo(e.target.value)}>
                  {targets.map((p) => (
                    <option key={p.key} value={p.key}>
                      {programLabel(p)}
                    </option>
                  ))}
                </select>
              </span>
              <Button size="sm" disabled={busy || !pick} onClick={() => move.mutate({ key: orphanKey, toKey: pick }, done)}>
                Move{" "}<span className="sr-only">{these}</span>
              </Button>
            </>
          )}
          <ConfirmAction
            ask={
              <>
                Delete balances{" "}<span className="sr-only">for {label} entered as {was}</span>
              </>
            }
            question={`Delete ${these}?`}
            confirmLabel="Delete balances"
            busy={busy}
            onConfirm={() => del.mutate({ key: orphanKey, unit }, done)}
          />
        </div>
      )}
      <FormStatus problem={problem} saved={null} />
    </>
  );
}
