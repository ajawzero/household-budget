import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { CashflowMonth } from "../../lib/api";
import { CashflowBars } from "./CashflowBars";
import { seriesFade } from "./SeriesLegend";

const MONTHS: CashflowMonth[] = [
  { month: "2026-08", income_cents: 900_000, spending_cents: 700_000, saved_invested_cents: 0, left_over_cents: 200_000, basis: "actual", complete: true },
];

describe("seriesFade", () => {
  it("leaves every series at full opacity when there is no focus", () => {
    expect(seriesFade(null, "income")).not.toContain("opacity-25");
  });

  it("dims a series when another key has focus", () => {
    expect(seriesFade("income", "spending")).toContain("opacity-25");
  });

  it("keeps the focused series at full opacity", () => {
    expect(seriesFade("income", "income")).not.toContain("opacity-25");
  });
});

describe("CashflowBars legend", () => {
  it("dims the other chart series while Income is hovered and restores them on leave", () => {
    const { container } = render(<CashflowBars width={600} months={MONTHS} />);
    const incomeLegend = screen.getByText("Income").closest("li");
    expect(incomeLegend).not.toBeNull();

    let bars = container.querySelectorAll(".recharts-bar");
    let line = container.querySelector(".recharts-line");
    expect(bars).toHaveLength(2);
    expect(bars[0]?.getAttribute("class")).not.toContain("opacity-25");
    expect(bars[1]?.getAttribute("class")).not.toContain("opacity-25");
    expect(line?.getAttribute("class")).not.toContain("opacity-25");

    fireEvent.mouseEnter(incomeLegend!);
    bars = container.querySelectorAll(".recharts-bar");
    line = container.querySelector(".recharts-line");
    expect(bars[0]?.getAttribute("class")).not.toContain("opacity-25");
    expect(bars[1]?.getAttribute("class")).toContain("opacity-25");
    expect(line?.getAttribute("class")).toContain("opacity-25");

    fireEvent.mouseLeave(incomeLegend!);
    bars = container.querySelectorAll(".recharts-bar");
    line = container.querySelector(".recharts-line");
    expect(bars[1]?.getAttribute("class")).not.toContain("opacity-25");
    expect(line?.getAttribute("class")).not.toContain("opacity-25");
  });
});
