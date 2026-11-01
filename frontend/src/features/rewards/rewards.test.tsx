import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RewardBenefit, RewardRedemption, RewardsResponse } from "../../lib/api";
import { renderWithProviders } from "../../test/render";
import { NO_REWARDS, REWARDS } from "./fixtures";
import {
  balanceText,
  benefitGroups,
  benefitLine,
  cppText,
  daysLeftText,
  formatPoints,
  holderColors,
  MICRO,
  parseTab,
  rangeText,
  redemptionText,
  usedText,
} from "./model";
import { RewardsPage } from "./RewardsPage";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

let payload: RewardsResponse | "error" = REWARDS;
const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input);
  if (url === "/api/rewards") return payload === "error" ? json(500, { detail: "Server error." }) : json(200, payload);
  return json(404, { detail: "Not found." });
});

beforeEach(() => {
  payload = REWARDS;
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function benefit(key: string): RewardBenefit {
  const b = REWARDS.benefits.find((x) => x.key === key);
  if (!b) throw new Error(key);
  return b;
}

describe("rewards helpers", () => {
  it("formats points, balances, ranges and cents per point", () => {
    expect(formatPoints(84_250 * MICRO)).toBe("84,250");
    expect(formatPoints(37_420_000)).toBe("37.42");
    expect(formatPoints(null)).toBe("None");
    expect(balanceText({ kind: "miles", balance_micro: 42_000 * MICRO })).toBe("42,000 miles");
    expect(balanceText({ kind: "points", balance_micro: MICRO })).toBe("1 point");
    expect(balanceText({ kind: "cash_back", balance_micro: 37_420_000 })).toBe("$37.42");
    expect(rangeText(101_100, 151_650)).toBe("$1,011 to $1,517");
    expect(rangeText(3_742, 3_742)).toBe("$37");
    expect(rangeText(null, null)).toBe("Not valued");
    expect(cppText("1.2", "1.8")).toBe("1.2 to 1.8¢ a point");
    expect(cppText(null, null)).toBeNull();
  });

  it("words a redemption in the program's unit", () => {
    expect(redemptionText("miles", 50_000 * MICRO, 42_000 * MICRO)).toBe("needed 50,000, have 42,000 miles");
    expect(redemptionText("cash_back", 25 * MICRO, 37_420_000)).toBe("needed $25.00, have $37.42");
    expect(redemptionText("points", 50_000 * MICRO, null)).toBe("needed 50,000 points, no balance");
    expect(usedText("miles", 50_000 * MICRO)).toBe("Used 50,000 miles");
    expect(usedText("cash_back", 25 * MICRO)).toBe("Used $25.00");
    expect(usedText("points", MICRO)).toBe("Used 1 point");
    // Format 1.4: with the day it was used.
    expect(usedText("points", 20_000 * MICRO, "2026-04-18")).toBe("Used 20,000 points on Apr 18, 2026");
    expect(usedText("points", null, "2026-04-18")).toBe("Used on Apr 18, 2026");
    expect(usedText("miles", 50_000 * MICRO, null)).toBe("Used 50,000 miles");
  });

  it("counts days left and reads the tab from the URL", () => {
    expect(daysLeftText(0)).toBe("Ends today");
    expect(daysLeftText(1)).toBe("1 day left");
    expect(daysLeftText(11)).toBe("11 days left");
    expect(daysLeftText(null)).toBeNull();
    expect(parseTab("fees")).toBe("fees");
    expect(parseTab("nope")).toBe("points");
    expect(parseTab(null)).toBe("points");
  });

  it("groups benefits in the API's order and words each line", () => {
    expect(benefitGroups(REWARDS.benefits).map((g) => [g.group, g.items.map((b) => b.key)])).toEqual([
      ["open", ["dining-credit", "travel-credit", "anniversary-night"]],
      ["auto", ["wireless-credit"]],
      ["used", ["streaming-credit"]],
    ]);
    expect(benefitLine(benefit("dining-credit"))).toBe("$10.00 to use · ends Oct 31 · 11 days left");
    expect(benefitLine(benefit("streaming-credit"))).toBe("$15.00 used · ends Dec 31");
    expect(benefitLine(benefit("anniversary-night"))).toBe("Expires Mar 31 · 162 days left");
  });

  it("gives cardholders fixed colors by key, never by size", () => {
    const a = holderColors(["sam", "alex", null]);
    const b = holderColors(["alex", "sam"]);
    expect(a.get("alex")).toBe(b.get("alex"));
    expect(a.get("sam")).toBe(b.get("sam"));
    expect(a.get(null)).toBe("var(--chart-muted)");
  });
});

describe("rewards page", () => {
  it("shows the empty state before the first 1.3 run", async () => {
    payload = NO_REWARDS;
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    expect(await screen.findByText("No rewards data yet.")).toBeTruthy();
    expect(screen.getByText("It comes with the chat's next export.")).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Rewards summary" })).toBeNull();
  });

  it("shows an error with a retry", async () => {
    payload = "error";
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    expect(await screen.findByText("Could not load rewards.")).toBeTruthy();
  });

  // Empty texts that would wrongly say there is nothing, next to the banner.
  const EMPTY_TEXTS = [
    "None",
    "Nothing due",
    "No programs with a value.",
    "None right now.",
    "No current benefits.",
    "No earning caps.",
    "No spend milestones right now.",
    "No routing yet.",
    "No annual fees or memberships.",
  ];

  it.each([
    ["points", 3],
    ["benefits", 2],
    ["milestones", 1],
    ["routing", 1],
    ["fees", 1],
  ] as const)("keeps the %s cards quiet when the page fails to load", async (tab, cards) => {
    payload = "error";
    renderWithProviders(<RewardsPage />, { route: `/rewards?tab=${tab}` });
    expect((await screen.findByRole("alert")).textContent).toContain("Could not load rewards.");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getAllByText("Not loaded.")).toHaveLength(cards);
    for (const text of EMPTY_TEXTS) expect(screen.queryByText(text)).toBeNull();
    const tiles = screen.getByRole("list", { name: "Rewards summary" });
    expect(within(tiles).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Estimated value",
      "Credits left this cycle",
      "Next deadline",
    ]);
  });

  it("says None on the deadline tile only when nothing is due", async () => {
    payload = structuredClone(REWARDS);
    payload.tiles!.next_deadline = null;
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const tiles = await screen.findByRole("list", { name: "Rewards summary" });
    expect(await within(tiles).findByText("None")).toBeTruthy();
    expect(within(tiles).getByText("Nothing due")).toBeTruthy();
  });

  it("leaves Soon and the clock off a deadline more than 14 days out", async () => {
    payload = structuredClone(REWARDS);
    payload.tiles!.next_deadline!.days_left = 30;
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const tiles = await screen.findByRole("list", { name: "Rewards summary" });
    expect(await within(tiles).findByText("Enroll in Yearly travel credit · 30 days left")).toBeTruthy();
    expect(within(tiles).getByText("Oct 31").querySelector("svg")).toBeNull();
  });

  it("shows the tiles and tabs that scroll inside their bar", async () => {
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const tiles = await screen.findByRole("list", { name: "Rewards summary" });
    expect(await within(tiles).findByText("$1,468 to $2,142")).toBeTruthy();
    expect(within(tiles).getByText("3 programs valued")).toBeTruthy();
    expect(within(tiles).getByText("$180")).toBeTruthy();
    expect(within(tiles).getByText("5 credits")).toBeTruthy();
    expect(within(tiles).getByText("Oct 31")).toBeTruthy();
    // Within 14 days: a word and a clock icon, not only the warning color.
    expect(within(tiles).getByText("Soon · Enroll in Yearly travel credit · 11 days left")).toBeTruthy();
    expect(within(tiles).getByText("Oct 31").querySelector("svg")).not.toBeNull();
    const tabs = screen.getByRole("navigation", { name: "Rewards views" });
    expect(tabs.className).toContain("overflow-x-auto");
    expect(within(tabs).getAllByRole("link").map((a) => a.textContent)).toEqual(["Points", "Benefits", "Milestones", "Routing", "Fees"]);
    expect(within(tabs).getByRole("link", { name: "Points" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByText(/From the Oct 12, 2026 update/)).toBeTruthy();
  });

  it("shows programs, the value table and redemptions on Points", async () => {
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const banner = await screen.findByRole("region", { name: "Highlights" });
    expect(within(banner).getByText("The dining credit resets on the first of each month")).toBeTruthy();
    const main = screen.getByRole("list", { name: "Main programs" });
    expect(within(main).getByText("Summit Points")).toBeTruthy();
    expect(within(main).getByText("84,250 points")).toBeTruthy();
    expect(within(main).getByText("Worth $1,011 to $1,517")).toBeTruthy();
    expect(within(main).getByText("Transfer to airline partners")).toBeTruthy();
    expect(within(main).getByText("Gift cards")).toBeTruthy();
    expect(within(main).getByText("Silver status through the Cobalt card")).toBeTruthy();
    expect(within(main).getByText("Sam")).toBeTruthy(); // owner when set
    const other = screen.getByRole("list", { name: "Other programs" });
    expect(within(other).getByText("$37.42")).toBeTruthy();
    const club = within(other).getByRole("list", { name: "Shared Vacation Club by year" });
    expect(within(club).getByText("200 left")).toBeTruthy();
    expect(within(club).getByText("1,000 allocated, 0 used, 200 pulled forward")).toBeTruthy();
    expect(within(club).getAllByText("Book by Nov 30 · Expires Dec 31").length).toBe(2);
    const table = screen.getByRole("table", { name: "Estimated value by program" });
    expect(screen.getByRole("region", { name: "Estimated value by program" }).contains(table)).toBe(true);
    expect(within(table).getAllByText("Not valued").length).toBe(1);
    expect(within(table).getByText("Cash back at face value")).toBeTruthy();
    expect(within(table).getByText("$1,468")).toBeTruthy();
    const ready = screen.getByRole("region", { name: "Ready" });
    expect(within(ready).getByText("needed 50,000, have 42,000 miles")).toBeTruthy();
    expect(within(ready).getByText("Short")).toBeTruthy();
    const consider = screen.getByRole("region", { name: "Consider" });
    expect(within(consider).getByText("needed 60,000, have 84,250 points")).toBeTruthy();
    // Each redemption links to its trip.
    expect(within(ready).getByRole("link", { name: "Trip: Ski weekend" }).getAttribute("href")).toBe("/travel?trip=ski-weekend");
    expect(within(consider).getByRole("link", { name: "Trip: Beach week" }).getAttribute("href")).toBe("/travel?trip=beach-week");
  });

  it("says what a done redemption used, never Short, and links to its trip", async () => {
    const [ski, beach] = REWARDS.redemptions as [RewardRedemption, RewardRedemption];
    // enough false on a done row too: the page never says Short on one.
    payload = { ...REWARDS, redemptions: [{ ...ski, status: "done" }, { ...beach, status: "done", trip_key: "no-such-trip", trip_name: null }] };
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const done = await screen.findByRole("region", { name: "Done" });
    expect(within(done).getByText("Used 50,000 miles")).toBeTruthy();
    expect(within(done).getByText("Used 60,000 points")).toBeTruthy();
    expect(within(done).queryByText(/needed/)).toBeNull();
    expect(within(done).queryByText("Short")).toBeNull();
    // Only the trip the app has gets a link.
    expect(within(done).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual(["/travel?trip=ski-weekend"]);
    expect(screen.queryByRole("region", { name: "Ready" })).toBeNull();
  });

  it("says the day a done redemption was used, in the server's order", async () => {
    const [ski, beach] = REWARDS.redemptions as [RewardRedemption, RewardRedemption];
    payload = {
      ...REWARDS,
      redemptions: [
        { ...beach, key: "fall", title: "Fall stay", status: "done", done_on: "2026-09-15" },
        { ...ski, key: "spring", title: "Spring flights", status: "done", done_on: "2026-04-18" },
        { ...ski, key: "free", title: "A free night", status: "done", points_needed_micro: null, done_on: "2026-03-01" },
        { ...ski, key: "old", title: "An old trip", status: "done", points_needed_micro: null, done_on: null },
      ],
    };
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const done = await screen.findByRole("region", { name: "Done" });
    const rows = within(done).getAllByRole("listitem");
    expect(rows.map((li) => li.querySelector("p")!.textContent)).toEqual(["Fall stay", "Spring flights", "A free night", "An old trip"]);
    expect(within(rows[0]!).getByText("Used 60,000 points on Sep 15, 2026")).toBeTruthy();
    expect(within(rows[1]!).getByText("Used 50,000 miles on Apr 18, 2026")).toBeTruthy();
    expect(within(rows[2]!).getByText("Used on Mar 1, 2026")).toBeTruthy();
    expect(within(rows[3]!).queryByText(/^Used/)).toBeNull(); // no points and no day: nothing to say
  });

  it("shows the last redeemed day on a program card and on other programs", async () => {
    payload = structuredClone(REWARDS);
    payload.programs.find((p) => p.key === "harbor-miles")!.last_redeemed_on = "2026-09-15";
    payload.programs.find((p) => p.key === "pine-cash-back")!.last_redeemed_on = "2026-04-18";
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const other = await screen.findByRole("list", { name: "Other programs" });
    expect(within(other).getByText("Last redeemed Apr 18, 2026")).toBeTruthy();
    expect(within(other).getAllByText(/^Last redeemed/)).toHaveLength(1); // the timeshare has none
    const harbor = screen.getByRole("heading", { name: "Harbor Miles" }).closest("header")!.parentElement!;
    expect(within(harbor).getByText("Last redeemed Sep 15, 2026")).toBeTruthy();
    expect(screen.getAllByText(/^Last redeemed/)).toHaveLength(2); // Summit Points has none
  });

  it("shows best uses, avoid, earn cards and expiry on other programs", async () => {
    payload = structuredClone(REWARDS);
    Object.assign(payload.programs.find((p) => p.key === "pine-cash-back")!, {
      best_uses: ["Statement credit"],
      avoid: ["Gift cards"],
      expires_on: "2027-09-30",
    });
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const other = await screen.findByRole("list", { name: "Other programs" });
    const pine = within(other).getByText("Pine Cash Back").closest("li")!;
    expect(pine.textContent).toContain("Earned on Orchard card");
    expect(pine.textContent).toContain("Points expire Sep 30, 2027");
    expect(within(pine).getByText("Best uses")).toBeTruthy();
    expect(within(pine).getByText("Statement credit")).toBeTruthy();
    expect(within(pine).getByText("Avoid")).toBeTruthy();
    expect(within(pine).getByText("Gift cards")).toBeTruthy();
    // Empty lists draw nothing.
    const club = within(other).getByText("Shared Vacation Club").closest("li")!;
    expect(within(club).queryByText("Best uses")).toBeNull();
    expect(within(club).queryByText("Avoid")).toBeNull();
    expect(club.textContent).not.toContain("Earned on");
  });

  it("words redemptions on cash back and on a program with no balance", async () => {
    payload = structuredClone(REWARDS);
    const [ready, consider] = payload.redemptions as [RewardRedemption, RewardRedemption];
    Object.assign(ready, { program_key: "shared-vacation-club", program_name: "Shared Vacation Club", have_micro: null, enough: null });
    Object.assign(consider, { program_key: "pine-cash-back", program_name: "Pine Cash Back", points_needed_micro: 25 * MICRO, have_micro: 37_420_000 });
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const readyList = await screen.findByRole("region", { name: "Ready" });
    expect(within(readyList).getByText("needed 50,000 points, no balance")).toBeTruthy();
    expect(within(readyList).queryByText("Short")).toBeNull();
    const considerList = screen.getByRole("region", { name: "Consider" });
    expect(within(considerList).getByText("needed $25.00, have $37.42")).toBeTruthy();
    expect(screen.queryByText(/None/)).toBeNull();
  });

  it("puts the value range chart's numbers in a table", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    await screen.findByRole("list", { name: "Main programs" });
    const card = screen.getByRole("heading", { name: "Value range by program" }).closest("section")!;
    await user.click(within(card as HTMLElement).getByRole("button", { name: "Show the numbers" }));
    const table = within(card as HTMLElement).getByRole("table", { name: "Value range by program" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => r.textContent)).toEqual(["Summit Points$1,011$1,517", "Harbor Miles$420$588", "Pine Cash Back$37$37"]);
  });

  it("lists use-or-lose benefits with flags, and past cycles behind a button", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RewardsPage />, { route: "/rewards?tab=benefits" });
    const open = await screen.findByRole("region", { name: "Open this cycle" });
    const items = within(open).getAllByRole("listitem");
    expect(items[0]!.textContent).toContain("Dining credit");
    expect(items[0]!.textContent).toContain("11 days left");
    expect(within(open).getByText("Not enrolled. Enroll by Oct 31, 2026.")).toBeTruthy();
    const auto = screen.getByRole("region", { name: "Loads by itself" });
    expect(within(auto).getByText("Auto")).toBeTruthy();
    const used = screen.getByRole("region", { name: "Used this cycle" });
    expect(within(used).getByText("Used")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Dining credit past cycles" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Show past cycles" }));
    const past = screen.getByRole("region", { name: "Dining credit past cycles" });
    expect(within(past).getByText("Expired")).toBeTruthy(); // gray, not red
    expect(within(past).getByText("$6.00 of $10.00")).toBeTruthy();
    expect(within(past).getByText("Expired").className).not.toContain("text-bad");
  });

  it("shows earning caps by cardholder with the chat's number in the table", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RewardsPage />, { route: "/rewards?tab=benefits" });
    await screen.findByRole("region", { name: "Open this cycle" });
    const card = screen.getByRole("heading", { name: "Earning caps" }).closest("section") as HTMLElement;
    expect(within(card).getByText("$1,500 of $6,000")).toBeTruthy();
    expect(within(card).getByText("Sam")).toBeTruthy();
    expect(within(card).getByText("No cardholder")).toBeTruthy();
    expect(within(card).getByText("$4,500 left")).toBeTruthy();
    expect(within(card).getByText("The chat counts $1,820 through Sep 30.")).toBeTruthy();
    await user.click(within(card).getByRole("button", { name: "Show the numbers" }));
    const table = within(card).getByRole("table");
    expect(within(table).getByText("$1,820 as of Sep 30")).toBeTruthy();
    expect(screen.getByText("Hotel status")).toBeTruthy();
  });

  it("shows milestones with pace, the gap to the chat and the monthly chart", async () => {
    renderWithProviders(<RewardsPage />, { route: "/rewards?tab=milestones" });
    expect(await screen.findByText("Summit card · 2026")).toBeTruthy();
    expect(screen.getByText("$24,698 to go")).toBeTruthy();
    expect(screen.getByText("Need ~$6,707/mo")).toBeTruthy();
    expect(screen.getByText("$7,650")).toBeTruthy();
    expect(screen.getAllByText("Behind pace").map((e) => e.tagName)).toContain("SPAN"); // the pace badge
    expect(screen.getByText("The chat counts $14,650 through Sep 30. The rows show $5,302.")).toBeTruthy();
    expect(screen.getByText("Met Aug 14")).toBeTruthy();
    expect(screen.getByText("Rows from May 1 through Sep 22, 2026.")).toBeTruthy();
    expect(screen.getAllByText("Show the numbers").length).toBe(2);
  });

  it.each(["open", "missed"] as const)("shows a row-computed met milestone when the imported status is %s", async (status) => {
    payload = structuredClone(REWARDS);
    const m = payload.milestones.find((m) => m.key === "summit-spend")!;
    m.status = status;
    m.threshold_cents = m.spent_cents;
    m.to_go_cents = 0;
    m.need_per_month_cents = null;
    m.pace = "met";
    payload.milestones = [m];
    renderWithProviders(<RewardsPage />, { route: "/rewards?tab=milestones" });
    await screen.findByText("Summit card · 2026");
    expect(screen.getByText("Met")).toBeTruthy();
    expect(screen.queryByText("Open")).toBeNull();
    expect(screen.queryByText("Missed")).toBeNull();
    expect(screen.queryByText("To go")).toBeNull();
    expect(screen.queryByText("Year-end projection")).toBeNull();
    const progress = screen.getByRole("img", { name: "$5,302 of $5,302 spent" });
    expect((progress.firstElementChild as HTMLElement).style.background).toBe("var(--fill-good)");
  });

  it.each(["open", "missed"] as const)("shows a missed milestone once when the imported status is %s", async (status) => {
    payload = structuredClone(REWARDS);
    const m = payload.milestones.find((m) => m.key === "summit-spend")!;
    m.status = status;
    m.pace = "missed"; // the rows reached Dec 31 short of the threshold
    m.rows_through = "2026-12-31";
    m.need_per_month_cents = null;
    m.note = null;
    payload.milestones = [m];
    renderWithProviders(<RewardsPage />, { route: "/rewards?tab=milestones" });
    await screen.findByText("Summit card · 2026");
    expect(screen.getAllByText("Missed")).toHaveLength(1);
    expect(screen.queryByText("Open")).toBeNull();
    expect(screen.queryByText("To go")).toBeNull();
    expect(screen.queryByText("Year-end projection")).toBeNull();
    expect(screen.queryByText("Pace")).toBeNull();
    expect(screen.getByText("$5,302 spent")).toBeTruthy();
  });

  it("lists the bills on each card and the ones set to move there", async () => {
    renderWithProviders(<RewardsPage />, { route: "/rewards?tab=milestones" });
    await screen.findByText("Summit card · 2026");
    expect(screen.getByText("Bills on this card: about $53 a month")).toBeTruthy();
    const summit = screen.getByRole("list", { name: "Bills on Summit card" });
    expect(within(summit).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Shield Pest Control", "Cloud backup"]);
    expect(screen.getByText("Set to move here, about $65 a month: Wave Mobile")).toBeTruthy();
    expect(screen.getByText("Bills on this card: about $15 a month")).toBeTruthy();
    const cobalt = screen.getByRole("list", { name: "Bills on Cobalt card" });
    expect(within(cobalt).getByText("Personal subscription")).toBeTruthy();
    expect(screen.getAllByText(/^Set to move here/)).toHaveLength(1); // none for the Cobalt card
  });

  it("shows only the move line when the card has no bills of its own", async () => {
    payload = structuredClone(REWARDS);
    const m = payload.milestones.find((m) => m.key === "summit-spend")!;
    m.bills = [];
    m.bills_monthly_cents = 0;
    m.bills_to_move.push({ key: "cloud", name: "Cloud storage", monthly_cents: 999, account_name: "Cobalt card" });
    payload.milestones = [m];
    renderWithProviders(<RewardsPage />, { route: "/rewards?tab=milestones" });
    await screen.findByText("Summit card · 2026");
    expect(screen.getByText("Set to move here, about $75 a month: Wave Mobile, Cloud storage")).toBeTruthy();
    expect(screen.queryByText(/^Bills on this card/)).toBeNull();
    expect(screen.queryByRole("list", { name: "Bills on Summit card" })).toBeNull();
  });

  it("shows no bills block when a card has no bills and none to move", async () => {
    payload = structuredClone(REWARDS);
    for (const m of payload.milestones) {
      m.bills = [];
      m.bills_monthly_cents = 0;
      m.bills_to_move = [];
    }
    renderWithProviders(<RewardsPage />, { route: "/rewards?tab=milestones" });
    await screen.findByText("Summit card · 2026");
    expect(screen.queryByText(/^Bills on this card/)).toBeNull();
    expect(screen.queryByText(/^Set to move here/)).toBeNull();
    expect(screen.queryByRole("list", { name: /^Bills on/ })).toBeNull();
  });

  it.each(["primary", "secondary"] as const)("shows timeshare allocation years for a %s program", async (group) => {
    payload = structuredClone(REWARDS);
    payload.programs.find((p) => p.key === "shared-vacation-club")!.group = group;
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    await screen.findByRole("list", { name: "Main programs" });
    const club = screen.getByRole("list", { name: "Shared Vacation Club by year" });
    expect(within(club).getByText("200 left")).toBeTruthy();
    expect(within(club).getByText("1,000 allocated, 0 used, 200 pulled forward")).toBeTruthy();
    expect(within(club).getAllByText("Book by Nov 30 · Expires Dec 31").length).toBe(2);
  });

  it("shows routing in display order", async () => {
    renderWithProviders(<RewardsPage />, { route: "/rewards?tab=routing" });
    const table = await screen.findByRole("table", { name: "Card routing reference" });
    const region = screen.getByRole("region", { name: "Card routing reference" });
    expect(region.contains(table)).toBe(true);
    expect(region.getAttribute("tabindex")).toBe("0");
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => within(r).getAllByRole("cell")[0]!.textContent)).toEqual(["Summit card", "Cobalt card", "Summit card"]);
    expect(within(rows[0]!).getByText("Dining and groceries")).toBeTruthy();
  });

  it("wraps long free text from the chat on phones instead of widening the page", async () => {
    payload = structuredClone(REWARDS);
    payload.routing[0]!.earn = "3x on dining, 2x on travel, 1x on everything else";
    payload.perks[0]!.status_tier = "Lifetime Platinum Elite with suite upgrades at check-in";
    const routing = renderWithProviders(<RewardsPage />, { route: "/rewards?tab=routing" });
    const list = await screen.findByRole("list", { name: "Card routing reference" });
    const earn = within(list).getByText("3x on dining, 2x on travel, 1x on everything else");
    expect(earn.className).toContain("max-w-[40%]");
    expect(earn.className).toContain("break-words");
    routing.unmount();
    renderWithProviders(<RewardsPage />, { route: "/rewards?tab=benefits" });
    const tier = await screen.findByText("Lifetime Platinum Elite with suite upgrades at check-in");
    expect(tier.className).toContain("break-words");
    expect(tier.className).not.toContain("whitespace-nowrap");
  });

  it("shows fees net of credits, with the credits under the card", async () => {
    renderWithProviders(<RewardsPage />, { route: "/rewards?tab=fees" });
    const table = await screen.findByRole("table", { name: "Annual fees and memberships" });
    expect(screen.getByRole("region", { name: "Annual fees and memberships" }).contains(table)).toBe(true);
    expect(within(table).getByText("Summit card annual fee")).toBeTruthy();
    const credits = within(table).getByRole("list", { name: "Credits on Summit card annual fee" });
    expect(within(credits).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Airline fee credit: $100.00",
      "Dining credit: $100.00 of $130.00",
      "Yearly travel credit: $100.00",
    ]);
    expect(within(table).getAllByText("Net positive").length).toBe(1);
    expect(within(table).getByText("Warehouse club")).toBeTruthy();
    const foot = within(table).getAllByRole("row").at(-1)!;
    expect(foot.textContent).toBe("Total$315−$300$15");
  });

  it("shows a counted free night and a counted credit with no full value without a full amount", async () => {
    payload = structuredClone(REWARDS);
    payload.fees.rows[0]!.credits.push(
      { key: "anniversary-night", name: "Anniversary night", cents: 25_000, full_cents: 25_000, counted: true },
      { key: "traveler-credit", name: "Trusted traveler credit", cents: 10_000, full_cents: 0, counted: true },
    );
    renderWithProviders(<RewardsPage />, { route: "/rewards?tab=fees" });
    const table = await screen.findByRole("table", { name: "Annual fees and memberships" });
    const credits = within(table).getByRole("list", { name: "Credits on Summit card annual fee" });
    expect(within(credits).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Airline fee credit: $100.00",
      "Dining credit: $100.00 of $130.00",
      "Yearly travel credit: $100.00",
      "Anniversary night: $250.00",
      "Trusted traveler credit: $100.00",
    ]);
  });
});
