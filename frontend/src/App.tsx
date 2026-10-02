import React, { Suspense, lazy } from 'react';
import { Routes, Route, Navigate, useLocation, matchPath } from 'react-router-dom';
import { TooltipProvider } from '@radix-ui/react-tooltip';
import { CustomThemeProvider } from './contexts/ThemeContext';
import { AuthProvider } from './contexts/AuthContext';
import { ProjectProvider } from './contexts/ProjectContext';
import { ToastProvider } from './contexts/ToastContext';
import Layout from './components/Layout';
import ProtectedRoute from './components/ProtectedRoute';
import ErrorBoundary from './components/ErrorBoundary';
import HubRedirect from './components/HubRedirect';
import { ListPageSkeleton, DetailSkeleton, CardListSkeleton } from './components/PageSkeleton';
import Login from './pages/Login';
import NotFound, { ProjectsRedirect } from './pages/NotFound';

/**
 * Route-aware Suspense fallback (audit H16 + PRF·H2).  Pre-audit every
 * lazy route used ListPageSkeleton, which is table-shaped.  Detail
 * pages (`/scopes/:id`, `/hosts/:id`, `/findings/:id`) flashed a
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
// patterns so e.g. `/scans/:id` resolves to detail before
// `/scans` would resolve to list.
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
  { pattern: '/agent-sessions/:id', kind: 'detail' },
  { pattern: '/findings/:id', kind: 'detail' },
  { pattern: '/profile', kind: 'detail' },
  { pattern: '/force-change-password', kind: 'detail' },
  { pattern: '/force-2fa-setup', kind: 'detail' },
  // cards
  { pattern: '/portfolio', kind: 'cards' },
  { pattern: '/oversight', kind: 'cards' },
  { pattern: '/llm-settings', kind: 'cards' },
  { pattern: '/integrations', kind: 'cards' },
  { pattern: '/operations', kind: 'cards' },
  { pattern: '/inventory', kind: 'cards' },
  { pattern: '/workflows', kind: 'cards' },
  { pattern: '/collaboration', kind: 'cards' },
  { pattern: '/settings', kind: 'cards' },
  { pattern: '/administration', kind: 'cards' },
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
const Hosts = lazy(() => import('./pages/Hosts'));
const Activity = lazy(() => import('./pages/Activity'));
const Proposals = lazy(() => import('./pages/Proposals'));
const HostDetail = lazy(() => import('./pages/HostDetail'));
const Scopes = lazy(() => import('./pages/Scopes'));
const Names = lazy(() => import('./pages/Names'));
const SecurityPosture = lazy(() => import('./pages/SecurityPosture'));
const Segments = lazy(() => import('./pages/Segments'));
const Patterns = lazy(() => import('./pages/Patterns'));
const Evidence = lazy(() => import('./pages/Evidence'));
const ParseErrors = lazy(() => import('./pages/ParseErrors'));
const DefaultCredentials = lazy(() => import('./pages/DefaultCredentials'));
const Profile = lazy(() => import('./pages/Profile'));
const SystemSettings = lazy(() => import('./pages/SystemSettings'));
const ToolReference = lazy(() => import('./pages/ToolReference'));
const ProjectSettings = lazy(() => import('./pages/ProjectSettings'));
const AllProjects = lazy(() => import('./pages/AllProjects'));
const PortfolioDashboard = lazy(() => import('./pages/PortfolioDashboard'));
const Oversight = lazy(() => import('./pages/Oversight'));
const Reference = lazy(() => import('./pages/Reference'));
// User Guide — multi-page under /reference/user-guide/* (see pages/userguide/).
const GettingStartedGuide = lazy(() => import('./pages/userguide/GettingStartedGuide'));
const DataGuide = lazy(() => import('./pages/userguide/DataGuide'));
const TriageGuide = lazy(() => import('./pages/userguide/TriageGuide'));
const AgentsGuide = lazy(() => import('./pages/userguide/AgentsGuide'));
const AdminGuide = lazy(() => import('./pages/userguide/AdminGuide'));
const SbomReference = lazy(() => import('./pages/SbomReference'));
const ToolCoverage = lazy(() => import('./pages/ToolCoverage'));
const McpReference = lazy(() => import('./pages/McpReference'));
const AssistSessions = lazy(() => import('./pages/AssistSessions'));
const AgentSessionDetail = lazy(() => import('./pages/AgentSessionDetail'));
const Feedback = lazy(() => import('./pages/Feedback'));
const LLMSettings = lazy(() => import('./pages/LLMSettings'));
const IntegrationSettings = lazy(() => import('./pages/IntegrationSettings'));
const ForceChangePassword = lazy(() => import('./pages/ForceChangePassword'));
const ForceTwoFactorSetup = lazy(() => import('./pages/ForceTwoFactorSetup'));
const ProjectActivity = lazy(() => import('./pages/ProjectActivity'));
// v2.56.0 — cross-project SOC-correlation page.  Different intent from
// /activity (notes/notifications) and /agent-activity (per-project
// agent timeline): asks "what tools ran across all my projects at
// time X" for correlating against SOC alerts.
const ToolActivity = lazy(() => import('./pages/ToolActivity'));
const Operations = lazy(() => import('./pages/Operations'));
const Findings = lazy(() => import('./pages/Findings'));
const FindingDetail = lazy(() => import('./pages/FindingDetail'));
const Reports = lazy(() => import('./pages/Reports'));
const ReportDetail = lazy(() => import('./pages/ReportDetail'));

/** A renamed path: go to the new one with the same query string and hash. */
function RedirectKeepingQuery({ to }: { to: string }) {
  const { search, hash } = useLocation();
  return <Navigate to={`${to}${search}${hash}`} replace />;
}

