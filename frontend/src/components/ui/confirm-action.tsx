import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { Button } from "./button";

/**
 * Focus for an inline confirm: the confirm button when it shows, and the
 * button that asked when it goes again (Cancel). Give each its ref.
 */
export function useConfirmFocus(asking: boolean) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const was = useRef(asking);
  useEffect(() => {
    if (asking && !was.current) confirmRef.current?.focus();
    if (!asking && was.current) triggerRef.current?.focus();
    was.current = asking;
  }, [asking]);
  return { confirmRef, triggerRef };
}

/**
 * A button that asks first, inline, never in a browser dialog. The confirm
 * button takes focus, and the question is its description, so a screen
 * reader reads both. Cancel puts focus back on the first button.
 */
export function ConfirmAction({
  ask,
  question,
  confirmLabel,
  busy,
  onConfirm,
  size = "sm",
  triggerClassName,
}: {
  /** The first button's content. */
  ask: ReactNode;
  question: string;
  confirmLabel: string;
  busy: boolean;
  onConfirm: () => void;
  size?: "sm" | "md";
  triggerClassName?: string;
}) {
  const id = useId();
  const [asking, setAsking] = useState(false);
  const { confirmRef, triggerRef } = useConfirmFocus(asking);
  if (asking) {
    return (
      <span className="flex flex-wrap items-center gap-2">
        <span id={`${id}-question`} className="text-sm text-fg">
          {question}
        </span>
        <Button ref={confirmRef} size={size} variant="primary" disabled={busy} aria-describedby={`${id}-question`} onClick={onConfirm}>
          {confirmLabel}
        </Button>
        <Button size={size} onClick={() => setAsking(false)}>
          Cancel
        </Button>
      </span>
    );
  }
  return (
    <Button ref={triggerRef} size={size} className={triggerClassName} disabled={busy} onClick={() => setAsking(true)}>
      {ask}
    </Button>
  );
}
