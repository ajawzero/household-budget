import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "../../../test/render";
import type { AccountRow } from "../../../lib/api";
import { EquityLine, RowEstBadge } from "../AccountRowItem";
import { GroupCard } from "../GroupCard";
import { EquityNotesProvider } from "./EquityNotes";
import { equityNote, milliPctText, todayIso, type ValuationItem } from "./api";
import { VEHICLE } from "./fixtures";
import { MileageForm, parseMiles } from "./MileageForm";
import { mileageText, ValueBreakdown } from "./VehiclePanel";

// Fictional vehicles and numbers only.

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

let postReply: () => Response = () => json(201, VEHICLE);
const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  if (url === "/api/vehicles/example-van/mileage" && init?.method === "POST") return postReply();
  if (url === "/api/valuations") return json(200, { as_of: "2026-10-21", index: {}, items: [HOME] });
  return json(404, { detail: "Not found." });
});

beforeEach(() => {
  postReply = () => json(201, VEHICLE);
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("parseMiles", () => {
  it("takes plain numbers, commas and a miles suffix", () => {
    expect(parseMiles("4210")).toBe(4_210);
    expect(parseMiles(" 54,210 ")).toBe(54_210);
    expect(parseMiles("4210 mi")).toBe(4_210);
    expect(parseMiles("54.5")).toBeNull();
    expect(parseMiles("-3")).toBeNull();
    expect(parseMiles("")).toBeNull();
  });
});

describe("MileageForm", () => {
  it("shows the last reading and defaults the date to today", () => {
    renderWithProviders(<MileageForm vehicle={VEHICLE} />);
    expect(screen.getByText("Last reading 30,000 miles on Oct 1, 2026.")).toBeTruthy();
    expect((screen.getByLabelText("Date") as HTMLInputElement).value).toBe(todayIso());
  });

  it("posts the reading and says it saved", async () => {
    const user = userEvent.setup();
    renderWithProviders(<MileageForm vehicle={VEHICLE} />);
    await user.type(screen.getByLabelText("Odometer"), "31,250");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText(/Saved 31,250 miles on/);
    const call = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/mileage"));
    expect(call).toBeDefined();
    expect(JSON.parse(String(call![1]!.body))).toEqual({ miles: 31_250, reading_date: todayIso() });
    expect((screen.getByLabelText("Odometer") as HTMLInputElement).value).toBe("");
  });

  it("stops a lower reading before sending it", async () => {
    const user = userEvent.setup();
    renderWithProviders(<MileageForm vehicle={VEHICLE} />);
    await user.type(screen.getByLabelText("Odometer"), "29,000");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/Mileage cannot go down/)).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects text that is not miles", async () => {
    const user = userEvent.setup();
    renderWithProviders(<MileageForm vehicle={VEHICLE} />);
    await user.type(screen.getByLabelText("Odometer"), "lots");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Enter the odometer as a whole number of miles.")).toBeTruthy();
  });

  it("shows the server's message when the save fails", async () => {
    postReply = () => json(422, { detail: "Mileage cannot go down. The Oct 1 reading is higher." });
    const user = userEvent.setup();
    renderWithProviders(<MileageForm vehicle={{ ...VEHICLE, readings: [] }} />);
    expect(screen.getByText(/No readings yet/)).toBeTruthy();
    await user.type(screen.getByLabelText("Odometer"), "31,000");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Mileage cannot go down. The Oct 1 reading is higher.")).toBeTruthy();
  });
});

describe("ValueBreakdown", () => {
  it("lists the KBB value, both parts, their total, mileage and the loan", () => {
    render(<ValueBreakdown vehicle={VEHICLE} />);
    expect(screen.getByText("KBB value")).toBeTruthy();
    expect(screen.getByText("Base vehicle (KBB) now").nextSibling?.textContent).toContain("$30,000, loses 10% a year");
    expect(screen.getByText("Conversion now").nextSibling?.textContent).toContain("$18,000, loses 8% a year");
    // The parts come first, then the total they add up to.
    const terms = screen.getAllByRole("term").map((t) => t.textContent);
    expect(terms.indexOf("Total now")).toBe(terms.indexOf("Conversion now") + 1);
    expect(screen.getByText("Mileage").nextSibling?.textContent).toContain("1,500 miles over pace as of Oct 1, 2026");
    expect(screen.getByText("Example van loan").nextSibling?.textContent).toContain("$20,000 owed (estimated)");
    expect(screen.getByText(VEHICLE.estimate!.note, { selector: "p" })).toBeTruthy();
  });

  it("words mileage under pace and on pace", () => {
    expect(mileageText(-800, null)).toBe("800 miles under pace");
    expect(mileageText(0, "2026-10-01")).toBe("On pace as of Oct 1, 2026");
    expect(milliPctText(500)).toBe("0.5%");
    expect(milliPctText(12_000)).toBe("12%");
  });
});

