import type { UseQueryResult } from "@tanstack/react-query";
import { type FocusEvent, type ReactNode, useEffect, useId, useRef, useState } from "react";
import { Button } from "../../components/ui/button";
import { Card, CardHeader } from "../../components/ui/card";
import { Drawer } from "../../components/ui/drawer";
import { ErrorBanner } from "../../components/ui/error-banner";
import type { CreditIn, CreditReason, NeedsLook, ReviewRow, RewardBenefit, RewardsResponse, RewardsReview } from "../../lib/api";
import { formatDate, formatMoney, formatMonthShort } from "../../lib/format";
import { useMediaQuery } from "../../lib/media";
import { INPUT_CLASS } from "../accounts/vehicles/fields";
import { ListSkeleton } from "../recurring/parts";
import { useClearCredit, useSetCredit, useSetMark } from "./api";
import { ConfirmAction } from "../../components/ui/confirm-action";
import { benefitLabel, cardCredits, countText, guessText, startedCycles } from "./model";
import { FormStatus } from "./parts";
import { failText } from "./ProgramNotices";

type Review = UseQueryResult<RewardsReview>;

/** "A return", "A points credit", "Something else": why a row is not a benefit. */
export const REASON_TEXT: Record<CreditReason, string> = {
  return: "A return",
  points: "A points credit",
  other: "Something else",
};

const REASONS: CreditReason[] = ["return", "points", "other"];

function money(cents: number): string {
  return formatMoney(cents, { cents: true });
}

/**
 * "Card credits" on the Benefits tab: how many rows wait, with the button that
 * opens the review. Only while there is something in it.
 */
export function ReviewCard({ review, onOpen }: { review: RewardsReview | undefined; onOpen: () => void }) {
  if (!review) return null;
  const look = review.needs_a_look.length;
  const parts = [
    review.credits.length > 0 ? `${review.credits.length} to review` : null,
    look > 0 ? `${countText(look, "needs", "need")} a look` : null,
    review.returns.length > 0 ? `${review.returns.length} ${review.returns.length === 1 ? "looks like a return" : "look like returns"}` : null,
    review.trips.length > 0 ? `${countText(review.trips.length, "points credit", "points credits")} for a trip` : null,
  ].filter((p): p is string => p !== null);
  if (parts.length === 0) return null;
  return (
    <Card>
      <CardHeader title="Card credits" subtitle={`${parts.join(" · ")}.`} className="mb-3" />
      <Button variant="primary" className="w-full justify-center md:w-auto" onClick={onOpen}>
        Review card credits
      </Button>
    </Card>
  );
}

/**
 * "Card credits to review" (docs/plans/rewards-inputs.md 4 and 7): statement
 * credits no match word or choice places, with a best guess. A list on wider
 * screens (with a live count of what is left), one card at a time on phones.
 * Returns and points credits for a trip wait in closed groups. Lost choices,
 * rows no cycle holds, choices on a credit that takes no rows, the chat's
 * used with no row, and cycles over their amount need a look. Anyone signed
 * in decides. `onOpenBenefit` leaves the review for a benefit's drawer.
 */
export function ReviewCredits({
  data,
  review,
  open,
  onOpenChange,
  isAdmin,
  onOpenBenefit,
}: {
  data: RewardsResponse;
  review: Review;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  isAdmin: boolean;
  onOpenBenefit: (key: string) => void;
}) {
  const r = review.data;
  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      title="Card credits to review"
      description="Statement credits no match word places yet. Count each one for a benefit, or say what it is."
      widthClass="md:w-[560px]"
    >
      {!r ? (
        review.isError ? (
          <ErrorBanner message="Could not load card credits." onRetry={() => void review.refetch()} retrying={review.isFetching} />
        ) : (
          <ListSkeleton rows={3} />
        )
      ) : (
        <div className="flex flex-col gap-6">
          <ToReview rows={r.credits} benefits={data.benefits} />
          {(r.returns.length > 0 || r.trips.length > 0) && (
            <div className="flex flex-col gap-2">
              <ClosedGroup title="Looks like returns" rows={r.returns} benefits={data.benefits} />
              <ClosedGroup title="Points credits for a trip" rows={r.trips} benefits={data.benefits} />
            </div>
          )}
          <NeedsLookList items={r.needs_a_look} data={data} isAdmin={isAdmin} onOpenBenefit={onOpenBenefit} />
        </div>
      )}
    </Drawer>
  );
}

