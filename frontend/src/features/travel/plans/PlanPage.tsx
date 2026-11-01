import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, CircleAlert, TriangleAlert } from "lucide-react";
import { type FormEvent, type ReactNode, useEffect, useId, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { PageHeader } from "../../../components/shell/PageHeader";
import { Badge, type BadgeTone } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { Card, CardHeader } from "../../../components/ui/card";
import { EmptyState } from "../../../components/ui/empty-state";
import { ErrorBanner } from "../../../components/ui/error-banner";
import { Skeleton } from "../../../components/ui/skeleton";
import {
  ApiError,
  deleteTripPlan,
  followUpTripPlan,
  getTripPlan,
  getTrips,
  linkTripPlan,
  type PlanDay,
  type PlanLinkOut,
  type PlanVersion,
  type PlanView,
  tripKeys,
  tripPlanKeys,
} from "../../../lib/api";
import { cn } from "../../../lib/cn";
import { formatMoney, formatShortDate, formatTimestamp } from "../../../lib/format";
import { modelName, RUN_POLL_MS, showsStored, useAiStatus } from "../../ai/api";
import { SafeText } from "../../ai/SafeText";
import { rangeText, tripDates, whoText } from "../model";
import {
  CHECK_INTRO,
  COST_KIND_TEXT,
  FOLLOW_UP_MAX,
  GROUND_TEXT,
  INTERESTS,
  ITEM_KIND_TEXT,
  LEAN_OPTIONS,
  neededText,
  nightsText,
  PACE_OPTIONS,
  PART_TEXT,
  PLAN_STATUS_TEXT,
  PLAN_STATUS_TONE,
  pointsCount,
  STEP_TEXT,
  usd,
  usdRange,
  valueText,
} from "./model";
import { PlanLink, SourceLinks } from "./PlanLink";

/**
 * One trip plan (docs/plans/trip-planner.md section 9): the summary and cost
 * range, the points plan, flights, where to stay, getting around, day by day
 * with the night bar, the checks and the links. Older versions stay readable.
 * While a plan is being made the page checks it every few seconds.
 */
export function PlanPage() {
  const { planId = "" } = useParams();
  const st = useAiStatus();
  const shown = showsStored(st.data);
  const q = useQuery({
    queryKey: tripPlanKeys.one(planId),
    queryFn: () => getTripPlan(planId),
    enabled: shown && /^[0-9a-f]{32}$/.test(planId),
    refetchInterval: (query) => (query.state.data?.status === "running" ? RUN_POLL_MS : false),
  });
  const qc = useQueryClient();
  const status = q.data?.status;
  const was = useRef(status);
  // When a run ends, the Travel list and the AI usage change too.
  useEffect(() => {
    if (was.current === "running" && status && status !== "running") {
      void qc.invalidateQueries({ queryKey: tripPlanKeys.list });
      void qc.invalidateQueries({ queryKey: ["ai"] });
    }
    was.current = status;
  }, [status, qc]);

  const back = (
    <div className="mb-3">
      <Link to="/travel" className="inline-flex min-h-11 items-center gap-1 text-sm md:min-h-0">
        <ChevronLeft size={16} aria-hidden="true" />
        Travel
      </Link>
    </div>
  );

  if (st.isPending) {
    return (
      <>
        <PageHeader title="Trip plan" />
        {back}
        <Skeleton className="h-48" />
      </>
    );
  }
  if (!shown) {
    return (
      <>
        <PageHeader title="Trip plan" />
        {back}
        <Card>
          <EmptyState title="Trip plans show when AI is on.">An admin can turn AI on in Settings.</EmptyState>
        </Card>
      </>
    );
  }
  const missing = (q.error instanceof ApiError && q.error.status === 404) || !/^[0-9a-f]{32}$/.test(planId);
  return (
    <>
      <PageHeader title={q.data?.title || "Trip plan"} />
      {back}
      {missing ? (
        <Card>
          <EmptyState title="This plan is not here.">It was deleted, or the link is wrong.</EmptyState>
        </Card>
      ) : q.isError && !q.data ? (
        <ErrorBanner message="Could not load the plan." onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : !q.data ? (
        <div className="flex flex-col gap-4">
          <Skeleton className="h-32" />
          <Skeleton className="h-64" />
        </div>
      ) : (
        <>
          {q.isError && (
            <ErrorBanner message="Could not refresh the plan. It tries again." onRetry={() => void q.refetch()} retrying={q.isFetching} />
          )}
          {/* Keyed by plan, so a delete confirmation, a picked version or a draft never carries over to another plan. */}
          <PlanBody key={q.data.plan_id} view={q.data} available={st.data?.available === true} />
        </>
      )}
    </>
  );
}

function PlanBody({ view, available }: { view: PlanView; available: boolean }) {
  const [picked, setPicked] = useState<number | null>(null);
  const versions = view.versions;
  const newest = versions[versions.length - 1];
  // A version picked earlier stays picked; otherwise the newest shows.
  const v = (picked !== null ? versions.find((x) => x.version === picked) : undefined) ?? newest;
  const running = view.status === "running";
  // Said once when a run this page watched ends with a plan (a failed run has its alert).
  const [ready, setReady] = useState(false);
  const was = useRef(view.status);
  useEffect(() => {
    if (was.current === "running" && view.status === "ready") setReady(true);
    else if (view.status === "running") setReady(false);
    was.current = view.status;
  }, [view.status]);
  return (
    <div className="flex min-w-0 flex-col gap-4 pb-8">
      <RequestCard view={view} />
      {/* One live region, kept mounted, so screen readers hear the step and the end. */}
      <div role="status">
        {running ? (
          <p className="m-0 rounded-lg bg-accent-tint px-4 py-3 text-sm text-fg">
            <span className="font-medium">{STEP_TEXT[view.step ?? "searching"]}…</span> This can take a few minutes. You can leave this page; the plan keeps going.
          </p>
        ) : (
          ready && <p className="sr-only">The plan is ready.</p>
        )}
      </div>
      {!running && view.notice && (
        <p role="alert" className="m-0 flex items-start gap-2 rounded-lg bg-tint-warn px-4 py-3 text-sm text-fg">
          <TriangleAlert size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-warn" />
          {view.notice}
        </p>
      )}
      {versions.length > 1 && v && (
        <VersionPicker
          versions={versions}
          value={v.version}
          onChange={(n) => setPicked(n === newest?.version ? null : n)}
        />
      )}
      {v ? (
        <Version v={v} />
      ) : (
        !running && (
          <Card>
            <EmptyState title="No plan yet.">{available ? "Try again below." : "Plans can be made again when AI is back on."}</EmptyState>
          </Card>
        )
      )}
      {/* No change box while the first plan is being made: there is nothing to change yet. */}
      {available && view.follow_ups_left > 0 && (versions.length > 0 || !running) && (
        <FollowUp view={view} hasVersion={versions.length > 0} onSent={() => setPicked(null)} />
      )}
      {available && view.follow_ups_left === 0 && (
        <p className="m-0 text-sm text-fg-secondary">This plan has had all its changes. Start a new plan for more.</p>
      )}
      <ManageCard view={view} />
    </div>
  );
}

// ---------- The request ----------

function RequestCard({ view }: { view: PlanView }) {
  const r = view.request;
  const interests = r.interests.map((i) => INTERESTS.find((x) => x.value === i)?.label ?? i).join(", ");
  const pace = PACE_OPTIONS.find((x) => x.value === r.pace)?.label ?? r.pace;
  const lean = LEAN_OPTIONS.find((x) => x.value === r.lean)?.label ?? r.lean;
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="m-0 text-base font-medium text-fg">{r.where}</p>
          <p className="m-0 text-sm text-fg-secondary">
            {tripDates(r)} · {nightsText(r.nights)} · {whoText(r)}
          </p>
        </div>
        <Badge tone={PLAN_STATUS_TONE[view.status]}>{PLAN_STATUS_TEXT[view.status]}</Badge>
      </div>
      <dl className="m-0 mt-3 grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
        {r.home_airport && <Meta label="From">{r.home_airport}</Meta>}
        <Meta label="Pace">{pace}</Meta>
        <Meta label="Pay with">{lean}</Meta>
        {interests && <Meta label="Interests">{interests}</Meta>}
        {r.note && <Meta label="Note">{r.note}</Meta>}
      </dl>
      <p className="m-0 mt-3 text-xs text-fg-secondary">
        Made {formatTimestamp(view.created_at)}
        {view.created_by_name && ` by ${view.created_by_name}`} with {modelName(view.model)}. AI cost so far {formatMoney(view.cost_cents, { cents: true })},{" "}
        {view.web_searches === 1 ? "1 web search" : `${view.web_searches} web searches`}.
      </p>
    </Card>
  );
}

function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 gap-2">
      <dt className="shrink-0 text-fg-secondary">{label}</dt>
      <dd className="m-0 min-w-0 break-words text-fg">{children}</dd>
    </div>
  );
}

