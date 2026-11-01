import { screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Checks } from "../../lib/api";
import { AppRoutes } from "../../routes/AppRoutes";
import { renderWithProviders } from "../../test/render";
import { CategoryLabel, emojiMap } from "../../components/ui/category-label";
import { balanceRows, cashflowRows, ChecksPage, checkSummary, diffText } from "./ChecksPage";
import { BENEFIT_NOTES, CREDITS_REVIEW, creditCountLines, PROGRAM_NOTES } from "./P4Checks";

// Fictional numbers only.
const CHECKS: Checks = {
  run_id: "2026-10-12",
  as_of: "2026-09-30",
  net_worth: { app_cents: 40_000_000, chat_cents: 40_000_050, diff_cents: -50, status: "match" },
  assets: { app_cents: 60_000_000, chat_cents: 60_000_000, diff_cents: 0, status: "match" },
  liabilities: { app_cents: 20_000_000, chat_cents: 19_990_000, diff_cents: 10_000, status: "differs" },
  parts: [
    { key: "home-equity", label: "Home equity", app_cents: 30_000_000, chat_cents: 30_000_000, diff_cents: 0, status: "match" },
    { key: "credit-cards", label: "Credit cards", app_cents: -500_000, chat_cents: -490_000, diff_cents: -10_000, status: "differs" },
    { key: "vehicles", label: "Vehicles", app_cents: 1_000_000, chat_cents: null, diff_cents: null, status: "app_only" },
  ],
  cashflow: [
    {
      month: "2026-09",
      income: { app_cents: null, chat_cents: 1_000_000, diff_cents: null, status: "chat_only" },
      spending: { app_cents: null, chat_cents: 700_000, diff_cents: null, status: "chat_only" },
      complete: false,
      saved_invested: { app_cents: null, chat_cents: 50_000, diff_cents: null, status: "chat_only" },
      by_group: [],
      payment_basis_cents: 0,
      card_no_statement_cents: 0,
      uncategorized_cents: 0,
      reimbursable_cents: 0,
      edited_out_count: 0,
      notes: [],
    },
  ],
  plan_mapping: [],
  plan_mapping_month: null,
  unpaired_transfers: [],
  card_payments_no_statement: [],
  missing_statements: [],
  payment_basis_watch: [],
  utility_past_due: [],
  utility_payments_no_bill: [],
  utility_warnings: [],
  utility_bill_count: 0,
  rewards: null,
  trips: { trip_count: 0, unknown: [], tagged: [] },
  latest_complete_month: null,
  notes: ["Credit cards differ because one card statement is newer in the app."],
};

const P4_CHECKS: Checks = {
  ...CHECKS,
  latest_complete_month: "2026-08",
  cashflow: [
    {
      ...CHECKS.cashflow[0]!,
      month: "2026-08",
      complete: true,
      income: { app_cents: 1_000_000, chat_cents: 1_000_000, diff_cents: 0, status: "match" },
      spending: { app_cents: 700_000, chat_cents: 700_000, diff_cents: 0, status: "match" },
      saved_invested: { app_cents: 50_000, chat_cents: 50_000, diff_cents: 0, status: "match" },
      by_group: [{ key: "lifestyle", label: "Lifestyle", app_cents: 50_000, chat_cents: 51_000, diff_cents: -1_000, status: "differs" }],
      payment_basis_cents: 4_000,
      edited_out_count: 2,
      notes: ["Aug 2026 counts card payments on a payment basis."],
    },
  ],
  plan_mapping: [
    { key: "lifestyle", label: "Lifestyle", measure: "group", maps_to: "Spending in Lifestyle" },
    { key: "plan-installments", label: "Plan It installments", measure: "plan_installments", maps_to: "Plan payments from installment plans" },
    { key: "gifts", label: "Gifts", measure: "unmapped", maps_to: "Nothing" },
  ],
  plan_mapping_month: "2026-10",
  unpaired_transfers: [
    {
      txn_key: "b".repeat(64),
      txn_date: "2026-09-03",
      account_key: "example-checking",
      account_name: "Example checking",
      counterparty_account_key: "example-savings",
      counterparty_name: "Example savings",
      amount_cents: -25_000,
    },
  ],
  card_payments_no_statement: [{ account_key: "example-card", account_name: "Example card", month: "2026-08", count: 2, cents: 30_000 }],
  missing_statements: [{ account_key: "example-card", name: "Example card", month: "2026-09" }],
  payment_basis_watch: [
    { rule: "min-payments", severity: "high", account_key: "example-store", account_name: "Example store card", month: "2026-09", message: "Two minimum payments in a row." },
    { rule: "no-payment-45d", severity: "medium", account_key: "example-store", account_name: "Example store card", month: null, message: "No payment for 45 days." },
  ],
  utility_bill_count: 29,
  utility_past_due: [
    {
      utility_key: "example-power",
      provider: "Example Power",
      bill_key: "example-power:2026-09-03",
      bill_date: "2026-09-06",
      period_end: "2026-09-03",
      due_date: "2026-09-26",
      total_cents: 17_425,
      account_name: "Example checking",
    },
  ],
  utility_payments_no_bill: [
    {
      utility_key: "example-water",
      provider: "Example Water",
      txn_key: "c".repeat(64),
      txn_date: "2026-08-22",
      account_key: "example-checking",
      account_name: "Example checking",
      amount_cents: 5_693,
    },
  ],
  utility_warnings: [
    {
      utility_key: "example-power",
      provider: "Example Power",
      bill_key: "example-power:2025-07-05",
      period_end: "2025-07-05",
      rule: "utility-total-rounding",
      message: "The line charges plus the credits differ from the total by 1 or 2 cents.",
    },
  ],
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

let checks: Checks | "missing" | "error" = CHECKS;
const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input);
  if (url === "/api/auth/me") return json(200, { username: "sam", display_name: "Sam", role: "member" });
  if (url.startsWith("/api/checks")) {
    if (checks === "missing") return json(404, { detail: "No imported run with net worth numbers." });
    if (checks === "error") return json(500, { detail: "Server error." });
    return json(200, checks);
  }
  return json(404, { detail: "Not found." });
});

