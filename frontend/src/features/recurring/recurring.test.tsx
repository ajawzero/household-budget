import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { ALL, CALENDAR, ITEMS, MONTHLY, PLANS, TAGGED } from "./fixtures";
import {
  allTotals,
  billSections,
  byPerson,
  cadenceText,
  cancelledRows,
  costCut,
  endingRows,
  keptText,
  lastPaidText,
  parseMonth,
  parsePerson,
  personOptions,
  railText,
  reviewText,
  savingsCard,
  seasonMonthlyText,
  seasons,
  seasonText,
  seasonTotalText,
  sectionName,
  stateCountText,
  subscriptionRows,
  subscriptionTotalText,
  weekday,
} from "./model";
import { ItemBadges } from "./DueList";
import { ChangeBadge } from "./parts";
import { RecurringPage } from "./RecurringPage";
import { stepRows, stepSummary } from "./StepDownChart";
import { paidOffText, warningText } from "./PlansView";
import { addMonths } from "../../lib/format";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

let failPlans = false;
let plans = PLANS;
let all = ALL;
let monthly = MONTHLY;
const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input);
  if (url.startsWith("/api/recurring/calendar")) return json(200, CALENDAR);
  if (url.startsWith("/api/recurring?view=all")) return json(200, all);
  if (url.startsWith("/api/recurring")) return json(200, monthly);
  if (url === "/api/plans") return failPlans ? json(500, { detail: "Server error." }) : json(200, plans);
  return json(404, { detail: "Not found." });
});

