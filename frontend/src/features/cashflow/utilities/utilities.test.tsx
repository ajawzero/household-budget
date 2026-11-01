import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { setPinnedToday } from "../../../lib/format";
import { renderWithProviders } from "../../../test/render";
import { CashflowPage } from "../CashflowPage";
import { POWER_DETAIL, UTILITIES, WATER_DETAIL } from "./fixtures";
import {
  billStatus,
  chargesSummary,
  periodText,
  costRows,
  creditRows,
  formatCost,
  formatUsage,
  formatPerDegreeDay,
  formatUsageTick,
  moneyTickFormatter,
  servicesInRange,
  showCharges,
  signedPct,
  spanFrom,
  tickDecimals,
  usageRows,
  usageTickFormatter,
  yoyTone,
} from "./model";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

let list: unknown = UTILITIES;
async function answer(input: RequestInfo | URL): Promise<Response> {
  const url = String(input);
  if (url.startsWith("/api/utilities/example-power")) return json(200, POWER_DETAIL);
  if (url.startsWith("/api/utilities/example-water")) return json(200, WATER_DETAIL);
  if (url === "/api/utilities") return json(200, list);
  return json(404, { detail: "Not found." });
}
const fetchMock = vi.fn(answer);

beforeEach(() => {
  list = UTILITIES;
  fetchMock.mockClear();
  // A test may swap the answers. Every test starts from the default ones.
  fetchMock.mockImplementation(answer);
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function urls(): string[] {
  return fetchMock.mock.calls.map((c) => String(c[0]));
}

/** The card (a <section>) holding this eyebrow or title text, once it has loaded. */
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
  const button = within(section).queryByRole("button", { name: "Show the numbers" });
  if (button) await userEvent.setup().click(button);
  const table = within(section).getByRole("table");
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((tr) => Array.from(tr.children).map((c) => c.textContent ?? ""));
}

function renderTab(query = "") {
  return renderWithProviders(<CashflowPage />, { route: `/cash-flow?tab=utilities${query}` });
}

describe("utility helpers", () => {
  it("formats usage, cost and change", () => {
    expect(formatUsage(719_000_000)).toBe("719");
    expect(formatUsage(23_966_667)).toBe("24.0");
    expect(formatUsage(633_333)).toBe("0.63");
    expect(formatUsage(5_642_000_000)).toBe("5,642");
    expect(formatUsage(19_000_000)).toBe("19");
    expect(formatUsage(null)).toBe("None");
    expect(formatUsageTick(0)).toBe("0");
    expect(formatUsageTick(2.5)).toBe("2.5");
    expect(formatCost(20.694, "kwh")).toBe("20.7¢");
    expect(formatCost(1.7184, "therms")).toBe("$1.72");
    expect(signedPct("24.0")).toBe("+24.0%");
    expect(signedPct("-1.8")).toBe("−1.8%");
    expect(signedPct(null)).toBe("None");
  });

  it("turns cost per unit into cents per kWh, dollars per therm and per 1,000 gallons", () => {
    const kwh = costRows(POWER_DETAIL.cost, "electric", "kwh");
    // 14,425 cents over 719 kWh.
    expect(formatCost(kwh[2]!.allIn, "kwh")).toBe("20.1¢");
    const gal = costRows(WATER_DETAIL.cost, "water", "gallons");
    // 3,371 cents over 5,642 gallons is $5.97 per 1,000 gallons.
    expect(formatCost(gal[1]!.allIn, "gallons")).toBe("$5.97");
  });

  it("counts the span back from the newest period end", () => {
    expect(spanFrom("1y", "2026-09-03")).toBe("2025-10");
    expect(spanFrom("2y", "2026-09-03")).toBe("2024-10");
    expect(spanFrom("all", "2026-09-03")).toBeUndefined();
  });

  it("gives lower use the good tone and leaves days and degree days neutral", () => {
    expect(yoyTone("24.0", "lower_is_better")).toBe("bad");
    expect(yoyTone("-13.8", "lower_is_better")).toBe("good");
    expect(yoyTone("0.0", "lower_is_better")).toBe("neutral");
    expect(yoyTone("3.3", "none")).toBe("neutral");
    expect(yoyTone(null, "lower_is_better")).toBe("neutral");
  });

  it("adds up credits per bill, keeping the server's running total", () => {
    expect(creditRows(POWER_DETAIL.credits).map((r) => [r.billDate, r.label, r.amountCents, r.soFarCents])).toEqual([
      ["2026-07-08", "Example credit", -219, 219],
      ["2026-08-07", "Example credit, Savings credit", -719, 938],
    ]);
  });

  it("formats ticks with the decimals their step needs", () => {
    expect(tickDecimals([0, 250, 500])).toBe(0);
    expect(tickDecimals([0, 0.25, 0.5, 0.75])).toBe(2);
    expect([0, 0.25, 0.5].map(usageTickFormatter([0, 0.25, 0.5]))).toEqual(["0.00", "0.25", "0.50"]);
    expect([0, 500, 1000].map(usageTickFormatter([0, 500, 1000]))).toEqual(["0", "500", "1,000"]);
    // Credits: $2.50 steps keep their cents, whole-dollar steps do not.
    expect([0, 250, 500].map(moneyTickFormatter([0, 250, 500]))).toEqual(["$0.00", "$2.50", "$5.00"]);
    expect([0, 500, 1000].map(moneyTickFormatter([0, 500, 1000]))).toEqual(["$0", "$5", "$10"]);
  });

  it("shows use per degree day to three significant digits", () => {
    expect(formatPerDegreeDay(41_415)).toBe("0.0414");
    expect(formatPerDegreeDay(66_000)).toBe("0.066");
    expect(formatPerDegreeDay(null)).toBe("None");
  });

  it("charts each service in its newest unit and drops lines in another unit", () => {
    const [jul, aug, sep] = POWER_DETAIL.bills;
    const oldUnit = { ...jul!, lines: jul!.lines.map((l) => (l.service === "gas" ? { ...l, unit: "ccf" as const } : l)) };
    expect(servicesInRange([oldUnit, aug!, sep!])).toEqual([
      { service: "electric", unit: "kwh" },
      { service: "gas", unit: "therms" },
    ]);
    const yoy = POWER_DETAIL.yoy.map((r, i) => (i === 0 ? { ...r, services: r.services.map((x) => (x.service === "gas" ? { ...x, unit: "ccf" as const } : x)) } : r));
    const rows = usageRows(yoy, "gas", "total", "therms");
    expect(rows[0]!.value).toBeNull();
    expect(rows[1]!.value).not.toBeNull();
    // The line in another unit has no bill before to compare with either.
    expect(rows[0]!.prevMicro).toBeNull();
    expect(rows[0]!.prevChangePct).toBeNull();
    expect(rows[2]!.prevMicro).toBe(10_000_000);
    expect(rows[2]!.prevEnd).toBe("2026-08-04");
  });

  it("lists dollars per service on a bill in the fixed service order", () => {
    expect(chargesSummary(POWER_DETAIL.bills[0]!)).toBe("Electric\u00a0$83.90 · Gas\u00a0$30.00");
    // The API sorts by service name (sewer first). The line keeps water first.
    expect(chargesSummary(WATER_DETAIL.bills[0]!)).toBe("Water\u00a0$29.81 · Sewer\u00a0$27.00 · Stormwater\u00a0$6.00");
    expect(showCharges(POWER_DETAIL.bills[2]!)).toBe(true);
    // One line and no credits: the line would repeat the total.
    const one = { ...POWER_DETAIL.bills[2]!, lines: [POWER_DETAIL.bills[2]!.lines[0]!] };
    expect(showCharges(one)).toBe(false);
    expect(showCharges({ ...one, credits: [{ label: "Example credit", amount_cents: -219 }] })).toBe(true);
  });

  it("says each paid status in words", () => {
    const [jul, aug, sep] = POWER_DETAIL.bills;
    expect(billStatus(jul!)).toEqual({ tone: "good", text: "Paid Jul 18 from Example checking" });
    expect(billStatus(aug!)).toEqual({ tone: "warn", text: "Paid $120.00 on Aug 18 from Example checking. Not the bill total." });
    expect(billStatus(sep!)).toEqual({ tone: "neutral", text: "Not paid yet. Due Sep 26" });
    expect(billStatus(sep!, "2026-09-26")).toEqual({ tone: "neutral", text: "Not paid yet. Due Sep 26" });
    // Due date passed, but the paying account's statements do not reach it yet.
    expect(billStatus(sep!, "2026-10-20")).toEqual({ tone: "neutral", text: "No payment in yet. Was due Sep 26" });
    // Past due with no due date: counted from the bill date + 40 days.
    expect(billStatus(WATER_DETAIL.bills[0]!)).toEqual({ tone: "bad", text: "No payment by Jul 11, 40 days after the bill" });
    expect(billStatus(WATER_DETAIL.bills[1]!).text).toBe("Before our payment history");
  });
});

describe("Utilities tab", () => {
  it("loads the first utility for the last year of bills", async () => {
    renderTab();
    expect(await screen.findByRole("tab", { name: "Utilities", selected: true })).toBeTruthy();
    await card("Electric use");
    expect(urls()).toContain("/api/utilities");
    expect(urls()).toContain("/api/utilities/example-power?from=2025-10");
    // The page range picker belongs to the other tabs.
    expect(screen.queryByRole("group", { name: "Date range" })).toBeNull();
    const summary = screen.getByRole("region", { name: "Summary" });
    expect(summary.textContent).toContain("$174.25");
    // The 12-month average is a summary: whole dollars.
    expect(summary.textContent).toContain("12-month average$137Over 12 bills");
    expect(summary.textContent).toContain("$9.38");
  });

  it("waits for the next import once the next bill's day has passed", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-20T17:00:00Z"));
    try {
      renderTab();
      await card("Electric use");
      expect(screen.getByText("Not in yet. It comes with the next import.")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("marks the next bill late when an import after its day did not have it", async () => {
    list = { utilities: [{ ...UTILITIES.utilities[0]!, next_bill_late: true, last_import_date: "2026-10-14" }, UTILITIES.utilities[1]!] };
    const late = { ...POWER_DETAIL, summary: { ...POWER_DETAIL.summary, next_bill_late: true, last_import_date: "2026-10-14" } };
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/utilities/example-power")) return json(200, late);
      if (url === "/api/utilities") return json(200, list);
      return json(404, { detail: "Not found." });
    });
    renderTab();
    await card("Electric use");
    const note = screen.getByText("Late. Not in the Oct 14 import.");
    expect(note.className).toContain("text-warn");
  });

  it("shows only the error when the bills fail to load", async () => {
    fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/utilities") return json(200, UTILITIES);
      return json(500, { detail: "Server error." });
    });
    renderTab();
    expect(await screen.findByText("Could not load these bills.", {}, { timeout: 5000 })).toBeTruthy();
    expect(screen.queryByText("No bills in this range.")).toBeNull();
    expect(screen.queryByRole("region", { name: "Summary" })).toBeNull();
  });

  it("does not mark the next bill late before its day", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-01T17:00:00Z"));
    try {
      renderTab();
      await card("Electric use");
      expect(screen.getByText("Expected, from the usual gap")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("follows the demo date, not the device clock", async () => {
    // Oct 1 on the device would say "Expected"; demo pins Oct 20, when the bill is late.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-01T17:00:00Z"));
    setPinnedToday("2026-10-20");
    try {
      renderTab();
      await card("Electric use");
      expect(screen.getByText("Not in yet. It comes with the next import.")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows the same numbers in the usage table as the chart rows, in total and per day", async () => {
    renderTab();
    const section = await card("Electric use");
    const totals = usageRows(POWER_DETAIL.yoy, "electric", "total");
    const rows = await tableRows(section);
    expect(rows).toEqual(
      totals.map((r) => [
        periodText(r.periodStart, r.periodEnd),
        String(r.days),
        formatUsage(r.valueMicro),
        r.lastYearStart && r.lastYearEnd ? periodText(r.lastYearStart, r.lastYearEnd) : "No bill",
        formatUsage(r.lastYearMicro),
        signedPct(r.changePct),
        signedPct(r.prevChangePct),
      ]),
    );
    expect(rows[2]).toEqual(["Aug 4 to Sep 3, 2026", "30", "719", "Aug 4 to Sep 3, 2025", "580", "+24.0%", "+51.4%"]);
    // Jul against the Jun bill, which is outside the range.
    expect(rows[0]![6]).toBe("+10.5%");
    expect(within(section).getByRole("columnheader", { name: "Change from last year" })).toBeTruthy();
    expect(within(section).getByRole("columnheader", { name: "Change from bill before" })).toBeTruthy();
    expect(rows[0]![2]).toBe("420");

    const user = userEvent.setup();
    await user.click(screen.getByRole("radio", { name: "Per day" }));
    const perDay = usageRows(POWER_DETAIL.yoy, "electric", "day");
    await waitFor(() => expect(within(section).getByRole("heading", { name: "kWh per day, by bill" })).toBeTruthy());
    const dayRows = await tableRows(section);
    expect(dayRows.map((r) => [r[2], r[4], r[5]])).toEqual(perDay.map((r) => [formatUsage(r.valueMicro), formatUsage(r.lastYearMicro), signedPct(r.changePct)]));
    // Jul had 31 days against 30: per day falls more than the total does.
    expect(dayRows[0]!.slice(2)).toEqual(["13.5", "Jun 5 to Jul 5, 2025", "16.2", "−16.5%", "+7.0%"]);
    expect(within(section).getByRole("columnheader", { name: "This bill (kWh per day)" })).toBeTruthy();
    // Per day is worked out from the same response: no new request.
    expect(urls().filter((u) => u.startsWith("/api/utilities/example-power"))).toHaveLength(1);
  });

  it("says the newest bill against the bill before, with no tone", async () => {
    renderTab();
    const section = await card("Electric use");
    const sentence = within(section).getByText("Newest bill 719 kWh, up 51.4% from the bill before.");
    expect(sentence.closest("[data-tone]")).toBeNull();
    expect(sentence.querySelector("[data-tone]")).toBeNull();
    // The chart's spoken summary says it too.
    expect(within(section).getByRole("img").getAttribute("aria-label")).toContain("Up 51.4% from the bill before.");
    await userEvent.setup().click(screen.getByRole("radio", { name: "Per day" }));
    expect(await within(section).findByText("Newest bill 24.0 kWh per day, up 51.4% from the bill before.")).toBeTruthy();
  });

  it("matches the cost and credits tables to their chart rows", async () => {
    renderTab();
    const cost = await card("Gas cost");
    const rows = costRows(POWER_DETAIL.cost, "gas", "therms");
    expect(await tableRows(cost)).toEqual(rows.map((r) => [expect.any(String), formatCost(r.allIn, "therms"), formatCost(r.exFixed, "therms")]));
    expect(within(cost).getByRole("columnheader", { name: "All in (per therm)" })).toBeTruthy();

    const credits = await card("Credits");
    expect(within(credits).getByRole("heading", { name: "$9.38 in credits" })).toBeTruthy();
    expect(await tableRows(credits)).toEqual([
      ["Jul 8, 2026", "Example credit", "−$2.19", "$2.19"],
      ["Aug 7, 2026", "Example credit, Savings credit", "−$7.19", "$9.38"],
    ]);
  });

  it("tones year over year changes: less use is better, days stay neutral", async () => {
    renderTab();
    const section = await card("Year over year");
    const table = within(section).getByRole("table");
    // The table scrolls sideways on phones, and by keyboard too.
    expect(within(section).getByRole("region", { name: "Each bill against last year" }).tabIndex).toBe(0);
    const headers = within(table)
      .getAllByRole("columnheader")
      .map((h) => h.textContent);
    expect(headers).toEqual([
      "Bill period",
      "Days",
      "kWh",
      "kWh per day",
      "therms",
      "therms per day",
      "Heating degree days",
      "therms per degree day",
      "Total",
    ]);
    const body = within(table).getAllByRole("row").slice(1);
    // Newest first. Sep: 719 kWh against 580, up 24%, which is worse.
    const sep = body[0]!;
    expect(sep.querySelector("th")?.textContent).toBe("Aug 4 to Sep 3, 2026vs Aug 4 to Sep 3, 2025");
    const kwh = sep.children[2]!;
    expect(kwh.textContent).toBe("719last year 580 · ↑ up 24.0%, worse");
    expect(kwh.querySelector("[data-tone]")?.getAttribute("data-tone")).toBe("bad");
    expect(kwh.querySelector("[data-tone]")?.className).toContain("text-bad");
    // Jul: kWh down, which is better. Days up 3.3% with no tone.
    const jul = body[2]!;
    expect(jul.children[2]!.querySelector("[data-tone]")?.getAttribute("data-tone")).toBe("good");
    expect(jul.children[2]!.querySelector("[data-tone]")?.className).toContain("text-good");
    expect(jul.children[1]!.textContent).toBe("31last year 30 · ↑ up 3.3%");
    expect(jul.children[1]!.querySelector("[data-tone]")?.getAttribute("data-tone")).toBe("neutral");
  });

  it("lists the bills newest first with their paid status and warnings", async () => {
    renderTab();
    const section = await card("Bills");
    const items = within(within(section).getByRole("list", { name: "Bills" })).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    // Today is after Sep 26, and the statements do not reach it yet.
    expect(items[0]!.textContent).toMatch(/No payment in yet\. Was due Sep 26|Not paid yet\. Due Sep 26/);
    expect(items[0]!.textContent).toContain("$174.25");
    expect(items[1]!.textContent).toContain("Estimated read");
    expect(items[1]!.textContent).toContain("Not the bill total.");
    expect(items[1]!.textContent).toContain("differ from the total by 1 or 2 cents");
    expect(items[2]!.textContent).toContain("Paid Jul 18 from Example checking");
    expect(items[2]!.textContent).toContain("Billed Jul 8 · 31 days · 420\u00a0kWh · 8\u00a0therms");
    expect(items[2]!.textContent).toContain("Electric\u00a0$83.90 · Gas\u00a0$30.00");
  });

  it("switches to quarterly water with gallons and no degree days", async () => {
    renderTab();
    await card("Electric use");
    await userEvent.setup().click(screen.getByRole("radio", { name: "Example Water" }));
    const water = await card("Water use");
    expect(urls()).toContain("/api/utilities/example-water?from=2025-10");
    expect(await tableRows(water)).toEqual([
      ["Mar 1 to Jun 1, 2026", "92", "5,000", "No bill", "None", "None", "None"],
      ["Jun 1 to Sep 1, 2026", "92", "5,642", "No bill", "None", "None", "+12.8%"],
    ]);
    expect(screen.getByText("Sewer use", { selector: "p.eyebrow" })).toBeTruthy();
    // Stormwater has no metered unit, so it has no chart.
    expect(screen.queryByText("Stormwater use")).toBeNull();
    const cost = await card("Water cost");
    expect(within(cost).getByRole("heading", { name: "Cost per 1,000 gallons" })).toBeTruthy();
    const yoy = within(await card("Year over year")).getByRole("table");
    const headers = within(yoy)
      .getAllByRole("columnheader")
      .map((h) => h.textContent);
    expect(headers).toEqual(["Bill period", "Days", "Water gallons", "Water gallons per day", "Sewer gallons", "Sewer gallons per day", "Total"]);
    expect(screen.getByText("No credits in this range.")).toBeTruthy();
    const bills = within(await card("Bills")).getAllByRole("listitem");
    expect(bills[0]!.textContent).toContain("Before our payment history");
    expect(bills[1]!.textContent).toContain("No payment by Jul 11, 40 days after the bill");
    expect(bills[1]!.textContent).toContain("Water 5,000\u00a0gallons · Sewer 5,000\u00a0gallons");
    expect(bills[1]!.textContent).toContain("Water\u00a0$29.81 · Sewer\u00a0$27.00 · Stormwater\u00a0$6.00");
    expect(water.textContent).toContain("No bills from last year to compare with yet.");
    expect(water.textContent).not.toContain("Same period last year");
  });

  it("asks for every bill on All", async () => {
    renderTab("&span=all");
    await card("Electric use");
    expect(urls()).toContain("/api/utilities/example-power");
  });

  it("shows an empty state with no utilities", async () => {
    list = { utilities: [] };
    renderTab();
    expect(await screen.findByText("No utility bills yet.")).toBeTruthy();
  });
});
