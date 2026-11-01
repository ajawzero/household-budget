import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  getAccount,
  getAccounts,
  getCategories,
  getDashboard,
  getMe,
  getNetworthSeries,
  getTransactions,
  logout,
  setUnauthorizedHandler,
} from "./index";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api client", () => {
  it("calls the unauthorized handler on a 401 from a data route", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(401, { detail: "Please sign in." })));
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    await expect(getAccounts()).rejects.toBeInstanceOf(ApiError);
    expect(handler).toHaveBeenCalledOnce();
  });

  it("treats 401 from me as signed out without redirecting", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(401, { detail: "Please sign in." })));
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    expect(await getMe()).toBeNull();
    expect(handler).not.toHaveBeenCalled();
  });

  it("builds the transactions query from set filters only", async () => {
    const fetchMock = vi.fn(async () => json(200, { rows: [], next_cursor: null }));
    vi.stubGlobal("fetch", fetchMock);
    await getTransactions({ account: "example-card", month: "2026-09", category: "", cursor: null, limit: 100 });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/transactions?account=example-card&month=2026-09&limit=100",
      expect.objectContaining({ method: "GET", credentials: "same-origin" }),
    );
  });

  it("builds the Phase 2 urls", async () => {
    const fetchMock = vi.fn(async () => json(200, {}));
    vi.stubGlobal("fetch", fetchMock);
    await getAccounts("YTD");
    await getNetworthSeries({ range: "1Y", series: "credit_cards" });
    await getAccount("example card", "3M");
    await getDashboard("2026-09");
    await getDashboard();
    expect(fetchMock.mock.calls.map((c) => (c as unknown as [string])[0])).toEqual([
      "/api/accounts?range=YTD",
      "/api/networth/series?range=1Y&series=credit_cards",
      "/api/accounts/example%20card?range=3M",
      "/api/dashboard?month=2026-09",
      "/api/dashboard",
    ]);
  });

  it("returns undefined for 204 and uses a plain message for non-JSON errors", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 204 })));
    await expect(logout()).resolves.toBeUndefined();

    vi.stubGlobal("fetch", vi.fn(async () => new Response("oops", { status: 500 })));
    await expect(getCategories()).rejects.toThrow("Something went wrong. Try again.");
  });

  it("uses the server's message for a 404", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json(404, { detail: "No such account." })));
    await expect(getAccount("nope")).rejects.toThrow("No such account.");
  });
});