beforeEach(() => {
  failPlans = false;
  monthly = MONTHLY;
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function calls(): string[] {
  return fetchMock.mock.calls.map((c) => String(c[0]));
}

describe("recurring helpers", () => {
  it("names cadences and seasons in plain words", () => {
    expect(cadenceText("monthly")).toBe("Monthly");
    expect(cadenceText("every_n_weeks", 3)).toBe("Every 3 weeks");
    expect(cadenceText("annual")).toBe("Yearly");
    expect(seasonText([4, 5, 6, 7, 8, 9, 10])).toBe("Apr to Oct");
    expect(seasonText([3, 8])).toBe("Mar and Aug");
    expect(seasonText([3, 6, 9])).toBe("Mar, Jun and Sep");
    expect(seasonText([12])).toBe("Dec only");
    expect(seasonText([1, 2, 3, 4, 5, 9, 10, 11, 12])).toBe("Sep to May"); // across the new year
    expect(seasonText([1, 12])).toBe("Jan and Dec");
    expect(seasonText([1, 2, 6, 12])).toBe("Jan, Feb, Jun and Dec");
    expect(seasonText(null)).toBeNull();
    expect(seasonText([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])).toBeNull();
  });

  it("moves months and finds weekdays without time zone drift", () => {
    expect(addMonths("2026-12", 1)).toBe("2027-01");
    expect(addMonths("2026-01", -1)).toBe("2025-12");
    expect(weekday("2026-10-01")).toBe("Thu");
    expect(parseMonth("2026-13")).toBeUndefined();
    expect(parseMonth("2026-09")).toBe("2026-09");
  });

  it("reads the chat's cost cutting flags and review dates on active items only", () => {
    const by = new Map(ITEMS.map((i) => [i.key, i]));
    expect(costCut(by.get("stream")!)).toEqual({ key: "stream-cut", reason: "Cancel after the season", open: true });
    expect(costCut(by.get("net")!)).toEqual({ key: "net-rate", reason: "Ask for a lower rate", open: false });
    expect(costCut(by.get("fee")!)).toBeNull();
    expect(costCut(by.get("club")!)).toBeNull(); // flagged, but already ending
    expect(costCut(by.get("music")!)).toBeNull();
    expect(reviewText(by.get("fee")!)).toBe("Review by Feb 1, 2027");
    expect(reviewText(by.get("stream")!)).toBeNull();
    // Paid subscriptions plus flagged bills, in list order. The free one, the ending one and the fee are left out.
    expect(subscriptionRows(ITEMS).map((r) => r.item.key)).toEqual(["net", "stream"]);
    const unflagged = ITEMS.map((i) => ({ ...i, cost_cut: null }));
    expect(subscriptionRows(unflagged).map((r) => [r.item.key, r.cut])).toEqual([["stream", null]]);
  });

  it("names rails, last payments and end dates in plain words", () => {
    expect(railText("ach")).toBe("By ACH");
    expect(railText("bill_pay")).toBe("By bill pay");
    expect(railText("payroll")).toBe("From payroll");
    expect(railText("wire_transfer")).toBe("wire transfer");
    const by = new Map(ITEMS.map((i) => [i.key, i]));
    expect(lastPaidText(by.get("net")!)).toBe("Last paid Sep 14, 2026");
    expect(lastPaidText(by.get("stream")!)).toBe("Last paid Sep 28, 2026: $13.00");
    expect(lastPaidText(by.get("lawn")!)).toBe("No tagged payment yet");
    expect(lastPaidText(by.get("clean")!)).toBeNull(); // not counted
    expect(endingRows(ITEMS).map((r) => [r.item.key, r.when])).toEqual([
      ["club", "Cancelled, runs through Dec 31, 2026"],
      ["lawn", "Ends Oct 31, 2027"],
    ]);
    // A transfer that ends is not a bill, so "Bills drop by" leaves it out.
    const save = { ...ITEMS.find((i) => i.key === "save")!, ends_on: "2026-12-01" };
    expect(endingRows([save], ALL.sections)).toEqual([]);
  });

  it("lists each owner once by label, and filters by one only when there are two or more", () => {
    const people = personOptions(ITEMS);
    expect(people).toEqual([
      { key: "joint", label: "Joint" },
      { key: "riley", label: "Riley" },
      { key: "sam", label: "Sam" },
    ]);
    expect(parsePerson("sam", people)).toBe("sam");
    expect(parsePerson("nobody", people)).toBeNull();
    expect(parsePerson(null, people)).toBeNull();
    expect(parsePerson("sam", [{ key: "sam", label: "Sam" }])).toBeNull();
    expect(byPerson(ITEMS, null)).toBe(ITEMS);
    // The card fee has no owner: it shows under Everyone only.
    const keys = people.flatMap((p) => byPerson(ITEMS, p.key).map((i) => i.key));
    expect(keys).not.toContain("fee");
    expect(keys).toHaveLength(ITEMS.length - 1);
  });

  it("totals counted bills apart from savings and groups bills by section", () => {
    expect(allTotals(ITEMS, ALL.sections)).toEqual({ gross: 21_700, net: 20_700, count: 7, savings: 35_166, savingsCount: 3 });
    const groups = billSections(ITEMS, ALL.sections);
    expect(groups.map((g) => [g.section.key, g.items.map((i) => i.key), g.count, g.net, g.credits])).toEqual([
      ["lifestyle", ["stream", "music"], 2, 1_200, true],
      ["housing", ["net", "lawn", "clean"], 2, 8_500, false],
      ["kids", ["lessons"], 1, 6_000, false],
      ["services", ["club"], 1, 4_000, false],
      ["fees", ["fee"], 1, 1_000, false],
    ]);
    // No sections from the server: nothing to group.
    expect(billSections(ITEMS, undefined)).toEqual([]);
  });

  it("works out school-year and summer months from the by-month amounts", () => {
    const by = new Map(ITEMS.map((i) => [i.key, i]));
    const kids = seasons([by.get("lessons")!]);
    expect(kids).toEqual({ schoolMonthly: 8_000, summerMonthly: 0, schoolTotal: 72_000, summerTotal: 0 });
    expect(seasonMonthlyText(kids)).toBe("School year about $80 a month · summer about $0 a month");
    expect(seasonTotalText(kids)).toBe("About $720 over the school year");
    const housing = seasons([by.get("net")!, by.get("lawn")!, by.get("clean")!]);
    expect(housing?.schoolTotal).toBe(69_000); // the paused cleaning is not counted
    expect(housing?.summerMonthly).toBe(11_000);
    expect(seasonMonthlyText(housing)).toBe("School year about $77 a month · summer about $110 a month");
    expect(seasonTotalText(housing)).toBe("About $690 over the school year and $330 over the summer");
    // A summer-only bill leaves out the school-year part.
    const camp = { ...by.get("lessons")!, active_months: [6, 7, 8], by_month_cents: [0, 0, 0, 0, 0, 10_000, 10_000, 10_000, 0, 0, 0, 0] };
    expect(seasonTotalText(seasons([camp]))).toBe("About $300 over the summer");
    // Nothing seasonal: no season line.
    expect(seasons([by.get("net")!])).toBeNull();
    expect(seasonMonthlyText(null)).toBeNull();
    // Seasonal, but the same in whole dollars: no line.
    const even = { ...camp, by_month_cents: Array.from({ length: 12 }, () => 1_000) };
    expect(seasonMonthlyText(seasons([even]))).toBeNull();
  });

  it("finds where savings go and what cancelled items save", () => {
    const card = savingsCard(ITEMS, ALL.sections);
    expect(card.rows.map((r) => [r.item.key, r.where])).toEqual([
      ["save", "To Example Savings"],
      ["extra", "To Example Savings"],
      ["allow", "From Example checking"],
    ]);
    expect(card.total).toBe(35_166);
    expect(card.subtotals).toEqual([{ key: "example-savings", name: "Example Savings", cents: 30_833 }]);
    const today = "2026-10-20";
    expect(cancelledRows(ITEMS, ALL.sections, today).map((r) => [r.item.key, r.badge, r.when, r.yearly, r.from, r.transfer])).toEqual([
      ["club", "Cancelled", "Runs to Dec 31, 2026", 48_000, "from Jan 2027", false],
      ["oldstream", "Ended", "Ended Jun 30, 2026", 18_000, null, false],
    ]);
    const club = ITEMS.find((i) => i.key === "club")!;
    const one = (i: typeof club) => cancelledRows([i], ALL.sections, today)[0]!;
    // The stop date is the earlier of end_date and active_through, past or not.
    expect(one({ ...club, ends_on: null, end_date: null, active_through: "2027-03-15" }).from).toBe("from Apr 2027");
    expect(one({ ...club, ends_on: null, end_date: "2026-09-30", active_through: "2026-12-31" }).when).toBe("Stopped Sep 30, 2026");
    const past = one({ ...club, ends_on: null, end_date: null, active_through: "2026-10-15" });
    expect([past.when, past.from]).toEqual(["Stopped Oct 15, 2026", null]);
    // A stopped transfer saves nothing: it is marked, and the card leaves it out of the total.
    const save = ITEMS.find((i) => i.key === "save")!;
    expect(one({ ...save, status: "ended", counted: false, end_date: "2026-09-01" }).transfer).toBe(true);
  });

  it("counts a day's bills by state in words", () => {
    expect(stateCountText([{ state: "missed" }, { state: "paid" }, { state: "missed" }, { state: "waiting" }])).toBe(
      "1 paid, 1 statement not in, 2 missed",
    );
    expect(stateCountText([{ state: "no_history" }, { state: "missed" }])).toBe("1 with no payment history yet, 1 missed");
    expect(stateCountText([])).toBe("");
  });
});

describe("RecurringPage, Monthly", () => {
  it("shows the summary strip, Upcoming and Complete with states, badges and paid amounts", async () => {
    renderWithProviders(<RecurringPage />, { route: "/recurring?month=2026-09" });
    const strip = await screen.findByRole("list", { name: "Bills and transfers in Sep 2026" });
    await waitFor(() => expect(within(strip).getAllByRole("listitem")[0]?.textContent).toBe("$182Expected4 payments"));
    expect(within(strip).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "$182Expected4 payments",
      "$62Paid2 of 4 paid",
      "$120Remaining2 not paid yet",
    ]);
    expect(calls()).toContain("/api/recurring?view=monthly&month=2026-09");

    const upcoming = screen.getByRole("list", { name: "Upcoming" });
    const rows = within(upcoming).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]?.textContent).toContain("Example Lawn");
    expect(within(rows[0]!).getAllByText("Missed").length).toBeGreaterThan(0);
    // Season from the All view's item.
    await waitFor(() => expect(within(rows[0]!).getByText("Apr to Oct")).toBeTruthy());
    // The chat's note shows in full, with no label.
    expect(rows[0]?.textContent).toContain("Season runs April to October");
    expect(within(rows[1]!).getAllByText("Statement not in").length).toBeGreaterThan(0);
    expect(within(rows[1]!).getByText("Cost cutting")).toBeTruthy();
    expect(screen.queryByText(/Cancel candidate/)).toBeNull();

    const complete = screen.getByRole("list", { name: "Complete" });
    const paid = within(complete).getAllByRole("listitem");
    // A weekly or twice-a-month item can be due more than once: one row per due date.
    expect(paid).toHaveLength(2);
    expect(paid[1]?.textContent).toContain("$12.00");
    expect(paid[1]?.textContent).toContain("Paid Sep 29");
    expect(paid[1]?.textContent).toContain("Expected $10.00");
    expect(screen.getByText(/1 bill has no matching payment/)).toBeTruthy();
  });

  it("says No payment history yet, not Missed, for a bill with no tagged payment to check", async () => {
    monthly = { ...MONTHLY, upcoming: [{ ...MONTHLY.upcoming[0]!, state: "no_history" }, MONTHLY.upcoming[1]!] };
    renderWithProviders(<RecurringPage />, { route: "/recurring?month=2026-09" });
    const upcoming = await screen.findByRole("list", { name: "Upcoming" });
    const row = within(upcoming).getAllByRole("listitem")[0]!;
    const badges = within(row).getAllByText("No payment history yet");
    expect(badges[0]?.closest("[title]")?.getAttribute("title")).toBe(
      "Past due, but no tagged payment goes back this far, so the app cannot check it.",
    );
    expect(within(row).queryByText("Missed")).toBeNull();
    // On phones the banner is the only place that says why.
    expect(screen.getByText(/1 past due bill shows "No payment history yet": no tagged payment goes back that far/)).toBeTruthy();
    expect(screen.queryByText(/no matching payment/)).toBeNull();
  });

  it("moves between months and back to this month", async () => {
    renderWithProviders(<RecurringPage />, { route: "/recurring?month=2026-09" });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Next month" }));
    await waitFor(() => expect(calls()).toContain("/api/recurring?view=monthly&month=2026-10"));
    await user.click(screen.getByRole("button", { name: "This month" }));
    await waitFor(() => expect(calls()).toContain("/api/recurring?view=monthly"));
  });

  it("shows a calendar with a day list under it", async () => {
    renderWithProviders(<RecurringPage />, { route: "/recurring?view=calendar&month=2026-09" });
    // The day's label names each bill state, so it never lives in the icon color alone.
    const day = await screen.findByRole("button", { name: "Sep 15, 2026: 1 due, $60. 1 missed." });
    expect(screen.getByRole("button", { name: "Sep 14, 2026: 1 due, $50. 1 paid." })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sep 1, 2026: nothing due" }).hasAttribute("disabled")).toBe(true);
    // Today is not in this month, so the first day with a bill is picked.
    expect(screen.getByRole("region", { name: "Due Sep 14, 2026" }).textContent).toContain("Example Internet");
    await userEvent.setup().click(day);
    const list = screen.getByRole("region", { name: "Due Sep 15, 2026" });
    expect(list.textContent).toContain("Example Lawn");
    expect(list.textContent).toContain("Season runs April to October");
    expect(within(list).getAllByText("Missed").length).toBeGreaterThan(0);
    expect(day.getAttribute("aria-pressed")).toBe("true");
    expect(day.className).toContain("ring-2");
    const legend = screen.getByRole("list", { name: "Bill states" });
    expect(legend.textContent).toBe("PaidUpcomingStatement not inNo payment history yetMissed");
    // Each state has an icon shape next to its word.
    expect(within(legend).getAllByRole("listitem").map((li) => li.querySelectorAll("svg").length)).toEqual([1, 1, 1, 1, 1]);
  });

  it("lists paid subscriptions and flagged bills, marking the flagged ones, with no advice to cancel", async () => {
    renderWithProviders(<RecurringPage />, { route: "/recurring" });
    const heading = await screen.findByRole("heading", { name: "Subscriptions and memberships" });
    const card = heading.closest("section")!;
    expect(card.textContent).toContain("Paid subscriptions and memberships after credits, and any bill the chat flagged to cut.");
    const list = await within(card).findByRole("list", { name: "Subscriptions and memberships" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows.map((r) => r.querySelector("p")?.textContent)).toEqual(["Example Internet", "Example Stream"]);
    // Watching: a neutral badge and the flag's title in words.
    expect(within(rows[0]!).getByText("Watching")).toBeTruthy();
    expect(within(rows[0]!).getByText("Ask for a lower rate")).toBeTruthy();
    expect(within(rows[0]!).queryByText("Cost cutting")).toBeNull();
    expect(within(rows[1]!).getByText("Cost cutting")).toBeTruthy();
    expect(within(rows[1]!).getByText("Cancel after the season")).toBeTruthy();
    for (const name of ["Example card fee", "Example Music", "Example Club"]) expect(within(card).queryByText(name)).toBeNull();
    expect(within(card).getByText("Together they cost about $62 a month.")).toBeTruthy();
    expect(within(card).getByRole("link", { name: "See all flags" }).getAttribute("href")).toBe("/flags");
    // The flag's own title may say "Cancel"; the card itself never does.
    expect(card.textContent).not.toMatch(/Cancelling|Cancel candidate|saves/);
    // The end dates card is on the All tab only.
    expect(screen.queryByRole("heading", { name: "Bills that end" })).toBeNull();
  });

  it("still lists a paid subscription the chat did not flag, with no badge", async () => {
    all = structuredClone(ALL);
    for (const i of all.items!) i.cost_cut = null;
    renderWithProviders(<RecurringPage />, { route: "/recurring" });
    const heading = await screen.findByRole("heading", { name: "Subscriptions and memberships" });
    const list = await within(heading.closest("section")!).findByRole("list", { name: "Subscriptions and memberships" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows).toHaveLength(1);
    expect(rows[0]?.textContent).toContain("Example Stream");
    expect(within(rows[0]!).queryByText("Cost cutting")).toBeNull();
    expect(screen.getByText("Together they cost about $12 a month.")).toBeTruthy();
    all = ALL;
  });
});

/** The data rows of a section's table (md and up). */
async function sectionRows(label: string): Promise<HTMLElement[]> {
  return within(await screen.findByRole("table", { name: label })).getAllByRole("row").slice(1);
}

/** The rows of a section's phone list. */
function phoneRows(label: string): HTMLElement[] {
  return within(screen.getByRole("list", { name: label })).getAllByRole("listitem");
}

/** The card (a <section>) under a heading. */
async function cardOf(name: string): Promise<HTMLElement> {
  return (await screen.findByRole("heading", { name })).closest("section")!;
}

function LocationProbe() {
  const loc = useLocation();
  return <output data-testid="location">{loc.search}</output>;
}

describe("RecurringPage, All", () => {
  it("shows one card per bill section with its count and subtotal, paused last and not counted", async () => {
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=all" });
    const housing = await sectionRows("Housing and utilities");
    // Sections come in the server's order. Savings and transfers have their own card, not a table.
    expect(screen.getAllByRole("table").map((t) => t.querySelector("caption")?.textContent)).toEqual([
      "Lifestyle",
      "Housing and utilities",
      "Kids and education",
      "Services and fitness",
      "Fees and interest",
    ]);
    expect(housing.map((r) => r.querySelector("p")?.textContent)).toEqual(["Example Internet", "Example Lawn", "Example Cleaning"]);
    const [, lawn, cleaning] = housing;
    expect(lawn!.textContent).toContain("Apr to Oct");
    expect(lawn!.textContent).toContain("$60.00");
    expect(lawn!.textContent).toContain("$35.00");
    expect(cleaning!.textContent).toContain("Every 3 weeks");
    expect(cleaning!.textContent).toContain("Not counted");
    // The ended item is in the cancelled card, not in its section.
    const lifestyle = await sectionRows("Lifestyle");
    expect(lifestyle.map((r) => r.querySelector("p")?.textContent)).toEqual(["Example Stream", "Example Music"]);
    expect(lifestyle[1]!.textContent).toContain("$0.00 after credits");

    // Subtotals are net, counted items only. The paused cleaning is left out.
    const subtitle = async (name: string) => (await cardOf(name)).querySelector("header p")?.textContent;
    expect(await subtitle("Lifestyle")).toBe("2 bills · about $12 a month after credits");
    expect(await subtitle("Housing and utilities")).toBe("2 bills · 1 paused · about $85 a month");
    expect(await subtitle("Kids and education")).toBe("1 bill · about $60 a month");
    expect(screen.getAllByText(/Each bill as a monthly amount/)).toHaveLength(1);

    // Bills only in the first three tiles; savings and transfers in the fourth.
    const strip = screen.getByRole("list", { name: "Recurring totals" });
    expect(within(strip).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "$217A month7 bills counted",
      "$207After credits$10 a month in card credits",
      "$2,484A yearAfter credits",
      "$352Savings and transfersA month · 3 transfers",
    ]);
    // The tab is a link with aria-current.
    expect(screen.getByRole("link", { name: "All" }).getAttribute("aria-current")).toBe("page");
    // The wide table scrolls in its own labeled region, so a keyboard can reach every column.
    const region = screen.getByRole("region", { name: "Housing and utilities" });
    expect(region.getAttribute("tabindex")).toBe("0");
    expect(within(region).getByRole("table")).toBe(screen.getByRole("table", { name: "Housing and utilities" }));
  });

  it("keeps the empty state when there are no items at all", async () => {
    all = { ...ALL, items: [], sections: [] };
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=all" });
    expect(await screen.findByText("No recurring items yet.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "All recurring" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Person/ })).toBeNull();
    all = ALL;
  });

  it("shows what a credit is in the table and the phone list, not only on hover", async () => {
    all = structuredClone(ALL);
    all.items!.find((i) => i.key === "music")!.credit_note = "Card streaming credit";
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=all" });
    const music = (await sectionRows("Lifestyle"))[1]!;
    expect(music.textContent).toContain("$10.00 creditCard streaming credit");
    expect(Array.from(music.querySelectorAll("[title]")).map((el) => el.getAttribute("title"))).not.toContainEqual(expect.stringMatching(/credit/i));
    const phone = phoneRows("Lifestyle")[1]!;
    expect(phone.textContent).toContain("$10.00 credit · Card streaming credit");
    all = ALL;
  });

  it("dates an ending badge like Bills that end: the earlier of the end date and active through", () => {
    const ending = ALL.items.find((i) => i.status === "ending")!;
    render(<ItemBadges item={{ ...ending, end_date: "2026-11-30", active_through: "2026-12-31", ends_on: "2026-11-30" }} status="ending" />);
    expect(screen.getByText("Ending Nov 30")).toBeTruthy();
  });

  it("shows notes, how each bill is paid, dates to know and the last payment, in the table and the phone list", async () => {
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=all" });
    const [net, lawn, clean] = await sectionRows("Housing and utilities");
    const [stream] = await sectionRows("Lifestyle");
    const [club] = await sectionRows("Services and fitness");
    const [fee] = await sectionRows("Fees and interest");
    expect(lawn!.textContent).toContain("Season runs April to October");
    expect(within(lawn!).getByText("Ends Oct 2027")).toBeTruthy();
    expect(within(net!).getByText("By ACH")).toBeTruthy();
    expect(within(fee!).getByText("Renews Mar 1, 2027")).toBeTruthy();
    expect(within(fee!).getByText("Review by Feb 1, 2027")).toBeTruthy();
    expect(within(fee!).queryByText("Cost cutting")).toBeNull();
    expect(within(club!).getByText("Ending Dec 31")).toBeTruthy();
    expect(club!.textContent).not.toContain("Ends Dec");
    expect(club!.textContent).not.toContain("Cost cutting"); // flagged, but already ending
    expect(net!.textContent).not.toContain("Cost cutting"); // only watching
    expect(within(stream!).getByText("Cost cutting")).toBeTruthy();
    expect(net!.textContent).toContain("Last paid Sep 14, 2026");
    expect(fee!.textContent).toContain("Last paid Mar 2, 2026");
    expect(stream!.textContent).toContain("Last paid Sep 28, 2026: $13.00");
    expect(lawn!.textContent).toContain("No tagged payment yet");
    expect(clean!.textContent).not.toMatch(/Last paid|No tagged payment/);

    const phone = phoneRows("Housing and utilities");
    expect(phone[1]?.textContent).toContain("Season runs April to October");
    expect(phone[1]?.textContent).toContain("Example checking · By ACH");
    expect(phone[1]?.textContent).toContain("No tagged payment yet");
    expect(phone[0]?.textContent).toContain("Last paid Sep 14, 2026");
    expect(phone[2]?.textContent).not.toMatch(/Last paid|No tagged payment/);
    expect(phoneRows("Lifestyle")[0]?.textContent).toContain("Last paid Sep 28, 2026: $13.00");
    expect(phoneRows("Fees and interest")[0]?.textContent).toContain("Last paid Mar 2, 2026");
  });

  it("names the card to move a bill to once, as a badge, and an unknown rail on the phone line", async () => {
    all = structuredClone(ALL);
    const net = all.items!.find((i) => i.key === "net")!;
    Object.assign(net, { preferred_account_key: "example-card", preferred_account_name: "Example card", payment_rail: "wire_transfer" });
    const lawn = all.items!.find((i) => i.key === "lawn")!;
    Object.assign(lawn, { account_key: null, account_name: null, payment_rail: null });
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=all" });
    const [row] = await sectionRows("Housing and utilities");
    expect(within(row!).getAllByText("Move to Example card")).toHaveLength(1);
    expect(row!.textContent).not.toContain("Better on");
    const phone = phoneRows("Housing and utilities");
    expect(phone[0]?.textContent).toContain("Example checking · wire transfer");
    expect(within(phone[0]!).getAllByText("Move to Example card")).toHaveLength(1);
    expect(phone[0]?.textContent).not.toContain("Better on");
    expect(phone[1]?.textContent).toContain("Account not set");
    expect(phone[1]?.textContent).not.toContain("Account not set ·");
    all = ALL;
  });

  it("lists the bills that end, soonest first, with how much the bills drop", async () => {
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=all" });
    const heading = await screen.findByRole("heading", { name: "Bills that end" });
    const card = heading.closest("section")!;
    expect(card.textContent).toContain("Soonest first, with what each one costs a month.");
    const rows = within(await within(card).findByRole("list", { name: "Bills that end" })).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]?.textContent).toContain("Example Club");
    expect(rows[0]?.textContent).toContain("Cancelled, runs through Dec 31, 2026");
    expect(rows[0]?.textContent).toContain("$40.00");
    expect(rows[1]?.textContent).toContain("Example Lawn");
    expect(rows[1]?.textContent).toContain("Ends Oct 31, 2027");
    expect(within(card).getByText("Bills drop by about $75 a month by Oct 2027.")).toBeTruthy();
    // The subscriptions card sits beside it on this tab.
    expect(screen.getByRole("heading", { name: "Subscriptions and memberships" })).toBeTruthy();
  });

  it("says so when no bill has an end date", async () => {
    all = structuredClone(ALL);
    for (const i of all.items!) i.ends_on = null;
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=all" });
    const heading = await screen.findByRole("heading", { name: "Bills that end" });
    expect(await within(heading.closest("section")!).findByText("No bills with an end date.")).toBeTruthy();
    all = ALL;
  });

  it("says what a school-year and a summer month cost, and the kids' season totals", async () => {
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=all" });
    const housing = await cardOf("Housing and utilities");
    // Internet all year plus lawn care April to October.
    expect(within(housing).getByText("School year about $77 a month · summer about $110 a month")).toBeTruthy();
    expect(housing.textContent).not.toContain("over the school year");
    const kids = await cardOf("Kids and education");
    expect(within(kids).getByText("School year about $80 a month · summer about $0 a month")).toBeTruthy();
    // The summer part is zero, so it is left out.
    expect(within(kids).getByText("About $720 over the school year")).toBeTruthy();
    // No seasonal bills, no season line.
    expect((await cardOf("Lifestyle")).textContent).not.toMatch(/School year|summer/);
  });

  it("lists savings and transfers apart, with where each goes and a subtotal per place", async () => {
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=all" });
    const card = await cardOf("Savings and transfers");
    const rows = within(within(card).getByRole("list", { name: "Savings and transfers" })).getAllByRole("listitem");
    expect(rows.map((r) => r.querySelector("p")?.textContent)).toEqual(["Example savings transfer", "Example extra savings", "Example allowance"]);
    expect(rows[0]!.textContent).toContain("To Example Savings");
    expect(rows[0]!.textContent).toContain("Monthly · $200.00 each");
    expect(rows[0]!.textContent).toContain("$200.00a month");
    expect(rows[1]!.textContent).toContain("Every 2 weeks · $50.00 each");
    expect(rows[1]!.textContent).toContain("$108.33a month");
    // No tagged destination: the paying account instead.
    expect(rows[2]!.textContent).toContain("From Example checking");
    expect(within(card).getByText("To Example Savings: about $308 a month")).toBeTruthy();
    expect(within(card).getByText("Together about $352 a month.")).toBeTruthy();
  });

  it("keeps each transfer's note, how it is paid, next date and last payment", async () => {
    all = structuredClone(ALL);
    Object.assign(all.items!.find((i) => i.key === "save")!, {
      notes: "Example emergency fund",
      payment_rail: "ach",
      next_due: "2026-11-01",
      last_paid_date: "2026-10-01",
      last_paid_cents: 20_000,
    });
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=all" });
    const card = await cardOf("Savings and transfers");
    const [row] = within(within(card).getByRole("list", { name: "Savings and transfers" })).getAllByRole("listitem");
    expect(row!.textContent).toContain("To Example Savings · By ACH");
    expect(row!.textContent).toContain("Example emergency fund");
    expect(row!.textContent).toContain("next Nov 1");
    expect(row!.textContent).toContain("Last paid Oct 1, 2026");
    all = ALL;
  });

  it("mutes a paused transfer and leaves it out of the totals", async () => {
    all = structuredClone(ALL);
    Object.assign(all.items!.find((i) => i.key === "extra")!, { status: "paused", counted: false, next_due: null });
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=all" });
    const card = await cardOf("Savings and transfers");
    const extra = within(card).getAllByRole("listitem")[1]!;
    expect(extra.className).toContain("opacity-80");
    expect(within(extra).getByText("Paused")).toBeTruthy();
    expect(within(extra).getByText("Not counted")).toBeTruthy();
    // One counted row goes to Example Savings now, so no subtotal line.
    expect(card.textContent).not.toContain("To Example Savings:");
    expect(within(card).getByText("Together about $243 a month.")).toBeTruthy();
    const strip = screen.getByRole("list", { name: "Recurring totals" });
    expect(within(strip).getAllByRole("listitem")[3]?.textContent).toBe("$243Savings and transfersA month · 2 transfers");
    all = ALL;
  });

  it("lists cancelled and ended items with what each saves a year", async () => {
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=all" });
    const card = await cardOf("Cancelled and ended");
    const rows = within(await within(card).findByRole("list", { name: "Cancelled and ended" })).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]!).getByText("Cancelled")).toBeTruthy();
    expect(rows[0]!.textContent).toContain("Example Club");
    expect(rows[0]!.textContent).toContain("Runs to Dec 31, 2026");
    expect(rows[0]!.textContent).toContain("Saves about $480 a year");
    expect(rows[0]!.textContent).toContain("from Jan 2027");
    expect(within(rows[1]!).getByText("Ended")).toBeTruthy();
    expect(rows[1]!.textContent).toContain("Example old stream");
    expect(rows[1]!.textContent).toContain("Ended Jun 30, 2026");
    expect(rows[1]!.textContent).toContain("Saves about $180 a year");
    expect(rows[1]!.textContent).not.toContain("from");
    expect(within(card).getByText("Together the bills save about $660 a year.")).toBeTruthy();
  });

  it("says a stopped transfer no longer moves money, and leaves it out of the savings total", async () => {
    all = structuredClone(ALL);
    Object.assign(all.items!.find((i) => i.key === "save")!, { status: "ended", counted: false, end_date: "2026-09-01" });
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=all" });
    const card = await cardOf("Cancelled and ended");
    const row = within(await within(card).findByRole("list", { name: "Cancelled and ended" }))
      .getAllByRole("listitem")
      .find((r) => r.textContent!.includes("Example savings transfer"))!;
    expect(row.textContent).toContain("No longer moves about $2,400 a year");
    expect(row.textContent).not.toContain("Saves");
    expect(within(card).getByText("Together the bills save about $660 a year.")).toBeTruthy();
    all = ALL;
  });

  it("says so when nothing is cancelled or ended", async () => {
    all = structuredClone(ALL);
    all.items = all.items!.filter((i) => i.status !== "ending" && i.status !== "ended");
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=all" });
    expect(await within(await cardOf("Cancelled and ended")).findByText("Nothing cancelled or ended.")).toBeTruthy();
    all = ALL;
  });
});

