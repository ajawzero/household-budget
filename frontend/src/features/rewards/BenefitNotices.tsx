import { useId, useState } from "react";
import { Button } from "../../components/ui/button";
import { Card, CardHeader } from "../../components/ui/card";
import type { BenefitField, RewardNotice, RewardOrphan, RewardsResponse } from "../../lib/api";
import { INPUT_CLASS } from "../accounts/vehicles/fields";
import { useDeleteBenefit, useKeepBothBenefits, useMoveBenefit, useResetBenefit, useResolveBenefit } from "./api";
import { ConfirmAction } from "../../components/ui/confirm-action";
import { benefitLabel, FIELD_NAME } from "./model";
import { FormStatus, MemberHint } from "./parts";
import { failText, type Role } from "./ProgramNotices";

const BENEFIT_NOTICES: RewardNotice["kind"][] = [
  "benefit_chat_changed",
  "benefit_no_fit",
  "benefit_app_key",
  "benefit_duplicate",
  "benefit_app_no_fit",
];

const HOLD_TEXT: Record<RewardOrphan["holds"][number], string> = {
  balances: "balances",
  edits: "edits",
  terms: "match words",
  choices: "credit choices",
  marks: "cycle marks",
};

/** "edits", "edits and match words", "edits, match words and cycle marks". */
export function holdsText(holds: RewardOrphan["holds"]): string {
  const words = holds.map((h) => HOLD_TEXT[h]);
  return words.length < 2 ? (words[0] ?? "inputs") : `${words.slice(0, -1).join(", ")} and ${words.at(-1)}`;
}

interface Target {
  key: string;
  label: string;
}

/** Every benefit the page shows, hidden ones too, as "Name · Card" by key. */
function benefitLabels(data: RewardsResponse): Map<string, string> {
  const out = new Map<string, string>();
  for (const b of [...data.benefits, ...data.hidden, ...data.caps, ...data.perks]) out.set(b.key, benefitLabel(b.name, b.account_name));
  return out;
}

/** Current benefits a kept key can move to, app ones marked. The API checks the card and kind. */
function moveTargets(data: RewardsResponse): Target[] {
  return [...data.benefits, ...data.caps, ...data.perks].map((b) => ({
    key: b.key,
    label: benefitLabel(b.name, b.account_name, b.origin === "app"),
  }));
}

/**
 * "Needs a look" on Benefits (docs/plans/rewards-inputs.md 3.2): the chat
 * changed an edited field, an edit that no longer fits, an app benefit the
 * chat now sends, an app benefit like a chat one, and inputs on a benefit the
 * chat no longer sends. Anyone sees them. Only an admin acts.
 */
export function BenefitNotices({ data, role }: { data: RewardsResponse; role: Role }) {
  const items = data.notices.filter((n) => BENEFIT_NOTICES.includes(n.kind) && n.key !== null);
  const lost = data.orphans.filter((o) => o.kind === "benefit");
  if (items.length + lost.length === 0) return null;
  const labels = benefitLabels(data);
  const labelOf = (key: string) => labels.get(key) ?? key;
  const isAdmin = role === "admin";
  return (
    <Card>
      <CardHeader title="Needs a look" />
      <ul className="m-0 flex list-none flex-col p-0" aria-label="Needs a look">
        {items.map((n) => (
          <li key={`${n.kind}:${n.key}:${n.other_key ?? ""}:${n.fields.join(",")}`} className="border-t border-line py-3 first:border-t-0 first:pt-0">
            <p className="m-0 text-sm font-medium text-fg">{labelOf(n.key!)}</p>
            <p className="m-0 text-sm text-fg-secondary">{n.text}</p>
            <NoticeActions notice={n} label={labelOf(n.key!)} otherLabel={n.other_key ? labelOf(n.other_key) : null} isAdmin={isAdmin} />
          </li>
        ))}
        {lost.map((o) => (
          <li key={`orphan:${o.key}`} className="border-t border-line py-3 first:border-t-0 first:pt-0">
            <OrphanItem orphan={o} targets={moveTargets(data).filter((t) => t.key !== o.key)} isAdmin={isAdmin} />
          </li>
        ))}
      </ul>
      {role === "member" && (
        <div className="mt-3">
          <MemberHint>Only an admin can change benefits.</MemberHint>
        </div>
      )}
    </Card>
  );
}

