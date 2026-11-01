import { Sparkles } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartCard, type ChartColumn } from "../../components/charts/ChartCard";
import { AXIS_TICK, CHART_ANIMATION_MS, ChartTooltipBox } from "../../components/charts/ChartTooltip";
import { niceScale } from "../../components/charts/scale";
import { AsOf } from "../../components/money/AsOf";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardHeader } from "../../components/ui/card";
import { TableScroll } from "../../components/ui/table-scroll";
import type { RewardProgram, RewardRedemption, RewardsResponse, RewardValueRow } from "../../lib/api";
import { useMe } from "../../lib/auth";
import { cn } from "../../lib/cn";
import { formatCompact, formatDate, formatMoney, formatShortDate } from "../../lib/format";
import { useReducedMotion } from "../../lib/media";
import { useResolvedTheme } from "../../lib/theme";
import { ListSkeleton } from "../recurring/parts";
import { AddProgram } from "./AddProgram";
import { BalanceHistoryDrawer } from "./BalanceHistory";
import { BalancesDrawer, balancePrograms } from "./BalancesDrawer";
import { travelHref } from "../travel/model";
import { balanceText, cppText, formatPoints, rangeText, redemptionText, sameDayText, SOURCE_TEXT, STALE_ENTER, usedText } from "./model";
import { CardPending, MiniList } from "./parts";
import { ProgramNotices, type Role } from "./ProgramNotices";

/** One series (estimated value), so one color and no legend. */
const VALUE_COLOR = "var(--chart-7)";

export function PointsTab({ data, loading, error }: { data: RewardsResponse | undefined; loading: boolean; error: string | null }) {
  const me = useMe();
  const role: Role = me.data ? (me.data.role === "admin" ? "admin" : "member") : null;
  const [params, setParams] = useSearchParams();
  // ?update=balances opens the drawer, so Checks and the This month strip can link here.
  // The link can come again while the tab is open, so it is read on each change.
  const fromLink = params.get("update") === "balances";
  const [updating, setUpdating] = useState(fromLink);
  useEffect(() => {
    if (fromLink) setUpdating(true);
  }, [fromLink]);
  const [historyKey, setHistoryKey] = useState<string | null>(null);
  const programs = data?.programs ?? [];
  const primary = programs.filter((p) => p.group === "primary");
  const secondary = programs.filter((p) => p.group === "secondary");
  const enterable = balancePrograms(programs);
  const history = programs.find((p) => p.key === historyKey);
  const chatSends = new Set((data?.notices ?? []).filter((n) => n.kind === "program_app_key").map((n) => n.key));

  function onUpdateOpenChange(open: boolean) {
    setUpdating(open);
    if (!open && params.has("update")) {
      setParams(
        (p) => {
          p.delete("update");
          return p;
        },
        { replace: true },
      );
    }
  }

  return (
    <>
      {data && data.highlights.length > 0 && <Highlights lines={data.highlights} />}
      {data && <ProgramNotices notices={data.notices} orphans={data.orphans} programs={programs} role={role} />}
      {loading && !data ? (
        <Card>
          <ListSkeleton rows={4} />
        </Card>
      ) : (
        <>
          {enterable.length > 0 && (
            <div className="flex justify-end">
              <Button variant="primary" className="w-full justify-center md:w-auto" onClick={() => setUpdating(true)}>
                Update balances
              </Button>
            </div>
          )}
          {primary.length > 0 && (
            <ul aria-label="Main programs" className="m-0 grid list-none gap-4 p-0 lg:grid-cols-2">
              {primary.map((p) => (
                <li key={p.key} className="min-w-0">
                  <ProgramCard p={p} onHistory={() => setHistoryKey(p.key)} />
                </li>
              ))}
            </ul>
          )}
          {data && (secondary.length > 0 || role === "admin") && <SecondaryCard programs={secondary} onHistory={setHistoryKey} role={role} />}
        </>
      )}
      {data && enterable.length > 0 && <BalancesDrawer open={updating} onOpenChange={onUpdateOpenChange} programs={programs} />}
      {history && (
        <BalanceHistoryDrawer
          program={history}
          open
          onOpenChange={(open) => !open && setHistoryKey(null)}
          canDeleteProgram={role === "admin" && history.origin === "app" && !chatSends.has(history.key)}
        />
      )}
      <ValueTableCard data={data} loading={loading} error={error} />
      <ValueChart rows={data?.value_table.rows.filter((r) => r.basis !== "not_valued")} loading={loading} error={error} />
      <RedemptionsCard rows={data?.redemptions} programs={programs} loading={loading} error={error} />
    </>
  );
}

