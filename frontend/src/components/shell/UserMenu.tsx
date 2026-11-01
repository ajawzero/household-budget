import { useQueryClient } from "@tanstack/react-query";
import { ChevronDown, KeyRound, LogOut } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";
import { logout } from "../../lib/api";
import { resetSession, useMe } from "../../lib/auth";
import { cn } from "../../lib/cn";
import { leaveDemo } from "../../lib/demo";
import { setThemePref, type ThemePref, useThemePref } from "../../lib/theme";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "../ui/dropdown-menu";
import { Monogram } from "../money/Monogram";

/** Name button at the bottom of the nav: theme, change password, sign out. In demo: theme and Leave demo. */
export function UserMenu({ compact = false }: { compact?: boolean }) {
  const me = useMe();
  const theme = useThemePref();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [signingOut, setSigningOut] = useState(false);
  const name = me.data?.display_name ?? "";
  const demo = me.data?.demo === true;

  async function signOut() {
    setSigningOut(true);
    if (demo) {
      await leaveDemo();
      return;
    }
    try {
      await logout();
    } catch {
      // The session is gone either way. Clear local state below.
    }
    resetSession(queryClient);
    navigate("/login", { replace: true });
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        aria-label={compact ? `${name}, account menu` : undefined}
        className={cn(
          "flex h-11 w-full items-center gap-2.5 rounded-lg px-1.5 text-left text-sm text-fg hover:bg-active",
          compact && "justify-center px-0",
        )}
      >
        <Monogram name={name || "?"} size={24} />
        {!compact && (
          <>
            <span className="min-w-0 flex-1 truncate font-medium">{name}</span>
            <ChevronDown size={16} aria-hidden="true" className="text-fg-secondary" />
          </>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start">
        <DropdownMenuLabel>Theme</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={theme} onValueChange={(v) => setThemePref(v as ThemePref)}>
          <DropdownMenuRadioItem value="auto">Auto</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="light">Light</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="dark">Dark</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
        <DropdownMenuSeparator />
        {!demo && (
          <DropdownMenuItem onSelect={() => navigate("/settings/password")}>
            <KeyRound size={16} aria-hidden="true" />
            Change password
          </DropdownMenuItem>
        )}
        <DropdownMenuItem disabled={signingOut} onSelect={signOut}>
          <LogOut size={16} aria-hidden="true" />
          {demo ? "Leave demo" : "Sign out"}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
