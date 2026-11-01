import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, CircleAlert, ExternalLink } from "lucide-react";
import { type FormEvent, type ReactNode, useId, useState } from "react";
import { Link } from "react-router";
import { PageHeader } from "../../components/shell/PageHeader";
import { Badge, type BadgeTone } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardHeader } from "../../components/ui/card";
import { ErrorBanner } from "../../components/ui/error-banner";
import { SegmentedControl } from "../../components/ui/segmented-control";
import { Skeleton } from "../../components/ui/skeleton";
import { TableScroll } from "../../components/ui/table-scroll";
import { ApiError } from "../../lib/api";
import { useMe } from "../../lib/auth";
import { cn } from "../../lib/cn";
import { formatDate, formatMoney, formatMonth, formatTimestamp } from "../../lib/format";
import { INPUT_CLASS } from "../accounts/vehicles/fields";
import { centsToText, parseDollarsCents, parseWhole } from "../settings/vehicles/parse";
import {
  type AiSettings,
  type AiSettingsOut,
  type AiStatus,
  type AiUsage,
  type ReviewFailed,
  type RunResult,
  type RunReviewStartOut,
  type UsageCall,
  aiKeys,
  clearAiBlock,
  formatMicroUsd,
  getAiSettings,
  getAiUsage,
  invalidateAi,
  modelName,
  putAiSettings,
  runReview,
  useAiStatus,
  useOtherRun,
  WORKING_TEXT,
} from "./api";

/**
 * Settings > AI (PLAN 7.2, 7.5, 7.6, 7.7). Admin only: the off switch,
 * models, caps, the spend-limit pause, this month's cost, the last 20 calls
 * and the price table.
 */
export function AiSettingsPage() {
  const isAdmin = useMe().data?.role === "admin";
  return (
    <>
      <PageHeader title="AI" />
      <div className="mb-3">
        <Link to="/settings" className="inline-flex min-h-11 items-center gap-1 text-sm md:min-h-0">
          <ChevronLeft size={16} aria-hidden="true" />
          Settings
        </Link>
      </div>
      {isAdmin ? <AdminAi /> : <p className="text-fg-secondary">Only an admin can see AI settings.</p>}
    </>
  );
}

function AdminAi() {
  const q = useQuery({ queryKey: aiKeys.settings, queryFn: getAiSettings });
  const usage = useQuery({ queryKey: aiKeys.usage, queryFn: getAiUsage });
  return (
    // A little wider than other settings pages, so the calls table fits on a desktop.
    <div className="flex max-w-4xl flex-col gap-4">
      {q.isPending && <Skeleton className="h-48" />}
      {q.isError && <ErrorBanner message="Could not load AI settings." onRetry={() => void q.refetch()} retrying={q.isFetching} />}
      {q.data && (
        <>
          <StatusCard data={q.data} />
          <SettingsForm data={q.data} />
        </>
      )}
      {usage.isError && <ErrorBanner message="Could not load AI usage." onRetry={() => void usage.refetch()} retrying={usage.isFetching} />}
      {usage.data && (
        <>
          <MonthCard usage={usage.data} />
          <CallsCard usage={usage.data} />
        </>
      )}
      {q.data && <PricesCard data={q.data} />}
    </div>
  );
}

// ---------- Status and the off switch ----------

function statusBadge(st: AiStatus): { tone: BadgeTone; label: string } {
  if (st.available) return { tone: "good", label: "On" };
  if (st.reason === "spend_limit") return { tone: "bad", label: "Paused" };
  if (st.reason === "no_key") return { tone: "warn", label: "No key" };
  return { tone: "neutral", label: "Off" };
}

const REVIEW_RESULT: Record<string, { ok: boolean; text: string }> = {
  ready: { ok: true, text: "The review is on the Dashboard." },
  running: { ok: true, text: "A review is being written now. It shows on the Dashboard when it is done." },
  no_run: { ok: false, text: "There is no monthly update to review yet." },
  no_complete_month: { ok: false, text: "The newest update has no complete month to review yet." },
  skipped: { ok: false, text: "Skipped: a cost cap was reached or AI is paused." },
  unavailable: { ok: false, text: "Skipped: AI is off or paused." },
};

