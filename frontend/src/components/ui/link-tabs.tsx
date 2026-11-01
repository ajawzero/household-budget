import { Link } from "react-router";
import { cn } from "../../lib/cn";
import { INDICATOR_MOTION, indicatorStyle, useIndicator, useKeepInView } from "../../lib/indicator";

export interface LinkTab {
  /** Shown text. */
  label: string;
  /** Where the tab goes, like "?tab=all". Keeps views bookmarkable. */
  to: string;
  active: boolean;
}

/**
 * Page tabs next to the title (Monarch: "Reports  Cash Flow  Spending").
 * Each tab is a link that changes the URL, so it is a nav, not an ARIA
 * tablist. The active tab has a teal underline and aria-current="page".
 * The underline slides to a new tab; until it is measured, the tab draws its own.
 */
export function LinkTabs({ tabs, label }: { tabs: LinkTab[]; label: string }) {
  const active = tabs.find((t) => t.active)?.label;
  const { ref, box, animate } = useIndicator<HTMLElement>('[aria-current="page"]', active);
  useKeepInView(ref, box);
  return (
    <nav ref={ref} aria-label={label} className="relative max-w-full overflow-x-auto">
      {box && (
        <span
          aria-hidden="true"
          className={cn("absolute bottom-0 left-0 h-0.5 rounded-full bg-accent", animate && INDICATOR_MOTION)}
          // Inset 8px each side, like the tab's own underline.
          style={indicatorStyle({ ...box, left: box.left + 8, width: Math.max(0, box.width - 16) }, "x")}
        />
      )}
      <ul className="m-0 flex list-none gap-1 p-0">
        {tabs.map((t) => (
          <li key={t.label}>
            <Link
              to={t.to}
              replace
              aria-current={t.active ? "page" : undefined}
              className={cn(
                "relative inline-flex h-11 min-w-11 items-center justify-center whitespace-nowrap px-2 text-base font-medium no-underline transition-colors duration-100 md:h-9 md:min-w-0",
                t.active ? "text-fg" : "text-fg-secondary hover:text-fg",
              )}
            >
              {t.label}
              {t.active && !box && (
                <span aria-hidden="true" className="absolute inset-x-2 bottom-0 h-0.5 rounded-full bg-accent" />
              )}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
