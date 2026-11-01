import { CircleAlert, CircleCheck, CircleMinus } from "lucide-react";
import type { BudgetStatus } from "../../lib/api";
import { formatMoney, formatMoneyGap } from "../../lib/format";
import { cn } from "../../lib/cn";

interface MeterProps {
  /** Cents spent. Null when the statements are not in yet. */
  actual: number | null;
  /** Cents. */
  target: number;
  status: BudgetStatus;
  /** Screen reader name, like "Groceries". */
  label: string;
}

const FILL: Record<BudgetStatus, string> = {
  over: "var(--fill-bad)",
  on_target: "var(--fill-good)",
  under: "var(--fill-good)",
  no_actual: "var(--chart-muted)",
};

/**
 * Budget meter: the fill is what was spent, the dark tick is the target.
 * Past the tick is over budget. The status line says it in words too.
 */
export function Meter({ actual, target, status, label }: MeterProps) {
  const max = Math.max(actual ?? 0, target) * 1.15 || 1;
  const fillPct = actual === null ? 0 : (Math.max(actual, 0) / max) * 100;
  const tickPct = (target / max) * 100;
  const fill = FILL[status];
  const text = statusText(actual, target, status);

  return (
    <div>
      <div
        className="relative h-2 rounded-full"
        style={{ background: `color-mix(in oklab, ${fill} 18%, var(--bg-card))` }}
        role="img"
        aria-label={`${label}: ${actual === null ? "no actual yet" : formatMoney(actual)} of ${formatMoney(target)} target. ${text}`}
      >
        {/* The fill and the tick ease to new values when the data changes. */}
        <span className="absolute inset-y-0 left-0 rounded-full transition-[width] duration-(--dur-slow) ease-(--ease-out)" style={{ width: `${fillPct}%`, background: fill }} />
        <span className="absolute -top-1 h-4 w-0.5 rounded-full bg-chart-target transition-[left] duration-(--dur-slow) ease-(--ease-out)" style={{ left: `calc(${tickPct}% - 1px)` }} />
      </div>
      <p className={cn("m-0 mt-1.5 flex items-center gap-1 text-sm", status === "over" ? "text-bad" : status === "no_actual" ? "text-fg-secondary" : "text-good")}>
        <StatusIcon status={status} />
        {text}
      </p>
    </div>
  );
}

function StatusIcon({ status }: { status: BudgetStatus }) {
  const props = { size: 14, "aria-hidden": true } as const;
  if (status === "over") return <CircleAlert {...props} />;
  if (status === "no_actual") return <CircleMinus {...props} />;
  return <CircleCheck {...props} />;
}

export function statusText(actual: number | null, target: number, status: BudgetStatus): string {
  if (status === "no_actual" || actual === null) return "No actual yet";
  if (status === "over") return `Over by ${formatMoneyGap(actual, target)}`;
  if (status === "under") return `Under by ${formatMoneyGap(target, actual)}`;
  return "On target";
}
