import React, { Suspense, lazy } from 'react';
import { Routes, Route, Navigate, useLocation, matchPath } from 'react-router-dom';
import { TooltipProvider } from '@radix-ui/react-tooltip';
import { CustomThemeProvider } from './contexts/ThemeContext';
import { AuthProvider } from './contexts/AuthContext';
import { ProjectProvider } from './contexts/ProjectContext';
import { ToastProvider } from './contexts/ToastContext';
import Layout from './components/Layout';
import ProtectedRoute from './components/ProtectedRoute';
import HubRedirect from './components/HubRedirect';
import { ListPageSkeleton, DetailSkeleton, CardListSkeleton } from './components/PageSkeleton';
import Login from './pages/Login';

/**
 * Route-aware Suspense fallback (audit H16 + PRF·H2).  Pre-audit every
 * lazy route used ListPageSkeleton, which is table-shaped.  Detail
 * pages (`/scopes/:id`, `/hosts/:id`, `/test-plans/:id/*`) flashed a
 * table skeleton then reflowed into a header+content layout,
 * displacing scroll-anchor targets and sticky action bars.
 *
 * PRF·H2: the old version used a generic numeric-segment regex which
 * misclassified several routes — `/portfolio` and the hub landings
 * fell through to ListPageSkeleton then snapped to a card grid.  The
 * explicit choice map below is the source of truth for which skeleton
 * each known route shape gets.  Anything unknown stays
 * ListPageSkeleton (the safest default — most pages are list-shaped).
 */
type RouteSkeletonKind = 'list' | 'detail' | 'cards';

// matchPath patterns + their skeleton shape.  Order matters — the
// first match wins.  Detail patterns precede the static list /cards
// patterns so e.g. `/test-plans/:id` resolves to detail before
// `/test-plans` would resolve to list.
const ROUTE_SKELETON: Array<{ pattern: string; kind: RouteSkeletonKind }> = [
  // detail
  // v4.50.0 — ScopeDetail retired; /scopes/:id redirects to /scopes
  // synchronously (no lazy boundary), so it no longer needs a
  // skeleton entry.
  { pattern: '/hosts/:id', kind: 'detail' },
  // /scans/compare must precede /scans/:id so the picker gets the
  // card skeleton rather than the detail skeleton.
  { pattern: '/scans/compare', kind: 'cards' },
  { pattern: '/scans/:id', kind: 'detail' },
  { pattern: '/test-plans/:id/*', kind: 'detail' },
  { pattern: '/recon/runs/:id', kind: 'detail' },
  { pattern: '/executions/:id', kind: 'detail' },
  { pattern: '/findings/:id', kind: 'detail' },
  { pattern: '/profile', kind: 'detail' },
  { pattern: '/force-change-password', kind: 'detail' },
  // cards
  { pattern: '/portfolio', kind: 'cards' },
  { pattern: '/llm-settings', kind: 'cards' },
  { pattern: '/integrations', kind: 'cards' },
  { pattern: '/operations', kind: 'cards' },
  { pattern: '/network-topology', kind: 'cards' },
  { pattern: '/inventory', kind: 'cards' },
  { pattern: '/workflows', kind: 'cards' },
  { pattern: '/collaboration', kind: 'cards' },
  { pattern: '/settings', kind: 'cards' },
];

const resolveSkeletonKind = (pathname: string): RouteSkeletonKind => {
  for (const { pattern, kind } of ROUTE_SKELETON) {
    if (matchPath({ path: pattern, end: pattern.endsWith('/*') ? false : true }, pathname)) {
      return kind;
    }
  }
  return 'list';
};

