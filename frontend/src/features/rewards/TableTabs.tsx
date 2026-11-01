// The Routing and Fees tabs: tables on wide screens, lists on phones.
import { Badge } from "../../components/ui/badge";
import { Card, CardHeader } from "../../components/ui/card";
import { TableScroll } from "../../components/ui/table-scroll";
import type { RewardFeeRow, RewardsResponse } from "../../lib/api";
import { formatDate, formatMoney } from "../../lib/format";
import { CardPending } from "./parts";

export function RoutingTab({ data, loading, error }: { data: RewardsResponse | undefined; loading: boolean; error: string | null }) {
  const rows = data?.routing;
  return (
    <Card>
      <CardHeader title="Card routing reference" subtitle="Which card to use for which spending." />
      {!rows ? (
        <CardPending loading={loading} error={error} rows={4} />
      ) : rows.length === 0 ? (
        <p className="m-0 text-sm text-fg-secondary">No routing yet.</p>
      ) : (
        <>
          {/* compact: no 640px floor, so the table fits at 768px and scrolls only when it must. */}
          <TableScroll className="hidden md:block" label="Card routing reference">
            <table className="data-table compact">
              <caption className="sr-only">Card routing reference</caption>
              <thead>
                <tr>
                  <th scope="col">Spend</th>
                  <th scope="col">Card</th>
                  <th scope="col">Earn</th>
                  <th scope="col">Note</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.key}>
                    <th scope="row" className="font-normal">
                      {r.spend}
                    </th>
                    <td>{r.account_name ?? r.account_key}</td>
                    <td>{r.earn ?? ""}</td>
                    <td className="text-fg-secondary">{r.note ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
          <ul className="m-0 flex list-none flex-col p-0 md:hidden" aria-label="Card routing reference">
            {rows.map((r) => (
              <li key={r.key} className="flex items-start justify-between gap-3 border-t border-line py-3 first:border-t-0 first:pt-0">
                <div className="min-w-0 flex-1">
                  <p className="m-0 font-medium text-fg">{r.spend}</p>
                  <p className="m-0 text-sm text-fg-secondary">
                    {r.account_name ?? r.account_key}
                    {r.note && ` · ${r.note}`}
                  </p>
                </div>
                {/* Free text from the chat: a long one wraps instead of pushing the row past the card. */}
                {r.earn && <span className="max-w-[40%] shrink-0 break-words text-right text-sm font-medium text-fg">{r.earn}</span>}
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

function money(cents: number): string {
  return formatMoney(cents, { cents: true });
}

function NetCell({ r }: { r: RewardFeeRow }) {
  return (
    <span className="inline-flex flex-wrap items-center justify-end gap-2">
      <span className={r.net_positive ? "text-good" : undefined}>{money(r.net_cents)}</span>
      {r.net_positive && <Badge tone="good">Net positive</Badge>}
    </span>
  );
}

function FeeName({ r }: { r: RewardFeeRow }) {
  return (
    <>
      <span className="block text-fg">{r.name}</span>
      <span className="block text-sm text-fg-secondary">
        {[r.kind === "membership" ? "Membership" : r.account_name, r.owner_name].filter(Boolean).join(" · ")}
      </span>
      {r.credits.length > 0 && (
        <ul className="m-0 mt-1 list-none p-0 text-sm text-fg-secondary" aria-label={`Credits on ${r.name}`}>
          {r.credits.map((c) => (
            <li key={c.key}>
              {c.name}: {money(c.cents)}
              {/* Only a counted credit worth less than its full value. A free night's full value is its counted value. */}
              {c.counted && c.full_cents > c.cents ? ` of ${money(c.full_cents)}` : ""}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

/** "Annual fees & memberships": gross, credits and net a year. */
export function FeesTab({ data, loading, error }: { data: RewardsResponse | undefined; loading: boolean; error: string | null }) {
  const fees = data?.fees;
  return (
    <Card>
      <CardHeader
        title="Annual fees & memberships"
        subtitle={`A year. Credits are what the card's credits are worth in ${fees?.year ?? "the year"}.`}
      />
      {!fees ? (
        <CardPending loading={loading} error={error} rows={3} />
      ) : fees.rows.length === 0 ? (
        <p className="m-0 text-sm text-fg-secondary">No annual fees or memberships.</p>
      ) : (
        <>
          <TableScroll className="hidden md:block" label="Annual fees and memberships">
            <table className="data-table compact">
              <caption className="sr-only">Annual fees and memberships</caption>
              <thead>
                <tr>
                  <th scope="col">Card / Service</th>
                  <th scope="col" className="num">
                    Gross/yr
                  </th>
                  <th scope="col" className="num">
                    Credits
                  </th>
                  <th scope="col" className="num">
                    Net/yr
                  </th>
                  <th scope="col">Renews</th>
                </tr>
              </thead>
              <tbody>
                {fees.rows.map((r) => (
                  <tr key={r.key}>
                    <th scope="row" className="font-normal">
                      <FeeName r={r} />
                    </th>
                    <td className="num">{money(r.gross_cents)}</td>
                    <td className="num">{r.credits_cents ? money(-r.credits_cents) : ""}</td>
                    <td className="num">
                      <NetCell r={r} />
                    </td>
                    <td className="whitespace-nowrap">{r.renews_on ? formatDate(r.renews_on) : ""}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <th scope="row">Total</th>
                  <td className="num font-medium">{formatMoney(fees.gross_cents)}</td>
                  <td className="num font-medium">{fees.credits_cents ? formatMoney(-fees.credits_cents) : ""}</td>
                  <td className="num font-medium">{formatMoney(fees.net_cents)}</td>
                  <td />
                </tr>
              </tfoot>
            </table>
          </TableScroll>
          <ul className="m-0 flex list-none flex-col p-0 md:hidden" aria-label="Annual fees and memberships">
            {fees.rows.map((r) => (
              <li key={r.key} className="border-t border-line py-3 first:border-t-0 first:pt-0">
                <FeeName r={r} />
                <dl className="m-0 mt-2 grid grid-cols-3 gap-2 text-sm">
                  <div>
                    <dt className="text-fg-secondary">Gross/yr</dt>
                    <dd className="m-0 text-fg">{money(r.gross_cents)}</dd>
                  </div>
                  <div>
                    <dt className="text-fg-secondary">Credits</dt>
                    <dd className="m-0 text-fg">{r.credits_cents ? money(-r.credits_cents) : "None"}</dd>
                  </div>
                  <div>
                    <dt className="text-fg-secondary">Net/yr</dt>
                    <dd className={r.net_positive ? "m-0 text-good" : "m-0 text-fg"}>{money(r.net_cents)}</dd>
                  </div>
                </dl>
                {r.net_positive && <Badge tone="good" className="mt-2">Net positive</Badge>}
                {r.renews_on && <span className="mt-1 block text-sm text-fg-secondary">Renews {formatDate(r.renews_on)}</span>}
              </li>
            ))}
            <li className="flex justify-between gap-3 border-t border-line pt-3 font-medium text-fg">
              <span>Net a year</span>
              <span>{formatMoney(fees.net_cents)}</span>
            </li>
          </ul>
        </>
      )}
    </Card>
  );
}
