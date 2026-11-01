import { Plus, X } from "lucide-react";
import { type FormEvent, Fragment, useEffect, useId, useRef, useState } from "react";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { ConfirmAction, useConfirmFocus } from "../../components/ui/confirm-action";
import { Drawer } from "../../components/ui/drawer";
import {
  ApiError,
  type BenefitField,
  type BenefitPatchIn,
  type BenefitSetIn,
  type ResolveChoice,
  type RewardBenefit,
  type RewardCycle,
  type RewardCycleSource,
  type TermPreview,
} from "../../lib/api";
import { formatDate, formatMoney, formatMonthShort, formatShortDate } from "../../lib/format";
import { INPUT_CLASS } from "../accounts/vehicles/fields";
import { parseDollarsCents } from "../settings/vehicles/parse";
import { EditedBadge, SwitchRow } from "../transactions/controls";
import { useDebounced } from "../transactions/RulePrompt";
import {
  useClearMark,
  useDeleteBenefit,
  usePatchBenefit,
  useResetBenefit,
  useResolveBenefit,
  useSaveTerms,
  useSetCredit,
  useSetMark,
  useTermPreview,
  useTerms,
} from "./api";
import {
  amountStartDefault,
  CADENCE_TEXT,
  countText,
  CYCLE_SOURCE_TEXT,
  CYCLE_TEXT,
  CYCLE_TONE,
  cycleNotes,
  FIELD_NAME,
  KIND_TEXT,
  markUsedOn,
  matches,
  MONEY_RULE,
  moneyText,
  parseMoney,
  startedCycles,
  termProblem,
  termText,
} from "./model";
import { FormStatus, MemberHint } from "./parts";
import { failText } from "./ProgramNotices";

/** Why a benefit left the page from its drawer, so focus can go somewhere that stays. */
export type Gone = "hidden" | "deleted";

/** The fields the drawer edits. The end date has its own part. */
export type FormField = Extract<
  BenefitField,
  "name" | "amount" | "counted_value" | "enroll_by" | "enrolled" | "auto" | "rolls_over" | "how_to_use" | "note"
>;
export type BenefitForm = Record<FormField, string>;

export type Input = "text" | "money" | "date" | "yesno";

interface FieldSpec {
  key: FormField;
  label: string;
  input: Input;
  creditOnly?: boolean;
  hint?: string;
  max?: number;
}

const FIELDS: FieldSpec[] = [
  { key: "name", label: "Name", input: "text", max: 100 },
  { key: "amount", label: "Amount each cycle", input: "money", creditOnly: true },
  {
    key: "counted_value",
    label: "Yearly value",
    input: "money",
    hint: "What it is worth to you a year. Fees counts this. Leave it empty to count the full value.",
  },
  { key: "enroll_by", label: "Enroll by", input: "date" },
  { key: "enrolled", label: "Enrolled", input: "yesno" },
  { key: "auto", label: "Loads by itself", input: "yesno", creditOnly: true },
  { key: "rolls_over", label: "Rolls over", input: "yesno", creditOnly: true },
  { key: "how_to_use", label: "How to use it", input: "text", max: 500 },
  { key: "note", label: "Note", input: "text", max: 500 },
];

/** The fields that fit a kind: the amount, Loads by itself and Rolls over are for credits. */
export function formFields(kind: RewardBenefit["kind"]): FieldSpec[] {
  return FIELDS.filter((f) => kind === "credit" || !f.creditOnly);
}

function yesNo(v: boolean | null): string {
  return v === null ? "" : v ? "yes" : "no";
}

/** The benefit as shown, as field text. */
export function formOf(b: RewardBenefit): BenefitForm {
  return {
    name: b.name,
    amount: b.amount_cents === null ? "" : moneyText(b.amount_cents),
    counted_value: b.counted_value_cents === null ? "" : moneyText(b.counted_value_cents),
    enroll_by: b.enroll_by ?? "",
    enrolled: yesNo(b.enrolled),
    auto: yesNo(b.auto),
    rolls_over: yesNo(b.rolls_over),
    how_to_use: b.how_to_use ?? "",
    note: b.note ?? "",
  };
}

type Value = string | boolean | null;

/** A field's text to the API's value. Null when empty, undefined when not valid. */
function valueOf(input: Input, text: string): Value | undefined {
  if (input === "money") return parseMoney(text);
  if (input === "yesno") return text === "" ? null : text === "yes";
  const t = text.trim();
  return t === "" ? null : t;
}

/**
 * The fields typed away from where they started (`base`, see FormState), as
 * the API's `set` (null clears one), and a problem per field. Only those
 * fields are sent, so a field the page refetched under is never sent back as
 * it was. The name, and a credit's amount, cannot be empty.
 */
export function benefitSet(
  kind: RewardBenefit["kind"],
  base: BenefitForm,
  form: BenefitForm,
): { set: BenefitSetIn; problems: Partial<Record<FormField, string>> } {
  const set: Record<string, Value> = {};
  const problems: Partial<Record<FormField, string>> = {};
  for (const f of formFields(kind)) {
    const value = valueOf(f.input, form[f.key]);
    if (value === undefined) problems[f.key] = MONEY_RULE;
    else if (value === null && f.key === "name") problems[f.key] = "Enter a name.";
    else if (value === null && f.key === "amount") problems[f.key] = "Enter the amount.";
    else if (value !== valueOf(f.input, base[f.key])) set[f.key] = value;
  }
  return { set: set as BenefitSetIn, problems };
}

const INPUT = Object.fromEntries(FIELDS.map((f) => [f.key, f.input])) as Record<FormField, Input>;

