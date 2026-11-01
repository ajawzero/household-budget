// The three ranked-bar cards: Spending by group, Where the net worth is, Debt balances.
import { useState } from "react";
import { ChartCard, type ChartColumn } from "../../components/charts/ChartCard";
import { HBars, type HBarRow } from "../../components/charts/HBars";
import { sharePercents } from "../../components/charts/StackedBar";
import { SegmentedControl } from "../../components/ui/segmented-control";
import type { DebtItem, NetworthPart, SpendingGroup } from "../../lib/api";
import { formatDate, formatMoney, formatMonth, formatPct } from "../../lib/format";
import { contentsText } from "../cashflow/parts";
import { aprText } from "./format";
import { ActualBadge, Title, type LoadState } from "./parts";

/** Saved and invested is planned spending, but money kept: it takes the left over green, never the spending orange. */
export const SAVED_COLOR = "var(--chart-leftover)";
const SPENDING_COLOR = "var(--chart-spending)";
const KIND_TEXT: Record<SpendingGroup["kind"], string> = {
  spending: "Spending",
  saved_invested: "Saved and invested",
};

/** "Lifestyle $2,400, Housing $1,900, and 3 more." */
function barsSummary(prefix: string, rows: HBarRow[], money: (c: number) => string): string {
  const shown = rows.slice(0, 3).map((r) => `${r.label} ${money(r.cents)}`);
  const more = rows.length > 3 ? `, and ${rows.length - 3} more` : "";
  return `${prefix}: ${shown.join(", ")}${more}.`;
}

/**
 * Each bar's share of its side, adding up to 100.0% per side. Rows above zero
 * share the positive total. With owedSide, rows below zero share the owed
 * total too; otherwise they (and zero rows) get no share.
 */
export function withShares<T extends HBarRow>(rows: T[], owedSide: boolean): T[] {
  const share = new Map<number, number>();
  const side = (sign: 1 | -1) => {
    const picked = rows.map((r, i) => [sign * r.cents, i] as const).filter(([c]) => c > 0);
    const pcts = sharePercents(picked.map(([c]) => c));
    picked.forEach(([, i], j) => share.set(i, pcts[j]!));
  };
  side(1);
  if (owedSide) side(-1);
  return rows.map((r, i) => ({ ...r, share: share.get(i) ?? null }));
}

const SHARE_COLUMN = { header: "Share", cell: (r: HBarRow) => (r.share == null ? "" : formatPct(r.share, true)), numeric: true };

// ---------- Spending by group ----------

export interface SpendingRow extends HBarRow {
  kind: SpendingGroup["kind"];
}

export const SPENDING_COLUMNS: ChartColumn<SpendingRow>[] = [
  { header: "Group", cell: (r) => r.label },
  { header: "Plan a month", cell: (r) => formatMoney(r.cents), numeric: true },
  SHARE_COLUMN,
  { header: "Type", cell: (r) => KIND_TEXT[r.kind] },
  { header: "What is in it", cell: (r) => r.note ?? "" },
];

export const ACTUAL_SPENDING_COLUMNS: ChartColumn<SpendingRow>[] = [
  { header: "Group", cell: (r) => r.label },
  { header: "Actual", cell: (r) => formatMoney(r.cents), numeric: true },
  SHARE_COLUMN,
  { header: "Type", cell: (r) => KIND_TEXT[r.kind] },
  { header: "What is in it", cell: (r) => r.note ?? "" },
];

/**
 * Plan groups as bars. Spending groups keep the plan's order and come first;
 * Saved and invested is its own bar after them, in its own color.
 */
export function spendingRows(groups: SpendingGroup[]): SpendingRow[] {
  const spending = groups.filter((g) => g.kind !== "saved_invested");
  const saved = groups.filter((g) => g.kind === "saved_invested");
  // Shares are of every bar shown, saved and invested included: the subtitle's total.
  return withShares(
    [...spending, ...saved].map((g) => ({
      key: g.key,
      label: g.label,
      cents: g.cents,
      // What is in the group, on a line under its bar (E5).
      note: contentsText(g.contents),
      kind: g.kind,
      color: g.kind === "saved_invested" ? SAVED_COLOR : SPENDING_COLOR,
    })),
    false,
  );
}

/**
 * How the groups tie back to the Spending tile. The plan's groups, saved and
 * invested included, should add up to its planned spending.
 */
