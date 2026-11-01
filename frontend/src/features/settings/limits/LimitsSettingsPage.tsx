import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, CircleAlert, CircleCheck, ExternalLink } from "lucide-react";
import { type FormEvent, useId, useState } from "react";
import { Link } from "react-router";
import { PageHeader } from "../../../components/shell/PageHeader";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { Card, CardHeader } from "../../../components/ui/card";
import { ErrorBanner } from "../../../components/ui/error-banner";
import { SegmentedControl } from "../../../components/ui/segmented-control";
import { Skeleton } from "../../../components/ui/skeleton";
import { ApiError } from "../../../lib/api";
import { useMe } from "../../../lib/auth";
import { cn } from "../../../lib/cn";
import { formatMoney, formatTimestamp } from "../../../lib/format";
import { INPUT_CLASS } from "../../accounts/vehicles/fields";
import { todayIso } from "../../accounts/vehicles/api";
import {
  invalidateContributions,
  LIMIT_KINDS,
  type LimitKind,
  type LimitRow,
  type PersonContributions,
  type PersonSettingsIn,
  putLimit,
  putPersonSettings,
  useContributions,
} from "../../investments/contributions/api";
import { centsToText, parseDollarsCents } from "../vehicles/parse";

/**
 * Settings > Contribution limits: the IRS limits the app seeded, to confirm
 * or correct, and each person's catch-up, HSA coverage and election lead time.
 */
export function LimitsSettingsPage() {
  const thisYear = Number(todayIso().slice(0, 4));
  const [year, setYear] = useState(thisYear);
  const q = useContributions(year);
  const me = useMe();
  const isAdmin = me.data?.role === "admin";
  const years = [thisYear, thisYear + 1].map((y) => ({ value: String(y), label: String(y) }));

  return (
    <>
      <PageHeader title="Contribution limits" />
      <div className="mb-3">
        <Link to="/settings" className="inline-flex min-h-11 items-center gap-1 text-sm md:min-h-0">
          <ChevronLeft size={16} aria-hidden="true" />
          Settings
        </Link>
      </div>
      <div className="flex max-w-3xl flex-col gap-4">
        <Card>
          <CardHeader
            title="IRS limits"
            subtitle="The app starts from the IRS figures. Check each one against the IRS page, then confirm it."
            actions={
              <SegmentedControl label="Year" value={String(year)} options={years} onChange={(v) => setYear(Number(v))} />
            }
          />
          {q.isPending && <Skeleton className="h-64" />}
          {q.isError && (
            <ErrorBanner message="Could not load the limits." onRetry={() => void q.refetch()} retrying={q.isFetching} />
          )}
          {q.data && <LimitsList year={year} rows={q.data.limits} isAdmin={isAdmin} />}
        </Card>
        {q.data && q.data.people.length > 0 && (
          <Card>
            <CardHeader
              title="People"
              subtitle="Catch-up room depends on age, and the HSA limit on coverage. The app does not store birthdays, so pick each person's catch-up group and coverage here."
            />
            <div className="flex flex-col gap-6">
              {q.data.people.map((p) => (
                <PersonForm key={`${p.person_key}-${p.settings.updated_at ?? "default"}`} person={p} isAdmin={isAdmin} />
              ))}
            </div>
          </Card>
        )}
      </div>
    </>
  );
}

function LimitsList({ year, rows, isAdmin }: { year: number; rows: LimitRow[]; isAdmin: boolean }) {
  const qc = useQueryClient();
  const byKind = new Map(rows.map((r) => [r.kind, r]));
  const pending = rows.filter((r) => !r.confirmed);
  const unconfirmed = pending.length;
  const confirmAll = useMutation({
    mutationFn: () =>
      Promise.all(pending.map((r) => putLimit({ year: r.year, kind: r.kind, limit_cents: r.limit_cents, confirm: true }))),
    onSettled: () => invalidateContributions(qc),
  });
  return (
    <>
      {rows.length === 0 && (
        <p className="m-0 mb-3 text-sm text-warn">No {year} limits yet. Enter them when the IRS publishes them.</p>
      )}
      {unconfirmed > 0 && (
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <p className="m-0 text-sm text-fg-secondary">
            {unconfirmed === 1 ? "1 limit is" : `${unconfirmed} limits are`} not confirmed yet.
          </p>
          {isAdmin && unconfirmed > 1 && (
            <Button size="sm" disabled={confirmAll.isPending} onClick={() => confirmAll.mutate()}>
              {confirmAll.isPending ? "Confirming…" : `Confirm all ${unconfirmed} as shown`}
            </Button>
          )}
          {confirmAll.isError && (
            <p role="alert" className="m-0 text-sm text-bad">
              Some limits did not save. Try again.
            </p>
          )}
        </div>
      )}
      <ul className="m-0 list-none p-0">
        {LIMIT_KINDS.map((k) => (
          <LimitItem
            key={`${year}-${k.kind}-${byKind.get(k.kind)?.updated_at ?? "none"}`}
            year={year}
            kind={k.kind}
            label={byKind.get(k.kind)?.label ?? k.label}
            includesEmployer={byKind.get(k.kind)?.includes_employer ?? k.includesEmployer}
            row={byKind.get(k.kind) ?? null}
            isAdmin={isAdmin}
          />
        ))}
      </ul>
    </>
  );
}

