import { type ReactNode, useId, useState } from "react";
import { cn } from "../../lib/cn";
import { Button } from "../ui/button";
import { Card, CardHeader } from "../ui/card";
import { EmptyState } from "../ui/empty-state";
import { TableScroll } from "../ui/table-scroll";

export interface ChartColumn<T> {
  header: string;
  cell: (row: T) => ReactNode;
  /** Right-aligned, tabular digits. */
  numeric?: boolean;
}

interface ChartCardProps<T> {
  eyebrow?: ReactNode;
  title?: ReactNode;
  subtitle?: ReactNode;
  /** Pickers on the right of the header. */
  controls?: ReactNode;
  /** Extra classes for the controls box, like full width on phones. */
  controlsClassName?: string;
  /** One-line summary read by screen readers, like "Net worth up 4% this year". */
  ariaLabel: string;
  /** The rows behind the chart. The table view renders these same rows. */
  data: readonly T[] | undefined;
  columns: ChartColumn<T>[];
  rowKey: (row: T, index: number) => string;
  tableCaption?: string;
  /** Extra classes for the "Show the numbers" table. */
  tableClassName?: string;
  loading?: boolean;
  /** Refetching with old data on screen: hold the chart, dimmed. */
  refreshing?: boolean;
  error?: string | null;
  /** Show the error as quiet gray text, when a page-level banner already says it. */
  quietError?: boolean;
  /** Adds a Retry button under the error. */
  onRetry?: () => void;
  emptyText?: string;
  /** Chart height, so the skeleton holds the same space. */
  height?: number;
  /** Legend or notes under the chart. */
  footer?: ReactNode;
  className?: string;
  /** The chart itself. */
  children: ReactNode;
}

/**
 * Every chart sits in one of these. It owns the header, loading, empty and
 * error states, the role="img" summary, and the "Show the numbers" table.
 */
export function ChartCard<T>({
  eyebrow,
  title,
  subtitle,
  controls,
  controlsClassName,
  ariaLabel,
  data,
  columns,
  rowKey,
  tableCaption,
  tableClassName,
  loading = false,
  refreshing = false,
  error,
  quietError = false,
  onRetry,
  emptyText = "Nothing to show yet.",
  height = 240,
  footer,
  className,
  children,
}: ChartCardProps<T>) {
  const [showTable, setShowTable] = useState(false);
  const tableId = useId();
  const hasData = !!data && data.length > 0;

  let body: ReactNode;
  if (!data && loading) {
    body = (
      <div role="status" aria-label="Loading">
        <div style={{ height }} className="animate-pulse rounded-lg bg-subtle" />
      </div>
    );
  } else if (error && !data) {
    body = quietError ? (
      <p className="m-0 py-8 text-center text-sm text-fg-secondary">{error}</p>
    ) : (
      <div role="alert" className="flex flex-col items-center gap-3 py-8 text-center">
        <p className="m-0 text-sm text-bad">{error}</p>
        {onRetry && (
          <Button size="sm" onClick={onRetry}>
            Retry
          </Button>
        )}
      </div>
    );
  } else if (!hasData) {
    body = <EmptyState title={emptyText} />;
  } else {
    body = (
      <>
        <div
          role="img"
          aria-label={ariaLabel}
          className={cn("min-w-0 animate-fade-in transition-opacity duration-(--dur-base)", refreshing && "opacity-60")}
        >
          {children}
        </div>
        {footer}
      </>
    );
  }

  return (
    <Card className={className}>
      <CardHeader eyebrow={eyebrow} title={title} subtitle={subtitle} actions={controls} actionsClassName={controlsClassName} />
      {body}
      {hasData && (
        <div className="mt-3">
          <button
            type="button"
            className="inline-flex h-11 items-center rounded-lg px-1 text-sm font-medium text-accent-text hover:underline md:h-8"
            aria-expanded={showTable}
            aria-controls={tableId}
            onClick={() => setShowTable((v) => !v)}
          >
            {showTable ? "Hide the numbers" : "Show the numbers"}
          </button>
          <TableScroll id={tableId} hidden={!showTable} className="mt-2" label={tableCaption ?? `${ariaLabel}, numbers`}>
            {showTable && (
              <table className={cn("data-table compact", tableClassName)}>
                {tableCaption && <caption>{tableCaption}</caption>}
                <thead>
                  <tr>
                    {columns.map((c) => (
                      <th key={c.header} scope="col" className={c.numeric ? "num" : undefined}>
                        {c.header}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {data.map((row, i) => (
                    <tr key={rowKey(row, i)}>
                      {columns.map((c, ci) =>
                        ci === 0 ? (
                          <th key={c.header} scope="row" className={cn("font-normal", c.numeric && "num")}>
                            {c.cell(row)}
                          </th>
                        ) : (
                          <td key={c.header} className={c.numeric ? "num" : undefined}>
                            {c.cell(row)}
                          </td>
                        ),
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </TableScroll>
        </div>
      )}
    </Card>
  );
}
