import { useQuery } from "@tanstack/react-query";
import { Circle, CircleAlert, CircleCheck, Info, TriangleAlert } from "lucide-react";
import { useId, useState } from "react";
import { Link } from "react-router";
import { PageHeader } from "../../components/shell/PageHeader";
import { Badge, type BadgeTone } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { Card, CardHeader } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { ErrorBanner } from "../../components/ui/error-banner";
import {
  type FlagAction,
  type FlagItem,
  type FlagsResponse,
  type FlagSeverity,
  type FlagStatus,
  getFlags,
  getPending,
  getTodos,
  type PendingItem,
  recurringKeys,
  type TodoListItem,
} from "../../lib/api";
import { cn } from "../../lib/cn";
import { formatDate, formatShortDate } from "../../lib/format";
import { ListSkeleton, SourceLine } from "../recurring/parts";

// Flags & To do (PLAN 8.2): the chat's flags, to do list and pending items. Read only in v1.

export const SEVERITY_TEXT: Record<FlagSeverity, string> = { high: "High", medium: "Medium", low: "Low" };
const SEVERITY_ORDER: FlagSeverity[] = ["high", "medium", "low"];

export const STATUS_TEXT: Record<FlagStatus, string> = { open: "Open", watching: "Watching", resolved: "Resolved" };
const STATUS_TONE: Record<FlagStatus, BadgeTone> = { open: "accent", watching: "neutral", resolved: "good" };

const KIND_TEXT: Record<FlagItem["kind"], string> = {
  debt: "Debt",
  tax: "Tax",
  budget: "Budget",
  subscription: "Subscription",
  fraud: "Fraud",
  travel: "Travel",
  rewards: "Rewards",
  cost_cutting: "Cost cutting",
  milestone: "Milestone",
  retirement: "Retirement",
  investment: "Investment",
  insurance: "Insurance",
  medical: "Medical",
  payroll: "Payroll",
  home: "Home",
  other: "Other",
};

// What to do about a flag (format 1.4). The word carries the meaning, so the badge is neutral.
// An info flag has no badge: it sits in its own group.
const ACTION: Record<Exclude<FlagAction, "info">, { text: string; hint: string }> = {
  act: { text: "Act", hint: "Something to do" },
  ask: { text: "Ask", hint: "Something to ask about" },
  reimburse: { text: "Reimburse", hint: "Money to claim back" },
};

/** Updates shown before "Show N earlier". The API sends them newest first. */
export const UPDATES_SHOWN = 3;

/** An info flag not resolved: nothing to do, so it is not counted as open. */
const isInfo = (f: FlagItem) => f.action === "info" && f.status !== "resolved";

const RELATED_TEXT: Record<string, string> = {
  account: "account",
  recurring: "bill",
  plan: "plan",
  budget: "budget",
  todo: "to do",
  pending: "pending item",
  trip: "trip",
  category: "category",
  person: "person",
};

/** The related item's name. A trip links to Travel, or shows its key when the app has no such trip. */
function RelatedName({ flag }: { flag: FlagItem }) {
  if (flag.related_kind === "trip" && flag.related_key) {
    if (!flag.related_label) return <>{flag.related_key} (not in Travel)</>;
    return <Link to={`/travel?trip=${encodeURIComponent(flag.related_key)}`}>{flag.related_label}</Link>;
  }
  return <>{flag.related_label ?? flag.related_key}</>;
}

export function kindText(kind: string): string {
  return KIND_TEXT[kind as FlagItem["kind"]] ?? kind.replace(/_/g, " ");
}

function SeverityIcon({ severity }: { severity: FlagSeverity }) {
  if (severity === "high") return <CircleAlert size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-bad" />;
  if (severity === "medium") return <TriangleAlert size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-warn" />;
  return <Info size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-fg-secondary" />;
}

export interface FlagGroup {
  key: string;
  title: string;
  note?: string;
  flags: FlagItem[];
}

/**
 * Open and watching flags by severity, then info flags, then resolved, then archived.
 * Keeps the server's order inside a group.
 */
