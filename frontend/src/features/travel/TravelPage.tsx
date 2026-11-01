import { useQuery } from "@tanstack/react-query";
import { Plane } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartCard, type ChartColumn } from "../../components/charts/ChartCard";
import { AXIS_TICK, CHART_ANIMATION_MS, ChartTooltipBox } from "../../components/charts/ChartTooltip";
import { niceScale } from "../../components/charts/scale";
import { Money } from "../../components/money/Money";
import { PageHeader } from "../../components/shell/PageHeader";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardHeader } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorBanner } from "../../components/ui/error-banner";
import { Skeleton } from "../../components/ui/skeleton";
import { getTrips, type TripOpenItems, type TripPoints, type TripSummary, type TripSummaryRow, type TripsResponse, type TripTiles, tripKeys } from "../../lib/api";
import { cn } from "../../lib/cn";
import { chicagoToday, formatCompact, formatMoney } from "../../lib/format";
import { useReducedMotion } from "../../lib/media";
import { useResolvedTheme } from "../../lib/theme";
import { ListSkeleton, SourceLine } from "../recurring/parts";
import { formatPoints } from "../rewards/model";
import { dueText, iouText, isPastDue, itemAmountText, KIND_GROUPS, remainingText, STATUS_TEXT, STATUS_TONE, travelHref, tripDates, whoText } from "./model";
import { plansByTrip, PlansSection, PlanTripButton, useTripPlans } from "./plans/Plans";
import { TripCard } from "./TripCard";

/**
 * Travel (docs/plans/phase-7.md section 4): trip cards for open trips, what
 * is still open, the travel summary with its chart, and completed trips.
 * No year picker: tiles, summary and completed trips cover this year, last
 * year and later. Older trips are under "Show older". `?trip=<key>` opens
 * and scrolls to that trip. Read only: the statement chat keeps the trips.
 * Trip plans (docs/plans/trip-planner.md) sit under the trips while AI is on.
 * Format 1.4: payments due (booked items with a due date) lead What's still
 * open, and maybe items are muted with a chip, in no total or tile.
 */
