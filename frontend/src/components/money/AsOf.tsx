import { ClockAlert } from "lucide-react";
import { formatDate, formatShortDate } from "../../lib/format";
import { cn } from "../../lib/cn";
import { Tooltip } from "../ui/tooltip";

interface AsOfProps {
  date: string | null | undefined;
  /** Past the expected next statement for the account's cadence. */
  stale?: boolean;
  className?: string;
  /** The words before the date. */
  label?: string;
  /** Hover text once stale. Default: a newer statement is overdue. */
  staleText?: string;
  /** Also shows the stale text in words, for a screen with no other note (phones have no hover). */
  showStaleText?: boolean;
}

/** "as of Sep 30" under a statement-based balance. Warning color once stale. */
export function AsOf({
  date,
  stale = false,
  className,
  label = "as of",
  staleText = "A newer statement is overdue.",
  showStaleText = false,
}: AsOfProps) {
  if (!date) return null;
  const full = formatDate(date);
  const tip = stale ? `Balance ${label} ${full}. ${staleText}` : `Balance ${label} ${full}.`;
  return (
    <Tooltip content={tip}>
      <span className={cn("inline-flex flex-wrap items-center gap-x-1 text-sm", stale ? "text-warn" : "text-fg-secondary", className)}>
        {stale && <ClockAlert size={14} aria-hidden="true" />}
        <span aria-hidden="true" className="whitespace-nowrap">
          {label} {formatShortDate(date)}
        </span>
        <span className="sr-only">
          {label} {full}
          {stale ? `, stale. ${showStaleText ? "" : staleText}` : ""}
        </span>
        {stale && showStaleText && <span>· {staleText}</span>}
      </span>
    </Tooltip>
  );
}
