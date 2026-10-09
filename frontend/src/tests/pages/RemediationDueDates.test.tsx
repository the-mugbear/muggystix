/**
 * Managing due dates on the project's Remediation page: a due date set by
 * hand, a deferral with a review date, the two flags, the search box,
 * starting the clock from a report, and how far ahead a reminder looks —
 * and, for each, a server that predates them (none of the new fields).
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, useSearchParams } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const listRemediation = vi.fn();
const exportRemediation = vi.fn();
const applyRemediation = vi.fn();
const getRemediationPolicy = vi.fn();
const listRemediationContacts = vi.fn();
const getRemediationFollowUp = vi.fn();
const recordRemediationFollowUp = vi.fn();
const recordRemediationFollowUpOverview = vi.fn();
const getRemediationTrend = vi.fn();
const listClientReports = vi.fn();
const assignRemediationFromReport = vi.fn();
const saveBlob = vi.hoisted(() => vi.fn());
vi.mock('../../utils/download', () => ({ saveBlob }));
vi.mock('../../services/api', () => ({
  listRemediation: (...a: unknown[]) => listRemediation(...a),
  exportRemediation: (...a: unknown[]) => exportRemediation(...a),
  exportRemediationOverview: vi.fn(),
  applyRemediation: (...a: unknown[]) => applyRemediation(...a),
  getRemediationPolicy: (...a: unknown[]) => getRemediationPolicy(...a),
  listRemediationContacts: (...a: unknown[]) => listRemediationContacts(...a),
  getRemediationFollowUp: (...a: unknown[]) => getRemediationFollowUp(...a),
  recordRemediationFollowUp: (...a: unknown[]) => recordRemediationFollowUp(...a),
  recordRemediationFollowUpOverview: (...a: unknown[]) => recordRemediationFollowUpOverview(...a),
  getRemediationTrend: (...a: unknown[]) => getRemediationTrend(...a),
  listClientReports: (...a: unknown[]) => listClientReports(...a),
  assignRemediationFromReport: (...a: unknown[]) => assignRemediationFromReport(...a),
  listRemediationOverview: vi.fn(),
  listRemediationTeams: vi.fn().mockResolvedValue([]),
  listRemediationEvents: vi.fn().mockResolvedValue({ items: [], total: 0, has_more: false }),
  addRemediationNote: vi.fn(),
  deleteRemediationNote: vi.fn(),
  prepareContactReport: vi.fn(),
  getContactReport: vi.fn(),
  downloadContactReport: vi.fn(),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, role: 'member' }, hasPermission: () => true }),
}));
const projectRole = vi.hoisted(() => ({ value: 'admin' as string }));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'P', my_role: projectRole.value } }),
}));

import Remediation from '../../pages/Remediation';
import { TooltipProvider } from '../../components/ui/tooltip';
import { resetRemediationPolicy } from '../../hooks/useRemediationPolicy';
import { formatDate } from '../../utils/relativeTime';
import type { RemediationPage, RemediationRow } from '../../services/api';

const LONG = 'a'.repeat(200);
const row = (id: number, over: Partial<RemediationRow> = {}): RemediationRow => ({
  finding_host_id: id, finding_id: 10, project_id: 1, project_name: 'P', finding_title: `Finding ${id}`,
  state: 'on_track', due_on: '2026-12-01', days_left: 21, closed_days_late: null, last_follow_up_on: null,
  severity: 'high', finding_status: 'confirmed', endpoint_status: 'open', host_id: 100 + id,
  ip_address: `10.0.0.${id}`, hostname: null, contact_email: 'roger@example.com', contact_name: null, team: null,
  notified_on: '2026-11-01', status: 'open', closed_on: null, updated_at: null, verification: null, ...over,
});
/** A page as a server that predates all of this sends it. */
const oldPage = (items: RemediationRow[], total = items.length): RemediationPage => ({
  items, total, has_more: total > items.length, limit: 25, offset: 0,
  status_counts: { open: 2, closed: 0, deferred: 1 },
  verification_counts: { reported_fixed_not_retested: 0, remediated_record_open: 0 },
  state_counts: { overdue: 0, due_soon: 0, on_track: 2, not_assigned: 3, no_deadline: 0, deferred: 1, closed: 0 },
  severity_counts: {}, overdue_ages: { '1-7': 0, '8-30': 0, '31-90': 0, '90+': 0 },
  not_followed_up: 0, not_followed_up_days: 7, as_of: '2026-11-10',
});
const page = (items: RemediationRow[], flags: RemediationPage['flag_counts'] = { deferral_review_due: 0, deadline_overridden: 0 }) =>
  ({ ...oldPage(items), flag_counts: flags });
