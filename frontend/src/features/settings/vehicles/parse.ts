// Text fields to the API's integer units. Each returns null for an empty
// field (use the default) and undefined when the text is not valid.

/** "12" or "0.5%" to milli-percent (12_000, 500). At most three decimals. */
export function parsePctMilli(text: string): number | null | undefined {
  const t = text.trim().replace(/%$/, "").trim();
  if (t === "") return null;
  if (!/^\d{1,3}(\.\d{1,3})?$/.test(t)) return undefined;
  const [whole, frac = ""] = t.split(".");
  return Number(whole) * 1000 + Number(frac.padEnd(3, "0"));
}

/** "$1,250" or "1250.50" to cents. */
export function parseDollarsCents(text: string): number | null | undefined {
  const t = text.trim().replace(/^\$/, "").replace(/,/g, "");
  if (t === "") return null;
  if (!/^\d{1,9}(\.\d{1,2})?$/.test(t)) return undefined;
  const [whole, frac = ""] = t.split(".");
  return Number(whole) * 100 + Number(frac.padEnd(2, "0"));
}

/** A whole number such as a year or miles. Commas are fine. */
export function parseWhole(text: string): number | null | undefined {
  const t = text.trim().replace(/,/g, "");
  if (t === "") return null;
  if (!/^\d{1,7}$/.test(t)) return undefined;
  return Number(t);
}

/** Milli-percent back to field text: 12_000 to "12", 500 to "0.5". */
export function milliToText(milli: number | null): string {
  if (milli === null) return "";
  return (milli / 1000).toFixed(3).replace(/\.?0+$/, "");
}

/** Cents back to field text: whole dollars when there are no cents. */
export function centsToText(cents: number | null): string {
  if (cents === null) return "";
  const whole = Math.floor(cents / 100);
  const rest = cents % 100;
  const text = whole.toLocaleString("en-US");
  return rest ? `${text}.${String(rest).padStart(2, "0")}` : text;
}
