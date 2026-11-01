import { type RefObject, useEffect, useLayoutEffect, useRef, useState } from "react";

export interface IndicatorBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** The item's offset inside root, or null when root is not a positioned ancestor. */
function offsetWithin(item: HTMLElement, root: HTMLElement): { left: number; top: number } | null {
  let left = 0;
  let top = 0;
  let node: HTMLElement | null = item;
  while (node && node !== root) {
    left += node.offsetLeft;
    top += node.offsetTop;
    node = node.offsetParent instanceof HTMLElement ? node.offsetParent : null;
  }
  return node === root ? { left, top } : null;
}

/**
 * Where the active item sits inside a positioned container, so one indicator
 * (tab underline, segment pill, nav pill) can slide to it.
 * - `selector` finds the active item; `activeKey` changes when it does.
 * - `box` is null while nothing is active or the list is hidden (and in
 *   jsdom), so callers keep their plain active style as the fallback.
 * - `animate` is false for the first placement, so the indicator appears in
 *   place instead of sliding in from the corner.
 * Offsets ignore transforms, so a sheet sliding in does not move the box.
 */
export function useIndicator<T extends HTMLElement>(selector: string, activeKey: unknown) {
  const ref = useRef<T>(null);
  const [box, setBox] = useState<IndicatorBox | null>(null);
  const [animate, setAnimate] = useState(false);

  useLayoutEffect(() => {
    const root = ref.current;
    if (!root) return;
    const measure = () => {
      const item = root.querySelector<HTMLElement>(selector);
      const at = item && item.offsetWidth > 0 ? offsetWithin(item, root) : null;
      if (!item || !at) {
        setBox(null);
        return;
      }
      const next = { ...at, width: item.offsetWidth, height: item.offsetHeight };
      setBox((prev) =>
        prev &&
        prev.left === next.left &&
        prev.top === next.top &&
        prev.width === next.width &&
        prev.height === next.height
          ? prev
          : next,
      );
    };
    measure();
    // A late web font changes item widths without resizing the container.
    let live = true;
    document.fonts?.ready.then(() => live && measure());
    if (typeof ResizeObserver === "undefined") {
      return () => {
        live = false;
      };
    }
    // The container (shown, hidden, rail or full) and the active item itself.
    const observer = new ResizeObserver(measure);
    observer.observe(root);
    const item = root.querySelector<HTMLElement>(selector);
    if (item) observer.observe(item);
    return () => {
      live = false;
      observer.disconnect();
    };
  }, [selector, activeKey]);

  const shown = box !== null;
  useEffect(() => {
    if (!shown) {
      setAnimate(false);
      return;
    }
    const frame = requestAnimationFrame(() => setAnimate(true));
    return () => cancelAnimationFrame(frame);
  }, [shown]);

  return { ref, box, animate };
}

/** Inline style that puts an absolutely placed indicator on the box. */
export function indicatorStyle(box: IndicatorBox, axis: "x" | "y" | "both" = "both") {
  return {
    width: box.width,
    height: axis === "x" ? undefined : box.height,
    transform: `translate(${box.left}px, ${axis === "x" ? 0 : box.top}px)`,
  };
}

/** Transition classes for the indicator once it has been placed. */
export const INDICATOR_MOTION = "transition-[transform,width,height] duration-(--dur-base) ease-(--ease-out)";

/** Scrolls a bar that scrolls sideways, and only the bar (never the page), so
 * the span from `left` to `left + width` inside it is in view, with a 16px edge. */
export function scrollBarTo(bar: HTMLElement, left: number, width: number) {
  if (bar.scrollWidth <= bar.clientWidth) return;
  const edge = 16;
  if (left < bar.scrollLeft) bar.scrollLeft = Math.max(0, left - edge);
  else if (left + width > bar.scrollLeft + bar.clientWidth) bar.scrollLeft = left + width - bar.clientWidth + edge;
}

/** Keeps the active item of a sideways-scrolling bar in view, on load and when
 * it changes (a phone opening `?tab=fees` sees Fees, not half of it). */
export function useKeepInView(ref: RefObject<HTMLElement | null>, box: IndicatorBox | null) {
  const left = box?.left;
  const width = box?.width;
  useLayoutEffect(() => {
    if (ref.current && left !== undefined && width !== undefined) scrollBarTo(ref.current, left, width);
  }, [ref, left, width]);
}
