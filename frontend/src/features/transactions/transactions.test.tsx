import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useLocation } from "react-router";
import { type CategoriesResponse, tripKeys } from "../../lib/api";
import { renderWithProviders } from "../../test/render";
import { NO_TRIPS, TRIPS } from "../travel/fixtures";
import type { OrphanedEdit, Rule, TxnEvent, TxnRow, TxnSearchOut, TxnSummary } from "./api";
import {
  dollarsToCents,
  EMPTY_FILTERS,
  filterCount,
  parseFilters,
  searchBody,
  writeFilters,
} from "./filters";
import { dayBands, eventText, parseTags } from "./model";
import { editSummary, OrphanedEditsCard } from "./OrphanedEdits";
import { previewText } from "./RulePrompt";
import { RulesPage } from "./RulesPage";
import { rangeText } from "./SummaryPanel";
import { TransactionsPage } from "./TransactionsPage";

// Fictional merchants, accounts and amounts only.

const KEY_A = "a".repeat(64);
const KEY_B = "b".repeat(64);
const KEY_C = "c".repeat(64);

function txn(over: Partial<TxnRow>): TxnRow {
  return {
    txn_key: KEY_A,
    txn_date: "2026-09-12",
    post_date: null,
    account_key: "example-card",
    account_name: "Example card",
    institution: "Example Card Co",
    description: "EXAMPLE MARKET 042",
    merchant: "Example Market",
    imported_merchant: "Example Market",
    category_key: "groceries",
    category_name: "Groceries",
    category_emoji: "🛒",
    category_group: "lifestyle",
    category_kind: "spending",
    category_source: "imported",
    rule_id: null,
    imported_category_key: "groceries",
    cardholder: "sam",
    cardholder_label: "Sam",
    owner: "sam",
    owner_label: "Sam",
    amount_cents: -4_210,
    txn_type: "purchase",
    is_transfer: false,
    is_reimbursable: false,
    exclude_from_budget: false,
    hidden: false,
    needs_review: false,
    pre_history: false,
    plan_key: null,
    recurring_key: null,
    trip_name: null,
    trip_key: null,
    import_notes: null,
    notes: null,
    tags: [],
    has_override: false,
    overridden: [],
    ...over,
  };
}

const ROWS: TxnRow[] = [
  txn({}),
  txn({
    txn_key: KEY_B,
    merchant: "Example Bistro",
    imported_merchant: "Example Bistro",
    description: "EXAMPLE BISTRO",
    category_key: "dining",
    category_name: "Dining",
    category_emoji: "🍽️",
    amount_cents: -2_500,
    has_override: true,
    overridden: ["category_key"],
    category_source: "override",
  }),
  txn({
    txn_key: KEY_C,
    txn_date: "2026-09-10",
    merchant: null,
    imported_merchant: null,
    description: "PAYROLL DEPOSIT",
    category_key: "paycheck",
    category_name: "Paycheck",
    category_emoji: "💰",
    category_kind: "income",
    account_name: "Example checking",
    cardholder: null,
    cardholder_label: null,
    owner_label: "Joint",
    amount_cents: 210_000,
  }),
];

const TRIP_ROWS: TxnRow[] = [
  txn({ txn_key: "d".repeat(64), merchant: "Example Lodge", description: "EXAMPLE LODGE", trip_key: "lake-weekend", trip_name: "Lake weekend" }),
  txn({ txn_key: "e".repeat(64), txn_date: "2026-01-09", merchant: "Example Fuel", description: "EXAMPLE FUEL", trip_key: "lake-weekend", trip_name: "Lake weekend" }),
];

const SUMMARY: TxnSummary = {
  count: 3,
  total_cents: 203_290,
  income_cents: 210_000,
  spending_cents: 6_710,
  average_cents: 67_763,
  largest: { txn_key: KEY_C, txn_date: "2026-09-10", merchant: null, description: "PAYROLL DEPOSIT", amount_cents: 210_000 },
  first_date: "2026-09-10",
  last_date: "2026-09-12",
};

const SEARCH: TxnSearchOut = {
  rows: ROWS,
  next_cursor: null,
  days: [
    { date: "2026-09-12", total_cents: -6_710, count: 2 },
    { date: "2026-09-10", total_cents: 210_000, count: 1 },
  ],
  summary: SUMMARY,
};

