import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import type { BudgetsOut } from "./api";
import { BudgetPage, conflictsText } from "./BudgetPage";
import { progressColor } from "./BudgetTable";
import { cycleLabel } from "./ChatBudgetsCard";
import { draftResult, laterMonthsText, MOVED_TEXT, preview } from "./EditTargetsDrawer";
import { BUDGET_AUG, BUDGET_OCT, BUDGET_OCT_EDIT, withLifestyleEdit } from "./fixtures";

// Fictional people and numbers only.

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

/** What the server sends a member: the same targets, no edit controls. */
function asMember(page: BudgetsOut): BudgetsOut {
  return {
    ...page,
    can_edit: false,
    edit_note: page.edit_note ?? "Only an admin can change targets.",
    later_months: [],
    addable_groups: [],
    groups: page.groups.map((g) => ({ ...g, editable: false })),
  };
}

let failing = false;
let role: "admin" | "member" = "admin";
let oct: BudgetsOut = BUDGET_OCT;
/** What PUT /api/budgets/targets answers: a page (and the next GET), or an error. */
let putAnswer: { status: number; body: unknown } = { status: 200, body: BUDGET_OCT };
/** When set, the October page after any PUT (a refetch that shows what the server has now). */
let nextOct: BudgetsOut | null = null;
const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  if (url === "/api/auth/me") return json(200, { id: 1, username: "example", display_name: "Example", role, demo: false });
  if (url === "/api/budgets/targets" && init?.method === "PUT") {
    if (putAnswer.status === 200) oct = putAnswer.body as BudgetsOut;
    if (nextOct) oct = nextOct;
    return json(putAnswer.status, putAnswer.body);
  }
  if (url.startsWith("/api/budgets")) {
    if (failing) return json(500, { detail: "Server error." });
    const month = new URL(url, "http://x").searchParams.get("month");
    const page =
      month === "2026-10"
        ? oct
        : month === null || month === "2026-08"
          ? BUDGET_AUG
          : { ...BUDGET_AUG, month: month!, chat_budgets: [], statements_needed: [] };
    return json(200, role === "member" ? asMember(page) : page);
  }
  return json(404, { detail: "Not found." });
});

