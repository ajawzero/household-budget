import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Drawer } from "../../components/ui/drawer";
import { request } from "../../lib/api/client";
import { MODE_CHANNEL, MODE_KEY } from "../../lib/demo";
import { chicagoToday, thisMonth } from "../../lib/format";
import { reloadPage, replacePage } from "../../lib/reload";
import { AppRoutes } from "../../routes/AppRoutes";
import { renderWithProviders } from "../../test/render";
import { todayIso } from "../accounts/vehicles/api";
import { ME_CHECK_MS, TabCover } from "./TabCover";

// Whole-page loads are stubbed: jsdom cannot load a new page.
vi.mock("../../lib/reload", () => ({ replacePage: vi.fn(), reloadPage: vi.fn() }));

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

// The real admin carries a canary name, so a test can tell real data from demo data.
const REAL = { username: "pat", display_name: "Pat Canary", role: "admin", demo: false, demo_available: true, household_today: null };
const DEMO = { username: "sample", display_name: "Alex", role: "member", demo: true, demo_available: true, household_today: "2026-10-20" };
const META = {
  app_version: "0.1.0",
  schema_versions: ["1.0"],
  household_today: "2026-10-20",
  latest_run_id: "2026-10-14",
  updated: "2026-10-14",
  this_month: "2026-10",
  complete_as_of: "2026-09-30",
  latest_complete_month: "2026-08",
  location_label: "Sample Town",
};

let me: object | null;
let routes: Record<string, () => Response | Promise<Response>>;
const calls: string[] = [];
const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const key = `${init?.method ?? "GET"} ${url.split("?")[0]}`;
  calls.push(`${init?.method ?? "GET"} ${url}`);
  const route = routes[key];
  if (route) return route();
  if (url === "/api/auth/me") return me ? json(200, me) : json(401, { detail: "Not signed in." });
  if (url === "/api/meta") return json(200, META);
  return json(404, { detail: "Not found." });
});

const stamp = () => JSON.parse(window.localStorage.getItem(MODE_KEY) ?? "null") as { mode: string; at: number } | null;
const meCalls = () => calls.filter((c) => c === "GET /api/auth/me").length;
/** Lets queued messages and timers run, so a stray reload would have happened. */
const settle = () => act(() => new Promise((done) => setTimeout(done, 50)));
/** The real user's name in the account menu, if the page shows it (covered content is hidden). */
const realName = () => screen.queryByRole("button", { name: /Pat Canary/ });