/** Two texts with one meaning: "10" and "10.00", or a name with spaces around it. */
function sameValue(key: FormField, a: string, b: string): boolean {
  return valueOf(INPUT[key], a) === valueOf(INPUT[key], b);
}

/**
 * The edit form while the drawer is open. `base` is where each field started:
 * the benefit when the drawer opened, or a newer value the form took since. A
 * field is typed in when its text means something else than its base. `newer`
 * holds what a refetch brought for a typed field, until the user keeps what
 * they typed or takes the new value.
 */
export interface FormState {
  base: BenefitForm;
  form: BenefitForm;
  newer: Partial<BenefitForm>;
}

export function openForm(b: RewardBenefit): FormState {
  const base = formOf(b);
  return { base, form: base, newer: {} };
}

/** The field's base moves to `value`. What is typed stays. */
function rebase(s: FormState, key: FormField, value: string): FormState {
  const newer = { ...s.newer };
  delete newer[key];
  return { base: { ...s.base, [key]: value }, form: s.form, newer };
}

/** The field shows `value`, a value the benefit holds, and is no longer typed in. */
export function settle(s: FormState, key: FormField, value: string): FormState {
  return { ...rebase(s, key, value), form: { ...s.form, [key]: value } };
}

/** "Keep what I typed": the typed value is sent against the newer one. */
export function keepTyped(s: FormState, key: FormField): FormState {
  const value = s.newer[key];
  return value === undefined ? s : rebase(s, key, value);
}

/** "Use the new value": the field shows it and is no longer typed in. */
export function takeNewer(s: FormState, key: FormField): FormState {
  const value = s.newer[key];
  return value === undefined ? s : settle(s, key, value);
}

/**
 * The form once the page refetched the benefit (an import, a save in another
 * tab). A field not typed in shows the new value. A typed field the refetch
 * left alone stays typed. A typed field it changed waits in `newer`, unless
 * the new value is what was typed, or where the field started. Gives `s`
 * itself back when nothing changed, so a render can call it each time.
 */
export function followForm(s: FormState, fresh: BenefitForm): FormState {
  let next = s;
  for (const { key } of FIELDS) {
    const now = fresh[key];
    if (now === (next.newer[key] ?? next.base[key])) continue;
    const typed = next.form[key];
    if (sameValue(key, typed, next.base[key])) next = settle(next, key, now);
    else if (sameValue(key, typed, now) || now === next.base[key]) next = rebase(next, key, now);
    else next = { ...next, newer: { ...next.newer, [key]: now } };
  }
  return next;
}

/** A field's value in a sentence: "$20.00", "Oct 31, 2026", "yes", "not known", "empty". */
export function valueText(input: Input, text: string): string {
  if (input === "yesno") return text === "" ? "not known" : text;
  if (text.trim() === "") return "empty";
  if (input === "date") return formatDate(text);
  if (input === "money") {
    const cents = parseDollarsCents(text);
    return typeof cents === "number" ? formatMoney(cents, { cents: true }) : text;
  }
  return `"${text}"`;
}

/** Fields the chat changed since the owner's edit. */
function changedFields(b: RewardBenefit): string[] {
  return b.notices.filter((n) => n.kind === "benefit_chat_changed").flatMap((n) => n.fields);
}

/** "Summit card · Credit, monthly. Added in the app." */
function aboutText(b: RewardBenefit): string {
  const kind = b.cadence ? `${KIND_TEXT[b.kind]}, ${CADENCE_TEXT[b.cadence].toLowerCase()}` : KIND_TEXT[b.kind];
  const from = b.origin === "app" ? "Added in the app." : "Your changes sit on top of the chat's.";
  return `${b.account_name ?? b.account_key} · ${kind}. ${from}`;
}

/**
 * Change one benefit (docs/plans/rewards-inputs.md 3.2 and 7). Each edited
 * field can go back to the chat's. A new amount says when it starts. A credit
 * has its match words and its cycles. End, hide and (for an app benefit)
 * delete have their own parts. While an edit no longer fits, only Delete edit
 * and hide are offered. A member sees a credit's cycles only: anyone signed
 * in marks a cycle, only an admin changes the benefit.
 */
export function BenefitDrawer({
  benefit,
  today,
  open,
  onOpenChange,
  onGone,
  isAdmin = true,
}: {
  benefit: RewardBenefit;
  /** Household today, from the page. */
  today: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The benefit was hidden or deleted. The drawer is done. */
  onGone: (why: Gone) => void;
  isAdmin?: boolean;
}) {
  const noFit = benefit.notices.some((n) => n.kind === "benefit_no_fit");
  const credit = benefit.kind === "credit";
  if (!isAdmin) {
    return (
      <Drawer
        open={open}
        onOpenChange={onOpenChange}
        title={benefit.name}
        description={`${benefit.account_name ?? benefit.account_key} · ${KIND_TEXT[benefit.kind]}. Anyone signed in can mark a cycle.`}
      >
        <div className="flex flex-col gap-6">
          {credit && <CyclesPart b={benefit} today={today} first />}
          <MemberHint>Only an admin can change benefits.</MemberHint>
        </div>
      </Drawer>
    );
  }
  return (
    <Drawer open={open} onOpenChange={onOpenChange} title={`Edit ${benefit.name}`} description={aboutText(benefit)}>
      <div className="flex flex-col gap-6">
        {noFit ? (
          <NoFitNote b={benefit} />
        ) : (
          <Fragment key={benefit.key}>
            {/* Keyed once, so what is typed never carries over to another benefit. */}
            <FieldsForm b={benefit} today={today} />
            {credit && <MatchWordsPart b={benefit} />}
            {credit && <CyclesPart b={benefit} today={today} />}
            <EndPart b={benefit} today={today} />
          </Fragment>
        )}
        <HidePart benefitKey={benefit.key} onHidden={() => onGone("hidden")} />
        {benefit.origin === "app" && <DeletePart benefitKey={benefit.key} name={benefit.name} onDeleted={() => onGone("deleted")} />}
      </div>
    </Drawer>
  );
}

