// Rewards inputs (docs/plans/rewards-inputs.md chunk C): the Benefits tab's
// edit drawer, Add a benefit, Needs a look, the Hidden group and who sees
// which control. Made-up numbers only.
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AccountsResponse, RewardBenefit, RewardsResponse } from "../../lib/api";
import { renderWithProviders } from "../../test/render";
import { type AddForm, benefitBody, cardOptions } from "./AddBenefit";
import { benefitSet, followForm, formFields, formOf, keepTyped, openForm, takeNewer, valueText } from "./BenefitDrawer";
import { holdsText } from "./BenefitNotices";
import { NO_REWARDS, REWARDS } from "./fixtures";
import { amountStartDefault, benefitLabel, MONEY_RULE, moneyText, parseMoney, withBenefit } from "./model";
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

const BENEFITS_TAB = "/rewards?tab=benefits";

function benefit(key: string, from: RewardsResponse = REWARDS): RewardBenefit {
  const b = from.benefits.find((x) => x.key === key);
  if (!b) throw new Error(key);
  return b;
}

/** A page copy the test can change. */
function page(): RewardsResponse {
  return structuredClone(REWARDS);
}

/** A benefit added in the app. */
function appBenefit(key: string, name: string, over: Partial<RewardBenefit> = {}): RewardBenefit {
  return { ...benefit("dining-credit"), key, name, origin: "app", edited_fields: [], past_cycles: [], ...over };
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

const DINING = "/api/rewards/benefits/dining-credit";

/** The text an element's aria-describedby points to. */
function described(el: HTMLElement): string | null {
  const id = el.getAttribute("aria-describedby");
  return id ? (document.getElementById(id)?.textContent ?? null) : null;
}

/** A saved benefit: the reply, and what the refetch then sees. */
function serve(b: RewardBenefit): Response {
  const next = page();
  next.benefits = next.benefits.map((x) => (x.key === b.key ? b : x));
  payload = next;
  return json(200, b);
}

/** Open the edit drawer for a benefit by its name. */
async function openDrawer(user: ReturnType<typeof userEvent.setup>, name: string): Promise<HTMLElement> {
  await user.click(await screen.findByRole("button", { name: `Edit ${name}` }));
  return screen.getByRole("dialog", { name: `Edit ${name}` });
}

// Only the fields cardOptions reads.
const ACCOUNTS = {
  groups: [
    {
      accounts: [
        { account_key: "summit-card", display_name: "Summit card", type: "credit_card", active: true },
        { account_key: "orchard-card", display_name: "Orchard card", type: "store_card", active: true },
        { account_key: "closed-card", display_name: "Closed card", type: "credit_card", active: false },
      ],
    },
    { accounts: [{ account_key: "joint-checking", display_name: "Joint checking", type: "checking", active: true }] },
  ],
} as unknown as AccountsResponse;

const EMPTY_ADD: AddForm = {
  card: "summit-card",
  name: "Gym credit",
  kind: "credit",
  cadence: "monthly",
  amount: "",
  program: "",
  useBy: "",
  cycles: [{ start: "", end: "", amount: "" }],
};

describe("benefit input helpers", () => {
  it("reads money and words labels and kept inputs", () => {
    expect(parseMoney("12")).toBe("12.00");
    expect(parseMoney("$1,250.5")).toBe("1250.50");
    expect(parseMoney("0")).toBe("0.00");
    expect(parseMoney("")).toBeNull();
    expect(parseMoney("12.345")).toBeUndefined();
    expect(parseMoney("-3")).toBeUndefined();
    const top = String(9_999_999); // the most the API takes
    expect(parseMoney(`${top}.99`)).toBe(`${top}.99`);
    expect(parseMoney("10,000,000")).toBeUndefined(); // 8 digits: over the API's limit
    expect(moneyText(1_250)).toBe("12.50");
    expect(moneyText(5)).toBe("0.05");
    expect(benefitLabel("Dining credit", "Summit card")).toBe("Dining credit · Summit card");
    expect(benefitLabel("Dining credit", null)).toBe("Dining credit");
    expect(holdsText(["edits"])).toBe("edits");
    expect(holdsText(["edits", "terms"])).toBe("edits and match words");
    expect(holdsText(["edits", "terms", "marks"])).toBe("edits, match words and cycle marks");
  });

  it("starts a new amount at this cycle's start, else today", () => {
    expect(amountStartDefault(benefit("dining-credit"), "2026-10-20")).toBe("2026-10-01");
    expect(amountStartDefault(benefit("anniversary-night"), "2026-10-20")).toBe("2026-10-20"); // no cycles
    const upcoming = { cycle: { ...benefit("dining-credit").cycle!, start: "2026-11-01", end: "2026-11-30" }, amount_from: null };
    expect(amountStartDefault(upcoming, "2026-10-20")).toBe("2026-10-20");
    // A second change starts where the first one did: the API keeps one start.
    expect(amountStartDefault({ ...benefit("dining-credit"), amount_from: "2026-04-01" }, "2026-10-20")).toBe("2026-04-01");
  });

  it("sends only the fields that differ, with null to clear one", () => {
    const b = benefit("travel-credit");
    const form = formOf(b);
    expect(form).toMatchObject({ name: "Yearly travel credit", amount: "100.00", enrolled: "no", enroll_by: "2026-10-31", auto: "" });
    expect(benefitSet(b.kind, form, form)).toEqual({ set: {}, problems: {} });
    // "100" is the same amount as "100.00". Spaces around text do not count.
    expect(benefitSet(b.kind, form, { ...form, amount: "100", name: " Yearly travel credit " }).set).toEqual({});
    expect(benefitSet(b.kind, form, { ...form, amount: "$120", enrolled: "yes", how_to_use: "", note: " Book through the portal " }).set).toEqual({
      amount: "120.00",
      enrolled: true,
      how_to_use: null,
      note: "Book through the portal",
    });
    expect(benefitSet(b.kind, form, { ...form, enroll_by: "", auto: "no" }).set).toEqual({ enroll_by: null, auto: false });
    expect(benefitSet(b.kind, form, { ...form, name: " ", amount: "", counted_value: "lots" }).problems).toEqual({
      name: "Enter a name.",
      amount: "Enter the amount.",
      counted_value: MONEY_RULE,
    });
  });

  it("shows only the fields that fit the kind", () => {
    expect(formFields("credit").map((f) => f.key)).toEqual([
      "name",
      "amount",
      "counted_value",
      "enroll_by",
      "enrolled",
      "auto",
      "rolls_over",
      "how_to_use",
      "note",
    ]);
    expect(formFields("free_night").map((f) => f.key)).toEqual(["name", "counted_value", "enroll_by", "enrolled", "how_to_use", "note"]);
    // A free night has no amount, so nothing about it is sent.
    const night = benefit("anniversary-night");
    expect(benefitSet(night.kind, formOf(night), { ...formOf(night), amount: "5.00" }).set).toEqual({});
  });

  it("follows a refetch on fields not typed in, and holds a typed one the refetch changed", () => {
    const b = benefit("dining-credit");
    const opened = openForm(b);
    expect(followForm(opened, formOf(b))).toBe(opened); // nothing changed: the same state back
    // Codex's case: the amount goes from 10 to 20 while only the note is typed.
    const twenty = formOf({ ...b, amount_cents: 2_000 });
    const noted = followForm({ ...opened, form: { ...opened.form, note: "Weekends only" } }, twenty);
    expect(noted.form).toMatchObject({ amount: "20.00", note: "Weekends only" });
    expect(noted.newer).toEqual({});
    expect(benefitSet(b.kind, noted.base, noted.form).set).toEqual({ note: "Weekends only" });
    expect(followForm(noted, twenty)).toBe(noted);
    // A typed amount the refetch changed keeps what is typed and waits for a pick.
    const held = followForm({ ...opened, form: { ...opened.form, amount: "15" } }, twenty);
    expect(held.form.amount).toBe("15");
    expect(held.newer).toEqual({ amount: "20.00" });
    expect(followForm(held, twenty)).toBe(held);
    const kept = keepTyped(held, "amount");
    expect(kept.newer).toEqual({});
    expect(benefitSet(b.kind, kept.base, kept.form).set).toEqual({ amount: "15.00" });
    const taken = takeNewer(held, "amount");
    expect(taken.form.amount).toBe("20.00");
    expect(taken.newer).toEqual({});
    expect(benefitSet(b.kind, taken.base, taken.form).set).toEqual({});
    // No pick when the refetch brings what was typed, or goes back to where the field started.
    const same = followForm({ ...opened, form: { ...opened.form, amount: "20" } }, twenty);
    expect(same.newer).toEqual({});
    expect(benefitSet(b.kind, same.base, same.form).set).toEqual({});
    const back = followForm(held, formOf(b));
    expect(back.newer).toEqual({});
    expect(benefitSet(b.kind, back.base, back.form).set).toEqual({ amount: "15.00" });
    // A typed field the refetch left alone stays typed.
    const both = followForm({ ...opened, form: { ...opened.form, amount: "15", note: "Weekends only" } }, formOf({ ...b, note: "Weekends only" }));
    expect(both.newer).toEqual({});
    expect(benefitSet(b.kind, both.base, both.form).set).toEqual({ amount: "15.00" });
    // The new value in the line under the field.
    expect(valueText("money", "1250.00")).toBe("$1,250.00");
    expect(valueText("date", "2026-10-31")).toBe("Oct 31, 2026");
    expect(valueText("yesno", "yes")).toBe("yes");
    expect(valueText("yesno", "")).toBe("not known");
    expect(valueText("text", "")).toBe("empty");
    expect(valueText("text", "Dining perk")).toBe('"Dining perk"');
  });

  it("moves a hidden benefit to Hidden and back to its place", () => {
    const dining = benefit("dining-credit");
    const hidden = withBenefit(REWARDS, { ...dining, hidden: true });
    expect(hidden.benefits.map((b) => b.key)).not.toContain("dining-credit");
    expect(hidden.hidden).toEqual([
      { key: "dining-credit", name: "Dining credit", kind: "credit", account_key: "summit-card", account_name: "Summit card", origin: "chat" },
    ]);
    const twice = withBenefit({ ...hidden, hidden: [{ ...hidden.hidden[0]!, key: "zoo-credit" }, ...hidden.hidden] }, { ...dining, hidden: true });
    expect(twice.hidden.map((h) => h.key)).toEqual(["dining-credit", "zoo-credit"]); // by key, no twin
    const back = withBenefit(hidden, dining);
    expect(back.hidden).toEqual([]);
    // After the last benefit in its group, so the group shows once, until the refetch sorts it.
    expect(back.benefits.map((b) => b.key)).toEqual(["travel-credit", "anniversary-night", "dining-credit", "wireless-credit", "streaming-credit"]);
    const alone = withBenefit({ ...hidden, benefits: [] }, dining);
    expect(alone.benefits.map((b) => b.key)).toEqual(["dining-credit"]);
    const renamed = withBenefit(REWARDS, { ...dining, name: "Dining" });
    expect(renamed.benefits.map((b) => b.key)).toEqual(REWARDS.benefits.map((b) => b.key)); // keeps its place
    expect(renamed.benefits[0]!.name).toBe("Dining");
    // An earning cap shown again has its own card, not the benefit list.
    const cap = withBenefit(REWARDS, { ...dining, key: "fuel-bonus", kind: "earning_cap" });
    expect(cap.benefits).toHaveLength(REWARDS.benefits.length);
    expect(cap.benefits.map((b) => b.key)).not.toContain("fuel-bonus");
    // A hidden cap or perk leaves its card at once.
    const hiddenCap = withBenefit(REWARDS, { ...dining, key: "fuel-bonus", name: "Fuel bonus", kind: "earning_cap", hidden: true });
    expect(hiddenCap.caps).toEqual([]);
    expect(hiddenCap.hidden.map((h) => h.key)).toEqual(["fuel-bonus"]);
    const hiddenPerk = withBenefit(REWARDS, { ...dining, key: "hotel-status", kind: "status_perk", hidden: true });
    expect(hiddenPerk.perks).toEqual([]);
    expect(hiddenPerk.caps).toEqual(REWARDS.caps);
  });

  it("checks Add a benefit before it sends", () => {
    expect(benefitBody({ ...EMPTY_ADD, card: "" })).toBe("Pick a card.");
    expect(benefitBody({ ...EMPTY_ADD, name: "  " })).toBe("Enter a name.");
    expect(benefitBody(EMPTY_ADD)).toBe("Enter the amount.");
    expect(benefitBody({ ...EMPTY_ADD, amount: "12.345" })).toBe(MONEY_RULE);
    expect(benefitBody({ ...EMPTY_ADD, name: " Gym credit ", amount: "$12" })).toEqual({
      account_key: "summit-card",
      name: "Gym credit",
      kind: "credit",
      cadence: "monthly",
      amount: "12.00",
    });
    const stored = { ...EMPTY_ADD, cadence: "multi_year" as const, amount: "100" };
    expect(benefitBody(stored)).toBe("Enter a start and an end for each cycle.");
    expect(benefitBody({ ...stored, cycles: [{ start: "2027-01-01", end: "2026-12-31", amount: "" }] })).toBe("Each cycle ends on or after its start.");
    expect(benefitBody({ ...stored, cycles: [] })).toBe("Add at least one cycle.");
    expect(
      benefitBody({
        ...stored,
        cycles: [
          { start: "2026-01-01", end: "2029-12-31", amount: "" },
          { start: "2026-01-01", end: "2030-12-31", amount: "" },
        ],
      }),
    ).toBe("Each cycle needs its own start date.");
    expect(benefitBody({ ...stored, cycles: [{ start: "2026-01-01", end: "2029-12-31", amount: "x" }] })).toBe(MONEY_RULE);
    expect(
      benefitBody({
        ...stored,
        cycles: [
          { start: "2026-01-01", end: "2029-12-31", amount: "100.00" },
          { start: "2030-01-01", end: "2033-12-31", amount: "120" },
        ],
      }),
    ).toEqual({
      account_key: "summit-card",
      name: "Gym credit",
      kind: "credit",
      cadence: "multi_year",
      amount: "100.00",
      // A cycle's amount goes only when it differs from the benefit's.
      cycles: [
        { start: "2026-01-01", end: "2029-12-31" },
        { start: "2030-01-01", end: "2033-12-31", amount: "120.00" },
      ],
    });
    // Calendar cadences send no cycles: the app makes them.
    expect(benefitBody({ ...EMPTY_ADD, cadence: "quarterly", amount: "15" })).not.toHaveProperty("cycles");
    const night = { ...EMPTY_ADD, kind: "free_night" as const, name: "Spa night" };
    expect(benefitBody(night)).toBe("Pick the hotel program.");
    expect(benefitBody({ ...night, program: "summit-points" })).toEqual({
      account_key: "summit-card",
      name: "Spa night",
      kind: "free_night",
      program_key: "summit-points",
    });
    expect(benefitBody({ ...night, program: "summit-points", useBy: "2027-06-30" })).toMatchObject({ ends_on: "2027-06-30" });
    expect(benefitBody({ ...EMPTY_ADD, kind: "other", name: "Lounge visits" })).toEqual({
      account_key: "summit-card",
      name: "Lounge visits",
      kind: "other",
    });
  });

  it("offers open credit and store cards only, by name", () => {
    expect(cardOptions(ACCOUNTS)).toEqual([
      { key: "orchard-card", name: "Orchard card" },
      { key: "summit-card", name: "Summit card" },
    ]);
    expect(cardOptions(undefined)).toEqual([]);
  });
});

describe("Benefits tab inputs", () => {
  it("marks edited and app benefits and gives an admin a pencil on each", async () => {
    payload = page();
    benefit("dining-credit", payload).edited_fields = ["amount"];
    payload.benefits.unshift(appBenefit("app-gym-credit", "Gym credit", { group: "open" }));
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const open = await screen.findByRole("region", { name: "Open this cycle" });
    const dining = within(open).getByText("Dining credit").closest("li")!;
    expect(within(dining).getByText("Edited")).toBeTruthy();
    expect(within(dining).queryByText("Added in the app")).toBeNull();
    const gym = within(open).getByText("Gym credit").closest("li")!;
    expect(within(gym).getByText("Added in the app")).toBeTruthy();
    expect(within(gym).queryByText("Edited")).toBeNull();
    const pencil = within(dining).getByRole("button", { name: "Edit Dining credit" });
    expect(pencil.className).toContain("h-11"); // 44px on phones
    expect(pencil.className).toContain("w-11");
    // One pencil per benefit in the list, the free night too, and on each cap and perk.
    expect(screen.getAllByRole("button", { name: /^Edit / })).toHaveLength(8);
    expect(screen.getByRole("button", { name: "Edit Fuel bonus" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Edit Hotel status" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add a benefit" })).toBeTruthy();
    expect(screen.queryByText(/Only an admin/)).toBeNull();
  });

  it("puts the pencil for an ended benefit under past cycles, so its end can be undone", async () => {
    const user = userEvent.setup();
    payload = page();
    Object.assign(benefit("travel-credit", payload), { group: "ended", ends_on: "2026-09-30", cycle: null, edited_fields: ["ends_on"] });
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    await screen.findByRole("region", { name: "Open this cycle" });
    expect(screen.queryByRole("button", { name: "Edit Yearly travel credit" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Show past cycles" }));
    const past = screen.getByRole("region", { name: "Yearly travel credit past cycles" });
    // Past cycles of a current benefit have no second pencil.
    expect(within(screen.getByRole("region", { name: "Dining credit past cycles" })).queryByRole("button")).toBeNull();
    await user.click(within(past).getByRole("button", { name: "Edit Yearly travel credit" }));
    const drawer = screen.getByRole("dialog", { name: "Edit Yearly travel credit" });
    expect(within(drawer).getByText("Ends Sep 30, 2026.")).toBeTruthy();
    expect(within(drawer).getByRole("button", { name: "Use the chat's for the end date" })).toBeTruthy();
  });

  it("shows members who can change benefits, with no pencils or admin buttons", async () => {
    role = "member";
    payload = page();
    payload.hidden = [{ key: "wireless-credit", name: "Wireless credit", kind: "credit", account_key: "orchard-card", account_name: "Orchard card", origin: "chat" }];
    const user = userEvent.setup();
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    expect(await screen.findByText("Only an admin can change benefits.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Edit / })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add a benefit" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Hidden (1)" }));
    expect(within(screen.getByRole("list", { name: "Hidden benefits" })).getByText("Wireless credit")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Show again/ })).toBeNull();
  });

  it("edits the fields that fit the kind, with 16px inputs, and words the drawer plainly", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    let drawer = await openDrawer(user, "Dining credit");
    expect(within(drawer).getByText("Summit card · Credit, monthly. Your changes sit on top of the chat's.")).toBeTruthy();
    const amount = within(drawer).getByLabelText("Amount each cycle") as HTMLInputElement;
    expect(amount.value).toBe("10.00");
    expect(amount.getAttribute("inputmode")).toBe("decimal");
    expect(amount.className).toContain("text-base");
    expect(amount.className).toContain("h-11");
    expect((within(drawer).getByLabelText("How to use it") as HTMLInputElement).value).toBe("Order from the partner delivery app");
    expect(within(drawer).getByLabelText("Loads by itself")).toBeTruthy();
    expect(within(drawer).queryByRole("button", { name: /Use the chat's/ })).toBeNull(); // nothing edited yet
    expect(drawer.textContent).not.toContain("—");
    await user.click(within(drawer).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    drawer = await openDrawer(user, "Anniversary free night");
    expect(within(drawer).queryByLabelText("Amount each cycle")).toBeNull();
    expect(within(drawer).queryByLabelText("Loads by itself")).toBeNull();
    expect(within(drawer).getByLabelText("Enroll by")).toBeTruthy();
    expect(within(drawer).getByText("The chat says it ends Mar 31, 2027.")).toBeTruthy();
  });

  it("saves a new amount from the day it starts and refreshes what reads benefits", async () => {
    const user = userEvent.setup();
    replies[`PATCH ${DINING}`] = () => serve({ ...benefit("dining-credit"), amount_cents: 1_200, edited_fields: ["amount"] });
    const { queryClient } = renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    const drawer = await openDrawer(user, "Dining credit");
    expect(within(drawer).queryByLabelText("Change starts")).toBeNull();
    const amount = within(drawer).getByLabelText("Amount each cycle");
    await user.clear(amount);
    await user.type(amount, "12");
    // The start of the cycle that holds today, like the API's default.
    const starts = within(drawer).getByLabelText("Change starts") as HTMLInputElement;
    expect(starts.value).toBe("2026-10-01");
    expect(within(drawer).getByText("Cycles that start before this day keep the amount from before your change.")).toBeTruthy();
    await user.clear(starts);
    await user.type(starts, "2026-11-01");
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    expect(await within(drawer).findByText("Saved.")).toBeTruthy();
    expect(sentBody("PATCH", DINING)).toEqual({ set: { amount: "12.00" }, amount_from: "2026-11-01" });
    expect(invalidated(spy)).toEqual(["rewards", "dashboard", "checks", "trips"]);
    // The pill and the way back show at once.
    expect(within(drawer).getByRole("button", { name: "Use the chat's for the amount" })).toBeTruthy();
    expect((within(drawer).getByLabelText("Amount each cycle") as HTMLInputElement).value).toBe("12.00");
  });

  it("marks bad fields, sends nothing unchanged, and shows a refused save in words", async () => {
    const user = userEvent.setup();
    replies[`PATCH ${DINING}`] = () => json(422, { detail: "That does not fit this kind of benefit." });
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const drawer = await openDrawer(user, "Dining credit");
    const save = within(drawer).getByRole("button", { name: "Save" });
    await user.click(save);
    expect(within(drawer).getByText("Nothing to change.")).toBeTruthy();
    const name = within(drawer).getByLabelText("Name");
    await user.clear(name);
    await user.type(within(drawer).getByLabelText("Yearly value"), "lots");
    await user.click(save);
    expect(within(drawer).getByText("Enter a name.")).toBeTruthy();
    expect(within(drawer).getByText(MONEY_RULE)).toBeTruthy();
    expect(within(drawer).getByText("Fix the fields marked above.")).toBeTruthy();
    expect(name.getAttribute("aria-invalid")).toBe("true");
    expect(calls("PATCH", DINING)).toHaveLength(0);
    await user.type(name, "Dining");
    await user.clear(within(drawer).getByLabelText("Yearly value"));
    await user.click(save);
    expect(await within(drawer).findByText("That does not fit this kind of benefit.")).toBeTruthy();
    expect(sentBody("PATCH", DINING)).toEqual({ set: { name: "Dining", counted_value: null } });
    expect(within(drawer).queryByText("Saved.")).toBeNull();
  });

  it("puts each edited field back to the chat's, or every field at once", async () => {
    const user = userEvent.setup();
    payload = page();
    Object.assign(benefit("dining-credit", payload), { amount_cents: 1_200, note: "Weekends only", edited_fields: ["amount", "note"] });
    const chats = benefit("dining-credit");
    replies[`PATCH ${DINING}`] = () => serve({ ...chats, note: "Weekends only", edited_fields: ["note"] });
    replies[`DELETE ${DINING}/edits`] = () => serve(chats);
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const drawer = await openDrawer(user, "Dining credit");
    expect(within(drawer).getAllByText("Edited")).toHaveLength(2);
    // Typed and not saved: kept when another field goes back.
    await user.type(within(drawer).getByLabelText("How to use it"), " first");
    await user.click(within(drawer).getByRole("button", { name: "Use the chat's for the amount" }));
    await waitFor(() => expect((within(drawer).getByLabelText("Amount each cycle") as HTMLInputElement).value).toBe("10.00"));
    expect(sentBody("PATCH", DINING)).toEqual({ reset: ["amount"] });
    expect(within(drawer).queryByRole("button", { name: "Use the chat's for the amount" })).toBeNull();
    expect((within(drawer).getByLabelText("How to use it") as HTMLInputElement).value).toBe("Order from the partner delivery app first");
    expect(within(drawer).getByRole("button", { name: "Use the chat's for the note" })).toBeTruthy();
    await user.click(within(drawer).getByRole("button", { name: "Use the chat's for every field" }));
    await waitFor(() => expect(calls("DELETE", `${DINING}/edits`)).toHaveLength(1));
    await waitFor(() => expect((within(drawer).getByLabelText("Note") as HTMLInputElement).value).toBe(""));
    expect(within(drawer).queryByRole("button", { name: /Use the chat's/ })).toBeNull();
    expect(within(drawer).queryByText("Edited")).toBeNull();
  });

  it("says when the chat changed an edited field, with Keep mine and Use the chat's", async () => {
    const user = userEvent.setup();
    payload = page();
    const notice = { kind: "benefit_chat_changed" as const, fields: ["amount"] };
    Object.assign(benefit("dining-credit", payload), { amount_cents: 1_200, edited_fields: ["amount"], notices: [notice] });
    replies[`POST ${DINING}/resolve`] = () => json(200, benefit("dining-credit", payload as RewardsResponse));
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const drawer = await openDrawer(user, "Dining credit");
    const amount = within(drawer).getByLabelText("Amount each cycle").parentElement!;
    expect(within(amount).getByText("The chat changed this since your edit.")).toBeTruthy();
    // The two choices stand in for the plain way back.
    expect(within(amount).getAllByRole("button").map((b) => b.textContent)).toEqual(["Keep mine for the amount", "Use the chat's for the amount"]);
    await user.click(within(amount).getByRole("button", { name: "Keep mine for the amount" }));
    await waitFor(() => expect(calls("POST", `${DINING}/resolve`)).toHaveLength(1));
    expect(sentBody("POST", `${DINING}/resolve`)).toEqual({ field: "amount", choice: "mine" });
    await user.click(within(amount).getByRole("button", { name: "Use the chat's for the amount" }));
    await waitFor(() => expect(calls("POST", `${DINING}/resolve`)).toHaveLength(2));
    expect(sentBody("POST", `${DINING}/resolve`)).toEqual({ field: "amount", choice: "chat" });
  });

  it("follows a refetch on fields not typed in, and sends only the typed ones", async () => {
    const user = userEvent.setup();
    const errors = vi.spyOn(console, "error");
    replies[`PATCH ${DINING}`] = () => serve({ ...benefit("dining-credit"), amount_cents: 2_000, note: "Weekends only", edited_fields: ["note"] });
    const { queryClient } = renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const drawer = await openDrawer(user, "Dining credit");
    const amount = within(drawer).getByLabelText("Amount each cycle") as HTMLInputElement;
    const last = within(drawer).getByLabelText("Last day") as HTMLInputElement;
    expect(amount.value).toBe("10.00");
    expect(last.value).toBe("2026-10-20");
    // An import changes the amount and the end while the drawer is open.
    payload = page();
    Object.assign(benefit("dining-credit", payload), { amount_cents: 2_000, ends_on: "2026-09-30" });
    await act(() => queryClient.invalidateQueries({ queryKey: ["rewards"] }));
    await waitFor(() => expect(amount.value).toBe("20.00"));
    expect(last.value).toBe("2026-09-30");
    expect(within(drawer).getByText("The chat says it ends Sep 30, 2026.")).toBeTruthy();
    await user.type(within(drawer).getByLabelText("Note"), "Weekends only");
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    expect(await within(drawer).findByText("Saved.")).toBeTruthy();
    // Only the note: the old amount never goes back as an edit.
    expect(sentBody("PATCH", DINING)).toEqual({ set: { note: "Weekends only" } });
    expect(within(drawer).queryByText(/changed since you opened it/)).toBeNull();
    // Codex: the form and the end part had the same key, and React said so.
    expect(errors.mock.calls.filter((c) => String(c[0]).includes("same key"))).toEqual([]);
    errors.mockRestore();
  });

  it("asks before it sends a typed field that a refetch changed", async () => {
    const user = userEvent.setup();
    replies[`PATCH ${DINING}`] = () =>
      serve({ ...benefit("dining-credit"), name: "Dining perk", amount_cents: 1_500, amount_from: "2026-04-01", edited_fields: ["amount"] });
    const { queryClient } = renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const drawer = await openDrawer(user, "Dining credit");
    const amount = within(drawer).getByLabelText("Amount each cycle") as HTMLInputElement;
    const name = within(drawer).getByLabelText("Name") as HTMLInputElement;
    await user.clear(amount);
    await user.type(amount, "15");
    await user.clear(name);
    await user.type(name, "Dining out");
    expect((within(drawer).getByLabelText("Change starts") as HTMLInputElement).value).toBe("2026-10-01");
    // The chat renames it and changes the amount, with an earlier start, while both are typed.
    payload = page();
    Object.assign(benefit("dining-credit", payload), { name: "Dining perk", amount_cents: 2_000, amount_from: "2026-04-01" });
    await act(() => queryClient.invalidateQueries({ queryKey: ["rewards"] }));
    const amountField = amount.parentElement!;
    const nameField = name.parentElement!;
    expect(await within(amountField).findByText("This changed since you opened it. It is now $20.00.")).toBeTruthy();
    expect(within(nameField).getByText('This changed since you opened it. It is now "Dining perk".')).toBeTruthy();
    expect(described(amount)).toBe("This changed since you opened it. It is now $20.00.");
    // A screen reader hears it: the live region was there before the change.
    const heard = within(drawer).getByText("The name changed since you opened it. The amount changed since you opened it.");
    expect(heard.getAttribute("aria-live")).toBe("polite");
    // What is typed stays. The start was not typed, so it follows: a later day would be refused.
    expect(amount.value).toBe("15");
    expect(name.value).toBe("Dining out");
    expect((within(drawer).getByLabelText("Change starts") as HTMLInputElement).value).toBe("2026-04-01");
    const save = within(drawer).getByRole("button", { name: "Save" });
    await user.click(save);
    expect(within(drawer).getByText("Pick a value for each field that changed since you opened it.")).toBeTruthy();
    expect(calls("PATCH", DINING)).toHaveLength(0);
    // Codex: focus stayed on Save. The first field waiting for a pick takes it.
    expect(document.activeElement).toBe(name);
    // Codex: a pick took its own button away, and focus fell to the drawer. It goes to the field.
    within(amountField).getByRole("button", { name: "Keep what I typed for the amount" }).focus();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(document.activeElement).toBe(amount));
    await user.click(within(nameField).getByRole("button", { name: "Use the new value for the name" }));
    await waitFor(() => expect(document.activeElement).toBe(name));
    expect(name.value).toBe("Dining perk");
    expect(amount.value).toBe("15");
    expect(heard.textContent).toBe("");
    expect(within(drawer).queryByText(/changed since you opened it/)).toBeNull();
    expect(calls("PATCH", DINING)).toHaveLength(0); // a pick sends nothing on its own
    await user.click(save);
    expect(await within(drawer).findByText("Saved.")).toBeTruthy();
    expect(sentBody("PATCH", DINING)).toEqual({ set: { amount: "15.00" }, amount_from: "2026-04-01" });
  });

  it("keeps the day the confirm asks about when the page refetches", async () => {
    const user = userEvent.setup();
    replies[`PATCH ${DINING}`] = () => serve({ ...benefit("dining-credit"), ends_on: "2026-10-20", edited_fields: ["ends_on"] });
    const { queryClient } = renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const drawer = await openDrawer(user, "Dining credit");
    await user.click(within(drawer).getByRole("button", { name: "End this benefit" }));
    expect(within(drawer).getByText("End Dining credit on Oct 20, 2026? Later cycles drop out, and Fees stop counting it after that day.")).toBeTruthy();
    // Codex: a refetch with the chat's earlier end moved the day under the open confirm.
    payload = page();
    Object.assign(benefit("dining-credit", payload), { ends_on: "2026-09-30" });
    await act(() => queryClient.invalidateQueries({ queryKey: ["rewards"] }));
    expect(await within(drawer).findByText("The chat says it ends Sep 30, 2026.")).toBeTruthy();
    expect((within(drawer).getByLabelText("Last day") as HTMLInputElement).value).toBe("2026-10-20");
    expect(within(drawer).getByText(/^End Dining credit on Oct 20, 2026\? That is after the end it has now, Sep 30, 2026/)).toBeTruthy();
    await user.click(within(drawer).getByRole("button", { name: "End benefit" }));
    await waitFor(() => expect(calls("PATCH", DINING)).toHaveLength(1));
    expect(sentBody("PATCH", DINING)).toEqual({ set: { ends_on: "2026-10-20" } });
  });

  it("ends a benefit on a chosen day after an inline confirm, never a browser dialog", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm");
    const ended = { ...benefit("dining-credit"), ends_on: "2026-10-20", edited_fields: ["ends_on"] };
    replies[`PATCH ${DINING}`] = (body) => serve((body as { reset?: string[] }).reset ? benefit("dining-credit") : ended);
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const drawer = await openDrawer(user, "Dining credit");
    // Household today from the page, not the browser's clock.
    expect((within(drawer).getByLabelText("Last day") as HTMLInputElement).value).toBe("2026-10-20");
    await user.click(within(drawer).getByRole("button", { name: "End this benefit" }));
    expect(within(drawer).getByText("End Dining credit on Oct 20, 2026? Later cycles drop out, and Fees stop counting it after that day.")).toBeTruthy();
    expect(calls("PATCH", DINING)).toHaveLength(0);
    await user.click(within(drawer).getByRole("button", { name: "Cancel" }));
    expect(within(drawer).queryByRole("button", { name: "End benefit" })).toBeNull();
    await user.click(within(drawer).getByRole("button", { name: "End this benefit" }));
    await user.click(within(drawer).getByRole("button", { name: "End benefit" }));
    await waitFor(() => expect(calls("PATCH", DINING)).toHaveLength(1));
    expect(sentBody("PATCH", DINING)).toEqual({ set: { ends_on: "2026-10-20" } });
    expect(await within(drawer).findByText("Ends Oct 20, 2026.")).toBeTruthy();
    expect(within(drawer).queryByLabelText("Last day")).toBeNull();
    // Undo is Use the chat's on the end date.
    await user.click(within(drawer).getByRole("button", { name: "Use the chat's for the end date" }));
    await waitFor(() => expect(calls("PATCH", DINING)).toHaveLength(2));
    expect(sentBody("PATCH", DINING)).toEqual({ reset: ["ends_on"] });
    expect(await within(drawer).findByLabelText("Last day")).toBeTruthy();
    expect(confirm).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it("lets an app benefit drop its end date, with no way back to a chat's value", async () => {
    const user = userEvent.setup();
    payload = page();
    payload.benefits.unshift(appBenefit("app-gym-credit", "Gym credit", { ends_on: "2026-12-31" }));
    replies["PATCH /api/rewards/benefits/app-gym-credit"] = () => json(200, appBenefit("app-gym-credit", "Gym credit"));
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const drawer = await openDrawer(user, "Gym credit");
    expect(within(drawer).getByText("Summit card · Credit, monthly. Added in the app.")).toBeTruthy();
    expect(within(drawer).getByText("Ends Dec 31, 2026.")).toBeTruthy();
    expect(within(drawer).queryByText("Edited")).toBeNull(); // all of it is the app's
    expect(within(drawer).queryByRole("button", { name: /Use the chat's/ })).toBeNull();
    await user.click(within(drawer).getByRole("button", { name: "Remove the end date" }));
    await waitFor(() => expect(calls("PATCH", "/api/rewards/benefits/app-gym-credit")).toHaveLength(1));
    expect(sentBody("PATCH", "/api/rewards/benefits/app-gym-credit")).toEqual({ set: { ends_on: null } });
  });

  it("hides a benefit into Hidden, and Show again brings it back", async () => {
    const user = userEvent.setup();
    const dining = benefit("dining-credit");
    replies[`PATCH ${DINING}`] = (body) => {
      const hide = (body as { hidden: boolean }).hidden;
      const next = page();
      if (hide) {
        next.benefits = next.benefits.filter((b) => b.key !== "dining-credit");
        next.hidden = [{ key: "dining-credit", name: "Dining credit", kind: "credit", account_key: "summit-card", account_name: "Summit card", origin: "chat" }];
      }
      payload = next; // what the refetch sees
      return json(200, { ...dining, hidden: hide });
    };
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const drawer = await openDrawer(user, "Dining credit");
    expect(screen.queryByRole("button", { name: /^Hidden/ })).toBeNull();
    await user.click(within(drawer).getByRole("button", { name: "Hide this benefit" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(sentBody("PATCH", DINING)).toEqual({ hidden: true });
    const open = screen.getByRole("region", { name: "Open this cycle" });
    expect(within(open).queryByText("Dining credit")).toBeNull();
    // Its pencil is gone, so focus goes to Hidden, where it now waits.
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Hidden (1)" })));
    await user.click(screen.getByRole("button", { name: "Hidden (1)" }));
    const list = screen.getByRole("list", { name: "Hidden benefits" });
    expect(within(list).getByText("Summit card · Credit")).toBeTruthy();
    await user.click(within(list).getByRole("button", { name: "Show again: Dining credit" }));
    await waitFor(() => expect(within(screen.getByRole("region", { name: "Open this cycle" })).getByText("Dining credit")).toBeTruthy());
    expect(sentBody("PATCH", DINING)).toEqual({ hidden: false });
    await waitFor(() => expect(screen.queryByRole("button", { name: /^Hidden/ })).toBeNull());
    expect(screen.queryByRole("dialog")).toBeNull(); // the drawer stays closed
  });

  it("adds a credit with a calendar cadence, with no cycles", async () => {
    const user = userEvent.setup();
    replies["GET /api/accounts"] = () => json(200, ACCOUNTS);
    replies["POST /api/rewards/benefits"] = (body) => json(201, appBenefit("app-gym-credit", (body as { name: string }).name));
    const { queryClient } = renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    const add = await screen.findByRole("button", { name: "Add a benefit" });
    expect(add.className).toContain("w-full"); // full width on phones
    expect(add.className).toContain("md:w-auto");
    // At the bottom of the tab, after the other cards.
    const caps = screen.getByRole("heading", { name: "Earning caps" });
    expect(caps.compareDocumentPosition(add) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    await user.click(add);
    const drawer = screen.getByRole("dialog", { name: "Add a benefit" });
    const card = within(drawer).getByLabelText("Card");
    await waitFor(() => expect(within(card).getAllByRole("option").map((o) => o.textContent)).toEqual(["Pick a card", "Orchard card", "Summit card"]));
    await user.click(within(drawer).getByRole("button", { name: "Add benefit" }));
    expect(within(drawer).getByText("Pick a card.")).toBeTruthy();
    await user.selectOptions(card, "summit-card");
    await user.type(within(drawer).getByLabelText("Name"), "Gym credit");
    await user.selectOptions(within(drawer).getByLabelText("Cadence"), "quarterly");
    expect(within(drawer).queryByRole("list", { name: "Cycles" })).toBeNull();
    await user.type(within(drawer).getByLabelText("Amount each cycle"), "15");
    await user.click(within(drawer).getByRole("button", { name: "Add benefit" }));
    expect(await screen.findByText("Added Gym credit.")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(sentBody("POST", "/api/rewards/benefits")).toEqual({
      account_key: "summit-card",
      name: "Gym credit",
      kind: "credit",
      cadence: "quarterly",
      amount: "15.00",
    });
    expect(invalidated(spy)).toEqual(["rewards", "dashboard", "checks", "trips"]);
  });

  it("asks a multi-year credit for its cycles", async () => {
    const user = userEvent.setup();
    replies["GET /api/accounts"] = () => json(200, ACCOUNTS);
    replies["POST /api/rewards/benefits"] = () => json(201, appBenefit("app-trusted-traveler", "Trusted traveler"));
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    await user.click(await screen.findByRole("button", { name: "Add a benefit" }));
    const drawer = screen.getByRole("dialog", { name: "Add a benefit" });
    await waitFor(() => expect((within(drawer).getByLabelText("Card") as HTMLSelectElement).disabled).toBe(false));
    await user.selectOptions(within(drawer).getByLabelText("Card"), "orchard-card");
    await user.type(within(drawer).getByLabelText("Name"), "Trusted traveler");
    await user.selectOptions(within(drawer).getByLabelText("Cadence"), "multi_year");
    await user.type(within(drawer).getByLabelText("Amount each cycle"), "100");
    const cycles = within(drawer).getByRole("list", { name: "Cycles" });
    expect(within(cycles).getAllByRole("listitem")).toHaveLength(1);
    expect(within(cycles).queryByRole("button", { name: /^Remove/ })).toBeNull(); // the last one stays
    await user.type(within(drawer).getByLabelText("Start of cycle 1"), "2026-01-01");
    await user.click(within(drawer).getByRole("button", { name: "Add benefit" }));
    expect(within(drawer).getByText("Enter a start and an end for each cycle.")).toBeTruthy();
    expect(calls("POST", "/api/rewards/benefits")).toHaveLength(0);
    await user.type(within(drawer).getByLabelText("End of cycle 1"), "2029-12-31");
    await user.click(within(drawer).getByRole("button", { name: "Add a cycle" }));
    await user.click(within(drawer).getByRole("button", { name: "Add a cycle" }));
    expect(within(cycles).getAllByRole("listitem")).toHaveLength(3);
    await user.click(within(drawer).getByRole("button", { name: "Remove cycle 3" }));
    await user.type(within(drawer).getByLabelText("Start of cycle 2"), "2030-01-01");
    await user.type(within(drawer).getByLabelText("End of cycle 2"), "2033-12-31");
    const own = within(drawer).getByLabelText("Amount of cycle 2");
    expect(own.className).toContain("text-base");
    await user.type(own, "120");
    await user.click(within(drawer).getByRole("button", { name: "Add benefit" }));
    expect(await screen.findByText("Added Trusted traveler.")).toBeTruthy();
    expect(sentBody("POST", "/api/rewards/benefits")).toEqual({
      account_key: "orchard-card",
      name: "Trusted traveler",
      kind: "credit",
      cadence: "multi_year",
      amount: "100.00",
      cycles: [
        { start: "2026-01-01", end: "2029-12-31" },
        { start: "2030-01-01", end: "2033-12-31", amount: "120.00" },
      ],
    });
  });

  it("adds a free night with its program, and shows a refused add in words", async () => {
    const user = userEvent.setup();
    replies["GET /api/accounts"] = () => json(200, ACCOUNTS);
    replies["POST /api/rewards/benefits"] = () => json(422, { detail: "Unknown program." });
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    await user.click(await screen.findByRole("button", { name: "Add a benefit" }));
    const drawer = screen.getByRole("dialog", { name: "Add a benefit" });
    await waitFor(() => expect((within(drawer).getByLabelText("Card") as HTMLSelectElement).disabled).toBe(false));
    await user.selectOptions(within(drawer).getByLabelText("Card"), "summit-card");
    await user.type(within(drawer).getByLabelText("Name"), "Spa night");
    await user.selectOptions(within(drawer).getByLabelText("Kind"), "free_night");
    expect(within(drawer).queryByLabelText("Amount each cycle")).toBeNull();
    await user.click(within(drawer).getByRole("button", { name: "Add benefit" }));
    expect(within(drawer).getByText("Pick the hotel program.")).toBeTruthy();
    await user.selectOptions(within(drawer).getByLabelText("Hotel program"), "summit-points");
    await user.type(within(drawer).getByLabelText("Use by"), "2027-06-30");
    await user.click(within(drawer).getByRole("button", { name: "Add benefit" }));
    expect(await within(drawer).findByText("Unknown program.")).toBeTruthy();
    expect(sentBody("POST", "/api/rewards/benefits")).toEqual({
      account_key: "summit-card",
      name: "Spa night",
      kind: "free_night",
      program_key: "summit-points",
      ends_on: "2027-06-30",
    });
  });

  it("offers the fixes in Needs a look, each to its own route", async () => {
    const user = userEvent.setup();
    payload = page();
    payload.benefits.push(
      appBenefit("app-dining", "Dining", { group: "other" }),
      appBenefit("app-streaming", "Streaming credit", { group: "other", account_key: "cobalt-card", account_name: "Cobalt card" }),
    );
    payload.notices = [
      { kind: "benefit_chat_changed", text: "The chat changed the amount since your edit.", key: "dining-credit", other_key: null, fields: ["amount"] },
      { kind: "benefit_no_fit", text: "Your edit no longer fits this benefit.", key: "travel-credit", other_key: null, fields: ["amount"] },
      { kind: "benefit_app_key", text: "The chat now sends this benefit too. Yours is shown.", key: "app-streaming", other_key: null, fields: [] },
      {
        kind: "benefit_duplicate",
        text: "The chat now sends a benefit like this one. Fees may count it twice.",
        key: "app-dining",
        other_key: "dining-credit",
        fields: [],
      },
      { kind: "program_duplicate", text: "Two programs have the same name.", key: "app-harbor", other_key: "harbor-miles", fields: [] }, // the Points tab's
    ];
    payload.orphans = [
      { kind: "benefit", key: "old-credit", holds: ["edits", "terms"], reason: "dropped", unit: null },
      { kind: "program", key: "old-miles", holds: ["balances"], reason: "dropped", unit: "whole" }, // the Points tab's
    ];
    const ok = () => json(200, benefit("dining-credit"));
    replies[`POST ${DINING}/resolve`] = ok;
    replies["DELETE /api/rewards/benefits/travel-credit/edits"] = ok;
    replies["DELETE /api/rewards/benefits/app-streaming"] = () => new Response(null, { status: 204 });
    replies["POST /api/rewards/benefits/app-dining/move"] = ok;
    replies["POST /api/rewards/benefits/app-dining/keep-both"] = ok;
    replies["POST /api/rewards/benefits/old-credit/move"] = ok;
    replies["DELETE /api/rewards/benefits/old-credit"] = () => new Response(null, { status: 204 });
    const { queryClient } = renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const spy = vi.spyOn(queryClient, "invalidateQueries");
    const list = await screen.findByRole("list", { name: "Needs a look" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(5);
    // Needs a look is the first card on the tab.
    const open = screen.getByRole("region", { name: "Open this cycle" });
    expect(list.compareDocumentPosition(open) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(items[0]!).getByText("Dining credit · Summit card")).toBeTruthy();
    expect(within(items[0]!).getByText("The chat changed the amount since your edit.")).toBeTruthy();
    expect(within(list).queryByText(/old-miles|Two programs/)).toBeNull();

    await user.click(within(list).getByRole("button", { name: "Keep mine for the amount of Dining credit · Summit card" }));
    await waitFor(() => expect(calls("POST", `${DINING}/resolve`)).toHaveLength(1));
    expect(sentBody("POST", `${DINING}/resolve`)).toEqual({ field: "amount", choice: "mine" });
    await user.click(within(list).getByRole("button", { name: "Use the chat's for the amount of Dining credit · Summit card" }));
    await waitFor(() => expect(calls("POST", `${DINING}/resolve`)).toHaveLength(2));
    expect(sentBody("POST", `${DINING}/resolve`)).toEqual({ field: "amount", choice: "chat" });

    await user.click(within(list).getByRole("button", { name: "Delete edit for Yearly travel credit · Summit card" }));
    await waitFor(() => expect(calls("DELETE", "/api/rewards/benefits/travel-credit/edits")).toHaveLength(1));

    await user.click(within(list).getByRole("button", { name: "Use the chat's for Streaming credit · Cobalt card" }));
    await waitFor(() => expect(calls("DELETE", "/api/rewards/benefits/app-streaming")).toHaveLength(1));

    expect(within(items[3]!).getByText(/^The chat's is Dining credit · Summit card\./)).toBeTruthy();
    await user.click(within(list).getByRole("button", { name: "Use the chat's Dining credit · Summit card" }));
    await waitFor(() => expect(calls("POST", "/api/rewards/benefits/app-dining/move")).toHaveLength(1));
    expect(sentBody("POST", "/api/rewards/benefits/app-dining/move")).toEqual({ to_key: "dining-credit" });
    await user.click(within(list).getByRole("button", { name: "Keep both (yours and the chat's Dining credit · Summit card)" }));
    await waitFor(() => expect(calls("POST", "/api/rewards/benefits/app-dining/keep-both")).toHaveLength(1));
    expect(sentBody("POST", "/api/rewards/benefits/app-dining/keep-both")).toEqual({ other_key: "dining-credit" });

    const orphan = items[4]!;
    expect(within(orphan).getByText("The chat no longer sends old-credit.")).toBeTruthy();
    expect(within(orphan).getByText(/^Your edits and match words on it are kept, not shown\./)).toBeTruthy();
    const moveTo = within(orphan).getByRole("combobox", { name: "Move to (what old-credit holds)" });
    // Every current benefit, caps and perks too. The API checks the card and kind.
    expect(within(moveTo).getAllByRole("option").map((o) => o.textContent)).toEqual([
      "Pick a benefit",
      "Dining credit · Summit card",
      "Yearly travel credit · Summit card",
      "Anniversary free night · Summit card",
      "Wireless credit · Orchard card",
      "Streaming credit · Cobalt card",
      "Dining · Summit card (added in the app)",
      "Streaming credit · Cobalt card (added in the app)",
      "Fuel bonus · Cobalt card",
      "Hotel status · Summit card",
    ]);
    // Nothing is picked at first, so one tap cannot move to the first benefit.
    expect((moveTo as HTMLSelectElement).value).toBe("");
    expect((within(orphan).getByRole("button", { name: "Move what old-credit holds" }) as HTMLButtonElement).disabled).toBe(true);
    await user.selectOptions(moveTo, "wireless-credit");
    await user.click(within(orphan).getByRole("button", { name: "Move what old-credit holds" }));
    await waitFor(() => expect(calls("POST", "/api/rewards/benefits/old-credit/move")).toHaveLength(1));
    expect(sentBody("POST", "/api/rewards/benefits/old-credit/move")).toEqual({ to_key: "wireless-credit" });
    await user.click(within(orphan).getByRole("button", { name: "Delete edits for old-credit" }));
    expect(within(orphan).getByText("Delete everything kept for old-credit?")).toBeTruthy();
    expect(calls("DELETE", "/api/rewards/benefits/old-credit")).toHaveLength(0);
    // Focus is on the confirm button, and the question is its description.
    const confirmDelete = within(orphan).getByRole("button", { name: "Delete edits" });
    await waitFor(() => expect(document.activeElement).toBe(confirmDelete));
    expect(described(confirmDelete)).toBe("Delete everything kept for old-credit?");
    await user.click(confirmDelete);
    await waitFor(() => expect(calls("DELETE", "/api/rewards/benefits/old-credit")).toHaveLength(1));
    expect(invalidated(spy).filter((k) => k === "rewards")).toHaveLength(8);
  });

  it("shows a refused fix next to its notice", async () => {
    const user = userEvent.setup();
    payload = page();
    payload.orphans = [{ kind: "benefit", key: "old-credit", holds: ["edits"], reason: "dropped", unit: null }];
    replies["POST /api/rewards/benefits/old-credit/move"] = () => json(422, { detail: "Pick a benefit of the same kind on the same card." });
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const list = await screen.findByRole("list", { name: "Needs a look" });
    expect(within(list).getByText(/^Your edits on it are kept, not shown\./)).toBeTruthy();
    await user.selectOptions(within(list).getByRole("combobox"), "travel-credit");
    await user.click(within(list).getByRole("button", { name: "Move what old-credit holds" }));
    expect(await within(list).findByText("Pick a benefit of the same kind on the same card.")).toBeTruthy();
  });

  it("shows members the notices and the hint, with no fix buttons", async () => {
    role = "member";
    payload = page();
    payload.notices = [{ kind: "benefit_no_fit", text: "Your edit no longer fits this benefit.", key: "travel-credit", other_key: null, fields: ["amount"] }];
    payload.orphans = [{ kind: "benefit", key: "old-credit", holds: ["edits"], reason: "dropped", unit: null }];
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const list = await screen.findByRole("list", { name: "Needs a look" });
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(within(list).queryAllByRole("button")).toHaveLength(0);
    expect(within(list).queryByRole("combobox")).toBeNull();
    const card = list.closest("section")!;
    expect(within(card).getByText("Only an admin can change benefits.")).toBeTruthy();
  });

  it("keeps benefit notices off the Points tab and program notices off Benefits", async () => {
    payload = page();
    payload.notices = [{ kind: "benefit_no_fit", text: "Your edit no longer fits this benefit.", key: "travel-credit", other_key: null, fields: ["amount"] }];
    payload.orphans = [{ kind: "benefit", key: "old-credit", holds: ["edits"], reason: "dropped", unit: null }];
    renderWithProviders(<RewardsPage />, { route: "/rewards" });
    await screen.findByRole("list", { name: "Main programs" });
    expect(screen.queryByText("Needs a look")).toBeNull();
    expect(screen.queryByText(/old-credit/)).toBeNull();
  });

  it("keeps the empty state and no benefit controls before the first 1.3 run", async () => {
    payload = NO_REWARDS;
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    expect(await screen.findByText("No rewards data yet.")).toBeTruthy();
    await waitFor(() => expect(calls("GET", "/api/auth/me")).toHaveLength(1));
    expect(screen.queryByRole("button", { name: "Add a benefit" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Edit / })).toBeNull();
    expect(screen.queryByText(/Only an admin/)).toBeNull();
    expect(screen.queryByText("Needs a look")).toBeNull();
  });

  it.each(["admin", "member"] as const)("keeps benefit controls and hints off the page when it fails to load (%s)", async (who) => {
    payload = "error";
    role = who;
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    expect((await screen.findByRole("alert")).textContent).toContain("Could not load rewards.");
    await waitFor(() => expect(calls("GET", "/api/auth/me")).toHaveLength(1));
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getAllByText("Not loaded.")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: "Add a benefit" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Hidden/ })).toBeNull();
    expect(screen.queryByText(/Only an admin/)).toBeNull();
    expect(screen.queryByText("Needs a look")).toBeNull();
  });

  it("offers only Delete edit and hide while an edit no longer fits", async () => {
    const user = userEvent.setup();
    payload = page();
    benefit("dining-credit", payload).notices = [{ kind: "benefit_no_fit", fields: ["amount"] }];
    replies[`PATCH ${DINING}`] = () => json(200, { ...benefit("dining-credit", payload as RewardsResponse), hidden: true });
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const drawer = await openDrawer(user, "Dining credit");
    expect(within(drawer).getByText(/Delete the edit first to change this benefit\.$/)).toBeTruthy();
    expect(within(drawer).getByRole("button", { name: "Delete edit" })).toBeTruthy();
    // No field, Save or End that would only be refused.
    expect(within(drawer).queryByLabelText("Amount each cycle")).toBeNull();
    expect(within(drawer).queryByRole("button", { name: "Save" })).toBeNull();
    expect(within(drawer).queryByRole("button", { name: "End this benefit" })).toBeNull();
    await user.click(within(drawer).getByRole("button", { name: "Hide this benefit" }));
    await waitFor(() => expect(calls("PATCH", DINING)).toHaveLength(1));
    expect(sentBody("PATCH", DINING)).toEqual({ hidden: true });
  });

  it("saves the chat's amount typed back in, and starts a second change where the first did", async () => {
    const user = userEvent.setup();
    payload = page();
    Object.assign(benefit("dining-credit", payload), { amount_cents: 1_200, edited_fields: ["amount"], amount_from: "2026-04-01" });
    replies[`PATCH ${DINING}`] = () => serve(benefit("dining-credit"));
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const drawer = await openDrawer(user, "Dining credit");
    const amount = within(drawer).getByLabelText("Amount each cycle");
    await user.clear(amount);
    await user.type(amount, "10");
    // The start it had, not this cycle's: a later day would be refused.
    expect((within(drawer).getByLabelText("Change starts") as HTMLInputElement).value).toBe("2026-04-01");
    await user.click(within(drawer).getByRole("button", { name: "Save" }));
    expect(await within(drawer).findByText("Saved.")).toBeTruthy();
    expect(sentBody("PATCH", DINING)).toEqual({ set: { amount: "10.00" }, amount_from: "2026-04-01" });
    expect((within(drawer).getByLabelText("Amount each cycle") as HTMLInputElement).value).toBe("10.00");
  });

  it("starts the last day of an ended benefit at its end, and says when a later day brings it back", async () => {
    const user = userEvent.setup();
    payload = page();
    Object.assign(benefit("travel-credit", payload), { group: "ended", ends_on: "2026-06-30", cycle: null });
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    await screen.findByRole("region", { name: "Open this cycle" });
    await user.click(screen.getByRole("button", { name: "Show past cycles" }));
    await user.click(within(screen.getByRole("region", { name: "Yearly travel credit past cycles" })).getByRole("button", { name: "Edit Yearly travel credit" }));
    const drawer = screen.getByRole("dialog", { name: "Edit Yearly travel credit" });
    const last = within(drawer).getByLabelText("Last day") as HTMLInputElement;
    expect(last.value).toBe("2026-06-30");
    await user.clear(last);
    await user.type(last, "2026-10-20");
    await user.click(within(drawer).getByRole("button", { name: "End this benefit" }));
    expect(
      within(drawer).getByText("End Yearly travel credit on Oct 20, 2026? That is after the end it has now, Jun 30, 2026, so it counts again up to that day."),
    ).toBeTruthy();
  });

  it("moves focus into an inline confirm and back out on Cancel", async () => {
    const user = userEvent.setup();
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const drawer = await openDrawer(user, "Dining credit");
    await user.click(within(drawer).getByRole("button", { name: "End this benefit" }));
    const end = within(drawer).getByRole("button", { name: "End benefit" });
    await waitFor(() => expect(document.activeElement).toBe(end));
    expect(described(end)).toBe("End Dining credit on Oct 20, 2026? Later cycles drop out, and Fees stop counting it after that day.");
    await user.click(within(drawer).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(document.activeElement).toBe(within(drawer).getByRole("button", { name: "End this benefit" })));
  });

  it("hides an earning cap or a status perk from its own pencil", async () => {
    const user = userEvent.setup();
    const fuel = { ...benefit("dining-credit"), key: "fuel-bonus", name: "Fuel bonus", kind: "earning_cap" as const };
    replies["PATCH /api/rewards/benefits/fuel-bonus"] = () => {
      const next = page();
      next.caps = [];
      next.hidden = [{ key: "fuel-bonus", name: "Fuel bonus", kind: "earning_cap", account_key: "cobalt-card", account_name: "Cobalt card", origin: "chat" }];
      payload = next;
      return json(200, { ...fuel, hidden: true });
    };
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const pencil = await screen.findByRole("button", { name: "Edit Fuel bonus" });
    expect(pencil.className).toContain("h-11");
    await user.click(pencil);
    const drawer = screen.getByRole("dialog", { name: "Edit Fuel bonus" });
    expect(within(drawer).getByText("Cobalt card · Earning cap. From the chat.")).toBeTruthy();
    expect(within(drawer).queryByRole("button", { name: "Delete this benefit" })).toBeNull(); // the chat's
    await user.click(within(drawer).getByRole("button", { name: "Hide this benefit" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(sentBody("PATCH", "/api/rewards/benefits/fuel-bonus")).toEqual({ hidden: true });
    expect(screen.queryByRole("button", { name: "Edit Fuel bonus" })).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", { name: "Hidden (1)" })));
  });

  it("deletes a benefit added in the app after an inline confirm", async () => {
    const user = userEvent.setup();
    const lounge = { ...REWARDS.perks[0]!, key: "app-lounge", name: "Lounge access", origin: "app" as const };
    payload = page();
    payload.benefits.unshift(appBenefit("app-gym-credit", "Gym credit", { group: "open" }));
    payload.perks.push(lounge);
    replies["DELETE /api/rewards/benefits/app-gym-credit"] = () => {
      payload = page();
      payload.perks.push(lounge);
      return new Response(null, { status: 204 });
    };
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    let drawer = await openDrawer(user, "Gym credit");
    await user.click(within(drawer).getByRole("button", { name: "Delete this benefit" }));
    expect(calls("DELETE", "/api/rewards/benefits/app-gym-credit")).toHaveLength(0);
    await user.click(within(drawer).getByRole("button", { name: "Delete benefit" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(calls("DELETE", "/api/rewards/benefits/app-gym-credit")).toHaveLength(1);
    // Its pencil is gone, so focus goes to the list's heading.
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("heading", { name: "Use-or-lose benefits" })));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Edit Gym credit" })).toBeNull());
    // A chat benefit has no Delete, an app perk does.
    drawer = await openDrawer(user, "Dining credit");
    expect(within(drawer).queryByRole("button", { name: "Delete this benefit" })).toBeNull();
    await user.click(within(drawer).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    drawer = await openDrawer(user, "Lounge access");
    expect(within(drawer).getByText("Summit card · Status perk. Added in the app.")).toBeTruthy();
    expect(within(drawer).getByRole("button", { name: "Delete this benefit" })).toBeTruthy();
  });

  it("offers Delete for an app benefit that can no longer be shown", async () => {
    const user = userEvent.setup();
    payload = page();
    payload.notices = [
      {
        kind: "benefit_app_no_fit",
        text: "This benefit can no longer be shown. The program it names may be gone. Delete it and add it again.",
        key: "app-spring-night",
        other_key: null,
        fields: [],
      },
    ];
    replies["DELETE /api/rewards/benefits/app-spring-night"] = () => new Response(null, { status: 204 });
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    const list = await screen.findByRole("list", { name: "Needs a look" });
    expect(within(list).getByText(/^This benefit can no longer be shown\./)).toBeTruthy();
    await user.click(within(list).getByRole("button", { name: "Delete app-spring-night" }));
    expect(calls("DELETE", "/api/rewards/benefits/app-spring-night")).toHaveLength(0);
    await user.click(within(list).getByRole("button", { name: "Delete benefit" }));
    await waitFor(() => expect(calls("DELETE", "/api/rewards/benefits/app-spring-night")).toHaveLength(1));
  });

  it("uses no em dashes on the tab", async () => {
    payload = page();
    payload.hidden = [{ key: "wireless-credit", name: "Wireless credit", kind: "credit", account_key: "orchard-card", account_name: "Orchard card", origin: "chat" }];
    payload.orphans = [{ kind: "benefit", key: "old-credit", holds: ["edits"], reason: "dropped", unit: null }];
    renderWithProviders(<RewardsPage />, { route: BENEFITS_TAB });
    await screen.findByRole("list", { name: "Needs a look" });
    expect(document.body.textContent).not.toContain("—");
  });
});
