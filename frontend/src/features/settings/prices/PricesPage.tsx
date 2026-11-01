import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft } from "lucide-react";
import { type FormEvent, type ReactNode, useId, useState } from "react";
import { Link } from "react-router";
import { PageHeader } from "../../../components/shell/PageHeader";
import { Badge } from "../../../components/ui/badge";
import { Button } from "../../../components/ui/button";
import { Card, CardHeader } from "../../../components/ui/card";
import { EmptyState } from "../../../components/ui/empty-state";
import { ErrorBanner } from "../../../components/ui/error-banner";
import { TableScroll } from "../../../components/ui/table-scroll";
import { useMe } from "../../../lib/auth";
import { formatDate, formatShortDate, formatTimestamp } from "../../../lib/format";
import {
  CLASS_CHOICES,
  type ClassChoice,
  type ClassIn,
  classLabel,
  formatGap,
  getPricesStatus,
  getSymbols,
  investmentKeys,
  MODE_HELP,
  MODE_LABELS,
  type PriceMode,
  type PriceRun,
  type PricesStatus,
  putClass,
  putSymbol,
  type SymbolIn,
  type SymbolRow,
  type SymbolStatus,
} from "../../investments/api";
import { INPUT } from "../../transactions/controls";

const SOURCE_LABELS: Record<NonNullable<SymbolStatus["source"]>, string> = {
  tiingo: "Tiingo",
  yahoo: "Yahoo",
  constant: "Fixed",
};

const PROVIDER_LABELS: Record<PriceRun["stopped"][number], string> = { tiingo: "Tiingo", yahoo: "Yahoo" };

const SYMBOL_RE = /^[A-Z0-9.-]{1,12}$/;

/**
 * Settings, Prices (PLAN 6.2): whether the daily price run can run, how the
 * last one went, each ticker's newest close, the 1% statement check, and
 * every holding's price mode and class. Admins can set a mode, a ticker or a
 * class by hand.
 */
export function PricesPage() {
  const status = useQuery({ queryKey: investmentKeys.pricesStatus, queryFn: getPricesStatus });
  const symbols = useSymbolsQuery();
  const me = useMe();
  const isAdmin = me.data?.role === "admin";

  return (
    <>
      <PageHeader title="Prices" />
      <div className="mb-3">
        <Link to="/settings" className="inline-flex min-h-11 items-center gap-1 text-sm md:min-h-0">
          <ChevronLeft size={16} aria-hidden="true" />
          Settings
        </Link>
      </div>
      <div className="flex max-w-4xl flex-col gap-4 pb-8">
        {status.isError && !status.data && (
          <ErrorBanner message="Could not load the price status." onRetry={() => void status.refetch()} retrying={status.isFetching} />
        )}
        {status.isPending && <p className="m-0 text-fg-secondary">Loading…</p>}
        {status.data && <RunCard status={status.data} />}
        {status.data && <SanityCard status={status.data} />}
        {status.data && <TickersCard rows={status.data.symbols} />}
        <ModesCard query={symbols} isAdmin={isAdmin} />
      </div>
    </>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5 border-t border-line py-2 first:border-t-0 sm:flex-row sm:gap-4">
      <dt className="text-sm text-fg-secondary sm:w-40 sm:shrink-0">{label}</dt>
      <dd className="m-0 text-sm text-fg">{children}</dd>
    </div>
  );
}

