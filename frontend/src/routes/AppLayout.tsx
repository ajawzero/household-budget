import { useState } from "react";
import { Outlet, useLocation } from "react-router";
import { NavDrawer } from "../components/shell/NavDrawer";
import { TabBar, TopBar } from "../components/shell/PhoneBars";
import { Sidebar } from "../components/shell/Sidebar";
import { AskProvider } from "../features/ai/AskContext";
import { AskPanel } from "../features/ai/AskPanel";
import { DemoBanner } from "../features/demo/DemoBanner";
import { cn } from "../lib/cn";
import { useMediaQuery } from "../lib/media";
import { useFadeIn } from "../lib/motion";
import { useScrollToTop } from "./useScrollToTop";

const COLLAPSE_KEY = "budget.sidebar";

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSE_KEY) === "collapsed";
  } catch {
    return false;
  }
}

/**
 * The signed-in shell (PLAN 8.1).
 * 1200px and up: 224px sidebar (can collapse to the rail).
 * 768 to 1199px: 52px icon rail. Its expand button opens the full nav as an overlay.
 * Under 768px: top bar, bottom tabs and a drawer.
 * Ask (Phase 6) opens from the sidebar or the phone top bar.
 */
export function AppLayout() {
  const wide = useMediaQuery("(min-width: 1200px)");
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const rail = !wide || collapsed;
  // A new page fades in. No exit, so navigation stays instant.
  const { pathname } = useLocation();
  const mainRef = useFadeIn<HTMLElement>(pathname);
  useScrollToTop();

  function toggle() {
    if (!wide) {
      setDrawerOpen(true);
      return;
    }
    const next = !collapsed;
    setCollapsed(next);
    try {
      window.localStorage.setItem(COLLAPSE_KEY, next ? "collapsed" : "open");
    } catch {
      // Storage blocked. The choice lasts for this page only.
    }
  }

  return (
    <AskProvider>
      <div className="min-h-dvh bg-page">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded-lg focus:bg-card focus:px-3 focus:py-2"
        >
          Skip to content
        </a>
        <aside
          className={cn(
            "fixed inset-y-0 left-0 z-20 hidden bg-page md:block",
            rail ? "w-[52px]" : "w-56",
          )}
        >
          <Sidebar compact={rail} onToggle={toggle} />
        </aside>

        <TopBar onMenu={() => setDrawerOpen(true)} />

        <div className={cn(rail ? "md:pl-[52px]" : "md:pl-56")}>
          <DemoBanner />
          <main ref={mainRef} id="main" className="mx-auto w-full max-w-[1440px] px-4 pb-24 md:px-6 md:pb-10 lg:px-8">
            <Outlet />
          </main>
        </div>

        <TabBar onMore={() => setDrawerOpen(true)} />
        <NavDrawer open={drawerOpen} onOpenChange={setDrawerOpen} />
        <AskPanel />
      </div>
    </AskProvider>
  );
}
