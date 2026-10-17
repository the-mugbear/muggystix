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
const prepareContactReport = vi.fn();
vi.mock('../../services/api', () => ({
  // The defect metrics (owner, 2026-10-14) are not what these tests are about.
  getRemediationDefectMetrics: vi.fn(() => new Promise(() => undefined)),
  prepareContactReport: (...a: unknown[]) => prepareContactReport(...a),
  getContactReport: vi.fn(),
  downloadContactReport: vi.fn(),
  getRemediationPolicy: (...a: unknown[]) => getRemediationPolicy(...a),
  updateRemediationPolicy: (...a: unknown[]) => updateRemediationPolicy(...a),
  previewRemediationPolicy: (...a: unknown[]) => previewRemediationPolicy(...a),
  listRemediationProjects: (...a: unknown[]) => listRemediationProjects(...a),
  listRemediationOverview: (...a: unknown[]) => listRemediationOverview(...a),
  listRemediationContacts: (...a: unknown[]) => Promise.resolve(listRemediationContacts(...a)).then(asCapped),
  getRemediationFollowUp: (...a: unknown[]) => getRemediationFollowUp(...a),
  recordRemediationFollowUp: (...a: unknown[]) => recordRemediationFollowUp(...a),
  recordRemediationFollowUpOverview: (...a: unknown[]) => recordRemediationFollowUpOverview(...a),
  listRemediationTeams: vi.fn().mockResolvedValue({ items: [], total: 0, limit: 200 }),
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
import { asCapped } from '../helpers/cappedList';

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
  on_track: 3, deferred: 0, closed: 0, last_follow_up_on: null, projects: 2, work_project_ids: [] as number[],
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
    listRemediationContacts, getRemediationFollowUp, recordRemediationFollowUp, recordRemediationFollowUpOverview,
    prepareContactReport]
    .forEach((m) => m.mockReset());
  Object.values(toast).forEach((m) => m.mockReset());
  resetRemediationPolicy();
  projectCtx.projects = [{ id: 4, name: 'Alpha' }];
  getRemediationPolicy.mockResolvedValue(POLICY);
  listRemediationProjects.mockResolvedValue({
    items: [
      { project_id: 4, name: 'Alpha', archived: false, states: states({ overdue: 2, not_assigned: 5 }), total: 7, remediated: 0 },
      { project_id: 9, name: 'Old engagement', archived: true, states: states({ overdue: 1, closed: 3 }), total: 4, remediated: 0 },
    ],
    totals: states({ overdue: 3, not_assigned: 5, closed: 3 }), defect_totals: { total: 11, remediated: 0 },
    as_of: '2026-11-10', policy: POLICY,
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

  // Third review 2026-10-15, R5: the fallback for a server without the route
  // (one project at a time, not all-or-nothing) is gone — backend and
  // frontend ship together.  A 405 is an error like any other.
  it('a refused cross-project request is said; nothing is recorded project by project', async () => {
    recordRemediationFollowUpOverview.mockRejectedValue({ response: { status: 405 } });
    const dialog = await open();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Record follow-up' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(recordRemediationFollowUp).not.toHaveBeenCalled();
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
      'Follow up covers every project. A contact’s list as a document is one project’s: Document asks which.',
    )).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Follow up' })).toBeInTheDocument();
    // No project where the contact still has work: nothing to offer.
    expect(screen.queryByRole('button', { name: 'Document' })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Contacts' })).toBeInTheDocument();
  });

  // 5.387.0 — the walkthrough of 2026-10-12: Document used to vanish until a
  // project was chosen in the filter above.
  it('with every project shown, Document asks which project — one document per project', async () => {
    listRemediationContacts.mockResolvedValue([{ ...ROGER, work_project_ids: [4, 9] }]);
    prepareContactReport.mockResolvedValue({
      id: 7, status: 'completed', format: 'contact-docx', message: null, error: null,
      filename: 'remediation-old-engagement-roger-2026-11-10.docx', contact_email: 'roger@example.com',
      created_at: null, images_withheld: 0, findings: 1, without_recommendation: 1, ready: true,
    });
    show('/remediation-deadlines?view=contacts');
    fireEvent.click(await screen.findByRole('button', { name: 'Document' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText(/This contact has open findings in 2 projects: prepare one for each\./)).toBeInTheDocument();
    // Nothing can be prepared for "no project".
    expect(within(dialog).getByRole('button', { name: 'Prepare' })).toBeDisabled();
    fireEvent.keyDown(within(dialog).getByRole('combobox', { name: 'Project' }), { key: 'Enter' });
    fireEvent.click(await screen.findByRole('option', { name: 'Old engagement' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Prepare' }));
    expect(await within(dialog).findByText('remediation-old-engagement-roger-2026-11-10.docx')).toBeInTheDocument();
    // Through the mount that also serves an archived project, in the project chosen.
    expect(prepareContactReport.mock.calls).toEqual([
      [9, { contact_email: 'roger@example.com', format: 'contact-docx' }, 'overview']]);
    expect(within(dialog).getByText(/1 of its 1 finding has no recommendation written/)).toBeInTheDocument();
  });

  it('a contact with work in one project is not asked which', async () => {
    listRemediationContacts.mockResolvedValue([{ ...ROGER, work_project_ids: [4] }]);
    show('/remediation-deadlines?view=contacts');
    fireEvent.click(await screen.findByRole('button', { name: 'Document' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).queryByRole('combobox', { name: 'Project' })).not.toBeInTheDocument();
    expect(dialog).toHaveTextContent('The findings assigned to this contact in Alpha');
    expect(within(dialog).getByRole('button', { name: 'Prepare' })).toBeEnabled();
  });

  it('narrowed to one project, the Projects column still says in how many the contact has findings', async () => {
    listRemediationContacts.mockImplementation(async (projectId: number | null) => (
      projectId == null ? [ROGER] : [{ ...ROGER, projects: 1, work_project_ids: [4] }]));
    show('/remediation-deadlines?view=contacts&project=4');
    const table = await screen.findByRole('table', { name: /contacts/i });
    expect(await within(table).findByText('1 of 2')).toBeInTheDocument();
    expect(listRemediationContacts.mock.calls.map((c) => c.slice(0, 2))).toEqual(
      expect.arrayContaining([[4, 'all'], [null, 'all']]));
    // The chosen project is the document's: no question.
    fireEvent.click(within(table).getByRole('button', { name: 'Document' }));
    expect(await screen.findByRole('dialog')).toHaveTextContent('The findings assigned to this contact in Alpha');
  });

  it('says when further projects have open findings and nothing in the reminder’s window', async () => {
    getRemediationFollowUp.mockResolvedValue(followUp({ other_projects: 1 }));
    const dialog = await open();
    expect(dialog).toHaveTextContent(
      '4 overdue and 2 due soon across 2 projects. 1 more project has open findings for this contact, none overdue or due soon.');
  });

  it('the Projects section counts the rows it lists, not the projects folded under it', async () => {
    listRemediationProjects.mockResolvedValue({
      items: [
        { project_id: 4, name: 'Alpha', archived: false, states: states({ overdue: 2, not_assigned: 5 }), total: 7, remediated: 0 },
        { project_id: 5, name: 'Idle one', archived: false, states: states({ not_assigned: 3 }), total: 3, remediated: 0 },
        { project_id: 6, name: 'Idle two', archived: false, states: states(), total: 0, remediated: 0 },
      ],
      totals: states({ overdue: 2, not_assigned: 8 }), defect_totals: { total: 10, remediated: 0 },
      as_of: '2026-11-10', policy: POLICY,
    });
    show();
    const heading = await screen.findByRole('heading', { name: /^Projects/ });
    await waitFor(() => expect(heading).toHaveTextContent('Projects1'));
    expect(screen.getByText(/Not shown: 2 projects where nothing has been assigned yet/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show them' }));
    expect(heading).toHaveTextContent('Projects3');
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
