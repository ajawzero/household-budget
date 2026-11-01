import { describe, expect, it } from "vitest";
import rawCases from "./payoff-cases.json?raw";
import type { DebtLedgerRow } from "../../../lib/api";
import { monthInterest, monthlyToFinish, rateChangeMonth, rateFor, rateSteps, simulate, termsOf, type Terms } from "./projection";

interface Case {
  name: string;
  terms: TermsJson;
  monthly_cents: string;
  run: {
    months: number | null;
    interest_cents: string | null;
    last_payment_cents: string | null;
    early_cents: string | null;
  };
  by_month: string;
  monthly_to_finish: string;
}

interface TermsJson {
  owed_cents: string;
  apr_milli_pct: string | null;
  promo: boolean;
  promo_month: string | null;
  from_month: string;
  rate_changes?: { effective_on: string; apr_milli_pct: string }[];
}

interface CasesFile {
  about: string;
  cases: Case[];
}

const { about, cases } = JSON.parse(rawCases) as CasesFile;

function amount(value: string | null): number | null {
  return value === null ? null : Number(value.replaceAll("_", ""));
}

function terms(value: TermsJson): Terms {
  return {
    owedCents: amount(value.owed_cents)!,
    aprMilliPct: amount(value.apr_milli_pct),
    promo: value.promo,
    promoMonth: value.promo_month,
    fromMonth: value.from_month,
    rateChanges: (value.rate_changes ?? []).map((c) => ({ effectiveOn: c.effective_on, aprMilliPct: amount(c.apr_milli_pct)! })),
  };
}

describe("debt payoff projection", () => {
  it("documents the shared case amount format", () => {
    expect(about).toContain("drop the _");
  });

  it.each(cases)("matches shared case: $name", (value) => {
    const t = terms(value.terms);
    expect(simulate(t, amount(value.monthly_cents)!)).toEqual({
      months: value.run.months,
      interestCents: amount(value.run.interest_cents),
      lastPaymentCents: amount(value.run.last_payment_cents),
      earlyCents: amount(value.run.early_cents),
    });
    expect(monthlyToFinish(t, value.by_month)).toBe(amount(value.monthly_to_finish));
  });

  it("rounds monthly interest half up", () => {
    expect(monthInterest(100_000, 12_000)).toBe(1_000);
    expect(monthInterest(50, 12_000)).toBe(1);
    expect(monthInterest(49, 12_000)).toBe(0);
    expect(monthInterest(100_000, 0)).toBe(0);
  });

  it("finds the smallest payment that finishes by a month", () => {
    const t: Terms = { owedCents: 1_000_000, aprMilliPct: 6_000, promo: false, promoMonth: null, fromMonth: "2026-09", rateChanges: [] };
    const need = monthlyToFinish(t, "2027-06");
    expect(simulate(t, need).months).toBe(9);
    expect(simulate(t, need - 1).months).toBe(10);
    expect(monthlyToFinish(t, "2026-08")).toBe(1_000_000 + monthInterest(1_000_000, 6_000));
  });

  it("returns no payoff for a nonpositive payment", () => {
    const t: Terms = { owedCents: 100_000, aprMilliPct: 0, promo: false, promoMonth: null, fromMonth: "2026-09", rateChanges: [] };
    const none = { months: null, interestCents: null, lastPaymentCents: null, earlyCents: null };
    expect(simulate(t, 0)).toEqual(none);
    expect(simulate(t, -500)).toEqual(none);
  });

  it("starts the APR after the promo month", () => {
    const t: Terms = { owedCents: 100_000, aprMilliPct: 24_000, promo: true, promoMonth: "2026-10", fromMonth: "2026-09", rateChanges: [] };
    expect(rateFor(t, "2026-10")).toBe(0);
    expect(rateFor(t, "2026-11")).toBe(24_000);
    expect(simulate(t, 50_000)).toMatchObject({ months: 3, interestCents: 1_020, lastPaymentCents: 1_020 });
  });

  it("maps projection terms from a debt row", () => {
    const row = {
      owed_cents: 100_000,
      apr_milli_pct: 24_000,
      rate_type: "promo",
      rate_changes: [] as DebtLedgerRow["rate_changes"],
      projection: { from_month: "2026-09", promo_until: "2026-10-31", promo_month: "2026-10" },
    } as DebtLedgerRow;
    expect(termsOf({ ...row, projection: null })).toBeNull();
    expect(termsOf(row)).toEqual({
      owedCents: 100_000,
      aprMilliPct: 24_000,
      promo: true,
      promoMonth: "2026-10",
      fromMonth: "2026-09",
      rateChanges: [],
    });
    const variable = { ...row, rate_type: "variable", rate_changes: [{ effective_on: "2026-11-01", apr_milli_pct: 19_990 }] } as DebtLedgerRow;
    expect(termsOf(variable)?.rateChanges).toEqual([{ effectiveOn: "2026-11-01", aprMilliPct: 19_990 }]);
  });

  it("starts a new rate on the 1st, else the month after", () => {
    expect(rateChangeMonth("2026-11-01")).toBe("2026-11");
    expect(rateChangeMonth("2026-11-15")).toBe("2026-12");
    expect(rateChangeMonth("2026-12-31")).toBe("2027-01");
  });

  it("applies a change that started by the balance's month from month one, the later one winning a month", () => {
    const rateChanges = [
      { effectiveOn: "2026-09-01", aprMilliPct: 1 },
      { effectiveOn: "2026-09-02", aprMilliPct: 2 },
      { effectiveOn: "2026-10-01", aprMilliPct: 3 },
    ];
    expect(rateSteps({ rateChanges })).toEqual([
      { month: "2026-09", aprMilliPct: 1 },
      { month: "2026-10", aprMilliPct: 2 },
      { month: "2026-10", aprMilliPct: 3 },
    ]);
    const t: Terms = { owedCents: 100_000, aprMilliPct: 19_240, promo: false, promoMonth: null, fromMonth: "2026-08", rateChanges };
    expect(rateFor(t, "2026-08")).toBe(19_240);
    expect(rateFor(t, "2026-09")).toBe(1);
    expect(rateFor(t, "2026-10")).toBe(3);
    // A step that started by fromMonth (the server sends only the ones that count).
    expect(rateFor({ ...t, fromMonth: "2026-09" }, "2026-10")).toBe(3);
  });

  it("bounds the finish search by the highest scheduled rate", () => {
    const t: Terms = {
      owedCents: 300_000,
      aprMilliPct: 19_240,
      promo: false,
      promoMonth: null,
      fromMonth: "2026-09",
      rateChanges: [{ effectiveOn: "2026-10-01", aprMilliPct: 24_990 }],
    };
    // Today's APR as the bound would give 304_810.
    expect(monthlyToFinish(t, "2026-10")).toBe(300_000 + monthInterest(300_000, 24_990));
    expect(monthlyToFinish(t, "2026-10")).toBe(306_248);
  });

  it("runs the shared rise, fall and past change cases", () => {
    expect(cases.map((c) => c.name)).toEqual(expect.arrayContaining(["rate rise", "rate fall mid-month", "rate change already started"]));
  });
});
