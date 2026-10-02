/**
 * "Changed since review" (5.329.0; "Needs another look" until then).
 *
 * Carries what `MyWorkCardInvestigate.test.tsx` pinned for this queue — it
 * says why each host is back, the reviewer's own review asks once more before
 * it is re-opened, a host opens with the section as its queue, and the footer
 * counts the rows on screen — and adds what the design review asked for: an
 * answer other than re-opening ("Still reviewed"), selection, bulk actions,
 * one-line rows, and a count that opens its exact list.
 */
import React from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import ChangedSinceReviewSection from '../../components/operations/ChangedSinceReviewSection';
import type { ReviewFollowupRow, ReviewFollowupsResponse } from '../../services/api';

// The real router (setupTests stubs useLocation): rows are links, and where
// one went is read back from the location.
vi.mock('react-router-dom', async () => vi.importActual<typeof import('react-router-dom')>('react-router-dom'));
const api = vi.hoisted(() => ({ followHost: vi.fn(), unfollowHost: vi.fn(), markStillReviewed: vi.fn() }));
vi.mock('../../services/api', () => api);
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));

let lastLocation: { pathname: string; state: unknown } | null = null;
const LocationProbe: React.FC = () => {
  const loc = useLocation();
  lastLocation = { pathname: loc.pathname, state: loc.state };
  return null;
};

const row = (over: Partial<ReviewFollowupRow> = {}): ReviewFollowupRow => ({
  host_id: 21, ip_address: '10.8.0.2', hostname: 'app01.corp.local', reviewer_id: 1, reviewer: 'me',
  mine: true, reviewed_at: new Date(Date.now() - 3 * 86_400_000).toISOString(),
  review_conclusion: 'no_issue', review_summary: 'looked at ssh',
  reasons: [{ kind: 'new_ports', text: '3 open ports first seen after the review (80, 445, 8080)' }],
  ...over,
});
const openQuestion = row({
  host_id: 22, ip_address: '10.8.0.3', hostname: null, review_conclusion: 'needs_evidence', review_summary: null,
  reasons: [{ kind: 'needs_evidence', text: 'Concluded “needs more evidence” — the question is still open' }],
});
const theirs = row({
  host_id: 23, ip_address: '10.8.0.4', hostname: null, reviewer_id: 9, reviewer: 'sam', mine: false,
  reasons: [{ kind: 'new_vulns', text: '1 critical scanner observation recorded after the review' }],
});
const data = (items: ReviewFollowupRow[], over: Partial<ReviewFollowupsResponse> = {}): ReviewFollowupsResponse => ({
  items, total: items.length, mine_total: items.filter((r) => r.mine).length, host_total: items.length, ...over,
});

const onChanged = vi.fn();
const renderIt = (d: ReviewFollowupsResponse, extra: Partial<React.ComponentProps<typeof ChangedSinceReviewSection>> = {}) =>
  render(
    <MemoryRouter>
      <LocationProbe />
      <ChangedSinceReviewSection data={d} canWrite onChanged={onChanged} {...extra} />
    </MemoryRouter>,
  );
const rowOf = (ip: string) => screen.getByRole('link', { name: ip }).closest('tr') as HTMLElement;

beforeEach(() => {
  vi.clearAllMocks();
  lastLocation = null;
  api.followHost.mockResolvedValue({ status: 'in_review' });
  api.markStillReviewed.mockImplementation(async (ids: number[]) => ({ host_ids: ids }));
});

