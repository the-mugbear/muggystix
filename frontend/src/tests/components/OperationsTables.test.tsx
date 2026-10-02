/**
 * The three personal tabs of Operations (5.331.0): Findings, Hosts, Tests —
 * each ONE table with labelled columns, one line per row, a page at a time.
 *
 * They replace "My work" (`MyWorkCard`, deleted with its test file).  What
 * `MyWorkCard.test.tsx` guarded, and where it is now:
 *  - "says on the row what is owed, and counts need — not ownership" → the
 *    Findings table's Needs column, below (the count: `Operations.test`, and
 *    count == list in `backend/tests/test_operations_tabs.py`);
 *  - "a finding wears the severity ramp; a test's priority does not" → below;
 *  - "the total is the sum of the groups listed; to claim is beside it" →
 *    `operationsTabs.test.ts` (the counts), the Tests table's "which number
 *    is which" line below, the tab label in `Operations.test`;
 *  - "without the server's sum (an older backend) it adds the same groups" →
 *    `operationsTabs.test.ts`;
 *  - "hosts and tests are separate groups, each row naming its kind" → they
 *    are separate tabs with their own columns (below);
 *  - "labels the age column, and says so when no time was recorded" → below,
 *    on every table;
 *  - "a group a page lists exactly opens that list" → the Hosts footer, below;
 *  - "a group no page lists expands in place" → gone with samples: every tab
 *    is paged (`QueueParts` footer, below; `usePagedList.test`), and Findings
 *    / Tests offer no link;
 *  - "carries every host in the group, not only the previewed rows" / "says
 *    the queue is partial" → the Hosts table's navigation state, below;
 *  - "links a task to its test on the host page", "claims with the revision
 *    it was shown", "no Claim on a test already assigned", "a reader is shown
 *    the claimable tests without the Claim", "a 200-character description on
 *    one line" → the Tests table, below;
 *  - "says nothing is waiting, and what would be" → each table's empty line;
 *  - "a failed load is an error with a retry, never an empty queue" → below;
 *  - "links to the reader's own activity" → not ported: the page is the lead,
 *    the tab bar and one list; Collaboration is in the navigation.
 */
import React from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import FindingsNeedingMeTable, { type FindingsNeedingMeTableProps } from '../../components/operations/FindingsNeedingMeTable';
import MyTestsTable, { type MyTestsTableProps } from '../../components/operations/MyTestsTable';
import ReviewHostsTable from '../../components/operations/ReviewHostsTable';
import type { ListState, Pager } from '../../components/operations/QueueParts';
import type { MyAttentionHost, MyFindingItem, MyTaskItem } from '../../services/api';

// The real router (setupTests stubs useLocation): rows are links, and where
// one went is read back from the location.
vi.mock('react-router-dom', async () => vi.importActual<typeof import('react-router-dom')>('react-router-dom'));
const api = vi.hoisted(() => ({ updateHostTest: vi.fn() }));
vi.mock('../../services/api', () => api);
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: { id: 1 } }) }));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));

let lastLocation: { pathname: string; hash: string; state: unknown } | null = null;
const LocationProbe: React.FC = () => {
  const loc = useLocation();
  lastLocation = { pathname: loc.pathname, hash: loc.hash, state: loc.state };
  return null;
};
const inRouter = (ui: React.ReactElement) => render(<MemoryRouter><LocationProbe />{ui}</MemoryRouter>);

const onRetry = vi.fn();
const onPage = vi.fn();
const ok: ListState = { loading: false, error: null, onRetry };
const pager = (total: number, page = 0): Pager => ({ page, pageSize: 25, total, onPage });
const headers = () => screen.getAllByRole('columnheader').map((h) => h.textContent);
const LONG = 'x'.repeat(200);

