import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ListChecks } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useParams } from "react-router";
import { PageHeader } from "../../components/shell/PageHeader";
import { Button } from "../../components/ui/button";
import { Card, CardHeader } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { TableScroll } from "../../components/ui/table-scroll";
import { ApiError, getImportRun, getImports, type ImportRun, queryKeys, retryImport, type RunError } from "../../lib/api";
import { useMe } from "../../lib/auth";
import { cn } from "../../lib/cn";
import { formatDate, formatTimestamp } from "../../lib/format";
import { CoverageCard } from "../statements/CoverageCard";
import { OrphanedEditsCard } from "../transactions/OrphanedEdits";

// Counts worth a column. Other numeric counts still show in the detail view.
const MAIN_COUNTS = ["inserted", "updated", "removed", "superseded", "drift"] as const;

// A waiting run is not imported: it uses keys that no earlier imported run has.
const STATUS_LABELS: Record<string, string> = {
  waiting: "not imported (waiting for earlier runs)",
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

function StatusPill({ status }: { status: string }) {
  const tone =
    status === "failed"
      ? "bg-tint-bad text-bad"
      : status === "waiting"
        ? "bg-tint-warn text-warn"
        : status === "imported"
          ? "bg-tint-good text-good"
          : "bg-subtle text-fg-secondary";
  return <span className={cn("inline-block rounded-full px-2 py-0.5 text-xs font-medium", tone)}>{statusLabel(status)}</span>;
}

function numericCounts(counts: Record<string, unknown>): [string, number][] {
  return Object.entries(counts).filter((e): e is [string, number] => typeof e[1] === "number");
}

function CountsSummary({ counts }: { counts: Record<string, unknown> }) {
  const parts = MAIN_COUNTS.filter((k) => typeof counts[k] === "number" && counts[k] !== 0).map(
    (k) => `${counts[k] as number} ${k}`,
  );
  return <>{parts.length ? parts.join(", ") : "No changes"}</>;
}

function ErrorText({ error }: { error: RunError | null }) {
  if (!error) return null;
  return (
    <span>
      {error.message ?? error.rule}
      {error.loc ? ` (at ${error.loc})` : ""}
    </span>
  );
}

export function RunsTable({ runs }: { runs: ImportRun[] }) {
  if (runs.length === 0) return <EmptyState title="No runs imported yet." />;
  return (
    <TableScroll label="Imports">
      <table className="data-table">
        <caption className="sr-only">Import runs, newest first</caption>
        <thead>
          <tr>
            <th scope="col">Run</th>
            <th scope="col">Mode</th>
            <th scope="col">Version</th>
            <th scope="col">Status</th>
            <th scope="col">Finished</th>
            <th scope="col">Changes</th>
            <th scope="col" className="num">
              Warnings
            </th>
            <th scope="col">Error</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.run_id}>
              <td className="whitespace-nowrap">
                <Link to={`/statements/${encodeURIComponent(run.run_id)}`} className="inline-flex min-h-11 items-center md:min-h-0">
                  {run.run_id}
                </Link>
              </td>
              <td>{run.mode}</td>
              <td>{run.schema_version}</td>
              <td>
                <StatusPill status={run.status} />
              </td>
              <td className="whitespace-nowrap">{formatTimestamp(run.finished_at ?? run.started_at)}</td>
              {/* Wide enough that the summary does not wrap into a tall row on phones. */}
              <td className="min-w-64">
                <CountsSummary counts={run.counts} />
              </td>
              <td className="num">{run.warning_count}</td>
              <td className="text-bad">
                <ErrorText error={run.error} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}

export function ImportsPage() {
  const runs = useQuery({ queryKey: queryKeys.imports, queryFn: getImports });
  return (
    <>
      <PageHeader
        title="Statements"
        actions={
          <Link
            to="/checks"
            className="inline-flex h-11 items-center gap-2 rounded-lg border border-line bg-card px-4 text-sm font-medium text-fg no-underline transition-colors duration-100 hover:bg-hover md:h-9"
          >
            <ListChecks size={16} aria-hidden="true" />
            Checks
          </Link>
        }
      />
      <div className="flex flex-col gap-4 pb-8">
        <CoverageCard />
        <Card>
          <CardHeader title="Imports" subtitle="Each run from the monthly statement chat, newest first." />
          {runs.isPending && <p className="text-fg-secondary">Loading…</p>}
          {runs.isError && <p className="text-bad">{runs.error.message}</p>}
          {runs.data && <RunsTable runs={runs.data.runs} />}
        </Card>
        <OrphanedEditsCard />
      </div>
    </>
  );
}

const RETRYABLE = new Set(["failed", "waiting"]);

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader title={title} className="mb-2" />
      {children}
    </Card>
  );
}