/** How a review run ended, in words. A run that failed in the background says why. */
function reviewNote(r: RunReviewStartOut | RunResult | null): { ok: boolean; text: string } {
  if (r?.status === "skipped" && r.message) return { ok: false, text: `Skipped. ${r.message}` };
  return (r && REVIEW_RESULT[r.status]) ?? { ok: false, text: r?.message ?? "The review did not finish. Try again later." };
}

function StatusCard({ data }: { data: AiSettingsOut }) {
  const qc = useQueryClient();
  const st = data.status;
  const badge = statusBadge(st);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const fail = (err: unknown) => setNote({ ok: false, text: err instanceof ApiError ? err.message : "That did not work. Try again." });
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => putAiSettings({ ...data.settings, enabled }),
    onSuccess: () => setNote(null),
    onError: fail,
    onSettled: () => void invalidateAi(qc),
  });
  const clear = useMutation({
    mutationFn: clearAiBlock,
    onSuccess: () => setNote({ ok: true, text: "The pause is cleared." }),
    onError: fail,
    onSettled: () => void invalidateAi(qc),
  });
  // A review that takes over 5 s goes on in the background (api.ts runReview).
  const review = useMutation({
    mutationFn: () => runReview(qc, () => setNote({ ok: true, text: WORKING_TEXT })),
    onSuccess: (r) => setNote(reviewNote(r)),
    onError: fail,
    onSettled: () => void invalidateAi(qc),
  });
  // Also a review started in another tab or before this page opened: say how it ended.
  const elsewhere = useOtherRun(
    "review",
    review.isPending,
    () => setNote(null),
    (end) => {
      setNote(reviewNote(end));
      void invalidateAi(qc);
    },
  );
  const writing = review.isPending || elsewhere;
  // The job gave up on the latest complete month (backend review.failed_review).
  const failed = useAiStatus().data?.review_failed;
  const shown = elsewhere ? { ok: true, text: WORKING_TEXT } : note;

  return (
    <Card>
      <CardHeader
        title={
          <span className="inline-flex items-center gap-2">
            Claude <Badge tone={badge.tone}>{badge.label}</Badge>
          </span>
        }
        subtitle="Ask, the monthly review, category suggestions and the trip planner. When AI is off they are hidden and no calls are made."
        actions={
          <SegmentedControl
            label="AI"
            size="md"
            value={data.settings.enabled ? "on" : "off"}
            options={[
              { value: "off", label: "Off" },
              { value: "on", label: "On" },
            ]}
            onChange={(v) => toggle.mutate(v === "on")}
          />
        }
      />
      <div className="flex flex-col gap-3 text-sm">
        {!st.key_present && (
          <p className="m-0 flex items-start gap-2 text-warn">
            <CircleAlert size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
            No API key is set on the server. AI stays off until ANTHROPIC_API_KEY is in the env file.
          </p>
        )}
        {st.blocked_until && (
          <div className="rounded-lg bg-tint-bad px-3 py-2 text-fg">
            {/* The server's text says why: a spend limit or a billing problem. */}
            <p className="m-0">{st.blocked_reason}</p>
            <p className="m-0 mt-1 text-fg-secondary">
              AI is paused until {formatTimestamp(st.blocked_until)}. Clear the pause to start again before then.
            </p>
            <Button size="sm" className="mt-2" disabled={clear.isPending} onClick={() => clear.mutate()}>
              Clear the pause
            </Button>
          </div>
        )}
        {st.available && (
          <div className="flex flex-wrap items-center gap-3">
            <Button size="sm" disabled={writing} onClick={() => review.mutate()}>
              {writing ? "Writing the review…" : "Write the review now"}
            </Button>
            <span className="text-fg-secondary">The review also runs on its own after each monthly update.</span>
          </div>
        )}
        {/* This page's own failed run already says so in its note. */}
        {st.available && failed && !writing && !(shown && !shown.ok) && (
          <p className="m-0 flex items-start gap-2 text-warn">
            <CircleAlert size={16} aria-hidden="true" className="mt-0.5 shrink-0" />
            {failedText(failed)}
          </p>
        )}
        {shown && (
          <p role={shown.ok ? "status" : "alert"} className={cn("m-0", shown.ok ? "text-fg-secondary" : "text-bad")}>
            {shown.text}
          </p>
        )}
      </div>
    </Card>
  );
}

