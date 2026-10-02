/**
 * "Untouched, with a reason" (5.329.0; "Worth a look" until then).
 *
 * Carries what `MyWorkCardInvestigate.test.tsx` pinned for this queue — the
 * tiers narrow the list and a second click restores it, every row says why in
 * words (never a score), Review takes the host, a queue the server could not
 * compute is "unavailable" and never "no work", the host opens with the whole
 * list as its queue — and what the design review changed: chips instead of
 * bars, one-line rows, what is true of every row said once, selection and
 * bulk Review, and a count that opens its exact list where a query exists.
 *
 * Not carried: the row's "Upload evidence" button. A row has one action now;
 * the "no vulnerability data yet" step is on the row's tooltip.
 */
import React from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import UntouchedQueueSection, {
  type UntouchedQueueSectionProps,
} from '../../components/operations/UntouchedQueueSection';
import type { InvestigateRow, InvestigationQueueResponse } from '../../services/api';

// The real router (setupTests stubs useLocation): rows are links, and where
// one went is read back from the location.
vi.mock('react-router-dom', async () => vi.importActual<typeof import('react-router-dom')>('react-router-dom'));
const api = vi.hoisted(() => ({ followHost: vi.fn(), unfollowHost: vi.fn() }));
vi.mock('../../services/api', () => api);
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));

let lastLocation: { pathname: string; state: unknown } | null = null;
const LocationProbe: React.FC = () => {
  const loc = useLocation();
  lastLocation = { pathname: loc.pathname, state: loc.state };
  return null;
};

const TIERS = ['Exploitable critical', 'Critical vulnerability', 'Exploit available', 'High-value service, new or changed', 'Scans disagree'];
const item = (over: Partial<InvestigateRow> = {}): InvestigateRow => ({
  host_id: 7, ip_address: '10.0.0.7', hostname: 'dc01.corp.local', tier: 1, tier_label: TIERS[0],
  reasons: [
    { kind: 'critical_exploitable', text: '1 critical vulnerability with a known public exploit' },
    { kind: 'high_value', text: 'MySQL, SSH, Telnet open' },
  ],
  evidence: { sources: [], last_seen: new Date().toISOString(), confirmation: 'scanner' },
  next_action: { kind: 'review', text: 'Take it into review — nobody has looked at this host yet.', generic: true },
  ...over,
});
const collect = item({
  host_id: 8, ip_address: '10.0.0.8', hostname: null, tier: 4, tier_label: TIERS[3],
  reasons: [{ kind: 'new_host', text: 'First seen 2 days ago' }],
  evidence: { sources: ['nmap', 'nessus'], last_seen: null, confirmation: 'scanner' },
  next_action: { kind: 'collect', text: 'No vulnerability data on this host — run a vulnerability scan against it.' },
});
const queue = (over: Partial<InvestigationQueueResponse> = {}): InvestigationQueueResponse => ({
  untouched_total: 290, queue_total: 112, tiers: TIERS, tier_counts: [3, 30, 4, 74, 1],
  items: [item(), collect],
  ...over,
});

const onChanged = vi.fn();
const onTier = vi.fn();
const onRetry = vi.fn();
const onMore = vi.fn();
const props = (over: Partial<UntouchedQueueSectionProps> = {}): UntouchedQueueSectionProps => ({
  data: queue(), loading: false, unavailable: false, onRetry, tier: null, onTier, onMore,
  canWrite: true, onChanged, ...over,
});
const renderIt = (over: Partial<UntouchedQueueSectionProps> = {}) =>
  render(<MemoryRouter><LocationProbe /><UntouchedQueueSection {...props(over)} /></MemoryRouter>);
const rowOf = (ip: string) => screen.getByRole('link', { name: ip }).closest('tr') as HTMLElement;
const q = (el: HTMLElement) => new URL(el.getAttribute('href')!, 'https://x').searchParams.get('q');

beforeEach(() => {
  vi.clearAllMocks();
  lastLocation = null;
  api.followHost.mockResolvedValue({ status: 'in_review' });
});

