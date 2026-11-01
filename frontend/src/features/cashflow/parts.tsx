import { CircleAlert, CircleCheck, CircleMinus } from "lucide-react";
import type { ReactNode } from "react";
import { Badge } from "../../components/ui/badge";
import type { GroupContents } from "../../lib/api";
import { cn } from "../../lib/cn";
import { formatMoney } from "../../lib/format";
import type { PlanEvent } from "./api";

const CONTENTS_LEAD: Record<GroupContents["kind"], string> = { bills: "Bills", transfers: "Transfers", categories: "Categories" };

/**
 * What is in a plan group (E5): "Bills: Pinecrest Mortgage, City Power and
 * Gas, Green Thumb Lawn Care, and 2 more". Commas only, since names can hold
 * "and". Null when there is nothing to name.
 */
export function contentsText(c: GroupContents | null | undefined): string | null {
  if (!c || c.names.length === 0) return null;
  return `${CONTENTS_LEAD[c.kind]}: ${c.names.join(", ")}${c.more > 0 ? `, and ${c.more} more` : ""}`;
}

export type EventStatus = NonNullable<PlanEvent["status"]>;

const EVENT_STATUS: Record<EventStatus, { text: string; hint: string }> = {
  confirmed: { text: "Confirmed", hint: "Scheduled, or seen on a statement" },
  expected: { text: "Expected", hint: "Likely, not confirmed yet" },
  target: { text: "Target", hint: "A goal you set" },
  suggested: { text: "Suggested", hint: "An idea from the chat, not decided" },
};

/**
 * Format 1.4: how sure a cash flow change is. Neutral: the word says it, and
 * the change amount already carries the color. Left out: no badge.
 */
export function EventStatusBadge({ status }: { status: EventStatus | null | undefined }) {
  if (!status) return null;
  const s = EVENT_STATUS[status];
  return <Badge title={s.hint}>{s.text}</Badge>;
}

/** Small gray pill, like "plan" or "payment basis". */
export function Pill({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "ml-2 inline-flex h-5 items-center whitespace-nowrap rounded-full bg-subtle px-2 align-middle text-xs font-medium text-fg-secondary",
        className,
      )}
    >
      {children}
    </span>
  );
}

export type LineStatus = "over" | "on_target" | "under" | "no_plan" | "no_actual";

/**
 * Remaining against a plan as a tinted pill with words, never color alone.
 * `remaining` is plan minus actual (negative is over). For saved and
 * invested, more than the plan is good, so `keep` flips the tone.
 */
export function RemainingPill({
  status,
  remaining,
  keep = false,
  incomplete = false,
}: {
  status: LineStatus;
  remaining: number | null;
  keep?: boolean;
  /** The month's statements are not all in: only an overspend is final. */
  incomplete?: boolean;
}) {
  if (status === "no_plan" || remaining === null) return <TonePill tone="neutral">No plan</TonePill>;
  if (status === "no_actual") return <TonePill tone="neutral">No actual yet</TonePill>;
  const over = remaining < 0;
  if (incomplete && (keep || !over)) return <TonePill tone="neutral">Not in yet</TonePill>;
  const amount = formatMoney(Math.abs(remaining));
  if (amount === "$0") return <TonePill tone="good">On target</TonePill>;
  if (keep) {
    return over ? <TonePill tone="good">{amount} more than plan</TonePill> : <TonePill tone="neutral">{amount} to go</TonePill>;
  }
  return over ? <TonePill tone="bad">{amount} over</TonePill> : <TonePill tone="good">{amount} left</TonePill>;
}

function TonePill({ tone, children }: { tone: "good" | "bad" | "neutral"; children: ReactNode }) {
  const cls =
    tone === "good" ? "bg-tint-good text-good" : tone === "bad" ? "bg-tint-bad text-bad" : "bg-subtle text-fg-secondary";
  const Icon = tone === "bad" ? CircleAlert : tone === "good" ? CircleCheck : CircleMinus;
  return (
    <span className={cn("inline-flex h-6 items-center gap-1 whitespace-nowrap rounded-full px-2 text-xs font-medium tabular", cls)}>
      <Icon size={12} aria-hidden="true" />
      {children}
    </span>
  );
}

/** Thin progress line: the fill is the actual, the dark tick is the plan. Past the tick is over. */
export function ProgressLine({
  actual,
  plan,
  color,
  label,
}: {
  actual: number;
  plan: number | null;
  /** A token var for the fill. */
  color: string;
  label: string;
}) {
  if (plan === null || plan <= 0) return null;
  const max = Math.max(actual, plan) * 1.1 || 1;
  const fill = (Math.max(0, actual) / max) * 100;
  const tick = (plan / max) * 100;
  return (
    <div
      className="relative h-1 rounded-full bg-subtle"
      role="img"
      aria-label={`${label}: ${formatMoney(actual)} of ${formatMoney(plan)} plan`}
    >
      <span className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${fill}%`, background: color }} />
      <span className="absolute -top-1 h-3 w-0.5 rounded-full bg-chart-target" style={{ left: `calc(${tick}% - 1px)` }} />
    </div>
  );
}
