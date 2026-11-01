// Dev only (/dev/trip-plan): the trip planner screens with the test fixtures,
// so they can be seen and checked at phone width without an AI key. Their own
// query cache holds the fixtures and never refetches them. Plan actions (make,
// change, link, delete) still call the dev API. Not part of the production build.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { Link, Route, Routes, useSearchParams } from "react-router";
import { tripPlanKeys } from "../../lib/api/tripPlans";
import { tripKeys } from "../../lib/api/trips";
import { aiKeys } from "../ai/api";
import { TRIPS } from "../travel/fixtures";
import { AI_ON, PLAN_ID, plansResponse, planView } from "../travel/plans/fixtures";
import { PlanFormDrawer } from "../travel/plans/PlanForm";
import { PlanPage } from "../travel/plans/PlanPage";
import { TravelPage } from "../travel/TravelPage";

const LIST = plansResponse({
  plans: [
    {
      plan_id: PLAN_ID,
      title: "Lantern City",
      status: "ready",
      updated_at: "2026-10-27T12:00:00Z",
      start_date: "2027-01-15",
      end_date: "2027-01-18",
      month: null,
      nights: 3,
      low_cents: 139_200,
      high_cents: 188_400,
      versions: 1,
      created_by_name: null,
      trip_key: "ski-weekend",
      trip_name: "Ski weekend",
    },
  ],
});

const VIEWS = [
  { key: "plan", label: "Plan" },
  { key: "travel", label: "Travel" },
  { key: "form", label: "Form" },
] as const;

function seeded(): QueryClient {
  const qc = new QueryClient({
    // Some hooks set their own staleTime, so mounting must not refetch either.
    defaultOptions: {
      queries: { staleTime: Infinity, retry: false, refetchOnMount: false, refetchOnWindowFocus: false, refetchOnReconnect: false },
    },
  });
  qc.setQueryData(aiKeys.status, AI_ON);
  qc.setQueryData(tripKeys.page, TRIPS);
  qc.setQueryData(tripPlanKeys.list, LIST);
  qc.setQueryData(tripPlanKeys.one(PLAN_ID), planView());
  return qc;
}

export function TripPlanPreview() {
  const [qc] = useState(seeded);
  const [params] = useSearchParams();
  const view = params.get("view") ?? "plan";
  return (
    <QueryClientProvider client={qc}>
      <nav aria-label="Preview" className="mb-4 flex gap-2 text-sm">
        {VIEWS.map((v) => (
          <Link
            key={v.key}
            to={`?view=${v.key}`}
            aria-current={v.key === view ? "page" : undefined}
            className="inline-flex h-11 min-w-11 items-center justify-center px-2"
          >
            {v.label}
          </Link>
        ))}
      </nav>
      {view === "plan" && (
        // PlanPage reads the plan id from the route.
        <Routes location={`/dev/trip-plan/${PLAN_ID}`}>
          <Route path=":planId" element={<PlanPage />} />
        </Routes>
      )}
      {view === "travel" && <TravelPage />}
      {view === "form" && <PlanFormDrawer open onOpenChange={() => {}} people={LIST.people} estimateCents={LIST.estimate_cents} />}
    </QueryClientProvider>
  );
}
