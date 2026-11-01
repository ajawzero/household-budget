import { PanelLeftClose, PanelLeftOpen, Settings, Sparkles } from "lucide-react";
import { NavLink } from "react-router";
import { useAsk } from "../../features/ai/AskContext";
import { useAiStatus } from "../../features/ai/api";
import { cn } from "../../lib/cn";
import { MAIN_NAV, MORE_NAV } from "../../routes/nav";
import { Tooltip } from "../ui/tooltip";
import { Logo } from "./Logo";
import { NavList } from "./NavList";
import { UserMenu } from "./UserMenu";

interface SidebarProps {
  /** The 52px icon rail. */
  compact?: boolean;
  /** Collapse (full) or expand (rail). Left out in the phone drawer. */
  onToggle?: () => void;
  /** Closes the drawer after a tap. */
  onNavigate?: () => void;
}

/** Nav on the page background: logo, main pages, More, then Ask, Settings and the user. */
export function Sidebar({ compact = false, onToggle, onNavigate }: SidebarProps) {
  return (
    <nav aria-label="Main" className={cn("flex h-full flex-col gap-4 py-3", compact ? "px-1.5" : "px-3")}>
      <div className={cn("flex items-center gap-1", compact && "flex-col")}>
        <div className={cn(!compact && "min-w-0 flex-1")}>
          <Logo compact={compact} />
        </div>
        {onToggle && (
          <button
            type="button"
            onClick={onToggle}
            aria-label={compact ? "Expand sidebar" : "Collapse sidebar"}
            className="inline-flex h-9 w-9 items-center justify-center rounded-lg text-fg-secondary hover:bg-active"
          >
            {compact ? <PanelLeftOpen size={18} aria-hidden="true" /> : <PanelLeftClose size={18} aria-hidden="true" />}
          </button>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <NavList items={MAIN_NAV} compact={compact} onNavigate={onNavigate} />
        <h2 className={cn("eyebrow mb-1 mt-5 px-2.5", compact && "sr-only")}>More</h2>
        {compact && <div aria-hidden="true" className="mx-2 my-3 h-px bg-line" />}
        <NavList items={MORE_NAV} compact={compact} onNavigate={onNavigate} />
      </div>

      <div className="flex flex-col gap-0.5">
        <AskButton compact={compact} onNavigate={onNavigate} />
        <SettingsLink compact={compact} onNavigate={onNavigate} />
        <UserMenu compact={compact} />
      </div>
    </nav>
  );
}

/** Opens the Ask panel. Hidden unless AI may run (PLAN 7.7). */
function AskButton({ compact, onNavigate }: { compact: boolean; onNavigate?: () => void }) {
  const status = useAiStatus();
  const { setOpen } = useAsk();
  if (!status.data?.available) return null;
  const button = (
    <button
      type="button"
      onClick={() => {
        onNavigate?.();
        setOpen(true);
      }}
      aria-label={compact ? "Ask" : undefined}
      aria-haspopup="dialog"
      className={cn(
        "flex h-11 w-full items-center gap-3 rounded-lg px-2.5 text-sm text-accent-text hover:bg-active md:h-9",
        compact && "justify-center px-0",
      )}
    >
      <Sparkles size={20} strokeWidth={1.75} aria-hidden="true" className="shrink-0" />
      {!compact && <span className="flex-1 text-left">Ask</span>}
    </button>
  );
  return compact ? (
    <Tooltip content="Ask" side="right">
      {button}
    </Tooltip>
  ) : (
    button
  );
}

function SettingsLink({ compact, onNavigate }: { compact: boolean; onNavigate?: () => void }) {
  return (
    <NavLink
      to="/settings"
      onClick={onNavigate}
      aria-label={compact ? "Settings" : undefined}
      className={({ isActive }) =>
        cn(
          "flex h-11 items-center gap-3 rounded-lg px-2.5 text-sm text-fg no-underline hover:bg-active md:h-9",
          compact && "justify-center px-0",
          isActive && "bg-active font-medium",
        )
      }
    >
      <Settings size={20} strokeWidth={1.75} aria-hidden="true" className="shrink-0" />
      {!compact && <span>Settings</span>}
    </NavLink>
  );
}
