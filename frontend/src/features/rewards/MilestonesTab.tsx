import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartCard, type ChartColumn } from "../../components/charts/ChartCard";
import { AXIS_TICK, CHART_ANIMATION_MS, ChartTooltipBox } from "../../components/charts/ChartTooltip";
import { niceScale } from "../../components/charts/scale";
import { Badge } from "../../components/ui/badge";
import { Card, CardHeader } from "../../components/ui/card";
import type { RewardMilestone, RewardMonthSpend, RewardsResponse } from "../../lib/api";
import { formatCompact, formatDate, formatMoney, formatMonth, formatMonthShort, formatShortDate } from "../../lib/format";
import { useReducedMotion } from "../../lib/media";
import { useResolvedTheme } from "../../lib/theme";
import { PACE_TEXT, PACE_TONE, pctOf } from "./model";
import { CardPending, ProgressBar } from "./parts";

/** Card spend counts as spending, so it takes the spending color. */
const SPEND_COLOR = "var(--chart-spending)";

export function MilestonesTab({ data, loading, error }: { data: RewardsResponse | undefined; loading: boolean; error: string | null }) {
  const ms = data?.milestones;
  if (!ms) {
    return (
      <Card>
        <CardHeader title="Card spend milestones" />
        <CardPending loading={loading} error={error} rows={3} />
      </Card>
    );
  }
  if (ms.length === 0) {
    return (
      <Card>
        <CardHeader title="Card spend milestones" />
        <p className="m-0 text-sm text-fg-secondary">No spend milestones right now.</p>
      </Card>
    );
  }
  return (
    <section aria-label="Card spend milestones" className="flex flex-col gap-4">
      <h2 className="m-0 text-lg font-medium text-fg">Card spend milestones</h2>
      <p className="m-0 -mt-3 text-sm text-fg-secondary">
        Spend from rows: purchases minus refunds on the card this year, every cardholder. Plan It purchases count. Fees,
        interest, plan fees, payments and transfers do not.
      </p>
      {ms.map((m) => (
        <div key={m.key} className="grid items-start gap-4 lg:grid-cols-2">
          <MilestoneCard m={m} />
          <SpendChart m={m} />
        </div>
      ))}
    </section>
  );
}

function isMet(m: RewardMilestone): boolean {
  return m.status === "met" || m.pace === "met";
}

/** The chat's missed, or the rows reached Dec 31 short of the threshold. */
function isMissed(m: RewardMilestone): boolean {
  return !isMet(m) && (m.status === "missed" || m.pace === "missed");
}

function statusBadge(m: RewardMilestone) {
  if (isMet(m)) return <Badge tone="good">Met{m.met_on ? ` ${formatShortDate(m.met_on)}` : ""}</Badge>;
  if (isMissed(m)) return <Badge>Missed</Badge>;
  return <Badge tone="accent">Open</Badge>;
}

function MilestoneCard({ m }: { m: RewardMilestone }) {
  const met = isMet(m);
  // Met or missed: nothing left to plan, and the header badge says which.
  const done = met || isMissed(m);
  return (
    <Card className="flex flex-col gap-3">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="m-0 text-lg font-medium text-fg">
            {m.account_name} · {m.year}
          </h3>
          <p className="m-0 text-sm text-fg-secondary">{m.reward}</p>
        </div>
        {statusBadge(m)}
      </header>
      <div>
        <p className="m-0 flex flex-wrap items-baseline justify-between gap-2 text-sm">
          <span className="text-base font-medium text-fg">{formatMoney(m.spent_cents)} spent</span>
          <span className="text-fg-secondary">of {formatMoney(m.threshold_cents)}</span>
        </p>
        <div className="mt-2">
          <ProgressBar
            pct={pctOf(m.spent_cents, m.threshold_cents)}
            color={met ? "var(--fill-good)" : SPEND_COLOR}
            label={`${formatMoney(m.spent_cents)} of ${formatMoney(m.threshold_cents)} spent`}
          />
        </div>
      </div>
      <dl className="m-0 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        {!done && (
          <div>
            <dt className="text-fg-secondary">To go</dt>
            <dd className="m-0 font-medium text-fg">{formatMoney(m.to_go_cents)} to go</dd>
          </div>
        )}
        {!done && m.need_per_month_cents !== null && (
          <div>
            <dt className="text-fg-secondary">Pace needed</dt>
            <dd className="m-0 font-medium text-fg">Need ~{formatMoney(m.need_per_month_cents)}/mo</dd>
          </div>
        )}
        {m.projection_cents !== null && !done && (
          <div>
            <dt className="text-fg-secondary">Year-end projection</dt>
            <dd className="m-0 font-medium text-fg">{formatMoney(m.projection_cents)}</dd>
          </div>
        )}
        {!done && m.pace && (
          <div>
            <dt className="text-fg-secondary">Pace</dt>
            <dd className="m-0">
              <Badge tone={PACE_TONE[m.pace]}>{PACE_TEXT[m.pace]}</Badge>
            </dd>
          </div>
        )}
      </dl>
      {m.rows_through && (
        <p className="m-0 text-sm text-fg-secondary">
          Rows {m.rows_from && m.rows_from > `${m.year}-01-01` ? `from ${formatShortDate(m.rows_from)} ` : ""}through{" "}
          {formatDate(m.rows_through)}.
        </p>
      )}
      {m.reported_differs && m.reported_spent_cents !== null && (
        <p className="m-0 text-sm text-fg">
          The chat counts {formatMoney(m.reported_spent_cents)}
          {m.reported_as_of && ` through ${formatShortDate(m.reported_as_of)}`}. The rows show{" "}
          {formatMoney(m.app_at_reported_cents ?? m.spent_cents)}.
        </p>
      )}
      {m.plan && <p className="m-0 text-sm text-fg">{m.plan}</p>}
      {m.note && <p className="m-0 text-sm text-fg-secondary">{m.note}</p>}
      <CardBills m={m} />
    </Card>
  );
}

