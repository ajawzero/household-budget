import {
  CalendarDays,
  ChartColumn,
  CreditCard,
  FileText,
  Flag,
  Gift,
  House,
  Layers,
  Map,
  Plane,
  Target,
  TrendingUp,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  /** Set for pages not built yet. They show "Coming in Phase N". */
  phase?: number | "later";
  /** One line for the empty state. */
  blurb?: string;
}

export const MAIN_NAV: NavItem[] = [
  { to: "/", label: "Dashboard", icon: House },
  { to: "/accounts", label: "Accounts", icon: Layers },
  { to: "/transactions", label: "Transactions", icon: CreditCard },
  { to: "/cash-flow", label: "Cash Flow", icon: ChartColumn },
  { to: "/budget", label: "Budget", icon: Map },
  { to: "/recurring", label: "Recurring", icon: CalendarDays },
  { to: "/investments", label: "Investments", icon: TrendingUp },
  { to: "/goals", label: "Goals", icon: Target },
];

export const MORE_NAV: NavItem[] = [
  { to: "/flags", label: "Flags & To do", icon: Flag },
  { to: "/rewards", label: "Rewards", icon: Gift },
  { to: "/travel", label: "Travel", icon: Plane },
  { to: "/statements", label: "Statements", icon: FileText },
];

/** Phone bottom bar. "More" opens the drawer. */
export const TAB_NAV = ["/", "/accounts", "/transactions", "/budget"];

export const ALL_NAV = [...MAIN_NAV, ...MORE_NAV];

/** The nav item for a path, by longest matching prefix. */
export function navItemFor(pathname: string): NavItem | undefined {
  if (pathname === "/") return MAIN_NAV[0];
  return ALL_NAV.filter((n) => n.to !== "/" && (pathname === n.to || pathname.startsWith(`${n.to}/`))).sort(
    (a, b) => b.to.length - a.to.length,
  )[0];
}
