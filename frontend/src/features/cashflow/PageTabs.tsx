import { type KeyboardEvent, useRef } from "react";
import { cn } from "../../lib/cn";
import { INDICATOR_MOTION, indicatorStyle, useIndicator } from "../../lib/indicator";

export interface TabItem<T extends string> {
  value: T;
  label: string;
}

interface PageTabsProps<T extends string> {
  value: T;
  tabs: readonly TabItem<T>[];
  onChange: (value: T) => void;
  label: string;
  /** Prefix for tab and panel ids. */
  idPrefix: string;
}

/**
 * Page tabs next to the title, with a teal underline on the active tab
 * (Monarch's layout, our accent). Arrow keys move between tabs.
 * The underline slides to a new tab; until it is measured, the tab draws its own.
 */
export function PageTabs<T extends string>({ value, tabs, onChange, label, idPrefix }: PageTabsProps<T>) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const { ref, box, animate } = useIndicator<HTMLDivElement>('[aria-selected="true"]', value);
  function onKey(e: KeyboardEvent, index: number) {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = (index + step + tabs.length) % tabs.length;
    const tab = tabs[next];
    if (!tab) return;
    onChange(tab.value);
    refs.current[next]?.focus();
  }
  return (
    <div ref={ref} role="tablist" aria-label={label} className="relative flex gap-5">
      {box && (
        <span
          aria-hidden="true"
          className={cn("absolute left-0 top-0 bg-accent", animate && INDICATOR_MOTION)}
          // Over the tab's own 2px bottom border.
          style={indicatorStyle({ ...box, top: box.top + box.height - 2, height: 2 })}
        />
      )}
      {tabs.map((t, i) => {
        const active = t.value === value;
        return (
          <button
            key={t.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="tab"
            id={`${idPrefix}-tab-${t.value}`}
            aria-selected={active}
            aria-controls={`${idPrefix}-panel`}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(t.value)}
            onKeyDown={(e) => onKey(e, i)}
            className={cn(
              "tap-target -mb-px inline-flex h-11 items-center border-b-2 text-sm font-medium transition-colors duration-100 md:h-9",
              active ? "text-fg" : "text-fg-secondary hover:text-fg",
              active && !box ? "border-accent" : "border-transparent",
            )}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}
