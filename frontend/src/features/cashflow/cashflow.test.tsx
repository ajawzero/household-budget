import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { addMonths, formatMoney, formatMonth, thisMonth } from "../../lib/format";
import { renderWithProviders } from "../../test/render";
import { isMonth } from "./api";
import { CashflowPage, rangeFor } from "./CashflowPage";
import { toChartRows } from "./CashflowChart";
import { OTHER_KEY, ringSlices } from "./colors";
import { contentsText } from "./parts";
import {
  BREAKDOWN_CATEGORY,
  BREAKDOWN_GROUP,
  BREAKDOWN_MERCHANT,
  CASHFLOW,
  CATEGORY_GROCERIES,
  INCOME,
  PLAN,
} from "./fixtures";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

let cashflowStatus = 200;
let cashflowBody: unknown = CASHFLOW;
let planBody: unknown = PLAN;
const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input);
  if (url.startsWith("/api/cashflow/breakdown")) {
    const by = new URL(url, "http://x").searchParams.get("by");
    return json(200, by === "group" ? BREAKDOWN_GROUP : by === "merchant" ? BREAKDOWN_MERCHANT : BREAKDOWN_CATEGORY);
  }
  if (url.startsWith("/api/cashflow/category/groceries")) return json(200, CATEGORY_GROCERIES);
  if (url.startsWith("/api/cashflow/plan")) return json(200, planBody);
  if (url.startsWith("/api/cashflow")) return cashflowStatus === 200 ? json(200, cashflowBody) : json(500, { detail: "Server error." });
  if (url.startsWith("/api/income")) return json(200, INCOME);
  return json(404, { detail: "Not found." });
});