/**
 * When a decided row leaves, the button that had focus goes with it. Focus
 * moves to the row that took its place, or the done message. Put the handlers
 * on the list and `data-item` (its place) on each row.
 */
function useFocusAfterRemoval(count: number) {
  const ref = useRef<HTMLDivElement>(null);
  const at = useRef<number | null>(null);
  useEffect(() => {
    const box = ref.current;
    if (!box || at.current === null || box.contains(document.activeElement)) return;
    const items = box.querySelectorAll<HTMLElement>("[data-item]");
    const next = items[Math.min(at.current, items.length - 1)] ?? box.querySelector<HTMLElement>("[data-done]");
    next?.focus();
    at.current = null;
  }, [count]);
  return {
    ref,
    onFocus: (e: FocusEvent<HTMLElement>) => {
      const item = (e.target as HTMLElement).closest<HTMLElement>("[data-item]");
      at.current = item ? Number(item.dataset.item) : null;
    },
    // Focus that moves away on purpose forgets the row. A removed row blurs with nowhere to go.
    onBlur: (e: FocusEvent<HTMLElement>) => {
      if (e.relatedTarget !== null && !e.currentTarget.contains(e.relatedTarget as Node)) at.current = null;
    },
  };
}

function ToReview({ rows, benefits }: { rows: ReviewRow[]; benefits: RewardBenefit[] }) {
  const wide = useMediaQuery("(min-width: 768px)");
  const [index, setIndex] = useState(0);
  const { ref, onFocus, onBlur } = useFocusAfterRemoval(rows.length);
  const at = Math.min(index, Math.max(rows.length - 1, 0));
  const row = rows[at];
  return (
    <div ref={ref} onFocus={onFocus} onBlur={onBlur}>
      {rows.length === 0 ? (
        <p data-done tabIndex={-1} className="m-0 rounded-lg bg-tint-good px-3 py-3 text-sm text-fg outline-none">
          All card credits are matched.
        </p>
      ) : wide ? (
        <>
          <p className="m-0 mb-2 text-sm text-fg-secondary" aria-live="polite">
            {countText(rows.length, "card credit", "card credits")} to review.
          </p>
          <ul aria-label="Card credits to review" className="m-0 flex list-none flex-col p-0">
            {rows.map((r, i) => (
              <li key={r.txn_key} data-item={i} tabIndex={-1} className="border-t border-line py-3 outline-none first:border-t-0 first:pt-0">
                <ReviewItem row={r} benefits={benefits} />
              </li>
            ))}
          </ul>
        </>
      ) : (
        row && (
          <section aria-label="Card credits to review" className="flex flex-col gap-3">
            <div key={row.txn_key} data-item={0} tabIndex={-1} className="rounded-lg border border-line p-3 outline-none">
              <ReviewItem row={row} benefits={benefits} />
            </div>
            <div className="flex items-center justify-between gap-2">
              <Button disabled={at === 0} onClick={() => setIndex(at - 1)}>
                Previous
              </Button>
              <p className="m-0 text-sm text-fg-secondary" aria-live="polite">
                {at + 1} of {rows.length}
              </p>
              <Button disabled={at >= rows.length - 1} onClick={() => setIndex(at + 1)}>
                Next
              </Button>
            </div>
          </section>
        )
      )}
    </div>
  );
}

