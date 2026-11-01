import { useQuery } from "@tanstack/react-query";
import { Sparkles } from "lucide-react";
import { Link } from "react-router";
import { aiKeys, getSuggestions, showsStored, useAiStatus } from "./api";

/**
 * Transactions header: a link to category suggestions, with the waiting count.
 * Hidden while AI is off or has no key; it stays during a spend-limit pause.
 */
export function SuggestionsLink() {
  const status = useAiStatus();
  const enabled = showsStored(status.data);
  const q = useQuery({ queryKey: aiKeys.suggestions, queryFn: getSuggestions, enabled });
  if (!enabled) return null;
  const count = q.data?.suggestions.length ?? 0;
  return (
    <Link
      to="/transactions/suggestions"
      className="inline-flex h-11 items-center gap-2 rounded-lg border border-line bg-card px-3 text-sm font-medium text-fg no-underline hover:bg-hover md:h-9 md:px-4"
    >
      <Sparkles size={16} aria-hidden="true" className="text-accent-text" />
      Suggestions
      {count > 0 && (
        <span className="rounded-full bg-accent-tint px-1.5 text-xs text-accent-text">
          {count}
          <span className="sr-only"> waiting</span>
        </span>
      )}
    </Link>
  );
}
