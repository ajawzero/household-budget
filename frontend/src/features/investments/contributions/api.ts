// 401k and HSA contributions against the IRS limits (Phase 5). Kept in the
// feature so the shared lib/api module stays untouched.

import { type QueryClient, useQuery } from "@tanstack/react-query";
import { query, request } from "../../../lib/api/client";
import type { components } from "../../../lib/api/schema";

type S = components["schemas"];

export type ContributionsOut = S["ContributionsOut"];
export type PersonContributions = S["PersonContributions"];
export type PlanRow = S["PlanRow"];
export type PlanStatus = PlanRow["status"];
export type LimitRow = S["LimitRow"];
export type LimitKind = LimitRow["kind"];
export type LimitIn = S["LimitIn"];
export type PersonSettings = S["PersonSettingsOut"];
export type PersonSettingsIn = S["PersonSettingsIn"];
export type HsaFamily = S["HsaFamilyOut"];
export type OtherContribution = S["OtherContribution"];

export function getContributions(year?: number): Promise<ContributionsOut> {
  return request("GET", `/api/contributions${query({ year })}`);
}

/** Admin only. Upserts, so it can add a year. confirm=false clears the confirmation. */
export function putLimit(body: LimitIn): Promise<LimitRow> {
  return request("PUT", "/api/contribution-limits", { body });
}

/** Admin only. */
export function putPersonSettings(personKey: string, body: PersonSettingsIn): Promise<PersonSettings> {
  return request("PUT", `/api/contributions/people/${encodeURIComponent(personKey)}`, { body });
}

export const contributionKeys = {
  all: ["contributions"] as const,
  year: (year?: number) => ["contributions", year ?? null] as const,
};

export function useContributions(year?: number) {
  return useQuery({ queryKey: contributionKeys.year(year), queryFn: () => getContributions(year) });
}

export function invalidateContributions(qc: QueryClient): void {
  void qc.invalidateQueries({ queryKey: contributionKeys.all });
}

/** Every limit kind in a fixed order, with the server's labels, for a year that has none yet. */
export const LIMIT_KINDS: { kind: LimitKind; label: string; includesEmployer: boolean }[] = [
  { kind: "k401_deferral", label: "401k employee limit", includesEmployer: false },
  { kind: "k401_catchup_50", label: "401k catch-up, age 50 and over", includesEmployer: false },
  { kind: "k401_catchup_60_63", label: "401k catch-up, age 60 to 63", includesEmployer: false },
  { kind: "k415c_total", label: "401k total, employee and employer", includesEmployer: true },
  { kind: "hsa_self", label: "HSA, self-only coverage", includesEmployer: true },
  { kind: "hsa_family", label: "HSA, family coverage", includesEmployer: true },
  { kind: "hsa_catchup_55", label: "HSA catch-up, age 55 and over", includesEmployer: false },
];
