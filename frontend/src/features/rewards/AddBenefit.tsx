import { useQuery } from "@tanstack/react-query";
import { type FormEvent, useId, useRef, useState } from "react";
import { Button } from "../../components/ui/button";
import { Drawer } from "../../components/ui/drawer";
import {
  type AccountsResponse,
  type BenefitCycleIn,
  type BenefitIn,
  getAccounts,
  queryKeys,
  type RewardProgram,
} from "../../lib/api";
import { INPUT_CLASS } from "../accounts/vehicles/fields";
import { useAddBenefit } from "./api";
import { CADENCE_TEXT, MONEY_RULE, parseMoney, STORED_CYCLES } from "./model";
import { FormStatus } from "./parts";
import { failText, programLabel } from "./ProgramNotices";

type Kind = "credit" | "free_night" | "other";
type Cadence = NonNullable<BenefitIn["cadence"]>;

const KINDS: { key: Kind; label: string }[] = [
  { key: "credit", label: "Credit" },
  { key: "free_night", label: "Free night" },
  { key: "other", label: "Other" },
];

const CADENCES = Object.keys(CADENCE_TEXT) as Cadence[];

/** The API takes benefits on credit and store cards only. */
const CARD_TYPES = ["credit_card", "store_card"];

/** At most 20 stored cycles, like the API. */
const CYCLES_MAX = 20;

export interface CycleText {
  start: string;
  end: string;
  amount: string;
}

export interface AddForm {
  card: string;
  name: string;
  kind: Kind;
  cadence: Cadence;
  amount: string;
  program: string;
  useBy: string;
  cycles: CycleText[];
}

/** Open credit and store cards, by name. */
export function cardOptions(data: AccountsResponse | undefined): { key: string; name: string }[] {
  const seen = new Map<string, string>();
  for (const a of data?.groups.flatMap((g) => g.accounts) ?? []) {
    if (a.active && CARD_TYPES.includes(a.type)) seen.set(a.account_key, a.display_name);
  }
  return [...seen].map(([key, name]) => ({ key, name })).sort((a, b) => a.name.localeCompare(b.name));
}

/**
 * The form's text to the API body, or the first problem. Credits need a
 * cadence and an amount. Multi-year and one-time credits list their cycles
 * (a cycle's amount only when it differs). A free night needs its program.
 */
export function benefitBody(f: AddForm): BenefitIn | string {
  const name = f.name.trim();
  if (f.card === "") return "Pick a card.";
  if (name === "") return "Enter a name.";
  const base = { account_key: f.card, name, kind: f.kind };
  if (f.kind === "free_night") {
    if (f.program === "") return "Pick the hotel program.";
    return f.useBy ? { ...base, program_key: f.program, ends_on: f.useBy } : { ...base, program_key: f.program };
  }
  if (f.kind === "other") return base;
  const amount = parseMoney(f.amount);
  if (amount === null) return "Enter the amount.";
  if (amount === undefined) return MONEY_RULE;
  if (!STORED_CYCLES.includes(f.cadence)) return { ...base, cadence: f.cadence, amount };
  const cycles: BenefitCycleIn[] = [];
  for (const c of f.cycles) {
    if (!c.start || !c.end) return "Enter a start and an end for each cycle.";
    if (c.start > c.end) return "Each cycle ends on or after its start.";
    const own = parseMoney(c.amount);
    if (own === undefined) return MONEY_RULE;
    cycles.push(own === null || own === amount ? { start: c.start, end: c.end } : { start: c.start, end: c.end, amount: own });
  }
  if (cycles.length === 0) return "Add at least one cycle.";
  if (new Set(cycles.map((c) => c.start)).size !== cycles.length) return "Each cycle needs its own start date.";
  return { ...base, cadence: f.cadence, amount, cycles };
}

/**
 * "Add a benefit" on the Benefits tab, for a benefit the chat does not send.
 * Admin only. The key comes from the name.
 */
export function AddBenefit({ programs }: { programs: RewardProgram[] }) {
  const [open, setOpen] = useState(false);
  const [added, setAdded] = useState<string | null>(null);
  return (
    <div className="flex flex-col md:items-end">
      <Button onClick={() => setOpen(true)} className="w-full justify-center md:w-auto">
        Add a benefit
      </Button>
      <FormStatus problem={null} saved={added} />
      <Drawer open={open} onOpenChange={setOpen} title="Add a benefit" description="For a benefit the chat does not send. You can change, end or hide it later.">
        <NewBenefitForm
          programs={programs}
          onAdded={(name) => {
            setAdded(`Added ${name}.`);
            setOpen(false);
          }}
        />
      </Drawer>
    </div>
  );
}

interface CycleRow extends CycleText {
  id: number;
}

