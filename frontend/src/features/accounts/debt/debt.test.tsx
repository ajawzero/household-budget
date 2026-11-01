import { screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "../../../test/render";
import { AccountsPage } from "../AccountsPage";
import type { DebtLedgerRow, DebtResponse } from "../../../lib/api";
import { DEBT } from "../../recurring/fixtures";
import { DebtTab, interestLine, nextPriority, paidDownText, payingTone, payoffText, rateText, scheduledRateText, statementText, utilLevel } from "./DebtTab";

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

let fail = false;
let payload: DebtResponse = DEBT;
const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  const url = String(input);
  if (url === "/api/debt") return fail ? json(500, { detail: "Server error." }) : json(200, payload);
  return json(404, { detail: "Not found." });
});

/** DEBT with the card (row 0) and the mortgage (row 1) changed. */
function withRows(card: Partial<DebtLedgerRow>, mortgage: Partial<DebtLedgerRow> = {}, top: Partial<DebtResponse> = {}): DebtResponse {
  return { ...DEBT, ...top, ledger: [{ ...DEBT.ledger[0]!, ...card }, { ...DEBT.ledger[1]!, ...mortgage }] };
}

/** The desktop table's body rows. */
async function tableRows(): Promise<HTMLElement[]> {
  const table = await screen.findByRole("table", { name: "Debts by priority" });
  return within(table).getAllByRole("row").slice(1);
}

/** The phone list's items. */
async function phoneItems(): Promise<HTMLElement[]> {
  await screen.findByRole("table", { name: "Debts by priority" });
  return within(screen.getByRole("list", { name: "Debts by priority" })).getAllByRole("listitem");
}