function LimitItem({
  year,
  kind,
  label,
  includesEmployer,
  row,
  isAdmin,
}: {
  year: number;
  kind: LimitKind;
  label: string;
  includesEmployer: boolean;
  row: LimitRow | null;
  isAdmin: boolean;
}) {
  const qc = useQueryClient();
  const id = useId();
  const [text, setText] = useState(row ? centsToText(row.limit_cents) : "");
  const [problem, setProblem] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (cents: number) => putLimit({ year, kind, limit_cents: cents, confirm: true }),
    onSuccess: () => {
      setProblem(null);
      invalidateContributions(qc);
    },
    onError: (err) => setProblem(err instanceof ApiError ? err.message : "Could not save. Try again."),
  });
  const cents = parseDollarsCents(text);
  const edited = row === null || cents !== row.limit_cents;

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (cents === null || cents === undefined || cents > 10_000_000) {
      setProblem("Enter the limit in dollars, like 24,500.");
      return;
    }
    save.mutate(cents);
  }

  return (
    <li className="border-b border-line py-3 last:border-b-0">
      <form onSubmit={onSubmit} noValidate className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1 basis-56">
          <label htmlFor={id} className="block text-sm text-fg">
            {label}
          </label>
          <span className="flex flex-wrap items-center gap-2 text-xs text-fg-secondary">
            {includesEmployer ? "Employee and employer together" : "Employee only"}
            {row?.source_url && (
              <a href={row.source_url} target="_blank" rel="noreferrer" className="inline-flex min-h-11 items-center gap-0.5 md:min-h-0">
                IRS source
                <ExternalLink size={12} aria-hidden="true" />
                <span className="sr-only">(opens in a new tab)</span>
              </a>
            )}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {row?.confirmed ? (
            <Badge tone="good" icon={<CircleCheck size={12} aria-hidden="true" />} title={row.confirmed_at ? `Confirmed ${formatTimestamp(row.confirmed_at)}` : undefined}>
              Confirmed
            </Badge>
          ) : row ? (
            <Badge tone="warn" icon={<CircleAlert size={12} aria-hidden="true" />}>
              Not confirmed
            </Badge>
          ) : (
            <Badge>Not set</Badge>
          )}
          <span className="relative w-24 md:w-32">
            <span aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-secondary">
              $
            </span>
            <input
              id={id}
              className={`${INPUT_CLASS} pl-6 text-right tabular-nums`}
              inputMode="decimal"
              autoComplete="off"
              value={text}
              disabled={!isAdmin}
              onChange={(e) => setText(e.target.value)}
              aria-invalid={problem !== null || undefined}
              aria-describedby={problem ? `${id}-problem` : undefined}
            />
          </span>
          {isAdmin && (edited || !row?.confirmed) && (
            <Button type="submit" size="sm" variant={edited ? "primary" : "secondary"} disabled={save.isPending}>
              {edited ? "Save" : "Confirm"}{" "}
              <span className="sr-only">{label}</span>
            </Button>
          )}
        </div>
      </form>
      {problem && (
        <p id={`${id}-problem`} role="alert" className="m-0 mt-1 text-sm text-bad">
          {problem}
        </p>
      )}
      {row && edited && cents !== undefined && cents !== null && (
        <p className="m-0 mt-1 text-xs text-fg-secondary">Was {formatMoney(row.limit_cents)}. Saving also confirms it.</p>
      )}
    </li>
  );
}