/** Settings' note when the job gave up on the latest month's review (backend review.failed_review). */
function failedText(failed: ReviewFailed): string {
  const month = formatMonth(failed.month);
  if (failed.rewrite) {
    // A correction changed the month, and writing it again failed. The old review still shows.
    const what = failed.tries
      ? `Updating the review for ${month} after a correction failed after ${failed.tries} tries.`
      : `The review for ${month} could not be updated after a correction.`;
    return `${what} The Dashboard shows the earlier version. Write it now to try again.`;
  }
  return failed.tries
    ? `The review for ${month} failed after ${failed.tries} tries. Write it now to try again.`
    : `The review for ${month} could not be written. Write it now to try again.`;
}

// ---------- Models and caps ----------

type Feature = "ask" | "review" | "categorize" | "planner";
const FEATURES: { key: Feature; field: "ask_model" | "review_model" | "categorize_model" | "planner_model"; label: string }[] = [
  { key: "ask", field: "ask_model", label: "Ask" },
  { key: "review", field: "review_model", label: "Monthly review" },
  { key: "categorize", field: "categorize_model", label: "Category suggestions" },
  { key: "planner", field: "planner_model", label: "Trip planner" },
];

const MONEY_FIELDS = [
  { field: "monthly_cap_cents", label: "Monthly cap", help: "By UTC month, like the Console bill." },
  { field: "ask_reserve_cents", label: "Kept for the review", help: "Ask stops this far below the monthly cap." },
  { field: "daily_cap_cents", label: "Daily cap", help: "Household-wide. Resets at midnight Central time." },
] as const;

const COUNT_FIELDS = [
  { field: "daily_questions", label: "Questions a day", help: "Household-wide.", max: 1000 },
  { field: "thread_retention_days", label: "Keep conversations", help: "Days, then they are deleted.", max: 3650 },
] as const;

