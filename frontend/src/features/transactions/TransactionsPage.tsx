import { keepPreviousData, useInfiniteQuery, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDownUp, Check, Search, SquareCheckBig, X } from "lucide-react";
import { useEffect, useMemo, useState, ViewTransition } from "react";
import { Link, useSearchParams } from "react-router";
import { PageHeader } from "../../components/shell/PageHeader";
import { Button } from "../../components/ui/button";
import { CategoryLabel } from "../../components/ui/category-label";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "../../components/ui/dropdown-menu";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorBanner } from "../../components/ui/error-banner";
import { SegmentedControl } from "../../components/ui/segmented-control";
import { Skeleton } from "../../components/ui/skeleton";
import { getAccounts, getCategories, getMeta, getTrips, queryKeys, tripKeys } from "../../lib/api";
import { cn } from "../../lib/cn";
import { formatDate, formatMonth } from "../../lib/format";
import { BOTTOM_BAR, LIST_ITEM, listChange } from "../../lib/motion";
import { SuggestionsLink } from "../ai/SuggestionsLink";
import { searchTransactions, type Sort, type TxnRow, type TxnSearchBody, txnKeys } from "./api";
import { BulkBar } from "./BulkBar";
import { INPUT } from "./controls";
import { FilterPopover, type Option } from "./FilterPopover";
import { parseFilters, SORTS, searchBody, type TxnFilterState, type View, writeFilters } from "./filters";
import { categoryIndex, dayBands, replaceRow } from "./model";
import { useDebounced } from "./RulePrompt";
import { SummaryPanel, SummaryStrip } from "./SummaryPanel";
import { TxnDrawer } from "./TxnDrawer";
import { TxnList } from "./TxnList";

const PAGE_SIZE = 100;

/**
 * Transactions (PLAN 8.2, Monarch's transactions list): search, filters kept
 * in the URL, date bands with day totals, the Summary panel, the detail
 * drawer, Edit multiple and the review queue. Search text stays in page state
 * and goes to the server in a POST body, never in the URL.
 */