const CATEGORIES: CategoriesResponse = {
  groups: [
    {
      group_key: "lifestyle",
      name: "Lifestyle",
      categories: [
        { category_key: "groceries", name: "Groceries", emoji: "🛒", kind: "spending" },
        { category_key: "dining", name: "Dining", emoji: "🍽️", kind: "spending" },
      ],
    },
    {
      group_key: "income",
      name: "Income",
      categories: [{ category_key: "paycheck", name: "Paycheck", emoji: "💰", kind: "income" }],
    },
  ],
};

const EVENTS: TxnEvent[] = [
  { id: null, at: "2026-10-06T15:00:00Z", kind: "imported", via: "import", from_value: null, to_value: null, user: null, rule_id: null, prev_txn_key: null, run_id: "2026-10-06" },
  { id: 4, at: "2026-10-07T15:00:00Z", kind: "category_changed", via: "edit", from_value: "groceries", to_value: "dining", user: "Sam", rule_id: null, prev_txn_key: null, run_id: null },
];

const RULE: Rule = {
  id: 7,
  match_kind: "merchant_equals",
  pattern: "Example Market",
  category_key: "dining",
  category_name: "Dining",
  category_emoji: "🍽️",
  active: true,
  applied_count: 4,
  created_from_txn_key: KEY_A,
  created_by: "Sam",
  created_at: "2026-10-07T15:00:00Z",
  updated_at: "2026-10-07T15:00:00Z",
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

interface Call {
  method: string;
  url: string;
  body: Record<string, unknown> | null;
}

let calls: Call[] = [];
let orphans: OrphanedEdit[] = [];
let role: "admin" | "member" = "admin";
let tripsResponse: () => Response | Promise<Response> = () => json(TRIPS);
let patchResponse: (body: Record<string, unknown>) => Response = (body) =>
  json({ ...ROWS[0], ...body, has_override: true, overridden: Object.keys(body), category_source: "override", category_name: "Dining", category_emoji: "🍽️" });

const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const method = init?.method ?? "GET";
  const body = typeof init?.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : null;
  calls.push({ method, url, body });
  if (url === "/api/auth/me") return json({ username: "sam", display_name: "Sam", role });
  if (url === "/api/categories") return json(CATEGORIES);
  if (url.startsWith("/api/accounts")) return json({ groups: [], summary: null, notes: null });
  if (url === "/api/trips") return tripsResponse();
  if (url === "/api/transactions/search") {
    if (body?.needs_review || body?.uncategorized) return json({ ...SEARCH, rows: [], days: [], summary: { ...SUMMARY, count: 0 } });
    if (body?.trip_key && body.cursor === "page-2") return json({ ...SEARCH, rows: [TRIP_ROWS[1]], days: [], next_cursor: null, summary: null });
    if (body?.trip_key) return json({ ...SEARCH, rows: [TRIP_ROWS[0]], next_cursor: "page-2", summary: { ...SUMMARY, count: 2 } });
    return json(SEARCH);
  }
  if (url.endsWith("/events")) return json({ txn_key: KEY_A, events: EVENTS });
  if (url === `/api/transactions/${KEY_A}` && method === "PATCH") return patchResponse(body ?? {});
  if (url === "/api/transactions/bulk") return json({ count: 2, updated: 2 });
  if (url === "/api/rules/preview") return json({ matches: 5, edited: 1, would_change: 4 });
  if (url === "/api/rules" && method === "POST") return json({ ...RULE, ...body, applied_count: 4 }, 201);
  if (url === "/api/rules") return json({ rules: [RULE] });
  if (url.startsWith("/api/rules/") && method === "PATCH") return json({ ...RULE, ...body });
  if (url === "/api/transactions/orphaned-edits") return json({ rows: orphans });
  return json({ detail: "Not found." }, 404);
});

