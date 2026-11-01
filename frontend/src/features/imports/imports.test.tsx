import { screen, within } from "@testing-library/react";
import { Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ImportRun, ImportRunDetail } from "../../lib/api";
import { renderWithProviders } from "../../test/render";
import { ImportDetailPage, RunsTable } from "./ImportsPage";

const RUN: ImportRun = {
  run_id: "2026-10-12",
  mode: "monthly",
  schema_version: "1.2",
  status: "failed",
  started_at: "2026-10-12T14:00:00Z",
  finished_at: "2026-10-12T14:01:00Z",
  counts: { inserted: 3 },
  warning_count: 1,
  error: { loc: "state_sections.rewards.programs[0].allocations[0].pulled_forward_micro", rule: "type", message: "Not a number." },
};

const DETAIL: ImportRunDetail = {
  ...RUN,
  counts: { inserted: 3, superseded_statement_rows: 2 },
  warnings: [{ loc: "transactions[4]", rule: "merchant", message: "No merchant." }],
  statements: [{ statement_key: "s1", account_key: "example-card", period_end: "2026-09-30", is_current: true, txn_count: 12, reconciled: true }],
};

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input);
  if (url === "/api/auth/me") return json(200, { username: "sam", display_name: "Sam", role: "member" });
  if (url === "/api/imports/2026-10-12") return json(200, DETAIL);
  return json(404, { detail: "Not found." });
});

beforeEach(() => {
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Imports", () => {
  it("lists runs in a labeled region that scrolls by keyboard, with a tall run link", () => {
    renderWithProviders(<RunsTable runs={[RUN]} />);
    const region = screen.getByRole("region", { name: "Imports" });
    expect(region.tabIndex).toBe(0);
    expect(within(region).getByRole("table", { name: "Import runs, newest first" })).toBeTruthy();
    const link = within(region).getByRole("link", { name: "2026-10-12" });
    expect(link.getAttribute("href")).toBe("/statements/2026-10-12");
    expect(link.className).toContain("min-h-11");
  });

  it("wraps long error paths and count keys, and labels each table region", async () => {
    renderWithProviders(
      <Routes>
        <Route path="/statements/:runId" element={<ImportDetailPage />} />
      </Routes>,
      { route: "/statements/2026-10-12" },
    );
    const error = await screen.findByText(/Not a number\./);
    expect(error.closest("dd")!.className).toContain("wrap-anywhere");
    const counts = screen.getByRole("region", { name: "Counts" });
    expect(within(counts).getByRole("rowheader", { name: "superseded_statement_rows" }).className).toContain("wrap-anywhere");
    for (const name of ["Counts", "Warnings", "Statements in this run"]) {
      expect(screen.getByRole("region", { name }).tabIndex).toBe(0);
    }
  });
});
