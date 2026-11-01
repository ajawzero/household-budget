import { CircleAlert, CircleCheck, CircleMinus, Info } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";
import { Badge } from "../../../components/ui/badge";
import { Card, CardHeader } from "../../../components/ui/card";
import { ErrorBanner } from "../../../components/ui/error-banner";
import { Skeleton } from "../../../components/ui/skeleton";
import { cn } from "../../../lib/cn";
import { formatDate, formatMoney, formatPct, formatShortDate } from "../../../lib/format";
import {
  type ContributionsOut,
  type HsaFamily,
  type OtherContribution,
  type PersonContributions,
  type PlanRow,
  useContributions,
} from "./api";
import { ContributionMeter, MeterLegend } from "./ContributionMeter";

const CADENCE: Record<string, string> = {
  weekly: "Paid every week",
  biweekly: "Paid every two weeks",
  semimonthly: "Paid twice a month",
  monthly: "Paid once a month",
};

/**
 * Investments > Contributions: each person's 401k and HSA so far this year
 * against the IRS limit, where the paychecks left take it by Dec 31, and the
 * paycheck to change the election on.
 */
export function ContributionsTab({ year }: { year?: number }) {
  const q = useContributions(year);
  if (q.isPending) {
    return (
      <div role="status" aria-label="Loading contributions" className="flex flex-col gap-4">
        <Skeleton className="h-72 rounded-card" />
      </div>
    );
  }
  if (q.isError) {
    return <ErrorBanner message="Could not load contributions." onRetry={() => void q.refetch()} retrying={q.isFetching} />;
  }
  const d = q.data;
  return (
    <div className="flex flex-col gap-4">
      <LimitsNotice data={d} />
      {d.people.length === 0 ? (
        <Card>
          <p className="m-0 text-fg-secondary">
            No 401k or HSA contributions for {d.year} yet. They come in with pay stubs from the monthly chat.
          </p>
        </Card>
      ) : (
        <div className="flex flex-col gap-4">
          {d.people.map((p) => (
            <PersonCard key={p.person_key} person={p} />
          ))}
        </div>
      )}
      {d.hsa_family && <FamilyHsa family={d.hsa_family} />}
      {d.other.length > 0 && <OtherCard rows={d.other} year={d.year} />}
    </div>
  );
}

/** Says when the limits in use are not confirmed, or missing for the year. */
export function LimitsNotice({ data }: { data: ContributionsOut }) {
  const unconfirmed = data.limits.filter((l) => !l.confirmed);
  if (data.limits_found && unconfirmed.length === 0) return null;
  const text = !data.limits_found
    ? `No IRS limits for ${data.year} yet, so there is nothing to compare to. Add them in Settings.`
    : `The ${data.year} limits are the IRS figures the app started with. They are not confirmed yet.`;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-card border border-line px-4 py-3 text-sm md:px-5">
      <span className="inline-flex items-start gap-2 text-warn">
        <CircleAlert size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
        {text}
      </span>
      <Link to="/settings/limits" className="inline-flex min-h-11 items-center md:min-h-0">
        {data.limits_found ? "Check and confirm" : "Add limits"}
      </Link>
    </div>
  );
}

function PersonCard({ person }: { person: PersonContributions }) {
  const s = person.schedule;
  const sub = [
    s.cadence ? CADENCE[s.cadence] : "Pay schedule not known yet",
    s.last_pay_date ? `newest pay stub ${formatDate(s.last_pay_date)}` : null,
  ]
    .filter(Boolean)
    .join(", ");
  return (
    <Card>
      <CardHeader
        title={person.display_name}
        subtitle={`${sub}.`}
        actions={
          !person.settings_set && (
            <Link to="/settings/limits" className="inline-flex min-h-11 items-center text-sm md:min-h-0">
              Set age and coverage
            </Link>
          )
        }
      />
      <div className="flex flex-col divide-y divide-[var(--border)]">
        {person.plans.map((plan) => (
          <PlanBlock key={plan.kind} plan={plan} person={person.display_name} />
        ))}
      </div>
    </Card>
  );
}

