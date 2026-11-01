import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { monthStartTicks, partialRuns } from "../../components/charts/AreaChart";
import { groupColor } from "../../components/charts/colors";
import { sharePercents } from "../../components/charts/StackedBar";
import type { AccountDetail, AccountGroup, AccountRow, AccountsResponse, FlowPeriod, NetworthSeries } from "../../lib/api";
import { formatDate, formatMoney, MINUS } from "../../lib/format";
import { renderWithProviders } from "../../test/render";
import { AccountDrawer, formatApr } from "./AccountDrawer";
import { InvestmentPanel, signedMoney, signedPct } from "./InvestmentPanel";
import { EquityLine } from "./AccountRowItem";
import { AccountsPage } from "./AccountsPage";
import { GroupCard } from "./GroupCard";
import { balanceState, metaLine, otherName, ownerColors, ownerGroupTypes } from "./model";
import { seriesPoints } from "./NetWorthCard";
import { SummaryCard } from "./SummaryCard";
import { EquityNotesProvider } from "./vehicles/EquityNotes";
import type { ValuationItem } from "./vehicles/api";

// Fictional accounts and numbers only.

function row(over: Partial<AccountRow>): AccountRow {
  return {
    account_key: "example-checking",
    display_name: "Example checking",
    institution: "Example Bank",
    owner: "joint",
    owner_label: "Joint",
    type: "checking",
    subtype: null,
    group: "cash",
    nw_group: "cash-and-misc",
    is_asset: true,
    include_in_net_worth: true,
    tracked_by: "statements",
    active: true,
    dormant: false,
    balance_cents: 120_000,
    as_of: "2026-09-30",
    source: "statement",
    stale: false,
    missing: false,
    change_cents: 10_000,
    sparkline: [],
    equity: null,
    estimate: null,
    ...over,
  };
}

const CARD = row({
  account_key: "example-card",
  display_name: "Example card",
  institution: "Example Card Co",
  type: "credit_card",
  group: "credit_cards",
  nw_group: "credit-cards",
  is_asset: false,
  balance_cents: -63_200,
  // Owed went from $832 to $632: the signed change is +$200, which is good.
  change_cents: 20_000,
});

const TRACKED = row({
  account_key: "example-store-card",
  display_name: "Example store card",
  type: "store_card",
  group: "credit_cards",
  nw_group: null,
  is_asset: false,
  include_in_net_worth: false,
  tracked_by: "payments",
  balance_cents: null,
  as_of: null,
  source: "tracked",
  change_cents: null,
});

const CARDS_GROUP: AccountGroup = {
  group: "credit_cards",
  label: "Credit cards",
  is_liability: true,
  total_cents: -63_200,
  change_cents: 20_000,
  change_pct: "24.0",
  estimated: false,
  accounts: [CARD, TRACKED],
};

const SERIES: NetworthSeries = {
  series: "networth",
  range: { key: "YTD", start: "2026-01-01", end: "2026-01-05" },
  points: [
    { date: "2026-01-01", value_cents: 100_000, partial: true, estimated: false },
    { date: "2026-01-02", value_cents: 110_000, partial: true, estimated: false },
    { date: "2026-01-03", value_cents: 105_050, partial: false, estimated: false },
    { date: "2026-01-04", value_cents: 130_000, partial: false, estimated: false },
    { date: "2026-01-05", value_cents: 125_000, partial: true, estimated: false },
  ],
  start_cents: 100_000,
  end_cents: 125_000,
  change_cents: 25_000,
  change_pct: "25.0",
  estimated_accounts: [],
};

const ACCOUNTS: AccountsResponse = {
  as_of: "2026-10-21",
  range: { key: "YTD", start: "2026-01-01", end: "2026-10-21" },
  groups: [
    { group: "cash", label: "Cash", is_liability: false, total_cents: 120_000, change_cents: 10_000, change_pct: "9.1", estimated: false, accounts: [row({})] },
    CARDS_GROUP,
  ],
  summary: {
    assets_cents: 120_000,
    liabilities_cents: 63_200,
    net_worth_cents: 56_800,
    by_type: [
      { key: "cash", label: "Cash", cents: 120_000, is_liability: false },
      { key: "credit_cards", label: "Credit cards", cents: -63_200, is_liability: true },
    ],
    by_owner: [
      { key: "cash-and-misc", label: "Cash and misc", cents: 120_000, is_liability: false },
      { key: "credit-cards", label: "Credit cards", cents: -63_200, is_liability: true },
    ],
  },
  notes: { partial: true, missing: [], stale: ["example-card"], tracked: ["example-store-card"], excluded: [], estimated: [] },
};

