import { type CSSProperties, useId } from "react";
import {
  Area,
  AreaChart as RAreaChart,
  CartesianGrid,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatCompact, formatDate, formatMoney, formatMonthShort, formatShortDate } from "../../lib/format";
import { useReducedMotion } from "../../lib/media";
import { useResolvedTheme } from "../../lib/theme";
import { AXIS_TICK, CHART_ANIMATION_MS, ChartTooltipBox } from "./ChartTooltip";
import { crossesZero, niceScale } from "./scale";

export interface AreaPoint {
  date: string;
  /** Cents. */
  value: number;
  /** Some included account was missing or stale on this day. */
  partial?: boolean;
  /** The value is (or includes) a computed estimate. */
  estimated?: boolean;
  /** A real value (statement, valuation or stored balance) lands on this day. */
  real?: boolean;
}

interface AreaChartProps {
  points: AreaPoint[];
  /** Series name for the tooltip, like "Net worth". */
  label: string;
  height?: number;
  /** A token var. Teal accent for net worth. */
  color?: string;
  /** Phones hide the y axis and use the dashed start line instead. */
  showYAxis?: boolean;
  /** Dashed line at the first value. */
  startLine?: boolean;
  /** Fixed width (tests). Leave out to fill the card. */
  width?: number;
  /** Hatch runs of partial days behind the line. Off by default. */
  markPartial?: boolean;
  /** X ticks on month starts ("Feb", "Mar"), for ranges of a few months or more. */
  monthTicks?: boolean;
  /** Most x ticks to show with monthTicks. */
  maxTicks?: number;
  /** Y axis width in px. Phones use a narrower one. */
  yAxisWidth?: number;
  /** Tint estimated days: every run, or only the run that reaches today. */
  estimateBands?: "none" | "all" | "tail";
  /** A dot on each day with a real value. */
  realDots?: boolean;
}

/** The estimate band as a CSS background, for the legend swatch (the Investments chart's wash). */
export const ESTIMATE_SWATCH_STYLE: CSSProperties = {
  background: "color-mix(in srgb, var(--fill-warn) 12%, transparent)",
};

/** First and last date of each run of estimated points; "tail" keeps only the run that ends the range. */
export function estimateRuns(points: AreaPoint[], mode: "none" | "all" | "tail"): { from: string; to: string }[] {
  if (mode === "none") return [];
  const runs: { from: string; to: string }[] = [];
  points.forEach((p, i) => {
    if (!p.estimated) return;
    const last = runs[runs.length - 1];
    if (last && points[i - 1]?.estimated) last.to = p.date;
    else runs.push({ from: p.date, to: p.date });
  });
  if (mode === "all") return runs;
  const end = points[points.length - 1];
  const tail = runs[runs.length - 1];
  return tail && end && tail.to === end.date ? [tail] : [];
}

/**
 * The partial-day hatch as a CSS background, for the legend swatch. The same
 * look as the chart's SVG pattern: muted lines on the subtle fill.
 */
export const PARTIAL_SWATCH_STYLE: CSSProperties = {
  backgroundColor: "var(--bg-subtle)",
  backgroundImage:
    "repeating-linear-gradient(135deg, var(--chart-muted) 0 1px, transparent 1px 5px)",
};

/**
 * The first day of each month in the points, skipping the first point, thinned
 * to at most `max` ticks by taking every nth month.
 */
export function monthStartTicks(dates: string[], max: number): string[] {
  const starts = dates.filter((d, i) => i > 0 && d.slice(0, 7) !== dates[i - 1]!.slice(0, 7));
  const step = Math.max(1, Math.ceil(starts.length / Math.max(1, max)));
  return starts.filter((_, i) => i % step === 0);
}

/** First and last date of each run of partial points. */
export function partialRuns(points: AreaPoint[]): { from: string; to: string }[] {
  const runs: { from: string; to: string }[] = [];
  points.forEach((p, i) => {
    if (!p.partial) return;
    const last = runs[runs.length - 1];
    if (last && points[i - 1]?.partial) last.to = p.date;
    else runs.push({ from: p.date, to: p.date });
  });
  return runs;
}

