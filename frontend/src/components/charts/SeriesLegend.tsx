import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

/**
 * Legend hover (mouse only, an extra): the hovered series stays and the others
 * step back. The tooltip and "Show the numbers" still carry every value.
 */
export function seriesFade(focus: string | null, key: string): string {
  return cn("transition-opacity duration-(--dur-base)", focus !== null && focus !== key && "opacity-25");
}

/** A legend row that sets the focused series while the mouse is on it. */
export function LegendItem({
  id,
  onFocus,
  children,
}: {
  id: string;
  onFocus: (id: string | null) => void;
  children: ReactNode;
}) {
  return (
    <li className="flex items-center gap-1.5" onMouseEnter={() => onFocus(id)} onMouseLeave={() => onFocus(null)}>
      {children}
    </li>
  );
}
