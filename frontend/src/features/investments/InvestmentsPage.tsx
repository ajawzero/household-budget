import { useSearchParams } from "react-router";
import { PageHeader } from "../../components/shell/PageHeader";
import { DEFAULT_RANGE, INVESTMENT_RANGE_KEYS, type InvestmentRangeKey } from "../../lib/api/types";
import { useFadeIn } from "../../lib/motion";
import { PageTabs, type TabItem } from "../cashflow/PageTabs";
import { AllocationTab } from "./AllocationTab";
import { ContributionsTab } from "./contributions/ContributionsTab";
import type { GroupBy } from "./api";
import { FlowsCard } from "./FlowsCard";
import { HistoryCard } from "./HistoryCard";
import { HoldingsTab } from "./HoldingsTab";

type Tab = "holdings" | "allocation" | "contributions";

const TABS: TabItem<Tab>[] = [
  { value: "holdings", label: "Holdings" },
  { value: "allocation", label: "Allocation" },
  { value: "contributions", label: "Contributions" },
];

function asTab(v: string | null): Tab {
  return v === "allocation" || v === "contributions" ? v : "holdings";
}

function asGroup(v: string | null): GroupBy {
  return v === "class" ? v : "account";
}

function asRange(v: string | null): InvestmentRangeKey {
  return (INVESTMENT_RANGE_KEYS as readonly string[]).includes(v ?? "") ? (v as InvestmentRangeKey) : DEFAULT_RANGE;
}

/**
 * Investments (PLAN 8.2): value history, holdings by account or class, and
 * the allocation. Tab, grouping and range live in the URL.
 */
export function InvestmentsPage() {
  const [params, setParams] = useSearchParams();
  const tab = asTab(params.get("tab"));
  const panelRef = useFadeIn<HTMLDivElement>(tab, true);
  const groupBy = asGroup(params.get("group"));
  const range = asRange(params.get("range"));

  function set(key: string, value: string, fallback: string) {
    setParams(
      (p) => {
        const next = new URLSearchParams(p);
        if (value === fallback) next.delete(key);
        else next.set(key, value);
        return next;
      },
      { replace: true },
    );
  }

  return (
    <>
      <PageHeader
        title="Investments"
        tabs={<PageTabs label="Investments views" idPrefix="investments" value={tab} tabs={TABS} onChange={(t) => set("tab", t, "holdings")} />}
      />
      <div ref={panelRef} id="investments-panel" role="tabpanel" aria-labelledby={`investments-tab-${tab}`} className="flex flex-col gap-6 pb-8">
        {tab === "holdings" && (
          <>
            <HistoryCard range={range} onRangeChange={(r) => set("range", r, DEFAULT_RANGE)} />
            <FlowsCard />
            <HoldingsTab groupBy={groupBy} onGroupByChange={(g) => set("group", g, "account")} />
          </>
        )}
        {tab === "allocation" && <AllocationTab />}
        {tab === "contributions" && <ContributionsTab />}
      </div>
    </>
  );
}
