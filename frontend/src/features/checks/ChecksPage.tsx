import { useQuery } from "@tanstack/react-query";
import { ChevronLeft } from "lucide-react";
import { Link, useSearchParams } from "react-router";
import { PageHeader } from "../../components/shell/PageHeader";
import { Card, CardHeader } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorBanner } from "../../components/ui/error-banner";
import { Skeleton } from "../../components/ui/skeleton";
import { TableScroll } from "../../components/ui/table-scroll";
import { ApiError, type CashflowCheck, type Checks, type CheckStatus, getChecks, queryKeys } from "../../lib/api";
import { moneyLine, P4Checks } from "./P4Checks";
import { cn } from "../../lib/cn";
import { formatDate, formatMoney, formatMonth, formatRunDate } from "../../lib/format";

// Statement checks (PLAN 8.2): the app's numbers next to the chat's for one run.

/** One line on the page: a total, a part or a cash flow number. */
export interface CheckRow {
  key: string;
  label: string;
  app: number | null;
  chat: number | null;
  diff: number | null;
  status: CheckStatus;
  /** Totals are set in bold. */
  total?: boolean;
}

export const STATUS_TEXT: Record<CheckStatus, string> = {
  match: "Match",
  differs: "Differs",
  app_only: "App only",
  chat_only: "Chat only",
};

const STATUS_TONE: Record<CheckStatus, string> = {
  match: "bg-tint-good text-good",
  differs: "bg-tint-bad text-bad",
  app_only: "bg-subtle text-fg-secondary",
  chat_only: "bg-subtle text-fg-secondary",
};

export function StatusChip({ status }: { status: CheckStatus }) {
  return (
    <span className={cn("inline-block whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium", STATUS_TONE[status])}>
      {STATUS_TEXT[status]}
    </span>
  );
}

/** Net worth, assets and liabilities, then each part. */
export function balanceRows(data: Checks): CheckRow[] {
  const totals: CheckRow[] = [
    { key: "net_worth", label: "Net worth", ...line(data.net_worth), total: true },
    { key: "assets", label: "Assets", ...line(data.assets), total: true },
    { key: "liabilities", label: "Liabilities", ...line(data.liabilities), total: true },
  ];
  return [...totals, ...data.parts.map((p) => ({ key: `part-${p.key}`, label: p.label, ...line(p) }))];
}

/** Income, spending and saved and invested for each month the chat reported. Partial months say so. */
export function cashflowRows(months: CashflowCheck[]): CheckRow[] {
  return months.flatMap((m) => {
    const name = `${formatMonth(m.month)}${m.complete ? "" : " (partial)"}`;
    return [
      { key: `${m.month}-income`, label: `${name} income`, ...line(m.income) },
      { key: `${m.month}-spending`, label: `${name} spending`, ...line(m.spending) },
      { key: `${m.month}-saved`, label: `${name} saved and invested`, ...line(m.saved_invested) },
    ];
  });
}

/** Each plan group of one month, app vs chat. */
export function groupRows(month: CashflowCheck): CheckRow[] {
  return month.by_group.map((g) => ({ key: `${month.month}-${g.key}`, label: g.label, ...line(g) }));
}

function line(l: { app_cents: number | null; chat_cents: number | null; diff_cents: number | null; status: CheckStatus }) {
  return { app: l.app_cents, chat: l.chat_cents, diff: l.diff_cents, status: l.status };
}

/** "All 9 lines match." or "2 of 9 lines differ." */
export function checkSummary(rows: CheckRow[]): string {
  const differ = rows.filter((r) => r.status === "differs").length;
  const oneSide = rows.filter((r) => r.status === "app_only" || r.status === "chat_only").length;
  const lines = rows.length === 1 ? "line" : "lines";
  let text =
    differ === 0
      ? oneSide === 0
        ? `All ${rows.length} ${lines} match.`
        : `No differences in ${rows.length} ${lines}.`
      : `${differ} of ${rows.length} ${lines} ${differ === 1 ? "differs" : "differ"}.`;
  if (oneSide > 0) text += ` ${oneSide} ${oneSide === 1 ? "line is" : "lines are"} on one side only.`;
  return text;
}

