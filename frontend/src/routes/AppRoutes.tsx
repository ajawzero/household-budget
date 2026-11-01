import { useQueryClient } from "@tanstack/react-query";
import { lazy, type ReactNode, Suspense, useEffect } from "react";
import { Navigate, Outlet, Route, Routes, useLocation, useNavigate } from "react-router";
import { LoginPage } from "../features/auth/LoginPage";
import { DemoEndedPage } from "../features/demo/DemoEndedPage";
import { TabCover } from "../features/demo/TabCover";
import { ChecksPage } from "../features/checks/ChecksPage";
import { PasswordPage } from "../features/auth/PasswordPage";
import { ImportDetailPage, ImportsPage } from "../features/imports/ImportsPage";
import { ComingSoonPage, NotFoundPage } from "../features/placeholder/ComingSoonPage";
import { SettingsPage } from "../features/settings/SettingsPage";
import { setUnauthorizedHandler } from "../lib/api";
import { resetSession, useMe } from "../lib/auth";
import { demoEnded } from "../lib/demo";
import { replacePage } from "../lib/reload";
import { AppLayout } from "./AppLayout";
import { ALL_NAV } from "./nav";

// The chart pages load on first visit, so sign-in and the shell stay light (Recharts is big).
const DashboardPage = lazy(() => import("../features/dashboard/DashboardPage").then((m) => ({ default: m.DashboardPage })));
const AccountsPage = lazy(() => import("../features/accounts/AccountsPage").then((m) => ({ default: m.AccountsPage })));
const RecurringPage = lazy(() => import("../features/recurring/RecurringPage").then((m) => ({ default: m.RecurringPage })));
const FlagsPage = lazy(() => import("../features/flags/FlagsPage").then((m) => ({ default: m.FlagsPage })));
const CashflowPage = lazy(() => import("../features/cashflow/CashflowPage").then((m) => ({ default: m.CashflowPage })));
const BudgetPage = lazy(() => import("../features/budget/BudgetPage").then((m) => ({ default: m.BudgetPage })));
const TransactionsPage = lazy(() =>
  import("../features/transactions/TransactionsPage").then((m) => ({ default: m.TransactionsPage })),
);
const InvestmentsPage = lazy(() =>
  import("../features/investments/InvestmentsPage").then((m) => ({ default: m.InvestmentsPage })),
);
const PricesPage = lazy(() => import("../features/settings/prices/PricesPage").then((m) => ({ default: m.PricesPage })));
const VehiclesSettingsPage = lazy(() =>
  import("../features/settings/vehicles/VehiclesSettingsPage").then((m) => ({ default: m.VehiclesSettingsPage })),
);
const LimitsSettingsPage = lazy(() =>
  import("../features/settings/limits/LimitsSettingsPage").then((m) => ({ default: m.LimitsSettingsPage })),
);
const SuggestionsPage = lazy(() => import("../features/ai/SuggestionsPage").then((m) => ({ default: m.SuggestionsPage })));
const AiSettingsPage = lazy(() => import("../features/ai/AiSettingsPage").then((m) => ({ default: m.AiSettingsPage })));
const GoalsPage = lazy(() => import("../features/goals/GoalsPage").then((m) => ({ default: m.GoalsPage })));
const RewardsPage = lazy(() => import("../features/rewards/RewardsPage").then((m) => ({ default: m.RewardsPage })));
const TravelPage = lazy(() => import("../features/travel/TravelPage").then((m) => ({ default: m.TravelPage })));
const PlanPage = lazy(() => import("../features/travel/plans/PlanPage").then((m) => ({ default: m.PlanPage })));
const RulesPage = lazy(() => import("../features/transactions/RulesPage").then((m) => ({ default: m.RulesPage })));

function Lazy({ children }: { children: ReactNode }) {
  return <Suspense fallback={<p className="text-fg-secondary">Loading…</p>}>{children}</Suspense>;
}

// Dev-only component gallery. import.meta.env.DEV is false in builds, so it is left out.
const GalleryPage = import.meta.env.DEV
  ? lazy(() => import("../features/dev/GalleryPage").then((m) => ({ default: m.GalleryPage })))
  : null;
const TripPlanPreview = import.meta.env.DEV
  ? lazy(() => import("../features/dev/TripPlanPreview").then((m) => ({ default: m.TripPlanPreview })))
  : null;

