import { type FormEvent, useLayoutEffect, useRef, useState } from "react";
import { Button } from "../../components/ui/button";
import { Drawer, focusPanel } from "../../components/ui/drawer";
import { ApiError } from "../../lib/api/client";
import { cn } from "../../lib/cn";
import { formatDate, formatMoney, formatMonth, formatMonthShort } from "../../lib/format";
import { INPUT_CLASS } from "../accounts/vehicles/fields";
import { MONEY_RULE } from "../rewards/model";
import { FormStatus } from "../rewards/parts";
import { centsToText, parseDollarsCents } from "../settings/vehicles/parse";
import { EditedBadge } from "../transactions/controls";
import { type BudgetGroup, type BudgetsOut, type TargetChangeIn, useSaveTargets } from "./api";

/** The API's cap on one target: $1,000,000. */
export const MAX_TARGET_CENTS = 100_000_000;
export const AMOUNT_RULE = "Use an amount from $0 to $1,000,000.";
export const EMPTY_RULE = "Enter an amount. $0 is fine.";
export const MOVED_TEXT = "The chat's plan changed while you edited. Check the new numbers, then save again.";

/** A conflict or an edited row: keep the owner's number, or go back to the chat's. */
export type Choice = "mine" | "chat";

export interface Draft {
  /** What was typed. Left out: the row shows the current target. */
  text?: string;
  choice?: Choice;
  /**
   * The chat's number on screen when the row was first changed. Save sends it,
   * so the server can say when the plan moved, even if a refetch changed the
   * screen in between. Left out: the chat's number now.
   */
  seen?: number | null;
  /** The chat's newer number the form pointed out (see MOVED_TEXT). Once shown, Save sends it. */
  ack?: number | null;
}

/** The chat's number one row sends as seen_chat_cents. */
function seenFor(g: BudgetGroup, d: Draft): number | null {
  if (d.seen === undefined || (d.ack !== undefined && d.ack === g.chat_plan_cents)) return g.chat_plan_cents;
  return d.seen;
}

/** The chat's number moved after the row was changed. */
export function moved(g: BudgetGroup, d: Draft): boolean {
  return d.seen !== undefined && d.seen !== g.chat_plan_cents;
}

export interface DraftResult {
  /** The target the row would have after Save (null: no target). */
  cents: number | null;
  /** What Save sends for the row. Null: nothing changed. */
  change: TargetChangeIn | null;
  error: string | null;
}

/**
 * What one row would save. Use the chat's sends null (the edit goes). Keep mine
 * sends the same target again, which takes the chat's new number as the base.
 * A typed amount goes only when it differs from the target on screen.
 */
export function draftResult(g: BudgetGroup, d: Draft): DraftResult {
  const seen = seenFor(g, d);
  if (d.choice === "chat") {
    return { cents: g.chat_plan_cents, change: { group_key: g.key, target_cents: null, seen_chat_cents: seen }, error: null };
  }
  if (d.choice === "mine") {
    return { cents: g.plan_cents, change: { group_key: g.key, target_cents: g.plan_cents, seen_chat_cents: seen }, error: null };
  }
  if (d.text === undefined) return { cents: g.plan_cents, change: null, error: null };
  const cents = parseDollarsCents(d.text);
  if (cents === undefined) return { cents: g.plan_cents, change: null, error: MONEY_RULE };
  if (cents === null) return { cents: g.plan_cents, change: null, error: g.plan_cents === null ? null : EMPTY_RULE };
  if (cents > MAX_TARGET_CENTS) return { cents: g.plan_cents, change: null, error: AMOUNT_RULE };
  if (cents === g.plan_cents) return { cents, change: null, error: null };
  return { cents, change: { group_key: g.key, target_cents: cents, seen_chat_cents: seen }, error: null };
}

/**
 * The drawer's rows: the page's groups, then the groups that can get a first
 * target but have no row on the page (the chat left them out and nothing was
 * spent), as empty editable rows.
 */
