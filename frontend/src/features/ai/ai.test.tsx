import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../../routes/AppRoutes";
import { renderWithProviders } from "../../test/render";
import { DASHBOARD } from "../dashboard/fixtures";
import { type AiStatusOut, type AiUsage, formatMicroUsd, RUN_POLL_MS, WORKING_TEXT } from "./api";
import { NEW_THREAD_TEXT, STOPPED_EARLY_TEXT, STOPPED_TEXT } from "./AskContext";
import { ENDED_TEXT, offText } from "./AskPanel";
import { SafeText } from "./SafeText";
import { SuggestionsLink } from "./SuggestionsLink";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** A text/event-stream body with these events. */
function sse(events: unknown[]): Response {
  const body = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
  // Split mid-event, as a real stream can.
  const bytes = new TextEncoder().encode(body);
  const cut = Math.floor(bytes.length / 2);
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      c.enqueue(bytes.slice(0, cut));
      c.enqueue(bytes.slice(cut));
      c.close();
    },
  });
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

const META = {
  app_version: "0.1.0",
  schema_versions: ["1.0"],
  household_today: "2026-10-21",
  latest_run_id: "2026-10-14",
  updated: "2026-10-14",
  this_month: "2026-10",
  complete_as_of: "2026-09-30",
  latest_complete_month: "2026-08",
  location_label: "Example Town",
};

/** A text/event-stream that sends a thread event, then waits: `finish` ends it, an abort errors it like fetch does. */
function openStream(signal: AbortSignal | null | undefined, onFinish: (finish: () => void) => void): Response {
  const enc = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      c.enqueue(enc.encode(`data: ${JSON.stringify({ type: "thread", thread_id: "a".repeat(32), new: true, notice: null })}\n\n`));
      c.enqueue(enc.encode(`data: ${JSON.stringify({ type: "text", text: "Half an answer." })}\n\n`));
      signal?.addEventListener("abort", () => c.error(new DOMException("The operation was aborted.", "AbortError")));
      onFinish(() => {
        const done = { type: "done", thread_id: "a".repeat(32), text: "A whole answer.", notice: null, thread_closed: false, cost_micro_usd: null };
        c.enqueue(enc.encode(`data: ${JSON.stringify(done)}\n\n`));
        c.close();
      });
    },
  });
  return new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

const ON: AiStatusOut = {
  enabled: true,
  key_present: true,
  available: true,
  reason: null,
  blocked_until: null,
  blocked_reason: null,
  thread_retention_days: 90,
  running_review: false,
  running_suggestions: false,
  last_review_result: null,
  last_suggestions_result: null,
  review_failed: null,
};
const OFF: AiStatusOut = { ...ON, enabled: false, available: false, reason: "off" };
const NO_KEY: AiStatusOut = { ...ON, key_present: false, available: false, reason: "no_key" };
const BILLING_TEXT = "The Claude Console account is out of credit or has a billing problem.";
/** The 409 and error-event text (backend app/ai/client.py UNAVAILABLE_TEXT). */
const CLIENT_BILLING_TEXT = "AI is paused: the Claude Console account has no credit or a billing problem.";
const PAUSED: AiStatusOut = {
  ...ON,
  available: false,
  reason: "spend_limit",
  blocked_until: "2026-11-01T00:00:00+00:00",
  blocked_reason: BILLING_TEXT,
};

const ANSWER_EVENTS = [
  { type: "thread", thread_id: "a".repeat(32), new: true, notice: null },
  { type: "status", tool: "spending_summary", label: "Adding up spending" },
  { type: "text", text: "You spent **$2,947.39** " },
  { type: "text", text: "on dining." },
  {
    type: "done",
    thread_id: "a".repeat(32),
    text: "You spent **$2,947.39** on dining.",
    notice: null,
    thread_closed: false,
    cost_micro_usd: 42_000,
  },
];

let role = "admin";
let status: AiStatusOut = ON;
let askResponse: (init?: RequestInit) => Response = () => sse(ANSWER_EVENTS);
let runResponse: () => Response | Promise<Response> = () => json(404, { detail: "Not found." });
let decideResponse: () => Response = () => json(200, { changed: 1 });
// The suggestions list waits for this before it answers, so a test can hold a refresh.
let listGate: Promise<void> = Promise.resolve();
let suggestions: unknown[] = [];
const posts: { url: string; body: unknown }[] = [];

const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const method = init?.method ?? "GET";
  if (method !== "GET") posts.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
  if (url === "/api/auth/me") return json(200, { username: "alex", display_name: "Alex Example", role });
  if (url === "/api/meta") return json(200, META);
  if (url === "/api/ai/status") return json(200, status);
  if (url === "/api/ask") return askResponse(init);
  if (url === "/api/ai/review/run" || url === "/api/category-suggestions/run") return runResponse();
  if (url === "/api/ask/threads")
    return json(200, {
      threads: [
        {
          thread_id: "b".repeat(32),
          title: "What is our net worth?",
          created_at: "2026-10-20T15:00:00+00:00",
          updated_at: "2026-10-20T15:01:00+00:00",
          turn_count: 1,
          closed: false,
          model: "claude-sonnet-5-5",
        },
      ],
    });
  if (url === `/api/ask/threads/${"b".repeat(32)}`)
    return json(200, {
      thread_id: "b".repeat(32),
      title: "What is our net worth?",
      created_at: "2026-10-20T15:00:00+00:00",
      updated_at: "2026-10-20T15:01:00+00:00",
      closed: false,
      model: "claude-sonnet-5-5",
      turns: [{ question: "What is our net worth?", answer: "It is **$400,000**.", tools: ["Reading balances"], notice: null }],
      cost_micro_usd: role === "admin" ? 12_000 : null,
    });
  if (url === "/api/dashboard") return json(200, { ...DASHBOARD, ai_review: AI_REVIEW });
  if (url === "/api/settings/ai") {
    if (method === "PUT") settings = JSON.parse(String(init?.body));
    return json(200, settingsOut());
  }
  if (url === "/api/ai/usage") return json(200, usage);
  if (url === "/api/category-suggestions") {
    await listGate;
    return json(200, { suggestions });
  }
  if (url.startsWith("/api/category-suggestions/")) return decideResponse();
  return json(404, { detail: "Not found." });
});

