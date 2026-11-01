import type { ContributionsOut, LimitRow, PlanRow } from "./api";

// Fictional people and numbers only.

function limit(kind: LimitRow["kind"], label: string, cents: number, confirmed = false): LimitRow {
  return {
    year: 2026,
    kind,
    label,
    limit_cents: cents,
    includes_employer: kind === "k415c_total" || (kind.startsWith("hsa_") && kind !== "hsa_catchup_55"),
    source_url: "https://www.irs.gov/example",
    confirmed,
    confirmed_at: confirmed ? "2026-10-20T15:00:00+00:00" : null,
    updated_at: "2026-10-20T15:00:00+00:00",
  };
}

export const LIMITS: LimitRow[] = [
  limit("k401_deferral", "401k employee limit", 2_450_000),
  limit("k401_catchup_50", "401k catch-up, age 50 and over", 800_000),
  limit("hsa_family", "HSA, family coverage", 875_000, true),
];

export const K401: PlanRow = {
  kind: "k401",
  label: "401k",
  ytd_cents: 1_800_000,
  ytd_as_of: "2026-09-25",
  ytd_parts: { stubs_cents: 1_700_000, employer_cents: null, prior_employer_cents: 100_000, personal_cents: 0 },
  sum_of_checks_cents: 1_690_000,
  checks_counted: 19,
  cross_check_diff_cents: 10_000,
  limit_cents: 2_450_000,
  limit_base_cents: 2_450_000,
  limit_catch_up_cents: 0,
  limit_confirmed: false,
  room_cents: 650_000,
  per_check_cents: 90_000,
  per_check_employer_cents: null,
  per_check_pct: "10.0",
  checks_left: 7,
  remaining_dates: ["2026-10-09", "2026-10-23", "2026-11-06", "2026-11-20", "2026-12-04", "2026-12-18", "2027-01-01"],
  projected_cents: 2_430_000,
  projected_room_cents: 20_000,
  status: "under",
  change_on: "2026-11-06",
  checks_before_change: 2,
  checks_from_change: 5,
  needed_per_check_cents: 94_000,
  needed_pct: "10.4",
  shared: false,
  k415c: null,
  notes: [],
};

export const HSA: PlanRow = {
  ...K401,
  kind: "hsa",
  label: "HSA",
  ytd_cents: 900_000,
  ytd_parts: { stubs_cents: 900_000, employer_cents: 50_000, prior_employer_cents: 0, personal_cents: 0 },
  cross_check_diff_cents: 0,
  limit_cents: 875_000,
  limit_base_cents: 875_000,
  limit_confirmed: true,
  room_cents: -25_000,
  per_check_cents: 20_000,
  per_check_employer_cents: 2_000,
  per_check_pct: null,
  projected_cents: 1_040_000,
  projected_room_cents: -165_000,
  status: "over",
  change_on: "2026-10-23",
  checks_before_change: 1,
  checks_from_change: 6,
  needed_per_check_cents: 0,
  needed_pct: null,
  shared: true,
  notes: ["The family limit is shared."],
};

export const CONTRIBUTIONS: ContributionsOut = {
  year: 2026,
  as_of: "2026-10-01",
  limits_found: true,
  limits: LIMITS,
  people: [
    {
      person_key: "example-person",
      display_name: "Riley",
      settings: { k401_catch_up: "none", hsa_coverage: "family", hsa_catch_up: false, election_lead_checks: 1, updated_at: null },
      settings_set: false,
      schedule: { cadence: "biweekly", last_pay_date: "2026-09-25" },
      plans: [K401, HSA],
    },
  ],
  hsa_family: { ytd_cents: 900_000, projected_cents: 1_040_000, limit_cents: 875_000, room_cents: -25_000 },
  other: [],
};

export const OTHER: ContributionsOut["other"] = [
  {
    person_key: "example-person",
    person: "Riley",
    account_key: "example-529",
    account_name: "Example 529",
    kind: "529",
    label: "529",
    ytd_cents: 255_000,
    gift_cents: 75_000,
    count: 11,
    last_date: "2026-09-05",
    last_cents: 20_000,
  },
  {
    person_key: "example-person",
    person: "Riley",
    account_key: "example-ira",
    account_name: "Example IRA",
    kind: "ira",
    label: "IRA",
    ytd_cents: 100_000,
    gift_cents: 0,
    count: 1,
    last_date: "2026-04-01",
    last_cents: 100_000,
  },
];
