// Rewards inputs (docs/plans/rewards-inputs.md chunk D2): the This month
// strip, Card credits to review, the cycle line, match words and cycle marks.
// Made-up numbers only.
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RewardBenefit, RewardCycle, RewardsResponse, RewardsReview, RewardTerms, TermPreview } from "../../lib/api";
import { renderWithProviders } from "../../test/render";
import { previewLine } from "./BenefitDrawer";
import { NO_REWARDS, REVIEW, REVIEW_DONE, REWARDS, TERMS, txnKey } from "./fixtures";
import {
  balancesToUpdate,
  cardCredits,
  cycleNotes,
  guessText,
  markUsedOn,
  startedCycles,
  TERM_RULE,
  termProblem,
  withoutChatOnly,
  withoutRow,
} from "./model";
import { RewardsPage } from "./RewardsPage";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Reply = (body: unknown) => Response | Promise<Response>;

let payload: RewardsResponse = REWARDS;
let review: RewardsReview = REVIEW;
let terms: RewardTerms = TERMS;
let role: "admin" | "member" = "admin";
let replies: Record<string, Reply> = {};

const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const method = init?.method ?? "GET";
  if (url === "/api/auth/me") return json(200, { username: "alex", display_name: "Alex", role });
  const reply = replies[`${method} ${url}`];
  if (reply) return reply(init?.body ? JSON.parse(String(init.body)) : undefined);
  if (url === "/api/rewards" && method === "GET") return json(200, payload);
  if (url === "/api/rewards/review" && method === "GET") return json(200, review);
  if (url === "/api/rewards/benefits/dining-credit/terms" && method === "GET") return json(200, terms);
  return json(404, { detail: "Not found." });
});

let wide = false;
const realMatchMedia = window.matchMedia;

beforeEach(() => {
  payload = REWARDS;
  review = REVIEW;
  terms = TERMS;
  role = "admin";
  replies = {};
  wide = false;
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
  // Phones by default, like jsdom. A test sets `wide` for the desktop list.
  window.matchMedia = (query: string) => ({ ...realMatchMedia(query), matches: wide && query === "(min-width: 768px)" });
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.matchMedia = realMatchMedia;
});

const REVIEW_LINK = "/rewards?tab=benefits&review=credits";

function page(): RewardsResponse {
  return structuredClone(REWARDS);
}

function benefit(key: string, from: RewardsResponse = REWARDS): RewardBenefit {
  const b = from.benefits.find((x) => x.key === key);
  if (!b) throw new Error(key);
  return b;
}

function cycle(over: Partial<RewardCycle> = {}): RewardCycle {
  return { ...benefit("dining-credit").cycle!, ...over };
}

function calls(method: string, url: string) {
  return fetchMock.mock.calls.filter(([u, init]) => String(u) === url && (init?.method ?? "GET") === method);
}

function sentBody(method: string, url: string): unknown {
  const call = calls(method, url).at(-1);
  if (!call) throw new Error(`${method} ${url} was not sent`);
  return JSON.parse(String(call[1]!.body));
}

function invalidated(spy: { mock: { calls: unknown[][] } }): string[] {
  return spy.mock.calls.map((c) => ((c[0] as { queryKey: string[] }).queryKey ?? [])[0]!);
}

const credit = (c: string) => `/api/rewards/credits/${txnKey(c)}`;

async function openReview(): Promise<HTMLElement> {
  return screen.findByRole("dialog", { name: "Card credits to review" });
}