/**
 * An earning cap or a status perk: it has its own card, and its fields are
 * the chat's. It can be hidden here, and deleted when it was added in the app.
 */
export function OtherBenefitDrawer({
  item,
  open,
  onOpenChange,
  onGone,
}: {
  item: { key: string; name: string; kind: RewardBenefit["kind"]; account_name: string | null; origin: RewardBenefit["origin"] };
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onGone: (why: Gone) => void;
}) {
  const from = item.origin === "app" ? "Added in the app." : "From the chat.";
  const about = `${item.account_name ? `${item.account_name} · ` : ""}${KIND_TEXT[item.kind]}. ${from}`;
  return (
    <Drawer open={open} onOpenChange={onOpenChange} title={`Edit ${item.name}`} description={about}>
      <div className="flex flex-col gap-6">
        <HidePart benefitKey={item.key} onHidden={() => onGone("hidden")} first />
        {item.origin === "app" && <DeletePart benefitKey={item.key} name={item.name} onDeleted={() => onGone("deleted")} />}
      </div>
    </Drawer>
  );
}

/** The chat changed the benefit and the edit is skipped (rule 9). */
function NoFitNote({ b }: { b: RewardBenefit }) {
  const reset = useResetBenefit();
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <div className="rounded-lg bg-tint-warn p-3">
      <p className="m-0 text-sm text-fg">
        Your edit no longer fits this benefit. The chat's is shown. Delete the edit first to change this benefit.
      </p>
      <Button
        size="sm"
        className="mt-2"
        disabled={reset.isPending}
        onClick={() => reset.mutate(b.key, { onSuccess: () => setProblem(null), onError: (err) => setProblem(failText(err)) })}
      >
        Delete edit
      </Button>
      <FormStatus problem={problem} saved={null} />
    </div>
  );
}

/**
 * "Use the chat's" on an edited field, or, when the chat changed it since the
 * edit, "Keep mine" and "Use the chat's". Chat benefits only.
 */
function ChatChoice({
  field,
  conflict,
  edited,
  busy,
  onReset,
  onResolve,
}: {
  field: BenefitField;
  conflict: boolean;
  edited: boolean;
  busy: boolean;
  onReset: () => void;
  onResolve: (choice: ResolveChoice) => void;
}) {
  const what = <span className="sr-only">for the {FIELD_NAME[field]}</span>;
  if (conflict) {
    return (
      <div className="flex flex-col gap-1">
        <p className="m-0 text-sm text-warn">The chat changed this since your edit.</p>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" disabled={busy} onClick={() => onResolve("mine")}>
            Keep mine{" "}{what}
          </Button>
          <Button size="sm" disabled={busy} onClick={() => onResolve("chat")}>
            Use the chat's{" "}{what}
          </Button>
        </div>
      </div>
    );
  }
  if (!edited) return null;
  return (
    <Button size="sm" variant="ghost" className="-ml-3 self-start" disabled={busy} onClick={onReset}>
      Use the chat's{" "}{what}
    </Button>
  );
}

/** Save's problem while a refetch changed a typed field and the user has not picked. */
const PICK_NEWER = "Pick a value for each field that changed since you opened it.";