export function TransactionsPage() {
  const [params, setParams] = useSearchParams();
  const state = parseFilters(params);
  const [q, setQ] = useState("");
  const query = useDebounced(q, 300);
  const queryClient = useQueryClient();

  const [open, setOpen] = useState<TxnRow | null>(null);
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [notice, setNotice] = useState<string | null>(null);

  const categories = useQuery({ queryKey: queryKeys.categories, queryFn: getCategories });
  const accounts = useQuery({ queryKey: queryKeys.accounts(), queryFn: () => getAccounts() });
  const meta = useQuery({ queryKey: queryKeys.meta, queryFn: getMeta });
  const trips = useQuery({ queryKey: tripKeys.page, queryFn: getTrips });

  const body = searchBody(state, query);
  const search = useInfiniteQuery({
    queryKey: txnKeys.search(body),
    queryFn: ({ pageParam }) => searchTransactions({ ...body, cursor: pageParam, limit: PAGE_SIZE, include_summary: pageParam === null }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
  });

  // Review queue counts, so the tabs say how much is waiting.
  const reviewBody = (extra: TxnSearchBody): TxnSearchBody => ({ limit: 1, ...extra });
  const reviewCount = useQuery({
    queryKey: txnKeys.reviewCount("review"),
    queryFn: () => searchTransactions(reviewBody({ needs_review: true })),
  });
  const uncategorizedCount = useQuery({
    queryKey: txnKeys.reviewCount("uncategorized"),
    queryFn: () => searchTransactions(reviewBody({ uncategorized: true })),
  });

  const pages = search.data?.pages ?? [];
  const rows = pages.flatMap((p) => p.rows);
  const days = pages.flatMap((p) => p.days);
  const summary = pages[0]?.summary ?? null;
  const byDate = state.sort === "date_desc" || state.sort === "date_asc";
  const bands = dayBands(rows, days, byDate);

  const index = useMemo(() => categoryIndex(categories.data), [categories.data]);
  const accountOptions: Option[] = (accounts.data?.groups ?? []).flatMap((g) =>
    g.accounts.map((a) => ({ key: a.account_key, label: a.display_name })),
  );
  const ownerOptions = useMemo(() => {
    const map = new Map<string, string>();
    for (const g of accounts.data?.groups ?? []) for (const a of g.accounts) map.set(a.owner, a.owner_label);
    for (const r of rows) if (r.owner && r.owner_label) map.set(r.owner, r.owner_label);
    return [...map].map(([key, label]) => ({ key, label })).sort((a, b) => a.label.localeCompare(b.label));
  }, [accounts.data, rows]);
  const tripOptions: Option[] = useMemo(() => {
    const d = trips.data;
    if (!d) return [];
    return [...d.active, ...d.completed, ...d.older].map((t) => ({ key: t.key, label: t.name })).sort((a, b) => a.label.localeCompare(b.label));
  }, [trips.data]);
  // While Travel's list loads, or if it fails, a loaded row can still name the trip.
  const tripsReady = trips.isSuccess;
  const tripName = state.trip ? (rows.find((r) => r.trip_key === state.trip)?.trip_name ?? null) : null;

  // Keep the selection to rows still on screen.
  const visible = new Set(rows.map((r) => r.txn_key));
  const selectedKeys = [...selected].filter((k) => visible.has(k));

  useEffect(() => {
    if (!notice) return;
    const t = setTimeout(() => setNotice(null), 6000);
    return () => clearTimeout(t);
  }, [notice]);

  function update(next: TxnFilterState) {
    setParams(writeFilters(next, params), { replace: true });
  }

  function toggleRow(key: string) {
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(key)) n.delete(key);
      else n.add(key);
      return n;
    });
  }

  function onUpdated(row: TxnRow) {
    setOpen(row);
    // Show the edit at once. The refetch that follows may move or drop the row.
    queryClient.setQueriesData<typeof search.data>({ queryKey: ["transactions", "search"] }, (data) =>
      data ? { ...data, pages: data.pages.map((p) => ({ ...p, rows: replaceRow(p.rows, row) })) } : data,
    );
  }

  const allSelected = rows.length > 0 && selectedKeys.length === rows.length;
  const count = (n: number | undefined) => (n ? ` (${n})` : "");
  const views = [
    { value: "all", label: "All" },
    { value: "review", label: `Review${count(reviewCount.data?.summary?.count)}` },
    { value: "uncategorized", label: `Uncategorized${count(uncategorizedCount.data?.summary?.count)}` },
  ] as const;

  return (
    <>
      <PageHeader
        title="Transactions"
        actions={
          <>
            <SuggestionsLink />
            <Link
              to="/settings/rules"
              className="hidden h-9 items-center rounded-lg border border-line bg-card px-4 text-sm font-medium text-fg no-underline hover:bg-hover md:inline-flex"
            >
              Rules
            </Link>
          </>
        }
      />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative min-w-0 flex-1 basis-56">
          <Search size={16} aria-hidden="true" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-secondary" />
          <input
            type="search"
            className={cn(INPUT, "pl-9")}
            placeholder="Search transactions"
            aria-label="Search transactions by merchant, statement text, notes, tags or category"
            value={q}
            maxLength={100}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
        <FilterPopover
          state={state}
          onChange={update}
          accounts={accountOptions}
          owners={ownerOptions}
          trips={tripOptions}
          tripsReady={tripsReady}
          tripName={tripName}
          categories={categories.data}
          thisMonth={meta.data?.this_month ?? null}
        />
      </div>
      <ActiveFilters
        state={state}
        onChange={update}
        index={index}
        accounts={accountOptions}
        owners={ownerOptions}
        trips={tripOptions}
        tripsReady={tripsReady}
        tripName={tripName}
      />

      {search.isError && !search.data && (
        <ErrorBanner message="Could not load transactions." onRetry={() => void search.refetch()} retrying={search.isFetching} />
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px] lg:items-start">
        <div className="hidden min-w-0 lg:sticky lg:top-4 lg:col-start-2 lg:row-start-1 lg:block">
          <SummaryPanel summary={summary} loading={search.isPending} />
        </div>
        <section
          className={cn(
            "min-w-0 rounded-card bg-card shadow-card lg:col-start-1 lg:row-start-1",
            search.isPlaceholderData && "opacity-60",
          )}
          aria-labelledby="txn-list-title"
        >
          <header className="flex flex-wrap items-center gap-2 px-4 pb-3 pt-4 md:px-6 md:pt-5">
            <h2 id="txn-list-title" className="m-0 mr-auto text-lg font-medium text-fg">
              Transactions
            </h2>
            <Button
              size="sm"
              className="min-w-11 justify-center md:min-w-0"
              aria-label={selecting ? "Done" : "Edit multiple"}
              onClick={() => {
                // Leaving Edit multiple slides the bulk bar away.
                if (selecting)
                  listChange(() => {
                    setSelecting(false);
                    setSelected(new Set());
                  });
                else {
                  setSelecting(true);
                  setSelected(new Set());
                }
              }}
            >
              {selecting ? <X size={16} aria-hidden="true" /> : <SquareCheckBig size={16} aria-hidden="true" />}
              <span className="hidden sm:inline">{selecting ? "Done" : "Edit multiple"}</span>
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button size="sm" className="min-w-11 justify-center md:min-w-0" aria-label={`Sort: ${SORTS.find((s) => s.value === state.sort)?.label ?? ""}`}>
                  <ArrowDownUp size={16} aria-hidden="true" />
                  <span className="hidden sm:inline">Sort</span>
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuLabel>Sort by</DropdownMenuLabel>
                <DropdownMenuRadioGroup value={state.sort} onValueChange={(v) => update({ ...state, sort: v as Sort })}>
                  {SORTS.map((s) => (
                    <DropdownMenuRadioItem key={s.value} value={s.value}>
                      {s.label}
                    </DropdownMenuRadioItem>
                  ))}
                </DropdownMenuRadioGroup>
              </DropdownMenuContent>
            </DropdownMenu>
            {summary && <SummaryStrip summary={summary} />}
            <div className="basis-full">
              <SegmentedControl<View>
                label="Review queue"
                value={state.view}
                options={views}
                onChange={(v) => update({ ...state, view: v })}
              />
            </div>
            {selecting && (
              <label className="flex min-h-11 basis-full cursor-pointer items-center gap-3 text-sm text-fg-secondary md:min-h-8">
                <input
                  type="checkbox"
                  className="h-4 w-4 accent-[var(--primary)]"
                  checked={allSelected}
                  onChange={() => setSelected(allSelected ? new Set() : new Set(rows.map((r) => r.txn_key)))}
                />
                Select all {rows.length} loaded
              </label>
            )}
          </header>
          {notice && (
            <p role="status" className="m-0 flex items-center gap-2 border-t border-line px-4 py-2 text-sm text-fg md:px-6">
              <Check size={16} aria-hidden="true" className="text-good" />
              {notice}
            </p>
          )}
          {search.isPending && <ListSkeleton />}
          {search.isSuccess && rows.length === 0 && (
            <div className="border-t border-line">
              <EmptyState title={state.view === "all" ? "No transactions match." : "Nothing waiting here."}>
                {state.view === "all" ? "Try fewer filters or other search words." : "Every row in this view is sorted."}
              </EmptyState>
            </div>
          )}
          {rows.length > 0 && (
            <div className="border-t border-line">
              <TxnList bands={bands} onOpen={setOpen} selecting={selecting} selected={selected} onToggle={toggleRow} />
            </div>
          )}
          {(search.hasNextPage || (summary && rows.length > 0)) && (
            <div className="flex flex-wrap items-center gap-3 border-t border-line px-4 py-3 text-sm text-fg-secondary md:px-6">
              {summary && (
                <span>
                  Showing {rows.length.toLocaleString("en-US")} of {summary.count.toLocaleString("en-US")}
                </span>
              )}
              {search.hasNextPage && (
                <Button size="sm" onClick={() => void search.fetchNextPage()} disabled={search.isFetchingNextPage}>
                  {search.isFetchingNextPage ? "Loading…" : "Load more"}
                </Button>
              )}
            </div>
          )}
        </section>
      </div>
      {selecting && <div className="h-28" aria-hidden="true" />}
      {selecting && (
        <ViewTransition {...BOTTOM_BAR}>
          <BulkBar
            selected={selectedKeys}
            categories={categories.data}
            onClear={() => setSelected(new Set())}
            onDone={(m) => {
              setNotice(m);
              setSelected(new Set());
            }}
          />
        </ViewTransition>
      )}
      <TxnDrawer row={open} categories={categories.data} onClose={() => setOpen(null)} onUpdated={onUpdated} />
    </>
  );
}