beforeEach(() => {
  checks = CHECKS;
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** Body rows of the wide-screen table in a card, as cell texts. */
function tableRows(table: HTMLElement): string[][] {
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((tr) => Array.from(tr.children).map((c) => c.textContent ?? ""));
}

describe("ChecksPage", () => {
  it("shows the run, the as-of date and every line with app, chat, difference and status", async () => {
    renderWithProviders(<ChecksPage />, { route: "/checks" });
    expect(await screen.findByText("From the Oct 12, 2026 update · Balances as of Sep 30, 2026")).toBeTruthy();
    const table = screen.getByRole("table", { name: "Net worth checks as of Sep 30, 2026" });
    expect(tableRows(table)).toEqual([
      ["Net worth", "$400,000.00", "$400,000.50", "−$0.50", "Match"],
      ["Assets", "$600,000.00", "$600,000.00", "$0.00", "Match"],
      ["Liabilities", "$200,000.00", "$199,900.00", "+$100.00", "Differs"],
      ["Home equity", "$300,000.00", "$300,000.00", "$0.00", "Match"],
      ["Credit cards", "−$5,000.00", "−$4,900.00", "−$100.00", "Differs"],
      ["Vehicles", "$10,000.00", "None", "", "App only"],
    ]);
    // Match in good text, differs in bad text.
    expect(within(table).getAllByText("Match")[0]?.className).toContain("text-good");
    expect(within(table).getAllByText("Differs")[0]?.className).toContain("text-bad");
    expect(screen.getByText(/2 of 6 lines differ\. 1 line is on one side only\./)).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith("/api/checks", expect.anything());
    // The wide table scrolls by keyboard too.
    const region = screen.getByRole("region", { name: "Net worth checks as of Sep 30, 2026" });
    expect(region.contains(table)).toBe(true);
    expect(region.tabIndex).toBe(0);
  });

  it("has a phone list with the same lines", async () => {
    renderWithProviders(<ChecksPage />, { route: "/checks" });
    const list = await screen.findByRole("list", { name: "Net worth checks as of Sep 30, 2026" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(6);
    expect(items[2]?.textContent).toBe("LiabilitiesDiffersApp$200,000.00Chat$199,900.00Difference+$100.00");
    expect(items[5]?.textContent).toBe("VehiclesApp onlyApp$10,000.00ChatNoneDifferenceNone");
  });

  it("shows the chat's cash flow with the app side \"Not yet\" until Phase 4, and the notes", async () => {
    renderWithProviders(<ChecksPage />, { route: "/checks" });
    const table = await screen.findByRole("table", { name: "Cash flow checks by month" });
    expect(tableRows(table)).toEqual([
      ["Sep 2026 (partial) income", "Not yet", "$10,000.00", "", "Chat only"],
      ["Sep 2026 (partial) spending", "Not yet", "$7,000.00", "", "Chat only"],
      ["Sep 2026 (partial) saved and invested", "Not yet", "$500.00", "", "Chat only"],
    ]);
    expect(screen.getByText("Credit cards differ because one card statement is newer in the app.")).toBeTruthy();
  });

  it("shows the Phase 4 lists: watch, unpaired legs, card payments, missing statements and the plan mapping", async () => {
    checks = P4_CHECKS;
    renderWithProviders(<ChecksPage />, { route: "/checks" });
    const watch = await screen.findByRole("list", { name: "Payment-basis watch" });
    const items = within(watch).getAllByRole("listitem");
    expect(items[0]?.textContent).toBe("Example store card · Sep 2026Two minimum payments in a row.High");
    expect(items[1]?.textContent).toBe("Example store cardNo payment for 45 days.Medium");
    expect(tableRows(screen.getByRole("table", { name: "Unpaired transfer legs" }))).toEqual([
      ["Sep 3, 2026", "Example checking", "Example savings", "−$250.00"],
    ]);
    expect(tableRows(screen.getByRole("table", { name: "Card payments with no statement" }))).toEqual([
      ["Example card", "Aug 2026", "2", "$300.00"],
    ]);
    expect(tableRows(screen.getByRole("table", { name: "Missing statements" }))).toEqual([["Example card", "Sep 2026"]]);
    expect(tableRows(screen.getByRole("table", { name: "Plan mapping" }))).toEqual([
      ["Lifestyle", "Spending group", "Spending in Lifestyle"],
      ["Plan It installments", "Plan payments", "Plan payments from installment plans"],
      ["Gifts", "Not mapped", "Nothing"],
    ]);
    expect(screen.getByText("How each plan group is compared with actuals, from the Oct 2026 plan.")).toBeTruthy();
    // The month by group, folded under the cash flow table.
    const details = screen.getByText(/Aug 2026 by group/).closest("details")!;
    expect(tableRows(within(details).getByRole("table", { name: "Aug 2026 spending by group" }))).toEqual([
      ["Lifestyle", "$500.00", "$510.00", "−$10.00", "Differs"],
    ]);
    expect(details.textContent).toContain("Counted on a payment basis$40.00");
    expect(details.textContent).toContain("Rows hidden or excluded by edits2");
    expect(details.textContent).toContain("Aug 2026 counts card payments on a payment basis.");
  });

  it("shows the one combined line for a run sent before the 1.3 groups, with its note", async () => {
    const note = "Aug 2026: groups changed in 1.3. This month's actual groups have no Transportation, so Lifestyle, Insurance and Transportation are compared as one line.";
    checks = {
      ...P4_CHECKS,
      cashflow: [
        {
          ...P4_CHECKS.cashflow[0]!,
          by_group: [{ key: "lifestyle-insurance-transportation", label: "Lifestyle, Insurance, Transportation", app_cents: 60_000, chat_cents: 60_000, diff_cents: 0, status: "match" }],
          notes: [note],
        },
      ],
    };
    renderWithProviders(<ChecksPage />, { route: "/checks" });
    // In the wide table and the phone list. The label is short, so a phone shows it whole.
    const labels = await screen.findAllByText("Lifestyle, Insurance, Transportation");
    expect(labels.length).toBeGreaterThan(0);
    expect(screen.getByText(note)).toBeTruthy();
  });

  it("says when every Phase 4 list is empty", async () => {
    renderWithProviders(<ChecksPage />, { route: "/checks" });
    expect(await screen.findByText("Nothing to watch. Every rule passes.")).toBeTruthy();
    expect(screen.getByText("None. Every transfer has both legs.")).toBeTruthy();
    expect(screen.getByText("None. Every expected statement is in.")).toBeTruthy();
    expect(screen.getByText("No plan to map yet.")).toBeTruthy();
    expect(screen.getByText("No utility bills yet.")).toBeTruthy();
    expect(screen.getByText("No rewards or trip data yet.")).toBeTruthy();
  });

  it("shows stale reward balances and the milestone and cap gaps", async () => {
    checks = {
      ...P4_CHECKS,
      rewards: {
        source_run_id: "2026-10-12",
        stale: [{ key: "example-hotels", name: "Example Hotels", what: "balance", as_of: "2026-06-30" }],
        counts: { to_review: 0, ambiguous: 0, returns: 0, trips: 0, over: 0, chat_says_used: 0, needs_a_look: 0, benefit_notes: 0, program_notes: 0 },
        gaps: [
          {
            kind: "milestone",
            key: "example-spend",
            name: "Example card spend milestone",
            as_of: "2026-09-30",
            app_cents: 530_000,
            chat_cents: 1_465_000,
            diff_cents: -935_000,
            status: "differs",
            note: "Example card: the rows reach Sep 10, 2026. The chat counts to Sep 30, 2026.",
          },
          {
            kind: "cap",
            key: "example-cap",
            name: "Example card fuel bonus",
            as_of: "2026-09-30",
            app_cents: 182_000,
            chat_cents: 182_040,
            diff_cents: -40,
            status: "match",
            note: null,
          },
        ],
      },
    };
    renderWithProviders(<ChecksPage />, { route: "/checks" });
    const stale = await screen.findByRole("list", { name: "Stale reward numbers" });
    expect(within(stale).getByRole("listitem").textContent).toBe("Example Hotels: the balance is from Jun 30, 2026, more than 90 days old.");
    expect(tableRows(screen.getByRole("table", { name: "Card spend from rows next to the chat's numbers" }))).toEqual([
      ["Example card spend milestone", "Sep 30", "$5,300.00", "$14,650.00", "Differs"],
      ["Example card fuel bonus", "Sep 30", "$1,820.00", "$1,820.40", "Match"],
    ]);
    expect(screen.getByText("Example card: the rows reach Sep 10, 2026. The chat counts to Sep 30, 2026.")).toBeTruthy();
    expect(screen.getByRole("link", { name: "See Rewards" }).getAttribute("href")).toBe("/rewards");
  });

  it("shows unknown trip keys and tagged rows next to trip items, keeping the rewards part", async () => {
    checks = {
      ...P4_CHECKS,
      trips: {
        trip_count: 2,
        unknown: [
          { key: "day-trip", status: "not_sent", rows: 1, plans: 0, flags: 0, redemptions: 0 },
          { key: "old-trip", status: "removed", rows: 0, plans: 1, flags: 1, redemptions: 0 },
        ],
        tagged: [
          { key: "lake", name: "Example lake", row_count: 2, out_cents: 26_347, posted_cents: 26_347, out_differs: false, deposits_cents: 0, settled_to_us_cents: 0, deposits_differ: false },
          { key: "ski", name: "Example ski", row_count: 0, out_cents: 0, posted_cents: -4_000, out_differs: true, deposits_cents: 0, settled_to_us_cents: 0, deposits_differ: false },
        ],
      },
    };
    renderWithProviders(<ChecksPage />, { route: "/checks" });
    const unknown = await screen.findByRole("list", { name: "Trip keys the app has no trip for" });
    const items = within(unknown).getAllByRole("listitem");
    expect(items[0]!.textContent).toBe("day-trip: 1 row names a trip no update has sent. See the rows");
    expect(within(items[0]!).getByRole("link", { name: "See the rows" }).getAttribute("href")).toBe("/transactions?trip=day-trip");
    expect(items[1]!.textContent).toBe("old-trip: 1 plan purchase and 1 flag name a trip a later update removed.");
    expect(tableRows(screen.getByRole("table", { name: "Tagged rows next to posted trip items and settled IOUs" }))).toEqual([
      ["Example lake", "$263.47", "$263.47", "$0.00", "$0.00", "Match"],
      ["Example ski", "$0.00", "−$40.00", "$0.00", "$0.00", "Differs"],
    ]);
    expect(screen.getByRole("region", { name: "Tagged rows next to posted trip items and settled IOUs" }).tabIndex).toBe(0);
    // A 44px tap target on phones.
    expect(screen.getByRole("link", { name: "Example lake" }).className).toContain("min-h-11");
    expect(screen.getByRole("link", { name: "See Travel" }).getAttribute("href")).toBe("/travel");
    expect(screen.getByRole("link", { name: "See Rewards" }).getAttribute("href")).toBe("/rewards");
  });

  it("words the card credit lines, each only when its count is above 0", () => {
    const none = { to_review: 0, ambiguous: 0, returns: 0, trips: 0, over: 0, chat_says_used: 0, needs_a_look: 0, benefit_notes: 0, program_notes: 0 };
    const texts = (c: typeof none) => creditCountLines(c).map((l) => l.text);
    expect(creditCountLines(none)).toEqual([]);
    // Returns and points credits for a trip wait in their own closed groups: not a check.
    expect(creditCountLines({ ...none, returns: 3, trips: 2 })).toEqual([]);
    expect(texts({ ...none, to_review: 1, over: 1, chat_says_used: 1, needs_a_look: 1, benefit_notes: 1, program_notes: 1 })).toEqual([
      "1 card credit to review",
      "1 cycle has more credits than its amount",
      "1 cycle the chat says is used has no credit row",
      "1 card credit needs a look",
      "1 benefit note needs a look",
      "1 program note needs a look",
    ]);
    expect(texts({ ...none, to_review: 5, ambiguous: 2, over: 2, chat_says_used: 3, needs_a_look: 4, benefit_notes: 2, program_notes: 3 })).toEqual([
      "5 card credits to review (2 match more than one benefit)",
      "2 cycles have more credits than their amount",
      "3 cycles the chat says are used have no credit row",
      "4 card credits need a look",
      "2 benefit notes need a look",
      "3 program notes need a look",
    ]);
    expect(texts({ ...none, to_review: 1, ambiguous: 1 })).toEqual(["1 card credit to review (1 matches more than one benefit)"]);
  });

  it("links the card credit lines to the review on Rewards", async () => {
    checks = {
      ...P4_CHECKS,
      rewards: {
        source_run_id: "2026-10-12",
        stale: [],
        gaps: [],
        counts: { to_review: 3, ambiguous: 1, returns: 1, trips: 0, over: 0, chat_says_used: 1, needs_a_look: 2, benefit_notes: 0, program_notes: 0 },
      },
    };
    renderWithProviders(<ChecksPage />, { route: "/checks" });
    const lines = await screen.findByRole("list", { name: "Card credits" });
    const links = within(lines).getAllByRole("link");
    expect(links.map((a) => a.textContent)).toEqual([
      "3 card credits to review (1 matches more than one benefit)",
      "1 cycle the chat says is used has no credit row",
      "2 card credits need a look",
    ]);
    expect(CREDITS_REVIEW).toBe("/rewards?tab=benefits&review=credits");
    for (const a of links) {
      expect(a.getAttribute("href")).toBe(CREDITS_REVIEW);
      expect(a.className).toContain("min-h-11");
    }
  });

  it("links benefit and program notes to the tab that lists them, not the review", async () => {
    const none = { to_review: 0, ambiguous: 0, returns: 0, trips: 0, over: 0, chat_says_used: 0, needs_a_look: 0, benefit_notes: 0, program_notes: 0 };
    const withCounts = (counts: typeof none): Checks => ({ ...P4_CHECKS, rewards: { source_run_id: "2026-10-12", stale: [], gaps: [], counts } });
    const links = async () =>
      within(await screen.findByRole("list", { name: "Card credits" }))
        .getAllByRole("link")
        .map((a) => [a.textContent, a.getAttribute("href")]);
    // A program duplicate only: it sits on the Points tab, never in the review drawer.
    checks = withCounts({ ...none, program_notes: 1 });
    const { unmount } = renderWithProviders(<ChecksPage />, { route: "/checks" });
    expect(await links()).toEqual([["1 program note needs a look", PROGRAM_NOTES]]);
    expect(PROGRAM_NOTES).toBe("/rewards");
    unmount();
    checks = withCounts({ ...none, benefit_notes: 2 });
    renderWithProviders(<ChecksPage />, { route: "/checks" });
    expect(await links()).toEqual([["2 benefit notes need a look", BENEFIT_NOTES]]);
    expect(BENEFIT_NOTES).toBe("/rewards?tab=benefits");
    // Lines to act on: not "Nothing to check".
    expect(screen.queryByText("Nothing to check. Every balance is recent.")).toBeNull();
  });

  it("says nothing to check when the counts are all 0", async () => {
    checks = {
      ...P4_CHECKS,
      rewards: {
        source_run_id: "2026-10-12",
        stale: [],
        gaps: [],
        counts: { to_review: 0, ambiguous: 0, returns: 2, trips: 1, over: 0, chat_says_used: 0, needs_a_look: 0, benefit_notes: 0, program_notes: 0 },
      },
    };
    renderWithProviders(<ChecksPage />, { route: "/checks" });
    expect(await screen.findByText("Nothing to check. Every balance is recent.")).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Card credits" })).toBeNull();
  });

  it("lists utility bills past due, payments with no bill and import warnings", async () => {
    checks = P4_CHECKS;
    renderWithProviders(<ChecksPage />, { route: "/checks" });
    const pastDue = await screen.findByRole("table", { name: "Utility bills past due with no payment" });
    expect(tableRows(pastDue)).toEqual([["Example Power", "Sep 26, 2026", "Example checking", "$174.25"]]);
    expect(tableRows(screen.getByRole("table", { name: "Utility payments with no bill" }))).toEqual([
      ["Aug 22, 2026", "Example Water", "Example checking", "$56.93"],
    ]);
    const warnings = screen.getByRole("list", { name: "Utility bill warnings" });
    expect(within(warnings).getByRole("listitem").textContent).toBe(
      "Example Power · period ending Jul 5, 2025The line charges plus the credits differ from the total by 1 or 2 cents.",
    );
    expect(screen.getByRole("link", { name: "See the bills on Cash Flow" }).getAttribute("href")).toBe("/cash-flow?tab=utilities");
  });

  it("asks for a run from the URL", async () => {
    renderWithProviders(<ChecksPage />, { route: "/checks?run_id=2026-09-14" });
    await screen.findByText("From the Oct 12, 2026 update · Balances as of Sep 30, 2026");
    expect(fetchMock).toHaveBeenCalledWith("/api/checks?run_id=2026-09-14", expect.anything());
  });

  it("shows an empty state when no run has net worth numbers", async () => {
    checks = "missing";
    renderWithProviders(<ChecksPage />, { route: "/checks" });
    expect(await screen.findByText("Nothing to check yet.")).toBeTruthy();
    expect(screen.getByText(/once a monthly run with net worth numbers is imported/)).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows a retry banner on other errors", async () => {
    checks = "error";
    renderWithProviders(<ChecksPage />, { route: "/checks" });
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Could not load the checks.");
  });

  it("is reachable from Statements and Settings", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/auth/me") return json(200, { username: "sam", display_name: "Sam", role: "member" });
        if (url === "/api/imports") return json(200, { runs: [] });
        return json(404, { detail: "Not found." });
      }),
    );
    const first = renderWithProviders(<AppRoutes />, { route: "/statements" });
    await waitFor(() => expect(screen.getByRole("link", { name: "Checks" }).getAttribute("href")).toBe("/checks"));
    first.unmount();
    renderWithProviders(<AppRoutes />, { route: "/settings" });
    await waitFor(() => expect(screen.getByRole("link", { name: /^Checks/ }).getAttribute("href")).toBe("/checks"));
  });
});

