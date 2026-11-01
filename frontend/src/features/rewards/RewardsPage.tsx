import { useQuery } from "@tanstack/react-query";
import { Gift } from "lucide-react";
import { useSearchParams } from "react-router";
import { PageHeader } from "../../components/shell/PageHeader";
import { Card } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorBanner } from "../../components/ui/error-banner";
import { LinkTabs } from "../../components/ui/link-tabs";
import { getRewards, rewardKeys } from "../../lib/api";
import { SourceLine } from "../recurring/parts";
import { BenefitsTab } from "./BenefitsTab";
import { MilestonesTab } from "./MilestonesTab";
import { parseTab, TABS } from "./model";
import { RewardTilesRow } from "./parts";
import { PointsTab } from "./PointsTab";
import { FeesTab, RoutingTab } from "./TableTabs";
import { ThisMonth } from "./ThisMonth";

/**
 * Rewards (docs/plans/phase-7.md section 4): points, use-or-lose benefits,
 * spend milestones, card routing and annual fees, from the chat's newest
 * monthly run with the app's inputs on top (docs/plans/rewards-inputs.md).
 * The tab lives in the URL, and so do the drawers Checks links to:
 * ?update=balances and ?tab=benefits&review=credits.
 */
export function RewardsPage() {
  const [params] = useSearchParams();
  const tab = parseTab(params.get("tab"));
  const q = useQuery({ queryKey: rewardKeys.page, queryFn: getRewards });
  const data = q.data;
  const noData = data !== undefined && data.source_run_id === null;
  // The banner says it once. Cards below show this quiet text, not their empty text.
  const loadError = q.isError && !data ? "Not loaded." : null;
  const failed = data?.notices.find((n) => n.kind === "overlay_failed");

  return (
    <>
      <PageHeader
        title="Rewards"
        tabs={
          <LinkTabs
            label="Rewards views"
            tabs={TABS.map((t) => ({ label: t.label, to: t.key === "points" ? "?" : `?tab=${t.key}`, active: t.key === tab }))}
          />
        }
      />
      <div className="flex min-w-0 flex-col gap-4 pb-8">
        {loadError && <ErrorBanner message="Could not load rewards." onRetry={() => void q.refetch()} />}
        {noData ? (
          <Card>
            <EmptyState icon={<Gift size={28} strokeWidth={1.5} aria-hidden="true" />} title="No rewards data yet.">
              It comes with the chat's next export.
            </EmptyState>
          </Card>
        ) : (
          <>
            {failed && (
              <p role="status" className="m-0 rounded-card bg-tint-warn px-4 py-3 text-sm text-fg">
                {failed.text}
              </p>
            )}
            <RewardTilesRow tiles={data?.tiles} loading={q.isPending} />
            {data && <ThisMonth data={data} />}
            {tab === "points" && <PointsTab data={data} loading={q.isPending} error={loadError} />}
            {tab === "benefits" && <BenefitsTab data={data} loading={q.isPending} error={loadError} />}
            {tab === "milestones" && <MilestonesTab data={data} loading={q.isPending} error={loadError} />}
            {tab === "routing" && <RoutingTab data={data} loading={q.isPending} error={loadError} />}
            {tab === "fees" && <FeesTab data={data} loading={q.isPending} error={loadError} />}
            <SourceLine runId={data?.source_run_id} note="Your app edits are on top." />
          </>
        )}
      </div>
    </>
  );
}
