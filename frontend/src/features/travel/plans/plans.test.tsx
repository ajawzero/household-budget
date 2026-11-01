import { type QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PlanRequestBody, TripPlansResponse } from "../../../lib/api";
import { renderWithProviders } from "../../../test/render";
import { TravelPage } from "../TravelPage";
import { buildRequest, estimateText, nightsBetween, neededText, safeHref, usdRange, valueText, type PlanForm } from "./model";
import { PlanPage } from "./PlanPage";
import {
  AI_OFF,
  AI_ON,
  AI_PAUSED,
  PLAN_ID,
  failedPlan,
  planView,
  plansResponse,
  runningPlan,
  TRAVEL_DATA,
} from "./fixtures";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const OTHER_ID = "efab".repeat(8);
let aiStatus = AI_ON;
let planList: TripPlansResponse;
let view = planView();
let trips: unknown = TRAVEL_DATA;
let startError: { status: number; detail: string } | null = null;
let nextPlanResponse: () => Response = () => json(200, view);
const calls: { url: string; method: string; body: unknown }[] = [];

const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  const method = init?.method ?? "GET";
  const body = init?.body ? JSON.parse(String(init.body)) : null;
  calls.push({ url, method, body });
  if (url === "/api/ai/status") return json(200, aiStatus);
  if (url === "/api/trips") return json(200, trips);
  if (url === "/api/trip-plans" && method === "GET") return json(200, planList);
  if (url === "/api/trip-plans" && method === "POST") {
    return startError ? json(startError.status, { detail: startError.detail }) : json(200, { plan_id: PLAN_ID, status: "running" });
  }
  if (url === `/api/trip-plans/${PLAN_ID}` && method === "GET") return nextPlanResponse();
  if (url === `/api/trip-plans/${PLAN_ID}/follow-up` && method === "POST") return json(200, { plan_id: PLAN_ID, status: "running" });
  if (url === `/api/trip-plans/${PLAN_ID}/trip` && method === "PUT") {
    view = planView({ trip_key: "ski-weekend", trip_name: "Ski weekend" });
    return json(200, view);
  }
  if (url === `/api/trip-plans/${PLAN_ID}` && method === "DELETE") return new Response(null, { status: 204 });
  if (url === `/api/trip-plans/${OTHER_ID}` && method === "GET") return json(200, planView({ plan_id: OTHER_ID, title: "Harbor Town" }));
  if (url === `/api/trip-plans/${OTHER_ID}` && method === "DELETE") return new Response(null, { status: 204 });
  return json(404, { detail: "Not found." });
});

beforeEach(() => {
  aiStatus = AI_ON;
  planList = plansResponse();
  view = planView();
  trips = TRAVEL_DATA;
  startError = null;
  nextPlanResponse = () => json(200, view);
  calls.length = 0;
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function app() {
  return (
    <Routes>
      <Route path="/travel" element={<TravelPage />} />
      <Route path="/travel/plans/:planId" element={<PlanPage />} />
    </Routes>
  );
}

/** Travel again on an existing query client, as after a visit to Settings. */
function renderWithCache(queryClient: QueryClient) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/travel"]}>{app()}</MemoryRouter>
    </QueryClientProvider>,
  );
}

function planUrl(): string {
  return `/api/trip-plans/${PLAN_ID}`;
}

