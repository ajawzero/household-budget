import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "../../../test/render";
import { InvestmentsPage } from "../InvestmentsPage";
import type { ContributionsOut } from "./api";
import { adviceText, ContributionsTab } from "./ContributionsTab";
import { CONTRIBUTIONS, HSA, K401, OTHER } from "./fixtures";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

let data: ContributionsOut = CONTRIBUTIONS;
let fail = false;
const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input);
  if (url.startsWith("/api/contributions")) return fail ? json(500, { detail: "Server error." }) : json(200, data);
  return json(404, { detail: "Not found." });
});

beforeEach(() => {
  data = CONTRIBUTIONS;
  fail = false;
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Contributions tab", () => {
  it("shows each plan against its limit with a meter", async () => {
    renderWithProviders(<ContributionsTab />);
    const k401 = await screen.findByRole("region", { name: "Riley, 401k" });
    expect(within(k401).getByRole("img").getAttribute("aria-label")).toBe(
      "Riley, 401k: $18,000 so far, $24,300 projected by Dec 31, limit $24,500",
    );
    expect(within(k401).getByText("On pace for $24,300 by Dec 31, $200 under the limit.")).toBeTruthy();
    expect(within(k401).getByText("Room left").nextSibling?.textContent).toBe("$6,500");
    expect(within(k401).getByText("Per paycheck").nextSibling?.textContent).toBe("$900 (10.0% of gross)");
    expect(within(k401).getByText("Limit not confirmed")).toBeTruthy();
    expect(within(k401).getByText("Change on the Nov 6 paycheck")).toBeTruthy();
  });

  it("explains the per-check cross-check and the prior employer part", async () => {
    renderWithProviders(<ContributionsTab />);
    const k401 = await screen.findByRole("region", { name: "Riley, 401k" });
    expect(
      within(k401).getByText(
        "Pay stub total is $100.00 more than the 19 paychecks added up. A missing stub or a payroll fix can do this.",
      ),
    ).toBeTruthy();
    expect(within(k401).getByText("Includes $1,000 from a prior employer.")).toBeTruthy();
  });

  it("says an over-limit plan is over, in words", async () => {
    renderWithProviders(<ContributionsTab />);
    const hsa = await screen.findByRole("region", { name: "Riley, HSA" });
    expect(within(hsa).getByText("On pace for $10,400 by Dec 31. That is $1,650 over the limit.")).toBeTruthy();
    expect(within(hsa).getByText("Room left").nextSibling?.textContent).toBe("$250 over");
    expect(within(hsa).getByText("Family, shared")).toBeTruthy();
    expect(within(hsa).queryByText("Limit not confirmed")).toBeNull();
    expect(screen.getByRole("heading", { name: "HSA, family limit" })).toBeTruthy();
  });

  it("flags unconfirmed limits and links to Settings", async () => {
    renderWithProviders(<ContributionsTab />);
    await screen.findByText("The 2026 limits are the IRS figures the app started with. They are not confirmed yet.");
    expect(screen.getByRole("link", { name: "Check and confirm" }).getAttribute("href")).toBe("/settings/limits");
    expect(screen.getByRole("link", { name: "Set age and coverage" })).toBeTruthy();
  });

  it("hides the notice when every limit is confirmed", async () => {
    data = { ...CONTRIBUTIONS, limits: CONTRIBUTIONS.limits.map((l) => ({ ...l, confirmed: true })) };
    renderWithProviders(<ContributionsTab />);
    await screen.findByRole("region", { name: "Riley, 401k" });
    expect(screen.queryByText(/not confirmed yet/)).toBeNull();
  });

  it("says when there is nothing yet", async () => {
    data = { ...CONTRIBUTIONS, people: [], hsa_family: null, limits_found: false, limits: [] };
    renderWithProviders(<ContributionsTab />);
    await screen.findByText(/No 401k or HSA contributions for 2026 yet/);
    expect(screen.getByRole("link", { name: "Add limits" })).toBeTruthy();
  });

  it("lists IRA and 529 money with gifts split out", async () => {
    data = { ...CONTRIBUTIONS, other: OTHER };
    renderWithProviders(<ContributionsTab />);
    const title = await screen.findByRole("heading", { name: "IRA and 529" });
    const card = title.closest("section")!;
    expect(within(card).getByText("Money put in during 2026. The app has no limits for these.")).toBeTruthy();
    const items = within(card).getAllByRole("listitem");
    expect(items).toHaveLength(2);
    expect(items[0]!.textContent).toContain("Example 529");
    expect(within(items[0]!).getByText("Riley · 529")).toBeTruthy();
    expect(within(items[0]!).getByText("$2,550")).toBeTruthy();
    expect(within(items[0]!).getByText("11 deposits, last $200 on Sep 5.")).toBeTruthy();
    expect(within(items[0]!).getByText("Includes $750 from gifts.")).toBeTruthy();
    expect(within(items[1]!).getByText("1 deposit, last $1,000 on Apr 1.")).toBeTruthy();
    expect(items[1]!.textContent).not.toContain("from gifts");
  });

  it("shows IRA and 529 money even with no 401k or HSA", async () => {
    data = { ...CONTRIBUTIONS, people: [], hsa_family: null, other: OTHER };
    renderWithProviders(<ContributionsTab />);
    await screen.findByText(/No 401k or HSA contributions for 2026 yet/);
    expect(screen.getByRole("heading", { name: "IRA and 529" })).toBeTruthy();
  });

  it("has no IRA and 529 card without that money", async () => {
    renderWithProviders(<ContributionsTab />);
    await screen.findByRole("region", { name: "Riley, 401k" });
    expect(screen.queryByRole("heading", { name: "IRA and 529" })).toBeNull();
  });

  it("shows one error with Retry", async () => {
    fail = true;
    renderWithProviders(<ContributionsTab />);
    await screen.findByText("Could not load contributions.");
    expect(screen.getByRole("button", { name: /Retry/ })).toBeTruthy();
  });

  it("is a tab on Investments", async () => {
    renderWithProviders(<InvestmentsPage />, { route: "/investments?tab=contributions" });
    expect(screen.getByRole("tab", { name: "Contributions" }).getAttribute("aria-selected")).toBe("true");
    await screen.findByRole("region", { name: "Riley, 401k" });
  });
});

