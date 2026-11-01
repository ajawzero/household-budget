import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../../routes/AppRoutes";
import { renderWithProviders } from "../../test/render";
import { mixRows } from "./AllocationTab";
import { type AllocationOut, classLabel, formatGap, formatPrice, formatQuantity } from "./api";
import { classColorMap, OTHER_CLASSES, OTHER_FILL } from "./colors";
import { ALLOCATION, BOND, CASH, FLOWS, HISTORY, HOLDINGS, HOLDINGS_BY_CLASS, STATUS, STOCK, TRUST } from "./fixtures";
import { currentEstimate, HistoryChart, historyRows } from "./HistoryCard";
import { InvestmentsPage } from "./InvestmentsPage";
import { priceLine } from "./PriceStatus";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

let status = STATUS;
let holdings = HOLDINGS;
let flows: unknown = FLOWS;
let allocation: AllocationOut = ALLOCATION;
const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input);
  if (url === "/api/auth/me") return json(200, { username: "sam", display_name: "Sam", role: "member" });
  if (url === "/api/meta") return json(404, {});
  if (url === "/api/investments/holdings?group_by=account") return json(200, holdings);
  if (url === "/api/investments/holdings?group_by=class") return json(200, HOLDINGS_BY_CLASS);
  if (url === "/api/investments/allocation") return json(200, allocation);
  if (url.startsWith("/api/investments/history")) return json(200, HISTORY);
  if (url === "/api/investments/flows") return flows === null ? json(500, { detail: "Server error." }) : json(200, flows);
  if (url === "/api/prices/status") return json(200, status);
  return json(404, { detail: "Not found." });
});

