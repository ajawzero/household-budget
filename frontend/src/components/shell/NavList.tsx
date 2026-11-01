import { NavLink, useLocation } from "react-router";
import { cn } from "../../lib/cn";
import { INDICATOR_MOTION, indicatorStyle, useIndicator } from "../../lib/indicator";
import type { NavItem } from "../../routes/nav";
import { Tooltip } from "../ui/tooltip";

interface NavListProps {
  items: NavItem[];
  /** Icons only (the rail). Labels move to tooltips and aria-labels. */
  compact?: boolean;
  onNavigate?: () => void;
}

const ITEM =
  "relative flex h-11 items-center gap-3 rounded-lg px-2.5 text-sm text-fg no-underline transition-colors duration-100 hover:bg-active md:h-9";

/**
 * The active gray pill slides to the new page within a list. A page in another
 * list gets its pill in place. Until measured, the link draws its own pill.
 */
export function NavList({ items, compact = false, onNavigate }: NavListProps) {
  const { pathname } = useLocation();
  const { ref, box, animate } = useIndicator<HTMLDivElement>('[aria-current="page"]', `${pathname} ${compact}`);
  return (
    <div ref={ref} className="relative">
      {box && (
        <span
          aria-hidden="true"
          className={cn("absolute left-0 top-0 rounded-lg bg-active", animate && INDICATOR_MOTION)}
          style={indicatorStyle(box)}
        />
      )}
      <ul className="m-0 flex list-none flex-col gap-0.5 p-0">
        {items.map((item) => {
          const Icon = item.icon;
          const link = (
            <NavLink
              to={item.to}
              end={item.to === "/"}
              onClick={onNavigate}
              aria-label={compact ? item.label : undefined}
              className={({ isActive }) =>
                cn(ITEM, compact && "justify-center px-0", isActive && "font-medium", isActive && !box && "bg-active")
              }
            >
              {({ isActive }) => (
                <>
                  <Icon size={20} strokeWidth={isActive ? 2.25 : 1.75} aria-hidden="true" className="shrink-0" />
                  {!compact && <span className="truncate">{item.label}</span>}
                </>
              )}
            </NavLink>
          );
          return (
            <li key={item.to}>
              {compact ? (
                // The tooltip needs a plain element: NavLink takes a className function.
                <Tooltip content={item.label} side="right">
                  <span className="block">{link}</span>
                </Tooltip>
              ) : (
                link
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