describe("RecurringPage, All, person filter", () => {
  function renderAll(route = "/recurring?tab=all") {
    return renderWithProviders(
      <>
        <RecurringPage />
        <LocationProbe />
      </>,
      { route },
    );
  }

  it("narrows the tiles and every card to one person, and keeps the choice in the URL", async () => {
    renderAll();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Person: Everyone" }));
    expect((await screen.findAllByRole("menuitemradio")).map((o) => o.textContent)).toEqual(["Everyone", "Joint", "Riley", "Sam"]);
    await user.click(screen.getByRole("menuitemradio", { name: "Sam" }));
    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("?tab=all&person=sam"));
    expect(screen.getByRole("button", { name: "Person: Sam" })).toBeTruthy();

    const strip = screen.getByRole("list", { name: "Recurring totals" });
    expect(within(strip).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "$62A month3 bills counted",
      "$52After credits$10 a month in card credits",
      "$624A yearAfter credits",
      "$108Savings and transfersA month · 1 transfer",
    ]);
    // Sections with none of Sam's bills are hidden. The card fee has no owner, so it shows under Everyone only.
    expect(screen.getAllByRole("table").map((t) => t.querySelector("caption")?.textContent)).toEqual(["Lifestyle", "Services and fitness"]);
    expect(screen.queryByText("Example card fee")).toBeNull();
    const savings = await cardOf("Savings and transfers");
    expect(within(savings).getAllByRole("listitem")).toHaveLength(1);
    expect(savings.textContent).not.toContain("To Example Savings:");
    expect(within(savings).getByText("Together about $108 a month.")).toBeTruthy();
    expect(within(await cardOf("Subscriptions and memberships")).getByText("Together they cost about $12 a month.")).toBeTruthy();
    expect(within(await cardOf("Bills that end")).getAllByRole("listitem")).toHaveLength(1);
    expect(within(await cardOf("Cancelled and ended")).getAllByRole("listitem")).toHaveLength(2);

    await user.click(screen.getByRole("button", { name: "Person: Sam" }));
    await user.click(await screen.findByRole("menuitemradio", { name: "Everyone" }));
    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("?tab=all"));
    expect(screen.getAllByRole("table")).toHaveLength(5);
  });

  it("reads the person from the URL, and ignores one it does not know", async () => {
    const { unmount } = renderAll("/recurring?tab=all&person=riley");
    expect(await screen.findByRole("button", { name: "Person: Riley" })).toBeTruthy();
    expect(screen.getAllByRole("table").map((t) => t.querySelector("caption")?.textContent)).toEqual(["Kids and education"]);
    expect((await cardOf("Savings and transfers")).textContent).toContain("Example allowance");
    unmount();
    renderAll("/recurring?tab=all&person=nobody");
    expect(await screen.findByRole("button", { name: "Person: Everyone" })).toBeTruthy();
    expect(screen.getAllByRole("table")).toHaveLength(5);
  });

  it("shows no person filter when one person owns everything", async () => {
    all = structuredClone(ALL);
    for (const i of all.items!) Object.assign(i, { owner: "joint", owner_label: "Joint" });
    renderAll("/recurring?tab=all&person=joint");
    await screen.findByRole("table", { name: "Lifestyle" });
    expect(screen.queryByRole("button", { name: /Person/ })).toBeNull();
    expect(screen.getAllByRole("table")).toHaveLength(5);
    all = ALL;
  });
});

