import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "../../../test/render";
import { VEHICLE } from "../../accounts/vehicles/fixtures";
import { centsToText, milliToText, parseDollarsCents, parsePctMilli, parseWhole } from "./parse";
import { toSettingsBody, VehiclesSettingsPage } from "./VehiclesSettingsPage";

// Fictional vehicle and numbers only.

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

let role: "admin" | "member" = "admin";
const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = String(input);
  if (url === "/api/auth/me") return json(200, { id: 1, username: "example", display_name: "Example", role });
  if (url === "/api/vehicles") return json(200, { as_of: "2026-10-21", vehicles: [VEHICLE] });
  if (url === "/api/vehicles/example-van/settings" && init?.method === "PUT") return json(200, VEHICLE);
  return json(404, { detail: "Not found." });
});

beforeEach(() => {
  role = "admin";
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("field parsing", () => {
  it("reads percents as milli-percent", () => {
    expect(parsePctMilli("12")).toBe(12_000);
    expect(parsePctMilli("0.5%")).toBe(500);
    expect(parsePctMilli("8.125")).toBe(8_125);
    expect(parsePctMilli("")).toBeNull();
    expect(parsePctMilli("ten")).toBeUndefined();
    expect(parsePctMilli("1.2345")).toBeUndefined();
  });

  it("reads dollars as cents", () => {
    expect(parseDollarsCents("$12,500")).toBe(1_250_000);
    expect(parseDollarsCents("99.5")).toBe(9_950);
    expect(parseDollarsCents(" ")).toBeNull();
    expect(parseDollarsCents("-4")).toBeUndefined();
  });

  it("reads whole numbers and writes fields back", () => {
    expect(parseWhole("12,000")).toBe(12_000);
    expect(parseWhole("1.5")).toBeUndefined();
    expect(milliToText(12_500)).toBe("12.5");
    expect(milliToText(null)).toBe("");
    expect(centsToText(2_500_000)).toBe("25,000");
    expect(centsToText(9_950)).toBe("99.50");
  });
});

const BLANK = {
  enabled: true,
  model_year: "",
  depreciation: "",
  expected_miles: "",
  mileage_rate: "",
  conversion_value: "",
  conversion_date: "",
  conversion_rate: "",
};

describe("toSettingsBody", () => {
  it("sends null for empty fields, so the defaults apply", () => {
    expect(toSettingsBody(BLANK, "2026-10-21")).toEqual({
      enabled: true,
      model_year: null,
      depreciation_milli_pct: null,
      expected_miles_year: null,
      mileage_milli_pct: null,
      conversion_value_cents: null,
      conversion_value_date: null,
      conversion_milli_pct: null,
    });
  });

  it("needs the conversion value and date together", () => {
    expect(toSettingsBody({ ...BLANK, conversion_value: "20,000" }, "2026-10-21")).toBe(
      "Give the conversion value and its date together, or leave both empty.",
    );
    expect(toSettingsBody({ ...BLANK, conversion_value: "20,000", conversion_date: "2026-11-01" }, "2026-10-21")).toBe(
      "The conversion date cannot be in the future.",
    );
  });

  it("checks the ranges", () => {
    expect(toSettingsBody({ ...BLANK, depreciation: "60" }, "2026-10-21")).toMatch(/0 to 50/);
    expect(toSettingsBody({ ...BLANK, mileage_rate: "6" }, "2026-10-21")).toMatch(/0 to 5/);
    expect(toSettingsBody({ ...BLANK, model_year: "1900" }, "2026-10-21")).toMatch(/Model year/);
  });
});

describe("Settings > Vehicles", () => {
  it("shows the vehicle, its mileage field and its settings", async () => {
    renderWithProviders(<VehiclesSettingsPage />);
    const card = await screen.findByRole("region", { name: "Example van" });
    expect(within(card).getByText("Update mileage")).toBeTruthy();
    expect((within(card).getByLabelText("Conversion value, $") as HTMLInputElement).value).toBe("20,000");
    expect((within(card).getByLabelText("Yearly loss, %") as HTMLInputElement).placeholder).toBe("10");
    expect(within(card).getByText(/Now 10%, from the model year\./)).toBeTruthy();
  });

  it("saves the settings as a full replace", async () => {
    const user = userEvent.setup();
    renderWithProviders(<VehiclesSettingsPage />);
    const card = await screen.findByRole("region", { name: "Example van" });
    const rate = within(card).getByLabelText("Conversion yearly loss, %");
    await user.clear(rate);
    await user.type(rate, "6.5");
    const saves = within(card).getAllByRole("button", { name: "Save" });
    await user.click(saves[saves.length - 1]!);
    await within(card).findByText("Saved. The estimate is updated.");
    const put = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT");
    expect(JSON.parse(String(put![1]!.body))).toEqual({
      enabled: true,
      model_year: 2020,
      depreciation_milli_pct: null,
      expected_miles_year: 8_000,
      mileage_milli_pct: null,
      conversion_value_cents: 2_000_000,
      conversion_value_date: "2026-07-15",
      conversion_milli_pct: 6_500,
    });
  });

  it("lets a member add mileage but not change settings", async () => {
    role = "member";
    renderWithProviders(<VehiclesSettingsPage />);
    const card = await screen.findByRole("region", { name: "Example van" });
    await within(card).findByText(/Only an admin can change these\./);
    expect((within(card).getByLabelText("Model year") as HTMLInputElement).disabled).toBe(true);
    expect((within(card).getByLabelText("Odometer") as HTMLInputElement).disabled).toBe(false);
    expect(within(card).getAllByRole("button", { name: "Save" })).toHaveLength(1);
  });
});
