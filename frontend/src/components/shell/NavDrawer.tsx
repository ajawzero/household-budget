import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "../../lib/cn";
import { focusPanel, OVERLAY_MOTION, sheetMotion } from "../ui/drawer";
import { Sidebar } from "./Sidebar";

/** The full nav as a left sheet: phone menu and the tablet rail's expand. */
export function NavDrawer({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className={cn("fixed inset-0 z-40 bg-overlay", OVERLAY_MOTION)} />
        <Dialog.Content
          aria-describedby={undefined}
          onOpenAutoFocus={focusPanel}
          className={cn(
            "fixed inset-y-0 left-0 z-50 w-[min(288px,85vw)] bg-page pt-[env(safe-area-inset-top)] shadow-pop outline-none",
            sheetMotion("left"),
          )}
        >
          <Dialog.Title className="sr-only">Menu</Dialog.Title>
          <Dialog.Close
            aria-label="Close menu"
            className="absolute right-2 top-2 z-10 inline-flex h-11 w-11 items-center justify-center rounded-lg text-fg-secondary hover:bg-active"
          >
            <X size={20} aria-hidden="true" />
          </Dialog.Close>
          <Sidebar onNavigate={() => onOpenChange(false)} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
