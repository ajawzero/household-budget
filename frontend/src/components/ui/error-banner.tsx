import { TriangleAlert } from "lucide-react";
import { Button } from "./button";

interface ErrorBannerProps {
  message: string;
  onRetry: () => void;
  /** True while the retry request runs. */
  retrying?: boolean;
}

/** One page-level load error with a Retry button. Cards below stay quiet. */
export function ErrorBanner({ message, onRetry, retrying = false }: ErrorBannerProps) {
  return (
    <div role="alert" className="flex animate-fade-in flex-wrap items-center gap-3 rounded-card border border-line bg-card px-4 py-3 shadow-card">
      <TriangleAlert size={18} aria-hidden="true" className="shrink-0 text-bad" />
      <p className="m-0 min-w-0 flex-1 text-sm text-fg">{message}</p>
      <Button size="sm" onClick={onRetry} disabled={retrying}>
        {retrying ? "Trying again" : "Retry"}
      </Button>
    </div>
  );
}
