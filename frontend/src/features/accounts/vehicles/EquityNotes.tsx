import { createContext, type ReactNode, useContext, useMemo } from "react";
import { equityNote, useValuations } from "./api";

type Notes = (accountKey: string) => string | null;

const EquityNotesContext = createContext<Notes>(() => null);

/**
 * Says which equity lines use estimates. Account rows only carry the equity
 * numbers, so this reads /api/valuations once for the page and gives each
 * home or vehicle row its tooltip. Without the provider, no row is marked.
 */
export function EquityNotesProvider({ children }: { children: ReactNode }) {
  const q = useValuations();
  const items = q.data?.items;
  const notes = useMemo<Notes>(() => (key) => equityNote(items, key), [items]);
  return <EquityNotesContext.Provider value={notes}>{children}</EquityNotesContext.Provider>;
}

/** The equity tooltip for this home or vehicle, or null when nothing in it is estimated. */
export function useEquityNote(accountKey: string): string | null {
  return useContext(EquityNotesContext)(accountKey);
}