export function spendingNote(groups: SpendingGroup[], planSpendingCents: number | undefined): string | null {
  const all = groups.reduce((sum, g) => sum + g.cents, 0);
  if (planSpendingCents === undefined) return null;
  if (all === planSpendingCents) return `The groups add up to the ${formatMoney(planSpendingCents)} planned spending.`;
  const rest = all < planSpendingCents ? " The plan does not split the rest." : "";
  return `The groups add up to ${formatMoney(all)}. Planned spending is ${formatMoney(planSpendingCents)}.${rest}`;
}

/** Legend for the two bar colors, what Saved and invested means, and how the groups add up. */
function SpendingFooter({ savedCents, note, actual = false }: { savedCents: number | null; note: string | null; actual?: boolean }) {
  return (
    <div className="mt-4 flex flex-col gap-2 text-sm text-fg-secondary">
      {savedCents !== null && (
        <>
          <ul className="m-0 flex list-none flex-wrap gap-x-4 gap-y-1 p-0" aria-label="Bar colors">
            {[
              { label: "Spending", color: SPENDING_COLOR },
              { label: "Saved and invested", color: SAVED_COLOR },
            ].map((item) => (
              <li key={item.label} className="flex items-center gap-1.5">
                <span aria-hidden="true" className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: item.color }} />
                {item.label}
              </li>
            ))}
          </ul>
          <p className="m-0">
            {actual
              ? `Saved and invested (${formatMoney(savedCents)} that month) is money kept, so it counts toward % saved, not spending.`
              : `Saved and invested (${formatMoney(savedCents)} a month) is part of planned spending. It is money kept, so it also counts toward % saved.`}
          </p>
        </>
      )}
      {note && <p className="m-0">{note}</p>}
    </div>
  );
}

interface SpendingCardProps {
  groups: SpendingGroup[] | undefined;
  month: string | undefined;
  /** The plan's spending total (the Spending tile), to tie the groups back to it. */
  planSpendingCents: number | undefined;
  /** Phase 4: actual groups for the latest complete month. Empty until a month is complete. */
  actualGroups?: SpendingGroup[];
  actualMonth?: string;
  state: LoadState;
}

type SpendingBasis = "actual" | "plan";

export function SpendingCard({ groups, month, planSpendingCents, actualGroups, actualMonth, state }: SpendingCardProps) {
  const hasActual = !!actualGroups?.length && !!actualMonth;
  const [picked, setPicked] = useState<SpendingBasis>("actual");
  const basis: SpendingBasis = hasActual ? picked : "plan";
  const shown = basis === "actual" ? actualGroups : groups;
  const rows = shown ? spendingRows(shown) : undefined;
  const note = basis === "plan" && groups ? spendingNote(groups, planSpendingCents) : null;
  const saved = rows?.filter((r) => r.kind === "saved_invested") ?? [];
  const savedCents = saved.reduce((sum, r) => sum + r.cents, 0);
  const total = rows?.reduce((sum, r) => sum + r.cents, 0) ?? 0;
  return (
    <ChartCard<SpendingRow>
      title={<Title plan={basis === "plan"}>Spending by group{basis === "actual" && <ActualBadge />}</Title>}
      subtitle={
        rows?.length
          ? basis === "actual"
            ? `${formatMonth(actualMonth)}, the latest complete month: ${formatMoney(total)} with saved and invested`
            : `A month, in the ${formatMonth(month)} plan`
          : undefined
      }
      controls={
        hasActual ? (
          <SegmentedControl<SpendingBasis>
            label="Spending by group shows"
            variant="pill"
            value={basis}
            onChange={setPicked}
            options={[
              { value: "actual", label: "Actual" },
              { value: "plan", label: "Plan" },
            ]}
          />
        ) : undefined
      }
      ariaLabel={barsSummary(basis === "actual" ? `Spending by group in ${formatMonth(actualMonth)}` : "Planned spending by group", rows ?? [], formatMoney)}
      data={rows}
      columns={basis === "actual" ? ACTUAL_SPENDING_COLUMNS : SPENDING_COLUMNS}
      rowKey={(r) => r.key}
      tableCaption={basis === "actual" ? `Spending by group in ${formatMonth(actualMonth)}` : "Planned spending by group"}
      loading={state.loading}
      refreshing={state.refreshing}
      error={state.error}
      quietError
      emptyText="No spending plan for this month."
      footer={
        (saved.length > 0 || note) && (
          <SpendingFooter savedCents={saved.length > 0 ? savedCents : null} note={note} actual={basis === "actual"} />
        )
      }
    >
      <HBars rows={rows ?? []} color={SPENDING_COLOR} />
    </ChartCard>
  );
}

