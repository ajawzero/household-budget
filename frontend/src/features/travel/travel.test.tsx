import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Trip, TripItem, TripsResponse } from "../../lib/api";
import { renderWithProviders } from "../../test/render";
import { NO_TRIPS, TRIPS } from "./fixtures";
import { dueText, iouText, isPastDue, itemAmountText, itemDetail, pointsText, remainingText, taggedNotes, tripDates, whoText } from "./model";
import { COST_COLUMNS, TravelPage } from "./TravelPage";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

let payload: TripsResponse | "error" = TRIPS;
const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input);
  if (url === "/api/trips") return payload === "error" ? json(500, { detail: "Server error." }) : json(200, payload);
  return json(404, { detail: "Not found." });
});

beforeEach(() => {
  payload = TRIPS;
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function trip(key: string): Trip {
  const t = [...TRIPS.active, ...TRIPS.completed, ...TRIPS.older].find((x) => x.key === key);
  if (!t) throw new Error(key);
  return t;
}

function item(tripKey: string, key: string): TripItem {
  const i = trip(tripKey).items.find((x) => x.key === key);
  if (!i) throw new Error(key);
  return i;
}

describe("travel helpers", () => {
  it("words dates without making any up", () => {
    expect(tripDates(trip("ski-weekend"))).toBe("Jan 15 to 18, 2027");
    expect(tripDates(trip("beach-week"))).toBe("Jun 2027");
    expect(tripDates({ start_date: "2024-12-30", end_date: "2025-01-02", month: null })).toBe("Dec 30, 2024 to Jan 2, 2025");
    expect(tripDates({ start_date: "2026-07-30", end_date: "2026-08-02", month: null })).toBe("Jul 30 to Aug 2, 2026");
  });

  it("words who, ranges, points and IOU directions", () => {
    expect(whoText(trip("lake-weekend"))).toBe("Alex, Jamie, Riley, Sam +2 guests");
    expect(remainingText(30_000, 42_000, 1)).toBe("$300 to $420 + 1 TBD");
    expect(remainingText(0, 0, 0)).toBe("None");
    expect(pointsText(trip("ski-weekend").totals.points)).toBe("50,000 Harbor Miles");
    const ski = trip("ski-weekend").ious[0]!;
    expect(iouText(ski)).toBe("Collect $150.00 from friend couple");
    expect(iouText(trip("hills-jun24").ious[0]!)).toBe("Pay relative $40.00");
    const waived = trip("lake-weekend").ious[0]!;
    expect(iouText(waived)).toBe("$25.00 from relative"); // never "Collect" once called a wash
    expect(iouText({ ...waived, status: "settled", settled_on: "2026-07-10" })).toBe("Collected $25.00 from relative");
    expect(iouText({ ...waived, direction: "we_owe", status: "settled", settled_on: "2026-07-10" })).toBe("Paid relative $25.00");
  });

  it("shows cash fees on points bookings and a points credit as money back", () => {
    const flights = item("ski-weekend", "flights");
    expect(itemAmountText(flights)).toBe("$11.20");
    expect(itemDetail(flights)).toBe("50,000 Harbor Miles · $11.20 in cash fees · Cobalt card · Sam");
    expect(itemAmountText(item("ski-weekend", "points-credit"))).toBe("−$40.00");
    expect(itemAmountText(item("ski-weekend", "parking"))).toBe("TBD");
    expect(itemAmountText(item("ski-weekend", "lift-tickets"))).toBe("$300.00 to $420.00");
  });

  it("words a due payment and a maybe item (format 1.4)", () => {
    const car = item("ski-weekend", "rental-car");
    expect(dueText(car, "2026-10-24")).toBe("Due Nov 10, 2026 on Summit card");
    expect(dueText(car, "2026-11-10")).toBe("Due Nov 10, 2026 on Summit card"); // the day itself is not past
    expect(dueText(car, "2026-11-11")).toBe("Was due Nov 10, 2026 on Summit card");
    expect(dueText(car, "2026-10-24", false)).toBe("Due Nov 10, 2026");
    expect(dueText({ ...car, account_name: null }, "2026-10-24")).toBe("Due Nov 10, 2026");
    expect(dueText(item("ski-weekend", "gear"), "2026-10-24")).toBeNull();
    expect([isPastDue(car, "2026-11-10"), isPastDue(car, "2026-11-11"), isPastDue({ due_on: null }, "2026-11-11")]).toEqual([false, true, false]);
    const snorkel = item("beach-week", "snorkel-tour");
    expect(itemAmountText(snorkel)).toBe("$150.00 to $250.00"); // a maybe estimate keeps its range
    expect(itemAmountText({ ...snorkel, status: "tbd", estimate_low_cents: null, estimate_high_cents: null })).toBe("Not booked");
  });

  it("notes tagged rows only when they differ by more than $1", () => {
    expect(taggedNotes(trip("lake-weekend"))).toEqual([]);
    expect(taggedNotes(trip("ski-weekend"))).toEqual(["Tagged rows show $0.00 out, net of refunds. Posted items add up to −$40.00."]);
  });
});

describe("TravelPage", () => {
  it("shows the tiles from the server", async () => {
    renderWithProviders(<TravelPage />);
    await screen.findAllByText("$715"); // the tile and the summary total
    const tiles = screen.getByRole("list", { name: "Travel summary tiles" });
    expect(tiles.className).toContain("grid-cols-2");
    expect(tiles.className).toContain("md:grid-cols-4");
    const t = within(tiles);
    expect(t.getByText("$715")).toBeTruthy(); // 451.20 + 263.47
    expect(t.getByText("$2,400 to $3,520")).toBeTruthy();
    expect(t.getByText("plus 1 TBD")).toBeTruthy();
    expect(t.getByText("Harbor Miles").parentElement!.textContent).toBe("50,000 Harbor Miles");
    expect(t.getByText("Collect $150 · Pay $0")).toBeTruthy();
  });

  it("puts each program on its own line in the Points used tile", async () => {
    payload = structuredClone(TRIPS);
    payload.tiles.points.push({ points_micro: 120_000_000_000, program_key: null, program_name: "Lantern Hotels Rewards" });
    renderWithProviders(<TravelPage />);
    const tiles = await screen.findByRole("list", { name: "Travel summary tiles" });
    const lines = [await within(tiles).findByText("Harbor Miles"), within(tiles).getByText("Lantern Hotels Rewards")].map((e) => e.parentElement!);
    expect(lines.map((l) => [l.textContent, l.className])).toEqual([
      ["50,000 Harbor Miles", "block"],
      ["120,000 Lantern Hotels Rewards", "block"],
    ]);
  });

  it("gives the trip links a 44px tap height on phones only", async () => {
    renderWithProviders(<TravelPage />);
    const collect = await screen.findByRole("list", { name: "Collect" });
    const summary = screen.getByRole("list", { name: "Trips in the summary" });
    for (const link of [within(collect).getByRole("link", { name: "Ski weekend" }), within(summary).getByRole("link", { name: "Ski weekend" })]) {
      expect(link.className).toContain("min-h-11");
      expect(link.className).toContain("md:min-h-0");
    }
  });

  it("shows open trips as cards with totals, items and the transactions link", async () => {
    renderWithProviders(<TravelPage />);
    await screen.findByRole("button", { name: /Ski weekend/ });
    const trips = screen.getByRole("region", { name: "Trips" });
    const ski = within(trips).getByRole("button", { name: /Ski weekend/ });
    expect(ski.getAttribute("aria-expanded")).toBe("true");
    const items = within(trips).getByRole("list", { name: "Ski weekend items" });
    expect(within(items).getByText("Free night")).toBeTruthy();
    expect(within(items).getByText("Points credit")).toBeTruthy();
    expect(within(trips).getByText("$301")).toBeTruthy(); // our net: 451.20 less 150 owed to us
    const link = within(trips).getAllByRole("link", { name: "Transactions for this trip (0)" })[0]!;
    expect(link.getAttribute("href")).toBe("/transactions?trip=ski-weekend");
    expect(within(trips).getByText("Tagged rows show $0.00 out, net of refunds. Posted items add up to −$40.00.")).toBeTruthy();
    expect(within(trips).getAllByText("From the Oct 12, 2026 update.").length).toBe(2);
  });

  it("lists what is still open with collect and pay apart", async () => {
    renderWithProviders(<TravelPage />);
    const collect = await screen.findByRole("list", { name: "Collect" });
    expect(within(collect).getByText("Collect $150.00 from friend couple")).toBeTruthy();
    expect(screen.getByText("We owe nobody.")).toBeTruthy(); // the older trip's IOU is out of scope
    const tbd = screen.getByRole("list", { name: "TBD and estimates" });
    expect(within(tbd).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      expect.stringContaining("Lift tickets"),
      expect.stringContaining("Resort parking"),
      expect.stringContaining("Flights"),
      expect.stringContaining("Beach rental"),
      expect.stringContaining("Snorkel tour"), // a maybe item stays open (format 1.4)
    ]);
  });

  it("lists payments due with their card first, in four columns on wide screens", async () => {
    renderWithProviders(<TravelPage />);
    const due = await screen.findByRole("list", { name: "Due" });
    const row = within(due).getByRole("listitem");
    expect(within(row).getByText("Rental car")).toBeTruthy();
    const when = within(row).getByText("Due Nov 10, 2026 on Summit card");
    expect(when.className).toContain("text-fg-secondary");
    expect(within(row).getByText("$300.00")).toBeTruthy();
    const link = within(row).getByRole("link", { name: "Ski weekend" });
    expect(link.getAttribute("href")).toBe("/travel?trip=ski-weekend");
    expect(link.className).toContain("min-h-11");
    expect(link.className).toContain("md:min-h-0");
    const grid = due.parentElement!.parentElement!;
    expect(grid.className).toContain("md:grid-cols-2");
    expect(grid.className).toContain("xl:grid-cols-4");
    expect(screen.getByText("Payments still to post, money to collect or pay back, and costs not known yet.")).toBeTruthy();
  });

  it("says Was due in the warn tone once the day has passed", async () => {
    payload = { ...TRIPS, today: "2026-11-11" };
    renderWithProviders(<TravelPage />);
    const due = await screen.findByRole("list", { name: "Due" });
    expect(within(due).getByText("Was due Nov 10, 2026 on Summit card").className).toContain("text-warn");
    const items = screen.getByRole("list", { name: "Ski weekend items" });
    expect(within(items).getByText("Was due Nov 10, 2026").className).toContain("text-warn");
  });

  it("shows no Due list when nothing is due", async () => {
    payload = structuredClone(TRIPS);
    payload.open_items.due = [];
    renderWithProviders(<TravelPage />);
    const collect = await screen.findByRole("list", { name: "Collect" });
    expect(screen.queryByRole("list", { name: "Due" })).toBeNull();
    expect(collect.parentElement!.parentElement!.className).toContain("md:grid-cols-3");
  });

  it("says when a booked item is due in place of Booked", async () => {
    renderWithProviders(<TravelPage />);
    const items = await screen.findByRole("list", { name: "Ski weekend items" });
    const car = within(items).getByText("Rental car").closest("li")!;
    const when = within(car).getByText("Due Nov 10, 2026");
    expect(when.className).toContain("text-fg-secondary");
    expect(within(car).queryByText("Booked")).toBeNull();
    expect(within(car).getByText("Summit card · Alex")).toBeTruthy(); // the card stays on the line under it
    const gear = within(items).getByText("Winter gear on the store plan").closest("li")!;
    expect(within(gear).getByText("Booked")).toBeTruthy();
  });

  it("mutes maybe items with a chip and keeps them out of the totals", async () => {
    renderWithProviders(<TravelPage />);
    const items = await screen.findByRole("list", { name: "Beach week items" });
    const snorkel = within(items).getByText("Snorkel tour");
    expect(snorkel.className).toContain("text-fg-secondary");
    const row = snorkel.closest("li")!;
    expect(within(row).getByText("Maybe")).toBeTruthy();
    expect(within(row).queryByText("Estimate")).toBeNull();
    expect(within(row).getByText("$150.00 to $250.00").className).toContain("text-fg-secondary");
    const rental = within(items).getByText("Beach rental").closest("li")!;
    expect(within(rental).getByText("Estimate")).toBeTruthy();
    // The note shows on the beach week only; its totals and the tiles leave the tour out.
    expect(screen.getAllByText("Maybe items are not in the totals.")).toHaveLength(1);
    const beach = items.parentElement!;
    expect(within(beach).getByText("Maybe items are not in the totals.")).toBeTruthy();
    expect(within(beach).getByText("$2,100 to $3,100")).toBeTruthy();
    const tiles = screen.getByRole("list", { name: "Travel summary tiles" });
    expect(within(tiles).getByText("$2,400 to $3,520")).toBeTruthy();
    const tbd = screen.getByRole("list", { name: "TBD and estimates" });
    const open = within(tbd).getByText("Snorkel tour");
    expect(open.className).toContain("text-fg-secondary");
    expect(within(open).getByText("Maybe")).toBeTruthy();
  });

  it("collapses completed trips with the estimate and excluded items", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TravelPage />);
    const done = await screen.findByRole("region", { name: "Completed trips" });
    const lake = within(done).getByRole("button", { name: /Lake weekend/ });
    expect(lake.getAttribute("aria-expanded")).toBe("false");
    expect(within(lake).getByText("total actual")).toBeTruthy();
    await user.click(lake);
    expect(within(done).getByText("Excluded, verify")).toBeTruthy();
    expect(within(done).getByText(/est\. was \$450 to \$600 · Came in under · final ✓/)).toBeTruthy();
    expect(within(done).getByText("From the Oct 6, 2026 update.")).toBeTruthy(); // the backfill run
  });

  it("keeps older trips under Show older", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TravelPage />);
    const button = await screen.findByRole("button", { name: "Show older (1)" });
    expect(screen.queryByRole("button", { name: /Hills trip/ })).toBeNull();
    await user.click(button);
    expect(screen.getByRole("button", { name: /Hills trip/ })).toBeTruthy();
  });

  it("opens the trip named in the link, older ones too", async () => {
    renderWithProviders(<TravelPage />, { route: "/travel?trip=hills-jun24" });
    const hills = await screen.findByRole("button", { name: /Hills trip/ });
    expect(hills.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("button", { name: /Ski weekend/ }).getAttribute("aria-expanded")).toBe("false");
  });

  it("scrolls to an older trip named in the link", async () => {
    const scroll = vi.fn();
    Object.defineProperty(Element.prototype, "scrollIntoView", { value: scroll, configurable: true, writable: true });
    try {
      renderWithProviders(<TravelPage />, { route: "/travel?trip=hills-jun24" });
      await screen.findByRole("button", { name: /Hills trip/ });
      expect(scroll.mock.contexts.map((el) => (el as Element).id)).toContain("trip-hills-jun24");
    } finally {
      Reflect.deleteProperty(Element.prototype, "scrollIntoView");
    }
  });

  it("hides older trips while the link names one, and the link opens them again", async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <>
        <Link to="/travel?trip=hills-jun24">Go to Hills</Link>
        <TravelPage />
      </>,
      { route: "/travel?trip=hills-jun24" },
    );
    await screen.findByRole("button", { name: /Hills trip/ });

    const hide = screen.getByRole("button", { name: "Hide older" });
    expect(hide.getAttribute("aria-expanded")).toBe("true");
    await user.click(hide);
    const show = screen.getByRole("button", { name: "Show older (1)" });
    expect(show.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("button", { name: /Hills trip/ })).toBeNull();

    // Following the same link again opens the older trips and the card.
    await user.click(screen.getByRole("link", { name: "Go to Hills" }));
    expect(screen.getByRole("button", { name: "Hide older" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Hills trip/ }).getAttribute("aria-expanded")).toBe("true");
  });

  it("opens a completed trip from its summary link, including a repeat visit", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TravelPage />, { route: "/travel" });
    const lakeCard = () => screen.getByRole("button", { name: /Lake weekend/ });
    await screen.findByRole("button", { name: /Lake weekend/ });
    expect(lakeCard().getAttribute("aria-expanded")).toBe("false");

    await user.click(screen.getByRole("link", { name: "Lake weekend" }));
    expect(lakeCard().getAttribute("aria-expanded")).toBe("true");
    await user.click(lakeCard());
    expect(lakeCard().getAttribute("aria-expanded")).toBe("false");

    // Clicking the same URL again should reveal the trip again.
    await user.click(screen.getByRole("link", { name: "Lake weekend" }));
    expect(lakeCard().getAttribute("aria-expanded")).toBe("true");
  });

  it("opens a collapsed active trip from What's still open", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TravelPage />, { route: "/travel" });
    await user.click(await screen.findByRole("button", { name: /Ski weekend/ }));
    expect(screen.getByRole("button", { name: /Ski weekend/ }).getAttribute("aria-expanded")).toBe("false");

    const collect = screen.getByRole("list", { name: "Collect" });
    await user.click(within(collect).getByRole("link", { name: "Ski weekend" }));
    expect(screen.getByRole("button", { name: /Ski weekend/ }).getAttribute("aria-expanded")).toBe("true");
  });

  it("says so when the linked trip is gone", async () => {
    renderWithProviders(<TravelPage />, { route: "/travel?trip=city-break" });
    expect(await screen.findByText(/The app has no trip named/)).toBeTruthy();
    // A long key from a hand-edited link breaks, not the page width.
    expect(screen.getByText("city-break").className).toContain("break-all");
  });

  it("has a chart whose table shows the same numbers", async () => {
    const user = userEvent.setup();
    renderWithProviders(<TravelPage />);
    await user.click(await screen.findByRole("button", { name: "Show the numbers" }));
    const table = screen.getByRole("table", { name: "Cost by trip, by kind" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((r) => [...r.querySelectorAll("th, td")].map((c) => c.textContent))).toEqual(
      TRIPS.summary.rows.map((r) => COST_COLUMNS.map((c) => String(c.cell(r)))),
    );
    expect(rows.map((r) => [...r.querySelectorAll("th, td")].map((c) => c.textContent))).toEqual([
      ["Lake weekend", "$0", "$0", "$210", "$53", "$263"],
      ["Ski weekend", "$11", "$0", "$0", "$440", "$451"],
      ["Beach week", "$0", "$0", "$0", "$0", "$0"],
    ]);
    expect(screen.getByRole("img", { name: "Ski weekend cost the most, $451." })).toBeTruthy();
  });

  it("shows the empty state before any trips", async () => {
    payload = NO_TRIPS;
    renderWithProviders(<TravelPage />);
    expect(await screen.findByText("No trips yet.")).toBeTruthy();
    expect(screen.getByText("They come with the chat's next export.")).toBeTruthy();
  });

  it("shows an error with a retry, and the cards stay quiet", async () => {
    payload = "error";
    renderWithProviders(<TravelPage />);
    expect(await screen.findByText("Could not load trips.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
    // Trips, What's still open, Travel summary and the chart.
    expect(screen.getAllByText("Not loaded.")).toHaveLength(4);
    for (const empty of ["No trips coming up.", "Nothing open.", "No trips in these years."]) {
      expect(screen.queryByText(empty)).toBeNull();
    }
  });
});