const DETAIL: AccountDetail = {
  account: CARD,
  points: [
    { date: "2026-09-29", balance_cents: -60_000, estimated: false, real: false },
    { date: "2026-09-30", balance_cents: -63_200, estimated: false, real: true },
  ],
  statements: [
    {
      period_start: "2026-09-01",
      period_end: "2026-09-30",
      opening_cents: -60_000,
      closing_cents: -63_200,
      balance_cents: null,
      net_flows_cents: null,
      market_change_cents: null,
      txn_count: 3,
      reconciled: true,
    },
  ],
  investment: null,
  recent_transactions: [],
  holdings: [],
  terms: {
    apr_milli_pct: 24_990,
    rate_type: "variable",
    payoff_date: null,
    priority: null,
    policy_note: null,
    credit_limit_cents: 500_000,
    original_cents: null,
    min_payment_cents: null,
    autopay: "full",
  },
  about: { custodian: null, advisor: null, servicer: null, notes: null, opened_on: null, closed_on: null },
};

const NO_TERMS = { ...DETAIL.terms, apr_milli_pct: null, rate_type: null, credit_limit_cents: null, autopay: null };

// A dormant IRA held at another custodian, with the chat's note.
const IRA_DETAIL: AccountDetail = {
  ...DETAIL,
  account: row({
    account_key: "example-ira",
    display_name: "Example IRA",
    institution: "Example Brokerage",
    type: "retirement",
    group: "retirement",
    nw_group: "retirement",
    dormant: true,
    balance_cents: 2_500_000,
  }),
  statements: [],
  terms: NO_TERMS,
  about: {
    custodian: "Example Trust Bank",
    advisor: "Example Advisors",
    servicer: null,
    notes: "Rollover done. New money goes to the index fund.",
    opened_on: "2015-04-01",
    closed_on: null,
  },
};

const SAVINGS_DETAIL: AccountDetail = {
  ...DETAIL,
  account: row({ account_key: "example-savings", display_name: "Example savings", type: "savings" }),
  statements: [],
  terms: { ...NO_TERMS, apr_milli_pct: 4_100, rate_type: "variable" },
  // The custodian is the institution again, so the drawer leaves it out.
  about: { ...DETAIL.about, custodian: " example bank" },
};

// A loan with its original amount (format 1.4).
const LOAN_DETAIL: AccountDetail = {
  ...DETAIL,
  account: { ...CARD, account_key: "example-loan", display_name: "Example loan", type: "loan", group: "loans", nw_group: "loans" },
  statements: [],
  terms: { ...NO_TERMS, apr_milli_pct: 6_490, rate_type: "fixed", original_cents: 4_200_000 },
};

// A stale card whose balance is the monthly run's estimate.
const STALE_DETAIL: AccountDetail = {
  ...DETAIL,
  account: { ...CARD, account_key: "example-stale", stale: true, source: "estimate" },
};

const HOME_NOTE = "Estimated from the Jun 30 valuation, moved by the FHFA Austin index through 2026 Q2.";
const MORTGAGE_NOTE = "Estimated from the Aug 31 balance, 6.5% APR and 1 payment since.";

const HOME_DETAIL: AccountDetail = {
  ...DETAIL,
  account: row({
    account_key: "example-home",
    display_name: "Example home",
    type: "property",
    group: "real_estate",
    nw_group: "real-estate",
    as_of: "2026-06-30",
    source: "valuation",
    balance_cents: 40_000_000,
    equity: { value_cents: 40_000_000, loan_cents: -10_000_000, equity_cents: 30_000_000, underwater: false },
    estimate: { kind: "home", based_on: "2026-06-30", through: "2026-04-01", note: HOME_NOTE },
  }),
  statements: [],
  terms: NO_TERMS,
};

const NO_FLOWS: FlowPeriod = {
  period_start: "2026-01-01",
  period_end: "2026-09-30",
  statements: 9,
  gaps: 0,
  split_statements: 9,
  net_flows_cents: null,
  market_change_cents: null,
  return_pct: null,
  fees_paid_cents: null,
  fee_statements: 0,
  fee_months: 0,
  fees_per_month_cents: null,
  dividends_cents: null,
};

const LAST_STATEMENT: FlowPeriod = {
  ...NO_FLOWS,
  period_start: "2026-09-01",
  statements: 1,
  split_statements: 1,
  net_flows_cents: 50_000,
  market_change_cents: 62_490,
  return_pct: "1.1",
  dividends_cents: 6_633,
};

