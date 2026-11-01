import { type ReactNode, useLayoutEffect } from "react";
import { createPortal } from "react-dom";
import { shieldPage, unshieldPage } from "./shield";

/**
 * A plain screen over the whole page, so nothing under it shows. Portaled, so no
 * parent can clip it. It takes taps itself: an open drawer turns them off on the body.
 * While it is up, the rest of the page, open drawers too, is hidden and inert. Show it
 * inside `flushSync` when the page is about to go away, so it is there before the browser
 * keeps or freezes the page.
 */
export function PageCover({ children }: { children?: ReactNode }) {
  useLayoutEffect(() => {
    shieldPage();
    return unshieldPage;
  }, []);
  return createPortal(
    <div
      data-page-cover=""
      data-testid="page-cover"
      className="pointer-events-auto fixed inset-0 z-[60] flex items-center justify-center bg-page px-4"
    >
      {children}
    </div>,
    document.body,
  );
}
