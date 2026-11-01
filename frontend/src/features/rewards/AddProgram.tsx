import { ChevronDown } from "lucide-react";
import { type FormEvent, useId, useState } from "react";
import { Button } from "../../components/ui/button";
import { Drawer } from "../../components/ui/drawer";
import { ApiError, type ProgramIn } from "../../lib/api";
import { cn } from "../../lib/cn";
import { INPUT_CLASS } from "../accounts/vehicles/fields";
import { useAddProgram } from "./api";
import { parseCpp } from "./model";
import { FormStatus, MemberHint } from "./parts";

type Kind = ProgramIn["kind"];
type Group = ProgramIn["group"];

const KINDS: { key: Kind; label: string }[] = [
  { key: "points", label: "Points" },
  { key: "miles", label: "Miles" },
  { key: "cash_back", label: "Cash back" },
];

const RANGE_RULE = "Enter both ends of the value range, low first, like 1.2 and 1.8.";

/** The form's text to the API body, or the first problem. */
export function programBody(f: { name: string; kind: Kind; group: Group; low: string; high: string }): ProgramIn | string {
  const name = f.name.trim();
  if (name === "") return "Enter a name.";
  if (f.kind === "cash_back") return { name, kind: f.kind, group: f.group };
  const low = parseCpp(f.low);
  const high = parseCpp(f.high);
  if (low === undefined || high === undefined || (low === null) !== (high === null)) return RANGE_RULE;
  if (low !== null && high !== null && Number(low) > Number(high)) return RANGE_RULE;
  return { name, kind: f.kind, group: f.group, cpp_low: low, cpp_high: high };
}

/**
 * "Add a program" under Other programs, for programs the chat never sends
 * (an airline or a hotel). Admin only. Members see who can.
 */
export function AddProgram({ isAdmin }: { isAdmin: boolean }) {
  const [open, setOpen] = useState(false);
  const [added, setAdded] = useState<string | null>(null);
  if (!isAdmin) return <MemberHint>Only an admin can add programs.</MemberHint>;
  return (
    <div>
      <Button onClick={() => setOpen(true)} className="w-full justify-center md:w-auto">
        Add a program
      </Button>
      <FormStatus problem={null} saved={added} />
      <Drawer open={open} onOpenChange={setOpen} title="Add a program" description="For a program the chat does not send. Enter its balance with Update balances.">
        <ProgramForm
          onAdded={(name) => {
            setAdded(`Added ${name}.`);
            setOpen(false);
          }}
        />
      </Drawer>
    </div>
  );
}

function ProgramForm({ onAdded }: { onAdded: (name: string) => void }) {
  const id = useId();
  const [name, setName] = useState("");
  const [kind, setKind] = useState<Kind>("points");
  const [group, setGroup] = useState<Group>("secondary");
  const [low, setLow] = useState("");
  const [high, setHigh] = useState("");
  const [more, setMore] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const add = useAddProgram();

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const body = programBody({ name, kind, group, low, high });
    if (typeof body === "string") {
      setProblem(body);
      if (body === RANGE_RULE) setMore(true);
      return;
    }
    setProblem(null);
    add.mutate(body, {
      onSuccess: (p) => onAdded(p.name),
      onError: (err) => setProblem(err instanceof ApiError ? err.message : "Could not add the program. Try again."),
    });
  }

  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
      <label className="flex flex-col gap-1 text-sm text-fg-secondary">
        Name
        <input className={INPUT_CLASS} autoComplete="off" maxLength={64} value={name} onChange={(e) => setName(e.target.value)} />
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
      <div>
        <button
          type="button"
          className="inline-flex min-h-11 items-center gap-1 text-sm font-medium text-accent-text md:min-h-0"
          aria-expanded={more}
          aria-controls={`${id}-more`}
          onClick={() => setMore((m) => !m)}
        >
          <ChevronDown size={16} aria-hidden="true" className={cn("transition-transform duration-100", !more && "-rotate-90")} />
          More
        </button>
        <div id={`${id}-more`} hidden={!more} className="mt-2 flex flex-col gap-4">
          <label className="flex flex-col gap-1 text-sm text-fg-secondary">
            Group
            <select className={INPUT_CLASS} value={group} onChange={(e) => setGroup(e.target.value as Group)}>
              <option value="secondary">Other programs</option>
              <option value="primary">Main programs</option>
            </select>
          </label>
          {kind === "cash_back" ? (
            <p className="m-0 text-sm text-fg-secondary">Cash back counts at face value.</p>
          ) : (
            <fieldset className="m-0 border-0 p-0">
              <legend className="mb-1 p-0 text-sm text-fg-secondary">Value range, in cents a point</legend>
              <div className="flex gap-3">
                <label className="flex min-w-0 flex-1 flex-col gap-1 text-sm text-fg-secondary">
                  Low
                  <input className={INPUT_CLASS} inputMode="decimal" autoComplete="off" placeholder="1.2" value={low} onChange={(e) => setLow(e.target.value)} />
                </label>
                <label className="flex min-w-0 flex-1 flex-col gap-1 text-sm text-fg-secondary">
                  High
                  <input className={INPUT_CLASS} inputMode="decimal" autoComplete="off" placeholder="1.8" value={high} onChange={(e) => setHigh(e.target.value)} />
                </label>
              </div>
            </fieldset>
          )}
        </div>
      </div>
      <div>
        <Button type="submit" variant="primary" className="w-full justify-center md:w-auto" disabled={add.isPending}>
          {add.isPending ? "Adding…" : "Add program"}
        </Button>
        <FormStatus problem={problem} saved={null} />
      </div>
    </form>
  );
}