export function editRows(data: BudgetsOut): BudgetGroup[] {
  return [
    ...data.groups,
    ...data.addable_groups.map(
      (a): BudgetGroup => ({
        ...a,
        plan_cents: null,
        actual_cents: 0,
        remaining_cents: null,
        status: "no_plan",
        categories: [],
        chat_plan_cents: null,
        source: "chat",
        editable: true,
        edit: null,
      }),
    ),
  ];
}

export interface Preview {
  total: number | null;
  chatTotal: number | null;
  leftOver: number | null;
}

/**
 * The footer's live numbers. Each change moves the total and planned spending
 * by the same amount, as on the server, so left over drops when a target goes
 * up. Planned income never changes. A row with a bad amount counts as it was.
 */
export function preview(data: BudgetsOut, drafts: Record<string, Draft>): Preview {
  let delta = 0;
  for (const g of editRows(data)) {
    if (!g.editable) continue;
    const r = draftResult(g, drafts[g.key] ?? {});
    if (r.error === null) delta += (r.cents ?? 0) - (g.plan_cents ?? 0);
  }
  const t = data.totals;
  const total = t.plan_cents === null && delta === 0 ? null : (t.plan_cents ?? 0) + delta;
  const spending = data.planned_spending_cents === null ? null : data.planned_spending_cents + delta;
  const leftOver = data.planned_income_cents === null || spending === null ? null : data.planned_income_cents - spending;
  return { total, chatTotal: t.chat_plan_cents, leftOver };
}

/**
 * With "also later" ticked and no row changed, Save copies each edited target
 * to the later months: the same target again (a no-op for this month). A row
 * with a conflict waits for Keep mine or Use the chat's.
 */
export function copiesForLater(data: BudgetsOut): TargetChangeIn[] {
  return data.groups.flatMap((g) =>
    g.editable && g.source === "edited" && g.edit?.conflict !== true && g.plan_cents !== null
      ? [{ group_key: g.key, target_cents: g.plan_cents, seen_chat_cents: g.chat_plan_cents }]
      : [],
  );
}

/** "Nov and Dec", or "Dec 2026 and Jan 2027" when a month is in another year. */
export function laterMonthsText(month: string, later: string[]): string {
  const year = month.slice(0, 4);
  const names = later.map((m) => (m.slice(0, 4) === year ? formatMonthShort(m) : formatMonth(m)));
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/** "The chat changed Lifestyle to $3,800 since your edit." */
export function conflictText(g: Pick<BudgetGroup, "label" | "chat_plan_cents">): string {
  return g.chat_plan_cents === null
    ? `The chat took ${g.label} out of its plan since your edit.`
    : `The chat changed ${g.label} to ${formatMoney(g.chat_plan_cents)} since your edit.`;
}

/** The field id for a group, so the page can open the drawer at that group. */
export function targetFieldId(base: string, key: string): string {
  return `${base}-${key}`;
}

const SPENT_WORD: Record<BudgetGroup["kind"], string> = {
  spending: "Spent",
  saved_invested: "Saved",
  plan_installments: "Billed",
};

/**
 * Edit targets (docs/plans/budget-targets.md 5): one row per group for the
 * month, admin only. Each row has what was spent so far, a dollar field, the
 * chat's number, Use the chat's for an edited group, and Keep mine or Use the
 * chat's when the chat changed it since. Plan It installments are read only.
 * The footer shows the live total, the chat's total and left over. Save sends
 * only the rows that changed, with the chat's number on screen at the first
 * change, so the server can say when the plan moved (or, with only "also later"
 * ticked, the edited rows, to copy them). A number that moved while typing is
 * pointed out once before Save goes. Full screen on phones. When a refetch
 * turns editing off, the form stays open and says why.
 */
export function EditTargetsDrawer({
  open,
  onOpenChange,
  data,
  focusKey,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  data: BudgetsOut;
  /** Open at this group's field. */
  focusKey: string | null;
}) {
  const base = "budget-target";
  function onOpenAutoFocus(event: Event) {
    const field = focusKey ? document.getElementById(targetFieldId(base, focusKey)) : null;
    if (field instanceof HTMLInputElement) {
      event.preventDefault();
      field.focus();
    } else {
      focusPanel(event);
    }
  }
  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      title={`Edit targets for ${formatMonth(data.month)}`}
      description="Your number sits on top of the chat's plan. Every screen and Ask use it."
      onOpenAutoFocus={onOpenAutoFocus}
    >
      <TargetsForm data={data} base={base} />
    </Drawer>
  );
}