function FieldsForm({ b, today }: { b: RewardBenefit; today: string }) {
  const id = useId();
  const [kept, setState] = useState<FormState>(() => openForm(b));
  // A refetch (an import, a save in another tab) brings a newer benefit. The
  // form follows it in this render, so no render shows or sends the older one.
  const state = followForm(kept, formOf(b));
  if (state !== kept) setState(state);
  const { base, form, newer } = state;
  // The start of a new amount follows the benefit until one is typed.
  const [typedStart, setStart] = useState<string | null>(null);
  const start = typedStart ?? amountStartDefault(b, today);
  const [problems, setProblems] = useState<Partial<Record<FormField, string>>>({});
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const patch = usePatchBenefit();
  const resolve = useResolveBenefit();
  const resetAll = useResetBenefit();
  const busy = patch.isPending || resolve.isPending || resetAll.isPending;
  const conflicts = changedFields(b);
  const typed = parseMoney(form.amount);
  const amountChanged = b.kind === "credit" && typeof typed === "string" && typed !== parseMoney(base.amount);
  const waiting = formFields(b.kind).filter((f) => newer[f.key] !== undefined);
  // After a pick its buttons go away: focus moves to that field, not to the drawer's top.
  const focusNext = useRef<FormField | null>(null);
  useEffect(() => {
    if (focusNext.current === null) return;
    document.getElementById(`${id}-${focusNext.current}`)?.focus();
    focusNext.current = null;
  });

  /** After a save, the fields show what the API gave back. `only` keeps what is typed in the rest. */
  function done(only?: FormField) {
    return {
      onSuccess: (out: RewardBenefit) => {
        const fresh = formOf(out);
        setState((s) => (only ? settle(s, only, fresh[only]) : openForm(out)));
        if (!only) setStart(null);
        setProblems({});
        setProblem(null);
        setSaved("Saved.");
      },
      onError: (err: unknown) => {
        setSaved(null);
        setProblem(failText(err));
      },
    };
  }

  /** Keep what is typed, or take the newer value, on a field a refetch changed. */
  function pick(key: FormField, next: (s: FormState) => FormState) {
    focusNext.current = key;
    setState(next);
    setSaved(null);
    setProblem(null);
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSaved(null);
    const { set, problems: found } = benefitSet(b.kind, base, form);
    setProblems(found);
    if (Object.keys(found).length > 0) {
      setProblem("Fix the fields marked above.");
      return;
    }
    if (waiting.length > 0) {
      // The first field waiting for a pick takes focus. Its description reads the newer value.
      setProblem(PICK_NEWER);
      document.getElementById(`${id}-${waiting[0]!.key}`)?.focus();
      return;
    }
    if (Object.keys(set).length === 0) {
      setProblem("Nothing to change.");
      return;
    }
    const body: BenefitPatchIn = { set };
    if (set.amount !== undefined) {
      if (!start) {
        setProblem("Pick when the new amount starts.");
        return;
      }
      body.amount_from = start;
    }
    setProblem(null);
    patch.mutate({ key: b.key, body }, done());
  }

  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
      {formFields(b.kind).map((f) => {
        const field = `${id}-${f.key}`;
        const error = problems[f.key];
        const now = newer[f.key];
        const edited = b.edited_fields.includes(f.key);
        const described = [f.hint ? `${field}-hint` : null, error ? `${field}-problem` : null, now !== undefined ? `${field}-newer` : null]
          .filter(Boolean)
          .join(" ");
        return (
          <div key={f.key} className="flex flex-col gap-1">
            <div className="flex flex-wrap items-center gap-2">
              <label htmlFor={field} className="text-sm text-fg-secondary">
                {f.label}
              </label>
              {edited && <EditedBadge />}
            </div>
            <FieldInput
              id={field}
              spec={f}
              value={form[f.key]}
              onChange={(text) => {
                setState((s) => ({ ...s, form: { ...s.form, [f.key]: text } }));
                setSaved(null);
              }}
              invalid={Boolean(error)}
              describedBy={described || undefined}
            />
            {f.hint && (
              <p id={`${field}-hint`} className="m-0 text-sm text-fg-secondary">
                {f.hint}
              </p>
            )}
            {error && (
              <p id={`${field}-problem`} className="m-0 text-sm text-bad">
                {error}
              </p>
            )}
            {now !== undefined && (
              <NewerChoice
                id={`${field}-newer`}
                field={f.key}
                value={valueText(f.input, now)}
                onKeep={() => pick(f.key, (s) => keepTyped(s, f.key))}
                onTake={() => pick(f.key, (s) => takeNewer(s, f.key))}
              />
            )}
            {f.key === "amount" && amountChanged && (
              <div className="mt-2 flex flex-col gap-1">
                <label htmlFor={`${id}-start`} className="text-sm text-fg-secondary">
                  Change starts
                </label>
                <input
                  id={`${id}-start`}
                  className={INPUT_CLASS}
                  type="date"
                  value={start}
                  onChange={(e) => setStart(e.target.value)}
                  aria-describedby={`${id}-start-hint`}
                />
                <p id={`${id}-start-hint`} className="m-0 text-sm text-fg-secondary">
                  Cycles that start before this day keep the amount from before your change.
                </p>
              </div>
            )}
            {b.origin === "chat" && (
              <ChatChoice
                field={f.key}
                conflict={conflicts.includes(f.key)}
                edited={edited}
                busy={busy}
                onReset={() => patch.mutate({ key: b.key, body: { reset: [f.key] } }, done(f.key))}
                onResolve={(choice) => resolve.mutate({ key: b.key, field: f.key, choice }, done(f.key))}
              />
            )}
          </div>
        );
      })}
      {/* Always there, so a screen reader hears a field change under what is typed. */}
      <p className="sr-only" aria-live="polite">
        {waiting.map((f) => `The ${FIELD_NAME[f.key]} changed since you opened it.`).join(" ")}
      </p>
      <div>
        <div className="flex flex-col gap-2 md:flex-row">
          <Button type="submit" variant="primary" className="w-full justify-center md:w-auto" disabled={busy}>
            {patch.isPending ? "Saving…" : "Save"}
          </Button>
          {b.origin === "chat" && b.edited_fields.length > 0 && (
            <Button className="w-full justify-center md:w-auto" disabled={busy} onClick={() => resetAll.mutate(b.key, done())}>
              Use the chat's for every field
            </Button>
          )}
        </div>
        <FormStatus problem={problem} saved={saved} />
      </div>
    </form>
  );
}

/**
 * A refetch changed a field while it was typed in: the value it holds now,
 * and a pick. Save waits for the pick, so neither value goes by surprise.
 */
function NewerChoice({
  id,
  field,
  value,
  onKeep,
  onTake,
}: {
  id: string;
  field: FormField;
  value: string;
  onKeep: () => void;
  onTake: () => void;
}) {
  const what = <span className="sr-only">for the {FIELD_NAME[field]}</span>;
  return (
    <div className="flex flex-col gap-1">
      <p id={id} className="m-0 break-words text-sm text-warn">
        This changed since you opened it. It is now {value}.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={onKeep}>
          Keep what I typed{" "}{what}
        </Button>
        <Button size="sm" onClick={onTake}>
          Use the new value{" "}{what}
        </Button>
      </div>
    </div>
  );
}

