import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../../lib/cn";

interface DrawerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  /** Right edge (account detail) or left edge (phone nav). */
  side?: "right" | "left";
  /** Panel width from the tablet size up. Phones get the full width. */
  widthClass?: string;
  /** Hide the title visually but keep it for screen readers. */
  hideTitle?: boolean;
  /** Where focus goes on open. The panel itself by default (focusPanel). */
  onOpenAutoFocus?: (event: Event) => void;
}

/**
 * Radix focuses the first button (Close) on open, which draws a focus ring
 * that looks like a stray box. Focus the panel itself instead; Tab still
 * reaches Close first, and the ring shows once the keyboard is used.
 */
export function focusPanel(event: Event) {
  event.preventDefault();
  const panel = event.currentTarget;
  if (panel instanceof HTMLElement) panel.focus();
}

/** Overlay fade. Radix waits for the exit animation before it unmounts. */
export const OVERLAY_MOTION = "data-[state=open]:animate-fade-in data-[state=closed]:animate-fade-out";

/**
 * Sheet slide. A right sheet rises from the bottom on phones (it is full
 * screen there) and slides in from the right from the tablet size up.
 * A left sheet (the nav) slides from the left at every size.
 */
export function sheetMotion(side: "right" | "left"): string {
  return side === "right"
    ? "data-[state=open]:animate-sheet-in-up data-[state=closed]:animate-sheet-out-down md:data-[state=open]:animate-sheet-in-right md:data-[state=closed]:animate-sheet-out-right"
    : "data-[state=open]:animate-sheet-in-left data-[state=closed]:animate-sheet-out-left";
}

/**
 * Side sheet on Radix Dialog: focus trap, Esc to close, focus goes back to
 * the opener. Full screen on phones.
 */
export function Drawer({
  open,
  onOpenChange,
  title,
  description,
  children,
  side = "right",
  widthClass = "md:w-[480px]",
  hideTitle = false,
  onOpenAutoFocus = focusPanel,
}: DrawerProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className={cn("fixed inset-0 z-40 bg-overlay", OVERLAY_MOTION)} />
        <Dialog.Content
          className={cn(
            "fixed inset-y-0 z-50 flex w-full flex-col bg-card shadow-pop outline-none",
            side === "right" ? "right-0" : "left-0",
            sheetMotion(side),
            widthClass,
          )}
          {...(description ? {} : { "aria-describedby": undefined })}
          onOpenAutoFocus={onOpenAutoFocus}
        >
          <div className="flex items-center gap-3 border-b border-line px-5 py-3">
            <Dialog.Title className={cn("m-0 min-w-0 flex-1 text-lg font-medium text-fg", hideTitle && "sr-only")}>
              {title}
            </Dialog.Title>
            {hideTitle && <span className="flex-1" />}
            <Dialog.Close
              className="inline-flex h-11 w-11 items-center justify-center rounded-lg text-fg-secondary hover:bg-active md:h-9 md:w-9"
              aria-label="Close"
            >
              <X size={20} aria-hidden="true" />
            </Dialog.Close>
          </div>
          {description && (
            <Dialog.Description className="m-0 px-5 pt-3 text-sm text-fg-secondary">{description}</Dialog.Description>
          )}
          <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-[max(20px,env(safe-area-inset-bottom))] pt-4">
            {children}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
