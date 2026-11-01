import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { Link } from "react-router";
import { Button } from "../../components/ui/button";
import { CategoryLabel } from "../../components/ui/category-label";
import { cn } from "../../lib/cn";
import { createRule, invalidateAfterEdit, type MatchKind, previewRule, type TxnRow, txnKeys } from "./api";
import { INPUT } from "./controls";

interface RulePromptProps {
  row: TxnRow;
  categoryKey: string;
  categoryName: string;
  categoryEmoji: string | null;
  onDone: () => void;
}

const MIN_CONTAINS = 3;

/** Waits until typing pauses, so the preview is not asked on every key. */
export function useDebounced<T>(value: T, ms = 300): T {
  const [out, setOut] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setOut(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return out;
}

export function previewText(p: { matches: number; would_change: number; edited: number }): string {
  const rows = (n: number) => `${n} transaction${n === 1 ? "" : "s"}`;
  const parts = [`Matches ${rows(p.matches)}.`];
  if (p.matches > 0) parts.push(`${p.would_change} would change.`);
  if (p.edited === 1) parts.push("1 transaction has its own edit and keeps it.");
  if (p.edited > 1) parts.push(`${p.edited} transactions have their own edit and keep it.`);
  return parts.join(" ");
}

/**
 * "Create rule?" after a category change: always use this category for the
 * same merchant, or for statement text that contains some words. Rules apply
 * when rows are read, and a row's own edit always wins.
 */
export function RulePrompt({ row, categoryKey, categoryName, categoryEmoji, onDone }: RulePromptProps) {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<MatchKind>(row.merchant ? "merchant_equals" : "description_contains");
  const [text, setText] = useState(row.description);
  const pattern = kind === "merchant_equals" ? (row.merchant ?? "") : text.replace(/\s+/g, " ").trim();
  const valid = kind === "merchant_equals" ? pattern.length > 0 : pattern.length >= MIN_CONTAINS && pattern.length <= 64;
  const debounced = useDebounced(pattern);
  const headingId = useId();
  const hintId = useId();

  const preview = useQuery({
    queryKey: ["rules", "preview", kind, debounced, categoryKey],
    queryFn: () => previewRule({ match_kind: kind, pattern: debounced, category_key: categoryKey }),
    enabled: valid && debounced === pattern,
    retry: false,
  });

  const create = useMutation({
    mutationFn: () =>
      createRule({ match_kind: kind, pattern, category_key: categoryKey, created_from_txn_key: row.txn_key }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: txnKeys.rules });
      void invalidateAfterEdit(queryClient);
    },
  });

  if (create.isSuccess) {
    return (
      <div role="status" className="animate-rise-in rounded-card border border-line p-4 text-sm">
        <p className="m-0 font-medium text-fg">Rule created.</p>
        <p className="m-0 mt-1 text-fg-secondary">
          It now sets the category on {create.data.applied_count} transaction{create.data.applied_count === 1 ? "" : "s"}.{" "}
          <Link to="/settings/rules">Manage rules</Link>
        </p>
        <Button size="sm" className="mt-3" onClick={onDone}>
          Close
        </Button>
      </div>
    );
  }

  return (
    <section aria-labelledby={headingId} className="animate-rise-in rounded-card border border-line p-4">
      <h3 id={headingId} className="m-0 text-sm font-medium text-fg">
        Create rule?
      </h3>
      <p className="m-0 mt-1 text-sm text-fg-secondary">
        Always use <CategoryLabel name={categoryName} emoji={categoryEmoji} /> for:
      </p>
      <fieldset className="m-0 mt-2 border-0 p-0">
        <legend className="sr-only">Match</legend>
        {row.merchant && (
          <label className="flex min-h-11 cursor-pointer items-center gap-2 text-sm md:min-h-9">
            <input
              type="radio"
              name={`${headingId}-kind`}
              checked={kind === "merchant_equals"}
              onChange={() => setKind("merchant_equals")}
              className="accent-[var(--primary)]"
            />
            Merchant is “{row.merchant}”
          </label>
        )}
        <label className="flex min-h-11 cursor-pointer items-center gap-2 text-sm md:min-h-9">
          <input
            type="radio"
            name={`${headingId}-kind`}
            checked={kind === "description_contains"}
            onChange={() => setKind("description_contains")}
            className="accent-[var(--primary)]"
          />
          Statement text contains
        </label>
        {kind === "description_contains" && (
          <div className="mt-1 pl-6">
            <input
              className={cn(INPUT)}
              value={text}
              maxLength={64}
              onChange={(e) => setText(e.target.value)}
              aria-label="Statement text contains"
              aria-describedby={hintId}
            />
            <p id={hintId} className="m-0 mt-1 text-xs text-fg-secondary">
              Trim it to the part that stays the same, like a store name. At least 3 letters. Case does not matter.
            </p>
          </div>
        )}
      </fieldset>
      <p className="m-0 mt-2 min-h-5 text-sm text-fg-secondary" aria-live="polite">
        {valid && preview.data ? previewText(preview.data) : ""}
        {preview.isError ? preview.error.message : ""}
      </p>
      {create.isError && (
        <p role="alert" className="m-0 mt-2 text-sm text-bad">
          {create.error.message}
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button variant="primary" size="sm" disabled={!valid || create.isPending} onClick={() => create.mutate()}>
          {create.isPending ? "Creating" : "Create rule"}
        </Button>
        <Button size="sm" variant="ghost" onClick={onDone}>
          Not now
        </Button>
      </div>
    </section>
  );
}
