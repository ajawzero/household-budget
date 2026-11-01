import type { ReactNode } from "react";
import { CircleAlert, CircleCheck, Clock } from "lucide-react";
import { Link } from "react-router";
import { Money } from "../../components/money/Money";
import { Badge } from "../../components/ui/badge";
import { Card, CardHeader } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import type { InstallmentPlan, PlanCard, PlansResponse, PlanWarningRow } from "../../lib/api";
import { cn } from "../../lib/cn";
import { formatDate, formatMoney, formatMonth } from "../../lib/format";
import { KpiStrip, ListSkeleton } from "./parts";
import { StepDownChart, stepRows } from "./StepDownChart";

/** Level words, so a deadline's level never shows by color alone. High is 45 days or less, medium 90. */
const LEVEL_TEXT: Record<PlanWarningRow["level"], string> = { high: "Urgent", medium: "Soon", low: "Later" };

/** "Pay before" and promo end dates, most urgent first. High is 45 days or less. */
export function warningText(w: PlanWarningRow, remaining: number | null | undefined): string {
  const days = `${w.days_left} ${w.days_left === 1 ? "day" : "days"} left`;
  const left = remaining ? ` ${formatMoney(remaining)} is still owed.` : "";
  if (w.kind === "pay_before") {
    return `Pay off ${w.name} on the ${w.account_name} by ${formatDate(w.date)} (${days}). If any of it is left, interest is charged on the whole purchase.${left}`;
  }
  return `The 0% promo on ${w.name} (${w.account_name}) ends ${formatDate(w.date)} (${days}).${left}`;
}

function Warnings({ warnings, plans }: { warnings: PlanWarningRow[]; plans: Map<string, InstallmentPlan> }) {
  if (warnings.length === 0) return null;
  return (
    <ul className="m-0 flex list-none flex-col gap-2 p-0" aria-label="Plan deadlines">
      {warnings.map((w) => {
        const high = w.level === "high";
        return (
          <li
            key={`${w.plan_key}-${w.kind}`}
            className={cn(
              "flex items-start gap-3 rounded-card px-4 py-3 text-sm shadow-card",
              high ? "bg-tint-bad text-fg" : w.level === "medium" ? "bg-tint-warn text-fg" : "bg-card text-fg",
            )}
          >
            {high ? (
              <CircleAlert size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-bad" />
            ) : (
              <Clock size={18} aria-hidden="true" className={cn("mt-0.5 shrink-0", w.level === "medium" ? "text-warn" : "text-fg-secondary")} />
            )}
            <p className="m-0">
              <span className={cn("font-semibold", high ? "text-bad" : w.level === "medium" ? "text-warn" : "text-fg")}>
                {LEVEL_TEXT[w.level]}: {w.kind === "pay_before" ? "deferred interest" : "promo ends"}.{" "}
              </span>
              {warningText(w, plans.get(w.plan_key)?.remaining_cents)}
            </p>
          </li>
        );
      })}
    </ul>
  );
}

/** "4 of 6 payments" with a thin bar. Plans with no count show their end month only. */
function Progress({ plan }: { plan: InstallmentPlan }) {
  if (plan.payments_total && plan.payments_made !== null) {
    const pct = Math.min(100, (plan.payments_made / plan.payments_total) * 100);
    return (
      <div>
        <p className="m-0 text-sm text-fg">
          {plan.progress_label ?? `${plan.payments_made} of ${plan.payments_total}`} payments
          {plan.payments_left !== null && <span className="text-fg-secondary"> · {plan.payments_left} left</span>}
        </p>
        <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-subtle" aria-hidden="true">
          <span className="block h-full rounded-full bg-accent" style={{ width: `${pct}%` }} />
        </div>
        {plan.billed_count !== plan.payments_made && (
          <p className="m-0 mt-1 text-xs text-warn">
            The statements show {plan.billed_count} billed, the chat says {plan.payments_made}.
          </p>
        )}
      </div>
    );
  }
  return <p className="m-0 text-sm text-fg-secondary">No payment count{plan.billed_count ? `. ${plan.billed_count} billed so far` : ""}</p>;
}

/** Format 1.4: "Paid off May 2026", or "Paid off May 2026 · early" before its end month. Null with no date. */
export function paidOffText(plan: InstallmentPlan): string | null {
  if (!plan.paid_off_on) return null;
  return `Paid off ${formatMonth(plan.paid_off_on.slice(0, 7))}${plan.paid_off_early ? " · early" : ""}`;
}

