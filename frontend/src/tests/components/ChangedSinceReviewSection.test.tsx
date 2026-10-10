/**
 * "Changed since review" (5.329.0; "Needs another look" until then).  5.330.0 —
 * the reader's own reviews only: no reviewer on the rows, no "Review"
 * (take over a teammate's host) action, and the count opens `follow:revisit`.
 * The tests that pinned a teammate's row ("is not offered on someone else's
 * review", "taking someone else's reviewed host is one click") went with the
 * rows; that such rows never arrive is pinned on the server
 * (`test_changed_since_review_lists_only_the_callers_reviews`).
 *
 * Carries what `MyWorkCardInvestigate.test.tsx` pinned for this queue — it
 * says why each host is back, the reviewer's own review asks once more before
 * it is re-opened, a host opens with the section as its queue, and the footer
 * counts the rows on screen — and adds what the design review asked for: an
 * answer other than re-opening ("Still reviewed"), selection, bulk actions,
 * one-line rows, and a count that opens its exact list.
 *
 * 5.331.0 — it is the content of Operations' "Changed since review" tab: the
 * tab is the heading and carries the count (pinned in `Operations.test.tsx`),
 * the list is one PAGE ("1–10 of N", previous / next — no "3 of 26"), and the
 * panel has the three states every tab has: loading, could not be checked,
 * empty.
 */
import React from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { readsOnScreen } from '../helpers/readsOnScreen';
import ChangedSinceReviewSection from '../../components/operations/ChangedSinceReviewSection';
import type { ReviewFollowupRow, ReviewFollowupsResponse } from '../../services/api';

// The real router (setupTests stubs useLocation): rows are links, and where
// one went is read back from the location.
vi.mock('react-router-dom', async () => vi.importActual<typeof import('react-router-dom')>('react-router-dom'));
const api = vi.hoisted(() => ({ followHost: vi.fn(), unfollowHost: vi.fn(), markStillReviewed: vi.fn() }));
vi.mock('../../services/api', () => api);
// The project the tab is shown in: every request names it first.
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => ({ currentProject: { id: 9 } }) }));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));

let lastLocation: { pathname: string; state: unknown } | null = null;
const LocationProbe: React.FC = () => {
  const loc = useLocation();
  lastLocation = { pathname: loc.pathname, state: loc.state };
  return null;
};

const row = (over: Partial<ReviewFollowupRow> = {}): ReviewFollowupRow => ({
  host_id: 21, ip_address: '10.8.0.2', hostname: 'app01.corp.local',
  reviewed_at: new Date(Date.now() - 3 * 86_400_000).toISOString(),
  review_summary: 'looked at ssh',
  reasons: [{ kind: 'new_ports', text: '3 open ports first seen after the review (80, 445, 8080)' }],
  ...over,
});
// A review with no note.  (It used to be a review that concluded "needs more
// evidence", listed for that alone and refused "Still reviewed"; a review
// records no conclusion now, so every row is here because the host changed.)
const unnoted = row({
  host_id: 22, ip_address: '10.8.0.3', hostname: null, review_summary: null,
  reasons: [{ kind: 'new_ports', text: '1 open port first seen after the review (8443)' }],
});
const observed = row({
  host_id: 23, ip_address: '10.8.0.4', hostname: null,
  reasons: [{ kind: 'new_vulns', text: '1 critical scanner observation recorded after the review' }],
});
const data = (items: ReviewFollowupRow[], over: Partial<ReviewFollowupsResponse> = {}): ReviewFollowupsResponse => ({
  items, total: items.length, ...over,
});

// 5.351.0 — an action no longer calls a parent's `onChanged` (which re-read
// the list and the counts): it says which reads are out of date, and what is
// on screen is read again.  These stand in for the tab's list and the page's
// counts; `reread` says which of them was asked for again.
const { reread, ReadsOnScreen } = readsOnScreen({ getReviewFollowupsPage: 'list', getWorkbench: 'counts' });
const onPage = vi.fn();
const onRetry = vi.fn();
type Props = React.ComponentProps<typeof ChangedSinceReviewSection>;
/** The props the tab's container hands the table for one page of the list. */
const propsFor = (d: ReviewFollowupsResponse, extra: Partial<Props> & { page?: number } = {}): Props => {
  const { page = 0, ...rest } = extra;
  return {
    rows: d.items,
    state: { loading: false, error: null, onRetry },
    pager: { page, pageSize: 25, total: d.total, onPage },
    canWrite: true,
    ...rest,
  };
};
const renderIt = (d: ReviewFollowupsResponse, extra: Partial<Props> & { page?: number } = {}) =>
  render(
    <MemoryRouter>
      <LocationProbe />
      <ChangedSinceReviewSection {...propsFor(d, extra)} />
      <ReadsOnScreen />
    </MemoryRouter>,
  );
const rowOf = (ip: string) => screen.getByRole('link', { name: ip }).closest('tr') as HTMLElement;

beforeEach(() => {
  vi.clearAllMocks();
  lastLocation = null;
  api.followHost.mockResolvedValue({ status: 'in_review' });
  api.markStillReviewed.mockImplementation(async (_projectId: number, ids: number[]) => ({ host_ids: ids }));
});

