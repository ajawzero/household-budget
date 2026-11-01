import type { ReactNode } from "react";
import { ChevronRight, CircleAlert } from "lucide-react";
import { Link } from "react-router";
import { EstBadge } from "../../../components/money/EstBadge";
import { Money } from "../../../components/money/Money";
import { Card, CardHeader } from "../../../components/ui/card";
import { formatDate, formatTimestamp } from "../../../lib/format";
import { useValuations, type ValuationsOut } from "./api";

type IndexStatus = ValuationsOut["index"];

const ERRORS: Record<string, string> = {
  rate_limited: "FHFA asked the app to slow down.",
  server: "The FHFA site had a problem.",
  not_found: "The FHFA file was not found.",
  bad_response: "The FHFA file looked wrong.",
  timeout: "The FHFA site took too long.",
  network: "Could not reach the FHFA site.",
};

/** "+3.1% a year" from milli-percent. */
function trendText(milli: number): string {
  return `${milli >= 0 ? "+" : "-"}${(Math.abs(milli) / 1000).toFixed(1)}% a year`;
}

/** "+1.8%" between two index levels. */
function indexChange(base: number, now: number): string {
  const pct = ((now - base) / base) * 100;
  return `${pct >= 0 ? "+" : "-"}${Math.abs(pct).toFixed(1)}%`;
}

/**
 * Homes in the account drawer: the last real valuation, the estimate now and
 * the FHFA index behind it, in words (the est. tooltip does not open on a tap).
 */
export function HomePanel({ accountKey }: { accountKey: string }) {
  const q = useValuations();
  if (q.isPending) return <p className="m-0 text-sm text-fg-secondary">Loading the home value…</p>;
  if (q.isError) return <p className="m-0 text-sm text-bad">Could not load the home value.</p>;
  const item = q.data.items.find((i) => i.account_key === accountKey);
  if (!item) return null;
  const idx = q.data.index;
  const home = item.inputs.home;
  const lines: [string, ReactNode][] = [];
  if (item.real) {
    lines.push([
      item.real.label.charAt(0).toUpperCase() + item.real.label.slice(1),
      <>
        <Money cents={item.real.value_cents} /> on {formatDate(item.real.as_of)}
      </>,
    ]);
    if (item.real.source_note) lines.push(["Based on", item.real.source_note]);
  }
  if (item.estimate) {
    lines.push([
      "Estimate now",
      <span className="inline-flex items-center gap-1.5">
        <Money cents={item.estimate.value_cents} />
        <EstBadge note={item.estimate.note} />
      </span>,
    ]);
  }
  if (idx.label && idx.through_label) lines.push(["Index", `${idx.label}, through ${idx.through_label}`]);
  if (home) lines.push(["Index since the valuation", indexChange(home.index_base_micro, home.index_now_micro)]);
  lines.push(["After the newest quarter", "Holds steady until FHFA publishes the next quarter"]);
  return (
    <section aria-labelledby={`home-${accountKey}`} className="flex flex-col gap-3">
      <h3 id={`home-${accountKey}`} className="eyebrow m-0">
        Home value
      </h3>
      {lines.length > 0 && (
        <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
          {lines.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-fg-secondary">{k}</dt>
              <dd className="m-0 min-w-0 break-words text-fg">{v}</dd>
            </div>
          ))}
        </dl>
      )}
      <p className="m-0 text-sm text-fg-secondary">
        {item.estimate
          ? item.estimate.note
          : (item.note ??
            "The chat checks comps once a quarter. Between checks the value moves with the FHFA index at each month end.")}
      </p>
      <p className="m-0 text-xs text-fg-secondary">{idx.attribution}</p>
      <Link
        to="/settings/vehicles#home-index"
        className="inline-flex min-h-11 items-center gap-1 self-start text-sm no-underline md:min-h-0"
      >
        Index status
        <ChevronRight size={16} aria-hidden="true" />
      </Link>
    </section>
  );
}

/** Settings: where the home estimate's index stands, with the FHFA attribution. */
export function IndexStatusCard() {
  const q = useValuations();
  const idx: IndexStatus | undefined = q.data?.index;
  return (
    <Card id="home-index">
      <CardHeader
        title="Home value index"
        subtitle="Between the quarterly comps the home value moves with this index. Only the public index file is downloaded. Nothing about the home is sent."
      />
      {q.isPending && <p className="m-0 text-sm text-fg-secondary">Loading…</p>}
      {q.isError && <p className="m-0 text-sm text-bad">Could not load the index status.</p>}
      {idx && <IndexFacts idx={idx} />}
    </Card>
  );
}

function IndexFacts({ idx }: { idx: IndexStatus }) {
  const items: [string, ReactNode][] = [
    ["Series", idx.label ?? "No index values yet"],
    ["Through", idx.through_label ?? "None yet"],
    ["Past year", idx.trend_milli_pct !== null ? trendText(idx.trend_milli_pct) : "Not known yet"],
    ["After that", "Holds steady until the next quarter"],
    ["Schedule", idx.schedule],
    ["Last success", idx.last_success_at ? formatTimestamp(idx.last_success_at) : "Never"],
  ];
  const failed = idx.last_run_status === "failed";
  return (
    <div className="flex flex-col gap-3">
      <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
        {items.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-fg-secondary">{k}</dt>
            <dd className="m-0 text-fg">{v}</dd>
          </div>
        ))}
      </dl>
      {failed && (
        <p className="m-0 flex items-start gap-1.5 text-sm text-warn">
          <CircleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
          The last download failed{idx.last_fetch_at ? ` on ${formatTimestamp(idx.last_fetch_at)}` : ""}.{" "}
          {(idx.error && ERRORS[idx.error]) ?? ""} The app keeps the last values and tries again tomorrow.
        </p>
      )}
      <p className="m-0 text-xs text-fg-secondary">{idx.attribution}</p>
    </div>
  );
}