const POLICY = { enabled: true, due_soon_days: 7, time_zone: 'UTC', days: { critical: 30, high: 30, medium: 90, low: 120, info: null } };

// (`useLocation` is mocked for every test; the search params are the real ones.)
const Where = () => <output data-testid="where">{useSearchParams()[0].toString()}</output>;
const show = (path = '/remediation') => render(
  <TooltipProvider>
    <MemoryRouter initialEntries={[path]}><Remediation /><Where /></MemoryRouter>
  </TooltipProvider>,
);
const table = () => screen.findByRole('table', { name: /remediation deadlines$/i });
const openEditor = async (name = 'Edit', index = 0) => {
  fireEvent.click(within(await table()).getAllByRole('button', { name })[index]);
  return screen.findByRole('dialog');
};
const choose = async (dialog: HTMLElement, label: string, option: string) => {
  fireEvent.keyDown(within(dialog).getByRole('combobox', { name: label }), { key: 'Enter' });
  fireEvent.click(await screen.findByRole('option', { name: option }));
};
const save = (dialog: HTMLElement) => within(dialog).getByRole('button', { name: 'Save' });

beforeEach(() => {
  projectRole.value = 'admin';
  exportRemediation.mockReset().mockResolvedValue({ items: [], total: 0, limit: 20000, as_of: '2026-11-10' });
  [listRemediation, applyRemediation, getRemediationPolicy, listRemediationContacts, getRemediationFollowUp,
    recordRemediationFollowUp, recordRemediationFollowUpOverview, listClientReports, assignRemediationFromReport, saveBlob]
    .forEach((m) => m.mockReset());
  Object.values(toast).forEach((m) => m.mockReset());
  resetRemediationPolicy();
  getRemediationTrend.mockReset().mockResolvedValue({ as_of: '2026-11-10', days: 90, daily: [], closed_by_month: [] });
  getRemediationPolicy.mockResolvedValue(POLICY);
  listRemediation.mockResolvedValue(page([row(1), row(2)]));
  applyRemediation.mockResolvedValue({
    dry_run: false, overwrite: true, rows: [],
    summary: { targets: 1, changed: 1, unchanged: 0, conflicts: 0, notes_added: 1, notes_already_recorded: 0 },
  });
});
afterEach(() => { vi.useRealTimers(); });