describe("adviceText", () => {
  it("names the paycheck and the amount to reach the limit", () => {
    expect(adviceText(K401)).toBe(
      "To reach the limit, raise the election to $940 a paycheck (10.4% of gross) starting with the Nov 6, 2026 paycheck. 2 paychecks at the current election come first, then 5 paychecks at the new one.",
    );
  });

  it("says to lower an over-limit HSA, for the employee part", () => {
    expect(adviceText(HSA)).toMatch(/^To stay at the limit, lower the employee part to \$0 a paycheck starting with the Oct 23, 2026 paycheck\./);
  });

  it("never advises more than 100% of gross", () => {
    const text = adviceText({ ...K401, needed_pct: "103.9", room_cents: 6_000_000 });
    expect(text).toBe(
      "The limit is out of reach this year with the paychecks left. Even all of the gross pay from the Nov 6, 2026 paycheck on adds about $46,800, so about $13,200 of room stays unused. Raise the election as far as payroll allows, or plan to max out next year.",
    );
    expect(text).not.toMatch(/103/);
  });

  it("covers the other states", () => {
    expect(adviceText({ ...K401, status: "at_limit" })).toBe("No change needed. The current election lands on the limit.");
    expect(adviceText({ ...K401, change_on: null })).toBe("Too few paychecks are left to change the election this year.");
    expect(adviceText({ ...K401, status: "no_limit" })).toBeNull();
    expect(adviceText({ ...K401, checks_before_change: 1, checks_from_change: 1 })).toMatch(
      /1 paycheck at the current election comes first, then 1 paycheck at the new one\./,
    );
  });
});
