/**
 * Tests for the v3 alpha.5 Operations page.
 *
 * Pins the coverage-first surface contract: each section renders
 * with realistic backend data, the Mine/All toggle propagates to
 * the agent-sessions calls, and Needs Attention surfaces pending
 * plans independently of the toggle.
 */
import React from 'react';
import { render, screen, waitFor, within, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// Override the global setupTests.ts react-router-dom mock so useNavigate
// is observable here.
const navigateSpy = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>(
    'react-router-dom',
  );
  return {
    ...actual,
    useNavigate: () => navigateSpy,
    useParams: () => ({}),
  };
});

vi.mock('../../services/api', () => ({
  getProjectCoverage: vi.fn(),
  getTestPlans: vi.fn(),
  listAgentSessions: vi.fn(),
  // v4.59.0 (NEW I) — Operations.reload Promise.all also awaits
  // getDashboardStats() and getStaleness().  Pre-fix the mock
  // omitted both; the resulting "X is not a function" throw
  // landed in the catch and the page rendered an error alert
  // instead of any of the section content the tests asserted on.
  // Default-empty resolves so the page renders its empty-state
  // cleanly.
  getDashboardStats: vi.fn().mockResolvedValue({
    total_scans: 0,
    total_hosts: 0,
    total_ports: 0,
    up_hosts: 0,
    open_ports: 0,
    total_subnets: 0,
    recent_scans: [],
    subnet_stats: [],
  }),
  getStaleness: vi.fn().mockResolvedValue(null),
  // P2 — Operations now owns ONE /workbench fetch and prop-drives the
  // personal cards (My Queue / My Tasks) + the since-last-visit diff.
  // Mocked empty so the cards render their empty-state and the banner
  // suppresses (first visit).  (team_review is still in the mock payload
  // but no longer rendered — the Team Review card was removed.)
  getWorkbench: vi.fn().mockResolvedValue({
    my_queue: { items: [], in_review_count: 0, watching_count: 0 },
    my_tasks: { items: [], total_open: 0, reason_counts: { assigned: 0, in_review: 0, triage: 0 } },
    team_review: { reviewers: [], total_hosts_in_review: 0 },
    since_last_visit: {
      last_viewed_at: null,
      is_first_visit: true,
      new_scan_count: 0,
      latest_scan_id: null,
      latest_scan_filename: null,
      latest_scan_created_at: null,
      new_host_count: 0,
      new_critical_findings: 0,
      new_high_findings: 0,
    },
  }),
  markWorkbenchSeen: vi.fn().mockResolvedValue({ last_viewed_at: '2026-01-01T00:00:00Z' }),
  getCurrentProjectId: vi.fn(() => 1),
  setCurrentProjectId: vi.fn(),
}));

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 7, username: 'test-admin', role: 'admin' },
    isAuthenticated: true,
    authStatus: 'authenticated',
    hasPermission: () => true,
    hasRole: () => true,
  }),
}));

import * as api from '../../services/api';
import Operations from '../../pages/Operations';

const mockedApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;


function renderPage() {
  return render(
    <MemoryRouter>
      <Operations />
</MemoryRouter>,
  );
}

const baseCoverage = {
  project_id: 1,
  total_hosts: 142,
  hosts_with_plan_entry: 87,
  hosts_with_execution_result: 23,
  hosts_no_plan: 55,
  hosts_no_execution: 119,
  total_scopes: 1,
  scopes: [
    {
      scope_id: 10,
      scope_name: 'Internal /24',
      subnet_count: 1,
      total_scoped_ips: 256,
      discovered_in_scope: 42,
      coverage_percent: 16.4,
    },
  ],
  hosts_outside_scope: 12,
};

const baseSession = {
  kind: 'execution' as const,
  id: 99,
  project_id: 1,
  agent_id: null,
  agent_name: null,
  user_id: 7,
  user_username: 'test-admin',
  status: 'active',
  started_at: '2026-05-15T18:00:00Z',
  completed_at: null,
  generated_by_model: 'claude-opus-4-7',
  generated_by_tool: 'claude-code',
  prompt_version: '1.13.0',
  scope_id: null,
  test_plan_id: 11,
};

beforeEach(() => {
  vi.clearAllMocks();
  navigateSpy.mockReset();
  localStorage.removeItem('nm.operations.scopeView');
  mockedApi.getProjectCoverage.mockResolvedValue(baseCoverage);
  mockedApi.getTestPlans.mockResolvedValue([
    {
      id: 5,
      project_id: 1,
      version: 1,
      title: 'Pending plan',
      status: 'proposed',
      entry_count: 8,
      completion_pct: 0,
      generated_by_model: 'claude-opus-4-7',
      created_at: '2026-05-15T10:00:00Z',
      updated_at: '2026-05-15T10:30:00Z',
    },
  ]);
  // First call: ?status=active (active runs).
  // Second call: no status (recent runs).
  mockedApi.listAgentSessions.mockResolvedValue({
    project_id: 1,
    sessions: [baseSession],
    total: 1,
  });
});