const THIS_YEAR: FlowPeriod = {
  ...NO_FLOWS,
  net_flows_cents: 450_000,
  market_change_cents: -10_600,
  return_pct: "-0.2",
  fees_paid_cents: 6_601,
  fee_statements: 3,
  fee_months: 9,
  fees_per_month_cents: 733,
  dividends_cents: 17_434,
};

const BROKERAGE_DETAIL: AccountDetail = {
  ...DETAIL,
  account: row({
    account_key: "example-brokerage",
    display_name: "Example brokerage",
    institution: "Example Investments",
    type: "brokerage",
    group: "investments",
    nw_group: "example-brokerage",
    balance_cents: 5_000_000,
  }),
  statements: [
    {
      period_start: "2026-09-01",
      period_end: "2026-09-30",
      opening_cents: 4_887_510,
      closing_cents: 5_000_000,
      balance_cents: null,
      net_flows_cents: 50_000,
      market_change_cents: 62_490,
      txn_count: 4,
      reconciled: true,
    },
    {
      period_start: "2026-08-01",
      period_end: "2026-08-31",
      opening_cents: null,
      closing_cents: 4_887_510,
      balance_cents: null,
      net_flows_cents: null,
      market_change_cents: null,
      txn_count: 0,
      reconciled: true,
    },
  ],
  investment: { year: 2026, latest: LAST_STATEMENT, ytd: THIS_YEAR },
  terms: { ...DETAIL.terms, apr_milli_pct: null, rate_type: null, credit_limit_cents: null, autopay: null },
};

const HOME_ITEM: ValuationItem = {
  account_key: "example-home",
  display_name: "Example home",
  kind: "home",
  secured_by: null,
  value_cents: 40_000_000,
  stale: false,
  real: {
    value_cents: 40_000_000,
    as_of: "2026-06-30",
    source: "valuation",
    method: "comps",
    label: "valuation",
    source_note: "Average and median of three comps",
  },
  estimate: { value_cents: 40_000_000, method: "fhfa_hpi", based_on: "2026-06-30", through: "2026-04-01", note: HOME_NOTE },
  inputs: { home: null, vehicle: null, loan: null },
  note: null,
  equity: { value_cents: 40_000_000, loan_cents: -10_000_000, equity_cents: 30_000_000, underwater: false, estimated: true },
};

const MORTGAGE_ITEM: ValuationItem = {
  ...HOME_ITEM,
  account_key: "example-mortgage",
  display_name: "Example mortgage",
  kind: "loan",
  secured_by: "example-home",
  value_cents: -10_000_000,
  real: null,
  estimate: { value_cents: -10_000_000, method: "amortization", based_on: "2026-08-31", through: "2026-09-15", note: MORTGAGE_NOTE },
  equity: null,
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input);
  if (url.startsWith("/api/networth/series")) return json(SERIES);
  if (url.startsWith("/api/accounts/example-card")) return json(DETAIL);
  if (url.startsWith("/api/accounts/example-stale")) return json(STALE_DETAIL);
  if (url.startsWith("/api/accounts/example-home")) return json(HOME_DETAIL);
  if (url.startsWith("/api/accounts/example-ira")) return json(IRA_DETAIL);
  if (url.startsWith("/api/accounts/example-savings")) return json(SAVINGS_DETAIL);
  if (url.startsWith("/api/accounts/example-loan")) return json(LOAN_DETAIL);
  if (url.startsWith("/api/accounts/example-brokerage")) return json(BROKERAGE_DETAIL);
  if (url === "/api/valuations") return json({ as_of: "2026-10-21", index: {}, items: [HOME_ITEM, MORTGAGE_ITEM] });
  if (url.startsWith("/api/accounts")) return json(ACCOUNTS);
  return json({ detail: "Not found." }, 404);
});