describe("check helpers", () => {
  it("builds rows and summaries", () => {
    expect(balanceRows(CHECKS).map((r) => r.label)).toEqual([
      "Net worth",
      "Assets",
      "Liabilities",
      "Home equity",
      "Credit cards",
      "Vehicles",
    ]);
    expect(cashflowRows(CHECKS.cashflow).map((r) => r.status)).toEqual(["chat_only", "chat_only", "chat_only"]);
    const matchAll = balanceRows({ ...CHECKS, liabilities: CHECKS.assets, parts: [] });
    expect(checkSummary(matchAll)).toBe("All 3 lines match.");
    expect(diffText(null)).toBe("");
    expect(diffText(1234)).toBe("+$12.34");
    expect(diffText(-5)).toBe("−$0.05");
  });
});

describe("category emoji", () => {
  it("shows each category's emoji in a fixed slot in the transactions table", () => {
    const emoji = emojiMap({
      groups: [
        {
          group_key: "lifestyle",
          name: "Lifestyle",
          categories: [
            { category_key: "groceries", name: "Groceries", emoji: "🛒", kind: "spending" },
            { category_key: "misc", name: "Misc", emoji: null, kind: "spending" },
          ],
        },
      ],
    });
    expect(emoji.get("groceries")).toBe("🛒");
    expect(emoji.has("misc")).toBe(false);
    const { container } = renderWithProviders(<CategoryLabel name="Groceries" emoji="🛒" />);
    const slot = container.querySelector('[aria-hidden="true"]');
    expect(slot?.textContent).toBe("🛒");
    expect(slot?.className).toContain("w-5");
  });
});