describe("recurring badges", () => {
  const by = new Map(ITEMS.map((i) => [i.key, i]));

  it("words the change badge, with the old amount only when there is one", () => {
    expect(render(<ChangeBadge change={null} />).container.textContent).toBe("");
    const down = render(<ChangeBadge change="rate_down" wasCents={null} />);
    expect(down.container.textContent).toBe("Rate down");
    expect(down.container.querySelector("[title]")).toBeNull();
    expect(render(<ChangeBadge change="rate_up" wasCents={4500} />).container.textContent).toBe("Rate up from $45.00");
  });

  it("marks new items and rate changes, with the old monthly amount on the badge", () => {
    const { unmount } = render(<ItemBadges item={by.get("stream")} status="active" />);
    expect(screen.getByText("New").getAttribute("title")).toBe("Not in the update before");
    unmount();
    const up = render(<ItemBadges item={by.get("net")} status="active" />);
    const rateUp = screen.getByText("Rate up from $45.00");
    expect(rateUp.getAttribute("title")).toBe("Was $45.00 a month");
    expect(rateUp.className).toContain("text-warn");
    up.unmount();
    render(<ItemBadges item={by.get("music")} status="active" />);
    expect(screen.getByText("Rate down from $12.00").getAttribute("title")).toBe("Was $12.00 a month");
    expect(screen.queryByText(/Rate up/)).toBeNull();
  });

  it("names the card to move a bill to, only when it differs and the bill is counted", () => {
    const lessons = by.get("lessons")!;
    const { unmount, container } = render(<ItemBadges item={lessons} status="active" />);
    expect(screen.getByText("Move to Example card")).toBeTruthy();
    expect(container.querySelector('[title="Paid from Example checking now"]')?.className).toContain("text-warn");
    unmount();
    const same = render(<ItemBadges item={{ ...lessons, preferred_account_key: lessons.account_key }} status="active" />);
    expect(screen.queryByText(/Move to/)).toBeNull();
    same.unmount();
    render(<ItemBadges item={{ ...lessons, status: "paused", counted: false }} status="paused" />);
    expect(screen.queryByText(/Move to/)).toBeNull();
  });

  it("shows the badges on the monthly rows too", async () => {
    renderWithProviders(<RecurringPage />, { route: "/recurring?month=2026-09" });
    const upcoming = await screen.findByRole("list", { name: "Upcoming" });
    await waitFor(() => expect(within(within(upcoming).getAllByRole("listitem")[1]!).getByText("New")).toBeTruthy());
    const paid = within(screen.getByRole("list", { name: "Complete" })).getAllByRole("listitem");
    expect(within(paid[0]!).getByText(/^Rate up from /)).toBeTruthy();
  });
});