describe('A — a due date set by hand', () => {
  it('shows the date in force and where it comes from, and a different date needs a note', async () => {
    show();
    const dialog = await openEditor();
    expect(within(dialog).getByTestId('rem-due-in-force'))
      .toHaveTextContent(`${formatDate('2026-12-01')} — high, 30 days from ${formatDate('2026-11-01')}`);
    expect(within(dialog).queryByLabelText('Due date')).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Set a different date' }));
    fireEvent.change(within(dialog).getByLabelText('Due date'), { target: { value: '2026-12-20' } });
    // The reason is part of the change: no Save without it.
    expect(within(dialog).getByText(/A due date set by hand needs a note for the timeline/)).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Note for the timeline (required)')).toBeInTheDocument();
    expect(save(dialog)).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText('Note for the timeline (required)'), { target: { value: 'Change window agreed with the owner' } });
    expect(save(dialog)).toBeEnabled();
    fireEvent.click(save(dialog));
    await waitFor(() => expect(applyRemediation).toHaveBeenCalledTimes(1));
    // Only the changed field, as an overwrite, with the note in the same row.
    expect(applyRemediation.mock.calls[0]).toEqual([
      1,
      [{ finding_host_id: 1, due_override_on: '2026-12-20',
        notes: [{ body: 'Change window agreed with the owner', request_key: expect.stringMatching(/:1$/) }] }],
      { overwrite: true },
    ]);
    // The page on screen is re-read in place.
    await waitFor(() => expect(listRemediation.mock.calls.length).toBeGreaterThan(1));
  });

  it('opens on a date already set by hand, and going back to the policy’s date needs a note too', async () => {
    listRemediation.mockResolvedValue(page([
      row(1, { due_on: '2026-12-20', due_override_on: '2026-12-20', policy_due_on: '2026-12-01', deadline_source: 'override' }),
    ], { deferral_review_due: 0, deadline_overridden: 1 }));
    show();
    const dialog = await openEditor();
    expect(within(dialog).getByLabelText('Due date')).toHaveValue('2026-12-20');
    expect(within(dialog).getByText(/Set by hand\. The policy’s: .* — high, 30 days from/)).toBeInTheDocument();
    expect(save(dialog)).toBeDisabled();                                    // nothing changed yet
    fireEvent.click(within(dialog).getByRole('button', { name: 'Use the policy’s date' }));
    expect(within(dialog).getByText(/Going back to the policy’s due date needs a note/)).toBeInTheDocument();
    expect(save(dialog)).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText('Note for the timeline (required)'), { target: { value: 'Extension withdrawn' } });
    fireEvent.click(save(dialog));
    await waitFor(() => expect(applyRemediation).toHaveBeenCalledTimes(1));
    expect(applyRemediation.mock.calls[0][1]).toEqual([
      { finding_host_id: 1, due_override_on: null, notes: [{ body: 'Extension withdrawn', request_key: expect.any(String) }] },
    ]);
  });

  it('a selection that disagrees on the date opens blank and sends nothing about it unless touched', async () => {
    listRemediation.mockResolvedValue(page([
      row(1, { due_on: '2026-12-20', due_override_on: '2026-12-20', policy_due_on: '2026-12-01', deadline_source: 'override' }),
      row(2, { deadline_source: 'policy', policy_due_on: '2026-12-01', due_override_on: null }),
    ], { deferral_review_due: 0, deadline_overridden: 1 }));
    show();
    const list = await table();
    fireEvent.click(within(list).getByRole('checkbox', { name: 'Select Finding 1 on 10.0.0.1' }));
    fireEvent.click(within(list).getByRole('checkbox', { name: 'Select Finding 2 on 10.0.0.2' }));
    fireEvent.click(screen.getByRole('button', { name: 'Assign or update' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByLabelText('Due date')).toHaveValue('');
    expect(within(dialog).getByText(/Left as it is on each row/)).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText('Team'), { target: { value: 'Web' } });
    // An untouched due date asks for no note and is not sent.
    expect(within(dialog).getByLabelText('Note for the timeline (optional)')).toBeInTheDocument();
    fireEvent.click(save(dialog));
    await waitFor(() => expect(applyRemediation).toHaveBeenCalledTimes(1));
    expect(applyRemediation.mock.calls[0][1]).toEqual([
      { finding_host_id: 1, team: 'Web' }, { finding_host_id: 2, team: 'Web' },
    ]);
  });

  it('a selection can be put back on the policy’s date, every row carrying the reason', async () => {
    listRemediation.mockResolvedValue(page([
      row(1, { due_override_on: '2026-12-20', deadline_source: 'override' }),
      row(2, { host_id: 101, due_override_on: '2026-12-24', deadline_source: 'override' }),
    ], { deferral_review_due: 0, deadline_overridden: 2 }));
    show();
    const list = await table();
    fireEvent.click(within(list).getByRole('checkbox', { name: 'Select every row on this page' }));
    fireEvent.click(screen.getByRole('button', { name: 'Assign or update' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Back to the policy’s date' }));
    expect(within(dialog).getByText('Every selected row goes back to the policy’s date.')).toBeInTheDocument();
    expect(save(dialog)).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText('Note for the timeline (required)'), { target: { value: 'Extensions ended' } });
    fireEvent.click(save(dialog));
    await waitFor(() => expect(applyRemediation).toHaveBeenCalledTimes(1));
    // Both findings are on ONE host: the reason still goes in each row.
    const sent = applyRemediation.mock.calls[0][1];
    expect(sent).toHaveLength(2);
    expect(sent.every((r: { due_override_on: unknown; notes?: unknown[] }) => r.due_override_on === null && r.notes?.length === 1)).toBe(true);
    expect(new Set(sent.map((r: { notes: Array<{ request_key: string }> }) => r.notes[0].request_key)).size).toBe(2);
  });

  it('the ONE Deadline cell says a hand-set date in a second phrase, with the policy’s date', async () => {
    listRemediation.mockResolvedValue(page([
      row(1, { due_on: '2026-12-20', due_override_on: '2026-12-20', policy_due_on: '2026-12-01', deadline_source: 'override',
        finding_title: LONG }),
      row(2, { deadline_source: 'policy', policy_due_on: '2026-12-01' }),
    ], { deferral_review_due: 0, deadline_overridden: 1 }));
    show();
    const rows = within(await table()).getAllByRole('row').slice(1);
    const phrase = within(rows[0]).getByText(`set by hand · policy ${formatDate('2026-12-01')}`);
    expect(phrase).toHaveAttribute('title', `This due date was set by hand. The policy’s date is ${formatDate('2026-12-01')}.`);
    expect(phrase.className).toContain('truncate');
    expect(within(rows[1]).queryByText(/set by hand/)).not.toBeInTheDocument();
    // Still one Deadline column.
    expect(within(await table()).getAllByRole('columnheader').map((h) => h.textContent))
      .toEqual(['', 'Deadline', 'Host', 'Finding', 'Contact', 'Assigned', 'Actions']);
  });
});

describe('B — a deferral has a review date and a reason', () => {
  it('asks for "Review on" (today or later, on the SERVER’s day) and a note', async () => {
    show();
    const dialog = await openEditor();
    await choose(dialog, 'Status', 'Deferred');
    const review = within(dialog).getByLabelText('Review on');
    expect(review).toHaveAttribute('min', '2026-11-10');                  // the list's as_of
    expect(within(dialog).getByRole('alert')).toHaveTextContent('A deferral needs a “Review on” date.');
    expect(save(dialog)).toBeDisabled();
    fireEvent.change(review, { target: { value: '2026-11-09' } });
    expect(within(dialog).getByRole('alert')).toHaveTextContent('The “Review on” date must be today or later.');
    fireEvent.change(review, { target: { value: '2026-11-10' } });
    expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument();
    expect(within(dialog).getByText(/A deferral needs a note for the timeline/)).toBeInTheDocument();
    expect(save(dialog)).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText('Note for the timeline (required)'), { target: { value: 'Waiting for the vendor patch' } });
    fireEvent.click(save(dialog));
    await waitFor(() => expect(applyRemediation).toHaveBeenCalledTimes(1));
    expect(applyRemediation.mock.calls[0][1]).toEqual([
      { finding_host_id: 1, status: 'deferred', deferred_review_on: '2026-11-10',
        notes: [{ body: 'Waiting for the vendor patch', request_key: expect.any(String) }] },
    ]);
  });

  it('the Deadline cell says when a deferral is reviewed, that the review is due, or that it has no date', async () => {
    const deferred = { status: 'deferred' as const, state: 'deferred' as const, due_on: null, days_left: null };
    listRemediation.mockResolvedValue(page([
      row(1, { ...deferred, deferred_review_on: '2026-12-01', deferral_review_due: false }),
      row(2, { ...deferred, deferred_review_on: '2026-11-05', deferral_review_due: true }),
      row(3, { ...deferred, deferred_review_on: null, deferral_review_due: true }),
    ], { deferral_review_due: 2, deadline_overridden: 0 }));
    show();
    const rows = within(await table()).getAllByRole('row').slice(1);
    expect(within(rows[0]).getByText(`Deferred · review ${formatDate('2026-12-01')}`).className).not.toContain('text-warning');
    const due = within(rows[1]).getByText('Deferred · review due');
    expect(due.className).toContain('text-warning');
    expect(within(rows[1]).getByText(formatDate('2026-11-05'))).toBeInTheDocument();
    expect(within(rows[2]).getByText('Deferred · no review date').className).toContain('text-warning');
  });

  it('a deferred row with no review date can still have another field edited', async () => {
    listRemediation.mockResolvedValue(page([
      row(1, { status: 'deferred', state: 'deferred', deferred_review_on: null, deferral_review_due: true }),
    ], { deferral_review_due: 1, deadline_overridden: 0 }));
    show();
    const dialog = await openEditor();
    fireEvent.change(within(dialog).getByLabelText('Team'), { target: { value: 'Web' } });
    expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument();
    fireEvent.click(save(dialog));
    await waitFor(() => expect(applyRemediation).toHaveBeenCalledTimes(1));
    expect(applyRemediation.mock.calls[0][1]).toEqual([{ finding_host_id: 1, team: 'Web' }]);
  });
});

