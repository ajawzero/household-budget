import { cn } from "../../lib/cn";

// Tints come from the chart slots, mixed with the card color in tokens.css
// strength. Color is fixed per name, so an institution keeps its color.
// Only the category slots and the neutral: income blue, spending orange and
// left over green carry meaning elsewhere, so a monogram never wears them.
const SLOTS = ["--chart-4", "--chart-5", "--chart-7", "--chart-6", "--chart-8", "--chart-muted"] as const;

const SKIP = new Set(["the", "of", "and", "&", "a"]);

/** "Example Credit Union" to "EC", "USBK" to "US", "Chase" to "C". */
export function initials(name: string): string {
  const words = name
    .split(/[\s\-–—/]+/)
    .map((w) => w.replace(/[^\p{L}\p{N}]/gu, ""))
    .filter((w) => w && !SKIP.has(w.toLowerCase()));
  const [first, second] = words;
  if (!first) return "?";
  if (second) return (first[0]! + second[0]!).toUpperCase();
  if (first.length <= 4 && first === first.toUpperCase()) return first.slice(0, 2);
  return first[0]!.toUpperCase();
}

function slotFor(name: string): string {
  let h = 0;
  for (const ch of name.toLowerCase()) h = (h * 31 + ch.codePointAt(0)!) >>> 0;
  return SLOTS[h % SLOTS.length]!;
}

interface MonogramProps {
  /** Institution name. The row shows it as text too, so this is decorative. */
  name: string | null | undefined;
  /** 48 in account rows (40 on phones), 40 in headers, 24 in compact lists. */
  size?: 48 | 40 | 24;
  className?: string;
}

/** Initials on a tinted circle. No bank logos. */
export function Monogram({ name, size = 40, className }: MonogramProps) {
  const label = name?.trim() || "Other";
  const slot = slotFor(label);
  return (
    <span
      aria-hidden="true"
      data-slot={slot}
      className={cn(
        "inline-flex shrink-0 select-none items-center justify-center rounded-full font-semibold text-fg",
        size === 48 && "h-10 w-10 text-sm md:h-12 md:w-12 md:text-base",
        size === 40 && "h-10 w-10 text-sm",
        size === 24 && "h-6 w-6 text-[10px]",
        className,
      )}
      style={{ background: `color-mix(in oklab, var(${slot}) var(--mono-strength), var(--bg-card))` }}
    >
      {initials(label)}
    </span>
  );
}
