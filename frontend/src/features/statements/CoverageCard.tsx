import { useQuery } from "@tanstack/react-query";
import { Check, CircleDot, Minus, TriangleAlert, X } from "lucide-react";
import { useLayoutEffect, useRef, useState } from "react";
import { Card, CardHeader } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { SegmentedControl } from "../../components/ui/segmented-control";
import { TableScroll } from "../../components/ui/table-scroll";
import { type CoverageAccount, type CoverageCell, type CoverageResponse, type CoverageStatus, getCoverage, recurringKeys } from "../../lib/api";
import { cn } from "../../lib/cn";
import { formatDate, formatMonth, formatMonthShort } from "../../lib/format";
import { ListSkeleton } from "../recurring/parts";

export const COVERAGE_TEXT: Record<CoverageStatus, string> = {
  present: "Statement closed",
  missing: "Missing",
  not_expected: "Not expected",
  tracked: "Tracked by payments",
};

/** The cell's words: status, period end and reconcile state. Also the hover text. */
export function cellText(cell: CoverageCell): string {
  let text = COVERAGE_TEXT[cell.status];
  if (cell.status === "present" && cell.period_end) text += `, period ends ${formatDate(cell.period_end)}`;
  if (cell.reconciled === false) text += ", not reconciled";
  return text;
}

function lastDay(month: string): string {
  const [y, m] = month.split("-").map(Number) as [number, number];
  const d = new Date(Date.UTC(y, m, 0)).getUTCDate();
  return `${month}-${String(d).padStart(2, "0")}`;
}

/**
 * The first month that is not complete, and the cash flow accounts holding it
 * back (their statements do not reach its last day). Null when every past
 * month is complete.
 */
export function blockingAccounts(data: CoverageResponse): { month: string; names: string[] } | null {
  const done = new Set(data.complete_months);
  const thisMonth = data.today.slice(0, 7);
  const month = data.months.find((m) => !done.has(m) && m < thisMonth);
  if (!month) return null;
  const end = lastDay(month);
  const names = data.accounts
    .filter((a) => a.in_cashflow && a.tracked_by === "statements" && a.active)
    .filter((a) => !a.covered_through || a.covered_through < end)
    .map((a) => a.name);
  return { month, names };
}

function CellIcon({ cell }: { cell: CoverageCell }) {
  const props = { size: 14, "aria-hidden": true, strokeWidth: 2.5 } as const;
  if (cell.status === "present" && cell.reconciled === false) return <TriangleAlert {...props} className="text-warn" />;
  if (cell.status === "present") return <Check {...props} className="text-good" />;
  if (cell.status === "missing") return <X {...props} className="text-bad" />;
  if (cell.status === "tracked") return <CircleDot {...props} className="text-fg-secondary" />;
  return <Minus {...props} className="text-fg-faint" />;
}

const CELL_BG: Record<CoverageStatus, string> = {
  present: "bg-tint-good",
  missing: "bg-tint-bad",
  not_expected: "",
  tracked: "bg-subtle",
};

const LEGEND: { key: string; cell: CoverageCell; text: string }[] = [
  { key: "present", cell: { month: "", period_end: null, reconciled: true, status: "present" }, text: "A statement closed this month" },
  { key: "unreconciled", cell: { month: "", period_end: null, reconciled: false, status: "present" }, text: "Closed, not reconciled" },
  { key: "missing", cell: { month: "", period_end: null, reconciled: null, status: "missing" }, text: "Missing" },
  { key: "tracked", cell: { month: "", period_end: null, reconciled: null, status: "tracked" }, text: "Tracked by payments" },
  { key: "not_expected", cell: { month: "", period_end: null, reconciled: null, status: "not_expected" }, text: "Not expected" },
];

type Scope = "cashflow" | "all";