describe('Operations page', () => {
  it('renders the coverage tiles in the merged Project state card', async () => {
    renderPage();
    // RV-UI — "Security snapshot" + "Project coverage" merged into one
    // "Project state" card with Exposure + Coverage rows; the duplicate
    // Hosts tile (was coverage.total_hosts=142) was dropped.
    await waitFor(() => {
      expect(screen.getByText('Project state')).toBeInTheDocument();
    });
    expect(screen.getByText('Coverage')).toBeInTheDocument();
    // Coverage tiles still render their values.
    expect(screen.getByText('87')).toBeInTheDocument(); // hosts_with_plan_entry
    expect(screen.getByText('23')).toBeInTheDocument(); // hosts_with_execution_result
    expect(screen.getByText('12')).toBeInTheDocument(); // hosts_outside_scope
    // Scope-coverage row.
    expect(screen.getByText('Internal /24')).toBeInTheDocument();
    // v4.59.0 (NEW I) — pre-fix asserted on "16.4%"; the page now
    // renders the scope-row tail as "<discovered_in_scope> hosts
    // discovered" without surfacing the raw percent.  Pin the
    // host-count instead — that's the operator-facing signal that
    // survived the redesign.
    expect(screen.getByText(/42 hosts discovered/)).toBeInTheDocument();
  });

  it('surfaces a pending-review plan in the Pending approvals queue', async () => {
    renderPage();
    await waitFor(() => {
      // §27 role-aware: the test user has the analyst role (mocked
      // hasPermission → true), so the heading is the actionable
      // "Needs your approval" rather than the passive "Pending approvals".
      expect(screen.getByRole('heading', { name: 'Needs your approval' })).toBeInTheDocument();
    });
    expect(screen.getByText('1 pending review')).toBeInTheDocument();
    expect(screen.getByText(/Pending plan/)).toBeInTheDocument();
    // v4.59.0 (NEW I) — pre-fix used /Review/i which now also matches
    // the "All In Review" filter chip; tighten to an exact name.
    fireEvent.click(screen.getByRole('button', { name: /^Review$/ }));
    expect(navigateSpy).toHaveBeenCalledWith('/test-plans/5');
  });

  it('renders the consolidated Runs section from /agent-sessions', async () => {
    // v3 alpha.15: ActiveRunsSection + RecentRunsSection collapsed
    // into a single RunsSection with status filter chips.  Default
    // filter is "all" (no status param).
    renderPage();
    await waitFor(() => {
      // Use getAllByText to allow other "Runs" text on the page (filter
      // chip is also labelled "Runs"... actually just one heading "Runs"
      // is rendered).
      expect(screen.getByRole('heading', { name: /^Runs$/ })).toBeInTheDocument();
    });
    // Initial fetch should be the default ("all" → no status param).
    const calls = mockedApi.listAgentSessions.mock.calls.map((c) => c[0]);
    expect(calls.some((c: any) => c?.status === undefined)).toBe(true);
  });

  it('Mine toggle propagates user_id to the Runs section fetch and persists to localStorage', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /^Runs$/ })).toBeInTheDocument();
    });
    // Initial: All — no user_id filter on Runs fetch.
    const firstCallArgs = mockedApi.listAgentSessions.mock.calls.map((c) => c[0]);
    expect(firstCallArgs.every((c: any) => c?.user_id === undefined)).toBe(true);

    // Click Mine — RunsSection re-fetches with user_id=7.
    fireEvent.click(screen.getByRole('button', { name: 'Mine' }));
    await waitFor(() => {
      const allCalls = mockedApi.listAgentSessions.mock.calls.map((c) => c[0]);
      expect(allCalls.some((c: any) => c?.user_id === 7)).toBe(true);
    });
    expect(localStorage.getItem('nm.operations.scopeView')).toBe('mine');
  });

  it('Active filter chip narrows the Runs fetch to status=active', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /^Runs$/ })).toBeInTheDocument();
    });
    // Click the "Active" filter chip in the Runs section.
    fireEvent.click(screen.getByRole('button', { name: /^Active$/ }));
    await waitFor(() => {
      const calls = mockedApi.listAgentSessions.mock.calls.map((c) => c[0]);
      expect(calls.some((c: any) => c?.status === 'active')).toBe(true);
    });
  });

  it('shows an error alert when the API fails', async () => {
    mockedApi.getProjectCoverage.mockRejectedValue(
      new Error('coverage unavailable'),
    );
    renderPage();
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
  });

  it('RV-10b: a non-coverage failure does not blank the page', async () => {
    // Stats rejects, but coverage succeeds — the page must still render
    // the coverage section instead of collapsing to the error alert.
    mockedApi.getDashboardStats.mockRejectedValue(new Error('stats down'));
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Project state')).toBeInTheDocument();
    });
    expect(screen.queryByText('Failed to load Operations data.')).not.toBeInTheDocument();
  });
});