beforeEach(() => {
  failing = false;
  role = "admin";
  oct = BUDGET_OCT;
  putAnswer = { status: 200, body: BUDGET_OCT };
  nextOct = null;
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function urls(): string[] {
  return fetchMock.mock.calls
    .filter(([, init]) => (init?.method ?? "GET") === "GET")
    .map((c) => String(c[0]))
    .filter((u) => u.startsWith("/api/budgets"));
}

function puts(): unknown[] {
  return fetchMock.mock.calls
    .filter(([u, init]) => String(u) === "/api/budgets/targets" && init?.method === "PUT")
    .map(([, init]) => JSON.parse(String(init!.body)));
}

async function monthHeading(name: string) {
  return screen.findByRole("heading", { level: 2, name });
}

/** The table row whose header cell starts with this text. */
function row(name: string): HTMLElement {
  const header = screen.getAllByRole("rowheader").find((th) => (th.textContent ?? "").replace(/^\P{L}+/u, "").startsWith(name));
  if (!header) throw new Error(`No row ${name}`);
  return header.closest("tr")!;
}

describe("helpers", () => {
  it("labels cycles and picks progress colors", () => {
    expect(cycleLabel({ period_basis: "calendar", cycle_note: null })).toBe("Calendar month");
    expect(cycleLabel({ period_basis: "card_cycle", cycle_note: "Closes on the 22nd" })).toBe("Card cycle · Closes on the 22nd");
    expect(cycleLabel({ period_basis: "card_cycle", cycle_note: null })).toBe("Card cycle");
    expect(progressColor({ kind: "spending", status: "over" })).toBe("var(--fill-bad)");
    expect(progressColor({ kind: "spending", status: "under" })).toBe("var(--fill-good)");
    expect(progressColor({ kind: "saved_invested", status: "over" })).toBe("var(--chart-leftover)");
  });
});

describe("BudgetPage", () => {
  it("opens on the latest complete month without asking for a month", async () => {
    renderWithProviders(<BudgetPage />, { route: "/budget" });
    await monthHeading("Aug 2026");
    expect(urls()[0]).toBe("/api/budgets");
    expect(screen.queryByRole("button", { name: "Latest complete" })).toBeNull();
  });

  it("shows plan, actual and a remaining pill in words for each group", async () => {
    renderWithProviders(<BudgetPage />, { route: "/budget" });
    await monthHeading("Aug 2026");
    const housing = row("Housing and utilities");
    expect(within(housing).getByText("$300 over").closest("span")!.className).toContain("text-bad");
    expect(within(housing).getByRole("img", { name: "Housing and utilities: $2,300 of $2,000 plan" })).toBeTruthy();
    expect(within(row("Lifestyle")).getByText("$500 left").closest("span")!.className).toContain("text-good");
    // More saved than planned is good, not "over".
    expect(within(row("Saved and invested")).getByText("$200 more than plan").closest("span")!.className).toContain("text-good");
    expect(within(row("Other discretionary")).getAllByText("No plan").length).toBeGreaterThan(0);
    // A category with a chat target gets a pill; one without does not.
    expect(within(row("Dining")).getByText("$50 over")).toBeTruthy();
    expect(within(row("Mortgage")).queryByText(/over|left/)).toBeNull();
    expect(within(row("Total")).getByText("$200 over")).toBeTruthy();
  });

  it("hides and shows a group's categories", async () => {
    renderWithProviders(<BudgetPage />, { route: "/budget" });
    await monthHeading("Aug 2026");
    const user = userEvent.setup();
    const toggle = screen.getByRole("button", { name: "Hide categories in Housing and utilities" });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    await user.click(toggle);
    expect(screen.queryByText("Mortgage")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Show categories in Housing and utilities" }));
    expect(screen.getByText("Mortgage")).toBeTruthy();
    // A 44px hit area on phones.
    expect(toggle.className).toContain("tap-target");
  });

  it("puts the table in a labeled region a keyboard can scroll", async () => {
    renderWithProviders(<BudgetPage />, { route: "/budget" });
    await monthHeading("Aug 2026");
    const region = screen.getByRole("region", { name: "Budget by group" });
    expect(region.getAttribute("tabindex")).toBe("0");
    expect(within(region).getByRole("table")).toBeTruthy();
  });

  it("lists the chat's budgets labeled by cycle, with statements-needed warnings", async () => {
    renderWithProviders(<BudgetPage />, { route: "/budget" });
    await monthHeading("Aug 2026");
    const panel = screen.getByRole("heading", { level: 2, name: "The chat's budgets" }).closest("section")!;
    expect(within(panel).getByText(/Card cycle · Cycle closes on the 22nd · Cobalt card/)).toBeTruthy();
    expect(within(panel).getByText(/Calendar month/)).toBeTruthy();
    expect(within(panel).getByText("Needs the statement for Cobalt card")).toBeTruthy();
    expect(within(panel).getByText("Over by $50")).toBeTruthy();
    expect(screen.getByText("Waiting on statements: Cobalt card.")).toBeTruthy();
  });

  it("shows the tracked cards for the month, payment basis", async () => {
    renderWithProviders(<BudgetPage />, { route: "/budget" });
    await monthHeading("Aug 2026");
    const card = screen.getByRole("heading", { level: 2, name: /^Sam's cards/ }).closest("section")!;
    expect(within(card).getByText("payment basis")).toBeTruthy();
    expect(within(card).getByText("$44 in payments, $33 from a statement")).toBeTruthy();
  });

  it("moves by month with the arrows and Today, and says statements are not in yet", async () => {
    renderWithProviders(<BudgetPage />, { route: "/budget" });
    await monthHeading("Aug 2026");
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Previous month" }));
    await monthHeading("Jul 2026");
    expect(urls()).toContain("/api/budgets?month=2026-07");

    await user.click(screen.getByRole("button", { name: "Today" }));
    await monthHeading("Oct 2026");
    expect(urls()).toContain("/api/budgets?month=2026-10");
    expect(await screen.findByText("Statements not in yet.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Next month" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByText("Nothing spent or planned this month.")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Latest complete" }));
    await monthHeading("Aug 2026");
  });

  it("reads the month from the URL", async () => {
    renderWithProviders(<BudgetPage />, { route: "/budget?month=2026-10" });
    await monthHeading("Oct 2026");
    expect(urls()[0]).toBe("/api/budgets?month=2026-10");
  });

  it("ignores a bad month in the URL", async () => {
    renderWithProviders(<BudgetPage />, { route: "/budget?month=nope" });
    await monthHeading("Aug 2026");
    expect(urls()[0]).toBe("/api/budgets");
  });

  it("shows one banner when the budget fails to load", async () => {
    failing = true;
    renderWithProviders(<BudgetPage />, { route: "/budget" });
    await waitFor(() => expect(screen.getByText("Could not load the budget.")).toBeTruthy());
  });
});

describe("target edit helpers", () => {
  const life = BUDGET_OCT_EDIT.groups[1]!;

  it("works out what a row would save", () => {
    expect(draftResult(life, {})).toEqual({ cents: 365_000, change: null, error: null });
    expect(draftResult(life, { text: "3,650" }).change).toBeNull();
    expect(draftResult(life, { text: "$3,400" }).change).toEqual({ group_key: "lifestyle", target_cents: 340_000, seen_chat_cents: 365_000 });
    expect(draftResult(life, { text: "0" }).change?.target_cents).toBe(0);
    expect(draftResult(life, { text: "12.345" }).error).toBe("Use dollars and cents, like 12.00.");
    expect(draftResult(life, { text: "" }).error).toBe("Enter an amount. $0 is fine.");
    expect(draftResult(life, { text: "1,000,000.01" }).error).toBe("Use an amount from $0 to $1,000,000.");
    expect(draftResult({ ...life, plan_cents: null, chat_plan_cents: null }, { text: "" })).toEqual({ cents: null, change: null, error: null });
    const edited = withLifestyleEdit(BUDGET_OCT_EDIT, 380_000).groups[1]!;
    expect(draftResult(edited, { choice: "chat" }).change).toEqual({ group_key: "lifestyle", target_cents: null, seen_chat_cents: 380_000 });
    expect(draftResult(edited, { choice: "mine" }).change).toEqual({ group_key: "lifestyle", target_cents: 340_000, seen_chat_cents: 380_000 });
    // The chat's number seen at the first change goes, until the form has pointed out the new one.
    expect(draftResult(life, { text: "3400", seen: 360_000 }).change?.seen_chat_cents).toBe(360_000);
    expect(draftResult(life, { text: "3400", seen: 360_000, ack: 365_000 }).change?.seen_chat_cents).toBe(365_000);
  });

  it("moves the total and left over together and names the later months", () => {
    expect(preview(BUDGET_OCT_EDIT, {})).toEqual({ total: 860_000, chatTotal: 860_000, leftOver: 240_000 });
    expect(preview(BUDGET_OCT_EDIT, { lifestyle: { text: "3400" }, housing: { text: "nope" } })).toEqual({
      total: 835_000,
      chatTotal: 860_000,
      leftOver: 265_000,
    });
    // Plan It installments are read only, so typing there cannot move the total.
    expect(preview(BUDGET_OCT_EDIT, { "plan-installments": { text: "0" } }).total).toBe(860_000);
    expect(laterMonthsText("2026-10", ["2026-11"])).toBe("Nov");
    expect(laterMonthsText("2026-10", ["2026-11", "2026-12"])).toBe("Nov and Dec");
    expect(laterMonthsText("2026-11", ["2026-12", "2027-01", "2027-02"])).toBe("Dec, Jan 2027 and Feb 2027");
    expect(conflictsText(1, true)).toBe("The chat changed 1 target since your edit.");
    expect(conflictsText(2, false)).toBe("The chat changed 2 targets since it was edited.");
  });
});

describe("Edit targets", () => {
  /** The footer line, as one string. */
  function footer(): string {
    const p = screen.getAllByText((_, el) => el?.tagName === "P" && /^Total /.test(el.textContent ?? ""));
    return p[0]!.textContent ?? "";
  }

  async function openEditor() {
    const user = userEvent.setup();
    renderWithProviders(<BudgetPage />, { route: "/budget?month=2026-10" });
    await user.click(await screen.findByRole("button", { name: "Edit targets" }));
    const drawer = await screen.findByRole("dialog", { name: "Edit targets for Oct 2026" });
    return { user, drawer };
  }

  it("shows Edit targets to an admin on this month", async () => {
    oct = BUDGET_OCT_EDIT;
    renderWithProviders(<BudgetPage />, { route: "/budget?month=2026-10" });
    expect(await screen.findByRole("button", { name: "Edit targets" })).toBeTruthy();
    expect(screen.getByText("Targets come from the chat's plan. You can change this month's.")).toBeTruthy();
    expect(screen.queryByText("Only an admin can change targets.")).toBeNull();
  });

  it("shows a member the hint and no button or edit links", async () => {
    role = "member";
    oct = withLifestyleEdit(BUDGET_OCT_EDIT);
    renderWithProviders(<BudgetPage />, { route: "/budget?month=2026-10" });
    expect(await screen.findByText("Only an admin can change targets.")).toBeTruthy();
    expect(screen.getByText("Targets come from the chat's plan.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Edit targets" })).toBeNull();
    expect(screen.queryByRole("button", { name: /edit the .* target/ })).toBeNull();
    // The tag still shows, as text, on wide screens and on phones.
    expect(within(row("Lifestyle")).getAllByText("Edited")).toHaveLength(2);
  });

  it("says past months keep their targets and goes to this month", async () => {
    renderWithProviders(<BudgetPage />, { route: "/budget" });
    await monthHeading("Aug 2026");
    expect(screen.getByText("Past months keep the targets they had.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Edit targets" })).toBeNull();
    await userEvent.setup().click(screen.getByRole("button", { name: "Go to Oct 2026" }));
    await monthHeading("Oct 2026");
    expect(urls()).toContain("/api/budgets?month=2026-10");
  });

  it("updates the footer as you type and saves only the changed rows", async () => {
    oct = BUDGET_OCT_EDIT;
    putAnswer = { status: 200, body: withLifestyleEdit(BUDGET_OCT_EDIT) };
    const { user, drawer } = await openEditor();
    const d = within(drawer);
    expect(footer()).toBe("Total $8,600 · chat planned $8,600 · Left over $2,400");
    const save = d.getByRole("button", { name: "Save" });
    expect(save.hasAttribute("disabled")).toBe(true);
    // Plan It installments are read only.
    expect(d.getByText(/Set by your plans\./)).toBeTruthy();
    expect(d.queryByLabelText("Plan It installments")).toBeNull();
    expect(d.getByText("Spent $800 so far")).toBeTruthy();
    expect(d.getAllByText("Chat planned $3,650.").length).toBe(1);

    const field = d.getByLabelText("Lifestyle");
    expect(field.getAttribute("inputmode")).toBe("decimal");
    await user.clear(field);
    await user.type(field, "$3,400");
    expect(footer()).toBe("Total $8,350 · chat planned $8,600 · Left over $2,650");
    expect(save.hasAttribute("disabled")).toBe(false);
    await user.click(d.getByRole("checkbox", { name: "Also for Nov and Dec (planned by the chat)" }));

    const gets = urls().length;
    await user.click(save);
    expect(await d.findByText("Saved.")).toBeTruthy();
    expect(puts()).toEqual([
      { month: "2026-10", changes: [{ group_key: "lifestyle", target_cents: 340_000, seen_chat_cents: 365_000 }], also_later: true },
    ]);
    // The page refetched, so the table and the form show the saved target.
    expect(urls().length).toBeGreaterThan(gets);
    expect((d.getByLabelText("Lifestyle") as HTMLInputElement).value).toBe("3,400");
    expect(d.getByRole("button", { name: "Save" }).hasAttribute("disabled")).toBe(true);
    await user.click(d.getByRole("button", { name: "Close" }));
    await waitFor(() => expect(within(row("Lifestyle")).getAllByText("Edited")).toHaveLength(2));
  });

  it("offers a group the chat left out and saves its first target", async () => {
    oct = BUDGET_OCT_EDIT;
    const { user, drawer } = await openEditor();
    const d = within(drawer);
    // Kids has no row on the page (no chat target, nothing spent), but the drawer offers it.
    expect(screen.queryByRole("rowheader", { name: /Kids and education/ })).toBeNull();
    const field = d.getByLabelText("Kids and education");
    expect((field as HTMLInputElement).value).toBe("");
    expect(d.getByText("Spent $0 so far")).toBeTruthy();
    expect(d.getAllByText("The chat sent no target.").length).toBeGreaterThan(0);
    await user.type(field, "150");
    expect(footer()).toBe("Total $8,750 · chat planned $8,600 · Left over $2,250");
    await user.click(d.getByRole("button", { name: "Save" }));
    expect(await d.findByText("Saved.")).toBeTruthy();
    expect(puts()).toEqual([
      { month: "2026-10", changes: [{ group_key: "kids", target_cents: 15_000, seen_chat_cents: null }], also_later: false },
    ]);
  });

  it("sends null for Use the chat's", async () => {
    oct = withLifestyleEdit(BUDGET_OCT_EDIT);
    putAnswer = { status: 200, body: BUDGET_OCT_EDIT };
    const { user, drawer } = await openEditor();
    const d = within(drawer);
    expect(d.getByText(/Changed by Sam Example on Oct 20, 2026\./)).toBeTruthy();
    expect(footer()).toBe("Total $8,350 · chat planned $8,600 · Left over $2,650");
    await user.click(d.getByRole("button", { name: "Use the chat's for Lifestyle" }));
    expect((d.getByLabelText("Lifestyle") as HTMLInputElement).value).toBe("3,650");
    expect(d.getByText("Uses the chat's number when you save.")).toBeTruthy();
    expect(footer()).toBe("Total $8,600 · chat planned $8,600 · Left over $2,400");
    await user.click(d.getByRole("button", { name: "Save" }));
    expect(await d.findByText("Saved.")).toBeTruthy();
    expect(puts()).toEqual([
      { month: "2026-10", changes: [{ group_key: "lifestyle", target_cents: null, seen_chat_cents: 365_000 }], also_later: false },
    ]);
  });

  it("reviews a conflict and Keep mine resends the same target", async () => {
    oct = withLifestyleEdit(BUDGET_OCT_EDIT, 380_000);
    putAnswer = { status: 200, body: { ...oct, conflicts: 0 } };
    const user = userEvent.setup();
    renderWithProviders(<BudgetPage />, { route: "/budget?month=2026-10" });
    expect(await screen.findByText("The chat changed 1 target since your edit.")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Review" }));
    const drawer = await screen.findByRole("dialog", { name: "Edit targets for Oct 2026" });
    const d = within(drawer);
    // Opens at the group the chat changed.
    await waitFor(() => expect(document.activeElement).toBe(d.getByLabelText("Lifestyle")));
    expect(d.getByText("The chat changed Lifestyle to $3,800 since your edit.")).toBeTruthy();
    // A screen reader hears why it landed there.
    expect(d.getByLabelText("Lifestyle").getAttribute("aria-describedby")).toContain("budget-target-lifestyle-conflict");
    await user.click(d.getByRole("button", { name: "Keep mine for Lifestyle" }));
    expect(d.getByText("Keeps yours when you save.")).toBeTruthy();
    await user.click(d.getByRole("button", { name: "Save" }));
    expect(await d.findByText("Saved.")).toBeTruthy();
    expect(puts()).toEqual([
      { month: "2026-10", changes: [{ group_key: "lifestyle", target_cents: 340_000, seen_chat_cents: 380_000 }], also_later: false },
    ]);
  });

  it("shows the Edited tag as text, and the phone line opens the drawer at that group", async () => {
    oct = withLifestyleEdit(BUDGET_OCT_EDIT);
    const user = userEvent.setup();
    renderWithProviders(<BudgetPage />, { route: "/budget?month=2026-10" });
    await monthHeading("Oct 2026");
    const life = await waitFor(() => row("Lifestyle"));
    const [wide, phone] = within(life).getAllByText("Edited");
    // From 640px up in the Plan cell, on phones in the "of $X" line.
    expect(wide!.closest("td")!.className).toContain("hidden sm:table-cell");
    const opener = within(life).getByRole("button", { name: "of $3,400 Edited. Edit the Lifestyle target." });
    expect(opener.contains(phone!)).toBe(true);
    expect(opener.className).toContain("sm:hidden");
    expect(opener.className).toContain("tap-target");
    expect(within(row("Housing and utilities")).queryByText("Edited")).toBeNull();
    await user.click(opener);
    const drawer = await screen.findByRole("dialog", { name: "Edit targets for Oct 2026" });
    await waitFor(() => expect(document.activeElement).toBe(within(drawer).getByLabelText("Lifestyle")));
  });

  it.each([
    [403, "Changes are off in demo."],
    [403, "Only an admin can do this."],
    [409, "The chat's plan changed. Look again."],
  ])("shows a %i in place", async (status, detail) => {
    oct = BUDGET_OCT_EDIT;
    putAnswer = { status, body: { detail } };
    const { user, drawer } = await openEditor();
    const d = within(drawer);
    await user.clear(d.getByLabelText("Lifestyle"));
    await user.type(d.getByLabelText("Lifestyle"), "3400");
    const gets = urls().length;
    await user.click(d.getByRole("button", { name: "Save" }));
    expect((await d.findByRole("alert")).textContent).toBe(detail);
    expect(d.queryByText("Saved.")).toBeNull();
    // What was typed stays. A 409 refetches the page for the chat's new numbers.
    expect((d.getByLabelText("Lifestyle") as HTMLInputElement).value).toBe("3400");
    if (status === 409) await waitFor(() => expect(urls().length).toBeGreaterThan(gets));
    else expect(urls().length).toBe(gets);
  });

  it("shows the usual amount error and sends nothing", async () => {
    oct = BUDGET_OCT_EDIT;
    const { user, drawer } = await openEditor();
    const d = within(drawer);
    const field = d.getByLabelText("Lifestyle");
    await user.clear(field);
    await user.type(field, "12.345");
    // A bad amount leaves the footer as it was.
    expect(footer()).toBe("Total $8,600 · chat planned $8,600 · Left over $2,400");
    await user.click(d.getByRole("button", { name: "Save" }));
    expect(d.getByText("Use dollars and cents, like 12.00.")).toBeTruthy();
    expect(field.getAttribute("aria-invalid")).toBe("true");
    expect(d.getByRole("alert").textContent).toBe("Fix the amounts marked above.");
    expect(puts()).toEqual([]);
    // Fixing the amount clears the problem line.
    await user.clear(field);
    await user.type(field, "3400");
    expect(d.queryByRole("alert")).toBeNull();
  });

  it("keeps focus on the row when Use the chat's, Keep mine or Undo swap buttons", async () => {
    oct = withLifestyleEdit(BUDGET_OCT_EDIT);
    const { user, drawer } = await openEditor();
    const d = within(drawer);
    d.getByRole("button", { name: "Use the chat's for Lifestyle" }).focus();
    await user.keyboard("{Enter}");
    expect(document.activeElement).toBe(d.getByRole("button", { name: "Undo for Lifestyle" }));
    await user.keyboard("{Enter}");
    expect(document.activeElement).toBe(d.getByRole("button", { name: "Use the chat's for Lifestyle" }));
  });

  it("keeps focus on a conflict's choices too", async () => {
    oct = withLifestyleEdit(BUDGET_OCT_EDIT, 380_000);
    const { user, drawer } = await openEditor();
    const d = within(drawer);
    d.getByRole("button", { name: "Keep mine for Lifestyle" }).focus();
    await user.keyboard("{Enter}");
    expect(document.activeElement).toBe(d.getByRole("button", { name: "Undo for Lifestyle" }));
    await user.keyboard("{Enter}");
    expect(document.activeElement).toBe(d.getByRole("button", { name: "Keep mine for Lifestyle" }));
  });

  it("copies the edited targets to the later months with only the box ticked", async () => {
    oct = withLifestyleEdit(BUDGET_OCT_EDIT);
    putAnswer = { status: 200, body: oct };
    const { user, drawer } = await openEditor();
    const d = within(drawer);
    const save = d.getByRole("button", { name: "Save" });
    expect(save.hasAttribute("disabled")).toBe(true);
    await user.click(d.getByRole("checkbox", { name: "Also for Nov and Dec (planned by the chat)" }));
    expect(d.getByText("Save copies your edited target to Nov and Dec.")).toBeTruthy();
    expect(save.hasAttribute("disabled")).toBe(false);
    await user.click(save);
    expect(await d.findByText("Saved.")).toBeTruthy();
    expect(puts()).toEqual([
      { month: "2026-10", changes: [{ group_key: "lifestyle", target_cents: 340_000, seen_chat_cents: 365_000 }], also_later: true },
    ]);
  });

  it("stays open and says why when the month has no plan after all", async () => {
    oct = BUDGET_OCT_EDIT;
    const note = "The chat has not sent a plan for this month yet.";
    putAnswer = { status: 409, body: { detail: note } };
    nextOct = {
      ...BUDGET_OCT_EDIT,
      can_edit: false,
      edit_note: note,
      later_months: [],
      addable_groups: [],
      groups: BUDGET_OCT_EDIT.groups.map((g) => ({ ...g, editable: false })),
    };
    const { user, drawer } = await openEditor();
    const d = within(drawer);
    await user.clear(d.getByLabelText("Lifestyle"));
    await user.type(d.getByLabelText("Lifestyle"), "3400");
    await user.click(d.getByRole("button", { name: "Save" }));
    // The refetch turns editing off: the drawer and the message stay.
    await waitFor(() => expect(d.queryByLabelText("Lifestyle")).toBeNull());
    expect(screen.getByRole("dialog", { name: "Edit targets for Oct 2026" })).toBe(drawer);
    expect(d.getByRole("alert").textContent).toBe(note);
    expect(d.getByRole("button", { name: "Save" }).hasAttribute("disabled")).toBe(true);
    expect(drawer.contains(document.activeElement)).toBe(true);
    expect(puts()).toHaveLength(1);
  });

  it("points out a chat number that moved while typing before it saves", async () => {
    oct = BUDGET_OCT_EDIT;
    const stale = "The chat's plan changed. Look again.";
    putAnswer = { status: 409, body: { detail: stale } };
    // The chat's new run plans $3,800 for Lifestyle.
    nextOct = {
      ...BUDGET_OCT_EDIT,
      groups: BUDGET_OCT_EDIT.groups.map((g) => (g.key === "lifestyle" ? { ...g, plan_cents: 380_000, chat_plan_cents: 380_000 } : g)),
    };
    const { user, drawer } = await openEditor();
    const d = within(drawer);
    const field = d.getByLabelText("Lifestyle");
    await user.clear(field);
    await user.type(field, "3400");
    await user.click(d.getByRole("button", { name: "Save" }));
    expect((await d.findByRole("alert")).textContent).toBe(stale);
    // The refetch shows the new number. What was typed stays, and the row says so.
    expect(await d.findByText("The chat now plans $3,800.")).toBeTruthy();
    expect((d.getByLabelText("Lifestyle") as HTMLInputElement).value).toBe("3400");
    expect(d.getByLabelText("Lifestyle").getAttribute("aria-describedby")).toContain("budget-target-lifestyle-moved");
    // The first Save stops once to point it out. It sends nothing.
    await user.click(d.getByRole("button", { name: "Save" }));
    expect(d.getByRole("alert").textContent).toBe(MOVED_TEXT);
    expect(puts()).toHaveLength(1);
    // The next goes, with the chat's new number.
    putAnswer = { status: 200, body: withLifestyleEdit(BUDGET_OCT_EDIT, 380_000) };
    nextOct = null;
    await user.click(d.getByRole("button", { name: "Save" }));
    expect(await d.findByText("Saved.")).toBeTruthy();
    expect(puts()[1]).toEqual({
      month: "2026-10",
      changes: [{ group_key: "lifestyle", target_cents: 340_000, seen_chat_cents: 380_000 }],
      also_later: false,
    });
  });
});