describe("review and cycle helpers", () => {
  it("counts the balances to update this month", () => {
    const today = "2026-10-20";
    const progs = page().programs;
    expect(balancesToUpdate(progs, today)).toBe(0); // the chat's from this month, statements, a timeshare
    const harbor = progs.find((p) => p.key === "harbor-miles")!;
    harbor.as_of = "2026-09-28"; // given to the chat last month: no statement refreshes it
    expect(balancesToUpdate(progs, today)).toBe(1);
    harbor.source = "app";
    expect(balancesToUpdate(progs, today)).toBe(1);
    const summit = progs.find((p) => p.key === "summit-points")!;
    summit.as_of = "2026-06-30"; // a statement balance counts only once stale
    expect(balancesToUpdate(progs, today)).toBe(1);
    summit.stale = true;
    expect(balancesToUpdate(progs, today)).toBe(2);
    progs.find((p) => p.key === "pine-cash-back")!.balance_micro = null;
    expect(balancesToUpdate(progs, today)).toBe(3);
  });

  it("words a cycle's source and notes, one source at a time", () => {
    expect(cycleNotes(cycle({ source: "set", used_cents: 0 }), true)).toEqual(["Set in the app"]);
    expect(cycleNotes(cycle({ source: "rows", used_cents: 1_000, chat_used_cents: 1_000 }), true)).toEqual(["From your statements"]);
    expect(cycleNotes(cycle({ source: "rows", used_cents: 600, chat_used_cents: 1_000 }), true)).toEqual([
      "From your statements",
      "The chat says $10.00",
    ]);
    // The chat's own number never says "The chat says".
    expect(cycleNotes(cycle({ source: "chat", used_cents: 600, chat_used_cents: 600 }), true)).toEqual(["From the chat"]);
    expect(cycleNotes(cycle({ source: "set", used_cents: 1_000, chat_used_cents: 600 }), true)).toEqual(["Set in the app"]);
    // Not covered and nothing used: waiting, on a credit the app matches only.
    expect(cycleNotes(cycle({ source: null, covered: false }), true)).toEqual(["Waiting for rows"]);
    expect(cycleNotes(cycle({ source: null, covered: false }), false)).toEqual([]);
    expect(cycleNotes(cycle({ source: null, covered: true }), true)).toEqual([]);
    expect(cycleNotes(cycle({ source: "chat", covered: false, used_cents: 1_000, chat_used_cents: 1_000 }), true)).toEqual(["From the chat"]);
    expect(cycleNotes(cycle({ source: "rows", used_cents: 1_500, chat_used_cents: null, over_cents: 500 }), true)).toEqual([
      "From your statements",
      "More than the credit by $5.00",
    ]);
  });

  it("lists started cycles newest first and marks used on a day that has come", () => {
    const dining = benefit("dining-credit");
    expect(startedCycles(dining, "2026-10-20").map((c) => c.start)).toEqual(["2026-10-01", "2026-09-01", "2026-08-01"]);
    // A cycle that starts later is not one to mark.
    expect(startedCycles({ ...dining, cycle: cycle({ start: "2026-11-01", end: "2026-11-30" }) }, "2026-10-20").map((c) => c.start)).toEqual([
      "2026-09-01",
      "2026-08-01",
    ]);
    expect(markUsedOn({ end: "2026-09-30" }, "2026-10-20")).toBe("2026-09-30");
    expect(markUsedOn({ end: "2026-10-31" }, "2026-10-20")).toBe("2026-10-20");
  });

  it("words the guess and the preview, and checks a typed word like the API", () => {
    expect(guessText({ benefit_key: "dining-credit", benefit_name: "Dining credit", cycle_start: "2026-09-01" })).toBe("Looks like Dining credit, Sep");
    const row = (now_key: string | null, after_key: string | null, tie = false): TermPreview["rows"][number] => ({
      txn_key: txnKey("p"),
      date: "2026-08-12",
      merchant: null,
      description: "DINING CREDIT",
      cents: 1_000,
      now_key,
      now_name: null,
      after_key,
      after_name: null,
      tie,
    });
    const change = { benefit_key: "dining-credit", benefit_name: "Dining credit", start: "2026-08-01", used_cents_before: 1_000, used_cents_after: 1_000, source_before: "chat", source_after: "rows" } as const;
    const key = "travel-credit";
    // Counts come from where each row would go, not from where it counts now.
    expect(previewLine({ matches: 3, rows: [row(null, key), row("dining-credit", key), row("streaming-credit", key)], cycles: [change, change, change] }, key)).toBe(
      "Matches 3 credits this year. 2 would move here from another benefit. Changes 3 cycles.",
    );
    // A longer word on another benefit keeps its rows: nothing moves.
    expect(previewLine({ matches: 8, rows: Array.from({ length: 8 }, () => row("dining-credit", "dining-credit")), cycles: [] }, key)).toBe(
      "Matches 8 credits this year. 8 stay with another benefit.",
    );
    // A word as long as another benefit's: the rows wait for review.
    expect(previewLine({ matches: 2, rows: [row("dining-credit", null, true), row(null, null, true)], cycles: [change] }, key)).toBe(
      "Matches 2 credits this year. 2 would tie with another benefit and wait for review. Changes 1 cycle.",
    );
    expect(previewLine({ matches: 1, rows: [row("dining-credit", "dining-credit")], cycles: [] }, key)).toBe(
      "Matches 1 credit this year. 1 stays with another benefit.",
    );
    expect(previewLine({ matches: 0, rows: [], cycles: [] }, key)).toBe("Matches 0 credits this year.");
    expect(termProblem("")).toBeNull();
    expect(termProblem("  dining  credit ")).toBeNull();
    expect(termProblem("DI")).toBe(TERM_RULE);
    expect(termProblem("X".repeat(41))).toBe(TERM_RULE);
    expect(termProblem("CAFÉ")).toBe(TERM_RULE);
    expect(termProblem("DINING\tCREDIT")).toBe(TERM_RULE); // a tab is not plain
  });

  it("takes a decided row out of the review with its counts", () => {
    const tie = withoutRow(REVIEW, txnKey("b"));
    expect(tie.credits.map((r) => r.txn_key)).toEqual([txnKey("a"), txnKey("c")]);
    expect(tie.counts).toMatchObject({ to_review: 2, ambiguous: 0, returns: 1, trips: 1 });
    const back = withoutRow(REVIEW, txnKey("d"));
    expect(back.returns).toEqual([]);
    expect(back.counts).toMatchObject({ to_review: 3, returns: 0 });
    const lost = withoutRow(REVIEW, txnKey("f"));
    expect(lost.needs_a_look.map((n) => n.kind)).toEqual(["chat_only"]);
    expect(lost.counts.needs_a_look).toBe(0);
    expect(withoutRow(REVIEW, txnKey("9"))).toEqual(REVIEW);
    const marked = withoutChatOnly(REVIEW, "streaming-credit", "2026-07-01");
    expect(marked.needs_a_look.map((n) => n.kind)).toEqual(["lost_choice"]);
    expect(marked.counts.chat_says_used).toBe(0);
    expect(withoutChatOnly(REVIEW, "streaming-credit", "2026-04-01")).toEqual(REVIEW);
  });

  it("offers the card's shown credits that match rows", () => {
    const benefits = page().benefits;
    expect(cardCredits(benefits, "summit-card").map((b) => b.key)).toEqual(["dining-credit", "travel-credit"]);
    benefits.find((b) => b.key === "travel-credit")!.match_off = true;
    expect(cardCredits(benefits, "summit-card").map((b) => b.key)).toEqual(["dining-credit"]);
  });
});