describe('Untouched, with a reason — tiers', () => {
  it('are filter chips with their counts, in tier order — no bars, no warning colour', () => {
    renderIt();
    const chips = screen.getByRole('group', { name: 'Filter by tier' });
    const buttons = within(chips).getAllByRole('button');
    expect(buttons.map((b) => b.textContent)).toEqual([
      'All tiers 112', 'Exploitable critical3', 'Critical vulnerability30', 'Exploit available4',
      'High-value service, new or changed74', 'Scans disagree1',
    ]);
    expect(buttons[0]).toHaveAttribute('aria-pressed', 'true');
    // The tier to act on first is marked by order and weight — nothing is
    // sized to the largest tier, and nothing wears a warning colour.
    expect(buttons[1]).toHaveClass('font-semibold');
    expect(buttons[4]).not.toHaveClass('font-semibold');
    expect(chips.querySelector('[style*="width"]')).toBeNull();
    expect(chips.innerHTML).not.toMatch(/warning|destructive|sev-/);
  });

  it('a chip narrows the list; a second click, or "All tiers", restores it', () => {
    const { rerender } = renderIt();
    fireEvent.click(screen.getByRole('button', { name: /Exploit available/ }));
    expect(onTier).toHaveBeenLastCalledWith(3);
    rerender(<MemoryRouter><UntouchedQueueSection {...props({ tier: 3 })} /></MemoryRouter>);
    const on = screen.getByRole('button', { name: /Exploit available/ });
    expect(on).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(on);
    expect(onTier).toHaveBeenLastCalledWith(null);
    fireEvent.click(screen.getByRole('button', { name: /All tiers/ }));
    expect(onTier).toHaveBeenLastCalledWith(null);
  });

  it('an empty tier is not a button', () => {
    renderIt({ data: queue({ tier_counts: [3, 0, 4, 74, 1] }) });
    const chips = screen.getByRole('group', { name: 'Filter by tier' });
    expect(within(chips).queryByRole('button', { name: /Critical vulnerability/ })).not.toBeInTheDocument();
    expect(chips).toHaveTextContent('Critical vulnerability 0');
  });
});

