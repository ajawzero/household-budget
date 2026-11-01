// Rewards inputs (docs/plans/rewards-inputs.md chunk B): source labels, the
// Update balances drawer, balance history, Add a program, the program notices
// and who sees which control. Made-up numbers only.
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BalanceHistory, RewardProgram, RewardsResponse } from "../../lib/api";
import { renderWithProviders } from "../../test/render";
import { todayIso } from "../accounts/vehicles/api";
import { SourceLine } from "../recurring/parts";
import { programBody } from "./AddProgram";
import { historyRows } from "./BalanceHistory";
import { savedText } from "./BalancesDrawer";
import { NO_REWARDS, REWARDS } from "./fixtures";
import { entryText, lastValueText, MICRO, parseBalance, parseCpp, sameDayText, sameUnit } from "./model";
import { programLabel } from "./ProgramNotices";
import { RewardsPage } from "./RewardsPage";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

type Reply = (body: unknown) => Response;

let payload: RewardsResponse | "error" = REWARDS;
let role: "admin" | "member" = "admin";
let replies: Record<string, Reply> = {};

const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const method = init?.method ?? "GET";
  if (url === "/api/auth/me") return json(200, { username: "alex", display_name: "Alex", role });
  if (url === "/api/rewards" && method === "GET") return payload === "error" ? json(500, { detail: "Server error." }) : json(200, payload);
  const reply = replies[`${method} ${url}`];
  if (reply) return reply(init?.body ? JSON.parse(String(init.body)) : undefined);
  return json(404, { detail: "Not found." });
});