beforeEach(() => {
  calls = [];
  orphans = [];
  role = "admin";
  tripsResponse = () => json(TRIPS);
  patchResponse = (body) =>
    json({
      ...ROWS[0],
      ...body,
      has_override: true,
      overridden: Object.keys(body),
      category_source: "override",
      category_name: "Dining",
      category_emoji: "🍽️",
    });
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function searches(): Record<string, unknown>[] {
  return calls.filter((c) => c.url === "/api/transactions/search" && c.body && !("needs_review" in c.body) && !("uncategorized" in c.body)).map((c) => c.body!);
}

function LocationProbe() {
  const loc = useLocation();
  return <output data-testid="location">{loc.search}</output>;
}

function renderPage(route = "/transactions") {
  return renderWithProviders(
    <>
      <TransactionsPage />
      <LocationProbe />
    </>,
    { route },
  );
}

// ---------- Pure helpers ----------

describe("filters in the URL", () => {
  it("round-trips every filter and leaves defaults out", () => {
    const state = {
      ...EMPTY_FILTERS,
      accounts: ["example-card", "example-checking"],
      categories: ["groceries"],
      owners: ["sam"],
      months: ["2026-09"],
      from: "2026-09-01",
      to: "2026-09-30",
      min: "10",
      max: "250.5",
      direction: "out" as const,
      transfer: "no" as const,
      reimbursable: "yes" as const,
      edited: "yes" as const,
      hidden: "include" as const,
      trip: "lake-weekend",
      view: "review" as const,
      sort: "amount_desc" as const,
    };
    const params = writeFilters(state);
    expect(parseFilters(params)).toEqual(state);
    expect(params.get("trip")).toBe("lake-weekend");
    expect(writeFilters(EMPTY_FILTERS).toString()).toBe("");
    expect(filterCount(state)).toBe(15);
    expect(searchBody(state, "").trip_key).toBe("lake-weekend");
    expect(parseFilters(new URLSearchParams("trip=Lake Weekend")).trip).toBeNull();
  });

  it("drops values that do not look right, so an old link never fails", () => {
    const s = parseFilters(new URLSearchParams("account=Bad Key&month=2026-13&from=yesterday&min=-5&sort=random&view=x&account=ok-key"));
    expect(s.accounts).toEqual(["ok-key"]);
    expect(s.months).toEqual([]);
    expect(s.from).toBeNull();
    expect(s.min).toBeNull();
    expect(s.sort).toBe("date_desc");
    expect(s.view).toBe("all");
  });

  it("keeps search text out of the URL and puts it in the POST body", () => {
    const params = writeFilters({ ...EMPTY_FILTERS, accounts: ["example-card"] });
    expect(params.toString()).not.toContain("q=");
    const body = searchBody({ ...EMPTY_FILTERS, accounts: ["example-card"], min: "12.5", transfer: "no", view: "uncategorized" }, "  market ");
    expect(body).toEqual({
      sort: "date_desc",
      hidden: "exclude",
      q: "market",
      accounts: ["example-card"],
      amount_min_cents: 1_250,
      is_transfer: false,
      uncategorized: true,
    });
  });

  it("turns dollars into cents with integer math", () => {
    expect(dollarsToCents("0.1")).toBe(10);
    expect(dollarsToCents("19.99")).toBe(1_999);
    expect(dollarsToCents("1,000")).toBeNull();
  });
});

describe("model", () => {
  it("bands rows by date with the server's day totals", () => {
    const bands = dayBands(ROWS, SEARCH.days, true);
    expect(bands.map((b) => [b.date, b.rows.length, b.totalCents])).toEqual([
      ["2026-09-12", 2, -6_710],
      ["2026-09-10", 1, 210_000],
    ]);
    expect(dayBands(ROWS, SEARCH.days, false)).toHaveLength(1);
  });

  it("writes activity in plain words and never shows note text", () => {
    const name = (k: string) => (k === "dining" ? "Dining" : "Groceries");
    expect(eventText(EVENTS[1]!, name)).toBe("Category changed from Groceries to Dining");
    expect(eventText({ ...EVENTS[1]!, via: "rule" }, name)).toBe("A rule changed the category from Groceries to Dining");
    expect(eventText({ ...EVENTS[1]!, via: "suggestion" }, name)).toBe("Category suggestion approved from Groceries to Dining");
    expect(eventText({ ...EVENTS[1]!, via: "suggestion", from_value: null }, name)).toBe("Category suggestion approved to Dining");
    expect(eventText({ ...EVENTS[1]!, kind: "transfer_changed", to_value: "true" }, name)).toBe("Marked as a transfer");
    expect(eventText({ ...EVENTS[1]!, kind: "note_changed", from_value: null, to_value: null }, name)).toBe("Note changed");
    expect(eventText({ ...EVENTS[1]!, kind: "tags_changed", to_value: '["trip","gift"]' }, name)).toBe("Tags changed");
    expect(eventText({ ...EVENTS[1]!, kind: "rekeyed" }, name)).toContain("moved here");
  });

  it("cleans typed tags", () => {
    expect(parseTags(" trip, Trip ,gift,, ")).toEqual(["trip", "gift"]);
  });

  it("says how many rows a rule matches", () => {
    expect(previewText({ matches: 5, would_change: 4, edited: 1 })).toBe(
      "Matches 5 transactions. 4 would change. 1 transaction has its own edit and keeps it.",
    );
    expect(previewText({ matches: 0, would_change: 0, edited: 0 })).toBe("Matches 0 transactions.");
  });

  it("shows a summary range in short words", () => {
    expect(rangeText("2026-09-10", "2026-09-12")).toBe("Sep 10 to Sep 12, 2026");
    expect(rangeText("2025-12-30", "2026-01-02")).toBe("Dec 30, 2025 to Jan 2, 2026");
  });
});

// ---------- Page ----------

describe("TransactionsPage", () => {
  it("shows date bands with day totals, the columns and the Summary", async () => {
    renderPage();
    const band = await screen.findByRole("region", { name: "Sep 12, 2026" });
    expect(within(band).getByRole("heading").textContent).toContain("−$67.10");
    const market = within(band).getByRole("button", { name: /Example Market/ });
    expect(market.textContent).toContain("Groceries");
    expect(market.textContent).toContain("🛒");
    expect(market.textContent).toContain("Example card");
    expect(market.textContent).toContain("Sam");
    expect(market.textContent).toContain("−$42.10");
    // Edited rows carry a marker, and phones say it in words under the merchant.
    const bistro = within(band).getByRole("button", { name: /Example Bistro/ });
    expect(bistro.textContent).toContain("Edited");
    expect(within(bistro).getByText("🍽️ Dining · Example card · Edited")).toBeTruthy();
    expect(market.textContent).not.toContain("Edited");
    // Income is a green "+".
    const pay = screen.getByRole("button", { name: /PAYROLL DEPOSIT/ });
    expect(pay.querySelector(".text-good")?.textContent).toContain("+$2,100.00");

    const summary = screen.getByRole("region", { name: "Summary" });
    expect(summary.textContent).toContain("Total transactions3");
    expect(summary.textContent).toContain("$67.10");
    expect(summary.textContent).toContain("+$2,100.00");
    expect(summary.textContent).toContain("+$677.63");
  });

  it("sends search text in the POST body only, never the URL", async () => {
    const user = userEvent.setup();
    renderPage("/transactions?account=example-card");
    await screen.findByRole("region", { name: "Sep 12, 2026" });
    await user.type(screen.getByRole("searchbox", { name: /^Search transactions/ }), "market");
    await waitFor(() => expect(searches().some((b) => b.q === "market")).toBe(true));
    const last = searches().find((b) => b.q === "market")!;
    expect(last.accounts).toEqual(["example-card"]);
    expect(calls.every((c) => !c.url.includes("market"))).toBe(true);
    expect(screen.getByTestId("location").textContent).toBe("?account=example-card");
  });

  it("filters from the popover go to the URL and the request, with removable chips", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("region", { name: "Sep 12, 2026" });
    await user.click(screen.getByRole("button", { name: /Filters/ }));
    const dialog = screen.getByRole("dialog", { name: "Filters" });
    await user.click(within(dialog).getByRole("button", { name: "Categories" }));
    await user.click(within(dialog).getByRole("checkbox", { name: /Dining/ }));
    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("?category=dining"));
    await waitFor(() => expect(searches().some((b) => JSON.stringify(b.categories) === '["dining"]')).toBe(true));
    // Esc closes and returns focus to the button.
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Filters" })).toBeNull();
    expect(document.activeElement?.textContent).toContain("Filters");
    // The chip removes the filter.
    const chips = screen.getByRole("list", { name: "Filters in use" });
    await user.click(within(chips).getByRole("button", { name: /Dining/ }));
    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe(""));
  });

  it("filters by trip from the link, with the trip's name on the chip and every page", async () => {
    const user = userEvent.setup();
    renderPage("/transactions?trip=lake-weekend");
    await screen.findByRole("button", { name: /Example Lodge/ });
    const chips = screen.getByRole("list", { name: "Filters in use" });
    expect(await within(chips).findByRole("button", { name: /Trip: Lake weekend/ })).toBeTruthy();
    expect(searches()[0]).toMatchObject({ trip_key: "lake-weekend" });
    await user.click(screen.getByRole("button", { name: "Load more" }));
    await screen.findByRole("button", { name: /Example Fuel/ });
    const paged = searches().find((b) => b.cursor === "page-2");
    expect(paged).toMatchObject({ trip_key: "lake-weekend", cursor: "page-2" });
    expect(screen.getByText("Showing 2 of 2")).toBeTruthy();
    // Removing the chip drops the filter and asks again without it.
    await user.click(within(chips).getByRole("button", { name: /Trip: Lake weekend/ }));
    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe(""));
    await waitFor(() => expect(searches().some((b) => !("trip_key" in b))).toBe(true));
  });

  it("names a trip the app does not have plainly", async () => {
    const user = userEvent.setup();
    renderPage("/transactions?trip=day-trip-sep26");
    const chips = await screen.findByRole("list", { name: "Filters in use" });
    expect(await within(chips).findByRole("button", { name: /Trip: day-trip-sep26 \(not in Travel\)/ })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /Filters/ }));
    const dialog = screen.getByRole("dialog", { name: "Filters" });
    await user.click(within(dialog).getByRole("button", { name: /^Trips/ }));
    expect(within(dialog).getByRole("radio", { name: "day-trip-sep26 (not in Travel)" })).toHaveProperty("checked", true);
    expect(within(dialog).getByRole("radio", { name: "Lake weekend" })).toBeTruthy();
    expect(within(dialog).queryByText("No trips yet.")).toBeNull();
  });

  it("says there are no trips only once Travel has answered with none", async () => {
    const user = userEvent.setup();
    tripsResponse = () => json(NO_TRIPS);
    renderPage("/transactions?trip=day-trip-sep26");
    const chips = await screen.findByRole("list", { name: "Filters in use" });
    expect(await within(chips).findByRole("button", { name: /Trip: day-trip-sep26 \(not in Travel\)/ })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /Filters/ }));
    const dialog = screen.getByRole("dialog", { name: "Filters" });
    await user.click(within(dialog).getByRole("button", { name: /^Trips/ }));
    expect(within(dialog).getByText("No trips yet.")).toBeTruthy();
  });

  it("does not call a trip missing while Travel's list is still loading", async () => {
    const user = userEvent.setup();
    let answer: (r: Response) => void = () => undefined;
    tripsResponse = () => new Promise<Response>((resolve) => (answer = resolve));
    const { queryClient } = renderPage("/transactions?trip=lake-weekend");
    const chips = await screen.findByRole("list", { name: "Filters in use" });
    // Before any row is in, the chip shows the key, with no claim about Travel.
    expect(within(chips).getByRole("button", { name: /^Trip: lake-weekend/ }).textContent).not.toContain("not in Travel");
    // A loaded row names the trip.
    await screen.findByRole("button", { name: /Example Lodge/ });
    expect(within(chips).getByRole("button", { name: /Trip: Lake weekend/ }).textContent).not.toContain("not in Travel");
    expect(queryClient.getQueryState(tripKeys.page)?.status).toBe("pending");
    await user.click(screen.getByRole("button", { name: /Filters/ }));
    const dialog = screen.getByRole("dialog", { name: "Filters" });
    await user.click(within(dialog).getByRole("button", { name: /^Trips/ }));
    expect(within(dialog).getByRole("radio", { name: "Lake weekend" })).toHaveProperty("checked", true);
    expect(within(dialog).queryByText(/not in Travel/)).toBeNull();
    expect(within(dialog).queryByText("No trips yet.")).toBeNull();
    // Once Travel answers, its own name is used.
    answer(json(TRIPS));
    await within(dialog).findByRole("radio", { name: "Ski weekend" });
    expect(within(chips).getByRole("button", { name: /Trip: Lake weekend/ }).textContent).not.toContain("not in Travel");
  });

  it("does not call a trip missing when Travel's list fails to load", async () => {
    const user = userEvent.setup();
    tripsResponse = () => json({ detail: "Something went wrong." }, 500);
    const { queryClient } = renderPage("/transactions?trip=lake-weekend");
    await screen.findByRole("button", { name: /Example Lodge/ });
    await waitFor(() => expect(queryClient.getQueryState(tripKeys.page)?.status).toBe("error"));
    const chips = screen.getByRole("list", { name: "Filters in use" });
    expect(within(chips).getByRole("button", { name: /Trip: Lake weekend/ }).textContent).not.toContain("not in Travel");
    await user.click(screen.getByRole("button", { name: /Filters/ }));
    const dialog = screen.getByRole("dialog", { name: "Filters" });
    await user.click(within(dialog).getByRole("button", { name: /^Trips/ }));
    expect(within(dialog).getByRole("radio", { name: "Lake weekend" })).toHaveProperty("checked", true);
    expect(within(dialog).queryByText(/not in Travel/)).toBeNull();
    expect(within(dialog).queryByText("No trips yet.")).toBeNull();
  });

  it("keeps the open filter section in view on the phone's sideways bar", async () => {
    // jsdom has no layout: a 340px bar with 600px of sections, Trips at the end.
    const spies = [
      vi.spyOn(Element.prototype, "scrollWidth", "get").mockImplementation(function (this: Element) {
        return this.tagName === "UL" ? 600 : 0;
      }),
      vi.spyOn(Element.prototype, "clientWidth", "get").mockImplementation(function (this: Element) {
        return this.tagName === "UL" ? 340 : 0;
      }),
      vi.spyOn(HTMLElement.prototype, "offsetLeft", "get").mockImplementation(function (this: HTMLElement) {
        return this.textContent === "Trips" ? 520 : 0;
      }),
      vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockImplementation(function (this: HTMLElement) {
        return this.textContent === "Trips" ? 70 : 0;
      }),
    ];
    try {
      const user = userEvent.setup();
      renderPage();
      await screen.findByRole("region", { name: "Sep 12, 2026" });
      await user.click(screen.getByRole("button", { name: /Filters/ }));
      await user.click(within(screen.getByRole("dialog", { name: "Filters" })).getByRole("button", { name: "Trips" }));
      await user.click(screen.getByRole("button", { name: "Done" }));
      // Reopened, the bar starts over at the left. Trips still shows.
      await user.click(screen.getByRole("button", { name: /Filters/ }));
      const nav = within(screen.getByRole("dialog", { name: "Filters" })).getByRole("navigation", { name: "Filter sections" });
      expect(within(nav).getByRole("list").scrollLeft).toBe(520 + 70 - 340 + 16);
    } finally {
      for (const spy of spies) spy.mockRestore();
    }
  });

  it("picks a trip in the Filters popover", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("region", { name: "Sep 12, 2026" });
    await user.click(screen.getByRole("button", { name: /Filters/ }));
    const dialog = screen.getByRole("dialog", { name: "Filters" });
    await user.click(within(dialog).getByRole("button", { name: "Trips" }));
    await user.click(await within(dialog).findByRole("radio", { name: "Ski weekend" }));
    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("?trip=ski-weekend"));
    await waitFor(() => expect(searches().some((b) => b.trip_key === "ski-weekend")).toBe(true));
  });

  it("shows the trip chip in the row detail, linking to Travel", async () => {
    const user = userEvent.setup();
    renderPage("/transactions?trip=lake-weekend");
    await user.click(await screen.findByRole("button", { name: /Example Lodge/ }));
    const dialog = await screen.findByRole("dialog");
    const link = within(dialog).getByRole("link", { name: /Trip: Lake weekend/ });
    expect(link.getAttribute("href")).toBe("/travel?trip=lake-weekend");
  });

  it("review queue views ask for needs review or uncategorized rows", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("region", { name: "Sep 12, 2026" });
    await user.click(screen.getByRole("radio", { name: /Review/ }));
    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("?view=review"));
    await waitFor(() =>
      expect(calls.some((c) => c.url === "/api/transactions/search" && c.body?.needs_review === true && c.body?.limit !== 1)).toBe(true),
    );
    expect(await screen.findByText("Nothing waiting here.")).toBeTruthy();
  });

  it("edits a category, then offers a rule and creates it", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: /Example Market/ }));
    const drawer = await screen.findByRole("dialog", { name: "Example Market" });
    expect(within(drawer).getByText("EXAMPLE MARKET 042")).toBeTruthy();
    await user.selectOptions(within(drawer).getByLabelText("Category"), "dining");
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ category_key: "dining" });

    const prompt = await within(drawer).findByRole("region", { name: "Create rule?" });
    expect(within(prompt).getByRole("radio", { name: /Merchant is “Example Market”/ })).toHaveProperty("checked", true);
    await within(prompt).findByText(/Matches 5 transactions/);
    await user.click(within(prompt).getByRole("button", { name: "Create rule" }));
    await within(drawer).findByText("Rule created.");
    expect(calls.find((c) => c.url === "/api/rules" && c.method === "POST")?.body).toEqual({
      match_kind: "merchant_equals",
      pattern: "Example Market",
      category_key: "dining",
      created_from_txn_key: KEY_A,
    });
    // The activity timeline.
    expect(within(drawer).getByText("Category changed from 🛒 Groceries to 🍽️ Dining")).toBeTruthy();
  });

  it("switches save at once and can go back to the imported value", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: /Example Market/ }));
    const drawer = await screen.findByRole("dialog", { name: "Example Market" });
    await user.click(within(drawer).getByRole("switch", { name: /Transfer/ }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH").at(-1)?.body).toEqual({ is_transfer: true }));
    await user.click(await within(drawer).findByRole("button", { name: /Use imported value for Transfer/ }));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH").at(-1)?.body).toEqual({ is_transfer: null }));
  });

  it("the whole switch row flips the switch", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: /Example Market/ }));
    const drawer = await screen.findByRole("dialog", { name: "Example Market" });
    // The label stretches to the 44px row instead of sitting in its middle.
    const label = within(drawer).getByRole("switch", { name: /Reimbursable/ }).closest("label")!;
    expect(label.parentElement?.className).toContain("items-stretch");
    await user.click(within(label).getByText("Someone will pay this back."));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH").at(-1)?.body).toEqual({ is_reimbursable: true }));
  });

  it("says in words why tags cannot be saved", async () => {
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: /Example Market/ }));
    const drawer = await screen.findByRole("dialog", { name: "Example Market" });
    const tags = within(drawer).getByLabelText("Tags");
    expect(within(drawer).getByText("Separate tags with commas. Up to 10, each up to 32 letters.")).toBeTruthy();
    await user.type(tags, "a, b, c, d, e, f, g, h, i, j, k");
    expect(within(drawer).getByText("Too many tags: 11 of 10.")).toBeTruthy();
    expect(within(drawer).getByRole("button", { name: "Save" })).toHaveProperty("disabled", true);
    await user.clear(tags);
    await user.type(tags, "x".repeat(33));
    expect(within(drawer).getByText("A tag is over 32 letters.")).toBeTruthy();
    expect(tags.getAttribute("aria-invalid")).toBe("true");
  });

  it("shows the server's 422 message plainly and keeps the typed note", async () => {
    const message = "The note looks like it holds an account or card number, a phone number, an email, an address or a private name. Remove that part and try again.";
    patchResponse = () => json({ detail: message }, 422);
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: /Example Market/ }));
    const drawer = await screen.findByRole("dialog", { name: "Example Market" });
    await user.type(within(drawer).getByLabelText("Notes"), "call the example desk");
    await user.type(within(drawer).getByLabelText("Tags"), "trip, Trip, gift");
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    expect((await within(drawer).findByRole("alert")).textContent).toBe(message);
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ notes: "call the example desk", tags: ["trip", "gift"] });
    expect((within(drawer).getByLabelText("Notes") as HTMLTextAreaElement).value).toBe("call the example desk");
  });

  it("Edit multiple selects rows and sends one bulk edit", async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("region", { name: "Sep 12, 2026" });
    // Icon only on phones, so the buttons hold a 44px width.
    expect(screen.getByRole("button", { name: "Edit multiple" }).className).toContain("min-w-11");
    expect(screen.getByRole("button", { name: /^Sort: / }).className).toContain("min-w-11");
    await user.click(screen.getByRole("button", { name: "Edit multiple" }));
    await user.click(screen.getByRole("checkbox", { name: /Select Example Market/ }));
    await user.click(screen.getByRole("checkbox", { name: /Select Example Bistro/ }));
    const bar = screen.getByRole("region", { name: "Edit selected transactions" });
    expect(bar.textContent).toContain("2 selected");
    await user.selectOptions(within(bar).getByLabelText("Category for selected"), "dining");
    await user.click(within(bar).getByRole("button", { name: "Apply" }));
    await screen.findByText("Updated 2 of 2 transactions.");
    expect(calls.find((c) => c.url === "/api/transactions/bulk")?.body).toEqual({
      txn_keys: [KEY_A, KEY_B],
      changes: { category_key: "dining" },
    });
  });
});