beforeEach(() => {
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("account rows", () => {
  it("shows owed balances as positive, and paying down as a green down arrow", () => {
    const { container } = render(<GroupCard group={CARDS_GROUP} range="YTD" onOpen={() => {}} />);
    const rowEl = screen.getByRole("button", { name: /Example card/ });
    expect(rowEl.textContent).toContain("$632");
    expect(rowEl.textContent).not.toContain(MINUS);
    const good = rowEl.querySelector(".text-good");
    expect(good?.textContent).toContain("↓");
    expect(good?.textContent).toContain("$200");
    expect(rowEl.querySelector(".text-bad")).toBeNull();
    // The group header follows the same rule.
    const header = container.querySelector("section > button");
    expect(header?.textContent).toContain("$632");
    expect(header?.querySelector(".text-good")?.textContent).toContain("↓");
  });

  it("shows owed going up as a red up arrow", () => {
    const worse = { ...CARDS_GROUP, change_cents: -5_000, accounts: [{ ...CARD, change_cents: -5_000 }] };
    render(<GroupCard group={worse} range="YTD" onOpen={() => {}} />);
    const bad = screen.getByRole("button", { name: /Example card/ }).querySelector(".text-bad");
    expect(bad?.textContent).toContain("↑");
  });

  it("says Tracked by payments for cards tracked by payments, with no balance", () => {
    render(<GroupCard group={CARDS_GROUP} range="YTD" onOpen={() => {}} />);
    const rowEl = screen.getByRole("button", { name: /Example store card/ });
    expect(rowEl.textContent).toContain("Tracked by payments");
    expect(rowEl.textContent).not.toContain("No balance");
    expect(rowEl.textContent).not.toContain("$0");
  });

  it("marks a shown balance that is not in net worth", () => {
    const kid = row({ display_name: "Kid savings", include_in_net_worth: false });
    render(<GroupCard group={{ ...CARDS_GROUP, group: "cash", is_liability: false, accounts: [kid] }} range="YTD" onOpen={() => {}} />);
    expect(screen.getByRole("button", { name: /Kid savings/ }).textContent).toContain("Not in net worth");
  });

  it("shows the type, not the valuation source, for a home", () => {
    const home = row({ display_name: "Family home", institution: "Valuation", type: "property", group: "real_estate" });
    render(<GroupCard group={{ ...CARDS_GROUP, group: "real_estate", is_liability: false, accounts: [home] }} range="YTD" onOpen={() => {}} />);
    const text = screen.getByRole("button", { name: /Family home/ }).textContent ?? "";
    expect(text).toContain("Property · ");
    expect(text).not.toContain("Valuation");
  });

  it("says No balance for a missing account, never zero", () => {
    const missing = row({ balance_cents: null, as_of: null, source: null, missing: true, change_cents: null });
    expect(balanceState(missing)).toBe("missing");
    render(<GroupCard group={{ ...CARDS_GROUP, group: "cash", is_liability: false, accounts: [missing] }} range="YTD" onOpen={() => {}} />);
    expect(screen.getByRole("button", { name: /Example checking/ }).textContent).toContain("No balance");
  });

  it("turns the as of date to the warning color when stale", () => {
    render(<GroupCard group={{ ...CARDS_GROUP, accounts: [{ ...CARD, stale: true }] }} range="YTD" onOpen={() => {}} />);
    const asOf = screen.getByText("as of Sep 30").parentElement;
    expect(asOf?.className).toContain("text-warn");
  });

  it("marks a dormant account in muted words, and closed wins", () => {
    const sleepy = row({ display_name: "Sleepy savings", dormant: true });
    const gone = row({ account_key: "gone", display_name: "Gone savings", dormant: true, active: false, source: null });
    const cash = { ...CARDS_GROUP, group: "cash" as const, is_liability: false, accounts: [sleepy, gone] };
    render(<GroupCard group={cash} range="YTD" onOpen={() => {}} />);
    const marker = within(screen.getByRole("button", { name: /Sleepy savings/ })).getByText("Dormant");
    expect(marker.className).toContain("text-fg-secondary");
    const closed = screen.getByRole("button", { name: /Gone savings/ });
    expect(closed.textContent).toContain("Closed");
    expect(closed.textContent).not.toContain("Dormant");
  });

  it("shows no dormant marker on a card tracked by payments", () => {
    render(<GroupCard group={{ ...CARDS_GROUP, accounts: [{ ...TRACKED, dormant: true }] }} range="YTD" onOpen={() => {}} />);
    expect(screen.getByRole("button", { name: /Example store card/ }).textContent).not.toContain("Dormant");
  });

  it("shows underwater equity in words and the bad color", () => {
    const { container } = render(
      <EquityLine equity={{ value_cents: 2_000_000, loan_cents: -2_500_000, equity_cents: -500_000, underwater: true }} />,
    );
    expect(container.textContent).toBe("Underwater by $5,000");
    expect(container.firstElementChild?.className).toContain("text-bad");
  });

  it("shows positive equity in gray", () => {
    const { container } = render(
      <EquityLine equity={{ value_cents: 3_000_000, loan_cents: -1_000_000, equity_cents: 2_000_000, underwater: false }} />,
    );
    expect(container.textContent).toBe("Equity $20,000");
    expect(container.firstElementChild?.className).not.toContain("text-bad");
  });
});

describe("net worth card", () => {
  it("Show the numbers lists the same points the chart draws", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AccountsPage />, { route: "/accounts" });
    const toggle = await screen.findByRole("button", { name: "Show the numbers" });
    await user.click(toggle);
    const table = screen.getByRole("table", { name: /by day/ });
    const bodyRows = within(table).getAllByRole("row").slice(1);
    expect(bodyRows).toHaveLength(SERIES.points.length);
    SERIES.points.forEach((p, i) => {
      const cells = bodyRows[i]!.textContent ?? "";
      expect(cells).toContain(formatDate(p.date));
      expect(cells).toContain(formatMoney(p.value_cents));
      expect(cells.includes("Partial")).toBe(p.partial);
    });
    // Hero and change come from the same response.
    const eyebrow = screen.getByRole("button", { name: "About this chart" }).closest("p.eyebrow")!;
    expect(eyebrow.textContent).toBe("Net worth");
    const hero = eyebrow.parentElement!;
    expect(hero.textContent).toContain("$1,250");
    expect(hero.querySelector(".text-good")?.textContent).toContain("$250 (25%)");
    expect(hero.textContent).toContain("Year to date change");
  });

  it("opens the About this chart note on a tap", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AccountsPage />, { route: "/accounts" });
    const info = await screen.findByRole("button", { name: "About this chart" });
    expect(info.className).toContain("tap-target");
    expect(screen.queryByRole("tooltip")).toBeNull();
    await user.click(info);
    expect(screen.getByRole("tooltip").textContent).toContain("Each day adds up the latest balance of every account in net worth.");
  });

  it("puts month ticks on month starts, thinned to the most allowed", () => {
    const days = ["2026-01-13", "2026-01-31", "2026-02-01", "2026-02-02", "2026-03-01", "2026-04-01", "2026-05-01"];
    expect(monthStartTicks(days, 8)).toEqual(["2026-02-01", "2026-03-01", "2026-04-01", "2026-05-01"]);
    expect(monthStartTicks(days, 2)).toEqual(["2026-02-01", "2026-04-01"]);
    expect(monthStartTicks(["2026-01-01"], 4)).toEqual([]);
  });

  it("uses one gray line format on every row", () => {
    expect(metaLine({ type: "checking", subtype: null, institution: "Harbor Credit Union", owner_label: "Sam" })).toBe(
      "Checking · Sam · Harbor Credit Union",
    );
    expect(metaLine({ type: "property", subtype: null, institution: "Zillow", owner_label: "Joint" })).toBe("Property · Joint");
  });

  it("draws owed series as positive amounts", () => {
    const pts = seriesPoints([{ date: "2026-01-01", value_cents: -50_000, partial: false }], "loans");
    expect(pts[0]!.value).toBe(50_000);
    expect(seriesPoints([{ date: "2026-01-01", value_cents: 50_000, partial: false, estimated: false }], "cash")[0]!.value).toBe(50_000);
  });

  it("groups partial days into runs for shading", () => {
    const runs = partialRuns(SERIES.points.map((p) => ({ date: p.date, value: p.value_cents, partial: p.partial })));
    expect(runs).toEqual([
      { from: "2026-01-01", to: "2026-01-02" },
      { from: "2026-01-05", to: "2026-01-05" },
    ]);
  });
});