describe('C — deferrals to review and due dates set by hand, as openable counts', () => {
  it('each count opens exactly its rows through the address, as a chip that clears', async () => {
    listRemediation.mockResolvedValue(page([row(1)], { deferral_review_due: 4, deadline_overridden: 2 }));
    show('/remediation?state=overdue&severity=high&band=8-30&verification=reported_fixed_not_retested');
    await table();
    fireEvent.click(screen.getByRole('button', { name: '4 deferrals to review: show them' }));
    // Counted before the state and gap filters, so those go; severity stays.
    await waitFor(() => expect(listRemediation).toHaveBeenLastCalledWith(
      1, expect.objectContaining({
        flag: 'deferral_review_due', state: undefined, overdue_band: undefined, verification: undefined,
        severity: 'high', offset: 0,
      }), expect.anything()));
    expect(screen.getByTestId('where')).toHaveTextContent('flag=deferral_review_due');
    expect(screen.getByRole('button', { name: 'About “Deferrals to review”' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '2 due date set by hand: show them' }));
    await waitFor(() => expect(listRemediation).toHaveBeenLastCalledWith(
      1, expect.objectContaining({ flag: 'deadline_overridden' }), expect.anything()));
    fireEvent.click(screen.getByRole('button', { name: 'Due date set by hand: remove this filter' }));
    await waitFor(() => expect(screen.getByTestId('where')).not.toHaveTextContent('flag='));
    expect(listRemediation.mock.calls[listRemediation.mock.calls.length - 1][1]).not.toHaveProperty('flag');
  });

  it('a count of 0 is absent, a value the page does not know is ignored, and the CSV follows the filter', async () => {
    listRemediation.mockResolvedValue(page([row(1)], { deferral_review_due: 0, deadline_overridden: 3 }));
    const first = show('/remediation?flag=deadline_overridden');
    await table();
    expect(listRemediation.mock.calls[0][1]).toMatchObject({ flag: 'deadline_overridden' });
    expect(screen.queryByText('Deferrals to review')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '3 due date set by hand: show them' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(screen.getByRole('button', { name: /CSV/ }));
    await waitFor(() => expect(saveBlob).toHaveBeenCalled());
    expect(exportRemediation).toHaveBeenCalledTimes(1);
    expect(exportRemediation).toHaveBeenCalledWith(1, expect.objectContaining({ flag: 'deadline_overridden' }));
    first.unmount();
    listRemediation.mockClear();
    show('/remediation?flag=everything');
    await table();
    expect(listRemediation.mock.calls[0][1]).not.toHaveProperty('flag');
  });
});

describe('D — search by finding or host', () => {
  it('writes ?q= a moment after typing (two characters or more), replacing the entry, from page 1', async () => {
    listRemediation.mockResolvedValue({ ...page([row(1), row(2)]), total: 80, has_more: true });
    show('/remediation?page=3');
    await table();
    expect(listRemediation).toHaveBeenLastCalledWith(1, expect.objectContaining({ offset: 50 }), expect.anything());
    const box = screen.getByRole('searchbox', { name: 'Search by finding or host' });
    expect(box).toHaveAttribute('placeholder', 'Finding or host…');
    vi.useFakeTimers();
    listRemediation.mockClear();
    fireEvent.change(box, { target: { value: 's' } });
    await act(async () => { await vi.advanceTimersByTimeAsync(400); });
    expect(listRemediation).not.toHaveBeenCalled();                        // one character searches nothing
    expect(box).toHaveValue('s');
    fireEvent.change(box, { target: { value: 'sm' } });
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    fireEvent.change(box, { target: { value: 'smb' } });
    await act(async () => { await vi.advanceTimersByTimeAsync(400); });
    vi.useRealTimers();
    await waitFor(() => expect(listRemediation).toHaveBeenCalled());
    // Debounced: "sm" was never asked for, and the page is the first again.
    expect(listRemediation.mock.calls.every(([, query]) => query.q === 'smb' && query.offset === 0)).toBe(true);
    await waitFor(() => expect(screen.getByTestId('where')).toHaveTextContent(/^q=smb$/));
    // The contact filter is still there.
    expect(screen.getByLabelText('Contact')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Clear the search' }));
    await waitFor(() => expect(screen.getByTestId('where')).not.toHaveTextContent('q='));
  });

  it('reads the search from the address and says what was searched when nothing matches', async () => {
    listRemediation.mockResolvedValue(page([]));
    show(`/remediation?q=${LONG}`);
    expect(await screen.findByText(`No finding or host matches “${LONG}”.`)).toBeInTheDocument();
    expect(listRemediation.mock.calls[0][1]).toMatchObject({ q: LONG });
    expect(screen.getByRole('searchbox', { name: 'Search by finding or host' })).toHaveValue(LONG);
    expect(screen.getByText(/in this selection/)).toBeInTheDocument();
  });

  it('a one-character search in the address is not sent', async () => {
    show('/remediation?q=x');
    await table();
    expect(listRemediation.mock.calls[0][1]).not.toHaveProperty('q');
  });
});

describe('E — start the clock from a report', () => {
  const report = (id: number, over: Record<string, unknown> = {}) => ({
    id, number: id, title: `Report ${id}`, status: 'issued', issued_at: `2026-10-0${id}T09:00:00Z`, ...over,
  });
  const answer = (over: Record<string, unknown> = {}) => ({
    report_id: 2, assigned_on: '2026-10-02', assigned: 42, already_assigned: 11, not_open: 3, not_in_list: 0,
    dry_run: true, ...over,
  });
  const start = () => screen.findByRole('button', { name: 'Start the clock from a report…' });

  it('says the dry run’s answer before anything is written, then writes and re-reads the list in place', async () => {
    listRemediation.mockResolvedValue({ ...page([row(1), row(2)]), total: 80, has_more: true });
    listClientReports.mockResolvedValue({
      items: [report(1), report(2), report(3, { status: 'draft', number: null, issued_at: null }), report(4, { status: 'superseded' })],
      latest_issued_id: 2, can_create: true, can_issue: true,
    });
    assignRemediationFromReport.mockImplementation(async (_project: number, body: { dry_run?: boolean; assigned_on?: string }) =>
      answer({ dry_run: !!body.dry_run, assigned_on: body.assigned_on ?? '2026-10-02', assigned: body.assigned_on === '2026-10-05' ? 40 : 42 }));
    show('/remediation?page=3');
    fireEvent.click(await start());
    const dialog = await screen.findByRole('dialog');
    // The latest issued report, and its issue day as the server gives it.
    await waitFor(() => expect(within(dialog).getByLabelText('Assigned on')).toHaveValue('2026-10-02'));
    expect(within(dialog).getByRole('combobox', { name: 'Issued report' })).toHaveTextContent(/#2 · Report 2 · issued /);
    expect(within(dialog).getByLabelText('Assigned on')).toHaveAttribute('max', '2026-11-10');
    expect(await within(dialog).findByTestId('rem-afr-preview')).toHaveTextContent(
      '42 findings on hosts will get this assigned date. 11 already have one and are left alone. 3 are no longer open.');
    // Only dry runs so far, and only one for the opening.
    expect(assignRemediationFromReport.mock.calls.map(([, body]) => body)).toEqual([{ report_id: 2, dry_run: true }]);
    expect(assignRemediationFromReport.mock.calls.map(([asked]) => asked)).toEqual([1]);
    // Another day is asked about before it can be confirmed.
    fireEvent.change(within(dialog).getByLabelText('Assigned on'), { target: { value: '2026-10-05' } });
    expect(within(dialog).getByRole('button', { name: 'Set the assigned date' })).toBeDisabled();
    await waitFor(() => expect(within(dialog).getByTestId('rem-afr-preview')).toHaveTextContent('40 findings on hosts'));
    expect(assignRemediationFromReport).toHaveBeenLastCalledWith(
      1, { report_id: 2, dry_run: true, assigned_on: '2026-10-05' }, undefined, expect.anything());
    const reads = listRemediation.mock.calls.length;
    fireEvent.click(within(dialog).getByRole('button', { name: 'Set the assigned date' }));
    await waitFor(() => expect(assignRemediationFromReport).toHaveBeenLastCalledWith(1, { report_id: 2, assigned_on: '2026-10-05' }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith(
      'Assigned date set on 40 findings on hosts. Their deadlines are running.'));
    // Re-read where the reader is: the same page, not the first.
    await waitFor(() => expect(listRemediation.mock.calls.length).toBeGreaterThan(reads));
    expect(listRemediation).toHaveBeenLastCalledWith(1, expect.objectContaining({ offset: 50 }), expect.anything());
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('says so when the project has no issued report, with a way to Reports, and writes nothing', async () => {
    listClientReports.mockResolvedValue({
      items: [report(3, { status: 'draft', number: null, issued_at: null })], latest_issued_id: null, can_create: true, can_issue: true,
    });
    show();
    fireEvent.click(await start());
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByText(/This project has no issued report yet/)).toBeInTheDocument();
    expect(within(dialog).getByRole('link', { name: 'Reports' })).toHaveAttribute('href', '/reports');
    expect(within(dialog).queryByRole('button', { name: 'Set the assigned date' })).not.toBeInTheDocument();
    expect(assignRemediationFromReport).not.toHaveBeenCalled();
  });

  it('a dry run that fails says so and cannot be confirmed; nothing to assign cannot either', async () => {
    listClientReports.mockResolvedValue({ items: [report(2)], latest_issued_id: 2, can_create: true, can_issue: true });
    assignRemediationFromReport.mockRejectedValueOnce(new Error('boom'));
    show();
    fireEvent.click(await start());
    const dialog = await screen.findByRole('dialog');
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(/Could not work out what this would change/);
    expect(within(dialog).getByRole('button', { name: 'Set the assigned date' })).toBeDisabled();
    assignRemediationFromReport.mockResolvedValue(answer({ assigned: 0, already_assigned: 1, not_open: 1, not_in_list: 1 }));
    fireEvent.change(within(dialog).getByLabelText('Assigned on'), { target: { value: '2026-10-03' } });
    expect(await within(dialog).findByTestId('rem-afr-preview')).toHaveTextContent(
      'No finding on a host will get an assigned date. 1 already has one and is left alone. 1 is no longer open. 1 is no longer in the remediation list.');
    expect(within(dialog).getByRole('button', { name: 'Set the assigned date' })).toBeDisabled();
  });

  it('is hidden from a reader who cannot write, and when everything is assigned', async () => {
    projectRole.value = 'auditor';
    const first = show();
    await table();
    expect(screen.queryByRole('button', { name: 'Start the clock from a report…' })).not.toBeInTheDocument();
    first.unmount();
    projectRole.value = 'admin';
    const all = page([row(1)]);
    listRemediation.mockResolvedValue({ ...all, state_counts: { ...all.state_counts, not_assigned: 0 } });
    show();
    await table();
    expect(screen.queryByRole('button', { name: 'Start the clock from a report…' })).not.toBeInTheDocument();
  });
});

describe('H — how far ahead a reminder looks', () => {
  const contact = {
    contact_email: 'roger@example.com', contact_name: 'Roger Smith', total: 6, open: 6, overdue: 2, due_soon: 1,
    on_track: 3, deferred: 0, closed: 0, last_follow_up_on: null, projects: 1,
  };
  const followUp = (over: Record<string, unknown> = {}) => ({
    contact_email: 'roger@example.com', contact_name: 'Roger Smith', as_of: '2026-11-10', overdue: 2, due_soon: 1,
    items: [row(1)], has_more: false, project_ids: [1], text: 'Hello Roger Smith', ...over,
  });

  it('reloads the message for the next 30 or 90 days, counts the upcoming ones, and records what was listed', async () => {
    listRemediationContacts.mockResolvedValue([contact]);
    getRemediationFollowUp.mockImplementation(async (_p: unknown, _e: string, _s: unknown, _sig: unknown, ahead = 0) =>
      followUp(ahead > 0 ? { upcoming: 3, text: `Hello Roger Smith\nDue in the next ${ahead} days` } : { upcoming: 0 }));
    recordRemediationFollowUp.mockResolvedValue({ recorded: 6, already_recorded: 0, followed_up_on: '2026-11-10', finding_host_ids: [] });
    show('/remediation?view=contacts');
    fireEvent.click(await screen.findByRole('button', { name: 'Follow up' }));
    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(within(dialog).getByLabelText('Message')).toHaveValue('Hello Roger Smith'));
    expect(within(dialog).getByText(/^2 overdue and 1 due soon\. Copy the message/)).toBeInTheDocument();
    expect(getRemediationFollowUp.mock.calls[0][4]).toBe(0);
    expect(within(dialog).getByRole('combobox', { name: 'Remind about' })).toHaveTextContent('Overdue and due soon');
    await choose(dialog, 'Remind about', 'Overdue, due soon, and due in the next 30 days');
    await waitFor(() => expect(within(dialog).getByLabelText('Message')).toHaveValue('Hello Roger Smith\nDue in the next 30 days'));
    expect(getRemediationFollowUp.mock.calls[1][4]).toBe(30);
    expect(within(dialog).getByText(/^2 overdue, 1 due soon and 3 due in the next 30 days\. Copy the message/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Record follow-up' }));
    await waitFor(() => expect(recordRemediationFollowUp).toHaveBeenCalledTimes(1));
    expect(recordRemediationFollowUp.mock.calls[0][0]).toBe(1);
    expect(recordRemediationFollowUp.mock.calls[0][1]).toEqual({
      contact_email: 'roger@example.com', followed_up_on: '2026-11-10', upcoming_days: 30,
    });
    // One project's follow-up never goes through the cross-project route.
    expect(recordRemediationFollowUpOverview).not.toHaveBeenCalled();
  });

  it('an answer for a horizon the reader has left is never shown', async () => {
    listRemediationContacts.mockResolvedValue([contact]);
    let late: (value: unknown) => void = () => undefined;
    getRemediationFollowUp.mockImplementation((_p: unknown, _e: string, _s: unknown, _sig: unknown, ahead = 0) => (
      ahead === 30 ? new Promise((resolve) => { late = resolve; }) : Promise.resolve(followUp({ text: `horizon ${ahead}` }))));
    show('/remediation?view=contacts');
    fireEvent.click(await screen.findByRole('button', { name: 'Follow up' }));
    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(within(dialog).getByLabelText('Message')).toHaveValue('horizon 0'));
    await choose(dialog, 'Remind about', 'Overdue, due soon, and due in the next 30 days');
    await choose(dialog, 'Remind about', 'Overdue, due soon, and due in the next 90 days');
    await waitFor(() => expect(within(dialog).getByLabelText('Message')).toHaveValue('horizon 90'));
    await act(async () => { late(followUp({ text: 'horizon 30' })); });
    expect(within(dialog).getByLabelText('Message')).toHaveValue('horizon 90');
  });
});

describe('a server that predates all of this', () => {
  it('shows no new phrase, flag or control, and sends the request it always sent', async () => {
    listRemediation.mockResolvedValue(oldPage([
      row(1),
      row(2, { status: 'deferred', state: 'deferred', due_on: null, days_left: null }),
    ]));
    show();
    const rows = within(await table()).getAllByRole('row').slice(1);
    expect(within(rows[0]).getByText('Due in 21 days')).toBeInTheDocument();
    expect(within(rows[0]).queryByText(/set by hand/)).not.toBeInTheDocument();
    expect(within(rows[1]).getByText('Deferred')).toBeInTheDocument();
    expect(within(rows[1]).queryByText(/review/)).not.toBeInTheDocument();
    expect(screen.queryByText('Deferrals to review')).not.toBeInTheDocument();
    expect(screen.queryByText('Due date set by hand')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Deferrals to review and due dates set by hand')).not.toBeInTheDocument();
    // 3 are not assigned, but this server has no route to start the clock from.
    expect(screen.getByText(/due within 7 days/)).toHaveTextContent('3 have not been assigned, so no deadline is running.');
    expect(screen.queryByRole('button', { name: 'Start the clock from a report…' })).not.toBeInTheDocument();
    expect(Object.keys(listRemediation.mock.calls[0][1])).not.toEqual(expect.arrayContaining(['flag']));
    expect(Object.keys(listRemediation.mock.calls[0][1])).not.toEqual(expect.arrayContaining(['q']));
  });

  it('an ordinary edit sends what it always sent, and asks for no note', async () => {
    listRemediation.mockResolvedValue(oldPage([row(1)]));
    show();
    const dialog = await openEditor();
    expect(within(dialog).getByLabelText('Note for the timeline (optional)')).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText('Contact name'), { target: { value: 'R. Smith' } });
    fireEvent.click(save(dialog));
    await waitFor(() => expect(applyRemediation).toHaveBeenCalledTimes(1));
    expect(applyRemediation.mock.calls[0]).toEqual([1, [{ finding_host_id: 1, contact_name: 'R. Smith' }], { overwrite: true }]);
  });
});
