import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { GoalAccount, GoalItem, GoalsResponse } from "../../lib/api";
import { AppRoutes } from "../../routes/AppRoutes";
import { renderWithProviders } from "../../test/render";
import { GoalsPage, goalMeta } from "./GoalsPage";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function account(over: Partial<GoalAccount> = {}): GoalAccount {
  return {
    account_key: "kids-529",
    display_name: "Kids 529 (Bluestem)",
    balance_cents: 2_189_000,
    as_of: "2026-09-30",
    stale: false,
    missing: false,
    estimate: null,
    ...over,
  };
}

const COLLEGE: GoalItem = {
  key: "college-jamie",
  name: "College, older child",
  kind: "college",
  person_name: "Jamie",
  start_year: 2034,
  target_date: null,
  note: null,
  target_cents: 12_000_000,
  now_cents: 2_189_000,
  to_go_cents: 9_811_000,
  reached: false,
  as_of: "2026-09-30",
  stale: false,
  accounts: [account()],
};

const FUND: GoalItem = {
  key: "emergency-fund",
  name: "Emergency fund",
  kind: "save_up",
  person_name: null,
  start_year: null,
  target_date: "2027-12-31",
  note: "Six months of bills",
  target_cents: 3_000_000,
  now_cents: 1_384_600,
  to_go_cents: 1_615_400,
  reached: false,
  as_of: "2026-09-30",
  stale: false,
  accounts: [account({ account_key: "harbor-savings", display_name: "Harbor Savings", balance_cents: 1_384_600 })],
};

const PAGE: GoalsResponse = { goals: [COLLEGE, FUND], source_run_id: "2026-10-12" };

let body: GoalsResponse = PAGE;
let fail = false;
const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input);
  if (url === "/api/auth/me") return json(200, { username: "sam", display_name: "Sam", role: "member" });
  if (url === "/api/goals") return fail ? json(500, { detail: "Server error." }) : json(200, body);
  return json(404, { detail: "Not found." });
});