describe("This month", () => {
  it("shows what to update and review this month, linked to the drawers", async () => {
    payload = page();
    payload.programs.find((p) => p.key === "harbor-miles")!.as_of = "2026-09-28";
    payload.review_counts = { ...payload.review_counts, to_review: 3 };
    renderWithProviders(<RewardsPage />, { route: "/rewards?tab=milestones" });
    const strip = await screen.findByRole("region", { name: "This month" });
    const update = within(strip).getByRole("link", { name: "Update 1 balance" });
    expect(update.getAttribute("href")).toBe("/rewards?update=balances");
    expect(update.className).toContain("h-11");
    expect(within(strip).getByRole("link", { name: "Review 3 card credits" }).getAttribute("href")).toBe(REVIEW_LINK);
  });

  it("shows each item only when there is one to do, and no strip when there is none", async () => {
    payload = page();
    payload.review_counts = { ...payload.review_counts, to_review: 1, returns: 4 };
    const { unmount } = renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const strip = await screen.findByRole("region", { name: "This month" });
    expect(within(strip).getAllByRole("link").map((a) => a.textContent)).toEqual(["Review 1 card credit"]);
    unmount();
    payload = REWARDS;
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    await screen.findByText("Estimated value");
    await screen.findByRole("list", { name: "Main programs" });
    expect(screen.queryByRole("region", { name: "This month" })).toBeNull();
  });

  it("leaves out the review link when nothing waits", async () => {
    payload = page();
    payload.programs.find((p) => p.key === "summit-points")!.stale = true;
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const strip = await screen.findByRole("region", { name: "This month" });
    expect(within(strip).getAllByRole("link").map((a) => a.textContent)).toEqual(["Update 1 balance"]);
  });

  it("opens Update balances from the strip, even on the Points tab", async () => {
    const user = userEvent.setup();
    payload = page();
    payload.programs.find((p) => p.key === "harbor-miles")!.as_of = "2026-09-28";
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    await user.click(await screen.findByRole("link", { name: "Update 1 balance" }));
    const drawer = await screen.findByRole("dialog", { name: "Update balances" });
    await user.click(within(drawer).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await user.click(screen.getByRole("link", { name: "Update 1 balance" }));
    expect(await screen.findByRole("dialog", { name: "Update balances" })).toBeTruthy();
  });

  it("opens the review from the strip and closes it again", async () => {
    const user = userEvent.setup();
    payload = page();
    payload.review_counts = { ...payload.review_counts, to_review: 3 };
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    await user.click(await screen.findByRole("link", { name: "Review 3 card credits" }));
    const drawer = await openReview();
    expect(await within(drawer).findByText("STATEMENT CREDIT")).toBeTruthy();
    await user.click(within(drawer).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    // The Benefits tab keeps a way back in.
    await user.click(screen.getByRole("button", { name: "Review card credits" }));
    expect(await openReview()).toBeTruthy();
  });

  it("keeps the strip and the review off the page before the first 1.3 run", async () => {
    payload = { ...NO_REWARDS, review_counts: { ...NO_REWARDS.review_counts, to_review: 2 } };
    renderWithProviders(<RewardsPage />, { route: REVIEW_LINK });
    expect(await screen.findByText("No rewards data yet.")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "This month" })).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(calls("GET", "/api/rewards/review")).toHaveLength(0);
  });
});

