import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft } from "lucide-react";
import { type FormEvent, useId, useState } from "react";
import { Link } from "react-router";
import { PageHeader } from "../../components/shell/PageHeader";
import { Button } from "../../components/ui/button";
import { Card, CardHeader } from "../../components/ui/card";
import { CategoryLabel } from "../../components/ui/category-label";
import { EmptyState } from "../../components/ui/empty-state";
import { getCategories, queryKeys } from "../../lib/api";
import { useMe } from "../../lib/auth";
import { cn } from "../../lib/cn";
import {
  createRule,
  deleteRule,
  getRules,
  invalidateAfterEdit,
  type MatchKind,
  patchRule,
  previewRule,
  type Rule,
  type RulePatch,
  txnKeys,
} from "./api";
import { CategorySelect, INPUT, SwitchRow } from "./controls";
import { previewText, useDebounced } from "./RulePrompt";

export const MATCH_LABELS: Record<MatchKind, string> = {
  merchant_equals: "Merchant is",
  description_contains: "Statement text contains",
};

/** "Merchant is “Corner Market”". */
export function ruleText(rule: Pick<Rule, "match_kind" | "pattern">): string {
  return `${MATCH_LABELS[rule.match_kind]} “${rule.pattern}”`;
}

function patternProblem(kind: MatchKind, pattern: string): string | null {
  const p = pattern.replace(/\s+/g, " ").trim();
  if (!p) return "Type the text to match.";
  if (p.length > 64) return "Keep it to 64 letters or fewer.";
  if (kind === "description_contains" && p.length < 3) return "Use at least 3 letters for statement text.";
  return null;
}

/**
 * Settings, Rules: merchant rules that set a category when rows are read.
 * A row's own category edit always wins over a rule. Rules are never written
 * into the imported rows.
 */
export function RulesPage() {
  const rules = useQuery({ queryKey: txnKeys.rules, queryFn: getRules });
  const categories = useQuery({ queryKey: queryKeys.categories, queryFn: getCategories });
  const me = useMe();
  const isAdmin = me.data?.role === "admin";

  return (
    <>
      <PageHeader title="Rules" />
      <div className="mb-3">
        <Link to="/settings" className="inline-flex min-h-11 items-center gap-1 text-sm md:min-h-0">
          <ChevronLeft size={16} aria-hidden="true" />
          Settings
        </Link>
      </div>
      <div className="flex max-w-4xl flex-col gap-4">
        <NewRule categories={categories.data} />
        <Card>
          <CardHeader
            title="Rules"
            subtitle="Merchant rules come before statement text rules, then the longer match wins. A category you set on a transaction always wins."
          />
          {rules.isPending && <p className="m-0 text-fg-secondary">Loading…</p>}
          {rules.isError && <p className="m-0 text-bad">{rules.error.message}</p>}
          {rules.data && rules.data.rules.length === 0 && (
            <EmptyState title="No rules yet.">Change a category on a transaction and choose Create rule, or add one above.</EmptyState>
          )}
          {rules.data && rules.data.rules.length > 0 && (
            <ul className="m-0 list-none p-0">
              {rules.data.rules.map((r) => (
                <RuleItem key={r.id} rule={r} categories={categories.data} canDelete={isAdmin} />
              ))}
            </ul>
          )}
          {!isAdmin && me.data && <p className="m-0 mt-3 text-xs text-fg-secondary">Only an admin can delete a rule. You can turn one off.</p>}
        </Card>
      </div>
    </>
  );
}

function useRuleChanged() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: txnKeys.rules });
    void invalidateAfterEdit(queryClient);
  };
}

