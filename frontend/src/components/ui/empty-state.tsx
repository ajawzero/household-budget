import type { ReactNode } from "react";

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
}

/** Calm message for "nothing here yet". */
export function EmptyState({ icon, title, children }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-10 text-center">
      {icon && <div className="text-fg-secondary">{icon}</div>}
      <p className="m-0 text-base font-medium text-fg">{title}</p>
      {children && <div className="max-w-md text-sm text-fg-secondary">{children}</div>}
    </div>
  );
}
