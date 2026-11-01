// Fictional numbers for the Investments and Settings, Prices tests.

import type { FlowPeriod } from "../../lib/api";
import type { AllocationOut, HistoryOut, HoldingGroup, HoldingRow, HoldingsOut, InvestmentFlowsOut, PricesStatus, SymbolsOut } from "./api";

const base: Omit<HoldingRow, "holding_key" | "name" | "symbol" | "asset_class" | "mode" | "value_cents"> = {
  account_key: "brokerage",
  account_name: "Sample brokerage",
  mode_source: "rule",
  statement_date: "2026-09-30",
  statement_quantity_micro: null,
  statement_price_micro: null,
  statement_value_cents: 0,
  quantity_micro: null,
  price_micro: null,
  price_date: null,
  estimated: false,
  price_missing: false,
  price_stale: false,
  share_of_account_pct: null,
  concentrated: false,
  cost_basis_cents: null,
  gain_cents: null,
  gain_pct: null,
};

export const STOCK: HoldingRow = {
  ...base,
  holding_key: "stock-fund",
  name: "Stock Index Fund",
  symbol: "STKIDX",
  asset_class: "us-stock",
  mode: "api",
  statement_quantity_micro: 100_500_000,
  statement_price_micro: 250_000_000,
  statement_value_cents: 2_512_500,
  quantity_micro: 100_500_000,
  price_micro: 262_645_111,
  price_date: "2026-10-20",
  value_cents: 2_639_584,
  estimated: true,
  share_of_account_pct: "72.5",
  concentrated: true,
  cost_basis_cents: 2_400_000,
  gain_cents: 239_584,
  gain_pct: "10.0",
};

export const BOND: HoldingRow = {
  ...base,
  holding_key: "bond-fund",
  name: "Bond Fund",
  symbol: "BNDFND",
  asset_class: "bond",
  mode: "api",
  mode_source: "override",
  quantity_micro: 50_000_000,
  price_micro: 10_000_000,
  price_date: "2026-06-12",
  value_cents: 50_000,
  price_stale: true,
  share_of_account_pct: "1.4",
};

export const CASH: HoldingRow = {
  ...base,
  holding_key: "cash",
  name: "Cash",
  symbol: null,
  asset_class: "cash",
  mode: "constant",
  value_cents: 950_000,
  share_of_account_pct: "26.1",
};

export const TRUST: HoldingRow = {
  ...base,
  account_key: "k401",
  account_name: "Sample 401k",
  holding_key: "trust",
  name: "Target Trust 2050",
  symbol: null,
  asset_class: "target-date",
  mode: "statement",
  price_date: "2026-09-30",
  value_cents: 9_000_000,
  share_of_account_pct: "100.0",
  concentrated: true,
};

type GroupExtras = Omit<HoldingGroup, "key" | "label" | "total_cents" | "statement_date" | "estimated" | "holdings">;

/** A group with no cost basis, no note and not dormant. */
const PLAIN: GroupExtras = {
  account_type: null,
  notes: null,
  dormant: false,
  cost_basis_cents: null,
  gain_cents: null,
  gain_pct: null,
  gain_share_pct: null,
  basis_holdings: 0,
  no_basis_holdings: 0,
};

export const HOLDINGS: HoldingsOut = {
  as_of: "2026-10-21",
  group_by: "account",
  total_cents: 13_139_584,
  estimated: true,
  concentrated: 2,
  groups: [
    {
      key: "k401",
      label: "Sample 401k",
      total_cents: 9_000_000,
      statement_date: "2026-09-30",
      estimated: false,
      holdings: [TRUST],
      ...PLAIN,
      account_type: "retirement",
      dormant: true,
      no_basis_holdings: 1,
    },
    {
      key: "brokerage",
      label: "Sample brokerage",
      total_cents: 4_139_584,
      statement_date: "2026-09-30",
      estimated: true,
      holdings: [STOCK, CASH, BOND],
      ...PLAIN,
      account_type: "brokerage",
      notes: "Diversification plan needed.",
      cost_basis_cents: 2_400_000,
      gain_cents: 239_584,
      gain_pct: "10.0",
      basis_holdings: 1,
      no_basis_holdings: 1,
    },
  ],
};

