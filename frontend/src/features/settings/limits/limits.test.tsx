import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "../../../test/render";
import { CONTRIBUTIONS } from "../../investments/contributions/fixtures";
import { LimitsSettingsPage } from "./LimitsSettingsPage";

// Fictional people and numbers only.

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

let role: "admin" | "member" = "admin";
const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  if (url === "/api/auth/me") return json(200, { id: 1, username: "example", display_name: "Example", role });
  if (url.startsWith("/api/contributions?year=2027")) return json(200, { ...CONTRIBUTIONS, year: 2027, limits: [], limits_found: false });
  if (url.startsWith("/api/contributions/people/") && init?.method === "PUT") return json(200, JSON.parse(String(init.body)));
  if (url.startsWith("/api/contributions")) return json(200, CONTRIBUTIONS);
  if (url === "/api/contribution-limits" && init?.method === "PUT") {
    const body = JSON.parse(String(init.body));
    return json(200, { ...CONTRIBUTIONS.limits[0], ...body, confirmed: body.confirm });
  }
  return json(404, { detail: "Not found." });
});

function puts(path: string) {
  return fetchMock.mock.calls.filter(([u, init]) => String(u).startsWith(path) && init?.method === "PUT").map(([, init]) => JSON.parse(String(init!.body)));
}

beforeEach(() => {
  role = "admin";
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function row(label: string): HTMLElement {
  return screen.getByLabelText(label).closest("li")!;
}

describe("Settings > Contribution limits", () => {
  it("lists every limit with its state", async () => {
    renderWithProviders(<LimitsSettingsPage />);
    await screen.findByLabelText("401k employee limit");
    expect((screen.getByLabelText("401k employee limit") as HTMLInputElement).value).toBe("24,500");
    expect(within(row("401k employee limit")).getByText("Not confirmed")).toBeTruthy();
    expect(within(row("HSA, family coverage")).getByText("Confirmed")).toBeTruthy();
    expect(within(row("HSA, self-only coverage")).getByText("Not set")).toBeTruthy();
    expect(screen.getByText("2 limits are not confirmed yet.")).toBeTruthy();
  });

  it("confirms a limit as it is", async () => {
    const user = userEvent.setup();
    renderWithProviders(<LimitsSettingsPage />);
    await screen.findByLabelText("401k employee limit");
    await user.click(await within(row("401k employee limit")).findByRole("button", { name: "Confirm 401k employee limit" }));
    await waitFor(() => expect(puts("/api/contribution-limits")).toHaveLength(1));
    expect(puts("/api/contribution-limits")[0]).toEqual({ year: 2026, kind: "k401_deferral", limit_cents: 2_450_000, confirm: true });
  });

  it("saves an edited limit, which also confirms it", async () => {
    const user = userEvent.setup();
    renderWithProviders(<LimitsSettingsPage />);
    const input = await screen.findByLabelText("401k employee limit");
    await user.clear(input);
    await user.type(input, "25,000");
    expect(within(row("401k employee limit")).getByText("Was $24,500. Saving also confirms it.")).toBeTruthy();
    await user.click(await within(row("401k employee limit")).findByRole("button", { name: "Save 401k employee limit" }));
    await waitFor(() => expect(puts("/api/contribution-limits")[0]).toMatchObject({ limit_cents: 2_500_000, confirm: true }));
  });

  it("confirms all the shown amounts at once", async () => {
    const user = userEvent.setup();
    renderWithProviders(<LimitsSettingsPage />);
    await user.click(await screen.findByRole("button", { name: "Confirm all 2 as shown" }));
    await waitFor(() => expect(puts("/api/contribution-limits").map((b) => b.kind)).toEqual(["k401_deferral", "k401_catchup_50"]));
  });

  it("can enter next year's limits", async () => {
    const user = userEvent.setup();
    renderWithProviders(<LimitsSettingsPage />);
    await screen.findByLabelText("401k employee limit");
    await user.click(screen.getByRole("radio", { name: "2027" }));
    await screen.findByText("No 2027 limits yet. Enter them when the IRS publishes them.");
    const input = screen.getByLabelText("401k employee limit");
    await user.type(input, "25,000");
    await user.click(await within(row("401k employee limit")).findByRole("button", { name: "Save 401k employee limit" }));
    await waitFor(() => expect(puts("/api/contribution-limits")[0]).toEqual({ year: 2027, kind: "k401_deferral", limit_cents: 2_500_000, confirm: true }));
  });

  it("saves a person's catch-up, coverage and lead time", async () => {
    const user = userEvent.setup();
    renderWithProviders(<LimitsSettingsPage />);
    await screen.findByText("Riley");
    await user.click(screen.getByRole("radio", { name: "50 and over" }));
    await user.selectOptions(screen.getByLabelText("Paychecks before an election change takes effect"), "2");
    await user.click(screen.getByRole("button", { name: "Save Riley" }));
    await waitFor(() =>
      expect(puts("/api/contributions/people/example-person")[0]).toEqual({
        k401_catch_up: "50",
        hsa_coverage: "family",
        hsa_catch_up: false,
        election_lead_checks: 2,
      }),
    );
  });

  it("is read only for a member", async () => {
    role = "member";
    renderWithProviders(<LimitsSettingsPage />);
    await screen.findByLabelText("401k employee limit");
    await waitFor(() => expect((screen.getByLabelText("401k employee limit") as HTMLInputElement).disabled).toBe(true));
    expect(screen.queryByRole("button", { name: /Confirm|Save/ })).toBeNull();
  });
});