describe("summary", () => {
  it("percent shares add up to exactly 100", () => {
    for (const parts of [[1, 1, 1], [2, 2, 2, 1], [52_461_378, 9_183_413, 8_956_588, 5_659_128, 2_238_590], [7]]) {
      const pcts = sharePercents(parts);
      expect(Math.round(pcts.reduce((a, b) => a + b, 0) * 10)).toBe(1000);
    }
    expect(sharePercents([0, 0])).toEqual([0, 0]);
  });

  it("shows assets and liabilities in percent, each adding to 100", async () => {
    const user = userEvent.setup();
    const summary = {
      ...ACCOUNTS.summary,
      assets_cents: 300_000,
      by_type: [
        { key: "cash", label: "Cash", cents: 100_000, is_liability: false },
        { key: "investments", label: "Investments", cents: 100_000, is_liability: false },
        { key: "retirement", label: "Retirement", cents: 100_000, is_liability: false },
        { key: "credit_cards", label: "Credit cards", cents: -63_200, is_liability: true },
      ],
    };
    render(<SummaryCard summary={summary} groups={ACCOUNTS.groups} />);
    await user.click(screen.getByRole("radio", { name: "Percent" }));
    const lists = screen.getAllByRole("list");
    const sums = lists.map((ul) =>
      within(ul)
        .getAllByRole("listitem")
        .reduce((sum, li) => sum + Number(/([\d.]+)%$/.exec(li.textContent ?? "")?.[1]), 0),
    );
    expect(sums.map((s) => Math.round(s * 10))).toEqual([1000, 1000]);
    // Liabilities are drawn as positive owed amounts.
    await user.click(screen.getByRole("radio", { name: "Totals" }));
    expect(within(lists[1]!).getByRole("listitem").textContent).toBe("Credit cards$632");
  });

  it("keeps an owner color with its group, not its size or side", () => {
    const types = new Map([
      ["a-group", "cash"],
      ["b-group", "cash"],
      ["credit-cards", "credit_cards"],
    ]);
    const a = ownerColors(["b-group", "a-group", "credit-cards"], types);
    const b = ownerColors(["credit-cards", "a-group", "b-group"], types);
    expect(a).toEqual(b);
    // The By type color, so Credit cards is red in both views.
    expect(a.get("credit-cards")).toBe(groupColor("credit_cards"));
    expect(a.get("a-group")).toBe(groupColor("cash"));
    // Same type: the second by name takes a free slot, never orange.
    expect(a.get("b-group")).not.toBe(a.get("a-group"));
    expect(a.get("b-group")).not.toBe("var(--chart-spending)");
  });

  it("finds each owner group's type from its largest asset", () => {
    const home = row({ account_key: "home", type: "property", group: "real_estate", nw_group: "home-equity", balance_cents: 500_000 });
    const loan = row({ account_key: "mtg", type: "mortgage", group: "loans", nw_group: "home-equity", is_asset: false, balance_cents: -900_000 });
    const groups: AccountGroup[] = [{ ...CARDS_GROUP, accounts: [CARD, TRACKED, home, loan] }];
    const types = ownerGroupTypes(groups);
    expect(types.get("home-equity")).toBe("real_estate");
    expect(types.get("credit-cards")).toBe("credit_cards");
    expect(types.has("example-store-card")).toBe(false);
  });

  it("makes each header the sum of its parts in both views", async () => {
    const user = userEvent.setup();
    // The mortgage nets into Home equity under By owner, so the gross API
    // totals would not match the owner parts.
    const summary = {
      assets_cents: 620_000,
      liabilities_cents: 463_200,
      net_worth_cents: 156_800,
      by_type: [
        { key: "real_estate", label: "Real estate", cents: 500_000, is_liability: false },
        { key: "cash", label: "Cash", cents: 120_000, is_liability: false },
        { key: "loans", label: "Loans", cents: -400_000, is_liability: true },
        { key: "credit_cards", label: "Credit cards", cents: -63_200, is_liability: true },
      ],
      by_owner: [
        { key: "cash-and-misc", label: "Cash and misc", cents: 120_000, is_liability: false },
        { key: "home-equity", label: "Home equity", cents: 100_000, is_liability: false },
        { key: "credit-cards", label: "Credit cards", cents: -63_200, is_liability: true },
      ],
    };
    render(<SummaryCard summary={summary} groups={ACCOUNTS.groups} />);
    const headers = () => screen.getAllByRole("heading", { level: 3 }).map((h) => h.parentElement!.textContent);
    expect(headers()).toEqual(["Assets$6,200", "Liabilities$4,632"]);
    await user.click(screen.getByRole("radio", { name: "By owner" }));
    expect(headers()).toEqual(["Assets$2,200", "Liabilities$632"]);
  });

});

