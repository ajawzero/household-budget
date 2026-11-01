import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, CircleAlert } from "lucide-react";
import { type FormEvent, type ReactNode, useId, useState } from "react";
import { useNavigate } from "react-router";
import { Button } from "../../../components/ui/button";
import { Drawer } from "../../../components/ui/drawer";
import { SegmentedControl } from "../../../components/ui/segmented-control";
import { ApiError, type PlanPerson, type PlanRequestBody, startTripPlan, tripPlanKeys } from "../../../lib/api";
import { cn } from "../../../lib/cn";
import { chicagoToday } from "../../../lib/format";
import { INPUT_CLASS } from "../../accounts/vehicles/fields";
import {
  buildRequest,
  EMPTY_FORM,
  estimateText,
  INTERESTS,
  LEAN_OPTIONS,
  MAX_GUESTS,
  MAX_NIGHTS,
  NOTE_MAX,
  PACE_OPTIONS,
  type PlanForm as Form,
  planHref,
  WHERE_MAX,
} from "./model";

interface PlanFormProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  people: PlanPerson[];
  estimateCents: number;
}

/**
 * "Plan a trip": where, when, who, home airport, interests, a note, pace and
 * lean. A sheet (full screen on phones). On start it opens the plan page,
 * which shows the plan being made.
 */
export function PlanFormDrawer({ open, onOpenChange, people, estimateCents }: PlanFormProps) {
  return (
    <Drawer open={open} onOpenChange={onOpenChange} title="Plan a trip" widthClass="md:w-[520px]">
      {/* A fresh form each time it opens. */}
      {open && <PlanFormBody people={people} estimateCents={estimateCents} onDone={() => onOpenChange(false)} />}
    </Drawer>
  );
}

const TOGGLE =
  "inline-flex h-11 items-center rounded-full border px-3 text-sm transition duration-100 md:h-8 aria-pressed:border-transparent aria-pressed:bg-accent-tint aria-pressed:text-accent-text";

/** A check on a pressed chip, so the state is not shown by color alone. */
function Pressed({ on }: { on: boolean }) {
  return on ? <Check size={14} aria-hidden="true" className="-ml-0.5 mr-1 shrink-0" /> : null;
}