function PlanBlock({ plan, person }: { plan: PlanRow; person: string }) {
  const status = statusLine(plan);
  return (
    <section aria-label={`${person}, ${plan.label}`} className="py-5 first:pt-0 last:pb-0">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 className="m-0 flex items-center gap-2 text-base font-medium text-fg">
          {plan.label}
          {plan.shared && <Badge>Family, shared</Badge>}
        </h3>
        <span className="text-sm text-fg-secondary">
          <span className="text-base font-medium tabular-nums text-fg">{formatMoney(plan.ytd_cents)}</span>
          {plan.limit_cents !== null && <> of {formatMoney(plan.limit_cents)}</>}
          {plan.ytd_as_of && <> through {formatShortDate(plan.ytd_as_of)}</>}
          {plan.limit_cents !== null && !plan.limit_confirmed && (
            <Badge tone="warn" className="ml-2" title="The IRS figure the app started with. Confirm it in Settings.">
              Limit not confirmed
            </Badge>
          )}
        </span>
      </div>
      {plan.limit_cents !== null && (
        <ContributionMeter
          ytd={plan.ytd_cents}
          projected={plan.projected_cents}
          limit={plan.limit_cents}
          label={`${person}, ${plan.label}`}
        />
      )}
      <MeterLegend ytd={plan.ytd_cents} projected={plan.projected_cents} limit={plan.limit_cents} />
      <p className={cn("m-0 mt-3 flex items-start gap-1.5 text-sm", status.tone)}>
        {status.icon}
        <span>{status.text}</span>
      </p>
      <Facts plan={plan} />
      <ElectionAdvice plan={plan} />
      <Details plan={plan} />
    </section>
  );
}

function statusLine(plan: PlanRow): { text: string; tone: string; icon: ReactNode } {
  const icon = (n: "ok" | "bad" | "info") => {
    const props = { size: 14, "aria-hidden": true, className: "mt-0.5 shrink-0" } as const;
    if (n === "ok") return <CircleCheck {...props} />;
    if (n === "bad") return <CircleAlert {...props} />;
    return <CircleMinus {...props} />;
  };
  const room = plan.projected_room_cents;
  switch (plan.status) {
    case "over":
      return {
        text:
          plan.projected_cents !== null && room !== null
            ? `On pace for ${formatMoney(plan.projected_cents)} by Dec 31. That is ${formatMoney(-room)} over the limit.`
            : "Over the limit.",
        tone: "text-bad",
        icon: icon("bad"),
      };
    case "at_limit":
      return { text: "On pace to land on the limit by Dec 31.", tone: "text-good", icon: icon("ok") };
    case "under":
      return {
        text:
          plan.projected_cents !== null && room !== null
            ? `On pace for ${formatMoney(plan.projected_cents)} by Dec 31, ${formatMoney(room)} under the limit.`
            : "Under the limit.",
        tone: "text-fg-secondary",
        icon: icon("info"),
      };
    case "no_limit":
      return { text: "No limit to compare to yet.", tone: "text-fg-secondary", icon: icon("info") };
    case "no_schedule":
      return { text: "The pay schedule is not clear from the stubs yet, so there is no projection.", tone: "text-fg-secondary", icon: icon("info") };
    default:
      return { text: "No contributions this year yet.", tone: "text-fg-secondary", icon: icon("info") };
  }
}

