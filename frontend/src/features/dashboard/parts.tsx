import type { ReactNode } from "react";
import { Card, CardHeader } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import { Skeleton } from "../../components/ui/skeleton";

/** What every card needs to know about the one dashboard request. */
export interface LoadState {
  loading: boolean;
  refreshing: boolean;
  /** Short card text when the request failed. The page banner has the full message. */
  error: string | null;
}

/** Small gray "plan" pill: the number is the chat's plan, not actuals. */
export function PlanBadge() {
  return (
    <span className="ml-2 inline-flex h-5 items-center rounded-full bg-subtle px-2 align-middle text-xs font-medium text-fg-secondary">
      plan
    </span>
  );
}

/** Small gray "actual" pill: the number comes from the imported transactions. */
export function ActualBadge() {
  return (
    <span className="ml-2 inline-flex h-5 items-center rounded-full bg-subtle px-2 align-middle text-xs font-medium text-fg-secondary">
      actual
    </span>
  );
}

/** A header title with an optional "plan" pill after it. */
export function Title({ children, plan = false }: { children: ReactNode; plan?: boolean }) {
  return (
    <>
      {children}
      {plan && <PlanBadge />}
    </>
  );
}

interface DashCardProps {
  title: ReactNode;
  subtitle?: ReactNode;
  state: LoadState;
  /** True when there is nothing to list. */
  empty: boolean;
  emptyText: string;
  footer?: ReactNode;
  children: ReactNode;
}

/** A card that is a list or text, not a chart. Handles loading, error and empty. */
export function DashCard({ title, subtitle, state, empty, emptyText, footer, children }: DashCardProps) {
  let body: ReactNode;
  if (state.loading) {
    body = (
      <div role="status" aria-label="Loading" className="flex flex-col gap-3">
        <Skeleton className="h-4 w-3/4" />
        <Skeleton className="h-4 w-1/2" />
        <Skeleton className="h-4 w-2/3" />
      </div>
    );
  } else if (state.error) {
    body = <p className="m-0 py-6 text-center text-sm text-fg-secondary">{state.error}</p>;
  } else if (empty) {
    body = <EmptyState title={emptyText} />;
  } else {
    body = (
      <div className={state.refreshing ? "opacity-60 transition-opacity duration-100" : undefined}>
        {children}
        {footer}
      </div>
    );
  }
  return (
    <Card>
      <CardHeader title={title} subtitle={subtitle} />
      {body}
    </Card>
  );
}

/** Small note under a chart or list. */
export function CardNote({ children }: { children: ReactNode }) {
  return <p className="m-0 mt-4 text-sm text-fg-secondary">{children}</p>;
}
