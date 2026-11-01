import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { CashflowMonth } from "../../lib/api";
import { formatMoney } from "../../lib/format";
import { estimatesNote } from "../money/EstBadge";
import { AreaChart, estimateRuns } from "./AreaChart";
import { CashflowBars } from "./CashflowBars";
import { ChartCard, type ChartColumn } from "./ChartCard";
import { HBars } from "./HBars";
import { Meter, statusText } from "./Meter";
import { niceDomain, niceScale } from "./scale";
import { Sparkline } from "./Sparkline";
import { StackedBar } from "./StackedBar";

// Fictional numbers.
const MONTHS: CashflowMonth[] = [
  { month: "2026-08", income_cents: 900_000, spending_cents: 700_000, saved_invested_cents: 0, left_over_cents: 200_000, basis: "actual", complete: true },
  { month: "2026-09", income_cents: 900_000, spending_cents: 950_000, saved_invested_cents: 0, left_over_cents: -50_000, basis: "actual", complete: false },
  { month: "2026-10", income_cents: 920_000, spending_cents: 800_000, saved_invested_cents: 0, left_over_cents: 120_000, basis: "plan", complete: false },
];

const COLUMNS: ChartColumn<CashflowMonth>[] = [
  { header: "Month", cell: (m) => m.month },
  { header: "Income", cell: (m) => formatMoney(m.income_cents), numeric: true },
  { header: "Spending", cell: (m) => formatMoney(m.spending_cents), numeric: true },
];

function card(props: Partial<Parameters<typeof ChartCard<CashflowMonth>>[0]> = {}) {
  return (
    <ChartCard<CashflowMonth>
      title="Cash flow"
      ariaLabel="Income and spending by month"
      data={MONTHS}
      columns={COLUMNS}
      rowKey={(m) => m.month}
      {...props}
    >
      <div>chart here</div>
    </ChartCard>
  );
}