function SettingsForm({ data }: { data: AiSettingsOut }) {
  const qc = useQueryClient();
  const id = useId();
  const s = data.settings;
  const [models, setModels] = useState({
    ask_model: s.ask_model,
    review_model: s.review_model,
    categorize_model: s.categorize_model,
    planner_model: s.planner_model,
  });
  const [money, setMoney] = useState<Record<string, string>>(
    Object.fromEntries(MONEY_FIELDS.map((f) => [f.field, centsToText(s[f.field] ?? 0)])),
  );
  const [counts, setCounts] = useState<Record<string, string>>(
    Object.fromEntries(COUNT_FIELDS.map((f) => [f.field, String(s[f.field] ?? 0)])),
  );
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const save = useMutation({
    mutationFn: (body: AiSettings) => putAiSettings(body),
    onSuccess: () => setNote({ ok: true, text: "Saved." }),
    onError: (err) => setNote({ ok: false, text: err instanceof ApiError ? err.message : "Could not save. Try again." }),
    onSettled: () => void invalidateAi(qc),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const body: AiSettings = { ...s, ...models };
    for (const f of MONEY_FIELDS) {
      const cents = parseDollarsCents(money[f.field] ?? "");
      if (cents === null || cents === undefined || cents > 100_000) {
        setNote({ ok: false, text: `${f.label}: enter dollars, up to $1,000.` });
        return;
      }
      body[f.field] = cents;
    }
    for (const f of COUNT_FIELDS) {
      const n = parseWhole(counts[f.field] ?? "");
      if (n === null || n === undefined || n > f.max || (f.field === "thread_retention_days" && n < 1)) {
        setNote({ ok: false, text: `${f.label}: enter a whole number up to ${f.max.toLocaleString("en-US")}.` });
        return;
      }
      body[f.field] = n;
    }
    save.mutate(body);
  }

  return (
    <Card>
      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-6">
        <section>
          <CardHeader title="Models" subtitle="A change applies to new conversations. Effort is fixed per feature." className="mb-3" />
          <div className="flex flex-col gap-3">
            {FEATURES.map((f) => (
              <div key={f.key} className="flex flex-wrap items-center gap-x-4 gap-y-1">
                <label htmlFor={`${id}-${f.key}`} className="min-w-0 flex-1 basis-48 text-sm text-fg">
                  {f.label}
                  <span className="block text-xs text-fg-secondary">
                    {f.key === "planner"
                      ? `Effort: ${data.effort.plan_research ?? "default"} for research, ${data.effort.plan_write ?? "default"} for the plan`
                      : `Effort: ${data.effort[f.key] ?? "default"}`}
                  </span>
                </label>
                <select
                  id={`${id}-${f.key}`}
                  className={cn(INPUT_CLASS, "w-48 md:w-48")}
                  value={models[f.field]}
                  onChange={(e) => setModels((m) => ({ ...m, [f.field]: e.target.value as AiSettings["ask_model"] }))}
                >
                  {data.models.map((m) => (
                    <option key={m} value={m}>
                      {modelName(m)}
                    </option>
                  ))}
                </select>
              </div>
            ))}
          </div>
        </section>
        <section>
          <CardHeader
            title="Caps"
            subtitle="Checked before every call. The Console workspace limit is the backstop."
            className="mb-3"
          />
          <div className="flex flex-col gap-3">
            {MONEY_FIELDS.map((f) => (
              <Field key={f.field} id={`${id}-${f.field}`} label={f.label} help={f.help}>
                <span className="relative w-28">
                  <span aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-secondary">
                    $
                  </span>
                  <input
                    id={`${id}-${f.field}`}
                    className={`${INPUT_CLASS} pl-6 text-right tabular-nums`}
                    inputMode="decimal"
                    autoComplete="off"
                    value={money[f.field]}
                    onChange={(e) => setMoney((m) => ({ ...m, [f.field]: e.target.value }))}
                  />
                </span>
              </Field>
            ))}
            {COUNT_FIELDS.map((f) => (
              <Field key={f.field} id={`${id}-${f.field}`} label={f.label} help={f.help}>
                <span className="w-28">
                  <input
                    id={`${id}-${f.field}`}
                    className={`${INPUT_CLASS} text-right tabular-nums`}
                    inputMode="numeric"
                    autoComplete="off"
                    value={counts[f.field]}
                    onChange={(e) => setCounts((c) => ({ ...c, [f.field]: e.target.value }))}
                  />
                </span>
              </Field>
            ))}
          </div>
        </section>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" variant="primary" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save"}
          </Button>
          {note && (
            <p role={note.ok ? "status" : "alert"} className={cn("m-0 animate-fade-in text-sm", note.ok ? "text-fg-secondary" : "text-bad")}>
              {note.text}
            </p>
          )}
        </div>
      </form>
    </Card>
  );
}

function Field({ id, label, help, children }: { id: string; label: string; help: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
      <label htmlFor={id} className="min-w-0 flex-1 basis-48 text-sm text-fg">
        {label}
        <span className="block text-xs text-fg-secondary">{help}</span>
      </label>
      {children}
    </div>
  );
}

// ---------- Cost ----------

const FEATURE_NAMES: Record<string, string> = {
  ask: "Ask",
  review: "Review",
  categorize: "Suggestions",
  cache_check: "Cache check",
  plan_research: "Trip research",
  plan_write: "Trip plan",
};