function ListSkeleton() {
  return (
    <div role="status" aria-label="Loading transactions" className="flex flex-col gap-3 border-t border-line px-4 py-4 md:px-6">
      {Array.from({ length: 6 }, (_, i) => (
        <Skeleton key={i} className="h-8" />
      ))}
    </div>
  );
}

interface Chip {
  id: string;
  label: string;
  remove: () => TxnFilterState;
}

function ActiveFilters({
  state,
  onChange,
  index,
  accounts,
  owners,
  trips,
  tripsReady,
  tripName,
}: {
  state: TxnFilterState;
  onChange: (s: TxnFilterState) => void;
  index: ReturnType<typeof categoryIndex>;
  accounts: Option[];
  owners: Option[];
  trips: Option[];
  tripsReady: boolean;
  tripName: string | null;
}) {
  const label = (opts: Option[], key: string) => opts.find((o) => o.key === key)?.label ?? key;
  const groupName = (key: string) => [...index.values()].find((c) => c.groupKey === key)?.groupName ?? key;
  const without = (list: string[], v: string) => list.filter((x) => x !== v);
  const chips: Chip[] = [
    ...state.accounts.map((k) => ({ id: `a-${k}`, label: label(accounts, k), remove: () => ({ ...state, accounts: without(state.accounts, k) }) })),
    ...state.groups.map((k) => ({ id: `g-${k}`, label: groupName(k), remove: () => ({ ...state, groups: without(state.groups, k) }) })),
    ...state.categories.map((k) => ({
      id: `c-${k}`,
      label: index.get(k)?.name ?? k,
      remove: () => ({ ...state, categories: without(state.categories, k) }),
    })),
    ...state.owners.map((k) => ({ id: `o-${k}`, label: label(owners, k), remove: () => ({ ...state, owners: without(state.owners, k) }) })),
    ...state.months.map((m) => ({ id: `m-${m}`, label: formatMonth(m), remove: () => ({ ...state, months: without(state.months, m) }) })),
  ];
  if (state.from) chips.push({ id: "from", label: `From ${formatDate(state.from)}`, remove: () => ({ ...state, from: null }) });
  if (state.to) chips.push({ id: "to", label: `To ${formatDate(state.to)}`, remove: () => ({ ...state, to: null }) });
  if (state.min) chips.push({ id: "min", label: `At least $${state.min}`, remove: () => ({ ...state, min: null }) });
  if (state.max) chips.push({ id: "max", label: `At most $${state.max}`, remove: () => ({ ...state, max: null }) });
  if (state.direction)
    chips.push({ id: "dir", label: state.direction === "out" ? "Money out" : "Money in", remove: () => ({ ...state, direction: null }) });
  if (state.transfer)
    chips.push({ id: "tr", label: state.transfer === "yes" ? "Transfers only" : "No transfers", remove: () => ({ ...state, transfer: null }) });
  if (state.reimbursable)
    chips.push({
      id: "re",
      label: state.reimbursable === "yes" ? "Reimbursable only" : "Not reimbursable",
      remove: () => ({ ...state, reimbursable: null }),
    });
  if (state.edited)
    chips.push({ id: "ed", label: state.edited === "yes" ? "Edited" : "Not edited", remove: () => ({ ...state, edited: null }) });
  if (state.hidden !== "exclude")
    chips.push({
      id: "hi",
      label: state.hidden === "only" ? "Hidden only" : "Hidden included",
      remove: () => ({ ...state, hidden: "exclude" }),
    });
  if (state.trip) {
    // Only call a trip missing once Travel's list has loaded.
    const name = trips.find((t) => t.key === state.trip)?.label ?? (tripsReady ? `${state.trip} (not in Travel)` : (tripName ?? state.trip));
    chips.push({ id: "trip", label: `Trip: ${name}`, remove: () => ({ ...state, trip: null }) });
  }
  if (chips.length === 0) return null;
  return (
    <ul aria-label="Filters in use" className="m-0 mb-3 flex list-none flex-wrap gap-2 p-0">
      {chips.map((c) => {
        const cat = c.id.startsWith("c-") ? index.get(c.id.slice(2)) : undefined;
        return (
          <ViewTransition key={c.id} {...LIST_ITEM}>
            <li>
              <button
                type="button"
                // A removed chip fades out and the others slide over.
                onClick={() => listChange(() => onChange(c.remove()))}
                className="inline-flex min-h-11 items-center gap-1 rounded-full border border-line bg-card px-3 text-sm text-fg hover:bg-hover md:min-h-8"
              >
                {cat ? <CategoryLabel name={c.label} emoji={cat.emoji} /> : c.label}
                <X size={14} aria-hidden="true" className="text-fg-secondary" />
                <span className="sr-only">Remove filter</span>
              </button>
            </li>
          </ViewTransition>
        );
      })}
    </ul>
  );
}