beforeEach(() => {
  cashflowStatus = 200;
  cashflowBody = CASHFLOW;
  planBody = PLAN;
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function urls(): string[] {
  return fetchMock.mock.calls.map((c) => String(c[0]));
}

/** The card (a <section>) holding this eyebrow or title text. */
async function card(text: string): Promise<HTMLElement> {
  const el = await screen.findByText(text, { selector: "p.eyebrow, h2" });
  const section = el.closest("section");
  if (!section) throw new Error(`No card for ${text}`);
  await waitFor(() => {
    expect(within(section).queryAllByRole("status", { name: "Loading" })).toHaveLength(0);
    expect(within(section).queryByText("Loading…")).toBeNull();
  });
  return section;
}

/** Opens "Show the numbers" and returns body rows as cell texts. */
async function tableRows(section: HTMLElement): Promise<string[][]> {
  await userEvent.setup().click(within(section).getByRole("button", { name: "Show the numbers" }));
  const table = within(section).getByRole("table");
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((tr) => Array.from(tr.children).map((c) => c.textContent ?? ""));
}

describe("month helpers", () => {
  it("adds months across years and checks month strings", () => {
    expect(addMonths("2026-10", 3)).toBe("2027-01");
    expect(addMonths("2026-01", -1)).toBe("2025-12");
    expect(isMonth("2026-09")).toBe(true);
    expect(isMonth("2026-13")).toBe(false);
    expect(isMonth("x")).toBe(false);
    expect(thisMonth(new Date("2026-11-01T03:00:00Z"))).toBe("2026-10"); // still Oct 31 in Chicago
  });

  it("turns range keys into from and to", () => {
    expect(rangeFor("12m", "2026-10")).toEqual({});
    expect(rangeFor("6m", "2026-10")).toEqual({ from: "2026-05", to: "2026-10" });
    expect(rangeFor("ytd", "2026-10")).toEqual({ from: "2026-01", to: "2026-10" });
  });
});

describe("toChartRows", () => {
  const rows = toChartRows(CASHFLOW.months);

  it("draws this month from the plan, with planned spending less saved and invested", () => {
    const oct = rows.find((r) => r.month === "2026-10")!;
    expect(oct.basis).toBe("plan");
    expect(oct.income).toBe(1_100_000);
    expect(oct.spending).toBe(860_000 - 70_000);
    expect(oct.leftOver).toBe(240_000);
  });

  it("keeps actuals for past months, marks partial ones and keeps the plan for ticks", () => {
    const jul = rows.find((r) => r.month === "2026-07")!;
    expect(jul.basis).toBe("actual");
    expect(jul.spending).toBe(350_000);
    expect(jul.planIncome).toBe(950_000);
    expect(jul.planSpending).toBe(400_000);
    expect(rows.find((r) => r.month === "2026-09")!.basis).toBe("partial");
    expect(rows.find((r) => r.month === "2026-08")!.planIncome).toBeNull();
  });
});

describe("ringSlices", () => {
  it("keeps four colors at most, one per color, and folds the rest", () => {
    const slices = ringSlices(BREAKDOWN_GROUP.items, BREAKDOWN_GROUP.total_cents);
    expect(slices.map((s) => s.key)).toEqual(["housing", "lifestyle", "kids", "services", OTHER_KEY]);
    const colors = slices.filter((s) => s.color).map((s) => s.color);
    expect(new Set(colors).size).toBe(colors.length);
    const other = slices[slices.length - 1]!;
    expect(other.members).toEqual(["Debt payments"]);
    expect(other.cents).toBe(50_000);
    expect(other.share_pct).toBe("12.5");
    expect(other.color).toBeNull();
  });

  it("gives Transportation the green slot, shared with Services and fitness (Phase 7)", () => {
    const transport = { key: "transportation", label: "Transportation", emoji: null, group_key: "transportation", group_label: "Transportation", cents: 40_000, count: 6, payment_basis_cents: 0, share_pct: "10.0" };
    const slices = ringSlices([...BREAKDOWN_GROUP.items, transport], BREAKDOWN_GROUP.total_cents + 40_000);
    // Sorted by amount: Transportation outranks Services and fitness, so it takes the green.
    expect(slices.find((s) => s.key === "transportation")?.color).toBe("var(--chart-6)");
    expect(slices.find((s) => s.key === "services")).toBeUndefined();
    expect(slices[slices.length - 1]!.members).toContain("Services and fitness");
  });

  it("leaves out groups with nothing spent", () => {
    const slices = ringSlices([{ ...BREAKDOWN_GROUP.items[0]!, cents: -500 }], 0);
    expect(slices).toEqual([]);
  });
});

describe("CashflowPage overview", () => {
  it("shows the KPI tiles for the latest complete month, income in green", async () => {
    renderWithProviders(<CashflowPage />, { route: "/cash-flow" });
    const kpi = await screen.findByRole("heading", { level: 2, name: `${formatMonth("2026-08")} totals` });
    const row = kpi.closest("section")!;
    for (const [label, value] of [
      ["Income", "$10,000"],
      ["Spending", "$4,000"],
      ["Saved and invested", "$1,000"],
      ["Left over", "$5,000"],
      ["Savings rate", "60%"],
    ] as const) {
      const tile = within(row).getByRole("heading", { level: 3, name: label }).parentElement!;
      expect(within(tile).getByText(value)).toBeTruthy();
    }
    const income = within(within(row).getByRole("heading", { level: 3, name: "Income" }).parentElement!).getByText("$10,000");
    expect(income.className).toContain("text-good");
    expect(within(row).getByText(/Averages use the 2 complete months in range/)).toBeTruthy();
    // 12 months is the API default, so no range params go out.
    expect(urls()).toContain("/api/cashflow");
  });

  it("Show the numbers lists the same months as the chart", async () => {
    renderWithProviders(<CashflowPage />, { route: "/cash-flow" });
    const section = await card("Cash flow");
    const rows = await tableRows(section);
    const expected = toChartRows(CASHFLOW.months);
    expect(rows).toHaveLength(expected.length);
    rows.forEach((cells, i) => {
      const r = expected[i]!;
      expect(cells.slice(0, 5)).toEqual([
        formatMonth(r.month),
        formatMoney(r.income),
        formatMoney(r.spending),
        formatMoney(r.saved),
        formatMoney(r.leftOver),
      ]);
    });
    expect(rows[0]![5]).toBe("$9,500");
    expect(rows[0]![6]).toBe("$4,000");
    expect(rows[2]![7]).toMatch(/^Partial/);
    expect(rows[3]![7]).toBe("This month, plan");
  });

  it("asks for a 6 month range from the picker and keeps it in the URL", async () => {
    renderWithProviders(<CashflowPage />, { route: "/cash-flow" });
    await card("Cash flow");
    await userEvent.setup().click(screen.getByRole("radio", { name: "6 months" }));
    const now = thisMonth();
    await waitFor(() => expect(urls()).toContain(`/api/cashflow?from=${addMonths(now, -5)}&to=${now}`));
  });

  it("draws the ring by group with a legend and the same numbers in its table", async () => {
    renderWithProviders(<CashflowPage />, { route: "/cash-flow" });
    const section = await card("Spending by group");
    expect(within(section).getByText("Everything else")).toBeTruthy();
    expect(within(section).getByText(/Saved and invested, \$1,000, is not in the ring/)).toBeTruthy();
    const rows = await tableRows(section);
    const slices = ringSlices(BREAKDOWN_GROUP.items, BREAKDOWN_GROUP.total_cents);
    expect(rows.map((r) => r[1])).toEqual(slices.map((s) => formatMoney(s.cents)));
    expect(rows[rows.length - 1]![0]).toBe("Everything else (Debt payments)");
  });

  it("puts the ring's hover box above the Total in the hole", async () => {
    renderWithProviders(<CashflowPage />, { route: "/cash-flow" });
    const section = await card("Spending by group");
    const tip = section.querySelector<HTMLElement>(".recharts-tooltip-wrapper");
    expect(tip?.style.zIndex).toBe("10");
  });

  it("ranks categories, switches to merchants, and the table follows", async () => {
    renderWithProviders(<CashflowPage />, { route: "/cash-flow" });
    const section = await card("Where it went");
    await within(section).findByText("Mortgage");
    expect(within(section).getByText("payment basis")).toBeTruthy();
    const rows = await tableRows(section);
    expect(rows.map((r) => [r[0], r[2]])).toEqual(BREAKDOWN_CATEGORY.items.map((i) => [i.label, formatMoney(i.cents)]));

    await userEvent.setup().click(within(section).getByRole("radio", { name: "By merchant" }));
    // Once in the bars and once in the open table.
    expect(await within(section).findAllByText("Green Leaf Market")).toHaveLength(2);
    expect(urls().some((u) => u.includes("by=merchant"))).toBe(true);
    // Merchant rows are not buttons.
    expect(within(section).queryByRole("button", { name: /Green Leaf Market/ })).toBeNull();
  });

  it("uses the whole range for breakdowns when asked", async () => {
    renderWithProviders(<CashflowPage />, { route: "/cash-flow" });
    const section = await card("Spending by group");
    await userEvent.setup().click(within(section).getByRole("radio", { name: "Whole range" }));
    await waitFor(() => expect(urls()).toContain("/api/cashflow/breakdown?from=2026-07&to=2026-10&by=group"));
  });

  it("opens the category drawer with months, the budget line table and top merchants", async () => {
    renderWithProviders(<CashflowPage />, { route: "/cash-flow" });
    const section = await card("Where it went");
    await userEvent.setup().click(await within(section).findByRole("button", { name: /^Groceries/ }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("Green Leaf Market");
    expect(urls()).toContain("/api/cashflow/category/groceries");
    expect(within(dialog).getByText(/Average \$575 a month/)).toBeTruthy();
    const months = within(dialog).getByText("By month").closest("section")!;
    const rows = await tableRows(months);
    expect(rows.map((r) => r.slice(0, 3))).toEqual([
      ["Jul 2026", "$550", "$600"],
      ["Aug 2026", "$600", "$600"],
      ["Sep 2026", "$200", "None"],
    ]);
    expect(within(dialog).getByText(/The plan sets Lifestyle at \$2,000 for Jul 2026/)).toBeTruthy();
  });

  it("shows actual vs plan for the latest complete month with pills in words", async () => {
    renderWithProviders(<CashflowPage />, { route: "/cash-flow" });
    const section = await card("Actual vs plan");
    expect(within(section).getByRole("heading", { level: 2, name: "Jul 2026" })).toBeTruthy();
    expect(within(section).getAllByText("$300 over")[0]!.closest("span")!.className).toContain("text-bad");
    expect(within(section).getAllByText("$800 left").length).toBeGreaterThan(0);
    expect(within(section).getAllByText("No plan").length).toBeGreaterThan(0);
    expect(within(section).getAllByText("Not in the plan").length).toBe(1);

    expect(within(section).getByRole("region", { name: "Actual vs plan, Jul 2026" }).tabIndex).toBe(0);

    await userEvent.setup().click(within(section).getByRole("button", { name: "Later month" }));
    expect(within(section).getByRole("heading", { level: 2, name: "Oct 2026" })).toBeTruthy();
    expect(within(section).getByText("Statements not in yet. Actuals will grow.")).toBeTruthy();
  });

  it("shows the tracked cards line with the API label, per card and by month", async () => {
    renderWithProviders(<CashflowPage />, { route: "/cash-flow" });
    const section = await card("Sam's cards");
    expect(within(section).getByText("payment basis")).toBeTruthy();
    expect(within(section).getByText("Bramble store card")).toBeTruthy();
    const rows = await tableRows(section);
    expect(rows[1]).toEqual(["Aug 2026", "$77", "$0", "$77"]);
    expect(within(section).getByText("$153")).toBeTruthy();
    // Pale bars say what they are: a legend item and the word in the table.
    expect(within(section).getByText("Partial month")).toBeTruthy();
    expect(rows[2]![0]).toBe("Sep 2026 (partial)");
    // Averages over the complete months, and the newest payment to each card.
    expect(within(section).getByText("Avg $59 a month · Last paid $21 on Sep 24, 2026")).toBeTruthy();
    expect(within(section).getByText("Avg $5 a month · Last paid $5 on Sep 12, 2026")).toBeTruthy();
    expect(within(section).getByText("Avg $64 a month")).toBeTruthy();
    expect(within(section).getByText("Averages use the 2 complete months in range.")).toBeTruthy();
  });

  it("says when a tracked card was last paid when nothing came in the range", async () => {
    const body = structuredClone(CASHFLOW);
    const tracked = body.tracked_cards!;
    const lark = tracked.cards[1]!;
    lark.months = lark.months.map((m) => ({ ...m, cents: 0, payments_cents: 0 }));
    Object.assign(lark, { total_cents: 0, avg_cents: 0, last_payment_date: "2026-05-12", last_payment_cents: 1_200 });
    Object.assign(tracked.cards[0]!, { avg_cents: null, last_payment_date: null, last_payment_cents: null });
    Object.assign(tracked, { avg_cents: null, avg_months: 0 });
    cashflowBody = body;
    renderWithProviders(<CashflowPage />, { route: "/cash-flow" });
    const section = await card("Sam's cards");
    expect(within(section).getByText("Avg $0 a month · No payment since May 12, 2026")).toBeTruthy();
    expect(within(section).getByText("No payments yet")).toBeTruthy();
    expect(within(section).getByText("No complete month to average yet.")).toBeTruthy();
    expect(within(section).queryByText(/^Avg .* a month$/)).toBeNull();
  });

  it("lists charges waiting on reimbursement", async () => {
    renderWithProviders(<CashflowPage />, { route: "/cash-flow" });
    const section = await card("Waiting on reimbursement");
    expect(within(section).getByText("Pine Clinic")).toBeTruthy();
    expect(within(section).getByText(/\$120 left out of spending/)).toBeTruthy();
  });

  it("shows one banner when cash flow fails to load", async () => {
    cashflowStatus = 500;
    renderWithProviders(<CashflowPage />, { route: "/cash-flow" });
    expect(await screen.findByText("Could not load cash flow.")).toBeTruthy();
  });
});

describe("CashflowPage tabs", () => {
  it("Plan tab shows months, upcoming changes and the relief timeline", async () => {
    renderWithProviders(<CashflowPage />, { route: "/cash-flow?tab=plan" });
    expect(screen.getByRole("tab", { name: "Plan" }).getAttribute("aria-selected")).toBe("true");
    const changes = await card("Upcoming cash flow changes");
    expect(within(changes).getByText("Sofa plan ends")).toBeTruthy();
    expect(within(changes).getByText("Confirmed")).toBeTruthy();
    expect(within(changes).getByText("+$105").closest("span")!.className).toContain("text-good");
    expect(within(changes).getByText("Earlier")).toBeTruthy();
    const relief = await card("Surplus by phase");
    const rows = await tableRows(relief);
    expect(rows).toEqual([
      ["Base", "Oct 2026 to Dec 2026", "$400", "Plans still running"],
      ["Plans done", "From Jan 2027", "$580", ""],
    ]);
    const groups = await card("Plan groups");
    expect(within(groups).getByText("Compared with: Actual saved and invested")).toBeTruthy();
    // The wide plan table scrolls by keyboard too.
    const byMonth = await card("Plan by month");
    expect(within(byMonth).getByRole("region", { name: "Plan by month" }).tabIndex).toBe(0);
  });

  it("badges each change by its status, none when left out", async () => {
    const base = PLAN.events[0]!;
    const statuses = ["confirmed", "expected", "target", "suggested", null] as const;
    planBody = {
      ...PLAN,
      events: statuses.map((status, i) => ({ ...base, key: `change-${i}`, label: `Change ${i}`, upcoming: i !== 1, status })),
    };
    renderWithProviders(<CashflowPage />, { route: "/cash-flow?tab=plan" });
    const changes = await card("Upcoming cash flow changes");
    const words = ["Confirmed", "Expected", "Target", "Suggested"];
    const row = (label: string) => within(changes).getByText(label).closest("li")!;
    words.forEach((word, i) => expect(within(row(`Change ${i}`)).getByText(word).className).toContain("bg-subtle"));
    // Expected is an earlier change: still badged, in the muted list.
    expect(row("Change 1").className).toContain("opacity-80");
    const none = row("Change 4").textContent ?? "";
    expect(words.filter((w) => none.includes(w))).toEqual([]);
  });

  it("Plan groups list their bills, then everything else, outside the chart summary", async () => {
    renderWithProviders(<CashflowPage />, { route: "/cash-flow?tab=plan" });
    const groups = await card("Plan groups");
    const user = userEvent.setup();
    const show = within(groups).getByRole("button", { name: "Housing and utilities: 3 bills" });
    // Not inside the role="img" summary, which screen readers read as one image.
    expect(show.closest('[role="img"]')).toBeNull();
    expect(show.getAttribute("aria-expanded")).toBe("false");
    expect(within(groups).queryByText("Pinecrest Mortgage")).toBeNull();
    await user.click(show);
    expect(show.getAttribute("aria-expanded")).toBe("true");
    const lines = within(within(groups).getByRole("list", { name: "Bills in Housing and utilities" }))
      .getAllByRole("listitem")
      .map((li) => li.textContent);
    expect(lines).toEqual(["Pinecrest Mortgage$1,500.00", "City Power$150.00", "Lawn care Rate up from $80.00$85.00", "Everything else$1,865.00"]);
    expect(within(groups).getByText("A bill not charged every month shows its yearly cost spread over the months it runs.")).toBeTruthy();
    await user.click(show);
    expect(within(groups).queryByText("Pinecrest Mortgage")).toBeNull();
  });

  it("calls saved and invested lines transfers, and says when they are more than the plan", async () => {
    renderWithProviders(<CashflowPage />, { route: "/cash-flow?tab=plan" });
    const groups = await card("Plan groups");
    const show = within(groups).getByRole("button", { name: "Saved and invested: 2 transfers" });
    await userEvent.setup().click(show);
    const saved = show.closest("li")!;
    expect(within(saved).getByText("College 529")).toBeTruthy();
    expect(within(saved).getByText("Transfers are $100.00 more than this group's plan.")).toBeTruthy();
    expect(within(saved).queryByText("Everything else")).toBeNull();
  });

  it("Plan lines show the New and Rate up badges from Recurring", async () => {
    renderWithProviders(<CashflowPage />, { route: "/cash-flow?tab=plan" });
    const groups = await card("Plan groups");
    const user = userEvent.setup();
    await user.click(within(groups).getByRole("button", { name: "Housing and utilities: 3 bills" }));
    const housing = within(groups).getByRole("list", { name: "Bills in Housing and utilities" });
    const up = within(housing).getByText("Rate up from $80.00");
    expect(up.closest("[title]")?.getAttribute("title")).toBe("Was $80.00 a month");
    expect(up.closest("span[class*='text-warn']")).toBeTruthy();
    const [mortgage] = within(housing).getAllByRole("listitem");
    expect(within(mortgage!).queryByText(/^(New|Rate (up|down))/)).toBeNull();
    await user.click(within(groups).getByRole("button", { name: "Saved and invested: 2 transfers" }));
    const saved = within(groups).getByRole("list", { name: "Transfers in Saved and invested" });
    expect(within(saved).getByText("New").closest("[title]")?.getAttribute("title")).toBe("Not in the update before");
  });

  it("Plan group rows say what is in each group", async () => {
    expect(contentsText(null)).toBeNull();
    expect(contentsText({ kind: "bills", names: [], more: 0 })).toBeNull();
    expect(contentsText({ kind: "transfers", names: ["A", "B"], more: 0 })).toBe("Transfers: A, B");
    expect(contentsText({ kind: "bills", names: ["A", "B", "C"], more: 2 })).toBe("Bills: A, B, C, and 2 more");
    expect(contentsText({ kind: "categories", names: ["Life insurance", "Auto and home insurance"], more: 0 })).toBe(
      "Categories: Life insurance, Auto and home insurance",
    );
    renderWithProviders(<CashflowPage />, { route: "/cash-flow?tab=plan" });
    const groups = await card("Plan groups");
    expect(within(groups).getByText("Bills: Pinecrest Mortgage, City Power, Lawn care")).toBeTruthy();
    expect(within(groups).getByText("Transfers: College 529, Monthly savings")).toBeTruthy();
    const rows = await tableRows(groups);
    expect(rows.map((r) => r.at(-1))).toEqual(["Bills: Pinecrest Mortgage, City Power, Lawn care", "", "Transfers: College 529, Monthly savings"]);
  });

  it("gives a group with no bills no way to open it", async () => {
    renderWithProviders(<CashflowPage />, { route: "/cash-flow?tab=plan" });
    const groups = await card("Plan groups");
    expect(within(groups).queryByRole("button", { name: /^Plan It installments:/ })).toBeNull();
    expect(within(groups).getAllByRole("button", { name: /: \d+ (bills?|transfers?)$/ })).toHaveLength(2);
  });

  it("Income tab shows streams, deposits by month and paychecks", async () => {
    renderWithProviders(<CashflowPage />, { route: "/cash-flow?tab=income" });
    const streams = await card("Expected pay");
    expect(within(streams).getByText("Alex pay")).toBeTruthy();
    expect(within(streams).getByText("$2,900")).toBeTruthy();
    const deposits = await card("Income by month");
    const rows = await tableRows(deposits);
    expect(rows).toEqual([
      ["Aug 2026", "$5,800", "$4,400", "$1", "$10,201", "Complete"],
      ["Sep 2026", "$2,900", "$0", "$0", "$2,900", "Partial, statements not all in"],
    ]);
    const pay = await card("Paychecks by month");
    expect(within(pay).getAllByRole("row")).toHaveLength(3);
    expect(within(pay).getByRole("region", { name: "Paychecks by month" }).tabIndex).toBe(0);
    await userEvent.setup().click(within(pay).getByRole("button", { name: "Show each paycheck (2)" }));
    expect(within(pay).getByText("Aug 14, 2026")).toBeTruthy();
    expect(within(pay).getByRole("region", { name: "Each paycheck" }).tabIndex).toBe(0);
  });

  it("moves between tabs with the arrow keys", async () => {
    renderWithProviders(<CashflowPage />, { route: "/cash-flow" });
    const overview = screen.getByRole("tab", { name: "Overview" });
    overview.focus();
    await userEvent.setup().keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Plan" }).getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Plan" }));
  });
});