beforeEach(() => {
  status = STATUS;
  holdings = HOLDINGS;
  flows = FLOWS;
  allocation = ALLOCATION;
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function groupCard(title: string): HTMLElement {
  const heading = screen.getByRole("heading", { level: 2, name: title });
  return heading.closest("section")!;
}

describe("number helpers", () => {
  it("formats shares, prices and gaps", () => {
    expect(formatQuantity(138_288_048)).toBe("138.288");
    expect(formatQuantity(5_000_000)).toBe("5");
    expect(formatQuantity(null)).toBe("");
    expect(formatPrice(262_645_111)).toBe("$262.65");
    expect(formatPrice(1_000_000)).toBe("$1.00");
    expect(formatPrice(4_999)).toBe("$0.00");
    expect(formatPrice(5_000)).toBe("$0.01");
    expect(formatGap("1.50")).toBe("+1.50%");
    expect(formatGap("-1.25")).toBe("−1.25%");
  });

  it("names asset classes", () => {
    expect(classLabel("us-stock", "Us stock")).toBe("US stock");
    expect(classLabel("unclassified")).toBe("No class");
    expect(classLabel(null)).toBe("No class");
    expect(classLabel("frontier-debt", "Frontier debt")).toBe("Frontier debt");
    expect(classLabel("frontier-debt")).toBe("Frontier debt");
  });
});

describe("class colors", () => {
  it("follow the class, and fold a class into Other when a bigger one has its color", () => {
    const map = classColorMap(ALLOCATION.classes);
    expect(map.get("target-date")?.fill).toBe("var(--chart-5)");
    expect(map.get("us-stock")?.fill).toBe("var(--chart-7)");
    expect(map.get("cash")?.fill).toBe("var(--chart-6)");
    expect(map.get("bond")?.fill).toBe("var(--chart-4)");
    // Money market shares green with cash, which is bigger.
    expect(map.get("money-market")).toEqual({ fill: OTHER_FILL, legendKey: OTHER_CLASSES });
    // Unknown classes never get a generated color.
    expect(classColorMap([{ key: "odd", label: "Odd", value_cents: 1, pct: "100.0" }]).get("odd")?.fill).toBe(OTHER_FILL);
  });

  it("keep the same color when the order changes", () => {
    const reversed = classColorMap([...ALLOCATION.classes].reverse());
    expect(reversed.get("us-stock")?.fill).toBe("var(--chart-7)");
  });
});

describe("price status line", () => {
  it("gives the newest close and lists stale ones", () => {
    expect(priceLine([STOCK, BOND, CASH, TRUST])).toEqual({
      asOf: "2026-10-20",
      stale: ["BNDFND price as of Jun 12"],
      allStale: false,
      none: false,
    });
  });

  it("says when nothing is priced daily, and when every price is old", () => {
    expect(priceLine([CASH, TRUST]).none).toBe(true);
    expect(priceLine([BOND]).allStale).toBe(true);
    expect(priceLine([{ ...STOCK, price_missing: true }]).stale).toEqual(["STKIDX has no price yet"]);
  });
});

describe("history", () => {
  it("joins statement values onto their day", () => {
    const rows = historyRows(HISTORY);
    expect(rows).toHaveLength(5);
    expect(rows[2]).toEqual({
      date: "2026-09-30",
      value: 13_100_000,
      estimated: false,
      partial: false,
      statements: ["Sample brokerage $41,000", "Sample 401k $90,000"],
    });
    expect(rows[3]?.statements).toEqual([]);
  });

  it("finds the estimate still running today", () => {
    expect(currentEstimate(HISTORY)).toEqual({ start: "2026-10-01", end: "2026-10-02", basedOn: "2026-09-30" });
    expect(currentEstimate({ ...HISTORY, estimated_segments: [] })).toBeNull();
  });

  it("draws a dot only on statement days", () => {
    const { container } = renderWithProviders(
      <HistoryChart rows={historyRows(HISTORY)} estimate={currentEstimate(HISTORY)} height={200} yAxisWidth={56} monthTicks={false} maxTicks={8} width={600} />,
    );
    const dots = container.querySelectorAll("circle[r='4']");
    expect(dots).toHaveLength(1);
  });
});

describe("InvestmentsPage", () => {
  it("shows holdings by account with modes, flags, estimates and the price status", async () => {
    renderWithProviders(<InvestmentsPage />, { route: "/investments" });
    await screen.findByText("Stock Index Fund");
    const brokerage = groupCard("Sample brokerage");
    const rows = within(brokerage).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getByRole("rowheader").querySelector("span")?.textContent)).toEqual([
      "Stock Index Fund",
      "Cash",
      "Bond Fund",
    ]);
    const stock = rows[0]!;
    expect(stock.textContent).toContain("STKIDX");
    expect(stock.textContent).toContain("Daily price");
    expect(stock.textContent).toContain("Over 20%");
    expect(stock.textContent).toContain("100.5");
    expect(stock.textContent).toContain("$262.65");
    expect(stock.textContent).toContain("$26,396");
    expect(stock.textContent).toContain("72.5% of account");
    expect(stock.textContent).toContain("est.");
    // Cash is fixed at $1.00. The bond's override shows, and its old price is in the warning color.
    expect(rows[1]!.textContent).toContain("Fixed $1");
    expect(rows[1]!.textContent).toContain("$1.00");
    expect(within(rows[2]!).getByText("set by hand").className).not.toContain("sr-only");
    const stale = within(rows[2]!).getAllByText(/price as of Jun 12/);
    expect(stale[0]!.className).toContain("text-warn");
    // Not by color alone: a clock icon, and "stale" in the name.
    expect(stale[0]!.querySelector("svg")).toBeTruthy();
    expect(stale[0]!.textContent).toContain("stale");
    // The 20% flag says what it means in words, once per card, and the table scrolls by keyboard.
    expect(within(brokerage).getByText(/Over 20% marks a holding worth more than 20% of its account/)).toBeTruthy();
    expect(within(brokerage).getByRole("region", { name: "Holdings in Sample brokerage" }).getAttribute("tabindex")).toBe("0");
    // The statement-mode trust: no market price, its statement date instead.
    const trust = groupCard("Sample 401k");
    expect(trust.textContent).toContain("Statement");
    // Dates use no-break spaces so they never split on a phone.
    expect(trust.textContent).toContain("Sep\u00a030 statement");
    expect(trust.textContent).not.toContain("est.");

    const line = await screen.findByTestId("price-status");
    await waitFor(() => expect(line.textContent).toContain("Prices are not updating: the Tiingo key is not set."));
    expect(line.textContent).toContain("Prices as of Oct 20");
    expect(within(line).getByText("BNDFND price as of Jun 12.").className).toContain("text-warn");
    expect(within(line).getByRole("link", { name: "Price settings" }).getAttribute("href")).toBe("/settings/prices");
    // What each mode means, in words, not only in a tooltip.
    const key = screen.getByTestId("mode-key");
    expect(key.textContent).toContain("Daily price: Priced each weekday from market closes.");
    expect(key.textContent).toContain("Fixed $1: Cash or a money market fund.");
    expect(key.textContent).toContain("Statement: No market price.");
    expect(key.textContent).toContain("Set by hand: changed on Settings, Prices.");
  });

  it("shows gain on cost per holding and per account, with what it leaves out", async () => {
    renderWithProviders(<InvestmentsPage />, { route: "/investments" });
    await screen.findByText("Stock Index Fund");
    const brokerage = groupCard("Sample brokerage");
    const rows = within(brokerage).getAllByRole("row").slice(1);
    const stock = rows[0]!;
    expect(stock.textContent).toContain("+$2,396");
    expect(stock.textContent).toContain("on cost");
    // Phones drop the percent in brackets.
    const pct = within(stock).getByText("(+10.0%)", { exact: false });
    expect(pct.className).toContain("hidden md:inline");
    expect(pct.parentElement?.className).toContain("text-good");
    // Cash and the bond (no basis) have no gain line.
    expect(rows[1]!.textContent).not.toContain("on cost");
    expect(rows[2]!.textContent).not.toContain("on cost");
    const line = within(brokerage).getByText(/^Gain on cost:/);
    expect(line.textContent).toBe("Gain on cost: +$2,396 (+10.0%). For 1 of 2 holdings. The others have no cost basis.");
    // The account's note, as the chat wrote it.
    expect(within(brokerage).getByText("Diversification plan needed.").className).toContain("break-words");
    // The 401k trust has no basis: no line. It is dormant, said in words.
    const trust = groupCard("Sample 401k");
    expect(trust.textContent).not.toContain("Gain on cost");
    expect(within(trust).getByText("· Dormant").className).toContain("text-fg-secondary");
    expect(brokerage.textContent).not.toContain("Dormant");
  });

  it("says what share of a 529 is gains", async () => {
    const plan = {
      ...HOLDINGS.groups[1]!,
      key: "college",
      label: "Sample 529",
      account_type: "529",
      notes: null,
      holdings: [{ ...STOCK, account_key: "college", account_name: "Sample 529" }],
      gain_share_pct: "19.8",
      no_basis_holdings: 0,
    };
    holdings = { ...HOLDINGS, groups: [plan] };
    renderWithProviders(<InvestmentsPage />, { route: "/investments" });
    await screen.findByText("Stock Index Fund");
    expect(within(groupCard("Sample 529")).getByText(/^Gain on cost:/).textContent).toBe(
      "Gain on cost: +$2,396 (+10.0%). Gains are 19.8% of the account.",
    );
  });

  it("shows this year by account from statements", async () => {
    renderWithProviders(<InvestmentsPage />, { route: "/investments" });
    const heading = await screen.findByRole("heading", { level: 2, name: "This year by account" });
    const card = heading.closest("section")!;
    expect(card.textContent).toContain("From statements. Return is the market change only, not money put in.");
    const table = within(card).getByRole("table", { name: "This year by account" });
    const heads = within(table).getAllByRole("columnheader").map((h) => h.textContent);
    expect(heads).toEqual(["Account", "Money in", "Market", "Return", "Fees", "Dividends"]);
    const broker = within(table).getByRole("row", { name: /Sample brokerage/ });
    const cells = within(broker).getAllByRole("cell");
    expect(cells.map((c) => c.textContent)).toEqual(["+$4,500", "+$1,091", "+2.1%", "None", "$174.34"]);
    expect(cells[1]!.querySelector(".text-good")).toBeTruthy();
    // Fees and Dividends show from md up.
    expect(cells[3]!.className).toContain("hidden md:table-cell");
    expect(cells[4]!.className).toContain("hidden md:table-cell");
    expect(broker.textContent).toContain("Jan 1 to Sep 30");
    const k401 = within(table).getByRole("row", { name: /Sample 401k/ });
    expect(k401.textContent).toContain("Closed");
    expect(k401.textContent).toContain("From 2 of 3 statements");
    const k = within(k401).getAllByRole("cell");
    expect(k[1]!.querySelector(".text-bad")?.textContent).toBe("−$798");
    expect(k[2]!.textContent).toBe("None");
    expect(k[3]!.textContent).toBe("$66.01$7.33 a month");
    // No totals row: the accounts cover different periods.
    expect(within(table).getAllByRole("row")).toHaveLength(3);
  });

  it("says when fees or statements are missing from this year", async () => {
    const k401 = FLOWS.accounts[1]!;
    flows = { ...FLOWS, accounts: [{ ...k401, ytd: { ...k401.ytd!, fee_statements: 1, fee_months: 3, fees_per_month_cents: 2_200, gaps: 1 } }] };
    renderWithProviders(<InvestmentsPage />, { route: "/investments" });
    const table = await screen.findByRole("table", { name: "This year by account" });
    const row = within(table).getByRole("row", { name: /Sample 401k/ });
    expect(row.textContent).toContain("A statement is missing from this year");
    expect(within(row).getAllByRole("cell")[3]!.textContent).toBe("$66.01$22.00 a monthFees from 1 of 3 statements");
  });

  it("hides this year by account when no account has statements this year", async () => {
    flows = { ...FLOWS, accounts: [] };
    renderWithProviders(<InvestmentsPage />, { route: "/investments" });
    await screen.findByText("Stock Index Fund");
    expect(screen.queryByRole("heading", { name: "This year by account" })).toBeNull();
  });

  it("says when this year by account cannot load", async () => {
    flows = null;
    renderWithProviders(<InvestmentsPage />, { route: "/investments" });
    expect(await screen.findByText("Could not load this year by account.")).toBeTruthy();
    expect(screen.getByRole("button", { name: /Retry/ })).toBeTruthy();
  });

  it("says when a daily-priced holding has no share count", async () => {
    const noShares = { ...STOCK, quantity_micro: null, price_micro: null, estimated: false, price_date: "2026-09-30" };
    holdings = { ...HOLDINGS, groups: [{ ...HOLDINGS.groups[1]!, holdings: [noShares] }] };
    renderWithProviders(<InvestmentsPage />, { route: "/investments" });
    expect((await screen.findAllByText("No share count, so it keeps the statement value"))[0]!.className).toContain("text-warn");
  });

  it("groups by class", async () => {
    renderWithProviders(<InvestmentsPage />, { route: "/investments?group=class" });
    await screen.findByRole("heading", { level: 2, name: "US stock" });
    expect(fetchMock).toHaveBeenCalledWith("/api/investments/holdings?group_by=class", expect.anything());
    const stock = groupCard("US stock");
    // Class groups name the account on each holding.
    expect(stock.textContent).toContain("Sample brokerage");
    expect(stock.textContent).toContain("1 holding");
    // No subtitle date by class, so an estimated row says where it starts.
    expect(stock.textContent).toContain("estimated since the Sep\u00a030 statement");
    expect(groupCard("Bonds").textContent).not.toContain("estimated since");
    // The 20% note shows only on a card with a flagged holding.
    expect(groupCard("Cash").textContent).not.toContain("Over 20% marks");
    // By class: per-holding gain lines only, no group line, note or dormant mark.
    expect(stock.textContent).toContain("on cost");
    expect(stock.textContent).not.toContain("Gain on cost");
    expect(screen.queryByText("Diversification plan needed.")).toBeNull();
    expect(screen.queryByText(/Dormant/)).toBeNull();
  });

  it("switches grouping with the toggle", async () => {
    renderWithProviders(<InvestmentsPage />, { route: "/investments" });
    await screen.findByText("Stock Index Fund");
    await userEvent.setup().click(screen.getByRole("radio", { name: "By class" }));
    await screen.findByRole("heading", { level: 2, name: "Bonds" });
  });

  it("shows the value history with the estimate legend", async () => {
    renderWithProviders(<InvestmentsPage />, { route: "/investments" });
    await screen.findByText("Investments and retirement");
    expect(await screen.findByText("Estimated since the Sep 30 statement")).toBeTruthy();
    const img = screen.getByRole("img", { name: /Investments and retirement value/ });
    expect(img.getAttribute("aria-label")).toContain("2 statement values");
    await userEvent.setup().click(screen.getByRole("button", { name: "Show the numbers" }));
    const table = screen.getByRole("table", { name: /Value of investment and retirement accounts/ });
    const sep30 = within(table).getByRole("row", { name: /Sep 30, 2026/ });
    expect(sep30.textContent).toContain("Statement");
    expect(sep30.textContent).toContain("Sample 401k $90,000");
    expect(within(table).getByRole("row", { name: /Oct 1, 2026/ }).textContent).toContain("Estimated from prices");
  });

  it("offers 1D and 7D on the value chart and asks for the range picked", async () => {
    renderWithProviders(<InvestmentsPage />, { route: "/investments?range=7D" });
    await screen.findByText("Investments and retirement");
    expect(screen.getByRole("radio", { name: "7 days" }).getAttribute("aria-checked")).toBe("true");
    expect(fetchMock).toHaveBeenCalledWith("/api/investments/history?range=7D", expect.anything());
    await userEvent.setup().click(screen.getByRole("radio", { name: "1 day" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/investments/history?range=1D", expect.anything()));
    expect(screen.getByRole("radio", { name: "3 years" })).toBeTruthy();
  });

  it("shows the allocation by class and by account", async () => {
    renderWithProviders(<InvestmentsPage />, { route: "/investments?tab=allocation" });
    await screen.findByText("31.5% of total");
    expect(screen.getAllByText("US stock").length).toBeGreaterThan(0);
    expect(screen.getByText("68.5%")).toBeTruthy();
    expect(screen.getByText("31.5% of total")).toBeTruthy();
    expect(screen.getByText("US stock 63.8% · Cash 22.9% · Money market 12.1% · Bonds 1.2%")).toBeTruthy();
    expect(screen.getByRole("img", { name: /Asset classes: Target date 68.5%, US stock 20.1%/ })).toBeTruthy();
    // The est. pill's note is in words too.
    expect(screen.getByText("Includes values estimated from market prices.")).toBeTruthy();
    expect(screen.queryByText(/No class: the statement gave/)).toBeNull();
  });

  it("says what No class means and where to pick a class", async () => {
    allocation = {
      ...ALLOCATION,
      classes: [...ALLOCATION.classes, { key: "unclassified", label: "Unclassified", value_cents: 10_000, pct: "0.1" }],
    };
    renderWithProviders(<InvestmentsPage />, { route: "/investments?tab=allocation" });
    const note = await screen.findByText(/No class: the statement gave these holdings no asset class/);
    expect(note.textContent).toBe("No class: the statement gave these holdings no asset class. An admin can pick one in Settings, Prices.");
    expect(within(note).getByRole("link", { name: "Settings, Prices" }).getAttribute("href")).toBe("/settings/prices");
    expect(screen.getAllByText("No class").length).toBeGreaterThan(0);
  });

  it("builds the per account table rows", () => {
    const rows = mixRows(ALLOCATION);
    expect(rows).toHaveLength(5);
    expect(rows[1]).toEqual({ key: "brokerage:us-stock", account: "Sample brokerage", cls: "US stock", value_cents: 2_639_584, pct: "63.8" });
  });

  it("is a real page in the app, not Coming soon", async () => {
    renderWithProviders(<AppRoutes />, { route: "/investments" });
    expect(await screen.findByRole("heading", { level: 1, name: "Investments" })).toBeTruthy();
    expect(await screen.findByText("Stock Index Fund")).toBeTruthy();
    expect(screen.queryByText("Coming in Phase 5")).toBeNull();
  });
});