describe('Untouched, with a reason — rows', () => {
  it('one line: address, name, the reasons in words, the tier', () => {
    renderIt();
    expect(screen.getByRole('heading', { name: /Untouched, with a reason/ })).toBeInTheDocument();
    expect(screen.queryByText('Worth a look')).not.toBeInTheDocument();
    const first = rowOf('10.0.0.7');
    expect(within(first).getByText('dc01.corp.local')).toBeInTheDocument();
    const why = within(first).getByText('1 critical vulnerability with a known public exploit · MySQL, SSH, Telnet open');
    expect(why).toHaveClass('truncate');
    // No composite score anywhere: the tier is named.
    expect(within(first).getByText('Exploitable critical')).toBeInTheDocument();
    expect(screen.getByRole('table')).toHaveClass('table-fixed');
  });

  it('says once what is true of every row, and shows provenance only when there is some', () => {
    renderIt();
    // Under the heading, once — not on each row.
    expect(screen.getAllByText(/scanner-reported and unconfirmed/)).toHaveLength(1);
    expect(screen.queryByText(/source tool not recorded/)).not.toBeInTheDocument();
    expect(within(rowOf('10.0.0.7')).queryByText(/last observed/)).not.toBeInTheDocument();
    // A recorded source is muted provenance on the row that has it.
    expect(within(rowOf('10.0.0.8')).getByText(/nmap, nessus/)).toBeInTheDocument();
    // The next step that says something is on the row's tooltip.
    expect(within(rowOf('10.0.0.8')).getByTitle(/run a vulnerability scan against it/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Upload evidence' })).not.toBeInTheDocument();
  });

  it('with a tier chosen, the tier is not repeated on the rows', () => {
    renderIt({ tier: 1, data: queue({ items: [item()] }) });
    expect(screen.queryByRole('columnheader', { name: 'Tier' })).not.toBeInTheDocument();
    expect(within(rowOf('10.0.0.7')).queryByText('Exploitable critical')).not.toBeInTheDocument();
  });

  it('a 200-character hostname and reason stay on the row’s one line', () => {
    const long = 'x'.repeat(200);
    renderIt({ data: queue({ items: [item({ hostname: `${long}.corp.local`, reasons: [{ kind: 'new_host', text: `reason ${long}` }] })] }) });
    expect(screen.getByTitle(`${long}.corp.local`)).toHaveClass('truncate');
    expect(screen.getByText(`reason ${long}`)).toHaveClass('truncate');
  });

  it('opens a host with the whole list as its queue', () => {
    const many = queue({ queue_total: 7, items: [1, 2, 3, 4, 5, 6, 7].map((n) => item({ host_id: 100 + n, ip_address: `10.7.7.${n}` })) });
    renderIt({ data: many });
    fireEvent.click(screen.getByRole('link', { name: '10.7.7.1' }));
    expect(lastLocation?.pathname).toBe('/hosts/101');
    expect(lastLocation?.state).toEqual({
      fromOperations: true, hostIds: [101, 102, 103, 104, 105, 106, 107], queueLabel: 'Untouched, with a reason',
    });
  });
});

describe('Untouched, with a reason — the count and its list', () => {
  it('a tier a query expresses opens exactly that Hosts list', () => {
    renderIt({ tier: 2, data: queue({ items: [item({ tier: 2, tier_label: TIERS[1] })] }) });
    expect(screen.getByText('1 of 30')).toBeInTheDocument();
    const all = screen.getByRole('link', { name: 'Open all 30 hosts in Hosts' });
    expect(q(all)).toBe('has:untouched AND has:critical AND NOT has:critical_exploit');
    expect(screen.queryByRole('button', { name: /Show .* more/ })).not.toBeInTheDocument();
  });

  it('where no query expresses the set, the link names what it opens and the queue pages here', () => {
    renderIt();
    expect(screen.getByText('2 of 112')).toBeInTheDocument();
    // Never "Open all 112": no Hosts query lists those 112.
    expect(screen.queryByRole('link', { name: /Open all 112/ })).not.toBeInTheDocument();
    const all = screen.getByRole('link', { name: 'Open all 290 untouched hosts in Hosts' });
    expect(q(all)).toBe('has:untouched');
    fireEvent.click(screen.getByRole('button', { name: 'Show 15 more' }));
    expect(onMore).toHaveBeenCalled();
  });

  it('says so when nothing untouched has a reason, and opens the untouched hosts', () => {
    renderIt({ data: queue({ items: [], queue_total: 0, untouched_total: 12, tier_counts: [0, 0, 0, 0, 0] }) });
    expect(screen.getByText(/untouched, none with a weakness or change on record/)).toBeInTheDocument();
    expect(q(screen.getByRole('link', { name: '12 hosts' }))).toBe('has:untouched');
    expect(screen.queryByRole('group', { name: 'Filter by tier' })).not.toBeInTheDocument();
  });

  it('says every host has been touched when none is untouched', () => {
    renderIt({ data: queue({ items: [], queue_total: 0, untouched_total: 0, tier_counts: [0, 0, 0, 0, 0] }) });
    expect(screen.getByText('Every host has been touched by someone.')).toBeInTheDocument();
  });
});

describe('Untouched, with a reason — actions', () => {
  it('"Review" takes the host into review under the reader and refreshes', async () => {
    renderIt();
    fireEvent.click(within(rowOf('10.0.0.7')).getByRole('button', { name: 'Review' }));
    await waitFor(() => expect(api.followHost).toHaveBeenCalledWith(7, 'in_review'));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('bulk Review takes every selected host, and reports a partial failure honestly', async () => {
    api.followHost.mockImplementation(async (id: number) => {
      // eslint-disable-next-line @typescript-eslint/no-throw-literal
      if (id === 8) throw { response: { status: 403, data: { detail: 'not a member' } } };
      return { status: 'in_review' };
    });
    renderIt();
    expect(screen.queryByRole('toolbar')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all rows shown' }));
    expect(rowOf('10.0.0.7')).toHaveAttribute('data-state', 'selected');
    expect(rowOf('10.0.0.7')).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Review (2)' }));
    await waitFor(() => expect(api.followHost).toHaveBeenCalledTimes(2));
    expect(await screen.findByText(/Took 1 of 2 hosts into review; 1 could not be taken \(not a member\)/)).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalled();
  });

  it('a reader gets the rows and links, without checkboxes or Review', () => {
    renderIt({ canWrite: false });
    expect(screen.getByRole('link', { name: '10.0.0.7' })).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Review' })).not.toBeInTheDocument();
    expect(screen.queryByText(/Review takes a host into your queue/)).not.toBeInTheDocument();
    // The filter chips are not writes.
    expect(screen.getByRole('button', { name: /Exploitable critical/ })).toBeInTheDocument();
  });
});

describe('Untouched, with a reason — states', () => {
  it('a queue the server could not compute reads as unavailable, never as "no work"', () => {
    renderIt({ data: null, unavailable: true });
    expect(screen.getByRole('heading', { name: 'Untouched, with a reason' })).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent(/could not be computed/);
    expect(screen.queryByText(/Every host has been touched/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(onRetry).toHaveBeenCalled();
  });

  it('says it is looking while the queue loads, and renders nothing without one', () => {
    const { unmount } = renderIt({ data: null, loading: true });
    expect(screen.getByRole('status')).toHaveTextContent(/Finding untouched hosts/);
    unmount();
    renderIt({ data: null });
    expect(screen.queryByText('Untouched, with a reason')).not.toBeInTheDocument();
  });
});
