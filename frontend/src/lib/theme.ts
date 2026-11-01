// Theme choice: Auto (follow the device), Light or Dark. Stored per browser.
// public/theme-boot.js applies the same key before first paint.
import { useSyncExternalStore } from "react";

export type ThemePref = "auto" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export const THEME_KEY = "budget.theme";
const DARK_QUERY = "(prefers-color-scheme: dark)";

const listeners = new Set<() => void>();

export function readThemePref(): ThemePref {
  try {
    const v = window.localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : "auto";
  } catch {
    return "auto";
  }
}

function applyThemePref(pref: ThemePref): void {
  const root = document.documentElement;
  if (pref === "auto") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", pref);
}

export function setThemePref(pref: ThemePref): void {
  try {
    if (pref === "auto") window.localStorage.removeItem(THEME_KEY);
    else window.localStorage.setItem(THEME_KEY, pref);
  } catch {
    // Storage blocked (private window). The choice still applies for this page.
  }
  applyThemePref(pref);
  for (const fn of listeners) fn();
}

function systemIsDark(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(DARK_QUERY).matches;
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  const mq = typeof window.matchMedia === "function" ? window.matchMedia(DARK_QUERY) : null;
  mq?.addEventListener("change", fn);
  return () => {
    listeners.delete(fn);
    mq?.removeEventListener("change", fn);
  };
}

function currentPref(): ThemePref {
  const attr = document.documentElement.getAttribute("data-theme");
  return attr === "light" || attr === "dark" ? attr : "auto";
}

/** The saved choice. Re-renders when it changes. */
export function useThemePref(): ThemePref {
  return useSyncExternalStore(subscribe, currentPref, () => "auto");
}

/** What is on screen now. Charts use it to re-render when the theme flips. */
export function useResolvedTheme(): ResolvedTheme {
  return useSyncExternalStore(
    subscribe,
    () => {
      const pref = currentPref();
      if (pref !== "auto") return pref;
      return systemIsDark() ? "dark" : "light";
    },
    () => "light",
  );
}

/** Called once at start, in case the boot script could not run. */
export function initTheme(): void {
  applyThemePref(readThemePref());
}
