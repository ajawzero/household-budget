// Goals (format 1.4): GET /api/goals. Read only: the chat owns the goals. Types come
// from the generated schema.ts. index.ts re-exports this file. Money is integer cents.

import { request } from "./client";
import type { components } from "./schema";

type S = components["schemas"];

export type GoalsResponse = S["GoalsOut"];
export type GoalItem = S["GoalOut"];
export type GoalAccount = S["GoalAccountOut"];
export type GoalKind = GoalItem["kind"];

export function getGoals(): Promise<GoalsResponse> {
  return request("GET", "/api/goals");
}

/** Query keys for the call above. */
export const goalKeys = {
  page: ["goals"] as const,
};
