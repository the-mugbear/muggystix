/**
 * The Operations page — tabs, one list at a time (5.331.0; the reader's own
 * page since 5.330.0, redesigned 5.329.0).
 *
 * Pins the page's shape: a lead whose numbers open tabs, a tab bar with
 * counts, the ONE selected list, and one line for the reader's agent sessions
 * — and NO project status (Posture has it).  Each list's own behaviour is in
 * its component test (`OperationsTables`, `ChangedSinceReviewSection`,
 * `UntouchedQueueSection`); paging's mechanics in `usePagedList.test`.
 *
 * 5.331.0 — what the removed tests here guarded, and where it is now:
 *  - "the sections, in order" (My work · Changed since review · Untouched):
 *    the tab bar's order, below;
 *  - "renders sensibly with hosts but no reviews, tests or findings" (three
 *    empty lines at once): one empty line per tab — the component tests — and
 *    "with nothing anywhere" below;
 *  - the lead's `#my-work` / `#changed-since-review` / `#untouched-queue`
 *    anchors: the lead's links open tabs, below;
 *  - "Show 15 more asks the server for a longer page": the queue is paged
 *    ("a tab pages through its whole list", below; `usePagedList.test`);
 *  - "one list at a time owns the keyboard": only one list is on screen
 *    ("only the selected tab is mounted", below);
 *  - "a failed check of the reviewed hosts says so": the tab's count reads
 *    "—" (below), and the list's own failure line (`ChangedSinceReviewSection`).
 *
 * 5.330.0 — what left for Posture: the measures strip, the terrain, the
 * Exposure block (`AddressTerrainSection.test`, `PostureExposureSection.test`,
 * `SecurityPostureOverview.test`).  Earlier: the Runs section, the activity
 * column and Project state (Agent Sessions, Collaboration, Posture).
 */
import { act, render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { MemoryRouter, RouterProvider, createMemoryRouter } from 'react-router-dom';

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

const emptyWorkbench = {
  my_queue: { items: [], in_review_count: 0 },
  my_tasks: {
    items: [], total_open: 0,
    reason_counts: { assigned: 0, in_review: 0, triage: 0 },
    group_counts: { assigned: 0, in_review: 0, triage: 0 },
  },
  my_findings: { items: [], total_open: 0 },
  my_work: {
    total: 0, hosts_in_review: 0, tests_assigned: 0, tests_on_hosts_in_review: 0,
    findings_needing_me: 0, findings_to_decide: 0, findings_to_write: 0, to_claim: 0,
  },
  followups: { items: [], total: 0 },
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
};
const TIERS = ['Exploitable critical', 'Critical vulnerability', 'Exploit available', 'High-value service, new or changed', 'Scans disagree'];
const emptyQueue = { items: [], queue_total: 0, untouched_total: 0, tiers: TIERS, tier_counts: [0, 0, 0, 0, 0] };

vi.mock('../../services/api', () => ({
  getProjectCoverage: vi.fn(),
  listAgentSessions: vi.fn(),
  // Posture's reads: mocked only so the page can be shown NOT to make them.
  getDashboardStats: vi.fn(),
  getAddressTerrain: vi.fn(),
  getWorkbench: vi.fn(),
  getInvestigationQueue: vi.fn(),
  getMyFindingsPage: vi.fn(),
  getMyReviewHostsPage: vi.fn(),
  getMyTestsPage: vi.fn(),
  getReviewFollowupsPage: vi.fn(),
  markWorkbenchSeen: vi.fn(),
  markStillReviewed: vi.fn(),
  followHost: vi.fn(),
  unfollowHost: vi.fn(),
  updateHostTest: vi.fn(),
  getCurrentProjectId: vi.fn(() => 1),
  setCurrentProjectId: vi.fn(),
}));

// The ACCOUNT role: a global admin writes everywhere, whatever the project role.
const accountRole = vi.hoisted(() => ({ value: 'admin' }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({
    user: { id: 7, username: 'test-admin', role: accountRole.value },
    isAuthenticated: true,
    authStatus: 'authenticated',
    hasPermission: () => true,
    hasRole: () => true,
  }),
}));
// The caller's role on the project; unset = not yet known (controls shown).
const projectRole = vi.hoisted(() => ({ value: undefined as string | undefined }));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({
    currentProject: { id: 1, name: 'P', ...(projectRole.value ? { my_role: projectRole.value } : {}) },
  }),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));

import * as api from '../../services/api';
import Operations from '../../pages/Operations';

const mockedApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

function renderPage(entry = '/operations') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Operations />
    </MemoryRouter>,
  );
}

/** With a router the test can drive: where the page is, and Back. */
function renderRouted(entry = '/operations') {
  const router = createMemoryRouter(
    [{ path: '/operations', element: <Operations /> }],
    { initialEntries: [entry] },
  );
  render(<RouterProvider router={router} />);
  return router;
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
  hosts_in_subnet_scope: 128,
  hosts_name_scope_only: 2,
};

const session = (over: Record<string, unknown> = {}) => ({
  kind: 'project' as const,
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
  purpose: 'SMB review',
  key_expires_at: new Date(Date.now() + 3_600_000).toISOString(),
  renewable_until: new Date(Date.now() + 86_400_000).toISOString(),
  ...over,
});

const q = (el: HTMLElement) => new URL(el.getAttribute('href') ?? '', 'https://x').searchParams.get('q');

// -- a project with work in every tab (the owner's: 24 · 37 · 40 · 27 · 112) --
const busy = (over: Record<string, unknown> = {}) => ({
  ...emptyWorkbench,
  my_queue: { items: [], in_review_count: 37 },
  my_tasks: {
    items: [], total_open: 55,
    reason_counts: { assigned: 12, in_review: 30, triage: 15 },
    group_counts: { assigned: 12, in_review: 28, triage: 15 },
  },
  my_findings: { items: [], total_open: 24 },
  my_work: {
    total: 101, hosts_in_review: 37, tests_assigned: 12, tests_on_hosts_in_review: 28,
    findings_needing_me: 24, findings_to_decide: 3, findings_to_write: 21, to_claim: 15,
  },
  followups: { items: [], total: 27 },
  ...over,
});
/** No finding needs the reader. */
const NO_FINDINGS = { findings_needing_me: 0, findings_to_decide: 0, findings_to_write: 0 };
const NEEDS = { decide: 3, write: 21 };
const findingRow = (id: number) => ({
  finding_id: id, title: `Weak TLS ${id}`, severity: 'critical', status: 'open', host_id: null,
  host_count: 3, evidence_annotation_id: null, updated_at: null,
  needs: [{ kind: 'under_investigation', text: 'under investigation' }], missing_text: [], pending_proposals: 0,
});
const hostRow = (id: number) => ({
  host_id: id, ip_address: `10.9.0.${id}`, hostname: null, follow_status: 'in_review',
  open_port_count: 2, critical_vulns: 1, high_vulns: 0, last_viewed_at: null, follow_updated_at: null,
});
const testRow = (id: number, reason = 'triage') => ({
  test_id: id, tool: 'nxc', description: `SMB signing ${id}`, label: null, revision: 4,
  host_id: 5, host_ip: '10.0.0.5', host_hostname: null, priority: 'high', status: 'proposed',
  rationale: null, updated_at: null, reasons: [reason], assigned_to_id: reason === 'assigned' ? 7 : null,
});
const followRow = {
  host_id: 21, ip_address: '10.8.0.2', hostname: 'app01',
  reviewed_at: '2026-09-01T00:00:00Z', review_conclusion: 'no_issue', review_summary: null,
  reasons: [{ kind: 'new_ports', text: '1 open port first seen after the review (8443)' }],
};
const queueRow = {
  host_id: 7, ip_address: '10.0.0.7', hostname: null, tier: 1, tier_label: 'Exploitable critical',
  reasons: [{ kind: 'critical_exploitable', text: '1 critical vulnerability with a known public exploit' }],
  evidence: { sources: [], last_seen: null, confirmation: 'scanner' },
  next_action: { kind: 'review', text: 'Take it into review.', generic: true },
};
const busyQueue = { items: [queueRow], queue_total: 112, untouched_total: 251, tiers: TIERS, tier_counts: [3, 30, 4, 74, 1] };

