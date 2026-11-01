import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { INVESTMENT_RANGE_KEYS, type RangeKey } from "../../lib/api";
import { CollapsibleGroup } from "./collapsible-group";
import { Drawer } from "./drawer";
import { RangePicker, rangeLabel } from "./range-picker";
import { SegmentedControl } from "./segmented-control";
import { TableScroll } from "./table-scroll";
import { Tooltip } from "./tooltip";

describe("RangePicker", () => {
  it("offers the seven shared ranges and reports a pick", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<RangePicker value="YTD" onChange={onChange} />);
    const group = screen.getByRole("radiogroup", { name: "Date range" });
    const items = group.querySelectorAll("button");
    expect(Array.from(items).map((b) => b.textContent)).toEqual(["1M", "3M", "6M", "YTD", "1Y", "3Y", "All"]);
    expect(screen.getByRole("radio", { name: "3 years" })).toBeTruthy();
    expect(screen.getByRole("radio", { name: "Year to date" }).getAttribute("aria-checked")).toBe("true");
    await user.click(screen.getByRole("radio", { name: "3 months" }));
    expect(onChange).toHaveBeenCalledWith("3M");
    expect(rangeLabel("ALL")).toBe("All time");
  });

  it("offers 1D and 7D only when asked (the Investments chart)", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<RangePicker value="1D" onChange={onChange} keys={INVESTMENT_RANGE_KEYS} />);
    const items = screen.getByRole("radiogroup", { name: "Date range" }).querySelectorAll("button");
    expect(Array.from(items).map((b) => b.textContent)).toEqual(["1D", "7D", "1M", "3M", "6M", "YTD", "1Y", "3Y", "All"]);
    expect(screen.getByRole("radio", { name: "1 day" }).getAttribute("aria-checked")).toBe("true");
    await user.click(screen.getByRole("radio", { name: "7 days" }));
    expect(onChange).toHaveBeenCalledWith("7D");
    expect(rangeLabel("1D")).toBe("1 day");
    // Range labels never wrap ("YT D" on a phone): 7 fit, 9 scroll inside the control.
    for (const b of Array.from(items)) expect(b.className).toContain("whitespace-nowrap");
  });

  it("keeps the choice when the selected item is clicked again", async () => {
    const user = userEvent.setup();
    function Harness() {
      const [v, setV] = useState<RangeKey>("1M");
      return <RangePicker value={v} onChange={setV} />;
    }
    render(<Harness />);
    await user.click(screen.getByRole("radio", { name: "1 month" }));
    expect(screen.getByRole("radio", { name: "1 month" }).getAttribute("aria-checked")).toBe("true");
  });
});

describe("SegmentedControl", () => {
  it("switches between options", async () => {
    const user = userEvent.setup();
    function Harness() {
      const [v, setV] = useState<"totals" | "percent">("totals");
      return (
        <SegmentedControl
          label="Show as"
          value={v}
          onChange={setV}
          options={[
            { value: "totals", label: "Totals" },
            { value: "percent", label: "Percent" },
          ]}
        />
      );
    }
    render(<Harness />);
    await user.click(screen.getByRole("radio", { name: "Percent" }));
    expect(screen.getByRole("radio", { name: "Percent" }).getAttribute("aria-checked")).toBe("true");
  });
});

describe("CollapsibleGroup", () => {
  it("folds its body with an aria-expanded header button", async () => {
    const user = userEvent.setup();
    render(
      <CollapsibleGroup title="Cash" summary={<span>$1,000</span>}>
        <p>Example Checking</p>
      </CollapsibleGroup>,
    );
    const header = screen.getByRole("button", { name: /Cash/ });
    expect(header.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("Example Checking")).toBeTruthy();
    await user.click(header);
    expect(header.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("Example Checking")).toBeNull();
    expect(screen.getByText("$1,000")).toBeTruthy();
  });
});

describe("Drawer", () => {
  it("is a labelled dialog that closes", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(
      <Drawer open onOpenChange={onOpenChange} title="Example Checking">
        <p>Details</p>
      </Drawer>,
    );
    expect(screen.getByRole("dialog", { name: "Example Checking" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});

describe("TableScroll", () => {
  it("is a labelled region the keyboard can reach", () => {
    render(
      <TableScroll label="Imported counts">
        <table>
          <tbody>
            <tr>
              <td>1</td>
            </tr>
          </tbody>
        </table>
      </TableScroll>,
    );
    const region = screen.getByRole("region", { name: "Imported counts" });
    expect(region.getAttribute("tabindex")).toBe("0");
    expect(region.className).toContain("table-scroll");
  });
});

describe("Tooltip", () => {
  it("opens and closes on a tap when asked to", async () => {
    const user = userEvent.setup();
    render(
      <Tooltip tap content="Hatched days are partial.">
        <button type="button" aria-label="About this chart">
          i
        </button>
      </Tooltip>,
    );
    const button = screen.getByRole("button", { name: "About this chart" });
    expect(screen.queryByRole("tooltip")).toBeNull();
    await user.pointer({ keys: "[TouchA]", target: button });
    expect(screen.getByRole("tooltip").textContent).toBe("Hatched days are partial.");
    await user.pointer({ keys: "[TouchA]", target: button });
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("closes a tapped tip on Escape", async () => {
    const user = userEvent.setup();
    render(
      <Tooltip tap content="A note.">
        <button type="button">Note</button>
      </Tooltip>,
    );
    await user.click(screen.getByRole("button", { name: "Note" }));
    expect(screen.getByRole("tooltip")).toBeTruthy();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("does not open on a tap without tap", async () => {
    const user = userEvent.setup();
    render(
      <Tooltip content="Hover only.">
        <button type="button">Hover</button>
      </Tooltip>,
    );
    await user.pointer({ keys: "[TouchA]", target: screen.getByRole("button", { name: "Hover" }) });
    expect(screen.queryByRole("tooltip")).toBeNull();
  });
});
