import type { PlanVersion, PlanView, TripPlansResponse } from "../../../lib/api";
import type { AiStatusOut } from "../../ai/api";
import { TRIPS } from "../fixtures";

export const PLAN_ID = "abcd".repeat(8);

export const AI_ON: AiStatusOut = {
  enabled: true,
  key_present: true,
  available: true,
  reason: null,
  blocked_until: null,
  blocked_reason: null,
  thread_retention_days: 90,
  running_review: false,
  running_suggestions: false,
  last_review_result: null,
  last_suggestions_result: null,
  review_failed: null,
};

export const AI_OFF: AiStatusOut = { ...AI_ON, enabled: false, available: false, reason: "off" };
export const AI_PAUSED: AiStatusOut = { ...AI_ON, available: false, reason: "spend_limit", blocked_reason: "AI is paused." };

export function plansResponse(overrides: Partial<TripPlansResponse> = {}): TripPlansResponse {
  return {
    available: true,
    estimate_cents: 92,
    follow_up_limit: 3,
    model: "claude-opus-5-5",
    people: [
      { key: "alex", name: "Alex" },
      { key: "jamie", name: "Jamie" },
      { key: "riley", name: "Riley" },
      { key: "sam", name: "Sam" },
    ],
    plans: [],
    running_plan_id: null,
    ...overrides,
  };
}

const links: PlanVersion["links"] = [
  { id: "S1", site: "fares.example", title: "Fare options", url: "https://fares.example/search" },
  { id: "S2", site: "lantern.example", title: "Lantern Hotel", url: "https://lantern.example/stay" },
  { id: "S3", site: "visit.example", title: "Local guide", url: "https://visit.example/guide" },
  { id: "S4", site: "unsafe.example", title: "Unsafe link", url: "javascript:alert(1)" },
  { id: "S5", site: "credentials.example", title: "Credentials link", url: "https://alex:secret" + "@credentials.example/path" },
];

function version(versionNumber = 1, summary = "A calm trip with fares.example as a reference.", followUp: string | null = null): PlanVersion {
  return {
    version: versionNumber,
    created_at: "2026-10-27T12:00:00Z",
    follow_up: followUp,
    links,
    plan: {
      title: "Lantern City",
      summary,
      checks: ["Confirm the fare rules before booking."],
      link_ids: ["S1", "S2"],
      costs: [{ kind: "flight", low_usd: 420, high_usd: 520, note: "Round trip" }],
      points_plan: [
        { item: "Two seats", kind: "flight", program: "Summit Points", points: 40_000, taxes_usd: 38, cash_price_usd: 420, benefit: "", note: "Check award space.", source_ids: ["S1"] },
      ],
      flights: [
        { label: "Round trip", route: "MSP to Lantern City", when: "Jan 2027", cash_low_usd: 420, cash_high_usd: 520, points: 40_000, program: "Summit Points", taxes_usd: 38, note: "Compare dates.", source_ids: ["S1"] },
      ],
      lodging: [
        { name: "Lantern Hotel", area: "Old town", nightly_low_usd: 139, nightly_high_usd: 174, program: "Harbor Miles", points_per_night: 18_000, free_night_ok: true, why: "Good location.", source_ids: ["S2"] },
      ],
      ground: [{ mode: "transit", low_usd: 24, high_usd: 40, note: "Use the airport train.", source_ids: ["S3"] }],
      days: [
        {
          day: 1,
          date: "2027-01-15",
          base: "Lantern City",
          travel: "Arrive by train",
          lodging: "Lantern Hotel",
          items: [
            { part: "morning", kind: "sight", title: "Harbor walk", low_usd: 0, high_usd: 0, note: "See fares.example for options.", source_ids: ["S3"] },
          ],
        },
      ],
    },
    derived: {
      costs: { low_cents: 139_200, high_cents: 188_400, by_kind: [{ kind: "flight", low_cents: 42_000, high_cents: 52_000 }] },
      points: [{ value_cents_per_point: "1.85", range: "within", yours: false }],
      balances: [
        { program: "Summit Points", needed: 40_000, have: 55_000, enough: true },
        { program: "Harbor Miles", needed: 18_000, have: 12_000, enough: false },
        { program: "Other points", needed: 5_000, have: null, enough: null },
      ],
      nights: [
        { night: 1, date: "2027-01-15", lodging: "Lantern Hotel" },
        { night: 2, date: "2027-01-16", lodging: null },
      ],
      checks: [{ kind: "no_lodging", text: "Confirm the hotel cancellation terms." }],
    },
  };
}

export function planView(overrides: Partial<PlanView> = {}): PlanView {
  return {
    plan_id: PLAN_ID,
    title: "Lantern City",
    status: "ready",
    step: null,
    created_at: "2026-10-27T12:00:00Z",
    updated_at: "2026-10-27T12:00:00Z",
    created_by_name: "Alex",
    model: "claude-opus-5-5",
    cost_cents: 92,
    web_searches: 2,
    notice: null,
    follow_ups_left: 2,
    trip_key: null,
    trip_name: null,
    request: {
      where: "Lantern City",
      start_date: "2027-01-15",
      end_date: "2027-01-18",
      month: null,
      nights: 3,
      people: [
        { key: "alex", name: "Alex" },
        { key: "sam", name: "Sam" },
      ],
      guests: 0,
      home_airport: "MSP",
      interests: ["food", "history"],
      note: "A URL-like string fares.example stays plain text.",
      pace: "balanced",
      lean: "value",
    },
    versions: [version()],
    ...overrides,
  };
}

export function runningPlan(step: "searching" | "writing" = "searching"): PlanView {
  return planView({ status: "running", step, versions: [] });
}

export function failedPlan(): PlanView {
  return planView({ status: "failed", step: null, notice: "The search did not finish. Try a different date range.", versions: [] });
}

export const TRAVEL_DATA = TRIPS;