function setVisibility(state: "hidden" | "visible") {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

beforeEach(() => {
  me = REAL;
  routes = {};
  calls.length = 0;
  fetchMock.mockClear();
  vi.mocked(replacePage).mockClear();
  vi.mocked(reloadPage).mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  // Back to jsdom's own getter.
  delete (document as { visibilityState?: string }).visibilityState;
});

async function demoCard() {
  const heading = await screen.findByRole("heading", { name: "Demo mode" });
  return heading.closest("section") as HTMLElement;
}

describe("Demo mode card", () => {
  it("asks first, blanks the page and loads the app again in demo", async () => {
    routes["POST /api/demo/start"] = () => new Response(null, { status: 204 });
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: "/settings" });
    const card = await demoCard();
    expect(card.textContent).toContain("Shows a fake household. Your data stays hidden.");

    await user.click(within(card).getByRole("button", { name: "Start demo" }));
    expect(calls).not.toContain("POST /api/demo/start");
    expect(within(card).getByText("Start demo in this browser?")).toBeTruthy();
    let coverAtLoad: string | null | undefined;
    vi.mocked(replacePage).mockImplementationOnce(() => {
      coverAtLoad = document.querySelector("[data-page-cover]")?.textContent;
    });
    await user.click(within(card).getByRole("button", { name: "Start demo" }));

    await waitFor(() => expect(replacePage).toHaveBeenCalledWith("/"));
    expect(calls).toContain("POST /api/demo/start");
    // The opaque cover is in the page already when the new page starts to load.
    expect(coverAtLoad).toBe("Starting demo");
    // Its own message must not reload this page over the move to the Dashboard.
    await settle();
    expect(reloadPage).not.toHaveBeenCalled();
    expect(stamp()?.mode).toBe("demo");
  });

  it("says when demo is not available, with no Start button", async () => {
    me = { ...REAL, demo_available: false };
    renderWithProviders(<AppRoutes />, { route: "/settings" });
    const card = await demoCard();
    expect(card.textContent).toContain("Demo is not available on this server.");
    expect(within(card).queryByRole("button", { name: "Start demo" })).toBeNull();
  });

  it("keeps the page and shows the reason when demo cannot start", async () => {
    routes["POST /api/demo/start"] = () => json(503, { detail: "Demo is not available right now." });
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: "/settings" });
    const card = await demoCard();
    await user.click(within(card).getByRole("button", { name: "Start demo" }));
    await user.click(within(card).getByRole("button", { name: "Start demo" }));

    expect((await within(card).findByRole("alert")).textContent).toBe("Demo is not available right now.");
    expect(replacePage).not.toHaveBeenCalled();
    expect(stamp()).toBeNull();
    expect(realName()).not.toBeNull();
  });

  it("does not start when this browser cannot store the change of mode", async () => {
    // Reads work, writes fail (storage full): a frozen tab would never see the change.
    const full = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("The quota has been exceeded.");
    });
    try {
      routes["POST /api/demo/start"] = () => new Response(null, { status: 204 });
      const user = userEvent.setup();
      renderWithProviders(<AppRoutes />, { route: "/settings" });
      const card = await demoCard();
      await user.click(within(card).getByRole("button", { name: "Start demo" }));
      await user.click(within(card).getByRole("button", { name: "Start demo" }));
      expect((await within(card).findByRole("alert")).textContent).toBe(
        "Demo needs this browser's site storage, and it is full or blocked here.",
      );
      expect(calls).not.toContain("POST /api/demo/start");
      expect(replacePage).not.toHaveBeenCalled();
    } finally {
      full.mockRestore();
    }
  });

  it("starts when storage cannot be read at all: every tab then checks /me", async () => {
    const blocked = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("Storage is blocked.");
    });
    try {
      routes["POST /api/demo/start"] = () => new Response(null, { status: 204 });
      const user = userEvent.setup();
      renderWithProviders(<AppRoutes />, { route: "/settings" });
      const card = await demoCard();
      await user.click(within(card).getByRole("button", { name: "Start demo" }));
      await user.click(within(card).getByRole("button", { name: "Start demo" }));
      await waitFor(() => expect(replacePage).toHaveBeenCalledWith("/"));
    } finally {
      blocked.mockRestore();
    }
  });

  it("goes to sign in when a failed start left this browser signed out", async () => {
    routes["POST /api/demo/start"] = () => {
      me = null;
      return json(503, { detail: "Demo is not available right now." });
    };
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: "/settings" });
    const card = await demoCard();
    await user.click(within(card).getByRole("button", { name: "Start demo" }));
    await user.click(within(card).getByRole("button", { name: "Start demo" }));
    await waitFor(() => expect(replacePage).toHaveBeenCalledWith("/login"));
  });

  it.each([
    ["a member", { ...REAL, role: "member" }],
    ["an admin in demo", { ...DEMO, role: "admin" }],
  ])("is not shown to %s", async (_who, user) => {
    me = user;
    renderWithProviders(<AppRoutes />, { route: "/settings" });
    expect(await screen.findByRole("heading", { level: 1, name: "Settings" })).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "Demo mode" })).toBeNull();
  });
});