export function ImportDetailPage() {
  const { runId = "" } = useParams();
  const me = useMe();
  const queryClient = useQueryClient();
  const run = useQuery({ queryKey: queryKeys.importRun(runId), queryFn: () => getImportRun(runId) });
  const retry = useMutation({
    mutationFn: () => retryImport(runId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.imports }),
  });

  const back = (
    <Link to="/statements" className="inline-flex min-h-11 items-center gap-1 text-sm md:min-h-0">
      <ChevronLeft size={16} aria-hidden="true" />
      All imports
    </Link>
  );

  if (run.isPending)
    return (
      <>
        <PageHeader title={`Run ${runId}`} />
        <p className="text-fg-secondary">Loading…</p>
      </>
    );
  if (run.isError) {
    const notFound = run.error instanceof ApiError && run.error.status === 404;
    return (
      <>
        <PageHeader title="Import run" />
        {back}
        <p className="text-bad">{notFound ? "No run with that id." : run.error.message}</p>
      </>
    );
  }

  const data = run.data;
  const isAdmin = me.data?.role === "admin";

  return (
    <>
      <PageHeader title={`Run ${data.run_id}`} />
      <div className="mb-3">{back}</div>
      <div className="flex flex-col gap-4">
        <Card>
          <dl className="m-0 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-6 gap-y-2 text-sm">
            <dt className="text-fg-secondary">Status</dt>
            <dd className="m-0">
              <StatusPill status={data.status} />
            </dd>
            <dt className="text-fg-secondary">Mode</dt>
            <dd className="m-0">{data.mode}</dd>
            <dt className="text-fg-secondary">Format version</dt>
            <dd className="m-0">{data.schema_version}</dd>
            <dt className="text-fg-secondary">Started</dt>
            <dd className="m-0">{formatTimestamp(data.started_at)}</dd>
            <dt className="text-fg-secondary">Finished</dt>
            <dd className="m-0">{formatTimestamp(data.finished_at)}</dd>
            {data.error && (
              <>
                <dt className="text-fg-secondary">Error</dt>
                <dd className="m-0 text-bad wrap-anywhere">
                  <ErrorText error={data.error} />
                </dd>
              </>
            )}
          </dl>
          {isAdmin && RETRYABLE.has(data.status) && (
            <div className="mt-4 flex flex-wrap items-center gap-3">
              <Button variant="primary" onClick={() => retry.mutate()} disabled={retry.isPending}>
                Retry import
              </Button>
              {retry.isSuccess && <span role="status">Retry started ({retry.data.status}).</span>}
              {retry.isError && (
                <span role="alert" className="text-bad">
                  {retry.error.message}
                </span>
              )}
            </div>
          )}
        </Card>

        <Section title="Counts">
          {numericCounts(data.counts).length === 0 ? (
            <p className="m-0 text-fg-secondary">No counts.</p>
          ) : (
            <TableScroll label="Counts">
              <table className="data-table compact max-w-sm">
                <tbody>
                  {numericCounts(data.counts).map(([key, value]) => (
                    <tr key={key}>
                      <th scope="row" className="font-normal wrap-anywhere">
                        {key}
                      </th>
                      <td className="num">{value}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
          )}
        </Section>

        <Section title="Warnings">
          {data.warnings.length === 0 ? (
            <p className="m-0 text-fg-secondary">No warnings.</p>
          ) : (
            <TableScroll label="Warnings">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">Where</th>
                    <th scope="col">Rule</th>
                    <th scope="col">Message</th>
                  </tr>
                </thead>
                <tbody>
                  {data.warnings.map((w, i) => (
                    <tr key={`${w.rule}-${w.loc}-${i}`}>
                      <td>{w.loc}</td>
                      <td>{w.rule}</td>
                      <td>{w.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
          )}
        </Section>

        <Section title="Statements">
          {data.statements.length === 0 ? (
            <p className="m-0 text-fg-secondary">No statements in this run.</p>
          ) : (
            <TableScroll label="Statements in this run">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">Account</th>
                    <th scope="col">Period end</th>
                    <th scope="col" className="num">
                      Transactions
                    </th>
                    <th scope="col">Reconciled</th>
                    <th scope="col">Current</th>
                  </tr>
                </thead>
                <tbody>
                  {data.statements.map((s) => (
                    <tr key={s.statement_key}>
                      <td>{s.account_key}</td>
                      <td className="whitespace-nowrap">{formatDate(s.period_end)}</td>
                      <td className="num">{s.txn_count ?? ""}</td>
                      <td>{s.reconciled === null ? "" : s.reconciled ? "Yes" : "No"}</td>
                      <td>{s.is_current ? "Yes" : "Replaced"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
          )}
        </Section>
      </div>
    </>
  );
}