const HOME: ValuationItem = {
  account_key: "example-home",
  display_name: "Example home",
  kind: "home",
  secured_by: null,
  value_cents: 40_000_000,
  stale: false,
  real: null,
  estimate: {
    value_cents: 40_000_000,
    method: "fhfa_hpi",
    based_on: "2026-06-30",
    through: "2026-04-01",
    note: "Estimated from the Jun 30 valuation, moved by the FHFA Austin index through 2026 Q2.",
  },
  inputs: { home: null, vehicle: null, loan: null },
  note: null,
  equity: { value_cents: 40_000_000, loan_cents: -10_000_000, equity_cents: 30_000_000, underwater: false, estimated: true },
};

const LOAN: ValuationItem = {
  ...HOME,
  account_key: "example-van-loan",
  display_name: "Example van loan",
  kind: "loan",
  secured_by: "example-van",
  value_cents: -2_000_000,
  estimate: {
    value_cents: -2_000_000,
    method: "amortization",
    based_on: "2026-08-31",
    through: "2026-09-15",
    note: "Estimated from the Aug 31 balance, 6.5% APR and 1 payment since.",
  },
  equity: null,
};

const VAN: ValuationItem = { ...HOME, account_key: "example-van", kind: "vehicle", estimate: null };

describe("equity notes", () => {
  it("names each estimated part", () => {
    expect(equityNote([HOME], "example-home")).toBe(`Equity uses estimates. Value: ${HOME.estimate!.note}`);
    expect(equityNote([VAN, LOAN], "example-van")).toBe(`Equity uses estimates. Example van loan: ${LOAN.estimate!.note}`);
  });

  it("is null when nothing in the equity is estimated", () => {
    const real = { ...HOME, equity: { ...HOME.equity!, estimated: false } };
    expect(equityNote([real], "example-home")).toBeNull();
    expect(equityNote(undefined, "example-home")).toBeNull();
    expect(equityNote([HOME], "other")).toBeNull();
  });

  it("puts an est. badge with the note on the equity line", () => {
    const equity = { value_cents: 1, loan_cents: 0, equity_cents: 2_000_000, underwater: false };
    render(<EquityLine equity={equity} note="Equity uses estimates. Value: from the index." />);
    expect(screen.getByText(/estimated\. Equity uses estimates\. Value: from the index\./)).toBeTruthy();
  });

  it("has no badge without a note", () => {
    const equity = { value_cents: 1, loan_cents: 0, equity_cents: 2_000_000, underwater: false };
    render(<EquityLine equity={equity} />);
    expect(screen.queryByText("est.")).toBeNull();
  });
});

describe("RowEstBadge", () => {
  it("uses the estimate's own note", () => {
    render(
      <RowEstBadge
        row={{
          estimate: { kind: "loan", based_on: "2026-08-31", through: null, note: "Estimated from payments since the Aug 31 statement." },
          source: "statement",
          as_of: "2026-08-31",
        }}
      />,
    );
    expect(screen.getByText("estimated. Estimated from payments since the Aug 31 statement.")).toBeTruthy();
  });

  it("falls back to the monthly run's estimate", () => {
    render(<RowEstBadge row={{ estimate: null, source: "estimate", as_of: "2026-09-30" }} />);
    expect(screen.getByText(/Estimated by the monthly run/)).toBeTruthy();
  });

  it("shows nothing for a real balance", () => {
    const { container } = render(<RowEstBadge row={{ estimate: null, source: "statement", as_of: "2026-09-30" }} />);
    expect(container.innerHTML).toBe("");
  });
});

describe("Accounts rows with estimates", () => {
  const homeRow: AccountRow = {
    account_key: "example-home",
    display_name: "Example home",
    institution: "",
    owner: "joint",
    owner_label: "Joint",
    type: "property",
    subtype: null,
    group: "real_estate",
    nw_group: "real-estate",
    is_asset: true,
    include_in_net_worth: true,
    tracked_by: "statements",
    active: true,
    dormant: false,
    balance_cents: 40_000_000,
    as_of: "2026-06-30",
    source: "valuation",
    stale: false,
    missing: false,
    change_cents: null,
    sparkline: [],
    equity: { value_cents: 40_000_000, loan_cents: -10_000_000, equity_cents: 30_000_000, underwater: false },
    estimate: { kind: "home", based_on: "2026-06-30", through: "2026-04-01", note: HOME.estimate!.note },
  };

  it("badges the balance and the equity, and dates the estimate from its start", async () => {
    renderWithProviders(
      <EquityNotesProvider>
        <GroupCard
          group={{ group: "real_estate", label: "Real estate", is_liability: false, total_cents: 40_000_000, change_cents: 0, change_pct: null, estimated: true, accounts: [homeRow] }}
          range="YTD"
          onOpen={() => {}}
        />
      </EquityNotesProvider>,
    );
    expect(screen.getByText(`estimated. ${HOME.estimate!.note}`)).toBeTruthy();
    await screen.findByText(`estimated. Equity uses estimates. Value: ${HOME.estimate!.note}`);
    expect(screen.getByText("from Jun 30")).toBeTruthy();
  });
});