function PlanFormBody({ people, estimateCents, onDone }: { people: PlanPerson[]; estimateCents: number; onDone: () => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const id = useId();
  const today = chicagoToday();
  const [form, setForm] = useState<Form>(EMPTY_FORM);
  const [problem, setProblem] = useState<string | null>(null);
  const set = <K extends keyof Form>(key: K, value: Form[K]) => setForm((f) => ({ ...f, [key]: value }));
  const toggle = <T extends string>(list: T[], value: T) => (list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);

  const start = useMutation({
    mutationFn: (body: PlanRequestBody) => startTripPlan(body),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: tripPlanKeys.all });
      onDone();
      navigate(planHref(r.plan_id));
    },
    onError: (err) => setProblem(err instanceof ApiError ? err.message : "Could not start the plan. Try again."),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const built = buildRequest(form, today);
    if ("problem" in built) {
      setProblem(built.problem);
      return;
    }
    setProblem(null);
    start.mutate(built.body);
  }

  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-5" aria-describedby={problem ? `${id}-problem` : undefined}>
      <Field label="Where" htmlFor={`${id}-where`} help="A city, a region or a few stops.">
        <input
          id={`${id}-where`}
          className={INPUT_CLASS}
          maxLength={WHERE_MAX}
          autoComplete="off"
          placeholder="Lisbon and Porto"
          value={form.where}
          onChange={(e) => set("where", e.target.value)}
        />
      </Field>

      <fieldset className="m-0 flex min-w-0 flex-col gap-2 border-0 p-0">
        <legend className="mb-2 p-0 text-sm font-medium text-fg">When</legend>
        <SegmentedControl
          label="Dates or a month"
          value={form.when}
          onChange={(v) => set("when", v)}
          options={[
            { value: "dates", label: "Dates" },
            { value: "month", label: "A month" },
          ]}
        />
        {form.when === "dates" ? (
          <div className="flex flex-wrap gap-3">
            <SmallField label="First night" htmlFor={`${id}-start`}>
              <input id={`${id}-start`} type="date" className={INPUT_CLASS} min={today} value={form.start} onChange={(e) => set("start", e.target.value)} />
            </SmallField>
            <SmallField label="Day home" htmlFor={`${id}-end`}>
              <input id={`${id}-end`} type="date" className={INPUT_CLASS} min={form.start || today} value={form.end} onChange={(e) => set("end", e.target.value)} />
            </SmallField>
          </div>
        ) : (
          <div className="flex flex-wrap gap-3">
            <SmallField label="Month" htmlFor={`${id}-month`}>
              <input
                id={`${id}-month`}
                type="month"
                className={INPUT_CLASS}
                min={today.slice(0, 7)}
                placeholder="2027-05"
                value={form.month}
                onChange={(e) => set("month", e.target.value)}
              />
            </SmallField>
            <SmallField label="Nights" htmlFor={`${id}-nights`}>
              <input
                id={`${id}-nights`}
                className={INPUT_CLASS}
                inputMode="numeric"
                autoComplete="off"
                placeholder={`1 to ${MAX_NIGHTS}`}
                value={form.nights}
                onChange={(e) => set("nights", e.target.value)}
              />
            </SmallField>
          </div>
        )}
      </fieldset>

      <fieldset className="m-0 flex min-w-0 flex-col gap-2 border-0 p-0">
        <legend className="mb-2 p-0 text-sm font-medium text-fg">Who is going</legend>
        {people.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {people.map((p) => (
              <button
                key={p.key}
                type="button"
                aria-pressed={form.people.includes(p.key)}
                className={cn(TOGGLE, "border-line text-fg hover:bg-hover")}
                onClick={() => set("people", toggle(form.people, p.key))}
              >
                <Pressed on={form.people.includes(p.key)} />
                {p.name}
              </button>
            ))}
          </div>
        )}
        <SmallField label="Guests" htmlFor={`${id}-guests`} help="People outside the household.">
          <input
            id={`${id}-guests`}
            className={cn(INPUT_CLASS, "max-w-28")}
            inputMode="numeric"
            autoComplete="off"
            placeholder={`0 to ${MAX_GUESTS}`}
            value={form.guests}
            onChange={(e) => set("guests", e.target.value)}
          />
        </SmallField>
      </fieldset>

      <Field label="Home airport" htmlFor={`${id}-airport`} help="Optional. Three letters. Sent to the web search for flights.">
        <input
          id={`${id}-airport`}
          className={cn(INPUT_CLASS, "max-w-28 uppercase")}
          maxLength={3}
          autoComplete="off"
          autoCapitalize="characters"
          placeholder="MSP"
          value={form.airport}
          onChange={(e) => set("airport", e.target.value)}
        />
      </Field>

      <fieldset className="m-0 flex min-w-0 flex-col gap-2 border-0 p-0">
        <legend className="mb-2 p-0 text-sm font-medium text-fg">Interests</legend>
        <div className="flex flex-wrap gap-2">
          {INTERESTS.map((i) => (
            <button
              key={i.value}
              type="button"
              aria-pressed={form.interests.includes(i.value)}
              className={cn(TOGGLE, "border-line text-fg hover:bg-hover")}
              onClick={() => set("interests", toggle(form.interests, i.value))}
            >
              <Pressed on={form.interests.includes(i.value)} />
              {i.label}
            </button>
          ))}
        </div>
      </fieldset>

      <Field label="Anything else" htmlFor={`${id}-note`} help="Optional. Like a beach day or no red-eyes.">
        <textarea
          id={`${id}-note`}
          className={cn(INPUT_CLASS, "h-20 py-2 md:h-20")}
          maxLength={NOTE_MAX}
          value={form.note}
          onChange={(e) => set("note", e.target.value)}
        />
      </Field>

      <div className="flex flex-wrap gap-x-6 gap-y-4">
        <div className="flex flex-col gap-2">
          <span className="text-sm font-medium text-fg">Pace</span>
          <SegmentedControl label="Pace" value={form.pace} onChange={(v) => set("pace", v)} options={PACE_OPTIONS} />
        </div>
        <div className="flex flex-col gap-2">
          <span className="text-sm font-medium text-fg">Pay with</span>
          <SegmentedControl label="Pay with" value={form.lean} onChange={(v) => set("lean", v)} options={LEAN_OPTIONS} />
        </div>
      </div>

      <div aria-live="polite">
        {problem && (
          <p id={`${id}-problem`} className="m-0 flex items-start gap-1.5 text-sm text-bad">
            <CircleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
            {problem}
          </p>
        )}
      </div>

      <div className="flex flex-col gap-1 border-t border-line pt-4">
        <Button type="submit" variant="primary" className="justify-center" disabled={start.isPending}>
          {start.isPending ? "Starting…" : "Make the plan"}
        </Button>
        <p className="m-0 text-center text-xs text-fg-secondary">
          {estimateText(estimateCents)}, counts toward the AI limit. The web search gets the trip details. Names, balances and cards are taken out.
        </p>
      </div>
    </form>
  );
}

function Field({ label, htmlFor, help, children }: { label: string; htmlFor: string; help?: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <label htmlFor={htmlFor} className="text-sm font-medium text-fg">
        {label}
      </label>
      {children}
      {help && <p className="m-0 text-xs text-fg-secondary">{help}</p>}
    </div>
  );
}

function SmallField({ label, htmlFor, help, children }: { label: string; htmlFor: string; help?: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-1 basis-36 flex-col gap-1">
      <label htmlFor={htmlFor} className="text-sm text-fg-secondary">
        {label}
      </label>
      {children}
      {help && <p className="m-0 text-xs text-fg-secondary">{help}</p>}
    </div>
  );
}