/** Single-series area chart with a soft gradient. Net worth and balances. */
export function AreaChart({
  points,
  label,
  height = 240,
  color = "var(--accent)",
  showYAxis = true,
  startLine = false,
  width,
  markPartial = false,
  monthTicks = false,
  maxTicks = 8,
  yAxisWidth = 56,
  estimateBands = "none",
  realDots = false,
}: AreaChartProps) {
  const gradientId = useId().replace(/:/g, "");
  const hatchId = `${gradientId}-hatch`;
  const reduced = useReducedMotion();
  // Colors are CSS vars, so they follow the theme. Reading the theme here
  // re-renders the chart when it flips, for anything Recharts caches.
  const theme = useResolvedTheme();
  const { domain, ticks } = niceScale(points.map((p) => p.value));
  const first = points[0]?.value;
  const dates = points.map((p) => p.date);
  const xTicks = monthTicks ? monthStartTicks(dates, maxTicks) : undefined;
  const multiYear = !!dates.length && dates[0]!.slice(0, 4) !== dates[dates.length - 1]!.slice(0, 4);
  const monthTick = (d: string) => (multiYear ? `${formatMonthShort(d)} ${d.slice(0, 4)}` : formatMonthShort(d));

  const chart = (
    <RAreaChart
      key={theme}
      // ChartCard's role="img" summary and table are the accessible path; no extra tab stop.
      accessibilityLayer={false}
      data={points}
      width={width}
      height={width ? height : undefined}
      margin={{ top: 8, right: 8, bottom: 0, left: showYAxis ? 0 : 8 }}
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} style={{ stopOpacity: "var(--area-fill-top)" }} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
        {/* Partial days: muted diagonal lines on the subtle fill, so the band shows on the card. */}
        <pattern id={hatchId} width={5} height={5} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect width={5} height={5} fill="var(--bg-subtle)" />
          <line x1={0} y1={0} x2={0} y2={5} stroke="var(--chart-muted)" strokeWidth={1} />
        </pattern>
      </defs>
      {markPartial &&
        partialRuns(points).map((r) => (
          <ReferenceArea
            key={r.from}
            x1={r.from}
            x2={r.to}
            fill={`url(#${hatchId})`}
            fillOpacity={1}
            stroke="none"
            ifOverflow="hidden"
            zIndex={-150}
          />
        ))}
      {estimateRuns(points, estimateBands).map((r) => (
        <ReferenceArea
          key={`est-${r.from}`}
          x1={r.from}
          x2={r.to}
          fill="var(--fill-warn)"
          fillOpacity={0.12}
          stroke="none"
          ifOverflow="hidden"
          zIndex={-140}
        />
      ))}
      <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
      <XAxis
        dataKey="date"
        tickFormatter={monthTicks ? monthTick : formatShortDate}
        ticks={xTicks}
        interval={monthTicks ? 0 : "preserveEnd"}
        tick={AXIS_TICK}
        axisLine={false}
        tickLine={false}
        minTickGap={32}
        tickMargin={8}
      />
      <YAxis
        hide={!showYAxis}
        domain={domain}
        tickFormatter={formatCompact}
        tick={AXIS_TICK}
        axisLine={false}
        tickLine={false}
        width={yAxisWidth}
        ticks={ticks}
        interval={0}
      />
      {/* A slightly stronger zero line when the values cross zero. */}
      {crossesZero(domain) && <ReferenceLine y={0} stroke="var(--chart-axis)" />}
      {startLine && first !== undefined && (
        <ReferenceLine y={first} stroke="var(--chart-axis)" strokeDasharray="3 3" ifOverflow="extendDomain" />
      )}
      <Tooltip
        cursor={{ stroke: "var(--border-strong)", strokeWidth: 1 }}
        content={({ active, payload }) => {
          const p = active ? (payload?.[0]?.payload as AreaPoint | undefined) : undefined;
          if (!p) return null;
          return (
            <ChartTooltipBox
              title={formatDate(p.date)}
              rows={[{ key: "v", label, value: formatMoney(p.value), color }]}
              note={
                p.partial
                  ? "Partial: some accounts have no balance yet"
                  : p.estimated && estimateBands !== "none"
                    ? p.real
                      ? "Real value plus an estimated part"
                      : "Estimate"
                    : p.real && realDots
                      ? "Real value"
                      : undefined
              }
            />
          );
        }}
      />
      <Area
        type="linear"
        dataKey="value"
        stroke={color}
        strokeWidth={2}
        fill={`url(#${gradientId})`}
        isAnimationActive={!reduced}
        animationDuration={CHART_ANIMATION_MS}
        dot={
          realDots
            ? (props: { cx?: number; cy?: number; index?: number; payload?: AreaPoint }) => {
                const key = `dot-${props.index ?? 0}`;
                if (!props.payload?.real || props.cx === undefined || props.cy === undefined) return <g key={key} />;
                return <circle key={key} cx={props.cx} cy={props.cy} r={4} fill={color} stroke="var(--bg-card)" strokeWidth={2} />;
              }
            : false
        }
        activeDot={{ r: 4, stroke: "var(--bg-card)", strokeWidth: 2, fill: color }}
      />
    </RAreaChart>
  );

  if (width) return chart;
  return (
    <ResponsiveContainer width="100%" height={height}>
      {chart}
    </ResponsiveContainer>
  );
}
