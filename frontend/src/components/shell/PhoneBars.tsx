import { Ellipsis, Menu, Sparkles } from "lucide-react";
import { NavLink, useLocation } from "react-router";
import { useAsk } from "../../features/ai/AskContext";
import { useAiStatus } from "../../features/ai/api";
import { cn } from "../../lib/cn";
import { ALL_NAV, navItemFor, TAB_NAV } from "../../routes/nav";

/** Phone top bar: menu button and the page name. */
export function TopBar({ onMenu }: { onMenu: () => void }) {
  const { pathname } = useLocation();
  const title = pathname.startsWith("/settings")
    ? "Settings"
    : pathname === "/checks"
      ? "Checks"
      : pathname === "/transactions/suggestions"
        ? "Suggestions"
        : (navItemFor(pathname)?.label ?? "");
  return (
    <header className="sticky top-0 z-30 flex h-14 [view-transition-name:phone-top] items-center gap-2 bg-page/95 px-2 pt-[env(safe-area-inset-top)] backdrop-blur md:hidden">
      <button
        type="button"
        onClick={onMenu}
        aria-label="Open menu"
        className="inline-flex h-11 w-11 items-center justify-center rounded-lg text-fg hover:bg-active"
      >
        <Menu size={22} aria-hidden="true" />
      </button>
      <p className="m-0 flex-1 truncate text-center text-base font-medium text-fg">{title}</p>
      <PhoneAskButton />
    </header>
  );
}

/** Ask, when AI may run. Otherwise a spacer that keeps the title centered. */
function PhoneAskButton() {
  const status = useAiStatus();
  const { setOpen } = useAsk();
  if (!status.data?.available) return <span className="w-11" aria-hidden="true" />;
  return (
    <button
      type="button"
      onClick={() => setOpen(true)}
      aria-label="Ask"
      aria-haspopup="dialog"
      className="inline-flex h-11 w-11 items-center justify-center rounded-lg text-accent-text hover:bg-active"
    >
      <Sparkles size={22} aria-hidden="true" />
    </button>
  );
}

/** Phone bottom tabs: Dashboard, Accounts, Transactions, Budget, More. */
export function TabBar({ onMore }: { onMore: () => void }) {
  const { pathname } = useLocation();
  const tabs = TAB_NAV.map((to) => ALL_NAV.find((n) => n.to === to)!);
  // Pages outside the four main tabs live under More, so More shows as the current tab.
  const onTab = tabs.some((t) => (t.to === "/" ? pathname === "/" : pathname === t.to || pathname.startsWith(`${t.to}/`)));
  return (
    <nav
      aria-label="Tabs"
      className="fixed inset-x-0 bottom-0 z-30 flex border-t [view-transition-name:phone-tabs] border-line bg-card pb-[env(safe-area-inset-bottom)] md:hidden"
    >
      {tabs.map((t) => {
        const Icon = t.icon;
        return (
          <NavLink
            key={t.to}
            to={t.to}
            end={t.to === "/"}
            className={({ isActive }) =>
              cn(
                "flex min-h-14 flex-1 flex-col items-center justify-center gap-0.5 text-[11px] no-underline",
                isActive ? "font-medium text-accent-text" : "text-fg-secondary",
              )
            }
          >
            {({ isActive }) => (
              <>
                <Icon size={22} strokeWidth={isActive ? 2.25 : 1.75} aria-hidden="true" />
                {t.label}
              </>
            )}
          </NavLink>
        );
      })}
      <button
        type="button"
        onClick={onMore}
        className={cn(
          "flex min-h-14 flex-1 flex-col items-center justify-center gap-0.5 text-[11px]",
          onTab ? "text-fg-secondary" : "font-medium text-accent-text",
        )}
      >
        <Ellipsis size={22} strokeWidth={onTab ? 1.75 : 2.25} aria-hidden="true" />
        More
        {!onTab && <span className="sr-only">, current section</span>}
      </button>
    </nav>
  );
}
