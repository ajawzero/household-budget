import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../../../routes/AppRoutes";
import { renderWithProviders } from "../../../test/render";
import { STATUS, SYMBOLS } from "../../investments/fixtures";
import { classBody, PricesPage, symbolBody, symbolProblem } from "./PricesPage";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

let role: "admin" | "member" = "admin";
let putStatus = 200;
const puts: unknown[] = [];
const classPuts: unknown[] = [];
const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  if (url === "/api/auth/me") return json(200, { username: "alex", display_name: "Alex", role });
  if (url === "/api/prices/status") return json(200, STATUS);
  if (url === "/api/symbols/class" && init?.method === "PUT") {
    const body = JSON.parse(String(init.body));
    classPuts.push(body);
    return json(200, { ...SYMBOLS.rows[0], class_pick: body.asset_class });
  }
  if (url === "/api/symbols" && init?.method === "PUT") {
    puts.push(JSON.parse(String(init.body)));
    if (putStatus !== 200) return json(putStatus, { detail: "The note looks like it holds private data. Remove it and try again." });
    return json(200, SYMBOLS.rows[0]);
  }
  if (url === "/api/symbols") return json(200, SYMBOLS);
  return json(404, { detail: "Not found." });
});

beforeEach(() => {
  role = "admin";
  putStatus = 200;
  puts.length = 0;
  classPuts.length = 0;
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function card(title: string): HTMLElement {
  return screen.getByRole("heading", { level: 2, name: title }).closest("section")!;
}

describe("symbol form helpers", () => {
  const row = SYMBOLS.rows[0]!;

  it("checks the ticker only for daily price", () => {
    expect(symbolProblem("statement", "", null)).toBeNull();
    expect(symbolProblem("api", "", "STKIDX")).toBeNull();
    expect(symbolProblem("api", "", null)).toBe("Type a ticker. This holding has no printed symbol.");
    expect(symbolProblem("api", "brk.b", null)).toBeNull();
    expect(symbolProblem("api", "BAD TICKER", null)).toBe("Use up to 12 letters, digits, dots or dashes.");
  });

  it("builds the PUT body", () => {
    expect(symbolBody(row, "rule", "X", "n")).toEqual({ account_key: "brokerage", holding_key: "stock-fund", price_mode: null });
    expect(symbolBody(row, "api", " vtsax ", "  ")).toEqual({
      account_key: "brokerage",
      holding_key: "stock-fund",
      price_mode: "api",
      symbol: "VTSAX",
      note: null,
    });
    // The printed ticker needs no override symbol.
    expect(symbolBody(row, "api", "STKIDX", "")).toMatchObject({ symbol: null });
    expect(symbolBody(row, "statement", "VTSAX", "Trust")).toMatchObject({ price_mode: "statement", symbol: null, note: "Trust" });
    expect(classBody(row, "statement")).toEqual({ account_key: "brokerage", holding_key: "stock-fund", asset_class: null });
    expect(classBody(row, "bond")).toEqual({ account_key: "brokerage", holding_key: "stock-fund", asset_class: "bond" });
  });
});

describe("PricesPage", () => {
  it("shows the run, the key, the tickers and the 1% check", async () => {
    renderWithProviders(<PricesPage />, { route: "/settings/prices" });
    await screen.findByText("Daily price run");
    const run = card("Daily price run");
    expect(within(run).getByText("Not set")).toBeTruthy();
    expect(run.textContent).toContain("Add TIINGO_API_KEY to the server env file.");
    expect(run.textContent).toContain("06:30 America/Chicago, Tuesday to Saturday");
    expect(run.textContent).toContain("Updated 1 of 2 tickers. 2 Tiingo calls, 1 Yahoo call.");
    expect(run.textContent).toContain("Tiingo hit a rate limit and stopped until the next run.");

    const check = card("1% check");
    expect(check.textContent).toContain("9 statement prices checked. 1 waiting on a close.");
    const flagged = within(check).getByRole("row", { name: /Stock Index Fund/ });
    expect(flagged.textContent).toContain("Aug 31, 2026");
    expect(flagged.textContent).toContain("Close from Aug 28");
    expect(flagged.textContent).toContain("−1.25%");

    const tickers = card("Tickers");
    const bond = within(tickers).getByRole("row", { name: /BNDFND/ });
    expect(bond.textContent).toContain("Jun 12");
    expect(bond.textContent).toContain("Yahoo");
    expect(bond.textContent).toContain("Stale");
    expect(bond.textContent).toContain("Symbol not found.");
    expect(within(tickers).getByRole("row", { name: /STKIDX/ }).textContent).toContain("Current");
    // Wide tables scroll by keyboard, and say which table they hold.
    expect(within(check).getByRole("region", { name: "1% check" }).getAttribute("tabindex")).toBe("0");
    expect(within(tickers).getByRole("region", { name: "Tickers" })).toBeTruthy();
  });

  it("lists every holding's mode, and members cannot change them", async () => {
    role = "member";
    renderWithProviders(<PricesPage />, { route: "/settings/prices" });
    const list = await screen.findByRole("list", { name: "Holdings, price modes and classes" });
    const items = within(list).getAllByRole("listitem");
    expect(items[0]!.textContent).toContain("Stock Index Fund");
    expect(items[0]!.textContent).toContain("printed STKIDX");
    expect(items[0]!.textContent).toContain("by rule");
    expect(items[1]!.textContent).toContain("TGTTR");
    expect(items[1]!.textContent).toContain("set by hand");
    expect(items[1]!.textContent).toContain("Note: Proxy fund");
    expect(items[0]!.textContent).toContain("ClassUS stockfrom the statement");
    expect(items[1]!.textContent).toContain("ClassTarget dateset by hand");
    await screen.findByText("Only an admin can change a price mode or a class.");
    expect(screen.queryByRole("button", { name: /Change the price mode/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Change the class/ })).toBeNull();
  });

  it("lets an admin pick a class, and go back to the statement's", async () => {
    const user = userEvent.setup();
    renderWithProviders(<PricesPage />, { route: "/settings/prices" });
    await user.click(await screen.findByRole("button", { name: "Change the class for Stock Index Fund, Sample brokerage" }));
    const form = screen.getByRole("form", { name: "Class for Stock Index Fund" });
    const select = within(form).getByLabelText("Class") as HTMLSelectElement;
    expect(select.value).toBe("statement");
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual([
      "From the statement (US stock)",
      "US stock",
      "International stock",
      "Bonds",
      "Cash",
      "Target date",
      "Balanced",
      "Real estate",
      "Other",
    ]);
    expect(form.textContent).toContain("It never changes its value or its price mode.");
    await user.selectOptions(select, "intl-stock");
    await user.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(classPuts).toEqual([{ account_key: "brokerage", holding_key: "stock-fund", asset_class: "intl-stock" }]));
    await waitFor(() => expect(screen.queryByRole("form", { name: "Class for Stock Index Fund" })).toBeNull());
    expect(puts).toHaveLength(0);

    // A pick with no class on the statement says so, and "From the statement" removes it.
    await user.click(screen.getByRole("button", { name: "Change the class for Target Trust 2050, Sample 401k" }));
    const trust = screen.getByRole("form", { name: "Class for Target Trust 2050" });
    const pick = within(trust).getByLabelText("Class") as HTMLSelectElement;
    expect(pick.value).toBe("target-date");
    expect(pick.options[0]!.textContent).toBe("From the statement (no class)");
    await user.selectOptions(pick, "statement");
    await user.click(within(trust).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(classPuts).toHaveLength(2));
    expect(classPuts[1]).toEqual({ account_key: "k401", holding_key: "trust", asset_class: null });
  });

  it("lets an admin set a ticker by hand", async () => {
    const user = userEvent.setup();
    renderWithProviders(<PricesPage />, { route: "/settings/prices" });
    await user.click(await screen.findByRole("button", { name: "Change the price mode for Stock Index Fund, Sample brokerage" }));
    const form = screen.getByRole("form", { name: "Price mode for Stock Index Fund" });
    const ticker = within(form).getByLabelText("Ticker");
    expect(ticker.hasAttribute("disabled")).toBe(true);
    await user.selectOptions(within(form).getByLabelText("Mode"), "api");
    await user.type(ticker, "bad ticker");
    await user.click(within(form).getByRole("button", { name: "Save" }));
    expect(within(form).getByText("Use up to 12 letters, digits, dots or dashes.")).toBeTruthy();
    expect(puts).toHaveLength(0);
    await user.clear(ticker);
    await user.type(ticker, "vtsax");
    await user.type(within(form).getByLabelText(/Note/), "Same fund, other class");
    await user.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0]).toEqual({
      account_key: "brokerage",
      holding_key: "stock-fund",
      price_mode: "api",
      symbol: "VTSAX",
      note: "Same fund, other class",
    });
    await waitFor(() => expect(screen.queryByRole("form", { name: "Price mode for Stock Index Fund" })).toBeNull());
  });

  it("puts a holding back on its rule, and shows the server's message on an error", async () => {
    const user = userEvent.setup();
    renderWithProviders(<PricesPage />, { route: "/settings/prices" });
    await user.click(await screen.findByRole("button", { name: "Change the price mode for Target Trust 2050, Sample 401k" }));
    const form = screen.getByRole("form", { name: "Price mode for Target Trust 2050" });
    expect((within(form).getByLabelText("Mode") as HTMLSelectElement).value).toBe("api");
    expect((within(form).getByLabelText("Ticker") as HTMLInputElement).value).toBe("TGTTR");
    putStatus = 422;
    await user.selectOptions(within(form).getByLabelText("Mode"), "rule");
    expect(form.textContent).toContain("The rule gives Statement.");
    await user.click(within(form).getByRole("button", { name: "Save" }));
    expect((await within(form).findByRole("alert")).textContent).toBe(
      "The note looks like it holds private data. Remove it and try again.",
    );
    expect(puts[0]).toEqual({ account_key: "k401", holding_key: "trust", price_mode: null });
    await user.click(within(form).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("form", { name: "Price mode for Target Trust 2050" })).toBeNull();
  });

  it("is linked from Settings and routed", async () => {
    renderWithProviders(<AppRoutes />, { route: "/settings" });
    const link = await screen.findByRole("link", { name: /Prices/ });
    expect(link.getAttribute("href")).toBe("/settings/prices");
    await userEvent.setup().click(link);
    expect(await screen.findByRole("heading", { level: 1, name: "Prices" })).toBeTruthy();
  });
});
