import { act, render, screen } from "@testing-library/react";
import { useState, ViewTransition } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NavList } from "../components/shell/NavList";
import { LinkTabs } from "../components/ui/link-tabs";
import { SegmentedControl } from "../components/ui/segmented-control";
import { PageTabs } from "../features/cashflow/PageTabs";
import { MAIN_NAV } from "../routes/nav";
import { indicatorStyle, scrollBarTo } from "./indicator";
import { LIST_ITEM, listChange, useFadeIn } from "./motion";

const originalAnimate = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "animate");
const originalMatchMedia = Object.getOwnPropertyDescriptor(window, "matchMedia");

function stubAnimate() {
  const animate = vi.fn();
  Object.defineProperty(HTMLElement.prototype, "animate", { configurable: true, value: animate });
  return animate;
}

function restoreMotionMocks() {
  if (originalAnimate) Object.defineProperty(HTMLElement.prototype, "animate", originalAnimate);
  else Reflect.deleteProperty(HTMLElement.prototype, "animate");
  if (originalMatchMedia) Object.defineProperty(window, "matchMedia", originalMatchMedia);
  else Reflect.deleteProperty(window, "matchMedia");
}

describe("useFadeIn", () => {
  it("animates when the key changes", () => {
    const animate = stubAnimate();
    const View = ({ page }: { page: string }) => {
      const ref = useFadeIn<HTMLDivElement>(page);
      return <div ref={ref}>Page {page}</div>;
    };
    const view = render(<View page="one" />);
    expect(animate).toHaveBeenCalledTimes(1);

    view.rerender(<View page="two" />);
    expect(animate).toHaveBeenCalledTimes(2);
    expect(screen.getByText("Page two")).toBeTruthy();
  });

  it("does not animate on the first mount when skipFirst is true", () => {
    const animate = stubAnimate();
    const View = ({ page }: { page: string }) => {
      const ref = useFadeIn<HTMLDivElement>(page, true);
      return <div ref={ref}>Page {page}</div>;
    };
    const view = render(<View page="one" />);
    expect(animate).not.toHaveBeenCalled();

    view.rerender(<View page="two" />);
    expect(animate).toHaveBeenCalledTimes(1);
  });

  it("does not animate when reduced motion is enabled", () => {
    const animate = stubAnimate();
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      value: vi.fn((query: string) => ({
        matches: query === "(prefers-reduced-motion: reduce)",
        media: query,
        onchange: null,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        dispatchEvent: vi.fn(),
      })),
    });
    function View({ page }: { page: string }) {
      const ref = useFadeIn<HTMLDivElement>(page);
      return <div ref={ref}>Page {page}</div>;
    }

    const view = render(<View page="one" />);
    view.rerender(<View page="two" />);
    expect(animate).not.toHaveBeenCalled();
    expect(window.matchMedia).toHaveBeenCalledWith("(prefers-reduced-motion: reduce)");
  });

  it("does not throw when the Web Animations API is missing", () => {
    Reflect.deleteProperty(HTMLElement.prototype, "animate");
    expect(() => render(<MissingAnimateView />)).not.toThrow();
  });
});

function MissingAnimateView() {
  const ref = useFadeIn<HTMLDivElement>("page");
  return <div ref={ref}>Page</div>;
}

describe("indicatorStyle", () => {
  const box = { left: 12, top: 8, width: 90, height: 32 };

  it("moves on the x axis without setting a height", () => {
    expect(indicatorStyle(box, "x")).toEqual({
      width: 90,
      height: undefined,
      transform: "translate(12px, 0px)",
    });
  });

  it("sets both dimensions and moves on both axes", () => {
    expect(indicatorStyle(box, "both")).toEqual({
      width: 90,
      height: 32,
      transform: "translate(12px, 8px)",
    });
  });
});

