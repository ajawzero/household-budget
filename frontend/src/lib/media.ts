import { useSyncExternalStore } from "react";

/** True while the media query matches. False where matchMedia is missing. */
export function useMediaQuery(q: string): boolean {
  return useSyncExternalStore(
    (fn) => {
      if (typeof window.matchMedia !== "function") return () => {};
      const mq = window.matchMedia(q);
      mq.addEventListener("change", fn);
      return () => mq.removeEventListener("change", fn);
    },
    () => typeof window.matchMedia === "function" && window.matchMedia(q).matches,
    () => false,
  );
}

/** Charts turn animation off when this is true. */
export function useReducedMotion(): boolean {
  return useMediaQuery("(prefers-reduced-motion: reduce)");
}
