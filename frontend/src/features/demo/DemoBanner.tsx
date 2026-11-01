import { useState } from "react";
import { Button } from "../../components/ui/button";
import { useMe } from "../../lib/auth";
import { leaveDemo } from "../../lib/demo";
import { formatDate } from "../../lib/format";

/** The line at the top of every page in demo, with Leave. Nothing outside demo. */
export function DemoBanner() {
  const me = useMe().data;
  const [leaving, setLeaving] = useState(false);
  if (me?.demo !== true) return null;

  function leave() {
    setLeaving(true);
    void leaveDemo();
  }

  return (
    <div className="mx-auto w-full max-w-[1440px] px-4 pt-3 md:px-6 lg:px-8">
      <div
        role="status"
        className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-card border border-demo-line bg-demo px-4 py-2"
      >
        <p className="m-0 min-w-0 flex-1 text-sm text-fg">
          <span className="font-semibold text-demo-fg">Demo mode.</span> Fake data as of {formatDate(me.household_today)}.
        </p>
        <Button size="sm" disabled={leaving} onClick={leave}>
          {leaving ? "Leaving…" : "Leave"}
        </Button>
      </div>
    </div>
  );
}