export function TravelPage() {
  const location = useLocation();
  const [params] = useSearchParams();
  const focus = params.get("trip");
  const q = useQuery({ queryKey: tripKeys.page, queryFn: getTrips });
  const data = q.data;
  // The banner says the load failed. The cards below say "Not loaded." quietly, not their empty text.
  const loadError = q.isError && !data ? "Not loaded." : null;
  const [showOlder, setShowOlder] = useState(false);
  const focusOlder = !!focus && !!data?.older.some((t) => t.key === focus);
  const plans = useTripPlans();
  // Hidden with the rest of AI: a list cached before AI was turned off is not shown.
  const planFor = plansByTrip(plans.shown ? plans.q.data : undefined);
  // The server's household day, so "Was due" never follows the browser's clock.
  const today = data?.today ?? chicagoToday();

  // A link to an older trip opens "Show older" once per link visit, so "Hide
  // older" still works and the same link opens it again. This runs during
  // render, not in an effect, so the card is there when the scroll below runs.
  const [olderVisit, setOlderVisit] = useState<string | null>(null);
  if (focusOlder && olderVisit !== location.key) {
    setOlderVisit(location.key);
    setShowOlder(true);
  }

  // Each link visit resets the selected card's initial open state, even when
  // the URL is unchanged. Manual toggles and data refetches keep their state.
  const cardKey = (key: string) => key === focus ? `${key}:${location.key}` : key;

  useEffect(() => {
    if (!focus || !data) return;
    document.getElementById(`trip-${focus}`)?.scrollIntoView?.({ block: "start" });
    // The trip plans load on their own and push the trips below them down.
  }, [focus, data, location.key, plans.q.status]);

  return (
    <>
      <PageHeader title="Travel" actions={plans.shown ? <PlanTripButton data={plans.q.data} /> : undefined} />
      <div className="flex min-w-0 flex-col gap-4 pb-8">
        {loadError && <ErrorBanner message="Could not load trips." onRetry={() => void q.refetch()} />}
        {data && data.trip_count === 0 ? (
          <Card>
            <EmptyState icon={<Plane size={28} strokeWidth={1.5} aria-hidden="true" />} title="No trips yet.">
              They come with the chat's next export.
            </EmptyState>
          </Card>
        ) : null}
        {data && data.trip_count === 0 && plans.shown && <PlansSection q={plans.q} />}
        {data && data.trip_count === 0 ? null : (
          <>
            <TripTilesRow tiles={data?.tiles} loading={q.isPending} />
            {focus && data && !findTrip(data, focus) && (
              <p role="status" className="m-0 rounded-lg bg-subtle px-4 py-3 text-sm text-fg">
                The app has no trip named <span className="break-all font-mono">{focus}</span>. A later update may have removed it.
              </p>
            )}
            <Section title="Trips" subtitle="Planning, booked and in progress.">
              {q.isPending && !data ? (
                <Card>
                  <ListSkeleton rows={4} />
                </Card>
              ) : data && data.active.length > 0 ? (
                data.active.map((t) => <TripCard key={cardKey(t.key)} trip={t} plan={planFor.get(t.key)} defaultOpen={focus ? focus === t.key : undefined} today={today} />)
              ) : (
                <Card>
                  {loadError ? <p className="m-0 py-6 text-center text-sm text-fg-secondary">{loadError}</p> : <EmptyState title="No trips coming up." />}
                </Card>
              )}
            </Section>
            {plans.shown && <PlansSection q={plans.q} />}
            <OpenItemsCard open={data?.open_items} today={today} loading={q.isPending} error={loadError} />
            <SummaryCard summary={data?.summary} fromYear={data?.from_year} loading={q.isPending} error={loadError} />
            <CostChart rows={data?.summary.rows} loading={q.isPending} error={loadError} />
            {data && data.completed.length > 0 && (
              <Section title="Completed trips" subtitle="Complete and final trips. Excluded items are not counted.">
                {data.completed.map((t) => (
                  <TripCard key={cardKey(t.key)} trip={t} plan={planFor.get(t.key)} defaultOpen={focus === t.key} today={today} />
                ))}
              </Section>
            )}
            {data && data.older.length > 0 && (
              <div className="flex flex-col gap-3">
                <div>
                  <Button aria-expanded={showOlder} onClick={() => setShowOlder((v) => !v)}>
                    {showOlder ? "Hide older" : `Show older (${data.older.length})`}
                  </Button>
                  <p className="m-0 mt-1 text-xs text-fg-secondary">Trips that ended before {data.from_year}. Not in the totals above.</p>
                </div>
                {showOlder && data.older.map((t) => <TripCard key={cardKey(t.key)} trip={t} plan={planFor.get(t.key)} defaultOpen={focus === t.key} today={today} />)}
              </div>
            )}
            <SourceLine runId={data?.source_run_id} />
          </>
        )}
      </div>
    </>
  );
}

function findTrip(data: TripsResponse, key: string) {
  return [...data.active, ...data.completed, ...data.older].find((t) => t.key === key);
}

function Section({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3" aria-label={title}>
      <div>
        <h2 className="m-0 text-lg font-medium text-fg">{title}</h2>
        <p className="m-0 text-sm text-fg-secondary">{subtitle}</p>
      </div>
      {children}
    </section>
  );
}

interface Tile {
  key: string;
  label: string;
  value: ReactNode;
  sub?: ReactNode;
}

