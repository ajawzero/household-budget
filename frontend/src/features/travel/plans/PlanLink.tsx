import { ExternalLink } from "lucide-react";
import type { PlanLinkOut } from "../../../lib/api";
import { cn } from "../../../lib/cn";
import { safeHref } from "./model";

/**
 * One link from the plan's web search results (docs/plans/trip-planner.md
 * section 4). It shows the site name, opens in a new tab and sends no
 * referrer, so the site never sees the app's address. A link that fails the
 * check shows its site name as plain text.
 */
export function PlanLink({ link, className }: { link: PlanLinkOut; className?: string }) {
  const href = safeHref(link.url);
  if (!href) return <span className={cn("text-fg-secondary", className)}>{link.site}</span>;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      referrerPolicy="no-referrer"
      title={link.title}
      className={cn("inline-flex min-h-11 items-center gap-0.5 break-all md:min-h-0", className)}
    >
      {link.site}
      <ExternalLink size={12} aria-hidden="true" className="shrink-0" />
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}

/** "Sources: a.example · b.example" for one plan item. Ids with no checked link are left out. */
export function SourceLinks({ ids = [], links }: { ids?: string[]; links: Map<string, PlanLinkOut> }) {
  const found = [...new Set(ids)].flatMap((id) => {
    const link = links.get(id);
    return link ? [link] : [];
  });
  if (found.length === 0) return null;
  return (
    <p className="m-0 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-fg-secondary">
      <span>{found.length === 1 ? "Source:" : "Sources:"}</span>
      {found.map((link) => (
        <PlanLink key={link.id} link={link} />
      ))}
    </p>
  );
}
