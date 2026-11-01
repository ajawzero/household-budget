import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { HBarRow } from "../../components/charts/HBars";
import type { Dashboard } from "../../lib/api";
import { formatMoney, formatMonth } from "../../lib/format";
import { renderWithProviders } from "../../test/render";
import { payoffCell, spendingNote, withShares } from "./BarCards";
import { DashboardPage } from "./DashboardPage";
import { behindText } from "./StatTiles";
import { DASHBOARD, DASHBOARD_P4, EMPTY_DASHBOARD } from "./fixtures";
import { creditsEndingDetail, creditsEndingText, nextTripDetail, nextTripText, upcomingDays, utilityLineText } from "./UpcomingCard";
import { aprText, greeting, greetingTitle, monthlyDeltaText } from "./format";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

let payload: Dashboard | "error" | "pending" = DASHBOARD;
let me: { username: string; display_name: string; role: string } | null = null;
const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input);
  if (url === "/api/auth/me") return me ? json(200, me) : json(401, { detail: "Not signed in." });
  if (url.startsWith("/api/dashboard")) {
    if (payload === "pending") return new Promise<Response>(() => {});
    if (payload === "error") return json(500, { detail: "Server error." });
    return json(200, payload);
  }
  return json(404, { detail: "Not found." });
});