// ---------- Where the net worth is ----------

export const PARTS_COLUMNS: ChartColumn<HBarRow>[] = [
  { header: "Part", cell: (r) => r.label },
  { header: "Amount", cell: (r) => formatMoney(r.cents), numeric: true },
  SHARE_COLUMN,
];

/**
 * Parts as bars. Home and vehicle parts are already net of their loans, so a
 * share of "all assets" or "all debts" would be wrong: each part above zero
 * gets its share of the parts above zero, and owed parts get none (the Debt
 * balances card has each debt's share of all that is owed).
 */
export function partRows(parts: NetworthPart[]): HBarRow[] {
  return withShares(
    parts.map((p) => ({ key: p.key, label: p.label, cents: p.cents })),
    false,
  );
}

interface PartsCardProps {
  parts: NetworthPart[] | undefined;
  netWorthCents: number | undefined;
  state: LoadState;
}

export function NetWorthPartsCard({ parts, netWorthCents, state }: PartsCardProps) {
  const rows = parts ? partRows(parts) : undefined;
  return (
    <ChartCard<HBarRow>
      title="Where the net worth is"
      subtitle={
        netWorthCents !== undefined
          ? `${formatMoney(netWorthCents)} net worth. Owed amounts are negative. Home and vehicle parts are net of their loans. Shares are of the parts above zero.`
          : undefined
      }
      ariaLabel={barsSummary("Net worth by part", rows ?? [], formatMoney)}
      data={rows}
      columns={PARTS_COLUMNS}
      rowKey={(r) => r.key}
      tableCaption="Net worth by part"
      loading={state.loading}
      refreshing={state.refreshing}
      error={state.error}
      quietError
      emptyText="No balances yet."
    >
      <HBars rows={rows ?? []} color="var(--accent)" negativeColor="var(--chart-8)" shareOf={() => "the parts above zero"} />
    </ChartCard>
  );
}

// ---------- Debt balances ----------

export interface DebtRow extends HBarRow {
  apr: string;
  payoff: string;
}

export const DEBT_COLUMNS: ChartColumn<DebtRow>[] = [
  { header: "Debt", cell: (r) => r.label },
  { header: "Owed", cell: (r) => formatMoney(r.cents), numeric: true },
  SHARE_COLUMN,
  { header: "APR", cell: (r) => r.apr || "Not known", numeric: true },
  { header: "Payoff", cell: (r) => r.payoff || "Not set" },
];

/** "Jun 1, 2049", or "Was Mar 1, 2026" for a past date with money still owed. */
export function payoffCell(d: Pick<DebtItem, "payoff_date" | "payoff_ahead">): string {
  if (!d.payoff_date) return "";
  return d.payoff_ahead ? formatDate(d.payoff_date) : `Was ${formatDate(d.payoff_date)}`;
}

/** Debts as bars, each with its share of all that is owed. */
export function debtRows(debts: DebtItem[]): DebtRow[] {
  return withShares(
    debts.map((d) => ({
      key: d.account_key,
      label: d.name,
      cents: d.owed_cents,
      apr: aprText(d.apr_milli_pct),
      payoff: payoffCell(d),
    })),
    false,
  );
}

interface DebtsCardProps {
  debts: DebtItem[] | undefined;
  state: LoadState;
}

export function DebtsCard({ debts, state }: DebtsCardProps) {
  const rows = debts ? debtRows(debts) : undefined;
  const total = rows?.reduce((sum, r) => sum + r.cents, 0) ?? 0;
  const aprOf = new Map(rows?.map((r) => [r.key, r.apr]));
  return (
    <ChartCard<DebtRow>
      title="Debt balances"
      subtitle={rows?.length ? `${formatMoney(total)} owed in total` : undefined}
      ariaLabel={barsSummary("Amount owed by debt", rows ?? [], formatMoney)}
      data={rows}
      columns={DEBT_COLUMNS}
      rowKey={(r) => r.key}
      tableCaption="Debt balances"
      loading={state.loading}
      refreshing={state.refreshing}
      error={state.error}
      quietError
      emptyText="No debts."
    >
      <HBars
        rows={rows ?? []}
        color="var(--chart-8)"
        suffix={(r) => {
          const apr = aprOf.get(r.key);
          return apr ? <span className="text-fg-secondary"> · {apr} APR</span> : null;
        }}
        shareOf={() => "all debts"}
      />
    </ChartCard>
  );
}
