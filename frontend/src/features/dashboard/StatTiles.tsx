import type { ReactNode } from "react";
import { AsOf } from "../../components/money/AsOf";
import { Delta } from "../../components/money/Delta";
import { Money } from "../../components/money/Money";
import { Card } from "../../components/ui/card";
import { Skeleton } from "../../components/ui/skeleton";
import type { DashboardActualMonth, DashboardNetWorth, NetWorthBehind, PlanMonth } from "../../lib/api";
import { formatDate, formatMoney, formatMonth, formatMonthShort, formatPct } from "../../lib/format";
import { PlanBadge, type LoadState } from "./parts";

interface StatTilesProps {
  netWorth: DashboardNetWorth | undefined;
  plan: PlanMonth | null | undefined;
  /** The latest complete month's actuals (Phase 4). Null until a month is complete. */
  actual?: DashboardActualMonth | null;
  state: LoadState;
}

/**
 * Net worth, then the month's plan: income, spending, left over and % saved.
 * Planned spending includes saved and invested moves, so left over is income
 * minus all of it, and % saved adds saved and invested back.
 */
export function StatTiles({ netWorth, plan, actual, state }: StatTilesProps) {
  if (actual) return <ActualTiles netWorth={netWorth} plan={plan} actual={actual} state={state} />;
  const planSub = plan ? formatMonth(plan.month) : undefined;
  const noPlan = state.loading ? undefined : "No plan for this month yet";

  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-4 lg:grid-cols-6">
      <NetWorthTile netWorth={netWorth} state={state} />
      <PlanTile label="Income" swatch="var(--chart-income)" cents={plan?.income_cents} sub={planSub} state={state} empty={noPlan} />
      <PlanTile
        label="Spending"
        swatch="var(--chart-spending)"
        cents={plan?.spending_cents}
        sub={planSub}
        note={plan && plan.saved_invested_cents !== 0 ? `Includes ${formatMoney(plan.saved_invested_cents)} saved and invested` : undefined}
        state={state}
        empty={noPlan}
      />
      <PlanTile
        label="Left over"
        swatch="var(--chart-leftover)"
        cents={plan?.left_over_cents}
        sub={planSub}
        note={plan ? "Income minus planned spending" : undefined}
        state={state}
        empty={noPlan}
      />
      <Tile label="% saved" plan state={state} has={!!plan} empty={noPlan}>
        {plan && (
          <>
            <p className="m-0 text-2xl font-medium text-fg">{plan.saved_pct === null ? "None" : formatPct(plan.saved_pct)}</p>
            <p className="m-0 mt-1 text-sm text-fg-secondary">Left over plus saved and invested</p>
          </>
        )}
      </Tile>
    </div>
  );
}

function NetWorthTile({ netWorth, state }: { netWorth: DashboardNetWorth | undefined; state: LoadState }) {
  return (
      <Tile label="Net worth" className="col-span-2 md:col-span-4 lg:col-span-2" state={state} has={!!netWorth}>
        {netWorth && (
          <>
            <p className="m-0 text-3xl font-semibold text-fg">
              <Money cents={netWorth.value_cents} proportional />
            </p>
            <div className="mt-1">
              <Delta cents={netWorth.change_cents} polarity="higher_is_better" arrows="diagonal" period="vs 30 days ago" />
            </div>
            <p className="m-0 mt-1 text-sm text-fg-secondary">
              Assets {formatMoney(netWorth.assets_cents)} · Debts {formatMoney(netWorth.liabilities_cents)}
              {netWorth.balances_through && (
                <>
                  {" · "}
                  <AsOf date={netWorth.balances_through} label="balances through" />
                </>
              )}
            </p>
            {netWorth.partial && <BehindList behind={netWorth.behind} />}
          </>
        )}
      </Tile>
  );
}

/** Why one account makes net worth partial, in words: what to upload next. */
export function behindText(b: NetWorthBehind): string {
  if (b.reason === "no_balance") return `${b.name}: no balance yet.`;
  const what = b.source === "statement" ? "statement" : "balance";
  const next = b.source === "statement" ? "the next was due" : "an update was due";
  return b.due ? `${b.name}: last ${what} ${formatDate(b.as_of)}, ${next} ${formatDate(b.due)}.` : `${b.name}: last ${what} ${formatDate(b.as_of)}.`;
}

