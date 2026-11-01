import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ChevronDown, Info } from "lucide-react";
import { AreaChart, type AreaPoint, ChartCard, type ChartColumn } from "../../components/charts";
import { ESTIMATE_SWATCH_STYLE, PARTIAL_SWATCH_STYLE, estimateRuns } from "../../components/charts/AreaChart";
import { Delta } from "../../components/money/Delta";
import { EstBadge, estimatesNote } from "../../components/money/EstBadge";
import { Money } from "../../components/money/Money";
import { Button } from "../../components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";
import { RangePicker, rangeLabel } from "../../components/ui/range-picker";
import { Tooltip } from "../../components/ui/tooltip";
import { getNetworthSeries, queryKeys, SERIES_KEYS, type RangeKey, type SeriesKey } from "../../lib/api";
import { formatDate, formatMoney, formatPct } from "../../lib/format";
import { useMediaQuery } from "../../lib/media";
import { isOwedGroup, SERIES_LABELS } from "./model";

interface NetWorthCardProps {
  range: RangeKey;
  series: SeriesKey;
  onRangeChange: (range: RangeKey) => void;
  onSeriesChange: (series: SeriesKey) => void;
}

/** Owed series (credit cards, loans) are drawn as positive amounts owed. */
export function seriesPoints(
  points: { date: string; value_cents: number; partial: boolean; estimated?: boolean }[],
  series: SeriesKey,
): AreaPoint[] {
  const flip = isOwedGroup(series);
  return points.map((p) => ({
    date: p.date,
    value: flip ? -p.value_cents : p.value_cents,
    partial: p.partial,
    estimated: p.estimated ?? false,
  }));
}

const COLUMNS: ChartColumn<AreaPoint>[] = [
  { header: "Date", cell: (p) => formatDate(p.date) },
  { header: "Value", cell: (p) => formatMoney(p.value), numeric: true },
  { header: "Partial", cell: (p) => (p.partial ? "Partial" : "") },
  { header: "Estimate", cell: (p) => (p.estimated ? "Includes estimates" : "") },
];