/** "+$12.34" or "−$12.34". Empty when one side is missing. */
export function diffText(cents: number | null): string {
  if (cents === null) return "";
  if (cents === 0) return "$0.00";
  return `${cents > 0 ? "+" : ""}${formatMoney(cents, { cents: true })}`;
}

function money(cents: number | null, empty = "None"): string {
  return cents === null ? empty : formatMoney(cents, { cents: true });
}

/** A table on wide screens, a stacked list on phones. */
function CheckTable({
  rows,
  caption,
  labelHeader,
  appEmpty = "None",
}: {
  rows: CheckRow[];
  caption: string;
  labelHeader: string;
  /** Text for a missing app number. */
  appEmpty?: string;
}) {
  return (
    <>
      <TableScroll className="hidden md:block" label={caption}>
        <table className="data-table compact">
          <caption className="sr-only">{caption}</caption>
          <thead>
            <tr>
              <th scope="col">{labelHeader}</th>
              <th scope="col" className="num">
                App
              </th>
              <th scope="col" className="num">
                Chat
              </th>
              <th scope="col" className="num">
                Difference
              </th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className={cn(r.total && "font-semibold")}>
                <th scope="row" className={cn("text-fg", !r.total && "font-normal")}>
                  {r.label}
                </th>
                <td className={cn("num", r.app === null && "text-fg-secondary")}>{money(r.app, appEmpty)}</td>
                <td className={cn("num", r.chat === null && "text-fg-secondary")}>{money(r.chat)}</td>
                <td className={cn("num", r.status === "differs" && "text-bad")}>{diffText(r.diff)}</td>
                <td>
                  <StatusChip status={r.status} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      <ul className="m-0 flex list-none flex-col p-0 md:hidden" aria-label={caption}>
        {rows.map((r) => (
          <li key={r.key} className="border-b border-line py-3 first:pt-0 last:border-b-0 last:pb-0">
            <div className="flex items-center justify-between gap-3">
              <span className={cn("min-w-0 truncate text-base text-fg", r.total && "font-semibold")}>{r.label}</span>
              <StatusChip status={r.status} />
            </div>
            <dl className="m-0 mt-1 grid grid-cols-3 gap-2 text-sm">
              <PhoneCell label="App" value={money(r.app, appEmpty)} faint={r.app === null} />
              <PhoneCell label="Chat" value={money(r.chat)} faint={r.chat === null} />
              <PhoneCell label="Difference" value={diffText(r.diff) || "None"} faint={r.diff === null} bad={r.status === "differs"} />
            </dl>
          </li>
        ))}
      </ul>
    </>
  );
}

function PhoneCell({ label, value, faint, bad }: { label: string; value: string; faint: boolean; bad?: boolean }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-fg-secondary">{label}</dt>
      <dd className={cn("m-0 truncate tabular", faint ? "text-fg-secondary" : "text-fg", bad && "text-bad")}>{value}</dd>
    </div>
  );
}

function LoadingCard() {
  return (
    <Card>
      <div role="status" aria-label="Loading" className="flex flex-col gap-3">
        <Skeleton className="h-5 w-1/3" />
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-4 w-1/2" />
      </div>
    </Card>
  );
}

function BackLink() {
  return (
    <Link to="/statements" className="mb-1 inline-flex min-h-11 items-center gap-1 text-sm md:min-h-0">
      <ChevronLeft size={16} aria-hidden="true" />
      Statements
    </Link>
  );
}

export function ChecksPage() {
  const [params] = useSearchParams();
  const runId = params.get("run_id") ?? undefined;
  const checks = useQuery({ queryKey: queryKeys.checks(runId), queryFn: () => getChecks(runId) });
  const notFound = checks.error instanceof ApiError && checks.error.status === 404;

  return (
    <>
      <PageHeader title="Checks" />
      <BackLink />
      <div className="flex max-w-4xl flex-col gap-4 pb-8">
        {checks.isPending && <LoadingCard />}
        {checks.isError && !notFound && (
          <ErrorBanner message="Could not load the checks." onRetry={() => void checks.refetch()} retrying={checks.isFetching} />
        )}
        {notFound && (
          <Card>
            <EmptyState title={runId ? "This run has no net worth numbers." : "Nothing to check yet."}>
              Checks compare the app with the statement chat. They show up once a monthly run with net worth numbers
              is imported.
            </EmptyState>
          </Card>
        )}
        {checks.data && <ChecksBody data={checks.data} />}
      </div>
    </>
  );
}

