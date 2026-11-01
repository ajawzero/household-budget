import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, CircleAlert, CircleCheck } from "lucide-react";
import { type FormEvent, type ReactNode, useId, useState } from "react";
import { Link } from "react-router";
import { EstBadge } from "../../../components/money/EstBadge";
import { Money } from "../../../components/money/Money";
import { PageHeader } from "../../../components/shell/PageHeader";
import { Button } from "../../../components/ui/button";
import { Card, CardHeader } from "../../../components/ui/card";
import { ErrorBanner } from "../../../components/ui/error-banner";
import { Skeleton } from "../../../components/ui/skeleton";
import { ApiError } from "../../../lib/api";
import { useMe } from "../../../lib/auth";
import { formatDate } from "../../../lib/format";
import {
  deleteMileage,
  invalidateEstimates,
  milesText,
  milliPctText,
  putVehicleSettings,
  todayIso,
  useVehicles,
  type Vehicle,
  type VehicleSettingsIn,
} from "../../accounts/vehicles/api";
import { INPUT_CLASS } from "../../accounts/vehicles/fields";
import { IndexStatusCard } from "../../accounts/vehicles/HomePanel";
import { MileageForm } from "../../accounts/vehicles/MileageForm";
import { ValueBreakdown } from "../../accounts/vehicles/VehiclePanel";
import { centsToText, milliToText, parseDollarsCents, parsePctMilli, parseWhole } from "./parse";

/**
 * Settings > Vehicles: mileage readings and the numbers behind each
 * vehicle's estimate between KBB checks. Anyone signed in can add mileage.
 * Rates and the conversion value are admin only.
 */
export function VehiclesSettingsPage() {
  const q = useVehicles();
  const me = useMe();
  const isAdmin = me.data?.role === "admin";
  return (
    <>
      <PageHeader title="Home and vehicles" />
      <div className="mb-3">
        <Link to="/settings" className="inline-flex min-h-11 items-center gap-1 text-sm md:min-h-0">
          <ChevronLeft size={16} aria-hidden="true" />
          Settings
        </Link>
      </div>
      <div className="flex max-w-3xl flex-col gap-4">
        <p className="m-0 text-sm text-fg-secondary">
          The monthly chat checks KBB once a quarter. In between, the app estimates each value: it loses a set share a
          year, and a bit more or less when the miles run ahead of or behind pace. A real KBB value always wins.
        </p>
        {q.isPending && <Skeleton className="h-64 rounded-card" />}
        {q.isError && (
          <ErrorBanner message="Could not load vehicles." onRetry={() => void q.refetch()} retrying={q.isFetching} />
        )}
        {q.data?.vehicles.length === 0 && (
          <Card>
            <p className="m-0 text-fg-secondary">No vehicles yet. They appear after an import adds one.</p>
          </Card>
        )}
        {q.data?.vehicles.map((v) => <VehicleCard key={v.account_key} vehicle={v} isAdmin={isAdmin} />)}
        <IndexStatusCard />
      </div>
    </>
  );
}

function VehicleCard({ vehicle, isAdmin }: { vehicle: Vehicle; isAdmin: boolean }) {
  const titleId = useId();
  return (
    <Card aria-labelledby={titleId} id={vehicle.account_key}>
      <CardHeader
        titleId={titleId}
        title={vehicle.display_name}
        actions={
          vehicle.value_cents !== null && (
            <span className="inline-flex items-center gap-1.5 text-lg font-medium text-fg">
              {vehicle.estimate && <EstBadge note={vehicle.estimate.note} />}
              <Money cents={vehicle.value_cents} />
            </span>
          )
        }
      />
      <div className="flex flex-col gap-6">
        <ValueBreakdown vehicle={vehicle} />
        <div className="flex flex-col gap-3">
          <MileageForm vehicle={vehicle} />
          <Readings vehicle={vehicle} />
        </div>
        {/* Keyed by the save time, so a save from elsewhere resets the fields. */}
        <SettingsForm key={vehicle.settings.updated_at ?? "default"} vehicle={vehicle} isAdmin={isAdmin} />
      </div>
    </Card>
  );
}

