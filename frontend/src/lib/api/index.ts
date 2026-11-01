// The one module screens import for API calls, types and query keys.
// Names here are stable. Response types come from the generated schema.ts
// (see types.ts), so a backend change shows up as a type error here.

import { ApiError, query, request } from "./client";
import type {
  AccountDetail,
  AccountsResponse,
  CategoriesResponse,
  Checks,
  Dashboard,
  ImportRun,
  ImportRunDetail,
  LoginOut,
  Me,
  Meta,
  NetworthSeries,
  RangeKey,
  SeriesKey,
  TransactionFilters,
  TransactionsPage,
  User,
} from "./types";

export { ApiError, setUnauthorizedHandler } from "./client";
export * from "./types";
export * from "./recurring";
export * from "./utilities";
export * from "./rewards";
export * from "./trips";
export * from "./tripPlans";
export * from "./goals";

// ---------- Auth ----------

export async function login(username: string, password: string): Promise<User> {
  const data = await request<LoginOut>("POST", "/api/auth/login", {
    body: { username, password },
    handle401: false,
  });
  return data.user;
}

export function logout(): Promise<void> {
  return request<void>("POST", "/api/auth/logout", { handle401: false });
}

/** The signed-in user, or null when there is no session. */
export async function getMe(): Promise<Me | null> {
  try {
    return await request<Me>("GET", "/api/auth/me", { handle401: false });
  } catch (err) {
    if (err instanceof ApiError && err.status === 401) return null;
    throw err;
  }
}

/** Moves this browser to the demo household (admin). 503 when demo cannot start. */
export function startDemo(): Promise<void> {
  return request<void>("POST", "/api/demo/start");
}

export function changePassword(current_password: string, new_password: string): Promise<void> {
  return request<void>("POST", "/api/auth/password", { body: { current_password, new_password } });
}

// ---------- Meta and imports ----------

export function getMeta(): Promise<Meta> {
  return request("GET", "/api/meta");
}

export function getImports(): Promise<{ runs: ImportRun[] }> {
  return request("GET", "/api/imports");
}

export function getImportRun(runId: string): Promise<ImportRunDetail> {
  return request("GET", `/api/imports/${encodeURIComponent(runId)}`);
}

export function retryImport(runId: string): Promise<{ status: string }> {
  return request("POST", `/api/imports/${encodeURIComponent(runId)}/retry`);
}

// ---------- Accounts and net worth ----------

export function getAccounts(range?: RangeKey): Promise<AccountsResponse> {
  return request("GET", `/api/accounts${query({ range })}`);
}

export function getNetworthSeries(opts: { range?: RangeKey; series?: SeriesKey } = {}): Promise<NetworthSeries> {
  return request("GET", `/api/networth/series${query({ range: opts.range, series: opts.series })}`);
}

export function getAccount(accountKey: string, range?: RangeKey): Promise<AccountDetail> {
  return request("GET", `/api/accounts/${encodeURIComponent(accountKey)}${query({ range })}`);
}

// ---------- Dashboard ----------

export function getDashboard(month?: string): Promise<Dashboard> {
  return request("GET", `/api/dashboard${query({ month })}`);
}

// ---------- Checks ----------

/** App vs chat numbers for one run. Default: the newest monthly run with net worth numbers. 404 when there is none. */
export function getChecks(runId?: string): Promise<Checks> {
  return request("GET", `/api/checks${query({ run_id: runId })}`);
}

// ---------- Transactions and categories ----------

export function getTransactions(filters: TransactionFilters = {}): Promise<TransactionsPage> {
  return request("GET", `/api/transactions${query({ ...filters })}`);
}

export function getCategories(): Promise<CategoriesResponse> {
  return request("GET", "/api/categories");
}

// ---------- Query keys ----------

/** TanStack Query keys, so screens share one cache entry per request. */
export const queryKeys = {
  me: ["me"] as const,
  meta: ["meta"] as const,
  imports: ["imports"] as const,
  importRun: (runId: string) => ["imports", runId] as const,
  accounts: (range?: RangeKey) => ["accounts", range ?? null] as const,
  account: (key: string, range?: RangeKey) => ["account", key, range ?? null] as const,
  networthSeries: (range?: RangeKey, series?: SeriesKey) => ["networth-series", range ?? null, series ?? null] as const,
  dashboard: (month?: string) => ["dashboard", month ?? null] as const,
  checks: (runId?: string) => ["checks", runId ?? null] as const,
  transactions: (filters: TransactionFilters) => ["transactions", filters] as const,
  categories: ["categories"] as const,
};
