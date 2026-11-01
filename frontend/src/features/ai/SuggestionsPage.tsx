import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronLeft, Sparkles, X } from "lucide-react";
import { useState, ViewTransition } from "react";
import { Link } from "react-router";
import { Money } from "../../components/money/Money";
import { PageHeader } from "../../components/shell/PageHeader";
import { Button } from "../../components/ui/button";
import { CategoryLabel } from "../../components/ui/category-label";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorBanner } from "../../components/ui/error-banner";
import { Skeleton } from "../../components/ui/skeleton";
import { ApiError } from "../../lib/api";
import { useMe } from "../../lib/auth";
import { cn } from "../../lib/cn";
import { formatDate } from "../../lib/format";
import { LIST_ITEM, listChange } from "../../lib/motion";
import { invalidateAfterEdit } from "../transactions/api";
import {
  aiKeys,
  type CategorySuggestion,
  decideSuggestions,
  getSuggestions,
  NOT_FINISHED_TEXT,
  type RunResult,
  runSuggestions,
  showsStored,
  useAiStatus,
  useOtherRun,
  WORKING_TEXT,
} from "./api";

type Note = { ok: boolean; text: string };

/**
 * Category suggestions (PLAN 7.7, 8.2). Claude suggests a category for
 * uncategorized transactions with an unknown merchant. Nothing changes until
 * someone approves; approving writes an override, like an edit by hand.
 * During a spend-limit or billing pause the list stays, without Suggest categories.
 */
export function SuggestionsPage() {
  const status = useAiStatus();
  const isAdmin = useMe().data?.role === "admin";
  const enabled = showsStored(status.data);

  return (
    <>
      <PageHeader title="Category suggestions" />
      <div className="mb-3">
        <Link to="/transactions" className="inline-flex min-h-11 items-center gap-1 text-sm md:min-h-0">
          <ChevronLeft size={16} aria-hidden="true" />
          Transactions
        </Link>
      </div>
      {status.isPending && <Skeleton className="h-64 max-w-3xl" />}
      {status.isError && (
        <ErrorBanner message="Could not load the AI status." onRetry={() => void status.refetch()} retrying={status.isFetching} />
      )}
      {status.data && !enabled && (
        <div className="max-w-3xl rounded-card bg-card shadow-card">
          {status.data.reason === "no_key" ? (
            <EmptyState icon={<Sparkles size={24} aria-hidden="true" />} title="No API key">
              No API key is set on the server. Suggestions show here once it is.
            </EmptyState>
          ) : (
            <EmptyState icon={<Sparkles size={24} aria-hidden="true" />} title="AI is off">
              Suggestions show here when an admin turns AI on in Settings.
            </EmptyState>
          )}
        </div>
      )}
      {enabled && <Suggestions isAdmin={isAdmin} canRun={status.data?.available === true} />}
    </>
  );
}

