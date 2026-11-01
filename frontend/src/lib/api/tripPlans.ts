// The trip planner (docs/plans/trip-planner.md): /api/trip-plans. Plans
// belong to the household. A plan runs in the background, one at a time, and
// the page polls the plan until it is no longer running. index.ts re-exports
// this file. The plan's own prices are whole US dollars; the app's are cents.

import { request } from "./client";
import type { components } from "./schema";

type S = components["schemas"];

export type TripPlansResponse = S["TripPlansOut"];
export type PlanSummary = S["PlanSummary"];
export type PlanView = S["PlanView"];
export type PlanVersion = S["Version"];
export type PlanRequestBody = S["PlanRequest"];
export type PlanRequestView = S["RequestOut"];
export type PlanBody = S["PlanOut"];
export type PlanDerived = S["Derived"];
export type PlanLinkOut = S["Link"];
export type PlanCheck = S["Check"];
export type PlanPerson = S["Person"];
export type PlanStatus = PlanView["status"];
export type PlanStep = NonNullable<PlanView["step"]>;
export type PlanInterest = NonNullable<PlanRequestBody["interests"]>[number];
export type PlanPace = PlanRequestBody["pace"];
export type PlanLean = PlanRequestBody["lean"];
export type PlanDay = S["Day"];
export type PlanDayItem = S["DayItem"];
export type PlanCostKind = S["CostLine"]["kind"];
export type PlanGroundMode = S["Ground"]["mode"];

export function getTripPlans(): Promise<TripPlansResponse> {
  return request("GET", "/api/trip-plans");
}

export function getTripPlan(planId: string): Promise<PlanView> {
  return request("GET", `/api/trip-plans/${encodeURIComponent(planId)}`);
}

/** Starts a plan. Throws ApiError when it is refused (422 checks, 409 busy or off, 429 cap). */
export function startTripPlan(body: PlanRequestBody): Promise<S["PlanStarted"]> {
  return request("POST", "/api/trip-plans", { body });
}

export function followUpTripPlan(planId: string, text: string): Promise<S["PlanStarted"]> {
  return request("POST", `/api/trip-plans/${encodeURIComponent(planId)}/follow-up`, { body: { text } });
}

/** Links the plan to an imported trip, or unlinks it with null. */
export function linkTripPlan(planId: string, tripKey: string | null): Promise<PlanView> {
  return request("PUT", `/api/trip-plans/${encodeURIComponent(planId)}/trip`, { body: { trip_key: tripKey } });
}

export function deleteTripPlan(planId: string): Promise<void> {
  return request("DELETE", `/api/trip-plans/${encodeURIComponent(planId)}`);
}

/** Query keys for the calls above. */
export const tripPlanKeys = {
  all: ["trip-plans"] as const,
  list: ["trip-plans", "list"] as const,
  one: (planId: string) => ["trip-plans", "one", planId] as const,
};