function MonthCard({ usage: u }: { usage: AiUsage }) {
  const byFeature = Object.entries(u.month_by_feature_micro_usd).filter(([, v]) => v > 0);
  return (
    <Card>
      <CardHeader
        title="This month"
        subtitle={`${formatMonth(u.month)}, by UTC dates like the Console bill. Costs are worked out from each call's token counts.`}
      />
      <dl className="m-0 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Stat label="Cost so far">
          {formatMicroUsd(u.month_cost_micro_usd)}{" "}
          <span className="text-sm font-normal text-fg-secondary">
            of {formatMoney(u.monthly_cap_cents, { cents: u.monthly_cap_cents % 100 !== 0 })}
            {u.month_used_pct !== null && ` (${u.month_used_pct}%)`}
          </span>
          {u.warning_pct !== null && (
            <Badge tone={u.warning_pct >= 80 ? "bad" : "warn"} className="ml-2">
              Past {u.warning_pct}%
            </Badge>
          )}
        </Stat>
        <Stat label="Today">
          {formatMicroUsd(u.today_cost_micro_usd)}{" "}
          <span className="text-sm font-normal text-fg-secondary">
            of {formatMoney(u.daily_cap_cents, { cents: u.daily_cap_cents % 100 !== 0 })}
          </span>
        </Stat>
        <Stat label="Questions today">
          {u.today_questions} <span className="text-sm font-normal text-fg-secondary">of {u.daily_questions}</span>
        </Stat>
        <Stat label="Cache hit rate">
          {u.cache_hit_pct === null ? <span className="text-fg-secondary">No calls yet</span> : `${u.cache_hit_pct}%`}
        </Stat>
      </dl>
      <p className="m-0 mt-3 text-sm text-fg-secondary">
        {u.month_calls === 1 ? "1 call" : `${u.month_calls.toLocaleString("en-US")} calls`} this month
        {byFeature.length > 0 &&
          `: ${byFeature.map(([k, v]) => `${FEATURE_NAMES[k] ?? k} ${formatMicroUsd(v)}`).join(", ")}`}
        .
      </p>
    </Card>
  );
}

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="rounded-lg bg-subtle px-3 py-2">
      <dt className="text-sm text-fg-secondary">{label}</dt>
      <dd className="m-0 text-lg font-medium tabular-nums text-fg">{children}</dd>
    </div>
  );
}

const STOP_NAMES: Record<string, string> = {
  end_turn: "Done",
  stop_sequence: "Done",
  tool_use: "Used a tool",
  max_tokens: "Cut off",
  refusal: "Refused",
  pause_turn: "Paused",
};

function callResult(c: UsageCall): ReactNode {
  if (c.error) return <>Failed ({wordBreaks(c.error)})</>;
  return (c.stop_reason && STOP_NAMES[c.stop_reason]) ?? c.stop_reason ?? "";
}

/** "APIConnectionError" with a break chance between its words, so the calls table fits the card. */
function wordBreaks(name: string): ReactNode[] {
  const words = name.match(/[A-Z]+(?![a-z])|[A-Z]?[a-z\d]+|./g) ?? [];
  return words.flatMap((word, i) => (i > 0 ? [<wbr key={i} />, word] : [word]));
}

const n = (v: number) => v.toLocaleString("en-US");