function TargetsForm({ data, base }: { data: BudgetsOut; base: string }) {
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [alsoLater, setAlsoLater] = useState(false);
  const [shown, setShown] = useState<Record<string, string>>({});
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const save = useSaveTargets();

  const rows = editRows(data);
  const results = new Map(rows.map((g) => [g.key, draftResult(g, drafts[g.key] ?? {})]));
  const typed = rows.flatMap((g) => {
    const c = g.editable ? results.get(g.key)?.change : null;
    return c ? [c] : [];
  });
  const later = data.later_months;
  const laterOn = alsoLater && later.length > 0;
  // Only the ticked box: copy the edited targets to the later months.
  const copies = laterOn && typed.length === 0 ? copiesForLater(data) : [];
  const changes = typed.length > 0 ? typed : copies;
  const invalid = rows.some((g) => g.editable && results.get(g.key)?.error);
  const dirty = changes.length > 0 || invalid;
  const p = preview(data, drafts);

  /** A row changed. Keeps the chat's number seen at the first change. Undo ({}) forgets it. */
  function setDraft(g: BudgetGroup, next: Draft) {
    setDrafts((all) => {
      const prev = all[g.key];
      if (next.text === undefined && next.choice === undefined) return { ...all, [g.key]: {} };
      const seen = prev?.seen !== undefined ? prev.seen : g.chat_plan_cents;
      return { ...all, [g.key]: { ...next, seen, ack: prev?.ack } };
    });
    setSaved(null);
    setProblem(null);
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!data.can_edit) return;
    setSaved(null);
    const errors: Record<string, string> = {};
    for (const g of rows) {
      const err = g.editable ? results.get(g.key)?.error : null;
      if (err) errors[g.key] = err;
    }
    setShown(errors);
    if (Object.keys(errors).length > 0) {
      setProblem("Fix the amounts marked above.");
      return;
    }
    if (changes.length === 0) return;
    // The chat's number moved under a typed row (a refetch after an import):
    // stop once and point it out. The next Save goes with the new number.
    const unseen = rows.filter((g) => {
      const d = drafts[g.key] ?? {};
      return g.editable && results.get(g.key)?.change && moved(g, d) && d.ack !== g.chat_plan_cents;
    });
    if (unseen.length > 0) {
      setDrafts((all) => {
        const next = { ...all };
        for (const g of unseen) next[g.key] = { ...next[g.key], ack: g.chat_plan_cents };
        return next;
      });
      setProblem(MOVED_TEXT);
      return;
    }
    setProblem(null);
    save.mutate(
      { month: data.month, changes, also_later: laterOn },
      {
        onSuccess: () => {
          setDrafts({});
          setAlsoLater(false);
          setSaved("Saved.");
        },
        onError: (err) => setProblem(err instanceof ApiError ? err.message : "Could not save the targets. Try again."),
      },
    );
  }

  return (
    <form onSubmit={onSubmit} noValidate className="flex min-h-full flex-col">
      {!data.can_edit && data.edit_note && data.edit_note !== problem && (
        <p role="status" className="m-0 mb-3 text-sm text-fg-secondary">
          {data.edit_note}
        </p>
      )}
      {data.edits_notice && (
        <p role="status" className="m-0 mb-3 text-sm text-warn">
          {data.edits_notice}
        </p>
      )}
      <ul className="m-0 flex list-none flex-col p-0" aria-label="Group targets">
        {rows.map((g) => (
          <TargetRow
            key={g.key}
            group={g}
            field={targetFieldId(base, g.key)}
            draft={drafts[g.key] ?? {}}
            error={shown[g.key] ? (results.get(g.key)?.error ?? null) : null}
            busy={save.isPending}
            onDraft={(d) => setDraft(g, d)}
          />
        ))}
      </ul>
      {later.length > 0 && (
        <label className="mt-1 flex min-h-11 items-center gap-3 border-t border-line pt-3 text-sm text-fg">
          <input
            type="checkbox"
            className="h-5 w-5 shrink-0 accent-[var(--primary)]"
            checked={alsoLater}
            onChange={(e) => {
              setAlsoLater(e.target.checked);
              setSaved(null);
              setProblem(null);
            }}
          />
          Also for {laterMonthsText(data.month, later)} (planned by the chat)
        </label>
      )}
      {copies.length > 0 && (
        <p className="m-0 text-sm text-fg-secondary">
          Save copies your edited {copies.length === 1 ? "target" : "targets"} to {laterMonthsText(data.month, later)}.
        </p>
      )}
      {/*
        Sticky on phones, so Save stays in reach above the keyboard (as in
        BalancesDrawer). The negative bottom and margin match the drawer's
        bottom padding, so the bar sits flush. The bar keeps the safe area.
      */}
      <div className="sticky -bottom-[max(20px,env(safe-area-inset-bottom))] -mx-5 -mb-[max(20px,env(safe-area-inset-bottom))] mt-auto border-t border-line bg-card px-5 pt-3 pb-[max(12px,env(safe-area-inset-bottom))] md:static md:mx-0 md:mb-0 md:px-0 md:pb-3">
        <p className="m-0 mb-3 text-sm text-fg-secondary">
          <span className="font-medium text-fg">Total {p.total === null ? "no plan" : formatMoney(p.total)}</span>
          {p.chatTotal !== null && <> · chat planned {formatMoney(p.chatTotal)}</>}
          {p.leftOver !== null && <> · Left over {formatMoney(p.leftOver)}</>}
        </p>
        <Button type="submit" variant="primary" className="w-full justify-center md:w-auto" disabled={!dirty || save.isPending || !data.can_edit}>
          {save.isPending ? "Saving…" : "Save"}
        </Button>
        {problem && (
          <p role="alert" className="m-0 mt-2 text-sm text-bad">
            {problem}
          </p>
        )}
        <FormStatus problem={null} saved={saved} />
      </div>
    </form>
  );
}