const DEFAULT_SETTINGS = {
  enabled: false,
  ask_model: "claude-sonnet-5-5",
  review_model: "claude-opus-5-5",
  categorize_model: "claude-haiku-5-5",
  planner_model: "claude-opus-5-5",
  monthly_cap_cents: 2500,
  ask_reserve_cents: 200,
  daily_cap_cents: 400,
  daily_questions: 40,
  thread_retention_days: 90,
};
let settings = { ...DEFAULT_SETTINGS };
let settingsStatus: AiStatusOut | null = null;

function settingsOut() {
  return {
    settings,
    status: settingsStatus ?? (settings.enabled ? ON : OFF),
    effort: { ask: "medium", review: "high", categorize: "low", plan_research: "medium", plan_write: "high" },
    models: ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"],
    prices: [
      { model: "claude-sonnet-5-5", input: "2", write_5m: "2.50", write_1h: "4", cache_read: "0.10", output: "10", long_prompt: null },
    ],
    prices_checked_on: "2026-10-22",
    prices_source: "https://platform.claude.com/docs/en/about-claude/pricing",
  };
}

const USAGE = {
  month: "2026-10",
  month_cost_micro_usd: 3_456_000,
  month_calls: 61,
  month_by_feature_micro_usd: { ask: 3_000_000, review: 456_000, categorize: 0 },
  monthly_cap_cents: 2500,
  month_used_pct: "13.8",
  warning_pct: null,
  today_cost_micro_usd: 120_000,
  today_calls: 4,
  today_questions: 2,
  daily_cap_cents: 400,
  daily_questions: 40,
  cache_hit_pct: "91.2",
  calls: [
    {
      at: "2026-10-21T15:00:00+00:00",
      feature: "ask",
      model: "claude-sonnet-5-5",
      effort: "medium",
      input_tokens: 220,
      cache_creation_input_tokens: 0,
      cache_read_input_tokens: 9_800,
      output_tokens: 310,
      cost_micro_usd: 4_520,
      stop_reason: "end_turn",
      latency_ms: 2_400,
      error: null,
      web_searches: 0,
    },
  ],
};

const AI_REVIEW = {
  run_id: "2026-10-14",
  month: "2026-09",
  model: "claude-opus-5-5",
  created_at: "2026-10-14T12:00:00+00:00",
  summary: "A calm month with **$1,200** left over.",
  changes: ["Dining went down."],
  anomalies: ["<img src=x> showed up twice."],
  actions: ["Pay the card.", "Check the car value.", "Move $500 to savings."],
};

const SUGGESTIONS = [
  {
    id: 7,
    txn_key: "t1",
    txn_date: "2026-09-12",
    amount_cents: -1250,
    merchant: "Corner Cafe",
    description: "CORNER CAFE",
    account_name: "Blue card",
    category_key: "dining",
    category_name: "Dining",
    category_emoji: null,
    confidence: 82,
    reason: "A cafe name.",
    model: "claude-haiku-5-5",
    created_at: "2026-10-14T12:00:00+00:00",
  },
];

let usage: AiUsage = USAGE;

