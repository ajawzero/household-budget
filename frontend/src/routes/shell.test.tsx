import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Target } from "lucide-react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { headerLineText } from "../components/shell/HeaderLine";
import { ComingSoonPage } from "../features/placeholder/ComingSoonPage";
import { renderWithProviders } from "../test/render";
import { AppRoutes } from "./AppRoutes";
import { navItemFor } from "./nav";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const META = {
  app_version: "0.1.0",
  schema_versions: ["1.0"],
  household_today: "2026-10-21",
  latest_run_id: "2026-10-14",
  updated: "2026-10-14",
  this_month: "2026-10",
  complete_as_of: "2026-09-30",
  latest_complete_month: "2026-08",
  location_label: "Example Town",
};

let signedIn = true;
const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input);
  if (url === "/api/auth/me") return signedIn ? json(200, { username: "alex", display_name: "Alex", role: "admin" }) : json(401, {});
  if (url === "/api/meta") return json(200, META);
  return json(404, { detail: "Not found." });
});

beforeEach(() => {
  signedIn = true;
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("shell", () => {
  it("shows the main nav, the More group and the bottom items", async () => {
    renderWithProviders(<AppRoutes />, { route: "/cash-flow" });
    const nav = await screen.findByRole("navigation", { name: "Main" });
    const links = within(nav)
      .getAllByRole("link")
      .map((a) => a.getAttribute("aria-label") ?? a.textContent);
    for (const label of [
      "Dashboard",
      "Accounts",
      "Transactions",
      "Cash Flow",
      "Budget",
      "Recurring",
      "Investments",
      "Goals",
      "Flags & To do",
      "Rewards",
      "Travel",
      "Statements",
      "Settings",
    ]) {
      expect(links).toContain(label);
    }
    // AI status is a 404 here, so Ask stays hidden (PLAN 7.7).
    expect(within(nav).queryByRole("button", { name: /Ask/ })).toBeNull();
  });

  it("shows a calm coming-soon page for unbuilt screens", async () => {
    // No nav item is unbuilt now (Goals came with format 1.4), so the page is tested by itself.
    renderWithProviders(
      <ComingSoonPage item={{ to: "/later", label: "Later", icon: Target, phase: "later", blurb: "Soon." }} />,
    );
    expect(await screen.findByText("Coming in a later phase")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 1, name: "Later" })).toBeTruthy();
    expect(screen.getByText("Soon.")).toBeTruthy();
  });

  it("marks the current page in the nav", async () => {
    renderWithProviders(<AppRoutes />, { route: "/goals" });
    const nav = await screen.findByRole("navigation", { name: "Main" });
    const current = within(nav)
      .getAllByRole("link")
      .filter((a) => a.getAttribute("aria-current") === "page");
    expect(current.map((a) => a.getAttribute("aria-label") ?? a.textContent)).toEqual(["Goals"]);
  });

  it("shows the phone tab bar with More", async () => {
    renderWithProviders(<AppRoutes />, { route: "/" });
    const tabs = await screen.findByRole("navigation", { name: "Tabs" });
    expect(within(tabs).getAllByRole("link").map((a) => a.textContent)).toEqual([
      "Dashboard",
      "Accounts",
      "Transactions",
      "Budget",
    ]);
    expect(within(tabs).getByRole("button", { name: "More" })).toBeTruthy();
  });

  it("marks More as current on a page in the More group", async () => {
    renderWithProviders(<AppRoutes />, { route: "/goals" });
    const tabs = await screen.findByRole("navigation", { name: "Tabs" });
    const more = within(tabs).getByRole("button", { name: "More, current section" });
    expect(more.className).toContain("text-accent-text");
    expect(within(tabs).queryByRole("link", { current: "page" })).toBeNull();
  });

  it("shows the header line from meta when the dashboard is not there yet", async () => {
    renderWithProviders(<AppRoutes />, { route: "/" });
    expect(
      await screen.findByText("Example Town · Updated Oct 14, 2026 · Complete through Aug 2026"),
    ).toBeTruthy();
  });

  it("switches theme from the user menu", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: "/" });
    const nav = await screen.findByRole("navigation", { name: "Main" });
    await user.click(within(nav).getByRole("button", { name: /Alex/ }));
    await user.click(await screen.findByRole("menuitemradio", { name: "Dark" }));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(window.localStorage.getItem("budget.theme")).toBe("dark");
    window.localStorage.clear();
  });

  it("sends a signed-out visitor to the sign-in page", async () => {
    signedIn = false;
    renderWithProviders(<AppRoutes />, { route: "/accounts" });
    expect(await screen.findByRole("form", { name: "Sign in" })).toBeTruthy();
  });

  it("redirects Phase 1 import links to Statements", async () => {
    renderWithProviders(<AppRoutes />, { route: "/imports" });
    expect(await screen.findByRole("heading", { level: 1, name: "Statements" })).toBeTruthy();
  });
});

describe("helpers", () => {
  it("builds the header line, skipping missing parts", () => {
    expect(headerLineText({ location: "Example Town", updated: "2026-10-14", completeThrough: "2026-09" })).toBe(
      "Example Town · Updated Oct 14, 2026 · Complete through Sep 2026",
    );
    expect(headerLineText({ updated: "2026-10-14T12:00:00Z" })).toBe("Updated Oct 14, 2026");
    expect(headerLineText({})).toBe("");
  });

  it("finds the nav item for a path", () => {
    expect(navItemFor("/")?.label).toBe("Dashboard");
    expect(navItemFor("/statements/2026-10-14")?.label).toBe("Statements");
    expect(navItemFor("/nowhere")).toBeUndefined();
  });
});
