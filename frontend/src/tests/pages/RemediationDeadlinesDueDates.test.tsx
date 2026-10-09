/**
 * Managing due dates across projects: one contact reminded once for every
 * project, the search box and the flags on the cross-project list, and — in
 * System settings — what a changed timeline would do, said before saving.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const getRemediationPolicy = vi.fn();
const updateRemediationPolicy = vi.fn();
const previewRemediationPolicy = vi.fn();
const listRemediationProjects = vi.fn();
const listRemediationOverview = vi.fn();
const listRemediationContacts = vi.fn();
const getRemediationFollowUp = vi.fn();
const recordRemediationFollowUp = vi.fn();
const recordRemediationFollowUpOverview = vi.fn();
vi.mock('../../services/api', () => ({
  getRemediationPolicy: (...a: unknown[]) => getRemediationPolicy(...a),
  updateRemediationPolicy: (...a: unknown[]) => updateRemediationPolicy(...a),
  previewRemediationPolicy: (...a: unknown[]) => previewRemediationPolicy(...a),
  listRemediationProjects: (...a: unknown[]) => listRemediationProjects(...a),
  listRemediationOverview: (...a: unknown[]) => listRemediationOverview(...a),
  listRemediationContacts: (...a: unknown[]) => listRemediationContacts(...a),
  getRemediationFollowUp: (...a: unknown[]) => getRemediationFollowUp(...a),
  recordRemediationFollowUp: (...a: unknown[]) => recordRemediationFollowUp(...a),
  recordRemediationFollowUpOverview: (...a: unknown[]) => recordRemediationFollowUpOverview(...a),
  listRemediationTeams: vi.fn().mockResolvedValue([]),
  getRemediationTrend: vi.fn().mockResolvedValue({ as_of: '2026-11-10', days: 90, daily: [], closed_by_month: [] }),
  applyRemediation: vi.fn(),
  listRemediation: vi.fn(),
  listRemediationEvents: vi.fn().mockResolvedValue({ items: [], total: 0, has_more: false }),
  addRemediationNote: vi.fn(),
  deleteRemediationNote: vi.fn(),
}));
// The project selected in the app (77) is none of the rows': this page reads
// and writes each row through ITS project, never the selected one.
const projectCtx = vi.hoisted(() => ({
  currentProject: { id: 77, name: 'Selected' },
  projects: [] as Array<{ id: number; name: string }>, selectProject: vi.fn(),
}));
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => projectCtx }));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));

import RemediationDeadlines from '../../pages/RemediationDeadlines';
import RemediationSettingsSection, {
  PREVIEW_DELAY_MS, policyEffect,
} from '../../components/remediation/RemediationSettingsSection';
import { TooltipProvider } from '../../components/ui/tooltip';
import { resetRemediationPolicy } from '../../hooks/useRemediationPolicy';
import { formatDate } from '../../utils/relativeTime';
import type { RemediationPage, RemediationRow, RemediationState } from '../../services/api';

const POLICY = { enabled: true, due_soon_days: 7, time_zone: 'UTC', days: { critical: 30, high: 30, medium: 90, low: 120, info: null } };
const states = (over: Partial<Record<RemediationState, number>> = {}): Record<RemediationState, number> => ({
  overdue: 0, due_soon: 0, on_track: 0, not_assigned: 0, no_deadline: 0, deferred: 0, closed: 0, ...over,
});
const row = (id: number, projectId: number, projectName: string, over: Partial<RemediationRow> = {}): RemediationRow => ({
  finding_host_id: id, finding_id: 10, project_id: projectId, project_name: projectName, finding_title: `Finding ${id}`,
  severity: 'high', finding_status: 'confirmed', endpoint_status: 'open', host_id: 100 + id,
  ip_address: `10.0.0.${id}`, hostname: null, contact_email: 'roger@example.com', contact_name: null, team: null,
  notified_on: '2026-10-01', status: 'open', closed_on: null, updated_at: null,
  state: 'overdue', due_on: '2026-10-31', days_left: -10, closed_days_late: null, last_follow_up_on: null,
  verification: null, ...over,
});
const overview = (items: RemediationRow[], extra: Partial<RemediationPage> = {}): RemediationPage => ({
  items, total: items.length, has_more: false, limit: 25, offset: 0,
  status_counts: { open: 8, closed: 3, deferred: 0 },
  verification_counts: { reported_fixed_not_retested: 0, remediated_record_open: 0 },
  state_counts: states({ overdue: 3, not_assigned: 5, closed: 3 }),
  severity_counts: {}, overdue_ages: { '1-7': 0, '8-30': 2, '31-90': 1, '90+': 0 },
  not_followed_up: 0, not_followed_up_days: 7, as_of: '2026-11-10', ...extra,
});
const ROGER = {
  contact_email: 'roger@example.com', contact_name: 'Roger Smith', total: 9, open: 9, overdue: 4, due_soon: 2,
  on_track: 3, deferred: 0, closed: 0, last_follow_up_on: null, projects: 2,
};
const MESSAGE = 'Hello Roger Smith,\n9 findings across 2 projects.\n\nAlpha\n- Finding 1 on 10.0.0.1\n\nOld engagement\n- Finding 2 on 10.0.0.2';
const followUp = (over: Record<string, unknown> = {}) => ({
  contact_email: 'roger@example.com', contact_name: 'Roger Smith', as_of: '2026-11-10', overdue: 4, due_soon: 2,
  upcoming: 0, items: [row(1, 4, 'Alpha'), row(2, 9, 'Old engagement')], has_more: false, project_ids: [4, 9],
  text: MESSAGE, ...over,
});

const show = (path = '/remediation-deadlines') => render(
  <TooltipProvider><MemoryRouter initialEntries={[path]}><RemediationDeadlines /></MemoryRouter></TooltipProvider>,
);

beforeEach(() => {
  [getRemediationPolicy, updateRemediationPolicy, previewRemediationPolicy, listRemediationProjects, listRemediationOverview,
    listRemediationContacts, getRemediationFollowUp, recordRemediationFollowUp, recordRemediationFollowUpOverview]
    .forEach((m) => m.mockReset());
  Object.values(toast).forEach((m) => m.mockReset());
  resetRemediationPolicy();
  projectCtx.projects = [{ id: 4, name: 'Alpha' }];
  getRemediationPolicy.mockResolvedValue(POLICY);
  listRemediationProjects.mockResolvedValue({
    items: [
      { project_id: 4, name: 'Alpha', archived: false, states: states({ overdue: 2, not_assigned: 5 }) },
      { project_id: 9, name: 'Old engagement', archived: true, states: states({ overdue: 1, closed: 3 }) },
    ],
    totals: states({ overdue: 3, not_assigned: 5, closed: 3 }), as_of: '2026-11-10', policy: POLICY,
  });
  listRemediationOverview.mockResolvedValue(overview([row(1, 4, 'Alpha'), row(2, 9, 'Old engagement')]));
  listRemediationContacts.mockResolvedValue([ROGER]);
  getRemediationFollowUp.mockResolvedValue(followUp());
});
afterEach(() => { vi.useRealTimers(); });

describe('H — one contact, several projects', () => {
  const open = async (path = '/remediation-deadlines?view=contacts') => {
    show(path);
    fireEvent.click(await screen.findByRole('button', { name: 'Follow up' }));
    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(within(dialog).getByLabelText('Message')).toHaveValue(MESSAGE));
    return dialog;
  };

  it('shows the server’s message, grouped by project, as it is — and records it once for every project', async () => {
    recordRemediationFollowUpOverview.mockResolvedValue({ projects: 2, recorded: 9, already_today: 0 });
    const dialog = await open();
    // No project (null), every project ('all').
    expect(getRemediationFollowUp.mock.calls[0].slice(0, 3)).toEqual([null, 'roger@example.com', 'all']);
    expect(within(dialog).getByText(/^4 overdue and 2 due soon across 2 projects\. Copy the message/)).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText('What was said (optional)'), { target: { value: 'Mailed' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Record follow-up' }));
    await waitFor(() => expect(recordRemediationFollowUpOverview).toHaveBeenCalledTimes(1));
    expect(recordRemediationFollowUpOverview).toHaveBeenCalledWith({
      contact_email: 'roger@example.com', followed_up_on: '2026-11-10', note: 'Mailed',
    });
    expect(recordRemediationFollowUp).not.toHaveBeenCalled();
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Recorded for 9 findings in 2 projects.'));
  });

  it('carries the horizon the reminder listed into what is recorded', async () => {
    getRemediationFollowUp.mockImplementation(async (_p: unknown, _e: string, _s: unknown, _sig: unknown, ahead = 0) =>
      followUp({ upcoming: ahead > 0 ? 3 : 0 }));
    recordRemediationFollowUpOverview.mockResolvedValue({ projects: 1, recorded: 1, already_today: 8 });
    const dialog = await open();
    fireEvent.keyDown(within(dialog).getByRole('combobox', { name: 'Remind about' }), { key: 'Enter' });
    fireEvent.click(await screen.findByRole('option', { name: 'Overdue, due soon, and due in the next 90 days' }));
    await waitFor(() => expect(within(dialog)
      .getByText(/^4 overdue, 2 due soon and 3 due in the next 90 days across 2 projects\./)).toBeInTheDocument());
    expect(getRemediationFollowUp.mock.calls[1][4]).toBe(90);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Record follow-up' }));
    await waitFor(() => expect(recordRemediationFollowUpOverview).toHaveBeenCalledWith({
      contact_email: 'roger@example.com', followed_up_on: '2026-11-10', upcoming_days: 90,
    }));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Recorded for 1 finding in 1 project.'));
  });

  it('with one project chosen, records in that project only', async () => {
    getRemediationFollowUp.mockResolvedValue(followUp({ project_ids: [9] }));
    recordRemediationFollowUp.mockResolvedValue({ recorded: 2, already_recorded: 0, followed_up_on: '2026-11-10', finding_host_ids: [] });
    const dialog = await open('/remediation-deadlines?view=contacts&project=9');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Record follow-up' }));
    await waitFor(() => expect(recordRemediationFollowUp).toHaveBeenCalledTimes(1));
    // The chosen project first, through the mount that serves archived ones.
    expect(recordRemediationFollowUp.mock.calls[0][0]).toBe(9);
    expect(recordRemediationFollowUp.mock.calls[0][2]).toBe('overview');
    expect(getRemediationFollowUp.mock.calls[0].slice(0, 3)).toEqual([9, 'roger@example.com', 'all']);
    expect(recordRemediationFollowUpOverview).not.toHaveBeenCalled();
  });

  it('a server without the cross-project route is asked one project at a time, as before', async () => {
    recordRemediationFollowUpOverview.mockRejectedValue({ response: { status: 405 } });
    recordRemediationFollowUp.mockResolvedValue({ recorded: 2, already_recorded: 0, followed_up_on: '2026-11-10', finding_host_ids: [] });
    const dialog = await open();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Record follow-up' }));
    await waitFor(() => expect(recordRemediationFollowUp).toHaveBeenCalledTimes(2));
    expect(recordRemediationFollowUp.mock.calls.map((call) => call[0])).toEqual([4, 9]);
    expect(recordRemediationFollowUp.mock.calls.map((call) => call[2])).toEqual(['overview', 'overview']);
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Follow-up recorded on 4 findings on hosts.'));
  });

  it('a refusal is said, and nothing is recorded project by project behind it', async () => {
    recordRemediationFollowUpOverview.mockRejectedValue({ response: { status: 422, data: { detail: 'followed_up_on is in the future' } } });
    const dialog = await open();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Record follow-up' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(recordRemediationFollowUp).not.toHaveBeenCalled();
  });

  it('says a document is one project’s without making the reminder sound as if it needed one', async () => {
    show('/remediation-deadlines?view=contacts');
    expect(await screen.findByText(
      'Follow up covers every project. A contact’s list as a document is one project’s: choose a project above to prepare one.',
    )).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Follow up' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Document' })).not.toBeInTheDocument();
  });
});

describe('the cross-project list', () => {
  it('searches, opens a flag from an alert’s link, and says a hand-set date and a deferral’s review', async () => {
    listRemediationOverview.mockResolvedValue(overview([
      row(1, 4, 'Alpha', { state: 'on_track', days_left: 12, due_on: '2026-11-22', due_override_on: '2026-11-22',
        policy_due_on: '2026-10-31', deadline_source: 'override' }),
      row(2, 9, 'Old engagement', { status: 'deferred', state: 'deferred', due_on: null, days_left: null,
        deferred_review_on: '2026-11-01', deferral_review_due: true }),
    ], { flag_counts: { deferral_review_due: 1, deadline_overridden: 1 } }));
    show('/remediation-deadlines?flag=deferral_review_due&project=9&q=smb');
    const list = await screen.findByRole('table', { name: /remediation deadlines$/i });
    expect(listRemediationOverview.mock.calls[0][0]).toMatchObject({ flag: 'deferral_review_due', project_id: 9, q: 'smb' });
    expect(screen.getByRole('searchbox', { name: 'Search by finding or host' })).toHaveValue('smb');
    expect(screen.getByRole('button', { name: 'Deferrals to review: remove this filter' })).toBeInTheDocument();
    const [first, second] = within(list).getAllByRole('row').slice(1);
    expect(within(first).getByText(`set by hand · policy ${formatDate('2026-10-31')}`)).toBeInTheDocument();
    expect(within(second).getByText('Deferred · review due')).toBeInTheDocument();
    // Still text for a project the reader cannot switch to.
    expect(within(second).queryByRole('button', { name: 'Finding 2' })).not.toBeInTheDocument();
    // Starting the clock from a report is one project's: not offered here.
    expect(screen.queryByRole('button', { name: 'Start the clock from a report…' })).not.toBeInTheDocument();
  });

  it('with a server that predates all of this, reads and asks as it always did', async () => {
    show();
    const list = await screen.findByRole('table', { name: /remediation deadlines$/i });
    const [first] = within(list).getAllByRole('row').slice(1);
    expect(within(first).getByText('10 days overdue')).toBeInTheDocument();
    expect(within(first).queryByText(/set by hand|review/)).not.toBeInTheDocument();
    expect(screen.queryByText('Deferrals to review')).not.toBeInTheDocument();
    expect(screen.queryByText('Due date set by hand')).not.toBeInTheDocument();
    const asked = Object.keys(listRemediationOverview.mock.calls[0][0]);
    expect(asked).not.toContain('flag');
    expect(asked).not.toContain('q');
  });
});

describe('F — what a changed timeline would do, before saving', () => {
  const preview = (over: Record<string, unknown> = {}) => ({
    current: states({ overdue: 5, on_track: 20 }), proposed: states({ overdue: 14, on_track: 11 }),
    becomes_overdue: 12, no_longer_overdue: 3, becomes_due_soon: 0, ...over,
  });
  // The second step lets an answer that has arrived reach the page: a query's
  // result is delivered on the next timer tick, not inside the request's promise.
  const settle = async (ms = PREVIEW_DELAY_MS) => {
    await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  };
  const mount = async () => {
    render(<RemediationSettingsSection />);
    await screen.findByLabelText('High');
    vi.useFakeTimers();
  };

  it('appears only on a change, a moment after the last keystroke, with what Save would send', async () => {
    previewRemediationPolicy.mockResolvedValue(preview());
    await mount();
    await settle();
    expect(screen.queryByTestId('ss-remediation-effect')).not.toBeInTheDocument();
    expect(previewRemediationPolicy).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText('High'), { target: { value: '2' } });
    await settle(PREVIEW_DELAY_MS - 100);
    // The next keystroke cancels the question before it is asked.
    fireEvent.change(screen.getByLabelText('High'), { target: { value: '20' } });
    await settle(PREVIEW_DELAY_MS - 100);
    expect(previewRemediationPolicy).not.toHaveBeenCalled();
    await settle(100);
    expect(previewRemediationPolicy).toHaveBeenCalledTimes(1);
    expect(previewRemediationPolicy.mock.calls[0][0]).toEqual({
      days: { critical: 30, high: 20, medium: 90, low: 120, info: null }, due_soon_days: 7,
    });
    expect(screen.getByTestId('ss-remediation-effect')).toHaveTextContent(
      'Saving this makes 12 findings overdue that are not now, and 3 stop being overdue.');
    // Nothing was saved by asking.
    expect(updateRemediationPolicy).not.toHaveBeenCalled();
    // Back to what is saved: nothing to say.
    fireEvent.change(screen.getByLabelText('High'), { target: { value: '30' } });
    await settle();
    expect(screen.queryByTestId('ss-remediation-effect')).not.toBeInTheDocument();
    expect(previewRemediationPolicy).toHaveBeenCalledTimes(1);
  });

  it('an answer for fields that have changed since is never shown', async () => {
    let late: (value: unknown) => void = () => undefined;
    previewRemediationPolicy.mockImplementationOnce(() => new Promise((resolve) => { late = resolve; }));
    previewRemediationPolicy.mockResolvedValue(preview({ becomes_overdue: 0, no_longer_overdue: 0, proposed: states({ overdue: 5, on_track: 20 }) }));
    await mount();
    fireEvent.change(screen.getByLabelText('Warn before'), { target: { value: '14' } });
    await settle();
    expect(previewRemediationPolicy.mock.calls[0][1].aborted).toBe(false);
    fireEvent.change(screen.getByLabelText('Time zone'), { target: { value: 'Europe/Paris' } });
    expect(previewRemediationPolicy.mock.calls[0][1].aborted).toBe(true);        // cancelled on the keystroke
    await settle();
    expect(previewRemediationPolicy.mock.calls[1][0]).toEqual({
      days: { critical: 30, high: 30, medium: 90, low: 120, info: null }, due_soon_days: 14, time_zone: 'Europe/Paris',
    });
    await act(async () => { late(preview()); });
    expect(screen.getByTestId('ss-remediation-effect')).toHaveTextContent('No finding changes state.');
  });

  it('says it could not work out the effect — never silence that reads as "no effect"', async () => {
    previewRemediationPolicy.mockRejectedValue(new Error('boom'));
    await mount();
    fireEvent.change(screen.getByLabelText('High'), { target: { value: '20' } });
    await settle();
    expect(screen.getByTestId('ss-remediation-effect')).toHaveTextContent('Could not work out the effect of this change on open findings.');
    // The change can still be saved.
    expect(screen.getByRole('button', { name: 'Save timelines' })).toBeEnabled();
  });

  it('asks nothing for a value that cannot be saved, or while tracking is off', async () => {
    await mount();
    fireEvent.change(screen.getByLabelText('High'), { target: { value: '0' } });
    await settle();
    expect(previewRemediationPolicy).not.toHaveBeenCalled();
    expect(screen.queryByTestId('ss-remediation-effect')).not.toBeInTheDocument();
    vi.useRealTimers();
    document.body.innerHTML = '';
    getRemediationPolicy.mockResolvedValue({ ...POLICY, enabled: false });
    await mount();
    fireEvent.change(screen.getByLabelText('High'), { target: { value: '20' } });
    await settle();
    expect(previewRemediationPolicy).not.toHaveBeenCalled();
  });

  it('says the effect in one line, whatever moves', () => {
    const none = { current: states({ overdue: 5 }), proposed: states({ overdue: 5 }), becomes_overdue: 0, no_longer_overdue: 0, becomes_due_soon: 0 };
    expect(policyEffect(none)).toBe('No finding changes state.');
    expect(policyEffect({ ...none, becomes_overdue: 1 })).toBe('Saving this makes 1 finding overdue that is not now.');
    expect(policyEffect({ ...none, no_longer_overdue: 3 })).toBe('Saving this stops 3 findings being overdue.');
    expect(policyEffect({ ...none, becomes_due_soon: 2 })).toBe('Saving this makes 2 findings due soon.');
    expect(policyEffect({ ...none, becomes_overdue: 12, no_longer_overdue: 1, becomes_due_soon: 4 }))
      .toBe('Saving this makes 12 findings overdue that are not now, 1 stops being overdue, and 4 become due soon.');
    expect(policyEffect({ ...none, proposed: states({ overdue: 5, no_deadline: 2 }) }))
      .toBe('Saving this makes no finding overdue or due soon; some move between the other states.');
  });
});