const RouteSkeleton: React.FC = () => {
  const location = useLocation();
  const kind = resolveSkeletonKind(location.pathname);
  if (kind === 'detail') return <DetailSkeleton />;
  if (kind === 'cards') return <CardListSkeleton />;
  return <ListPageSkeleton />;
};
// v3 alpha.11 — Dashboard.tsx removed; / and /dashboard redirect to
// /operations.  My Queue + My Tasks widgets extracted into
// MyQueueCard / MyTasksCard components used by Operations.
//
// v4.0.0-alpha.0 — every protected page is React.lazy()'d so the
// initial paint pays only for Login + Layout shell + the first
// destination route.  Login stays eagerly imported because it's the
// most common cold-start landing and we don't want a Suspense flash on
// auth.  Suspense fallback uses ListPageSkeleton — it matches the
// shape of most pages closely enough that the swap is invisible for
// the common case.
const Scans = lazy(() => import('./pages/Scans'));
const ScanDetail = lazy(() => import('./pages/ScanDetail'));
const ScanDiff = lazy(() => import('./pages/ScanDiff'));
const NetworkTopology = lazy(() => import('./pages/NetworkTopology'));
const Hosts = lazy(() => import('./pages/Hosts'));
const Activity = lazy(() => import('./pages/Activity'));
const HostDetail = lazy(() => import('./pages/HostDetail'));
const Scopes = lazy(() => import('./pages/Scopes'));
const SubnetInsights = lazy(() => import('./pages/SubnetInsights'));
const ParseErrors = lazy(() => import('./pages/ParseErrors'));
const DefaultCredentials = lazy(() => import('./pages/DefaultCredentials'));
const Profile = lazy(() => import('./pages/Profile'));
const SystemSettings = lazy(() => import('./pages/SystemSettings'));
const ToolReference = lazy(() => import('./pages/ToolReference'));
const ProjectSettings = lazy(() => import('./pages/ProjectSettings'));
const PortfolioDashboard = lazy(() => import('./pages/PortfolioDashboard'));
const TestPlans = lazy(() => import('./pages/TestPlans'));
const TestPlanLayout = lazy(() => import('./pages/test-plan/TestPlanLayout'));
const TestPlanPlanTab = lazy(() => import('./pages/test-plan/PlanTab'));
const TestPlanRunsTab = lazy(() => import('./pages/test-plan/RunsTab'));
const TestPlanActivityTab = lazy(() => import('./pages/test-plan/ActivityTab'));
const TestPlanApiCallsTab = lazy(() => import('./pages/test-plan/ApiCallsTab'));
const TestPlanDangerTab = lazy(() => import('./pages/test-plan/DangerTab'));
const Reference = lazy(() => import('./pages/Reference'));
const UserGuide = lazy(() => import('./pages/UserGuide'));
const SbomReference = lazy(() => import('./pages/SbomReference'));
const Feedback = lazy(() => import('./pages/Feedback'));
const LLMSettings = lazy(() => import('./pages/LLMSettings'));
const IntegrationSettings = lazy(() => import('./pages/IntegrationSettings'));
const ForceChangePassword = lazy(() => import('./pages/ForceChangePassword'));
const ProjectActivity = lazy(() => import('./pages/ProjectActivity'));
// v2.56.0 — cross-project SOC-correlation page.  Different intent from
// /activity (notes/notifications) and /agent-activity (per-project
// agent timeline): asks "what tools ran across all my projects at
// time X" for correlating against SOC alerts.
const ToolActivity = lazy(() => import('./pages/ToolActivity'));
const TestPlanCompare = lazy(() => import('./pages/TestPlanCompare'));
const Operations = lazy(() => import('./pages/Operations'));
const Findings = lazy(() => import('./pages/Findings'));
const FindingDetail = lazy(() => import('./pages/FindingDetail'));
const ReconRunDetail = lazy(() => import('./pages/ReconRunDetail'));
const ReconRunsList = lazy(() => import('./pages/ReconRunsList'));
const ReconCompare = lazy(() => import('./pages/ReconCompare'));
const ExecutionDetail = lazy(() => import('./pages/ExecutionDetail'));
const ExecutionsList = lazy(() => import('./pages/ExecutionsList'));
const PlanCompare = lazy(() => import('./pages/PlanCompare'));