export function groupFlags(flags: FlagItem[]): FlagGroup[] {
  const live = flags.filter((f) => !f.archived);
  const groups: FlagGroup[] = SEVERITY_ORDER.map((s) => ({
    key: s,
    title: `${SEVERITY_TEXT[s]} severity`,
    flags: live.filter((f) => f.status !== "resolved" && !isInfo(f) && f.severity === s),
  }));
  groups.push({
    key: "info",
    title: "For information",
    note: "Nothing to do. Not counted as open.",
    flags: live.filter(isInfo),
  });
  groups.push({ key: "resolved", title: "Resolved", flags: live.filter((f) => f.status === "resolved") });
  groups.push({ key: "archived", title: "Archived", flags: flags.filter((f) => f.archived) });
  return groups.filter((g) => g.flags.length > 0);
}

/** The flag's dated notes, newest 3 first, with a toggle for the earlier ones. */
function FlagUpdates({ flag }: { flag: FlagItem }) {
  const [all, setAll] = useState(false);
  const id = useId();
  const earlier = flag.updates.length - UPDATES_SHOWN;
  const shown = all ? flag.updates : flag.updates.slice(0, UPDATES_SHOWN);
  return (
    <div className="mt-2">
      <p id={`${id}-h`} className="eyebrow m-0 mb-1">
        Updates
      </p>
      <ol id={`${id}-list`} aria-labelledby={`${id}-h`} className="m-0 flex list-none flex-col gap-1 p-0">
        {shown.map((u, i) => (
          <li key={`${u.on}-${i}`} className="break-words text-sm text-fg">
            <span className="text-fg-secondary">{formatDate(u.on)}</span> · {u.text}
          </li>
        ))}
      </ol>
      {earlier > 0 && (
        <button
          type="button"
          className="inline-flex h-11 items-center rounded-lg px-1 text-sm font-medium text-accent-text hover:underline md:h-8"
          aria-expanded={all}
          aria-controls={`${id}-list`}
          onClick={() => setAll((v) => !v)}
        >
          {all ? "Hide earlier" : `Show ${earlier} earlier`}
          <span className="sr-only"> updates: {flag.title}</span>
        </button>
      )}
    </div>
  );
}

function FlagRow({ flag }: { flag: FlagItem }) {
  const resolved = flag.status === "resolved";
  const info = isInfo(flag);
  const action = !resolved && flag.action !== "info" ? ACTION[flag.action] : null;
  return (
    <li className="flex gap-3 border-t border-line py-4 first:border-t-0 first:pt-0">
      {info ? (
        <Info size={18} aria-hidden="true" className="mt-0.5 shrink-0 text-fg-secondary" />
      ) : (
        <SeverityIcon severity={flag.severity} />
      )}
      <div className="min-w-0 flex-1">
        <p className={cn("m-0 break-words text-base font-medium", resolved ? "text-fg-secondary" : "text-fg")}>
          <span className="sr-only">{info ? "For information" : `${SEVERITY_TEXT[flag.severity]} severity`}: </span>
          {flag.title}
        </p>
        <div className="mt-1 flex flex-wrap gap-1">
          {!info && <Badge tone={STATUS_TONE[flag.status]}>{STATUS_TEXT[flag.status]}</Badge>}
          {action && <Badge title={action.hint}>{action.text}</Badge>}
          <Badge>{kindText(flag.kind)}</Badge>
          {flag.due_date &&
            !resolved &&
            (info ? (
              <Badge title="The date this is about">On {formatDate(flag.due_date)}</Badge>
            ) : (
              <Badge tone={flag.overdue ? "bad" : "neutral"}>
                {flag.overdue ? "Overdue, was due" : "Due"} {formatDate(flag.due_date)}
              </Badge>
            ))}
          {flag.archived && <Badge>Archived</Badge>}
        </div>
        {flag.body && <p className="m-0 mt-2 whitespace-pre-line break-words text-sm text-fg">{flag.body}</p>}
        {flag.updates.length > 0 && <FlagUpdates flag={flag} />}
        <p className="m-0 mt-1 text-sm text-fg-secondary">
          {flag.related_kind && (
            <>
              About the {RELATED_TEXT[flag.related_kind] ?? flag.related_kind} <RelatedName flag={flag} />
              {" · "}
            </>
          )}
          Added {formatShortDate(flag.created_on)}
          {flag.updates[0] && ` · Updated ${formatShortDate(flag.updates[0].on)}`}
          {flag.resolved_on && ` · Resolved ${formatShortDate(flag.resolved_on)}`}
        </p>
      </div>
    </li>
  );
}

