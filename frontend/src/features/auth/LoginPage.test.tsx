import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MODE_KEY } from "../../lib/demo";
import { replacePage } from "../../lib/reload";
import { renderWithProviders } from "../../test/render";
import { LoginPage } from "./LoginPage";

// A sign-in loads a whole new page; jsdom cannot, so the load is stubbed.
vi.mock("../../lib/reload", () => ({ replacePage: vi.fn(), reloadPage: vi.fn() }));

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const USER = { username: "alex", display_name: "Alex", role: "admin" };

let loginResponse: () => Response;
let meResponse: () => Response;
const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input);
  if (url === "/api/auth/me") return meResponse();
  if (url === "/api/auth/login") return loginResponse();
  return json(404, { detail: "Not found." });
});

function renderLogin() {
  return renderWithProviders(
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={<p>Dashboard page</p>} />
    </Routes>,
    { route: "/login" },
  );
}

describe("LoginPage", () => {
  beforeEach(() => {
    meResponse = () => json(401, { detail: "Not signed in." });
    fetchMock.mockClear();
    vi.mocked(replacePage).mockClear();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("posts the credentials and loads the app again on success", async () => {
    loginResponse = () => json(200, { user: USER });
    const user = userEvent.setup();
    renderLogin();

    await user.type(screen.getByLabelText("Username"), "alex");
    await user.type(screen.getByLabelText("Password"), "example-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(replacePage).toHaveBeenCalledWith("/"));
    // A whole new page, never a move inside this one, and the other tabs hear of it.
    expect(screen.queryByText("Dashboard page")).toBeNull();
    expect(JSON.parse(window.localStorage.getItem(MODE_KEY) ?? "null").mode).toBe("real");
    const call = fetchMock.mock.calls.find(([url]) => String(url) === "/api/auth/login");
    expect(call).toBeDefined();
    const init = (call as unknown as [string, RequestInit])[1];
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    expect(JSON.parse(String(init.body))).toEqual({ username: "alex", password: "example-password" });
  });

  it("shows the server message on a wrong password and keeps the form", async () => {
    loginResponse = () => json(401, { detail: "Wrong username or password." });
    const user = userEvent.setup();
    renderLogin();

    await user.type(screen.getByLabelText("Username"), "alex");
    await user.type(screen.getByLabelText("Password"), "nope");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect((await screen.findByRole("alert")).textContent).toBe("Wrong username or password.");
    expect(screen.queryByText("Dashboard page")).toBeNull();
    await waitFor(() => expect(screen.getByRole("button", { name: "Sign in" })).toHaveProperty("disabled", false));
  });

  it("shows the lockout message on 429", async () => {
    loginResponse = () => json(429, { detail: "Too many attempts. Try again later." });
    const user = userEvent.setup();
    renderLogin();

    await user.type(screen.getByLabelText("Username"), "alex");
    await user.type(screen.getByLabelText("Password"), "nope");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect((await screen.findByRole("alert")).textContent).toBe("Too many attempts. Try again later.");
  });

  it.each([
    ["/accounts?range=1Y", "/accounts?range=1Y"],
    ["//elsewhere.example", "/"],
  ])("loads the page the visitor asked for (%s), same app only", async (from, loaded) => {
    loginResponse = () => json(200, { user: USER });
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={[{ pathname: "/login", state: { from } }]}>
          <LoginPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await user.type(screen.getByLabelText("Username"), "alex");
    await user.type(screen.getByLabelText("Password"), "example-password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(replacePage).toHaveBeenCalledWith(loaded));
  });

  it("keeps the form for a browser still in demo", async () => {
    // A Leave that failed on the network: the demo cookie is still there.
    meResponse = () => json(200, { ...USER, display_name: "Alex", role: "member", demo: true, demo_available: true, household_today: "2026-10-20" });
    renderLogin();
    expect(await screen.findByRole("form", { name: "Sign in" })).toBeTruthy();
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(screen.queryByText("Dashboard page")).toBeNull();
  });

  it("sends a signed-in visitor on to the app", async () => {
    meResponse = () => json(200, { ...USER, demo: false, demo_available: true, household_today: null });
    renderLogin();
    expect(await screen.findByText("Dashboard page")).toBeTruthy();
  });
});