function Highlights({ lines }: { lines: string[] }) {
  return (
    <section aria-label="Highlights" className="flex gap-3 rounded-card bg-accent-tint p-4 md:p-5">
      <Sparkles size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-accent-text" />
      <ul className="m-0 flex list-none flex-col gap-1 p-0 text-sm text-fg">
        {lines.map((l) => (
          <li key={l}>{l}</li>
        ))}
      </ul>
    </section>
  );
}

function StatusLine({ p }: { p: RewardProgram }) {
  if (!p.status_tier) return null;
  return (
    <p className="m-0 text-sm text-fg">
      {p.status_tier} status{p.status_via_name ? ` through the ${p.status_via_name}` : ""}
    </p>
  );
}

/** "From a statement", "Given to the chat" or "Entered in the app". */
function SourceText({ p }: { p: RewardProgram }) {
  if (!p.source) return null;
  return <span>· {SOURCE_TEXT[p.source]}</span>;
}

/** The as of date. A balance no statement refreshes asks for a new one once stale, in words. */
function ProgramAsOf({ p }: { p: RewardProgram }) {
  return <AsOf date={p.as_of} stale={p.stale} staleText={p.source === "statement" ? undefined : STALE_ENTER} showStaleText />;
}

function SameDayNote({ p }: { p: RewardProgram }) {
  const text = sameDayText(p);
  return text ? <p className="m-0 text-sm text-fg-secondary">{text}</p> : null;
}

function HistoryButton({ p, onClick }: { p: RewardProgram; onClick: () => void }) {
  if (p.kind === "timeshare") return null;
  return (
    <Button size="sm" variant="ghost" className="-ml-3 self-start" onClick={onClick}>
      Balance history{" "}<span className="sr-only">for {p.name}</span>
    </Button>
  );
}

/** "Earned on Orchard card, Cobalt card". Nothing when no card earns it. */
function EarnedOn({ p }: { p: RewardProgram }) {
  if (p.earned_on.length === 0) return null;
  return (
    <p className="m-0 text-sm text-fg">
      <span className="text-fg-secondary">Earned on </span>
      {p.earned_on.map((c) => c.name ?? c.account_key).join(", ")}
    </p>
  );
}

/** "Best uses" and "Avoid" side by side from sm up. Nothing when both are empty. */
function UseLists({ p, className }: { p: RewardProgram; className?: string }) {
  if (p.best_uses.length === 0 && p.avoid.length === 0) return null;
  return (
    <div className={cn("grid gap-3 sm:grid-cols-2", className)}>
      <MiniList title="Best uses" items={p.best_uses} />
      <MiniList title="Avoid" items={p.avoid} />
    </div>
  );
}

function ExpiryLine({ p }: { p: RewardProgram }) {
  if (!p.expires_on) return null;
  return <p className="m-0 text-sm text-fg">Points expire {formatDate(p.expires_on)}</p>;
}

/** Format 1.4: the newest day a done redemption used this program. */
function LastRedeemedLine({ p }: { p: RewardProgram }) {
  if (!p.last_redeemed_on) return null;
  return <p className="m-0 text-sm text-fg-secondary">Last redeemed {formatDate(p.last_redeemed_on)}</p>;
}

function ProgramCard({ p, onHistory }: { p: RewardProgram; onHistory: () => void }) {
  const cpp = cppText(p.cpp_low, p.cpp_high);
  return (
    <Card className="flex h-full flex-col gap-3">
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h2 className="m-0 text-lg font-medium text-fg">{p.name}</h2>
          {p.owner_name && <p className="m-0 text-sm text-fg-secondary">{p.owner_name}</p>}
        </div>
        <span className="flex flex-wrap items-center gap-2">
          {p.origin === "app" && <Badge>Added in the app</Badge>}
          <Badge tone="accent">{balanceText(p)}</Badge>
        </span>
      </header>
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <p className="m-0 text-base font-medium text-fg">
          {p.value_low_cents !== null ? `Worth ${rangeText(p.value_low_cents, p.value_high_cents)}` : "Not valued"}
        </p>
        {cpp && <span className="text-sm text-fg-secondary">{cpp}</span>}
      </div>
      <p className="m-0 flex flex-wrap items-center gap-x-2 text-sm text-fg-secondary">
        <ProgramAsOf p={p} />
        <SourceText p={p} />
      </p>
      <SameDayNote p={p} />
      <StatusLine p={p} />
      <EarnedOn p={p} />
      <UseLists p={p} />
      {p.allocations.length > 0 && <Allocations p={p} />}
      <ExpiryLine p={p} />
      <LastRedeemedLine p={p} />
      {p.note && <p className="m-0 text-sm text-fg-secondary">{p.note}</p>}
      <HistoryButton p={p} onClick={onHistory} />
    </Card>
  );
}

