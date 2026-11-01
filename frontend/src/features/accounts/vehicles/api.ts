// Vehicles and valuation estimates (Phase 5). Kept in the feature so the
// shared lib/api module stays untouched while other pages are built.

import { type QueryClient, useQuery } from "@tanstack/react-query";
import { request } from "../../../lib/api/client";
import type { components } from "../../../lib/api/schema";
import { chicagoToday } from "../../../lib/format";

type S = components["schemas"];

export type VehiclesOut = S["VehiclesOut"];
export type Vehicle = S["VehicleOut"];
export type VehicleParts = S["VehicleInputs"];
export type VehicleSettings = S["VehicleSettingsOut"];
export type VehicleSettingsIn = S["VehicleSettingsIn"];
export type Reading = S["ReadingOut"];
export type ValuationsOut = S["ValuationsOut"];
export type ValuationItem = S["ValuationItem"];
export type EstimateValue = S["EstimateValue"];
export type RealValue = S["RealValue"];

export function getVehicles(): Promise<VehiclesOut> {
  return request("GET", "/api/vehicles");
}

export function getValuations(): Promise<ValuationsOut> {
  return request("GET", "/api/valuations");
}

/** Any signed-in user. Same day replaces the earlier reading. */
export function addMileage(key: string, miles: number, readingDate?: string): Promise<Vehicle> {
  return request("POST", `/api/vehicles/${encodeURIComponent(key)}/mileage`, {
    body: { miles, reading_date: readingDate || null },
  });
}

export function deleteMileage(key: string, readingId: number): Promise<Vehicle> {
  return request("DELETE", `/api/vehicles/${encodeURIComponent(key)}/mileage/${readingId}`);
}

/** Admin only. A full replace: null means the default. */
export function putVehicleSettings(key: string, body: VehicleSettingsIn): Promise<Vehicle> {
  return request("PUT", `/api/vehicles/${encodeURIComponent(key)}/settings`, { body });
}

export const vehicleKeys = {
  vehicles: ["vehicles"] as const,
  valuations: ["valuations"] as const,
};

export function useVehicles() {
  return useQuery({ queryKey: vehicleKeys.vehicles, queryFn: getVehicles });
}

export function useValuations(enabled = true) {
  return useQuery({ queryKey: vehicleKeys.valuations, queryFn: getValuations, enabled, staleTime: 60_000 });
}

/**
 * A new reading or setting moves estimates everywhere: vehicles, valuations,
 * account rows and details, net worth and the dashboard.
 */
export function invalidateEstimates(qc: QueryClient): void {
  for (const key of ["vehicles", "valuations", "accounts", "account", "networth-series", "dashboard"]) {
    void qc.invalidateQueries({ queryKey: [key] });
  }
}

/** Today in household time, as "YYYY-MM-DD" (pinned in demo, like `chicagoToday`). */
export function todayIso(now?: Date): string {
  return chicagoToday(now);
}

/** Rates are integer milli-percent: 12_000 is "12%", 500 is "0.5%". */
export function milliPctText(milli: number): string {
  return `${(milli / 1000).toFixed(3).replace(/\.?0+$/, "")}%`;
}

const MILES = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

export function milesText(miles: number): string {
  return `${MILES.format(miles)} miles`;
}

/**
 * Tooltip text for an equity line: what in it is estimated. Uses the value's
 * own estimate and the estimates of the loans it secures.
 */
export function equityNote(items: ValuationItem[] | undefined, accountKey: string): string | null {
  if (!items) return null;
  const asset = items.find((i) => i.account_key === accountKey);
  if (!asset?.equity?.estimated) return null;
  const parts: string[] = [];
  if (asset.estimate) parts.push(`Value: ${asset.estimate.note}`);
  for (const loan of items.filter((i) => i.kind === "loan" && i.secured_by === accountKey && i.estimate)) {
    parts.push(`${loan.display_name}: ${loan.estimate!.note}`);
  }
  return parts.length ? `Equity uses estimates. ${parts.join(" ")}` : "Equity uses estimates.";
}