const finding = (id: number, over: Partial<MyFindingItem> = {}): MyFindingItem => ({
  finding_id: id, title: `Weak TLS ${id}`, severity: 'high', status: 'open', host_id: null,
  host_count: 3, evidence_annotation_id: null, updated_at: new Date(Date.now() - 51 * 60_000).toISOString(),
  needs: [{ kind: 'under_investigation', text: 'under investigation' }], missing_text: [], pending_proposals: 0,
  ...over,
});
const host = (id: number, over: Partial<MyAttentionHost> = {}): MyAttentionHost => ({
  host_id: id, ip_address: `10.9.0.${id}`, hostname: null, follow_status: 'in_review',
  open_port_count: 2, critical_vulns: 0, high_vulns: 0, last_viewed_at: null,
  follow_updated_at: new Date(Date.now() - 5 * 86_400_000).toISOString(),
  ...over,
});
const task = (over: Partial<MyTaskItem> = {}): MyTaskItem => ({
  test_id: 31, tool: 'nxc', description: 'SMB signing', label: 'SMB sweep', revision: 4,
  host_id: 5, host_ip: '10.0.0.5', host_hostname: null, priority: 'high', status: 'proposed',
  rationale: null, updated_at: new Date().toISOString(), reasons: ['triage'], assigned_to_id: null,
  ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  lastLocation = null;
});

// ---------------------------------------------------------------------------
// Findings
// ---------------------------------------------------------------------------