beforeEach(() => {
  payload = REWARDS;
  role = "admin";
  replies = {};
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function program(key: string, from: RewardsResponse = REWARDS): RewardProgram {
  const p = from.programs.find((x) => x.key === key);
  if (!p) throw new Error(key);
  return p;
}

/** A program added in the app, like an airline the chat never sends. */
function appProgram(key: string, name: string, over: Partial<RewardProgram> = {}): RewardProgram {
  return {
    ...program("pine-cash-back"),
    key,
    name,
    kind: "points",
    origin: "app",
    source: "app",
    balance_micro: 12_500 * MICRO,
    as_of: "2026-10-02",
    owner_key: null,
    owner_name: null,
    earned_on: [],
    cpp_low: null,
    cpp_high: null,
    value_low_cents: null,
    value_high_cents: null,
    ...over,
  };
}

function calls(method: string, url: string) {
  return fetchMock.mock.calls.filter(([u, init]) => String(u) === url && (init?.method ?? "GET") === method);
}

function sentBody(method: string, url: string): unknown {
  const call = calls(method, url).at(-1);
  if (!call) throw new Error(`${method} ${url} was not sent`);
  return JSON.parse(String(call[1]!.body));
}

/** The text an element's aria-describedby points to. */
function described(el: HTMLElement): string | null {
  const id = el.getAttribute("aria-describedby");
  return id ? (document.getElementById(id)?.textContent ?? null) : null;
}

function invalidated(spy: { mock: { calls: unknown[][] } }): string[] {
  return spy.mock.calls.map((c) => ((c[0] as { queryKey: string[] }).queryKey ?? [])[0]!);
}

const HARBOR_HISTORY: BalanceHistory = {
  program_key: "harbor-miles",
  program: { ...program("harbor-miles"), source: "app", balance_micro: 52_000 * MICRO, as_of: "2026-10-12" },
  entries: [
    { id: 7, as_of: "2026-10-12", balance_micro: 52_000 * MICRO, shown: true, unit: "whole" },
    { id: 3, as_of: "2026-08-02", balance_micro: 38_000 * MICRO, shown: false, unit: "whole" },
  ],
  chat: { balance_micro: 42_000 * MICRO, as_of: "2026-10-01", source: "manual" },
};

describe("rewards inputs helpers", () => {
  it("reads balance fields by kind, with commas and a dollar sign", () => {
    expect(parseBalance("points", "52,000")).toBe(String(52_000));
    expect(parseBalance("miles", " 7 ")).toBe("7");
    expect(parseBalance("points", "0")).toBe("0");
    expect(parseBalance("points", "")).toBeNull();
    expect(parseBalance("points", "   ")).toBeNull();
    expect(parseBalance("points", "52.5")).toBeUndefined();
    expect(parseBalance("miles", "-3")).toBeUndefined();
    expect(parseBalance("points", "lots")).toBeUndefined();
    expect(parseBalance("cash_back", "$37.42")).toBe("37.42");
    expect(parseBalance("cash_back", "1,037.4")).toBe("1037.40");
    expect(parseBalance("cash_back", "37")).toBe("37.00");
    expect(parseBalance("cash_back", "37.425")).toBeUndefined();
    // Whole balances go up to 10 digits, like the API.
    expect(parseBalance("points", "12,000,000")).toBe(String(12_000_000));
    expect(parseBalance("miles", "9,999,999,999")).toBe(String(9_999_999_999));
    expect(parseBalance("points", String(12_345_678_901))).toBeUndefined(); // 11 digits
  });

  it("keeps balances in their unit and names programs apart", () => {
    expect(sameUnit("points", "whole")).toBe(true);
    expect(sameUnit("miles", "whole")).toBe(true);
    expect(sameUnit("cash_back", "cash")).toBe(true);
    expect(sameUnit("cash_back", "whole")).toBe(false);
    expect(sameUnit("points", "cash")).toBe(false);
    expect(sameUnit("miles", "cash")).toBe(false);
    expect(sameUnit("timeshare", "whole")).toBe(false);
    expect(sameUnit("timeshare", "cash")).toBe(false);
    expect(sameUnit("points", null)).toBe(false);
    expect(programLabel(program("harbor-miles"))).toBe("Harbor Miles · Sam");
    expect(programLabel(program("summit-points"))).toBe("Summit Points");
    expect(programLabel(appProgram("app-harbor-miles", "Harbor Miles"))).toBe("Harbor Miles (added in the app)");
  });

  it("words an entry in the unit it was typed in", () => {
    expect(entryText("miles", "whole", 52_000 * MICRO)).toBe("52,000 miles");
    expect(entryText("points", "whole", MICRO)).toBe("1 point");
    expect(entryText("cash_back", "cash", 41.5 * MICRO)).toBe("$41.50");
    // Another unit than the program's now: the entry keeps its own.
    expect(entryText("miles", "cash", 37.42 * MICRO)).toBe("$37.42");
    expect(entryText("cash_back", "whole", 52_000 * MICRO)).toBe("52,000 points or miles");
    expect(entryText("cash_back", "whole", MICRO)).toBe("1 point or mile");
    expect(entryText("timeshare", "whole", 8_000 * MICRO)).toBe("8,000 points or miles");
  });

  it("reads cents per point", () => {
    expect(parseCpp("1.2")).toBe("1.2");
    expect(parseCpp(" 1.25¢ ")).toBe("1.25");
    expect(parseCpp("")).toBeNull();
    expect(parseCpp("123")).toBeUndefined();
    expect(parseCpp("one")).toBeUndefined();
  });

  it("words the last value, the same-day note and the saved count", () => {
    expect(lastValueText(program("harbor-miles"))).toBe("42,000 as of Oct 1");
    expect(lastValueText(program("pine-cash-back"))).toBe("$37.42 as of Sep 30");
    expect(lastValueText({ kind: "points", balance_micro: null, as_of: null })).toBe("No balance yet");
    expect(sameDayText({ kind: "miles", chat_balance_micro: 40_000 * MICRO })).toBe("The chat has 40,000 for the same day.");
    expect(sameDayText({ kind: "cash_back", chat_balance_micro: 40 * MICRO })).toBe("The chat has $40.00 for the same day.");
    expect(sameDayText({ kind: "miles", chat_balance_micro: null })).toBeNull();
    expect(savedText(1)).toBe("Saved 1 balance.");
    expect(savedText(3)).toBe("Saved 3 balances.");
  });

  it("checks the program form before it sends", () => {
    const base = { name: "Harbor Hotels", kind: "points" as const, group: "secondary" as const, low: "", high: "" };
    expect(programBody({ ...base, name: "  " })).toBe("Enter a name.");
    expect(programBody({ ...base, low: "1.2" })).toMatch(/^Enter both ends/);
    expect(programBody({ ...base, low: "1.8", high: "1.2" })).toMatch(/^Enter both ends/);
    expect(programBody({ ...base, low: "x", high: "1.2" })).toMatch(/^Enter both ends/);
    expect(programBody({ ...base, kind: "cash_back", low: "1.2" })).toEqual({ name: "Harbor Hotels", kind: "cash_back", group: "secondary" });
    expect(programBody({ ...base, name: " Harbor Hotels ", low: "0.8", high: "1.1" })).toEqual({
      name: "Harbor Hotels",
      kind: "points",
      group: "secondary",
      cpp_low: "0.8",
      cpp_high: "1.1",
    });
  });

  it("puts history newest first, the app's entry ahead of the chat's on the same day", () => {
    const h: BalanceHistory = { ...HARBOR_HISTORY, chat: { balance_micro: 40_000 * MICRO, as_of: "2026-10-12", source: "statement" } };
    expect(historyRows(h).map((r) => [r.asOf, r.source, r.shown, r.unit])).toEqual([
      ["2026-10-12", "app", true, "whole"],
      ["2026-10-12", "statement", false, null], // the chat's is in the program's unit
      ["2026-08-02", "app", false, "whole"],
    ]);
    const chatWins: BalanceHistory = { ...HARBOR_HISTORY, program: program("harbor-miles"), entries: [] };
    expect(historyRows(chatWins).map((r) => [r.source, r.shown, r.id])).toEqual([["manual", true, null]]);
  });
});

describe("Points tab inputs", () => {
  it("labels where each balance came from, with the same-day note", async () => {
    payload = structuredClone(REWARDS);
    Object.assign(program("pine-cash-back", payload), { source: "app", chat_balance_micro: 40 * MICRO });
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const main = await screen.findByRole("list", { name: "Main programs" });
    expect(within(main).getByText("· Given to the chat")).toBeTruthy(); // Harbor Miles, manual
    expect(within(main).getByText("· From a statement")).toBeTruthy(); // Summit Points
    const other = screen.getByRole("list", { name: "Other programs" });
    expect(within(other).getByText("· Entered in the app")).toBeTruthy();
    expect(within(other).getByText("The chat has $40.00 for the same day.")).toBeTruthy();
    expect(screen.queryByText(/Entered by hand/)).toBeNull();
    expect(screen.queryByText("Added in the app")).toBeNull();
  });

  it("marks programs added in the app", async () => {
    payload = structuredClone(REWARDS);
    payload.programs.push(appProgram("app-river-rewards", "River Rewards"));
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const other = await screen.findByRole("list", { name: "Other programs" });
    const row = within(other).getByText("River Rewards").closest("li")!;
    expect(within(row).getByText("Added in the app")).toBeTruthy();
    expect(within(row).getByText("· Entered in the app")).toBeTruthy();
  });

  it("asks for a new balance when a stale one came from the chat or the app, not a statement", async () => {
    const user = userEvent.setup();
    payload = structuredClone(REWARDS);
    program("harbor-miles", payload).stale = true;
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const main = await screen.findByRole("list", { name: "Main programs" });
    await user.hover(within(main).getByText("as of Oct 1").parentElement!);
    expect((await screen.findByRole("tooltip")).textContent).toBe("Balance as of Oct 1, 2026. Older than 90 days. Enter a new balance.");
    // In words on the card too: a phone has no hover.
    expect(within(main).getByText("· Older than 90 days. Enter a new balance.")).toBeTruthy();
  });

  it("keeps the statement wording on a stale statement balance", async () => {
    const user = userEvent.setup();
    payload = structuredClone(REWARDS);
    program("summit-points", payload).stale = true;
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const main = await screen.findByRole("list", { name: "Main programs" });
    await user.hover(within(main).getByText("as of Sep 30").parentElement!);
    expect((await screen.findByRole("tooltip")).textContent).toBe("Balance as of Sep 30, 2026. A newer statement is overdue.");
    expect(within(main).getByText("· A newer statement is overdue.")).toBeTruthy();
  });

  it("opens Update balances with one row per program, today's date and the last values", async () => {
    const user = userEvent.setup();
    role = "member"; // any signed-in user
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    await user.click(await screen.findByRole("button", { name: "Update balances" }));
    const drawer = screen.getByRole("dialog", { name: "Update balances" });
    const rows = within(drawer).getByRole("list", { name: "Programs" });
    expect(within(rows).getAllByRole("textbox").map((i) => i.getAttribute("placeholder"))).toEqual([
      "42,000 as of Oct 1",
      "84,250 as of Sep 30",
      "$37.42 as of Sep 30",
    ]);
    expect(within(drawer).queryByText("Shared Vacation Club")).toBeNull(); // timeshares use yearly points
    const harbor = within(drawer).getByRole("textbox", { name: "Harbor Miles · Sam" });
    expect(harbor.getAttribute("inputmode")).toBe("numeric");
    expect(within(drawer).getByRole("textbox", { name: "Pine Cash Back · Alex" }).getAttribute("inputmode")).toBe("decimal");
    // 16px text so iOS does not zoom, 44px tall on phones.
    expect(harbor.className).toContain("text-base");
    expect(harbor.className).toContain("h-11");
    const date = within(drawer).getByLabelText("Balances as of") as HTMLInputElement;
    expect(date.value).toBe(todayIso());
    expect(date.max).toBe(todayIso());
    const save = within(drawer).getByRole("button", { name: "Save all" });
    expect(save.className).toContain("h-11");
    // The bar sticks flush to the bottom on phones, past the scroll area's padding, and keeps the safe area.
    const bar = save.parentElement!.className.split(" ");
    expect(bar).toEqual(
      expect.arrayContaining([
        "sticky",
        "-bottom-[max(20px,env(safe-area-inset-bottom))]",
        "-mb-[max(20px,env(safe-area-inset-bottom))]",
        "pb-[max(12px,env(safe-area-inset-bottom))]",
        "md:static",
        "md:mb-0",
      ]),
    );
  });

  it("saves the filled rows, skips empty ones and refreshes everything that reads balances", async () => {
    const user = userEvent.setup();
    replies["POST /api/rewards/balances"] = (body) => json(201, { saved: (body as { entries: unknown[] }).entries.length, programs: [] });
    const { queryClient } = renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    await user.click(await screen.findByRole("button", { name: "Update balances" }));
    const drawer = screen.getByRole("dialog", { name: "Update balances" });
    await user.type(within(drawer).getByRole("textbox", { name: "Harbor Miles · Sam" }), "52,000");
    await user.type(within(drawer).getByRole("textbox", { name: "Pine Cash Back · Alex" }), "$41.5");
    await user.click(within(drawer).getByRole("button", { name: "Save all" }));
    expect(await within(drawer).findByText("Saved 2 balances.")).toBeTruthy();
    expect(sentBody("POST", "/api/rewards/balances")).toEqual({
      entries: [
        { program_key: "harbor-miles", balance: String(52_000), as_of: todayIso() },
        { program_key: "pine-cash-back", balance: "41.50", as_of: todayIso() },
      ],
    });
    expect(invalidated(spy)).toEqual(["rewards", "dashboard", "checks", "trips"]);
    await waitFor(() => expect(calls("GET", "/api/rewards").length).toBe(2));
    expect((within(drawer).getByRole("textbox", { name: "Harbor Miles · Sam" }) as HTMLInputElement).value).toBe("");
  });

  it("marks a bad row, an empty form and a future date, and sends nothing", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    await user.click(await screen.findByRole("button", { name: "Update balances" }));
    const drawer = screen.getByRole("dialog", { name: "Update balances" });
    const save = within(drawer).getByRole("button", { name: "Save all" });
    await user.click(save);
    expect(within(drawer).getByText("Enter at least one balance.")).toBeTruthy();
    const summit = within(drawer).getByRole("textbox", { name: "Summit Points" });
    await user.type(summit, "52.5");
    await user.click(save);
    expect(within(drawer).getByText("Use whole points, like 52,000.")).toBeTruthy();
    expect(within(drawer).getByText("Fix the balances marked above.")).toBeTruthy();
    expect(summit.getAttribute("aria-invalid")).toBe("true");
    await user.clear(summit);
    await user.type(summit, "52,500");
    const date = within(drawer).getByLabelText("Balances as of");
    await user.clear(date);
    await user.type(date, "2099-01-01");
    await user.click(save);
    expect(within(drawer).getByText("Pick today or an earlier date.")).toBeTruthy();
    expect(summit.getAttribute("aria-invalid")).toBeNull();
    expect(calls("POST", "/api/rewards/balances")).toHaveLength(0);
  });

  it("shows the server's message when a save is refused", async () => {
    const user = userEvent.setup();
    replies["POST /api/rewards/balances"] = () => json(409, { detail: "Rewards start with the chat's first export." });
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    await user.click(await screen.findByRole("button", { name: "Update balances" }));
    const drawer = screen.getByRole("dialog", { name: "Update balances" });
    await user.type(within(drawer).getByRole("textbox", { name: "Summit Points" }), "1");
    await user.click(within(drawer).getByRole("button", { name: "Save all" }));
    expect(await within(drawer).findByText("Rewards start with the chat's first export.")).toBeTruthy();
    expect(within(drawer).queryByText(/Saved/)).toBeNull();
  });

  it("opens Update balances from the link and drops the link on close", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RewardsPage />, { route: "/rewards?update=balances" });
    const drawer = await screen.findByRole("dialog", { name: "Update balances" });
    await user.click(within(drawer).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await user.click(screen.getByRole("button", { name: "Update balances" }));
    expect(screen.getByRole("dialog", { name: "Update balances" })).toBeTruthy();
  });

  it("lists a program's balance history and deletes an app entry", async () => {
    const user = userEvent.setup();
    let history = HARBOR_HISTORY;
    replies["GET /api/rewards/programs/harbor-miles/balances"] = () => json(200, history);
    replies["DELETE /api/rewards/balances/7"] = () => {
      history = { ...HARBOR_HISTORY, program: program("harbor-miles"), entries: HARBOR_HISTORY.entries.slice(1) };
      return json(200, history);
    };
    role = "member";
    const { queryClient } = renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    await user.click(await screen.findByRole("button", { name: "Balance history for Harbor Miles" }));
    const drawer = screen.getByRole("dialog", { name: "Harbor Miles balance history" });
    const list = await within(drawer).findByRole("list", { name: "Balance history" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "52,000 milesShownOct 12, 2026 · Entered in the appDelete",
      "42,000 milesOct 1, 2026 · Given to the chat",
      "38,000 milesAug 2, 2026 · Entered in the appDelete",
    ]);
    expect(within(drawer).queryByRole("button", { name: "Delete this program" })).toBeNull(); // a chat program
    await user.click(within(list).getByRole("button", { name: "Delete the Oct 12, 2026 balance" }));
    await waitFor(() => expect(within(list).queryByText("52,000 miles")).toBeNull());
    expect(calls("DELETE", "/api/rewards/balances/7")).toHaveLength(1);
    expect(within(list).getAllByRole("listitem")[0]!.textContent).toBe("42,000 milesShownOct 1, 2026 · Given to the chat");
    expect(invalidated(spy)).toEqual(expect.arrayContaining(["rewards", "dashboard", "checks", "trips"]));
  });

  it("shows each history entry in its own unit and marks the ones not counted", async () => {
    const user = userEvent.setup();
    // Pine Cash Back counted points before the chat moved it to cash back.
    replies["GET /api/rewards/programs/pine-cash-back/balances"] = () =>
      json(200, {
        program_key: "pine-cash-back",
        program: { ...program("pine-cash-back"), source: "app", balance_micro: 41.5 * MICRO, as_of: "2026-10-10" },
        entries: [
          { id: 9, as_of: "2026-10-10", balance_micro: 41.5 * MICRO, shown: true, unit: "cash" },
          { id: 4, as_of: "2026-07-01", balance_micro: 5_200 * MICRO, shown: false, unit: "whole" },
          { id: 2, as_of: "2026-06-01", balance_micro: MICRO, shown: false, unit: "whole" },
        ],
        chat: { balance_micro: 37.42 * MICRO, as_of: "2026-09-30", source: "statement" },
      } satisfies BalanceHistory);
    // Harbor Miles with an entry typed as cash back.
    replies["GET /api/rewards/programs/harbor-miles/balances"] = () =>
      json(200, { ...HARBOR_HISTORY, entries: [...HARBOR_HISTORY.entries, { id: 1, as_of: "2026-05-02", balance_micro: 12 * MICRO, shown: false, unit: "cash" }] });
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    await user.click(await screen.findByRole("button", { name: "Balance history for Pine Cash Back" }));
    let drawer = screen.getByRole("dialog", { name: "Pine Cash Back balance history" });
    let list = await within(drawer).findByRole("list", { name: "Balance history" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "$41.50ShownOct 10, 2026 · Entered in the appDelete",
      "$37.42Sep 30, 2026 · From a statement",
      "5,200 points or milesJul 1, 2026 · Entered in the appNot counted: entered in another unit.Delete",
      "1 point or mileJun 1, 2026 · Entered in the appNot counted: entered in another unit.Delete",
    ]);
    // An entry in another unit can still be deleted.
    expect(within(list).getByRole("button", { name: "Delete the Jul 1, 2026 balance" })).toBeTruthy();
    await user.click(within(drawer).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await user.click(screen.getByRole("button", { name: "Balance history for Harbor Miles" }));
    drawer = screen.getByRole("dialog", { name: "Harbor Miles balance history" });
    list = await within(drawer).findByRole("list", { name: "Balance history" });
    expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "52,000 milesShownOct 12, 2026 · Entered in the appDelete",
      "42,000 milesOct 1, 2026 · Given to the chat",
      "38,000 milesAug 2, 2026 · Entered in the appDelete",
      "$12.00May 2, 2026 · Entered in the appNot counted: entered in another unit.Delete",
    ]);
    expect(within(list).getAllByText("Not counted: entered in another unit.")).toHaveLength(1);
  });

  it("lets an admin add a program under Other programs", async () => {
    const user = userEvent.setup();
    replies["POST /api/rewards/programs"] = (body) => json(201, appProgram("app-harbor-hotels", (body as { name: string }).name));
    const { queryClient } = renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    const card = (await screen.findByRole("heading", { name: "Other programs" })).closest("section")!;
    await user.click(within(card).getByRole("button", { name: "Add a program" }));
    const drawer = screen.getByRole("dialog", { name: "Add a program" });
    await user.type(within(drawer).getByLabelText("Name"), "Harbor Hotels");
    await user.selectOptions(within(drawer).getByLabelText("Kind"), "miles");
    const more = within(drawer).getByRole("button", { name: "More" });
    expect(more.getAttribute("aria-expanded")).toBe("false");
    await user.click(more);
    await user.selectOptions(within(drawer).getByLabelText("Group"), "primary");
    await user.type(within(drawer).getByLabelText("Low"), "0.8");
    await user.click(within(drawer).getByRole("button", { name: "Add program" }));
    expect(within(drawer).getByText(/^Enter both ends of the value range/)).toBeTruthy();
    expect(calls("POST", "/api/rewards/programs")).toHaveLength(0);
    await user.type(within(drawer).getByLabelText("High"), "1.1");
    await user.click(within(drawer).getByRole("button", { name: "Add program" }));
    expect(await within(card).findByText("Added Harbor Hotels.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(sentBody("POST", "/api/rewards/programs")).toEqual({
      name: "Harbor Hotels",
      kind: "miles",
      group: "primary",
      cpp_low: "0.8",
      cpp_high: "1.1",
    });
    expect(invalidated(spy)).toEqual(["rewards", "dashboard", "checks", "trips"]);
  });

  it("shows members who can add programs and fix notices, with no admin controls", async () => {
    role = "member";
    payload = structuredClone(REWARDS);
    payload.programs.push(appProgram("app-harbor-miles", "Harbor Miles"));
    payload.notices = [
      { kind: "program_duplicate", text: "Two programs have the same name.", key: "app-harbor-miles", other_key: "harbor-miles", fields: [] },
    ];
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    expect(await screen.findByText("Only an admin can add programs.")).toBeTruthy();
    expect(screen.getByText("Only an admin can change programs.")).toBeTruthy();
    expect(screen.getByText("Two programs named Harbor Miles.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Add a program" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Use the chat's/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Keep both/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Update balances" })).toBeTruthy();
    expect(screen.queryByText("Only an admin can change benefits.")).toBeNull();
  });

  it("keeps the admin hints off the page for an admin", async () => {
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    expect(await screen.findByRole("button", { name: "Add a program" })).toBeTruthy();
    expect(screen.queryByText(/Only an admin/)).toBeNull();
    expect(screen.queryByRole("region", { name: "Needs a look" })).toBeNull();
    expect(screen.queryByText("Needs a look")).toBeNull();
  });

  it("offers the fixes for duplicates, app keys the chat now sends and dropped programs", async () => {
    const user = userEvent.setup();
    payload = structuredClone(REWARDS);
    payload.programs.push(appProgram("app-harbor-miles", "Harbor Miles"), appProgram("app-river-rewards", "River Rewards"));
    payload.notices = [
      { kind: "program_app_key", text: "The chat now sends this program too. Yours is shown.", key: "app-river-rewards", other_key: null, fields: [] },
      { kind: "program_duplicate", text: "Two programs have the same name.", key: "app-harbor-miles", other_key: "harbor-miles", fields: [] },
    ];
    payload.orphans = [
      { kind: "program", key: "old-miles", holds: ["balances"], reason: "dropped", unit: "whole" },
      { kind: "benefit", key: "old-credit", holds: ["edits"], reason: "dropped", unit: null }, // the Benefits tab's, not shown here
    ];
    replies["POST /api/rewards/programs/app-harbor-miles/move"] = () => json(200, program("harbor-miles"));
    replies["POST /api/rewards/programs/app-harbor-miles/keep-both"] = () => json(200, program("harbor-miles"));
    replies["DELETE /api/rewards/programs/app-river-rewards"] = () => new Response(null, { status: 204 });
    replies["POST /api/rewards/programs/old-miles/move"] = () => json(200, program("summit-points"));
    replies["DELETE /api/rewards/programs/old-miles?unit=whole"] = () => new Response(null, { status: 204 });
    const { queryClient } = renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    const list = await screen.findByRole("list", { name: "Needs a look" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(3);
    expect(within(list).getByText("The chat now sends River Rewards too. Yours is shown.")).toBeTruthy();
    expect(within(list).getByText("Use the chat's keeps your balances.")).toBeTruthy();
    expect(within(list).getByText("Two programs named Harbor Miles.")).toBeTruthy();
    expect(within(list).getByText("The chat no longer sends old-miles.")).toBeTruthy();
    expect(within(list).getByText("Its balances entered as points or miles are kept, not shown.")).toBeTruthy();
    expect(within(list).queryByText(/old-credit/)).toBeNull();

    expect(within(list).getByText(/^One is yours\. The other is the chat's Harbor Miles · Sam\./)).toBeTruthy();
    await user.click(within(list).getByRole("button", { name: "Use the chat's Harbor Miles · Sam" }));
    await waitFor(() => expect(calls("POST", "/api/rewards/programs/app-harbor-miles/move")).toHaveLength(1));
    expect(sentBody("POST", "/api/rewards/programs/app-harbor-miles/move")).toEqual({ to_key: "harbor-miles" });
    await user.click(within(list).getByRole("button", { name: "Keep both (yours and the chat's Harbor Miles · Sam)" }));
    await waitFor(() => expect(calls("POST", "/api/rewards/programs/app-harbor-miles/keep-both")).toHaveLength(1));
    expect(sentBody("POST", "/api/rewards/programs/app-harbor-miles/keep-both")).toEqual({ other_key: "harbor-miles" });
    await user.click(within(list).getByRole("button", { name: "Use the chat's for River Rewards" }));
    await waitFor(() => expect(calls("DELETE", "/api/rewards/programs/app-river-rewards")).toHaveLength(1));

    const moveTo = within(list).getByRole("combobox", { name: "Move to (the old-miles balances entered as points or miles)" });
    // Shown programs in the same unit (miles go to points or miles): no cash back, no timeshare.
    expect(within(moveTo).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Harbor Miles · Sam",
      "Summit Points",
      "Harbor Miles (added in the app)",
      "River Rewards (added in the app)",
    ]);
    await user.selectOptions(moveTo, "summit-points");
    await user.click(within(list).getByRole("button", { name: "Move the old-miles balances entered as points or miles" }));
    await waitFor(() => expect(calls("POST", "/api/rewards/programs/old-miles/move")).toHaveLength(1));
    expect(sentBody("POST", "/api/rewards/programs/old-miles/move")).toEqual({ to_key: "summit-points" });

    await user.click(within(list).getByRole("button", { name: "Delete balances for old-miles entered as points or miles" }));
    const question = "Delete the old-miles balances entered as points or miles?";
    expect(within(list).getByText(question)).toBeTruthy();
    expect(calls("DELETE", "/api/rewards/programs/old-miles?unit=whole")).toHaveLength(0);
    const confirmDelete = within(list).getByRole("button", { name: "Delete balances" });
    await waitFor(() => expect(document.activeElement).toBe(confirmDelete));
    expect(described(confirmDelete)).toBe(question);
    await user.click(confirmDelete);
    // Only the balances in the unit the page listed go.
    await waitFor(() => expect(calls("DELETE", "/api/rewards/programs/old-miles?unit=whole")).toHaveLength(1));
    expect(invalidated(spy).filter((k) => k === "rewards")).toHaveLength(5);
  });

  it("shows a refused fix in words next to the notice", async () => {
    const user = userEvent.setup();
    payload = structuredClone(REWARDS);
    payload.orphans = [{ kind: "program", key: "old-miles", holds: ["balances"], reason: "dropped", unit: "whole" }];
    replies["POST /api/rewards/programs/old-miles/move"] = () =>
      json(409, { detail: "Both programs have a balance for the same day. Delete one first." });
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const list = await screen.findByRole("list", { name: "Needs a look" });
    await user.click(within(list).getByRole("button", { name: "Move the old-miles balances entered as points or miles" }));
    expect(await within(list).findByText("Both programs have a balance for the same day. Delete one first.")).toBeTruthy();
  });

  it("tells two chat programs with one name apart", async () => {
    const user = userEvent.setup();
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    payload = structuredClone(REWARDS);
    payload.programs.push(
      { ...program("harbor-miles"), key: "harbor-miles-alex", owner_key: "alex", owner_name: "Alex" },
      appProgram("app-harbor-miles", "Harbor Miles"),
    );
    payload.notices = ["harbor-miles", "harbor-miles-alex"].map((other) => ({
      kind: "program_duplicate" as const,
      text: "Two programs have the same name.",
      key: "app-harbor-miles",
      other_key: other,
      fields: [],
    }));
    replies["POST /api/rewards/programs/app-harbor-miles/move"] = () => json(200, program("harbor-miles"));
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const list = await screen.findByRole("list", { name: "Needs a look" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(within(list).getByText(/The other is the chat's Harbor Miles · Sam\./)).toBeTruthy();
    expect(within(list).getByText(/The other is the chat's Harbor Miles · Alex\./)).toBeTruthy();
    expect(within(list).getByRole("button", { name: "Keep both (yours and the chat's Harbor Miles · Sam)" })).toBeTruthy();
    await user.click(within(list).getByRole("button", { name: "Use the chat's Harbor Miles · Alex" }));
    await waitFor(() => expect(calls("POST", "/api/rewards/programs/app-harbor-miles/move")).toHaveLength(1));
    expect(sentBody("POST", "/api/rewards/programs/app-harbor-miles/move")).toEqual({ to_key: "harbor-miles-alex" });
    expect(errors.mock.calls.flat().join(" ")).not.toMatch(/same key/);
    errors.mockRestore();
  });

  it("names a program that is now a timeshare, and moves balances only within their unit", async () => {
    const user = userEvent.setup();
    payload = structuredClone(REWARDS);
    payload.orphans = [
      { kind: "program", key: "shared-vacation-club", holds: ["balances"], reason: "timeshare", unit: "whole" },
      { kind: "program", key: "old-cash", holds: ["balances"], reason: "dropped", unit: "cash" },
      // A dropped key with entries in both units is listed once per unit.
      { kind: "program", key: "old-mixed", holds: ["balances"], reason: "dropped", unit: "cash" },
      { kind: "program", key: "old-mixed", holds: ["balances"], reason: "dropped", unit: "whole" },
    ];
    replies["DELETE /api/rewards/programs/old-mixed?unit=cash"] = () => new Response(null, { status: 204 });
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const list = await screen.findByRole("list", { name: "Needs a look" });
    const [club, cash, mixedCash, mixedWhole] = within(list).getAllByRole("listitem");
    expect(within(club!).getByText("Shared Vacation Club now uses yearly points.")).toBeTruthy();
    expect(within(club!).getByText("Its balances entered as points or miles are kept, not shown.")).toBeTruthy();
    expect(within(list).queryByText(/no longer sends shared-vacation-club/)).toBeNull();
    const clubTo = within(club!).getByRole("combobox", { name: "Move to (the Shared Vacation Club balances entered as points or miles)" });
    expect(within(clubTo).getAllByRole("option").map((o) => o.textContent)).toEqual(["Harbor Miles · Sam", "Summit Points"]);
    expect(within(club!).getByRole("button", { name: "Delete balances for Shared Vacation Club entered as points or miles" })).toBeTruthy();
    expect(within(cash!).getByText("The chat no longer sends old-cash.")).toBeTruthy();
    const cashTo = within(cash!).getByRole("combobox", { name: "Move to (the old-cash balances entered as cash back)" });
    expect(within(cashTo).getAllByRole("option").map((o) => o.textContent)).toEqual(["Pine Cash Back · Alex"]);
    // Each unit of a dropped key moves to programs in that unit.
    const options = (li: HTMLElement) => within(within(li).getByRole("combobox")).getAllByRole("option").map((o) => o.textContent);
    expect(options(mixedCash!)).toEqual(["Pine Cash Back · Alex"]);
    expect(options(mixedWhole!)).toEqual(["Harbor Miles · Sam", "Summit Points"]);
    expect(within(list).getAllByText("The chat no longer sends old-mixed.")).toHaveLength(2);
    // The two items of one key say, and name, which unit each one is.
    expect(within(mixedCash!).getByText("Its balances entered as cash back are kept, not shown.")).toBeTruthy();
    expect(within(mixedWhole!).getByText("Its balances entered as points or miles are kept, not shown.")).toBeTruthy();
    for (const [li, was] of [
      [mixedCash!, "cash back"],
      [mixedWhole!, "points or miles"],
    ] as const) {
      expect(within(li).getByRole("combobox", { name: `Move to (the old-mixed balances entered as ${was})` })).toBeTruthy();
      expect(within(li).getByRole("button", { name: `Move the old-mixed balances entered as ${was}` })).toBeTruthy();
      expect(within(li).getByRole("button", { name: `Delete balances for old-mixed entered as ${was}` })).toBeTruthy();
    }
    // Delete on the cash back item asks about, and sends, cash back only.
    await user.click(within(mixedCash!).getByRole("button", { name: "Delete balances for old-mixed entered as cash back" }));
    expect(within(mixedCash!).getByText("Delete the old-mixed balances entered as cash back?")).toBeTruthy();
    await user.click(within(mixedCash!).getByRole("button", { name: "Delete balances" }));
    await waitFor(() => expect(calls("DELETE", "/api/rewards/programs/old-mixed?unit=cash")).toHaveLength(1));
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "DELETE")).toHaveLength(1);
    expect(errors.mock.calls.flat().join(" ")).not.toMatch(/same key/);
    errors.mockRestore();
  });

  it("says when the chat now counts a program in the other unit, both ways", async () => {
    payload = structuredClone(REWARDS);
    payload.orphans = [
      { kind: "program", key: "pine-cash-back", holds: ["balances"], reason: "unit", unit: "whole" },
      { kind: "program", key: "harbor-miles", holds: ["balances"], reason: "unit", unit: "cash" },
      { kind: "program", key: "summit-points", holds: ["balances"], reason: "unit", unit: "cash" },
    ];
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const list = await screen.findByRole("list", { name: "Needs a look" });
    const [pine, harbor, summit] = within(list).getAllByRole("listitem");
    expect(
      within(pine!).getByText("Pine Cash Back: your earlier balances were entered as points or miles. The chat now counts it in cash back."),
    ).toBeTruthy();
    expect(within(pine!).getByText("Those balances are kept, not shown.")).toBeTruthy();
    expect(within(harbor!).getByText("Harbor Miles: your earlier balances were entered as cash back. The chat now counts it in miles.")).toBeTruthy();
    expect(within(summit!).getByText("Summit Points: your earlier balances were entered as cash back. The chat now counts it in points.")).toBeTruthy();
    expect(within(list).queryByText(/no longer sends/)).toBeNull();
    expect(within(list).queryByText(/yearly points/)).toBeNull();
    // Move to lists only programs in the entries' unit, never the program itself.
    const pineTo = within(pine!).getByRole("combobox", { name: "Move to (the Pine Cash Back balances entered as points or miles)" });
    expect(within(pineTo).getAllByRole("option").map((o) => o.textContent)).toEqual(["Harbor Miles · Sam", "Summit Points"]);
    const harborTo = within(harbor!).getByRole("combobox", { name: "Move to (the Harbor Miles balances entered as cash back)" });
    expect(within(harborTo).getAllByRole("option").map((o) => o.textContent)).toEqual(["Pine Cash Back · Alex"]);
    expect(within(harbor!).getByRole("button", { name: "Move the Harbor Miles balances entered as cash back" })).toBeTruthy();
  });

  it("moves and deletes a unit orphan's balances, with the confirm taking focus", async () => {
    const user = userEvent.setup();
    payload = structuredClone(REWARDS);
    payload.programs.push(appProgram("app-river-rewards", "River Rewards"));
    payload.orphans = [{ kind: "program", key: "pine-cash-back", holds: ["balances"], reason: "unit", unit: "whole" }];
    replies["POST /api/rewards/programs/pine-cash-back/move"] = () => json(200, program("summit-points"));
    replies["DELETE /api/rewards/programs/pine-cash-back?unit=whole"] = () => new Response(null, { status: 204 });
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const list = await screen.findByRole("list", { name: "Needs a look" });
    const moveTo = within(list).getByRole("combobox", { name: "Move to (the Pine Cash Back balances entered as points or miles)" });
    expect(within(moveTo).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Harbor Miles · Sam",
      "Summit Points",
      "River Rewards (added in the app)",
    ]);
    await user.selectOptions(moveTo, "app-river-rewards");
    await user.click(within(list).getByRole("button", { name: "Move the Pine Cash Back balances entered as points or miles" }));
    await waitFor(() => expect(calls("POST", "/api/rewards/programs/pine-cash-back/move")).toHaveLength(1));
    expect(sentBody("POST", "/api/rewards/programs/pine-cash-back/move")).toEqual({ to_key: "app-river-rewards" });

    const ask = within(list).getByRole("button", { name: "Delete balances for Pine Cash Back entered as points or miles" });
    await user.click(ask);
    const question = "Delete the Pine Cash Back balances entered as points or miles?";
    expect(within(list).getByText(question)).toBeTruthy();
    const confirmDelete = within(list).getByRole("button", { name: "Delete balances" });
    await waitFor(() => expect(document.activeElement).toBe(confirmDelete));
    expect(described(confirmDelete)).toBe(question);
    // Cancel puts focus back on the first button and sends nothing.
    await user.click(within(list).getByRole("button", { name: "Cancel" }));
    const again = within(list).getByRole("button", { name: "Delete balances for Pine Cash Back entered as points or miles" });
    await waitFor(() => expect(document.activeElement).toBe(again));
    expect(calls("DELETE", "/api/rewards/programs/pine-cash-back?unit=whole")).toHaveLength(0);
    await user.click(again);
    await user.click(within(list).getByRole("button", { name: "Delete balances" }));
    await waitFor(() => expect(calls("DELETE", "/api/rewards/programs/pine-cash-back?unit=whole")).toHaveLength(1));
  });

  it("says when no program takes a unit orphan's balances, and moves them once one shows up", async () => {
    const user = userEvent.setup();
    // The chat now counts Pine Cash Back, the only cash back program, in points.
    const pinePoints = (): RewardsResponse => {
      const p = structuredClone(REWARDS);
      p.programs = p.programs.map((x) => (x.key === "pine-cash-back" ? { ...x, kind: "points" } : x));
      p.orphans = [{ kind: "program", key: "pine-cash-back", holds: ["balances"], reason: "unit", unit: "cash" }];
      return p;
    };
    payload = pinePoints();
    replies["POST /api/rewards/programs/pine-cash-back/move"] = () => json(200, program("summit-points"));
    const { queryClient } = renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const list = await screen.findByRole("list", { name: "Needs a look" });
    expect(within(list).getByText("No cash back program to move them to. Add one under Other programs.")).toBeTruthy();
    expect(within(list).queryByRole("combobox")).toBeNull();
    expect(within(list).queryByRole("button", { name: /^Move/ })).toBeNull();
    expect(within(list).getByRole("button", { name: "Delete balances for Pine Cash Back entered as cash back" })).toBeTruthy();

    // A cash back program added later is picked with no change event.
    payload = pinePoints();
    payload.programs.push(appProgram("app-river-cash", "River Cash", { kind: "cash_back" }), appProgram("app-lake-cash", "Lake Cash", { kind: "cash_back" }));
    await queryClient.invalidateQueries({ queryKey: ["rewards"] });
    const moveTo = await within(list).findByRole("combobox", { name: "Move to (the Pine Cash Back balances entered as cash back)" });
    expect(within(moveTo).getAllByRole("option").map((o) => o.textContent)).toEqual(["River Cash (added in the app)", "Lake Cash (added in the app)"]);
    expect((moveTo as HTMLSelectElement).value).toBe("app-river-cash");
    expect(within(list).queryByText(/No cash back program/)).toBeNull();
    const moveButton = within(list).getByRole("button", { name: "Move the Pine Cash Back balances entered as cash back" });
    expect((moveButton as HTMLButtonElement).disabled).toBe(false);

    // A picked program that is gone is never sent: the first one left is.
    await user.selectOptions(moveTo, "app-lake-cash");
    payload = pinePoints();
    payload.programs.push(appProgram("app-river-cash", "River Cash", { kind: "cash_back" }));
    await queryClient.invalidateQueries({ queryKey: ["rewards"] });
    await waitFor(() => expect(within(moveTo).getAllByRole("option")).toHaveLength(1));
    expect((moveTo as HTMLSelectElement).value).toBe("app-river-cash");
    await user.click(within(list).getByRole("button", { name: "Move the Pine Cash Back balances entered as cash back" }));
    await waitFor(() => expect(calls("POST", "/api/rewards/programs/pine-cash-back/move")).toHaveLength(1));
    expect(sentBody("POST", "/api/rewards/programs/pine-cash-back/move")).toEqual({ to_key: "app-river-cash" });
  });

  it("says when the chat counts an app program's key in another unit", async () => {
    payload = structuredClone(REWARDS);
    payload.programs.push(appProgram("app-lantern-cash", "Lantern Cash", { kind: "cash_back" }));
    payload.notices = [
      { kind: "program_app_key", text: "The chat now sends this program too. Yours is shown.", key: "app-lantern-cash", other_key: null, fields: ["unit"] },
    ];
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const list = await screen.findByRole("list", { name: "Needs a look" });
    expect(within(list).getByText("The chat now sends Lantern Cash too. Yours is shown.")).toBeTruthy();
    expect(
      within(list).getByText("The chat counts it in another unit. Use the chat's keeps your cash back balances, but does not show them."),
    ).toBeTruthy();
    expect(within(list).queryByText("Use the chat's keeps your balances.")).toBeNull();
    expect(within(list).getByRole("button", { name: "Use the chat's for Lantern Cash" })).toBeTruthy();
  });

  it("lets an admin delete a program added in the app from its history", async () => {
    const user = userEvent.setup();
    payload = structuredClone(REWARDS);
    payload.programs.push(appProgram("app-river-rewards", "River Rewards"));
    replies["GET /api/rewards/programs/app-river-rewards/balances"] = () =>
      json(200, { program_key: "app-river-rewards", program: program("app-river-rewards", payload as RewardsResponse), entries: [], chat: null });
    replies["DELETE /api/rewards/programs/app-river-rewards"] = () => new Response(null, { status: 204 });
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    await user.click(await screen.findByRole("button", { name: "Balance history for River Rewards" }));
    const drawer = screen.getByRole("dialog", { name: "River Rewards balance history" });
    expect(await within(drawer).findByText("No balances yet.")).toBeTruthy();
    await user.click(within(drawer).getByRole("button", { name: "Delete this program" }));
    expect(within(drawer).getByText("Delete River Rewards and the balances entered for it?")).toBeTruthy();
    await user.click(within(drawer).getByRole("button", { name: "Delete program" }));
    await waitFor(() => expect(calls("DELETE", "/api/rewards/programs/app-river-rewards")).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("keeps the empty state and no input controls before the first 1.3 run", async () => {
    payload = NO_REWARDS;
    renderWithProviders(<RewardsPage />, { route: "/rewards?update=balances" });
    expect(await screen.findByText("No rewards data yet.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Update balances" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add a program" })).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByText(/Your app edits/)).toBeNull();
  });

  it("keeps input controls and hints off the page when it fails to load", async () => {
    payload = "error";
    role = "member";
    renderWithProviders(<RewardsPage />, { route: "/rewards?update=balances" });
    expect((await screen.findByRole("alert")).textContent).toContain("Could not load rewards.");
    await waitFor(() => expect(calls("GET", "/api/auth/me")).toHaveLength(1));
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Update balances" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add a program" })).toBeNull();
    expect(screen.queryByText(/Only an admin/)).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("says when the app's edits could not be shown", async () => {
    payload = structuredClone(REWARDS);
    payload.notices = [{ kind: "overlay_failed", text: "Your edits could not be shown. Nothing was lost.", key: null, other_key: null, fields: [] }];
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    const note = await screen.findByText("Your edits could not be shown. Nothing was lost.");
    expect(note.getAttribute("role")).toBe("status");
    expect(screen.queryByRole("list", { name: "Needs a look" })).toBeNull();
  });

  it("says the app's edits are on top, and Recurring keeps its read-only line", async () => {
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    expect(await screen.findByText("From the Oct 12, 2026 update. Your app edits are on top.")).toBeTruthy();
    expect(screen.queryByText(/Read only/)).toBeNull();
    const { container } = renderWithProviders(<SourceLine runId="2026-10-12" />);
    expect(container.textContent).toBe("From the Oct 12, 2026 update. Read only: the statement chat keeps this list.");
  });
});
