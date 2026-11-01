import { CircleAlert, CircleCheck, CircleMinus, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { Money } from "../../../components/money/Money";
import { Badge } from "../../../components/ui/badge";
import { Card, CardHeader } from "../../../components/ui/card";
import { EmptyState } from "../../../components/ui/empty-state";
import { TableScroll } from "../../../components/ui/table-scroll";
import type { UtilityDetail, UtilityYoyRow, UtilityYoyValue } from "../../../lib/api";
import { cn } from "../../../lib/cn";
import { formatDate, formatMoney, formatShortDate } from "../../../lib/format";
import {
  billStatus,
  changeText,
  chargesSummary,
  formatPerDegreeDay,
  formatUsage,
  periodText,
  PLAIN_HEADERS,
  SERVICE_NAMES,
  servicesInRange,
  showCharges,
  type StatusView,
  unitName,
  usageSummary,
  yoyTone,
} from "./model";

const TONE_TEXT = { good: "text-good", bad: "text-bad", neutral: "text-fg-secondary" } as const;

/** The change as an arrow, a percent and spoken words. Lower use is good. */
export function YoyChange({ change, polarity }: { change: string | null; polarity: "lower_is_better" | "none" }) {
  const c = changeText(change);
  if (!c) return null;
  const tone = yoyTone(change, polarity);
  return (
    <span className={cn("whitespace-nowrap font-medium", TONE_TEXT[tone])} data-tone={tone}>
      {c.arrow && <span aria-hidden="true">{c.arrow} </span>}
      {c.dir && <span className="sr-only">{c.dir} </span>}
      {c.text}
      {tone !== "neutral" && <span className="sr-only">{tone === "good" ? ", better" : ", worse"}</span>}
    </span>
  );
}

interface YoyColumn {
  key: string;
  header: string;
  pick: (r: UtilityYoyRow) => UtilityYoyValue | null | undefined;
  format: (v: number | null) => string;
  polarity: "lower_is_better" | "none";
}

const plain = (v: number | null) => (v === null ? "None" : String(v));
const money = (v: number | null) => (v === null ? "None" : formatMoney(v, { cents: true }));

/**
 * The year over year columns for a utility, in the plan's order: days, then
 * per metered service its use and use per day (gas adds per degree day),
 * heating degree days when the bills have them, then the total. A line in
 * another unit than the newest one shows as None.
 */
export function yoyColumns(detail: UtilityDetail): YoyColumn[] {
  const cols: YoyColumn[] = [{ key: "days", header: "Days", pick: (r) => r.days, format: plain, polarity: "none" }];
  const services = servicesInRange(detail.bills);
  const hasHdd = detail.yoy.some((r) => r.heating_degree_days.value !== null);
  let perHdd: YoyColumn | null = null;
  for (const s of services) {
    const unit = unitName(s.unit);
    const name = services.filter((x) => x.unit === s.unit).length > 1 ? `${SERVICE_NAMES[s.service]} ${unit}` : unit;
    const svc = (r: UtilityYoyRow) => r.services.find((x) => x.service === s.service && x.unit === s.unit);
    cols.push(
      { key: `${s.service}-use`, header: name, pick: (r) => svc(r)?.usage_micro, format: formatUsage, polarity: "lower_is_better" },
      { key: `${s.service}-day`, header: `${name} per day`, pick: (r) => svc(r)?.per_day_micro, format: formatUsage, polarity: "lower_is_better" },
    );
    if (s.service === "gas" && hasHdd) {
      perHdd = {
        key: "gas-hdd",
        header: `${unit} per degree day`,
        pick: (r) => svc(r)?.per_degree_day_micro,
        format: formatPerDegreeDay,
        polarity: "lower_is_better",
      };
    }
  }
  if (hasHdd) cols.push({ key: "hdd", header: "Heating degree days", pick: (r) => r.heating_degree_days, format: plain, polarity: "none" });
  if (perHdd) cols.push(perHdd);
  cols.push({ key: "total", header: "Total", pick: (r) => r.total_cents, format: money, polarity: "lower_is_better" });
  return cols;
}

/** This bill's value, then last year's and the change. With no bill last year, the row header says so once. */
function YoyCell({ v, col, hasLastYear }: { v: UtilityYoyValue | null | undefined; col: YoyColumn; hasLastYear: boolean }) {
  if (!v) return <td className="num text-fg-secondary">None</td>;
  return (
    <td className="num align-top">
      <span className="block text-fg">{col.format(v.value)}</span>
      {hasLastYear && (
      <span className="block text-xs text-fg-secondary">
        {v.last_year === null ? (
          "None last year"
        ) : (
          <>
            <span className="sr-only">last year </span>
            {col.format(v.last_year)}
            {v.change_pct !== null && " · "}
            <YoyChange change={v.change_pct} polarity={col.polarity} />
          </>
        )}
      </span>
      )}
    </td>
  );
}

/** Each bill against last year's closest bill, newest first. */
export function YoyCard({ detail, loading }: { detail: UtilityDetail | undefined; loading: boolean }) {
  const rows = detail ? [...detail.yoy].reverse() : [];
  const cols = detail ? yoyColumns(detail) : [];
  const hdd = cols.some((c) => c.key === "hdd");
  return (
    <Card>
      <CardHeader
        eyebrow="Year over year"
        title="Each bill against last year"
        subtitle={`Under each number: last year's bill for the same period, then the change. Less use counts as better.${
          hdd ? " Heating degree days measure how cold the period was, so gas per degree day compares use with the weather." : ""
        }`}
      />
      {loading && !detail ? (
        <p className="m-0 text-sm text-fg-secondary">Loading…</p>
      ) : rows.length === 0 ? (
        <EmptyState title="No bills in this range." />
      ) : (
        <TableScroll label="Each bill against last year">
          <table className={cn("data-table compact", PLAIN_HEADERS)}>
            <caption className="sr-only">Year over year by bill. Each cell has this bill, last year's bill and the change.</caption>
            <thead>
              <tr>
                <th scope="col">Bill period</th>
                {cols.map((c) => (
                  <th key={c.key} scope="col" className="num min-w-20 whitespace-normal">
                    {c.header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.bill_key}>
                  <th scope="row" className="align-top font-normal">
                    <span className="block whitespace-nowrap text-fg">{periodText(r.period_start, r.period_end)}</span>
                    <span className="block whitespace-nowrap text-xs text-fg-secondary">
                      {r.last_year_period_start && r.last_year_period_end
                        ? `vs ${periodText(r.last_year_period_start, r.last_year_period_end)}`
                        : "No bill last year"}
                    </span>
                  </th>
                  {cols.map((c) => (
                    <YoyCell key={c.key} v={c.pick(r)} col={c} hasLastYear={r.last_year_bill_key !== null} />
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
    </Card>
  );
}

const STATUS_ICON: Record<StatusView["tone"], ReactNode> = {
  good: <CircleCheck size={14} aria-hidden="true" className="mt-0.5 shrink-0" />,
  warn: <TriangleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0" />,
  bad: <CircleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0" />,
  neutral: <CircleMinus size={14} aria-hidden="true" className="mt-0.5 shrink-0" />,
};

const STATUS_CLASS: Record<StatusView["tone"], string> = {
  good: "text-good",
  warn: "text-warn",
  bad: "text-bad",
  neutral: "text-fg-secondary",
};

export function StatusLine({ status }: { status: StatusView }) {
  return (
    <p className={cn("m-0 flex items-start gap-1.5 text-sm", STATUS_CLASS[status.tone])}>
      {STATUS_ICON[status.tone]}
      <span>{status.text}</span>
    </p>
  );
}

/** Every bill in the range, newest first, with its paid status. */
export function BillsCard({ detail, today, loading }: { detail: UtilityDetail | undefined; today: string; loading: boolean }) {
  const bills = detail ? [...detail.bills].reverse() : [];
  const warnings = detail?.warnings ?? [];
  return (
    <Card>
      <CardHeader
        eyebrow="Bills"
        title={detail ? `${bills.length} ${bills.length === 1 ? "bill" : "bills"}` : "Bills"}
        subtitle="A bill is paid when a payment tagged with this utility comes within 40 days of the bill date."
      />
      {loading && !detail ? (
        <p className="m-0 text-sm text-fg-secondary">Loading…</p>
      ) : bills.length === 0 ? (
        <EmptyState title="No bills in this range." />
      ) : (
        <ul className="-mx-5 m-0 list-none p-0 md:-mx-6" aria-label="Bills">
          {bills.map((b) => {
            const notes = warnings.filter((w) => w.bill_key === b.bill_key);
            return (
              <li key={b.bill_key} className="flex items-start justify-between gap-3 border-t border-line px-5 py-3 first:border-t-0 md:px-6">
                <div className="min-w-0">
                  <p className="m-0 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm font-medium text-fg">
                    <span>{periodText(b.period_start, b.period_end)}</span>
                    {b.estimated_read && <Badge>Estimated read</Badge>}
                  </p>
                  <p className="m-0 text-sm text-fg-secondary">
                    Billed {formatShortDate(b.bill_date)} · {b.days} days{usageSummary(b) ? ` · ${usageSummary(b)}` : ""}
                  </p>
                  {showCharges(b) && <p className="m-0 text-sm text-fg-secondary">{chargesSummary(b)}</p>}
                  {b.credits.length > 0 && (
                    <p className="m-0 text-sm text-fg-secondary">
                      {b.credits.map((c) => `${c.label} ${formatMoney(c.amount_cents, { cents: true })}`).join(" · ")}
                    </p>
                  )}
                  <StatusLine status={billStatus(b, today)} />
                  {notes.map((w) => (
                    <p key={w.rule} className="m-0 mt-0.5 flex items-start gap-1.5 text-sm text-warn">
                      <TriangleAlert size={14} aria-hidden="true" className="mt-0.5 shrink-0" />
                      <span>{w.message}</span>
                    </p>
                  ))}
                  {b.note && <p className="m-0 text-sm text-fg-secondary">{b.note}</p>}
                </div>
                <span className="shrink-0 text-sm font-medium text-fg">
                  <Money cents={b.total_cents} withCents />
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

/** Four quick numbers for the picked utility. */
export function SummaryCard({ detail, today, loading }: { detail: UtilityDetail | undefined; today: string; loading: boolean }) {
  const s = detail?.summary;
  const last = detail?.bills[detail.bills.length - 1];
  const late = !!s?.next_bill_late;
  const passed = !!s?.next_bill_date && s.next_bill_date < today;
  const tiles: { key: string; label: string; value: ReactNode; note: ReactNode }[] = s
    ? [
        {
          key: "last",
          label: "Last bill",
          value: <Money cents={s.last_total_cents} withCents proportional />,
          note: last ? periodText(last.period_start, last.period_end) : `Billed ${formatDate(s.last_bill_date)}`,
        },
        {
          key: "avg",
          label: "12-month average",
          value: <Money cents={s.avg_bill_cents} proportional empty="None yet" />,
          note: s.bills_in_avg
            ? `Over ${s.bills_in_avg} ${s.bills_in_avg === 1 ? "bill" : "bills"}, ${formatMoney(s.total_12m_cents)} in all`
            : "Not enough bills yet",
        },
        {
          key: "next",
          label: "Next bill",
          value: s.next_bill_date ? formatShortDate(s.next_bill_date) : "Not known",
          note: late
            ? `Late. Not in the ${formatShortDate(s.last_import_date)} import.`
            : passed
              ? "Not in yet. It comes with the next import."
              : s.next_bill_date
                ? "Expected, from the usual gap"
                : "Needs two bills",
        },
        {
          key: "credits",
          label: "Credits in range",
          value: <Money cents={detail ? -detail.credits_total_cents : 0} withCents proportional />,
          note: `${detail?.credits.length ?? 0} ${detail?.credits.length === 1 ? "credit" : "credits"}`,
        },
      ]
    : [];
  return (
    <Card aria-label="Summary">
      {loading && !s ? (
        <div role="status" aria-label="Loading" className="h-16 animate-pulse rounded-lg bg-subtle" />
      ) : (
        <dl className="m-0 grid grid-cols-2 gap-x-4 gap-y-5 lg:grid-cols-4">
          {tiles.map((t) => (
            <div key={t.key} className="min-w-0">
              <dt className="text-sm text-fg-secondary">{t.label}</dt>
              <dd className="m-0 text-2xl font-semibold text-fg">{t.value}</dd>
              <dd className={cn("m-0 text-sm", t.key === "next" && late ? "text-warn" : "text-fg-secondary")}>{t.note}</dd>
            </div>
          ))}
        </dl>
      )}
    </Card>
  );
}