describe('Findings tab', () => {
  const onNeed = vi.fn();
  const table = (
    rows: MyFindingItem[] | null, total: number, state: ListState, page: number,
    over: Partial<FindingsNeedingMeTableProps> = {},
  ) => (
    <FindingsNeedingMeTable
      rows={rows} state={state} pager={pager(total, page)}
      needCounts={{ decide: 3, write: 21 }} need={null} onNeed={onNeed}
      {...over}
    />
  );
  const renderIt = (
    rows: MyFindingItem[] | null, total = rows?.length ?? 0, state = ok, page = 0,
    over: Partial<FindingsNeedingMeTableProps> = {},
  ) => inRouter(table(rows, total, state, page, over));
  const chips = () => within(screen.getByRole('group', { name: 'Filter by what the finding needs' }));

  it('the chips are the two kinds of work, with the server’s counts', () => {
    renderIt([finding(1)], 24);
    expect(chips().getAllByRole('button').map((c) => c.textContent)).toEqual([
      'All 24', 'Needs a decision3', 'Needs report text21',
    ]);
    expect(chips().getByRole('button', { name: /^All/ })).toHaveAttribute('aria-pressed', 'true');
    expect(3 + 21).toBe(24);
  });

  it('a chip narrows the list; a second click, or "All", restores it', () => {
    const { rerender } = renderIt([finding(1)], 24);
    fireEvent.click(screen.getByRole('button', { name: /^Needs report text/ }));
    expect(onNeed).toHaveBeenLastCalledWith('write');
    rerender(<MemoryRouter>{table([finding(1)], 21, ok, 0, { need: 'write' })}</MemoryRouter>);
    const on = screen.getByRole('button', { name: /^Needs report text/ });
    expect(on).toHaveAttribute('aria-pressed', 'true');
    // The footer is the filtered list's size.
    expect(screen.getByText('1–1 of 21')).toBeInTheDocument();
    fireEvent.click(on);
    expect(onNeed).toHaveBeenLastCalledWith(null);
    fireEvent.click(screen.getByRole('button', { name: /^Needs a decision/ }));
    expect(onNeed).toHaveBeenLastCalledWith('decide');
    fireEvent.click(screen.getByRole('button', { name: /^All/ }));
    expect(onNeed).toHaveBeenLastCalledWith(null);
  });

  it('a count that is not known is a dash, never 0', () => {
    renderIt([finding(1)], 1, ok, 0, { needCounts: { decide: null, write: null } });
    expect(chips().getAllByRole('button').map((c) => c.textContent)).toEqual([
      'All —', 'Needs a decision—', 'Needs report text—',
    ]);
  });

  it('a narrowed list with nothing in it says which kind is empty', () => {
    const { unmount } = renderIt([], 0, ok, 0, { need: 'decide', needCounts: { decide: 0, write: 21 } });
    expect(screen.getByText('Nothing here — no finding you own is under investigation or has a proposal waiting for a decision.')).toBeInTheDocument();
    // The chips stay, so the reader can leave the empty filter.
    expect(screen.getByRole('button', { name: /^Needs a decision/ })).toHaveAttribute('aria-pressed', 'true');
    unmount();
    renderIt([], 0, ok, 0, { need: 'write', needCounts: { decide: 3, write: 0 } });
    expect(screen.getByText('Nothing here — no finding you own is only missing required report text.')).toBeInTheDocument();
  });

  it('one table: severity, #, finding, what is owed, how long it has waited', () => {
    renderIt([
      finding(21, { severity: 'critical' }),
      finding(1, {
        needs: [
          { kind: 'missing_text', text: 'report text missing: impact, recommendation' },
          { kind: 'proposals', text: '2 proposals to decide' },
        ],
        host_count: 1, updated_at: null,
      }),
    ], 24);
    expect(headers()).toEqual(['Severity', '#', 'Finding', 'Needs', 'Waiting']);
    expect(screen.getByRole('table')).toHaveClass('table-fixed');
    expect(screen.getByRole('table').parentElement).toHaveClass('overflow-x-auto', 'min-w-0');

    const first = screen.getByRole('link', { name: 'Weak TLS 21' }).closest('tr') as HTMLElement;
    expect(within(first).getByRole('link', { name: 'Weak TLS 21' })).toHaveAttribute('href', '/findings/21');
    expect(first).toHaveTextContent('· 3 hosts');
    expect(within(first).getByText('under investigation')).toBeInTheDocument();
    expect(within(first).getByText('51m')).toHaveAttribute('aria-label', 'waiting 51m');

    const second = screen.getByRole('link', { name: 'Weak TLS 1' }).closest('tr') as HTMLElement;
    // What is owed is said, in the order to act on — which report parts, how many proposals.
    expect(within(second).getByText('report text missing: impact, recommendation · 2 proposals to decide'))
      .toHaveClass('line-clamp-2');
    expect(second).toHaveTextContent('· 1 host');
    // No time recorded: a dash that says so, never an empty cell.
    expect(within(second).getByText('—')).toHaveAttribute('aria-label', 'waiting time not recorded');
    expect(screen.getByText('1–2 of 24')).toBeInTheDocument();
  });

  it('severity wears the severity ramp — never the semantic colours', () => {
    renderIt([finding(1, { severity: 'critical' }), finding(2, { severity: 'low' })]);
    const critical = screen.getByText('critical').closest('.rounded-chip') as HTMLElement;
    expect(critical.className).toMatch(/bg-sev-critical/);
    expect(screen.getByText('low').closest('.rounded-chip')?.className).toMatch(/bg-sev-low/);
    expect(screen.getByRole('table').innerHTML).not.toMatch(/destructive|bg-warning|text-warning/);
  });

  it('a 200-character title stays on one line; the need wraps to two before it is cut', () => {
    renderIt([finding(1, { title: `Title ${LONG}`, needs: [{ kind: 'missing_text', text: `needs ${LONG}` }] })]);
    const title = screen.getByRole('link', { name: `Title ${LONG}` });
    expect(title.closest('td')).toHaveClass('truncate');
    expect(title.closest('td')).toHaveAttribute('title', expect.stringContaining(`Title ${LONG}`));
    // NEEDS is the point of the row (UX walkthrough U5): two lines, never one.
    expect(screen.getByText(`needs ${LONG}`)).toHaveClass('line-clamp-2', 'break-words');
    expect(screen.getByText(`needs ${LONG}`)).not.toHaveClass('truncate');
    expect(screen.getByText(`needs ${LONG}`).closest('td')).not.toHaveClass('truncate');
    expect(screen.getByText(`needs ${LONG}`)).toHaveAttribute('title', `needs ${LONG}`);
  });

  it('has no link to another page: none lists exactly these', () => {
    renderIt([finding(1)], 60);
    expect(screen.getAllByRole('link')).toHaveLength(1);        // the row's own
    expect(screen.getByText('page 1 of 3')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Next 25 findings' }));
    expect(onPage).toHaveBeenCalledWith(1);
  });

  it('empty, loading and failed are three different things', () => {
    const { unmount } = renderIt([]);
    expect(screen.getByText(/^Nothing here — a finding you own shows when it is under investigation/)).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    unmount();

    const loading = renderIt(null, 0, { loading: true, error: null, onRetry });
    expect(screen.getByRole('status', { name: 'Loading the findings that need you…' })).toBeInTheDocument();
    expect(screen.queryByText(/Nothing here/)).not.toBeInTheDocument();
    loading.unmount();

    renderIt(null, 0, { loading: false, error: 'HTTP 500', onRetry });
    expect(screen.getByRole('alert')).toHaveTextContent(/Could not be checked.*HTTP 500.*not an empty list/);
    expect(screen.queryByText(/Nothing here/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(onRetry).toHaveBeenCalled();
  });

  it('j / k move a cursor through the rows, Enter opens the finding', () => {
    renderIt([finding(1), finding(2)]);
    fireEvent.keyDown(window, { key: 'j' });
    fireEvent.keyDown(window, { key: 'j' });
    expect(screen.getByRole('link', { name: 'Weak TLS 2' }).closest('tr')).toHaveAttribute('data-list-cursor', 'true');
    fireEvent.keyDown(window, { key: 'Enter' });
    expect(lastLocation?.pathname).toBe('/findings/2');
  });
});

// ---------------------------------------------------------------------------
// Hosts
// ---------------------------------------------------------------------------

describe('Hosts tab', () => {
  const renderIt = (rows: MyAttentionHost[] | null, total = rows?.length ?? 0, extra: { canWrite?: boolean } = {}) =>
    inRouter(<ReviewHostsTable rows={rows} state={ok} pager={pager(total)} {...extra} />);
  const q = (el: HTMLElement) => new URL(el.getAttribute('href')!, 'https://x').searchParams.get('q');

  it('one table: host, name, open ports, critical, high, how long it has waited', () => {
    renderIt([
      host(1, { hostname: 'dc01.corp.local', open_port_count: 14, critical_vulns: 3, high_vulns: 0 }),
      host(2, { follow_updated_at: null }),
    ], 37);
    expect(headers()).toEqual(['Host', 'Name', 'Open ports', 'Critical', 'High', 'Waiting']);
    expect(screen.getByRole('table')).toHaveClass('table-fixed');
    const first = screen.getByRole('link', { name: '10.9.0.1' }).closest('tr') as HTMLElement;
    expect(within(first).getByText('dc01.corp.local')).toBeInTheDocument();
    expect(within(first).getByText('14')).toBeInTheDocument();
    // A count wears its severity's own colour only when there is one to see.
    expect(within(first).getByLabelText('3 critical scanner observations')).toHaveClass('text-sev-critical');
    expect(within(first).getByLabelText('0 high scanner observations')).toHaveClass('text-muted-foreground');
    expect(within(first).getByText('5d')).toBeInTheDocument();
    const second = screen.getByRole('link', { name: '10.9.0.2' }).closest('tr') as HTMLElement;
    // No name, no recorded time: dashes, never empty cells.
    expect(within(second).getAllByText('—')).toHaveLength(2);
    expect(screen.getByRole('table').innerHTML).not.toMatch(/destructive|bg-warning|text-warning/);
  });

  it('the footer opens exactly the reader’s hosts in Hosts', () => {
    renderIt([host(1), host(2)], 37);
    expect(screen.getByText('1–2 of 37')).toBeInTheDocument();
    expect(q(screen.getByRole('link', { name: 'Open all 37 in Hosts' }))).toBe('follow:mine');
    expect(screen.queryByRole('button', { name: /Show .* more/ })).not.toBeInTheDocument();
  });

  it('opens a host with the way back to this tab, and the page as its queue', () => {
    renderIt([host(1), host(2), host(3)], 37);
    fireEvent.click(screen.getByRole('link', { name: '10.9.0.2' }));
    expect(lastLocation?.pathname).toBe('/hosts/2');
    expect(lastLocation?.state).toEqual({
      fromOperations: true, hostIds: [1, 2, 3], queueLabel: 'Hosts I am reviewing',
      // The list holds more than this page: the host page says so.
      queuePartial: true, operationsTab: 'hosts',
    });
  });

  it('a 200-character hostname stays on the row’s one line', () => {
    renderIt([host(1, { hostname: `${LONG}.corp.local` })]);
    expect(screen.getByTitle(`${LONG}.corp.local`)).toHaveClass('truncate');
  });

  it('has no write control — a review is concluded on the host’s page', () => {
    renderIt([host(1)]);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  });

  it('empty says what would put a host here — in words the reader’s role can act on', () => {
    const { unmount } = renderIt([]);
    expect(screen.getByText(/^Nothing here — a host shows when you take it into review/)).toBeInTheDocument();
    unmount();
    renderIt([], 0, { canWrite: false });
    expect(screen.getByText('Nothing here — a host shows when you have it In Review.')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Tests tab', () => {
  const onKind = vi.fn();
  const onChanged = vi.fn();
  const props = (over: Partial<MyTestsTableProps> = {}): MyTestsTableProps => ({
    rows: [
      task({ test_id: 30, reasons: ['assigned', 'in_review'], assigned_to_id: 1, priority: 'low', label: null }),
      task({ test_id: 31, reasons: ['in_review'], priority: 'critical' }),
      task({ test_id: 32, reasons: ['triage'], updated_at: null }),
    ],
    state: ok,
    pager: pager(55),
    kindCounts: { assigned: 12, in_review: 28, triage: 15 },
    kind: null, onKind, canWrite: true, onChanged,
    ...over,
  });
  const renderIt = (over: Partial<MyTestsTableProps> = {}) => inRouter(<MyTestsTable {...props(over)} />);
  const rowOf = (testId: number) => document.querySelector(`a[href$="#host-test-${testId}"]`)!.closest('tr') as HTMLElement;

  it('ONE table with why each test is here; each test under one kind, its strongest', () => {
    renderIt();
    expect(headers()).toEqual(['Priority', 'Host', 'Test', 'Why it is here', 'Waiting', 'Action']);
    expect(screen.getAllByRole('table')).toHaveLength(1);
    expect(screen.getByRole('table')).toHaveClass('table-fixed');
    // Assigned AND on a host in review: said once, as assigned.
    expect(within(rowOf(30)).getByText('assigned to me')).toBeInTheDocument();
    expect(within(rowOf(31)).getByText('on a host I review')).toBeInTheDocument();
    expect(within(rowOf(32)).getByText('free to claim')).toBeInTheDocument();
    // tool · description, the label leading when there is one.
    expect(within(rowOf(30)).getByRole('link')).toHaveTextContent('nxc · SMB signing');
    expect(within(rowOf(31)).getByRole('link')).toHaveTextContent('SMB sweep · nxc · SMB signing');
    expect(within(rowOf(32)).getByText('—')).toHaveAttribute('aria-label', 'waiting time not recorded');
    expect(screen.getByText('1–3 of 55')).toBeInTheDocument();
  });

  it('says which number is which: yours, and free to claim — not counted as yours', () => {
    renderIt();
    expect(screen.getByText('40 yours')).toBeInTheDocument();
    expect(screen.getByText('15 free to claim')).toBeInTheDocument();
    expect(screen.getByText(/unassigned\s+critical and high priority tests; not counted as yours/)).toBeInTheDocument();
    // The chips: every kind (yours + claimable), then each kind's own count.
    const chips = within(screen.getByRole('group', { name: 'Filter by why the test is here' })).getAllByRole('button');
    expect(chips.map((c) => c.textContent)).toEqual([
      'All 55', 'Assigned to me12', 'On a host I review28', 'Free to claim15',
    ]);
    expect(chips[0]).toHaveAttribute('aria-pressed', 'true');
    expect(12 + 28).toBe(40);
  });

  it('a chip narrows the list; a second click, or "All", restores it', () => {
    const { rerender } = renderIt();
    fireEvent.click(screen.getByRole('button', { name: /^Free to claim/ }));
    expect(onKind).toHaveBeenLastCalledWith('triage');
    rerender(<MemoryRouter><MyTestsTable {...props({ kind: 'triage' })} /></MemoryRouter>);
    const on = screen.getByRole('button', { name: /^Free to claim/ });
    expect(on).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(on);
    expect(onKind).toHaveBeenLastCalledWith(null);
    fireEvent.click(screen.getByRole('button', { name: /^All/ }));
    expect(onKind).toHaveBeenLastCalledWith(null);
  });

  it('a count that is not known is a dash, never 0', () => {
    renderIt({ kindCounts: { assigned: null, in_review: null, triage: null } });
    expect(screen.getByText('— yours')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^All/ })).toHaveTextContent('All —');
  });

  it('a test’s priority is a neutral badge, not a severity colour', () => {
    renderIt();
    const badge = within(rowOf(31)).getByText('critical').closest('.rounded-chip') as HTMLElement;
    expect(badge.className).not.toMatch(/sev-|destructive|warning/);
    expect(badge).toHaveAttribute('title', 'critical priority');
  });

  it('a row opens its test on the host page, with the way back to this tab', () => {
    renderIt();
    fireEvent.click(within(rowOf(31)).getByRole('link'));
    expect(lastLocation?.pathname).toBe('/hosts/5');
    expect(lastLocation?.hash).toBe('#host-test-31');
    expect(lastLocation?.state).toEqual({ fromOperations: true, operationsTab: 'tests' });
  });

  it('claims with the revision it was shown, then refreshes', async () => {
    api.updateHostTest.mockResolvedValue({ revision: 5 });
    renderIt();
    fireEvent.click(within(rowOf(32)).getByRole('button', { name: 'Claim' }));
    await waitFor(() => expect(api.updateHostTest).toHaveBeenCalledWith(32, { assigned_to_id: 1, expected_revision: 4 }));
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    // Undoable: the toast carries the way back, with the NEW revision.
    const undo = toast.success.mock.calls[0][1].action;
    expect(undo.label).toBe('Undo');
    undo.onClick();
    await waitFor(() => expect(api.updateHostTest).toHaveBeenLastCalledWith(32, { assigned_to_id: null, expected_revision: 5 }));
  });

  it('offers Claim only on a test that is free to claim', () => {
    renderIt();
    expect(screen.getAllByRole('button', { name: 'Claim' })).toHaveLength(1);
    expect(within(rowOf(30)).queryByRole('button')).not.toBeInTheDocument();
    expect(within(rowOf(31)).queryByRole('button')).not.toBeInTheDocument();
  });

  it('a reader is shown the claimable tests without the Claim or its column', () => {
    renderIt({ canWrite: false });
    expect(within(rowOf(32)).getByText('free to claim')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Claim' })).not.toBeInTheDocument();
    expect(headers()).toEqual(['Priority', 'Host', 'Test', 'Why it is here', 'Waiting']);
    // The filter chips are not writes.
    expect(screen.getByRole('button', { name: /^Free to claim/ })).toBeInTheDocument();
  });

  it('a test with no label or tool and a 200-character description stays on one line', () => {
    renderIt({ rows: [task({ label: null, tool: null, description: `desc ${LONG}`, host_hostname: `${LONG}.corp.local` })] });
    const link = screen.getByRole('link', { name: `desc ${LONG}` });
    expect(link.closest('td')).toHaveClass('truncate');
    expect(link.closest('td')).toHaveAttribute('title', `desc ${LONG}`);
    expect(screen.getByTitle(`10.0.0.5 · ${LONG}.corp.local`)).toHaveClass('truncate');
  });

  it('empty says what would put a test here; a narrowed list says which kind is empty', () => {
    const { unmount } = renderIt({ rows: [], pager: pager(0), kindCounts: { assigned: 0, in_review: 0, triage: 0 } });
    expect(screen.getByText(/^Nothing here — a test shows when it is assigned to you/)).toBeInTheDocument();
    unmount();
    renderIt({ rows: [], pager: pager(0), kind: 'triage', kindCounts: { assigned: 3, in_review: 0, triage: 0 } });
    expect(screen.getByText('Nothing here — no test is free to claim.')).toBeInTheDocument();
    // The chips stay, to leave the empty kind.
    expect(screen.getByRole('button', { name: /^Assigned to me/ })).toBeInTheDocument();
  });

  it('a failed load is "could not be checked" with a retry, never an empty list', () => {
    renderIt({ rows: null, state: { loading: false, error: 'offline', onRetry } });
    expect(screen.getByRole('alert')).toHaveTextContent(/Could not be checked/);
    expect(screen.queryByText(/Nothing here/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(onRetry).toHaveBeenCalled();
  });
});
