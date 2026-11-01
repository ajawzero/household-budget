import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, CircleCheck } from "lucide-react";
import { type FormEvent, useId, useState } from "react";
import { Button } from "../../../components/ui/button";
import { ApiError } from "../../../lib/api";
import { formatDate } from "../../../lib/format";
import { INPUT_CLASS } from "./fields";
import { addMileage, invalidateEstimates, milesText, todayIso, type Vehicle, type VehiclesOut, vehicleKeys } from "./api";

/** "4,210" or "4210 mi" to 4210. Null when it is not a whole number. */
export function parseMiles(text: string): number | null {
  const clean = text.replace(/[,\s]/g, "").replace(/(mi|miles)$/i, "");
  if (!/^\d{1,7}$/.test(clean)) return null;
  return Number(clean);
}

interface MileageFormProps {
  vehicle: Pick<Vehicle, "account_key" | "display_name" | "readings">;
  /** Wider layout on Settings; the drawer stacks on phones. */
  className?: string;
}

/**
 * The quick "Update mileage" field: odometer and date (today by default).
 * Anyone signed in can add one. The reading moves the vehicle's estimate.
 */
export function MileageForm({ vehicle, className }: MileageFormProps) {
  const qc = useQueryClient();
  const id = useId();
  const today = todayIso();
  const [miles, setMiles] = useState("");
  const [date, setDate] = useState(today);
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const last = vehicle.readings[0] ?? null;

  const save = useMutation({
    mutationFn: (v: { miles: number; date: string }) => addMileage(vehicle.account_key, v.miles, v.date),
    onSuccess: (updated, v) => {
      // Show the new reading at once, then refresh everything the estimate touches.
      qc.setQueryData<VehiclesOut>(vehicleKeys.vehicles, (old) =>
        old ? { ...old, vehicles: old.vehicles.map((x) => (x.account_key === updated.account_key ? updated : x)) } : old,
      );
      invalidateEstimates(qc);
      setSaved(`Saved ${milesText(v.miles)} on ${formatDate(v.date)}. The estimate is updated.`);
      setMiles("");
      setDate(todayIso());
    },
    onError: (err) => {
      setProblem(err instanceof ApiError ? err.message : "Could not save the reading. Try again.");
    },
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSaved(null);
    const n = parseMiles(miles);
    if (n === null) {
      setProblem("Enter the odometer as a whole number of miles.");
      return;
    }
    if (!date || date > today) {
      setProblem("Pick today or an earlier date.");
      return;
    }
    if (last && date >= last.reading_date && n < last.miles) {
      setProblem(`Mileage cannot go down. The ${formatDate(last.reading_date)} reading is ${milesText(last.miles)}.`);
      return;
    }
    setProblem(null);
    save.mutate({ miles: n, date });
  }

  return (
    <form onSubmit={onSubmit} className={className} aria-labelledby={`${id}-title`} noValidate>
      <p id={`${id}-title`} className="m-0 text-sm font-medium text-fg">
        Update mileage{" "}
        <span className="sr-only">for {vehicle.display_name}</span>
      </p>
      <p className="m-0 mt-0.5 text-sm text-fg-secondary">
        {last ? (
          <>
            Last reading {milesText(last.miles)} on {formatDate(last.reading_date)}.
          </>
        ) : (
          "No readings yet. One reading a month keeps the estimate close."
        )}
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="flex min-w-0 flex-1 basis-36 flex-col gap-1 text-sm text-fg-secondary">
          Odometer
          <input
            className={INPUT_CLASS}
            inputMode="numeric"
            autoComplete="off"
            placeholder="Miles now"
            value={miles}
            onChange={(e) => setMiles(e.target.value)}
            aria-invalid={problem !== null || undefined}
            aria-describedby={problem ? `${id}-problem` : undefined}
          />
        </label>
        <label className="flex min-w-0 flex-1 basis-36 flex-col gap-1 text-sm text-fg-secondary">
          Date
          <input className={INPUT_CLASS} type="date" max={today} value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
        <Button type="submit" variant="primary" disabled={save.isPending || miles.trim() === ""}>
          {save.isPending ? "Saving…" : "Save"}
        </Button>
      </div>
      <div aria-live="polite">
        {problem && (
          <p id={`${id}-problem`} className="m-0 mt-2 flex items-start gap-1.5 text-sm text-bad">
            <CircleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
            {problem}
          </p>
        )}
        {saved && !problem && (
          <p className="m-0 mt-2 flex items-start gap-1.5 text-sm text-good">
            <CircleCheck size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
            {saved}
          </p>
        )}
      </div>
    </form>
  );
}
