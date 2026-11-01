// Small helpers for chart axes. Values are cents.

export interface NiceScale {
  domain: [number, number];
  /** Round values, all multiples of one step, so 0 is a tick whenever the domain spans it. */
  ticks: number[];
}

/** A rounded [min, max] around the data with a little air, plus round ticks. */
export function niceScale(values: number[], { includeZero = false } = {}): NiceScale {
  if (values.length === 0) return { domain: [0, 1], ticks: [0, 1] };
  let lo = Math.min(...values);
  let hi = Math.max(...values);
  if (includeZero) {
    lo = Math.min(lo, 0);
    hi = Math.max(hi, 0);
  }
  if (lo === hi) {
    const pad = Math.abs(lo) * 0.1 || 100_00;
    return { domain: [lo - pad, hi + pad], ticks: [lo - pad, lo, hi + pad] };
  }
  const step = niceStep((hi - lo) / 4);
  const nLo = Math.floor(lo / step) * step;
  const nHi = Math.ceil(hi / step) * step;
  const ticks: number[] = [];
  // Count steps as integers so float drift never skips the last tick.
  for (let i = 0; i <= Math.round((nHi - nLo) / step); i++) ticks.push(nLo + i * step);
  return { domain: [nLo, nHi], ticks };
}

/** Just the domain of `niceScale`. */
export function niceDomain(values: number[], opts: { includeZero?: boolean } = {}): [number, number] {
  return niceScale(values, opts).domain;
}

/** True when the domain has values on both sides of zero. */
export function crossesZero([lo, hi]: [number, number]): boolean {
  return lo < 0 && hi > 0;
}

function niceStep(raw: number): number {
  const exp = 10 ** Math.floor(Math.log10(raw));
  const f = raw / exp;
  const nice = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
  return nice * exp;
}