function SecondaryCard({ programs, onHistory, role }: { programs: RewardProgram[]; onHistory: (key: string) => void; role: Role }) {
  return (
    <Card>
      <CardHeader title="Other programs" />
      {programs.length > 0 && (
        <ul className="m-0 flex list-none flex-col p-0" aria-label="Other programs">
          {programs.map((p) => (
            <li key={p.key} className="border-t border-line py-3 first:border-t-0 first:pt-0">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="m-0 flex flex-wrap items-center gap-2 font-medium text-fg">
                    {p.name}
                    {p.origin === "app" && <Badge>Added in the app</Badge>}
                  </p>
                  <p className="m-0 flex flex-wrap items-center gap-x-2 text-sm text-fg-secondary">
                    {p.owner_name && <span>{p.owner_name}</span>}
                    <ProgramAsOf p={p} />
                    <SourceText p={p} />
                  </p>
                  <SameDayNote p={p} />
                  <StatusLine p={p} />
                  <EarnedOn p={p} />
                  <ExpiryLine p={p} />
                  <LastRedeemedLine p={p} />
                  {p.note && <p className="m-0 text-sm text-fg-secondary">{p.note}</p>}
                </div>
                {p.kind !== "timeshare" && (
                  <div className="shrink-0 text-right">
                    <p className="m-0 text-sm font-medium text-fg">{balanceText(p)}</p>
                    {p.value_low_cents !== null && p.kind !== "cash_back" && (
                      <p className="m-0 text-sm text-fg-secondary">{rangeText(p.value_low_cents, p.value_high_cents)}</p>
                    )}
                  </div>
                )}
              </div>
              {/* Full row width, so the lists do not squeeze in next to the balance. */}
              <UseLists p={p} className="mt-2" />
              {p.allocations.length > 0 && <Allocations p={p} />}
              <HistoryButton p={p} onClick={() => onHistory(p.key)} />
            </li>
          ))}
        </ul>
      )}
      {role !== null && (
        <div className={programs.length > 0 ? "mt-3 border-t border-line pt-3" : undefined}>
          <AddProgram isAdmin={role === "admin"} />
        </div>
      )}
    </Card>
  );
}

/** Timeshare points by year, with expiry and book-by dates. */
function Allocations({ p }: { p: RewardProgram }) {
  return (
    <ul className="m-0 mt-2 grid list-none gap-2 p-0 sm:grid-cols-2" aria-label={`${p.name} by year`}>
      {p.allocations.map((a) => (
        <li key={a.year} className="rounded-lg bg-subtle px-3 py-2 text-sm">
          <p className="m-0 flex items-baseline justify-between gap-2 font-medium text-fg">
            <span>{a.year}</span>
            <span className="tabular">{formatPoints(a.left_micro)} left</span>
          </p>
          <p className="m-0 text-fg-secondary">
            {formatPoints(a.allocated_micro)} allocated, {formatPoints(a.used_micro)} used
            {a.pulled_forward_micro ? `, ${formatPoints(a.pulled_forward_micro)} pulled forward` : ""}
          </p>
          {(a.book_by || a.expires_on) && (
            <p className="m-0 text-fg-secondary">
              {a.book_by && `Book by ${formatShortDate(a.book_by)}`}
              {a.book_by && a.expires_on && " · "}
              {a.expires_on && `Expires ${formatShortDate(a.expires_on)}`}
            </p>
          )}
        </li>
      ))}
    </ul>
  );
}

function basisText(r: RewardValueRow): string {
  if (r.basis === "face") return "Cash back at face value";
  if (r.basis === "not_valued") return "Not valued";
  return cppText(r.cpp_low, r.cpp_high) ?? "";
}