/** Booked so far (cash), Estimated still to spend, Points used, Open IOUs. Two across on phones. */
export function TripTilesRow({ tiles, loading }: { tiles: TripTiles | undefined; loading: boolean }) {
  const t = tiles;
  const items: Tile[] = [
    { key: "booked", label: "Booked so far (cash)", value: t ? formatMoney(t.booked_cents) : "" },
    {
      key: "estimate",
      label: "Estimated still to spend",
      value: t ? remainingText(t.estimate_low_cents, t.estimate_high_cents, 0) : "",
      sub: t && t.tbd_count > 0 ? `plus ${t.tbd_count} TBD` : undefined,
    },
    { key: "points", label: "Points used", value: t ? <PointsValue points={t.points} /> : "" },
    {
      key: "ious",
      label: "Open IOUs",
      value: t ? (t.collect_count + t.pay_count === 0 ? "None" : formatMoney(t.collect_cents - t.pay_cents)) : "",
      sub: t && t.collect_count + t.pay_count > 0 ? `Collect ${formatMoney(t.collect_cents)} · Pay ${formatMoney(t.pay_cents)}` : undefined,
    },
  ];
  return (
    <ul aria-label="Travel summary tiles" className="m-0 grid list-none grid-cols-2 gap-3 p-0 md:grid-cols-4 md:gap-4">
      {items.map((k) => (
        <li key={k.key} className="min-w-0">
          <Card className="flex h-full flex-col items-center justify-center px-2 py-4 text-center md:p-5">
            {loading && !tiles ? (
              <div role="status" aria-label="Loading" className="flex flex-col items-center gap-2">
                <Skeleton className="h-7 w-20" />
                <Skeleton className="h-3 w-16" />
              </div>
            ) : (
              <>
                <p className="m-0 max-w-full break-words text-lg font-medium text-fg md:text-2xl">{k.value}</p>
                <p className="eyebrow m-0 mt-1">{k.label}</p>
                {k.sub && <p className="m-0 mt-1 text-xs text-fg-secondary md:text-sm">{k.sub}</p>}
              </>
            )}
          </Card>
        </li>
      ))}
    </ul>
  );
}

/**
 * One line per program, so a long program name wraps on its own line, not mid
 * phrase. On phones the name is smaller than the number; md and up it matches.
 */
function PointsValue({ points }: { points: TripPoints[] }) {
  if (points.length === 0) return "None";
  return points.map((p, i) => (
    <span key={p.program_key ?? i} className="block">
      {formatPoints(p.points_micro)}{" "}
      <span className="text-sm font-normal text-fg-secondary md:text-2xl md:font-medium md:text-fg">{p.program_name ?? "points"}</span>
    </span>
  ));
}

/** What's still open: Due (format 1.4, only when there is one), Collect (owed to us), Pay (we owe),
 * then TBD and estimate items. A maybe item is muted with a chip. */
function OpenItemsCard({ open, today, loading, error }: { open: TripOpenItems | undefined; today: string; loading: boolean; error: string | null }) {
  const none = open && open.due.length === 0 && open.collect.length === 0 && open.pay.length === 0 && open.items.length === 0;
  const hasDue = !!open && open.due.length > 0;
  return (
    <Card>
      <CardHeader title="What's still open" subtitle="Payments still to post, money to collect or pay back, and costs not known yet." />
      {loading && !open ? (
        <ListSkeleton rows={3} />
      ) : error && !open ? (
        <p className="m-0 text-sm text-fg-secondary">{error}</p>
      ) : none || !open ? (
        <p className="m-0 text-sm text-fg-secondary">Nothing open.</p>
      ) : (
        <div className={cn("grid gap-5", hasDue ? "md:grid-cols-2 xl:grid-cols-4" : "md:grid-cols-3")}>
          {hasDue && (
            <OpenList title="Due" empty="">
              {open.due.map((i) => (
                <OpenRow
                  key={`due-${i.trip_key}-${i.key}`}
                  tripKey={i.trip_key}
                  trip={i.trip_name}
                  text={i.label}
                  detail={<span className={cn("block text-xs", isPastDue(i, today) ? "text-warn" : "text-fg-secondary")}>{dueText(i, today)}</span>}
                  amount={itemAmountText(i)}
                />
              ))}
            </OpenList>
          )}
          <OpenList title="Collect" empty="Nobody owes us.">
            {open.collect.map((o) => (
              <OpenRow key={`${o.trip_key}-${o.key}`} tripKey={o.trip_key} trip={o.trip_name} text={iouText(o)} amount={<Money cents={o.amount_cents} withCents />} />
            ))}
          </OpenList>
          <OpenList title="Pay" empty="We owe nobody.">
            {open.pay.map((o) => (
              <OpenRow key={`${o.trip_key}-${o.key}`} tripKey={o.trip_key} trip={o.trip_name} text={iouText(o)} amount={<Money cents={o.amount_cents} withCents />} />
            ))}
          </OpenList>
          <OpenList title="TBD and estimates" empty="Every cost is known.">
            {open.items.map((i) => (
              <OpenRow
                key={`${i.trip_key}-${i.key}`}
                tripKey={i.trip_key}
                trip={i.trip_name}
                text={i.label}
                muted={i.maybe}
                chip={i.maybe ? <Badge>Maybe</Badge> : undefined}
                amount={<span className={i.maybe ? "text-fg-secondary" : i.status === "tbd" ? "text-warn" : undefined}>{itemAmountText(i)}</span>}
              />
            ))}
          </OpenList>
        </div>
      )}
    </Card>
  );
}

