/**
 * "My work" — only what needs the reader (5.329.0, design review 2026-10-02).
 *
 * Carries what `MyWorkCardInvestigate.test.tsx` pinned for the personal list
 * (groups expand by themselves and state the server's count; the queue a host
 * is opened with is the whole group; a task links to its test and claims with
 * the revision it was shown).  The two team queues that file also covered are
 * in `ChangedSinceReviewSection.test.tsx` and `UntouchedQueueSection.test.tsx`.
 */
import React from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import MyWorkCard, { personalWorkCounts } from '../../components/MyWorkCard';

// The real router (setupTests stubs useLocation): rows are links, and where
// one went is read back from the location.
vi.mock('react-router-dom', async () => vi.importActual<typeof import('react-router-dom')>('react-router-dom'));
const api = vi.hoisted(() => ({ updateHostTest: vi.fn() }));
vi.mock('../../services/api', () => api);
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: { id: 1 } }) }));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

// Rows are links (a middle-click opens a tab), so where one went is read from
// the router, not from a mocked navigate().
let lastLocation: { pathname: string; search: string; state: unknown } | null = null;
const LocationProbe: React.FC = () => {
  const loc = useLocation();
  lastLocation = { pathname: loc.pathname, search: loc.search, state: loc.state };
  return null;
};
// eslint-disable-next-line @typescript-eslint/no-explicit-any
const lastState = () => lastLocation?.state as any;

const onRetry = vi.fn();
const renderCard = (extra: Partial<React.ComponentProps<typeof MyWorkCard>> = {}) =>
  render(
    <MemoryRouter>
      <LocationProbe />
      <MyWorkCard queue={null} tasks={null} findings={null} loading={false} error={null} onRetry={onRetry} {...extra} />
    </MemoryRouter>,
  );

const host = (id: number) => ({
  host_id: id, ip_address: `10.9.0.${id}`, hostname: null, follow_status: 'in_review' as const,
  open_port_count: 2, critical_vulns: 0, high_vulns: 0, last_viewed_at: null,
  follow_updated_at: `2026-09-19T0${id % 10}:00:00Z`,
});
const hosts = (n: number, total = n) => ({
  items: Array.from({ length: n }, (_, i) => host(i + 1)), in_review_count: total, watching_count: 0,
});
const finding = (id: number, over: Record<string, unknown> = {}) => ({
  finding_id: id, title: `Finding ${id}`, severity: 'high', status: 'open', host_id: null,
  host_count: 1, evidence_annotation_id: null, updated_at: null,
  needs: [{ kind: 'under_investigation', text: 'under investigation' }], missing_text: [], pending_proposals: 0,
  ...over,
});
const task = (over: Record<string, unknown> = {}) => ({
  test_id: 31, description: 'SMB signing', label: 'SMB sweep', revision: 4,
  host_id: 5, host_ip: '10.0.0.5', host_hostname: null, priority: 'high', status: 'proposed',
  rationale: null, updated_at: new Date().toISOString(), reasons: ['triage'], assigned_to_id: null,
  ...over,
});
const tasks = (items: ReturnType<typeof task>[], groups = { assigned: 0, in_review: 0, triage: items.length }) => ({
  items, total_open: groups.assigned + groups.in_review + groups.triage,
  reason_counts: groups, group_counts: groups,
});

beforeEach(() => {
  vi.clearAllMocks();
  lastLocation = null;
});