// Route-level error boundary for the page area inside Layout. Keyed on
// pathname so navigating to another page clears a page-specific crash, while a
// crash itself keeps the Layout/sidebar/nav mounted instead of blanking the app.
function RoutedErrorBoundary({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  // The pathname key also REMOUNTS the page on every navigation between
  // routes — /findings/3 → /findings/4 is a fresh FindingDetail, not the same
  // instance with a new id.  Detail pages rely on that for stale-response
  // safety (a late response for #3 has no mounted component to write into);
  // if this key ever goes, each of them needs a latest-request guard
  // (hooks/useLatestRequest, hooks/useListQuery) first.
  return (
    <ErrorBoundary key={location.pathname} scope="route">
      {children}
    </ErrorBoundary>
  );
}

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

          {/* Forced 2FA enrollment (REQUIRE_2FA) — no Layout/sidebar */}
          <Route
            path="/force-2fa-setup"
            element={
              <ProtectedRoute>
                <Suspense fallback={<RouteSkeleton />}>
                  <ForceTwoFactorSetup />
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
                      <RoutedErrorBoundary>
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
                      {/* v5.294.0 — the instance's pages (All projects,
                          System), apart from the project's settings. */}
                      <Route
                        path="/administration"
                        element={
                          <ProtectedRoute requiredRole="admin">
                            <HubRedirect hubId="administration" />
                          </ProtectedRoute>
                        }
                      />
                      {/* 5.320.0 — test plans and execution runs are gone:
                          tests are proposed on hosts and shown on each
                          host's page.  Old links (bookmarks, earlier
                          notifications) land on the hosts that have tests
                          still to do. */}
                      <Route path="/test-plans/*" element={<Navigate to="/hosts?q=has%3Aplanned" replace />} />
                      <Route path="/executions/*" element={<Navigate to="/agent-activity" replace />} />
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
                      {/* Every client-report route is AUDITOR on the server
                          (client_reports.py gates the router), so the page
                          follows that read rule (style guide §40): a project
                          viewer is not offered Reports, and a direct link
                          gets the access screen instead of a failed load. */}
                      <Route
                        path="/reports"
                        element={
                          <ProtectedRoute requiredRole="auditor">
                            <Reports />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/reports/:reportId"
                        element={
                          <ProtectedRoute requiredRole="auditor">
                            <ReportDetail />
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
                      {/* v5.173.0 — AI Assist review, keyed by the detail
                          row's id. v5.312.0 — both paths redirect: the list
                          to Agent Sessions, a session to its page below. */}
                      <Route
                        path="/assist-sessions"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <Navigate to="/agent-activity" replace />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/assist-sessions/:sessionId"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <AssistSessions />
                          </ProtectedRoute>
                        }
                      />
                      {/* v5.312.0 — one agent session: its state, controls,
                          the work it opened, notes and calls. */}
                      <Route
                        path="/agent-sessions/:sessionId"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <AgentSessionDetail />
                          </ProtectedRoute>
                        }
                      />
                      {/* v3 — Project Activity (unified agent timeline);
                          v5.312.0 — Agent Sessions. */}
                      <Route
                        path="/agent-activity"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <ProjectActivity />
                          </ProtectedRoute>
                        }
                      />
                      {/* v5.316.0 — agents' proposed changes, for a person to decide. */}
                      <Route
                        path="/proposals"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <Proposals />
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
                      <Route
                        path="/scopes"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <Scopes />
                          </ProtectedRoute>
                        }
                      />
                      {/* v5.193.0 — named assets (FQDN inventory). */}
                      <Route
                        path="/names"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <Names />
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
                        path="/posture"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <SecurityPosture />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/posture/segments"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <Segments />
                          </ProtectedRoute>
                        }
                      />
                      {/* Legacy path → new Segments page (Phase 3 IA). */}
                      <Route path="/insights" element={<Navigate to="/posture/segments" replace />} />
                      <Route
                        path="/posture/evidence"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <Evidence />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/posture/patterns"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <Patterns />
                          </ProtectedRoute>
                        }
                      />
                      {/* Legacy path → new Patterns page (Phase 3 IA). */}
                      <Route path="/insights/systemic" element={<Navigate to="/posture/patterns" replace />} />
                      <Route
                        path="/ingestion-results"
                        element={
                          <ProtectedRoute requiredRole="analyst">
                            <ParseErrors />
                          </ProtectedRoute>
                        }
                      />
                      {/* v5.294.0 — the old path; links and bookmarks keep
                          their filters (?status=…) across the redirect. */}
                      <Route path="/parse-errors" element={<RedirectKeepingQuery to="/ingestion-results" />} />
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
                          /* viewer — integrations are ACCOUNT-level and their
                             list is open to every signed-in user; `analyst`
                             here is the PROJECT role and refused a viewer of
                             the selected project a page listing their own
                             integrations.  Writes need the global admin: the
                             page hides them for everyone else. */
                          <ProtectedRoute requiredRole="viewer">
                            <IntegrationSettings />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/project-settings"
                        element={
                          /* viewer — a page follows the server's READ rule
                             (style guide §40): every member reads the
                             project's details, members and tags, and the page
                             is read-only by `my_role`.  Kept in sync with the
                             nav entry (tests/navigation.test.ts). */
                          <ProtectedRoute requiredRole="viewer">
                            <ProjectSettings />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/settings/projects"
                        element={
                          <ProtectedRoute requiredRole="admin">
                            <AllProjects />
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
                        path="/oversight"
                        element={
                          // Global administrators only (the API router is
                          // admin-gated too).
                          <ProtectedRoute requiredRole="admin">
                            <Oversight />
                          </ProtectedRoute>
                        }
                      />
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
                            <GettingStartedGuide />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/reference/user-guide/data"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <DataGuide />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/reference/user-guide/triage"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <TriageGuide />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/reference/user-guide/agents"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <AgentsGuide />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/reference/user-guide/admin"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <AdminGuide />
                          </ProtectedRoute>
                        }
                      />
                      <Route
                        path="/reference/mcp"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <McpReference />
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
                      <Route
                        path="/reference/tool-coverage"
                        element={
                          <ProtectedRoute requiredRole="viewer">
                            <ToolCoverage />
                          </ProtectedRoute>
                        }
                      />
                      {/* v5.290.0 — kept LAST, and without a requiredRole of
                          their own (navigation.test.ts pairs each path with the
                          next requiredRole it finds).  /projects goes where the
                          projects are; any other unknown URL gets a
                          not-found page instead of an empty layout. */}
                      <Route path="/projects" element={<ProjectsRedirect />} />
                      <Route path="*" element={<NotFound />} />
                    </Routes>
                    </RoutedErrorBoundary>
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
