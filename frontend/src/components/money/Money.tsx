import { formatCompact, formatMoney } from "../../lib/format";
import { cn } from "../../lib/cn";

interface MoneyProps {
  cents: number | null | undefined;
  /** Show cents (transactions, plan rows). Summaries and tiles leave it off. */
  withCents?: boolean;
  /** "$1.2M" style, for axes and tight spots. */
  compact?: boolean;
  /**
   * A money movement (transactions). Received shows a green "+", spent keeps
   * the minus sign. Screen readers hear "received" or "spent".
   */
  flow?: boolean;
  /** Text for a missing value. Never a silent zero. */
  empty?: string;
  /** Proportional digits for big standalone numbers (hero values). */
  proportional?: boolean;
  className?: string;
}

export function Money({
  cents,
  withCents = false,
  compact = false,
  flow = false,
  empty = "No balance",
  proportional = false,
  className,
}: MoneyProps) {
  if (cents === null || cents === undefined || !Number.isFinite(cents)) {
    return <span className={cn("text-fg-secondary", className)}>{empty}</span>;
  }
  const text = compact ? formatCompact(cents) : formatMoney(cents, { cents: withCents });
  const received = flow && cents > 0;
  const spent = flow && cents < 0;
  return (
    <span
      className={cn(
        "whitespace-nowrap",
        !proportional && "tabular",
        received && "text-good",
        className,
      )}
    >
      {received && "+"}
      {text}
      {received && <span className="sr-only"> received</span>}
      {spent && <span className="sr-only"> spent</span>}
    </span>
  );
}
