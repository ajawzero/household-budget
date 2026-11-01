// Demo mode in the browser (docs/plans/demo-mode.md section 7). The server
// picks the mode from the cookie. This file keeps what the browser needs
// around it: the mode a tab can check with no network wait, the message to
// the other tabs, the brand and the pinned clock.

import { logout, type Me } from "./api";
import { setPinnedToday } from "./format";
import { replacePage } from "./reload";

export type Mode = "real" | "demo" | "ended";

/** localStorage: the browser's mode and when it last changed. A time and a word, no data. */
export const MODE_KEY = "budget.mode";
export const MODE_CHANNEL = "budget.mode";
// A BroadcastChannel reaches every other channel object in this page too. The id lets
// the page that made the change ignore its own message: it is loading a new page already.
const PAGE_ID = Math.random().toString(36).slice(2);

/** The stored stamp as it is, null when there is none, undefined when storage cannot be read. */
export function readModeStamp(): string | null | undefined {
  try {
    return window.localStorage.getItem(MODE_KEY);
  } catch {
    return undefined;
  }
}

function storedMode(): Mode | null {
  const raw = readModeStamp();
  if (!raw) return null;
  try {
    const mode = (JSON.parse(raw) as { mode?: unknown }).mode;
    return mode === "real" || mode === "demo" || mode === "ended" ? mode : null;
  } catch {
    return null;
  }
}

/**
 * Writes the "demo" stamp before demo starts and reads it back, so a tab that misses
 * every message still finds a new stamp when it comes back. Returns an undo for a start
 * that fails, or null when storage takes no write: then demo must not start.
 */
export function stampDemoStart(): (() => void) | null {
  const before = readModeStamp();
  // Storage that cannot be read at all: every tab checks /me before it uncovers.
  if (before === undefined) return () => {};
  const next = JSON.stringify({ mode: "demo", at: Date.now() });
  try {
    window.localStorage.setItem(MODE_KEY, next);
  } catch {
    return null;
  }
  if (readModeStamp() !== next) return null;
  return () => {
    try {
      if (before === null) window.localStorage.removeItem(MODE_KEY);
      else window.localStorage.setItem(MODE_KEY, before);
    } catch {
      // The stamp stays new. Other tabs reload once more, which is safe.
    }
  };
}

/** Records a change of mode and tells the other tabs, which cover themselves and reload. */
export function markModeChanged(mode: Mode): void {
  try {
    window.localStorage.setItem(MODE_KEY, JSON.stringify({ mode, at: Date.now() }));
  } catch {
    // Storage blocked. The broadcast still reaches open tabs, and hidden ones check /me.
  }
  try {
    const channel = new BroadcastChannel(MODE_CHANNEL);
    channel.postMessage({ mode, from: PAGE_ID });
    channel.close();
  } catch {
    // No BroadcastChannel. The storage event and the cover still work.
  }
}

/** Calls `onChange` when another tab changes the mode. Returns the cleanup. */
export function onModeMessage(onChange: () => void): () => void {
  let channel: BroadcastChannel | null = null;
  try {
    channel = new BroadcastChannel(MODE_CHANNEL);
    channel.onmessage = (e: MessageEvent) => {
      if ((e.data as { from?: unknown } | null)?.from !== PAGE_ID) onChange();
    };
  } catch {
    channel = null;
  }
  const onStorage = (e: StorageEvent) => {
    // A null key means another tab cleared storage.
    if (e.key === MODE_KEY || e.key === null) onChange();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    channel?.close();
    window.removeEventListener("storage", onStorage);
  };
}

// What this page has seen from /me. A full page load starts both again.
let demoSeen = false;
let startedAs: string | undefined;

const identity = (me: Me | null) => `${me?.username ?? ""}|${me?.demo === true}`;

/** Applies a fresh /me: pins "today" in demo, and remembers the mode this page started in. */
export function applyMe(me: Me | null): void {
  setPinnedToday(me?.demo === true ? (me.household_today ?? null) : null);
  if (me?.demo === true) demoSeen = true;
  startedAs ??= identity(me);
}

/** True when `me` is the same user and mode this page started with. */
export function sameAsStart(me: Me | null): boolean {
  return identity(me) === (startedAs ?? identity(null));
}

/** A sign-out lands on "Demo ended" when this page was in demo or the browser was last in demo. */
export function demoEnded(): boolean {
  const mode = storedMode();
  return demoSeen || mode === "demo" || mode === "ended";
}

/** Tests start each case with a fresh page. */
export function resetDemoState(): void {
  demoSeen = false;
  startedAs = undefined;
  setPinnedToday(null);
}

/** Leaves demo: the server drops the demo session and both cookies, then "Demo ended" loads. */
export async function leaveDemo(): Promise<void> {
  try {
    await logout();
  } catch {
    // Signed out either way: the demo session ends in 8 hours, and sign-in clears its cookie.
  }
  markModeChanged("ended");
  replacePage("/demo-ended");
}

export const REAL_BRAND = { name: "Your household", mark: "YH" } as const;
export const DEMO_BRAND = { name: "Sample household", mark: "SH" } as const;

/** The household name and mark for the current /me. Demo never shows the real one. */
export function brandFor(me: Me | null | undefined): { name: string; mark: string } {
  return me?.demo === true ? DEMO_BRAND : REAL_BRAND;
}