function NewRule({ categories }: { categories: Parameters<typeof CategorySelect>[0]["categories"] }) {
  const changed = useRuleChanged();
  const [kind, setKind] = useState<MatchKind>("merchant_equals");
  const [pattern, setPattern] = useState("");
  const [category, setCategory] = useState("");
  const [tried, setTried] = useState(false);
  const ids = { kind: useId(), pattern: useId(), category: useId(), problem: useId() };
  const problem = patternProblem(kind, pattern);
  const clean = pattern.replace(/\s+/g, " ").trim();
  const debounced = useDebounced(clean);

  const preview = useQuery({
    queryKey: ["rules", "preview", kind, debounced, category],
    queryFn: () => previewRule({ match_kind: kind, pattern: debounced, category_key: category || null }),
    enabled: !problem && debounced === clean,
    retry: false,
  });

  const create = useMutation({
    mutationFn: () => createRule({ match_kind: kind, pattern: clean, category_key: category }),
    onSuccess: () => {
      setPattern("");
      setCategory("");
      setTried(false);
      changed();
    },
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    setTried(true);
    if (!problem && category) create.mutate();
  }

  return (
    <Card>
      <CardHeader title="New rule" />
      <form onSubmit={submit} className="grid gap-3 md:grid-cols-[12rem_1fr_14rem_auto] md:items-end" aria-label="New rule">
        <div>
          <label htmlFor={ids.kind} className="mb-1 block text-sm text-fg">
            Match
          </label>
          <select id={ids.kind} className={INPUT} value={kind} onChange={(e) => setKind(e.target.value as MatchKind)}>
            <option value="merchant_equals">{MATCH_LABELS.merchant_equals}</option>
            <option value="description_contains">{MATCH_LABELS.description_contains}</option>
          </select>
        </div>
        <div>
          <label htmlFor={ids.pattern} className="mb-1 block text-sm text-fg">
            Text
          </label>
          <input
            id={ids.pattern}
            className={INPUT}
            value={pattern}
            maxLength={64}
            onChange={(e) => setPattern(e.target.value)}
            aria-invalid={(tried && problem !== null) || undefined}
            aria-describedby={tried && problem ? ids.problem : undefined}
          />
        </div>
        <div>
          <label htmlFor={ids.category} className="mb-1 block text-sm text-fg">
            Category
          </label>
          <CategorySelect id={ids.category} value={category} onChange={setCategory} categories={categories} placeholder="Pick a category" />
        </div>
        <Button type="submit" variant="primary" disabled={create.isPending}>
          {create.isPending ? "Adding" : "Add rule"}
        </Button>
      </form>
      <div className="mt-2 min-h-5 text-sm" aria-live="polite">
        {tried && problem && (
          <p id={ids.problem} className="m-0 text-bad">
            {problem}
          </p>
        )}
        {tried && !problem && !category && <p className="m-0 text-bad">Pick a category.</p>}
        {!problem && preview.data && <p className="m-0 text-fg-secondary">{previewText(preview.data)}</p>}
        {!problem && preview.isError && <p className="m-0 text-bad">{preview.error.message}</p>}
        {create.isError && (
          <p role="alert" className="m-0 text-bad">
            {create.error.message}
          </p>
        )}
        {create.isSuccess && <p className="m-0 text-fg-secondary">Rule added. It sets the category on {create.data.applied_count} now.</p>}
      </div>
    </Card>
  );
}

function RuleItem({
  rule,
  categories,
  canDelete,
}: {
  rule: Rule;
  categories: Parameters<typeof CategorySelect>[0]["categories"];
  canDelete: boolean;
}) {
  const changed = useRuleChanged();
  const [editing, setEditing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [kind, setKind] = useState<MatchKind>(rule.match_kind);
  const [pattern, setPattern] = useState(rule.pattern);
  const [category, setCategory] = useState(rule.category_key);
  const ids = { kind: useId(), pattern: useId(), category: useId() };
  const problem = editing ? patternProblem(kind, pattern) : null;

  const patch = useMutation({
    mutationFn: (changes: RulePatch) => patchRule(rule.id, changes),
    onSuccess: () => {
      setEditing(false);
      changed();
    },
  });
  const remove = useMutation({ mutationFn: () => deleteRule(rule.id), onSuccess: changed });
  const error = patch.error ?? remove.error;

  function save(e: FormEvent) {
    e.preventDefault();
    if (problem) return;
    const changes: RulePatch = {};
    if (kind !== rule.match_kind) changes.match_kind = kind;
    const clean = pattern.replace(/\s+/g, " ").trim();
    if (clean !== rule.pattern) changes.pattern = clean;
    if (category !== rule.category_key) changes.category_key = category;
    if (Object.keys(changes).length === 0) setEditing(false);
    else patch.mutate(changes);
  }

  return (
    <li className={cn("border-t border-line py-3 first:border-t-0", !rule.active && "text-fg-secondary")}>
      {!editing ? (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <div className="min-w-0 flex-1 basis-64">
            <p className="m-0 break-words text-sm text-fg">{ruleText(rule)}</p>
            <p className="m-0 text-sm text-fg-secondary">
              Sets <CategoryLabel name={rule.category_name ?? rule.category_key} emoji={rule.category_emoji} />
              {" · "}
              {rule.applied_count} transaction{rule.applied_count === 1 ? "" : "s"} now
              {rule.created_by ? ` · Added by ${rule.created_by}` : ""}
            </p>
          </div>
          <div className="w-32">
            <SwitchRow
              label="Active"
              checked={rule.active}
              disabled={patch.isPending}
              onChange={(v) => patch.mutate({ active: v })}
            />
          </div>
          <div className="flex gap-2">
            <Button size="sm" onClick={() => setEditing(true)}>
              Edit{" "}<span className="sr-only">rule {ruleText(rule)}</span>
            </Button>
            {canDelete && !confirming && (
              <Button size="sm" variant="ghost" onClick={() => setConfirming(true)}>
                Delete{" "}<span className="sr-only">rule {ruleText(rule)}</span>
              </Button>
            )}
            {canDelete && confirming && (
              <>
                <Button size="sm" variant="primary" onClick={() => remove.mutate()} disabled={remove.isPending}>
                  Yes, delete
                </Button>
                <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
                  Keep
                </Button>
              </>
            )}
          </div>
        </div>
      ) : (
        <form onSubmit={save} className="grid gap-3 md:grid-cols-[12rem_1fr_14rem] md:items-end" aria-label={`Edit rule ${ruleText(rule)}`}>
          <div>
            <label htmlFor={ids.kind} className="mb-1 block text-sm text-fg">
              Match
            </label>
            <select id={ids.kind} className={INPUT} value={kind} onChange={(e) => setKind(e.target.value as MatchKind)}>
              <option value="merchant_equals">{MATCH_LABELS.merchant_equals}</option>
              <option value="description_contains">{MATCH_LABELS.description_contains}</option>
            </select>
          </div>
          <div>
            <label htmlFor={ids.pattern} className="mb-1 block text-sm text-fg">
              Text
            </label>
            <input id={ids.pattern} className={INPUT} value={pattern} maxLength={64} onChange={(e) => setPattern(e.target.value)} />
          </div>
          <div>
            <label htmlFor={ids.category} className="mb-1 block text-sm text-fg">
              Category
            </label>
            <CategorySelect id={ids.category} value={category} onChange={setCategory} categories={categories} />
          </div>
          <div className="flex gap-2 md:col-span-3">
            <Button type="submit" size="sm" variant="primary" disabled={patch.isPending || problem !== null}>
              Save
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setEditing(false);
                setKind(rule.match_kind);
                setPattern(rule.pattern);
                setCategory(rule.category_key);
                patch.reset();
              }}
            >
              Cancel
            </Button>
            {problem && <p className="m-0 self-center text-sm text-bad">{problem}</p>}
          </div>
        </form>
      )}
      {error && (
        <p role="alert" className="m-0 mt-2 text-sm text-bad">
          {error.message}
        </p>
      )}
    </li>
  );
}