function CallsCard({ usage: u }: { usage: AiUsage }) {
  return (
    <Card className="px-0 md:px-0">
      <CardHeader title="Last 20 calls" subtitle="Metadata only. Questions and answers are never shown here." className="px-5 md:px-6" />
      {u.calls.length === 0 ? (
        <p className="m-0 px-5 text-sm text-fg-secondary md:px-6">No calls yet.</p>
      ) : (
        <TableScroll label="Last 20 calls">
          <table className="data-table compact">
            <caption className="sr-only">The last 20 AI calls, newest first</caption>
            <thead>
              <tr>
                <th scope="col">When</th>
                <th scope="col">Feature</th>
                <th scope="col" className="hidden md:table-cell">Model</th>
                <th scope="col" className="num hidden md:table-cell">Input</th>
                <th scope="col" className="num hidden md:table-cell">Cache write</th>
                <th scope="col" className="num hidden md:table-cell">Cache read</th>
                <th scope="col" className="num hidden md:table-cell">Output</th>
                <th scope="col" className="num">Cost</th>
                <th scope="col">Result</th>
              </tr>
            </thead>
            <tbody>
              {u.calls.map((c, i) => (
                <tr key={`${c.at}-${i}`}>
                  <td className="whitespace-nowrap">{formatTimestamp(c.at)}</td>
                  <td>
                    {FEATURE_NAMES[c.feature] ?? c.feature}
                    {c.web_searches > 0 && (
                      <span className="block text-xs text-fg-secondary">{c.web_searches === 1 ? "1 search" : `${c.web_searches} searches`}</span>
                    )}
                  </td>
                  <td className="hidden whitespace-nowrap md:table-cell">{modelName(c.model)}</td>
                  <td className="num hidden md:table-cell">{n(c.input_tokens)}</td>
                  <td className="num hidden md:table-cell">{n(c.cache_creation_input_tokens)}</td>
                  <td className="num hidden md:table-cell">{n(c.cache_read_input_tokens)}</td>
                  <td className="num hidden md:table-cell">{n(c.output_tokens)}</td>
                  <td className="num">{formatMicroUsd(c.cost_micro_usd)}</td>
                  <td className={cn(c.error && "text-bad")}>
                    {callResult(c)}
                    {c.latency_ms !== null && !c.error && (
                      <span className="text-fg-secondary"> · {(c.latency_ms / 1000).toFixed(1)}s</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
    </Card>
  );
}

// ---------- Prices ----------

function PricesCard({ data }: { data: AiSettingsOut }) {
  return (
    <Card className="px-0 md:px-0">
      <CardHeader
        title="Prices"
        subtitle={
          <>
            US dollars per million tokens. The trip planner's web search adds $10 per 1,000 searches. Checked {formatDate(data.prices_checked_on)} on{" "}
            <a href={data.prices_source} target="_blank" rel="noreferrer" className="tap-target inline-flex items-center gap-0.5">
              Anthropic's pricing page
              <ExternalLink size={12} aria-hidden="true" />
              <span className="sr-only">(opens in a new tab)</span>
            </a>
            .
          </>
        }
        className="px-5 md:px-6"
      />
      <TableScroll label="Prices per million tokens">
        <table className="data-table compact">
          <caption className="sr-only">Prices per million tokens by model</caption>
          <thead>
            <tr>
              <th scope="col">Model</th>
              <th scope="col" className="num">Input</th>
              <th scope="col" className="num hidden md:table-cell">5 min write</th>
              <th scope="col" className="num hidden md:table-cell">1 hour write</th>
              <th scope="col" className="num">Cache read</th>
              <th scope="col" className="num">Output</th>
            </tr>
          </thead>
          <tbody>
            {data.prices.flatMap((p) => {
              const rows = [{ key: p.model, label: modelName(p.model), price: p }];
              if (p.long_prompt) {
                rows.push({
                  key: `${p.model}-long`,
                  label: `${modelName(p.model)}, prompts over ${(p.long_prompt.over_tokens / 1000).toLocaleString("en-US")}K`,
                  price: { ...p, ...p.long_prompt },
                });
              }
              return rows.map((r) => (
                <tr key={r.key}>
                  <th scope="row" className="font-normal">
                    {r.label}
                  </th>
                  <td className="num">${r.price.input}</td>
                  <td className="num hidden md:table-cell">${r.price.write_5m}</td>
                  <td className="num hidden md:table-cell">${r.price.write_1h}</td>
                  <td className="num">${r.price.cache_read}</td>
                  <td className="num">${r.price.output}</td>
                </tr>
              ));
            })}
          </tbody>
        </table>
      </TableScroll>
    </Card>
  );
}