const CATCH_UP = [
  { value: "none", label: "None" },
  { value: "50", label: "50 and over" },
  { value: "60-63", label: "60 to 63" },
] as const;

const COVERAGE = [
  { value: "unset", label: "Not set" },
  { value: "none", label: "None" },
  { value: "self", label: "Self" },
  { value: "family", label: "Family" },
] as const;

type Coverage = (typeof COVERAGE)[number]["value"];

function PersonForm({ person, isAdmin }: { person: PersonContributions; isAdmin: boolean }) {
  const qc = useQueryClient();
  const s = person.settings;
  const [catchUp, setCatchUp] = useState<PersonSettingsIn["k401_catch_up"]>(s.k401_catch_up);
  const [coverage, setCoverage] = useState<Coverage>(s.hsa_coverage ?? "unset");
  const [hsaCatchUp, setHsaCatchUp] = useState(s.hsa_catch_up);
  const [lead, setLead] = useState(s.election_lead_checks);
  const [status, setStatus] = useState<{ ok: boolean; text: string } | null>(null);
  const save = useMutation({
    mutationFn: (body: PersonSettingsIn) => putPersonSettings(person.person_key, body),
    onSuccess: () => {
      setStatus({ ok: true, text: "Saved." });
      invalidateContributions(qc);
    },
    onError: (err) => setStatus({ ok: false, text: err instanceof ApiError ? err.message : "Could not save. Try again." }),
  });
  const leadId = useId();

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    save.mutate({
      k401_catch_up: catchUp,
      hsa_coverage: coverage === "unset" ? null : coverage,
      hsa_catch_up: hsaCatchUp,
      election_lead_checks: lead,
    });
  }

  const disabled = !isAdmin;
  return (
    <form onSubmit={onSubmit} noValidate className="flex flex-col gap-3 border-b border-line pb-6 last:border-b-0 last:pb-0">
      <p className="m-0 flex items-center gap-2 text-base font-medium text-fg">
        {person.display_name}
        {!person.settings_set && <Badge tone="warn">Not set yet</Badge>}
      </p>
      <div className="flex flex-col gap-1">
        <span className="text-sm text-fg">401k catch-up this year</span>
        <SegmentedControl
          label={`${person.display_name}, 401k catch-up`}
          value={catchUp}
          options={CATCH_UP}
          onChange={(v) => !disabled && setCatchUp(v)}
          variant="pill"
        />
        <span className="text-xs text-fg-secondary">By age at the end of the year. 60 to 63 gets a larger catch-up.</span>
      </div>
      <div className="flex flex-col gap-1">
        <span className="text-sm text-fg">HSA coverage</span>
        <SegmentedControl
          label={`${person.display_name}, HSA coverage`}
          value={coverage}
          options={COVERAGE}
          onChange={(v) => !disabled && setCoverage(v)}
          variant="pill"
        />
        <span className="text-xs text-fg-secondary">Family coverage shares one limit across the household.</span>
      </div>
      <label className="flex min-h-11 items-center gap-3 text-sm text-fg">
        <input
          type="checkbox"
          className="h-5 w-5 accent-[var(--accent)]"
          checked={hsaCatchUp}
          disabled={disabled}
          onChange={(e) => setHsaCatchUp(e.target.checked)}
        />
        55 or over this year (HSA catch-up)
      </label>
      <div className="flex flex-col gap-1">
        <label htmlFor={leadId} className="text-sm text-fg">
          Paychecks before an election change takes effect
        </label>
        <select
          id={leadId}
          className={INPUT_CLASS.replace("w-full", "w-24")}
          value={lead}
          disabled={disabled}
          onChange={(e) => setLead(Number(e.target.value))}
        >
          {[0, 1, 2, 3, 4, 5, 6].map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <span className="text-xs text-fg-secondary">
          Most payroll systems need one pay period. The Contributions view uses this to pick the paycheck to change on.
        </span>
      </div>
      {isAdmin && (
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" variant="primary" disabled={save.isPending}>
            {save.isPending ? "Saving…" : `Save ${person.display_name}`}
          </Button>
          <span aria-live="polite" className="text-sm">
            {status && (
              <span className={cn("inline-flex animate-fade-in items-center gap-1.5", status.ok ? "text-good" : "text-bad")}>
                {status.ok ? <CircleCheck size={14} aria-hidden="true" /> : <CircleAlert size={14} aria-hidden="true" />}
                {status.text}
              </span>
            )}
          </span>
        </div>
      )}
    </form>
  );
}
