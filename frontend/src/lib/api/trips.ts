// Travel (format 1.3): GET /api/trips and GET /api/trips/{key}. Read only,
// from the trip tables (the newest run per trip). Types come from the
// generated schema.ts. index.ts re-exports this file. Points are integer
// micro-units (1 point = 1,000,000).

import { request } from "./client";
import type { components } from "./schema";

type S = components["schemas"];

export type TripsResponse = S["TripsOut"];
export type Trip = S["TripOut"];
export type TripItem = S["TripItemOut"];
export type TripIou = S["TripIouOut"];
export type TripTotals = S["TripTotals"];
export type TripTiles = S["TripTiles"];
export type TripStatus = Trip["status"];
export type TripTender = TripItem["tender"];
export type TripPoints = S["PointsUsed"];
export type TripTagged = S["TaggedCheck"];
export type TripOpenItems = S["OpenItems"];
export type TripOpenIou = S["OpenIou"];
export type TripOpenItem = S["OpenItem"];
export type TripSummary = S["TripSummary"];
export type TripSummaryRow = S["SummaryRow"];
export type NextTrip = S["NextTrip"];
export type TripsCheck = S["TripsCheck"];
export type UnknownTrip = S["UnknownTrip"];
export type TaggedLine = S["TaggedLine"];

export function getTrips(): Promise<TripsResponse> {
  return request("GET", "/api/trips");
}

export function getTrip(key: string): Promise<Trip> {
  return request("GET", `/api/trips/${encodeURIComponent(key)}`);
}

/** Query keys for the calls above. */
export const tripKeys = {
  page: ["trips"] as const,
  one: (key: string) => ["trips", key] as const,
};