function Readings({ vehicle }: { vehicle: Vehicle }) {
  const qc = useQueryClient();
  const [problem, setProblem] = useState<string | null>(null);
  const del = useMutation({
    mutationFn: (id: number) => deleteMileage(vehicle.account_key, id),
    onSuccess: () => {
      setProblem(null);
      invalidateEstimates(qc);
    },
    onError: (err) => setProblem(err instanceof ApiError ? err.message : "Could not delete the reading."),
  });
  if (vehicle.readings.length === 0) return null;
  const shown = vehicle.readings.slice(0, 5);
  return (
    <div>
      <p className="m-0 mb-1 text-sm text-fg-secondary">Recent readings</p>
      <ul className="m-0 list-none p-0">
        {shown.map((r) => (
          <li key={r.id} className="flex items-center gap-3 border-b border-line py-1 text-sm last:border-b-0">
            <span className="w-28 shrink-0 text-fg-secondary">{formatDate(r.reading_date)}</span>
            <span className="flex-1 tabular-nums text-fg">{milesText(r.miles)}</span>
            <Button
              size="sm"
              variant="ghost"
              disabled={del.isPending}
              onClick={() => del.mutate(r.id)}
              aria-label={`Delete the ${formatDate(r.reading_date)} reading`}
            >
              Delete
            </Button>
          </li>
        ))}
      </ul>
      {problem && (
        <p role="alert" className="m-0 mt-1 text-sm text-bad">
          {problem}
        </p>
      )}
    </div>
  );
}

interface FormText {
  enabled: boolean;
  model_year: string;
  depreciation: string;
  expected_miles: string;
  mileage_rate: string;
  conversion_value: string;
  conversion_date: string;
  conversion_rate: string;
}

function initialText(v: Vehicle): FormText {
  const s = v.settings;
  return {
    enabled: s.enabled,
    model_year: s.model_year === null ? "" : String(s.model_year),
    depreciation: milliToText(s.depreciation_milli_pct),
    expected_miles: s.expected_miles_year === null ? "" : String(s.expected_miles_year),
    mileage_rate: milliToText(s.mileage_milli_pct),
    conversion_value: centsToText(s.conversion_value_cents),
    conversion_date: s.conversion_value_date ?? "",
    conversion_rate: milliToText(s.conversion_milli_pct),
  };
}

/** Field text to the PUT body, or the first problem in plain words. */
export function toSettingsBody(t: FormText, today: string): VehicleSettingsIn | string {
  const model_year = parseWhole(t.model_year);
  if (model_year === undefined || (model_year !== null && (model_year < 1950 || model_year > 2100))) {
    return "Model year should be a year like 2021.";
  }
  const depreciation = parsePctMilli(t.depreciation);
  if (depreciation === undefined || (depreciation !== null && depreciation > 50_000)) {
    return "Yearly loss should be a percent from 0 to 50.";
  }
  const expected = parseWhole(t.expected_miles);
  if (expected === undefined || (expected !== null && expected > 100_000)) {
    return "Miles a year should be a whole number up to 100,000.";
  }
  const mileage = parsePctMilli(t.mileage_rate);
  if (mileage === undefined || (mileage !== null && mileage > 5000)) {
    return "The mileage adjustment should be a percent from 0 to 5.";
  }
  const conversion = parseDollarsCents(t.conversion_value);
  if (conversion === undefined || (conversion !== null && conversion > 100_000_000)) {
    return "Conversion value should be a dollar amount.";
  }
  const date = t.conversion_date || null;
  if ((conversion === null) !== (date === null)) {
    return "Give the conversion value and its date together, or leave both empty.";
  }
  if (date !== null && date > today) return "The conversion date cannot be in the future.";
  const conversionRate = parsePctMilli(t.conversion_rate);
  if (conversionRate === undefined || (conversionRate !== null && conversionRate > 50_000)) {
    return "The conversion's yearly loss should be a percent from 0 to 50.";
  }
  return {
    enabled: t.enabled,
    model_year,
    depreciation_milli_pct: depreciation,
    expected_miles_year: expected,
    mileage_milli_pct: mileage,
    conversion_value_cents: conversion,
    conversion_value_date: date,
    conversion_milli_pct: conversionRate,
  };
}

const RATE_SOURCE = { set: "set here", model_year: "from the model year", default: "the default" } as const;

