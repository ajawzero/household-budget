import { type ReactNode, useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import { Button } from "../../components/ui/button";
import { getMe } from "../../lib/api";
import { onModeMessage, readModeStamp, sameAsStart } from "../../lib/demo";
import { reloadPage } from "../../lib/reload";
import { PageCover } from "./PageCover";

/** How long a tab waits for /me when it cannot read storage. Slower stays covered. */
export const ME_CHECK_MS = 3000;

type Cover = "off" | "on" | "stuck";

/**
 * Covers the page while the tab is hidden or in page history, so a tab left
 * open from before a change of mode never shows the other mode's numbers
 * (docs/plans/demo-mode.md section 7). Coming back, it reads the mode stamp
 * first, with no network wait: the same stamp uncovers at once, a new one
 * reloads. A message from another tab covers and reloads.
 */
export function TabCover({ children }: { children: ReactNode }) {
  const [cover, setCover] = useState<Cover>("off");
  // The stamp when this page loaded. A full load reads it again.
  const seen = useRef(readModeStamp());
  // Each hide, show and message starts a new round. Only the newest /me check may uncover.
  const round = useRef(0);

  useEffect(() => {
    let alive = true;
    // In the DOM before the handler returns: right after pagehide or hidden, the
    // browser may freeze the page or keep it in history as it is.
    const coverNow = () => flushSync(() => setCover("on"));
    const hide = () => {
      round.current++;
      coverNow();
    };
    const changed = () => {
      round.current++;
      coverNow();
      reloadPage();
    };

    async function show() {
      const mine = ++round.current;
      const stamp = readModeStamp();
      if (stamp !== undefined) {
        if (stamp === seen.current) setCover("off");
        else changed();
        return;
      }
      // Storage cannot be read: stay covered until an uncached /me says the same user and mode.
      coverNow();
      const timeout = new Promise<undefined>((done) => setTimeout(() => done(undefined), ME_CHECK_MS));
      const me = await Promise.race([getMe(), timeout]).catch(() => undefined);
      // A late answer from an older round, or a tab hidden again, keeps the cover.
      if (!alive || mine !== round.current || document.visibilityState === "hidden") return;
      setCover(me !== undefined && sameAsStart(me) ? "off" : "stuck");
    }

    const onVisibility = () => {
      if (document.visibilityState === "hidden") hide();
      else void show();
    };
    // A page coming back from the back-forward cache. The first load is not one.
    const onPageShow = (e: PageTransitionEvent) => {
      if (e.persisted) void show();
    };
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", hide);
    window.addEventListener("pageshow", onPageShow);
    const stop = onModeMessage(changed);
    return () => {
      alive = false;
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", hide);
      window.removeEventListener("pageshow", onPageShow);
      stop();
    };
  }, []);

  return (
    <>
      {/* Not painted, not read out, not focusable, but still laid out, so the page keeps
          its state and scroll for when it uncovers. The cover on top is opaque too, and
          hides the drawers and menus portaled outside this wrapper. */}
      <div
        className="contents"
        style={cover === "off" ? undefined : { visibility: "hidden" }}
        aria-hidden={cover === "off" ? undefined : true}
        inert={cover !== "off"}
      >
        {children}
      </div>
      {cover !== "off" && (
        <PageCover>
          {cover === "stuck" && (
            <Button variant="primary" onClick={reloadPage}>
              Reload
            </Button>
          )}
        </PageCover>
      )}
    </>
  );
}