function FlagsCard({
  data,
  loading,
  showArchived,
  onToggleArchived,
}: {
  data: FlagsResponse | undefined;
  loading: boolean;
  showArchived: boolean;
  onToggleArchived: () => void;
}) {
  const groups = data ? groupFlags(data.flags) : [];
  const c = data?.counts;
  return (
    <Card>
      <CardHeader
        title="Flags"
        subtitle={
          c
            ? [
                `${c.open} open`,
                `${c.watching} watching`,
                ...(c.info ? [`${c.info} for information`] : []),
                `${c.resolved} resolved`,
              ].join(", ")
            : undefined
        }
        actions={
          data && data.archived_count > 0 ? (
            <Button size="sm" onClick={onToggleArchived} aria-pressed={showArchived}>
              {showArchived ? "Hide archived" : `Show ${data.archived_count} archived`}
            </Button>
          ) : undefined
        }
      />
      {!data ? (
        loading ? <ListSkeleton /> : <EmptyState title="Flags did not load." />
      ) : groups.length === 0 ? (
        <EmptyState title="No flags.">The chat raises flags with each monthly run.</EmptyState>
      ) : (
        <div className="flex flex-col gap-6">
          {groups.map((g) => (
            <section key={g.key} aria-labelledby={`flags-${g.key}`}>
              <h3 id={`flags-${g.key}`} className="m-0 mb-3 flex items-center gap-2 text-sm font-semibold text-fg">
                {g.title}
                <span className="font-normal text-fg-secondary">{g.flags.length}</span>
              </h3>
              {g.note && <p className="m-0 mb-3 text-sm text-fg-secondary">{g.note}</p>}
              <ul className="m-0 list-none p-0">
                {g.flags.map((f) => (
                  <FlagRow key={f.key} flag={f} />
                ))}
              </ul>
            </section>
          ))}
          {!showArchived && data.archived_count > 0 && (
            <p className="m-0 text-sm text-fg-secondary">
              {data.archived_count} archived {data.archived_count === 1 ? "flag is" : "flags are"} hidden.
            </p>
          )}
        </div>
      )}
    </Card>
  );
}

function TodoRow({ todo }: { todo: TodoListItem }) {
  const done = todo.status === "done";
  return (
    <li className="flex items-start gap-3 border-t border-line py-3 first:border-t-0 first:pt-0">
      {done ? (
        <CircleCheck size={18} className="mt-0.5 shrink-0 text-good" aria-hidden="true" />
      ) : (
        <Circle size={18} className="mt-0.5 shrink-0 text-fg-faint" aria-hidden="true" />
      )}
      <div className="min-w-0 flex-1">
        <p className={cn("m-0 font-medium", done ? "text-fg-secondary line-through" : "text-fg")}>
          {todo.title}
          {done && <span className="sr-only"> (done)</span>}
        </p>
        {todo.subtitle && <p className="m-0 text-sm text-fg-secondary">{todo.subtitle}</p>}
        {todo.flag_title && <p className="m-0 text-sm text-fg-secondary">For the flag: {todo.flag_title}</p>}
      </div>
      {todo.due_date && !done && (
        <span className={cn("shrink-0 text-sm", todo.overdue ? "text-bad" : "text-fg-secondary")}>
          {todo.overdue ? "Overdue " : "Due "}
          {formatShortDate(todo.due_date)}
        </span>
      )}
    </li>
  );
}

const PENDING_STATUS: Record<PendingItem["status"], { text: string; tone: BadgeTone }> = {
  open: { text: "Open", tone: "accent" },
  waiting: { text: "Waiting", tone: "neutral" },
  done: { text: "Done", tone: "good" },
};

