import { ListFilter, X } from "lucide-react";
import { type ReactNode, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { Button } from "../../components/ui/button";
import { SegmentedControl } from "../../components/ui/segmented-control";
import type { CategoriesResponse } from "../../lib/api";
import { cn } from "../../lib/cn";
import { addMonths, formatMonth } from "../../lib/format";
import { scrollBarTo } from "../../lib/indicator";
import { INPUT } from "./controls";
import { clearFilters, filterCount, toggle, type TxnFilterState, type YesNo } from "./filters";

export interface Option {
  key: string;
  label: string;
}

interface FilterPopoverProps {
  state: TxnFilterState;
  onChange: (next: TxnFilterState) => void;
  accounts: Option[];
  owners: Option[];
  /** Trips from Travel, by name. */
  trips?: Option[];
  /** True once Travel's trip list has loaded. Until then no trip is called missing. */
  tripsReady?: boolean;
  /** The filtered trip's name from a loaded row, for when Travel's list is not in. */
  tripName?: string | null;
  categories: CategoriesResponse | undefined;
  /** "2026-10", for the month list. */
  thisMonth: string | null;
}

const SECTIONS = [
  { value: "accounts", label: "Accounts" },
  { value: "categories", label: "Categories" },
  { value: "people", label: "People" },
  { value: "dates", label: "Dates" },
  { value: "amount", label: "Amount" },
  { value: "flags", label: "Flags" },
  { value: "trips", label: "Trips" },
] as const;
type Section = (typeof SECTIONS)[number]["value"];

/**
 * The Filters button and its popover. Every change applies at once (the page
 * keeps filters in the URL). Esc or a click outside closes it. On phones it is
 * a sheet from the bottom.
 */
export function FilterPopover(props: FilterPopoverProps) {
  const [open, setOpen] = useState(false);
  const [section, setSection] = useState<Section>("accounts");
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const panelId = useId();
  const count = filterCount(props.state);

  // The phone's section bar scrolls sideways and opens at the start. Bring the
  // open section into view (reopening on Trips shows Trips). Does nothing at md.
  useLayoutEffect(() => {
    const list = listRef.current;
    const item = list?.querySelector<HTMLElement>('[aria-current="true"]');
    if (open && list && item) scrollBarTo(list, item.offsetLeft, item.offsetWidth);
  }, [open, section]);

  useEffect(() => {
    if (!open) return;
    panelRef.current?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    }
    function onDown(e: PointerEvent) {
      const t = e.target as Node;
      if (panelRef.current?.contains(t) || buttonRef.current?.contains(t)) return;
      setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [open]);

  return (
    <div className="relative">
      <Button
        ref={buttonRef}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((o) => !o)}
      >
        <ListFilter size={16} aria-hidden="true" />
        Filters
        {count > 0 && (
          <span className="tabular inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-primary px-1.5 text-xs text-on-primary">
            {count}
            {" "}<span className="sr-only">set</span>
          </span>
        )}
      </Button>
      {open && (
        <>
          <div className="fixed inset-0 z-40 bg-overlay md:hidden" aria-hidden="true" />
          <div
            ref={panelRef}
            id={panelId}
            role="dialog"
            aria-label="Filters"
            tabIndex={-1}
            className={cn(
              "z-50 flex flex-col bg-card text-fg shadow-pop outline-none",
              "fixed inset-x-0 bottom-0 max-h-[85dvh] rounded-t-card pb-[env(safe-area-inset-bottom)]",
              "md:absolute md:inset-x-auto md:bottom-auto md:right-0 md:top-full md:mt-2 md:max-h-[min(560px,80dvh)] md:w-[600px] md:rounded-card md:pb-0",
            )}
          >
            <div className="flex items-center gap-2 border-b border-line px-4 py-2">
              <p className="m-0 flex-1 text-base font-medium">Filters</p>
              <button
                type="button"
                onClick={() => {
                  setOpen(false);
                  buttonRef.current?.focus();
                }}
                aria-label="Close filters"
                className="inline-flex h-11 w-11 items-center justify-center rounded-lg text-fg-secondary hover:bg-active md:h-9 md:w-9"
              >
                <X size={18} aria-hidden="true" />
              </button>
            </div>
            <div className="flex min-h-0 flex-1 flex-col md:flex-row">
              <nav aria-label="Filter sections" className="shrink-0 border-line md:w-40 md:border-r md:py-2">
                <ul ref={listRef} className="relative m-0 flex list-none gap-1 overflow-x-auto px-3 py-2 md:flex-col md:px-2 md:py-0">
                  {SECTIONS.map((s) => {
                    const n = sectionCount(props.state, s.value);
                    return (
                      <li key={s.value} className="shrink-0">
                        <button
                          type="button"
                          aria-current={section === s.value ? "true" : undefined}
                          onClick={() => setSection(s.value)}
                          className={cn(
                            "flex h-11 w-full items-center justify-between gap-2 rounded-lg px-3 text-left text-sm md:h-9",
                            section === s.value ? "bg-active font-medium text-fg" : "text-fg-secondary hover:bg-hover",
                          )}
                        >
                          {s.label}
                          {n > 0 && <span className="tabular text-xs text-fg-secondary">{n}</span>}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </nav>
              <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
                <SectionBody section={section} {...props} />
              </div>
            </div>
            <div className="flex items-center justify-between gap-2 border-t border-line px-4 py-2">
              <Button variant="ghost" onClick={() => props.onChange(clearFilters(props.state))} disabled={count === 0}>
                Clear all
              </Button>
              <Button
                variant="primary"
                onClick={() => {
                  setOpen(false);
                  buttonRef.current?.focus();
                }}
              >
                Done
              </Button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function sectionCount(s: TxnFilterState, section: Section): number {
  switch (section) {
    case "accounts":
      return s.accounts.length;
    case "categories":
      return s.categories.length + s.groups.length;
    case "people":
      return s.owners.length;
    case "dates":
      return s.months.length + (s.from ? 1 : 0) + (s.to ? 1 : 0);
    case "amount":
      return (s.min ? 1 : 0) + (s.max ? 1 : 0) + (s.direction ? 1 : 0);
    case "flags":
      return (s.transfer ? 1 : 0) + (s.reimbursable ? 1 : 0) + (s.edited ? 1 : 0) + (s.hidden !== "exclude" ? 1 : 0);
    case "trips":
      return s.trip ? 1 : 0;
  }
}

function Check({ checked, onChange, children }: { checked: boolean; onChange: () => void; children: ReactNode }) {
  return (
    <label className="flex min-h-11 cursor-pointer items-center gap-3 rounded-lg px-2 text-sm hover:bg-hover md:min-h-9">
      <input type="checkbox" checked={checked} onChange={onChange} className="h-4 w-4 shrink-0 accent-[var(--primary)]" />
      <span className="min-w-0">{children}</span>
    </label>
  );
}

function Radio({ name, checked, onChange, children }: { name: string; checked: boolean; onChange: () => void; children: ReactNode }) {
  return (
    <label className="flex min-h-11 cursor-pointer items-center gap-3 rounded-lg px-2 text-sm hover:bg-hover md:min-h-9">
      <input type="radio" name={name} checked={checked} onChange={onChange} className="h-4 w-4 shrink-0 accent-[var(--primary)]" />
      <span className="min-w-0">{children}</span>
    </label>
  );
}

function Fieldset({ legend, hint, children }: { legend: string; hint?: string; children: ReactNode }) {
  return (
    <fieldset className="m-0 mb-4 border-0 p-0 last:mb-0">
      <legend className="mb-1 p-0 text-xs font-medium uppercase tracking-wider text-fg-secondary">{legend}</legend>
      {hint && <p className="m-0 mb-1 text-xs text-fg-secondary">{hint}</p>}
      {children}
    </fieldset>
  );
}

function SectionBody({
  section,
  state,
  onChange,
  accounts,
  owners,
  trips = [],
  tripsReady = true,
  tripName = null,
  categories,
  thisMonth,
}: FilterPopoverProps & { section: Section }) {
  const set = (patch: Partial<TxnFilterState>) => onChange({ ...state, ...patch });

  if (section === "accounts") {
    return (
      <Fieldset legend="Accounts">
        {accounts.map((a) => (
          <Check key={a.key} checked={state.accounts.includes(a.key)} onChange={() => set({ accounts: toggle(state.accounts, a.key) })}>
            {a.label}
          </Check>
        ))}
        {accounts.length === 0 && <p className="m-0 text-sm text-fg-secondary">No accounts yet.</p>}
      </Fieldset>
    );
  }

  if (section === "categories") {
    // The server combines groups and categories with AND, so the two are kept apart:
    // picking a group clears single categories, and the other way round.
    return (
      <>
        <Fieldset legend="Groups" hint="Pick whole groups or single categories, not both.">
          {categories?.groups.map((g) => (
            <Check
              key={g.group_key}
              checked={state.groups.includes(g.group_key)}
              onChange={() => set({ groups: toggle(state.groups, g.group_key), categories: [] })}
            >
              {g.name}
            </Check>
          ))}
        </Fieldset>
        {categories?.groups.map((g) => (
          <Fieldset key={g.group_key} legend={g.name}>
            {g.categories.map((c) => (
              <Check
                key={c.category_key}
                checked={state.categories.includes(c.category_key)}
                onChange={() => set({ categories: toggle(state.categories, c.category_key), groups: [] })}
              >
                <span aria-hidden="true" className="mr-1.5 inline-block w-5 text-center">
                  {c.emoji ?? ""}
                </span>
                {c.name}
              </Check>
            ))}
          </Fieldset>
        ))}
      </>
    );
  }

  if (section === "people") {
    return (
      <Fieldset legend="Cardholder or owner" hint="The cardholder when a row has one, else the account owner.">
        {owners.map((o) => (
          <Check key={o.key} checked={state.owners.includes(o.key)} onChange={() => set({ owners: toggle(state.owners, o.key) })}>
            {o.label}
          </Check>
        ))}
      </Fieldset>
    );
  }

  if (section === "dates") {
    const months = thisMonth ? Array.from({ length: 12 }, (_, i) => addMonths(thisMonth, -i)) : [];
    return (
      <>
        <Fieldset legend="Date range">
          <div className="grid grid-cols-2 gap-3">
            <label className="flex flex-col gap-1 text-xs font-medium text-fg-secondary">
              From
              <input type="date" className={INPUT} value={state.from ?? ""} onChange={(e) => set({ from: e.target.value || null })} />
            </label>
            <label className="flex flex-col gap-1 text-xs font-medium text-fg-secondary">
              To
              <input type="date" className={INPUT} value={state.to ?? ""} onChange={(e) => set({ to: e.target.value || null })} />
            </label>
          </div>
        </Fieldset>
        <Fieldset legend="Months">
          <div className="grid grid-cols-2 gap-x-2">
            {months.map((m) => (
              <Check key={m} checked={state.months.includes(m)} onChange={() => set({ months: toggle(state.months, m) })}>
                {formatMonth(m)}
              </Check>
            ))}
          </div>
        </Fieldset>
      </>
    );
  }

  if (section === "trips") {
    const known = trips.some((t) => t.key === state.trip);
    return (
      <Fieldset legend="Trip" hint="Rows tagged with the trip, whatever their date.">
        <Radio name="trip" checked={state.trip === null} onChange={() => set({ trip: null })}>
          Any or none
        </Radio>
        {trips.map((t) => (
          <Radio key={t.key} name="trip" checked={state.trip === t.key} onChange={() => set({ trip: t.key })}>
            {t.label}
          </Radio>
        ))}
        {state.trip && !known && (
          <Radio name="trip" checked onChange={() => undefined}>
            {tripsReady ? `${state.trip} (not in Travel)` : (tripName ?? state.trip)}
          </Radio>
        )}
        {tripsReady && trips.length === 0 && <p className="m-0 text-sm text-fg-secondary">No trips yet.</p>}
      </Fieldset>
    );
  }

  if (section === "amount") {
    return (
      <>
        <Fieldset legend="Direction">
          <SegmentedControl
            label="Direction"
            value={state.direction ?? "any"}
            options={[
              { value: "any", label: "Any" },
              { value: "out", label: "Money out" },
              { value: "in", label: "Money in" },
            ]}
            onChange={(v) => set({ direction: v === "any" ? null : v })}
          />
        </Fieldset>
        <Fieldset legend="Amount" hint="Whole dollars or dollars and cents. Compared without the sign.">
          <div className="grid grid-cols-2 gap-3">
            <DollarInput label="At least" value={state.min} onCommit={(v) => set({ min: v })} />
            <DollarInput label="At most" value={state.max} onCommit={(v) => set({ max: v })} />
          </div>
        </Fieldset>
      </>
    );
  }

  return (
    <>
      <TriState legend="Transfers" value={state.transfer} yes="Only transfers" no="Leave out" onChange={(v) => set({ transfer: v })} />
      <TriState
        legend="Reimbursable"
        value={state.reimbursable}
        yes="Only reimbursable"
        no="Leave out"
        onChange={(v) => set({ reimbursable: v })}
      />
      <TriState legend="Edited" value={state.edited} yes="Edited" no="Not edited" onChange={(v) => set({ edited: v })} />
      <Fieldset legend="Hidden">
        <SegmentedControl
          label="Hidden"
          value={state.hidden}
          options={[
            { value: "exclude", label: "Leave out" },
            { value: "include", label: "Include" },
            { value: "only", label: "Only hidden" },
          ]}
          onChange={(v) => set({ hidden: v })}
        />
      </Fieldset>
    </>
  );
}

function TriState({
  legend,
  value,
  yes,
  no,
  onChange,
}: {
  legend: string;
  value: YesNo | null;
  yes: string;
  no: string;
  onChange: (v: YesNo | null) => void;
}) {
  return (
    <Fieldset legend={legend}>
      <SegmentedControl
        label={legend}
        value={value ?? "any"}
        options={[
          { value: "any", label: "Any" },
          { value: "yes", label: yes },
          { value: "no", label: no },
        ]}
        onChange={(v) => onChange(v === "any" ? null : v)}
      />
    </Fieldset>
  );
}

const DOLLARS = /^[0-9]{1,9}(\.[0-9]{1,2})?$/;

/** A dollar box that applies on blur or Enter, and only when the text is a plain amount. */
function DollarInput({ label, value, onCommit }: { label: string; value: string | null; onCommit: (v: string | null) => void }) {
  const [text, setText] = useState(value ?? "");
  const [bad, setBad] = useState(false);
  const id = useId();
  useEffect(() => setText(value ?? ""), [value]);

  function commit() {
    const t = text.replace(/[$,\s]/g, "");
    if (t === "") {
      setBad(false);
      if (value !== null) onCommit(null);
    } else if (DOLLARS.test(t)) {
      setBad(false);
      if (t !== value) onCommit(t);
    } else setBad(true);
  }

  return (
    <label className="flex flex-col gap-1 text-xs font-medium text-fg-secondary">
      {label}
      <input
        inputMode="decimal"
        className={INPUT}
        value={text}
        placeholder="$0"
        aria-invalid={bad || undefined}
        aria-describedby={bad ? id : undefined}
        onChange={(e) => setText(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit();
        }}
      />
      {bad && (
        <span id={id} className="text-xs text-bad">
          Use a plain amount, like 25 or 12.50.
        </span>
      )}
    </label>
  );
}
