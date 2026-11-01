import { RANGE_KEYS, type InvestmentRangeKey } from "../../lib/api";
import { SegmentedControl, type SegmentOption } from "./segmented-control";

const LABELS: Record<InvestmentRangeKey, [label: string, spoken: string]> = {
  "1D": ["1D", "1 day"],
  "7D": ["7D", "7 days"],
  "1M": ["1M", "1 month"],
  "3M": ["3M", "3 months"],
  "6M": ["6M", "6 months"],
  YTD: ["YTD", "Year to date"],
  "1Y": ["1Y", "1 year"],
  "3Y": ["3Y", "3 years"],
  ALL: ["All", "All time"],
};

/** Spoken range name, for change labels like "Year to date change". */
export function rangeLabel(key: InvestmentRangeKey): string {
  return LABELS[key][1];
}

interface RangePickerProps<K extends InvestmentRangeKey> {
  value: K;
  onChange: (value: K) => void;
  /** The ranges offered. Default: the shared ones (no 1D or 7D). */
  keys?: readonly K[];
  className?: string;
}

export function RangePicker<K extends InvestmentRangeKey>({ value, onChange, keys, className }: RangePickerProps<K>) {
  const options: SegmentOption<K>[] = (keys ?? (RANGE_KEYS as readonly InvestmentRangeKey[] as readonly K[])).map((k) => ({
    value: k,
    label: LABELS[k][0],
    ariaLabel: LABELS[k][1],
  }));
  // Up to 7 ranges fit a phone. The Investments chart's 9 scroll inside the control.
  return <SegmentedControl label="Date range" value={value} options={options} onChange={onChange} compact className={className} />;
}