function OpenList({ title, empty, children }: { title: string; empty: string; children: ReactNode[] }) {
  return (
    <div className="min-w-0">
      <h3 className="m-0 mb-1 text-sm font-medium text-fg">{title}</h3>
      {children.length === 0 ? (
        <p className="m-0 text-sm text-fg-secondary">{empty}</p>
      ) : (
        <ul aria-label={title} className="m-0 list-none p-0">
          {children}
        </ul>
      )}
    </div>
  );
}

function OpenRow({
  tripKey,
  trip,
  text,
  amount,
  detail,
  chip,
  muted = false,
}: {
  tripKey: string;
  trip: string;
  text: string;
  amount: ReactNode;
  detail?: ReactNode;
  chip?: ReactNode;
  muted?: boolean;
}) {
  return (
    <li className="flex items-start justify-between gap-3 border-t border-line py-2 text-sm first:border-t-0">
      <span className="min-w-0">
        <span className={cn("flex flex-wrap items-center gap-x-2", muted ? "text-fg-secondary" : "text-fg")}>
          {text}
          {chip}
        </span>
        {detail}
        <Link to={travelHref(tripKey)} className="inline-flex min-h-11 items-center text-xs md:min-h-0">
          {trip}
        </Link>
      </span>
      <span className="shrink-0 tabular text-fg">{amount}</span>
    </li>
  );
}

/** Travel summary: one row per trip (when, who, total, status), with the two totals. Rows stack on phones. */
function SummaryCard({ summary, fromYear, loading, error }: { summary: TripSummary | undefined; fromYear: number | undefined; loading: boolean; error: string | null }) {
  return (
    <Card>
      <CardHeader title="Travel summary" subtitle={fromYear ? `Trips ending in ${fromYear} or later.` : undefined} />
      {loading && !summary ? (
        <ListSkeleton rows={3} />
      ) : error && !summary ? (
        <p className="m-0 text-sm text-fg-secondary">{error}</p>
      ) : !summary || summary.rows.length === 0 ? (
        <p className="m-0 text-sm text-fg-secondary">No trips in these years.</p>
      ) : (
        <>
          <ul aria-label="Trips in the summary" className="m-0 list-none p-0">
            {summary.rows.map((r) => (
              <li key={r.key} className="grid gap-1 border-t border-line py-3 text-sm first:border-t-0 md:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1fr)_auto_7rem] md:items-center md:gap-4">
                <Link to={travelHref(r.key)} className="inline-flex min-h-11 items-center font-medium md:min-h-0">
                  {r.name}
                </Link>
                <span className="text-fg-secondary">{tripDates(r)}</span>
                <span className="text-fg-secondary">{whoText(r)}</span>
                <span>
                  <Badge tone={STATUS_TONE[r.status]}>{STATUS_TEXT[r.status]}</Badge>
                </span>
                <span className="font-medium text-fg md:text-right">
                  <Money cents={r.total_cents} />
                </span>
              </li>
            ))}
          </ul>
          <dl className="m-0 mt-3 grid grid-cols-1 gap-2 border-t border-line pt-3 text-sm sm:grid-cols-2">
            <div className="flex justify-between gap-3 sm:block">
              <dt className="text-fg-secondary">Booked so far (cash)</dt>
              <dd className="m-0 font-medium text-fg">{formatMoney(summary.booked_cents)}</dd>
            </div>
            <div className="flex justify-between gap-3 sm:block">
              <dt className="text-fg-secondary">Estimated still to spend</dt>
              <dd className="m-0 font-medium text-fg">{remainingText(summary.estimate_low_cents, summary.estimate_high_cents, summary.tbd_count)}</dd>
            </div>
          </dl>
        </>
      )}
    </Card>
  );
}

