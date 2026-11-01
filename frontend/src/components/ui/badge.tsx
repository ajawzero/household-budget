import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

export type BadgeTone = "neutral" | "good" | "bad" | "warn" | "accent";

const TONES: Record<BadgeTone, string> = {
  neutral: "bg-subtle text-fg-secondary",
  good: "bg-tint-good text-good",
  bad: "bg-tint-bad text-bad",
  warn: "bg-tint-warn text-warn",
  accent: "bg-accent-tint text-accent-text",
};

/**
 * A small pill with a word in it: a status, a kind or a note. The word always
 * says what the color means, so color is never the only signal.
 */
export function Badge({
  tone = "neutral",
  icon,
  title,
  className,
  children,
}: {
  tone?: BadgeTone;
  /** A 12px lucide icon, aria-hidden. */
  icon?: ReactNode;
  /** Hover text with more detail. */
  title?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      title={title}
      className={cn(
        "inline-flex h-5 shrink-0 items-center gap-1 whitespace-nowrap rounded-full px-2 align-middle text-xs font-medium",
        TONES[tone],
        className,
      )}
    >
      {icon}
      {children}
    </span>
  );
}