const withWork = (over: Record<string, unknown> = {}) => {
  mockedApi.getWorkbench.mockResolvedValue(busy(over));
  mockedApi.getInvestigationQueue.mockResolvedValue(busyQueue);
  mockedApi.getMyFindingsPage.mockResolvedValue({
    items: [findingRow(21), findingRow(1)], total_open: 24, need_counts: NEEDS,
  });
  mockedApi.getMyReviewHostsPage.mockResolvedValue({ items: [hostRow(1), hostRow(2)], in_review_count: 37 });
  mockedApi.getMyTestsPage.mockResolvedValue({
    items: [testRow(31, 'assigned'), testRow(32)], total_open: 55,
    reason_counts: { assigned: 12, in_review: 30, triage: 15 },
    group_counts: { assigned: 12, in_review: 28, triage: 15 },
  });
  mockedApi.getReviewFollowupsPage.mockResolvedValue({ items: [followRow], total: 27 });
};

const tab = (name: RegExp | string) => screen.getByRole('tab', { name });
const selectedTab = () => screen.getAllByRole('tab').find((t) => t.getAttribute('aria-selected') === 'true')?.textContent;
/** Radix selects a tab on mouse down. */
const openTab = (name: RegExp | string) => fireEvent.mouseDown(tab(name), { button: 0 });
const listCalls = () => ({
  findings: mockedApi.getMyFindingsPage.mock.calls.length,
  hosts: mockedApi.getMyReviewHostsPage.mock.calls.length,
  tests: mockedApi.getMyTestsPage.mock.calls.length,
  changed: mockedApi.getReviewFollowupsPage.mock.calls.length,
  // The queue's ROWS: every request but the count's (one row, no tier).
  pickup: mockedApi.getInvestigationQueue.mock.calls.filter((c) => c[2]?.limit !== 1).length,
});

beforeEach(() => {
  vi.clearAllMocks();
  navigateSpy.mockReset();
  projectRole.value = undefined;
  accountRole.value = 'admin';
  mockedApi.getProjectCoverage.mockResolvedValue(baseCoverage);
  mockedApi.getWorkbench.mockResolvedValue(emptyWorkbench);
  mockedApi.getInvestigationQueue.mockResolvedValue(emptyQueue);
  mockedApi.getMyFindingsPage.mockResolvedValue({ items: [], total_open: 0, need_counts: { decide: 0, write: 0 } });
  mockedApi.getMyReviewHostsPage.mockResolvedValue({ items: [], in_review_count: 0 });
  mockedApi.getMyTestsPage.mockResolvedValue({ ...emptyWorkbench.my_tasks });
  mockedApi.getReviewFollowupsPage.mockResolvedValue({ items: [], total: 0 });
  mockedApi.markWorkbenchSeen.mockResolvedValue({ last_viewed_at: '2026-01-01T00:00:00Z' });
  mockedApi.listAgentSessions.mockResolvedValue({ project_id: 1, sessions: [session()], total: 1 });
});

