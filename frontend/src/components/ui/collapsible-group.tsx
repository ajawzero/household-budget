import * as Collapsible from "@radix-ui/react-collapsible";
import { ChevronDown } from "lucide-react";
import { type ReactNode, useState } from "react";
import { cn } from "../../lib/cn";

interface CollapsibleGroupProps {
  title: ReactNode;
  /** Right side of the header: change, total. Stays visible when collapsed. */
  summary?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
}

/**
 * A card whose header button folds its body away (account groups).
 * The header is one button with aria-expanded, so it works by keyboard.
 */
export function CollapsibleGroup({ title, summary, children, defaultOpen = true, className }: CollapsibleGroupProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <Collapsible.Root open={open} onOpenChange={setOpen} asChild>
      <section className={cn("rounded-card bg-card shadow-card", className)}>
        {/* Phones wrap the change under the name, so the chevron and total stay on the name line. */}
        <Collapsible.Trigger className="flex min-h-16 w-full items-start gap-3 rounded-card px-5 py-4 text-left hover:bg-hover md:items-center md:px-6">
          <ChevronDown
            aria-hidden="true"
            size={18}
            className={cn(
              "mt-[5px] shrink-0 text-fg-secondary transition-transform duration-(--dur-base) ease-(--ease-out) md:mt-0",
              !open && "-rotate-90",
            )}
          />
          <span className="min-w-0 flex-1 text-lg font-semibold text-fg">{title}</span>
          {summary && <span className="flex shrink-0 items-center gap-3">{summary}</span>}
        </Collapsible.Trigger>
        {/* The height animation clips the body, so focus rings inside draw inward. */}
        <Collapsible.Content className="overflow-hidden border-t border-line data-[state=closed]:animate-group-close data-[state=open]:animate-group-open [&_:focus-visible]:outline-offset-[-2px]">
          {children}
        </Collapsible.Content>
      </section>
    </Collapsible.Root>
  );
}
