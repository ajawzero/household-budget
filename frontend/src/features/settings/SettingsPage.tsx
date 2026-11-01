import { ChevronRight } from "lucide-react";
import { Link } from "react-router";
import { PageHeader } from "../../components/shell/PageHeader";
import { Card, CardHeader } from "../../components/ui/card";
import { SegmentedControl } from "../../components/ui/segmented-control";
import { useMe } from "../../lib/auth";
import { DemoCard } from "../demo/DemoCard";
import { setThemePref, type ThemePref, useThemePref } from "../../lib/theme";

const THEMES = [
  { value: "auto", label: "Auto" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
] as const;

/** Appearance and links to each settings page. AI and Demo mode are admin only; demo hides the password. */
export function SettingsPage() {
  const theme = useThemePref();
  const me = useMe().data;
  const isAdmin = me?.role === "admin";
  const demo = me?.demo === true;
  return (
    <>
      <PageHeader title="Settings" />
      <div className="flex max-w-2xl flex-col gap-4">
        <Card>
          <CardHeader title="Appearance" subtitle="Auto follows this device's light or dark setting." />
          <SegmentedControl<ThemePref> label="Theme" value={theme} options={THEMES} onChange={setThemePref} size="md" />
        </Card>
        <Card className="p-0 md:p-0">
          {!demo && <SettingsLink to="/settings/password" title="Change password" />}
          {isAdmin && <SettingsLink to="/settings/ai" title="AI" subtitle="On or off, models, caps and this month's cost" />}
          <SettingsLink to="/settings/prices" title="Prices" subtitle="Price modes, symbols and the daily price run" />
          <SettingsLink to="/settings/rules" title="Rules" subtitle="Merchant rules that set a category" />
          <SettingsLink to="/settings/vehicles" title="Home and vehicles" subtitle="Mileage, yearly loss, conversion value and the home index" />
          <SettingsLink to="/settings/limits" title="Contribution limits" subtitle="401k and HSA limits, catch-up and coverage" />
          <SettingsLink to="/statements" title="Imports" subtitle="Runs from the monthly statement chat" />
          <SettingsLink to="/checks" title="Checks" subtitle="The app's numbers next to the chat's" />
        </Card>
        {isAdmin && !demo && <DemoCard available={me?.demo_available === true} />}
      </div>
    </>
  );
}

function SettingsLink({ to, title, subtitle }: { to: string; title: string; subtitle?: string }) {
  return (
    <Link
      to={to}
      className="flex min-h-14 items-center gap-3 border-b border-line px-5 py-3 text-fg no-underline last:border-b-0 hover:bg-hover md:px-6"
    >
      <span className="flex-1">
        <span className="block text-base">{title}</span>
        {subtitle && <span className="block text-sm text-fg-secondary">{subtitle}</span>}
      </span>
      <ChevronRight size={18} aria-hidden="true" className="text-fg-secondary" />
    </Link>
  );
}