describe('Changed since review — what it shows', () => {
  it('one line per host: address, name, what changed, how long ago the reader reviewed it', () => {
    renderIt(data([row(), unnoted, observed]));
    // The tab is the heading: the panel repeats none.
    expect(screen.queryByRole('heading')).not.toBeInTheDocument();
    expect(screen.getByRole('table', { name: 'Changed since review' })).toBeInTheDocument();
    expect(screen.getAllByRole('columnheader').map((h) => h.textContent)).toEqual(
      ['', 'Host', 'Name', 'What changed', 'Reviewed', 'Action'],
    );
    const first = rowOf('10.8.0.2');
    expect(within(first).getByText('app01.corp.local')).toBeInTheDocument();
    expect(within(first).getByText('3 open ports first seen after the review (80, 445, 8080)')).toHaveClass('truncate');
    // Every row is the reader's own review: the column is its age, no "by you".
    expect(screen.queryByText(/\bby (you|sam)\b/)).not.toBeInTheDocument();
    expect(screen.queryByText(/reviews are yours/)).not.toBeInTheDocument();
    const age = within(first).getByText('3d');
    // The reader's summary is provenance: on the row's tooltip, not a second line.
    expect(age).toHaveAttribute('title', expect.stringContaining('“looked at ssh”'));
    expect(age).toHaveAttribute('title', expect.stringMatching(/^You reviewed it on /));
    expect(screen.getByText(/A teammate’s reviews are not listed here/)).toBeInTheDocument();
    // Nothing on the page speaks of a review's conclusion any more.
    expect(document.body.textContent).not.toMatch(/needs more evidence|conclu/i);
  });

  it('the footer is the one paging pattern, and its link opens exactly the reader’s Hosts list', () => {
    renderIt(data([row(), unnoted, observed], { total: 53 }), { page: 1 });
    const q = (el: HTMLElement) => new URL(el.getAttribute('href')!, 'https://x').searchParams.get('q');
    // Where the rows on screen sit in the whole list, then previous / next.
    expect(screen.getByText('26–28 of 53')).toBeInTheDocument();
    expect(screen.getByText('page 2 of 3')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Next 25 hosts' }));
    expect(onPage).toHaveBeenLastCalledWith(2);
    fireEvent.click(screen.getByRole('button', { name: 'Previous 25 hosts' }));
    expect(onPage).toHaveBeenLastCalledWith(0);
    // `follow:revisit` — the reader's own; the team-wide query it used to open
    // lists every teammate's reviews.
    expect(q(screen.getByRole('link', { name: 'Open all 53 hosts in Hosts' }))).toBe('follow:revisit');
    // No sample ("3 of 53"), no "more".
    expect(screen.queryByText('3 of 53')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Show .* more/ })).not.toBeInTheDocument();
  });

  it('a list that fits one page has no previous / next', () => {
    renderIt(data([row(), observed]));
    expect(screen.getByText('1–2 of 2')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Next 25/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Previous 25/ })).not.toBeInTheDocument();
  });

  it('opens a host with the way back to this tab, and the page as its queue', () => {
    renderIt(data([row(), unnoted]));
    fireEvent.click(screen.getByRole('link', { name: '10.8.0.2' }));
    expect(lastLocation?.pathname).toBe('/hosts/21');
    expect(lastLocation?.state).toEqual({
      fromOperations: true, hostIds: [21, 22], queueLabel: 'Changed since review', operationsTab: 'changed',
    });
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

  it('with nothing to re-check it says so, and what would put a host here', () => {
    renderIt(data([]));
    expect(screen.getByText(/^Nothing here — a host you reviewed shows when/)).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('while the page loads it is a skeleton — never the empty line', () => {
    renderIt(data([]), { rows: null, state: { loading: true, error: null, onRetry } });
    expect(screen.getByRole('status', { name: /Loading the hosts you reviewed/ })).toBeInTheDocument();
    expect(screen.queryByText(/Nothing here/)).not.toBeInTheDocument();
  });

  it('a failed read says "could not be checked", never "nothing here"', () => {
    renderIt(data([]), { rows: null, state: { loading: false, error: 'HTTP 503', onRetry } });
    expect(screen.getByRole('alert')).toHaveTextContent(/Could not be checked.*HTTP 503.*not an empty list/);
    expect(screen.queryByText(/Nothing here/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(onRetry).toHaveBeenCalled();
  });

  it('a failed refresh keeps the rows that were shown, and says they are as last loaded', () => {
    renderIt(data([row()]), { state: { loading: false, error: 'HTTP 500', onRetry } });
    expect(screen.getByRole('alert')).toHaveTextContent(/could not be refreshed/);
    expect(screen.getByRole('link', { name: '10.8.0.2' })).toBeInTheDocument();
  });
});

describe('Changed since review — answering a change', () => {
  it('"Still reviewed" re-stamps the reader’s own review and refreshes', async () => {
    renderIt(data([row(), observed]));
    fireEvent.click(within(rowOf('10.8.0.2')).getByRole('button', { name: 'Still reviewed' }));
    await waitFor(() => expect(api.markStillReviewed).toHaveBeenCalledWith(9, [21]));
    await waitFor(() => expect(reread).toHaveBeenCalledWith('list'));
    await waitFor(() => expect(reread).toHaveBeenCalledWith('counts'));
    expect(api.followHost).not.toHaveBeenCalled();
  });

  it('both answers are offered on every row', () => {
    renderIt(data([row(), unnoted, observed]));
    for (const ip of ['10.8.0.2', '10.8.0.3', '10.8.0.4']) {
      expect(within(rowOf(ip)).getByRole('button', { name: 'Still reviewed' })).toBeInTheDocument();
      expect(within(rowOf(ip)).getByRole('button', { name: 'Re-open review' })).toBeInTheDocument();
    }
    // There is no "Review" (take over a teammate's host): none is listed.
    expect(screen.queryByRole('button', { name: 'Review' })).not.toBeInTheDocument();
  });

  it('a refused "Still reviewed" says nothing was changed and keeps the row', async () => {
    api.markStillReviewed.mockRejectedValue({ response: { data: { detail: { message: 'Nothing was changed.' } } } });
    renderIt(data([row()]));
    fireEvent.click(screen.getByRole('button', { name: 'Still reviewed' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(reread).not.toHaveBeenCalled();
    expect(screen.getByRole('link', { name: '10.8.0.2' })).toBeInTheDocument();
  });

  // Re-opening YOUR review clears its date and its note, which no undo
  // restores exactly: it takes a confirming second click.
  it('re-opening a review asks once more, then puts it back In Review', async () => {
    renderIt(data([row()]));
    fireEvent.click(screen.getByRole('button', { name: 'Re-open review' }));
    expect(api.followHost).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm: re-open the review' }));
    await waitFor(() => expect(api.followHost).toHaveBeenCalledWith(9, 21, 'in_review'));
    await waitFor(() => expect(reread).toHaveBeenCalledWith('list'));
    await waitFor(() => expect(reread).toHaveBeenCalledWith('counts'));
  });

});

describe('Changed since review — selection and bulk', () => {
  const many = () => data([
    row(),
    row({ host_id: 31, ip_address: '10.8.1.1' }),
    row({ host_id: 32, ip_address: '10.8.1.2' }),
    unnoted,
    observed,
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

  it('bulk "Still reviewed" is ONE request, for every selected row', async () => {
    renderIt(many());
    fireEvent.click(screen.getByRole('checkbox', { name: 'Select all rows shown' }));
    // Five selected, five confirmed: no review is set apart by a conclusion.
    const still = screen.getByRole('button', { name: 'Still reviewed (5)' });
    expect(still).toHaveAttribute('title', expect.not.stringContaining('cannot be confirmed'));
    fireEvent.click(still);
    await waitFor(() => expect(api.markStillReviewed).toHaveBeenCalledTimes(1));
    expect(api.markStillReviewed).toHaveBeenCalledWith(9, [21, 31, 32, 22, 23]);
    await waitFor(() => expect(reread).toHaveBeenCalledWith('list'));
    await waitFor(() => expect(screen.queryByRole('toolbar')).not.toBeInTheDocument());
  });

  it('bulk re-open confirms first (it clears the reviews’ notes), and reports a partial failure honestly', async () => {
    api.followHost.mockImplementation(async (_projectId: number, id: number) => {
      if (id === 32) throw { response: { status: 409, data: { detail: 'host is locked' } } };
      return { status: 'in_review' };
    });
    renderIt(many());
    tick('10.8.0.2');
    tick('10.8.1.1');
    tick('10.8.1.2');
    fireEvent.click(screen.getByRole('button', { name: 'Re-open review (3)' }));
    expect(api.followHost).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Click to confirm — re-opens 3 reviews' }));
    await waitFor(() => expect(api.followHost).toHaveBeenCalledTimes(3));
    expect(await screen.findByText(/Re-opened 2 of 3 hosts; 1 could not be re-opened \(host is locked\)/)).toBeInTheDocument();
    expect(toast.success).not.toHaveBeenCalled();
    await waitFor(() => expect(reread).toHaveBeenCalledWith('list'));
  });

  it('a row that leaves the list leaves the selection', () => {
    const { rerender } = renderIt(many());
    tick('10.8.0.2');
    tick('10.8.1.1');
    expect(screen.getByText('2 reviews selected')).toBeInTheDocument();
    rerender(
      <MemoryRouter>
        <LocationProbe />
        <ChangedSinceReviewSection {...propsFor(data([row({ host_id: 31, ip_address: '10.8.1.1' }), observed]))} />
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
        <ChangedSinceReviewSection {...propsFor(many(), { keysActive: true })} />
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
    renderIt(data([row(), observed]), { canWrite: false });
    expect(screen.getByRole('link', { name: '10.8.0.2' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Open all 2 hosts in Hosts/ })).toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: 'Action' })).not.toBeInTheDocument();
    expect(rowOf('10.8.0.2')).not.toHaveAttribute('aria-selected');
  });
});
