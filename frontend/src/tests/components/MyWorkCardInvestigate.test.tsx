import React from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import MyWorkCard from '../../components/MyWorkCard';
import type { InvestigationQueueResponse } from '../../services/api';

const navigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});
const api = vi.hoisted(() => ({ updateTestPlanEntry: vi.fn(), followHost: vi.fn() }));
vi.mock('../../services/api', () => api);
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: { id: 1 } }) }));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

const queue: InvestigationQueueResponse = {
  untouched_total: 40,
  queue_total: 2,
  tiers: ['Exploitable critical', 'Critical vulnerability', 'Exploit available', 'High-value service, new or changed', 'Scans disagree'],
  items: [
    {
      host_id: 7,
      ip_address: '10.0.0.7',
      hostname: 'dc01.corp.local',
      tier: 1,
      tier_label: 'Exploitable critical',
      reasons: [
        { kind: 'critical_exploitable', text: '1 critical vulnerability with a known public exploit' },
        { kind: 'high_value', text: 'SMB, RDP open' },
      ],
      evidence: { sources: ['nmap', 'nessus'], last_seen: new Date().toISOString(), confirmation: 'scanner' },
      next_action: { kind: 'review', text: 'Take it into review — nobody has looked at this host yet.' },
    },
    {
      host_id: 8,
      ip_address: '10.0.0.8',
      hostname: null,
      tier: 4,
      tier_label: 'High-value service, new or changed',
      reasons: [{ kind: 'new_host', text: 'First seen 2 days ago' }],
      evidence: { sources: ['nmap'], last_seen: null, confirmation: 'scanner' },
      next_action: { kind: 'collect', text: 'No vulnerability data on this host — run a vulnerability scan against it.' },
    },
  ],
};

const onRetry = vi.fn();
const renderCard = (
  investigate: InvestigationQueueResponse | null,
  investigateUnavailable = false,
  extra: Partial<React.ComponentProps<typeof MyWorkCard>> = {},
) =>
  render(
    <MemoryRouter>
      <MyWorkCard
        queue={null}
        tasks={null}
        notes={null}
        findings={null}
        investigate={investigate}
        investigateUnavailable={investigateUnavailable}
        loading={false}
        error={null}
        onRetry={onRetry}
        {...extra}
      />
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  api.followHost.mockResolvedValue({ status: 'in_review' });
});