describe("RecurringPage, Plans", () => {
  it("shows the deadline warning, totals, plans by card and progress", async () => {
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=plans" });
    const warnings = await screen.findByRole("list", { name: "Plan deadlines" });
    expect(warnings.textContent).toContain("Urgent: deferred interest.");
    expect(warnings.textContent).toContain("by Nov 20, 2026 (30 days left)");
    expect(warnings.textContent).toContain("$100 is still owed.");
    const strip = screen.getByRole("list", { name: "Plan totals" });
    expect(within(strip).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "$300Total deferred2 active plans",
      "$155A monthPayments plus fees",
      "Nov 2026ShortestExample sofa",
      "Jan 2027LongestExample gear",
    ]);
    const sofa = screen.getByRole("list", { name: "Plans on the Example card" });
    expect(sofa.textContent).toContain("4 of 6 payments");
    expect(sofa.textContent).toContain("2 left");
    expect(sofa.textContent).toContain("incl. $5.00 fee");
    expect(sofa.textContent).toContain("$30.00 in fees in all, $20.00 posted");
    expect(sofa.textContent).toContain("Nov 2026");
    const gear = screen.getByRole("list", { name: "Plans on the Example store card" });
    expect(gear.textContent).toContain("Urgent: pay before Nov 20, 2026");
    expect(sofa.textContent).toContain("In the spending plan");
    expect(gear.textContent).toContain("Example Outfitters $150 for Example trip");
    expect(within(gear).getByRole("link", { name: "Example trip" }).getAttribute("href")).toBe("/travel?trip=example-trip");
  });

  it("says when a card's plans are all paid off, and shows a paid-off plan's numbers", async () => {
    plans = structuredClone(PLANS);
    const sofa = plans.cards[0]!.plans[0]!;
    const done = (over: Partial<typeof sofa>): typeof sofa => ({ ...sofa, status: "paid_off", warning: null, payments_left: 0, payments_made: 6, ...over });
    plans.cards[0]!.plans.push(done({ key: "chair", name: "Example chair", remaining_cents: 0, end_month: "2026-03" }));
    const card = (key: string, name: string, list: (typeof sofa)[]) => ({ account_key: key, account_name: name, monthly_cents: 0, remaining_cents: 0, plans: list });
    plans.cards.push(
      card("example-old", "Example old card", [done({ key: "coats", name: "Example coats", remaining_cents: null, end_month: "2026-06" })]),
      card("example-early", "Example early card", [done({ key: "desk", name: "Example desk", end_month: "2027-01" })]),
      card("example-closed", "Example closed card", [
        done({ key: "lamp", name: "Example lamp", status: "closed", end_month: "2026-02" }),
        done({ key: "rug", name: "Example rug", end_month: "2026-04" }),
      ]),
    );
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=plans" });
    const subtitle = async (name: string) => (await screen.findByRole("heading", { name })).closest("section")!.querySelector("header p")?.textContent;
    expect(await subtitle("Example old card")).toBe("All plans paid off · last payment Jun 2026");
    expect(await subtitle("Example early card")).toBe("All plans paid off"); // paid early: no month yet
    expect(await subtitle("Example closed card")).toBe("No active plans · 1 paid off");
    expect(await subtitle("Example card")).toBe("1 plan, 1 paid off · $105 a month · $200 left");
    const old = within(screen.getByRole("list", { name: "Plans on the Example old card" })).getByRole("listitem");
    expect(old.textContent).toContain("Paid off");
    expect(old.textContent).toContain("A monthNone");
    expect(old.textContent).toContain("Left$0.00");
    expect(old.textContent).toContain("incl. $5.00 fee"); // fee lines stay
    plans = PLANS;
  });

  it("shows a plan's note, when it was paid off and its refund", async () => {
    plans = structuredClone(PLANS);
    const sofa = plans.cards[0]!.plans[0]!;
    const done = (over: Partial<typeof sofa>): typeof sofa => ({ ...sofa, status: "paid_off", warning: null, payments_left: 0, payments_made: 6, note: null, refund_cents: 0, ...over });
    plans.cards[0]!.plans.push(
      done({ key: "chair", name: "Example chair", paid_off_on: "2026-05-18", end_month: "2026-06", paid_off_early: true }),
      done({ key: "desk", name: "Example desk", paid_off_on: "2026-06-30", end_month: "2026-06", paid_off_early: false }),
      done({ key: "lamp", name: "Example lamp", status: "closed", paid_off_on: "2026-02-10", end_month: "2026-04", paid_off_early: true }),
    );
    plans.cards.push({
      account_key: "example-old",
      account_name: "Example old card",
      monthly_cents: 0,
      remaining_cents: 0,
      plans: [done({ key: "coats", name: "Example coats", paid_off_on: "2026-05-18", end_month: "2026-06", paid_off_early: true })],
    });
    sofa.note = "Pay it off before the fee goes up";
    sofa.refund_cents = 4_500;
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=plans" });
    const list = await screen.findByRole("list", { name: "Plans on the Example card" });
    const row = (name: string) => within(list).getAllByRole("listitem").find((li) => li.textContent?.includes(name))!;
    expect(row("Example sofa").textContent).toContain("Pay it off before the fee goes up");
    expect(row("Example sofa").textContent).toContain("Refund $45.00");
    expect(row("Example chair").textContent).toContain("Paid off May 2026 · early");
    expect(row("Example desk").textContent).toContain("Paid off Jun 2026");
    expect(row("Example desk").textContent).not.toContain("early");
    expect(row("Example lamp").textContent).toContain("Closed");
    expect(row("Example lamp").textContent).toContain("Paid off Feb 2026 · early");
    for (const name of ["Example chair", "Example desk", "Example lamp"]) expect(row(name).textContent).not.toContain("Refund");
    const gear = screen.getByRole("list", { name: "Plans on the Example store card" });
    expect(gear.textContent).not.toContain("Refund");
    const old = (await screen.findByRole("heading", { name: "Example old card" })).closest("section")!;
    expect(old.querySelector("header p")?.textContent).toBe("All plans paid off · last payment May 2026");
    plans = PLANS;
  });

  it("counts active plans in words and shows a known $0 balance", async () => {
    plans = structuredClone(PLANS);
    plans.active_count = 1;
    plans.cards[0]!.remaining_cents = 0;
    plans.cards[0]!.plans[0]!.remaining_cents = 0;
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=plans" });
    const card = (await screen.findByRole("heading", { name: "Example card" })).closest("section")!;
    const strip = screen.getByRole("list", { name: "Plan totals" });
    expect(within(strip).getAllByRole("listitem")[0]?.textContent).toBe("$300Total deferred1 active plan");
    expect(card.querySelector("header p")?.textContent).toBe("1 plan · $105 a month · $0 left");
    plans = PLANS;
  });

  it("names each deadline level in words, not only by color", async () => {
    plans = structuredClone(PLANS);
    plans.warnings[0]!.level = "medium";
    plans.cards[1]!.plans[0]!.warning!.level = "low";
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=plans" });
    const warnings = await screen.findByRole("list", { name: "Plan deadlines" });
    expect(warnings.textContent).toContain("Soon: deferred interest.");
    const gear = screen.getByRole("list", { name: "Plans on the Example store card" });
    expect(gear.textContent).toContain("Later: pay before Nov 20, 2026");
    plans = PLANS;
  });

  it("names a plan purchase's trip plainly when the app has no such trip", async () => {
    plans = structuredClone(PLANS);
    for (const card of plans.cards) for (const p of card.plans) for (const c of p.components) if (c.trip_key) c.trip_name = null;
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=plans" });
    const gear = await screen.findByRole("list", { name: "Plans on the Example store card" });
    expect(gear.textContent).toContain("Example Outfitters $150 (trip example-trip, not in Travel)");
    expect(within(gear).queryByRole("link")).toBeNull();
    plans = PLANS;
  });

  it("step-down table matches the chart data", async () => {
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=plans" });
    const heading = await screen.findByRole("heading", { name: "Step-downs" });
    const card = heading.closest("section")!;
    expect((await within(card).findByRole("img")).getAttribute("aria-label")).toBe(
      "Monthly plan payments fall from $155 in Oct 2026 to $0 by Feb 2027, in 2 steps.",
    );
    await userEvent.setup().click(within(card).getByRole("button", { name: "Show the numbers" }));
    const rows = within(within(card).getByRole("table"))
      .getAllByRole("row")
      .slice(1)
      .map((tr) => Array.from(tr.children).map((c) => c.textContent));
    expect(rows).toEqual(
      PLANS.step_downs.map((s) => [
        expect.any(String),
        `$${(s.monthly_cents / 100).toLocaleString("en-US")}`,
        s.drop_cents ? `−$${s.drop_cents / 100}` : "",
        s.ended.map((k) => (k === "sofa" ? "Example sofa" : "Example gear")).join(", "),
        String(s.plan_count),
      ]),
    );
    expect(rows[2]?.[0]).toBe("Dec 2026");
    expect(within(card).getByText(/less a month\. Example sofa is paid off\./)).toBeTruthy();
  });

  it("builds step rows, summaries and warning text", () => {
    const rows = stepRows(PLANS.step_downs, new Map([["sofa", "Example sofa"]]));
    expect(rows[4]?.endedNames).toEqual(["gear"]);
    expect(stepSummary([])).toBe("No plan payments.");
    expect(paidOffText(PLANS.cards[0]!.plans[0]!)).toBeNull();
    expect(paidOffText({ ...PLANS.cards[0]!.plans[0]!, paid_off_on: "2026-05-18", paid_off_early: false })).toBe("Paid off May 2026");
    expect(warningText(PLANS.warnings[0]!, null)).toBe(
      "Pay off Example gear on the Example store card by Nov 20, 2026 (30 days left). If any of it is left, interest is charged on the whole purchase.",
    );
    expect(
      warningText({ ...PLANS.warnings[0]!, kind: "promo_expiry", days_left: 1 }, 5_000),
    ).toBe("The 0% promo on Example gear (Example store card) ends Nov 20, 2026 (1 day left). $50 is still owed.");
  });

  it("shows one error banner when plans fail", async () => {
    failPlans = true;
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=plans" });
    expect((await screen.findByRole("alert")).textContent).toContain("Could not load recurring bills.");
  });
});