/** "Looks like returns (2)": closed at first. Each row can still be counted. */
function ClosedGroup({ title, rows, benefits }: { title: string; rows: ReviewRow[]; benefits: RewardBenefit[] }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const { ref, onFocus, onBlur } = useFocusAfterRemoval(rows.length);
  if (rows.length === 0) return null;
  return (
    <section aria-label={title} className="border-t border-line pt-2">
      <button
        type="button"
        className="inline-flex h-11 items-center rounded-lg px-1 text-sm font-medium text-accent-text hover:underline md:h-8"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((v) => !v)}
      >
        {title} ({rows.length})
      </button>
      <div ref={ref} id={id} hidden={!open} onFocus={onFocus} onBlur={onBlur}>
        {open && (
          <ul className="m-0 flex list-none flex-col p-0" aria-label={title}>
            {rows.map((r, i) => (
              <li key={r.txn_key} data-item={i} tabIndex={-1} className="border-t border-line py-3 outline-none first:border-t-0">
                <ReviewItem row={r} benefits={benefits} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

type Mode = "idle" | "other" | "not";

/**
 * Focus for the inline choices: the first control when they open, the
 * button that opened them when they close (Cancel).
 */
function useModeFocus(mode: Mode) {
  const pick = useRef<HTMLSelectElement>(null);
  const reason = useRef<HTMLButtonElement>(null);
  const other = useRef<HTMLButtonElement>(null);
  const not = useRef<HTMLButtonElement>(null);
  const was = useRef<Mode>(mode);
  useEffect(() => {
    if (mode !== "idle" && was.current === "idle") (mode === "other" ? pick : reason).current?.focus();
    if (mode === "idle" && was.current !== "idle") (was.current === "other" ? other : not).current?.focus();
    was.current = mode;
  }, [mode]);
  return { pick, reason, other, not };
}

/** One card credit: what the statement says, the guess, and the choices. */
function ReviewItem({ row, benefits }: { row: ReviewRow; benefits: RewardBenefit[] }) {
  const id = useId();
  const set = useSetCredit();
  const [mode, setMode] = useState<Mode>("idle");
  const [pick, setPick] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const refs = useModeFocus(mode);
  const credits = cardCredits(benefits, row.account_key);
  const names = new Map(credits.map((b) => [b.key, b.name]));
  // Only credits this page shows can take the row; the API refuses the rest.
  const options = row.options.filter((key) => names.has(key));
  const what = `${row.description}, ${money(row.cents)}`;
  const busy = set.isPending;

  function send(body: CreditIn) {
    setProblem(null);
    set.mutate({ txnKey: row.txn_key, body }, { onError: (err) => setProblem(failText(err)) });
  }

  return (
    <div className="flex flex-col gap-1">
      <p className="m-0 flex items-baseline justify-between gap-3">
        <span className="min-w-0 break-words font-medium text-fg">{row.description}</span>
        <span className="tabular shrink-0 text-fg">{money(row.cents)}</span>
      </p>
      <p className="m-0 text-sm text-fg-secondary">
        {formatDate(row.date)} · {row.account_name ?? row.account_key}
        {row.merchant && row.merchant !== row.description && ` · ${row.merchant}`}
      </p>
      {row.note ? (
        <p className="m-0 text-sm text-warn">{row.note}</p>
      ) : row.guess ? (
        <p className="m-0 text-sm text-fg">{guessText(row.guess)}</p>
      ) : (
        <p className="m-0 text-sm text-fg-secondary">
          {credits.length === 0 ? "No credit on this card takes statement rows." : "No credit on this card has a cycle for this date."}
        </p>
      )}
      {mode === "idle" && (
        <div className="mt-1 flex flex-wrap gap-2">
          {options.length > 0
            ? options.map((key) => (
                <Button key={key} size="sm" variant="primary" disabled={busy} onClick={() => send({ decision: "link", benefit_key: key })}>
                  Count for {names.get(key)}
                  <span className="sr-only">: {what}</span>
                </Button>
              ))
            : row.guess && (
                <Button size="sm" variant="primary" disabled={busy} onClick={() => send({ decision: "link", benefit_key: row.guess!.benefit_key })}>
                  Count it<span className="sr-only">: {what}, for {row.guess.benefit_name}</span>
                </Button>
              )}
          {credits.length > 0 && (
            <Button ref={refs.other} size="sm" disabled={busy} onClick={() => setMode("other")}>
              Different benefit<span className="sr-only">: {what}</span>
            </Button>
          )}
          <Button ref={refs.not} size="sm" disabled={busy} onClick={() => setMode("not")}>
            Not a benefit<span className="sr-only">: {what}</span>
          </Button>
        </div>
      )}
      {mode === "other" && (
        <form
          className="mt-1 flex flex-wrap items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (pick) send({ decision: "link", benefit_key: pick });
          }}
        >
          <span className="flex min-w-0 flex-col gap-1">
            <label htmlFor={`${id}-pick`} className="text-sm text-fg-secondary">
              Count for
            </label>
            <select
              ref={refs.pick}
              id={`${id}-pick`}
              className={INPUT_CLASS.replace("w-full", "w-auto max-w-full")}
              value={pick}
              onChange={(e) => setPick(e.target.value)}
            >
              <option value="">Pick a credit</option>
              {credits.map((b) => (
                <option key={b.key} value={b.key}>
                  {benefitLabel(b.name, null, b.origin === "app")}
                </option>
              ))}
            </select>
          </span>
          <Button type="submit" size="sm" variant="primary" disabled={busy || !pick}>
            Count it<span className="sr-only">: {what}</span>
          </Button>
          <Button size="sm" onClick={() => setMode("idle")}>
            Cancel
          </Button>
        </form>
      )}
      {mode === "not" && (
        <div className="mt-1 flex flex-col gap-2">
          <p id={`${id}-why`} className="m-0 text-sm text-fg">
            What is it?
          </p>
          <div className="flex flex-wrap gap-2" role="group" aria-labelledby={`${id}-why`}>
            {REASONS.map((reason, i) => (
              <Button
                key={reason}
                ref={i === 0 ? refs.reason : undefined}
                size="sm"
                disabled={busy}
                onClick={() => send({ decision: "not_benefit", reason })}
              >
                {REASON_TEXT[reason]}
              </Button>
            ))}
            <Button size="sm" variant="ghost" onClick={() => setMode("idle")}>
              Cancel
            </Button>
          </div>
        </div>
      )}
      <FormStatus problem={problem} saved={null} />
    </div>
  );
}

interface Over {
  key: string;
  name: string;
  start: string;
  overCents: number;
}

/** Cycles whose matched rows go over the amount, from the page. Their rows are in the benefit's cycles. */
function overCycles(data: RewardsResponse): Over[] {
  return data.benefits.flatMap((b) =>
    b.kind !== "credit"
      ? []
      : startedCycles(b, data.today)
          .filter((c) => c.over_cents > 0)
          .map((c) => ({ key: b.key, name: b.name, start: c.start, overCents: c.over_cents })),
  );
}

/**
 * "Needs a look" in the review: a choice that lost its row (Delete), a row
 * no cycle holds, a choice on a credit that takes no rows (Delete), the
 * chat's used with no row (Mark not used, or an admin adds a match word), and
 * a cycle over its amount (see its cycles).
 */
function NeedsLookList({
  items,
  data,
  isAdmin,
  onOpenBenefit,
}: {
  items: NeedsLook[];
  data: RewardsResponse;
  isAdmin: boolean;
  onOpenBenefit: (key: string) => void;
}) {
  const over = overCycles(data);
  if (items.length + over.length === 0) return null;
  return (
    <section aria-label="Needs a look" className="flex flex-col gap-1 border-t border-line pt-4">
      <h3 className="m-0 text-base font-medium text-fg">Needs a look</h3>
      <ul className="m-0 flex list-none flex-col p-0">
        {items.map((n) => (
          <li key={`${n.kind}:${n.txn_key ?? ""}:${n.benefit_key ?? ""}:${n.cycle_start ?? ""}`} className="border-t border-line py-3 first:border-t-0">
            <LookItem item={n} isAdmin={isAdmin} onOpenBenefit={onOpenBenefit} />
          </li>
        ))}
        {over.map((o) => (
          <li key={`over:${o.key}:${o.start}`} className="border-t border-line py-3 first:border-t-0">
            <p className="m-0 text-sm font-medium text-fg">
              {o.name}, {formatMonthShort(o.start)}
            </p>
            <p className="m-0 text-sm text-fg-secondary">More than the credit by {money(o.overCents)}. Two rows may be the same credit, or one may be a return.</p>
            <Button size="sm" className="mt-2" onClick={() => onOpenBenefit(o.key)}>
              See its cycles<span className="sr-only">: {o.name}</span>
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}

function LookItem({ item, isAdmin, onOpenBenefit }: { item: NeedsLook; isAdmin: boolean; onOpenBenefit: (key: string) => void }) {
  const clear = useClearCredit();
  const mark = useSetMark();
  const set = useSetCredit();
  const [problem, setProblem] = useState<string | null>(null);
  const done = { onError: (err: unknown) => setProblem(failText(err)) };
  const name = item.benefit_name ?? (item.kind === "lost_choice" && item.benefit_key === null ? "Not a benefit" : "A benefit not shown");
  const when = item.kind === "chat_only" && item.cycle_start ? `${name}, ${formatMonthShort(item.cycle_start)}` : name;
  const facts: ReactNode[] = [];
  if (item.date) facts.push(formatDate(item.date));
  if (item.cents !== null) facts.push(item.kind === "chat_only" ? `The chat says ${money(item.cents)}` : money(item.cents));
  const busy = clear.isPending || mark.isPending || set.isPending;
  return (
    <>
      <p className="m-0 text-sm font-medium text-fg">{when}</p>
      <p className="m-0 text-sm text-fg-secondary">{item.text}</p>
      {facts.length > 0 && <p className="m-0 text-sm text-fg-secondary">{facts.join(" · ")}</p>}
      <div className="mt-2 flex flex-wrap gap-2">
        {item.kind === "lost_choice" && item.txn_key && (
          <ConfirmAction
            ask={
              <>
                Delete<span className="sr-only">: the lost choice for {name}</span>
              </>
            }
            question="Delete this choice? The row it was for is gone."
            confirmLabel="Delete choice"
            busy={busy}
            onConfirm={() => clear.mutate(item.txn_key!, done)}
          />
        )}
        {item.kind === "choice_off" && item.txn_key && (
          <ConfirmAction
            ask={
              <>
                Delete<span className="sr-only">: the choice for {name}</span>
              </>
            }
            question="Delete this choice? The row is matched again, or waits for review."
            confirmLabel="Delete choice"
            busy={busy}
            onConfirm={() => clear.mutate(item.txn_key!, done)}
          />
        )}
        {item.kind === "no_cycle" && item.txn_key && (
          <ConfirmAction
            ask={
              <>
                Not a benefit<span className="sr-only">: {name}</span>
              </>
            }
            question="Count this row for nothing?"
            confirmLabel="Not a benefit"
            busy={busy}
            onConfirm={() => set.mutate({ txnKey: item.txn_key!, body: { decision: "not_benefit", reason: "other" } }, done)}
          />
        )}
        {item.kind === "chat_only" && item.benefit_key && item.cycle_start && (
          <>
            <Button
              size="sm"
              disabled={busy}
              onClick={() => mark.mutate({ key: item.benefit_key!, start: item.cycle_start!, used: "0.00", usedOn: null }, done)}
            >
              Mark not used<span className="sr-only">: {when}</span>
            </Button>
            {isAdmin && (
              <Button size="sm" onClick={() => onOpenBenefit(item.benefit_key!)}>
                Add a match word<span className="sr-only">: {name}</span>
              </Button>
            )}
          </>
        )}
      </div>
      <FormStatus problem={problem} saved={null} />
    </>
  );
}