function RunCard({ status }: { status: PricesStatus }) {
  const run = status.last_run;
  return (
    <Card>
      <CardHeader
        title="Daily price run"
        subtitle="Gets the prior day's closes for every ticker. Tiingo first, Yahoo when Tiingo fails. Only tickers leave the server."
      />
      <dl className="m-0">
        <Row label="Tiingo key">
          {status.tiingo_key_set ? (
            <Badge tone="good">Set</Badge>
          ) : (
            <span className="flex flex-col gap-1">
              <span>
                <Badge tone="warn">Not set</Badge>
              </span>
              <span className="text-fg-secondary">
                Add TIINGO_API_KEY to the server env file. Until then the run does nothing, and values move only with prices
                already stored.
              </span>
            </span>
          )}
        </Row>
        <Row label="Schedule">{status.schedule}</Row>
        <Row label="Last run">
          {run ? (
            <span className="flex flex-col gap-1">
              <span className="flex flex-wrap items-center gap-2">
                <Badge tone={run.status === "ok" ? "good" : "bad"}>{run.status === "ok" ? "OK" : "Failed"}</Badge>
                <span>{formatTimestamp(run.finished_at ?? run.started_at)}</span>
              </span>
              <span className="text-fg-secondary">
                Updated {run.updated} of {run.symbols} {run.symbols === 1 ? "ticker" : "tickers"}. {run.tiingo_calls} Tiingo{" "}
                {run.tiingo_calls === 1 ? "call" : "calls"}, {run.yahoo_calls} Yahoo {run.yahoo_calls === 1 ? "call" : "calls"}.
              </span>
              {run.stopped.length > 0 && (
                <span className="text-warn">
                  {run.stopped.map((p) => PROVIDER_LABELS[p]).join(" and ")} hit a rate limit and stopped until the next run.
                </span>
              )}
            </span>
          ) : (
            <span className="text-fg-secondary">No run yet.</span>
          )}
        </Row>
        <Row label="Last success">
          {status.last_success_at ? formatTimestamp(status.last_success_at) : <span className="text-fg-secondary">None yet</span>}
        </Row>
      </dl>
      <p className="m-0 mt-3 text-sm text-fg-secondary">
        To run it now on the server: <code>python -m app.cli prices</code>
      </p>
    </Card>
  );
}

