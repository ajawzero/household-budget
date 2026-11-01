import type { InputHTMLAttributes } from "react";

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
}

/** Label above a 16px input (iOS does not zoom at 16px). */
export function Field({ label, ...input }: FieldProps) {
  return (
    <label className="flex flex-col gap-1.5 text-sm font-medium text-fg">
      {label}
      <input
        {...input}
        className="h-11 rounded-lg border border-line bg-card px-3 text-base font-normal text-fg outline-none focus:border-accent focus-visible:outline-2 focus-visible:outline-accent"
      />
    </label>
  );
}
