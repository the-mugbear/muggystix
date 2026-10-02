/**
 * The Operations page (redesigned 5.329.0 — design review 2026-10-02; the
 * reader's own page since 5.330.0).
 *
 * Pins the page's shape: a lead whose numbers are links, My work at full
 * width, the reader's own changed reviews, the untouched queue and one line
 * for the reader's agent sessions — and NO project status: no measures strip,
 * no terrain, no exposure block (Posture has them).  The sections' own
 * behaviour is in their component tests (MyWorkCard, ChangedSinceReview-
 * Section, UntouchedQueueSection).
 *
 * 5.330.0 — what the removed tests here guarded, and where it is now:
 *  - "four measures, each opening exactly what it counts": tested / untouched
 *    critical are the terrain's sentence (`AddressTerrainSection.test.tsx`;
 *    count == list in `backend/tests/test_operations_redesign.py` and
 *    `test_address_terrain.py`); changed-since-review and my-queue are the
 *    lead and the section headings (below, `ChangedSinceReviewSection.test`,
 *    `MyWorkCard.test`);
 *  - "a measure that could not be counted says so — never a zero": the
 *    terrain's and the exposure section's failure states
 *    (`AddressTerrainSection.test`, `PostureExposureSection.test`), and the
 *    followups-unavailable test below;
 *  - the Exposure describe (scope states, severity bar, failed count):
 *    `tests/components/PostureExposureSection.test.tsx`;
 *  - the terrain's position: `tests/pages/SecurityPostureOverview.test.tsx`.
 *
 * What the previous version of this file guarded, and where it is now:
 *  - the `?start=agent-session` deep link, the setup blocks, the since-last-
 *    visit banner, the blocked strip, the error alert, "no card anywhere",
 *    the queue on its own request — kept below;
 *  - the Runs section (its fetch, the Mine toggle and its localStorage key,
 *    the Active chip) — the section is gone: the agent-sessions line below,
 *    and `tests/utils/agentRuns.test.ts` for the sentence both pages print;
 *  - Project state's coverage tiles ("With tests to do", "Tested", "Outside
 *    scope") — "Tested" is a measure now; tests-to-do and the assessment are
 *    Posture's; the three scope states stay, in the Exposure section below;
 *  - "page Refresh refetches the self-fetching panels" — kept, for the panels
 *    that still fetch for themselves (the agent-sessions line).
 */
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
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