export const HOLDINGS_BY_CLASS: HoldingsOut = {
  ...HOLDINGS,
  group_by: "class",
  groups: [
    { key: "target-date", label: "Target date", total_cents: 9_000_000, statement_date: null, estimated: false, holdings: [TRUST], ...PLAIN, no_basis_holdings: 1 },
    {
      key: "us-stock",
      label: "Us stock",
      total_cents: 2_639_584,
      statement_date: null,
      estimated: true,
      holdings: [STOCK],
      ...PLAIN,
      cost_basis_cents: 2_400_000,
      gain_cents: 239_584,
      gain_pct: "10.0",
      gain_share_pct: "9.1",
      basis_holdings: 1,
    },
    { key: "cash", label: "Cash", total_cents: 950_000, statement_date: null, estimated: false, holdings: [CASH], ...PLAIN },
    { key: "bond", label: "Bond", total_cents: 50_000, statement_date: null, estimated: false, holdings: [BOND], ...PLAIN, no_basis_holdings: 1 },
  ],
};

const NO_FLOWS: FlowPeriod = {
  period_start: "2026-01-01",
  period_end: "2026-09-30",
  statements: 1,
  gaps: 0,
  split_statements: 1,
  net_flows_cents: null,
  market_change_cents: null,
  return_pct: null,
  fees_paid_cents: null,
  fee_statements: 0,
  fee_months: 0,
  fees_per_month_cents: null,
  dividends_cents: null,
};

export const FLOWS: InvestmentFlowsOut = {
  as_of: "2026-10-21",
  year: 2026,
  accounts: [
    {
      account_key: "brokerage",
      display_name: "Sample brokerage",
      closed: false,
      year: 2026,
      latest: { ...NO_FLOWS, period_start: "2026-09-01", net_flows_cents: 50_000, market_change_cents: 62_490, return_pct: "1.1", dividends_cents: 6_633 },
      ytd: {
        ...NO_FLOWS,
        statements: 9,
        split_statements: 9,
        net_flows_cents: 450_000,
        market_change_cents: 109_128,
        return_pct: "2.1",
        dividends_cents: 17_434,
      },
    },
    {
      account_key: "k401",
      display_name: "Sample 401k",
      closed: true,
      year: 2026,
      latest: { ...NO_FLOWS, period_start: "2026-07-01", fees_paid_cents: 2_295, fee_statements: 1, fee_months: 3, fees_per_month_cents: 765 },
      ytd: {
        ...NO_FLOWS,
        statements: 3,
        split_statements: 2,
        net_flows_cents: 508_800,
        market_change_cents: -79_787,
        fees_paid_cents: 6_601,
        fee_statements: 3,
        fee_months: 9,
        fees_per_month_cents: 733,
      },
    },
  ],
};

export const ALLOCATION: AllocationOut = {
  as_of: "2026-10-21",
  total_cents: 13_139_584,
  estimated: true,
  classes: [
    { key: "target-date", label: "Target date", value_cents: 9_000_000, pct: "68.5" },
    { key: "us-stock", label: "Us stock", value_cents: 2_639_584, pct: "20.1" },
    { key: "cash", label: "Cash", value_cents: 950_000, pct: "7.2" },
    { key: "money-market", label: "Money market", value_cents: 500_000, pct: "3.8" },
    { key: "bond", label: "Bond", value_cents: 50_000, pct: "0.4" },
  ],
  accounts: [
    {
      account_key: "k401",
      display_name: "Sample 401k",
      value_cents: 9_000_000,
      pct: "68.5",
      classes: [{ key: "target-date", label: "Target date", value_cents: 9_000_000, pct: "100.0" }],
    },
    {
      account_key: "brokerage",
      display_name: "Sample brokerage",
      value_cents: 4_139_584,
      pct: "31.5",
      classes: [
        { key: "us-stock", label: "Us stock", value_cents: 2_639_584, pct: "63.8" },
        { key: "cash", label: "Cash", value_cents: 950_000, pct: "22.9" },
        { key: "money-market", label: "Money market", value_cents: 500_000, pct: "12.1" },
        { key: "bond", label: "Bond", value_cents: 50_000, pct: "1.2" },
      ],
    },
  ],
};