// ---------- Rules and orphaned edits ----------

describe("RulesPage", () => {
  it("lists rules, turns one off, and lets only an admin delete", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RulesPage />);
    expect(await screen.findByText("Merchant is “Example Market”")).toBeTruthy();
    expect(screen.getByText(/4 transactions now/)).toBeTruthy();
    await user.click(screen.getByRole("switch", { name: "Active" }));
    await waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ active: false }));
    expect(await screen.findByRole("button", { name: /Delete/ })).toBeTruthy();
  });

  it("hides Delete from members", async () => {
    role = "member";
    renderWithProviders(<RulesPage />);
    await screen.findByText("Merchant is “Example Market”");
    await screen.findByText(/Only an admin can delete a rule/);
    expect(screen.queryByRole("button", { name: /Delete/ })).toBeNull();
  });

  it("checks a new rule before sending it", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RulesPage />);
    const form = await screen.findByRole("form", { name: "New rule" });
    await user.selectOptions(within(form).getByLabelText("Match"), "description_contains");
    await user.type(within(form).getByLabelText("Text"), "ab");
    await user.click(within(form).getByRole("button", { name: "Add rule" }));
    expect(screen.getByText("Use at least 3 letters for statement text.")).toBeTruthy();
    expect(calls.some((c) => c.url === "/api/rules" && c.method === "POST")).toBe(false);
  });
});