/** The warning under the net worth, naming each account behind and why. */
function BehindList({ behind }: { behind: NetWorthBehind[] }) {
  return (
    <div className="mt-1 text-sm">
      <p className="m-0 text-warn">Some balances are missing or old.</p>
      {behind.length > 0 && (
        <ul aria-label="Balances missing or old" className="m-0 mt-1 flex list-none flex-col gap-0.5 p-0 text-fg-secondary">
          {behind.map((b) => (
            <li key={b.account_key}>{behindText(b)}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Phase 4: the latest complete month's actuals, with the current plan beside
 * them. Spending counts saved and invested here too, like the plan, so the
 * two can be read side by side. % saved is the savings rate.
 */
function ActualTiles({
  netWorth,
  plan,
  actual,
  state,
}: {
  netWorth: DashboardNetWorth | undefined;
  plan: PlanMonth | null | undefined;
  actual: DashboardActualMonth;
  state: LoadState;
}) {
  // Short lines: the tiles are narrow. The month is the latest complete one.
  const month = `${formatMonth(actual.month)} actual`;
  const planLine = (text: string) => (plan ? `${formatMonthShort(plan.month)} plan ${text}` : undefined);
  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-4 lg:grid-cols-6">
      <NetWorthTile netWorth={netWorth} state={state} />
      <ActualTile label="Income" swatch="var(--chart-income)" value={<Money cents={actual.income_cents} proportional />} sub={month} plan={planLine(formatMoney(plan?.income_cents ?? 0))} state={state} />
      <ActualTile
        label="Spending and saved"
        swatch="var(--chart-spending)"
        value={<Money cents={actual.spending_cents + actual.saved_invested_cents} proportional />}
        sub={month}
        note={actual.saved_invested_cents !== 0 ? `Includes ${formatMoney(actual.saved_invested_cents)} saved` : undefined}
        plan={planLine(formatMoney(plan?.spending_cents ?? 0))}
        state={state}
      />
      <ActualTile
        label="Left over"
        swatch="var(--chart-leftover)"
        value={<Money cents={actual.left_over_cents} proportional />}
        sub={month}
        plan={planLine(formatMoney(plan?.left_over_cents ?? 0))}
        state={state}
      />
      <ActualTile
        label="% saved"
        value={actual.savings_rate === null ? "None" : formatPct(actual.savings_rate)}
        sub={month}
        plan={plan?.saved_pct ? planLine(formatPct(plan.saved_pct)) : undefined}
        state={state}
      />
    </div>
  );
}

function ActualTile({
  label,
  swatch,
  value,
  sub,
  note,
  plan,
  state,
}: {
  label: string;
  swatch?: string;
  value: ReactNode;
  sub: string;
  note?: string;
  plan: string | undefined;
  state: LoadState;
}) {
  return (
    <Tile label={label} swatch={swatch} state={state} has>
      <p className="m-0 text-2xl font-medium text-fg">{value}</p>
      <p className="m-0 mt-1 text-sm text-fg-secondary">{sub}</p>
      {note && <p className="m-0 text-sm text-fg-secondary">{note}</p>}
      {plan && <p className="m-0 mt-1 border-t border-line pt-1 text-sm text-fg-secondary">{plan}</p>}
    </Tile>
  );
}

interface TileProps {
  label: string;
  /** Chart color of the series this number belongs to (a swatch, never text). */
  swatch?: string;
  plan?: boolean;
  state: LoadState;
  has: boolean;
  empty?: string;
  className?: string;
  children: ReactNode;
}

function Tile({ label, swatch, plan = false, state, has, empty, className, children }: TileProps) {
  let body: ReactNode = children;
  if (!has && state.loading) {
    body = (
      <div role="status" aria-label="Loading">
        <Skeleton className="h-8 w-28" />
        <Skeleton className="mt-2 h-4 w-20" />
      </div>
    );
  } else if (!has) {
    body = <p className="m-0 text-sm text-fg-secondary">{state.error ?? empty}</p>;
  }
  return (
    <Card className={`p-4 md:p-5 ${className ?? ""}`}>
      <h2 className="m-0 mb-1 flex items-center text-sm font-medium text-fg-secondary">
        {swatch && <span aria-hidden="true" className="mr-1.5 h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: swatch }} />}
        {label}
        {plan && <PlanBadge />}
      </h2>
      {body}
    </Card>
  );
}

interface PlanTileProps {
  label: string;
  swatch: string;
  cents: number | undefined;
  sub: string | undefined;
  /** A second gray line under the month. */
  note?: string;
  state: LoadState;
  empty: string | undefined;
}

function PlanTile({ label, swatch, cents, sub, note, state, empty }: PlanTileProps) {
  return (
    <Tile label={label} swatch={swatch} plan state={state} has={cents !== undefined} empty={empty}>
      <p className="m-0 text-2xl font-medium text-fg">
        <Money cents={cents} proportional />
      </p>
      <p className="m-0 mt-1 text-sm text-fg-secondary">{sub}</p>
      {note && <p className="m-0 text-sm text-fg-secondary">{note}</p>}
    </Tile>
  );
}
