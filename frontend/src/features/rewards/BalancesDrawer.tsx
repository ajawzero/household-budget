import { type FormEvent, useId, useState } from "react";
import { Button } from "../../components/ui/button";
import { Drawer } from "../../components/ui/drawer";
import { ApiError, type BalanceEntryIn, type RewardProgram } from "../../lib/api";
import { todayIso } from "../accounts/vehicles/api";
import { INPUT_CLASS } from "../accounts/vehicles/fields";
import { useAddBalances } from "./api";
import { lastValueText, parseBalance } from "./model";
import { FormStatus } from "./parts";

/** Programs that take a balance. Timeshares use their yearly points instead. */
export function balancePrograms(programs: RewardProgram[]): RewardProgram[] {
  return programs.filter((p) => p.kind !== "timeshare");
}

/** "Saved 3 balances." */
export function savedText(n: number): string {
  return `Saved ${n} ${n === 1 ? "balance" : "balances"}.`;
}

const FIELD_RULE: Record<RewardProgram["kind"], string> = {
  points: "Use whole points, like 52,000.",
  miles: "Use whole miles, like 52,000.",
  cash_back: "Use dollars and cents, like 37.42.",
  timeshare: "",
};

/**
 * "Update balances": one row per program and one date for all (today by
 * default). Anyone signed in can save. Empty rows are skipped.
 */
export function BalancesDrawer({
  open,
  onOpenChange,
  programs,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  programs: RewardProgram[];
}) {
  return (
    <Drawer open={open} onOpenChange={onOpenChange} title="Update balances" description="Enter what each program shows now. Empty rows are skipped.">
      <BalancesForm programs={balancePrograms(programs)} />
    </Drawer>
  );
}

function BalancesForm({ programs }: { programs: RewardProgram[] }) {
  const id = useId();
  const today = todayIso();
  const [date, setDate] = useState(today);
  const [values, setValues] = useState<Record<string, string>>({});
  const [bad, setBad] = useState<Record<string, string>>({});
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const save = useAddBalances();

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSaved(null);
    const entries: BalanceEntryIn[] = [];
    const errors: Record<string, string> = {};
    for (const p of programs) {
      const balance = parseBalance(p.kind, values[p.key] ?? "");
      if (balance === undefined) errors[p.key] = FIELD_RULE[p.kind];
      else if (balance !== null) entries.push({ program_key: p.key, balance, as_of: date });
    }
    setBad(errors);
    if (Object.keys(errors).length > 0) {
      setProblem("Fix the balances marked above.");
      return;
    }
    if (!date || date > today) {
      setProblem("Pick today or an earlier date.");
      return;
    }
    if (entries.length === 0) {
      setProblem("Enter at least one balance.");
      return;
    }
    setProblem(null);
    save.mutate(entries, {
      onSuccess: (out) => {
        setSaved(savedText(out.saved));
        setValues({});
      },
      onError: (err) => setProblem(err instanceof ApiError ? err.message : "Could not save the balances. Try again."),
    });
  }

  return (
    <form onSubmit={onSubmit} noValidate className="flex min-h-full flex-col">
      <label className="flex flex-col gap-1 text-sm text-fg-secondary">
        Balances as of
        <input
          className={INPUT_CLASS}
          type="date"
          max={today}
          value={date}
          onChange={(e) => {
            setDate(e.target.value);
            setSaved(null);
          }}
        />
      </label>
      <ul className="m-0 mt-4 flex list-none flex-col p-0" aria-label="Programs">
        {programs.map((p) => {
          const field = `${id}-${p.key}`;
          const error = bad[p.key];
          return (
            <li key={p.key} className="flex flex-col gap-1 border-t border-line py-3">
              <label htmlFor={field} className="text-sm font-medium text-fg">
                {p.name}
                {p.owner_name && <>{" "}<span className="font-normal text-fg-secondary">· {p.owner_name}</span></>}
              </label>
              <input
                id={field}
                className={INPUT_CLASS}
                inputMode={p.kind === "cash_back" ? "decimal" : "numeric"}
                autoComplete="off"
                placeholder={lastValueText(p)}
                value={values[p.key] ?? ""}
                onChange={(e) => {
                  const text = e.target.value;
                  setValues((v) => ({ ...v, [p.key]: text }));
                  setSaved(null);
                }}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? `${field}-problem` : undefined}
              />
              {error && (
                <p id={`${field}-problem`} className="m-0 text-sm text-bad">
                  {error}
                </p>
              )}
            </li>
          );
        })}
      </ul>
      {/*
        Sticky on phones, so Save all stays in reach above the keyboard. The
        drawer's scroll area has bottom padding, and a sticky box stops at that
        padding. The negative bottom and margin match it, so the bar sits flush
        at the bottom and no row shows below it. The bar keeps the safe area.
      */}
      <div
        className="sticky -bottom-[max(20px,env(safe-area-inset-bottom))] -mx-5 -mb-[max(20px,env(safe-area-inset-bottom))] mt-auto border-t border-line bg-card px-5 pt-3 pb-[max(12px,env(safe-area-inset-bottom))] md:static md:mx-0 md:mb-0 md:px-0 md:pb-3"
      >
        <Button type="submit" variant="primary" className="w-full justify-center md:w-auto" disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save all"}
        </Button>
        <FormStatus problem={problem} saved={saved} />
      </div>
    </form>
  );
}
