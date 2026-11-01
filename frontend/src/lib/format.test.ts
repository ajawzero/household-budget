import { describe, expect, it } from "vitest";
import { formatDate, formatMonth, formatMoney, MINUS } from "./format";

describe("formatMoney", () => {
  it("shows whole dollars by default", () => {
    expect(formatMoney(123456)).toBe("$1,235");
    expect(formatMoney(100)).toBe("$1");
    expect(formatMoney(0)).toBe("$0");
  });

  it("rounds half away from zero", () => {
    expect(formatMoney(150)).toBe("$2");
    expect(formatMoney(149)).toBe("$1");
    expect(formatMoney(-150)).toBe(`${MINUS}$2`);
  });

  it("shows cents when asked", () => {
    expect(formatMoney(123456, { cents: true })).toBe("$1,234.56");
    expect(formatMoney(5, { cents: true })).toBe("$0.05");
    expect(formatMoney(1005, { cents: true })).toBe("$10.05");
    expect(formatMoney(0, { cents: true })).toBe("$0.00");
  });

  it("uses a real minus sign, not a hyphen", () => {
    const text = formatMoney(-4250, { cents: true });
    expect(text).toBe("−$42.50");
    expect(text).not.toContain("-");
  });

  it("drops the sign when the shown value is zero", () => {
    expect(formatMoney(-30)).toBe("$0");
    expect(formatMoney(-0)).toBe("$0");
  });

  it("groups large values", () => {
    expect(formatMoney(123456789012)).toBe("$1,234,567,890");
    expect(formatMoney(-123456789012, { cents: true })).toBe(`${MINUS}$1,234,567,890.12`);
  });

  it("returns empty text for a value that is not a safe integer", () => {
    expect(formatMoney(1.5)).toBe("");
    expect(formatMoney(Number.NaN)).toBe("");
  });
});

describe("dates", () => {
  it("formats ISO dates without time zone shifts", () => {
    expect(formatDate("2026-01-01")).toBe("Jan 1, 2026");
    expect(formatDate("2026-09-30")).toBe("Sep 30, 2026");
    expect(formatMonth("2026-12")).toBe("Dec 2026");
  });

  it("passes odd values through", () => {
    expect(formatDate("not a date")).toBe("not a date");
    expect(formatDate(null)).toBe("");
  });
});

describe("compact and short formats", () => {
  it("formats compact money for axes", async () => {
    const { formatCompact } = await import("./format");
    expect(formatCompact(120_000_000)).toBe("$1.2M");
    expect(formatCompact(4_500_000)).toBe("$45K");
    expect(formatCompact(95_000)).toBe("$950");
    expect(formatCompact(-4_500_000)).toBe(`${MINUS}$45K`);
    expect(formatCompact(0)).toBe("$0");
    expect(formatCompact(-20)).toBe("$0");
  });

  it("formats short dates, months and percents", async () => {
    const { formatShortDate, formatMonthShort, formatPct } = await import("./format");
    expect(formatShortDate("2026-09-30")).toBe("Sep 30");
    expect(formatMonthShort("2026-01")).toBe("Jan");
    expect(formatPct("4.2")).toBe("4.2%");
    expect(formatPct(-3)).toBe(`${MINUS}3%`);
    expect(formatPct("10.0")).toBe("10%");
    expect(formatPct(17, true)).toBe("17.0%");
    expect(formatPct("51.8", true)).toBe("51.8%");
    expect(formatPct(null)).toBe("");
  });
});