function App() {
  return (
    <CustomThemeProvider>
      <ToastProvider>
        <AuthProvider>
          {/*
            Single TooltipProvider mount.  Radix tooltips share one
            provider for delayDuration tracking + portal management;
            without this, every <Tooltip> spawns its own provider and
            hover delay is inconsistent across the app.
          */}
          <TooltipProvider delayDuration={300} skipDelayDuration={150}>
          <Routes>
          {/* Public routes */}
          <Route path="/login" element={<Login />} />

          {/* Forced password change — no Layout/sidebar */}
          <Route
            path="/force-change-password"
            element={
              <ProtectedRoute>
                <Suspense fallback={<RouteSkeleton />}>
                  <ForceChangePassword />
                </Suspense>
              </ProtectedRoute>
            }
          />

          {/* Protected routes */}
          <Route
            path="/*"
            element={
              <ProtectedRoute>
                <ProjectProvider>
                {/* width: '100%' is load-bearing.  This Box is a flex
                    container that wraps Layout + VersionFooter as
                    siblings, but a flex container with no explicit
                    width shrink-wraps to its content.  Combined with
                    Layout's own internal flex container and `<main>`'s
                    `flexGrow: 1`, that produces a shrink-to-fit
                    cascade where the visible page width is determined
                    by the natural content width of the rendered page.
                    Pages with wide tables (Hosts, TestPlanDetail) end
                    up at viewport width incidentally; pages with
                    naturally narrow content (Activity, Scopes) render
                    at e.g. 837px on a 1080px viewport.  Setting
                    width: 100% pins the outer wrapper to the body's
                    full width and the cascade resolves correctly. */}
                <div className="flex w-full">
                  <Layout>
                    <Suspense fallback={<RouteSkeleton />}>
                      <Routes>
                      {/* v3 alpha.11 — / and /dashboard both redirect
                          to /operations.  Dashboard.tsx absorbed:
                          counter tiles ↔ Operations coverage tiles;
                          My Queue + My Tasks ↔ MyQueueCard/MyTasksCard
                          under Operations' Mine toggle; Team Activity
                          ↔ /activity (Collaboration page). */}
                      <Route path="/" element={<Navigate to="/operations" replace />} />
                      <Route path="/dashboard" element={<Navigate to="/operations" replace />} />
                      {/* v3 alpha.5 — Operations: coverage-first project
                          coordination view.  Additive — does not replace
                          Dashboard, Activity, or any existing surface. */}
                      <Route
                        path="/operations"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <Operations />
                          </ProtectedRoute>
                        }
                      />
                      {/* Hub paths redirect straight to a child page — the
                          interim card-grid landing was redundant with the
                          secondary-nav tab strip that lists the same children
                          on every child page.  ProtectedRoute (gated at the
                          hub role) is kept so the route/role manifest cross-
                          check still holds; HubRedirect picks the first
                          role-visible child (or the designated default).
                          Sub-page URLs unchanged for bookmark stability. */}
                      <Route
                        path="/inventory"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <HubRedirect hubId="inventory" />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/workflows"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <HubRedirect hubId="workflows" />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/collaboration"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <HubRedirect hubId="collaboration" />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/settings"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <HubRedirect hubId="settings" />
                          </ProtectedRoute>
                        }
                      />
                      {/* v3 alpha.12 — Recon Runs list.  Project-wide
                          with multi-select compare.  Static path
                          registered before /recon/runs/:sessionId so
                          React Router ranks it first. */}
                      <Route
                        path="/recon/runs"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <ReconRunsList />
                          </ProtectedRoute>
                        }
                      />
                      {/* v3 alpha.6 — Recon Run Detail.  Per-session
                          deep-dive: metadata + uploads + hosts +
                          plans-generated-from.  Linked from Operations
                          and from agent-activity for recon rows. */}
                      <Route
                        path="/recon/runs/:sessionId"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <ReconRunDetail />
                          </ProtectedRoute>
                        }
                      />
                      {/* v3 alpha.12 — Executions list.  Static path
                          registered before /executions/:sessionId. */}
                      <Route
                        path="/executions"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <ExecutionsList />
                          </ProtectedRoute>
                        }
                      />
                      {/* v3 alpha.7 — Execution Detail permalink.
                          Standalone view of one execution session,
                          addressable without knowing the plan id.
                          Composed from alpha.4 primitives. */}
                      <Route
                        path="/executions/:sessionId"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <ExecutionDetail />
                          </ProtectedRoute>
                        }
                      />
                      {/* v3 alpha.8 — Recon vs recon comparison.
                          Reads ?a=<recon_id>&b=<recon_id>. */}
                      <Route
                        path="/recon/compare"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <ReconCompare />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/scans"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <Scans />
                          </ProtectedRoute>
                        }
                      />
                      {/* Scan-diff (attack-surface delta).  Static
                          segment, so it outranks /scans/:scanId.
                          Reads ?a=<scan_id>&b=<scan_id>. */}
                      <Route
                        path="/scans/compare"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <ScanDiff />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/scans/:scanId"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <ScanDetail />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/hosts"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <Hosts />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/findings"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <Findings />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/findings/:findingId"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <FindingDetail />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/hosts/:hostId"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <HostDetail />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/activity"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <Activity />
                          </ProtectedRoute>
                        }
                      />
                      {/* v3 — Project Activity (unified agent timeline). */}
                      <Route
                        path="/agent-activity"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <ProjectActivity />
                          </ProtectedRoute>
                        }
                      />
                      {/* v2.56.0 — Tool Activity (cross-project SOC
                          correlation).  No project_id in the path
                          because the analyst arrives with a timestamp
                          and doesn't know which project owns the
                          activity yet. */}
                      <Route
                        path="/tool-activity"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <ToolActivity />
                          </ProtectedRoute>
                        }
                      />
                      {/* v3 alpha.8.1 — Plan-vs-plan comparison.  Compares
                          two DIFFERENT plans (different intent from
                          /test-plans/:planId/compare which compares two
                          executions of ONE plan).  Registered before the
                          dynamic /test-plans/:planId routes so React Router
                          ranks the static path first regardless. */}
                      <Route
                        path="/test-plans/compare"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <PlanCompare />
                          </ProtectedRoute>
                        }
                      />
                      {/* v3 — Cross-execution comparison (alpha.2).  Reads
                          ?a=<session_id>&b=<session_id> from the URL. */}
                      <Route
                        path="/test-plans/:planId/compare"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <TestPlanCompare />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/scopes"
                        element={
                          <ProtectedRoute requiredRole="analyst">
                            <Scopes />
                          </ProtectedRoute>
                        }
                      />
                      {/* v4.50.0 — ScopeDetail retired.  Project has
                          exactly one scope (since v2.9.4) so the
                          per-scope detail page duplicated /scopes for
                          everything except its "Mapped Hosts" tab,
                          which is now served better by /hosts with a
                          subnets filter.  Redirect preserves any
                          bookmarks / shared links. */}
                      <Route
                        path="/scopes/:scopeId"
                        element={<Navigate to="/scopes" replace />}
                      />
                      <Route
                        path="/network-topology"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <NetworkTopology />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/insights"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <SubnetInsights />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/parse-errors"
                        element={
                          <ProtectedRoute requiredRole="analyst">
                            <ParseErrors />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/default-credentials"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <DefaultCredentials />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/profile"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <Profile />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/tool-reference"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <ToolReference />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/system-settings"
                        element={
                          <ProtectedRoute requiredRole="admin">
                            <SystemSettings />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/feedback"
                        element={
                          <ProtectedRoute requiredRole="admin">
                            <Feedback />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/llm-settings"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <LLMSettings />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/integrations"
                        element={
                          <ProtectedRoute requiredRole="analyst">
                            <IntegrationSettings />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/project-settings"
                        element={
                          /* analyst — kept in sync with the Layout nav
                             entry and the ProjectSelector "Manage" item so
                             all three gate /project-settings identically
                             and the route is never reachable from a path
                             the IA says shouldn't expose it. */
                          <ProtectedRoute requiredRole="analyst">
                            <ProjectSettings />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/portfolio"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <PortfolioDashboard />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/test-plans"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <TestPlans />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/test-plans/:planId"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <TestPlanLayout />
                          </ProtectedRoute>
                        }
                      >
                        <Route index element={<Navigate to="plan" replace />} />
                        <Route path="plan" element={<TestPlanPlanTab />} />
                        <Route path="runs" element={<TestPlanRunsTab />} />
                        <Route path="activity" element={<TestPlanActivityTab />} />
                        <Route path="api-calls" element={<TestPlanApiCallsTab />} />
                        <Route path="danger" element={<TestPlanDangerTab />} />
                      </Route>
                      <Route
                        path="/reference"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <Reference />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/reference/user-guide"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <UserGuide />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/reference/sbom"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <SbomReference />
                          </ProtectedRoute>
                        }
                      />
                    </Routes>
                    </Suspense>
                  </Layout>
                  {/* VersionFooter removed per UX audit #12 —
                      build info now lives in the UserMenu "About" entry
                      so it doesn't occlude table pagination or snackbars. */}
                </div>
                </ProjectProvider>
              </ProtectedRoute>
            }
          />
          </Routes>
          </TooltipProvider>
        </AuthProvider>
      </ToastProvider>
    </CustomThemeProvider>
  );
}

export default App;
