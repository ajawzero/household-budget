import { type ReactNode, useEffect } from "react";
import { useMe } from "../../lib/auth";
import { cn } from "../../lib/cn";
import { brandFor } from "../../lib/demo";
import { HeaderLine } from "./HeaderLine";

interface PageHeaderProps {
  title: string;
  /** Buttons on the right. */
  actions?: ReactNode;
  /** Sub-tabs right after the title. */
  tabs?: ReactNode;
  /** The phone top bar already shows the page name. Set for a greeting. */
  showTitleOnPhone?: boolean;
  /** Browser tab name when the title is not a page name (a greeting). */
  documentTitle?: string;
}

/** Page title, data freshness line and actions. Sets the browser tab title. */
export function PageHeader({ title, actions, tabs, showTitleOnPhone = false, documentTitle }: PageHeaderProps) {
  const tabTitle = documentTitle ?? title;
  const brand = brandFor(useMe().data).name;
  useEffect(() => {
    document.title = `${tabTitle} · ${brand}`;
  }, [tabTitle, brand]);

  return (
    <header className="flex flex-wrap items-end justify-between gap-3 pb-4 pt-2 md:pb-6 md:pt-5">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
          <h1 className={cn("m-0 text-xl font-medium text-fg", !showTitleOnPhone && "sr-only md:not-sr-only")}>{title}</h1>
          {tabs}
        </div>
        <HeaderLine />
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}