beforeEach(() => {
  payload = DASHBOARD;
  me = { username: "alex", display_name: "Alex", role: "admin" };
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** The card (a <section>) whose heading starts with this text. */
async function card(title: string): Promise<HTMLElement> {
  await waitFor(() => expect(screen.queryAllByRole("status", { name: "Loading" })).toHaveLength(0));
  const heading = await screen.findByRole("heading", { level: 2, name: new RegExp(`^${title}( ?(plan|actual))?$`) });
  const section = heading.closest("section");
  if (!section) throw new Error(`No card for ${title}`);
  return section;
}

/** Opens "Show the numbers" in a card and returns the body rows as cell texts. */
async function tableRows(section: HTMLElement): Promise<string[][]> {
  await userEvent.setup().click(within(section).getByRole("button", { name: "Show the numbers" }));
  const table = within(section).getByRole("table");
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((tr) => Array.from(tr.children).map((c) => c.textContent ?? ""));
}

/** The bar rows drawn inside the chart (label and value text). */
function barRows(section: HTMLElement): string[] {
  const chart = within(section).getByRole("img");
  return within(chart)
    .getAllByRole("listitem")
    .map((li) => li.firstElementChild?.textContent ?? "");
}

/** The line under each bar, or null. */
function barNotes(section: HTMLElement): (string | null)[] {
  const chart = within(section).getByRole("img");
  return within(chart)
    .getAllByRole("listitem")
    .map((li) => li.querySelector("p")?.textContent ?? null);
}

describe("DashboardPage", () => {
  it("greets the signed-in user and shows the tiles, plan numbers labeled plan", async () => {
    renderWithProviders(<DashboardPage />);
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1 }).textContent).toMatch(/^Good (morning|afternoon|evening), Alex$/),
    );
    const nw = await card("Net worth");
    expect(within(nw).getByText("$400,000")).toBeTruthy();
    expect(within(nw).getByText(/\$5,000/).className).toContain("text-good");
    expect(within(nw).getByText("vs 30 days ago")).toBeTruthy();
    expect(within(nw).getByText("Some balances are missing or old.")).toBeTruthy();
    const behind = within(nw).getByRole("list", { name: "Balances missing or old" });
    expect(within(behind).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "New card: no balance yet.",
      "Sample brokerage: last statement Aug 31, 2026, the next was due Sep 30, 2026.",
      "Home: last balance Jun 1, 2026, an update was due Sep 1, 2026.",
    ]);

    for (const [label, value] of [
      ["Income", "$10,000"],
      ["Spending", "$8,000"],
      ["Left over", "$2,000"],
      ["% saved", "29%"],
    ] as const) {
      const tile = await card(label);
      expect(within(tile).getByRole("heading").textContent).toBe(`${label}plan`);
      expect(within(tile).getByText(value)).toBeTruthy();
    }
    expect(document.title).toBe("Dashboard · Your household");
  });

  it("shows budget status in words, with cycle notes and the statements warning", async () => {
    renderWithProviders(<DashboardPage />);
    const budget = await card("Budget watch");
    const over = within(budget).getByText("Over by $50");
    expect(over.className).toContain("text-bad");
    expect(within(budget).getByText("On target")).toBeTruthy();
    expect(within(budget).getByText("Under by $100")).toBeTruthy();
    expect(within(budget).getByText("No actual yet")).toBeTruthy();
    expect(within(budget).getByText("No actual / $250")).toBeTruthy();
    expect(within(budget).getByText("$550 / $500")).toBeTruthy();
    expect(within(budget).getByText("Cycle closes on the 22nd")).toBeTruthy();
    expect(within(budget).getByText("Statements needed. This number may be old.")).toBeTruthy();
    expect(within(budget).getByText("The tick mark is the target. A bar past the tick is over budget.")).toBeTruthy();
  });

  it("budget table matches the meters", async () => {
    renderWithProviders(<DashboardPage />);
    const rows = await tableRows(await card("Budget watch"));
    expect(rows).toEqual([
      ["Dining", "$550", "$500", "Over by $50"],
      ["Groceries", "$980", "$1,000", "On target"],
      ["Store cards", "$200", "$300", "Under by $100"],
      ["Fuel", "No actual yet", "$250", "No actual yet"],
    ]);
  });

  it("cash flow table matches the chart data", async () => {
    renderWithProviders(<DashboardPage />);
    const section = await card("Cash flow");
    expect(within(section).getByRole("heading").textContent).toBe("Cash flowplan");
    expect(within(section).getByText("Example plan, Oct to Dec")).toBeTruthy();
    const rows = await tableRows(section);
    expect(rows).toEqual(
      DASHBOARD.cashflow.map((m) => [
        formatMonth(m.month),
        formatMoney(m.income_cents),
        formatMoney(m.spending_cents),
        formatMoney(m.saved_invested_cents),
        formatMoney(m.left_over_cents),
      ]),
    );
    expect(rows[1]?.[4]).toBe("−$500");
  });

  it("bar tables match the bars, in the same order", async () => {
    renderWithProviders(<DashboardPage />);
    for (const title of ["Spending by group", "Where the net worth is", "Debt balances"]) {
      const section = await card(title);
      const bars = barRows(section);
      const rows = await tableRows(section);
      expect(rows.length).toBe(bars.length);
      // Same rows, same order, whole dollars in both.
      rows.forEach((cells, i) => expect(bars[i]?.startsWith(cells[0] ?? "")).toBe(true));
      rows.forEach((cells, i) => expect(bars[i]?.startsWith(`${cells[0]}${cells[1]}`)).toBe(true));
    }
  });

  it("draws saved and invested as its own bar, apart from spending, and ties the groups to planned spending", async () => {
    renderWithProviders(<DashboardPage />);
    const section = await card("Spending by group");
    // Each bar's share of all bars shown, saved and invested included. The shares add up to 100.0%.
    expect(barRows(section)).toEqual([
      "Lifestyle$3,000 37.5%",
      "Housing$2,000 25.0%",
      "Insurance$400 5.0%",
      "Kids$1,700 21.3%",
      "Saved and invested$900 11.2%",
    ]);
    // Spending bars are orange; the saved and invested bar is the left over green.
    const fills = within(within(section).getByRole("img"))
      .getAllByRole("listitem")
      .map((li) => (li.querySelector("[aria-hidden] > span") as HTMLElement | null)?.style.background);
    expect(fills).toEqual([...Array(4).fill("var(--chart-spending)"), "var(--chart-leftover)"]);
    expect(within(section).getByText("A month, in the Oct 2026 plan")).toBeTruthy();
    const legend = within(section).getByRole("list", { name: "Bar colors" });
    expect(within(legend).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Spending", "Saved and invested"]);
    expect(within(section).getByText(/Saved and invested \(\$900 a month\) is part of planned spending/)).toBeTruthy();
    expect(within(section).getByText("The groups add up to the $8,000 planned spending.")).toBeTruthy();
    const rows = await tableRows(section);
    expect(rows.at(-1)).toEqual(["Saved and invested", "$900", "11.2%", "Saved and invested", "Transfers: College 529"]);
    expect(rows[0]).toEqual(["Lifestyle", "$3,000", "37.5%", "Spending", "Categories: Groceries, Dining, Online shopping, and 7 more"]);
  });

  it("says what is in each group under its bar, with no hover needed", async () => {
    renderWithProviders(<DashboardPage />);
    const section = await card("Spending by group");
    expect(barNotes(section)).toEqual([
      "Categories: Groceries, Dining, Online shopping, and 7 more",
      "Bills: Mortgage, Electric, Internet, and 4 more",
      "Categories: Life insurance, Auto and home insurance",
      null,
      "Transfers: College 529",
    ]);
    // The hover tip holds only the share now.
    const first = within(within(section).getByRole("img")).getAllByRole("listitem")[0]!;
    expect(first.getAttribute("title")).toBe("37.5% of the total");
  });

  it("says when the groups do not add up to the planned spending", () => {
    const groups = DASHBOARD.spending_by_group;
    expect(spendingNote(groups, 800_000)).toBe("The groups add up to the $8,000 planned spending.");
    expect(spendingNote(groups, 900_000)).toBe(
      "The groups add up to $8,000. Planned spending is $9,000. The plan does not split the rest.",
    );
    expect(spendingNote(groups, 700_000)).toBe("The groups add up to $8,000. Planned spending is $7,000.");
    expect(spendingNote(groups, undefined)).toBeNull();
  });

  it("says the Spending tile includes saved and invested, and how left over and % saved count", async () => {
    renderWithProviders(<DashboardPage />);
    expect((await card("Spending")).textContent).toContain("Includes $900 saved and invested");
    expect((await card("Left over")).textContent).toContain("Income minus planned spending");
    expect((await card("% saved")).textContent).toContain("Left over plus saved and invested");
  });

  it("gives exact dollars in the net worth parts table", async () => {
    renderWithProviders(<DashboardPage />);
    const rows = await tableRows(await card("Where the net worth is"));
    expect(rows[0]).toEqual(["Home equity", "$300,000", "66.7%"]);
  });

  it("dates the net worth tile by its newest balance, not today", async () => {
    renderWithProviders(<DashboardPage />);
    expect(await screen.findByText("balances through Sep 30")).toBeTruthy();
    expect(screen.queryByText("as of Oct 21")).toBeNull();
  });

  it("shows net worth parts with owed amounts negative, and debts with APR", async () => {
    renderWithProviders(<DashboardPage />);
    // Parts are net of their loans, so shares are of the parts above zero only. Owed parts get none.
    const parts = await card("Where the net worth is");
    expect(barRows(parts)).toEqual(["Home equity$300,000 66.7%", "Retirement$150,000 33.3%", "Mortgage−$50,000"]);
    expect(within(parts).getByText(/Owed amounts are negative\. Home and vehicle parts are net of their loans\. Shares are of the parts above zero\.$/)).toBeTruthy();
    // The hover says what each share is of. The same share is on screen too.
    const partItems = within(within(parts).getByRole("img")).getAllByRole("listitem");
    expect(partItems.map((li) => li.getAttribute("title"))).toEqual(["66.7% of the parts above zero", "33.3% of the parts above zero", null]);
    const debts = await card("Debt balances");
    expect(barRows(debts)).toEqual(["Mortgage$190,000 · 4.5% APR 99.5%", "Blue card$1,000 · 21.99% APR 0.5%"]);
    expect(within(debts).getByText("$191,000 owed in total")).toBeTruthy();
    const debtRows = await tableRows(debts);
    expect(within(within(debts).getByRole("table")).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Debt",
      "Owed",
      "Share",
      "APR",
      "Payoff",
    ]);
    expect(debtRows).toEqual([
      ["Mortgage", "$190,000", "99.5%", "4.5%", "Jun 1, 2049"],
      ["Blue card", "$1,000", "0.5%", "21.99%", "Not set"],
    ]);
  });

  it("says Was before a payoff date that is past", () => {
    expect(payoffCell({ payoff_date: "2049-06-01", payoff_ahead: true })).toBe("Jun 1, 2049");
    expect(payoffCell({ payoff_date: "2026-03-01", payoff_ahead: false })).toBe("Was Mar 1, 2026");
    expect(payoffCell({ payoff_date: null, payoff_ahead: false })).toBe("");
  });

  it("gives each side its own shares, and none to refunds or zero rows", () => {
    const row = (key: string, cents: number): HBarRow => ({ key, label: key, cents });
    // A negative spending row (refunds) gets no share and is not in the total.
    expect(withShares([row("a", 300), row("b", 100), row("r", -50), row("z", 0)], false).map((r) => r.share)).toEqual([75, 25, null, null]);
    const parts = withShares([row("a", 200), row("b", 100), row("c", -30), row("d", -10)], true);
    expect(parts.map((r) => r.share)).toEqual([66.7, 33.3, 75, 25]);
    expect(withShares([row("only", 100)], false)[0]!.share).toBe(100);
    expect(withShares([row("z", 0)], true)[0]!.share).toBeNull();
    expect(withShares([], true)).toEqual([]);
  });

  it("shows next changes with signed monthly amounts", async () => {
    renderWithProviders(<DashboardPage />);
    const section = await card("Next changes");
    expect(within(section).getByText("+$105 a month").className).toContain("text-good");
    expect(within(section).getByText("−$50 a month").className).toContain("text-bad");
    expect(within(section).getByText("From Dec 2026")).toBeTruthy();
    // Format 1.4: the sofa change is confirmed; the rent change leaves its status out.
    expect(within(section).getByText("Sofa plan ends").closest("li")!.textContent).toContain("Confirmed");
    const rent = within(section).getByText("Storage rent goes up").closest("li")!.textContent ?? "";
    expect(["Confirmed", "Expected", "Target", "Suggested"].filter((w) => rent.includes(w))).toEqual([]);
  });

  it("badges each next change with its status word", async () => {
    const base = DASHBOARD.next_changes[0]!;
    payload = {
      ...DASHBOARD,
      next_changes: [
        { ...base, key: "a", label: "Device plan ends", status: "expected" },
        { ...base, key: "b", label: "Trim dining out", status: "target" },
        { ...base, key: "c", label: "Cheaper phone plan", status: "suggested" },
      ],
    };
    renderWithProviders(<DashboardPage />);
    const section = await card("Next changes");
    const row = (label: string) => within(section).getByText(label).closest("li")!;
    expect(within(row("Device plan ends")).getByText("Expected")).toBeTruthy();
    expect(within(row("Trim dining out")).getByText("Target")).toBeTruthy();
    expect(within(row("Cheaper phone plan")).getByText("Suggested")).toBeTruthy();
  });

  it("lists to-dos read only and marks done ones", async () => {
    renderWithProviders(<DashboardPage />);
    const section = await card("To do");
    expect(within(section).queryByRole("button")).toBeNull();
    expect(within(section).queryByRole("checkbox")).toBeNull();
    expect(within(section).getByText("Due Oct 31")).toBeTruthy();
    expect(within(section).getByText("File the claim").textContent).toBe("File the claim (done)");
  });

  it("shows the latest review as text", async () => {
    renderWithProviders(<DashboardPage />);
    const section = await card("Latest review");
    expect(within(section).getByText("A steady month.")).toBeTruthy();
    expect(within(section).getByText("$400,000")).toBeTruthy();
    expect(within(section).getByText("The store card is closed.")).toBeTruthy();
    expect(within(section).getByText("Two same charges on one day.")).toBeTruthy();
    const top = within(section).getByRole("heading", { name: "Top three" }).nextElementSibling;
    expect(top?.tagName).toBe("OL");
    expect(top?.children).toHaveLength(3);
  });

  it("renders text from the chat as text, never as markup", async () => {
    payload = { ...DASHBOARD, review: { ...DASHBOARD.review!, summary: "<b>bold</b> <img src=x>" } };
    renderWithProviders(<DashboardPage />);
    const section = await card("Latest review");
    expect(within(section).getByText("<b>bold</b> <img src=x>")).toBeTruthy();
    expect(section.querySelector("b, img")).toBeNull();
  });

  it("shows an empty state in every card when there is nothing yet", async () => {
    payload = EMPTY_DASHBOARD;
    renderWithProviders(<DashboardPage />);
    for (const text of [
      "No cash flow plan yet.",
      "No spending plan for this month.",
      "No budgets to watch yet.",
      "No balances yet.",
      "No debts.",
      "No changes coming up.",
      "Nothing to do.",
      "No review yet.",
      "Nothing due in the next 30 days.",
    ]) {
      expect(await screen.findByText(text)).toBeTruthy();
    }
    expect(screen.getAllByText("No plan for this month yet")).toHaveLength(4);
    expect(screen.queryByRole("button", { name: "Show the numbers" })).toBeNull();
  });

  it("shows skeletons while loading", () => {
    payload = "pending";
    renderWithProviders(<DashboardPage />);
    // 5 tiles plus 9 cards.
    expect(screen.getAllByRole("status", { name: "Loading" })).toHaveLength(14);
  });

  it("shows one error banner with Retry when the request fails", async () => {
    payload = "error";
    renderWithProviders(<DashboardPage />);
    const alert = await screen.findByRole("alert");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(alert.textContent).toContain("Could not load the dashboard.");
    // 5 tiles and 9 cards stay as quiet frames
    expect(screen.getAllByText("Not loaded.")).toHaveLength(14);

    payload = DASHBOARD;
    await userEvent.setup().click(within(alert).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
    expect(screen.queryByText("Not loaded.")).toBeNull();
  });
});