function VersionPicker({ versions, value, onChange }: { versions: PlanVersion[]; value: number; onChange: (n: number) => void }) {
  const id = useId();
  const last = versions[versions.length - 1]?.version;
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <label htmlFor={id} className="text-fg-secondary">
        Version
      </label>
      <select
        id={id}
        // As wide as its longest option, but never wider than the page.
        className="h-11 min-w-0 max-w-full rounded-lg border border-line bg-card px-3 text-base text-fg md:h-9 md:text-sm"
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      >
        {[...versions].reverse().map((x) => (
          <option key={x.version} value={x.version}>
            {x.version === last ? `${x.version} (newest)` : x.version}
            {x.follow_up ? `: ${clip(x.follow_up, 40)}` : ": first plan"}
          </option>
        ))}
      </select>
    </div>
  );
}

function clip(text: string, n: number): string {
  return text.length > n ? `${text.slice(0, n - 1)}…` : text;
}

// ---------- One version ----------

function Version({ v }: { v: PlanVersion }) {
  const links = new Map(v.links.map((l) => [l.id, l]));
  const p = v.plan;
  const d = v.derived;
  return (
    <>
      <Card>
        <CardHeader
          title="Summary"
          subtitle={v.follow_up ? `Changed: ${v.follow_up}` : `Version ${v.version}, ${formatTimestamp(v.created_at)}.`}
        />
        <SafeText text={p.summary} className="text-sm text-fg" />
        <div className="mt-4 rounded-lg bg-subtle p-3">
          <p className="m-0 text-xs text-fg-secondary">Estimated cost</p>
          <p className="m-0 text-lg font-medium text-fg">{rangeText(d.costs.low_cents, d.costs.high_cents)}</p>
          {d.costs.by_kind.length > 0 && (
            <ul aria-label="Cost by kind" className="m-0 mt-2 grid list-none grid-cols-1 gap-x-6 gap-y-1 p-0 text-sm sm:grid-cols-2">
              {d.costs.by_kind.map((k) => {
                // Claude's note on a line says when a price was not checked.
                const notes = p.costs.filter((c) => c.kind === k.kind && c.note).map((c) => c.note);
                return (
                  <li key={k.kind} className="min-w-0">
                    <span className="flex justify-between gap-3">
                      <span className="text-fg-secondary">{COST_KIND_TEXT[k.kind as keyof typeof COST_KIND_TEXT] ?? k.kind}</span>
                      <span className="tabular text-fg">{rangeText(k.low_cents, k.high_cents)}</span>
                    </span>
                    {notes.length > 0 && <span className="block text-xs text-fg-secondary">{notes.join(" · ")}</span>}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </Card>
      <PointsCard v={v} links={links} />
      <FlightsCard v={v} links={links} />
      <LodgingCard v={v} links={links} />
      <GroundCard v={v} links={links} />
      <DaysCard v={v} links={links} />
      <ChecksCard v={v} />
      <LinksCard v={v} />
    </>
  );
}

type Links = Map<string, PlanLinkOut>;

const RANGE: Record<"below" | "within" | "above", { tone: BadgeTone; text: string }> = {
  below: { tone: "warn", text: "Below the usual value" },
  within: { tone: "neutral", text: "Usual value" },
  above: { tone: "good", text: "Above the usual value" },
};

function Row({ children }: { children: ReactNode }) {
  return <li className="flex flex-col gap-1 border-t border-line py-3 text-sm first:border-t-0 first:pt-0">{children}</li>;
}

function Line({ main, side }: { main: ReactNode; side?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
      <span className="min-w-0 font-medium text-fg">{main}</span>
      {side !== undefined && <span className="tabular text-fg">{side}</span>}
    </div>
  );
}

function Small({ children }: { children: ReactNode }) {
  return <p className="m-0 text-fg-secondary">{children}</p>;
}

function None({ children }: { children: ReactNode }) {
  return <p className="m-0 text-sm text-fg-secondary">{children}</p>;
}

function PointsCard({ v, links }: { v: PlanVersion; links: Links }) {
  const uses = v.plan.points_plan;
  const balances = v.derived.balances;
  return (
    <Card>
      <CardHeader title="Points plan" subtitle="Points plus taxes against the cash price. The app works out the value per point." />
      {uses.length === 0 ? (
        <None>No points or benefits in this plan.</None>
      ) : (
        // A table from the tablet size up (one row per use); rows stack on phones.
        <ul aria-label="Points plan" className="m-0 list-none p-0 text-sm">
          <li aria-hidden="true" className={cn(POINTS_GRID, "hidden pb-2 text-xs text-fg-secondary md:grid")}>
            <span>Item</span>
            <span>Program</span>
            <span className="text-right">Points</span>
            <span className="text-right">Taxes</span>
            <span className="text-right">Cash price</span>
            <span>Value per point</span>
          </li>
          {uses.map((u, i) => {
            const value = v.derived.points[i];
            const cpp = valueText(value?.value_cents_per_point ?? null);
            const notYours = value && !value.yours && u.points > 0;
            return (
              <li key={i} className={cn(POINTS_GRID, "grid gap-1 border-t border-line py-3 md:items-baseline")}>
                <span className="min-w-0">
                  <span className="block font-medium text-fg">{u.item}</span>
                  {u.note && <span className="block text-fg-secondary">{u.note}</span>}
                  <SourceLinks ids={u.source_ids} links={links} />
                </span>
                <span className="min-w-0 text-fg-secondary md:text-fg">
                  {[u.program || null, u.benefit ? `Uses ${u.benefit}` : null].filter(Boolean).join(" · ") || "Cash"}
                </span>
                <Cell label="Points">{u.points > 0 ? pointsCount(u.points) : "None"}</Cell>
                <Cell label="Taxes">{u.points > 0 ? usd(u.taxes_usd) : "None"}</Cell>
                <Cell label="Cash price">{usd(u.cash_price_usd)}</Cell>
                <span className="flex min-w-0 flex-wrap items-center gap-2">
                  {cpp && <span className="text-fg">{cpp}</span>}
                  {value?.range && <Badge tone={RANGE[value.range].tone}>{RANGE[value.range].text}</Badge>}
                  {notYours && <Badge tone="warn">Not in your rewards</Badge>}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      {balances.length > 0 && (
        <div className="mt-4 border-t border-line pt-3">
          <h3 className="m-0 mb-1 text-sm font-medium text-fg">Points needed</h3>
          <ul aria-label="Points needed" className="m-0 list-none p-0 text-sm">
            {balances.map((b) => (
              <li key={b.program} className="flex flex-wrap items-baseline justify-between gap-x-3 py-1">
                <span className="min-w-0 text-fg">{b.program}</span>
                <span className="flex flex-wrap items-center gap-2 tabular">
                  {neededText(b.needed, b.have)}
                  {b.enough === true && <Badge tone="good">Enough</Badge>}
                  {b.enough === false && <Badge tone="bad">Not enough</Badge>}
                  {b.enough === null && <Badge>Not compared</Badge>}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}

const POINTS_GRID = "md:grid md:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)_6rem_5rem_6rem_minmax(0,1.3fr)] md:gap-4";

/** A number cell: right-aligned in the table, "Points 60,000" when rows stack. */
function Cell({ label, children }: { label: string; children: ReactNode }) {
  return (
    <span className="tabular text-fg md:text-right">
      <span className="text-fg-secondary md:sr-only">{label} </span>
      {children}
    </span>
  );
}

function FlightsCard({ v, links }: { v: PlanVersion; links: Links }) {
  const flights = v.plan.flights;
  return (
    <Card>
      <CardHeader title="Flights" subtitle="Round trip for the whole party." />
      {flights.length === 0 ? (
        <None>No flights in this plan.</None>
      ) : (
        <ul aria-label="Flights" className="m-0 list-none p-0">
          {flights.map((f, i) => (
            <Row key={i}>
              <Line main={f.label} side={usdRange(f.cash_low_usd, f.cash_high_usd)} />
              <Small>{[f.route, f.when].filter(Boolean).join(" · ")}</Small>
              {f.program && f.points > 0 && (
                <Small>
                  Or {pointsCount(f.points)} points with {f.program}, plus {usd(f.taxes_usd)}
                </Small>
              )}
              {f.note && <Small>{f.note}</Small>}
              <SourceLinks ids={f.source_ids} links={links} />
            </Row>
          ))}
        </ul>
      )}
    </Card>
  );
}

function LodgingCard({ v, links }: { v: PlanVersion; links: Links }) {
  const hotels = v.plan.lodging;
  return (
    <Card>
      <CardHeader title="Where to stay" subtitle="Prices are a night, before taxes unless the source says otherwise." />
      {hotels.length === 0 ? (
        <None>No places to stay in this plan.</None>
      ) : (
        <ul aria-label="Where to stay" className="m-0 grid list-none gap-3 p-0 md:grid-cols-2 xl:grid-cols-3">
          {hotels.map((h, i) => (
            <li key={i} className="flex min-w-0 flex-col gap-1 rounded-lg border border-line p-3 text-sm">
              <p className="m-0 font-medium text-fg">{h.name}</p>
              {h.area && <Small>{h.area}</Small>}
              <p className="m-0 tabular text-fg">{usdRange(h.nightly_low_usd, h.nightly_high_usd)} a night</p>
              {h.program && h.points_per_night > 0 && (
                <Small>
                  Or {pointsCount(h.points_per_night)} points a night with {h.program}
                </Small>
              )}
              {h.free_night_ok && (
                <span>
                  <Badge tone="accent">Free night can be used</Badge>
                </span>
              )}
              {h.why && <Small>{h.why}</Small>}
              <SourceLinks ids={h.source_ids} links={links} />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function GroundCard({ v, links }: { v: PlanVersion; links: Links }) {
  const ground = v.plan.ground;
  return (
    <Card>
      <CardHeader title="Getting around" />
      {ground.length === 0 ? (
        <None>Nothing planned for getting around.</None>
      ) : (
        <ul aria-label="Getting around" className="m-0 list-none p-0">
          {ground.map((g, i) => (
            <Row key={i}>
              <Line main={GROUND_TEXT[g.mode]} side={usdRange(g.low_usd, g.high_usd)} />
              {g.note && <Small>{g.note}</Small>}
              <SourceLinks ids={g.source_ids} links={links} />
            </Row>
          ))}
        </ul>
      )}
    </Card>
  );
}

function DaysCard({ v, links }: { v: PlanVersion; links: Links }) {
  return (
    <Card>
      <CardHeader title="Day by day" />
      <NightBar nights={v.derived.nights} />
      {v.plan.days.length === 0 ? (
        <None>No days in this plan.</None>
      ) : (
        <ol aria-label="Days" className="m-0 mt-4 flex list-none flex-col gap-4 p-0">
          {v.plan.days.map((day) => (
            <DayRow key={day.day} day={day} links={links} />
          ))}
        </ol>
      )}
    </Card>
  );
}

/** One mark per night: a place to stay, or none (a check below says so too). */
function NightBar({ nights }: { nights: PlanVersion["derived"]["nights"] }) {
  if (nights.length === 0) return null;
  const empty = nights.filter((n) => !n.lodging).length;
  return (
    <div>
      <ul aria-label="Nights" className="m-0 flex list-none flex-wrap gap-1 p-0">
        {nights.map((n) => {
          const when = n.date ? `, ${formatShortDate(n.date)}` : "";
          return (
            <li
              key={n.night}
              title={`Night ${n.night}${when}: ${n.lodging ?? "no place to stay"}`}
              className={cn("h-3 min-w-3 flex-1 rounded-sm", n.lodging ? "bg-accent" : "border border-dashed border-warn bg-tint-warn")}
            >
              <span className="sr-only">
                Night {n.night}
                {when}: {n.lodging ?? "no place to stay"}
              </span>
            </li>
          );
        })}
      </ul>
      <p className="m-0 mt-1 text-xs text-fg-secondary">
        {nightsText(nights.length)}
        {empty > 0 ? `, ${empty} with no place to stay.` : ", each with a place to stay."}
      </p>
    </div>
  );
}

const PARTS = ["morning", "afternoon", "evening"] as const;

function DayRow({ day, links }: { day: PlanDay; links: Links }) {
  return (
    <li className="min-w-0 border-t border-line pt-3 first:border-t-0 first:pt-0">
      <h3 className="m-0 text-sm font-medium text-fg">
        Day {day.day}
        {day.date && ` · ${formatShortDate(day.date)}`}
        {day.base && <span className="font-normal text-fg-secondary"> · {day.base}</span>}
      </h3>
      {(day.travel || day.lodging) && (
        <p className="m-0 text-sm text-fg-secondary">
          {[day.travel ? `Travel: ${day.travel}` : null, day.lodging ? `Stay: ${day.lodging}` : null].filter(Boolean).join(" · ")}
        </p>
      )}
      <div className="mt-2 grid gap-3 md:grid-cols-3">
        {PARTS.map((part) => {
          const items = day.items.filter((i) => i.part === part);
          if (items.length === 0) return null;
          return (
            <div key={part} className="min-w-0">
              <p className="eyebrow m-0 mb-1">{PART_TEXT[part]}</p>
              <ul className="m-0 flex list-none flex-col gap-2 p-0 text-sm">
                {items.map((it, i) => (
                  <li key={i} className="min-w-0">
                    <p className="m-0 text-fg">
                      {it.title}
                      <span className="text-xs text-fg-secondary"> · {ITEM_KIND_TEXT[it.kind]}</span>
                      {it.high_usd > 0 && <span className="tabular text-fg-secondary"> · {usdRange(it.low_usd, it.high_usd)}</span>}
                    </p>
                    {it.note && <p className="m-0 text-xs text-fg-secondary">{it.note}</p>}
                    <SourceLinks ids={it.source_ids} links={links} />
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
    </li>
  );
}

function ChecksCard({ v }: { v: PlanVersion }) {
  const app = v.derived.checks;
  const claude = v.plan.checks;
  return (
    <Card>
      <CardHeader title="Check before you book" subtitle={CHECK_INTRO} />
      {app.length === 0 && claude.length === 0 ? (
        <None>Nothing flagged.</None>
      ) : (
        <ul aria-label="Checks" className="m-0 flex list-none flex-col gap-2 p-0 text-sm">
          {app.map((c, i) => (
            <li key={`a${i}`} className="flex items-start gap-2 text-fg">
              <TriangleAlert size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-warn" />
              {c.text}
            </li>
          ))}
          {claude.map((c, i) => (
            <li key={`c${i}`} className="flex items-start gap-2 text-fg">
              <CircleAlert size={16} aria-hidden="true" className="mt-0.5 shrink-0 text-fg-secondary" />
              {c}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/** Links come only from web search results; the plan names them by id. */
function LinksCard({ v }: { v: PlanVersion }) {
  const top = new Set(v.plan.link_ids);
  const book = v.links.filter((l) => top.has(l.id));
  const rest = v.links.filter((l) => !top.has(l.id));
  return (
    <Card>
      <CardHeader title="Links and sources" subtitle="From the web search. They open in a new tab. The app never books." />
      {v.links.length === 0 ? (
        <None>No links in this version.</None>
      ) : (
        <div className="flex flex-col gap-4">
          {book.length > 0 && <LinkList title="To check and book" links={book} />}
          {rest.length > 0 && <LinkList title="Other sources" links={rest} />}
        </div>
      )}
    </Card>
  );
}

function LinkList({ title, links }: { title: string; links: PlanLinkOut[] }) {
  return (
    <div>
      <h3 className="m-0 mb-1 text-sm font-medium text-fg">{title}</h3>
      <ul aria-label={title} className="m-0 list-none p-0 text-sm">
        {links.map((l) => (
          <li key={l.id} className="flex min-w-0 flex-col border-t border-line py-2 first:border-t-0 sm:flex-row sm:items-baseline sm:gap-3">
            <PlanLink link={l} className="shrink-0 font-medium" />
            <span className="min-w-0 break-words text-fg-secondary">{l.title}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------- Follow-up, link to a trip, delete ----------

function FollowUp({ view, hasVersion, onSent }: { view: PlanView; hasVersion: boolean; onSent: () => void }) {
  const qc = useQueryClient();
  const id = useId();
  const [text, setText] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const running = view.status === "running";
  const send = useMutation({
    mutationFn: (t: string) => followUpTripPlan(view.plan_id, t),
    onSuccess: () => {
      setText("");
      setProblem(null);
      onSent();
      // Running at once, so the page polls until the run ends even if the next fetch fails.
      qc.setQueryData<PlanView>(tripPlanKeys.one(view.plan_id), (old) =>
        old ? { ...old, status: "running", step: "searching", notice: null } : old,
      );
      void qc.invalidateQueries({ queryKey: tripPlanKeys.all });
    },
    onError: (err) => setProblem(err instanceof ApiError ? err.message : "Could not send the change. Try again."),
  });

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    const t = text.trim();
    if (!t) {
      setProblem("Type the change you want.");
      return;
    }
    send.mutate(t);
  }

  const title = hasVersion ? "Change the plan" : "Try again";
  return (
    <Card>
      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-2">
        <label htmlFor={id} className="text-base font-medium text-fg">
          {title}
        </label>
        <p className="m-0 text-sm text-fg-secondary">
          {hasVersion ? "Like a cheaper hotel or a beach day. Each change makes a new version; older ones stay." : "Say what to try, like other dates or a nearby city."}{" "}
          {view.follow_ups_left === 1 ? "1 change left." : `${view.follow_ups_left} changes left.`}
        </p>
        <textarea
          id={id}
          className="min-h-20 w-full min-w-0 rounded-lg border border-line bg-card px-3 py-2 text-base text-fg placeholder:text-fg-faint"
          maxLength={FOLLOW_UP_MAX}
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={running}
          aria-describedby={problem ? `${id}-problem` : undefined}
        />
        <div aria-live="polite">
          {problem && (
            <p id={`${id}-problem`} className="m-0 flex items-start gap-1.5 text-sm text-bad">
              <CircleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
              {problem}
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" variant="primary" disabled={running || send.isPending}>
            {send.isPending ? "Sending…" : running ? "Wait for this run" : "Send the change"}
          </Button>
          <span className="text-xs text-fg-secondary">Counts toward the AI limit.</span>
        </div>
      </form>
    </Card>
  );
}

function ManageCard({ view }: { view: PlanView }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const id = useId();
  const trips = useQuery({ queryKey: tripKeys.page, queryFn: getTrips });
  const options = trips.data ? [...trips.data.active, ...trips.data.completed, ...trips.data.older] : [];
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [confirming, setConfirming] = useState(false);
  const running = view.status === "running";
  const fail = (err: unknown) => setNote({ ok: false, text: err instanceof ApiError ? err.message : "That did not work. Try again." });
  const link = useMutation({
    mutationFn: (key: string | null) => linkTripPlan(view.plan_id, key),
    onSuccess: (updated) => {
      qc.setQueryData(tripPlanKeys.one(view.plan_id), updated);
      void qc.invalidateQueries({ queryKey: tripPlanKeys.list });
      setNote({ ok: true, text: updated.trip_name ? `Linked to ${updated.trip_name}. Its card on Travel shows the plan's estimate.` : "Unlinked." });
    },
    onError: fail,
  });
  const remove = useMutation({
    mutationFn: () => deleteTripPlan(view.plan_id),
    onSuccess: () => {
      qc.removeQueries({ queryKey: tripPlanKeys.one(view.plan_id) });
      void qc.invalidateQueries({ queryKey: tripPlanKeys.list });
      navigate("/travel");
    },
    onError: (err) => {
      setConfirming(false);
      fail(err);
    },
  });

  return (
    <Card>
      <CardHeader title="This plan" />
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <label htmlFor={id} className="text-sm font-medium text-fg">
            Linked trip
          </label>
          <p className="m-0 text-xs text-fg-secondary">When the chat sends the real trip, link it here. Its card shows "plan est." next to the actual cost.</p>
          <select
            id={id}
            className="h-11 w-full max-w-sm rounded-lg border border-line bg-card px-3 text-base text-fg md:h-9 md:text-sm"
            value={view.trip_key ?? ""}
            // Not disabled while it saves, so keyboard focus stays on it.
            disabled={!trips.data}
            aria-busy={link.isPending || undefined}
            onChange={(e) => {
              if (!link.isPending) link.mutate(e.target.value || null);
            }}
          >
            <option value="">Not linked</option>
            {view.trip_key && !options.some((t) => t.key === view.trip_key) && (
              <option value={view.trip_key}>{view.trip_name ?? view.trip_key}</option>
            )}
            {options.map((t) => (
              <option key={t.key} value={t.key}>
                {t.name} ({tripDates(t)})
              </option>
            ))}
          </select>
          {trips.isError && <p className="m-0 text-xs text-fg-secondary">Could not load the trips.</p>}
        </div>
        <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4">
          {confirming ? (
            <>
              <span className="text-sm text-fg">Delete this plan and every version for good?</span>
              <Button variant="primary" onClick={() => remove.mutate()} disabled={remove.isPending}>
                {remove.isPending ? "Deleting…" : "Delete"}
              </Button>
              <Button variant="ghost" onClick={() => setConfirming(false)}>
                Keep it
              </Button>
            </>
          ) : (
            <>
              <Button onClick={() => setConfirming(true)} disabled={running}>
                Delete plan
              </Button>
              {running && <span className="text-sm text-fg-secondary">You can delete it when the plan is done.</span>}
            </>
          )}
        </div>
        <div aria-live="polite">{note && <p className={cn("m-0 text-sm", note.ok ? "text-good" : "text-bad")}>{note.text}</p>}</div>
      </div>
    </Card>
  );
}
