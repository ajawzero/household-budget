// Transaction filters kept in the URL (PLAN 8.2). Search text is never put in
// the URL: it lives in page state and goes to the server in a POST body.
// Values that do not look right are dropped, so an old or edited link never
// turns into a 422.

import type { Sort, TxnSearchBody } from "./api";

const KEY = /^[a-z][a-z0-9_-]{0,63}$/;
const MONTH = /^[0-9]{4}-(0[1-9]|1[0-2])$/;
const DATE = /^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])$/;
const DOLLARS = /^[0-9]{1,9}(\.[0-9]{1,2})?$/;
const MAX_ITEMS = 50;

export const SORTS: { value: Sort; label: string }[] = [
  { value: "date_desc", label: "Date (new to old)" },
  { value: "date_asc", label: "Date (old to new)" },
  { value: "amount_desc", label: "Amount (high to low)" },
  { value: "amount_asc", label: "Amount (low to high)" },
];

/** Review queue views. Each is its own filter, since the server combines filters with AND. */
export type View = "all" | "review" | "uncategorized";

export type YesNo = "yes" | "no";

export interface TxnFilterState {
  accounts: string[];
  categories: string[];
  groups: string[];
  owners: string[];
  months: string[];
  from: string | null;
  to: string | null;
  /** Dollars as typed ("50" or "12.50"), compared to the absolute amount. */
  min: string | null;
  max: string | null;
  direction: "in" | "out" | null;
  transfer: YesNo | null;
  reimbursable: YesNo | null;
  edited: YesNo | null;
  hidden: "exclude" | "include" | "only";
  /** Rows tagged with this trip, whatever their date. */
  trip: string | null;
  view: View;
  sort: Sort;
}

export const EMPTY_FILTERS: TxnFilterState = {
  accounts: [],
  categories: [],
  groups: [],
  owners: [],
  months: [],
  from: null,
  to: null,
  min: null,
  max: null,
  direction: null,
  transfer: null,
  reimbursable: null,
  edited: null,
  hidden: "exclude",
  trip: null,
  view: "all",
  sort: "date_desc",
};

function list(params: URLSearchParams, name: string, re: RegExp): string[] {
  const out: string[] = [];
  for (const v of params.getAll(name)) if (re.test(v) && !out.includes(v)) out.push(v);
  return out.slice(0, MAX_ITEMS);
}

function one<T extends string>(params: URLSearchParams, name: string, allowed: readonly T[]): T | null {
  const v = params.get(name);
  return v !== null && (allowed as readonly string[]).includes(v) ? (v as T) : null;
}

function match(params: URLSearchParams, name: string, re: RegExp): string | null {
  const v = params.get(name);
  return v !== null && re.test(v) ? v : null;
}

const YES_NO = ["yes", "no"] as const;

export function parseFilters(params: URLSearchParams): TxnFilterState {
  return {
    accounts: list(params, "account", KEY),
    categories: list(params, "category", KEY),
    groups: list(params, "group", KEY),
    owners: list(params, "owner", KEY),
    months: list(params, "month", MONTH),
    from: match(params, "from", DATE),
    to: match(params, "to", DATE),
    min: match(params, "min", DOLLARS),
    max: match(params, "max", DOLLARS),
    direction: one(params, "dir", ["in", "out"] as const),
    transfer: one(params, "transfer", YES_NO),
    reimbursable: one(params, "reimbursable", YES_NO),
    edited: one(params, "edited", YES_NO),
    hidden: one(params, "hidden", ["include", "only"] as const) ?? "exclude",
    trip: match(params, "trip", KEY),
    view: one(params, "view", ["review", "uncategorized"] as const) ?? "all",
    sort: one(params, "sort", ["date_desc", "date_asc", "amount_desc", "amount_asc"] as const) ?? "date_desc",
  };
}

