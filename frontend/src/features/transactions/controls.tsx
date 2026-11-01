import type { ReactNode } from "react";
import type { CategoriesResponse } from "../../lib/api";
import { cn } from "../../lib/cn";
import { categoryOption } from "./model";

/** Text inputs and selects: 44px and 16px on phones (no iOS zoom), 36px and 14px from tablets up. */
export const INPUT =
  "h-11 w-full min-w-0 rounded-lg border border-line bg-card px-3 text-base text-fg placeholder:text-fg-faint md:h-9 md:text-sm";

interface CategorySelectProps {
  id?: string;
  value: string;
  onChange: (key: string) => void;
  categories: CategoriesResponse | undefined;
  /** Text for an empty first option, like "Pick a category". Left out when not set. */
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  "aria-label"?: string;
  "aria-describedby"?: string;
}

/** Native select grouped by category group, with each emoji in the label. */
export function CategorySelect({ id, value, onChange, categories, placeholder, disabled, className, ...aria }: CategorySelectProps) {
  return (
    <select
      id={id}
      className={cn(INPUT, className)}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      {...aria}
    >
      {placeholder !== undefined && <option value="">{placeholder}</option>}
      {categories?.groups.map((g) => (
        <optgroup key={g.group_key} label={g.name}>
          {g.categories.map((c) => (
            <option key={c.category_key} value={c.category_key}>
              {categoryOption(c.name, c.emoji)}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
  );
}

interface SwitchRowProps {
  label: string;
  hint?: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  /** Shown when the value comes from an edit, not the import. */
  edited?: boolean;
  onReset?: () => void;
}

/** A labeled on/off switch (a checkbox with role="switch"). */
export function SwitchRow({ label, hint, checked, onChange, disabled, edited, onReset }: SwitchRowProps) {
  return (
    // The label fills the 44px row, so a tap anywhere on it flips the switch.
    <div className="flex min-h-11 items-stretch gap-3">
      <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-3 py-1">
        <input
          type="checkbox"
          role="switch"
          checked={checked}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked)}
          className="peer sr-only"
        />
        <span
          aria-hidden="true"
          className={cn(
            "relative inline-flex h-6 w-10 shrink-0 items-center rounded-full transition-colors duration-100",
            "peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-accent",
            "peer-disabled:opacity-50",
            // Off: an outlined track and a dark knob, both 3:1 or more on the card (WCAG 1.4.11).
            checked ? "border-2 border-primary bg-primary" : "border-2 border-fg-faint bg-card",
          )}
        >
          <span
            className={cn(
              "inline-block h-4 w-4 rounded-full transition-transform duration-100",
              checked ? "translate-x-[18px] bg-on-primary" : "translate-x-0.5 bg-fg-secondary",
            )}
          />
        </span>
        <span className="min-w-0">
          <span className="block text-sm text-fg">{label}</span>
          {hint && <span className="block text-xs text-fg-secondary">{hint}</span>}
        </span>
      </label>
      {edited && onReset && (
        <button type="button" onClick={onReset} className="min-h-11 shrink-0 text-xs text-accent-text underline md:min-h-0">
          Use imported{" "}<span className="sr-only">value for {label}</span>
        </button>
      )}
    </div>
  );
}

/** A small "Edited" marker for values that come from a household edit. */
export function EditedBadge({ className }: { className?: string }) {
  return (
    <span className={cn("inline-block rounded-full bg-subtle px-2 py-0.5 text-xs font-medium text-fg-secondary", className)}>
      Edited
    </span>
  );
}