describe("ChartCard", () => {
  it("wraps the chart in role=img with the summary", () => {
    render(card());
    expect(screen.getByRole("img", { name: "Income and spending by month" }).textContent).toBe("chart here");
  });

  it("shows a real table of the same rows on demand", async () => {
    const user = userEvent.setup();
    render(card());
    const toggle = screen.getByRole("button", { name: "Show the numbers" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("table")).toBeNull();

    await user.click(toggle);
    const table = screen.getByRole("table");
    expect(screen.getByRole("button", { name: "Hide the numbers" }).getAttribute("aria-expanded")).toBe("true");
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(4); // header plus three months
    expect(rows[2]?.textContent).toBe("2026-09$9,000$9,500");
    expect(within(table).getAllByRole("rowheader")).toHaveLength(3);
  });

  it("shows the empty state and no toggle when there are no rows", () => {
    render(card({ data: [], emptyText: "No months yet." }));
    expect(screen.getByText("No months yet.")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("shows a skeleton while loading", () => {
    render(card({ data: undefined, loading: true }));
    expect(screen.getByRole("status", { name: "Loading" })).toBeTruthy();
  });

  it("shows an error", () => {
    render(card({ data: undefined, error: "Could not reach the server." }));
    expect(screen.getByRole("alert").textContent).toBe("Could not reach the server.");
  });

  it("dims the chart while refreshing", () => {
    render(card({ refreshing: true }));
    expect(screen.getByRole("img").className).toContain("opacity-60");
  });
});

describe("Sparkline", () => {
  const pts = (n: number) =>
    Array.from({ length: n }, (_, i) => ({ date: `2026-0${i + 1}-28`, balance_cents: 100_000 + i * 5000 }));

  it("hides under three points", () => {
    const { container } = render(<Sparkline points={pts(2)} />);
    expect(container.innerHTML).toBe("");
  });

  it("draws a line and a dashed start line", () => {
    const { container } = render(<Sparkline points={pts(4)} />);
    const svg = container.querySelector("svg")!;
    expect(svg.getAttribute("width")).toBe("120");
    expect(svg.getAttribute("height")).toBe("32");
    expect(svg.querySelector("line")?.getAttribute("stroke-dasharray")).toBeTruthy();
    expect(svg.querySelector("path")?.getAttribute("d")?.split("L")).toHaveLength(4);
    expect(svg.getAttribute("aria-label")).toBe("Balance from $1,000 to $1,150");
  });
});

describe("HBars", () => {
  it("lists every label and value as text", () => {
    render(
      <HBars
        rows={[
          { key: "a", label: "Retirement", cents: 5_000_000 },
          { key: "b", label: "Loans", cents: -2_000_000 },
        ]}
      />,
    );
    const items = screen.getAllByRole("listitem");
    expect(items[0]?.textContent).toBe("Retirement$50,000");
    expect(items[1]?.textContent).toBe("Loans−$20,000");
    expect(items[0]?.getAttribute("title")).toBeNull();
  });

  it("shows a share after the value when given, says what it is of on hover, and shows the note as a line", () => {
    render(
      <HBars
        rows={[
          { key: "a", label: "Retirement", cents: 5_000_000, share: 75, note: "Two accounts" },
          { key: "b", label: "Loans", cents: -2_000_000, share: null },
        ]}
        shareOf={() => "all assets"}
      />,
    );
    const items = screen.getAllByRole("listitem");
    expect(items[0]?.textContent).toBe("Retirement$50,000 75.0%Two accounts");
    expect(items[1]?.textContent).toBe("Loans−$20,000");
    // The note is a visible line, not only a hover tip, so phones see it.
    expect(items[0]?.querySelector("p")?.textContent).toBe("Two accounts");
    expect(items[0]?.getAttribute("title")).toBe("75.0% of all assets");
    expect(items[1]?.getAttribute("title")).toBeNull();
  });
});

describe("StackedBar", () => {
  const segs = [
    { key: "cash", label: "Cash", cents: 1_000_000, color: "var(--chart-leftover)" },
    { key: "inv", label: "Investments", cents: 3_000_000, color: "var(--chart-income)" },
  ];

  it("orders parts largest first with totals", () => {
    render(<StackedBar segments={segs} label="Assets" />);
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Investments$30,000", "Cash$10,000"]);
    expect(screen.getByRole("img", { name: "Assets by part" })).toBeTruthy();
  });

  it("shows percents in percent mode", () => {
    render(<StackedBar segments={segs} label="Assets" mode="percent" />);
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Investments75.0%", "Cash25.0%"]);
  });

  it("lists a negative part with its signed value instead of dropping it", () => {
    const withCredit = [...segs, { key: "card", label: "Card credit", cents: -3_000, color: "var(--chart-4)" }];
    render(<StackedBar segments={withCredit} label="Liabilities" />);
    const items = screen.getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toHaveLength(3);
    expect(items[2]).toBe("Card credit−$30 (not drawn in the bar)");
  });
});

describe("Meter", () => {
  it("says over in words with the amount", () => {
    render(<Meter actual={55_000} target={50_000} status="over" label="Dining" />);
    expect(screen.getByText("Over by $50")).toBeTruthy();
    expect(screen.getByRole("img").getAttribute("aria-label")).toBe("Dining: $550 of $500 target. Over by $50");
  });

  it("covers the other states", () => {
    expect(statusText(50_000, 50_000, "on_target")).toBe("On target");
    expect(statusText(30_000, 50_000, "under")).toBe("Under by $200");
    // The gap comes from the rounded figures: $213 of $300 is $87 under.
    expect(statusText(21_250, 30_000, "under")).toBe("Under by $87");
    expect(statusText(null, 50_000, "no_actual")).toBe("No actual yet");
  });
});

describe("Recharts charts", () => {
  it("renders the area chart at a fixed width", () => {
    const { container } = render(
      <AreaChart
        width={600}
        label="Net worth"
        points={[
          { date: "2026-09-01", value: 100_000 },
          { date: "2026-09-02", value: 120_000 },
        ]}
      />,
    );
    expect(container.querySelector("svg")).not.toBeNull();
    expect(container.querySelector("linearGradient")).not.toBeNull();
  });

  it("renders cash flow bars with a legend and hatches only plan months when mixed", () => {
    const { container } = render(<CashflowBars width={600} months={MONTHS} />);
    expect(screen.getByText("Left over")).toBeTruthy();
    expect(screen.getByText("Plan")).toBeTruthy();
    expect(container.querySelectorAll("pattern")).toHaveLength(2);
  });

  it("leaves the Plan legend out when every month is plan", () => {
    render(<CashflowBars width={600} months={MONTHS.map((m) => ({ ...m, basis: "plan" as const }))} />);
    expect(screen.queryByText("Plan")).toBeNull();
  });
});

describe("niceScale", () => {
  it("uses round ticks that include zero when the values cross it", () => {
    const { domain, ticks } = niceScale([-22_635_080, 53_000_000]);
    expect(ticks).toContain(0);
    expect(ticks[0]).toBe(domain[0]);
    expect(ticks[ticks.length - 1]).toBe(domain[1]);
    const steps = ticks.slice(1).map((t, i) => t - ticks[i]!);
    expect(new Set(steps).size).toBe(1);
  });
});

describe("niceDomain", () => {
  it("rounds outward and can include zero", () => {
    expect(niceDomain([120, 980])).toEqual([0, 1000]);
    expect(niceDomain([500, 900], { includeZero: true })).toEqual([0, 1000]);
    expect(niceDomain([-300, 900], { includeZero: true })).toEqual([-500, 1000]);
    expect(niceDomain([])).toEqual([0, 1]);
  });
});

describe("estimateRuns", () => {
  const pts = [
    { date: "2026-09-28", value: 1, estimated: true },
    { date: "2026-09-29", value: 1, estimated: false, real: true },
    { date: "2026-09-30", value: 1, estimated: true },
    { date: "2026-10-01", value: 1, estimated: true },
  ];
  it("finds every run, or only the one that reaches the end", () => {
    expect(estimateRuns(pts, "all")).toEqual([
      { from: "2026-09-28", to: "2026-09-28" },
      { from: "2026-09-30", to: "2026-10-01" },
    ]);
    expect(estimateRuns(pts, "tail")).toEqual([{ from: "2026-09-30", to: "2026-10-01" }]);
    expect(estimateRuns(pts.slice(0, 2), "tail")).toEqual([]);
    expect(estimateRuns(pts, "none")).toEqual([]);
  });

  it("names each estimated account and its base date for a total's badge", () => {
    expect(estimatesNote([{ display_name: "Van loan", based_on: "2026-09-30" }])).toBe(
      "Includes estimates: Van loan (from Sep 30). Each account's own est. note says how it was made.",
    );
  });
});