describe('MyWorkCard — findings that need me', () => {
  it('says on the row what is owed, and counts need — not ownership', () => {
    renderCard({
      findings: {
        total_open: 3,
        items: [
          finding(11),
          finding(12, {
            status: 'confirmed', missing_text: ['impact', 'recommendation'],
            needs: [{ kind: 'missing_text', text: 'report text missing: impact, recommendation' }],
          }),
          finding(13, {
            status: 'confirmed', pending_proposals: 2,
            needs: [{ kind: 'proposals', text: '2 proposals to decide' }],
          }),
        ],
      } as never,
    });
    const group = screen.getByRole('region', { name: 'Findings that need me' });
    expect(within(group).getByText('3')).toBeInTheDocument();
    expect(within(group).getByText('under investigation')).toBeInTheDocument();
    expect(within(group).getByText('report text missing: impact, recommendation')).toBeInTheDocument();
    expect(within(group).getByText('2 proposals to decide')).toBeInTheDocument();
    // Each row opens the finding — where its status, text and proposals are.
    expect(within(group).getByRole('link', { name: /#12/ })).toHaveAttribute('href', '/findings/12');
    // Ownership is a secondary link, not the group.
    expect(within(group).getByRole('link', { name: 'All findings I own' })).toHaveAttribute('href', '/findings?owner=me');
    expect(screen.queryByText('Findings I own')).not.toBeInTheDocument();
  });

  it('a finding wears the severity ramp; a test’s priority does not', () => {
    renderCard({
      findings: { total_open: 1, items: [finding(11, { severity: 'critical' })] } as never,
      tasks: tasks([task({ priority: 'critical' })]) as never,
    });
    const severity = within(screen.getByRole('region', { name: 'Findings that need me' })).getByText('critical');
    expect(severity.className).toMatch(/sev-critical/);
    const priority = within(screen.getByRole('region', { name: 'Available to claim' })).getByText('critical priority');
    expect(priority.className).not.toMatch(/sev-|destructive|warning/);
  });
});

describe('MyWorkCard — the heading adds up', () => {
  const full = {
    queue: hosts(3) as never,
    findings: { total_open: 1, items: [finding(11)] } as never,
    tasks: tasks(
      [
        task({ test_id: 1, reasons: ['assigned'], assigned_to_id: 1 }),
        task({ test_id: 2, reasons: ['in_review'] }),
        task({ test_id: 3, reasons: ['in_review', 'triage'] }),
        task({ test_id: 4 }),
      ],
      { assigned: 1, in_review: 2, triage: 1 },
    ) as never,
  };

  it('the total is the sum of the groups listed; "to claim" is beside it and has its list', () => {
    renderCard({
      ...full,
      totals: {
        total: 7, hosts_in_review: 3, tests_assigned: 1, tests_on_hosts_in_review: 2,
        findings_needing_me: 1, to_claim: 1,
      },
    });
    expect(screen.getByText('7 waiting on you')).toBeInTheDocument();
    const personal = ['Tests assigned to me', 'Findings that need me', 'Hosts I am reviewing', 'Tests on hosts I am reviewing'];
    const counts = personal.map((name) => {
      const heading = within(screen.getByRole('region', { name })).getByRole('heading', { name });
      return Number(heading.nextElementSibling?.textContent);
    });
    expect(counts).toEqual([1, 1, 3, 2]);
    expect(counts.reduce((a, b) => a + b, 0)).toBe(7);

    // The count names a list that is on the page.
    const toClaim = screen.getByRole('link', { name: '1 to claim' });
    expect(toClaim).toHaveAttribute('href', '#available-to-claim');
    const claimable = document.getElementById('available-to-claim')!;
    expect(within(claimable).getByRole('button', { name: 'Claim' })).toBeInTheDocument();
  });

  it('without the server’s sum (an older backend) it adds the same groups', () => {
    expect(personalWorkCounts(full.queue, full.tasks, full.findings)).toEqual({ total: 7, available: 1 });
    expect(personalWorkCounts(full.queue, full.tasks, full.findings, {
      total: 9, hosts_in_review: 3, tests_assigned: 1, tests_on_hosts_in_review: 4, findings_needing_me: 1, to_claim: 2,
    })).toEqual({ total: 9, available: 2 });
  });

  it('hosts and tests are separate groups, each row naming its kind', () => {
    renderCard(full);
    const reviewing = screen.getByRole('region', { name: 'Hosts I am reviewing' });
    const onThem = screen.getByRole('region', { name: 'Tests on hosts I am reviewing' });
    expect(within(reviewing).getAllByText('Host:')).toHaveLength(3);
    expect(within(onThem).getAllByText('Test:')).toHaveLength(2);
    expect(within(reviewing).queryByText('Test:')).not.toBeInTheDocument();
  });

  it('labels the age column, and says so when no time was recorded', () => {
    renderCard({ findings: { total_open: 1, items: [finding(11)] } as never });
    const group = screen.getByRole('region', { name: 'Findings that need me' });
    expect(within(group).getByText('waiting')).toBeInTheDocument();
    expect(within(group).getByLabelText('waiting time not recorded')).toHaveTextContent('—');
  });
});

describe('MyWorkCard — one "more" pattern', () => {
  it('a group a page lists exactly opens that list; it does not expand in place', () => {
    renderCard({ queue: hosts(8, 37) as never });
    const group = screen.getByRole('region', { name: 'Hosts I am reviewing' });
    expect(within(group).getByText('5 of 37')).toBeInTheDocument();
    const all = within(group).getByRole('link', { name: 'Open all 37 in Hosts' });
    expect(new URL(all.getAttribute('href')!, 'https://x').searchParams.get('q')).toBe('follow:mine');
    expect(within(group).queryByRole('button', { name: /Show/ })).not.toBeInTheDocument();
  });

  it('a group no page lists expands in place, by itself, and states the server’s count', () => {
    renderCard({
      findings: { total_open: 40, items: Array.from({ length: 8 }, (_, i) => finding(500 + i)) } as never,
      queue: hosts(6) as never,
    });
    const owned = screen.getByRole('region', { name: 'Findings that need me' });
    const reviewing = screen.getByRole('region', { name: 'Hosts I am reviewing' });
    expect(within(owned).getByText('40')).toBeInTheDocument();
    expect(within(owned).getByText('5 of 40')).toBeInTheDocument();
    expect(within(owned).queryByRole('link', { name: /Open all/ })).not.toBeInTheDocument();
    fireEvent.click(within(owned).getByRole('button', { name: 'Show 3 more' }));
    expect(within(owned).getByText('8 of 40')).toBeInTheDocument();
    expect(within(owned).getByText(/32 more are not loaded/)).toBeInTheDocument();
    // The other group did not move.
    expect(within(reviewing).getAllByRole('listitem')).toHaveLength(5);
    fireEvent.click(within(owned).getByRole('button', { name: 'Show fewer' }));
    expect(within(owned).getAllByRole('listitem')).toHaveLength(5);
  });
});

// Reported against v5.243.0's first build: four hosts in review, three
// previewed, and Next on the host page walked only those three. The queue a
// host is opened with is the GROUP, not the rows on screen.
describe('MyWorkCard — the queue a host is opened with', () => {
  it('carries every host in the group, not only the previewed rows', () => {
    renderCard({ queue: hosts(7) as never });
    const shown = screen.getAllByRole('link').filter((b) => /10\.9\.0\.\d/.test(b.textContent ?? ''));
    expect(shown).toHaveLength(5);
    fireEvent.click(shown[0]);
    expect([...lastState().hostIds].sort()).toEqual([1, 2, 3, 4, 5, 6, 7]);
    expect(lastState().queueLabel).toBe('Hosts I am reviewing');
    expect(lastState().queuePartial).toBeUndefined();
  });

  it('says the queue is partial when the server holds more than Operations loaded', () => {
    renderCard({ queue: hosts(4, 14) as never });
    const shown = screen.getAllByRole('link').filter((b) => /10\.9\.0\.\d/.test(b.textContent ?? ''));
    fireEvent.click(shown[0]);
    expect(lastState()).toMatchObject({ hostIds: expect.any(Array), queuePartial: true });
    expect(lastState().hostIds).toHaveLength(4);
  });
});

// A task is a host test: the row opens the test on its host's page, and Claim
// assigns it under the revision read.
describe('MyWorkCard — host tests as tasks', () => {
  it('links a task to its test on the host page, with the label and description', () => {
    renderCard({ tasks: tasks([task()]) as never });
    const link = screen.getByRole('link', { name: /10\.0\.0\.5/ });
    expect(link).toHaveAttribute('href', '/hosts/5#host-test-31');
    expect(link).toHaveTextContent('SMB sweep · SMB signing');
  });

  it('claims with the revision it was shown, then refreshes', async () => {
    const onChanged = vi.fn();
    api.updateHostTest.mockResolvedValue({ id: 31, revision: 5 });
    renderCard({ tasks: tasks([task()]) as never, onChanged });
    fireEvent.click(screen.getByRole('button', { name: 'Claim' }));
    await waitFor(() => expect(api.updateHostTest).toHaveBeenCalledWith(31, { assigned_to_id: 1, expected_revision: 4 }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('offers no Claim on a test already assigned to the caller', () => {
    renderCard({
      tasks: tasks([task({ reasons: ['assigned'], assigned_to_id: 1 })], { assigned: 1, in_review: 0, triage: 0 }) as never,
    });
    expect(screen.getByRole('link', { name: /10\.0\.0\.5/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Claim' })).not.toBeInTheDocument();
  });

  it('a reader is shown the claimable tests without the Claim', () => {
    renderCard({ tasks: tasks([task()]) as never, canWrite: false });
    expect(screen.getByRole('region', { name: 'Available to claim' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Claim' })).not.toBeInTheDocument();
  });

  it('renders a task with no label and a 200-character description on one line', () => {
    const long = 'x'.repeat(200);
    renderCard({ tasks: tasks([task({ label: null, description: long })]) as never });
    const meta = screen.getByText(new RegExp(long));
    expect(meta).toHaveClass('truncate', 'min-w-0');
  });
});

describe('MyWorkCard — states', () => {
  it('says nothing is waiting, and what would be', () => {
    renderCard();
    expect(screen.getByText(/Nothing is waiting on you\./)).toBeInTheDocument();
    expect(screen.getByText(/a finding you own needs something/)).toBeInTheDocument();
  });

  it('a failed load is an error with a retry, never an empty queue', () => {
    renderCard({ error: 'workbench down' });
    expect(screen.getByRole('alert')).toHaveTextContent('workbench down');
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }));
    expect(onRetry).toHaveBeenCalled();
    expect(screen.queryByText(/Nothing is waiting/)).not.toBeInTheDocument();
  });

  it('links to the reader’s own activity, which lives on Collaboration', () => {
    renderCard();
    expect(screen.getByRole('link', { name: 'My activity' })).toHaveAttribute('href', '/activity?author=me');
  });
});