describe('MyWorkCard — Worth a look', () => {
  it('lists untouched hosts with their reasons, evidence and next step, ordered by stated tier', () => {
    renderCard(queue);
    expect(screen.getByText('Worth a look')).toBeInTheDocument();
    expect(screen.getByText('1 critical vulnerability with a known public exploit')).toBeInTheDocument();
    expect(screen.getByText('SMB, RDP open')).toBeInTheDocument();
    expect(screen.getByText('nmap, nessus')).toBeInTheDocument();
    expect(screen.getAllByText(/scanner-reported, unconfirmed/)).toHaveLength(2);
    expect(screen.getByText(/Ordered by tier: Exploitable critical › Critical vulnerability/)).toBeInTheDocument();
    // No composite score anywhere: the row says its tier in words.
    expect(screen.getByText('Exploitable critical')).toBeInTheDocument();
  });

  it('"Review" takes the host into review under the caller and refreshes the card', async () => {
    renderCard(queue);
    fireEvent.click(screen.getAllByRole('button', { name: 'Review' })[0]);
    await waitFor(() => expect(api.followHost).toHaveBeenCalledWith(7, 'in_review'));
    await waitFor(() => expect(onRetry).toHaveBeenCalled());
  });

  it('a collect action offers the upload page', () => {
    renderCard(queue);
    fireEvent.click(screen.getByRole('button', { name: 'Upload evidence' }));
    expect(navigate).toHaveBeenCalledWith('/scans');
  });

  it('says so when nothing untouched has a reason', () => {
    renderCard({ ...queue, items: [], queue_total: 0, untouched_total: 12 });
    expect(screen.getByText(/12 untouched hosts, none with a weakness or change on record/)).toBeInTheDocument();
  });

  it('bounds worst-case text so a row cannot stretch or push its buttons out of the card', () => {
    // The card is half the page wide. As a four-column table the next step
    // got ~110px: it wrapped to many lines and "Upload evidence" overflowed.
    const long = 'x'.repeat(200);
    renderCard({
      ...queue,
      items: [{
        ...queue.items[1],
        hostname: `${long}.corp.local`,
        reasons: [{ kind: 'new_host', text: `reason ${long}` }],
        next_action: { kind: 'collect', text: `step ${long}` },
      }],
    });
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.getByTitle(`${long}.corp.local`)).toHaveClass('truncate', 'min-w-0');
    expect(screen.getByTitle(`reason ${long}`)).toHaveClass('line-clamp-2', 'break-words');
    expect(screen.getByTitle(`step ${long}`)).toHaveClass('line-clamp-2', 'break-words');
    // The actions are a fixed column beside the text, never inside a text cell.
    const actions = screen.getByRole('button', { name: 'Upload evidence' }).parentElement!;
    expect(actions).toHaveClass('shrink-0');
    expect(actions.previousElementSibling).toHaveClass('min-w-0', 'flex-1');
  });

  it('a queue the server could not compute reads as unavailable, never as "no work"', () => {
    // The failure placeholder is an empty queue, which used to render
    // "Every host has been touched by someone."
    renderCard({ items: [], queue_total: 0, untouched_total: 0, tiers: [] }, true);
    expect(screen.getByText('Worth a look')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(/could not be computed/);
    expect(screen.queryByText(/Every host has been touched/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(onRetry).toHaveBeenCalled();
  });

  describe('categories', () => {
    const hosts = (n: number) =>
      Array.from({ length: n }, (_, i) => ({
        host_id: 100 + i, ip_address: `10.1.0.${i + 1}`, hostname: null, follow_status: 'in_review',
        follow_updated_at: null, last_viewed_at: null, critical_vulns: 0, high_vulns: 0, open_port_count: 1,
      }));
    const findings = (n: number) =>
      Array.from({ length: n }, (_, i) => ({
        finding_id: 500 + i, title: `Finding ${i}`, severity: 'high', status: 'open', host_id: null,
        host_count: 1, evidence_annotation_id: null, updated_at: null,
      }));

    it('each category expands by itself and states its full count, not what is on screen', () => {
      renderCard(null, false, {
        findings: { items: findings(5), total_open: 40 } as never,
        queue: { items: hosts(5), in_review_count: 5, watching_count: 0 } as never,
      });
      const owned = screen.getByRole('region', { name: 'Findings I own' });
      const inReview = screen.getByRole('region', { name: 'In review' });
      // The server's total, where the card loaded only a slice of it.
      expect(within(owned).getByText('40')).toBeInTheDocument();
      // One pattern: how much of the whole is on screen, then one View all.
      expect(within(owned).getByText('Showing 3 of 40')).toBeInTheDocument();
      expect(within(owned).getByRole('button', { name: 'View all (40)' })).toBeInTheDocument();
      expect(within(owned).queryByText(/not loaded here/)).not.toBeInTheDocument();
      expect(within(inReview).getByText('5')).toBeInTheDocument();
      expect(within(inReview).getAllByRole('listitem')).toHaveLength(3);

      // Reaching "In review" no longer means paging through what ranks above it.
      fireEvent.click(within(inReview).getByRole('button', { name: 'Show 2 more' }));
      expect(within(inReview).getAllByRole('listitem')).toHaveLength(5);
      expect(within(owned).getAllByRole('listitem')).toHaveLength(3);
    });

    it('View all opens the list that holds the whole category, filtered to the caller', () => {
      renderCard(null, false, {
        findings: { items: findings(1), total_open: 1 } as never,
        queue: { items: hosts(1), in_review_count: 1, watching_count: 0 } as never,
      });
      fireEvent.click(within(screen.getByRole('region', { name: 'Findings I own' })).getByRole('button', { name: 'View all (1)' }));
      expect(navigate).toHaveBeenLastCalledWith('/findings?owner=me');
      fireEvent.click(within(screen.getByRole('region', { name: 'In review' })).getByRole('button', { name: 'View all (1)' }));
      expect(navigate.mock.calls[navigate.mock.calls.length - 1][0]).toContain('follow%3Ain_review');
    });
  });

  describe('Needs another look', () => {
    const followups = {
      total: 2,
      mine_total: 1,
      items: [
        {
          host_id: 21, ip_address: '10.8.0.2', hostname: 'app01.corp.local', reviewer_id: 1, reviewer: 'me',
          mine: true, reviewed_at: new Date(Date.now() - 3 * 86_400_000).toISOString(),
          review_conclusion: 'needs_evidence', review_summary: 'waiting on the creds test',
          reasons: [{ kind: 'needs_evidence', text: 'Concluded “needs more evidence” — the question is still open' }],
        },
        {
          host_id: 22, ip_address: '10.8.0.3', hostname: null, reviewer_id: 9, reviewer: 'sam',
          mine: false, reviewed_at: null, review_conclusion: 'no_issue', review_summary: null,
          reasons: [{ kind: 'new_ports', text: '1 open port first seen after the review (8443)' }],
        },
      ],
    };

    it('resurfaces reviewed hosts that are not done, the reviewer\'s own first, with why', () => {
      renderCard(null, false, { followups });
      expect(screen.getByText('Needs another look')).toBeInTheDocument();
      expect(screen.getByText(/— 1 yours/)).toBeInTheDocument();
      expect(screen.getByText(/Concluded “needs more evidence”/)).toBeInTheDocument();
      expect(screen.getByText('“waiting on the creds test”')).toBeInTheDocument();
      expect(screen.getByText(/reviewed by you/)).toBeInTheDocument();
      expect(screen.getByText(/reviewed by sam/)).toBeInTheDocument();
      expect(screen.getByText('1 open port first seen after the review (8443)')).toBeInTheDocument();
    });

    it('re-opening the review puts the host back In Review and refreshes', async () => {
      renderCard(null, false, { followups });
      fireEvent.click(screen.getByRole('button', { name: 'Re-open review' }));
      await waitFor(() => expect(api.followHost).toHaveBeenCalledWith(21, 'in_review'));
      await waitFor(() => expect(onRetry).toHaveBeenCalled());
    });

    // v5.243.0 — and with the section itself, so Next on the host page walks
    // these hosts instead of going nowhere.
    it('opens the host with the way back to the work list, and the section as its queue', () => {
      renderCard(null, false, { followups });
      fireEvent.click(screen.getByRole('button', { name: '10.8.0.2' }));
      expect(navigate).toHaveBeenCalledWith('/hosts/21', {
        state: { fromOperations: true, hostIds: [21, 22], queueLabel: 'Needs another look' },
      });
    });

    // UX review 2026-09-24: 15 loaded of 29, five on screen, and the footer
    // said "Showing 15 of 29" — the count must describe what is visible.
    it('"Showing N of M" counts the rows on screen, not the rows loaded', () => {
      const row = followups.items[1];
      const fifteen = {
        total: 29,
        mine_total: 0,
        items: Array.from({ length: 15 }, (_, i) => ({ ...row, host_id: 300 + i, ip_address: `10.8.1.${i}` })),
      };
      renderCard(null, false, { followups: fifteen });
      expect(screen.getByText('Showing 5 of 29')).toBeInTheDocument();
      expect(screen.queryByText(/Showing 15 of 29/)).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: 'Show 10 more' }));
      expect(screen.getByText('Showing 15 of 29')).toBeInTheDocument();
    });

    it('is absent when nothing is owed, and says so when it could not be computed', () => {
      const { unmount } = renderCard(null, false, { followups: { items: [], total: 0, mine_total: 0 } });
      expect(screen.queryByText('Needs another look')).not.toBeInTheDocument();
      unmount();
      renderCard(null, false, { followups: null, followupsUnavailable: true });
      expect(screen.getByText('Needs another look')).toBeInTheDocument();
      expect(screen.getByRole('alert')).toHaveTextContent(/could not be checked/);
    });
  });

  it('renders nothing for the section on an older backend without the block', () => {
    renderCard(null);
    expect(screen.queryByText('Worth a look')).not.toBeInTheDocument();
  });
});

