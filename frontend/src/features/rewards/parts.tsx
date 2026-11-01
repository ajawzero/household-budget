// Small pieces shared by the Rewards tabs.
import { CircleAlert, CircleCheck, ClockAlert } from "lucide-react";
import type { ReactNode } from "react";
import { Card } from "../../components/ui/card";
import { Skeleton } from "../../components/ui/skeleton";
import type { RewardTiles } from "../../lib/api";
import { cn } from "../../lib/cn";
import { formatMoney, formatShortDate } from "../../lib/format";
import { ListSkeleton } from "../recurring/parts";
import { daysLeftText, rangeText } from "./model";

interface Tile {
  key: string;
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "warn";
}

/** The three tiles: estimated value, credits left this cycle, next deadline. Two across on phones. */
export function RewardTilesRow({ tiles, loading }: { tiles: RewardTiles | null | undefined; loading: boolean }) {
  const t = tiles ?? undefined;
  const d = t?.next_deadline ?? null;
  const items: Tile[] = [
    {
      key: "value",
      label: "Estimated value",
      value: t ? rangeText(t.value_low_cents, t.value_high_cents) : "",
      sub: t ? `${t.valued_count} ${t.valued_count === 1 ? "program" : "programs"} valued` : undefined,
    },
    {
      key: "credits",
      label: "Credits left this cycle",
      value: t ? formatMoney(t.credits_left_cents) : "",
      sub: t ? `${t.credits_left_count} ${t.credits_left_count === 1 ? "credit" : "credits"}` : undefined,
    },
    {
      key: "deadline",
      label: "Next deadline",
      // Blank with no tiles (a load error), like the other two. "None" only when the API says so.
      value: d ? formatShortDate(d.date) : t ? "None" : "",
      // Within 14 days: a word and a clock icon, not only the warning color.
      sub: d ? `${d.days_left <= 14 ? "Soon · " : ""}${d.label} · ${daysLeftText(d.days_left)}` : t ? "Nothing due" : undefined,
      tone: d && d.days_left <= 14 ? "warn" : undefined,
    },
  ];
  return (
    <ul aria-label="Rewards summary" className="m-0 grid list-none grid-cols-2 gap-3 p-0 md:grid-cols-3 md:gap-4">
      {items.map((k) => (
        <li key={k.key} className="min-w-0">
          <Card className="flex h-full flex-col items-center justify-center px-2 py-4 text-center md:p-5">
            {loading && !tiles ? (
              <div role="status" aria-label="Loading" className="flex flex-col items-center gap-2">
                <Skeleton className="h-7 w-20" />
                <Skeleton className="h-3 w-16" />
              </div>
            ) : (
              <>
                <p className={cn("m-0 max-w-full text-lg font-medium md:text-2xl", k.tone === "warn" ? "text-warn" : "text-fg")}>
                  {k.tone === "warn" ? (
                    <span className="inline-flex items-center gap-1">
                      <ClockAlert size={16} aria-hidden="true" className="shrink-0" />
                      {k.value}
                    </span>
                  ) : (
                    k.value
                  )}
                </p>
                <p className="eyebrow m-0 mt-1">{k.label}</p>
                {k.sub && <p className="m-0 mt-1 text-xs text-fg-secondary md:text-sm">{k.sub}</p>}
              </>
            )}
          </Card>
        </li>
      ))}
    </ul>
  );
}

/** A list card's body before its data: a skeleton while loading, quiet text after a load error. */
export function CardPending({ loading, error, rows }: { loading: boolean; error: string | null; rows?: number }) {
  if (loading) return <ListSkeleton rows={rows} />;
  return error ? <p className="m-0 py-8 text-center text-sm text-fg-secondary">{error}</p> : null;
}

/** A bar from 0 to `max` with a fill, at most 12px tall. The text next to it carries the numbers. */
export function ProgressBar({ pct, color, label }: { pct: number; color: string; label: string }) {
  return (
    <div
      className="h-2 overflow-hidden rounded-full"
      style={{ background: `color-mix(in oklab, ${color} 18%, var(--bg-card))` }}
      role="img"
      aria-label={label}
    >
      <span className="block h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
    </div>
  );
}

/** A labeled list inside a card, like "Best uses". */
export function MiniList({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) return null;
  return (
    <div>
      <p className="eyebrow m-0">{title}</p>
      <ul className="m-0 mt-1 list-disc break-words pl-5 text-sm text-fg">
        {items.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ul>
    </div>
  );
}

/** Where admin controls would be, for members: "Only an admin can add programs." */
export function MemberHint({ children }: { children: ReactNode }) {
  return <p className="m-0 text-sm text-fg-secondary">{children}</p>;
}

/** A saved or failed note under a form, read out politely. Empty keeps the live region in place. */
export function FormStatus({ id, problem, saved }: { id?: string; problem: string | null; saved: string | null }) {
  return (
    <div aria-live="polite">
      {problem && (
        <p id={id} className="m-0 mt-2 flex items-start gap-1.5 text-sm text-bad">
          <CircleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
          {problem}
        </p>
      )}
      {saved && !problem && (
        <p className="m-0 mt-2 flex items-start gap-1.5 text-sm text-good">
          <CircleCheck size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
          {saved}
        </p>
      )}
    </div>
  );
}