describe('Operations page', () => {
  it('opens the Start Agent Session dialog when linked with ?start=agent-session', async () => {
    // The Agent Sessions page links here as "where a session is started".
    renderPage('/operations?start=agent-session');
    expect(
      await screen.findByRole('dialog', { name: /Start Agent Session/ }),
    ).toBeInTheDocument();
  });

  it('does not open the dialog without the param', async () => {
    renderPage();
    await screen.findByRole('tablist');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
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

  describe('shape (5.331.0): a tab bar and one list', () => {
    it('lead, tab bar with counts, the one selected list, the agent line — and nothing else', async () => {
      withWork();
      renderPage();
      await screen.findByRole('table');
      await screen.findByText(/session of yours live now/);
      await waitFor(() => expect(tab(/^Pick up/)).toHaveTextContent('Pick up112'));

      // The bar, in order, each tab with its count.
      expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual([
        'Findings24', 'Hosts37', 'Tests40+ 15 to claim', 'Changed since review27', 'Pick up112',
      ]);
      // Which number is which, for a screen reader too.
      expect(tab(/^Tests/)).toHaveAccessibleName('Tests: 40 yours, 15 free to claim');
      // ONE list on screen: one panel, one table.
      expect(screen.getAllByRole('tabpanel')).toHaveLength(1);
      expect(screen.getAllByRole('table')).toHaveLength(1);
      // No stack of sections: the page's only heading is its title.
      expect(screen.getAllByRole('heading').map((h) => h.textContent)).toEqual(['Operations']);

      const before = (x: Element, y: Element) =>
        // eslint-disable-next-line no-bitwise
        !!(x.compareDocumentPosition(y) & Node.DOCUMENT_POSITION_FOLLOWING);
      const lead = screen.getByTestId('lead-needs-me');
      expect(before(lead, screen.getByRole('tablist'))).toBe(true);
      // No grand total anywhere on the page (owner, 2026-10-02).
      expect(document.body).not.toHaveTextContent(/\bitems?\b|in your queue|101/);
      expect(before(screen.getByRole('tablist'), screen.getByRole('tabpanel'))).toBe(true);
      // The agent line closes the page.
      expect(before(screen.getByRole('tabpanel'), screen.getByText(/session of yours live now/))).toBe(true);

      // No samples and no "more": the list is paged.
      expect(screen.queryByRole('button', { name: /Show .* more|Show fewer/ })).not.toBeInTheDocument();
      expect(screen.queryByText(/^My work$|Available to claim|waiting on you/)).not.toBeInTheDocument();

      // 5.330.0 — project status left for Posture: no measures strip, no
      // terrain, no exposure block, and no request for any of them.
      expect(screen.queryByRole('group', { name: 'Where the engagement stands' })).not.toBeInTheDocument();
      expect(screen.queryByText(/^Tested$|not yet tested|Untouched hosts with a critical observation/)).not.toBeInTheDocument();
      expect(screen.queryByText(/Where the team has been/)).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /Show the (map|table)/ })).not.toBeInTheDocument();
      expect(screen.queryByText(/^Exposure$|Scanner observations by severity|in scope subnets|outside scope/)).not.toBeInTheDocument();
      expect(screen.queryByText(/Across the team/)).not.toBeInTheDocument();
      expect(mockedApi.getAddressTerrain).not.toHaveBeenCalled();
      expect(mockedApi.getDashboardStats).not.toHaveBeenCalled();

      // Gone since 5.329.0: the subtitle, the Runs list, the recent-activity column, Project state.
      expect(screen.queryByText(/Project-wide coordination view/)).not.toBeInTheDocument();
      expect(screen.queryByRole('heading', { name: /^Runs$/ })).not.toBeInTheDocument();
      expect(screen.queryByText(/My recent activity/)).not.toBeInTheDocument();
      expect(screen.queryByText('Project state')).not.toBeInTheDocument();
      // Nothing waits on an approval, and nothing speaks of plans.
      expect(screen.queryByText(/approv|plan entr|in any plan/i)).toBeNull();
    });

    it('renders no card anywhere on the page', async () => {
      withWork();
      renderPage();
      await screen.findByRole('table');
      expect(document.querySelector('.rounded-panel.border.bg-card')).toBeNull();
    });

    it('the counts are one light request, and the queue’s total one row', async () => {
      withWork();
      renderPage();
      await screen.findByRole('table');
      // No rows and no queue in the workbench; the queue's size on its own.
      expect(mockedApi.getWorkbench).toHaveBeenCalledWith(
        1, { includeInvestigate: false, includeRows: false }, expect.any(AbortSignal),
      );
      expect(mockedApi.getWorkbench).toHaveBeenCalledTimes(1);
      expect(mockedApi.getInvestigationQueue).toHaveBeenCalledWith(1, null, { limit: 1, signal: expect.any(AbortSignal) });
    });

    it('requests only the selected tab’s rows', async () => {
      withWork();
      renderPage('/operations?tab=hosts');
      await screen.findByRole('link', { name: '10.9.0.1' });
      expect(listCalls()).toEqual({ findings: 0, hosts: 1, tests: 0, changed: 0, pickup: 0 });
      // Opening another tab fetches that tab's, and only then.
      openTab(/^Changed since review/);
      await screen.findByRole('link', { name: '10.8.0.2' });
      expect(listCalls()).toEqual({ findings: 0, hosts: 1, tests: 0, changed: 1, pickup: 0 });
      // The list that was left is gone from the page.
      expect(screen.queryByRole('link', { name: '10.9.0.1' })).not.toBeInTheDocument();
      expect(screen.getAllByRole('table')).toHaveLength(1);
    });

    it('with nothing anywhere it opens Pick up and says what an empty list means', async () => {
      renderPage();
      expect(await screen.findByText(/^Nothing here — every host has been touched by someone/)).toBeInTheDocument();
      expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual([
        'Findings0', 'Hosts0', 'Tests0', 'Changed since review0', 'Pick up0',
      ]);
      expect(selectedTab()).toBe('Pick up0');
      expect(screen.getByText('Nothing is waiting on a decision from you.')).toBeInTheDocument();
    });
  });

  describe('which tab opens', () => {
    it('with no ?tab=, the first NON-EMPTY tab in bar order', async () => {
      // No finding needs the reader; hosts are the first list with rows.
      withWork({
        my_findings: { items: [], total_open: 0 },
        my_work: { ...busy().my_work, ...NO_FINDINGS, total: 77 },
      });
      const router = renderRouted();
      await screen.findByRole('link', { name: '10.9.0.1' });
      expect(selectedTab()).toBe('Hosts37');
      expect(listCalls()).toEqual({ findings: 0, hosts: 1, tests: 0, changed: 0, pickup: 0 });
      // The default is not written into the address.
      expect(router.state.location.search).toBe('');
    });

    it('Tests opens for claimable tests alone — the tab lists them', async () => {
      withWork({
        my_findings: { items: [], total_open: 0 },
        my_queue: { items: [], in_review_count: 0 },
        my_work: {
          total: 0, hosts_in_review: 0, tests_assigned: 0, tests_on_hosts_in_review: 0,
          ...NO_FINDINGS, to_claim: 15,
        },
      });
      renderPage();
      await screen.findByRole('table', { name: 'Tests to do' });
      expect(selectedTab()).toBe('Tests0+ 15 to claim');
    });

    it('does not move when the tab it opened on empties', async () => {
      withWork();
      renderPage();
      await screen.findByRole('table', { name: 'Findings that need me' });
      expect(selectedTab()).toBe('Findings24');
      // The last finding was dealt with; the page is refreshed.
      mockedApi.getWorkbench.mockResolvedValue(busy({
        my_findings: { items: [], total_open: 0 },
        my_work: { ...busy().my_work, ...NO_FINDINGS, total: 77 },
      }));
      mockedApi.getMyFindingsPage.mockResolvedValue({ items: [], total_open: 0, need_counts: { decide: 0, write: 0 } });
      fireEvent.click(screen.getByRole('button', { name: 'Refresh Operations' }));
      await waitFor(() => expect(tab(/^Findings/)).toHaveTextContent('Findings0'));
      expect(selectedTab()).toBe('Findings0');
      expect(await screen.findByText(/^Nothing here — a finding you own shows when/)).toBeInTheDocument();
    });

    it('?tab= is read on arrival, written on a click, and Back returns', async () => {
      withWork();
      const router = renderRouted('/operations?tab=tests');
      await screen.findByRole('table', { name: 'Tests to do' });
      expect(selectedTab()).toBe('Tests40+ 15 to claim');

      openTab(/^Changed since review/);
      await screen.findByRole('table', { name: 'Changed since review' });
      expect(router.state.location.search).toBe('?tab=changed');

      await act(async () => { await router.navigate(-1); });
      await screen.findByRole('table', { name: 'Tests to do' });
      expect(router.state.location.search).toBe('?tab=tests');
      await act(async () => { await router.navigate(1); });
      await screen.findByRole('table', { name: 'Changed since review' });
    });

    it('an unknown ?tab= is no tab: the default opens', async () => {
      withWork();
      renderPage('/operations?tab=everything');
      await screen.findByRole('table', { name: 'Findings that need me' });
    });

    it('remembers nothing in the browser', async () => {
      withWork();
      const setItem = vi.spyOn(Storage.prototype, 'setItem');
      renderPage();
      await screen.findByRole('table');
      openTab(/^Hosts/);
      await screen.findByRole('table', { name: 'Hosts I am reviewing' });
      expect(setItem).not.toHaveBeenCalled();
      setItem.mockRestore();
    });
  });

  describe('lead', () => {
    const leadOf = async () => (await screen.findByTestId('lead-needs-me')).closest('p') as HTMLElement;

    it('says each kind of work apart, in the order to do it, with NO total — each number a link to the list it counts', async () => {
      withWork({ blockers: { failed_import_count: 2, partial_import_count: 0, imports: [] } });
      const router = renderRouted('/operations?tab=hosts&tier=2');
      const lead = await leadOf();
      await within(lead).findByRole('link', { name: /untouched hosts have a reason to look/ });
      // Three lines: what needs the reader, what they hold, what to pick up.
      expect(screen.getByTestId('lead-needs-me')).toHaveTextContent(
        /^3 findings need a decision and 21 need report text; 12 tests are assigned to you\.$/,
      );
      expect(screen.getByTestId('lead-holds')).toHaveTextContent(
        /^In review: 37 hosts, with 28 tests on them; 27 hosts you reviewed have changed since\.$/,
      );
      expect(screen.getByTestId('lead-pick-up')).toHaveTextContent(
        /^To pick up: 2 imports failed, 112 untouched hosts have a reason to look and 15 tests are free to claim\.$/,
      );
      expect(screen.getByTestId('lead-holds')).toHaveClass('block');
      expect(screen.getByTestId('lead-pick-up')).toHaveClass('block');
      expect(lead).toHaveTextContent(
        '3 findings need a decision and 21 need report text; 12 tests are assigned to you. '
        + 'In review: 37 hosts, with 28 tests on them; 27 hosts you reviewed have changed since. '
        + 'To pick up: 2 imports failed, 112 untouched hosts have a reason to look and 15 tests are free to claim.',
      );
      expect(lead).not.toHaveTextContent(/Across the team|You have|in your queue|\bitems?\b/);
      // The links open tabs of this page — with the filter that lists exactly
      // what was counted — keeping the page's other parameters.
      const href = (name: string | RegExp) => within(lead).getByRole('link', { name }).getAttribute('href');
      expect(href('3 findings need a decision')).toBe('/operations?tab=findings&tier=2&need=decide');
      expect(href('21 need report text')).toBe('/operations?tab=findings&tier=2&need=write');
      expect(href('12 tests are assigned to you')).toBe('/operations?tab=tests&tier=2&kind=assigned');
      expect(href('37 hosts')).toBe('/operations?tab=hosts&tier=2');
      expect(href('28 tests on them')).toBe('/operations?tab=tests&tier=2&kind=in_review');
      expect(href('27 hosts you reviewed have changed since')).toBe('/operations?tab=changed&tier=2');
      expect(href('112 untouched hosts have a reason to look')).toBe('/operations?tab=pickup&tier=2');
      // "Free to claim" opens the Tests tab narrowed to exactly those.
      expect(href('15 tests are free to claim')).toBe('/operations?tab=tests&tier=2&kind=triage');
      expect(href('2 imports failed')).toBe('/parse-errors?status=needs_attention');
      // NO number that is not a link: nothing is added up (it said "101 items").
      const plain = lead.cloneNode(true) as HTMLElement;
      plain.querySelectorAll('a').forEach((a) => a.remove());
      expect(plain.textContent?.match(/\d+/g)).toBeNull();
      // What "a decision" counts is on the lead's (i), not a caption line.
      expect(screen.queryByText(/^A decision is a finding under investigation/)).toBeNull();
      expect(within(lead).getByRole('button', { name: 'What counts as a decision' })).toBeInTheDocument();

      // A link switches the tab, with its filter.
      fireEvent.click(within(lead).getByRole('link', { name: '15 tests are free to claim' }));
      await screen.findByRole('table', { name: 'Tests to do' });
      expect(router.state.location.search).toBe('?tab=tests&tier=2&kind=triage');
      expect(screen.getByRole('button', { name: /^Free to claim/ })).toHaveAttribute('aria-pressed', 'true');
      await waitFor(() => expect(mockedApi.getMyTestsPage).toHaveBeenLastCalledWith(
        1, 'triage', expect.objectContaining({ offset: 0, limit: 10 }),
      ));
      // …and from Tests, "need report text" opens Findings narrowed to those,
      // dropping the Tests tab's kind.
      fireEvent.click(within(await leadOf()).getByRole('link', { name: '21 need report text' }));
      await screen.findByRole('table', { name: 'Findings that need me' });
      expect(router.state.location.search).toBe('?tab=findings&tier=2&need=write');
      expect(screen.getByRole('button', { name: /^Needs report text/ })).toHaveAttribute('aria-pressed', 'true');
      await waitFor(() => expect(mockedApi.getMyFindingsPage).toHaveBeenLastCalledWith(
        1, 'write', expect.objectContaining({ offset: 0, limit: 10 }),
      ));
    });

    it('the owner’s page: 3 to decide, 21 to write, 1 assigned; 37 hosts with 39 tests; 27 changed; 112 and 1 to pick up', async () => {
      withWork({
        my_work: { ...busy().my_work, tests_assigned: 1, tests_on_hosts_in_review: 39, to_claim: 1 },
      });
      renderPage();
      expect(await leadOf()).toHaveTextContent(
        '3 findings need a decision and 21 need report text; 1 test is assigned to you. '
        + 'In review: 37 hosts, with 39 tests on them; 27 hosts you reviewed have changed since. '
        + 'To pick up: 112 untouched hosts have a reason to look and 1 test is free to claim.',
      );
    });

    it('one of each is singular', async () => {
      withWork({
        my_work: {
          ...busy().my_work, hosts_in_review: 1, tests_assigned: 1, tests_on_hosts_in_review: 1,
          findings_needing_me: 2, findings_to_decide: 1, findings_to_write: 1, to_claim: 1,
        },
        followups: { items: [], total: 1 },
      });
      mockedApi.getInvestigationQueue.mockResolvedValue({ ...busyQueue, queue_total: 1 });
      renderPage();
      const lead = await leadOf();
      await within(lead).findByRole('link', { name: /a reason to look/ });
      expect(lead).toHaveTextContent(
        '1 finding needs a decision and 1 needs report text; 1 test is assigned to you. '
        + 'In review: 1 host, with 1 test on it; 1 host you reviewed has changed since. '
        + 'To pick up: 1 untouched host has a reason to look and 1 test is free to claim.',
      );
    });

    it('a clause whose number is 0 is left out, and the first one names what it counts', async () => {
      // Nothing to decide: "findings" is said by the report-text clause.
      withWork({
        my_work: {
          ...busy().my_work, findings_needing_me: 21, findings_to_decide: 0,
          tests_assigned: 0, tests_on_hosts_in_review: 0,
        },
        followups: { items: [], total: 0 },
      });
      renderPage();
      const lead = await leadOf();
      expect(screen.getByTestId('lead-needs-me')).toHaveTextContent(/^21 findings need report text\.$/);
      expect(within(lead).getByRole('link', { name: '21 findings need report text' }))
        .toHaveAttribute('href', '/operations?tab=findings&need=write');
      expect(screen.getByTestId('lead-holds')).toHaveTextContent(/^In review: 37 hosts\.$/);
    });

    it('only tests assigned: no finding clause, no separator', async () => {
      withWork({
        my_work: { ...busy().my_work, ...NO_FINDINGS, hosts_in_review: 0, tests_on_hosts_in_review: 0 },
      });
      renderPage();
      await leadOf();
      expect(screen.getByTestId('lead-needs-me')).toHaveTextContent(/^12 tests are assigned to you\.$/);
      // Nothing held in review but changed reviews: the line is those alone.
      expect(screen.getByTestId('lead-holds')).toHaveTextContent(/^27 hosts you reviewed have changed since\.$/);
    });

    it('a server that does not say the kinds of finding apart never has them said as zero', async () => {
      const { findings_to_decide: _d, findings_to_write: _w, ...older } = busy().my_work;
      withWork({ my_work: older });
      renderPage();
      const lead = await leadOf();
      expect(screen.getByTestId('lead-needs-me')).toHaveTextContent(/^24 findings need you; 12 tests are assigned to you\.$/);
      expect(within(lead).getByRole('link', { name: '24 findings need you' }))
        .toHaveAttribute('href', '/operations?tab=findings');
    });

    it('says nothing is waiting when nothing is, and never counts an unavailable queue', async () => {
      mockedApi.getInvestigationQueue.mockRejectedValue(new Error('503'));
      renderPage();
      const lead = await leadOf();
      // Everything zero: one short sentence, and no other line.
      expect(lead).toHaveTextContent(/^Nothing is waiting on a decision from you\.$/);
      expect(screen.queryByTestId('lead-holds')).not.toBeInTheDocument();
      expect(screen.queryByTestId('lead-pick-up')).not.toBeInTheDocument();
      expect(within(lead).queryByRole('link')).not.toBeInTheDocument();
      // The tab says the count could not be checked — never 0 — and so does its list.
      await waitFor(() => expect(tab(/^Pick up/)).toHaveTextContent('Pick up—'));
      expect(tab(/^Pick up/)).toHaveAccessibleName('Pick up: could not be checked');
      expect(await screen.findByRole('alert')).toHaveTextContent(/Could not be checked/);
      expect(screen.queryByText(/Nothing here/)).not.toBeInTheDocument();
      // And the lead does not turn the failure into a count.
      expect(screen.queryByText(/ha(s|ve) a reason to look/)).not.toBeInTheDocument();
      expect(screen.queryByText(/To pick up/)).not.toBeInTheDocument();
    });

    it('with an empty queue it still says a host the reader reviewed changed', async () => {
      mockedApi.getWorkbench.mockResolvedValue({ ...emptyWorkbench, followups: { items: [], total: 1 } });
      renderPage();
      const lead = await leadOf();
      expect(lead).toHaveTextContent(/^Nothing is waiting on a decision from you\. 1 host you reviewed has changed since\.$/);
      expect(within(lead).getByRole('link', { name: '1 host you reviewed has changed since' }))
        .toHaveAttribute('href', '/operations?tab=changed');
    });
  });

  describe('the Findings tab’s filter (?need=)', () => {
    const chip = (name: RegExp) => screen.getByRole('button', { name });
    const chipTexts = () => within(screen.getByRole('group', { name: 'Filter by what the finding needs' }))
      .getAllByRole('button').map((c) => c.textContent);

    it('the chips show the server’s counts; selecting one asks for that list and writes ?need=', async () => {
      withWork();
      const router = renderRouted('/operations?tab=findings');
      await screen.findByRole('table', { name: 'Findings that need me' });
      expect(chipTexts()).toEqual(['All 24', 'Needs a decision3', 'Needs report text21']);
      expect(mockedApi.getMyFindingsPage).toHaveBeenLastCalledWith(1, null, expect.objectContaining({ offset: 0, limit: 10 }));
      expect(screen.getByText('1–2 of 24')).toBeInTheDocument();

      // Page two of the whole list, then the filter: back to the first page.
      fireEvent.click(screen.getByRole('button', { name: 'Next 10 findings' }));
      await waitFor(() => expect(mockedApi.getMyFindingsPage).toHaveBeenLastCalledWith(
        1, null, expect.objectContaining({ offset: 10, limit: 10 }),
      ));
      mockedApi.getMyFindingsPage.mockResolvedValue({ items: [findingRow(5)], total_open: 24, need_counts: NEEDS });
      fireEvent.click(chip(/^Needs report text/));
      await waitFor(() => expect(mockedApi.getMyFindingsPage).toHaveBeenLastCalledWith(
        1, 'write', expect.objectContaining({ offset: 0, limit: 10 }),
      ));
      expect(router.state.location.search).toBe('?tab=findings&need=write');
      expect(await screen.findByRole('link', { name: 'Weak TLS 5' })).toBeInTheDocument();
      // Rows of the previous filter are gone, and the footer is the filtered list's size.
      expect(screen.queryByRole('link', { name: 'Weak TLS 21' })).not.toBeInTheDocument();
      expect(screen.getByText('1–1 of 21')).toBeInTheDocument();
      expect(chip(/^Needs report text/)).toHaveAttribute('aria-pressed', 'true');
      // The chips and the tab keep the whole list's counts.
      expect(chipTexts()).toEqual(['All 24', 'Needs a decision3', 'Needs report text21']);
      expect(tab(/^Findings/)).toHaveTextContent('Findings24');

      // "All" clears it.
      fireEvent.click(chip(/^All/));
      await waitFor(() => expect(mockedApi.getMyFindingsPage).toHaveBeenLastCalledWith(
        1, null, expect.objectContaining({ offset: 0, limit: 10 }),
      ));
      expect(router.state.location.search).toBe('?tab=findings');
    });

    it('a reload with ?tab=findings&need=write opens that filter', async () => {
      withWork();
      renderPage('/operations?tab=findings&need=write');
      await screen.findByRole('table', { name: 'Findings that need me' });
      expect(chip(/^Needs report text/)).toHaveAttribute('aria-pressed', 'true');
      expect(mockedApi.getMyFindingsPage).toHaveBeenCalledTimes(1);
      expect(mockedApi.getMyFindingsPage).toHaveBeenCalledWith(1, 'write', expect.objectContaining({ offset: 0, limit: 10 }));
      expect(screen.getByText('1–2 of 21')).toBeInTheDocument();
    });

    it('an unknown ?need= is no filter, and leaving the tab drops it', async () => {
      withWork();
      const router = renderRouted('/operations?tab=findings&need=everything');
      await screen.findByRole('table', { name: 'Findings that need me' });
      expect(mockedApi.getMyFindingsPage).toHaveBeenLastCalledWith(1, null, expect.anything());
      expect(chip(/^All/)).toHaveAttribute('aria-pressed', 'true');
      openTab(/^Hosts/);
      await screen.findByRole('table', { name: 'Hosts I am reviewing' });
      expect(router.state.location.search).toBe('?tab=hosts');
    });

    it('an empty filter says which kind is empty', async () => {
      withWork();
      mockedApi.getMyFindingsPage.mockResolvedValue({ items: [], total_open: 21, need_counts: { decide: 0, write: 21 } });
      renderPage('/operations?tab=findings&need=decide');
      expect(await screen.findByText(/^Nothing here — no finding you own is under investigation/)).toBeInTheDocument();
    });

    it('counts that are not known are a dash on the chips — never 0', async () => {
      withWork();
      mockedApi.getWorkbench.mockRejectedValue(new Error('offline'));
      mockedApi.getMyFindingsPage.mockRejectedValue(new Error('HTTP 500'));
      renderPage('/operations?tab=findings');
      await screen.findByText('Couldn\'t load your work\'s counts');
      await waitFor(() => expect(chipTexts()).toEqual(['All —', 'Needs a decision—', 'Needs report text—']));
      expect(tab(/^Findings/)).toHaveTextContent('Findings—');
    });
  });

  describe('counts that could not be loaded', () => {
    it('a failed check of the reviewed hosts is "—" on its tab, and not a count in the lead', async () => {
      withWork({ followups: { items: [], total: 0 }, followups_unavailable: true });
      renderPage();
      await screen.findByRole('table');
      expect(tab(/^Changed since review/)).toHaveTextContent('Changed since review—');
      expect(tab(/^Changed since review/)).toHaveAccessibleName('Changed since review: could not be checked');
      expect(screen.queryByText(/changed since/)).not.toBeInTheDocument();
    });

    it('a failed workbench is "—" on every tab it counts, said once, and the lists still answer', async () => {
      withWork();
      mockedApi.getWorkbench.mockRejectedValue(new Error('offline'));
      renderPage();
      expect(await screen.findByText('Couldn\'t load your work\'s counts')).toBeInTheDocument();
      await waitFor(() => expect(tab(/^Pick up/)).toHaveTextContent('Pick up112'));
      expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual([
        'Findings—', 'Hosts—', 'Tests—', 'Changed since review—', 'Pick up112',
      ]);
      // No count to choose by: the first tab opens, and its list loads for itself.
      expect(await screen.findByRole('table', { name: 'Findings that need me' })).toBeInTheDocument();
      expect(screen.queryByTestId('lead-needs-me')).not.toBeInTheDocument();
      expect(screen.queryByText(/Nothing is waiting/)).not.toBeInTheDocument();
    });

    it('while the counts load the bar is there, with no number invented', async () => {
      let release: (v: unknown) => void = () => undefined;
      mockedApi.getWorkbench.mockReturnValue(new Promise((resolve) => { release = resolve; }));
      renderPage();
      await screen.findByRole('tablist');
      expect(tab(/^Findings/)).toHaveTextContent('Findings…');
      expect(tab(/^Findings/)).toHaveAccessibleName('Findings: loading');
      // No list is chosen — or fetched — before the counts say which.
      expect(screen.queryByRole('tabpanel')).not.toBeInTheDocument();
      expect(listCalls()).toEqual({ findings: 0, hosts: 0, tests: 0, changed: 0, pickup: 0 });
      await act(async () => { release(busy()); });
      await waitFor(() => expect(tab(/^Findings/)).toHaveTextContent('Findings24'));
    });
  });

  describe('the list on screen', () => {
    it('a tab pages through its whole list: next asks the server for the next 10', async () => {
      withWork();
      renderPage('/operations?tab=tests');
      await screen.findByRole('table', { name: 'Tests to do' });
      expect(mockedApi.getMyTestsPage).toHaveBeenLastCalledWith(1, null, expect.objectContaining({ offset: 0, limit: 10 }));
      expect(screen.getByText('1–2 of 55')).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'Next 10 tests' }));
      await waitFor(() => expect(mockedApi.getMyTestsPage).toHaveBeenLastCalledWith(
        1, null, expect.objectContaining({ offset: 10, limit: 10 }),
      ));
      expect(await screen.findByText('11–12 of 55')).toBeInTheDocument();
    });

    it('the Pick up tier lives in the URL: read on arrival, written on a click, and the list follows', async () => {
      withWork();
      const router = renderRouted('/operations?tab=pickup&tier=2');
      await waitFor(() => expect(mockedApi.getInvestigationQueue).toHaveBeenCalledWith(
        1, 2, expect.objectContaining({ offset: 0, limit: 10 }),
      ));
      const chip = await screen.findByRole('button', { name: /Critical vulnerability/ });
      expect(chip).toHaveAttribute('aria-pressed', 'true');
      // The tier's 30 hosts are the list being paged — not the queue's 112.
      expect(await screen.findByText('1–1 of 30')).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: /Exploit available/ }));
      await waitFor(() => expect(mockedApi.getInvestigationQueue).toHaveBeenLastCalledWith(
        1, 3, expect.objectContaining({ offset: 0, limit: 10 }),
      ));
      expect(router.state.location.search).toBe('?tab=pickup&tier=3');
      await waitFor(() => expect(screen.getByRole('button', { name: /Exploit available/ })).toHaveAttribute('aria-pressed', 'true'));
      // The tab's count stays the whole queue's.
      expect(tab(/^Pick up/)).toHaveTextContent('Pick up112');
    });

    it('the Pick up footer never offers the 251 untouched hosts as this list of 112', async () => {
      withWork();
      renderPage('/operations?tab=pickup');
      const all = await screen.findByRole('link', { name: 'All 251 untouched hosts in Hosts, with or without a reason' });
      expect(q(all)).toBe('has:untouched');
      expect(screen.queryByRole('link', { name: /^Open all/ })).not.toBeInTheDocument();
    });

    it('the changed reviews and the hosts in review each open exactly their list in Hosts', async () => {
      withWork();
      renderPage('/operations?tab=changed');
      expect(q(await screen.findByRole('link', { name: 'Open all 27 hosts in Hosts' }))).toBe('follow:revisit');
      openTab(/^Hosts/);
      expect(q(await screen.findByRole('link', { name: 'Open all 37 in Hosts' }))).toBe('follow:mine');
      // Findings and Tests have no page that lists exactly them: no link.
      openTab(/^Findings/);
      await screen.findByRole('table', { name: 'Findings that need me' });
      expect(screen.queryByRole('link', { name: /Open all|All findings/ })).not.toBeInTheDocument();
    });

    it('an action refreshes the counts and its own list quietly, not the whole page', async () => {
      withWork();
      mockedApi.markStillReviewed.mockResolvedValue({ host_ids: [21] });
      renderPage('/operations?tab=changed');
      fireEvent.click(await screen.findByRole('button', { name: 'Still reviewed' }));
      await waitFor(() => expect(mockedApi.markStillReviewed).toHaveBeenCalledWith(1, [21]));
      await waitFor(() => expect(mockedApi.getWorkbench).toHaveBeenCalledTimes(2));
      await waitFor(() => expect(mockedApi.getReviewFollowupsPage).toHaveBeenCalledTimes(2));
      // The queue's count too: taking a host into review changes it.
      await waitFor(() => expect(mockedApi.getInvestigationQueue).toHaveBeenCalledTimes(2));
      // Coverage — the structural fetch — was not repeated, and no other tab's list was read.
      expect(mockedApi.getProjectCoverage).toHaveBeenCalledTimes(1);
      expect(listCalls()).toMatchObject({ findings: 0, hosts: 0, tests: 0, pickup: 0 });
      // The rows stayed on screen while it re-read.
      expect(screen.getByRole('link', { name: '10.8.0.2' })).toBeInTheDocument();
    });

    it('a list that fails says "could not be checked" — never an empty list — and Retry reads it again', async () => {
      withWork();
      mockedApi.getMyReviewHostsPage.mockRejectedValueOnce(new Error('HTTP 500'));
      renderPage('/operations?tab=hosts');
      expect(await screen.findByRole('alert')).toHaveTextContent(/Could not be checked.*not an empty list/);
      expect(screen.queryByText(/Nothing here/)).not.toBeInTheDocument();
      // The tab bar stays, with its counts.
      expect(tab(/^Hosts/)).toHaveTextContent('Hosts37');
      fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
      expect(await screen.findByRole('link', { name: '10.9.0.1' })).toBeInTheDocument();
    });

    it('the keyboard moves through the one list on screen', async () => {
      withWork();
      renderPage('/operations?tab=changed');
      const row = (await screen.findByRole('link', { name: '10.8.0.2' })).closest('tr') as HTMLElement;
      fireEvent.keyDown(window, { key: 'j' });
      expect(row).toHaveAttribute('data-list-cursor', 'true');
      expect(document.querySelectorAll('[data-list-cursor]')).toHaveLength(1);
    });

    it('a reader (viewer) gets every tab without a write control', async () => {
      projectRole.value = 'viewer';
      accountRole.value = 'member';
      withWork();
      renderPage('/operations?tab=changed');
      const writes = () => {
        expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
        for (const name of ['Still reviewed', 'Re-open review', 'Review', 'Claim']) {
          expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
        }
        expect(screen.queryByRole('columnheader', { name: 'Action' })).not.toBeInTheDocument();
      };
      await screen.findByRole('link', { name: '10.8.0.2' });
      writes();
      openTab(/^Pick up/);
      await screen.findByRole('link', { name: '10.0.0.7' });
      writes();
      openTab(/^Tests/);
      await screen.findByRole('table', { name: 'Tests to do' });
      // The claimable test is listed; the Claim is not offered.
      expect(screen.getByText('free to claim')).toBeInTheDocument();
      writes();
    });
  });

  describe('agent sessions: one line', () => {
    it('is about the reader’s own sessions: asks the server for those only, and links to the list', async () => {
      // What the server returns for `user_id=7`: the reader's two sessions.
      // A teammate's live session is not in the answer, so it is not counted.
      mockedApi.listAgentSessions.mockImplementation(async (_projectId: number, filters: Record<string, unknown>) => (
        filters?.user_id === 7
          ? {
              project_id: 1, total: 2,
              sessions: [
                session(),
                // The key ran out, the session did not: resumable — never "active".
                session({ id: 100, key_expires_at: new Date(Date.now() - 3_600_000).toISOString() }),
              ],
            }
          : {
              project_id: 1, total: 3,
              sessions: [session(), session({ id: 100 }), session({ id: 101, user_id: 9, user_username: 'sam' })],
            }
      ));
      renderPage();
      const line = await screen.findByRole('link', {
        name: '1 session of yours live now; 1 more waiting to be resumed (the key ran out, the session did not).',
      });
      expect(line).toHaveAttribute('href', '/agent-activity');
      expect(screen.getByText('Your agent sessions')).toBeInTheDocument();
      // Never the project-wide request (Agent Sessions' own): every call
      // names the reader.
      expect(mockedApi.listAgentSessions.mock.calls.every((c) => c[0] === 1)).toBe(true);
      const asked = mockedApi.listAgentSessions.mock.calls.map((c) => c[1]);
      expect(asked.length).toBeGreaterThan(0);
      for (const filters of asked) expect(filters).toEqual({ kind: 'project', status: 'active', user_id: 7 });
      expect(screen.queryByText(/\(active\)|^active$|^ended$/)).not.toBeInTheDocument();
    });

    it('with none of the reader’s own live it says so — whatever teammates run', async () => {
      mockedApi.listAgentSessions.mockResolvedValue({ project_id: 1, sessions: [], total: 0 });
      renderPage();
      expect(await screen.findByRole('link', { name: 'You have no agent session live on this project.' }))
        .toHaveAttribute('href', '/agent-activity');
    });

    it('a failed read says so, never "no session is live"', async () => {
      mockedApi.listAgentSessions.mockRejectedValue(new Error('offline'));
      renderPage();
      expect(await screen.findByText(/could not be checked — this is not a confirmation that none is live/)).toBeInTheDocument();
      expect(screen.queryByText(/no agent session live/i)).not.toBeInTheDocument();
    });
  });

  // 5.204.3 — the setup card's scan button used to navigate('/scopes') (a
  // dead end). 5.313.0 — no per-scope key: it hands the operator's one agent
  // session a task naming the registered scope, in place. 5.313.1 — the task
  // is a scan uploaded to the session, not a recon run.
  describe('a project with nothing in it yet', () => {
    const noHosts = {
      ...baseCoverage,
      total_hosts: 0, hosts_with_plan_entry: 0, hosts_with_execution_result: 0,
      hosts_no_plan: 0, hosts_no_execution: 0, hosts_outside_scope: 0,
      hosts_in_subnet_scope: 0, hosts_name_scope_only: 0,
    };

    it('setup card: Scan with your agent hands the task for the single scope to the agent session', async () => {
      mockedApi.getProjectCoverage.mockResolvedValue(noHosts);
      renderPage();
      fireEvent.click(await screen.findByRole('button', { name: /Scan with your agent/ }));
      expect(await screen.findByRole('dialog', { name: /Start Agent Session/ })).toBeInTheDocument();
      expect(
        screen.getByText(
          `Read this project’s scope in BlueStick (scope id ${baseCoverage.scopes[0].scope_id}), run your scanners on what is in scope, and upload the output to this session.`,
        ),
      ).toBeInTheDocument();
      expect(navigateSpy).not.toHaveBeenCalled();
    });

    it('setup card: with several scopes, the task names none and lets the agent choose', async () => {
      mockedApi.getProjectCoverage.mockResolvedValue({
        ...noHosts,
        total_scopes: 2,
        scopes: [
          ...baseCoverage.scopes,
          { ...baseCoverage.scopes[0], scope_id: 11, scope_name: 'DMZ' },
        ],
      });
      renderPage();
      fireEvent.click(await screen.findByRole('button', { name: /Scan with your agent/ }));
      expect(
        await screen.findByText(
          'Read this project’s scopes in BlueStick, run your scanners on what is in scope, and upload the output to this session.',
        ),
      ).toBeInTheDocument();
      expect(navigateSpy).not.toHaveBeenCalled();
    });

    it('shows the setup block alone: no tab bar, no list', async () => {
      mockedApi.getProjectCoverage.mockResolvedValue(noHosts);
      renderPage();
      await screen.findByText(/Scope is registered — time to discover hosts/);
      expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
      expect(screen.queryByRole('table')).not.toBeInTheDocument();
      expect(screen.queryByText('Your agent sessions')).not.toBeInTheDocument();
      expect(listCalls()).toEqual({ findings: 0, hosts: 0, tests: 0, changed: 0, pickup: 0 });
    });

    // 5.332.2 — every project has its one scope row from creation; with no
    // subnet in it, nothing is "registered" yet.
    it('a project whose scope has no entries gets the welcome block, not "Scope is registered"', async () => {
      mockedApi.getProjectCoverage.mockResolvedValue({
        ...noHosts, scopes: [{ ...noHosts.scopes[0], subnet_count: 0, total_scoped_ips: 0 }],
      });
      renderPage();
      expect(await screen.findByText(/Welcome — let's set up this project/)).toBeInTheDocument();
      expect(screen.queryByText(/Scope is registered/)).not.toBeInTheDocument();
    });

    it('a brand-new project gets the welcome block and no refresh chrome', async () => {
      mockedApi.getProjectCoverage.mockResolvedValue({ ...noHosts, total_scopes: 0, scopes: [] });
      renderPage();
      expect(await screen.findByText(/Welcome — let's set up this project/)).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Refresh Operations' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /Start Agent Session/ })).not.toBeInTheDocument();
    });
  });

  describe('since your last visit', () => {
    const since = {
      last_viewed_at: '2026-09-01T00:00:00Z',
      is_first_visit: false,
      new_scan_count: 2,
      latest_scan_id: 9,
      latest_scan_filename: 'sweep.xml',
      latest_scan_created_at: '2026-09-18T00:00:00Z',
      new_host_count: 3,
      new_critical_findings: 0,
      new_high_findings: 0,
      as_of: '2026-09-19T08:00:00+00:00',
    };
    const workbench = (s: Record<string, unknown>) => ({ ...emptyWorkbench, since_last_visit: s });
    beforeEach(() => {
      mockedApi.getWorkbench.mockResolvedValue(workbench(since));
    });

    it('acknowledges the snapshot that was displayed, not the time of the click', async () => {
      renderPage();
      fireEvent.click(await screen.findByRole('button', { name: /Acknowledge updates/ }));
      await waitFor(() => expect(mockedApi.markWorkbenchSeen).toHaveBeenCalledWith(1, since.as_of));
      await waitFor(() => expect(screen.queryByText('Since your last visit')).not.toBeInTheDocument());
      // "Mark reviewed" claimed a review nobody did.
      expect(screen.queryByRole('button', { name: /Mark reviewed/ })).not.toBeInTheDocument();
    });

    it('keeps the banner and says so when the acknowledgement cannot be saved', async () => {
      mockedApi.markWorkbenchSeen.mockRejectedValue(new Error('offline'));
      renderPage();
      fireEvent.click(await screen.findByRole('button', { name: /Acknowledge updates/ }));
      expect(await screen.findByText(/Could not save the acknowledgement|offline/)).toBeInTheDocument();
      expect(screen.getByText('Since your last visit')).toBeInTheDocument();
    });

    // v5.242.0 — a change inbox: the counts were passive badges.
    it('each count is a link to the hosts it counted', async () => {
      renderPage();
      const link = await screen.findByRole('link', { name: /3 new hosts/ });
      expect(q(link)).toBe(`firstseen:"${since.last_viewed_at}..${since.as_of}"`);
      expect(screen.getByRole('link', { name: /2 new imports/ }))
        .toHaveAttribute('href', `/scans?since=${encodeURIComponent(since.last_viewed_at)}`);
    });

    // Blockers — stopped work, each with the action that unblocks it.
    describe('blocked work', () => {
      const blockers = {
        failed_import_count: 1, partial_import_count: 1,
        imports: [
          { job_id: 4, filename: 'broken.xml', kind: 'failed', message: 'not well-formed' },
          { job_id: 5, filename: 'half.nessus', kind: 'partial', message: '3 hosts skipped' },
        ],
      };

      it('names what is blocked and carries the recovery action, above the tab bar', async () => {
        mockedApi.getWorkbench.mockResolvedValue({ ...workbench(since), blockers });
        renderPage();
        const heading = await screen.findByRole('heading', { name: 'Blocked' });
        // eslint-disable-next-line no-bitwise
        expect(heading.compareDocumentPosition(screen.getByRole('tablist')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
        expect(screen.getByText('1 import failed · 1 finished partial')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('button', { name: 'Inspect import errors' }));
        expect(navigateSpy).toHaveBeenCalledWith('/parse-errors?status=needs_attention');
        // 5.320.0 — imports are the only blocked work: no run rows.
        expect(screen.queryByText(/Run #|interrupted run/)).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Open run' })).not.toBeInTheDocument();
      });

      it('renders nothing when nothing is blocked', async () => {
        mockedApi.getWorkbench.mockResolvedValue({
          ...workbench(since),
          blockers: { failed_import_count: 0, partial_import_count: 0, imports: [] },
        });
        renderPage();
        await screen.findByText('Since your last visit');
        expect(screen.queryByRole('heading', { name: 'Blocked' })).not.toBeInTheDocument();
      });

      it('a failed check says so — never "nothing blocked"', async () => {
        mockedApi.getWorkbench.mockResolvedValue({ ...workbench(since), blockers_unavailable: true });
        renderPage();
        expect(await screen.findByText(/Blocked work \(failed imports\) could not be checked/)).toBeInTheDocument();
        expect(screen.queryByRole('heading', { name: 'Blocked' })).not.toBeInTheDocument();
      });
    });
  });

  it('page Refresh also refetches what fetches for itself: the list on screen and the agent-sessions line', async () => {
    withWork();
    renderPage('/operations?tab=hosts');
    // On mount: the Start-Agent-Session badge's hook and the line ask the
    // same question (the same filters since 5.330.0), so it is asked ONCE —
    // the line, which appears after the page's counts, uses the answer the
    // hook just got (defect 1.20: it was asked twice).  Refresh asks once more.
    const sessionReads = () => mockedApi.listAgentSessions.mock.calls.length;
    await screen.findByRole('link', { name: '10.9.0.1' });
    await screen.findByText('Your agent sessions');
    const refresh = await screen.findByRole('button', { name: 'Refresh Operations' });
    await waitFor(() => expect(refresh).not.toBeDisabled());
    expect(sessionReads()).toBe(1);
    fireEvent.click(refresh);
    await waitFor(() => expect(sessionReads()).toBe(2));
    await waitFor(() => expect(mockedApi.getWorkbench).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(mockedApi.getMyReviewHostsPage).toHaveBeenCalledTimes(2));
    // Still only the selected tab's rows.
    expect(listCalls()).toEqual({ findings: 0, hosts: 2, tests: 0, changed: 0, pickup: 0 });
  });
});