describe("trip plan model helpers", () => {
  const base: PlanForm = {
    where: "  Lantern City  ", when: "dates", start: "2027-01-15", end: "2027-01-18", month: "", nights: "",
    people: ["alex"], guests: "2", airport: "msp", interests: ["food"], note: "  window seat  ", pace: "relaxed", lean: "points",
  };

  it("builds date and month requests, bounds nights and guests, and uppercases airport codes", () => {
    expect(nightsBetween("2027-01-15", "2027-01-18")).toBe(3);
    expect(buildRequest(base, "2026-10-27")).toEqual({
      body: {
        where: "Lantern City", people: ["alex"], guests: 2, interests: ["food"], note: "window seat", pace: "relaxed", lean: "points",
        start_date: "2027-01-15", end_date: "2027-01-18", home_airport: "MSP",
      },
    });
    expect(buildRequest({ ...base, when: "month", month: "2027-05", nights: "30" }, "2026-10-27")).toMatchObject({ body: { month: "2027-05", nights: 30 } });
    expect(buildRequest({ ...base, when: "month", month: "2027-05", nights: "31" }, "2026-10-27")).toEqual({ problem: "Enter the nights, 1 to 30." });
    expect(buildRequest({ ...base, end: "2027-02-14" }, "2026-10-27")).toMatchObject({ body: { end_date: "2027-02-14" } });
    expect(buildRequest({ ...base, guests: "21" }, "2026-10-27")).toEqual({ problem: "Guests: a whole number up to 20." });
    expect(buildRequest({ ...base, airport: "msp1" }, "2026-10-27")).toEqual({ problem: "Use a 3-letter airport code, like MSP." });
  });

  it("rejects past dates and months, short trips, and a request with no travelers", () => {
    expect(buildRequest({ ...base, start: "2026-10-26", end: "2026-10-29" }, "2026-10-27")).toEqual({ problem: "Pick dates from today on." });
    expect(buildRequest({ ...base, end: "2027-01-15" }, "2026-10-27")).toEqual({ problem: "A trip is 1 to 30 nights. The end comes after the start." });
    expect(buildRequest({ ...base, when: "month", month: "2026-09", nights: "2" }, "2026-10-27")).toEqual({ problem: "Pick a month from this one on." });
    expect(buildRequest({ ...base, people: [], guests: "0" }, "2026-10-27")).toEqual({ problem: "Pick who is going." });
  });

  it("accepts only safe HTTP links and formats estimates, point values, balances, and ranges", () => {
    for (const unsafe of ["javascript:alert(1)", "data:text/plain,x", "https://alex:secret" + "@fares.example", "https://fares.example\\path", `https://fares.example/${"x".repeat(500)}`]) {
      expect(safeHref(unsafe)).toBeNull();
    }
    expect(safeHref("https://fares.example/path?q=stay")).toBe("https://fares.example/path?q=stay");
    expect(safeHref("http://fares.example/path")).toBe("http://fares.example/path");
    expect(estimateText(139_200)).toBe("About $1,392");
    expect(estimateText(40)).toBe("About $0.40");
    expect(valueText("1.85")).toBe("1.85¢ a point");
    expect(valueText(null)).toBeNull();
    expect(neededText(40_000, 55_000)).toBe("40,000 of 55,000");
    expect(neededText(40_000, null)).toBe("40,000 needed, balance not known");
    expect(neededText(40_000, "45.20")).toBe("40,000 needed, balance $45.20 cash back"); // dollars, never set against points
    expect(usdRange(300, 420)).toBe("$300 to $420");
    expect(usdRange(300, 300)).toBe("$300");
  });

  it("rejects a non-ASCII host, also once the parser would make it punycode", () => {
    expect(safeHref("https://caf\u00e9.example")).toBeNull();
    expect(safeHref("https://f\u0430res.example/")).toBeNull(); // a Cyrillic a
    expect(safeHref("https://fares.example/a b")).toBeNull();
  });
});

