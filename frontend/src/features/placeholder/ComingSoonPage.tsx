import { Hourglass } from "lucide-react";
import { Link } from "react-router";
import { PageHeader } from "../../components/shell/PageHeader";
import { Card } from "../../components/ui/card";
import { EmptyState } from "../../components/ui/empty-state";
import type { NavItem } from "../../routes/nav";

/** Calm stand-in for a page that a later phase builds. */
export function ComingSoonPage({ item }: { item: NavItem }) {
  const when = item.phase === "later" ? "Coming in a later phase" : `Coming in Phase ${item.phase}`;
  return (
    <>
      <PageHeader title={item.label} />
      <Card>
        <EmptyState icon={<Hourglass size={28} strokeWidth={1.5} aria-hidden="true" />} title={when}>
          {item.blurb}
        </EmptyState>
      </Card>
    </>
  );
}

export function NotFoundPage() {
  return (
    <>
      <PageHeader title="Not found" />
      <Card>
        <EmptyState title="There is no page here.">
          <Link to="/" className="inline-flex min-h-11 items-center md:min-h-0">
            Go to the dashboard
          </Link>
        </EmptyState>
      </Card>
    </>
  );
}
