import type { ComponentProps } from "react";
import { cn } from "../../lib/cn";

interface TableScrollProps extends ComponentProps<"div"> {
  /** What the table shows, read out when the region gets focus. */
  label: string;
}

/**
 * A wide table scrolls inside its card, never the page (`.table-scroll`). The
 * region takes keyboard focus, so arrow keys scroll it without a mouse.
 */
export function TableScroll({ label, className, ...rest }: TableScrollProps) {
  return <div role="region" aria-label={label} tabIndex={0} className={cn("table-scroll", className)} {...rest} />;
}