describe("recurring keep and tag (format 1.4)", () => {
  const tagged = new Map(TAGGED.items.map((i) => [i.key, i]));
  const plain = new Map(ITEMS.map((i) => [i.key, i]));

  beforeEach(() => {
    all = TAGGED;
  });

  afterEach(() => {
    all = ALL;
  });

  it("words the kept badge, with the review date only on an active bill", () => {
    const fee = tagged.get("fee")!;
    expect(keptText(fee)).toBe("Kept · Review by Feb 1, 2027");
    expect(keptText(tagged.get("stream")!)).toBe("Kept");
    expect(keptText({ ...fee, status: "paused", counted: false })).toBe("Kept");
    expect(keptText(plain.get("fee")!)).toBeNull();
  });

  it("puts kept subscriptions last and names them in the total", () => {
    const rows = subscriptionRows([tagged.get("stream")!, plain.get("net")!]);
    expect(rows.map((r) => [r.item.key, r.kept])).toEqual([
      ["net", false],
      ["stream", true],
    ]);
    expect(subscriptionTotalText(subscriptionRows(TAGGED.items))).toBe("Together about $62 a month. $12 of it you chose to keep.");
    expect(subscriptionTotalText(subscriptionRows(ITEMS))).toBe("Together they cost about $62 a month.");
  });

  it("lists tag sections in the server's order and names them with their group", () => {
    const groups = billSections(TAGGED.items, TAGGED.sections);
    expect(groups.map((g) => [g.section.key, g.items.map((i) => i.key)])).toEqual([
      ["lifestyle", ["music"]],
      ["lifestyle.streaming", ["stream"]],
      ["housing", ["net", "lawn", "clean"]],
      ["kids.hobbies", ["lessons"]],
      ["services", ["club"]],
      ["fees", ["fee"]],
    ]);
    expect(groups.map((g) => sectionName(g.section))).toEqual([
      "Lifestyle",
      "Streaming in Lifestyle",
      "Housing and utilities",
      "Hobbies in Kids and education",
      "Services and fitness",
      "Fees and interest",
    ]);
  });

  it("shows a card per tag right after its plan group, with the same totals", async () => {
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=all" });
    await sectionRows("Streaming in Lifestyle");
    expect(screen.getAllByRole("table").map((t) => t.querySelector("caption")?.textContent)).toEqual([
      "Lifestyle",
      "Streaming in Lifestyle",
      "Housing and utilities",
      "Hobbies in Kids and education",
      "Services and fitness",
      "Fees and interest",
    ]);
    // The phone lists and the scroll regions carry the same names.
    expect(phoneRows("Hobbies in Kids and education").map((r) => r.querySelector("p")?.textContent)).toEqual(["Example music lessons"]);
    expect(screen.getByRole("region", { name: "Streaming in Lifestyle" })).toBeTruthy();
    // The visible title is the tag alone; the subtitle names the plan group, then the count and monthly total.
    const subtitle = async (name: string) => (await cardOf(name)).querySelector("header p")?.textContent;
    expect(await subtitle("Streaming")).toBe("In Lifestyle · 1 bill · about $12 a month");
    expect(await subtitle("Hobbies")).toBe("In Kids and education · 1 bill · about $60 a month");
    expect(await subtitle("Lifestyle")).toBe("1 bill · about $0 a month after credits");
    // Kids has no untagged bill, so no card. Its Hobbies card says the season totals, as Kids did.
    expect(screen.queryByRole("heading", { name: "Kids and education" })).toBeNull();
    expect(within(await cardOf("Hobbies")).getByText("About $720 over the school year")).toBeTruthy();
    // The stream is in its tag card only.
    expect((await sectionRows("Lifestyle")).map((r) => r.querySelector("p")?.textContent)).toEqual(["Example Music"]);
    // Tags move no totals.
    const strip = screen.getByRole("list", { name: "Recurring totals" });
    expect(within(strip).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "$217A month7 bills counted",
      "$207After credits$10 a month in card credits",
      "$2,484A yearAfter credits",
      "$352Savings and transfersA month · 3 transfers",
    ]);
  });

  it("marks kept bills, with the review date on the same badge", async () => {
    renderWithProviders(<RecurringPage />, { route: "/recurring?tab=all" });
    const [fee] = await sectionRows("Fees and interest");
    const badge = within(fee!).getByText("Kept · Review by Feb 1, 2027");
    expect(badge.getAttribute("title")).toBe("You chose to keep this bill.");
    expect(badge.className).not.toMatch(/text-(warn|good|bad|accent)/);
    expect(within(phoneRows("Fees and interest")[0]!).getByText("Kept · Review by Feb 1, 2027")).toBeTruthy();
    expect(screen.queryByText("Review by Feb 1, 2027")).toBeNull();
    const [stream] = await sectionRows("Streaming in Lifestyle");
    expect(within(stream!).getByText("Kept")).toBeTruthy();
    // A kept bill with an open cost cutting flag shows both.
    expect(within(stream!).getByText("Cost cutting")).toBeTruthy();
  });

  it("lists kept subscriptions last on the card, with a Kept badge and the kept share", async () => {
    renderWithProviders(<RecurringPage />, { route: "/recurring" });
    const card = await cardOf("Subscriptions and memberships");
    const list = await within(card).findByRole("list", { name: "Subscriptions and memberships" });
    const rows = within(list).getAllByRole("listitem");
    expect(rows.map((r) => r.querySelector("p")?.textContent)).toEqual(["Example Internet", "Example Stream"]);
    expect(within(rows[0]!).queryByText("Kept")).toBeNull();
    expect(within(rows[1]!).getByText("Kept")).toBeTruthy();
    expect(within(rows[1]!).getByText("Cost cutting")).toBeTruthy();
    expect(within(rows[1]!).getByText("Cancel after the season")).toBeTruthy();
    expect(within(card).getByText("Together about $62 a month. $12 of it you chose to keep.")).toBeTruthy();
    expect(card.textContent).not.toMatch(/Cancelling|Cancel candidate|saves/);
  });

  it("shows Kept on the monthly rows too", async () => {
    renderWithProviders(<RecurringPage />, { route: "/recurring?month=2026-09" });
    const upcoming = await screen.findByRole("list", { name: "Upcoming" });
    await waitFor(() => expect(within(within(upcoming).getAllByRole("listitem")[1]!).getByText("Kept")).toBeTruthy());
  });
});
