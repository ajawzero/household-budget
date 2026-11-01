import { useMemo, useState } from "react";
import { groupColor, StackedBar, type StackSegment } from "../../components/charts";
import { Money } from "../../components/money/Money";
import { Card, CardHeader } from "../../components/ui/card";
import { SegmentedControl } from "../../components/ui/segmented-control";
import type { AccountGroup, AccountsSummary, SummaryPart } from "../../lib/api";
import { ownerColors, ownerGroupTypes } from "./model";

type Mode = "totals" | "percent";
type View = "type" | "owner";

/**
 * Parts as positive bar segments. By type colors by group; By owner borrows
 * the color of each part's account group (see ownerColors), one map for both
 * sides so a part keeps its color. Totals are the sums of the parts drawn.
 */
export function summarySegments(
  parts: SummaryPart[],
  view: View,
  types: Map<string, string> = new Map(),
): { assets: StackSegment[]; liabilities: StackSegment[]; assetsCents: number; liabilitiesCents: number } {
  const assetParts = parts.filter((p) => !p.is_liability);
  const owedParts = parts.filter((p) => p.is_liability);
  const colors =
    view === "owner"
      ? ownerColors(
          parts.map((p) => p.key),
          types,
        )
      : new Map(parts.map((p) => [p.key, groupColor(p.key)]));
  const toSegments = (side: SummaryPart[], sign: 1 | -1) =>
    side.map((p) => ({ key: p.key, label: p.label, cents: sign * p.cents, color: colors.get(p.key)! }));
  const sum = (side: SummaryPart[]) => side.reduce((total, p) => total + p.cents, 0);
  return {
    assets: toSegments(assetParts, 1),
    liabilities: toSegments(owedParts, -1),
    assetsCents: sum(assetParts),
    liabilitiesCents: -sum(owedParts),
  };
}

/** Right-hand card: assets and liabilities as stacked bars, by type or by owner. */
export function SummaryCard({ summary, groups }: { summary: AccountsSummary; groups: AccountGroup[] }) {
  const [mode, setMode] = useState<Mode>("totals");
  const [view, setView] = useState<View>("type");
  const types = useMemo(() => ownerGroupTypes(groups), [groups]);
  const { assets, liabilities, assetsCents, liabilitiesCents } = summarySegments(
    view === "type" ? summary.by_type : summary.by_owner,
    view,
    types,
  );
  // By owner nets secured loans into their asset, so its headers are the sums
  // of its own parts. By type keeps the gross totals from the API.
  const assetsTotal = view === "type" ? summary.assets_cents : assetsCents;
  const liabilitiesTotal = view === "type" ? summary.liabilities_cents : liabilitiesCents;

  return (
    <Card>
      <CardHeader
        title="Summary"
        actions={
          <SegmentedControl
            label="Show totals or percent"
            variant="pill"
            value={mode}
            onChange={setMode}
            options={[
              { value: "totals", label: "Totals" },
              { value: "percent", label: "Percent" },
            ]}
          />
        }
      />
      <SegmentedControl
        label="Group by"
        variant="pill"
        value={view}
        onChange={setView}
        className="mb-5"
        options={[
          { value: "type", label: "By type" },
          { value: "owner", label: "By owner" },
        ]}
      />
      <Block title="Assets" total={assetsTotal} segments={assets} mode={mode} />
      <div className="my-5 h-px bg-line" />
      <Block title="Liabilities" total={liabilitiesTotal} segments={liabilities} mode={mode} />
      <div className="mt-5 flex items-baseline justify-between border-t border-line pt-4">
        <span className="text-base font-semibold text-fg">Net worth</span>
        <Money cents={summary.net_worth_cents} className="text-base font-semibold text-fg" />
      </div>
    </Card>
  );
}

function Block({ title, total, segments, mode }: { title: string; total: number; segments: StackSegment[]; mode: Mode }) {
  return (
    <div>
      <div className="mb-3 flex items-baseline justify-between">
        <h3 className="m-0 text-base font-semibold text-fg">{title}</h3>
        <Money cents={total} className="text-fg-secondary" />
      </div>
      {segments.some((s) => s.cents !== 0) ? (
        <StackedBar segments={segments} mode={mode} label={title} />
      ) : (
        <p className="m-0 text-sm text-fg-secondary">None</p>
      )}
    </div>
  );
}