describe("active style fallbacks", () => {
  it("keeps the selected SegmentedControl item styled when there is no indicator box", () => {
    render(
      <SegmentedControl
        label="Range"
        value="month"
        onChange={() => {}}
        options={[
          { value: "month", label: "Month" },
          { value: "year", label: "Year" },
        ]}
      />,
    );

    expect(screen.getByRole("radio", { name: "Month" }).className).toContain("data-[state=on]:bg-card");
  });

  it("keeps the active LinkTabs underline when there is no indicator box", () => {
    render(
      <MemoryRouter>
        <LinkTabs
          label="Cash flow views"
          tabs={[
            { label: "Overview", to: "/cash-flow", active: true },
            { label: "Spending", to: "/cash-flow/spending", active: false },
          ]}
        />
      </MemoryRouter>,
    );

    const active = screen.getByRole("link", { name: "Overview" });
    expect(active.querySelector("span")?.className).toContain("bg-accent");
  });

  it("keeps the active PageTabs underline when there is no indicator box", () => {
    render(
      <PageTabs
        label="Cash flow views"
        idPrefix="cashflow"
        value="overview"
        tabs={[
          { value: "overview", label: "Overview" },
          { value: "spending", label: "Spending" },
        ]}
        onChange={() => {}}
      />,
    );

    expect(screen.getByRole("tab", { name: "Overview" }).className).toContain("border-accent");
  });

  it("keeps the active NavList link styled when there is no indicator box", () => {
    render(
      <MemoryRouter initialEntries={["/cash-flow"]}>
        <NavList items={MAIN_NAV} />
      </MemoryRouter>,
    );

    expect(screen.getByRole("link", { name: "Cash Flow" }).className).toContain("bg-active");
  });
});

afterEach(restoreMotionMocks);

describe("scrollBarTo", () => {
  function bar(scrollLeft: number) {
    const el = document.createElement("div");
    Object.defineProperties(el, {
      scrollWidth: { value: 600 },
      clientWidth: { value: 300 },
    });
    el.scrollLeft = scrollLeft;
    return el;
  }

  it("brings an item past the right edge into view, with an edge", () => {
    const el = bar(0);
    scrollBarTo(el, 500, 60);
    expect(el.scrollLeft).toBe(276);
  });

  it("brings an item before the left edge into view", () => {
    const el = bar(250);
    scrollBarTo(el, 100, 60);
    expect(el.scrollLeft).toBe(84);
  });

  it("leaves a bar alone when the item shows or nothing scrolls", () => {
    const el = bar(100);
    scrollBarTo(el, 150, 60);
    expect(el.scrollLeft).toBe(100);
    const fits = document.createElement("div");
    scrollBarTo(fits, 500, 60);
    expect(fits.scrollLeft).toBe(0);
  });
});

describe("listChange", () => {
  function Chips() {
    const [items, setItems] = useState(["a", "b", "c"]);
    return (
      <ul>
        {items.map((k) => (
          <ViewTransition key={k} {...LIST_ITEM}>
            <li>
              <button type="button" onClick={() => listChange(() => setItems((xs) => xs.filter((x) => x !== k)))}>
                {k}
              </button>
            </li>
          </ViewTransition>
        ))}
      </ul>
    );
  }

  // React calls document.startViewTransition only where it exists; jsdom has none.
  function stubViewTransitions() {
    const calls: string[][] = [];
    const doc = document as unknown as Record<string, unknown>;
    doc.startViewTransition = ({ update, types }: { update: () => unknown; types?: string[] }) => {
      calls.push([...(types ?? [])]);
      const done = Promise.resolve().then(update);
      return { ready: done, finished: done, updateCallbackDone: done, skipTransition: () => {}, types: new Set(types) };
    };
    Object.defineProperty(document, "fonts", { configurable: true, value: { status: "loaded", ready: Promise.resolve() } });
    Object.defineProperty(document.documentElement, "getAnimations", { configurable: true, value: () => [] });
    Object.defineProperty(document.documentElement, "animate", { configurable: true, value: () => ({ cancel: () => {} }) });
    return calls;
  }

  afterEach(() => {
    delete (document as unknown as Record<string, unknown>).startViewTransition;
    delete (document as unknown as Record<string, unknown>).fonts;
    delete (document.documentElement as unknown as Record<string, unknown>).getAnimations;
    delete (document.documentElement as unknown as Record<string, unknown>).animate;
    document.documentElement.style.viewTransitionName = "";
  });

  it("marks the browser's view transition as a list change", async () => {
    const calls = stubViewTransitions();
    render(<Chips />);
    await act(async () => screen.getByRole("button", { name: "b" }).click());
    expect(calls).toEqual([["list-change"]]);
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["a", "c"]);
  });

  it("applies the change where the browser has no View Transitions", async () => {
    render(<Chips />);
    expect("startViewTransition" in document).toBe(false);
    await act(async () => screen.getByRole("button", { name: "b" }).click());
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["a", "c"]);
  });
});