describe("DashboardPage, Phase 4", () => {
  beforeEach(() => {
    payload = DASHBOARD_P4;
  });

  it("shows the latest complete month's actuals with the plan beside them", async () => {
    renderWithProviders(<DashboardPage />);
    const income = await card("Income");
    expect(income.textContent).toBe("Income$10,500Aug 2026 actualOct plan $10,000");
    // Spending counts saved and invested, like the plan, so the two line up.
    expect((await card("Spending and saved")).textContent).toBe("Spending and saved$6,900Aug 2026 actualIncludes $900 savedOct plan $8,000");
    expect((await card("Left over")).textContent).toBe("Left over$3,600Aug 2026 actualOct plan $2,000");
    expect((await card("% saved")).textContent).toBe("% saved42.9%Aug 2026 actualOct plan 29%");
  });

  it("marks cash flow as actual then plan, with a basis column that matches the chart data", async () => {
    renderWithProviders(<DashboardPage />);
    const section = await card("Cash flow");
    expect(within(section).getByRole("heading").textContent).toBe("Cash flow");
    expect(within(section).getByText("Actual months, then the plan (hatched). Spending counts saved and invested, like the plan. Sep 2026 is partial (pale): statements not all in.")).toBeTruthy();
    expect(within(section).getByRole("img").getAttribute("aria-label")).toContain("2 actual months (Sep 2026 partial), then 3 plan months");
    const rows = await tableRows(section);
    expect(rows).toEqual(
      DASHBOARD_P4.cashflow.map((m) => [
        formatMonth(m.month),
        formatMoney(m.income_cents),
        formatMoney(m.spending_cents),
        formatMoney(m.saved_invested_cents),
        formatMoney(m.left_over_cents),
        m.basis === "plan" ? "Plan" : m.complete ? "Actual" : "Actual, partial",
      ]),
    );
  });

  it("shows actual spending by group first, with a switch to the plan", async () => {
    renderWithProviders(<DashboardPage />);
    const section = await card("Spending by group");
    expect(within(section).getByRole("heading").textContent).toBe("Spending by groupactual");
    expect(barRows(section)).toEqual(["Housing$4,000 58.0%", "Lifestyle$2,000 29.0%", "Saved and invested$900 13.0%"]);
    expect(within(section).getByText("Aug 2026, the latest complete month: $6,900 with saved and invested")).toBeTruthy();
    expect(barNotes(section)).toEqual(["Bills: Mortgage, Electric", "Categories: Groceries, Dining, Online shopping, and 7 more", null]);
    const rows = await tableRows(section);
    expect(rows[0]).toEqual(["Housing", "$4,000", "58.0%", "Spending", "Bills: Mortgage, Electric"]);
    await userEvent.setup().click(within(section).getByRole("radio", { name: "Plan" }));
    expect(within(section).getByRole("heading").textContent).toBe("Spending by groupplan");
    expect(barRows(section)[0]).toBe("Lifestyle$3,000 37.5%");
    expect(barNotes(section)[0]).toBe("Categories: Groceries, Dining, Online shopping, and 7 more");
  });

  it("lists upcoming bills and plan payments in date bands with day totals", async () => {
    renderWithProviders(<DashboardPage />);
    const section = await card("Upcoming bills");
    expect(within(section).getByText("11 due in the next 30 days, $255 in all")).toBeTruthy();
    const oct22 = within(section).getByRole("region", { name: "Due Oct 22" });
    expect(within(oct22).getByRole("heading").textContent).toBe("Thu, Oct 22$165.00");
    expect(oct22.textContent).toContain("Example sofaPlan payment");
    // Eight rows at most, then a link to Recurring.
    expect(section.querySelectorAll('section[aria-label^="Due"] li')).toHaveLength(8);
    expect(within(section).getByRole("link", { name: "See 3 more on Recurring" }).getAttribute("href")).toBe("/recurring");
  });

  it("lists trip payments with a badge and a link to the trip (format 1.4)", async () => {
    const tripRow = { date: "2026-10-22", kind: "trip" as const, key: "rental-car", name: "Rental car", amount_cents: 30_000, account_name: "Summit card", trip_key: "ski-weekend", trip_name: "Ski weekend" };
    payload = { ...DASHBOARD_P4, upcoming: [...DASHBOARD_P4.upcoming.slice(0, 2), tripRow] };
    renderWithProviders(<DashboardPage />);
    const section = await card("Upcoming bills");
    const oct22 = within(section).getByRole("region", { name: "Due Oct 22" });
    expect(within(oct22).getByRole("heading").textContent).toBe("Thu, Oct 22$465.00"); // the day total counts it
    expect(oct22.textContent).toContain("Rental carTrip payment");
    const link = within(oct22).getByRole("link", { name: /Rental car/ });
    expect(link.getAttribute("href")).toBe("/travel?trip=ski-weekend");
    expect(within(link).getByText("Ski weekend · Summit card")).toBeTruthy();
    expect(within(link).getByText("$300.00")).toBeTruthy();
    // Bill and plan rows stay plain rows.
    expect(within(oct22).getAllByRole("link")).toHaveLength(1);
  });

  it("keys trip rows by trip, so the same item key on two trips the same day is two rows", async () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    const row = (trip: string) => ({ date: "2026-10-22", kind: "trip" as const, key: "flights", name: "Flights", amount_cents: 10_000, account_name: null, trip_key: trip, trip_name: trip });
    payload = { ...DASHBOARD_P4, upcoming: [row("ski-weekend"), row("beach-week")] };
    renderWithProviders(<DashboardPage />);
    const section = await card("Upcoming bills");
    const oct22 = within(section).getByRole("region", { name: "Due Oct 22" });
    expect(within(oct22).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(["/travel?trip=ski-weekend", "/travel?trip=beach-week"]);
    expect(errors.mock.calls.flat().join(" ")).not.toMatch(/same key/);
    errors.mockRestore();
  });

  it("shows one line per utility under the upcoming bills", async () => {
    renderWithProviders(<DashboardPage />);
    const section = await card("Upcoming bills");
    const utilities = within(section).getByRole("region", { name: "Utilities" });
    expect(utilities.textContent).toContain("Example Power");
    expect(utilities.textContent).toMatch(/Next bill expected Oct 6(, with the next import)? · 12-month average \$137$/);
    const link = within(utilities).getByRole("link", { name: /Example Power/ });
    expect(link.getAttribute("href")).toBe("/cash-flow?tab=utilities&utility=example-power");
  });

  it("shows the next trip next to the credits line, linking to Travel", async () => {
    renderWithProviders(<DashboardPage />);
    const section = await card("Upcoming bills");
    const trip = within(section).getByRole("region", { name: "Next trip" });
    const link = within(trip).getByRole("link", { name: /Example trip/ });
    expect(link.getAttribute("href")).toBe("/travel?trip=example-trip");
    expect(link.textContent).toContain("Example trip · Jan 15, 2027");
    expect(link.textContent).toContain("$451 so far · $300 to $420 still to spend · 1 TBD");
    expect(within(section).getByRole("region", { name: "Card credits" })).toBeTruthy();
    const month = { key: "b", name: "Beach week", status: "planning" as const, start_date: null, month: "2027-06", cash_cents: 0, estimate_low_cents: 0, estimate_high_cents: 0, tbd_count: 0 };
    expect(nextTripText(month)).toBe("Beach week · Jun 2027");
    expect(nextTripDetail(month)).toBe("$0 so far");
  });

  it("shows one line when card credits end within 14 days, linking to Rewards, Benefits", async () => {
    renderWithProviders(<DashboardPage />);
    const section = await card("Upcoming bills");
    const credits = within(section).getByRole("region", { name: "Card credits" });
    const link = within(credits).getByRole("link", { name: /2 credits end soon/ });
    expect(link.getAttribute("href")).toBe("/rewards?tab=benefits");
    expect(link.textContent).toContain("$35.00 not used yet, the first ends Oct 31");
    const one = { count: 1, left_cents: 1_000, first_end: "2026-10-31" };
    expect(creditsEndingText(one)).toBe("1 credit ends soon");
    expect(creditsEndingDetail(one)).toBe("$10.00 not used yet, ends Oct 31");
  });

  it("says a utility bill is late only when an import after its day did not have it", () => {
    const u = DASHBOARD_P4.utilities[0]!;
    expect(utilityLineText(u, "2026-10-01")).toBe("Next bill expected Oct 6 · 12-month average $137");
    expect(utilityLineText(u, "2026-10-06")).toBe("Next bill expected Oct 6 · 12-month average $137");
    // The day passed, but bills come with the imports: not late yet.
    expect(utilityLineText(u, "2026-10-07")).toBe("Next bill expected Oct 6, with the next import · 12-month average $137");
    expect(utilityLineText({ ...u, next_bill_late: true }, "2026-10-20")).toBe("Next bill is late, expected Oct 6 · 12-month average $137");
    expect(utilityLineText({ ...u, next_bill_date: null, avg_bill_cents: null }, "2026-10-07")).toBe("");
  });

  it("groups upcoming items by day", () => {
    const days = upcomingDays(DASHBOARD_P4.upcoming.slice(0, 3));
    expect(days.map((d) => [d.date, d.items.length, d.totalCents])).toEqual([
      ["2026-10-22", 2, 16_500],
      ["2026-10-23", 1, 1_000],
    ]);
  });
});

