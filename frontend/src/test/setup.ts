import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom has no matchMedia or ResizeObserver. Charts and the shell use both.
if (typeof window.matchMedia !== "function") {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as MediaQueryList;
}

if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}

// jsdom has no scrolling. The shell resets it on a new page.
window.scrollTo = (() => {}) as typeof window.scrollTo;

afterEach(async () => {
  cleanup();
  document.documentElement.removeAttribute("data-theme");
  // Each test is a fresh page: no mode stamp, no pinned date. Imported here, not at the
  // top, so a test file's mock of lib/reload also reaches lib/demo.
  window.localStorage.clear();
  (await import("../lib/demo")).resetDemoState();
});
