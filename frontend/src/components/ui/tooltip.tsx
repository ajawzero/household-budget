import * as RadixTooltip from "@radix-ui/react-tooltip";
import { type MouseEvent, type PointerEvent, type ReactNode, useRef, useState } from "react";

interface TooltipProps {
  content: ReactNode;
  /** One focusable or hoverable element. */
  children: ReactNode;
  side?: "top" | "bottom" | "left" | "right";
  /** Also opens and closes on a tap or click, for a note a phone must reach
   * (phones have no hover). The child must be a button. */
  tap?: boolean;
}

/** Extra detail on hover and keyboard focus. A touch never opens it, so it is
 * never the only place a value lives, unless `tap` makes it open on a tap too. */
export function Tooltip({ content, children, side = "top", tap = false }: TooltipProps) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  // Radix closes a tooltip on pointer down and on click. With `tap`, a tap
  // toggles it instead, and a tap on the trigger is not a tap outside.
  const tapTrigger = tap
    ? {
        ref: trigger,
        onPointerDown: (e: PointerEvent) => e.preventDefault(),
        onClick: (e: MouseEvent) => {
          e.preventDefault();
          setOpen((o) => !o);
        },
      }
    : {};
  // Own provider, so a tooltip works anywhere without app setup.
  return (
    <RadixTooltip.Provider delayDuration={200}>
      <RadixTooltip.Root {...(tap ? { open, onOpenChange: setOpen } : {})}>
        <RadixTooltip.Trigger asChild {...tapTrigger}>
          {children}
        </RadixTooltip.Trigger>
        <RadixTooltip.Portal>
          <RadixTooltip.Content
            side={side}
            sideOffset={6}
            collisionPadding={8}
            onPointerDownOutside={(e) => {
              if (trigger.current?.contains(e.target as Node)) e.preventDefault();
            }}
            // Fades in on the first hover. Moving between tooltips ("instant-open") does not replay it.
            className="z-50 max-w-xs origin-(--radix-tooltip-content-transform-origin) rounded-lg bg-primary px-3 py-2 text-xs text-on-primary shadow-pop data-[state=closed]:animate-fade-out data-[state=delayed-open]:animate-pop-in"
          >
            {content}
          </RadixTooltip.Content>
        </RadixTooltip.Portal>
      </RadixTooltip.Root>
    </RadixTooltip.Provider>
  );
}
