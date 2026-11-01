import { useState } from "react";
import { flushSync } from "react-dom";
import { Card, CardHeader } from "../../components/ui/card";
import { ConfirmAction } from "../../components/ui/confirm-action";
import { ApiError, getMe, startDemo } from "../../lib/api";
import { markModeChanged, stampDemoStart } from "../../lib/demo";
import { replacePage } from "../../lib/reload";
import { PageCover } from "./PageCover";

const NOT_AVAILABLE = "Demo is not available right now.";
const NO_STORAGE = "Demo needs this browser's site storage, and it is full or blocked here.";

/** Settings card for an admin outside demo: start the fake household in this browser. */
export function DemoCard({ available }: { available: boolean }) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  async function start() {
    setBusy(true);
    setProblem(null);
    // The stamp goes first: a tab that misses the message must still see the change.
    const undo = stampDemoStart();
    if (!undo) {
      setProblem(NO_STORAGE);
      setBusy(false);
      return;
    }
    try {
      await startDemo();
    } catch (err) {
      undo();
      // A start that failed after the real session went leaves this browser signed out.
      const me = await getMe().catch(() => undefined);
      if (me === null) {
        replacePage("/login");
        return;
      }
      setProblem(err instanceof ApiError ? err.message : NOT_AVAILABLE);
      setBusy(false);
      return;
    }
    markModeChanged("demo");
    // Blank first, in the DOM before the page goes, so the back-forward cache keeps no
    // numbers from before demo.
    flushSync(() => setStarting(true));
    replacePage("/");
  }

  return (
    <Card>
      <CardHeader
        title="Demo mode"
        subtitle="Shows a fake household. Your data stays hidden. To get back, sign in again with your password."
      />
      {available ? (
        <div className="flex flex-col gap-3">
          <ConfirmAction
            ask="Start demo"
            question="Start demo in this browser?"
            confirmLabel="Start demo"
            busy={busy}
            onConfirm={() => void start()}
            size="md"
            triggerClassName="self-start"
          />
          <p className="m-0 text-sm text-fg-secondary">
            Before you hand this device over, make sure saved passwords here ask for Face ID or a fingerprint.
          </p>
        </div>
      ) : (
        <p className="m-0 text-sm text-fg-secondary">Demo is not available on this server.</p>
      )}
      {problem && (
        <p role="alert" className="m-0 mt-3 text-sm text-bad">
          {problem}
        </p>
      )}
      {starting && (
        <PageCover>
          <p role="status" className="m-0 text-base text-fg-secondary">
            Starting demo
          </p>
        </PageCover>
      )}
    </Card>
  );
}