/** "Estimated total portfolio value": balance x cents per point. A table on wide screens, a list on phones. */
function ValueTableCard({ data, loading, error }: { data: RewardsResponse | undefined; loading: boolean; error: string | null }) {
  const t = data?.value_table;
  return (
    <Card>
      <CardHeader title="Estimated total portfolio value" subtitle="Balance times cents per point. Cash back at face value." />
      {!t ? (
        <CardPending loading={loading} error={error} rows={4} />
      ) : (
        <>
          <TableScroll className="hidden md:block" label="Estimated value by program">
            <table className="data-table compact">
              <caption className="sr-only">Estimated value by program</caption>
              <thead>
                <tr>
                  <th scope="col">Program</th>
                  <th scope="col" className="num">
                    Balance
                  </th>
                  <th scope="col">Basis</th>
                  <th scope="col" className="num">
                    Low
                  </th>
                  <th scope="col" className="num">
                    High
                  </th>
                </tr>
              </thead>
              <tbody>
                {t.rows.map((r) => (
                  <tr key={r.key} className={r.basis === "not_valued" ? "text-fg-secondary" : undefined}>
                    <th scope="row" className="font-normal">
                      {r.name}
                    </th>
                    <td className="num">{r.kind === "timeshare" ? "" : balanceText(r)}</td>
                    <td>{basisText(r)}</td>
                    <td className="num">{r.low_cents === null ? "" : formatMoney(r.low_cents)}</td>
                    <td className="num">{r.high_cents === null ? "" : formatMoney(r.high_cents)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <th scope="row">Total</th>
                  <td />
                  <td />
                  <td className="num font-medium">{formatMoney(t.low_cents)}</td>
                  <td className="num font-medium">{formatMoney(t.high_cents)}</td>
                </tr>
              </tfoot>
            </table>
          </TableScroll>
          <ul className="m-0 flex list-none flex-col p-0 md:hidden" aria-label="Estimated value by program">
            {t.rows.map((r) => (
              <li key={r.key} className="flex items-start justify-between gap-3 border-t border-line py-3 first:border-t-0 first:pt-0">
                <div className="min-w-0">
                  <p className="m-0 font-medium text-fg">{r.name}</p>
                  <p className="m-0 text-sm text-fg-secondary">{basisText(r)}</p>
                </div>
                <span className="shrink-0 text-right text-sm text-fg">{rangeText(r.low_cents, r.high_cents)}</span>
              </li>
            ))}
            <li className="flex justify-between gap-3 border-t border-line pt-3 font-medium text-fg">
              <span>Total</span>
              <span>{rangeText(t.low_cents, t.high_cents)}</span>
            </li>
          </ul>
        </>
      )}
    </Card>
  );
}

interface RangeRow {
  key: string;
  name: string;
  low: number;
  span: number;
  high: number;
}

const RANGE_COLUMNS: ChartColumn<RangeRow>[] = [
  { header: "Program", cell: (r) => r.name },
  { header: "Low", cell: (r) => formatMoney(r.low), numeric: true },
  { header: "High", cell: (r) => formatMoney(r.high), numeric: true },
];

/** Value range by program: a bar from the low value to the high value. */
function ValueChart({ rows, loading, error }: { rows: RewardValueRow[] | undefined; loading: boolean; error: string | null }) {
  const reduced = useReducedMotion();
  const theme = useResolvedTheme();
  const data: RangeRow[] | undefined = rows?.map((r) => ({
    key: r.key,
    name: r.name,
    low: r.low_cents ?? 0,
    high: r.high_cents ?? 0,
    span: (r.high_cents ?? 0) - (r.low_cents ?? 0),
  }));
  const { domain, ticks } = niceScale((data ?? []).map((r) => r.high), { includeZero: true });
  const height = Math.max(120, (data?.length ?? 0) * 44 + 32);
  const top = data?.[0];
  return (
    <ChartCard<RangeRow>
      title="Value range by program"
      subtitle="Each bar runs from the low estimate to the high one."
      ariaLabel={top ? `${top.name} is worth the most, ${formatMoney(top.low)} to ${formatMoney(top.high)}.` : "No valued programs."}
      data={data}
      columns={RANGE_COLUMNS}
      rowKey={(r) => r.key}
      tableCaption="Value range by program"
      loading={loading}
      error={error}
      quietError
      emptyText="No programs with a value."
      height={height}
    >
      <ResponsiveContainer width="100%" height={height}>
        <BarChart key={theme} accessibilityLayer={false} data={data} layout="vertical" margin={{ top: 4, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid horizontal={false} stroke="var(--chart-grid)" />
          <XAxis type="number" domain={domain} ticks={ticks} interval={0} tickFormatter={formatCompact} tick={AXIS_TICK} axisLine={false} tickLine={false} />
          <YAxis type="category" dataKey="name" tick={AXIS_TICK} axisLine={false} tickLine={false} width={112} />
          <Tooltip
            cursor={{ fill: "var(--bg-hover)" }}
            content={({ active, payload }) => {
              const r = active ? (payload?.[0]?.payload as RangeRow | undefined) : undefined;
              if (!r) return null;
              return <ChartTooltipBox title={r.name} rows={[{ key: "v", label: "estimated", value: rangeText(r.low, r.high), color: VALUE_COLOR }]} />;
            }}
          />
          <Bar dataKey="low" stackId="v" fill="transparent" isAnimationActive={false} maxBarSize={24} />
          <Bar dataKey="span" stackId="v" fill={VALUE_COLOR} radius={4} maxBarSize={24} minPointSize={3} isAnimationActive={!reduced} animationDuration={CHART_ANIMATION_MS} />
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

const REDEMPTION_TITLE: Record<RewardRedemption["status"], string> = {
  ready: "Ready",
  consider: "Consider",
  done: "Done",
};

/**
 * "Upcoming redemption opportunities": Ready, Consider, Done. Ready and Consider
 * say what is needed and what is there; Done says what it used and, from format
 * 1.4, on what day (the server sends done ones newest first). Each links to its
 * trip when the app has it.
 */
function RedemptionsCard({
  rows,
  programs,
  loading,
  error,
}: {
  rows: RewardRedemption[] | undefined;
  programs: RewardProgram[];
  loading: boolean;
  error: string | null;
}) {
  const kinds = new Map(programs.map((p) => [p.key, p.kind]));
  const groups = (["ready", "consider", "done"] as const)
    .map((s) => ({ status: s, items: (rows ?? []).filter((r) => r.status === s) }))
    .filter((g) => g.items.length > 0);
  return (
    <Card>
      <CardHeader title="Upcoming redemption opportunities" />
      {!rows ? (
        <CardPending loading={loading} error={error} rows={2} />
      ) : groups.length === 0 ? (
        <p className="m-0 text-sm text-fg-secondary">None right now.</p>
      ) : (
        <div className="flex flex-col gap-4">
          {groups.map((g) => (
            <section key={g.status} aria-label={REDEMPTION_TITLE[g.status]}>
              <h3 className="m-0 mb-1 text-sm font-medium text-fg-secondary">{REDEMPTION_TITLE[g.status]}</h3>
              <ul className="m-0 flex list-none flex-col p-0">
                {g.items.map((r) => {
                  const kind = kinds.get(r.program_key) ?? "points";
                  const done = r.status === "done";
                  return (
                    <li key={r.key} className="flex flex-wrap items-start justify-between gap-3 border-t border-line py-3 first:border-t-0">
                      <div className="min-w-0">
                        <p className="m-0 font-medium text-fg">{r.title}</p>
                        <p className="m-0 text-sm text-fg-secondary">
                          {r.program_name}
                          {r.note && ` · ${r.note}`}
                        </p>
                        {r.trip_key && r.trip_name && (
                          <Link to={travelHref(r.trip_key)} aria-label={`Trip: ${r.trip_name}`} className="inline-flex min-h-11 items-center text-sm md:min-h-0">
                            {r.trip_name}
                          </Link>
                        )}
                      </div>
                      {(r.points_needed_micro !== null || (done && r.done_on)) && (
                        <span className="shrink-0 text-right text-sm">
                          <span className="block text-fg">
                            {done
                              ? usedText(kind, r.points_needed_micro, r.done_on)
                              : r.points_needed_micro !== null && redemptionText(kind, r.points_needed_micro, r.have_micro)}
                          </span>
                          {!done && r.enough === false && <Badge tone="warn">Short</Badge>}
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
      )}
    </Card>
  );
}
