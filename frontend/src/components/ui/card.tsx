import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "../../lib/cn";

/** White card on the page. Radius 12, soft shadow, no border in light mode. */
export function Card({ className, ...rest }: HTMLAttributes<HTMLElement>) {
  return <section className={cn("min-w-0 rounded-card bg-card shadow-card p-5 md:p-6", className)} {...rest} />;
}

interface CardHeaderProps {
  /** Small caps label, like "NET WORTH". */
  eyebrow?: ReactNode;
  title?: ReactNode;
  subtitle?: ReactNode;
  /** Controls on the right (pickers, buttons). */
  actions?: ReactNode;
  /** Extra classes for the actions box. */
  actionsClassName?: string;
  titleId?: string;
  className?: string;
}

export function CardHeader({ eyebrow, title, subtitle, actions, actionsClassName, titleId, className }: CardHeaderProps) {
  return (
    <header className={cn("mb-4 flex flex-wrap items-start justify-between gap-3", className)}>
      <div className="min-w-0">
        {eyebrow && <p className="eyebrow m-0">{eyebrow}</p>}
        {title && (
          <h2 id={titleId} className="m-0 text-lg font-medium text-fg">
            {title}
          </h2>
        )}
        {subtitle && <p className="m-0 text-sm text-fg-secondary">{subtitle}</p>}
      </div>
      {actions && <div className={cn("flex flex-wrap items-center gap-2", actionsClassName)}>{actions}</div>}
    </header>
  );
}