const emptyWorkbench = {
  my_queue: { items: [], in_review_count: 0, watching_count: 0 },
  my_tasks: {
    items: [], total_open: 0,
    reason_counts: { assigned: 0, in_review: 0, triage: 0 },
    group_counts: { assigned: 0, in_review: 0, triage: 0 },
  },
  my_findings: { items: [], total_open: 0 },
  my_work: {
    total: 0, hosts_in_review: 0, tests_assigned: 0, tests_on_hosts_in_review: 0,
    findings_needing_me: 0, to_claim: 0,
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
const emptyQueue = { items: [], queue_total: 0, untouched_total: 0, tiers: [], tier_counts: [] };

vi.mock('../../services/api', () => ({
  getProjectCoverage: vi.fn(),
  listAgentSessions: vi.fn(),
  // Posture's reads: mocked only so the page can be shown NOT to make them.
  getDashboardStats: vi.fn(),
  getAddressTerrain: vi.fn(),
  getWorkbench: vi.fn(),
  getInvestigationQueue: vi.fn(),
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

beforeEach(() => {
  vi.clearAllMocks();
  navigateSpy.mockReset();
  projectRole.value = undefined;
  accountRole.value = 'admin';
  mockedApi.getProjectCoverage.mockResolvedValue(baseCoverage);
  mockedApi.getWorkbench.mockResolvedValue(emptyWorkbench);
  mockedApi.getInvestigationQueue.mockResolvedValue(emptyQueue);
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
    await screen.findByRole('heading', { name: /My work/ });
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

  describe('shape (5.330.0): the reader’s own page', () => {
    it('the sections, in order, and nothing of what was removed', async () => {
      renderPage();
      await screen.findByRole('heading', { name: /Untouched, with a reason/ });
      await screen.findByText(/session of yours live now/);
      const headings = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent);
      expect(headings).toEqual([
        'My work',
        'Changed since review',
        'Untouched, with a reason',
      ]);
      expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
      // The agent line closes the page.
      const before = (x: Element, y: Element) =>
        // eslint-disable-next-line no-bitwise
        !!(x.compareDocumentPosition(y) & Node.DOCUMENT_POSITION_FOLLOWING);
      expect(before(screen.getByRole('heading', { name: /Untouched, with a reason/ }), screen.getByText(/session of yours live now/))).toBe(true);

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
      expect(screen.queryByRole('group', { name: /Runs status filter|Scope of runs view/ })).not.toBeInTheDocument();
      expect(screen.queryByText(/My recent activity/)).not.toBeInTheDocument();
      expect(screen.queryByText('Project state')).not.toBeInTheDocument();
      expect(screen.queryByText('With tests to do')).not.toBeInTheDocument();
      // Nothing waits on an approval, and nothing speaks of plans.
      expect(screen.queryByText(/approv|plan entr|in any plan/i)).toBeNull();
    });

    it('renders no card anywhere on the page', async () => {
      renderPage();
      await screen.findByRole('heading', { name: /Untouched, with a reason/ });
      expect(document.querySelector('.rounded-panel.border.bg-card')).toBeNull();
    });

    it('asks for the workbench without the queue, and loads the queue on its own request', async () => {
      renderPage();
      await waitFor(() => expect(mockedApi.getInvestigationQueue).toHaveBeenCalled());
      expect(mockedApi.getWorkbench).toHaveBeenCalledWith({ includeInvestigate: false });
      expect(mockedApi.getInvestigationQueue).toHaveBeenCalledWith(null, { limit: 15 });
    });

    it('renders sensibly with hosts but no reviews, tests or findings', async () => {
      renderPage();
      // The queues say what an empty one means.
      expect(await screen.findByText(/Nothing to re-check: no host you reviewed has changed/)).toBeInTheDocument();
      expect(await screen.findByText('Every host has been touched by someone.')).toBeInTheDocument();
      expect(screen.getByText(/Nothing is waiting on you\. Work shows here/)).toBeInTheDocument();
    });
  });

  describe('lead', () => {
    const wb = (extra: Record<string, unknown>) => ({
      ...emptyWorkbench,
      my_queue: { items: [], in_review_count: 2, watching_count: 0 },
      // An older server may still send assigned notes; they are not work (5.325.0).
      my_notes: { items: [], total_open: 1, overdue_count: 1 },
      my_findings: { items: [], total_open: 3 },
      my_work: {
        total: 5, hosts_in_review: 2, tests_assigned: 0, tests_on_hosts_in_review: 0,
        findings_needing_me: 3, to_claim: 0,
      },
      since_last_visit: { is_first_visit: true, as_of: null },
      ...extra,
    });

    it('is about the reader: their queue, their changed reviews, then what to pick up — every number a link', async () => {
      mockedApi.getWorkbench.mockResolvedValue(wb({
        followups: { items: [], total: 6 },
        blockers: { failed_import_count: 2, partial_import_count: 0, imports: [] },
      }));
      mockedApi.getInvestigationQueue.mockResolvedValue({ ...emptyQueue, queue_total: 4, untouched_total: 9 });
      renderPage();
      const lead = (await screen.findByText(/in your queue/)).closest('p') as HTMLElement;
      await within(lead).findByRole('link', { name: /untouched hosts have a reason to look/ });
      expect(lead).toHaveTextContent(
        'You have 5 items in your queue, and 6 hosts you reviewed have changed since. To pick up: 2 imports failed and 4 untouched hosts have a reason to look.',
      );
      expect(lead).not.toHaveTextContent(/Across the team/);
      expect(within(lead).getByRole('link', { name: '5 items' })).toHaveAttribute('href', '/operations#my-work');
      expect(within(lead).getByRole('link', { name: '2 imports failed' }))
        .toHaveAttribute('href', '/parse-errors?status=needs_attention');
      expect(within(lead).getByRole('link', { name: '6 hosts you reviewed have changed since' }))
        .toHaveAttribute('href', '/operations#changed-since-review');
      expect(within(lead).getByRole('link', { name: '4 untouched hosts have a reason to look' }))
        .toHaveAttribute('href', '/operations#untouched-queue');
      // No number in the sentence is plain text: take the links out, and no digit is left.
      const plain = lead.cloneNode(true) as HTMLElement;
      plain.querySelectorAll('a').forEach((a) => a.remove());
      expect(plain.textContent).not.toMatch(/\d/);
      expect(screen.getByText(/findings you own that need something/)).toBeInTheDocument();
    });

    it('says nothing is waiting when nothing is, and never counts an unavailable queue', async () => {
      mockedApi.getInvestigationQueue.mockRejectedValue(new Error('503'));
      renderPage();
      expect(await screen.findByText('Nothing is waiting on you.')).toBeInTheDocument();
      expect(await screen.findByText(/Unavailable — this queue could not be computed/)).toBeInTheDocument();
      expect(screen.queryByText(/reason to look/)).not.toBeInTheDocument();
    });

    it('with an empty queue it still says a host the reader reviewed changed', async () => {
      mockedApi.getWorkbench.mockResolvedValue({ ...emptyWorkbench, followups: { items: [], total: 1 } });
      renderPage();
      const lead = (await screen.findByText(/Nothing is waiting on you/)).closest('p') as HTMLElement;
      expect(lead).toHaveTextContent('Nothing is waiting on you, but 1 host you reviewed has changed since.');
    });
  });

  describe('the two queues', () => {
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
    const tiers = ['Exploitable critical', 'Critical vulnerability', 'Exploit available', 'High-value service, new or changed', 'Scans disagree'];
    const withQueues = () => {
      mockedApi.getWorkbench.mockResolvedValue({
        ...emptyWorkbench, followups: { items: [followRow], total: 1 },
      });
      mockedApi.getInvestigationQueue.mockResolvedValue({
        items: [queueRow], queue_total: 40, untouched_total: 90, tiers, tier_counts: [3, 30, 4, 2, 1],
      });
    };

    it('the tier lives in the URL: it is read on arrival and written on a click', async () => {
      withQueues();
      renderPage('/operations?tier=2');
      await waitFor(() => expect(mockedApi.getInvestigationQueue).toHaveBeenCalledWith(2, { limit: 15 }));
      const chip = await screen.findByRole('button', { name: /Critical vulnerability/ });
      expect(chip).toHaveAttribute('aria-pressed', 'true');
      fireEvent.click(screen.getByRole('button', { name: /Exploit available/ }));
      await waitFor(() => expect(mockedApi.getInvestigationQueue).toHaveBeenLastCalledWith(3, { limit: 15 }));
      await waitFor(() => expect(screen.getByRole('button', { name: /Exploit available/ })).toHaveAttribute('aria-pressed', 'true'));
    });

    it('"Show 15 more" asks the server for a longer page of the same queue', async () => {
      withQueues();
      renderPage();
      fireEvent.click(await screen.findByRole('button', { name: 'Show 15 more' }));
      await waitFor(() => expect(mockedApi.getInvestigationQueue).toHaveBeenLastCalledWith(null, { limit: 30 }));
    });

    it('the changed reviews open exactly the reader’s list in Hosts', async () => {
      withQueues();
      renderPage();
      expect(q(await screen.findByRole('link', { name: 'Open all 1 host in Hosts' }))).toBe('follow:revisit');
      // No reviewer column: every row is the reader's own.
      expect(screen.queryByText(/^by you/)).not.toBeInTheDocument();
    });

    it('an action refreshes the queues quietly, not the whole page', async () => {
      withQueues();
      mockedApi.markStillReviewed.mockResolvedValue({ host_ids: [21] });
      renderPage();
      fireEvent.click(await screen.findByRole('button', { name: 'Still reviewed' }));
      await waitFor(() => expect(mockedApi.markStillReviewed).toHaveBeenCalledWith([21]));
      await waitFor(() => expect(mockedApi.getWorkbench).toHaveBeenCalledTimes(2));
      await waitFor(() => expect(mockedApi.getInvestigationQueue).toHaveBeenCalledTimes(2));
      // Coverage — the structural fetch — was not repeated.
      expect(mockedApi.getProjectCoverage).toHaveBeenCalledTimes(1);
    });

    it('one list at a time owns the keyboard', async () => {
      withQueues();
      renderPage();
      const changed = (await screen.findByRole('link', { name: '10.8.0.2' })).closest('tr') as HTMLElement;
      const untouched = (await screen.findByRole('link', { name: '10.0.0.7' })).closest('tr') as HTMLElement;
      // Neither, until the reader points at one.
      fireEvent.keyDown(window, { key: 'j' });
      expect(document.querySelector('[data-list-cursor]')).toBeNull();
      fireEvent.mouseEnter(untouched.closest('table')!.parentElement!);
      fireEvent.keyDown(window, { key: 'j' });
      expect(untouched).toHaveAttribute('data-list-cursor', 'true');
      expect(changed).not.toHaveAttribute('data-list-cursor');
    });

    it('a failed check of the reviewed hosts says so, never "nothing to re-check"', async () => {
      mockedApi.getWorkbench.mockResolvedValue({ ...emptyWorkbench, followups_unavailable: true });
      renderPage();
      expect(await screen.findByText(/the hosts you reviewed could not be checked/)).toBeInTheDocument();
      expect(screen.queryByText(/Nothing to re-check/)).not.toBeInTheDocument();
      // And the lead does not turn the failure into a count.
      expect(screen.queryByText(/changed since\./)).not.toBeInTheDocument();
    });

    it('a reader (viewer) gets both queues without a write control', async () => {
      projectRole.value = 'viewer';
      accountRole.value = 'member';
      withQueues();
      renderPage();
      await screen.findByRole('link', { name: '10.8.0.2' });
      await screen.findByRole('link', { name: '10.0.0.7' });
      expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
      for (const name of ['Still reviewed', 'Re-open review', 'Review', 'Claim']) {
        expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
      }
    });
  });

  describe('agent sessions: one line', () => {
    it('is about the reader’s own sessions: asks the server for those only, and links to the list', async () => {
      // What the server returns for `user_id=7`: the reader's two sessions.
      // A teammate's live session is not in the answer, so it is not counted.
      mockedApi.listAgentSessions.mockImplementation(async (filters: Record<string, unknown>) => (
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
      const asked = mockedApi.listAgentSessions.mock.calls.map((c) => c[0]);
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
          `Read scope ${baseCoverage.scopes[0].scope_id} in BlueStick, run your scanners on what is in scope, and upload the output to this session.`,
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

    it('shows the setup block alone: no queues, no sections', async () => {
      mockedApi.getProjectCoverage.mockResolvedValue(noHosts);
      renderPage();
      await screen.findByText(/Scope is registered — time to discover hosts/);
      expect(screen.queryByRole('heading', { name: /My work|Changed since review|Untouched/ })).not.toBeInTheDocument();
      expect(screen.queryByText('Your agent sessions')).not.toBeInTheDocument();
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
      await waitFor(() => expect(mockedApi.markWorkbenchSeen).toHaveBeenCalledWith(since.as_of));
      await waitFor(() => expect(screen.queryByText('Since your last visit')).not.toBeInTheDocument());
      // "Mark reviewed" claimed a review nobody did.
      expect(screen.queryByRole('button', { name: /Mark reviewed/ })).not.toBeInTheDocument();
    });

    it('keeps the banner and says so when the acknowledgement cannot be saved', async () => {
      mockedApi.markWorkbenchSeen.mockRejectedValue(new Error('offline'));
      renderPage();
      fireEvent.click(await screen.findByRole('button', { name: /Acknowledge updates/ }));
      expect(await screen.findByRole('alert')).toBeInTheDocument();
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

      it('names what is blocked and carries the recovery action', async () => {
        mockedApi.getWorkbench.mockResolvedValue({ ...workbench(since), blockers });
        renderPage();
        expect(await screen.findByRole('heading', { name: 'Blocked' })).toBeInTheDocument();
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

  it('page Refresh also refetches what fetches for itself (the agent-sessions line)', async () => {
    renderPage();
    // On mount: the Start-Agent-Session badge's hook and the line, one each
    // (the same filters since 5.330.0).  Refresh re-asks for the line only.
    const sessionReads = () => mockedApi.listAgentSessions.mock.calls.length;
    await waitFor(() => expect(sessionReads()).toBe(2));
    const refresh = await screen.findByRole('button', { name: 'Refresh Operations' });
    await waitFor(() => expect(refresh).not.toBeDisabled());
    fireEvent.click(refresh);
    await waitFor(() => expect(sessionReads()).toBe(3));
    await waitFor(() => expect(mockedApi.getWorkbench).toHaveBeenCalledTimes(2));
  });
});