beforeEach(() => {
  role = "admin";
  settings = { ...DEFAULT_SETTINGS };
  settingsStatus = null;
  status = ON;
  usage = USAGE;
  askResponse = () => sse(ANSWER_EVENTS);
  runResponse = () => json(404, { detail: "Not found." });
  decideResponse = () => json(200, { changed: 1 });
  listGate = Promise.resolve();
  suggestions = SUGGESTIONS;
  posts.length = 0;
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function openAsk() {
  const nav = await screen.findByRole("navigation", { name: "Main" });
  await userEvent.click(await within(nav).findByRole("button", { name: /Ask/ }));
  return screen.findByRole("dialog", { name: "Ask" });
}

describe("Ask panel", () => {
  it("is hidden while AI is off", async () => {
    status = OFF;
    renderWithProviders(<AppRoutes />, { route: "/flags" });
    const nav = await screen.findByRole("navigation", { name: "Main" });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/ai/status", expect.anything()));
    expect(within(nav).queryByRole("button", { name: /Ask/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Ask" })).toBeNull(); // phone top bar too
  });

  it("starts empty with three starters, streams an answer with status chips, and shows the admin cost", async () => {
    renderWithProviders(<AppRoutes />, { route: "/flags" });
    const panel = await openAsk();
    const starters = within(panel).getAllByRole("listitem");
    expect(starters).toHaveLength(3);
    await userEvent.click(within(panel).getByRole("button", { name: "How much did we spend on dining last month?" }));
    expect(posts).toContainEqual({ url: "/api/ask", body: { question: "How much did we spend on dining last month?" } });
    expect(await within(panel).findByText("Cost $0.042")).toBeTruthy();
    expect(within(panel).getByText("Adding up spending")).toBeTruthy();
    expect(within(panel).getByText("$2,947.39").tagName).toBe("STRONG");
    // The keyboard can scroll back through the answers.
    expect(within(panel).getByRole("region", { name: "Conversation" }).getAttribute("tabindex")).toBe("0");
    // A follow-up goes to the same thread.
    await userEvent.type(within(panel).getByLabelText("Your question"), "And in August?{Enter}");
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1]).toEqual({ url: "/api/ask", body: { question: "And in August?", thread_id: "a".repeat(32) } });
  });

  it("never shows a member the cost", async () => {
    role = "member";
    renderWithProviders(<AppRoutes />, { route: "/flags" });
    const panel = await openAsk();
    await userEvent.type(within(panel).getByLabelText("Your question"), "Dining?{Enter}");
    await within(panel).findByText("$2,947.39");
    expect(within(panel).queryByText(/Cost/)).toBeNull();
  });

  it("shows the server's message when a question is refused before streaming", async () => {
    askResponse = () => json(429, { detail: "Today's AI budget is used up. Try again tomorrow." });
    renderWithProviders(<AppRoutes />, { route: "/flags" });
    const panel = await openAsk();
    await userEvent.type(within(panel).getByLabelText("Your question"), "Dining?{Enter}");
    expect((await within(panel).findByRole("alert")).textContent).toBe("Today's AI budget is used up. Try again tomorrow.");
  });

  it("shows an error event and a notice", async () => {
    askResponse = () =>
      sse([
        { type: "thread", thread_id: "a".repeat(32), new: false, notice: "This is a new conversation. The last one was long or had ended." },
        { type: "error", message: "Claude did not answer just now. Try again in a minute.", kind: "failed" },
      ]);
    renderWithProviders(<AppRoutes />, { route: "/flags" });
    const panel = await openAsk();
    await userEvent.type(within(panel).getByLabelText("Your question"), "Dining?{Enter}");
    expect((await within(panel).findByRole("alert")).textContent).toContain("Try again in a minute");
    expect(within(panel).getByText(/This is a new conversation/)).toBeTruthy();
  });

  it("stays open and says so when AI goes off mid-conversation", async () => {
    askResponse = () => {
      status = OFF;
      return json(409, { detail: "AI is turned off in Settings." });
    };
    renderWithProviders(<AppRoutes />, { route: "/flags" });
    const panel = await openAsk();
    await userEvent.type(within(panel).getByLabelText("Your question"), "Dining?{Enter}");
    expect(await within(panel).findByText("Ask is off right now. An admin can turn it on in Settings.")).toBeTruthy();
    // The footer says why; the turn does not say it again.
    expect(within(panel).queryByText("AI is turned off in Settings.")).toBeNull();
    expect(screen.getByRole("dialog", { name: "Ask" })).toBeTruthy();
    expect(within(panel).queryByLabelText("Your question")).toBeNull();
  });

  it("gives a refused question back to edit", async () => {
    askResponse = () => json(422, { detail: "Leave out the account number and ask again." });
    renderWithProviders(<AppRoutes />, { route: "/flags" });
    const panel = await openAsk();
    const box = within(panel).getByLabelText("Your question") as HTMLTextAreaElement;
    await userEvent.type(box, "Is card ending 12 paid?{Enter}");
    await within(panel).findByRole("alert");
    expect(box.value).toBe("Is card ending 12 paid?");
  });

  it("replaces streamed text when the filter changes it", async () => {
    askResponse = () =>
      sse([
        { type: "thread", thread_id: "a".repeat(32), new: true, notice: null },
        { type: "text", text: "Ref 41. " },
        { type: "replace", text: "Ref …" },
        { type: "done", thread_id: "a".repeat(32), text: "Ref …", notice: null, thread_closed: false, cost_micro_usd: null },
      ]);
    renderWithProviders(<AppRoutes />, { route: "/flags" });
    const panel = await openAsk();
    await userEvent.type(within(panel).getByLabelText("Your question"), "Ref?{Enter}");
    expect(await within(panel).findByText("Ref …")).toBeTruthy();
    expect(within(panel).queryByText(/Ref 41/)).toBeNull();
  });

  it("starts a new conversation after one ends, and says so", async () => {
    askResponse = () =>
      sse([
        { type: "thread", thread_id: "a".repeat(32), new: true, notice: null },
        { type: "done", thread_id: "a".repeat(32), text: "Cut.", notice: "The answer was cut off.", thread_closed: true, cost_micro_usd: null },
      ]);
    renderWithProviders(<AppRoutes />, { route: "/flags" });
    const panel = await openAsk();
    await userEvent.type(within(panel).getByLabelText("Your question"), "Long one?{Enter}");
    expect(await within(panel).findByText(ENDED_TEXT)).toBeTruthy();
    await userEvent.type(within(panel).getByLabelText("Your question"), "Next?{Enter}");
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1]?.body).toEqual({ question: "Next?" });
    expect(await within(panel).findByText(NEW_THREAD_TEXT)).toBeTruthy();
  });

  it("opens a past conversation", async () => {
    renderWithProviders(<AppRoutes />, { route: "/flags" });
    const panel = await openAsk();
    await userEvent.click(within(panel).getByRole("button", { name: "Past conversations" }));
    const dialog = await screen.findByRole("dialog", { name: "Past conversations" });
    expect(within(dialog).getByText(/deleted 90 days after they start/)).toBeTruthy();
    await userEvent.click(await within(dialog).findByRole("button", { name: /What is our net worth/ }));
    const chat = await screen.findByRole("dialog", { name: "Ask" });
    expect(within(chat).getByText("$400,000").tagName).toBe("STRONG");
    expect(within(chat).getByText("Reading balances")).toBeTruthy();
    expect(within(chat).getByText("This conversation cost $0.012.")).toBeTruthy();
  });

  describe("on a wide screen", () => {
    const matchMedia = window.matchMedia;
    beforeEach(() => {
      window.matchMedia = (query: string) => ({ ...matchMedia(query), matches: true });
    });
    afterEach(() => {
      window.matchMedia = matchMedia;
    });

    it("keeps focus in the panel when Stop is pressed", async () => {
      askResponse = (init) => openStream(init?.signal, () => {});
      renderWithProviders(<AppRoutes />, { route: "/flags" });
      const panel = await openAsk();
      await userEvent.type(within(panel).getByLabelText("Your question"), "Long one?{Enter}");
      const stop = await within(panel).findByRole("button", { name: "Stop" });
      stop.focus();
      await userEvent.click(stop);
      await within(panel).findByText(STOPPED_TEXT);
      await waitFor(() => expect(document.activeElement?.id).toBe("ask-input"));
    });

    it("keeps focus in the panel when the answer ends while Stop has focus", async () => {
      let finish = () => {};
      askResponse = (init) => openStream(init?.signal, (f) => (finish = f));
      renderWithProviders(<AppRoutes />, { route: "/flags" });
      const panel = await openAsk();
      await userEvent.type(within(panel).getByLabelText("Your question"), "Long one?{Enter}");
      (await within(panel).findByRole("button", { name: "Stop" })).focus();
      act(() => finish());
      await within(panel).findByText("A whole answer.");
      await waitFor(() => expect(document.activeElement?.id).toBe("ask-input"));
    });
  });

  it("says Stopped once, without the ended line", async () => {
    askResponse = (init) => openStream(init?.signal, () => {});
    renderWithProviders(<AppRoutes />, { route: "/flags" });
    const panel = await openAsk();
    await userEvent.type(within(panel).getByLabelText("Your question"), "Long one?{Enter}");
    await userEvent.click(await within(panel).findByRole("button", { name: "Stop" }));
    expect(await within(panel).findByText(STOPPED_TEXT)).toBeTruthy();
    await within(panel).findByRole("button", { name: "Send" });
    expect(within(panel).queryByText(ENDED_TEXT)).toBeNull();
  });

  it("keeps a past conversation open when it is opened mid-answer", async () => {
    askResponse = (init) => openStream(init?.signal, () => {});
    renderWithProviders(<AppRoutes />, { route: "/flags" });
    const panel = await openAsk();
    await userEvent.type(within(panel).getByLabelText("Your question"), "Long one?{Enter}");
    await within(panel).findByRole("button", { name: "Stop" });
    askResponse = () => sse(ANSWER_EVENTS);
    await userEvent.click(within(panel).getByRole("button", { name: "Past conversations" }));
    const list = await screen.findByRole("dialog", { name: "Past conversations" });
    await userEvent.click(await within(list).findByRole("button", { name: /What is our net worth/ }));
    const chat = await screen.findByRole("dialog", { name: "Ask" });
    await within(chat).findByRole("button", { name: "Send" });
    expect(within(chat).getByText("$400,000")).toBeTruthy();
    expect(within(chat).queryByText(ENDED_TEXT)).toBeNull();
    await userEvent.type(within(chat).getByLabelText("Your question"), "And last year?{Enter}");
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1]?.body).toEqual({ question: "And last year?", thread_id: "b".repeat(32) });
  });

  it("says so when the stream closes before the answer is done", async () => {
    askResponse = () =>
      sse([
        { type: "thread", thread_id: "a".repeat(32), new: true, notice: null },
        { type: "text", text: "Your cash flow in September was " },
      ]);
    renderWithProviders(<AppRoutes />, { route: "/flags" });
    const panel = await openAsk();
    await userEvent.type(within(panel).getByLabelText("Your question"), "Cash flow?{Enter}");
    expect((await within(panel).findByRole("alert")).textContent).toBe(STOPPED_EARLY_TEXT);
    expect(within(panel).getByText(ENDED_TEXT)).toBeTruthy();
    await userEvent.type(within(panel).getByLabelText("Your question"), "Next?{Enter}");
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1]?.body).toEqual({ question: "Next?" });
  });

  it("says why it is paused in the server's words", () => {
    const text = offText(PAUSED);
    expect(text).toContain(BILLING_TEXT);
    expect(text).not.toContain("spend limit");
    expect(offText({ ...PAUSED, blocked_reason: "The Claude Console workspace reached its spend limit." })).toContain(
      "workspace reached its spend limit",
    );
  });

  it.each([
    ["refused with a 409", () => json(409, { detail: CLIENT_BILLING_TEXT })],
    [
      "stopped by a pause error event",
      () =>
        sse([
          { type: "thread", thread_id: "a".repeat(32), new: true, notice: null },
          { type: "error", message: CLIENT_BILLING_TEXT, kind: "spend_limit" },
        ]),
    ],
  ])("says a billing pause once when a question is %s", async (_, response) => {
    askResponse = () => {
      status = PAUSED;
      return response();
    };
    renderWithProviders(<AppRoutes />, { route: "/flags" });
    const panel = await openAsk();
    await userEvent.type(within(panel).getByLabelText("Your question"), "Dining?{Enter}");
    expect(await within(panel).findByText(offText(PAUSED))).toBeTruthy();
    expect(within(panel).queryByText(CLIENT_BILLING_TEXT)).toBeNull();
    expect(within(panel).queryByRole("alert")).toBeNull();
  });

  it("still shows a 409 that is not a pause", async () => {
    askResponse = () => json(409, { detail: "Another question is still being answered. Try again in a moment." });
    renderWithProviders(<AppRoutes />, { route: "/flags" });
    const panel = await openAsk();
    await userEvent.type(within(panel).getByLabelText("Your question"), "Dining?{Enter}");
    expect((await within(panel).findByRole("alert")).textContent).toBe("Another question is still being answered. Try again in a moment.");
  });
});