function ChecksBody({ data }: { data: Checks }) {
  const balances = balanceRows(data);
  const flows = cashflowRows(data.cashflow);
  const appFlowMissing = data.cashflow.some((m) => m.income.app_cents === null || m.spending.app_cents === null);
  return (
    <>
      <Card>
        <CardHeader
          title="Net worth"
          subtitle={`From the ${formatRunDate(data.run_id)} update · Balances as of ${formatDate(data.as_of)}`}
        />
        <p className="m-0 mb-4 text-sm text-fg-secondary">
          The app's numbers next to the chat's, from statements only (no price estimates). A difference of $1 or less
          counts as a match. {checkSummary(balances)}
        </p>
        <CheckTable rows={balances} caption={`Net worth checks as of ${formatDate(data.as_of)}`} labelHeader="Line" />
      </Card>
      {flows.length > 0 && (
        <Card>
          <CardHeader
            title="Cash flow"
            subtitle={`Spending here leaves out saved and invested. ${data.latest_complete_month ? `Complete through ${formatMonth(data.latest_complete_month)}.` : "No month is complete yet."}`}
          />
          <p className="m-0 mb-4 text-sm text-fg-secondary">{checkSummary(flows)}</p>
          <CheckTable
            rows={flows}
            caption="Cash flow checks by month"
            labelHeader="Month"
            appEmpty={appFlowMissing ? "Not yet" : "None"}
          />
          {data.cashflow.map((m) => (
            <MonthDetails key={m.month} month={m} />
          ))}
        </Card>
      )}
      <P4Checks data={data} />
      {data.notes.length > 0 && (
        <Card>
          <CardHeader title="Notes" className="mb-2" />
          <ul className="m-0 flex list-disc flex-col gap-2 pl-5 text-sm text-fg">
            {data.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </Card>
      )}
    </>
  );
}

/** One month's groups and the parts of its numbers, folded away under the table. */
function MonthDetails({ month }: { month: CashflowCheck }) {
  const groups = groupRows(month);
  const parts = [
    { label: "Counted on a payment basis", cents: month.payment_basis_cents },
    { label: "Card spending with no statement", cents: month.card_no_statement_cents },
    { label: "Uncategorized, counted as spending", cents: month.uncategorized_cents },
    { label: "Waiting on reimbursement, left out", cents: month.reimbursable_cents },
  ].filter((p) => p.cents !== 0);
  if (groups.length === 0 && parts.length === 0 && month.notes.length === 0 && month.edited_out_count === 0) return null;
  return (
    <details className="mt-4 border-t border-line pt-3">
      <summary className="min-h-11 cursor-pointer text-sm font-medium text-fg md:min-h-0">
        {formatMonth(month.month)} by group{month.complete ? "" : " (partial month)"}
        {groups.length > 0 && <span className="font-normal text-fg-secondary"> · {checkSummary(groups)}</span>}
      </summary>
      <div className="mt-3 flex flex-col gap-3">
        {groups.length > 0 && <CheckTable rows={groups} caption={`${formatMonth(month.month)} spending by group`} labelHeader="Group" />}
        {(parts.length > 0 || month.edited_out_count > 0) && (
          <ul className="m-0 flex list-none flex-col gap-1 p-0 text-sm">
            {parts.map((p) => (
              <li key={p.label} className="flex justify-between gap-3">
                <span className="text-fg-secondary">{p.label}</span>
                <span className="tabular text-fg">{moneyLine(p.cents)}</span>
              </li>
            ))}
            {month.edited_out_count > 0 && (
              <li className="flex justify-between gap-3">
                <span className="text-fg-secondary">Rows hidden or excluded by edits</span>
                <span className="tabular text-fg">{month.edited_out_count}</span>
              </li>
            )}
          </ul>
        )}
        {month.notes.length > 0 && (
          <ul className="m-0 flex list-disc flex-col gap-1 pl-5 text-sm text-fg">
            {month.notes.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        )}
      </div>
    </details>
  );
}
