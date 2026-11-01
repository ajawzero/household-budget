import * as ToggleGroup from "@radix-ui/react-toggle-group";
import { cn } from "../../lib/cn";
import { INDICATOR_MOTION, indicatorStyle, useIndicator, useKeepInView } from "../../lib/indicator";

export interface SegmentOption<T extends string> {
  value: T;
  label: string;
  /** Screen reader name when the label is short, like "1M". */
  ariaLabel?: string;
}

interface SegmentedControlProps<T extends string> {
  value: T;
  options: readonly SegmentOption<T>[];
  onChange: (value: T) => void;
  /** Names the group for screen readers. */
  label: string;
  size?: "sm" | "md";
  /** "track": white pill on a gray track. "pill": no track, the chosen option gets a gray pill. */
  variant?: "track" | "pill";
  /** Short labels that never wrap, with less side padding on phones (range pickers). */
  compact?: boolean;
  className?: string;
}

/**
 * One selected option out of a few. Arrow keys move between options.
 * The pill slides to the new option; until it is measured, the option draws its own.
 */
export function SegmentedControl<T extends string>({
  value,
  options,
  onChange,
  label,
  size = "sm",
  variant = "track",
  compact = false,
  className,
}: SegmentedControlProps<T>) {
  const { ref, box, animate } = useIndicator<HTMLDivElement>('[data-state="on"]', value);
  useKeepInView(ref, box);
  return (
    <ToggleGroup.Root
      ref={ref}
      type="single"
      value={value}
      // Radix sends "" when the selected item is clicked again. Keep the choice.
      onValueChange={(v) => v && onChange(v as T)}
      aria-label={label}
      className={cn(
        "relative inline-flex max-w-full overflow-x-auto",
        variant === "track" ? "rounded-lg bg-subtle p-0.5" : "gap-1",
        className,
      )}
    >
      {box && (
        <span
          aria-hidden="true"
          className={cn(
            "absolute left-0 top-0",
            variant === "track" ? "rounded-md bg-card shadow-card" : "rounded-full bg-subtle",
            animate && INDICATOR_MOTION,
          )}
          style={indicatorStyle(box)}
        />
      )}
      {options.map((o) => (
        <ToggleGroup.Item
          key={o.value}
          value={o.value}
          aria-label={o.ariaLabel}
          className={cn(
            "relative min-w-11 shrink-0 font-medium text-fg-secondary transition-colors duration-100 hover:text-fg data-[state=on]:text-fg md:min-w-0",
            compact ? "whitespace-nowrap px-2 md:px-3" : "px-3",
            variant === "track" ? "rounded-md" : "rounded-full",
            !box &&
              (variant === "track"
                ? "data-[state=on]:bg-card data-[state=on]:shadow-card"
                : "data-[state=on]:bg-subtle"),
            size === "sm" ? "h-11 text-[13px] md:h-7" : "h-11 text-sm md:h-9",
          )}
        >
          {o.label}
        </ToggleGroup.Item>
      ))}
    </ToggleGroup.Root>
  );
}