function TargetRow({
  group: g,
  field,
  draft,
  error,
  busy,
  onDraft,
}: {
  group: BudgetGroup;
  field: string;
  draft: Draft;
  error: string | null;
  busy: boolean;
  onDraft: (d: Draft) => void;
}) {
  // Keep Use the chat's, Keep mine and Undo from dropping focus: the pressed
  // button goes away, so focus moves to the one that takes its place (Undo, or
  // back to the pressed one), or else to the field.
  const inputRef = useRef<HTMLInputElement>(null);
  const undoRef = useRef<HTMLButtonElement>(null);
  const mineRef = useRef<HTMLButtonElement>(null);
  const chatRef = useRef<HTMLButtonElement>(null);
  const focusNext = useRef<"undo" | Choice | null>(null);
  useLayoutEffect(() => {
    const want = focusNext.current;
    if (want === null) return;
    focusNext.current = null;
    const el = want === "undo" ? undoRef.current : want === "mine" ? mineRef.current : chatRef.current;
    (el ?? inputRef.current)?.focus();
  }, [draft.choice]);
  function choose(choice: Choice) {
    focusNext.current = "undo";
    onDraft({ choice });
  }
  function undo() {
    focusNext.current = draft.choice ?? null;
    onDraft({});
  }
  const spent = `${SPENT_WORD[g.kind]} ${formatMoney(g.actual_cents)} so far`;
  const chat = g.chat_plan_cents === null ? "The chat sent no target." : `Chat planned ${formatMoney(g.chat_plan_cents)}.`;
  if (!g.editable) {
    return (
      <li className="flex flex-col gap-1 border-t border-line py-3 first:border-t-0 first:pt-0">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3">
          <span className="text-sm font-medium text-fg">{g.label}</span>
          <span className="text-sm text-fg">{g.plan_cents === null ? "No plan" : formatMoney(g.plan_cents)}</span>
        </div>
        <p className="m-0 text-sm text-fg-secondary">
          {g.kind === "plan_installments" ? "Set by your plans." : "This group cannot get a target."} {spent}.
        </p>
      </li>
    );
  }
  const edited = g.source === "edited";
  const conflict = edited && g.edit?.conflict === true;
  const shift = moved(g, draft);
  const text = draft.choice === "chat" ? centsToText(g.chat_plan_cents) : (draft.text ?? centsToText(g.plan_cents));
  const what = <span className="sr-only">for {g.label}</span>;
  const described = [
    `${field}-chat`,
    conflict && !draft.choice ? `${field}-conflict` : null,
    shift ? `${field}-moved` : null,
    error ? `${field}-problem` : null,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <li className="flex flex-col gap-1.5 border-t border-line py-3 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <span className="flex items-center gap-2">
          <label htmlFor={field} className="text-sm font-medium text-fg">
            {g.label}
          </label>
          {edited && <EditedBadge />}
        </span>
        <span className="text-sm text-fg-secondary">{spent}</span>
      </div>
      <span className="relative">
        <span aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-secondary">
          $
        </span>
        <input
          ref={inputRef}
          id={field}
          className={cn(INPUT_CLASS, "pl-6 text-right tabular-nums")}
          inputMode="decimal"
          autoComplete="off"
          value={text}
          onChange={(e) => onDraft({ text: e.target.value })}
          aria-invalid={error ? true : undefined}
          aria-describedby={described}
        />
      </span>
      <p id={`${field}-chat`} className="m-0 text-sm text-fg-secondary">
        {chat}
        {edited && g.edit && ` ${g.edit.by ? `Changed by ${g.edit.by}` : "Changed"} on ${formatDate(g.edit.at)}.`}
      </p>
      {shift && (
        <p id={`${field}-moved`} className="m-0 text-sm text-warn">
          {g.chat_plan_cents === null
            ? "The chat took this group out of its plan while you edited."
            : `The chat now plans ${formatMoney(g.chat_plan_cents)}.`}
        </p>
      )}
      {error && (
        <p id={`${field}-problem`} className="m-0 text-sm text-bad">
          {error}
        </p>
      )}
      {draft.choice ? (
        <p className="m-0 flex flex-wrap items-center gap-x-2 text-sm text-fg">
          {draft.choice === "chat" ? "Uses the chat's number when you save." : "Keeps yours when you save."}
          <Button ref={undoRef} size="sm" variant="ghost" className="-ml-1" disabled={busy} onClick={undo}>
            Undo{" "}{what}
          </Button>
        </p>
      ) : conflict ? (
        <div className="flex flex-col gap-1">
          <p id={`${field}-conflict`} className="m-0 text-sm text-warn">
            {conflictText(g)}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button ref={mineRef} size="sm" disabled={busy} onClick={() => choose("mine")}>
              Keep mine{" "}{what}
            </Button>
            <Button ref={chatRef} size="sm" disabled={busy} onClick={() => choose("chat")}>
              Use the chat's{" "}{what}
            </Button>
          </div>
        </div>
      ) : (
        edited && (
          <Button ref={chatRef} size="sm" variant="ghost" className="-ml-3 self-start" disabled={busy} onClick={() => choose("chat")}>
            Use the chat's{" "}{what}
          </Button>
        )
      )}
    </li>
  );
}