function AccountRow({ account, months }: { account: CoverageAccount; months: string[] }) {
  const cells = new Map(account.cells.map((c) => [c.month, c]));
  return (
    <tr className={cn(!account.active && "text-fg-secondary")}>
      <th scope="row" className="sticky left-0 z-10 min-w-40 bg-card font-normal">
        <span className="block max-w-48 truncate font-medium text-fg">{account.name}</span>
        <span className="block truncate text-xs text-fg-secondary">
          {account.owner_label}
          {account.tracked_by === "payments" ? " · tracked by payments" : account.statement_cadence === "quarterly" ? " · quarterly" : ""}
          {!account.active && " · closed"}
        </span>
      </th>
      {months.map((m) => {
        const cell = cells.get(m) ?? { month: m, period_end: null, reconciled: null, status: "not_expected" as const };
        return (
          <td key={m} className="p-0.5 text-center" title={`${formatMonth(m)}: ${cellText(cell)}`}>
            <span className={cn("mx-auto inline-flex h-7 w-7 items-center justify-center rounded-md", CELL_BG[cell.status])}>
              <CellIcon cell={cell} />
              <span className="sr-only">{cellText(cell)}</span>
            </span>
          </td>
        );
      })}
    </tr>
  );
}

/** Coverage grid (PLAN 8.2, Statements): account by month, which statements are in. */
export function CoverageGrid({ data }: { data: CoverageResponse }) {
  const [scope, setScope] = useState<Scope>("cashflow");
  const accounts = scope === "cashflow" ? data.accounts.filter((a) => a.in_cashflow) : data.accounts;
  const complete = new Set(data.complete_months);
  const block = blockingAccounts(data);
  // Open on the newest months: on phones the grid scrolls sideways.
  const scroller = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, [data, scope]);
  return (
    <>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <p className="m-0 text-sm text-fg">
          {data.latest_complete_month ? `Complete through ${formatMonth(data.latest_complete_month)}.` : "No month is complete yet."}
          {block && block.names.length > 0 && (
            <span className="text-fg-secondary">
              {" "}
              {formatMonthShort(block.month)} waits on {block.names.join(", ")}.
            </span>
          )}
        </p>
        <SegmentedControl<Scope>
          label="Accounts shown"
          variant="pill"
          value={scope}
          onChange={setScope}
          options={[
            { value: "cashflow", label: "Cash flow" },
            { value: "all", label: "All accounts" },
          ]}
        />
      </div>
      {accounts.length === 0 ? (
        <EmptyState title="No accounts yet." />
      ) : (
        <TableScroll ref={scroller} label="Statement coverage">
          <table className="data-table compact w-auto">
            <caption className="sr-only">Statements by account and month</caption>
            <thead>
              <tr>
                <th scope="col" className="sticky left-0 z-10 bg-card">
                  Account
                </th>
                {data.months.map((m) => (
                  <th key={m} scope="col" className="px-1 text-center">
                    <span className={cn("block", complete.has(m) ? "text-fg" : "text-fg-secondary")}>{formatMonthShort(m)}</span>
                    <span className="sr-only">{complete.has(m) ? ", complete" : ", not complete"}</span>
                    <span
                      aria-hidden="true"
                      className={cn("mx-auto mt-1 block h-0.5 w-5 rounded-full", complete.has(m) ? "bg-accent" : "bg-transparent")}
                    />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {accounts.map((a) => (
                <AccountRow key={a.account_key} account={a} months={data.months} />
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
      <ul className="m-0 mt-3 flex list-none flex-wrap gap-x-4 gap-y-2 p-0 text-sm text-fg-secondary" aria-label="Legend">
        {LEGEND.map((l) => (
          <li key={l.key} className="flex items-center gap-1.5">
            <span className={cn("inline-flex h-5 w-5 items-center justify-center rounded", CELL_BG[l.cell.status])}>
              <CellIcon cell={l.cell} />
            </span>
            {l.text}
          </li>
        ))}
        <li className="flex items-center gap-1.5">
          <span aria-hidden="true" className="h-0.5 w-5 rounded-full bg-accent" />
          Complete month
        </li>
      </ul>
    </>
  );
}

export function CoverageCard() {
  const coverage = useQuery({ queryKey: recurringKeys.coverage(), queryFn: () => getCoverage() });
  return (
    <Card>
      <CardHeader
        title="Statement coverage"
        subtitle="A month is complete when every cash flow account has its statement through the month's last day."
      />
      {coverage.data ? (
        <CoverageGrid data={coverage.data} />
      ) : coverage.isPending ? (
        <ListSkeleton rows={5} />
      ) : (
        <p className="m-0 text-sm text-bad">{coverage.error?.message ?? "Could not load coverage."}</p>
      )}
    </Card>
  );
}