function FieldInput({
  id,
  spec,
  value,
  onChange,
  invalid,
  describedBy,
}: {
  id: string;
  spec: FieldSpec;
  value: string;
  onChange: (text: string) => void;
  invalid: boolean;
  describedBy: string | undefined;
}) {
  const common = {
    id,
    className: INPUT_CLASS,
    value,
    "aria-invalid": invalid ? true : undefined,
    "aria-describedby": describedBy,
  };
  if (spec.input === "yesno") {
    return (
      <select {...common} onChange={(e) => onChange(e.target.value)}>
        <option value="">Not known</option>
        <option value="yes">Yes</option>
        <option value="no">No</option>
      </select>
    );
  }
  if (spec.input === "date") return <input {...common} type="date" onChange={(e) => onChange(e.target.value)} />;
  return (
    <input
      {...common}
      autoComplete="off"
      inputMode={spec.input === "money" ? "decimal" : undefined}
      maxLength={spec.max}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

/**
 * "End this benefit" with a last day (household today by default, or the end
 * it has now when that is past) and an inline confirm. Later cycles drop out
 * and Fees stop counting it. A day after the end it has now brings it back up
 * to that day, and the confirm says so. An end the household set shows with a
 * way back.
 */
function EndPart({ b, today }: { b: RewardBenefit; today: string }) {
  const id = useId();
  const patch = usePatchBenefit();
  const resolve = useResolveBenefit();
  // The last day follows the benefit until one is typed or asked about, so a refetch never
  // leaves an old default, and never changes the day the confirm asks about.
  const [typedDay, setDay] = useState<string | null>(null);
  const day = typedDay ?? (b.ends_on !== null && b.ends_on < today ? b.ends_on : today);
  const [asking, setAsking] = useState(false);
  const { confirmRef, triggerRef } = useConfirmFocus(asking);
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const busy = patch.isPending || resolve.isPending;
  const done = {
    onSuccess: () => {
      setAsking(false);
      setProblem(null);
      setSaved("Saved.");
    },
    onError: (err: unknown) => {
      setSaved(null);
      setProblem(failText(err));
    },
  };
  const edited = b.edited_fields.includes("ends_on");
  const yours = b.origin === "app" ? b.ends_on !== null : edited;
  const question =
    b.ends_on !== null && day > b.ends_on
      ? `End ${b.name} on ${formatDate(day)}? That is after the end it has now, ${formatDate(b.ends_on)}, so it counts again up to that day.`
      : `End ${b.name} on ${formatDate(day)}? Later cycles drop out, and Fees stop counting it after that day.`;
  return (
    <div className="flex flex-col gap-2 border-t border-line pt-4">
      {yours && b.ends_on ? (
        <p className="m-0 flex flex-wrap items-center gap-2 text-sm text-fg">
          <span>Ends {formatDate(b.ends_on)}.</span>
          {b.origin === "chat" && <EditedBadge />}
        </p>
      ) : (
        <>
          {b.ends_on && <p className="m-0 text-sm text-fg-secondary">The chat says it ends {formatDate(b.ends_on)}.</p>}
          <label htmlFor={`${id}-day`} className="text-sm text-fg-secondary">
            Last day
          </label>
          <input
            id={`${id}-day`}
            className={INPUT_CLASS}
            type="date"
            value={day}
            onChange={(e) => {
              setDay(e.target.value);
              setAsking(false);
            }}
          />
          {asking ? (
            <div className="flex flex-col gap-2">
              <p id={`${id}-question`} className="m-0 text-sm text-fg">
                {question}
              </p>
              <div className="flex flex-wrap gap-2">
                <Button
                  ref={confirmRef}
                  variant="primary"
                  disabled={busy}
                  aria-describedby={`${id}-question`}
                  onClick={() => patch.mutate({ key: b.key, body: { set: { ends_on: day } } }, done)}
                >
                  End benefit
                </Button>
                <Button onClick={() => setAsking(false)}>Cancel</Button>
              </div>
            </div>
          ) : (
            <Button
              ref={triggerRef}
              className="w-full justify-center md:w-auto md:self-start"
              onClick={() => {
                setSaved(null);
                if (!day) {
                  setProblem("Pick the last day.");
                  return;
                }
                setProblem(null);
                setDay(day);
                setAsking(true);
              }}
            >
              End this benefit
            </Button>
          )}
        </>
      )}
      {b.origin === "chat" ? (
        <ChatChoice
          field="ends_on"
          conflict={changedFields(b).includes("ends_on")}
          edited={edited}
          busy={busy}
          onReset={() => patch.mutate({ key: b.key, body: { reset: ["ends_on"] } }, done)}
          onResolve={(choice) => resolve.mutate({ key: b.key, field: "ends_on", choice }, done)}
        />
      ) : (
        yours && (
          <Button size="sm" className="self-start" disabled={busy} onClick={() => patch.mutate({ key: b.key, body: { set: { ends_on: null } } }, done)}>
            Remove the end date
          </Button>
        )
      )}
      <FormStatus problem={problem} saved={saved} />
    </div>
  );
}

const CHIP = "inline-flex min-h-11 items-center gap-1 rounded-full border px-3 text-sm md:min-h-8";

/** Words alike in any case: the API keeps each word once, case ignored. */
function sameWord(a: string, b: string): boolean {
  return a.toUpperCase() === b.toUpperCase();
}

/**
 * "Matches 8 credits this year. 2 would move here from another benefit. 1
 * stays with another benefit. Changes 3 cycles." Where each row goes comes
 * from the rows: a longer word or a choice keeps a row where it is, and a tie
 * sends it to review.
 */
export function previewLine(p: Pick<TermPreview, "matches" | "rows" | "cycles">, key: string): string {
  const other = (k: string | null) => k !== null && k !== key;
  const moves = p.rows.filter((r) => other(r.now_key) && r.after_key === key).length;
  const stays = p.rows.filter((r) => other(r.now_key) && r.after_key === r.now_key).length;
  const ties = p.rows.filter((r) => r.tie).length;
  const parts = [`Matches ${countText(p.matches, "credit", "credits")} this year.`];
  if (moves > 0) parts.push(`${moves} would move here from another benefit.`);
  if (stays > 0) parts.push(`${countText(stays, "stays", "stay")} with another benefit.`);
  if (ties > 0) parts.push(`${ties} would tie with another benefit and wait for review.`);
  if (p.cycles.length > 0) parts.push(`Changes ${countText(p.cycles.length, "cycle", "cycles")}.`);
  return parts.join(" ");
}

/** How many preview rows and cycles show before "And N more." */
const PREVIEW_MAX = 10;

function money(cents: number): string {
  return formatMoney(cents, { cents: true });
}

/** "$10.00 from the chat", or "not used". */
function cycleUseText(cents: number, source: RewardCycleSource | null): string {
  return source === null ? "not used" : `${money(cents)} ${CYCLE_SOURCE_TEXT[source].toLowerCase()}`;
}

function More({ total }: { total: number }) {
  return total > PREVIEW_MAX ? <p className="m-0 text-sm text-fg-secondary">And {total - PREVIEW_MAX} more.</p> : null;
}

/**
 * What a typed word would do (plan 4.10): the credit rows it matches this
 * year, where each counts now and with the word, and the cycles whose use or
 * source would change. Nothing is saved.
 */
function PreviewDetails({ p }: { p: TermPreview }) {
  const where = (name: string | null, key: string | null, tie = false) => (tie ? "to review (a tie)" : key === null ? "not counted" : (name ?? "a benefit not shown"));
  if (p.rows.length + p.cycles.length === 0) return null;
  return (
    <div className="flex flex-col gap-3">
      {p.rows.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="eyebrow m-0">Credit rows it matches</p>
          <ul aria-label="Credit rows the word matches" className="m-0 flex list-none flex-col gap-2 p-0 text-sm">
            {p.rows.slice(0, PREVIEW_MAX).map((r) => (
              <li key={r.txn_key}>
                <p className="m-0 flex flex-wrap justify-between gap-x-3">
                  <span className="min-w-0 break-words text-fg">
                    {formatShortDate(r.date)} · {r.description}
                  </span>
                  <span className="tabular text-fg">{money(r.cents)}</span>
                </p>
                <p className="m-0 text-fg-secondary">
                  Now {where(r.now_name, r.now_key)}. With the word, {where(r.after_name, r.after_key, r.tie)}.
                </p>
              </li>
            ))}
          </ul>
          <More total={p.rows.length} />
        </div>
      )}
      {p.cycles.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="eyebrow m-0">Cycles that change</p>
          <ul aria-label="Cycles the word changes" className="m-0 flex list-none flex-col gap-1 p-0 text-sm text-fg">
            {p.cycles.slice(0, PREVIEW_MAX).map((c) => (
              <li key={`${c.benefit_key}:${c.start}`}>
                {c.benefit_name}, {formatMonthShort(c.start)}: {cycleUseText(c.used_cents_before, c.source_before)}, then {cycleUseText(c.used_cents_after, c.source_after)}.
              </li>
            ))}
          </ul>
          <More total={p.cycles.length} />
        </div>
      )}
    </div>
  );
}

