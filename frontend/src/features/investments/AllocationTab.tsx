import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { ChartCard, type ChartColumn } from "../../components/charts/ChartCard";
import { EstBadge } from "../../components/money/EstBadge";
import { Money } from "../../components/money/Money";
import { ErrorBanner } from "../../components/ui/error-banner";
import { formatMoney, formatPct } from "../../lib/format";
import { type AllocationOut, type AllocationSlice, classLabel, getAllocation, investmentKeys } from "./api";
import { type ClassColor, classColorMap, OTHER_FILL } from "./colors";

const NO_CLASS = "unclassified";

interface MixRow {
  key: string;
  account: string;
  cls: string;
  value_cents: number;
  pct: string | null;
}

const CLASS_COLUMNS: ChartColumn<AllocationSlice>[] = [
  { header: "Class", cell: (c) => classLabel(c.key, c.label) },
  { header: "Value", cell: (c) => formatMoney(c.value_cents), numeric: true },
  { header: "Share", cell: (c) => formatPct(c.pct, true), numeric: true },
];

const MIX_COLUMNS: ChartColumn<MixRow>[] = [
  { header: "Account", cell: (r) => r.account },
  { header: "Class", cell: (r) => r.cls },
  { header: "Value", cell: (r) => formatMoney(r.value_cents), numeric: true },
  { header: "Share of account", cell: (r) => formatPct(r.pct, true), numeric: true },
];

export function mixRows(d: AllocationOut): MixRow[] {
  return d.accounts.flatMap((a) =>
    a.classes.map((c) => ({
      key: `${a.account_key}:${c.key}`,
      account: a.display_name,
      cls: classLabel(c.key, c.label),
      value_cents: c.value_cents,
      pct: c.pct,
    })),
  );
}

/** Allocation (PLAN 8.2): the household's mix by asset class, then each account's mix. */
export function AllocationTab() {
  const q = useQuery({ queryKey: investmentKeys.allocation, queryFn: getAllocation });
  const d = q.data;
  const colors = d ? classColorMap(d.classes) : new Map<string, ClassColor>();
  const error = q.isError && !d ? "Not loaded." : null;
  const top = d?.classes[0];

  return (
    <div className="flex flex-col gap-4">
      {error && <ErrorBanner message="Could not load the allocation." onRetry={() => void q.refetch()} retrying={q.isFetching} />}
      <ChartCard<AllocationSlice>
        eyebrow="By asset class"
        subtitle={
          d ? (
            <>
              <span className="mt-1 inline-flex items-baseline gap-2 text-3xl font-semibold text-fg">
                <Money cents={d.total_cents} proportional />
                {d.estimated && <EstBadge className="self-center" />}
              </span>
              {d.estimated && <span className="block">Includes values estimated from market prices.</span>}
            </>
          ) : null
        }
        ariaLabel={
          d && top
            ? `Asset classes: ${d.classes.map((c) => `${classLabel(c.key, c.label)} ${formatPct(c.pct, true)}`).join(", ")}.`
            : "Asset classes"
        }
        data={d?.classes}
        columns={CLASS_COLUMNS}
        rowKey={(c) => c.key}
        tableCaption="Value by asset class, across investment and retirement accounts."
        loading={q.isPending}
        error={error}
        quietError
        emptyText="No holdings yet. They come in with brokerage and retirement statements."
        height={120}
      >
        {d && (
          <>
            <MixBar slices={d.classes} colors={colors} />
            <ul className="m-0 mt-4 flex list-none flex-col gap-3 p-0 text-sm">
              {d.classes.map((c) => (
                <li key={c.key} className="flex items-center gap-2">
                  <Swatch fill={colors.get(c.key)?.fill} />
                  <span className="min-w-0 flex-1 truncate text-fg">{classLabel(c.key, c.label)}</span>
                  <span className="tabular text-fg">
                    <Money cents={c.value_cents} />
                  </span>
                  <span className="tabular w-14 text-right text-fg-secondary">{formatPct(c.pct, true)}</span>
                </li>
              ))}
            </ul>
            {d.classes.some((c) => c.key === NO_CLASS) && (
              <p className="m-0 mt-3 text-sm text-fg-secondary">
                No class: the statement gave these holdings no asset class. An admin can pick one in{" "}
                <Link to="/settings/prices" className="inline-flex min-h-11 items-center md:min-h-0">
                  Settings, Prices
                </Link>
                .
              </p>
            )}
          </>
        )}
      </ChartCard>
      <ChartCard<MixRow>
        eyebrow="By account"
        subtitle="Each bar is one account's mix, in the same colors."
        ariaLabel={
          d
            ? d.accounts
                .map((a) => `${a.display_name}: ${a.classes.map((c) => `${classLabel(c.key, c.label)} ${formatPct(c.pct, true)}`).join(", ")}`)
                .join(". ")
            : "Mix by account"
        }
        data={d ? mixRows(d) : undefined}
        columns={MIX_COLUMNS}
        rowKey={(r) => r.key}
        tableCaption="Each account's value by asset class."
        loading={q.isPending}
        error={error}
        quietError
        emptyText="No accounts with holdings yet."
        height={160}
      >
        {d && (
          <ul className="m-0 flex list-none flex-col gap-5 p-0">
            {d.accounts.map((a) => (
              <li key={a.account_key}>
                <div className="mb-2 flex items-baseline justify-between gap-3 text-sm">
                  <span className="min-w-0 truncate font-medium text-fg" title={a.display_name}>
                    {a.display_name}
                  </span>
                  <span className="shrink-0 text-fg">
                    <Money cents={a.value_cents} />
                    <span className="tabular ml-2 text-fg-secondary">{formatPct(a.pct, true)} of total</span>
                  </span>
                </div>
                <MixBar slices={a.classes} colors={colors} />
                <p className="m-0 mt-1 text-xs text-fg-secondary">
                  {a.classes.map((c) => `${classLabel(c.key, c.label)} ${formatPct(c.pct, true)}`).join(" · ")}
                </p>
              </li>
            ))}
          </ul>
        )}
      </ChartCard>
    </div>
  );
}

function Swatch({ fill }: { fill: string | undefined }) {
  return <span aria-hidden="true" className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: fill ?? OTHER_FILL }} />;
}

/** One 12px bar split by class, largest first, 2px gaps, rounded ends. Parts at zero or below are left out. */
function MixBar({ slices, colors }: { slices: AllocationSlice[]; colors: Map<string, ClassColor> }) {
  const parts = slices.filter((s) => s.value_cents > 0).sort((a, b) => b.value_cents - a.value_cents);
  if (parts.length === 0) return null;
  return (
    <div className="flex h-3 gap-[2px] overflow-hidden rounded-full" aria-hidden="true">
      {parts.map((s) => (
        <span
          key={s.key}
          title={`${classLabel(s.key, s.label)}: ${formatPct(s.pct, true)}`}
          style={{ flexGrow: s.value_cents, flexBasis: 0, background: colors.get(s.key)?.fill ?? OTHER_FILL }}
        />
      ))}
    </div>
  );
}