function SettingsForm({ vehicle, isAdmin }: { vehicle: Vehicle; isAdmin: boolean }) {
  const qc = useQueryClient();
  const [t, setT] = useState<FormText>(() => initialText(vehicle));
  const [problem, setProblem] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const eff = vehicle.effective;
  const save = useMutation({
    mutationFn: (body: VehicleSettingsIn) => putVehicleSettings(vehicle.account_key, body),
    onSuccess: () => {
      setSaved(true);
      invalidateEstimates(qc);
    },
    onError: (err) => setProblem(err instanceof ApiError ? err.message : "Could not save. Try again."),
  });

  function set<K extends keyof FormText>(k: K, value: FormText[K]) {
    setT((old) => ({ ...old, [k]: value }));
    setSaved(false);
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const body = toSettingsBody(t, todayIso());
    if (typeof body === "string") {
      setProblem(body);
      return;
    }
    setProblem(null);
    save.mutate(body);
  }

  const disabled = !isAdmin || save.isPending;
  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4 border-t border-line pt-5">
      <div>
        <p className="m-0 text-sm font-medium text-fg">How the estimate works</p>
        <p className="m-0 mt-0.5 text-sm text-fg-secondary">
          Leave a field empty to use the default shown in it.
          {!isAdmin && " Only an admin can change these."}
        </p>
      </div>
      <label className="flex min-h-11 items-center gap-3 text-sm text-fg">
        <input
          type="checkbox"
          className="h-5 w-5 accent-[var(--accent)]"
          checked={t.enabled}
          disabled={disabled}
          onChange={(e) => set("enabled", e.target.checked)}
        />
        Estimate this value between KBB checks
      </label>
      <div className="grid gap-4 md:grid-cols-2">
        <TextField
          label="Model year"
          hint="Picks a standard yearly loss when you do not set one: newer cars lose value faster."
          value={t.model_year}
          placeholder="Not set"
          inputMode="numeric"
          disabled={disabled}
          onChange={(v) => set("model_year", v)}
        />
        <TextField
          label="Yearly loss, %"
          hint={`The share of value it loses each year. Now ${milliPctText(eff.depreciation_milli_pct)}, ${RATE_SOURCE[eff.rate_source]}.`}
          value={t.depreciation}
          placeholder={milliToText(eff.depreciation_milli_pct)}
          inputMode="decimal"
          disabled={disabled}
          onChange={(v) => set("depreciation", v)}
        />
        <TextField
          label="Expected miles a year"
          hint="The pace KBB assumes. More miles than this lowers the estimate, fewer raises it."
          value={t.expected_miles}
          placeholder={String(eff.expected_miles_year)}
          inputMode="numeric"
          disabled={disabled}
          onChange={(v) => set("expected_miles", v)}
        />
        <TextField
          label="Mileage adjustment, % per 1,000 miles"
          hint="How much each 1,000 miles over or under pace moves the value."
          value={t.mileage_rate}
          placeholder={milliToText(eff.mileage_milli_pct)}
          inputMode="decimal"
          disabled={disabled}
          onChange={(v) => set("mileage_rate", v)}
        />
      </div>
      <fieldset className="m-0 flex flex-col gap-3 rounded-lg border border-line p-4">
        <legend className="px-1 text-sm font-medium text-fg">Conversion</legend>
        <p className="m-0 text-sm text-fg-secondary">
          For a camper van: the build-out that KBB does not count. The chat sends the plain KBB value, and the app
          adds this part on top, losing value at its own rate. Leave it empty for a regular vehicle.
        </p>
        <div className="grid gap-4 md:grid-cols-3">
          <TextField
            label="Conversion value, $"
            value={t.conversion_value}
            placeholder="None"
            inputMode="decimal"
            disabled={disabled}
            onChange={(v) => set("conversion_value", v)}
          />
          <TextField
            label="Value as of"
            type="date"
            max={todayIso()}
            value={t.conversion_date}
            disabled={disabled}
            onChange={(v) => set("conversion_date", v)}
          />
          <TextField
            label="Conversion yearly loss, %"
            value={t.conversion_rate}
            placeholder={milliToText(eff.conversion_milli_pct ?? 8000)}
            inputMode="decimal"
            disabled={disabled}
            onChange={(v) => set("conversion_rate", v)}
          />
        </div>
      </fieldset>
      {isAdmin && (
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" variant="primary" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save"}
          </Button>
          <div aria-live="polite" className="text-sm">
            {problem && (
              <span className="inline-flex items-start gap-1.5 text-bad">
                <CircleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
                {problem}
              </span>
            )}
            {saved && !problem && (
              <span className="inline-flex animate-fade-in items-start gap-1.5 text-good">
                <CircleCheck size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
                Saved. The estimate is updated.
              </span>
            )}
          </div>
        </div>
      )}
    </form>
  );
}

function TextField({
  label,
  hint,
  value,
  onChange,
  ...input
}: {
  label: string;
  hint?: ReactNode;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  inputMode?: "numeric" | "decimal";
  type?: string;
  max?: string;
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <label htmlFor={id} className="text-sm text-fg">
        {label}
      </label>
      <input
        id={id}
        className={INPUT_CLASS}
        value={value}
        autoComplete="off"
        aria-describedby={hint ? `${id}-hint` : undefined}
        onChange={(e) => onChange(e.target.value)}
        {...input}
      />
      {hint && (
        <p id={`${id}-hint`} className="m-0 text-xs text-fg-secondary">
          {hint}
        </p>
      )}
    </div>
  );
}
