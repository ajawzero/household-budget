import { ChevronRight } from "lucide-react";
import { type ReactNode, useId, useState } from "react";
import { Cell, Pie, PieChart, Tooltip } from "recharts";
import { ChartCard, type ChartColumn } from "../../components/charts/ChartCard";
import { CHART_ANIMATION_MS, ChartTooltipBox } from "../../components/charts/ChartTooltip";
import { Money } from "../../components/money/Money";
import { SegmentedControl, type SegmentOption } from "../../components/ui/segmented-control";
import { formatMoney, formatMonth, formatPct } from "../../lib/format";
import { useReducedMotion } from "../../lib/media";
import { useResolvedTheme } from "../../lib/theme";
import type { BreakdownBy, BreakdownItem, BreakdownOut } from "./api";
import { LEFTOVER, SPENDING } from "./CashflowChart";
import { OTHER_KEY, ringSlices, type Slice } from "./colors";

export type BreakdownPeriod = "month" | "range";

export const PERIOD_OPTIONS: SegmentOption<BreakdownPeriod>[] = [
  { value: "month", label: "Latest month", ariaLabel: "Latest complete month" },
  { value: "range", label: "Whole range" },
];

const BY_OPTIONS: SegmentOption<BreakdownBy>[] = [
  { value: "category", label: "By category" },
  { value: "group", label: "By group" },
  { value: "merchant", label: "By merchant" },
];

const BY_NOUN: Record<BreakdownBy, [one: string, many: string]> = {
  category: ["category", "categories"],
  group: ["group", "groups"],
  merchant: ["merchant", "merchants"],
};

/** "Aug 2026" or "Jan 2026 to Aug 2026". */
export function periodText(b: Pick<BreakdownOut, "from" | "to"> | undefined): string {
  if (!b) return "";
  return b.from === b.to ? formatMonth(b.from) : `${formatMonth(b.from)} to ${formatMonth(b.to)}`;
}

function partialNote(b: BreakdownOut | undefined): ReactNode {
  if (!b || b.partial_months.length === 0) return null;
  const months = b.partial_months.map(formatMonth).join(", ");
  return <p className="m-0 mt-3 text-sm text-warn">Includes partial months: {months}. Statements are not all in.</p>;
}

interface LoadProps {
  loading: boolean;
  refreshing: boolean;
  error: string | null;
}

// ---------- Ring by group ----------

export const SLICE_COLUMNS: ChartColumn<Slice>[] = [
  { header: "Group", cell: (s) => (s.members.length ? `${s.label} (${s.members.join(", ")})` : s.label) },
  { header: "Spent", cell: (s) => formatMoney(s.cents), numeric: true },
  { header: "Share", cell: (s) => formatPct(s.share_pct, true), numeric: true },
];

interface RingCardProps extends LoadProps {
  data: BreakdownOut | undefined;
  period: BreakdownPeriod;
  onPeriodChange: (p: BreakdownPeriod) => void;
}