describe("SafeText", () => {
  it("renders paragraphs, lists, bold and code, and nothing else", () => {
    const { container } = render(
      <SafeText
        text={[
          "Here is **one** and `two`.",
          "",
          "- first",
          "- <b>second</b>",
          "1. [a link](https://example.com)",
          "2. ![pic](https://example.com/x.png)",
          "",
          "<script>alert(1)</script>",
        ].join("\n")}
      />,
    );
    expect(container.querySelector("strong")?.textContent).toBe("one");
    expect(container.querySelector("code")?.textContent).toBe("two");
    expect(container.querySelectorAll("ul li")).toHaveLength(2);
    expect(container.querySelectorAll("ol li")).toHaveLength(2);
    expect(container.querySelector("a, img, b, script")).toBeNull();
    expect(container.textContent).toContain("<b>second</b>");
    expect(container.textContent).toContain("[a link](https://example.com)");
    expect(container.textContent).toContain("<script>alert(1)</script>");
  });

  it("keeps the numbers of a list that does not start at 1", () => {
    const { container } = render(<SafeText text={["1. Pay the card", "", "Then:", "", "2. Move $500", "", "10) ten"].join("\n")} />);
    const lists = [...container.querySelectorAll("ol")];
    expect(lists.map((ol) => ol.getAttribute("start"))).toEqual([null, "2", "10"]);
    expect(lists.map((ol) => ol.textContent)).toEqual(["Pay the card", "Move $500", "ten"]);
  });
});

