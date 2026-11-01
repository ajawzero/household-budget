// Small text helpers for the Dashboard cards.
import { MINUS, formatMoney } from "../../lib/format";

/** "Good morning", "Good afternoon" or "Good evening" in household time. */
export function greeting(now: Date = new Date()): string {
  const hour = Number(
    new Intl.DateTimeFormat("en-US", { timeZone: "America/Chicago", hour: "numeric", hourCycle: "h23" }).format(now),
  );
  if (hour < 12) return "Good morning";
  if (hour < 17) return "Good afternoon";
  return "Good evening";
}

/** "Good morning, Alex" for the signed-in user, or "Welcome back" with no name. */
export function greetingTitle(name: string | null | undefined, now: Date = new Date()): string {
  const shown = name?.trim();
  return shown ? `${greeting(now)}, ${shown}` : "Welcome back";
}

/** APR in thousandths of a percent to "21.99%" (no trailing zeros). */
export function aprText(milliPct: number | null | undefined): string {
  if (milliPct === null || milliPct === undefined) return "";
  return `${(milliPct / 1000).toFixed(3).replace(/\.?0+$/, "")}%`;
}

/** A monthly change with a sign: "+$105 a month" or "−$50 a month". */
export function monthlyDeltaText(cents: number): string {
  const amount = formatMoney(Math.abs(cents));
  if (amount === "$0") return "No change";
  return `${cents > 0 ? "+" : MINUS}${amount} a month`;
}

/** Budget row "actual / target", with a plain word when there is no actual. */
export function actualOverTarget(actual: number | null, target: number): string {
  return `${actual === null ? "No actual" : formatMoney(actual)} / ${formatMoney(target)}`;
}