function Suggestions({ isAdmin, canRun }: { isAdmin: boolean; canRun: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: aiKeys.suggestions, queryFn: getSuggestions });
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [message, setMessage] = useState<Note | null>(null);
  // Rows decided here leave at once (with motion) instead of waiting for the refresh.
  // Only until the next load: the server's list then decides what stays.
  const [decided, setDecided] = useState<{ at: number; ids: number[] }>({ at: -1, ids: [] });
  const gone = decided.at === q.dataUpdatedAt ? decided.ids : [];
  const rows = (q.data?.suggestions ?? []).filter((r) => !gone.includes(r.id));
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: aiKeys.suggestions });
    void qc.invalidateQueries({ queryKey: aiKeys.usage });
  };
  // One note: the newest outcome of a run or a decision, also an error.
  const fail = (err: unknown) => setMessage({ ok: false, text: err instanceof ApiError ? err.message : "That did not work. Try again." });

  // A run that takes over 5 s goes on in the background (api.ts runSuggestions).
  const run = useMutation({
    mutationFn: () => runSuggestions(qc, () => setMessage({ ok: true, text: WORKING_TEXT })),
    onMutate: () => setMessage(null),
    onSuccess: (r) => setMessage({ ok: true, text: countsText(r.merchants, r.suggested) }),
    onError: fail,
    onSettled: refresh,
  });
  // Also a run started in another tab or before this page opened: say how it ended.
  const elsewhere = useOtherRun(
    "suggestions",
    run.isPending,
    // The working text is a note like any other, so a later decision replaces it.
    () => setMessage({ ok: true, text: WORKING_TEXT }),
    (end) => {
      setMessage(endNote(end));
      refresh();
    },
  );
  const running = run.isPending || elsewhere;

  const decide = useMutation({
    mutationFn: ({ ids, action }: { ids: number[]; action: "approve" | "reject" }) => decideSuggestions(ids, action),
    onSuccess: (r, { ids, action }) => {
      setMessage({
        ok: true,
        text: action === "approve" ? `Applied ${plural(r.changed, "category", "categories")}.` : `Rejected ${plural(r.changed, "suggestion")}.`,
      });
      // Only the decided rows leave the selection, and the list. Keyed to the list in the
      // cache now: a refresh may have landed since the last render.
      const at = qc.getQueryState(aiKeys.suggestions)?.dataUpdatedAt ?? 0;
      listChange(() => {
        setSelected((sel) => new Set([...sel].filter((id) => !ids.includes(id))));
        setDecided((d) => ({ at, ids: d.at === at ? [...d.ids, ...ids] : ids }));
      });
    },
    onError: fail,
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: aiKeys.suggestions });
      void invalidateAfterEdit(qc);
    },
  });

  function toggle(id: number) {
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // Work only with rows on screen: a row someone else decided drops out after the refresh.
  const chosen = rows.filter((r) => selected.has(r.id)).map((r) => r.id);
  const allSelected = rows.length > 0 && chosen.length === rows.length;
  const shown = message;

  return (
    <section className="max-w-3xl rounded-card bg-card shadow-card" aria-labelledby="sug-title">
      <header className="flex flex-wrap items-center gap-2 px-4 pb-3 pt-4 md:px-6 md:pt-5">
        <div className="mr-auto min-w-0">
          <h2 id="sug-title" className="m-0 text-lg font-medium text-fg">
            Waiting for a decision{rows.length > 0 && ` (${rows.length})`}
          </h2>
          <p className="m-0 text-sm text-fg-secondary">
            Nothing changes until you approve. Approving sets the category on that one transaction, like an edit.
          </p>
        </div>
        {isAdmin && canRun && (
          <Button size="sm" disabled={running} onClick={() => run.mutate()}>
            <Sparkles size={16} aria-hidden="true" />
            {running ? "Asking Claude…" : "Suggest categories"}
          </Button>
        )}
      </header>

      {shown && (
        <p role={shown.ok ? "status" : "alert"} className={cn("m-0 px-4 pb-3 text-sm md:px-6", shown.ok ? "text-fg-secondary" : "text-bad")}>
          {shown.text}
        </p>
      )}

      {q.isPending && <Skeleton className="mx-4 mb-4 h-40 md:mx-6" />}
      {q.isError && (
        <div className="px-4 pb-4 md:px-6">
          <ErrorBanner message="Could not load suggestions." onRetry={() => void q.refetch()} retrying={q.isFetching} />
        </div>
      )}
      {q.data && rows.length === 0 && (
        <EmptyState title="No suggestions waiting.">
          {isAdmin && canRun ? "Suggest categories sends uncategorized merchants to Claude." : null}
        </EmptyState>
      )}

      {rows.length > 0 && (
        <>
          <div className="flex flex-wrap items-center gap-2 border-y border-line bg-subtle px-4 py-2 md:px-6">
            <label className="mr-auto flex min-h-11 cursor-pointer items-center gap-2 text-sm text-fg md:min-h-8">
              <input
                type="checkbox"
                className="h-4 w-4 accent-[var(--primary)]"
                checked={allSelected}
                onChange={() => setSelected(allSelected ? new Set() : new Set(rows.map((r) => r.id)))}
              />
              Select all
              {chosen.length > 0 && <span className="text-fg-secondary">({chosen.length} selected)</span>}
            </label>
            <Button
              size="sm"
              disabled={chosen.length === 0 || decide.isPending}
              onClick={() => decide.mutate({ ids: chosen, action: "approve" })}
            >
              <Check size={16} aria-hidden="true" />
              {chosen.length > 0 ? `Approve ${chosen.length}` : "Approve"}
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={chosen.length === 0 || decide.isPending}
              onClick={() => decide.mutate({ ids: chosen, action: "reject" })}
            >
              <X size={16} aria-hidden="true" />
              {chosen.length > 0 ? `Reject ${chosen.length}` : "Reject"}
            </Button>
          </div>
          <ul className="m-0 list-none p-0">
            {rows.map((r) => (
              <ViewTransition key={r.id} {...LIST_ITEM}>
                <SuggestionRow
                  row={r}
                  checked={selected.has(r.id)}
                  onToggle={() => toggle(r.id)}
                  busy={decide.isPending}
                  onDecide={(action) => decide.mutate({ ids: [r.id], action })}
                />
              </ViewTransition>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function SuggestionRow({
  row,
  checked,
  onToggle,
  busy,
  onDecide,
}: {
  row: CategorySuggestion;
  checked: boolean;
  onToggle: () => void;
  busy: boolean;
  onDecide: (action: "approve" | "reject") => void;
}) {
  const name = row.merchant ?? row.description;
  return (
    <li className="flex items-start gap-1 border-b border-line py-2 pl-1 pr-4 last:border-b-0 md:gap-3 md:py-3 md:pl-6 md:pr-6">
      {/* 44px to tap on phones; the box itself stays small. */}
      <label className="flex h-11 w-11 shrink-0 cursor-pointer items-center justify-center md:h-5 md:w-4">
        <input
          type="checkbox"
          className="h-4 w-4 accent-[var(--primary)]"
          checked={checked}
          onChange={onToggle}
          aria-label={`Select ${name}, ${formatDate(row.txn_date)}`}
        />
      </label>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-3">
          <p className="m-0 min-w-0 truncate text-sm font-medium text-fg">{name}</p>
          <Money cents={row.amount_cents} withCents flow className="shrink-0 text-sm" />
        </div>
        <p className="m-0 text-sm text-fg-secondary">
          {formatDate(row.txn_date)} · {row.account_name}
        </p>
        <p className="m-0 mt-1.5 flex flex-wrap items-baseline gap-x-2 text-sm text-fg">
          <span className="sr-only">Suggested category: </span>
          <CategoryLabel name={row.category_name} emoji={row.category_emoji} />
          <span className="text-fg-secondary">
            {row.confidence}% sure{row.reason ? ` · ${row.reason}` : ""}
          </span>
        </p>
      </div>
      <div className="flex shrink-0 gap-1">
        <Button size="icon" variant="ghost" disabled={busy} aria-label={`Approve ${row.category_name} for ${name}`} title="Approve" onClick={() => onDecide("approve")}>
          <Check size={18} aria-hidden="true" className="text-good" />
        </Button>
        <Button size="icon" variant="ghost" disabled={busy} aria-label={`Reject ${row.category_name} for ${name}`} title="Reject" onClick={() => onDecide("reject")}>
          <X size={18} aria-hidden="true" />
        </Button>
      </div>
    </li>
  );
}

function countsText(merchants: number, suggested: number): string {
  return merchants === 0
    ? "Nothing new to suggest. Every uncategorized row has a suggestion or a rule."
    : `Checked ${plural(merchants, "merchant")}. ${plural(suggested, "new suggestion")}.`;
}

/** How a run seen only through the status ended, in the words of a run started here. */
function endNote(end: RunResult | null): Note {
  if (end?.status !== "done") return { ok: false, text: end?.message ?? NOT_FINISHED_TEXT };
  return { ok: true, text: countsText(end.merchants ?? 0, end.suggested ?? 0) };
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;
}
