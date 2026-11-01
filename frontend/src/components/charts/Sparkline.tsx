import { formatMoney } from "../../lib/format";

export interface SparkPoint {
  date: string;
  /** Cents. */
  balance_cents: number;
}

interface SparklineProps {
  points: SparkPoint[];
  width?: number;
  height?: number;
  /** Screen reader text. Defaults to "Balance from X to Y". */
  label?: string;
}

const PAD = 2;

/**
 * Tiny balance trend for account rows. Plain SVG, gray line, dashed line at
 * the first value. Hidden under 3 points, since 2 points say nothing.
 */
export function Sparkline({ points, width = 120, height = 32, label }: SparklineProps) {
  if (points.length < 3) return null;
  const times = points.map((p) => Date.parse(p.date));
  const values = points.map((p) => p.balance_cents);
  const t0 = Math.min(...times);
  const t1 = Math.max(...times);
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const x = (t: number) => PAD + (t1 === t0 ? 0 : ((t - t0) / (t1 - t0)) * (width - PAD * 2));
  const y = (v: number) => (hi === lo ? height / 2 : PAD + (1 - (v - lo) / (hi - lo)) * (height - PAD * 2));
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(times[i]!).toFixed(1)},${y(p.balance_cents).toFixed(1)}`).join(" ");
  const startY = y(values[0]!).toFixed(1);
  const text = label ?? `Balance from ${formatMoney(values[0]!)} to ${formatMoney(values[values.length - 1]!)}`;

  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={text} className="shrink-0">
      <line x1={0} x2={width} y1={startY} y2={startY} stroke="var(--chart-axis)" strokeWidth={1} strokeDasharray="2 3" />
      <path d={d} fill="none" stroke="var(--chart-muted)" strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
