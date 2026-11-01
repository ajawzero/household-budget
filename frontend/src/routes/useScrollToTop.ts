import { useLayoutEffect, useRef } from "react";
import { useLocation, useNavigationType } from "react-router";

/**
 * A new page opens at the top. A query change like ?tab= keeps the place.
 * Back and Forward (POP) are left to the browser.
 *
 * The window is the scroller: the sidebar and bars are fixed. A layout effect
 * runs before every passive effect in the same commit, so a page's own
 * scrollIntoView (Travel's ?trip=) still runs after this reset and wins. It
 * also runs before paint, so the old place never flashes. The first mount
 * does nothing, so a reload keeps the browser's own restore.
 */
export function useScrollToTop(): void {
  const { pathname } = useLocation();
  const type = useNavigationType();
  const last = useRef(pathname);
  useLayoutEffect(() => {
    if (last.current === pathname) return;
    last.current = pathname;
    if (type !== "POP") window.scrollTo(0, 0);
  }, [pathname, type]);
}