function NoticeActions({
  notice,
  label,
  otherLabel,
  isAdmin,
}: {
  notice: RewardNotice;
  label: string;
  otherLabel: string | null;
  isAdmin: boolean;
}) {
  const key = notice.key!;
  const resolve = useResolveBenefit();
  const reset = useResetBenefit();
  const del = useDeleteBenefit();
  const move = useMoveBenefit();
  const keep = useKeepBothBenefits();
  const [problem, setProblem] = useState<string | null>(null);
  const busy = resolve.isPending || reset.isPending || del.isPending || move.isPending || keep.isPending;
  const done = { onSuccess: () => setProblem(null), onError: (err: unknown) => setProblem(failText(err)) };
  const field = notice.fields[0] as BenefitField | undefined;
  const other = notice.other_key;
  return (
    <>
      {notice.kind === "benefit_duplicate" && otherLabel && (
        <p className="m-0 text-sm text-fg-secondary">The chat's is {otherLabel}. Use the chat's moves your match words and marks to it and deletes yours.</p>
      )}
      {notice.kind === "benefit_app_key" && <p className="m-0 text-sm text-fg-secondary">Use the chat's deletes yours. Its match words and marks stay.</p>}
      {isAdmin && (
        <div className="mt-2 flex flex-wrap gap-2">
          {notice.kind === "benefit_chat_changed" && field && (
            <>
              <Button size="sm" disabled={busy} onClick={() => resolve.mutate({ key, field, choice: "mine" }, done)}>
                Keep mine{" "}<span className="sr-only">for the {FIELD_NAME[field]} of {label}</span>
              </Button>
              <Button size="sm" disabled={busy} onClick={() => resolve.mutate({ key, field, choice: "chat" }, done)}>
                Use the chat's{" "}<span className="sr-only">for the {FIELD_NAME[field]} of {label}</span>
              </Button>
            </>
          )}
          {notice.kind === "benefit_no_fit" && (
            <Button size="sm" disabled={busy} onClick={() => reset.mutate(key, done)}>
              Delete edit{" "}<span className="sr-only">for {label}</span>
            </Button>
          )}
          {notice.kind === "benefit_app_key" && (
            <Button size="sm" disabled={busy} onClick={() => del.mutate(key, done)}>
              Use the chat's{" "}<span className="sr-only">for {label}</span>
            </Button>
          )}
          {notice.kind === "benefit_app_no_fit" && (
            <ConfirmAction
              ask={
                <>
                  Delete{" "}<span className="sr-only">{label}</span>
                </>
              }
              question={`Delete ${label} with its match words, credit choices and cycle marks?`}
              confirmLabel="Delete benefit"
              busy={busy}
              onConfirm={() => del.mutate(key, done)}
            />
          )}
          {notice.kind === "benefit_duplicate" && other && (
            <>
              <Button size="sm" disabled={busy} onClick={() => move.mutate({ key, toKey: other }, done)}>
                Use the chat's{" "}<span className="sr-only">{otherLabel}</span>
              </Button>
              <Button size="sm" disabled={busy} onClick={() => keep.mutate({ key, otherKey: other }, done)}>
                Keep both{" "}<span className="sr-only">(yours and the chat's {otherLabel})</span>
              </Button>
            </>
          )}
        </div>
      )}
      <FormStatus problem={problem} saved={null} />
    </>
  );
}

/**
 * Inputs on a key the chat no longer sends: kept, not shown. They move to
 * another current benefit on that card, of that kind, or go.
 */
function OrphanItem({ orphan, targets, isAdmin }: { orphan: RewardOrphan; targets: Target[]; isAdmin: boolean }) {
  const id = useId();
  const key = orphan.key;
  const [to, setTo] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const move = useMoveBenefit();
  const del = useDeleteBenefit();
  const busy = move.isPending || del.isPending;
  const done = { onSuccess: () => setProblem(null), onError: (err: unknown) => setProblem(failText(err)) };
  const kept = holdsText(orphan.holds);
  return (
    <>
      <p className="m-0 text-sm font-medium text-fg">The chat no longer sends {key}.</p>
      <p className="m-0 text-sm text-fg-secondary">
        Your {kept} on it are kept, not shown. They can move to a benefit of the same kind on the same card.
      </p>
      {isAdmin && (
        <div className="mt-2 flex flex-wrap items-end gap-2">
          {targets.length > 0 && (
            <>
              <span className="flex min-w-0 flex-col gap-1">
                <label htmlFor={`${id}-to`} className="text-sm text-fg-secondary">
                  Move to{" "}<span className="sr-only">(what {key} holds)</span>
                </label>
                <select id={`${id}-to`} className={INPUT_CLASS.replace("w-full", "w-auto max-w-full")} value={to} onChange={(e) => setTo(e.target.value)}>
                  <option value="">Pick a benefit</option>
                  {targets.map((t) => (
                    <option key={t.key} value={t.key}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </span>
              <Button size="sm" disabled={busy || !to} onClick={() => move.mutate({ key, toKey: to }, done)}>
                Move{" "}<span className="sr-only">what {key} holds</span>
              </Button>
            </>
          )}
          <ConfirmAction
            ask={
              <>
                Delete edits{" "}<span className="sr-only">for {key}</span>
              </>
            }
            question={`Delete everything kept for ${key}?`}
            confirmLabel="Delete edits"
            busy={busy}
            onConfirm={() => del.mutate(key, done)}
          />
        </div>
      )}
      <FormStatus problem={problem} saved={null} />
    </>
  );
}
