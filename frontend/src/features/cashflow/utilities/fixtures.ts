// Made-up utility bills for the Utilities tab tests. Fake names and numbers only.
import type {
  UtilitiesResponse,
  UtilityBill,
  UtilityBillBeforeValue,
  UtilityCostPoint,
  UtilityDetail,
  UtilitySummary,
  UtilityYoyRow,
  UtilityYoyValue,
} from "../../../lib/api";

const M = 1_000_000;

function yv(value: number | null, lastYear: number | null, change: string | null): UtilityYoyValue {
  return { value, last_year: lastYear, change_pct: change };
}

function bv(previous: number | null, change: string | null): UtilityBillBeforeValue {
  return { previous, change_pct: change };
}

export const POWER_SUMMARY: UtilitySummary = {
  utility_key: "example-power",
  provider: "Example Power",
  bill_count: 15,
  first_period_end: "2025-07-05",
  last_period_end: "2026-09-03",
  last_bill_date: "2026-09-06",
  last_total_cents: 17_425,
  last_status: "not_paid",
  services: [
    { service: "electric", unit: "kwh" },
    { service: "gas", unit: "therms" },
  ],
  next_bill_date: "2026-10-06",
  next_bill_late: false,
  last_import_date: "2026-10-12",
  avg_bill_cents: 13_744,
  bills_in_avg: 12,
  total_12m_cents: 164_928,
};

export const WATER_SUMMARY: UtilitySummary = {
  utility_key: "example-water",
  provider: "Example Water",
  bill_count: 2,
  first_period_end: "2026-06-01",
  last_period_end: "2026-09-01",
  last_bill_date: "2026-09-06",
  last_total_cents: 6_671,
  last_status: "paid",
  services: [
    { service: "sewer", unit: "gallons" },
    { service: "stormwater", unit: "none" },
    { service: "water", unit: "gallons" },
  ],
  next_bill_date: "2026-12-07",
  next_bill_late: false,
  last_import_date: "2026-10-12",
  avg_bill_cents: 6_476,
  bills_in_avg: 2,
  total_12m_cents: 12_952,
};

export const UTILITIES: UtilitiesResponse = { utilities: [POWER_SUMMARY, WATER_SUMMARY] };

const checking = { account_key: "example-checking", account_name: "Example checking" };

/** The bill date + 40 days: when a bill with no due date counts as due. */
function plus40(iso: string): string {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 40);
  return d.toISOString().slice(0, 10);
}

function powerBill(
  periodStart: string,
  periodEnd: string,
  days: number,
  kwh: number,
  therms: number,
  total: number,
  extra: Partial<UtilityBill>,
): UtilityBill {
  return {
    bill_key: `example-power:${periodEnd}`,
    provider: "Example Power",
    bill_date: extra.bill_date ?? periodEnd,
    period_start: periodStart,
    period_end: periodEnd,
    days,
    total_cents: total,
    due_date: null,
    due_by: extra.due_date ?? plus40(extra.bill_date ?? periodEnd),
    paid_from: "example-checking",
    paid_from_name: "Example checking",
    heating_degree_days: 10,
    estimated_read: null,
    note: null,
    lines: [
      { service: "electric", unit: "kwh", usage_micro: kwh * M, charges_cents: total - 3_000, fixed_charges_cents: 2_000 },
      { service: "gas", unit: "therms", usage_micro: therms * M, charges_cents: 3_000, fixed_charges_cents: 1_250 },
    ],
    credits: [],
    charges_cents: total,
    credits_cents: 0,
    status: "paid",
    payment: null,
    ...extra,
  };
}

