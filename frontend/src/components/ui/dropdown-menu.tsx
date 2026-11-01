// Thin styled wrappers over Radix DropdownMenu. Keyboard and focus come from Radix.
import * as Menu from "@radix-ui/react-dropdown-menu";
import { Check } from "lucide-react";
import type { ComponentProps } from "react";
import { cn } from "../../lib/cn";

export const DropdownMenu = Menu.Root;
export const DropdownMenuTrigger = Menu.Trigger;
export const DropdownMenuRadioGroup = Menu.RadioGroup;

export function DropdownMenuContent({ className, sideOffset = 6, ...rest }: ComponentProps<typeof Menu.Content>) {
  return (
    <Menu.Portal>
      <Menu.Content
        sideOffset={sideOffset}
        className={cn(
          "z-50 min-w-56 origin-(--radix-dropdown-menu-content-transform-origin) rounded-xl bg-card p-1 text-sm text-fg shadow-pop",
          "data-[state=open]:animate-pop-in data-[state=closed]:animate-fade-out",
          className,
        )}
        {...rest}
      />
    </Menu.Portal>
  );
}

const ITEM =
  "flex h-11 cursor-default select-none items-center gap-2 rounded-lg px-3 outline-none md:h-9 data-[highlighted]:bg-active data-[disabled]:opacity-50";

export function DropdownMenuItem({ className, ...rest }: ComponentProps<typeof Menu.Item>) {
  return <Menu.Item className={cn(ITEM, className)} {...rest} />;
}

export function DropdownMenuRadioItem({ className, children, ...rest }: ComponentProps<typeof Menu.RadioItem>) {
  return (
    <Menu.RadioItem className={cn(ITEM, "pr-8", className)} {...rest}>
      <span className="flex-1">{children}</span>
      <Menu.ItemIndicator className="absolute right-3 inline-flex">
        <Check size={16} strokeWidth={2.5} aria-hidden="true" className="text-accent-text" />
      </Menu.ItemIndicator>
    </Menu.RadioItem>
  );
}

export function DropdownMenuLabel({ className, ...rest }: ComponentProps<typeof Menu.Label>) {
  return <Menu.Label className={cn("px-3 pb-1 pt-2 text-xs font-medium text-fg-secondary", className)} {...rest} />;
}

export function DropdownMenuSeparator() {
  return <Menu.Separator className="my-1 h-px bg-line" />;
}
