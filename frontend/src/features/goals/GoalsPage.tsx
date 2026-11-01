import { useQuery } from "@tanstack/react-query";
import { CircleAlert, Target } from "lucide-react";
import { Fragment, type ReactNode } from "react";
import { Link } from "react-router";
import { AsOf, EstBadge, Money } from "../../components/money";
import { estimatesNote } from "../../components/money/EstBadge";
import { PageHeader } from "../../components/shell/PageHeader";
import { Badge } from "../../components/ui/badge";
import { Card, CardHeader } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorBanner } from "../../components/ui/error-banner";
import { type GoalItem, type GoalKind, getGoals, goalKeys } from "../../lib/api";
import { formatDate } from "../../lib/format";
import { ListSkeleton, SourceLine } from "../recurring/parts";

// Goals (format 1.4, docs/plans/format-14.md section 7): each goal from the chat's newest
// monthly run, with now = its accounts' balances today. A plain list: the college
// projection comes later (D2). The start year shows here only, never on the Dashboard.

export const KIND_TEXT: Record<GoalKind, string> = { college: "College", save_up: "Save up" };

/** "College · For Jamie · Starts 2034" or "Save up · By Dec 31, 2027". Parts left out when null. */
export function goalMeta(goal: GoalItem): string {
  const parts = [KIND_TEXT[goal.kind]];
  if (goal.person_name) parts.push(`For ${goal.person_name}`);
  if (goal.start_year !== null) parts.push(`Starts ${goal.start_year}`);
  if (goal.target_date) parts.push(`By ${formatDate(goal.target_date)}`);
  return parts.join(" · ");
}

/** The est. note for now: one estimated account's own note, or a list of them. */
export function estimateText(goal: GoalItem): string | null {
  const estimated = goal.accounts.flatMap((a) => (a.estimate ? [{ ...a, estimate: a.estimate }] : []));
  const [first] = estimated;
  if (!first) return null;
  if (estimated.length === 1) return first.estimate.note;
  return estimatesNote(estimated.map((a) => ({ display_name: a.display_name, based_on: a.estimate.based_on })));
}

function Pair({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-fg-secondary">{label}</dt>
      <dd className="m-0 flex flex-wrap items-center gap-x-1.5 text-base font-medium text-fg">{children}</dd>
    </div>
  );
}

function GoalCard({ goal }: { goal: GoalItem }) {
  const note = estimateText(goal);
  const missing = goal.accounts.filter((a) => a.missing);
  return (
    <li className="min-w-0">
      <Card className="h-full">
        <CardHeader title={goal.name} subtitle={goalMeta(goal)} className="mb-3" />
        {goal.note && <p className="m-0 mb-3 text-sm text-fg-secondary">{goal.note}</p>}
        <dl className="m-0 grid grid-cols-3 gap-3">
          <Pair label="Now">
            <Money cents={goal.now_cents} />
            {note && <EstBadge note={note} />}
            <AsOf date={goal.as_of} stale={goal.stale} showStaleText className="basis-full font-normal" />
          </Pair>
          <Pair label="Target">
            <Money cents={goal.target_cents} />
          </Pair>
          <Pair label="To go">
            {goal.reached ? <Badge tone="good">Reached</Badge> : <Money cents={goal.to_go_cents} empty="Unknown" />}
          </Pair>
        </dl>
        {note && <p className="m-0 mt-3 text-sm text-fg-secondary">{note}</p>}
        {missing.length > 0 && goal.now_cents !== null && (
          <p className="m-0 mt-3 flex items-start gap-1.5 text-sm text-warn">
            <CircleAlert size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
            <span>No balance yet for {missing.map((a) => a.display_name).join(", ")}. Now leaves it out.</span>
          </p>
        )}
        <p className="m-0 mt-3 text-sm text-fg-secondary">
          From{" "}
          {goal.accounts.map((a, i) => (
            <Fragment key={a.account_key}>
              {i > 0 && ", "}
              <Link
                to={`/accounts?account=${encodeURIComponent(a.account_key)}`}
                className="inline-flex min-h-11 items-center md:min-h-0"
              >
                {a.display_name}
              </Link>
            </Fragment>
          ))}
        </p>
      </Card>
    </li>
  );
}

export function GoalsPage() {
  const q = useQuery({ queryKey: goalKeys.page, queryFn: getGoals });
  const goals = q.data?.goals ?? [];

  return (
    <>
      <PageHeader title="Goals" />
      <div className="flex flex-col gap-4 pb-8">
        {q.isError && (
          <ErrorBanner message="Could not load goals." onRetry={() => void q.refetch()} retrying={q.isFetching} />
        )}
        {q.isPending && (
          <Card>
            <ListSkeleton rows={2} />
          </Card>
        )}
        {q.data && goals.length === 0 && (
          <Card>
            <EmptyState icon={<Target size={28} strokeWidth={1.5} aria-hidden="true" />} title="No goals yet.">
              Goals come with the monthly run from the statement chat.
            </EmptyState>
          </Card>
        )}
        {goals.length > 0 && (
          <>
            <p className="m-0 text-sm text-fg-secondary">Now adds up what each goal's accounts hold today.</p>
            <ul aria-label="Goals" className="m-0 grid list-none gap-4 p-0 lg:grid-cols-2">
              {goals.map((g) => (
                <GoalCard key={g.key} goal={g} />
              ))}
            </ul>
          </>
        )}
        {q.data && <SourceLine runId={q.data.source_run_id} />}
      </div>
    </>
  );
}
