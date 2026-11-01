import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "../../test/render";
import { COVERAGE } from "../recurring/fixtures";
import { blockingAccounts, cellText, CoverageCard } from "./CoverageCard";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  if (String(input) === "/api/statements/coverage") return json(200, COVERAGE);
  return json(404, { detail: "Not found." });
});

beforeEach(() => {
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function grid(): string[][] {
  const table = screen.getByRole("table", { name: "Statements by account and month" });
  return within(table)
    .getAllByRole("row")
    .map((tr) => Array.from(tr.children).map((c) => c.textContent ?? ""));
}

describe("Statement coverage grid", () => {
  it("shows each cash flow account by month with the status in words", async () => {
    renderWithProviders(<CoverageCard />);
    await screen.findByRole("table", { name: "Statements by account and month" });
    expect(grid()).toEqual([
      ["Account", "Jul, complete", "Aug, complete", "Sep, not complete", "Oct, not complete"],
      [
        "Example checkingJoint",
        "Statement closed, period ends Jul 31, 2026",
        "Statement closed, period ends Aug 31, 2026, not reconciled",
        "Statement closed, period ends Sep 30, 2026",
        "Not expected",
      ],
      ["Example cardSam", "Statement closed, period ends Jul 10, 2026", "Statement closed, period ends Aug 10, 2026", "Missing", "Not expected"],
      ["Example store cardSam · tracked by payments", "Tracked by payments", "Tracked by payments", "Tracked by payments", "Tracked by payments"],
    ]);
    expect(screen.getByText(/Complete through Aug 2026\./).textContent).toBe("Complete through Aug 2026. Sep waits on Example card.");
    // Keyboard users can scroll back to older months.
    const region = screen.getByRole("region", { name: "Statement coverage" });
    expect(region.tabIndex).toBe(0);
    expect(region.contains(screen.getByRole("table", { name: "Statements by account and month" }))).toBe(true);
  });

  it("adds accounts outside cash flow on request", async () => {
    renderWithProviders(<CoverageCard />);
    await screen.findByRole("table", { name: "Statements by account and month" });
    expect(grid()).toHaveLength(4);
    await userEvent.setup().click(screen.getByRole("radio", { name: "All accounts" }));
    expect(grid()).toHaveLength(5);
    expect(grid()[4]?.[0]).toBe("Example homeJoint");
  });

  it("finds what holds the first incomplete month back", () => {
    expect(blockingAccounts(COVERAGE)).toEqual({ month: "2026-09", names: ["Example card"] });
    expect(blockingAccounts({ ...COVERAGE, complete_months: COVERAGE.months.slice(0, 3) })).toBeNull();
    expect(cellText({ month: "2026-09", status: "missing", period_end: null, reconciled: null })).toBe("Missing");
  });
});