describe("accounts page", () => {
  it("lists notes and opens the detail drawer from a row", async () => {
    const user = userEvent.setup();
    renderWithProviders(<AccountsPage />, { route: "/accounts" });
    const notes = await screen.findByRole("complementary", { name: "Notes" });
    expect(notes.textContent).toContain("A newer statement is overdue: Example card.");
    expect(notes.textContent).toContain("Tracked by payments, not in net worth: Example store card.");

    await user.click(screen.getByRole("button", { name: /Example card/ }));
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(within(dialog).getByText("Statements")).toBeTruthy());
    expect(within(dialog).getByText("24.99%, variable")).toBeTruthy();
    expect(within(dialog).getByText("APR")).toBeTruthy();
    // No registry facts, so no About section.
    expect(within(dialog).queryByText("About")).toBeNull();
    expect(within(dialog).getByText("Full balance")).toBeTruthy();
    expect(within(dialog).getByText("No transactions yet.")).toBeTruthy();
    // The statement balance is shown as owed, positive, with cents.
    expect(within(dialog).getByText("$632.00")).toBeTruthy();
    // The statements table scrolls in a named region that takes keyboard focus.
    expect(within(dialog).getByRole("region", { name: "Statements" }).getAttribute("tabindex")).toBe("0");
  });

  it("says in words in the drawer that the estimate is out of date and who made it", async () => {
    renderWithProviders(<AccountDrawer accountKey="example-stale" range="YTD" onClose={() => {}} />);
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("Estimated by the monthly run, not from a statement.");
    expect(within(dialog).getByText(/The monthly run's estimate is out of date\./, { ignore: ".sr-only" })).toBeTruthy();
    // A monthly-run balance is never called an overdue statement.
    expect(within(dialog).queryByText(/A newer statement is overdue/)).toBeNull();
  });

  it("names the loan's estimate under a home's equity in the drawer", async () => {
    renderWithProviders(
      <EquityNotesProvider>
        <AccountDrawer accountKey="example-home" range="YTD" onClose={() => {}} />
      </EquityNotesProvider>,
    );
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText(`Equity uses estimates. Value: ${HOME_NOTE} Example mortgage: ${MORTGAGE_NOTE}`);
  });

  it("shows the chat's note, the dormant marker and the About facts in the drawer", async () => {
    renderWithProviders(<AccountDrawer accountKey="example-ira" range="YTD" onClose={() => {}} />);
    const dialog = await screen.findByRole("dialog");
    const note = await within(dialog).findByText("Rollover done. New money goes to the index fund.");
    expect(note.className).toContain("break-words");
    expect(within(dialog).getByText("Dormant").className).toContain("text-fg-secondary");
    const about = within(dialog).getByRole("heading", { name: "About" }).parentElement!;
    const facts = [...about.querySelectorAll("dt")].map((dt) => [dt.textContent, dt.nextElementSibling?.textContent]);
    expect(facts).toEqual([
      ["Custodian", "Example Trust Bank"],
      ["Managed by", "Example Advisors"],
      ["Opened", "Apr 1, 2015"],
    ]);
    expect(about.querySelector("dd")?.className).toContain("break-words");
  });

  it("labels a savings rate APY and a card rate APR", async () => {
    renderWithProviders(<AccountDrawer accountKey="example-savings" range="YTD" onClose={() => {}} />);
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("APY");
    expect(within(dialog).getByText("4.1%, variable")).toBeTruthy();
    expect(within(dialog).queryByText("APR")).toBeNull();
    // Only Opened would be left, and it is null: no About section.
    expect(within(dialog).queryByText("Custodian")).toBeNull();
    expect(within(dialog).queryByText("About")).toBeNull();
  });

  it("shows a loan's original amount in Terms, and none on a card", async () => {
    const { unmount } = renderWithProviders(<AccountDrawer accountKey="example-loan" range="YTD" onClose={() => {}} />);
    const dialog = await screen.findByRole("dialog");
    const original = await within(dialog).findByText("Original amount");
    expect(original.nextElementSibling?.textContent).toBe("$42,000");
    unmount();
    renderWithProviders(<AccountDrawer accountKey="example-card" range="YTD" onClose={() => {}} />);
    const card = await screen.findByRole("dialog");
    await within(card).findByText("APR");
    expect(within(card).queryByText("Original amount")).toBeNull();
  });

  it("names what a home's real value is based on", async () => {
    renderWithProviders(
      <EquityNotesProvider>
        <AccountDrawer accountKey="example-home" range="YTD" onClose={() => {}} />
      </EquityNotesProvider>,
    );
    const dialog = await screen.findByRole("dialog");
    const basis = await within(dialog).findByText("Based on");
    expect(basis.nextElementSibling?.textContent).toBe("Average and median of three comps");
    expect(basis.nextElementSibling?.className).toContain("break-words");
  });

  it("shows a custodian or servicer only when it is not the institution", () => {
    expect(otherName("Example Brokerage ", "example brokerage")).toBeNull();
    expect(otherName(null, "x")).toBeNull();
    expect(otherName("  ", "x")).toBeNull();
    expect(otherName("Example Trust Bank", "Example Brokerage")).toBe("Example Trust Bank");
  });

  it("shows money in and market for an investment account", async () => {
    renderWithProviders(<AccountDrawer accountKey="example-brokerage" range="YTD" onClose={() => {}} />);
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("Money in and market");
    const table = within(dialog).getByRole("region", { name: "Money in and market" });
    const heads = within(table).getAllByRole("columnheader").map((h) => h.textContent);
    expect(heads).toEqual(["Number", "Last statementSep 1 to Sep 30", "This yearJan 1 to Sep 30"]);
    const cells = (label: string) =>
      within(within(table).getByRole("row", { name: new RegExp(`^${label}`) }))
        .getAllByRole("cell")
        .map((c) => c.textContent);
    expect(cells("Money in")).toEqual(["+$500", "+$4,500"]);
    expect(cells("Market change")).toEqual(["+$625", `${MINUS}$106`]);
    expect(cells("Return")).toEqual(["+1.1%", `${MINUS}0.2%`]);
    expect(cells("Fees")).toEqual(["Not given", "$66.01$7.33 a month"]);
    expect(cells("Dividends")).toEqual(["$66.33", "$174.34"]);
    // A gain is green and a loss red, with the sign in the text too.
    expect(within(table).getByText("+1.1%").className).toContain("text-good");
    expect(within(table).getByText(`${MINUS}0.2%`).className).toContain("text-bad");
    expect(within(dialog).getByText("Return is the market change only, not money put in.")).toBeTruthy();
    expect(within(dialog).getByText("Fees are already taken out of the numbers above. Fees from 3 of 9 statements.")).toBeTruthy();
    // The statements table gets Money in and Market on wide screens.
    const statements = within(dialog).getByRole("region", { name: "Statements" });
    const money = within(statements).getByRole("columnheader", { name: "Money in" });
    expect(money.className).toContain("hidden md:table-cell");
    expect(within(statements).getByRole("columnheader", { name: "Market" })).toBeTruthy();
    expect(within(statements).getByText("+$500.00")).toBeTruthy();
    expect(within(statements).getByText("+$624.90")).toBeTruthy();
    expect(within(statements).getAllByText("Not given")).toHaveLength(2);
  });

  it("leaves out rows with no numbers and says what is missing", () => {
    const { rerender } = render(<InvestmentPanel summary={{ year: 2026, latest: LAST_STATEMENT, ytd: null }} />);
    expect(screen.queryByRole("columnheader", { name: /This year/ })).toBeNull();
    expect(screen.getByText("No statements from 2026 yet.")).toBeTruthy();
    expect(screen.queryByRole("row", { name: /^Fees/ })).toBeNull();
    expect(screen.queryByText(/Fees are already taken out/)).toBeNull();
    const partial = { ...THIS_YEAR, split_statements: 8, return_pct: null, fees_paid_cents: null, fee_statements: 0 };
    rerender(<InvestmentPanel summary={{ year: 2026, latest: LAST_STATEMENT, ytd: partial }} />);
    expect(screen.getByText("This year uses 8 of 9 statements. The others do not split money in from market change.")).toBeTruthy();
    expect(within(screen.getByRole("row", { name: /^Return/ })).getAllByRole("cell")[1]!.textContent).toBe("Not given");
    expect(screen.queryByRole("row", { name: /^Fees/ })).toBeNull();
    // A break between statements: the totals are short, and the return is not given.
    rerender(<InvestmentPanel summary={{ year: 2026, latest: LAST_STATEMENT, ytd: { ...THIS_YEAR, gaps: 1, return_pct: null } }} />);
    expect(screen.getByText("A statement is missing from this year, so these totals are short.")).toBeTruthy();
    rerender(<InvestmentPanel summary={{ year: 2026, latest: LAST_STATEMENT, ytd: { ...THIS_YEAR, gaps: 2, return_pct: null } }} />);
    expect(screen.getByText("Statements are missing from this year, so these totals are short.")).toBeTruthy();
    // Nothing to show at all: no panel.
    rerender(<InvestmentPanel summary={{ year: 2026, latest: NO_FLOWS, ytd: null }} />);
    expect(screen.queryByText("Money in and market")).toBeNull();
  });

  it("has no money in and market panel on a card", async () => {
    renderWithProviders(<AccountDrawer accountKey="example-card" range="YTD" onClose={() => {}} />);
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("Statements");
    expect(within(dialog).queryByText("Money in and market")).toBeNull();
    expect(within(dialog).queryByRole("columnheader", { name: "Money in" })).toBeNull();
  });

  it("signs money and percents", () => {
    expect(signedMoney(50_000)).toBe("+$500");
    expect(signedMoney(-106_200)).toBe(`${MINUS}$1,062`);
    expect(signedMoney(0)).toBe("$0");
    expect(signedMoney(40)).toBe("$0");
    expect(signedMoney(62_490, { cents: true })).toBe("+$624.90");
    expect(signedPct("2.1")).toBe("+2.1%");
    expect(signedPct("-1.1")).toBe(`${MINUS}1.1%`);
    expect(signedPct("0.0")).toBe("0.0%");
  });

  it("formats APR from thousandths of a percent", () => {
    expect(formatApr(24_990)).toBe("24.99%");
    expect(formatApr(4_500)).toBe("4.5%");
    expect(formatApr(0)).toBe("0%");
  });
});