// Reported by the user against v5.243.0's first build: four hosts in review,
// the card previews three behind "Show 1 more", and opening one of the three
// gave a Next that walked only those three. The queue was built from the rows
// ON SCREEN; it is the category.
describe('MyWorkCard — the queue a host is opened with', () => {
  const inReview = (id: number) => ({
    host_id: id, ip_address: `10.9.0.${id}`, hostname: null, follow_status: 'in_review' as const,
    open_port_count: 2, critical_vulns: 0, high_vulns: 0, last_viewed_at: null,
    follow_updated_at: `2026-09-19T0${id}:00:00Z`,
  });
  const four = { items: [1, 2, 3, 4].map(inReview), in_review_count: 4, watching_count: 0 };

  it('carries every host in the category, not only the previewed rows', () => {
    renderCard(null, false, { queue: four });
    // The preview really is three of four.
    expect(screen.getByRole('button', { name: /Show 1 more/ })).toBeInTheDocument();
    const shown = screen.getAllByRole('button').filter((b) => /^10\.9\.0\.\d/.test(b.textContent ?? ''));
    expect(shown).toHaveLength(3);

    fireEvent.click(shown[0]);
    const [, options] = navigate.mock.calls[0];
    expect([...options.state.hostIds].sort()).toEqual([1, 2, 3, 4]);
    expect(options.state.queueLabel).toBe('In review');
    expect(options.state.queuePartial).toBeUndefined();
  });

  it('says the queue is partial when the server holds more than Operations loaded', () => {
    renderCard(null, false, { queue: { ...four, in_review_count: 14 } });
    const shown = screen.getAllByRole('button').filter((b) => /^10\.9\.0\.\d/.test(b.textContent ?? ''));
    fireEvent.click(shown[0]);
    expect(navigate.mock.calls[0][1].state).toMatchObject({ hostIds: expect.any(Array), queuePartial: true });
    expect(navigate.mock.calls[0][1].state.hostIds).toHaveLength(4);
  });

  it('Worth a look carries its whole list too', () => {
    const many = { ...queue, queue_total: 7, items: [1, 2, 3, 4, 5, 6, 7].map((n) => ({ ...queue.items[0], host_id: 100 + n, ip_address: `10.7.7.${n}` })) };
    renderCard(many);
    fireEvent.click(screen.getByRole('button', { name: '10.7.7.1' }));
    expect(navigate.mock.calls[0][1].state.hostIds).toEqual([101, 102, 103, 104, 105, 106, 107]);
  });
});