/** The recurring bills on the card, and the ones set to move to it. Nothing when there are none. */
function CardBills({ m }: { m: RewardMilestone }) {
  const moving = m.bills_to_move;
  if (m.bills.length === 0 && moving.length === 0) return null;
  const movingCents = moving.reduce((sum, b) => sum + b.monthly_cents, 0);
  return (
    <div className="flex flex-col gap-2 border-t border-line pt-3 text-sm">
      {m.bills.length > 0 && (
        <div>
          <p className="m-0 font-medium text-fg">Bills on this card: about {formatMoney(m.bills_monthly_cents)} a month</p>
          <ul
            aria-label={`Bills on ${m.account_name ?? "this card"}`}
            className="m-0 mt-1 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-fg-secondary"
          >
            {m.bills.map((b) => (
              <li key={b.key} className="min-w-0 break-words">
                {b.name}
              </li>
            ))}
          </ul>
        </div>
      )}
      {moving.length > 0 && (
        <p className="m-0 break-words text-fg">
          Set to move here, about {formatMoney(movingCents)} a month: {moving.map((b) => b.name).join(", ")}
        </p>
      )}
    </div>
  );
}

const MONTH_COLUMNS: ChartColumn<RewardMonthSpend>[] = [
  { header: "Month", cell: (r) => formatMonth(r.month) },
  { header: "Spent so far", cell: (r) => formatMoney(r.cumulative_cents), numeric: true },
];

/** Cumulative spend by month with the threshold line. */
function SpendChart({ m }: { m: RewardMilestone }) {
  const reduced = useReducedMotion();
  const theme = useResolvedTheme();
  const rows = m.months;
  const { domain, ticks } = niceScale([...rows.map((r) => r.cumulative_cents), m.threshold_cents], { includeZero: true });
  const last = rows[rows.length - 1];
  return (
    <ChartCard<RewardMonthSpend>
      title="Spend by month"
      subtitle={`${m.account_name}, running total against ${formatMoney(m.threshold_cents)}`}
      ariaLabel={
        last
          ? `${formatMoney(last.cumulative_cents)} spent through ${formatMonth(last.month)}, against ${formatMoney(m.threshold_cents)}.`
          : "No rows this year."
      }
      data={rows}
      columns={MONTH_COLUMNS}
      rowKey={(r) => r.month}
      tableCaption={`${m.account_name} spend by month`}
      emptyText="No statement rows for this card this year."
      height={220}
      footer={
        <p className="m-0 mt-2 flex items-center gap-2 text-sm text-fg-secondary">
          <span aria-hidden="true" className="h-0.5 w-4 rounded-full" style={{ background: SPEND_COLOR }} /> Spent so far
          <span aria-hidden="true" className="ml-3 h-0 w-4 border-t-2 border-dashed border-chart-target" /> Threshold
        </p>
      }
    >
      <ResponsiveContainer width="100%" height={220}>
        <LineChart key={theme} accessibilityLayer={false} data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
          <XAxis dataKey="month" tickFormatter={formatMonthShort} tick={AXIS_TICK} axisLine={{ stroke: "var(--chart-axis)" }} tickLine={false} tickMargin={8} />
          <YAxis domain={domain} ticks={ticks} interval={0} tickFormatter={formatCompact} tick={AXIS_TICK} axisLine={false} tickLine={false} width={48} />
          <ReferenceLine y={m.threshold_cents} stroke="var(--chart-target)" strokeDasharray="4 4" />
          <Tooltip
            cursor={{ stroke: "var(--border-strong)", strokeWidth: 1 }}
            content={({ active, payload }) => {
              const r = active ? (payload?.[0]?.payload as RewardMonthSpend | undefined) : undefined;
              if (!r) return null;
              return (
                <ChartTooltipBox
                  title={formatMonth(r.month)}
                  rows={[{ key: "s", label: "spent so far", value: formatMoney(r.cumulative_cents), color: SPEND_COLOR }]}
                />
              );
            }}
          />
          <Line type="linear" dataKey="cumulative_cents" stroke={SPEND_COLOR} strokeWidth={2} dot={{ r: 3 }} isAnimationActive={!reduced} animationDuration={CHART_ANIMATION_MS} />
        </LineChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}