describe('Changed since review — what it shows', () => {
  it('one line per review: address, name, what changed, who reviewed it', () => {
    renderIt(data([row(), openQuestion, theirs]));
    expect(screen.getByRole('heading', { name: /Changed since review/ })).toBeInTheDocument();
    expect(screen.queryByText('Needs another look')).not.toBeInTheDocument();
    const first = rowOf('10.8.0.2');
    expect(within(first).getByText('app01.corp.local')).toBeInTheDocument();
    expect(within(first).getByText('3 open ports first seen after the review (80, 445, 8080)')).toHaveClass('truncate');
    expect(within(first).getByText(/^by you/)).toBeInTheDocument();
    expect(within(rowOf('10.8.0.4')).getByText(/^by sam/)).toBeInTheDocument();
    // The reviewer's summary is provenance: on the row's tooltip, not a second line.
    expect(within(first).getByText(/^by you/)).toHaveAttribute('title', expect.stringContaining('“looked at ssh”'));
    expect(screen.getByText(/2 of 3 reviews are yours/)).toBeInTheDocument();
  });

  it('the count is in hosts and opens exactly its Hosts list', () => {
    // Two reviewers of one host: four rows, three hosts.
    renderIt(data([row(), openQuestion, theirs, row({ reviewer_id: 9, reviewer: 'sam', mine: false })],
      { total: 30, host_total: 26 }));
    const q = (el: HTMLElement) => new URL(el.getAttribute('href')!, 'https://x').searchParams.get('q');
    const count = screen.getByRole('link', { name: '26 hosts changed since review — view hosts' });
    expect(q(count)).toBe('has:changed_since_review OR conclusion:needs_evidence');
    // One footer pattern: what is on screen, then the whole list.
    expect(screen.getByText('4 of 30')).toBeInTheDocument();
    expect(q(screen.getByRole('link', { name: 'Open all 26 hosts in Hosts' })))
      .toBe('has:changed_since_review OR conclusion:needs_evidence');
    expect(screen.queryByRole('button', { name: /Show .* more/ })).not.toBeInTheDocument();
  });

  it('opens a host with the way back, and the section as its queue', () => {
    renderIt(data([row(), openQuestion]));
    fireEvent.click(screen.getByRole('link', { name: '10.8.0.2' }));
    expect(lastLocation?.pathname).toBe('/hosts/21');
    expect(lastLocation?.state).toEqual({ fromOperations: true, hostIds: [21, 22], queueLabel: 'Changed since review' });
  });

  it('a 200-character hostname and reason stay on the row’s one line', () => {
    const long = 'x'.repeat(200);
    renderIt(data([row({ hostname: `${long}.corp.local`, reasons: [{ kind: 'new_ports', text: `reason ${long}` }] })]));
    expect(screen.getByTitle(`${long}.corp.local`)).toHaveClass('truncate');
    const reason = screen.getByText(`reason ${long}`);
    expect(reason).toHaveClass('truncate');
    // Clamped, so the whole of it is on the tooltip.
    expect(reason).toHaveAttribute('title', expect.stringContaining(`reason ${long}`));
    const table = screen.getByRole('table');
    expect(table).toHaveClass('table-fixed');
    // Whatever a browser makes of the columns, the page does not scroll sideways.
    expect(table.parentElement).toHaveClass('overflow-x-auto', 'min-w-0');
  });

  it('with nothing to re-check it says so — a real empty state, not a missing section', () => {
    renderIt(data([]));
    expect(screen.getByRole('heading', { name: /Changed since review/ })).toBeInTheDocument();
    expect(screen.getByText(/Nothing to re-check/)).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });
});

describe('Changed since review — answering a change', () => {
  it('"Still reviewed" re-stamps the reader’s own review and refreshes', async () => {
    renderIt(data([row(), theirs]));
    fireEvent.click(within(rowOf('10.8.0.2')).getByRole('button', { name: 'Still reviewed' }));
    await waitFor(() => expect(api.markStillReviewed).toHaveBeenCalledWith([21]));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(api.followHost).not.toHaveBeenCalled();
  });

  it('is not offered on an open question, nor on someone else’s review', () => {
    renderIt(data([row(), openQuestion, theirs]));
    expect(within(rowOf('10.8.0.2')).getByRole('button', { name: 'Still reviewed' })).toBeInTheDocument();
    expect(within(rowOf('10.8.0.3')).queryByRole('button', { name: 'Still reviewed' })).not.toBeInTheDocument();
    expect(within(rowOf('10.8.0.3')).getByRole('button', { name: 'Re-open review' })).toBeInTheDocument();
    expect(within(rowOf('10.8.0.4')).queryByRole('button', { name: 'Still reviewed' })).not.toBeInTheDocument();
    expect(within(rowOf('10.8.0.4')).getByRole('button', { name: 'Review' })).toBeInTheDocument();
  });

  it('a refused "Still reviewed" says nothing was changed and keeps the row', async () => {
    api.markStillReviewed.mockRejectedValue({ response: { data: { detail: { message: 'Nothing was changed.' } } } });
    renderIt(data([row()]));
    fireEvent.click(screen.getByRole('button', { name: 'Still reviewed' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(onChanged).not.toHaveBeenCalled();
    expect(screen.getByRole('link', { name: '10.8.0.2' })).toBeInTheDocument();
  });

  // Re-opening YOUR review clears its conclusion, which no undo restores
  // exactly: it takes a confirming second click.
  it('re-opening your own review asks once more, then puts it back In Review', async () => {
    renderIt(data([row()]));
    fireEvent.click(screen.getByRole('button', { name: 'Re-open review' }));
    expect(api.followHost).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /Confirm: clears the conclusion/ }));
    await waitFor(() => expect(api.followHost).toHaveBeenCalledWith(21, 'in_review'));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
  });

  it('taking someone else’s reviewed host is one click (theirs stays on record)', async () => {
    renderIt(data([theirs]));
    fireEvent.click(screen.getByRole('button', { name: 'Review' }));
    await waitFor(() => expect(api.followHost).toHaveBeenCalledWith(23, 'in_review'));
  });
});

