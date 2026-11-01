import { type QueryClient, useQuery } from "@tanstack/react-query";
import { getMe, queryKeys } from "./api";
import { applyMe } from "./demo";
import { setPinnedToday } from "./format";

export const ME_KEY = queryKeys.me;

async function fetchMe() {
  const me = await getMe();
  // Pages work out "today" as they render, so the demo date is set before RequireAuth lets them.
  applyMe(me);
  return me;
}

/** The signed-in user. `data` is null when there is no session. */
export function useMe() {
  return useQuery({ queryKey: ME_KEY, queryFn: fetchMe, staleTime: 5 * 60_000 });
}

/** Drops all cached data and marks the user as signed out. */
export function resetSession(queryClient: QueryClient): void {
  queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== ME_KEY[0] });
  queryClient.setQueryData(ME_KEY, null);
  setPinnedToday(null);
}
