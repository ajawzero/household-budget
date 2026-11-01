// The text cards: Next changes, To do and Latest review.
import { Circle, CircleCheck } from "lucide-react";
import { deltaTone } from "../../components/money/Delta";
import { Money } from "../../components/money/Money";
import type { NextChange, Review, Todo } from "../../lib/api";
import type { AiReview } from "../ai/api";
import { SafeInline } from "../ai/SafeText";
import { cn } from "../../lib/cn";
import { EventStatusBadge } from "../cashflow/parts";
import { formatMonth, formatShortDate } from "../../lib/format";
import { monthlyDeltaText } from "./format";
import { DashCard, type LoadState } from "./parts";

const ROW = "flex items-start justify-between gap-3 border-t border-line py-3 first:border-t-0 first:pt-0 last:pb-0";

// ---------- Next changes ----------

export function NextChangesCard({ changes, state }: { changes: NextChange[] | undefined; state: LoadState }) {
  return (
    <DashCard
      title="Next changes"
      subtitle="How the monthly plan moves next"
      state={state}
      empty={!changes?.length}
      emptyText="No changes coming up."
    >
      <ul className="m-0 list-none p-0">
        {changes?.map((c) => {
          const tone = deltaTone(c.delta_cents, "higher_is_better");
          return (
            <li key={c.key} className={ROW}>
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <p className="m-0 text-sm font-medium text-fg">{c.label}</p>
                  <EventStatusBadge status={c.status} />
                </div>
                <p className="m-0 text-sm text-fg-secondary">From {formatMonth(c.effective_month)}</p>
              </div>
              <span
                className={cn(
                  "tabular shrink-0 text-sm font-medium",
                  tone === "good" ? "text-good" : tone === "bad" ? "text-bad" : "text-fg-secondary",
                )}
              >
                {monthlyDeltaText(c.delta_cents)}
              </span>
            </li>
          );
        })}
      </ul>
    </DashCard>
  );
}

// ---------- To do ----------

export function TodoCard({ todos, state }: { todos: Todo[] | undefined; state: LoadState }) {
  return (
    <DashCard title="To do" subtitle="From the latest update" state={state} empty={!todos?.length} emptyText="Nothing to do.">
      <ul className="m-0 list-none p-0">
        {todos?.map((t) => {
          const done = t.status === "done";
          return (
            <li key={t.key} className={cn(ROW, "justify-start")}>
              {done ? (
                <CircleCheck size={18} className="mt-0.5 shrink-0 text-good" aria-hidden="true" />
              ) : (
                <Circle size={18} className="mt-0.5 shrink-0 text-fg-faint" aria-hidden="true" />
              )}
              <div className="min-w-0 flex-1">
                <p className={cn("m-0 text-sm font-medium", done ? "text-fg-secondary line-through" : "text-fg")}>
                  {t.title}
                  {done && <span className="sr-only"> (done)</span>}
                </p>
                {t.subtitle && <p className="m-0 text-sm text-fg-secondary">{t.subtitle}</p>}
              </div>
              {t.due_date && !done && (
                <span className="shrink-0 text-sm text-fg-secondary">Due {formatShortDate(t.due_date)}</span>
              )}
            </li>
          );
        })}
      </ul>
    </DashCard>
  );
}

// ---------- Latest review ----------

/**
 * The AI review of the newest monthly run when AI is on and one exists
 * (Phase 6), else the chat's own review.
 */
export function ReviewCard({
  review,
  aiReview,
  state,
}: {
  review: Review | null | undefined;
  aiReview?: AiReview | null;
  state: LoadState;
}) {
  if (aiReview) return <AiReviewCard review={aiReview} state={state} />;
  return (
    <DashCard
      title="Latest review"
      subtitle="Written by the chat with the monthly run"
      state={state}
      empty={!review}
      emptyText="No review yet."
    >
      {review && (
        <div className="flex flex-col gap-4 text-sm">
          <p className="m-0 text-base text-fg">{review.summary}</p>
          {review.key_numbers.length > 0 && (
            <dl className="m-0 grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
              {review.key_numbers.map((k) => (
                <div key={k.label} className="flex items-baseline justify-between gap-3 rounded-lg bg-subtle px-3 py-2">
                  <dt className="min-w-0 text-fg-secondary">{k.label}</dt>
                  <dd className="m-0 font-medium text-fg">
                    <Money cents={k.value} />
                  </dd>
                </div>
              ))}
            </dl>
          )}
          <ReviewList title="What changed" items={review.changes} />
          <ReviewList title="Anything odd" items={review.anomalies} />
          <ReviewList title="Top three" items={review.top_three} ordered />
        </div>
      )}
    </DashCard>
  );
}

function AiReviewCard({ review, state }: { review: AiReview; state: LoadState }) {
  const month = review.month ? formatMonth(review.month) : null;
  return (
    <DashCard
      title="Latest review"
      subtitle={month ? `Claude's review of ${month}` : "Claude's review of the latest month"}
      state={state}
      empty={false}
      emptyText=""
    >
      <div className="flex flex-col gap-4 text-sm">
        <p className="m-0 text-base text-fg">
          <SafeInline text={review.summary} />
        </p>
        <ReviewList title="What changed" items={review.changes} />
        <ReviewList title="Anything odd" items={review.anomalies} />
        <ReviewList title="Three things to do" items={review.actions} ordered />
      </div>
    </DashCard>
  );
}

function ReviewList({ title, items, ordered = false }: { title: string; items: string[]; ordered?: boolean }) {
  if (items.length === 0) return null;
  const List = ordered ? "ol" : "ul";
  return (
    <section>
      <h3 className="m-0 mb-1 text-sm font-medium text-fg">{title}</h3>
      <List className={cn("m-0 flex flex-col gap-1 pl-5 text-fg", ordered ? "list-decimal" : "list-disc")}>
        {items.map((item, i) => (
          <li key={i}>
            <SafeInline text={item} />
          </li>
        ))}
      </List>
    </section>
  );
}
