import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { renderWithProviders } from "../../../test/render";
import type { DebtLedgerRow, DebtResponse } from "../../../lib/api";
import { DEBT } from "../../recurring/fixtures";
import { durationText, fromNowText, lastPaymentText, paceText, PayoffCard, scheduleText, targetText, tryAmountText, tryMonthText, windowText } from "./PayoffCard";

type Projection = NonNullable<DebtLedgerRow["projection"]>;

const CARD = DEBT.ledger[0]!;
const MORTGAGE = DEBT.ledger[1]!;
// DEBT.as_of is Oct 21, 2026; the fixtures' balances are from September.
const NOW = "2026-10";

/** A ledger row with its projection changed. */
function withProjection(row: DebtLedgerRow, p: Partial<Projection>, rest: Partial<DebtLedgerRow> = {}): DebtLedgerRow {
  return { ...row, ...rest, projection: { ...row.projection!, ...p } };
}

function render(ledger: DebtLedgerRow[] = DEBT.ledger): void {
  const data: DebtResponse = { ...DEBT, ledger };
  renderWithProviders(<PayoffCard data={data} />);
}

function items(): HTMLElement[] {
  return within(screen.getByRole("list", { name: "Payoff at this pace" })).getAllByRole("listitem");
}

describe("Payoff at this pace", () => {
  it("shows the pace, the payoff month, interest to go and the last payment", () => {
    render();
    const [card, mortgage] = items();
    expect(card!.textContent).toContain("Pays down about $250 a month (Jul to Sep 2026 statements)");
    expect(card!.textContent).toContain("Paid off about Apr 2027");
    // Counted from this month, not the balance's.
    expect(card!.textContent).toContain("in 6 months");
    expect(card!.textContent).toContain("About $120 interest to go.");
    // The last payment is under half the pace: offer to finish a month sooner. A card says "pay it down".
    expect(card!.textContent).toContain("Pay it down about $120 in Apr 2027 to finish. Or pay it down about $367 in Mar 2027 to finish a month sooner.");
    expect(card!.textContent).not.toContain("Going by the balance");
    expect(mortgage!.textContent).toContain("Mar 2042");
    expect(mortgage!.textContent).toContain("in 15 years, 5 months");
    // A payoff more than a year out shows no last payment line.
    expect(mortgage!.textContent).not.toContain("Last payment");
    expect(mortgage!.textContent).toContain("On pace for the chat's payoff date, Jun 2049 (about 7 years, 3 months sooner).");
  });

  it("lists cards paid in full apart", () => {
    render([{ ...CARD, projection: null }, MORTGAGE]);
    expect(items()).toHaveLength(1);
    expect(screen.getByText("Paid in full each month, so nothing to project: Example card.")).toBeTruthy();
  });

  it("says when nothing is left to project", () => {
    render([{ ...CARD, projection: null }]);
    expect(screen.getByText("No balances to project.")).toBeTruthy();
  });

  it("warns when the pace misses a promo date, with what a month does", () => {
    const promo = withProjection(
      CARD,
      {
        promo_until: "2027-03-31",
        promo_month: "2027-03",
        targets: [{ kind: "promo", date: "2027-03-31", by_month: "2027-03", monthly_cents: 15_596, on_track: false }],
      },
      { rate_type: "promo" },
    );
    render([promo]);
    const [row] = items();
    expect(row!.textContent).toContain("0% until Mar 31, 2027, then 24.99%.");
    expect(row!.textContent).toContain("Not paid off before the promo ends Mar 31, 2027. Pay it down about $156 a month to clear it in time.");
  });

  it("says when the pace does not pay it off", () => {
    render([
      withProjection(CARD, { months: null, payoff_month: null, interest_to_go_cents: null, last_payment_cents: null, early_cents: null }),
      withProjection(MORTGAGE, { pace_cents: null, pace_basis: "payments", pace_from: "2026-08", months: null, payoff_month: null, targets: [] }),
    ]);
    const [card, mortgage] = items();
    expect(card!.textContent).toContain("Not at this pace");
    expect(card!.textContent).not.toContain("interest to go");
    expect(mortgage!.textContent).toContain("No payments in Aug to Sep 2026");
    expect(mortgage!.textContent).toContain("Not known");
  });

  it("gives no verdict without a pace, and the whole balance when no month is left", () => {
    const unknown = withProjection(
      CARD,
      {
        pace_cents: null,
        pace_basis: null,
        pace_from: null,
        pace_through: null,
        months: null,
        payoff_month: null,
        targets: [{ kind: "promo", date: "2028-01-31", by_month: "2028-01", monthly_cents: 9_000, on_track: null }],
      },
      { rate_type: "promo" },
    );
    const due = withProjection(
      CARD,
      { targets: [{ kind: "promo", date: "2026-10-31", by_month: "2026-09", monthly_cents: 150_000, on_track: null }] },
      { rate_type: "promo" },
    );
    render([unknown, due]);
    const [a, b] = items();
    expect(a!.textContent).toContain("To clear it before the promo ends Jan 31, 2028, pay it down about $90 a month.");
    expect(a!.querySelector(".text-warn")).toBeNull();
    expect(b!.textContent).toContain("Pay off the $1,500 before the promo ends Oct 31, 2026.");
    expect(b!.querySelector(".text-warn")).not.toBeNull();
  });

  it("says when the balance is old, and leaves out months gone by", () => {
    render([withProjection(CARD, { from_month: "2026-06", months: 3, payoff_month: "2026-09" })]);
    const [row] = items();
    expect(row!.textContent).toContain("Going by the balance from Jun 2026, the newest here.");
    expect(row!.textContent).toContain("Sep 2026");
    expect(row!.textContent).not.toContain(" in ");
    expect(row!.textContent).not.toContain("to finish");
  });

  it("says when the rate is not known", () => {
    render([{ ...CARD, apr_milli_pct: null }]);
    expect(items()[0]!.textContent).toContain("Rate not known, so interest is left out.");
  });

  it("tries a monthly amount and a finish month", async () => {
    const user = userEvent.setup();
    render([CARD]);
    // Each row's button and fields carry the account's name.
    const toggle = screen.getByRole("button", { name: "Try a payment for Example card" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    await user.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    await user.type(screen.getByLabelText("Pay down a month, Example card"), "500");
    expect(screen.getByText("Paid off about Jan 2027, in 3 months, about $66 interest.")).toBeTruthy();
    const month = screen.getByLabelText("Or finish by, Example card");
    expect(month.getAttribute("min")).toBe("2026-10");
    await user.type(month, "2026-12");
    expect(screen.getByText("Pay it down about $521 a month to finish by Dec 2026.")).toBeTruthy();
  });
});

describe("Payoff with a scheduled rate", () => {
  const NOV = { effective_on: "2026-11-01", apr_milli_pct: 19_990 };
  const MAY = { effective_on: "2027-05-01", apr_milli_pct: 17_990 };

  it("names a scheduled rate, and says rates hold unless one is scheduled", () => {
    render([{ ...CARD, rate_changes: [NOV] }, MORTGAGE]);
    const [card, mortgage] = items();
    expect(card!.textContent).toContain("Uses the scheduled rate: 19.99% from Nov 2026.");
    expect(mortgage!.textContent).not.toContain("scheduled");
    const heading = screen.getByRole("heading", { name: "Payoff at this pace" });
    expect(heading.closest("section")!.textContent).toContain("Rates are assumed to stay the same unless a change is scheduled.");
  });

  it("lists every scheduled rate, and none on a rate that is not variable", () => {
    expect(scheduleText({ ...CARD, rate_changes: [NOV, MAY] })).toBe("Uses the scheduled rates: 19.99% from Nov 2026 and 17.99% from May 2027.");
    expect(scheduleText({ ...CARD, rate_type: "fixed", rate_changes: [NOV] })).toBeNull();
    expect(scheduleText(CARD)).toBeNull();
  });

  it("follows the schedule in Try a payment", () => {
    const row = { ...CARD, rate_changes: [NOV] };
    // At a flat 24.99%: about $66 interest and $521 a month.
    expect(tryAmountText(row, "500", NOW)).toBe("Paid off about Jan 2027, in 3 months, about $59 interest.");
    expect(tryMonthText(row, "2026-12", NOW)).toBe("Pay it down about $519 a month to finish by Dec 2026.");
  });
});

describe("Payoff wording", () => {
  it("formats windows and durations", () => {
    expect(windowText("2026-09", "2026-09")).toBe("Sep 2026");
    expect(windowText("2026-07", "2026-09")).toBe("Jul to Sep 2026");
    expect(windowText("2026-11", "2027-01")).toBe("Nov 2026 to Jan 2027");
    expect(durationText(1)).toBe("1 month");
    expect(durationText(23)).toBe("23 months");
    expect(durationText(24)).toBe("2 years");
    expect(durationText(25)).toBe("2 years, 1 month");
    expect(fromNowText("2027-04", NOW)).toBe("in 6 months");
    expect(fromNowText(NOW, NOW)).toBe("this month");
    expect(fromNowText("2026-09", NOW)).toBeNull();
  });

  it("words the pace against the interest", () => {
    const pace = (p: Partial<Projection>, rest: Partial<DebtLedgerRow> = {}) => paceText(withProjection(CARD, p, rest));
    expect(pace({ pace_basis: "payments", pace_from: "2026-08" })).toBe("Pays down about $250 a month (payments in Aug to Sep 2026)");
    // $1,500 at 24.99% is about $31.24 interest a month.
    expect(pace({ pace_cents: 3_000 })).toBe("About $30 a month goes toward it, less than the interest: up about $1 a month (Jul to Sep 2026 statements)");
    expect(pace({ pace_cents: 3_124 })).toBe("About $31 a month goes toward it, all of it interest (Jul to Sep 2026 statements)");
    expect(pace({ pace_cents: 0 })).toBe("Not going down: up about $31 a month (Jul to Sep 2026 statements)");
    expect(pace({ pace_cents: -2_000 })).toBe("Not going down: up about $51 a month (Jul to Sep 2026 statements)");
    expect(pace({ pace_cents: 0 }, { apr_milli_pct: null })).toBe("Not going down (Jul to Sep 2026 statements)");
    expect(pace({ pace_cents: null, pace_basis: null, pace_from: null, pace_through: null })).toBe("No statements or payments to go on yet");
  });

  it("words a loan's last payment", () => {
    const near = withProjection(MORTGAGE, { months: 5, payoff_month: "2027-02", last_payment_cents: 5_000, early_cents: 155_000 });
    expect(lastPaymentText(near, NOW)).toBe("Last payment about $50 in Feb 2027. Or pay about $1,550 in Jan 2027 to finish a month sooner.");
    expect(lastPaymentText(withProjection(MORTGAGE, { months: 14, payoff_month: "2027-11" }), NOW)).toBeNull();
  });

  it("words a missed payoff date", () => {
    const p = MORTGAGE.projection!;
    expect(targetText({ kind: "payoff_date", date: "2049-06-01", by_month: "2049-06", monthly_cents: 117_176, on_track: false }, p)).toBe(
      "Behind the chat's payoff date, Jun 2049. Pay it down about $1,172 a month to make it.",
    );
    expect(targetText({ kind: "payoff_date", date: "2049-06-01", by_month: "2049-06", monthly_cents: 117_176, on_track: null }, p)).toBe(
      "To make the chat's payoff date, Jun 2049, pay it down about $1,172 a month.",
    );
  });

  it("checks what is typed", () => {
    expect(tryAmountText(CARD, "", NOW)).toBeNull();
    expect(tryAmountText(CARD, "abc", NOW)).toBe("Type an amount, like 1,500.");
    expect(tryAmountText(CARD, "0", NOW)).toBe("Type an amount, like 1,500.");
    // $10 is under a month's interest on the mortgage.
    expect(tryAmountText(MORTGAGE, "10", NOW)).toBe("Not paid off at that amount: a month's interest is about $750.");
    expect(tryAmountText({ ...CARD, projection: null }, "500", NOW)).toBeNull();
    expect(tryMonthText(CARD, "", NOW)).toBeNull();
    expect(tryMonthText(CARD, "Dec", NOW)).toBe("Pick a month, like 2027-12.");
    expect(tryMonthText(CARD, "2026-09", NOW)).toBe("Pick Oct 2026 or later.");
    // An old balance: never a month gone by.
    expect(tryMonthText(withProjection(CARD, { from_month: "2026-06" }), "2026-08", NOW)).toBe("Pick Oct 2026 or later.");
  });
});