describe("Orphaned edits", () => {
  const orphan: OrphanedEdit = {
    txn_key: KEY_A,
    txn_date: "2026-08-02",
    amount_cents: -1_999,
    desc_norm: "EXAMPLE ATM",
    imported_merchant: null,
    account_key: "example-checking",
    account_name: "Example checking",
    statement_key: "example-checking:2026-08-31",
    removed_at: "2026-10-14T12:00:00Z",
    orphaned_at: "2026-10-14T12:00:00Z",
    override: {
      category_key: "dining",
      merchant: null,
      is_transfer: true,
      is_reimbursable: null,
      exclude_from_budget: null,
      hidden: null,
      notes: "private words",
      tags: ["trip"],
    },
    updated_at: "2026-10-07T12:00:00Z",
    updated_by: "Sam",
  };

  it("says what the edit set without the note text", () => {
    expect(editSummary(orphan.override, () => "Dining")).toBe("Category Dining · Transfer · Note · 1 tag");
  });

  it("lists rows under Statements", async () => {
    orphans = [orphan];
    renderWithProviders(<OrphanedEditsCard />);
    const table = await screen.findByRole("table");
    // The wide table scrolls in a labeled region that takes keyboard focus.
    const scroller = table.closest('[role="region"]');
    expect(scroller?.getAttribute("aria-label")).toBe("Edits that lost their transaction");
    expect(scroller?.getAttribute("tabindex")).toBe("0");
    expect(table.textContent).toContain("EXAMPLE ATM");
    expect(table.textContent).toContain("−$19.99");
    expect(table.textContent).not.toContain("private words");
  });
});