describe("formatMicroUsd", () => {
  it("keeps a tenth of a cent under a dollar", () => {
    expect(formatMicroUsd(42_000)).toBe("$0.042");
    expect(formatMicroUsd(12_345_678)).toBe("$12.35");
    expect(formatMicroUsd(0)).toBe("$0.000");
    expect(formatMicroUsd(10)).toBe("<$0.001");
  });
});

describe("Dashboard review", () => {
  it("shows the AI review in place of the chat's, as text", async () => {
    renderWithProviders(<AppRoutes />, { route: "/" });
    const subtitle = await screen.findByText("Claude's review of Sep 2026");
    const card = subtitle.closest("section")!;
    expect(within(card).getByRole("heading", { name: "Latest review" })).toBeTruthy();
    expect(within(card).getByText("$1,200").tagName).toBe("STRONG");
    expect(within(card).getByText("<img src=x> showed up twice.")).toBeTruthy();
    expect(card.querySelector("img")).toBeNull();
    expect(within(card).getByRole("heading", { name: "Three things to do" })).toBeTruthy();
    expect(within(card).queryByText("Top three")).toBeNull();
  });
});

describe("Category suggestions", () => {
  it("approves one suggestion", async () => {
    renderWithProviders(<AppRoutes />, { route: "/transactions/suggestions" });
    await screen.findByText("Corner Cafe");
    expect(screen.getByText(/82% sure/)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Approve Dining for Corner Cafe" }));
    await waitFor(() => expect(posts).toContainEqual({ url: "/api/category-suggestions/7/approve", body: null }));
    expect(await screen.findByText("Applied 1 category.")).toBeTruthy();
  });

  it("takes a decided row off the list at once, until the server's list comes back", async () => {
    renderWithProviders(<AppRoutes />, { route: "/transactions/suggestions" });
    await screen.findByText("Corner Cafe");
    let release = () => {};
    listGate = new Promise((r) => (release = r));
    await userEvent.click(screen.getByRole("button", { name: "Approve Dining for Corner Cafe" }));
    expect(await screen.findByText("Applied 1 category.")).toBeTruthy();
    expect(screen.queryByText("Corner Cafe")).toBeNull();
    // This server still lists it, so it shows again: the refresh decides.
    release();
    expect(await screen.findByText("Corner Cafe")).toBeTruthy();
  });

  it("keeps each row decided before the refresh off the list", async () => {
    suggestions = [...SUGGESTIONS, { ...SUGGESTIONS[0], id: 8, txn_key: "t2", merchant: "Book Nook", description: "BOOK NOOK" }];
    renderWithProviders(<AppRoutes />, { route: "/transactions/suggestions" });
    await screen.findByText("Book Nook");
    let release = () => {};
    listGate = new Promise((r) => (release = r));
    await userEvent.click(screen.getByRole("button", { name: "Approve Dining for Corner Cafe" }));
    await waitFor(() => expect(screen.queryByText("Corner Cafe")).toBeNull());
    await userEvent.click(screen.getByRole("button", { name: "Approve Dining for Book Nook" }));
    await waitFor(() => expect(screen.queryByText("Book Nook")).toBeNull());
    expect(screen.queryByText("Corner Cafe")).toBeNull();
    release();
    expect(await screen.findByText("Book Nook")).toBeTruthy();
  });

  it("rejects in bulk", async () => {
    renderWithProviders(<AppRoutes />, { route: "/transactions/suggestions" });
    await screen.findByText("Corner Cafe");
    await userEvent.click(screen.getByRole("checkbox", { name: "Select all" }));
    await userEvent.click(screen.getByRole("button", { name: "Reject 1" }));
    await waitFor(() => expect(posts).toContainEqual({ url: "/api/category-suggestions/7/reject", body: null }));
  });

  it("says AI is off and lists nothing", async () => {
    status = OFF;
    renderWithProviders(<AppRoutes />, { route: "/transactions/suggestions" });
    expect(await screen.findByText("AI is off")).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalledWith("/api/category-suggestions", expect.anything());
  });

  it("hides the link and the list when there is no API key", async () => {
    status = NO_KEY;
    const { unmount } = renderWithProviders(<SuggestionsLink />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/ai/status", expect.anything()));
    expect(screen.queryByRole("link")).toBeNull();
    unmount();
    renderWithProviders(<AppRoutes />, { route: "/transactions/suggestions" });
    expect(await screen.findByText("No API key")).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalledWith("/api/category-suggestions", expect.anything());
  });

  it("says the server needs an API key, not to turn AI on, when the key is missing", async () => {
    status = NO_KEY;
    renderWithProviders(<AppRoutes />, { route: "/transactions/suggestions" });
    expect(await screen.findByText("No API key is set on the server. Suggestions show here once it is.")).toBeTruthy();
    expect(screen.queryByText(/turns AI on/)).toBeNull();
    expect(screen.queryByText("AI is off")).toBeNull();
  });

  it("keeps the link and the list during a pause, without Suggest categories", async () => {
    status = PAUSED;
    const { unmount } = renderWithProviders(<SuggestionsLink />);
    expect(await screen.findByRole("link", { name: /Suggestions/ })).toBeTruthy();
    unmount();
    renderWithProviders(<AppRoutes />, { route: "/transactions/suggestions" });
    await screen.findByText("Corner Cafe");
    expect(screen.queryByRole("button", { name: /Suggest categories/ })).toBeNull();
  });

  describe("a run that goes on in the background", () => {
    beforeEach(() => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
    });
    afterEach(() => {
      vi.useRealTimers();
    });

    it("says it is working, waits for the status, then shows the counts", async () => {
      runResponse = () => {
        status = { ...ON, running_suggestions: true };
        return json(200, { started: true });
      };
      renderWithProviders(<AppRoutes />, { route: "/transactions/suggestions" });
      await screen.findByText("Corner Cafe");
      await userEvent.click(screen.getByRole("button", { name: /Suggest categories/ }));
      expect(await screen.findByText(WORKING_TEXT)).toBeTruthy();
      expect(screen.getByRole("button", { name: /Asking Claude/ })).toHaveProperty("disabled", true);
      const lists = () => fetchMock.mock.calls.filter(([u]) => u === "/api/category-suggestions").length;
      const before = lists();
      await act(() => vi.advanceTimersByTimeAsync(RUN_POLL_MS));
      expect(screen.getByText(WORKING_TEXT)).toBeTruthy();
      status = {
        ...ON,
        last_suggestions_result: { status: "done", merchants: 3, suggested: 2, finished_at: "2026-10-22T15:00:00+00:00" },
      };
      await act(() => vi.advanceTimersByTimeAsync(RUN_POLL_MS));
      expect(await screen.findByText("Checked 3 merchants. 2 new suggestions.")).toBeTruthy();
      expect(screen.getByRole("button", { name: /Suggest categories/ })).toHaveProperty("disabled", false);
      await waitFor(() => expect(lists()).toBeGreaterThan(before));
    });

    it("shows why a run failed", async () => {
      runResponse = () => {
        status = { ...ON, running_suggestions: true };
        return json(200, { started: true });
      };
      renderWithProviders(<AppRoutes />, { route: "/transactions/suggestions" });
      await screen.findByText("Corner Cafe");
      await userEvent.click(screen.getByRole("button", { name: /Suggest categories/ }));
      await screen.findByText(WORKING_TEXT);
      status = {
        ...ON,
        last_suggestions_result: {
          status: "failed",
          message: "Claude did not answer just now. Try again in a minute.",
          finished_at: "2026-10-22T15:00:00+00:00",
        },
      };
      await act(() => vi.advanceTimersByTimeAsync(RUN_POLL_MS));
      expect((await screen.findByRole("alert")).textContent).toBe("Claude did not answer just now. Try again in a minute.");
    });

    it("shows how a run started before the page opened ended, and refreshes the list", async () => {
      status = { ...ON, running_suggestions: true };
      renderWithProviders(<AppRoutes />, { route: "/transactions/suggestions" });
      await screen.findByText("Corner Cafe");
      expect(screen.getByText(WORKING_TEXT)).toBeTruthy();
      expect(screen.getByRole("button", { name: /Asking Claude/ })).toHaveProperty("disabled", true);
      const lists = () => fetchMock.mock.calls.filter(([u]) => u === "/api/category-suggestions").length;
      const before = lists();
      status = {
        ...ON,
        last_suggestions_result: { status: "done", merchants: 3, suggested: 2, finished_at: "2026-10-22T15:00:00+00:00" },
      };
      await act(() => vi.advanceTimersByTimeAsync(RUN_POLL_MS));
      expect(await screen.findByText("Checked 3 merchants. 2 new suggestions.")).toBeTruthy();
      expect(screen.getByRole("button", { name: /Suggest categories/ })).toHaveProperty("disabled", false);
      await waitFor(() => expect(lists()).toBeGreaterThan(before));
      expect(posts).toEqual([]);
    });

    it("shows why a run started before the page opened failed", async () => {
      status = { ...ON, running_suggestions: true };
      renderWithProviders(<AppRoutes />, { route: "/transactions/suggestions" });
      await screen.findByText(WORKING_TEXT);
      status = {
        ...ON,
        last_suggestions_result: { status: "spend_limit", message: CLIENT_BILLING_TEXT, finished_at: "2026-10-22T15:00:00+00:00" },
      };
      await act(() => vi.advanceTimersByTimeAsync(RUN_POLL_MS));
      expect((await screen.findByRole("alert")).textContent).toBe(CLIENT_BILLING_TEXT);
      expect(screen.queryByText(WORKING_TEXT)).toBeNull();
    });
  });

  it("shows a run's working text and counts after a failed decision", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      decideResponse = () => json(409, { detail: "That suggestion was already decided." });
      runResponse = () => {
        status = { ...ON, running_suggestions: true };
        return json(200, { started: true });
      };
      renderWithProviders(<AppRoutes />, { route: "/transactions/suggestions" });
      await screen.findByText("Corner Cafe");
      await userEvent.click(screen.getByRole("button", { name: "Approve Dining for Corner Cafe" }));
      expect((await screen.findByRole("alert")).textContent).toBe("That suggestion was already decided.");
      await userEvent.click(screen.getByRole("button", { name: /Suggest categories/ }));
      expect(await screen.findByText(WORKING_TEXT)).toBeTruthy();
      expect(screen.queryByRole("alert")).toBeNull();
      status = {
        ...ON,
        last_suggestions_result: { status: "done", merchants: 4, suggested: 3, finished_at: "2026-10-22T15:00:00+00:00" },
      };
      await act(() => vi.advanceTimersByTimeAsync(RUN_POLL_MS));
      expect(await screen.findByText("Checked 4 merchants. 3 new suggestions.")).toBeTruthy();
      expect(screen.queryByRole("alert")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows a failed decision while a run from elsewhere goes on", async () => {
    status = { ...ON, running_suggestions: true };
    decideResponse = () => json(409, { detail: "That suggestion was already decided." });
    renderWithProviders(<AppRoutes />, { route: "/transactions/suggestions" });
    await screen.findByText("Corner Cafe");
    expect(await screen.findByText(WORKING_TEXT)).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Approve Dining for Corner Cafe" }));
    expect((await screen.findByRole("alert")).textContent).toBe("That suggestion was already decided.");
  });

  it("shows a decision after a failed run", async () => {
    runResponse = () => json(503, { detail: "Claude did not answer just now. Try again in a minute." });
    renderWithProviders(<AppRoutes />, { route: "/transactions/suggestions" });
    await screen.findByText("Corner Cafe");
    await userEvent.click(screen.getByRole("button", { name: /Suggest categories/ }));
    expect((await screen.findByRole("alert")).textContent).toBe("Claude did not answer just now. Try again in a minute.");
    await userEvent.click(screen.getByRole("button", { name: "Approve Dining for Corner Cafe" }));
    expect(await screen.findByText("Applied 1 category.")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows a quick run's counts at once when a status check landed while it ran", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      // A review goes on, so the status is checked every RUN_POLL_MS.
      status = { ...ON, running_review: true };
      runResponse = () => {
        status = { ...ON, running_review: true, running_suggestions: true };
        return new Promise((resolve) =>
          setTimeout(() => {
            status = { ...ON, running_review: true };
            resolve(json(200, { merchants: 2, suggested: 2 }));
          }, RUN_POLL_MS + 1_000),
        );
      };
      renderWithProviders(<AppRoutes />, { route: "/transactions/suggestions" });
      await screen.findByText("Corner Cafe");
      const checks = () => fetchMock.mock.calls.filter(([u]) => u === "/api/ai/status").length;
      const before = checks();
      await userEvent.click(screen.getByRole("button", { name: /Suggest categories/ }));
      await act(() => vi.advanceTimersByTimeAsync(RUN_POLL_MS + 1_000));
      // A check landed while the POST waited, and said running.
      expect(checks()).toBeGreaterThan(before);
      await act(() => vi.advanceTimersByTimeAsync(100));
      await waitFor(() => expect(screen.getByRole("button", { name: /Suggest categories/ })).toHaveProperty("disabled", false));
      expect(screen.getByText("Checked 2 merchants. 2 new suggestions.")).toBeTruthy();
      expect(screen.queryByText(WORKING_TEXT)).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("only offers Suggest categories to an admin", async () => {
    role = "member";
    renderWithProviders(<AppRoutes />, { route: "/transactions/suggestions" });
    await screen.findByText("Corner Cafe");
    expect(screen.queryByRole("button", { name: /Suggest categories/ })).toBeNull();
  });
});

describe("AI settings", () => {
  it("is linked from Settings for an admin only", async () => {
    const { unmount } = renderWithProviders(<AppRoutes />, { route: "/settings" });
    expect(await screen.findByRole("link", { name: /^AI/ })).toBeTruthy();
    unmount();
    role = "member";
    renderWithProviders(<AppRoutes />, { route: "/settings" });
    await screen.findByRole("link", { name: /Change password/ });
    expect(screen.queryByRole("link", { name: /^AI/ })).toBeNull();
  });

  it("turns AI on and shows the month's cost, the last calls and the prices", async () => {
    renderWithProviders(<AppRoutes />, { route: "/settings/ai" });
    expect(await screen.findByRole("heading", { name: "Claude Off" })).toBeTruthy();
    expect(await screen.findByText("$3.46")).toBeTruthy();
    expect(screen.getByText("91.2%")).toBeTruthy();
    expect(screen.getByText(/Ask \$3\.00, Review \$0\.456/)).toBeTruthy();
    expect(screen.getByText("9,800")).toBeTruthy();
    expect(screen.getByRole("rowheader", { name: "Sonnet 5.5" })).toBeTruthy();
    // Wide tables scroll by keyboard, and say which table they hold.
    expect(screen.getByRole("region", { name: "Last 20 calls" }).getAttribute("tabindex")).toBe("0");
    expect(screen.getByRole("region", { name: "Prices per million tokens" })).toBeTruthy();
    await userEvent.click(screen.getByRole("radio", { name: "On" }));
    await waitFor(() => expect(posts.at(-1)).toEqual({ url: "/api/settings/ai", body: { ...DEFAULT_SETTINGS, enabled: true } }));
    expect(await screen.findByRole("heading", { name: "Claude On" })).toBeTruthy();
  });

  it("saves models and caps, and refuses a bad cap", async () => {
    renderWithProviders(<AppRoutes />, { route: "/settings/ai" });
    const cap = await screen.findByLabelText(/Daily cap/);
    await userEvent.clear(cap);
    await userEvent.type(cap, "lots");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect((await screen.findByRole("alert")).textContent).toContain("Daily cap");
    await userEvent.clear(cap);
    await userEvent.type(cap, "0.01");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: /^Ask/ }), "claude-haiku-5-5");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(posts.at(-1)).toEqual({
        url: "/api/settings/ai",
        body: { ...DEFAULT_SETTINGS, daily_cap_cents: 1, ask_model: "claude-haiku-5-5" },
      }),
    );
  });

  it("sets the trip planner model and names its calls and searches", async () => {
    usage = {
      ...USAGE,
      month_by_feature_micro_usd: { ask: 3_000_000, plan_research: 420_000, plan_write: 0 },
      calls: [{ ...USAGE.calls[0]!, feature: "plan_research", model: "claude-opus-5-5", web_searches: 7 }],
    };
    renderWithProviders(<AppRoutes />, { route: "/settings/ai" });
    expect(await screen.findByText(/Ask \$3\.00, Trip research \$0\.420/)).toBeTruthy();
    expect(screen.getByRole("cell", { name: /Trip research\s*7 searches/ })).toBeTruthy();
    expect(screen.getByText(/web search adds \$10 per 1,000 searches/)).toBeTruthy();
    expect(screen.getByText("Effort: medium for research, high for the plan")).toBeTruthy();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: /^Trip planner/ }), "claude-sonnet-5-5");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(posts.at(-1)).toEqual({ url: "/api/settings/ai", body: { ...DEFAULT_SETTINGS, planner_model: "claude-sonnet-5-5" } }),
    );
  });

  it("writes the review in the background and says when it is ready", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      settings = { ...DEFAULT_SETTINGS, enabled: true };
      runResponse = () => {
        status = { ...ON, running_review: true };
        return json(200, { status: "running", started: true });
      };
      renderWithProviders(<AppRoutes />, { route: "/settings/ai" });
      await userEvent.click(await screen.findByRole("button", { name: "Write the review now" }));
      expect(await screen.findByText(WORKING_TEXT)).toBeTruthy();
      expect(screen.getByRole("button", { name: /Writing the review/ })).toHaveProperty("disabled", true);
      // The header reads the dashboard; it is fetched again once the review is in.
      const dashboards = () => fetchMock.mock.calls.filter(([u]) => u === "/api/dashboard").length;
      const before = dashboards();
      status = { ...ON, last_review_result: { status: "ready", finished_at: "2026-10-22T15:00:00+00:00" } };
      await act(() => vi.advanceTimersByTimeAsync(RUN_POLL_MS));
      expect(await screen.findByText("The review is on the Dashboard.")).toBeTruthy();
      expect(screen.getByRole("button", { name: "Write the review now" })).toHaveProperty("disabled", false);
      await waitFor(() => expect(dashboards()).toBeGreaterThan(before));
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows how a review started before the page opened ended, and refreshes the review", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      settings = { ...DEFAULT_SETTINGS, enabled: true };
      status = { ...ON, running_review: true };
      renderWithProviders(<AppRoutes />, { route: "/settings/ai" });
      expect(await screen.findByText(WORKING_TEXT)).toBeTruthy();
      expect(screen.getByRole("button", { name: /Writing the review/ })).toHaveProperty("disabled", true);
      const dashboards = () => fetchMock.mock.calls.filter(([u]) => u === "/api/dashboard").length;
      const before = dashboards();
      status = {
        ...ON,
        last_review_result: {
          status: "failed",
          message: "Claude did not answer just now. Try again in a minute.",
          finished_at: "2026-10-22T15:00:00+00:00",
        },
      };
      await act(() => vi.advanceTimersByTimeAsync(RUN_POLL_MS));
      expect((await screen.findByRole("alert")).textContent).toBe("Claude did not answer just now. Try again in a minute.");
      expect(screen.getByRole("button", { name: "Write the review now" })).toHaveProperty("disabled", false);
      await waitFor(() => expect(dashboards()).toBeGreaterThan(before));
      expect(posts).toEqual([]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows a quick review result at once", async () => {
    settings = { ...DEFAULT_SETTINGS, enabled: true };
    runResponse = () => json(200, { status: "no_complete_month" });
    renderWithProviders(<AppRoutes />, { route: "/settings/ai" });
    await userEvent.click(await screen.findByRole("button", { name: "Write the review now" }));
    expect(await screen.findByText("The newest update has no complete month to review yet.")).toBeTruthy();
    expect(screen.queryByText(WORKING_TEXT)).toBeNull();
  });

  it("shows one note, not two, when this page's own review fails for good", async () => {
    settings = { ...DEFAULT_SETTINGS, enabled: true };
    runResponse = () => {
      status = { ...ON, review_failed: { month: "2026-08", reason: "schema", tries: null, rewrite: false } };
      return json(200, { status: "failed", reason: "schema" });
    };
    renderWithProviders(<AppRoutes />, { route: "/settings/ai" });
    await userEvent.click(await screen.findByRole("button", { name: "Write the review now" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByText(/could not be written/)).toBeNull();
  });

  it("says when the job gave up on the latest month's review", async () => {
    settings = { ...DEFAULT_SETTINGS, enabled: true };
    status = { ...ON, review_failed: { month: "2026-08", reason: "transient", tries: 3, rewrite: false } };
    renderWithProviders(<AppRoutes />, { route: "/settings/ai" });
    expect(await screen.findByText(/The review for Aug(ust)? 2026 failed after 3 tries\. Write it now to try again\./)).toBeTruthy();
  });

  it("says when the review was not updated after a correction", async () => {
    settings = { ...DEFAULT_SETTINGS, enabled: true };
    status = { ...ON, review_failed: { month: "2026-08", reason: "transient", tries: 3, rewrite: true } };
    renderWithProviders(<AppRoutes />, { route: "/settings/ai" });
    expect(
      await screen.findByText(
        /Updating the review for Aug(ust)? 2026 after a correction failed after 3 tries\. The Dashboard shows the earlier version\. Write it now to try again\./,
      ),
    ).toBeTruthy();
  });

  it("says when the review could not be updated after a correction", async () => {
    settings = { ...DEFAULT_SETTINGS, enabled: true };
    status = { ...ON, review_failed: { month: "2026-08", reason: "schema", tries: null, rewrite: true } };
    renderWithProviders(<AppRoutes />, { route: "/settings/ai" });
    expect(
      await screen.findByText(
        /The review for Aug(ust)? 2026 could not be updated after a correction\. The Dashboard shows the earlier version\. Write it now to try again\./,
      ),
    ).toBeTruthy();
  });

  it("says a review is already being written as a note, not an error", async () => {
    settings = { ...DEFAULT_SETTINGS, enabled: true };
    runResponse = () => json(200, { status: "running", message: "A review is being written now." });
    renderWithProviders(<AppRoutes />, { route: "/settings/ai" });
    await userEvent.click(await screen.findByRole("button", { name: "Write the review now" }));
    const note = await screen.findByText("A review is being written now. It shows on the Dashboard when it is done.");
    expect(note.getAttribute("role")).toBe("status");
  });

  it("shows a billing pause in the server's words, without the Run button", async () => {
    settings = { ...DEFAULT_SETTINGS, enabled: true };
    settingsStatus = PAUSED;
    renderWithProviders(<AppRoutes />, { route: "/settings/ai" });
    expect(await screen.findByText(BILLING_TEXT)).toBeTruthy();
    expect(screen.queryByText(/raise the limit/)).toBeNull();
    expect(screen.getByRole("button", { name: "Clear the pause" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Write the review now" })).toBeNull();
  });

  it("lets a long error class wrap between its words in the calls table", async () => {
    usage = {
      ...USAGE,
      calls: [{ ...USAGE.calls[0]!, stop_reason: null, latency_ms: null, error: "APIConnectionError" }],
    };
    renderWithProviders(<AppRoutes />, { route: "/settings/ai" });
    const cell = await screen.findByText("Failed (APIConnectionError)");
    // API | Connection | Error
    expect(cell.querySelectorAll("wbr")).toHaveLength(2);
  });

  it("tells a member it is admin only", async () => {
    role = "member";
    renderWithProviders(<AppRoutes />, { route: "/settings/ai" });
    expect(await screen.findByText("Only an admin can see AI settings.")).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalledWith("/api/settings/ai", expect.anything());
  });
});