beforeEach(() => {
  fail = false;
  payload = DEBT;
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Debt tab", () => {
  it("shows total debt, highest rate, cheapest debt and what was paid", async () => {
    renderWithProviders(<DebtTab />);
    const strip = await screen.findByRole("list", { name: "Debt totals" });
    await within(strip).findByText("$201,500");
    expect(within(strip).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "$201,500Total debt2 debts",
      "24.99%Highest rateExample card, $1,500 owed",
      "4.5%Cheapest debtExample mortgage, $200,000 owed",
      "$1,525Paid in Aug 2026$1,540 required",
    ]);
  });

  it("lists debts by priority with rate, required vs paying and progress", async () => {
    renderWithProviders(<DebtTab />);
    const table = await screen.findByRole("table", { name: "Debts by priority" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    const card = rows[0]!;
    expect(card.textContent).toContain("Example card");
    expect(card.textContent).toContain("Credit card · Example Bank");
    expect(card.textContent).toContain("Autopay: minimum");
    expect(card.textContent).toContain("$200 in installment plans");
    expect(card.textContent).toContain("$1,500.00");
    expect(card.textContent).toContain("24.99% variable");
    expect(card.textContent).toContain("$40.00");
    expect(within(card).getByText("Below required").className).toContain("text-bad");
    // The bar's level is in words too, not only its color.
    expect(card.textContent).toContain("30% of $5,000 limit · High");
    const loan = rows[1]!;
    expect(loan.textContent).toContain("Do not prepay");
    expect(loan.textContent).toContain("Covers it");
    expect(loan.textContent).toContain("Payoff Jun 2049");
    expect(loan.textContent).not.toContain("Paid off");
    expect(screen.getByText(/Tracked by payments, so no balance here: Example store card \(autopay: minimum\)\./)).toBeTruthy();
    const steps = screen.getByRole("heading", { name: "Payoff sequence" }).closest("section")!;
    expect(within(steps).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "1Pay off the promoBy March 2027",
      "2Pay the card in fullAfter that",
    ]);
  });

  it("names the table's scroll region and says below required in words on phones", async () => {
    renderWithProviders(<DebtTab />);
    const region = await screen.findByRole("region", { name: "Debts by priority" });
    expect(region.getAttribute("tabindex")).toBe("0");
    const list = screen.getByRole("list", { name: "Debts by priority" });
    const card = within(list).getAllByRole("listitem")[0]!;
    const below = within(card).getByText("Below required");
    expect(below.className).toContain("text-bad");
    expect(below.className).not.toContain("sr-only");
    // A long note from the chat wraps instead of pushing the row wide.
    const note = within(list).getByText("Do not prepay");
    expect(note.className).toContain("break-words");
    expect(note.className).not.toContain("whitespace-nowrap");
  });

  it("says what the subtitle numbers are and which debts the rate tiles leave out", async () => {
    renderWithProviders(<DebtTab />);
    const heading = await screen.findByRole("heading", { name: "Debts by priority" });
    expect(heading.closest("section")!.textContent).toContain(
      "Owed as of the latest statements. Paying is what went in from household accounts in Aug 2026, and interest is what was charged. Rate tiles leave out cards paid in full and promo rates.",
    );
  });

  it("shows interest for the paying month and this year, or Not known", async () => {
    renderWithProviders(<DebtTab />);
    const [card, loan] = await tableRows();
    const header = within(screen.getByRole("table", { name: "Debts by priority" })).getAllByRole("columnheader");
    expect(header.map((h) => h.textContent)).toEqual(["Debt", "Owed", "Rate", "Required", "Paying", "Interest", "Progress"]);
    // The Interest column is the 6th cell, after Paying.
    expect(card!.children[5]!.textContent).toBe("$25.00 in Aug 2026$180.00 this year");
    expect(loan!.children[5]!.textContent).toBe("Not known");
    const [phoneCard, phoneLoan] = await phoneItems();
    expect(phoneCard!.textContent).toContain("Interest $25.00 in Aug 2026 · $180.00 this year");
    expect(phoneLoan!.textContent).not.toContain("Interest");
  });

  it("says None when no interest was charged", async () => {
    payload = withRows({ interest_cents: 0, interest_ytd_cents: 0 });
    renderWithProviders(<DebtTab />);
    const [card] = await tableRows();
    expect(card!.children[5]!.textContent).toBe("None");
    expect((await phoneItems())[0]!.textContent).not.toContain("Interest");
  });

  it("builds the phone interest line from what is known", () => {
    const card = DEBT.ledger[0]!;
    expect(interestLine(card, "2026-08")).toBe("Interest $25.00 in Aug 2026 · $180.00 this year");
    expect(interestLine({ ...card, interest_cents: null }, "2026-08")).toBe("Interest $180.00 this year");
    expect(interestLine(card, null)).toBe("Interest $180.00 this year");
    expect(interestLine({ ...card, interest_cents: 0, interest_ytd_cents: 0 }, "2026-08")).toBeNull();
    expect(interestLine({ ...card, interest_cents: null, interest_ytd_cents: null }, "2026-08")).toBeNull();
    // A refund of interest nets against the charges.
    expect(interestLine({ ...card, interest_cents: -500 }, "2026-08")).toBe("Interest −$5.00 in Aug 2026 · $180.00 this year");
  });

  it("says Payoff for a date ahead, and Payoff was for a past one", async () => {
    expect(payoffText({ payoff_date: "2049-06-01", payoff_ahead: true })).toBe("Payoff Jun 2049");
    expect(payoffText({ payoff_date: "2026-03-01", payoff_ahead: false })).toBe("Payoff was Mar 2026");
    expect(payoffText({ payoff_date: null, payoff_ahead: false })).toBe("");
    payload = withRows({}, { payoff_ahead: false });
    renderWithProviders(<DebtTab />);
    const loan = (await tableRows())[1]!;
    expect(loan.textContent).toContain("Payoff was Jun 2049");
    expect(loan.textContent).not.toContain("Paid off");
  });

  it("shows a card's statement amount and due date, and was due once it is past", async () => {
    renderWithProviders(<DebtTab />);
    const [card, loan] = await tableRows();
    expect(card!.textContent).toContain("Statement $1,200.00 · due Oct 25");
    expect(loan!.textContent).not.toContain("Statement $");
    const [phoneCard, phoneLoan] = await phoneItems();
    expect(phoneCard!.textContent).toContain("Statement $1,200.00 · due Oct 25");
    expect(phoneLoan!.textContent).not.toContain("Statement $");
  });

  it("says was due when the statement's due date is before today", async () => {
    payload = withRows({}, {}, { as_of: "2026-10-26" });
    renderWithProviders(<DebtTab />);
    expect((await tableRows())[0]!.textContent).toContain("Statement $1,200.00 · was due Oct 25");
  });

  it("leaves the statement line out when nothing is owed on it or it is not a card", () => {
    const card = DEBT.ledger[0]!;
    expect(statementText(card, "2026-10-25")).toBe("Statement $1,200.00 · due Oct 25");
    expect(statementText({ ...card, type: "store_card" }, "2026-10-21")).toBe("Statement $1,200.00 · due Oct 25");
    expect(statementText({ ...card, statement_owed_cents: 0 }, "2026-10-21")).toBeNull();
    expect(statementText({ ...card, statement_owed_cents: -500 }, "2026-10-21")).toBeNull();
    expect(statementText({ ...card, statement_due_date: null }, "2026-10-21")).toBeNull();
    expect(statementText(DEBT.ledger[1]!, "2026-09-21")).toBeNull();
  });

  it("badges the next payoff priority and numbers the rest", async () => {
    renderWithProviders(<DebtTab />);
    const [card, loan] = await tableRows();
    expect(within(card!).getByText("Next priority")).toBeTruthy();
    expect(loan!.textContent).not.toMatch(/priority/i);
    expect((await phoneItems())[0]!.textContent).toContain("Next priority");
  });

  it("shows Priority N after the first, and no badge when none is set", async () => {
    payload = withRows({}, { priority: 2 });
    const { unmount } = renderWithProviders(<DebtTab />);
    const loan = (await tableRows())[1]!;
    expect(within(loan).getByText("Priority 2")).toBeTruthy();
    unmount();
    payload = withRows({ priority: null }, { priority: null });
    renderWithProviders(<DebtTab />);
    for (const row of await tableRows()) expect(row.textContent).not.toMatch(/priority/i);
    expect(nextPriority(payload.ledger)).toBeNull();
    expect(nextPriority(withRows({ priority: 3 }, { priority: 3 }).ledger)).toBe(3);
  });

  it("shows the chat's note and the servicer when it is not the lender", async () => {
    renderWithProviders(<DebtTab />);
    const [card, loan] = await tableRows();
    expect(card!.textContent).toContain("Promo rate ends in May.");
    expect(card!.textContent).not.toContain("Serviced by");
    expect(loan!.textContent).not.toContain("Promo rate ends in May.");
    expect(loan!.textContent).toContain("Serviced by Example Servicing Co");
    // A long note wraps on phones.
    const note = within((await phoneItems())[0]!).getByText("Promo rate ends in May.");
    expect(note.className).toContain("break-words");
  });

  it("hides the servicer when it is the institution again", async () => {
    payload = withRows({}, { servicer: "  example lender " });
    renderWithProviders(<DebtTab />);
    expect((await tableRows())[1]!.textContent).not.toContain("Serviced by");
  });

  it("shows a retry banner when it fails", async () => {
    fail = true;
    renderWithProviders(<DebtTab />);
    expect((await screen.findByRole("alert")).textContent).toContain("Could not load debts.");
  });

  it("is a tab on Accounts, kept in the URL", async () => {
    renderWithProviders(<AccountsPage />, { route: "/accounts?tab=debt" });
    expect(await screen.findByRole("table", { name: "Debts by priority" })).toBeTruthy();
    const nav = screen.getByRole("navigation", { name: "Accounts views" });
    expect(within(nav).getByRole("link", { name: "Debt" }).getAttribute("aria-current")).toBe("page");
    expect(within(nav).getByRole("link", { name: "Overview" }).getAttribute("aria-current")).toBeNull();
    // The overview's accounts request is not sent while the Debt tab is open.
    expect(fetchMock.mock.calls.map((c) => String(c[0])).some((u) => u.startsWith("/api/accounts"))).toBe(false);
  });

  it("shows a scheduled rate on a variable rate", async () => {
    payload = withRows({ rate_changes: [{ effective_on: "2026-11-01", apr_milli_pct: 19_990 }] });
    renderWithProviders(<DebtTab />);
    const [card, loan] = await tableRows();
    // The Rate column is the 3rd cell; it wraps at the dot.
    expect(card!.children[2]!.textContent).toBe("24.99% variable · 19.99% from Nov 2026");
    expect(loan!.children[2]!.textContent).toBe("4.5% fixed");
    const [phoneCard, phoneLoan] = await phoneItems();
    expect(phoneCard!.textContent).toContain("Rate 24.99% variable · 19.99% from Nov 2026");
    expect(phoneLoan!.textContent).not.toContain("Rate 4.5%");
  });

  it("shows only the next scheduled rate, and none unless the rate is variable and known", () => {
    const changes = [
      { effective_on: "2026-11-15", apr_milli_pct: 19_990 },
      { effective_on: "2027-05-01", apr_milli_pct: 17_990 },
    ];
    // The lender's month, not the first month the projection charges it.
    expect(rateText({ apr_milli_pct: 24_990, rate_type: "variable", rate_changes: changes })).toBe("24.99% variable · 19.99% from Nov 2026");
    expect(rateText({ apr_milli_pct: 4_500, rate_type: "fixed", rate_changes: changes })).toBe("4.5% fixed");
    expect(rateText({ apr_milli_pct: null, rate_type: "variable", rate_changes: changes })).toBe("Rate not known");
    expect(scheduledRateText({ apr_milli_pct: 24_990, rate_type: "variable", rate_changes: [] })).toBeNull();
  });

  it("shows how much of the original is paid down", async () => {
    payload = withRows({}, { original_cents: 25_000_000, paid_down_pct: "20.0" });
    renderWithProviders(<DebtTab />);
    const loan = (await tableRows())[1]!;
    expect(loan.textContent).toContain("20% paid down of $250,000");
    expect(loan.textContent).toContain("Payoff Jun 2049");
    const phoneLoan = (await phoneItems())[1]!;
    expect(phoneLoan.textContent).toContain("20% paid down of $250,000");
    expect(phoneLoan.textContent).toContain("Payoff Jun 2049");
  });

  it("says when more is owed than the original", async () => {
    payload = withRows({}, { original_cents: 15_000_000, paid_down_pct: null });
    renderWithProviders(<DebtTab />);
    const loan = (await tableRows())[1]!;
    const over = within(loan).getByText("Owes more than the $150,000 original");
    expect(over.className).toContain("text-warn");
    expect(loan.textContent).toContain("Payoff Jun 2049");
  });

  it("keeps the limit bar on a card with an original", async () => {
    payload = withRows({ original_cents: 100_000, paid_down_pct: null });
    renderWithProviders(<DebtTab />);
    const card = (await tableRows())[0]!;
    expect(card.textContent).toContain("30% of $5,000 limit");
    expect(card.textContent).not.toContain("original");
  });

  it("words the paid down line", () => {
    const loan = DEBT.ledger[1]!;
    expect(paidDownText(loan)).toBeNull();
    expect(paidDownText({ ...loan, original_cents: 25_000_000, paid_down_pct: "20.0" })).toBe("20% paid down of $250,000");
    expect(paidDownText({ ...loan, original_cents: 20_000_000, paid_down_pct: "0.0" })).toBe("0% paid down of $200,000");
    expect(paidDownText({ ...loan, original_cents: 15_000_000, paid_down_pct: null })).toBe("Owes more than the $150,000 original");
  });

  it("formats rates and paying tone", () => {
    expect(rateText({ apr_milli_pct: null })).toBe("Rate not known");
    expect(rateText({ apr_milli_pct: 4_500, rate_type: "fixed" })).toBe("4.5% fixed");
    expect(payingTone(DEBT.ledger[0]!)).toBe("bad");
    expect(payingTone(DEBT.ledger[1]!)).toBe("good");
    expect(payingTone({ ...DEBT.ledger[1]!, paying_cents: null })).toBeNull();
    expect(utilLevel(29.9)).toBeNull();
    expect(utilLevel(30)).toBe("High");
    expect(utilLevel(50)).toBe("Very high");
  });
});