describe("Card credits to review", () => {
  it("shows one card at a time on a phone, with its place in the list", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RewardsPage />, { route: REVIEW_LINK });
    const drawer = await openReview();
    const list = await within(drawer).findByRole("region", { name: "Card credits to review" });
    expect(within(list).getByText("1 of 3")).toBeTruthy();
    expect(within(list).getByText("STATEMENT CREDIT")).toBeTruthy();
    expect(within(list).getByText("Sep 18, 2026 · Summit card · Summit Card")).toBeTruthy();
    expect(within(list).getByText("$25.00")).toBeTruthy();
    expect(within(list).getByText("Looks like Dining credit, Sep")).toBeTruthy();
    expect(within(list).queryByText("DINING TRAVEL CREDIT")).toBeNull();
    expect((within(list).getByRole("button", { name: "Previous" }) as HTMLButtonElement).disabled).toBe(true);
    await user.click(within(list).getByRole("button", { name: "Next" }));
    expect(within(list).getByText("2 of 3")).toBeTruthy();
    expect(within(list).getByText("DINING TRAVEL CREDIT")).toBeTruthy();
    expect(within(list).queryByText("STATEMENT CREDIT")).toBeNull();
    // 44px buttons on phones.
    expect(within(list).getByRole("button", { name: /^Not a benefit/ }).className).toContain("h-11");
    expect(drawer.textContent).not.toContain("—");
  });

  it("lists every card credit on a wider screen", async () => {
    wide = true;
    renderWithProviders(<RewardsPage />, { route: REVIEW_LINK });
    const drawer = await openReview();
    const list = await within(drawer).findByRole("list", { name: "Card credits to review" });
    expect(within(list).getAllByRole("listitem").map((li) => li.querySelector("span")!.textContent)).toEqual([
      "STATEMENT CREDIT",
      "DINING TRAVEL CREDIT",
      "ORCHARD PROMO CREDIT",
    ]);
    expect(within(drawer).queryByText(/ of 3$/)).toBeNull();
    // A live count of what is left, like the phone's "1 of 3".
    const left = within(drawer).getByText("3 card credits to review.");
    expect(left.getAttribute("aria-live")).toBe("polite");
  });

  it("offers only credits the page shows on a tie, and says why a row has no guess", async () => {
    const user = userEvent.setup();
    payload = page();
    payload.benefits = payload.benefits.filter((b) => b.key !== "travel-credit"); // hidden since
    renderWithProviders(<RewardsPage />, { route: REVIEW_LINK });
    const drawer = await openReview();
    await user.click(await within(drawer).findByRole("button", { name: "Next" }));
    expect(within(drawer).getByText("DINING TRAVEL CREDIT")).toBeTruthy();
    expect(within(drawer).getByRole("button", { name: "Count for Dining credit: DINING TRAVEL CREDIT, $10.00" })).toBeTruthy();
    expect(within(drawer).queryByRole("button", { name: /^Count for travel-credit/ })).toBeNull();
  });

  it("says when no credit on the card takes statement rows", async () => {
    const user = userEvent.setup();
    payload = page();
    payload.benefits = payload.benefits.map((b) => (b.account_key === "orchard-card" ? { ...b, match_off: true } : b));
    renderWithProviders(<RewardsPage />, { route: REVIEW_LINK });
    const drawer = await openReview();
    await user.click(await within(drawer).findByRole("button", { name: "Next" }));
    await user.click(within(drawer).getByRole("button", { name: "Next" }));
    expect(within(drawer).getByText("ORCHARD PROMO CREDIT")).toBeTruthy();
    expect(within(drawer).getByText("No credit on this card takes statement rows.")).toBeTruthy();
    expect(within(drawer).queryByRole("button", { name: /^Different benefit/ })).toBeNull();
  });

  it("counts a row for the guess with one tap, then moves on", async () => {
    const user = userEvent.setup();
    replies[`PUT ${credit("a")}`] = () => {
      review = withoutRow(REVIEW, txnKey("a"));
      return json(200, { txn_key: txnKey("a"), decision: "link", benefit_key: "dining-credit", cycle_start: null, reason: null, counted_in: null });
    };
    const { queryClient } = renderWithProviders(<RewardsPage />, { route: REVIEW_LINK });
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    const drawer = await openReview();
    await user.click(await within(drawer).findByRole("button", { name: "Count it: STATEMENT CREDIT, $25.00, for Dining credit" }));
    expect(sentBody("PUT", credit("a"))).toEqual({ decision: "link", benefit_key: "dining-credit" });
    const list = within(drawer).getByRole("region", { name: "Card credits to review" });
    await waitFor(() => expect(within(list).getByText("1 of 2")).toBeTruthy());
    expect(within(list).getByText("DINING TRAVEL CREDIT")).toBeTruthy();
    // Focus goes to the card that took its place, not the page.
    await waitFor(() => expect(list.contains(document.activeElement)).toBe(true));
    // The page, the review (a "rewards" key), the Dashboard, Checks and trips refetch.
    expect(invalidated(spy)).toEqual(["rewards", "dashboard", "checks", "trips"]);
  });

  it("asks what a row is for Not a benefit, inline, and Cancel goes back", async () => {
    const user = userEvent.setup();
    replies[`PUT ${credit("a")}`] = () => {
      // The refetch never lands: the row leaves on the answer alone.
      replies["GET /api/rewards/review"] = () => new Promise<Response>(() => {});
      return json(200, { txn_key: txnKey("a"), decision: "not_benefit", benefit_key: null, cycle_start: null, reason: "points", counted_in: null });
    };
    renderWithProviders(<RewardsPage />, { route: REVIEW_LINK });
    const drawer = await openReview();
    const not = await within(drawer).findByRole("button", { name: "Not a benefit: STATEMENT CREDIT, $25.00" });
    await user.click(not);
    const why = within(drawer).getByRole("group", { name: "What is it?" });
    expect(within(why).getAllByRole("button").map((b) => b.textContent)).toEqual(["A return", "A points credit", "Something else", "Cancel"]);
    expect(document.activeElement).toBe(within(why).getByRole("button", { name: "A return" }));
    await user.click(within(why).getByRole("button", { name: "Cancel" }));
    expect(document.activeElement).toBe(within(drawer).getByRole("button", { name: "Not a benefit: STATEMENT CREDIT, $25.00" }));
    await user.click(within(drawer).getByRole("button", { name: "Not a benefit: STATEMENT CREDIT, $25.00" }));
    await user.click(within(drawer).getByRole("button", { name: "A points credit" }));
    expect(sentBody("PUT", credit("a"))).toEqual({ decision: "not_benefit", reason: "points" });
    // Gone from the list at once.
    await waitFor(() => expect(within(drawer).queryByText("STATEMENT CREDIT")).toBeNull());
  });

  it("counts a row for a different benefit on its card", async () => {
    const user = userEvent.setup();
    replies[`PUT ${credit("a")}`] = () => json(200, { txn_key: txnKey("a"), decision: "link", benefit_key: "travel-credit", cycle_start: null, reason: null, counted_in: null });
    renderWithProviders(<RewardsPage />, { route: REVIEW_LINK });
    const drawer = await openReview();
    await user.click(await within(drawer).findByRole("button", { name: "Different benefit: STATEMENT CREDIT, $25.00" }));
    const pick = within(drawer).getByLabelText("Count for") as HTMLSelectElement;
    expect(document.activeElement).toBe(pick);
    expect(pick.className).toContain("text-base"); // 16px
    // Only the credits on the row's card.
    expect([...pick.options].map((o) => o.textContent)).toEqual(["Pick a credit", "Dining credit", "Yearly travel credit"]);
    const count = within(drawer).getByRole("button", { name: "Count it: STATEMENT CREDIT, $25.00" }) as HTMLButtonElement;
    expect(count.disabled).toBe(true);
    await user.selectOptions(pick, "travel-credit");
    await user.click(count);
    expect(sentBody("PUT", credit("a"))).toEqual({ decision: "link", benefit_key: "travel-credit" });
  });

  it("asks for a pick on a row that matches more than one benefit, and shows a refused save", async () => {
    const user = userEvent.setup();
    replies[`PUT ${credit("b")}`] = () => json(422, { detail: "Pick a credit on this row's card." });
    renderWithProviders(<RewardsPage />, { route: REVIEW_LINK });
    const drawer = await openReview();
    await user.click(await within(drawer).findByRole("button", { name: "Next" }));
    expect(within(drawer).getByText("Matches more than one benefit. Pick one.")).toBeTruthy();
    expect(within(drawer).queryByRole("button", { name: /^Count it/ })).toBeNull();
    await user.click(within(drawer).getByRole("button", { name: "Count for Yearly travel credit: DINING TRAVEL CREDIT, $10.00" }));
    expect(sentBody("PUT", credit("b"))).toEqual({ decision: "link", benefit_key: "travel-credit" });
    expect(await within(drawer).findByText("Pick a credit on this row's card.")).toBeTruthy();
    expect(within(drawer).getByText("DINING TRAVEL CREDIT")).toBeTruthy();
  });

  it("offers no Count it when no credit has room", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RewardsPage />, { route: REVIEW_LINK });
    const drawer = await openReview();
    await user.click(await within(drawer).findByRole("button", { name: "Next" }));
    await user.click(within(drawer).getByRole("button", { name: "Next" }));
    expect(within(drawer).getByText("ORCHARD PROMO CREDIT")).toBeTruthy();
    expect(within(drawer).getByText("No credit on this card has a cycle for this date.")).toBeTruthy();
    expect(within(drawer).queryByRole("button", { name: /^Count it/ })).toBeNull();
    expect(within(drawer).getByRole("button", { name: "Different benefit: ORCHARD PROMO CREDIT, $3.00" })).toBeTruthy();
  });

  it("keeps returns and points credits for a trip in closed groups, each row still countable", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RewardsPage />, { route: REVIEW_LINK });
    const drawer = await openReview();
    const returns = await within(drawer).findByRole("button", { name: "Looks like returns (1)" });
    const trips = within(drawer).getByRole("button", { name: "Points credits for a trip (1)" });
    expect(returns.getAttribute("aria-expanded")).toBe("false");
    expect(trips.getAttribute("aria-expanded")).toBe("false");
    expect(within(drawer).queryByText("TOWNE OUTFITTERS RETURN")).toBeNull();
    await user.click(returns);
    expect(returns.getAttribute("aria-expanded")).toBe("true");
    const group = within(drawer).getByRole("list", { name: "Looks like returns" });
    expect(within(group).getByText("TOWNE OUTFITTERS RETURN")).toBeTruthy();
    expect(within(group).getByRole("button", { name: /^Not a benefit/ })).toBeTruthy();
    expect(within(drawer).queryByText("POINTS CREDIT TRAVEL")).toBeNull();
  });

  it("says when every card credit is matched, and moves focus there after the last one", async () => {
    const user = userEvent.setup();
    wide = true;
    review = { ...REVIEW_DONE, credits: [REVIEW.credits[0]!], counts: { ...REVIEW_DONE.counts, to_review: 1 } };
    replies[`PUT ${credit("a")}`] = () => {
      review = REVIEW_DONE;
      return json(200, { txn_key: txnKey("a"), decision: "link", benefit_key: "dining-credit", cycle_start: null, reason: null, counted_in: null });
    };
    renderWithProviders(<RewardsPage />, { route: REVIEW_LINK });
    const drawer = await openReview();
    await user.click(await within(drawer).findByRole("button", { name: /^Count it/ }));
    const done = await within(drawer).findByText("All card credits are matched.");
    await waitFor(() => expect(document.activeElement).toBe(done));
    expect(within(drawer).queryByRole("region", { name: "Needs a look" })).toBeNull();
  });

  it("deletes a lost choice and marks a chat-only cycle not used, for a member too", async () => {
    const user = userEvent.setup();
    role = "member";
    replies[`DELETE ${credit("f")}`] = () => {
      review = withoutRow(review, txnKey("f"));
      return new Response(null, { status: 204 });
    };
    replies["PUT /api/rewards/benefits/streaming-credit/cycles/2026-07-01/mark"] = () => {
      review = withoutChatOnly(review, "streaming-credit", "2026-07-01");
      return json(200, benefit("streaming-credit"));
    };
    renderWithProviders(<RewardsPage />, { route: REVIEW_LINK });
    const drawer = await openReview();
    const look = await within(drawer).findByRole("region", { name: "Needs a look" });
    expect(within(look).getByText("A credit choice lost its row.")).toBeTruthy();
    expect(within(look).getByText("Jul 12, 2026 · $10.00")).toBeTruthy();
    expect(within(look).getByText("Streaming credit, Jul")).toBeTruthy();
    expect(within(look).getByText("Aug 3, 2026 · The chat says $15.00")).toBeTruthy();
    // A member marks the cycle. Adding a match word is for an admin.
    expect(within(look).queryByRole("button", { name: /^Add a match word/ })).toBeNull();
    await user.click(within(look).getByRole("button", { name: "Delete: the lost choice for Dining credit" }));
    await user.click(within(look).getByRole("button", { name: "Delete choice" }));
    await waitFor(() => expect(calls("DELETE", credit("f"))).toHaveLength(1));
    await waitFor(() => expect(within(look).queryByText("A credit choice lost its row.")).toBeNull());
    await user.click(within(look).getByRole("button", { name: "Mark not used: Streaming credit, Jul" }));
    expect(sentBody("PUT", "/api/rewards/benefits/streaming-credit/cycles/2026-07-01/mark")).toEqual({ used: "0.00" });
    await waitFor(() => expect(within(drawer).queryByRole("region", { name: "Needs a look" })).toBeNull());
  });

  it("shows a choice on a credit that takes no rows, with Delete", async () => {
    const user = userEvent.setup();
    const off = {
      kind: "choice_off",
      text: "Your choice counts it for a credit that takes no statement rows.",
      benefit_key: "dining-credit",
      benefit_name: "Dining credit",
      cycle_start: null,
      txn_key: txnKey("g"),
      date: "2026-08-18",
      cents: 2_500,
    } as const;
    review = { ...REVIEW_DONE, needs_a_look: [off], counts: { ...REVIEW_DONE.counts, needs_a_look: 1 } };
    replies[`DELETE ${credit("g")}`] = () => {
      review = REVIEW_DONE;
      return new Response(null, { status: 204 });
    };
    renderWithProviders(<RewardsPage />, { route: REVIEW_LINK });
    const drawer = await openReview();
    const look = await within(drawer).findByRole("region", { name: "Needs a look" });
    expect(within(look).getByText("Your choice counts it for a credit that takes no statement rows.")).toBeTruthy();
    expect(within(look).getByText("Aug 18, 2026 · $25.00")).toBeTruthy();
    await user.click(within(look).getByRole("button", { name: "Delete: the choice for Dining credit" }));
    await user.click(within(look).getByRole("button", { name: "Delete choice" }));
    await waitFor(() => expect(calls("DELETE", credit("g"))).toHaveLength(1));
    await waitFor(() => expect(within(drawer).queryByRole("region", { name: "Needs a look" })).toBeNull());
  });

  it("takes an admin from a chat-only cycle to the credit's match words", async () => {
    const user = userEvent.setup();
    review = { ...REVIEW, needs_a_look: [{ ...REVIEW.needs_a_look[1]!, benefit_key: "dining-credit", benefit_name: "Dining credit", cycle_start: "2026-09-01" }] };
    renderWithProviders(<RewardsPage />, { route: REVIEW_LINK });
    const drawer = await openReview();
    await user.click(await within(drawer).findByRole("button", { name: "Add a match word: Dining credit" }));
    const edit = await screen.findByRole("dialog", { name: "Edit Dining credit" });
    expect(await within(edit).findByLabelText("Add a match word")).toBeTruthy();
    expect(screen.queryByRole("dialog", { name: "Card credits to review" })).toBeNull();
  });

  it("lists a cycle over its amount, with a way to its cycles", async () => {
    const user = userEvent.setup();
    payload = page();
    benefit("dining-credit", payload).past_cycles[0]!.over_cents = 500;
    review = REVIEW_DONE;
    renderWithProviders(<RewardsPage />, { route: REVIEW_LINK });
    const drawer = await openReview();
    const look = await within(drawer).findByRole("region", { name: "Needs a look" });
    expect(within(look).getByText("Dining credit, Aug")).toBeTruthy();
    await user.click(within(look).getByRole("button", { name: "See its cycles: Dining credit" }));
    expect(await screen.findByRole("dialog", { name: "Edit Dining credit" })).toBeTruthy();
  });

  it("shows a load error in the drawer with Retry", async () => {
    replies["GET /api/rewards/review"] = () => json(500, { detail: "Server error." });
    renderWithProviders(<RewardsPage />, { route: REVIEW_LINK });
    const drawer = await openReview();
    expect(await within(drawer).findByText("Could not load card credits.")).toBeTruthy();
    expect(within(drawer).getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("sums up the review on the Benefits tab", async () => {
    renderWithProviders(<RewardsPage />, { route: "/rewards?tab=benefits" });
    expect(await screen.findByText("3 to review · 2 need a look · 1 looks like a return · 1 points credit for a trip.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("Benefit cycles and match words", () => {
  it("adds the source and notes to a benefit's cycle line", async () => {
    payload = page();
    Object.assign(benefit("dining-credit", payload).cycle!, { source: "rows", used_cents: 600, left_cents: 400, state: "partial", chat_used_cents: 1_000 });
    Object.assign(benefit("wireless-credit", payload).cycle!, { source: "set", used_cents: 0, state: "open" });
    benefit("streaming-credit", payload).cycle!.over_cents = 500;
    Object.assign(benefit("travel-credit", payload), { terms_count: 2 });
    renderWithProviders(<RewardsPage />, { route: "/rewards?tab=benefits" });
    const open = await screen.findByRole("region", { name: "Open this cycle" });
    const row = (name: string) => within(document.body).getByText(name).closest("li")!;
    expect(within(row("Dining credit")).getByText("From your statements · The chat says $10.00")).toBeTruthy();
    expect(within(open).getByText("Waiting for rows")).toBeTruthy(); // travel credit: words, no statement yet
    expect(within(row("Wireless credit")).getByText("Set in the app")).toBeTruthy();
    expect(within(row("Streaming credit")).getByText("From the chat · More than the credit by $5.00")).toBeTruthy();
  });

  it("previews a typed match word, then adds it", async () => {
    const user = userEvent.setup();
    const row = { merchant: null, description: "DINING CREDIT", cents: 1_000, after_key: "dining-credit", after_name: "Dining credit", tie: false };
    replies["POST /api/rewards/terms/preview"] = () =>
      json(200, {
        benefit_key: "dining-credit",
        matches: 2,
        elsewhere: 1,
        rows: [
          { ...row, txn_key: txnKey("p"), date: "2026-08-12", now_key: null, now_name: null },
          { ...row, txn_key: txnKey("q"), date: "2026-09-12", now_key: "travel-credit", now_name: "Yearly travel credit" },
        ],
        cycles: [
          {
            benefit_key: "dining-credit",
            benefit_name: "Dining credit",
            start: "2026-08-01",
            used_cents_before: 1_000,
            used_cents_after: 1_000,
            source_before: "chat",
            source_after: "rows",
          },
        ],
      });
    replies["PUT /api/rewards/benefits/dining-credit/terms"] = (body) => {
      terms = { ...TERMS, terms: (body as { terms: string[] }).terms };
      return json(200, terms);
    };
    renderWithProviders(<RewardsPage />, { route: "/rewards?tab=benefits" });
    await user.click(await screen.findByRole("button", { name: "Edit Dining credit" }));
    const drawer = screen.getByRole("dialog", { name: "Edit Dining credit" });
    const saved = await within(drawer).findByRole("list", { name: "Saved match words" });
    expect(within(saved).getByText("DELIVERY")).toBeTruthy();
    const word = within(drawer).getByLabelText("Add a match word") as HTMLInputElement;
    expect(word.className).toContain("text-base");
    expect(word.className).toContain("h-11");
    await user.type(word, "dining  credit");
    expect(await within(drawer).findByText("Matches 2 credits this year. 1 would move here from another benefit. Changes 1 cycle.")).toBeTruthy();
    // The rows and the cycles it would change, before anything is saved (plan 4.10).
    const rows = within(drawer).getByRole("list", { name: "Credit rows the word matches" });
    expect(within(rows).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Aug 12 · DINING CREDIT$10.00Now not counted. With the word, Dining credit.",
      "Sep 12 · DINING CREDIT$10.00Now Yearly travel credit. With the word, Dining credit.",
    ]);
    const changes = within(drawer).getByRole("list", { name: "Cycles the word changes" });
    expect(changes.textContent).toBe("Dining credit, Aug: $10.00 from the chat, then $10.00 from your statements.");
    // The word goes in the body, never the URL.
    expect(sentBody("POST", "/api/rewards/terms/preview")).toEqual({ benefit_key: "dining-credit", term: "dining credit" });
    expect(fetchMock.mock.calls.some(([u]) => String(u).toLowerCase().includes("dining%20credit") || String(u).toLowerCase().includes("dining credit"))).toBe(false);
    await user.click(within(drawer).getByRole("button", { name: "Add" }));
    expect(sentBody("PUT", "/api/rewards/benefits/dining-credit/terms")).toEqual({ terms: ["DELIVERY", "dining credit"] });
    expect(await within(drawer).findByText("Saved dining credit.")).toBeTruthy();
    expect(word.value).toBe("");
    expect(document.activeElement).toBe(word);
  });

  it("shows a word made only of generic words quietly, and the refused save plainly", async () => {
    const user = userEvent.setup();
    replies["POST /api/rewards/terms/preview"] = () => json(422, { detail: "Use a word from the credit line, like the merchant." });
    replies["PUT /api/rewards/benefits/dining-credit/terms"] = () => json(422, { detail: "Use a word from the credit line, like the merchant." });
    renderWithProviders(<RewardsPage />, { route: "/rewards?tab=benefits" });
    await user.click(await screen.findByRole("button", { name: "Edit Dining credit" }));
    const drawer = screen.getByRole("dialog", { name: "Edit Dining credit" });
    const word = await within(drawer).findByLabelText("Add a match word");
    await user.type(word, "STATEMENT CREDIT");
    const hint = await within(drawer).findByText("Use a word from the credit line, like the merchant.");
    // Quiet: the hint under the field, gray, no alert.
    expect(hint.id).toBe(word.getAttribute("aria-describedby"));
    expect(hint.className).toContain("text-fg-secondary");
    expect(within(drawer).queryByRole("alert")).toBeNull();
    // A short word is not sent at all.
    await user.clear(word);
    await user.type(word, "DI");
    await user.click(within(drawer).getByRole("button", { name: "Add" }));
    expect(within(drawer).getAllByText(TERM_RULE).length).toBeGreaterThan(0);
    expect(calls("PUT", "/api/rewards/benefits/dining-credit/terms")).toHaveLength(0);
  });

  it("saves a suggested word and the word seen on statements with one tap, and removes a saved one", async () => {
    const user = userEvent.setup();
    replies["PUT /api/rewards/benefits/dining-credit/terms"] = (body) => {
      const next = (body as { terms: string[] }).terms;
      terms = { ...TERMS, terms: next, suggested: TERMS.suggested.filter((w) => !next.includes(w)), seen: next.includes("DINING CREDIT") ? null : TERMS.seen };
      return json(200, terms);
    };
    renderWithProviders(<RewardsPage />, { route: "/rewards?tab=benefits" });
    await user.click(await screen.findByRole("button", { name: "Edit Dining credit" }));
    const drawer = screen.getByRole("dialog", { name: "Edit Dining credit" });
    const tap = await within(drawer).findByRole("list", { name: "Suggested match words" });
    const seen = within(tap).getByRole("button", { name: "Save DINING CREDIT · seen 8 times on statements" });
    expect(seen.className).toContain("border-dashed");
    await user.click(within(tap).getByRole("button", { name: "Save DINING · from the name" }));
    expect(sentBody("PUT", "/api/rewards/benefits/dining-credit/terms")).toEqual({ terms: ["DELIVERY", "DINING"] });
    expect(await within(drawer).findByText("Saved DINING.")).toBeTruthy();
    await user.click(within(drawer).getByRole("button", { name: "Save DINING CREDIT · seen 8 times on statements" }));
    expect(sentBody("PUT", "/api/rewards/benefits/dining-credit/terms")).toEqual({ terms: ["DELIVERY", "DINING", "DINING CREDIT"] });
    await waitFor(() => expect(within(drawer).queryByRole("list", { name: "Suggested match words" })).toBeNull());
    await user.click(within(drawer).getByRole("button", { name: "Remove DELIVERY" }));
    expect(sentBody("PUT", "/api/rewards/benefits/dining-credit/terms")).toEqual({ terms: ["DINING", "DINING CREDIT"] });
    expect(await within(drawer).findByText("Removed DELIVERY.")).toBeTruthy();
  });

  it("turns matching off with the never-on-statements switch", async () => {
    const user = userEvent.setup();
    replies["PATCH /api/rewards/benefits/dining-credit"] = (body) => {
      const out = { ...benefit("dining-credit"), match_off: (body as { match_off: boolean }).match_off };
      payload = page();
      payload.benefits = payload.benefits.map((b) => (b.key === out.key ? out : b));
      return json(200, out);
    };
    renderWithProviders(<RewardsPage />, { route: "/rewards?tab=benefits" });
    await user.click(await screen.findByRole("button", { name: "Edit Dining credit" }));
    const drawer = screen.getByRole("dialog", { name: "Edit Dining credit" });
    const off = within(drawer).getByRole("switch", { name: /This credit never shows on statements/ }) as HTMLInputElement;
    expect(off.checked).toBe(false);
    await within(drawer).findByLabelText("Add a match word");
    await user.click(off);
    expect(sentBody("PATCH", "/api/rewards/benefits/dining-credit")).toEqual({ match_off: true });
    expect(await within(drawer).findByText("Matching is off.")).toBeTruthy();
    expect((within(drawer).getByRole("switch", { name: /never shows/ }) as HTMLInputElement).checked).toBe(true);
    expect(within(drawer).queryByLabelText("Add a match word")).toBeNull();
  });

  it("lets a member mark, change and clear a cycle, with no admin parts", async () => {
    const user = userEvent.setup();
    role = "member";
    const url = "/api/rewards/benefits/dining-credit/cycles/2026-10-01/mark";
    const marked = (used: number) => {
      const out = structuredClone(benefit("dining-credit"));
      Object.assign(out.cycle!, { source: "set", used_cents: used, state: used > 0 ? "done" : "open", used_on: used > 0 ? "2026-10-20" : null });
      payload = page();
      payload.benefits = payload.benefits.map((b) => (b.key === out.key ? out : b));
      return json(200, out);
    };
    replies[`PUT ${url}`] = (body) => marked((body as { used: string }).used === "0.00" ? 0 : 1_000);
    replies[`DELETE ${url}`] = () => {
      payload = REWARDS;
      return json(200, benefit("dining-credit"));
    };
    renderWithProviders(<RewardsPage />, { route: "/rewards?tab=benefits" });
    expect(await screen.findByText("Only an admin can change benefits.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Edit / })).toBeNull();
    // Members get a cycles button on credits only.
    expect(screen.queryByRole("button", { name: "Cycles of Anniversary free night" })).toBeNull();
    const open = screen.getByRole("button", { name: "Cycles of Dining credit" });
    expect(open.className).toContain("h-11");
    await user.click(open);
    const drawer = screen.getByRole("dialog", { name: "Dining credit" });
    expect(within(drawer).queryByLabelText("Name")).toBeNull();
    expect(within(drawer).queryByText("Match words")).toBeNull();
    expect(within(drawer).queryByRole("button", { name: "Hide this benefit" })).toBeNull();
    expect(within(drawer).getByText("Only an admin can change benefits.")).toBeTruthy();
    const cycles = within(drawer).getByRole("list", { name: "Dining credit cycles" });
    expect(within(cycles).getAllByRole("listitem", { name: /to/ }).map((li) => li.getAttribute("aria-label"))).toEqual([
      "Oct 1 to Oct 31",
      "Sep 1 to Sep 30",
      "Aug 1 to Aug 31",
    ]);
    const oct = within(cycles).getByRole("listitem", { name: "Oct 1 to Oct 31" });
    expect(within(oct).getByText("Not used yet")).toBeTruthy(); // the state in a word
    expect(within(cycles).getByRole("listitem", { name: "Sep 1 to Sep 30" }).textContent).toContain("From the chat");
    expect(within(oct).queryByRole("button", { name: /^Clear/ })).toBeNull();
    await user.click(within(oct).getByRole("button", { name: "Mark used: Oct 1 to Oct 31" }));
    // The amount, on today: the cycle is still running.
    expect(sentBody("PUT", url)).toEqual({ used: "10.00", used_on: "2026-10-20" });
    await waitFor(() => expect(within(oct).getByText("Set in the app")).toBeTruthy());
    expect(document.activeElement).toBe(oct); // the pressed button is gone
    // A live status says what was saved: focus on the cycle reads only its dates.
    const status = within(oct).getByText("Marked used.");
    expect(status.closest("[aria-live]")?.getAttribute("aria-live")).toBe("polite");
    expect(within(oct).queryByRole("button", { name: /^Mark used/ })).toBeNull();
    await user.click(within(oct).getByRole("button", { name: "Mark not used: Oct 1 to Oct 31" }));
    expect(sentBody("PUT", url)).toEqual({ used: "0.00" });
    expect(await within(oct).findByText("Marked not used.")).toBeTruthy();
    await user.click(await within(oct).findByRole("button", { name: "Clear the mark: Oct 1 to Oct 31" }));
    await waitFor(() => expect(calls("DELETE", url)).toHaveLength(1));
    await waitFor(() => expect(within(oct).queryByText("Set in the app")).toBeNull());
    expect(within(oct).getByText("Mark cleared.")).toBeTruthy();
  });

  it("gives a member a cycles button on ended credits only", async () => {
    const user = userEvent.setup();
    role = "member";
    payload = page();
    Object.assign(benefit("travel-credit", payload), { group: "ended", ends_on: "2026-09-30", cycle: null });
    Object.assign(benefit("anniversary-night", payload), { group: "ended", past_cycles: [] });
    renderWithProviders(<RewardsPage />, { route: "/rewards?tab=benefits" });
    await user.click(await screen.findByRole("button", { name: "Show past cycles" }));
    const travel = screen.getByRole("region", { name: "Yearly travel credit past cycles" });
    expect(within(travel).getByRole("button", { name: "Cycles of Yearly travel credit" })).toBeTruthy();
    const night = screen.getByRole("region", { name: "Anniversary free night past cycles" });
    expect(within(night).queryByRole("button")).toBeNull();
  });

  it("marks a past cycle used on its last day, and moves a late row to the cycle before", async () => {
    const user = userEvent.setup();
    payload = page();
    const late = { txn_key: txnKey("1"), date: "2026-10-03", merchant: null, description: "DINING CREDIT", cents: 1_000, earlier_start: "2026-09-01" };
    Object.assign(benefit("dining-credit", payload).cycle!, { source: "rows", used_cents: 1_000, state: "done", rows: [late], covered: true });
    replies["PUT /api/rewards/benefits/dining-credit/cycles/2026-09-01/mark"] = () => json(200, benefit("dining-credit", payload));
    replies[`PUT /api/rewards/credits/${txnKey("1")}`] = () =>
      json(200, { txn_key: txnKey("1"), decision: "link", benefit_key: "dining-credit", cycle_start: "2026-09-01", reason: null, counted_in: null });
    renderWithProviders(<RewardsPage />, { route: "/rewards?tab=benefits" });
    await user.click(await screen.findByRole("button", { name: "Edit Dining credit" }));
    const drawer = screen.getByRole("dialog", { name: "Edit Dining credit" });
    const oct = within(drawer).getByRole("listitem", { name: "Oct 1 to Oct 31" });
    const rows = within(oct).getByRole("list", { name: "Statement credits, Oct 1 to Oct 31" });
    expect(within(rows).getByText("DINING CREDIT")).toBeTruthy();
    await user.click(within(rows).getByRole("button", { name: "Count in Sep instead: DINING CREDIT" }));
    expect(sentBody("PUT", `/api/rewards/credits/${txnKey("1")}`)).toEqual({ decision: "link", benefit_key: "dining-credit", cycle_start: "2026-09-01" });
    expect(await within(oct).findByText("Counted in Sep.")).toBeTruthy(); // said, not only shown
    const sep = within(drawer).getByRole("listitem", { name: "Sep 1 to Sep 30" });
    await user.click(within(sep).getByRole("button", { name: "Mark used: Sep 1 to Sep 30" }));
    expect(sentBody("PUT", "/api/rewards/benefits/dining-credit/cycles/2026-09-01/mark")).toEqual({ used: "10.00", used_on: "2026-09-30" });
  });
});
