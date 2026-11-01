import { Link } from "react-router";
import { useMe } from "../../lib/auth";
import { cn } from "../../lib/cn";
import { brandFor } from "../../lib/demo";

/** "JF" mark and the household name ("SH", "Sample household" in demo). The rail shows the mark only. */
export function Logo({ compact = false }: { compact?: boolean }) {
  const brand = brandFor(useMe().data);
  return (
    <Link
      to="/"
      className="flex min-h-11 items-center gap-2.5 rounded-lg text-fg no-underline md:min-h-9"
      aria-label={compact ? `${brand.name}, Dashboard` : undefined}
    >
      <span
        aria-hidden="true"
        className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary text-xs font-semibold tracking-wide text-on-primary"
      >
        {brand.mark}
      </span>
      <span className={cn("truncate text-base font-semibold", compact && "sr-only")}>{brand.name}</span>
    </Link>
  );
}