/** A paid off plan: one badge, with its month when known. A closed plan: "Closed", plus when it was paid off. */
function DoneBadges({ plan }: { plan: InstallmentPlan }) {
  if (plan.status === "active") return null;
  const paid = paidOffText(plan);
  const title = plan.paid_off_on ? `Paid off ${formatDate(plan.paid_off_on)}` : undefined;
  if (plan.status === "paid_off") {
    return (
      <Badge tone="good" title={title}>
        {paid ?? "Paid off"}
      </Badge>
    );
  }
  return (
    <>
      <Badge tone="good">Closed</Badge>
      {paid && (
        <Badge tone="good" title={title}>
          {paid}
        </Badge>
      )}
    </>
  );
}

function PlanRow({ plan }: { plan: InstallmentPlan }) {
  return (
    <li className="grid gap-3 border-t border-line py-4 first:border-t-0 first:pt-0 last:pb-0 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_repeat(3,minmax(0,0.7fr))] md:items-start">
      <div className="min-w-0">
        <p className="m-0 font-medium text-fg">{plan.name}</p>
        {plan.note && <p className="m-0 mt-0.5 break-words text-sm text-fg-secondary">{plan.note}</p>}
        <div className="mt-1 flex flex-wrap gap-1">
          <Badge>{plan.kind_label}</Badge>
          {plan.included_in_spending_plan && <Badge tone="accent">In the spending plan</Badge>}
          <DoneBadges plan={plan} />
          {plan.warning && (
            <Badge
              tone={plan.warning.level === "high" ? "bad" : plan.warning.level === "medium" ? "warn" : "neutral"}
              icon={plan.warning.level === "high" ? <CircleAlert size={12} aria-hidden="true" /> : <Clock size={12} aria-hidden="true" />}
            >
              {LEVEL_TEXT[plan.warning.level]}: {plan.warning.kind === "pay_before" ? "pay before" : "promo ends"} {formatDate(plan.warning.date)}
            </Badge>
          )}
        </div>
        {(plan.components.length > 1 || plan.components.some((c) => c.trip_key)) && (
          <p className="m-0 mt-1 text-xs text-fg-secondary">
            {plan.components.map((c, i) => (
              <span key={i}>
                {i > 0 && " · "}
                {c.merchant} {formatMoney(c.amount_cents)}
                {c.trip_key && <ComponentTrip tripKey={c.trip_key} name={c.trip_name} />}
              </span>
            ))}
          </p>
        )}
        {plan.refund_cents > 0 && (
          <p className="m-0 mt-1 text-xs text-fg-secondary">Refund {formatMoney(plan.refund_cents, { cents: true })}</p>
        )}
      </div>
      <Progress plan={plan} />
      <Field label="A month">
        {plan.status === "active" ? <Money cents={plan.monthly_cost_cents} withCents /> : "None"}
        {plan.monthly_fee_cents ? (
          <span className="block text-xs text-fg-secondary">incl. {formatMoney(plan.monthly_fee_cents, { cents: true })} fee</span>
        ) : null}
        {plan.total_fees_cents ? (
          <span className="block text-xs text-fg-secondary">
            {formatMoney(plan.total_fees_cents, { cents: true })} in fees in all
            {plan.fees_posted_cents ? `, ${formatMoney(plan.fees_posted_cents, { cents: true })} posted` : ""}
          </span>
        ) : null}
      </Field>
      <Field label="Ends">{plan.end_month ? formatMonth(plan.end_month) : "Open"}</Field>
      <Field label="Left">
        <Money cents={plan.status === "paid_off" && plan.remaining_cents === null ? 0 : plan.remaining_cents} withCents empty="Not known" />
        <span className="block text-xs text-fg-secondary">of {formatMoney(plan.original_cents)}</span>
      </Field>
    </li>
  );
}

/** " for Ski weekend", linking to Travel. A trip the app does not have shows its key. */
function ComponentTrip({ tripKey, name }: { tripKey: string; name: string | null }) {
  if (!name) return <> (trip {tripKey}, not in Travel)</>;
  return (
    <>
      {" for "}
      <Link to={`/travel?trip=${encodeURIComponent(tripKey)}`}>{name}</Link>
    </>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-sm md:block md:text-right">
      <span className="text-fg-secondary md:hidden">{label}</span>
      <span className="text-right text-fg">{children}</span>
    </div>
  );
}

/**
 * The card's line under its name. A card with no active plan says so, with a
 * check: "All plans paid off · last payment May 2026" (the paid off month when
 * known, else the end month). Else "1 plan, 1 paid off
 * · $105 a month · $200 left". The last payment month shows only once it has passed.
 */
