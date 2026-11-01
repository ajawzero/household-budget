import type { ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { Link } from "react-router";
import { EstBadge } from "../../../components/money/EstBadge";
import { Money } from "../../../components/money/Money";
import { formatDate, formatMoney } from "../../../lib/format";
import { MileageForm } from "./MileageForm";
import { milesText, milliPctText, useVehicles, type Vehicle, type VehicleParts } from "./api";

/**
 * Vehicle accounts in the account drawer: how the value is worked out, the
 * loans against it, and the quick "Update mileage" field.
 */
export function VehiclePanel({ accountKey }: { accountKey: string }) {
  const q = useVehicles();
  if (q.isPending) return <p className="m-0 text-sm text-fg-secondary">Loading the vehicle…</p>;
  if (q.isError) return <p className="m-0 text-sm text-bad">Could not load the vehicle details.</p>;
  const v = q.data.vehicles.find((x) => x.account_key === accountKey);
  if (!v) return null;
  return (
    <section aria-labelledby={`vehicle-${accountKey}`} className="flex flex-col gap-4">
      <h3 id={`vehicle-${accountKey}`} className="eyebrow m-0">
        Value and mileage
      </h3>
      <ValueBreakdown vehicle={v} />
      <MileageForm vehicle={v} className="rounded-lg border border-line p-4" />
      <Link
        to="/settings/vehicles"
        className="inline-flex min-h-11 items-center gap-1 self-start text-sm no-underline md:min-h-0"
      >
        Rates and conversion value
        <ChevronRight size={16} aria-hidden="true" />
      </Link>
    </section>
  );
}

/** The real value it starts from, the estimate and its parts, as plain lines. */
export function ValueBreakdown({ vehicle }: { vehicle: Vehicle }) {
  const lines: [string, ReactNode][] = [];
  if (vehicle.real) {
    lines.push([
      capitalize(vehicle.real.label),
      <>
        <Money cents={vehicle.real.value_cents} /> on {formatDate(vehicle.real.as_of)}
      </>,
    ]);
  }
  const hasConversion = !!vehicle.parts && vehicle.parts.conversion_cents > 0;
  // With a conversion the parts come first, then their total, so the lines add up.
  if (vehicle.parts && hasConversion) lines.push(...partLines(vehicle.parts));
  if (vehicle.estimate) {
    lines.push([
      hasConversion ? "Total now" : "Estimate now",
      <span className="inline-flex items-center gap-1.5">
        <Money cents={vehicle.estimate.value_cents} />
        <EstBadge note={vehicle.estimate.note} />
      </span>,
    ]);
  }
  if (vehicle.parts && !hasConversion) lines.push(...partLines(vehicle.parts));
  if (vehicle.parts && vehicle.parts.excess_miles !== null) {
    lines.push(["Mileage", mileageText(vehicle.parts.excess_miles, vehicle.parts.mileage_date)]);
  }
  for (const loan of vehicle.loans) {
    lines.push([
      loan.display_name,
      loan.balance_cents === null ? (
        "No balance"
      ) : (
        <span>
          <Money cents={-loan.balance_cents} /> owed
          {loan.estimated && <span className="text-fg-secondary"> (estimated)</span>}
        </span>
      ),
    ]);
  }
  return (
    <div className="flex flex-col gap-2">
      {lines.length > 0 && (
        <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
          {lines.map(([k, val]) => (
            <div key={k} className="contents">
              <dt className="text-fg-secondary">{k}</dt>
              <dd className="m-0 text-fg">{val}</dd>
            </div>
          ))}
        </dl>
      )}
      {vehicle.estimate && <p className="m-0 text-sm text-fg-secondary">{vehicle.estimate.note}</p>}
      {vehicle.note && <p className="m-0 text-sm text-fg-secondary">{vehicle.note}</p>}
    </div>
  );
}

function partLines(p: VehicleParts): [string, ReactNode][] {
  const out: [string, ReactNode][] = [];
  const hasConversion = p.conversion_cents > 0;
  out.push([
    hasConversion ? "Base vehicle (KBB) now" : "Depreciation",
    hasConversion
      ? `${formatMoney(p.chassis_cents)}, loses ${milliPctText(p.chassis_rate_milli_pct)} a year`
      : `${milliPctText(p.chassis_rate_milli_pct)} a year`,
  ]);
  if (hasConversion) {
    out.push([
      "Conversion now",
      `${formatMoney(p.conversion_cents)}` +
        (p.conversion_rate_milli_pct !== null ? `, loses ${milliPctText(p.conversion_rate_milli_pct)} a year` : ""),
    ]);
  }
  return out;
}

export function mileageText(excess: number, date: string | null): string {
  const when = date ? ` as of ${formatDate(date)}` : "";
  if (excess === 0) return `On pace${when}`;
  return `${milesText(Math.abs(excess))} ${excess > 0 ? "over" : "under"} pace${when}`;
}

function capitalize(s: string): string {
  return s ? s[0]!.toUpperCase() + s.slice(1) : s;
}
