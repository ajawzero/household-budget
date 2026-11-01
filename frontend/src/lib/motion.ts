import { addTransitionType, startTransition, useEffect, useRef } from "react";
import { useReducedMotion } from "./media";

/**
 * Fades the element in each time `key` changes: a new page, tab or view.
 * Opacity only, so fixed bars inside never move. Timing comes from the motion
 * tokens. Skipped under reduced motion and where the Web Animations API is
 * missing (jsdom). `skipFirst` leaves the first mount alone, for a panel
 * inside a page that already fades in.
 */
export function useFadeIn<T extends HTMLElement>(key: unknown, skipFirst = false) {
  const ref = useRef<T>(null);
  const reduced = useReducedMotion();
  const first = useRef(true);
  useEffect(() => {
    const skip = skipFirst && first.current;
    first.current = false;
    const el = ref.current;
    if (skip || !el || reduced || typeof el.animate !== "function") return;
    const css = getComputedStyle(el);
    el.animate([{ opacity: 0 }, { opacity: 1 }], {
      duration: Number.parseFloat(css.getPropertyValue("--dur-base")) || 220,
      easing: css.getPropertyValue("--ease-out").trim() || "ease-out",
    });
    // Only a new key replays it. A reduced-motion change alone does not.
  }, [key]);
  return ref;
}

/** The transition type for a change the user makes to a list (DESIGN.md "Motion"). */
export const LIST_CHANGE = "list-change";

/**
 * Runs `update` as a transition marked as a list change, so the list's
 * `<ViewTransition>` boundaries animate: an item that leaves fades out and the
 * rest slide to their new places. Page changes carry no type, so the same
 * boundaries stay still then. Where the browser has no View Transitions
 * (jsdom, older browsers) the update just happens.
 */
export function listChange(update: () => void) {
  startTransition(() => {
    addTransitionType(LIST_CHANGE);
    update();
  });
}

const onListChange = (name: string) => ({ [LIST_CHANGE]: name, default: "none" });

/** `<ViewTransition>` props for an item in a list (classes styled in `app.css`). */
export const LIST_ITEM = {
  default: "none",
  enter: onListChange("vt-item-in"),
  exit: onListChange("vt-item-out"),
  update: onListChange("vt-item-move"),
} as const;

/** `<ViewTransition>` props for a bar fixed to the bottom that leaves on a list change. */
export const BOTTOM_BAR = { default: "none", exit: onListChange("vt-bar-out") } as const;