type KindKey = (typeof KIND_GROUPS)[number]["key"];
const CENTS: Record<KindKey, (r: TripSummaryRow) => number> = {
  flight: (r) => r.flight_cents,
  food: (r) => r.food_cents,
  lodging: (r) => r.lodging_cents,
  other: (r) => r.other_cents,
};

export const COST_COLUMNS: ChartColumn<TripSummaryRow>[] = [
  { header: "Trip", cell: (r) => r.name },
  ...KIND_GROUPS.map((g) => ({ header: g.label, cell: (r: TripSummaryRow) => formatMoney(CENTS[g.key](r)), numeric: true })),
  { header: "Total", cell: (r) => formatMoney(r.total_cents), numeric: true },
];

/** Cost by trip, stacked by kind (flights, food, lodging, everything else). The table shows the same rows. */
function CostChart({ rows, loading, error }: { rows: TripSummaryRow[] | undefined; loading: boolean; error: string | null }) {
  const reduced = useReducedMotion();
  const theme = useResolvedTheme();
  const data = rows?.map((r) => ({ ...r, flight: r.flight_cents, food: r.food_cents, lodging: r.lodging_cents, other: r.other_cents }));
  const values = (data ?? []).flatMap((r) => [r.total_cents, ...KIND_GROUPS.map((g) => CENTS[g.key](r))]);
  const { domain, ticks } = niceScale(values, { includeZero: true });
  const height = Math.max(140, (data?.length ?? 0) * 44 + 40);
  const top = data && data.length > 0 ? data.reduce((a, b) => (b.total_cents > a.total_cents ? b : a)) : undefined;
  return (
    <ChartCard<TripSummaryRow>
      title="Cost by trip"
      subtitle="Cash so far, by kind."
      ariaLabel={top ? `${top.name} cost the most, ${formatMoney(top.total_cents)}.` : "No trips."}
      data={rows}
      columns={COST_COLUMNS}
      rowKey={(r) => r.key}
      tableCaption="Cost by trip, by kind"
      loading={loading}
      error={error}
      quietError
      emptyText="No trips in these years."
      height={height}
      footer={
        <ul className="m-0 mt-3 flex list-none flex-wrap gap-x-4 gap-y-1 p-0 text-sm text-fg-secondary" aria-label="Legend">
          {KIND_GROUPS.map((g) => (
            <li key={g.key} className="flex items-center gap-1.5">
              <span aria-hidden="true" className="h-2 w-2 rounded-full" style={{ background: g.color }} />
              {g.label}
            </li>
          ))}
        </ul>
      }
    >
      <ResponsiveContainer width="100%" height={height}>
        <BarChart key={theme} accessibilityLayer={false} data={data} layout="vertical" stackOffset="sign" margin={{ top: 4, right: 24, bottom: 0, left: 0 }}>
          <CartesianGrid horizontal={false} stroke="var(--chart-grid)" />
          <XAxis type="number" domain={domain} ticks={ticks} interval={0} tickFormatter={formatCompact} tick={AXIS_TICK} axisLine={false} tickLine={false} />
          <YAxis type="category" dataKey="name" tick={AXIS_TICK} axisLine={false} tickLine={false} width={112} />
          <Tooltip
            cursor={{ fill: "var(--bg-hover)" }}
            content={({ active, payload }) => {
              const r = active ? (payload?.[0]?.payload as TripSummaryRow | undefined) : undefined;
              if (!r) return null;
              return (
                <ChartTooltipBox
                  title={r.name}
                  rows={KIND_GROUPS.map((g) => ({ key: g.key, label: g.label, value: formatMoney(CENTS[g.key](r)), color: g.color }))}
                  note={`Total ${formatMoney(r.total_cents)}`}
                />
              );
            }}
          />
          {KIND_GROUPS.map((g, i) => (
            <Bar
              key={g.key}
              dataKey={g.key}
              name={g.label}
              stackId="kind"
              fill={g.color}
              stroke="var(--bg-card)"
              strokeWidth={2}
              maxBarSize={24}
              radius={i === KIND_GROUPS.length - 1 ? [0, 4, 4, 0] : 0}
              isAnimationActive={!reduced}
              animationDuration={CHART_ANIMATION_MS}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}
