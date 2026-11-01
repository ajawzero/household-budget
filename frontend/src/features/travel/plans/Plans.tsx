import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { Card } from "../../../components/ui/card";
import { EmptyState } from "../../../components/ui/empty-state";
import { ErrorBanner } from "../../../components/ui/error-banner";
import { getTripPlans, type PlanSummary, tripPlanKeys, type TripPlansResponse } from "../../../lib/api";
import { formatTimestamp } from "../../../lib/format";
import { RUN_POLL_MS, showsStored, useAiStatus } from "../../ai/api";
import { ListSkeleton } from "../../recurring/parts";
import { rangeText, tripDates } from "../model";
import { nightsText, PLAN_STATUS_TEXT, PLAN_STATUS_TONE, planHref } from "./model";
import { PlanFormDrawer } from "./PlanForm";

/**
 * The saved trip plans (docs/plans/trip-planner.md section 9). Like every AI
 * feature they are hidden while AI is off or has no key (PLAN 7.7); stored
 * plans still show during a spend-limit pause, but no new plan starts.
 * The list is checked again every few seconds while a plan is being made.
 */
export function useTripPlans() {
  const shown = showsStored(useAiStatus().data);
  const q = useQuery({
    queryKey: tripPlanKeys.list,
    queryFn: getTripPlans,
    enabled: shown,
    refetchInterval: (query) => (query.state.data?.running_plan_id ? RUN_POLL_MS : false),
  });
  // A run this list saw end: its page and the AI usage changed too.
  const qc = useQueryClient();
  const running = q.data?.running_plan_id ?? null;
  const was = useRef(running);
  useEffect(() => {
    if (was.current && was.current !== running) {
      void qc.invalidateQueries({ queryKey: tripPlanKeys.one(was.current) });
      void qc.invalidateQueries({ queryKey: ["ai"] });
    }
    was.current = running;
  }, [running, qc]);
  return { shown, q };
}

/** The newest plan linked to each imported trip, for its card's "plan est.". */
export function plansByTrip(data: TripPlansResponse | undefined): Map<string, PlanSummary> {
  const out = new Map<string, PlanSummary>();
  for (const p of data?.plans ?? []) {
    // The list is newest first, so the first plan for a trip wins.
    if (p.trip_key && p.low_cents !== null && p.high_cents !== null && !out.has(p.trip_key)) out.set(p.trip_key, p);
  }
  return out;
}

/** "Plan a trip" by the page title. Only while AI may run. */
export function PlanTripButton({ data }: { data: TripPlansResponse | undefined }) {
  const [open, setOpen] = useState(false);
  if (!data?.available) return null;
  const busy = data.running_plan_id !== null;
  return (
    <>
      {/* Busy says why in words: a phone has no hover, and the note below may be off screen. */}
      <Button variant="primary" onClick={() => setOpen(true)} disabled={busy}>
        <Sparkles size={16} aria-hidden="true" />
        {busy ? "Plan being made…" : "Plan a trip"}
      </Button>
      <PlanFormDrawer open={open} onOpenChange={setOpen} people={data.people} estimateCents={data.estimate_cents} />
    </>
  );
}

export function PlansSection({ q }: { q: ReturnType<typeof useTripPlans>["q"] }) {
  const data = q.data;
  return (
    <section className="flex flex-col gap-3" aria-label="Trip plans">
      <div>
        <h2 className="m-0 text-lg font-medium text-fg">Trip plans</h2>
        <p className="m-0 text-sm text-fg-secondary">Made by Claude from web search and your rewards. Check before you book.</p>
      </div>
      {q.isError && !data ? (
        <ErrorBanner message="Could not load trip plans." onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : !data ? (
        <Card>
          <ListSkeleton rows={2} />
        </Card>
      ) : data.plans.length === 0 ? (
        <Card>
          <EmptyState title="No trip plans yet.">
            {data.available ? "Plan a trip, and Claude puts together flights, hotels, points and a day by day." : "Plans can be made again when AI is back on."}
          </EmptyState>
        </Card>
      ) : (
        <Card>
          {data.running_plan_id && (
            <p role="status" className="m-0 mb-3 rounded-lg bg-subtle px-3 py-2 text-sm text-fg">
              A plan is being made. This can take a few minutes.
            </p>
          )}
          <ul aria-label="Trip plans" className="m-0 list-none p-0">
            {data.plans.map((p) => (
              <PlanRow key={p.plan_id} plan={p} />
            ))}
          </ul>
        </Card>
      )}
    </section>
  );
}

function PlanRow({ plan: p }: { plan: PlanSummary }) {
  const by = p.created_by_name ? ` by ${p.created_by_name}` : "";
  return (
    <li className="grid gap-1 border-t border-line py-3 text-sm first:border-t-0 first:pt-0 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_auto_8rem] md:items-center md:gap-4">
      <span className="min-w-0">
        <Link to={planHref(p.plan_id)} className="inline-flex min-h-11 items-center font-medium md:min-h-0">
          {p.title || "Trip plan"}
        </Link>
        <span className="block text-xs text-fg-secondary">
          Updated {formatTimestamp(p.updated_at)}
          {by}
          {p.versions > 1 && ` · ${p.versions} versions`}
          {p.trip_name && ` · ${p.trip_name}`}
        </span>
      </span>
      <span className="text-fg-secondary">
        {tripDates(p)} · {nightsText(p.nights)}
      </span>
      <span>
        <Badge tone={PLAN_STATUS_TONE[p.status]}>{PLAN_STATUS_TEXT[p.status]}</Badge>
      </span>
      <span className="text-fg md:text-right">
        {p.low_cents !== null && p.high_cents !== null ? `est. ${rangeText(p.low_cents, p.high_cents)}` : <span className="text-fg-secondary">No estimate</span>}
      </span>
    </li>
  );
}