const POWER_BILLS: UtilityBill[] = [
  powerBill("2026-06-04", "2026-07-05", 31, 420, 8, 11_390, {
    bill_date: "2026-07-08",
    status: "paid",
    payment: { txn_key: "t1", txn_date: "2026-07-18", amount_cents: 11_390, ...checking, amount_differs: false, days_after_bill: 10 },
    credits: [{ label: "Example credit", amount_cents: -219 }],
  }),
  powerBill("2026-07-05", "2026-08-04", 30, 475, 10, 12_584, {
    bill_date: "2026-08-07",
    status: "amount_differs",
    estimated_read: true,
    payment: { txn_key: "t2", txn_date: "2026-08-18", amount_cents: 12_000, ...checking, amount_differs: true, days_after_bill: 11 },
    credits: [
      { label: "Example credit", amount_cents: -219 },
      { label: "Savings credit", amount_cents: -500 },
    ],
  }),
  powerBill("2026-08-04", "2026-09-03", 30, 719, 19, 17_425, {
    bill_date: "2026-09-06",
    due_date: "2026-09-26",
    status: "not_paid",
  }),
];

type PowerBefore = { start: string; end: string; days: number; kwh: number; therms: number };

function powerYoy(
  b: UtilityBill,
  last: { start: string; end: string; days: number; kwh: number; therms: number; total: number; hdd: number } | null,
  prev: PowerBefore | null,
): UtilityYoyRow {
  const kwh = b.lines[0]!.usage_micro;
  const th = b.lines[1]!.usage_micro;
  const pct = (a: number, z: number | undefined) => (z === undefined || z === 0 ? null : (((a - z) / z) * 100).toFixed(1));
  const lk = last ? last.kwh * M : undefined;
  const lt = last ? last.therms * M : undefined;
  const perDay = (u: number, d: number) => Math.round(u / d);
  // The bill before: total, and per day from the rounded micro values.
  const before = (now: number, old: number | undefined, mode: "total" | "day") => {
    if (!prev || old === undefined) return bv(null, null);
    const a = mode === "day" ? perDay(now, b.days) : now;
    const z = mode === "day" ? perDay(old, prev.days) : old;
    return bv(z, pct(a, z));
  };
  const pk = prev ? prev.kwh * M : undefined;
  const pt = prev ? prev.therms * M : undefined;
  return {
    bill_key: b.bill_key,
    period_start: b.period_start,
    period_end: b.period_end,
    last_year_bill_key: last ? `example-power:${last.end}` : null,
    last_year_period_start: last?.start ?? null,
    last_year_period_end: last?.end ?? null,
    prev_bill_key: prev ? `example-power:${prev.end}` : null,
    prev_period_start: prev?.start ?? null,
    prev_period_end: prev?.end ?? null,
    days: yv(b.days, last?.days ?? null, last ? pct(b.days, last.days) : null),
    heating_degree_days: yv(b.heating_degree_days, last?.hdd ?? null, last ? pct(b.heating_degree_days ?? 0, last.hdd) : null),
    total_cents: yv(b.total_cents, last?.total ?? null, pct(b.total_cents, last?.total)),
    total_per_day_cents: yv(Math.round(b.total_cents / b.days), last ? Math.round(last.total / last.days) : null, null),
    services: [
      {
        service: "electric",
        unit: "kwh",
        usage_micro: yv(kwh, lk ?? null, pct(kwh, lk)),
        per_day_micro: yv(perDay(kwh, b.days), last && lk ? perDay(lk, last.days) : null, last && lk ? pct(perDay(kwh, b.days), perDay(lk, last.days)) : null),
        per_degree_day_micro: null,
        charges_cents: yv(b.lines[0]!.charges_cents, null, null),
        usage_vs_prev: before(kwh, pk, "total"),
        per_day_vs_prev: before(kwh, pk, "day"),
      },
      {
        service: "gas",
        unit: "therms",
        usage_micro: yv(th, lt ?? null, pct(th, lt)),
        per_day_micro: yv(perDay(th, b.days), last && lt ? perDay(lt, last.days) : null, last && lt ? pct(perDay(th, b.days), perDay(lt, last.days)) : null),
        per_degree_day_micro: yv(Math.round(th / 10), last && lt ? Math.round(lt / last.hdd) : null, last && lt ? pct(th / 10, lt / last.hdd) : null),
        charges_cents: yv(b.lines[1]!.charges_cents, null, null),
        usage_vs_prev: before(th, pt, "total"),
        per_day_vs_prev: before(th, pt, "day"),
      },
    ],
  };
}