function Facts({ plan }: { plan: PlanRow }) {
  const items: [string, ReactNode][] = [];
  if (plan.room_cents !== null) {
    items.push(["Room left", plan.room_cents < 0 ? `${formatMoney(-plan.room_cents)} over` : formatMoney(plan.room_cents)]);
  }
  if (plan.per_check_cents !== null) {
    const extra =
      plan.kind === "k401" && plan.per_check_pct !== null
        ? ` (${formatPct(plan.per_check_pct, true)} of gross)`
        : plan.per_check_employer_cents
          ? `, ${formatMoney(plan.per_check_employer_cents)} of it from the employer`
          : "";
    items.push(["Per paycheck", `${formatMoney(plan.per_check_cents)}${extra}`]);
  }
  if (plan.checks_left !== null) items.push(["Paychecks left", String(plan.checks_left)]);
  if (plan.limit_catch_up_cents) items.push(["Catch-up in the limit", formatMoney(plan.limit_catch_up_cents)]);
  if (!items.length) return null;
  return (
    <dl className="m-0 mt-4 grid grid-cols-2 gap-x-6 gap-y-3 text-sm md:grid-cols-4">
      {items.map(([k, v]) => (
        <div key={k} className="min-w-0">
          <dt className="text-fg-secondary">{k}</dt>
          <dd className="m-0 tabular-nums text-fg">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Which paycheck to change the election on, and to what. */
export function adviceText(plan: PlanRow): string | null {
  if (plan.status === "at_limit") return "No change needed. The current election lands on the limit.";
  if (plan.status !== "under" && plan.status !== "over") return null;
  if (!plan.change_on) {
    return plan.status === "over"
      ? "Too few paychecks are left to change the election in time. Ask payroll about the last checks."
      : "Too few paychecks are left to change the election this year.";
  }
  const when = `the ${formatDate(plan.change_on)} paycheck`;
  const before =
    plan.checks_before_change !== null && plan.checks_from_change !== null
      ? ` ${plural(plan.checks_before_change, "paycheck")} at the current election ${plan.checks_before_change === 1 ? "comes" : "come"} first, then ${plural(plan.checks_from_change, "paycheck")} at the new one.`
      : "";
  if (plan.needed_per_check_cents === null) return `The election change would start with ${when}.${before}`;
  if (outOfReach(plan)) {
    const most = mostAtFullPay(plan);
    const left = most !== null && plan.room_cents !== null ? plan.room_cents - most : null;
    const tail =
      most !== null && left !== null && left > 0
        ? ` Even all of the gross pay from ${when} on adds about ${formatMoney(most)}, so about ${formatMoney(left)} of room stays unused.`
        : "";
    return `The limit is out of reach this year with the paychecks left.${tail} Raise the election as far as payroll allows, or plan to max out next year.`;
  }
  const amount = formatMoney(plan.needed_per_check_cents);
  const pct = plan.kind === "k401" && plan.needed_pct !== null ? ` (${formatPct(plan.needed_pct, true)} of gross)` : "";
  const verb = plan.status === "over" ? "To stay at the limit, lower" : "To reach the limit, raise";
  const who = plan.kind === "hsa" ? "the employee part" : "the election";
  return `${verb} ${who} to ${amount} a paycheck${pct} starting with ${when}.${before}`;
}

/** True when reaching the limit would take more than the whole paycheck. */
export function outOfReach(plan: PlanRow): boolean {
  if (plan.status !== "under" || plan.needed_pct === null) return false;
  return Number(plan.needed_pct) > 100;
}

/** About how much 100% of gross adds from the change paycheck on, or null. */
function mostAtFullPay(plan: PlanRow): number | null {
  const pct = plan.per_check_pct === null ? NaN : Number(plan.per_check_pct);
  if (!(pct > 0) || plan.per_check_cents === null || plan.checks_from_change === null) return null;
  const before = (plan.checks_before_change ?? 0) * plan.per_check_cents;
  const gross = (plan.per_check_cents * 100) / pct;
  return Math.round(before + gross * plan.checks_from_change);
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

function ElectionAdvice({ plan }: { plan: PlanRow }) {
  const text = adviceText(plan);
  if (!text) return null;
  return (
    <div className="mt-4 rounded-lg bg-subtle px-4 py-3 text-sm">
      <p className="m-0 font-medium text-fg">
        {outOfReach(plan)
          ? "Limit out of reach this year"
          : plan.change_on
            ? `Change on the ${formatShortDate(plan.change_on)} paycheck`
            : "Election"}
      </p>
      <p className="m-0 mt-0.5 text-fg-secondary">{text}</p>
    </div>
  );
}

/** Cross-check, the parts of the year to date, the 415(c) line and the server's notes. */
function Details({ plan }: { plan: PlanRow }) {
  const lines: string[] = [];
  const diff = plan.cross_check_diff_cents;
  if (diff !== null && plan.checks_counted > 0) {
    lines.push(
      diff === 0
        ? `Pay stub total matches the ${plural(plan.checks_counted, "paycheck")} added up.`
        : `Pay stub total is ${formatMoney(Math.abs(diff), { cents: true })} ${diff > 0 ? "more" : "less"} than the ${plural(plan.checks_counted, "paycheck")} added up. A missing stub or a payroll fix can do this.`,
    );
  }
  const parts = plan.ytd_parts;
  if (parts.employer_cents) lines.push(`Includes ${formatMoney(parts.employer_cents)} from the employer.`);
  if (parts.prior_employer_cents) lines.push(`Includes ${formatMoney(parts.prior_employer_cents)} from a prior employer.`);
  if (parts.personal_cents) lines.push(`Includes ${formatMoney(parts.personal_cents)} paid in directly.`);
  if (plan.k415c) {
    const k = plan.k415c;
    lines.push(
      `With the employer match: ${formatMoney(k.total_cents)} of the ${formatMoney(k.limit_cents)} total limit, ${formatMoney(k.room_cents)} left.`,
    );
  }
  for (const n of plan.notes) {
    if (lines.includes(n)) continue;
    if (n.startsWith("Reaching the limit would take") && outOfReach(plan)) continue; // the callout says it
    lines.push(n);
  }
  if (!lines.length) return null;
  return (
    <ul className="m-0 mt-4 flex list-none flex-col gap-1 p-0 text-sm text-fg-secondary">
      {lines.map((l) => (
        <li key={l} className="flex items-start gap-1.5">
          <Info size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
          {l.startsWith("Set HSA coverage") ? (
            <span>
              <Link to="/settings/limits">Set HSA coverage in Settings</Link> to see the limit.
            </span>
          ) : (
            l
          )}
        </li>
      ))}
    </ul>
  );
}

function FamilyHsa({ family }: { family: HsaFamily }) {
  return (
    <Card>
      <CardHeader title="HSA, family limit" subtitle="Family coverage shares one limit across the household." />
      <ContributionMeter
        ytd={family.ytd_cents}
        projected={family.projected_cents}
        limit={family.limit_cents}
        label="Household HSA"
      />
      <MeterLegend ytd={family.ytd_cents} projected={family.projected_cents} limit={family.limit_cents} />
      <p className="m-0 mt-3 text-sm text-fg-secondary">
        {family.room_cents >= 0
          ? `${formatMoney(family.room_cents)} of room left this year.`
          : `${formatMoney(-family.room_cents)} over the shared limit.`}
      </p>
    </Card>
  );
}

/** IRA and 529 money put in this year. The app has no limits for these. */
function OtherCard({ rows, year }: { rows: OtherContribution[]; year: number }) {
  return (
    <Card>
      <CardHeader title="IRA and 529" subtitle={`Money put in during ${year}. The app has no limits for these.`} />
      <ul className="m-0 list-none p-0">
        {rows.map((r) => (
          <li key={`${r.person_key}:${r.kind}:${r.account_key}`} className="border-b border-line py-3 first:pt-0 last:border-b-0 last:pb-0">
            <div className="flex items-baseline justify-between gap-3">
              <span className="min-w-0">
                <span className="block break-words text-sm font-medium text-fg">{r.account_name}</span>
                <span className="block text-xs text-fg-secondary">
                  {r.person} · {r.label}
                </span>
              </span>
              <span className="shrink-0 text-base font-medium tabular-nums text-fg">{formatMoney(r.ytd_cents)}</span>
            </div>
            <p className="m-0 mt-1 text-sm text-fg-secondary">
              {`${plural(r.count, "deposit")}, last ${formatMoney(r.last_cents)} on ${formatShortDate(r.last_date)}.`}
            </p>
            {r.gift_cents > 0 && <p className="m-0 text-sm text-fg-secondary">{`Includes ${formatMoney(r.gift_cents)} from gifts.`}</p>}
          </li>
        ))}
      </ul>
    </Card>
  );
}