describe('Changed since review — selection and bulk', () => {
  const many = () => data([
    row(),
    row({ host_id: 31, ip_address: '10.8.1.1' }),
    row({ host_id: 32, ip_address: '10.8.1.2' }),
    openQuestion,
    theirs,
  ]);
  const tick = (ip: string) => fireEvent.click(within(rowOf(ip)).getByRole('checkbox'));

  it('a ticked row looks selected, and "select all" is a dash for some', () => {
    renderIt(many());
    expect(screen.queryByRole('toolbar')).not.toBeInTheDocument();
    tick('10.8.0.2');
    expect(rowOf('10.8.0.2')).toHaveAttribute('data-state', 'selected');
    expect(rowOf('10.8.0.2')).toHaveAttribute('aria-selected', 'true');
    expect(rowOf('10.8.1.1')).toHaveAttribute('aria-selected', 'false');
    const all = screen.getByRole('checkbox', { name: 'Select all rows shown' });
    expect(all).toHaveAttribute('aria-checked', 'mixed');
    expect(screen.getByText('1 review selected')).toBeInTheDocument();
    fireEvent.click(all);
    expect(all).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByText('5 reviews selected')).toBeInTheDocument();
  });

  it('bulk "Still reviewed" is ONE request, for the rows that can be confirmed', async () => {
    renderIt(many());
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all rows shown' }));
    // Five selected; the open question and sam's review cannot be confirmed.
    const still = screen.getByRole('button', { name: 'Still reviewed (3)' });
    expect(still).toHaveAttribute('title', expect.stringContaining('2 of the selected reviews cannot be confirmed'));
    fireEvent.click(still);
    await waitFor(() => expect(api.markStillReviewed).toHaveBeenCalledTimes(1));
    expect(api.markStillReviewed).toHaveBeenCalledWith([21, 31, 32]);
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByRole('toolbar')).not.toBeInTheDocument());
  });

  it('bulk re-open confirms first (it clears conclusions), and reports a partial failure honestly', async () => {
    api.followHost.mockImplementation(async (id: number) => {
      // eslint-disable-next-line @typescript-eslint/no-throw-literal
      if (id === 32) throw { response: { status: 409, data: { detail: 'host is locked' } } };
      return { status: 'in_review' };
    });
    renderIt(many());
    tick('10.8.0.2');
    tick('10.8.1.1');
    tick('10.8.1.2');
    fireEvent.click(screen.getByRole('button', { name: 'Re-open review (3)' }));
    expect(api.followHost).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Click to confirm — clears 3 conclusions' }));
    await waitFor(() => expect(api.followHost).toHaveBeenCalledTimes(3));
    expect(await screen.findByText(/Re-opened 2 of 3 hosts; 1 could not be re-opened \(host is locked\)/)).toBeInTheDocument();
    expect(toast.success).not.toHaveBeenCalled();
    expect(onChanged).toHaveBeenCalled();
  });

  it('a row that leaves the list leaves the selection', () => {
    const { rerender } = renderIt(many());
    tick('10.8.0.2');
    tick('10.8.1.1');
    expect(screen.getByText('2 reviews selected')).toBeInTheDocument();
    rerender(
      <MemoryRouter>
        <LocationProbe />
        <ChangedSinceReviewSection data={data([row({ host_id: 31, ip_address: '10.8.1.1' }), theirs])} canWrite onChanged={onChanged} />
      </MemoryRouter>,
    );
    expect(screen.getByText('1 review selected')).toBeInTheDocument();
  });

  it('x ticks the row under the keyboard cursor, only while this list owns the keys', () => {
    const { rerender } = renderIt(many(), { keysActive: false });
    fireEvent.keyDown(window, { key: 'j' });
    fireEvent.keyDown(window, { key: 'x' });
    expect(screen.queryByRole('toolbar')).not.toBeInTheDocument();
    rerender(
      <MemoryRouter>
        <ChangedSinceReviewSection data={many()} canWrite onChanged={onChanged} keysActive />
      </MemoryRouter>,
    );
    fireEvent.keyDown(window, { key: 'j' });
    fireEvent.keyDown(window, { key: 'j' });
    expect(rowOf('10.8.1.1')).toHaveAttribute('data-list-cursor', 'true');
    fireEvent.keyDown(window, { key: 'x' });
    expect(rowOf('10.8.1.1')).toHaveAttribute('data-state', 'selected');
    // A modifier is somebody else's shortcut.
    fireEvent.keyDown(window, { key: 'x', ctrlKey: true });
    expect(rowOf('10.8.1.1')).toHaveAttribute('data-state', 'selected');
  });
});

describe('Changed since review — a reader', () => {
  it('gets the rows and the links, without checkboxes or actions', () => {
    renderIt(data([row(), theirs]), { canWrite: false });
    expect(screen.getByRole('link', { name: '10.8.0.2' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Open all 2 hosts in Hosts/ })).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: 'Action' })).not.toBeInTheDocument();
    expect(rowOf('10.8.0.2')).not.toHaveAttribute('aria-selected');
  });
});