export const HISTORY: HistoryOut = {
  range: { key: "YTD", start: "2026-09-28", end: "2026-10-02" },
  accounts: [
    { account_key: "brokerage", display_name: "Sample brokerage" },
    { account_key: "k401", display_name: "Sample 401k" },
  ],
  points: [
    { date: "2026-09-28", value_cents: 13_000_000, partial: false, estimated: true },
    { date: "2026-09-29", value_cents: 13_010_000, partial: false, estimated: true },
    { date: "2026-09-30", value_cents: 13_100_000, partial: false, estimated: false },
    { date: "2026-10-01", value_cents: 13_120_000, partial: false, estimated: true },
    { date: "2026-10-02", value_cents: 13_139_584, partial: false, estimated: true },
  ],
  statement_points: [
    { date: "2026-09-30", account_key: "brokerage", value_cents: 4_100_000, source: "statement" },
    { date: "2026-09-30", account_key: "k401", value_cents: 9_000_000, source: "statement" },
  ],
  estimated_segments: [
    {
      account_key: "brokerage",
      start: "2026-09-01",
      end: "2026-09-29",
      based_on: "2026-08-31",
      note: "Estimated from prices as of Sep 29. Shares from the Aug 31 statement.",
    },
    {
      account_key: "brokerage",
      start: "2026-10-01",
      end: "2026-10-02",
      based_on: "2026-09-30",
      note: "Estimated from prices as of Oct 2. Shares from the Sep 30 statement.",
    },
  ],
  latest_price_date: "2026-10-02",
};

export const STATUS: PricesStatus = {
  tiingo_key_set: false,
  schedule: "06:30 America/Chicago, Tuesday to Saturday",
  last_run: {
    status: "ok",
    started_at: "2026-10-21T11:30:00+00:00",
    finished_at: "2026-10-21T11:31:00+00:00",
    symbols: 2,
    updated: 1,
    tiingo_calls: 2,
    yahoo_calls: 1,
    stopped: ["tiingo"],
  },
  last_success_at: "2026-10-21T11:31:00+00:00",
  symbols: [
    { symbol: "BNDFND", holdings: 1, last_date: "2026-06-12", source: "yahoo", stale: true, error: "not_found", error_label: "Symbol not found." },
    { symbol: "STKIDX", holdings: 1, last_date: "2026-10-20", source: "tiingo", stale: false, error: null, error_label: null },
  ],
  sanity: {
    checked: 9,
    unchecked: 1,
    flagged: [
      {
        account_key: "brokerage",
        account_name: "Sample brokerage",
        holding_key: "stock-fund",
        name: "Stock Index Fund",
        symbol: "STKIDX",
        statement_date: "2026-08-31",
        price_date: "2026-08-28",
        gap_pct: "-1.25",
      },
    ],
  },
};

export const SYMBOLS: SymbolsOut = {
  tiingo_key_set: false,
  rows: [
    {
      account_key: "brokerage",
      account_name: "Sample brokerage",
      holding_key: "stock-fund",
      name: "Stock Index Fund",
      printed_symbol: "STKIDX",
      cusip: null,
      asset_class: "us-stock",
      class_pick: null,
      last_statement_date: "2026-09-30",
      rule_mode: "api",
      mode: "api",
      symbol: "STKIDX",
      override: null,
    },
    {
      account_key: "k401",
      account_name: "Sample 401k",
      holding_key: "trust",
      name: "Target Trust 2050",
      printed_symbol: null,
      cusip: null,
      asset_class: null,
      class_pick: "target-date",
      last_statement_date: "2026-09-30",
      rule_mode: "statement",
      mode: "api",
      symbol: "TGTTR",
      override: { price_mode: "api", symbol: "TGTTR", note: "Proxy fund", updated_at: "2026-10-20T10:00:00+00:00" },
    },
  ],
};