function NewBenefitForm({ programs, onAdded }: { programs: RewardProgram[]; onAdded: (name: string) => void }) {
  const id = useId();
  const nextRow = useRef(1);
  // Mounted only while the drawer is open, so the cards load then.
  const accounts = useQuery({ queryKey: queryKeys.accounts(), queryFn: () => getAccounts() });
  const cards = cardOptions(accounts.data);
  const [card, setCard] = useState("");
  const [name, setName] = useState("");
  const [kind, setKind] = useState<Kind>("credit");
  const [cadence, setCadence] = useState<Cadence>("monthly");
  const [amount, setAmount] = useState("");
  const [program, setProgram] = useState("");
  const [useBy, setUseBy] = useState("");
  const [cycles, setCycles] = useState<CycleRow[]>([{ id: 0, start: "", end: "", amount: "" }]);
  const [problem, setProblem] = useState<string | null>(null);
  const add = useAddBenefit();
  const stored = kind === "credit" && STORED_CYCLES.includes(cadence);

  function setCycle(rowId: number, part: Partial<CycleText>) {
    setCycles((rows) => rows.map((r) => (r.id === rowId ? { ...r, ...part } : r)));
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const body = benefitBody({ card, name, kind, cadence, amount, program, useBy, cycles });
    if (typeof body === "string") {
      setProblem(body);
      return;
    }
    setProblem(null);
    add.mutate(body, {
      onSuccess: (b) => onAdded(b.name),
      onError: (err) => setProblem(failText(err)),
    });
  }

  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
      <label className="flex flex-col gap-1 text-sm text-fg-secondary">
        Card
        <select className={INPUT_CLASS} value={card} onChange={(e) => setCard(e.target.value)} disabled={accounts.isPending}>
          <option value="">{accounts.isPending ? "Loading cards…" : "Pick a card"}</option>
          {cards.map((c) => (
            <option key={c.key} value={c.key}>
              {c.name}
            </option>
          ))}
        </select>
      </label>
      {accounts.isError && <p className="m-0 text-sm text-bad">Could not load the cards. Close and try again.</p>}
      <label className="flex flex-col gap-1 text-sm text-fg-secondary">
        Name
        <input className={INPUT_CLASS} autoComplete="off" maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="flex flex-col gap-1 text-sm text-fg-secondary">
        Kind
        <select className={INPUT_CLASS} value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
          {KINDS.map((k) => (
            <option key={k.key} value={k.key}>
              {k.label}
            </option>
          ))}
        </select>
      </label>
      {kind === "credit" && (
        <>
          <label className="flex flex-col gap-1 text-sm text-fg-secondary">
            Cadence
            <select className={INPUT_CLASS} value={cadence} onChange={(e) => setCadence(e.target.value as Cadence)}>
              {CADENCES.map((c) => (
                <option key={c} value={c}>
                  {CADENCE_TEXT[c]}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm text-fg-secondary">
            Amount each cycle
            <input
              className={INPUT_CLASS}
              inputMode="decimal"
              autoComplete="off"
              placeholder="12.00"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
            />
          </label>
        </>
      )}
      {stored && (
        <fieldset className="m-0 border-0 p-0">
          <legend className="mb-1 p-0 text-sm text-fg-secondary">Cycles</legend>
          <p id={`${id}-cycles-hint`} className="m-0 mb-2 text-sm text-fg-secondary">
            Leave a cycle's amount empty to use the amount above.
          </p>
          <ol className="m-0 flex list-none flex-col gap-3 p-0" aria-label="Cycles">
            {cycles.map((c, i) => (
              <li key={c.id} className="flex flex-col gap-2 rounded-lg bg-subtle p-3">
                <div className="grid grid-cols-2 gap-3">
                  <label className="flex min-w-0 flex-col gap-1 text-sm text-fg-secondary">
                    <span>
                      Start{" "}<span className="sr-only">of cycle {i + 1}</span>
                    </span>
                    <input className={INPUT_CLASS} type="date" value={c.start} onChange={(e) => setCycle(c.id, { start: e.target.value })} />
                  </label>
                  <label className="flex min-w-0 flex-col gap-1 text-sm text-fg-secondary">
                    <span>
                      End{" "}<span className="sr-only">of cycle {i + 1}</span>
                    </span>
                    <input className={INPUT_CLASS} type="date" value={c.end} onChange={(e) => setCycle(c.id, { end: e.target.value })} />
                  </label>
                </div>
                <label className="flex flex-col gap-1 text-sm text-fg-secondary">
                  <span>
                    Amount{" "}<span className="sr-only">of cycle {i + 1}</span>
                  </span>
                  <input
                    className={INPUT_CLASS}
                    inputMode="decimal"
                    autoComplete="off"
                    value={c.amount}
                    onChange={(e) => setCycle(c.id, { amount: e.target.value })}
                    aria-describedby={`${id}-cycles-hint`}
                  />
                </label>
                {cycles.length > 1 && (
                  <Button size="sm" variant="ghost" className="-ml-3 self-start" onClick={() => setCycles((rows) => rows.filter((r) => r.id !== c.id))}>
                    Remove{" "}<span className="sr-only">cycle {i + 1}</span>
                  </Button>
                )}
              </li>
            ))}
          </ol>
          <Button
            size="sm"
            className="mt-3"
            disabled={cycles.length >= CYCLES_MAX}
            onClick={() => setCycles((rows) => [...rows, { id: nextRow.current++, start: "", end: "", amount: "" }])}
          >
            Add a cycle
          </Button>
        </fieldset>
      )}
      {kind === "free_night" && (
        <>
          <label className="flex flex-col gap-1 text-sm text-fg-secondary">
            Hotel program
            <select className={INPUT_CLASS} value={program} onChange={(e) => setProgram(e.target.value)}>
              <option value="">Pick a program</option>
              {programs.map((p) => (
                <option key={p.key} value={p.key}>
                  {programLabel(p)}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-sm text-fg-secondary">
            Use by
            <input className={INPUT_CLASS} type="date" value={useBy} onChange={(e) => setUseBy(e.target.value)} />
          </label>
        </>
      )}
      <div>
        <Button type="submit" variant="primary" className="w-full justify-center md:w-auto" disabled={add.isPending}>
          {add.isPending ? "Adding…" : "Add benefit"}
        </Button>
        <FormStatus problem={problem} saved={null} />
      </div>
    </form>
  );
}