/** Spending by group as a ring with the total in the middle and a three-column legend, like Monarch's spending card. */
export function SpendingRingCard({ data, period, onPeriodChange, loading, refreshing, error }: RingCardProps) {
  const slices = data ? ringSlices(data.items, data.total_cents) : undefined;
  const aria = data
    ? `Spending by group, ${periodText(data)}. Total ${formatMoney(data.total_cents)}. ${(slices ?? [])
        .map((s) => `${s.label} ${formatMoney(s.cents)}`)
        .join(", ")}.`
    : "Spending by group";
  return (
    <ChartCard<Slice>
      eyebrow="Spending by group"
      title={periodText(data) || undefined}
      controls={<SegmentedControl label="Period" value={period} options={PERIOD_OPTIONS} onChange={onPeriodChange} variant="pill" />}
      ariaLabel={aria}
      data={slices}
      columns={SLICE_COLUMNS}
      rowKey={(s) => s.key}
      tableCaption={`Spending by group, ${periodText(data)}`}
      loading={loading}
      refreshing={refreshing}
      error={error}
      quietError
      emptyText="No spending in this period."
      height={200}
      footer={
        <>
          {data && data.saved_invested_cents !== 0 && (
            <p className="m-0 mt-4 flex items-center gap-1.5 text-sm text-fg-secondary">
              <span aria-hidden="true" className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: LEFTOVER }} />
              Saved and invested, {formatMoney(data.saved_invested_cents)}, is not in the ring. It is money kept.
            </p>
          )}
          {partialNote(data)}
        </>
      }
    >
      {slices && data && (
        <div className="flex flex-col items-center gap-6 md:flex-row md:items-center md:gap-10">
          <Ring slices={slices} totalCents={data.total_cents} />
          <ul className="m-0 grid w-full min-w-0 flex-1 list-none grid-cols-2 gap-x-6 gap-y-4 p-0 xl:grid-cols-3">
            {slices.map((s) => (
              <li key={s.key} className="flex min-w-0 items-start gap-2">
                <Swatch slice={s} />
                <span className="min-w-0">
                  <span className="block text-sm text-fg" title={s.members.length ? s.members.join(", ") : undefined}>
                    {s.label}
                  </span>
                  <span className="block text-sm text-fg-secondary">
                    <Money cents={s.cents} className="text-fg" /> ({formatPct(s.share_pct, true)})
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </ChartCard>
  );
}

/** Texture for "Everything else": gray lines on the subtle surface, so it never reads as a fifth color. */
function OtherPattern({ id }: { id: string }) {
  return (
    <pattern id={id} patternUnits="userSpaceOnUse" width={6} height={6} patternTransform="rotate(45)">
      <rect width={6} height={6} fill="var(--bg-subtle)" />
      <line x1={0} y1={0} x2={0} y2={6} stroke="var(--chart-muted)" strokeWidth={2} />
    </pattern>
  );
}

function Swatch({ slice }: { slice: Slice }) {
  const id = useId().replace(/:/g, "");
  if (slice.color) {
    return <span aria-hidden="true" className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: slice.color }} />;
  }
  return (
    <svg aria-hidden="true" width={10} height={10} className="mt-1.5 shrink-0">
      <defs>
        <OtherPattern id={id} />
      </defs>
      <circle cx={5} cy={5} r={4.5} fill={`url(#${id})`} stroke="var(--border-strong)" strokeWidth={1} />
    </svg>
  );
}

const RING = 200;

function Ring({ slices, totalCents }: { slices: Slice[]; totalCents: number }) {
  const id = useId().replace(/:/g, "");
  const reduced = useReducedMotion();
  const theme = useResolvedTheme();
  return (
    // A clicked slice takes focus (tabindex -1, never reached by Tab); no box around it.
    <div className="relative shrink-0 [&_.recharts-sector:focus]:outline-none" style={{ width: RING, height: RING }}>
      <PieChart key={theme} width={RING} height={RING} accessibilityLayer={false}>
        <defs>
          <OtherPattern id={`${id}-other`} />
        </defs>
        <Pie
          data={slices}
          dataKey="cents"
          nameKey="label"
          innerRadius={66}
          outerRadius={96}
          startAngle={90}
          endAngle={-270}
          stroke="var(--bg-card)"
          strokeWidth={2}
          isAnimationActive={!reduced}
          animationDuration={CHART_ANIMATION_MS}
        >
          {slices.map((s) => (
            <Cell key={s.key} fill={s.color ?? `url(#${id}-other)`} />
          ))}
        </Pie>
        <Tooltip
          // Above the Total in the hole, which comes later in the page.
          wrapperStyle={{ zIndex: 10 }}
          content={({ active, payload }) => {
            const s = active ? (payload?.[0]?.payload as Slice | undefined) : undefined;
            if (!s) return null;
            return (
              <ChartTooltipBox
                title={s.label}
                rows={[{ key: "v", label: formatPct(s.share_pct, true), value: formatMoney(s.cents), color: s.color ?? undefined }]}
                note={s.key === OTHER_KEY && s.members.length ? s.members.join(", ") : undefined}
              />
            );
          }}
        />
      </PieChart>
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
        <Money cents={totalCents} proportional className="text-xl font-semibold text-fg" />
        <span className="text-sm text-fg-secondary">Total</span>
      </div>
    </div>
  );
}

// ---------- Ranked bars ----------

const LIMIT = 8;

interface RankedCardProps extends LoadProps {
  data: BreakdownOut | undefined;
  by: BreakdownBy;
  onByChange: (by: BreakdownBy) => void;
  /** Opens the category drawer. */
  onCategory: (key: string) => void;
}

/** Ranked bars for one breakdown. Category rows open the category drawer. */
export function RankedCard({ data, by, onByChange, onCategory, loading, refreshing, error }: RankedCardProps) {
  const [showAll, setShowAll] = useState(false);
  const items = data?.items;
  const columns: ChartColumn<BreakdownItem>[] = [
    { header: BY_NOUN[by][0].replace(/^./, (c) => c.toUpperCase()), cell: (i) => i.label },
    ...(by === "category" ? [{ header: "Group", cell: (i: BreakdownItem) => i.group_label ?? "" }] : []),
    { header: "Spent", cell: (i) => formatMoney(i.cents), numeric: true },
    { header: "Share", cell: (i) => formatPct(i.share_pct, true), numeric: true },
    { header: "Rows", cell: (i) => String(i.count), numeric: true },
  ];
  const shown = items ? (showAll ? items : items.slice(0, LIMIT)) : [];
  const max = Math.max(1, ...(items ?? []).map((i) => i.cents), data?.saved_invested_cents ?? 0);
  const clickable = by === "category";
  const aria = data
    ? `Spending ${BY_NOUN[by][1]}, ${periodText(data)}, largest first. ${(items ?? [])
        .slice(0, 3)
        .map((i) => `${i.label} ${formatMoney(i.cents)}`)
        .join(", ")}.`
    : "Spending breakdown";

  return (
    <ChartCard<BreakdownItem>
      eyebrow="Where it went"
      title={periodText(data) || undefined}
      controls={<SegmentedControl label="Break down" value={by} options={BY_OPTIONS} onChange={onByChange} variant="pill" />}
      ariaLabel={aria}
      data={items}
      columns={columns}
      rowKey={(i) => i.key}
      tableCaption={`Spending by ${BY_NOUN[by][0]}, ${periodText(data)}`}
      loading={loading}
      refreshing={refreshing}
      error={error}
      quietError
      emptyText="No spending in this period."
      height={280}
      footer={
        <>
          {items && items.length > LIMIT && (
            <button
              type="button"
              className="mt-3 inline-flex h-11 items-center rounded-lg px-1 text-sm font-medium text-accent-text hover:underline md:h-8"
              aria-expanded={showAll}
              onClick={() => setShowAll((v) => !v)}
            >
              {showAll ? `Show the top ${LIMIT}` : `Show all ${items.length} ${BY_NOUN[by][1]}`}
            </button>
          )}
          {data && data.saved_invested_cents !== 0 && (
            <div className="mt-4 border-t border-line pt-4">
              <BarRow label="Saved and invested" cents={data.saved_invested_cents} max={max} color={LEFTOVER} />
              <p className="m-0 mt-2 text-sm text-fg-secondary">Not spending. Shown for scale.</p>
            </div>
          )}
          {data?.items.some((i) => i.payment_basis_cents > 0) && (
            <p className="m-0 mt-3 text-sm text-fg-secondary">“Payment basis” marks card payments counted as spending in the month they were paid.</p>
          )}
          {partialNote(data)}
        </>
      }
    >
      <ul className="m-0 flex list-none flex-col gap-1 p-0">
        {shown.map((i) => (
          <li key={i.key}>
            {clickable ? (
              <button
                type="button"
                onClick={() => onCategory(i.key)}
                className="group -mx-2 block w-[calc(100%+16px)] rounded-lg px-2 py-1.5 text-left hover:bg-hover"
                aria-label={`${i.label}, ${formatMoney(i.cents)}. Open details`}
              >
                <BarRow
                  label={i.label}
                  emoji={i.emoji}
                  cents={i.cents}
                  max={max}
                  share={i.share_pct}
                  basis={i.payment_basis_cents > 0}
                  chevron
                />
              </button>
            ) : (
              <div className="py-1.5">
                <BarRow label={i.label} cents={i.cents} max={max} share={i.share_pct} basis={i.payment_basis_cents > 0} />
              </div>
            )}
          </li>
        ))}
      </ul>
    </ChartCard>
  );
}

interface BarRowProps {
  label: string;
  emoji?: string | null;
  cents: number;
  max: number;
  share?: string | null;
  color?: string;
  basis?: boolean;
  chevron?: boolean;
}

/** Label and value on one line, a thin bar under them. 4px rounded data end, square at zero. */
function BarRow({ label, emoji, cents, max, share, color = SPENDING, basis = false, chevron = false }: BarRowProps) {
  const pct = Math.max(0, cents) / max;
  return (
    <span className="block">
      <span className="flex items-baseline justify-between gap-3 text-sm">
        <span className="flex min-w-0 items-baseline gap-1.5 text-fg">
          {emoji !== undefined && (
            <span aria-hidden="true" className="inline-block w-5 shrink-0 text-center">
              {emoji ?? ""}
            </span>
          )}
          <span className="truncate">{label}</span>
          {basis && <span className="shrink-0 text-xs text-fg-secondary">payment basis</span>}
        </span>
        <span className="flex shrink-0 items-baseline gap-2">
          <Money cents={cents} className="text-fg" />
          {share !== undefined && <span className="w-12 text-right text-fg-secondary tabular">{formatPct(share, true)}</span>}
          {chevron && <ChevronRight size={16} aria-hidden="true" className="self-center text-fg-secondary" />}
        </span>
      </span>
      <span aria-hidden="true" className="mt-1 block h-2" style={{ marginRight: chevron ? 24 : 0 }}>
        <span
          className="block h-2 rounded-r"
          style={{ width: `${Math.max(pct * 100, cents > 0 ? 0.5 : 0)}%`, background: color }}
        />
      </span>
    </span>
  );
}