export function AppRoutes() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  // Any 401 from the API drops cached data and goes to the sign-in page.
  // In demo it loads "Demo ended" instead, a whole new page.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      if (demoEnded()) {
        replacePage("/demo-ended");
        return;
      }
      resetSession(queryClient);
      navigate("/login", { replace: true });
    });
  }, [queryClient, navigate]);

  return (
    <TabCover>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/demo-ended" element={<DemoEndedPage />} />
        <Route element={<RequireAuth />}>
          <Route element={<AppLayout />}>
            <Route
              index
              element={
                <Lazy>
                  <DashboardPage />
                </Lazy>
              }
            />
            <Route
              path="accounts"
              element={
                <Lazy>
                  <AccountsPage />
                </Lazy>
              }
            />
            <Route
              path="transactions"
              element={
                <Lazy>
                  <TransactionsPage />
                </Lazy>
              }
            />
            <Route
              path="transactions/suggestions"
              element={
                <Lazy>
                  <SuggestionsPage />
                </Lazy>
              }
            />
            <Route
              path="recurring"
              element={
                <Lazy>
                  <RecurringPage />
                </Lazy>
              }
            />
            <Route
              path="flags"
              element={
                <Lazy>
                  <FlagsPage />
                </Lazy>
              }
            />
            <Route
              path="cash-flow"
              element={
                <Lazy>
                  <CashflowPage />
                </Lazy>
              }
            />
            <Route
              path="budget"
              element={
                <Lazy>
                  <BudgetPage />
                </Lazy>
              }
            />
            <Route
              path="investments"
              element={
                <Lazy>
                  <InvestmentsPage />
                </Lazy>
              }
            />
            <Route
              path="goals"
              element={
                <Lazy>
                  <GoalsPage />
                </Lazy>
              }
            />
            <Route
              path="rewards"
              element={
                <Lazy>
                  <RewardsPage />
                </Lazy>
              }
            />
            <Route
              path="travel"
              element={
                <Lazy>
                  <TravelPage />
                </Lazy>
              }
            />
            <Route
              path="travel/plans/:planId"
              element={
                <Lazy>
                  <PlanPage />
                </Lazy>
              }
            />
            <Route path="statements" element={<ImportsPage />} />
            <Route path="statements/:runId" element={<ImportDetailPage />} />
            <Route path="checks" element={<ChecksPage />} />
            {/* Phase 1 links. */}
            <Route path="imports" element={<Navigate to="/statements" replace />} />
            <Route path="imports/:runId" element={<ImportsRedirect />} />
            <Route path="password" element={<Navigate to="/settings/password" replace />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="settings/password" element={<PasswordPage />} />
            <Route
              path="settings/prices"
              element={
                <Lazy>
                  <PricesPage />
                </Lazy>
              }
            />
            <Route
              path="settings/vehicles"
              element={
                <Lazy>
                  <VehiclesSettingsPage />
                </Lazy>
              }
            />
            <Route
              path="settings/limits"
              element={
                <Lazy>
                  <LimitsSettingsPage />
                </Lazy>
              }
            />
            <Route
              path="settings/ai"
              element={
                <Lazy>
                  <AiSettingsPage />
                </Lazy>
              }
            />
            <Route
              path="settings/rules"
              element={
                <Lazy>
                  <RulesPage />
                </Lazy>
              }
            />
            {ALL_NAV.filter((n) => n.phase !== undefined).map((n) => (
              <Route key={n.to} path={n.to.slice(1)} element={<ComingSoonPage item={n} />} />
            ))}
            {GalleryPage && (
              <Route
                path="dev/gallery"
                element={
                  <Suspense fallback={null}>
                    <GalleryPage />
                  </Suspense>
                }
              />
            )}
            {TripPlanPreview && (
              <Route
                path="dev/trip-plan/*"
                element={
                  <Suspense fallback={null}>
                    <TripPlanPreview />
                  </Suspense>
                }
              />
            )}
            <Route path="*" element={<NotFoundPage />} />
          </Route>
        </Route>
      </Routes>
    </TabCover>
  );
}

function ImportsRedirect() {
  const { pathname } = useLocation();
  return <Navigate to={pathname.replace(/^\/imports/, "/statements")} replace />;
}

function RequireAuth() {
  const me = useMe();
  const location = useLocation();
  if (me.isPending) return <p className="p-6 text-fg-secondary">Loading…</p>;
  if (me.isError) return <p className="p-6 text-bad">Could not reach the server.</p>;
  if (!me.data) {
    if (demoEnded()) return <LoadPage path="/demo-ended" />;
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  return <Outlet />;
}

/** A whole-page load, not a move inside the app. Blank until it happens. */
function LoadPage({ path }: { path: string }) {
  useEffect(() => replacePage(path), [path]);
  return null;
}
