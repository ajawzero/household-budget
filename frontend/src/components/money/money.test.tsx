import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MINUS } from "../../lib/format";
import { AsOf } from "./AsOf";
import { Delta, deltaTone } from "./Delta";
import { EstBadge } from "./EstBadge";
import { initials, Monogram } from "./Monogram";
import { Money } from "./Money";

describe("Money", () => {
  it("shows whole dollars by default and cents when asked", () => {
    const { container, rerender } = render(<Money cents={123_456} />);
    expect(container.textContent).toBe("$1,235");
    rerender(<Money cents={123_456} withCents />);
    expect(container.textContent).toBe("$1,234.56");
  });

  it("uses compact form for axes", () => {
    const { container } = render(<Money cents={120_000_000} compact />);
    expect(container.textContent).toBe("$1.2M");
  });

  it("says No balance for a missing value, never zero", () => {
    render(<Money cents={null} />);
    expect(screen.getByText("No balance")).toBeTruthy();
  });

  it("marks money in with a green plus and spoken text", () => {
    const { container } = render(<Money cents={4250} withCents flow />);
    expect(container.textContent).toBe("+$42.50 received");
    expect(container.firstElementChild?.className).toContain("text-good");
  });

  it("keeps a real minus sign on money out and says spent", () => {
    const { container } = render(<Money cents={-4250} withCents flow />);
    expect(container.textContent).toBe(`${MINUS}$42.50 spent`);
    expect(container.textContent).not.toContain("-");
  });

  it("uses tabular digits unless proportional", () => {
    const { container, rerender } = render(<Money cents={100} />);
    expect(container.firstElementChild?.className).toContain("tabular");
    rerender(<Money cents={100} proportional />);
    expect(container.firstElementChild?.className).not.toContain("tabular");
  });
});

describe("deltaTone", () => {
  it("reads higher_is_better on signed balances", () => {
    expect(deltaTone(500, "higher_is_better")).toBe("good");
    expect(deltaTone(-500, "higher_is_better")).toBe("bad");
    // A card balance goes from -1,000 to -800: the signed change is +200. Good.
    expect(deltaTone(-80_000 - -100_000, "higher_is_better")).toBe("good");
  });

  it("reads lower_is_better for spending", () => {
    expect(deltaTone(500, "lower_is_better")).toBe("bad");
    expect(deltaTone(-500, "lower_is_better")).toBe("good");
  });

  it("is neutral for zero or missing", () => {
    expect(deltaTone(0, "higher_is_better")).toBe("neutral");
    expect(deltaTone(null, "lower_is_better")).toBe("neutral");
  });
});

describe("Delta", () => {
  it("shows owed going down as a green down arrow", () => {
    // Signed change +200 on a card shown as an owed amount.
    const { container } = render(<Delta cents={20_000} display="owed" period="Year to date change" />);
    const value = container.querySelector(".text-good");
    expect(value).not.toBeNull();
    expect(value?.textContent).toContain("↓");
    expect(value?.textContent).toContain("down");
    expect(value?.textContent).toContain("$200");
    expect(container.textContent).toContain("Year to date change");
  });

  it("shows owed going up as a red up arrow", () => {
    const { container } = render(<Delta cents={-20_000} display="owed" />);
    const value = container.querySelector(".text-bad");
    expect(value?.textContent).toContain("↑");
  });

  it("shows spending up as bad with a diagonal arrow and percent", () => {
    const { container } = render(<Delta cents={5000} polarity="lower_is_better" pct="4.2" arrows="diagonal" />);
    const value = container.querySelector(".text-bad");
    expect(value?.textContent).toContain("↗");
    expect(value?.textContent).toContain("(4.2%)");
  });

  it("strips the sign from a negative percent (the arrow carries it)", () => {
    const { container } = render(<Delta cents={-5000} pct="-4.2" />);
    expect(container.textContent).toContain("(4.2%)");
    expect(container.textContent).toContain("↓");
  });

  it("says No change for zero", () => {
    const { container } = render(<Delta cents={0} />);
    expect(container.textContent).toBe("No change");
  });
});

describe("AsOf", () => {
  it("shows the short date and the full date for screen readers", () => {
    const { container } = render(<AsOf date="2026-09-30" />);
    expect(container.textContent).toContain("as of Sep 30");
    expect(container.textContent).toContain("as of Sep 30, 2026");
    expect(container.firstElementChild?.className).toContain("text-fg-secondary");
  });

  it("turns to the warning color when stale and says so", () => {
    const { container } = render(<AsOf date="2026-06-30" stale />);
    expect(container.firstElementChild?.className).toContain("text-warn");
    expect(container.textContent).toContain("stale");
  });

  it("renders nothing without a date", () => {
    const { container } = render(<AsOf date={null} />);
    expect(container.textContent).toBe("");
  });
});

describe("EstBadge", () => {
  it("shows est. and spells out the dates", () => {
    const { container } = render(<EstBadge priceDate="2026-10-20" statementDate="2026-09-30" />);
    expect(container.textContent).toContain("est.");
    expect(container.textContent).toContain("Prices from Oct 20, 2026.");
    expect(container.textContent).toContain("Last statement Sep 30, 2026.");
  });
});

describe("Monogram", () => {
  it("draws initials", () => {
    expect(initials("Example Credit Union")).toBe("EC");
    expect(initials("USBK")).toBe("US");
    expect(initials("Northwind")).toBe("N");
    expect(initials("The Sample Bank")).toBe("SB");
    expect(initials("")).toBe("?");
  });

  it("keeps the same color for the same name and hides from screen readers", () => {
    const { container } = render(
      <>
        <Monogram name="Example Bank" />
        <Monogram name="Example Bank" size={24} />
      </>,
    );
    const [a, b] = Array.from(container.querySelectorAll("span[aria-hidden]"));
    expect(a?.getAttribute("data-slot")).toBe(b?.getAttribute("data-slot"));
    expect(a?.className).toContain("h-10");
    expect(b?.className).toContain("h-6");
    expect(a?.getAttribute("style")).toContain("var(--");
    expect(a?.getAttribute("style")).not.toMatch(/#[0-9a-f]{3,6}/i);
  });

  it("never tints a monogram with the income, spending or left over color", () => {
    const names = ["Cobalt", "Orchard", "Pinecrest Mortgage", "Harbor Credit Union", "Lakeview", "Maple Bank", "Summit", "A", "B", "C"];
    const { container } = render(
      <>
        {names.map((n) => (
          <Monogram key={n} name={n} />
        ))}
      </>,
    );
    const slots = Array.from(container.querySelectorAll("span[data-slot]")).map((s) => s.getAttribute("data-slot"));
    expect(slots).toHaveLength(names.length);
    for (const slot of slots) expect(["--chart-income", "--chart-spending", "--chart-leftover"]).not.toContain(slot);
  });
});