describe("dashboard format helpers", () => {
  it("formats APR without trailing zeros", () => {
    expect(aprText(21_990)).toBe("21.99%");
    expect(aprText(4_500)).toBe("4.5%");
    expect(aprText(10_000)).toBe("10%");
    expect(aprText(null)).toBe("");
  });

  it("signs monthly changes with a real minus", () => {
    expect(monthlyDeltaText(10_500)).toBe("+$105 a month");
    expect(monthlyDeltaText(-5_000)).toBe("−$50 a month");
    expect(monthlyDeltaText(0)).toBe("No change");
  });

  it("greets by household time", () => {
    expect(greeting(new Date("2026-10-21T14:00:00Z"))).toBe("Good morning"); // 9 am Central
    expect(greeting(new Date("2026-10-21T19:00:00Z"))).toBe("Good afternoon");
    expect(greeting(new Date("2026-10-22T01:00:00Z"))).toBe("Good evening");
    // Edges in Chicago time: 4:59 pm CDT is afternoon, 5 pm is evening.
    expect(greeting(new Date("2026-10-21T21:59:00Z"))).toBe("Good afternoon");
    expect(greeting(new Date("2026-10-21T22:00:00Z"))).toBe("Good evening");
    expect(greeting(new Date("2026-12-21T17:59:00Z"))).toBe("Good morning"); // 11:59 am CST
  });

  it("welcomes back when the user has no display name", async () => {
    me = { username: "alex", display_name: "", role: "admin" };
    renderWithProviders(<DashboardPage />);
    await card("Net worth");
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Welcome back");
  });

  it("greets by display name, or welcomes back with no name", () => {
    const nine = new Date("2026-10-21T14:00:00Z");
    expect(greetingTitle("Alex", nine)).toBe("Good morning, Alex");
    expect(greetingTitle("  ", nine)).toBe("Welcome back");
    expect(greetingTitle(null, nine)).toBe("Welcome back");
    expect(greetingTitle(undefined, nine)).toBe("Welcome back");
  });
});

describe("behindText", () => {
  it("leaves out the due date when the account never goes stale on its own", () => {
    expect(
      behindText({ account_key: "x", name: "Old fund", reason: "overdue", as_of: "2025-12-31", source: "statement", due: null }),
    ).toBe("Old fund: last statement Dec 31, 2025.");
    expect(behindText({ account_key: "y", name: "Van", reason: "overdue", as_of: "2026-05-01", source: "manual", due: null })).toBe(
      "Van: last balance May 1, 2026.",
    );
  });
});