beforeEach(() => {
  body = PAGE;
  fail = false;
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function card(name: string): HTMLElement {
  const heading = screen.getByRole("heading", { level: 2, name });
  const li = heading.closest("li");
  if (!li) throw new Error(`no card for ${name}`);
  return li;
}

describe("goalMeta", () => {
  it("joins the parts it has", () => {
    expect(goalMeta(COLLEGE)).toBe("College · For Jamie · Starts 2034");
    expect(goalMeta(FUND)).toBe("Save up · By Dec 31, 2027");
    expect(goalMeta({ ...FUND, target_date: null })).toBe("Save up");
  });
});

describe("GoalsPage", () => {
  it("lists each goal with now, target, to go and its accounts", async () => {
    renderWithProviders(<GoalsPage />, { route: "/goals" });
    const list = await screen.findByRole("list", { name: "Goals" });
    expect(within(list).getAllByRole("heading", { level: 2 }).map((h) => h.textContent)).toEqual([
      "College, older child",
      "Emergency fund",
    ]);
    const college = card("College, older child");
    expect(within(college).getByText("College · For Jamie · Starts 2034")).toBeTruthy();
    for (const label of ["Now", "Target", "To go"]) expect(within(college).getByText(label)).toBeTruthy();
    expect(within(college).getByText("$21,890")).toBeTruthy();
    expect(within(college).getByText("$120,000")).toBeTruthy();
    expect(within(college).getByText("$98,110")).toBeTruthy();
    expect(within(college).getByText("as of Sep 30")).toBeTruthy();
    expect(within(college).queryByText("est.")).toBeNull();
    expect(within(college).getByRole("link", { name: "Kids 529 (Bluestem)" }).getAttribute("href")).toBe(
      "/accounts?account=kids-529",
    );

    const fund = card("Emergency fund");
    expect(within(fund).getByText("Save up · By Dec 31, 2027")).toBeTruthy();
    expect(within(fund).getByText("Six months of bills")).toBeTruthy();
    expect(within(fund).getByText("$13,846")).toBeTruthy();
    expect(within(fund).getByText("$16,154")).toBeTruthy();
    expect(within(fund).getByRole("link", { name: "Harbor Savings" }).getAttribute("href")).toBe(
      "/accounts?account=harbor-savings",
    );

    expect(screen.getByText("Now adds up what each goal's accounts hold today.")).toBeTruthy();
    expect(screen.getByText(/From the Oct 12, 2026 update\./)).toBeTruthy();
  });

  it("shows Reached and no amount to go", async () => {
    body = { ...PAGE, goals: [{ ...FUND, now_cents: 3_100_000, to_go_cents: 0, reached: true }] };
    renderWithProviders(<GoalsPage />, { route: "/goals" });
    const fund = await screen.findByRole("listitem");
    expect(within(fund).getByText("Reached")).toBeTruthy();
    expect(within(fund).queryByText("$0")).toBeNull();
  });

  it("says which accounts have no balance yet, or that none do", async () => {
    const riley = account({
      account_key: "riley-custodial",
      display_name: "Riley custodial (Bluestem)",
      balance_cents: null,
      as_of: null,
      missing: true,
    });
    body = {
      ...PAGE,
      goals: [
        { ...COLLEGE, accounts: [account(), riley] },
        {
          ...FUND,
          now_cents: null,
          to_go_cents: null,
          as_of: null,
          accounts: [account({ ...riley, account_key: "harbor-savings", display_name: "Harbor Savings" })],
        },
      ],
    };
    renderWithProviders(<GoalsPage />, { route: "/goals" });
    await screen.findByRole("list", { name: "Goals" });
    const college = card("College, older child");
    expect(
      within(college).getByText("No balance yet for Riley custodial (Bluestem). Now leaves it out."),
    ).toBeTruthy();
    expect(within(college).getAllByRole("link").map((a) => a.textContent)).toEqual([
      "Kids 529 (Bluestem)",
      "Riley custodial (Bluestem)",
    ]);
    const fund = card("Emergency fund");
    expect(within(fund).getByText("No balance")).toBeTruthy();
    expect(within(fund).getByText("Unknown")).toBeTruthy();
    expect(within(fund).queryByText(/No balance yet for/)).toBeNull();
  });

  it("says a stale balance in words", async () => {
    body = { ...PAGE, goals: [{ ...COLLEGE, stale: true }] };
    renderWithProviders(<GoalsPage />, { route: "/goals" });
    expect(await screen.findByText("· A newer statement is overdue.")).toBeTruthy();
  });

  it("marks an estimate and says its note in words", async () => {
    const note = "Estimated from prices of Oct 17, 2026, from the Sep 30 statement.";
    const estimate = { kind: "price", based_on: "2026-09-30", through: "2026-10-17", note };
    body = { ...PAGE, goals: [{ ...COLLEGE, accounts: [account({ estimate })] }] };
    renderWithProviders(<GoalsPage />, { route: "/goals" });
    const college = await screen.findByRole("listitem");
    expect(within(college).getByText("est.")).toBeTruthy();
    expect(within(college).getByText(note)).toBeTruthy();
  });

  it("lists every estimated account when there are more", async () => {
    const estimate = { kind: "price", based_on: "2026-09-30", through: "2026-10-17", note: "One." };
    const other = account({ account_key: "kids-529-b", display_name: "Kids 529 B", estimate });
    body = { ...PAGE, goals: [{ ...COLLEGE, accounts: [account({ estimate }), other] }] };
    renderWithProviders(<GoalsPage />, { route: "/goals" });
    const college = await screen.findByRole("listitem");
    expect(
      within(college).getByText(
        "Includes estimates: Kids 529 (Bluestem) (from Sep 30), Kids 529 B (from Sep 30). Each account's own est. note says how it was made.",
      ),
    ).toBeTruthy();
  });

  it("shows an empty state with no goals", async () => {
    body = { goals: [], source_run_id: null };
    renderWithProviders(<GoalsPage />, { route: "/goals" });
    expect(await screen.findByText("No goals yet.")).toBeTruthy();
    expect(screen.getByText("Goals come with the monthly run from the statement chat.")).toBeTruthy();
    expect(screen.queryByRole("list", { name: "Goals" })).toBeNull();
  });

  it("shows an error with Retry, which loads again", async () => {
    fail = true;
    renderWithProviders(<GoalsPage />, { route: "/goals" });
    expect(await screen.findByText("Could not load goals.")).toBeTruthy();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("list", { name: "Goals" })).toBeTruthy();
    await waitFor(() => expect(screen.queryByText("Could not load goals.")).toBeNull());
  });

  it("is the page at /goals, not a coming-soon page", async () => {
    renderWithProviders(<AppRoutes />, { route: "/goals" });
    expect(await screen.findByRole("list", { name: "Goals" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1, name: "Goals" })).toBeTruthy();
    expect(screen.queryByText("Coming in a later phase")).toBeNull();
  });
});
