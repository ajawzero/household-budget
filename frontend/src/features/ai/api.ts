// AI calls, types and query keys (PLAN 7, 8.2). Every AI feature is hidden
// unless /api/ai/status says it may run. The stored review and the
// suggestions list also show during a spend-limit or billing pause.

import { type QueryClient, useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { ApiError, postStream, request } from "../../lib/api/client";
import type { components } from "../../lib/api/schema";

type S = components["schemas"];

export type AiStatus = S["AiStatus"];
export type AiStatusOut = S["AiStatusOut"];
export type AiSettings = S["AiSettings"];
export type AiSettingsOut = S["AiSettingsOut"];
export type AiUsage = S["AiUsage"];
export type UsageCall = S["UsageCall"];
export type ModelPrice = S["ModelPrice"];
export type ThreadSummary = S["ThreadSummary"];
export type ThreadOut = S["ThreadOut"];
export type ThreadTurn = S["ThreadTurn"];
export type AiReview = S["AiReview"];
export type CategorySuggestion = S["CategorySuggestion"];
export type RunReviewStartOut = S["RunReviewStartOut"];
export type RunResult = S["RunResult"];
export type ReviewFailed = S["ReviewFailed"];

export const aiKeys = {
  all: ["ai"] as const,
  status: ["ai", "status"] as const,
  settings: ["ai", "settings"] as const,
  usage: ["ai", "usage"] as const,
  threads: ["ai", "threads"] as const,
  thread: (id: string) => ["ai", "thread", id] as const,
  suggestions: ["ai", "suggestions"] as const,
};

// ---------- Status and settings ----------

export function getAiStatus(): Promise<AiStatusOut> {
  return request("GET", "/api/ai/status");
}

/** How often the status is checked while a review or suggestions run goes on. */
export const RUN_POLL_MS = 5_000;

const running = (st: AiStatusOut | undefined) => st?.running_review === true || st?.running_suggestions === true;

/** Whether AI may run. Hidden features stay hidden while this loads or fails. */
export function useAiStatus() {
  return useQuery({
    queryKey: aiKeys.status,
    queryFn: getAiStatus,
    staleTime: 60_000,
    // While a run goes on, a page opened after it started still sees it end.
    refetchInterval: (q) => (running(q.state.data) ? RUN_POLL_MS : false),
  });
}

/**
 * A run this page did not start (in another tab, or before the page opened) is
 * seen only through the status. Returns true while one goes on. Calls
 * `onStart` when one is seen, and `onEnd` with how it ended when it ends.
 */
export function useOtherRun(
  kind: "review" | "suggestions",
  ownRun: boolean,
  onStart: () => void,
  onEnd: (result: RunResult | null) => void,
): boolean {
  const st = useAiStatus().data;
  const runningNow = (kind === "review" ? st?.running_review : st?.running_suggestions) === true;
  const last = (kind === "review" ? st?.last_review_result : st?.last_suggestions_result) ?? null;
  const other = runningNow && !ownRun;
  const seen = useRef(false);
  useEffect(() => {
    if (other && !seen.current) {
      seen.current = true;
      onStart();
    } else if (!runningNow && seen.current) {
      seen.current = false;
      onEnd(last);
    }
  }, [other, runningNow, last, onStart, onEnd]);
  return other;
}

/**
 * Stored AI results (the review, the suggestions list) show while the setting
 * is on and the key is set, also during a spend-limit or billing pause. No
 * calls are made then: Ask and the run buttons need `available`.
 */
export function showsStored(st: AiStatus | undefined): boolean {
  return st?.enabled === true && st.key_present;
}

export function getAiSettings(): Promise<AiSettingsOut> {
  return request("GET", "/api/settings/ai");
}

export function putAiSettings(body: AiSettings): Promise<AiSettingsOut> {
  return request("PUT", "/api/settings/ai", { body });
}

export function clearAiBlock(): Promise<AiSettingsOut> {
  return request("POST", "/api/settings/ai/clear-block");
}

export function getAiUsage(): Promise<AiUsage> {
  return request("GET", "/api/ai/usage");
}

/** After a setting changes: status, settings, usage, the dashboard (review), suggestions and trip plans. */
export function invalidateAi(qc: QueryClient): Promise<void> {
  return qc.invalidateQueries({
    predicate: (q) => q.queryKey[0] === "ai" || q.queryKey[0] === "dashboard" || q.queryKey[0] === "trip-plans",
  });
}

// ---------- Ask ----------

export function getThreads(): Promise<{ threads: ThreadSummary[] }> {
  return request("GET", "/api/ask/threads");
}

export function getThread(threadId: string): Promise<ThreadOut> {
  return request("GET", `/api/ask/threads/${encodeURIComponent(threadId)}`);
}

/** The server-sent events of one Ask turn (backend app/ai/ask.py). */
export type AskEvent =
  | { type: "thread"; thread_id: string; new: boolean; notice: string | null }
  | { type: "status"; tool: string; label: string }
  | { type: "text"; text: string }
  /** The output filter changed text already shown: this is the whole answer so far. */
  | { type: "replace"; text: string }
  | {
      type: "done";
      thread_id: string;
      /** The whole answer, filtered at once. It replaces the streamed text. */
      text: string;
      notice: string | null;
      thread_closed: boolean;
      /** Admin only. */
      cost_micro_usd: number | null;
    }
  | { type: "error"; message: string; kind: string };

/** Longest question the server takes. */
export const MAX_QUESTION = 2000;

/**
 * Asks one question and calls `onEvent` for each event as it arrives.
 * Throws ApiError when the question is refused before the stream starts
 * (422 checks, 409 off, 429 cap).
 */
export async function askStream(
  question: string,
  threadId: string | null,
  onEvent: (event: AskEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const body = threadId ? { question, thread_id: threadId } : { question };
  const res = await postStream("/api/ask", body, signal);
  if (!res.body) return;
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    let cut = buffer.indexOf("\n\n");
    while (cut !== -1) {
      const chunk = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      for (const line of chunk.split("\n")) {
        if (!line.startsWith("data: ")) continue;
        try {
          onEvent(JSON.parse(line.slice(6)) as AskEvent);
        } catch {
          // A broken event is skipped. The done event carries the whole answer.
        }
      }
      cut = buffer.indexOf("\n\n");
    }
    if (done) return;
  }
}

// ---------- Runs: the review and suggestions ----------

export const WORKING_TEXT = "Working on it. This can take a minute or two.";
export const NOT_FINISHED_TEXT = "The run did not finish. Try again later.";

/**
 * Waits for a run that goes on in the background: checks /api/ai/status every
 * RUN_POLL_MS until it is no longer running, and returns how it ended.
 */
async function waitForRun(qc: QueryClient, kind: "review" | "suggestions"): Promise<RunResult | null> {
  for (;;) {
    await new Promise((resolve) => setTimeout(resolve, RUN_POLL_MS));
    const st = await getAiStatus();
    qc.setQueryData(aiKeys.status, st);
    if (kind === "review" ? !st.running_review : !st.running_suggestions) {
      return kind === "review" ? st.last_review_result : st.last_suggestions_result;
    }
  }
}

/**
 * Runs `start` (this page's own run), then checks the status again before it
 * counts as over. A check that landed while the POST waited still says
 * running, and useOtherRun would take it for a run started elsewhere.
 */
async function runHere<T>(qc: QueryClient, start: () => Promise<T>): Promise<T> {
  try {
    return await start();
  } finally {
    await qc.refetchQueries({ queryKey: aiKeys.status });
  }
}

/**
 * Writes the review now. The POST waits up to 5 s. A review still going then
 * calls `onStarted` and is waited for. Returns how it ended.
 */
export function runReview(qc: QueryClient, onStarted: () => void): Promise<RunReviewStartOut | RunResult | null> {
  return runHere(qc, async () => {
    const r: RunReviewStartOut = await request("POST", "/api/ai/review/run");
    if (!r.started) return r;
    onStarted();
    return waitForRun(qc, "review");
  });
}

// ---------- Category suggestions ----------

export function getSuggestions(): Promise<{ suggestions: CategorySuggestion[] }> {
  return request("GET", "/api/category-suggestions");
}

/**
 * Suggests categories now, like runReview. Returns the counts. Throws
 * ApiError when the run was refused or failed.
 */
export function runSuggestions(qc: QueryClient, onStarted: () => void): Promise<{ merchants: number; suggested: number }> {
  return runHere(qc, async () => {
    const r: S["SuggestionRunStartOut"] = await request("POST", "/api/category-suggestions/run");
    if (!r.started) return { merchants: r.merchants ?? 0, suggested: r.suggested ?? 0 };
    onStarted();
    const end = await waitForRun(qc, "suggestions");
    if (end?.status !== "done") throw new ApiError(503, end?.message ?? NOT_FINISHED_TEXT);
    return { merchants: end.merchants ?? 0, suggested: end.suggested ?? 0 };
  });
}

export function decideSuggestions(ids: number[], action: "approve" | "reject"): Promise<{ changed: number }> {
  if (ids.length === 1) return request("POST", `/api/category-suggestions/${ids[0]}/${action}`);
  return request("POST", "/api/category-suggestions/bulk", { body: { ids, action } });
}

// ---------- Formatting ----------

/** Micro-dollars as dollars: "$0.042", "$12.35". Small amounts keep a tenth of a cent. */
export function formatMicroUsd(micro: number): string {
  const dollars = micro / 1_000_000;
  if (dollars > 0 && dollars < 0.001) return "<$0.001";
  // Pick the digits after rounding, so $0.9996 shows as $1.00, not $1.000.
  const digits = Math.round(dollars * 1000) / 1000 < 1 ? 3 : 2;
  return `$${dollars.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

const MODEL_NAMES: Record<string, string> = {
  "claude-opus-5-5": "Opus 5.5",
  "claude-sonnet-5-5": "Sonnet 5.5",
  "claude-haiku-5-5": "Haiku 5.5",
};

export function modelName(model: string): string {
  return MODEL_NAMES[model] ?? model;
}