function CardSubtitle({ card, month }: { card: PlanCard; month: string }) {
  const active = card.plans.filter((p) => p.status === "active");
  const paid = card.plans.filter((p) => p.status === "paid_off");
  if (active.length === 0) {
    // The month it was paid off (format 1.4) beats the planned end month.
    const ends = paid
      .flatMap((p) => {
        const m = p.paid_off_on ? p.paid_off_on.slice(0, 7) : p.end_month;
        return m ? [m] : [];
      })
      .sort();
    const last = ends[ends.length - 1];
    const text =
      paid.length === card.plans.length
        ? `All plans paid off${last && last <= month ? ` · last payment ${formatMonth(last)}` : ""}`
        : `No active plans${paid.length ? ` · ${paid.length} paid off` : ""}`;
    return (
      <>
        <CircleCheck size={14} aria-hidden="true" className="mr-1 inline-block align-[-2px] text-good" />
        {text}
      </>
    );
  }
  const n = active.length;
  const known = active.some((p) => p.remaining_cents !== null);
  return (
    <>
      {n} {n === 1 ? "plan" : "plans"}
      {paid.length ? `, ${paid.length} paid off` : ""} · {formatMoney(card.monthly_cents)} a month ·{" "}
      {known ? `${formatMoney(card.remaining_cents)} left` : "balance not known"}
    </>
  );
}

function CardPlans({ card, month }: { card: PlanCard; month: string }) {
  return (
    <Card>
      <CardHeader title={card.account_name} subtitle={<CardSubtitle card={card} month={month} />} />
      <div className="mb-2 hidden grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_repeat(3,minmax(0,0.7fr))] gap-3 text-xs font-medium uppercase tracking-wider text-fg-secondary md:grid" aria-hidden="true">
        <span>Plan</span>
        <span>Progress</span>
        <span className="text-right">A month</span>
        <span className="text-right">Ends</span>
        <span className="text-right">Left</span>
      </div>
      <ul className="m-0 list-none p-0" aria-label={`Plans on the ${card.account_name}`}>
        {card.plans.map((p) => (
          <PlanRow key={p.key} plan={p} />
        ))}
      </ul>
    </Card>
  );
}

/** "1 active plan", "2 active plans". */
function activeText(n: number): string {
  return `${n} active ${n === 1 ? "plan" : "plans"}`;
}

/** Plans tab: deadlines first, then the totals, the step-down chart and the plans by card. */
export function PlansView({ data, loading, error }: { data: PlansResponse | undefined; loading: boolean; error: string | null }) {
  const plans = new Map((data?.cards ?? []).flatMap((c) => c.plans.map((p) => [p.key, p] as const)));
  const names = new Map([...plans.values()].map((p) => [p.key, p.name]));
  const unknown = data?.remaining_unknown.length ?? 0;
  return (
    <div className="flex flex-col gap-4">
      {data && <Warnings warnings={data.warnings} plans={plans} />}
      <KpiStrip
        label="Plan totals"
        loading={loading && !data}
        items={[
          {
            key: "deferred",
            label: "Total deferred",
            value: <Money cents={data?.total_deferred_cents} />,
            sub: unknown ? `${unknown} ${unknown === 1 ? "plan has" : "plans have"} no balance yet` : activeText(data?.active_count ?? 0),
          },
          { key: "monthly", label: "A month", value: <Money cents={data?.monthly_total_cents} />, sub: "Payments plus fees" },
          {
            key: "shortest",
            label: "Shortest",
            value: data?.shortest?.end_month ? formatMonth(data.shortest.end_month) : "None",
            sub: data?.shortest?.name,
          },
          {
            key: "longest",
            label: "Longest",
            value: data?.longest?.end_month ? formatMonth(data.longest.end_month) : "None",
            sub: data?.longest?.name,
          },
        ]}
      />
      <StepDownChart rows={data ? stepRows(data.step_downs, names) : undefined} loading={loading} error={error} />
      {!data ? (
        <Card>{loading ? <ListSkeleton /> : <EmptyState title="Plans did not load." />}</Card>
      ) : data.cards.length === 0 ? (
        <Card>
          <EmptyState title="No installment plans.">Plan It, Pay Over Time, installments and 0% promos show here.</EmptyState>
        </Card>
      ) : (
        data.cards.map((c) => <CardPlans key={c.account_key} card={c} month={data.today.slice(0, 7)} />)
      )}
    </div>
  );
}