/** The full-width card on top: hero value, change, series and range pickers, area chart. */
export function NetWorthCard({ range, series, onRangeChange, onSeriesChange }: NetWorthCardProps) {
  const wide = useMediaQuery("(min-width: 768px)");
  const q = useQuery({
    queryKey: queryKeys.networthSeries(range, series),
    queryFn: () => getNetworthSeries({ range, series }),
    placeholderData: keepPreviousData,
  });
  const data = q.data;
  const owed = isOwedGroup(series);
  const label = SERIES_LABELS[series];
  const points = data ? seriesPoints(data.points, series) : undefined;
  const anyPartial = !!points?.some((p) => p.partial);
  const estimated = data?.estimated_accounts ?? [];
  const tail = points ? estimateRuns(points, "tail")[0] : undefined;
  const period = `${rangeLabel(range)} change`;

  const endShown = data ? (owed ? -data.end_cents : data.end_cents) : null;
  const changeShown = data ? (owed ? -data.change_cents : data.change_cents) : 0;
  const ariaLabel = data
    ? `${label}${owed ? " owed" : ""} ${changeShown >= 0 ? "up" : "down"} ${formatMoney(Math.abs(changeShown))}` +
      `${data.change_pct ? ` (${formatPct(data.change_pct.replace(/^-/, ""))})` : ""}, ${rangeLabel(range).toLowerCase()}. ` +
      `Now ${formatMoney(endShown ?? 0)}.`
    : label;

  // Hero value with the change on the same line (wraps under it on phones).
  const hero = data ? (
    <span className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <span className="text-3xl font-semibold text-fg">
        <Money cents={endShown} proportional />
        {owed && <span className="ml-2 text-base font-normal text-fg-secondary">owed</span>}
        {estimated.length > 0 && <EstBadge note={estimatesNote(estimated)} className="ml-2 align-middle" />}
      </span>
      <Delta
        cents={data.change_cents}
        display={owed ? "owed" : "signed"}
        pct={data.change_pct}
        period={period}
      />
    </span>
  ) : null;

  return (
    <ChartCard
      eyebrow={<EyebrowInfo label={label} />}
      subtitle={hero}
      controls={
        <>
          <SeriesPicker value={series} onChange={onSeriesChange} />
          {/* Full width on phones, under the series picker. */}
          <RangePicker value={range} onChange={onRangeChange} className="w-full md:w-auto *:flex-1 md:*:flex-none" />
        </>
      }
      controlsClassName="w-full md:w-auto"
      ariaLabel={ariaLabel}
      data={points}
      columns={COLUMNS}
      rowKey={(p) => p.date}
      tableCaption={`${label}${owed ? " owed" : ""} by day. Partial means some accounts had no balance yet or were out of date.`}
      loading={q.isPending}
      refreshing={q.isPlaceholderData}
      error={q.isError ? "Could not load the chart." : null}
      onRetry={() => void q.refetch()}
      emptyText="No balances in this range yet."
      height={wide ? 280 : 180}
      footer={
        <>
          {tail && estimated.length > 0 && (
            <p className="m-0 mt-2 flex items-center gap-2 text-sm text-fg-secondary">
              <span aria-hidden="true" className="inline-block h-3 w-4 shrink-0 rounded-sm border border-line-strong" style={ESTIMATE_SWATCH_STYLE} />
              Shaded days include estimates: {estimated.map((a) => a.display_name).join(", ")}.
            </p>
          )}
          {anyPartial && (
            <p className="m-0 mt-2 flex items-center gap-2 text-sm text-fg-secondary">
              <span aria-hidden="true" className="inline-block h-3 w-4 shrink-0 rounded-sm border border-line-strong" style={PARTIAL_SWATCH_STYLE} />
              Hatched days are partial: some accounts had no balance yet or were out of date.
            </p>
          )}
          {data && data.points[0] && data.points[0].value_cents !== data.start_cents && (
            <p className="m-0 mt-2 text-sm text-fg-secondary">
              The change counts each account from its first balance in the range, so it can differ from where the line starts.
            </p>
          )}
        </>
      }
    >
      {points && (
        <AreaChart
          points={points}
          label={owed ? `${label} owed` : label}
          height={wide ? 280 : 180}
          yAxisWidth={wide ? 56 : 44}
          startLine={!wide}
          markPartial
          estimateBands="tail"
          monthTicks={range === "YTD" || range === "1Y" || range === "3Y" || range === "ALL"}
          maxTicks={wide ? 8 : 4}
        />
      )}
    </ChartCard>
  );
}

/** The eyebrow with an info tip: what the line adds up and what partial means. A tap opens it too. */
function EyebrowInfo({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      {label}
      <Tooltip
        tap
        content={
          <>
            Each day adds up the latest balance of every account in net worth. Hatched days are partial: some
            accounts had no balance yet or were out of date. The change counts each account from its first balance
            in the range.
          </>
        }
      >
        <button
          type="button"
          aria-label="About this chart"
          className="tap-target inline-flex h-6 w-6 items-center justify-center rounded-full text-fg-secondary hover:text-fg"
        >
          <Info size={14} aria-hidden="true" />
        </button>
      </Tooltip>
    </span>
  );
}

function SeriesPicker({ value, onChange }: { value: SeriesKey; onChange: (s: SeriesKey) => void }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="sm" aria-label={`Chart: ${SERIES_LABELS[value]}`}>
          {SERIES_LABELS[value]}
          <ChevronDown size={16} aria-hidden="true" className="text-fg-secondary" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuRadioGroup value={value} onValueChange={(v) => onChange(v as SeriesKey)}>
          {SERIES_KEYS.map((k) => (
            <DropdownMenuRadioItem key={k} value={k}>
              {SERIES_LABELS[k]}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
