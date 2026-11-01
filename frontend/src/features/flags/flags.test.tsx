import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../../routes/AppRoutes";
import { renderWithProviders } from "../../test/render";
import { FLAGS, FLAGS_ARCHIVED, FLAGS_V14, PENDING, TODOS } from "../recurring/fixtures";
import { FlagsPage, groupFlags, kindText } from "./FlagsPage";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

let failTodos = false;
let flags: typeof FLAGS = FLAGS;
const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input);
  if (url === "/api/auth/me") return json(200, { username: "sam", display_name: "Sam", role: "member" });
  if (url === "/api/flags?include_archived=true") return json(200, FLAGS_ARCHIVED);
  if (url === "/api/flags") return json(200, flags);
  if (url === "/api/todos") return failTodos ? json(500, { detail: "Server error." }) : json(200, TODOS);
  if (url === "/api/pending") return json(200, PENDING);
  return json(404, { detail: "Not found." });
});

beforeEach(() => {
  failTodos = false;
  flags = FLAGS;
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function section(name: RegExp | string): HTMLElement {
  return screen.getByRole("region", { name });
}

describe("FlagsPage", () => {
  it("links a trip flag to Travel, and names a trip the app does not have", async () => {
    const base = FLAGS.flags[0]!;
    flags = {
      ...FLAGS,
      flags: [
        { ...base, key: "trip-a", title: "Trip costs posted", related_kind: "trip", related_key: "example-trip", related_label: "Example trip" },
        { ...base, key: "trip-b", title: "Old trip", related_kind: "trip", related_key: "gone-trip", related_label: null },
      ],
    };
    renderWithProviders(<FlagsPage />, { route: "/flags" });
    const link = await screen.findByRole("link", { name: "Example trip" });
    expect(link.getAttribute("href")).toBe("/travel?trip=example-trip");
    expect(screen.getByText(/About the trip gone-trip \(not in Travel\)/)).toBeTruthy();
  });


  it("groups flags by severity with status and kind badges, the full note and the related item", async () => {
    renderWithProviders(<FlagsPage />, { route: "/flags" });
    await screen.findByText("Pay off the promo");
    const high = section(/^High severity/);
    expect(high.textContent).toContain("Open");
    expect(high.textContent).toContain("Debt");
    expect(high.textContent).toContain("Overdue, was due Oct 1, 2026");
    const note = within(high).getByText(/Interest applies to the whole purchase\./);
    expect(note.textContent).toBe("Interest applies to the whole purchase.\nPay it early.");
    // A long link or reference wraps instead of pushing the page sideways.
    expect(note.className).toContain("break-words");
    expect(high.textContent).toContain("About the plan Example gear");
    const medium = section(/^Medium severity/);
    expect(medium.textContent).toContain("Watching");
    expect(medium.textContent).toContain("Cost cutting");
    expect(medium.textContent).toContain("Due Feb 1, 2027");
    const resolved = section(/^Resolved/);
    expect(resolved.textContent).toContain("Resolved Jul 12");
    expect(screen.queryByRole("region", { name: /^Low severity/ })).toBeNull();
    expect(screen.getByText("1 archived flag is hidden.")).toBeTruthy();
    // Counts match the sections: the low flag is resolved.
    expect(screen.getByText(/1 high, 1 medium and 0 low severity open\. 1 resolved\./)).toBeTruthy();
    expect(screen.getByText("1 open, 1 watching, 1 resolved")).toBeTruthy();
    // A 1.3 flag reads as act. No info flags, so no For information group.
    expect(within(high).getByText("Act")).toBeTruthy();
    expect(resolved.textContent).not.toContain("Act");
    expect(screen.queryByRole("region", { name: /^For information/ })).toBeNull();
  });

  it("shows archived flags on request", async () => {
    renderWithProviders(<FlagsPage />, { route: "/flags" });
    await userEvent.setup().click(await screen.findByRole("button", { name: "Show 1 archived" }));
    await waitFor(() => expect(section(/^Archived/).textContent).toContain("Claim paid back"));
    expect(fetchMock).toHaveBeenCalledWith("/api/flags?include_archived=true", expect.anything());
    expect(screen.getByRole("button", { name: "Hide archived" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("lists to dos and pending items read only", async () => {
    renderWithProviders(<FlagsPage />, { route: "/flags" });
    const todos = await screen.findByRole("list", { name: "To do" });
    const items = within(todos).getAllByRole("listitem");
    expect(items[0]?.textContent).toContain("For the flag: Pay off the promo");
    expect(within(items[0]!).getByText(/Overdue/).className).toContain("text-bad");
    expect(items[1]?.textContent).toContain("File the claim (done)");
    const pending = screen.getByRole("list", { name: "Pending" });
    const rows = within(pending).getAllByRole("listitem");
    expect(rows[0]?.textContent).toBe("ApplyWaitingTake the gym out of the planAfter the last charge.Added Oct 1 · Updated Oct 12");
    expect(rows[1]?.textContent).toBe("ConfirmOpenConfirm the store card paymentAdded Oct 12");
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("shows one banner when a list fails", async () => {
    failTodos = true;
    renderWithProviders(<FlagsPage />, { route: "/flags" });
    expect((await screen.findByRole("alert")).textContent).toContain("Could not load flags and to dos.");
  });

  it("is in the More nav and routed", async () => {
    renderWithProviders(<AppRoutes />, { route: "/flags" });
    expect(await screen.findByRole("heading", { level: 1, name: "Flags & To do" })).toBeTruthy();
    expect(screen.queryByText("Coming in Phase 4")).toBeNull();
  });

  it("groups and names", () => {
    expect(groupFlags(FLAGS_ARCHIVED.flags).map((g) => [g.key, g.flags.length])).toEqual([
      ["high", 1],
      ["medium", 1],
      ["resolved", 1],
      ["archived", 1],
    ]);
    expect(kindText("cost_cutting")).toBe("Cost cutting");
    expect(kindText("new_kind")).toBe("new kind");
    expect(
      ["retirement", "investment", "insurance", "medical", "payroll", "home", "other"].map(kindText),
    ).toEqual(["Retirement", "Investment", "Insurance", "Medical", "Payroll", "Home", "Other"]);
    expect(groupFlags(FLAGS_V14.flags).map((g) => [g.key, g.flags.map((f) => f.key)])).toEqual([
      ["medium", ["rollover"]],
      ["low", ["quote", "claim"]],
      ["info", ["gift"]],
      ["resolved", ["fees"]],
    ]);
  });
});

describe("FlagsPage, format 1.4", () => {
  beforeEach(() => {
    flags = FLAGS_V14;
  });

  it("shows the action as a neutral badge on flags not resolved", async () => {
    renderWithProviders(<FlagsPage />, { route: "/flags" });
    await screen.findByText("Roll over the old plan");
    const act = within(section(/^Medium severity/)).getByText("Act");
    expect(act.className).toContain("text-fg-secondary");
    expect(act.getAttribute("title")).toBe("Something to do");
    const low = section(/^Low severity/);
    expect(within(low).getByText("Ask").className).toContain("text-fg-secondary");
    expect(within(low).getByText("Reimburse").className).toContain("text-fg-secondary");
    expect(low.textContent).toContain("Insurance");
    expect(low.textContent).toContain("Medical");
    const resolved = section(/^Resolved/);
    for (const word of ["Act", "Ask", "Reimburse"]) expect(within(resolved).queryByText(word)).toBeNull();
  });

  it("puts info flags in their own group, after the severity groups, with a neutral date", async () => {
    renderWithProviders(<FlagsPage />, { route: "/flags" });
    await screen.findByText("Roll over the old plan");
    expect(screen.getAllByRole("region").map((r) => r.getAttribute("aria-labelledby"))).toEqual([
      "flags-medium",
      "flags-low",
      "flags-info",
      "flags-resolved",
    ]);
    const info = section(/^For information/);
    expect(info.textContent).toContain("Gift for the college fund");
    expect(info.textContent).toContain("Nothing to do. Not counted as open.");
    expect(info.textContent).toContain("For information: Gift for the college fund");
    expect(info.textContent).not.toContain("Open");
    expect(info.textContent).not.toContain("Overdue");
    expect(info.textContent).not.toContain("Due");
    const date = within(info).getByText("On Oct 1, 2026");
    expect(date.className).toContain("text-fg-secondary");
    expect(within(info).queryByText("Act")).toBeNull();
    // A resolved info flag is history: it goes in Resolved, with no date.
    const resolved = section(/^Resolved/);
    expect(resolved.textContent).toContain("Library fees paid");
    expect(resolved.textContent).toContain("Resolved");
  });

  it("keeps info flags out of the open counts", async () => {
    renderWithProviders(<FlagsPage />, { route: "/flags" });
    expect(
      await screen.findByText(/^0 high, 1 medium and 2 low severity open\. 1 for information\. 1 resolved\./),
    ).toBeTruthy();
    expect(screen.getByText("2 open, 1 watching, 1 for information, 1 resolved")).toBeTruthy();
  });

  it("lists the newest 3 updates and shows the earlier ones on request", async () => {
    renderWithProviders(<FlagsPage />, { route: "/flags" });
    const row = (await screen.findByText("Roll over the old plan")).closest("li")!;
    const list = within(row).getByRole("list", { name: "Updates" });
    let items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(3);
    expect(items[0]?.textContent).toBe("Oct 10, 2026 · Forms sent");
    expect(items[0]?.className).toContain("break-words");
    const more = within(row).getByRole("button", { name: /^Show 2 earlier/ });
    expect(more.getAttribute("aria-expanded")).toBe("false");
    expect(more.getAttribute("aria-controls")).toBe(list.id);
    expect(more.className).toContain("h-11");
    await userEvent.setup().click(more);
    items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(5);
    expect(items[4]?.textContent).toBe("Aug 21, 2026 · Found the old plan statement");
    const less = within(row).getByRole("button", { name: /^Hide earlier/ });
    expect(less.getAttribute("aria-expanded")).toBe("true");
    expect(row.textContent).toContain("Added Aug 20 · Updated Oct 10");
    // A flag with no updates has no list and no Updated date.
    const quote = screen.getByText("Ask for a new quote").closest("li")!;
    expect(within(quote).queryByRole("list", { name: "Updates" })).toBeNull();
    expect(quote.textContent).not.toContain("Updated");
  });

  it("shows no toggle with 3 updates or fewer, and no date badge on an info flag with no date", async () => {
    const base = FLAGS_V14.flags[0]!;
    flags = {
      ...FLAGS_V14,
      flags: [
        { ...base, updates: base.updates.slice(0, 3) },
        { ...FLAGS_V14.flags[3]!, key: "note", title: "A note", due_date: null },
      ],
    };
    renderWithProviders(<FlagsPage />, { route: "/flags" });
    const row = (await screen.findByText("Roll over the old plan")).closest("li")!;
    expect(within(within(row).getByRole("list", { name: "Updates" })).getAllByRole("listitem")).toHaveLength(3);
    expect(within(row).queryByRole("button")).toBeNull();
    const info = section(/^For information/);
    expect(info.textContent).not.toContain("On ");
  });
});