function PendingRow({ item }: { item: PendingItem }) {
  const s = PENDING_STATUS[item.status];
  return (
    <li className="border-t border-line py-3 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap gap-1">
        <Badge tone={item.kind === "apply" ? "accent" : "warn"} title={item.kind === "apply" ? "A change for the chat to apply" : "Something to confirm"}>
          {item.kind === "apply" ? "Apply" : "Confirm"}
        </Badge>
        <Badge tone={s.tone}>{s.text}</Badge>
      </div>
      <p className={cn("m-0 mt-1 font-medium", item.status === "done" ? "text-fg-secondary" : "text-fg")}>{item.subject}</p>
      {item.detail && <p className="m-0 text-sm text-fg">{item.detail}</p>}
      <p className="m-0 text-sm text-fg-secondary">
        Added {formatShortDate(item.added_on)}
        {item.updated_on && ` · Updated ${formatShortDate(item.updated_on)}`}
      </p>
    </li>
  );
}

export function FlagsPage() {
  const [showArchived, setShowArchived] = useState(false);
  const flags = useQuery({ queryKey: recurringKeys.flags(showArchived), queryFn: () => getFlags(showArchived) });
  const todos = useQuery({ queryKey: recurringKeys.todos, queryFn: getTodos });
  const pending = useQuery({ queryKey: recurringKeys.pending, queryFn: getPending });
  const failed = flags.isError || todos.isError || pending.isError;
  const c = flags.data?.counts;

  return (
    <>
      <PageHeader title="Flags & To do" />
      <div className="flex flex-col gap-4 pb-8">
        {failed && (
          <ErrorBanner
            message="Could not load flags and to dos."
            onRetry={() => {
              void flags.refetch();
              void todos.refetch();
              void pending.refetch();
            }}
          />
        )}
        {c && (
          <p className="m-0 text-sm text-fg-secondary">
            {openCountText(flags.data?.flags ?? [], c.resolved)} Read only: the statement chat keeps these lists.
          </p>
        )}
        <div className="grid items-start gap-4 lg:grid-cols-3">
          <div className="min-w-0 lg:col-span-2">
            <FlagsCard
              data={flags.data}
              loading={flags.isPending}
              showArchived={showArchived}
              onToggleArchived={() => setShowArchived((v) => !v)}
            />
          </div>
          <div className="flex min-w-0 flex-col gap-4">
            <Card>
              <CardHeader
                title="To do"
                subtitle={todos.data ? `${todos.data.open_count} open, ${todos.data.done_count} done` : undefined}
              />
              {!todos.data ? (
                todos.isPending ? <ListSkeleton rows={2} /> : <EmptyState title="To dos did not load." />
              ) : todos.data.todos.length === 0 ? (
                <EmptyState title="Nothing to do." />
              ) : (
                <ul className="m-0 list-none p-0" aria-label="To do">
                  {todos.data.todos.map((t) => (
                    <TodoRow key={t.key} todo={t} />
                  ))}
                </ul>
              )}
            </Card>
            <Card>
              <CardHeader
                title="Pending"
                subtitle={
                  pending.data
                    ? `Changes for the chat to apply and things to confirm. ${pending.data.counts.open} open, ${pending.data.counts.waiting} waiting.`
                    : undefined
                }
              />
              {!pending.data ? (
                pending.isPending ? <ListSkeleton rows={2} /> : <EmptyState title="Pending items did not load." />
              ) : pending.data.items.length === 0 ? (
                <EmptyState title="Nothing pending." />
              ) : (
                <ul className="m-0 list-none p-0" aria-label="Pending">
                  {pending.data.items.map((p) => (
                    <PendingRow key={p.key} item={p} />
                  ))}
                </ul>
              )}
            </Card>
            <SourceLine runId={flags.data?.source_run_id} />
          </div>
        </div>
      </div>
    </>
  );
}

/**
 * Severity counts over open and watching flags, so they match the sections below. Info flags
 * are not open: they get their own count.
 */
export function openCountText(flags: FlagItem[], resolved: number): string {
  const live = flags.filter((f) => f.status !== "resolved" && !f.archived);
  const open = live.filter((f) => f.action !== "info");
  const info = live.length - open.length;
  const n = (sev: FlagItem["severity"]) => open.filter((f) => f.severity === sev).length;
  const infoTail = info ? ` ${info} for information.` : "";
  const tail = resolved ? ` ${resolved} resolved.` : "";
  return `${n("high")} high, ${n("medium")} medium and ${n("low")} low severity open.${infoTail}${tail}`;
}
