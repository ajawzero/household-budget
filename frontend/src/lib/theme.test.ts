import { afterEach, describe, expect, it, vi } from "vitest";
import { readThemePref, setThemePref, THEME_KEY } from "./theme";

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("theme", () => {
  it("stores Light and Dark, and Auto clears the choice", () => {
    setThemePref("dark");
    expect(window.localStorage.getItem(THEME_KEY)).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(readThemePref()).toBe("dark");

    setThemePref("auto");
    expect(window.localStorage.getItem(THEME_KEY)).toBeNull();
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
    expect(readThemePref()).toBe("auto");
  });

  it("ignores junk values", () => {
    window.localStorage.setItem(THEME_KEY, "purple");
    expect(readThemePref()).toBe("auto");
  });

  it("still applies the theme when storage throws", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => setThemePref("light")).not.toThrow();
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(readThemePref()).toBe("auto");
  });
});