/** The URL for a filter state. Defaults are left out. Other params (like an open tab) are kept. */
export function writeFilters(state: TxnFilterState, base: URLSearchParams = new URLSearchParams()): URLSearchParams {
  const next = new URLSearchParams(base);
  for (const name of [
    "account",
    "category",
    "group",
    "owner",
    "month",
    "from",
    "to",
    "min",
    "max",
    "dir",
    "transfer",
    "reimbursable",
    "edited",
    "hidden",
    "trip",
    "view",
    "sort",
  ]) {
    next.delete(name);
  }
  for (const v of state.accounts) next.append("account", v);
  for (const v of state.categories) next.append("category", v);
  for (const v of state.groups) next.append("group", v);
  for (const v of state.owners) next.append("owner", v);
  for (const v of state.months) next.append("month", v);
  const singles: [string, string | null][] = [
    ["from", state.from],
    ["to", state.to],
    ["min", state.min],
    ["max", state.max],
    ["dir", state.direction],
    ["transfer", state.transfer],
    ["reimbursable", state.reimbursable],
    ["edited", state.edited],
    ["hidden", state.hidden === "exclude" ? null : state.hidden],
    ["trip", state.trip],
    ["view", state.view === "all" ? null : state.view],
    ["sort", state.sort === "date_desc" ? null : state.sort],
  ];
  for (const [name, v] of singles) if (v) next.set(name, v);
  return next;
}

/** "12.5" dollars to 1250 cents, with integer math only. */
export function dollarsToCents(text: string | null): number | null {
  if (text === null || !DOLLARS.test(text)) return null;
  const [whole = "0", frac = ""] = text.split(".");
  return Number(whole) * 100 + Number(frac.padEnd(2, "0"));
}

function yesNo(v: YesNo | null): boolean | null {
  return v === null ? null : v === "yes";
}

/** The POST body for a filter state and the search box text. */
export function searchBody(state: TxnFilterState, q: string): TxnSearchBody {
  const body: TxnSearchBody = { sort: state.sort, hidden: state.hidden };
  const text = q.trim();
  if (text) body.q = text.slice(0, 100);
  if (state.accounts.length) body.accounts = state.accounts;
  if (state.categories.length) body.categories = state.categories;
  if (state.groups.length) body.category_groups = state.groups;
  if (state.owners.length) body.owners = state.owners;
  if (state.months.length) body.months = state.months;
  if (state.from) body.date_from = state.from;
  if (state.to) body.date_to = state.to;
  const min = dollarsToCents(state.min);
  const max = dollarsToCents(state.max);
  if (min !== null) body.amount_min_cents = min;
  if (max !== null) body.amount_max_cents = max;
  if (state.direction) body.direction = state.direction;
  const transfer = yesNo(state.transfer);
  const reimbursable = yesNo(state.reimbursable);
  const edited = yesNo(state.edited);
  if (transfer !== null) body.is_transfer = transfer;
  if (reimbursable !== null) body.is_reimbursable = reimbursable;
  if (edited !== null) body.edited = edited;
  if (state.trip) body.trip_key = state.trip;
  if (state.view === "review") body.needs_review = true;
  if (state.view === "uncategorized") body.uncategorized = true;
  return body;
}

/** How many filters are set, for the Filters button badge. View and sort are not counted. */
export function filterCount(state: TxnFilterState): number {
  return (
    state.accounts.length +
    state.categories.length +
    state.groups.length +
    state.owners.length +
    state.months.length +
    (state.from ? 1 : 0) +
    (state.to ? 1 : 0) +
    (state.min ? 1 : 0) +
    (state.max ? 1 : 0) +
    (state.direction ? 1 : 0) +
    (state.transfer ? 1 : 0) +
    (state.reimbursable ? 1 : 0) +
    (state.edited ? 1 : 0) +
    (state.hidden !== "exclude" ? 1 : 0) +
    (state.trip ? 1 : 0)
  );
}

/** Clears the filters, keeping the view and sort. */
export function clearFilters(state: TxnFilterState): TxnFilterState {
  return { ...EMPTY_FILTERS, view: state.view, sort: state.sort };
}

/** Toggles one value in a list. */
export function toggle(values: string[], value: string): string[] {
  return values.includes(value) ? values.filter((v) => v !== value) : [...values, value];
}