function SanityCard({ status }: { status: PricesStatus }) {
  const s = status.sanity;
  return (
    <Card>
      <CardHeader
        title="1% check"
        subtitle="Each statement price is compared with the market close on the statement date. A gap over 1% usually means a wrong ticker."
      />
      <p className="m-0 text-sm text-fg">
        {s.checked} statement {s.checked === 1 ? "price" : "prices"} checked.
        {s.unchecked > 0 && ` ${s.unchecked} waiting on a close.`}
      </p>
      {s.flagged.length === 0 ? (
        <p className="m-0 mt-2 text-sm text-good">No gaps over 1%.</p>
      ) : (
        <TableScroll className="mt-3" label="1% check">
          <table className="data-table compact">
            <caption className="sr-only">Statement prices more than 1% from the market close</caption>
            <thead>
              <tr>
                <th scope="col">Holding</th>
                <th scope="col">Statement</th>
                <th scope="col" className="num">
                  Gap
                </th>
              </tr>
            </thead>
            <tbody>
              {s.flagged.map((f) => (
                <tr key={`${f.account_key}:${f.holding_key}:${f.statement_date}`}>
                  <th scope="row" className="font-normal">
                    <span className="block text-fg">
                      {f.name} <span className="text-fg-secondary">{f.symbol}</span>
                    </span>
                    <span className="block text-xs text-fg-secondary">{f.account_name}</span>
                  </th>
                  <td>
                    {formatDate(f.statement_date)}
                    {f.price_date !== f.statement_date && (
                      <span className="block text-xs text-fg-secondary">Close from {formatShortDate(f.price_date)}</span>
                    )}
                  </td>
                  <td className="num text-warn">{formatGap(f.gap_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
    </Card>
  );
}

function TickersCard({ rows }: { rows: SymbolStatus[] }) {
  return (
    <Card>
      <CardHeader title="Tickers" subtitle="Each ticker's newest stored close. Stale means no close in the last 5 days." />
      {rows.length === 0 ? (
        <EmptyState title="No tickers yet.">Holdings with a printed symbol show here after their first statement.</EmptyState>
      ) : (
        <TableScroll label="Tickers">
          <table className="data-table compact">
            <caption className="sr-only">Tickers and their newest close</caption>
            <thead>
              <tr>
                <th scope="col">Ticker</th>
                <th scope="col">Newest close</th>
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.symbol}>
                  <th scope="row" className="font-normal">
                    <span className="block font-medium text-fg">{r.symbol}</span>
                    <span className="block text-xs text-fg-secondary">
                      {r.holdings} {r.holdings === 1 ? "holding" : "holdings"}
                    </span>
                  </th>
                  <td>
                    {r.last_date ? formatShortDate(r.last_date) : <span className="text-fg-secondary">None</span>}
                    {r.source && <span className="block text-xs text-fg-secondary">{SOURCE_LABELS[r.source]}</span>}
                  </td>
                  <td>
                    <span className="flex flex-wrap gap-1">
                      {r.stale ? <Badge tone="warn">Stale</Badge> : <Badge tone="good">Current</Badge>}
                    </span>
                    {(r.error_label ?? r.error) && <span className="mt-1 block text-xs text-bad">{r.error_label ?? r.error}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
    </Card>
  );
}

function ModesCard({ query, isAdmin }: { query: ReturnType<typeof useSymbolsQuery>; isAdmin: boolean }) {
  const d = query.data;
  return (
    <Card>
      <CardHeader
        title="Holdings, price modes and classes"
        subtitle="Daily price uses the ticker the statement printed. Fixed $1 is for cash and money market funds. Statement keeps the statement's value until the next one. The class sorts a holding on Allocation."
      />
      {query.isPending && <p className="m-0 text-fg-secondary">Loading…</p>}
      {query.isError && !d && <p className="m-0 text-bad">{query.error.message}</p>}
      {d && d.rows.length === 0 && <EmptyState title="No holdings yet.">They come in with brokerage and retirement statements.</EmptyState>}
      {d && d.rows.length > 0 && (
        <ul className="m-0 list-none p-0" aria-label="Holdings, price modes and classes">
          {d.rows.map((r) => (
            <ModeItem key={`${r.account_key}:${r.holding_key}`} row={r} canEdit={isAdmin} />
          ))}
        </ul>
      )}
      {!isAdmin && d && d.rows.length > 0 && (
        <p className="m-0 mt-3 text-xs text-fg-secondary">Only an admin can change a price mode or a class.</p>
      )}
    </Card>
  );
}

function useSymbolsQuery() {
  return useQuery({ queryKey: investmentKeys.symbols, queryFn: getSymbols });
}

type ModeChoice = "rule" | PriceMode;

export function symbolProblem(mode: ModeChoice, symbol: string, printed: string | null): string | null {
  if (mode !== "api") return null;
  const s = symbol.trim().toUpperCase();
  if (!s) return printed ? null : "Type a ticker. This holding has no printed symbol.";
  if (!SYMBOL_RE.test(s)) return "Use up to 12 letters, digits, dots or dashes.";
  return null;
}

/** The PUT body for a choice. "rule" removes the override. */
export function symbolBody(row: SymbolRow, mode: ModeChoice, symbol: string, note: string): SymbolIn {
  if (mode === "rule") return { account_key: row.account_key, holding_key: row.holding_key, price_mode: null };
  const s = symbol.trim().toUpperCase();
  return {
    account_key: row.account_key,
    holding_key: row.holding_key,
    price_mode: mode,
    symbol: mode === "api" && s && s !== row.printed_symbol ? s : null,
    note: note.trim() || null,
  };
}

function ModeItem({ row, canEdit }: { row: SymbolRow; canEdit: boolean }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [mode, setMode] = useState<ModeChoice>(row.override?.price_mode ?? "rule");
  const [symbol, setSymbol] = useState(row.override?.symbol ?? "");
  const [note, setNote] = useState(row.override?.note ?? "");
  const [tried, setTried] = useState(false);
  const ids = { mode: useId(), symbol: useId(), note: useId(), problem: useId() };
  const problem = symbolProblem(mode, symbol, row.printed_symbol);

  const save = useMutation({
    mutationFn: (body: SymbolIn) => putSymbol(body),
    onSuccess: () => {
      setEditing(false);
      setTried(false);
      // Modes change holdings values, estimates and the price status.
      void queryClient.invalidateQueries({ queryKey: investmentKeys.all });
      void queryClient.invalidateQueries({ queryKey: ["accounts"] });
      void queryClient.invalidateQueries({ queryKey: ["account"] });
      void queryClient.invalidateQueries({ queryKey: ["networth-series"] });
    },
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    setTried(true);
    if (problem) return;
    save.mutate(symbolBody(row, mode, symbol, note));
  }

  function cancel() {
    setEditing(false);
    setTried(false);
    save.reset();
    setMode(row.override?.price_mode ?? "rule");
    setSymbol(row.override?.symbol ?? "");
    setNote(row.override?.note ?? "");
  }

  const set = row.override !== null;
  return (
    <li className="border-t border-line py-3 first:border-t-0">
      <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1 basis-56">
          <p className="m-0 text-sm font-medium text-fg">{row.name}</p>
          <p className="m-0 text-xs text-fg-secondary">
            {row.account_name}
            {row.printed_symbol && ` · printed ${row.printed_symbol}`}
            {row.cusip && ` · CUSIP ${row.cusip}`}
            {` · statement ${formatShortDate(row.last_statement_date)}`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge tone={set ? "accent" : "neutral"} title={MODE_HELP[row.mode]}>
            {MODE_LABELS[row.mode]}
          </Badge>
          {row.mode === "api" && row.symbol && <span className="font-medium text-fg">{row.symbol}</span>}
          <span className="text-xs text-fg-secondary">{set ? "set by hand" : "by rule"}</span>
          {canEdit && !editing && (
            <Button size="sm" variant="ghost" onClick={() => setEditing(true)} aria-label={`Change the price mode for ${row.name}, ${row.account_name}`}>
              Change
            </Button>
          )}
        </div>
      </div>
      {set && row.override?.note && !editing && <p className="m-0 mt-1 text-xs text-fg-secondary">Note: {row.override.note}</p>}
      <ClassLine row={row} canEdit={canEdit} />
      {editing && (
        <form
          onSubmit={submit}
          aria-label={`Price mode for ${row.name}`}
          className="mt-3 grid gap-3 rounded-lg bg-subtle p-3 md:grid-cols-[14rem_9rem_1fr] md:items-end"
        >
          <div>
            <label htmlFor={ids.mode} className="mb-1 block text-sm text-fg">
              Mode
            </label>
            <select id={ids.mode} className={INPUT} value={mode} onChange={(e) => setMode(e.target.value as ModeChoice)}>
              <option value="rule">Use the rule ({MODE_LABELS[row.rule_mode]})</option>
              <option value="api">{MODE_LABELS.api}</option>
              <option value="constant">{MODE_LABELS.constant}</option>
              <option value="statement">{MODE_LABELS.statement}</option>
            </select>
          </div>
          <div>
            <label htmlFor={ids.symbol} className="mb-1 block text-sm text-fg">
              Ticker
            </label>
            <input
              id={ids.symbol}
              className={INPUT}
              value={mode === "api" ? symbol : ""}
              placeholder={mode === "api" ? (row.printed_symbol ?? "") : ""}
              disabled={mode !== "api"}
              maxLength={12}
              autoCapitalize="characters"
              autoComplete="off"
              spellCheck={false}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              aria-invalid={(tried && problem !== null) || undefined}
              aria-describedby={tried && problem ? ids.problem : undefined}
            />
          </div>
          <div>
            <label htmlFor={ids.note} className="mb-1 block text-sm text-fg">
              Note <span className="text-fg-secondary">(optional)</span>
            </label>
            <input
              id={ids.note}
              className={INPUT}
              value={mode === "rule" ? "" : note}
              disabled={mode === "rule"}
              maxLength={200}
              autoComplete="off"
              onChange={(e) => setNote(e.target.value)}
            />
          </div>
          <p className="m-0 text-xs text-fg-secondary md:col-span-3">
            {mode === "rule" ? `The rule gives ${MODE_LABELS[row.rule_mode]}.` : MODE_HELP[mode]}
            {mode === "api" && row.printed_symbol && ` Leave the ticker empty to use ${row.printed_symbol}.`}
          </p>
          <div className="flex flex-wrap gap-2 md:col-span-3">
            <Button type="submit" variant="primary" disabled={save.isPending}>
              {save.isPending ? "Saving" : "Save"}
            </Button>
            <Button type="button" onClick={cancel}>
              Cancel
            </Button>
          </div>
          <div className="min-h-0 text-sm md:col-span-3" aria-live="polite">
            {tried && problem && (
              <p id={ids.problem} className="m-0 text-bad">
                {problem}
              </p>
            )}
            {save.isError && (
              <p role="alert" className="m-0 text-bad">
                {save.error.message}
              </p>
            )}
          </div>
        </form>
      )}
    </li>
  );
}

type ClassPick = "statement" | ClassChoice;

/** The PUT body for a class choice. "statement" removes the pick. */
export function classBody(row: SymbolRow, pick: ClassPick): ClassIn {
  return { account_key: row.account_key, holding_key: row.holding_key, asset_class: pick === "statement" ? null : pick };
}

/** The holding's class: the statement's, or an admin's pick, which wins for grouping only. */
function ClassLine({ row, canEdit }: { row: SymbolRow; canEdit: boolean }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [pick, setPick] = useState<ClassPick>(row.class_pick ?? "statement");
  const id = useId();
  const picked = row.class_pick !== null;
  const fromStatement = row.asset_class ? classLabel(row.asset_class) : "no class";

  const save = useMutation({
    mutationFn: (body: ClassIn) => putClass(body),
    onSuccess: () => {
      setEditing(false);
      // A class regroups Allocation, Holdings by class and the account drawer. No value moves.
      void queryClient.invalidateQueries({ queryKey: investmentKeys.all });
      void queryClient.invalidateQueries({ queryKey: ["account"] });
    },
  });

  function open() {
    save.reset();
    setPick(row.class_pick ?? "statement");
    setEditing(true);
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    save.mutate(classBody(row, pick));
  }

  return (
    <div className="mt-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-xs text-fg-secondary">Class</span>
        <Badge tone={picked ? "accent" : "neutral"}>{classLabel(row.class_pick ?? row.asset_class)}</Badge>
        <span className="text-xs text-fg-secondary">{picked ? "set by hand" : "from the statement"}</span>
        {canEdit && !editing && (
          <Button size="sm" variant="ghost" onClick={open} aria-label={`Change the class for ${row.name}, ${row.account_name}`}>
            Change
          </Button>
        )}
      </div>
      {editing && (
        <form
          onSubmit={submit}
          aria-label={`Class for ${row.name}`}
          className="mt-3 grid gap-3 rounded-lg bg-subtle p-3 md:grid-cols-[14rem_1fr] md:items-end"
        >
          <div>
            <label htmlFor={id} className="mb-1 block text-sm text-fg">
              Class
            </label>
            <select id={id} className={INPUT} value={pick} onChange={(e) => setPick(e.target.value as ClassPick)}>
              <option value="statement">From the statement ({fromStatement})</option>
              {CLASS_CHOICES.map((c) => (
                <option key={c} value={c}>
                  {classLabel(c)}
                </option>
              ))}
            </select>
          </div>
          <p className="m-0 text-xs text-fg-secondary">The class sorts the holding on Allocation. It never changes its value or its price mode.</p>
          <div className="flex flex-wrap gap-2 md:col-span-2">
            <Button type="submit" variant="primary" disabled={save.isPending}>
              {save.isPending ? "Saving" : "Save"}
            </Button>
            <Button type="button" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
          <div className="min-h-0 text-sm md:col-span-2" aria-live="polite">
            {save.isError && (
              <p role="alert" className="m-0 text-bad">
                {save.error.message}
              </p>
            )}
          </div>
        </form>
      )}
    </div>
  );
}