describe("In demo", () => {
  beforeEach(() => {
    me = DEMO;
  });

  it("shows the banner and the sample brand, and never the real household name", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: "/settings" });
    expect(await screen.findByText(/Fake data as of Oct 20, 2026\./)).toBeTruthy();
    const banner = screen.getByText(/Fake data as of/).closest("[role=status]") as HTMLElement;
    expect(banner.textContent).toBe("Demo mode. Fake data as of Oct 20, 2026.Leave");
    expect(screen.getByRole("link", { name: "Sample household, Dashboard" }).textContent).toContain("SH");
    expect(document.title).toBe("Settings · Sample household");
    expect(screen.queryByRole("link", { name: "Change password" })).toBeNull();

    await user.click(screen.getByRole("button", { name: /Alex/ }));
    expect(await screen.findByRole("menuitem", { name: "Leave demo" })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: "Change password" })).toBeNull();
    expect(document.body.textContent).not.toMatch(/Your household/);
    expect(document.title).not.toMatch(/Your household/);
  });

  it("shows no banner and the real brand outside demo", async () => {
    me = REAL;
    renderWithProviders(<AppRoutes />, { route: "/settings" });
    expect(await screen.findByRole("link", { name: "Your household, Dashboard" })).toBeTruthy();
    expect(screen.queryByText(/Fake data as of/)).toBeNull();
    expect(document.title).toBe("Settings · Your household");
  });

  it("sends the change password page back to Settings", async () => {
    renderWithProviders(<AppRoutes />, { route: "/settings/password" });
    expect(await screen.findByRole("heading", { level: 1, name: "Settings" })).toBeTruthy();
    expect(screen.queryByLabelText("Current password")).toBeNull();
  });

  it("Leave on the banner signs out and loads Demo ended", async () => {
    routes["POST /api/auth/logout"] = () => new Response(null, { status: 204 });
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: "/settings" });
    const banner = (await screen.findByText(/Fake data as of/)).closest("[role=status]") as HTMLElement;
    await user.click(within(banner).getByRole("button", { name: "Leave" }));
    await waitFor(() => expect(replacePage).toHaveBeenCalledWith("/demo-ended"));
    expect(calls).toContain("POST /api/auth/logout");
    expect(stamp()?.mode).toBe("ended");
    await settle();
    expect(reloadPage).not.toHaveBeenCalled();
  });

  it("Leave demo in the account menu does the same, even when the sign-out call fails", async () => {
    routes["POST /api/auth/logout"] = () => Promise.reject(new TypeError("Failed to fetch"));
    const user = userEvent.setup();
    renderWithProviders(<AppRoutes />, { route: "/settings" });
    await user.click(await screen.findByRole("button", { name: /Alex/ }));
    await user.click(await screen.findByRole("menuitem", { name: "Leave demo" }));
    await waitFor(() => expect(replacePage).toHaveBeenCalledWith("/demo-ended"));
    expect(stamp()?.mode).toBe("ended");
  });

  it("a 401 in demo loads Demo ended, a whole new page", async () => {
    routes["GET /api/flags"] = () => json(401, { detail: "Not signed in." });
    renderWithProviders(<AppRoutes />, { route: "/settings" });
    await screen.findByText(/Fake data as of/);
    await act(async () => {
      await request("GET", "/api/flags").catch(() => undefined);
    });
    expect(replacePage).toHaveBeenCalledWith("/demo-ended");
    expect(screen.queryByRole("form", { name: "Sign in" })).toBeNull();
  });

  it("an ended demo session or a restart loads Demo ended, not the sign-in form", async () => {
    // Start demo stored this; then the server forgot the session.
    window.localStorage.setItem(MODE_KEY, JSON.stringify({ mode: "demo", at: 1 }));
    me = null;
    renderWithProviders(<AppRoutes />, { route: "/accounts" });
    await waitFor(() => expect(replacePage).toHaveBeenCalledWith("/demo-ended"));
    expect(screen.queryByRole("form", { name: "Sign in" })).toBeNull();
  });

  it("a browser last in real mode still gets the sign-in form", async () => {
    window.localStorage.setItem(MODE_KEY, JSON.stringify({ mode: "real", at: 1 }));
    me = null;
    renderWithProviders(<AppRoutes />, { route: "/accounts" });
    expect(await screen.findByRole("form", { name: "Sign in" })).toBeTruthy();
    expect(replacePage).not.toHaveBeenCalled();
  });

  it("opens Cash Flow and Limits on the demo date when the device clock says another year", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2027-03-05T17:00:00Z"));
    try {
      const view = renderWithProviders(<AppRoutes />, { route: "/cash-flow?range=ytd" });
      await waitFor(() => expect(calls).toContain("GET /api/cashflow?from=2026-01&to=2026-10"));
      expect(chicagoToday()).toBe("2026-10-20");
      expect(thisMonth()).toBe("2026-10");
      expect(todayIso()).toBe("2026-10-20");
      // A caller that passes a date still gets that date.
      expect(chicagoToday(new Date("2027-03-05T17:00:00Z"))).toBe("2027-03-05");
      view.unmount();

      renderWithProviders(<AppRoutes />, { route: "/settings/limits" });
      await waitFor(() => expect(calls).toContain("GET /api/contributions?year=2026"));
      expect(calls.some((c) => c.includes("2027"))).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("Demo ended page", () => {
  it("names no household and links to sign in", async () => {
    me = null;
    renderWithProviders(<AppRoutes />, { route: "/demo-ended" });
    expect(await screen.findByRole("heading", { level: 1, name: "Demo ended" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Sign in" }).getAttribute("href")).toBe("/login");
    expect(document.title).toBe("Demo ended · Budget");
    expect(document.body.textContent).not.toMatch(/Your household/);
    expect(replacePage).not.toHaveBeenCalled();
  });
});

describe("Tab cover", () => {
  async function realTab() {
    renderWithProviders(<AppRoutes />, { route: "/settings" });
    await waitFor(() => expect(realName()).not.toBeNull());
  }

  it("covers a hidden tab and uncovers at once when the mode did not change", async () => {
    await realTab();
    setVisibility("hidden");
    expect(realName()).toBeNull();
    expect(screen.getByTestId("page-cover")).toBeTruthy();
    const before = meCalls();
    setVisibility("visible");
    expect(realName()).not.toBeNull();
    expect(screen.queryByTestId("page-cover")).toBeNull();
    expect(meCalls()).toBe(before);
    expect(reloadPage).not.toHaveBeenCalled();
  });

  it("stays covered and reloads when the mode changed while it was hidden, with no message", async () => {
    await realTab();
    setVisibility("hidden");
    // Another tab started demo; this tab heard nothing (a frozen phone tab).
    window.localStorage.setItem(MODE_KEY, JSON.stringify({ mode: "demo", at: 2 }));
    setVisibility("visible");
    expect(reloadPage).toHaveBeenCalledTimes(1);
    expect(realName()).toBeNull();
  });

  it("reloads a page that comes back from history after a change", async () => {
    await realTab();
    act(() => {
      window.dispatchEvent(new Event("pagehide"));
    });
    expect(realName()).toBeNull();
    window.localStorage.setItem(MODE_KEY, JSON.stringify({ mode: "demo", at: 3 }));
    act(() => {
      window.dispatchEvent(new PageTransitionEvent("pageshow", { persisted: true }));
    });
    expect(reloadPage).toHaveBeenCalledTimes(1);
    expect(realName()).toBeNull();
  });

  it("covers and reloads when another tab says the mode changed", async () => {
    await realTab();
    const other = new BroadcastChannel(MODE_CHANNEL);
    try {
      other.postMessage({ mode: "demo", from: "another-tab" });
      await waitFor(() => expect(reloadPage).toHaveBeenCalled());
    } finally {
      other.close();
    }
    expect(realName()).toBeNull();
  });

  it("covers and reloads on a storage message", async () => {
    await realTab();
    act(() => {
      window.dispatchEvent(new StorageEvent("storage", { key: MODE_KEY }));
    });
    expect(reloadPage).toHaveBeenCalledTimes(1);
    expect(realName()).toBeNull();
  });

  it("is in the page before a pagehide handler returns", async () => {
    await realTab();
    // No act(): the browser may keep or freeze the page right after the event.
    const env = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean };
    const was = env.IS_REACT_ACT_ENVIRONMENT;
    env.IS_REACT_ACT_ENVIRONMENT = false;
    try {
      window.dispatchEvent(new Event("pagehide"));
      expect(document.querySelector("[data-page-cover]")).not.toBeNull();
      expect(realName()).toBeNull();
    } finally {
      env.IS_REACT_ACT_ENVIRONMENT = was;
    }
  });

  it("hides an open drawer too, though it sits outside the app root", async () => {
    render(
      <TabCover>
        <Drawer open onOpenChange={() => {}} title="Checking ··1234">
          <p>Balance</p>
        </Drawer>
      </TabCover>,
    );
    const drawer = screen.getByRole("dialog", { name: "Checking ··1234" });
    setVisibility("hidden");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(drawer.closest("[inert]")).not.toBeNull();
    setVisibility("visible");
    expect(screen.getByRole("dialog", { name: "Checking ··1234" })).toBe(drawer);
    expect(document.querySelector("[inert]")).toBeNull();
  });

  it("hides a drawer that opens while the cover is up", async () => {
    await realTab();
    setVisibility("hidden");
    const late = document.createElement("div");
    late.setAttribute("role", "dialog");
    document.body.append(late);
    try {
      await act(async () => {});
      expect(late.hasAttribute("inert")).toBe(true);
      expect(screen.queryByRole("dialog")).toBeNull();
      setVisibility("visible");
      expect(late.hasAttribute("inert")).toBe(false);
    } finally {
      late.remove();
    }
  });

  describe("when storage cannot be read", () => {
    let blocked: ReturnType<typeof vi.spyOn> | null = null;
    afterEach(() => {
      blocked?.mockRestore();
      blocked = null;
      vi.useRealTimers();
    });
    const blockStorage = () => {
      blocked = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
        throw new Error("Storage is blocked.");
      });
    };

    it("stays covered until an uncached /me says the same user and mode", async () => {
      await realTab();
      blockStorage();
      setVisibility("hidden");
      const before = meCalls();
      setVisibility("visible");
      expect(realName()).toBeNull();
      await waitFor(() => expect(realName()).not.toBeNull());
      expect(meCalls()).toBe(before + 1);
      expect(reloadPage).not.toHaveBeenCalled();
    });

    it("stays covered when /me says another mode", async () => {
      await realTab();
      blockStorage();
      setVisibility("hidden");
      me = DEMO;
      setVisibility("visible");
      expect(await screen.findByRole("button", { name: "Reload" })).toBeTruthy();
      expect(realName()).toBeNull();
    });

    it("stays covered when /me fails", async () => {
      await realTab();
      blockStorage();
      setVisibility("hidden");
      routes["GET /api/auth/me"] = () => json(500, { detail: "Something went wrong." });
      setVisibility("visible");
      expect(await screen.findByRole("button", { name: "Reload" })).toBeTruthy();
      expect(realName()).toBeNull();
    });

    it("keeps an open drawer hidden when /me fails", async () => {
      render(
        <TabCover>
          <Drawer open onOpenChange={() => {}} title="Checking ··1234">
            <p>Balance</p>
          </Drawer>
        </TabCover>,
      );
      blockStorage();
      setVisibility("hidden");
      routes["GET /api/auth/me"] = () => json(500, { detail: "Something went wrong." });
      setVisibility("visible");
      expect(await screen.findByRole("button", { name: "Reload" })).toBeTruthy();
      expect(screen.queryByRole("dialog")).toBeNull();
      expect(screen.getByText("Balance").closest("[inert]")).not.toBeNull();
    });

    it("keeps the cover when a /me answer comes after the tab hid again", async () => {
      await realTab();
      blockStorage();
      let answer: (r: Response) => void = () => {};
      routes["GET /api/auth/me"] = () => new Promise<Response>((done) => (answer = done));
      setVisibility("hidden");
      setVisibility("visible");
      setVisibility("hidden");
      await act(async () => answer(json(200, REAL)));
      await settle();
      expect(realName()).toBeNull();
    });

    it("lets only the newest /me check uncover", async () => {
      await realTab();
      blockStorage();
      const answers: ((r: Response) => void)[] = [];
      routes["GET /api/auth/me"] = () => new Promise<Response>((done) => answers.push(done));
      setVisibility("hidden");
      setVisibility("visible"); // check 1 asks while the session is real
      setVisibility("hidden");
      setVisibility("visible"); // check 2 asks after demo started in another tab
      expect(answers).toHaveLength(2);
      await act(async () => answers[0]?.(json(200, REAL)));
      await settle();
      expect(realName()).toBeNull();
      await act(async () => answers[1]?.(json(200, DEMO)));
      expect(await screen.findByRole("button", { name: "Reload" })).toBeTruthy();
      expect(realName()).toBeNull();
    });

    it("stays covered when /me is slow", async () => {
      await realTab();
      blockStorage();
      setVisibility("hidden");
      routes["GET /api/auth/me"] = () => new Promise<Response>(() => {});
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
      setVisibility("visible");
      await act(async () => {
        await vi.advanceTimersByTimeAsync(ME_CHECK_MS);
      });
      expect(screen.getByRole("button", { name: "Reload" })).toBeTruthy();
      expect(realName()).toBeNull();
    });
  });
});