describe("travel plan screens", () => {
  it("hides plans and never requests them while AI is disabled or has no key", async () => {
    for (const status of [{ ...AI_OFF }, { ...AI_ON, key_present: false, available: false, reason: "no_key" as const }]) {
      aiStatus = status;
      calls.length = 0;
      const { unmount } = renderWithProviders(app(), { route: "/travel" });
      await screen.findByRole("heading", { name: "Travel" });
      await waitFor(() => expect(calls.some((c) => c.url === "/api/ai/status")).toBe(true));
      expect(screen.queryByRole("button", { name: /Plan a trip/ })).toBeNull();
      expect(screen.queryByRole("heading", { name: "Trip plans" })).toBeNull();
      expect(calls.some((c) => c.url.startsWith("/api/trip-plans"))).toBe(false);
      unmount();
    }
  });

  it("shows saved plans during an AI pause and hides the start button", async () => {
    aiStatus = AI_PAUSED;
    planList = plansResponse({ available: false, plans: [{ plan_id: PLAN_ID, title: "Lantern City", status: "ready", updated_at: "2026-10-27T12:00:00Z", start_date: "2027-01-15", end_date: "2027-01-18", month: null, nights: 3, low_cents: 139_200, high_cents: 188_400, versions: 1, created_by_name: null, trip_key: null, trip_name: null }] });
    renderWithProviders(app(), { route: "/travel" });
    expect(await screen.findByRole("heading", { name: "Trip plans" })).toBeTruthy();
    expect(await screen.findByRole("link", { name: "Lantern City" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Plan a trip/ })).toBeNull();
  });

  it("says in words that a plan is being made on the disabled start button", async () => {
    planList = plansResponse({ running_plan_id: PLAN_ID });
    renderWithProviders(app(), { route: "/travel" });
    const button = (await screen.findByRole("button", { name: "Plan being made…" })) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(button.getAttribute("title")).toBeNull();
  });

  it("submits a valid form body and opens the new plan", async () => {
    const user = userEvent.setup();
    renderWithProviders(app(), { route: "/travel" });
    await user.click(await screen.findByRole("button", { name: /Plan a trip/ }));
    const dialog = await screen.findByRole("dialog", { name: "Plan a trip" });
    expect(within(dialog).getByText(/About \$0\.92, counts toward the AI limit\./)).toBeTruthy();
    await user.type(within(dialog).getByLabelText("Where"), "Lantern City");
    fireEvent.change(within(dialog).getByLabelText("First night"), { target: { value: "2027-01-15" } });
    fireEvent.change(within(dialog).getByLabelText("Day home"), { target: { value: "2027-01-18" } });
    const alex = within(dialog).getByRole("button", { name: "Alex" });
    expect(alex.querySelector("svg")).toBeNull();
    await user.click(alex);
    // Pressed shows a check mark, not only a color.
    expect(alex.getAttribute("aria-pressed")).toBe("true");
    expect(alex.querySelector("svg")).not.toBeNull();
    await user.type(within(dialog).getByLabelText("Home airport"), "msp");
    await user.click(within(dialog).getByRole("button", { name: "Food" }));
    expect(within(dialog).getByRole("button", { name: "Food" }).querySelector("svg")).not.toBeNull();
    await user.click(within(dialog).getByRole("button", { name: "Make the plan" }));
    await screen.findByRole("heading", { name: "Lantern City" });
    expect(calls.find((c) => c.url === "/api/trip-plans" && c.method === "POST")?.body).toEqual({
      where: "Lantern City", people: ["alex"], guests: 0, interests: ["food"], note: "", pace: "balanced", lean: "value",
      start_date: "2027-01-15", end_date: "2027-01-18", home_airport: "MSP",
    } satisfies PlanRequestBody);
  });

  it("shows client form problems and a server 409 detail", async () => {
    const user = userEvent.setup();
    renderWithProviders(app(), { route: "/travel" });
    await user.click(await screen.findByRole("button", { name: /Plan a trip/ }));
    let dialog = await screen.findByRole("dialog", { name: "Plan a trip" });
    await user.click(within(dialog).getByRole("button", { name: "Make the plan" }));
    expect(await within(dialog).findByText("Type where you want to go.")).toBeTruthy();
    await user.type(within(dialog).getByLabelText("Where"), "Lantern City");
    fireEvent.change(within(dialog).getByLabelText("First night"), { target: { value: "2027-01-15" } });
    fireEvent.change(within(dialog).getByLabelText("Day home"), { target: { value: "2027-01-18" } });
    await user.click(within(dialog).getByRole("button", { name: "Make the plan" }));
    expect(await within(dialog).findByText("Pick who is going.")).toBeTruthy();
    await user.click(within(dialog).getByRole("button", { name: "Alex" }));
    startError = { status: 409, detail: "A plan is already running." };
    await user.click(within(dialog).getByRole("button", { name: "Make the plan" }));
    expect(await within(dialog).findByText("A plan is already running.")).toBeTruthy();
  });

  it("renders ready plan sections, checks, night bar, and separated source lists", async () => {
    renderWithProviders(app(), { route: `/travel/plans/${PLAN_ID}` });
    expect(await screen.findByRole("heading", { name: "Summary" })).toBeTruthy();
    expect(screen.getByText("$1,392 to $1,884")).toBeTruthy();
    for (const heading of ["Points plan", "Flights", "Where to stay", "Getting around", "Day by day", "Check before you book", "Links and sources"]) {
      expect(screen.getByRole("heading", { name: heading })).toBeTruthy();
    }
    expect(screen.getByText("1.85¢ a point")).toBeTruthy();
    expect(screen.getByText("Usual value")).toBeTruthy();
    expect(screen.getByText("Not in your rewards")).toBeTruthy();
    const needed = screen.getByRole("list", { name: "Points needed" });
    expect(within(needed).getByText("Enough")).toBeTruthy();
    expect(within(needed).getByText("Not enough")).toBeTruthy();
    expect(within(needed).getByText("Not compared")).toBeTruthy();
    expect(screen.getByText("Confirm the hotel cancellation terms.")).toBeTruthy();
    expect(screen.getByText("Confirm the fare rules before booking.")).toBeTruthy();
    expect(screen.getByText(/Night 2, Jan 16: no place to stay/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "To check and book" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Other sources" })).toBeTruthy();
    expect(within(screen.getByRole("list", { name: "To check and book" })).getByRole("link", { name: /fares.example/ })).toBeTruthy();
    expect(within(screen.getByRole("list", { name: "Other sources" })).getByRole("link", { name: /visit.example/ })).toBeTruthy();
    // Each source link is 44px tall on phones only.
    for (const link of screen.getAllByRole("link", { name: /fares.example/ })) {
      expect(link.className).toContain("min-h-11");
      expect(link.className).toContain("md:min-h-0");
    }
  });

  it("sets safe external-link attributes and leaves unsafe URLs and inline URL text unlinked", async () => {
    renderWithProviders(app(), { route: `/travel/plans/${PLAN_ID}` });
    await screen.findByRole("heading", { name: "Summary" });
    const links = screen.getAllByRole("link").filter((a) => a.getAttribute("href")?.startsWith("http"));
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) {
      expect(link.getAttribute("target")).toBe("_blank");
      expect(link.getAttribute("rel")).toContain("noopener");
      expect(link.getAttribute("rel")).toContain("noreferrer");
      expect(link.getAttribute("referrerpolicy")).toBe("no-referrer");
      expect(link.textContent).toMatch(/\w+\.example/);
    }
    expect(screen.getByText("unsafe.example").tagName).toBe("SPAN");
    expect(screen.getByText("credentials.example").tagName).toBe("SPAN");
    expect(screen.getByText(/A calm trip with fares.example as a reference\./).closest("a")).toBeNull();
    expect(screen.getByText(/A URL-like string fares.example stays plain text\./).closest("a")).toBeNull();
  });

  it("polls a running plan through writing and then renders its version", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let fetchCount = 0;
    nextPlanResponse = () => {
      fetchCount += 1;
      if (fetchCount === 1) return json(200, runningPlan("searching"));
      if (fetchCount === 2) return json(200, runningPlan("writing"));
      return json(200, planView());
    };
    renderWithProviders(app(), { route: `/travel/plans/${PLAN_ID}` });
    expect(await screen.findByText(/Searching the web/)).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(await screen.findByText(/Writing the plan/)).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(await screen.findByRole("heading", { name: "Summary" })).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("The plan is ready.");
  });

  it("sends a follow-up, validates empty text, and disables follow-up while running", async () => {
    const user = userEvent.setup();
    const mounted = renderWithProviders(app(), { route: `/travel/plans/${PLAN_ID}` });
    const textarea = await screen.findByLabelText("Change the plan");
    expect(screen.getByText(/2 changes left\./)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Send the change" }));
    expect(await screen.findByText("Type the change you want.")).toBeTruthy();
    await user.type(textarea, "Find a calmer hotel");
    await user.click(screen.getByRole("button", { name: "Send the change" }));
    await waitFor(() => expect(calls.some((c) => c.url === `${planUrl()}/follow-up` && c.method === "POST" && (c.body as { text?: string })?.text === "Find a calmer hotel")).toBe(true));

    mounted.unmount();
    // A change running on a plan with a version: the box waits.
    view = { ...planView(), status: "running", step: "writing" };
    const again = renderWithProviders(app(), { route: `/travel/plans/${PLAN_ID}` });
    const disabledTextarea = await screen.findByLabelText("Change the plan");
    expect((disabledTextarea as HTMLTextAreaElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "Wait for this run" }) as HTMLButtonElement).disabled).toBe(true);
    again.unmount();

    // The first plan being made: nothing to change yet, so no box.
    view = runningPlan("writing");
    renderWithProviders(app(), { route: `/travel/plans/${PLAN_ID}` });
    await screen.findByText(/Writing the plan/);
    expect(screen.queryByLabelText("Try again")).toBeNull();
    expect(screen.queryByLabelText("Change the plan")).toBeNull();
  });

  it("shows a failed run notice and retry option", async () => {
    view = { ...view, ...failedPlan() };
    renderWithProviders(app(), { route: `/travel/plans/${PLAN_ID}` });
    expect((await screen.findByRole("alert")).textContent).toContain("The search did not finish.");
    expect(screen.getByRole("button", { name: "Send the change" })).toBeTruthy();
    expect(screen.getByLabelText("Try again")).toBeTruthy();
  });

  it("defaults to the newest version and lets the user select version one", async () => {
    const user = userEvent.setup();
    const old = planView().versions[0]!;
    const newest = { ...old, version: 2, follow_up: "Make it quieter.", plan: { ...old.plan, summary: "The newest version uses a quieter hotel." } };
    view = planView({ versions: [old, newest] });
    renderWithProviders(app(), { route: `/travel/plans/${PLAN_ID}` });
    expect(await screen.findByText("The newest version uses a quieter hotel.")).toBeTruthy();
    // A long option never makes the select wider than the page.
    expect(screen.getByLabelText("Version").className).toContain("max-w-full");
    await user.selectOptions(screen.getByLabelText("Version"), "1");
    expect(screen.getByText("A calm trip with fares.example as a reference.")).toBeTruthy();
  });

  it("links a plan to an imported trip and shows its estimate on that trip card", async () => {
    const user = userEvent.setup();
    renderWithProviders(app(), { route: `/travel/plans/${PLAN_ID}` });
    const select = await screen.findByLabelText("Linked trip");
    await waitFor(() => expect((select as HTMLSelectElement).disabled).toBe(false));
    await user.selectOptions(select, "ski-weekend");
    await waitFor(() => expect(calls.some((c) => c.url === `${planUrl()}/trip` && c.method === "PUT")).toBe(true));
    expect(await screen.findByText(/Linked to Ski weekend\./)).toBeTruthy();
    expect(calls.find((c) => c.url === `${planUrl()}/trip` && c.method === "PUT")?.body).toEqual({ trip_key: "ski-weekend" });

    planList = plansResponse({ plans: [{ plan_id: PLAN_ID, title: "Lantern City", status: "ready", updated_at: "2026-10-27T12:00:00Z", start_date: "2027-01-15", end_date: "2027-01-18", month: null, nights: 3, low_cents: 139_200, high_cents: 188_400, versions: 1, created_by_name: null, trip_key: "ski-weekend", trip_name: "Ski weekend" }] });
    renderWithProviders(app(), { route: "/travel" });
    const tripsRegion = await screen.findByRole("region", { name: "Trips" });
    await within(tripsRegion).findByRole("button", { name: /Ski weekend/ });
    expect(await within(tripsRegion).findByRole("link", { name: "plan est. $1,392 to $1,884" })).toBeTruthy();
  });

  it("hides plan est. on trip cards once AI is off, even with the list cached", async () => {
    planList = plansResponse({ plans: [{ plan_id: PLAN_ID, title: "Lantern City", status: "ready", updated_at: "2026-10-27T12:00:00Z", start_date: "2027-01-15", end_date: "2027-01-18", month: null, nights: 3, low_cents: 139_200, high_cents: 188_400, versions: 1, created_by_name: null, trip_key: "ski-weekend", trip_name: "Ski weekend" }] });
    const { queryClient, unmount } = renderWithProviders(app(), { route: "/travel" });
    const trips = await screen.findByRole("region", { name: "Trips" });
    expect(await within(trips).findByRole("link", { name: "plan est. $1,392 to $1,884" })).toBeTruthy();
    unmount();
    // Same cache, AI now off.
    aiStatus = AI_OFF;
    queryClient.setQueryData(["ai", "status"], AI_OFF);
    renderWithCache(queryClient);
    const after = await screen.findByRole("region", { name: "Trips" });
    await within(after).findByRole("button", { name: /Ski weekend/ });
    expect(within(after).queryByRole("link", { name: /plan est\./ })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Trip plans" })).toBeNull();
  });

  it("never carries a delete confirmation over to another plan", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <Routes>
        <Route
          path="/travel/plans/:planId"
          element={
            <>
              <PlanPage />
              <Link to={`/travel/plans/${PLAN_ID}`}>Plan A</Link>
              <Link to={`/travel/plans/${OTHER_ID}`}>Plan B</Link>
            </>
          }
        />
      </Routes>,
      { route: `/travel/plans/${OTHER_ID}` },
    );
    await screen.findByRole("heading", { name: "Harbor Town" });
    await user.click(screen.getByRole("link", { name: "Plan A" }));
    await screen.findByRole("heading", { name: "Lantern City" });
    await user.click(screen.getByRole("button", { name: "Delete plan" }));
    expect(screen.getByRole("button", { name: "Delete" })).toBeTruthy();
    // Back to B, which is cached: its page starts without the confirmation.
    await user.click(screen.getByRole("link", { name: "Plan B" }));
    await screen.findByRole("heading", { name: "Harbor Town" });
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
    expect(screen.getByRole("button", { name: "Delete plan" })).toBeTruthy();
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
  });

  it("keeps polling a sent change when a refresh fails, and says so", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    renderWithProviders(app(), { route: `/travel/plans/${PLAN_ID}` });
    await user.type(await screen.findByLabelText("Change the plan"), "Find a calmer hotel");
    nextPlanResponse = () => json(500, { detail: "Server error." });
    await user.click(screen.getByRole("button", { name: "Send the change" }));
    expect(await screen.findByText("Could not refresh the plan. It tries again.")).toBeTruthy();
    expect(screen.getByText(/Searching the web/)).toBeTruthy();
    const gets = () => calls.filter((c) => c.url === planUrl() && c.method === "GET").length;
    const before = gets();
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(gets()).toBeGreaterThan(before); // still polling after the failure
    const old = planView().versions[0]!;
    nextPlanResponse = () => json(200, planView({ versions: [old, { ...old, version: 2, follow_up: "Find a calmer hotel" }] }));
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(await screen.findByText("The plan is ready.")).toBeTruthy();
    expect(screen.queryByText("Could not refresh the plan. It tries again.")).toBeNull();
  });

  it("confirms deletion and returns to Travel, and disables deletion during a run", async () => {
    const user = userEvent.setup();
    renderWithProviders(app(), { route: `/travel/plans/${PLAN_ID}` });
    await user.click(await screen.findByRole("button", { name: "Delete plan" }));
    expect(screen.queryByText("You can delete it when the plan is done.")).toBeNull();
    await user.click(screen.getAllByRole("button", { name: "Delete" })[0]!);
    expect(await screen.findByRole("heading", { name: "Travel" })).toBeTruthy();
    expect(calls.some((c) => c.url === planUrl() && c.method === "DELETE")).toBe(true);

    view = runningPlan();
    renderWithProviders(app(), { route: `/travel/plans/${PLAN_ID}` });
    const del = (await screen.findByRole("button", { name: "Delete plan" })) as HTMLButtonElement;
    expect(del.disabled).toBe(true);
    expect(del.getAttribute("title")).toBeNull();
    expect(screen.getByText("You can delete it when the plan is done.")).toBeTruthy();
  });
});