/** The bill before each one. Jun is outside the range, and still found. */
const JUN_BILL: PowerBefore = { start: "2026-05-05", end: "2026-06-04", days: 30, kwh: 380, therms: 7 };
const before = (b: UtilityBill): PowerBefore => ({
  start: b.period_start,
  end: b.period_end,
  days: b.days,
  kwh: b.lines[0]!.usage_micro / M,
  therms: b.lines[1]!.usage_micro / M,
});

const POWER_YOY: UtilityYoyRow[] = [
  // Jul: 31 days vs 30 last year, so kWh per day falls more than total kWh.
  powerYoy(POWER_BILLS[0]!, { start: "2025-06-05", end: "2025-07-05", days: 30, kwh: 487, therms: 10, total: 12_000, hdd: 20 }, JUN_BILL),
  powerYoy(POWER_BILLS[1]!, { start: "2025-07-05", end: "2025-08-04", days: 30, kwh: 663, therms: 17, total: 14_000, hdd: 10 }, before(POWER_BILLS[0]!)),
  // Sep: more use than last year, and than the bill before.
  powerYoy(POWER_BILLS[2]!, { start: "2025-08-04", end: "2025-09-03", days: 30, kwh: 580, therms: 14, total: 14_186, hdd: 10 }, before(POWER_BILLS[1]!)),
];

function cost(b: UtilityBill): UtilityCostPoint {
  return {
    bill_key: b.bill_key,
    period_end: b.period_end,
    services: b.lines.map((l) => ({
      service: l.service,
      unit: l.unit,
      usage_micro: l.usage_micro,
      charges_cents: l.charges_cents,
      fixed_charges_cents: l.fixed_charges_cents,
      cost_per_unit_millicents: Math.round((l.charges_cents * 1000 * M) / l.usage_micro),
      cost_per_unit_ex_fixed_millicents: Math.round(((l.charges_cents - (l.fixed_charges_cents ?? 0)) * 1000 * M) / l.usage_micro),
    })),
  };
}

export const POWER_DETAIL: UtilityDetail = {
  utility_key: "example-power",
  provider: "Example Power",
  from: "2025-10",
  to: null,
  summary: POWER_SUMMARY,
  bills: POWER_BILLS,
  yoy: POWER_YOY,
  cost: POWER_BILLS.map(cost),
  credits: [
    { bill_key: "example-power:2026-07-05", bill_date: "2026-07-08", period_end: "2026-07-05", label: "Example credit", amount_cents: -219, running_cents: -219 },
    { bill_key: "example-power:2026-08-04", bill_date: "2026-08-07", period_end: "2026-08-04", label: "Example credit", amount_cents: -219, running_cents: -438 },
    { bill_key: "example-power:2026-08-04", bill_date: "2026-08-07", period_end: "2026-08-04", label: "Savings credit", amount_cents: -500, running_cents: -938 },
  ],
  credits_total_cents: -938,
  warnings: [
    {
      utility_key: "example-power",
      provider: "Example Power",
      bill_key: "example-power:2026-08-04",
      period_end: "2026-08-04",
      rule: "utility-total-rounding",
      message: "The line charges plus the credits differ from the total by 1 or 2 cents.",
    },
  ],
};