/**
 * Match words (docs/plans/rewards-inputs.md 4.10), admin only: a statement
 * credit line that holds one counts for this credit. Saved words come off one
 * at a time. Suggested words (from the name) and the word seen on statements
 * save with one tap, never on their own. A typed word shows what it would
 * match before it is added. "This credit never shows on statements" turns
 * matching off.
 */
function MatchWordsPart({ b }: { b: RewardBenefit }) {
  const id = useId();
  const terms = useTerms(b.key, true);
  const save = useSaveTerms(b.key);
  const patch = usePatchBenefit();
  const input = useRef<HTMLInputElement>(null);
  const [typed, setTyped] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const t = terms.data;
  const words = t?.terms ?? [];
  const clean = termText(typed);
  const debounced = useDebounced(clean);
  const format = termProblem(typed);
  const known = words.some((w) => sameWord(w, clean));
  const ask = !b.match_off && format === null && clean.length >= 3 && !known && debounced === clean;
  const preview = useTermPreview(b.key, debounced, ask);
  const full = words.length >= 10;
  const busy = save.isPending || patch.isPending;

  function store(next: string[], done: string, after?: () => void) {
    setSaved(null);
    save.mutate(next, {
      onSuccess: () => {
        setProblem(null);
        setSaved(done);
        after?.();
        input.current?.focus();
      },
      onError: (err) => setProblem(failText(err)),
    });
  }

  function add(word: string, after?: () => void) {
    if (full) {
      setProblem("A benefit takes at most 10 match words.");
      return;
    }
    store([...words, word], `Saved ${word}.`, after);
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSaved(null);
    if (clean === "") return setProblem("Type a match word.");
    if (format) return setProblem(format);
    if (known) return setProblem("That word is saved already.");
    add(clean, () => setTyped(""));
  }

  // Quiet: what the word would match, or why it would not, as it is typed.
  let hint: string | null = null;
  if (format && (clean.length >= 3 || /[^\x20-\x7e]/.test(typed))) hint = format;
  else if (known) hint = "That word is saved already.";
  else if (ask && preview.isError) hint = preview.error instanceof ApiError ? preview.error.message : "Could not check the word.";
  else if (ask && preview.data) hint = previewLine(preview.data, b.key);
  else if (clean.length >= 3 && !format && !b.match_off) hint = "Checking this year's credits…";

  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-3 border-t border-line pt-4">
      <div>
        <h3 id={`${id}-title`} className="m-0 text-base font-medium text-fg">
          Match words
        </h3>
        <p className="m-0 text-sm text-fg-secondary">A statement credit line that holds one of these words counts for this credit.</p>
      </div>
      <SwitchRow
        label="This credit never shows on statements"
        hint="Turns matching off. The chat's numbers stand."
        checked={b.match_off}
        disabled={busy}
        onChange={(on) =>
          patch.mutate(
            { key: b.key, body: { match_off: on } },
            {
              onSuccess: () => {
                setProblem(null);
                setSaved(on ? "Matching is off." : "Matching is on.");
              },
              onError: (err) => setProblem(failText(err)),
            },
          )
        }
      />
      {!b.match_off &&
        (!t ? (
          <p className="m-0 text-sm text-fg-secondary">{terms.isError ? "Could not load the match words." : "Loading the match words…"}</p>
        ) : (
          <>
            {words.length === 0 ? (
              <p className="m-0 text-sm text-fg-secondary">No match words yet. Credit rows for it wait in the review.</p>
            ) : (
              <ul aria-label="Saved match words" className="m-0 flex list-none flex-wrap gap-2 p-0">
                {words.map((w) => (
                  <li key={w} className={`${CHIP} border-line bg-subtle pr-0 text-fg`}>
                    <span className="font-mono text-xs">{w}</span>
                    <button
                      type="button"
                      className="inline-flex h-11 w-11 items-center justify-center rounded-full text-fg-secondary hover:bg-active md:h-8 md:w-8"
                      aria-label={`Remove ${w}`}
                      disabled={busy}
                      onClick={() =>
                        store(
                          words.filter((x) => x !== w),
                          `Removed ${w}.`,
                        )
                      }
                    >
                      <X size={14} aria-hidden="true" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {(t.suggested.length > 0 || t.seen) && (
              <div className="flex flex-col gap-1">
                <p className="eyebrow m-0">Tap to save</p>
                <ul aria-label="Suggested match words" className="m-0 flex list-none flex-wrap gap-2 p-0">
                  {t.seen && (
                    <li>
                      <button
                        type="button"
                        className={`${CHIP} border-dashed border-fg-faint bg-card text-fg hover:bg-hover`}
                        disabled={busy || full}
                        onClick={() => add(t.seen!.term)}
                      >
                        <Plus size={14} aria-hidden="true" />
                        <span className="sr-only">Save</span>{" "}
                        <span className="font-mono text-xs">{t.seen.term}</span>{" "}
                        <span className="text-fg-secondary">· seen {countText(t.seen.count, "time", "times")} on statements</span>
                      </button>
                    </li>
                  )}
                  {t.suggested.map((w) => (
                    <li key={w}>
                      <button
                        type="button"
                        className={`${CHIP} border-dashed border-fg-faint bg-card text-fg hover:bg-hover`}
                        disabled={busy || full}
                        onClick={() => add(w)}
                      >
                        <Plus size={14} aria-hidden="true" />
                        <span className="sr-only">Save</span>{" "}
                        <span className="font-mono text-xs">{w}</span>{" "}
                        <span className="text-fg-secondary">· from the name</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <form onSubmit={onSubmit} noValidate className="flex flex-col gap-1">
              <label htmlFor={`${id}-word`} className="text-sm text-fg-secondary">
                Add a match word
              </label>
              <div className="flex gap-2">
                <input
                  ref={input}
                  id={`${id}-word`}
                  className={INPUT_CLASS}
                  value={typed}
                  maxLength={40}
                  autoComplete="off"
                  autoCapitalize="characters"
                  spellCheck={false}
                  aria-describedby={`${id}-hint`}
                  onChange={(e) => {
                    setTyped(e.target.value);
                    setSaved(null);
                    setProblem(null);
                  }}
                />
                <Button type="submit" className="shrink-0" disabled={busy || full}>
                  Add
                </Button>
              </div>
              <p id={`${id}-hint`} className="m-0 min-h-5 text-sm text-fg-secondary" aria-live="polite">
                {full ? "A benefit takes at most 10 match words." : (hint ?? "3 to 40 characters from the credit line, like the merchant.")}
              </p>
            </form>
            {!full && ask && preview.data && <PreviewDetails p={preview.data} />}
          </>
        ))}
      <FormStatus problem={problem} saved={saved} />
    </section>
  );
}

/**
 * A credit's cycles that have started, newest first: the state in a word,
 * where the use comes from, the statement credits matched to it, and marks
 * (anyone signed in). A mark wins over the rows and the chat until cleared.
 */
function CyclesPart({ b, today, first = false }: { b: RewardBenefit; today: string; first?: boolean }) {
  const id = useId();
  const cycles = startedCycles(b, today);
  return (
    <section aria-labelledby={`${id}-title`} className={first ? "flex flex-col gap-2" : "flex flex-col gap-2 border-t border-line pt-4"}>
      <div>
        <h3 id={`${id}-title`} className="m-0 text-base font-medium text-fg">
          Cycles
        </h3>
        <p className="m-0 text-sm text-fg-secondary">Mark a cycle when the statements and the chat get it wrong. A mark wins until you clear it.</p>
      </div>
      {cycles.length === 0 ? (
        <p className="m-0 text-sm text-fg-secondary">No cycle has started yet.</p>
      ) : (
        <ul aria-label={`${b.name} cycles`} className="m-0 flex list-none flex-col p-0">
          {cycles.map((c) => (
            <CycleItem key={c.start} b={b} c={c} today={today} />
          ))}
        </ul>
      )}
    </section>
  );
}

function CycleItem({ b, c, today }: { b: RewardBenefit; c: RewardCycle; today: string }) {
  const ref = useRef<HTMLLIElement>(null);
  const mark = useSetMark();
  const clear = useClearMark();
  const move = useSetCredit();
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const busy = mark.isPending || clear.isPending || move.isPending;
  const label = `${formatShortDate(c.start)} to ${formatShortDate(c.end)}`;
  const notes = cycleNotes(c, matches(b));
  const set = c.source === "set";
  // The button pressed may go, so focus stays on the cycle, and the status says what was saved.
  const done = (what: string) => {
    setSaved(null);
    return {
      onSuccess: () => {
        setProblem(null);
        setSaved(what);
        ref.current?.focus();
      },
      onError: (err: unknown) => setProblem(failText(err)),
    };
  };
  return (
    <li ref={ref} tabIndex={-1} aria-label={label} className="border-t border-line py-3 outline-none first:border-t-0">
      <p className="m-0 flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-fg">{label}</span>
        <Badge tone={CYCLE_TONE[c.state]}>{CYCLE_TEXT[c.state]}</Badge>
      </p>
      <p className="m-0 text-sm text-fg">
        {formatMoney(c.used_cents, { cents: true })} of {formatMoney(c.amount_cents, { cents: true })}
        {c.used_on && ` · used ${formatShortDate(c.used_on)}`}
      </p>
      {notes.length > 0 && <p className="m-0 text-sm text-fg-secondary">{notes.join(" · ")}</p>}
      {c.rows.length > 0 && (
        <ul aria-label={`Statement credits, ${label}`} className="m-0 mt-1 flex list-none flex-col gap-1 p-0">
          {c.rows.map((r) => (
            <li key={r.txn_key} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 text-sm">
              <span className="min-w-0 text-fg-secondary">
                {formatShortDate(r.date)} · <span className="break-words">{r.description}</span>
              </span>
              <span className="flex items-center gap-2">
                <span className="tabular text-fg">{formatMoney(r.cents, { cents: true })}</span>
                {r.earlier_start && (
                  <Button
                    size="sm"
                    disabled={busy}
                    onClick={() =>
                      move.mutate(
                        { txnKey: r.txn_key, body: { decision: "link", benefit_key: b.key, cycle_start: r.earlier_start } },
                        done(`Counted in ${formatMonthShort(r.earlier_start!)}.`),
                      )
                    }
                  >
                    Count in {formatMonthShort(r.earlier_start)} instead<span className="sr-only">: {r.description}</span>
                  </Button>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-2 flex flex-wrap gap-2">
        {!(set && c.used_cents > 0) && (
          <Button
            size="sm"
            disabled={busy}
            onClick={() => mark.mutate({ key: b.key, start: c.start, used: moneyText(c.amount_cents), usedOn: markUsedOn(c, today) }, done("Marked used."))}
          >
            Mark used<span className="sr-only">: {label}</span>
          </Button>
        )}
        {!(set && c.used_cents === 0) && (
          <Button size="sm" disabled={busy} onClick={() => mark.mutate({ key: b.key, start: c.start, used: "0.00", usedOn: null }, done("Marked not used."))}>
            Mark not used<span className="sr-only">: {label}</span>
          </Button>
        )}
        {set && (
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => clear.mutate({ key: b.key, start: c.start }, done("Mark cleared."))}>
            Clear{" "}<span className="sr-only">the mark: {label}</span>
          </Button>
        )}
      </div>
      <FormStatus problem={problem} saved={saved} />
    </li>
  );
}

/** Hide: the benefit leaves the list and every total, and waits under Hidden. */
function HidePart({ benefitKey, onHidden, first = false }: { benefitKey: string; onHidden: () => void; first?: boolean }) {
  const patch = usePatchBenefit();
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <div className={first ? "flex flex-col gap-2" : "flex flex-col gap-2 border-t border-line pt-4"}>
      <p className="m-0 text-sm text-fg-secondary">A hidden benefit leaves the list and every total. It waits under Hidden until you show it again.</p>
      <Button
        className="w-full justify-center md:w-auto md:self-start"
        disabled={patch.isPending}
        onClick={() =>
          patch.mutate({ key: benefitKey, body: { hidden: true } }, { onSuccess: onHidden, onError: (err) => setProblem(failText(err)) })
        }
      >
        Hide this benefit
      </Button>
      <FormStatus problem={problem} saved={null} />
    </div>
  );
}

/** Delete, for a benefit added in the app: it goes for good, with its match words, credit choices and cycle marks. */
function DeletePart({ benefitKey, name, onDeleted }: { benefitKey: string; name: string; onDeleted: () => void }) {
  const del = useDeleteBenefit();
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <div className="flex flex-col gap-2 border-t border-line pt-4">
      <p className="m-0 text-sm text-fg-secondary">You added this benefit in the app. Delete takes it off for good, with its match words, credit choices and cycle marks.</p>
      <ConfirmAction
        ask="Delete this benefit"
        question={`Delete ${name}?`}
        confirmLabel="Delete benefit"
        busy={del.isPending}
        size="md"
        triggerClassName="w-full justify-center md:w-auto md:self-start"
        onConfirm={() => del.mutate(benefitKey, { onSuccess: onDeleted, onError: (err) => setProblem(failText(err)) })}
      />
      <FormStatus problem={problem} saved={null} />
    </div>
  );
}