function waterBill(start: string, end: string, days: number, gallons: number, total: number, status: UtilityBill["status"]): UtilityBill {
  return {
    bill_key: `example-water:${end}`,
    provider: "Example Water",
    bill_date: end,
    period_start: start,
    period_end: end,
    days,
    total_cents: total,
    due_date: null,
    due_by: plus40(end),
    paid_from: null,
    paid_from_name: null,
    heating_degree_days: null,
    estimated_read: null,
    note: "Quarterly bill",
    lines: [
      { service: "sewer", unit: "gallons", usage_micro: gallons * M, charges_cents: 2_700, fixed_charges_cents: 700 },
      { service: "stormwater", unit: "none", usage_micro: 0, charges_cents: 600, fixed_charges_cents: 600 },
      { service: "water", unit: "gallons", usage_micro: gallons * M, charges_cents: total - 3_300, fixed_charges_cents: 800 },
    ],
    credits: [],
    charges_cents: total,
    credits_cents: 0,
    status,
    payment: null,
  };
}

const WATER_BILLS = [
  waterBill("2026-03-01", "2026-06-01", 92, 5_000, 6_281, "past_due"),
  waterBill("2026-06-01", "2026-09-01", 92, 5_642, 6_671, "no_history"),
];

function waterYoy(b: UtilityBill, i: number): UtilityYoyRow {
  const g = b.lines[0]!.usage_micro;
  const prev = i > 0 ? WATER_BILLS[i - 1]! : null;
  const pct = (a: number, z: number) => (z === 0 ? null : (((a - z) / z) * 100).toFixed(1));
  const svc = (service: "sewer" | "stormwater" | "water", unit: "gallons" | "none", u: number) => {
    const old = prev ? prev.lines.find((l) => l.service === service)!.usage_micro : null;
    const pd = (x: number, days: number) => Math.round(x / days);
    return {
      service,
      unit,
      usage_micro: yv(u, null, null),
      per_day_micro: yv(pd(u, b.days), null, null),
      per_degree_day_micro: null,
      charges_cents: yv(0, null, null),
      usage_vs_prev: old === null ? bv(null, null) : bv(old, pct(u, old)),
      per_day_vs_prev: old === null || !prev ? bv(null, null) : bv(pd(old, prev.days), pct(pd(u, b.days), pd(old, prev.days))),
    };
  };
  return {
    bill_key: b.bill_key,
    period_start: b.period_start,
    period_end: b.period_end,
    last_year_bill_key: null,
    last_year_period_start: null,
    last_year_period_end: null,
    prev_bill_key: prev?.bill_key ?? null,
    prev_period_start: prev?.period_start ?? null,
    prev_period_end: prev?.period_end ?? null,
    days: yv(b.days, null, null),
    heating_degree_days: yv(null, null, null),
    total_cents: yv(b.total_cents, null, null),
    total_per_day_cents: yv(Math.round(b.total_cents / b.days), null, null),
    services: [svc("sewer", "gallons", g), svc("stormwater", "none", 0), svc("water", "gallons", g)],
  };
}

export const WATER_DETAIL: UtilityDetail = {
  utility_key: "example-water",
  provider: "Example Water",
  from: "2025-10",
  to: null,
  summary: WATER_SUMMARY,
  bills: WATER_BILLS,
  yoy: WATER_BILLS.map((b, i) => waterYoy(b, i)),
  cost: WATER_BILLS.map((b) => ({
    bill_key: b.bill_key,
    period_end: b.period_end,
    services: b.lines.map((l) => ({
      service: l.service,
      unit: l.unit,
      usage_micro: l.usage_micro,
      charges_cents: l.charges_cents,
      fixed_charges_cents: l.fixed_charges_cents,
      cost_per_unit_millicents: l.unit === "none" ? null : Math.round((l.charges_cents * 1000 * M) / l.usage_micro),
      cost_per_unit_ex_fixed_millicents: l.unit === "none" ? null : Math.round(((l.charges_cents - (l.fixed_charges_cents ?? 0)) * 1000 * M) / l.usage_micro),
    })),
  })),
  credits: [],
  credits_total_cents: 0,
  warnings: [],
};
