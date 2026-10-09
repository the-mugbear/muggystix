/**
 * 5.335.0 — the Remediation page: one row per finding on a host, filters in
 * the address, write controls for project admins only, and an edit that
 * sends only what changed.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const listRemediation = vi.fn();
const applyRemediation = vi.fn();
const listRemediationEvents = vi.fn();
const addRemediationNote = vi.fn();
const deleteRemediationNote = vi.fn();
const getRemediationPolicy = vi.fn();
const listRemediationContacts = vi.fn();
const getRemediationFollowUp = vi.fn();
const recordRemediationFollowUp = vi.fn();
const listRemediationTeams = vi.fn();
const getRemediationTrend = vi.fn();
const prepareContactReport = vi.fn();
const getContactReport = vi.fn();
const downloadContactReport = vi.fn();
const saveBlob = vi.hoisted(() => vi.fn());
vi.mock('../../utils/download', () => ({ saveBlob }));
vi.mock('../../services/api', () => ({
  prepareContactReport: (...a: unknown[]) => prepareContactReport(...a),
  getContactReport: (...a: unknown[]) => getContactReport(...a),
  downloadContactReport: (...a: unknown[]) => downloadContactReport(...a),
  listRemediationTeams: (...a: unknown[]) => listRemediationTeams(...a),
  getRemediationTrend: (...a: unknown[]) => getRemediationTrend(...a),
  getRemediationPolicy: (...a: unknown[]) => getRemediationPolicy(...a),
  listRemediationContacts: (...a: unknown[]) => listRemediationContacts(...a),
  getRemediationFollowUp: (...a: unknown[]) => getRemediationFollowUp(...a),
  recordRemediationFollowUp: (...a: unknown[]) => recordRemediationFollowUp(...a),
  listRemediationOverview: vi.fn(),
  listRemediation: (...a: unknown[]) => listRemediation(...a),
  applyRemediation: (...a: unknown[]) => applyRemediation(...a),
  listRemediationEvents: (...a: unknown[]) => listRemediationEvents(...a),
  addRemediationNote: (...a: unknown[]) => addRemediationNote(...a),
  deleteRemediationNote: (...a: unknown[]) => deleteRemediationNote(...a),
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
import { LIST_CURSOR_CLASS } from '../../hooks/useListCursor';
import { resetRemediationPolicy } from '../../hooks/useRemediationPolicy';
import { getQueryScope, setQueryScope } from '../../lib/query';
import type { RemediationPage, RemediationRow } from '../../services/api';

const LONG = 'a'.repeat(200);
const row = (id: number, over: Partial<RemediationRow> = {}): RemediationRow => ({
  finding_host_id: id, finding_id: 10, project_id: 1, project_name: 'P', finding_title: `Finding ${id}`,
  state: 'not_assigned', due_on: null, days_left: null, closed_days_late: null, last_follow_up_on: null,
  severity: 'high', finding_status: 'confirmed', endpoint_status: 'open', host_id: 100 + id,
  ip_address: `10.0.0.${id}`, hostname: null, contact_email: null, contact_name: null, team: null,
  notified_on: null, status: 'open', closed_on: null, updated_at: null, verification: null, ...over,
});
const page = (items: RemediationRow[], total = items.length): RemediationPage => ({
  items, total, has_more: total > items.length, limit: 25, offset: 0,
  status_counts: { open: 2, closed: 1, deferred: 0 },
  verification_counts: { reported_fixed_not_retested: 0, remediated_record_open: 0 },
  state_counts: { overdue: 1, due_soon: 0, on_track: 0, not_assigned: 1, no_deadline: 0, deferred: 0, closed: 1 },
  severity_counts: { critical: { overdue: 0, due_soon: 0 }, high: { overdue: 1, due_soon: 0 }, medium: { overdue: 0, due_soon: 0 }, low: { overdue: 0, due_soon: 0 }, info: { overdue: 0, due_soon: 0 } },
  overdue_ages: { '1-7': 0, '8-30': 1, '31-90': 0, '90+': 0 }, not_followed_up: 1, not_followed_up_days: 7,
  as_of: '2026-11-10',
});
const POLICY = { enabled: true, due_soon_days: 7, days: { critical: 30, high: 30, medium: 90, low: 120, info: null } };

const show = (path = '/remediation') => render(
  <MemoryRouter initialEntries={[path]}><Remediation /></MemoryRouter>,
);

beforeEach(() => {
  projectRole.value = 'admin';
  [listRemediation, applyRemediation, listRemediationEvents, addRemediationNote, deleteRemediationNote,
    getRemediationPolicy, listRemediationContacts, getRemediationFollowUp, recordRemediationFollowUp]
    .forEach((m) => m.mockReset());
  resetRemediationPolicy();
  listRemediationTeams.mockReset().mockResolvedValue([]);
  getRemediationTrend.mockReset().mockResolvedValue({ as_of: '2026-11-10', days: 90, daily: [], closed_by_month: [] });
  getRemediationPolicy.mockResolvedValue(POLICY);
  Object.values(toast).forEach((m) => m.mockReset());
  listRemediation.mockResolvedValue(page([
    row(1, { contact_email: 'roger@example.com', contact_name: 'Roger Smith', notified_on: '2026-10-03',
      state: 'overdue', due_on: '2026-11-02', days_left: -8 }),
    row(2, { status: 'closed', state: 'closed', closed_on: '2026-10-06', hostname: `${LONG}.example.com`, finding_title: LONG }),
    row(3),
  ]));
  applyRemediation.mockResolvedValue({
    dry_run: false, overwrite: true, rows: [],
    summary: { targets: 1, changed: 1, unchanged: 0, conflicts: 0, notes_added: 0, notes_already_recorded: 0 },
  });
  listRemediationEvents.mockResolvedValue({ items: [], total: 0, has_more: false });
});

describe('Remediation', () => {
  it('lists a finding on a host with its contact, dates and status, and says what is missing', async () => {
    show();
    const table = await screen.findByRole('table', { name: /remediation/i });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByText('Roger Smith')).toBeInTheDocument();
    expect(within(rows[0]).getByText('roger@example.com')).toBeInTheDocument();
    // ONE Deadline cell: the state and the distance in one phrase, the date
    // under it ("Overdue" beside "8 days overdue" said it twice).
    expect(within(rows[0]).getByText('8 days overdue')).toBeInTheDocument();
    expect(within(rows[0]).queryByText('Overdue')).not.toBeInTheDocument();
    // The record's `closed` is the contact's claim: "Reported fixed", never "Closed".
    expect(within(rows[1]).getByText('Reported fixed')).toBeInTheDocument();
    expect(within(table).queryByText('Closed')).not.toBeInTheDocument();
    expect(within(rows[2]).getByText('Not assigned', { selector: 'span.block' })).toBeInTheDocument();
    expect(within(table).getAllByRole('columnheader').map((h) => h.textContent)).toContain('Deadline');
    expect(within(rows[2]).getByText('No contact yet')).toBeInTheDocument();
    // The lead says what needs someone, from the server's counts.
    const lead = await screen.findByText(/due within 7 days/);
    expect(lead).toHaveTextContent('1 overdue and 0 due within 7 days, of 2 open findings on hosts in this project.');
    expect(lead).toHaveTextContent('1 has not been assigned, so no deadline is running.');
    // The installation's timeline is said once, under the heading.
    expect(screen.getByText(/Critical and High 30 days · Medium 90 days · Low 120 days · Informational no deadline/)).toBeInTheDocument();
  });

  it('leaves the Finding column real room at the table’s minimum width', async () => {
    // 5.335.1 — the fixed columns summed to 59.5rem of a 64rem minimum, so
    // Finding (the one column with no width) was 4.5rem wide in the browser
    // and showed half a severity badge and no title.
    show();
    const table = await screen.findByRole('table', { name: /remediation/i });
    const rem = (cls: string, prefix: string): number | null => {
      const arbitrary = new RegExp(`(?:^|\\s)${prefix}-\\[([\\d.]+)rem\\]`).exec(cls);
      if (arbitrary) return Number(arbitrary[1]);
      const scale = new RegExp(`(?:^|\\s)${prefix}-(\\d+)(?:\\s|$)`).exec(cls);
      return scale ? Number(scale[1]) / 4 : null;
    };
    const minimum = rem(table.className, 'min-w');
    expect(minimum).not.toBeNull();
    const heads = within(table).getAllByRole('columnheader');
    const fixed = heads.map((h) => rem(h.className, 'w'));
    expect(fixed.filter((w) => w === null)).toHaveLength(1);   // Finding alone takes what is left
    const taken = fixed.reduce<number>((sum, w) => sum + (w ?? 0), 0);
    expect(minimum! - taken).toBeGreaterThanOrEqual(9);
  });

  it('keeps the bulk bar’s place before anything is ticked, so the rows do not move', async () => {
    show();
    const table = await screen.findByRole('table', { name: /remediation/i });
    expect(screen.getByText(/Tick rows to assign/)).toBeInTheDocument();
    fireEvent.click(within(table).getByRole('checkbox', { name: 'Select Finding 1 on 10.0.0.1' }));
    expect(screen.queryByText(/Tick rows to assign/)).not.toBeInTheDocument();
    expect(screen.getByText('1 row selected')).toBeInTheDocument();
  });

  it('refuses to save while a date is only half typed', async () => {
    show();
    const table = await screen.findByRole('table', { name: /remediation/i });
    fireEvent.click(within(table).getByRole('button', { name: 'Assign' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Contact email'), { target: { value: 'jane@example.com' } });
    const notified = within(dialog).getByLabelText('Assigned on') as HTMLInputElement;
    // What a browser reports for "10/03/": no value, and badInput.
    Object.defineProperty(notified, 'validity', { configurable: true, value: { badInput: true } });
    fireEvent.blur(notified);
    expect(within(dialog).getByRole('alert')).toHaveTextContent(/only partly filled in/);
    expect(within(dialog).getByRole('button', { name: 'Save' })).toBeDisabled();
    expect(applyRemediation).not.toHaveBeenCalled();
  });

  it('says the feature is off, and asks for nothing, on an installation that did not opt in', async () => {
    getRemediationPolicy.mockResolvedValue({ ...POLICY, enabled: false });
    show();
    expect(await screen.findByText(/not turned on for this installation/)).toBeInTheDocument();
    expect(listRemediation).not.toHaveBeenCalled();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  // Code review 2026-10-09: a failed read of the policy read as "off" for the
  // whole session, with nothing to press.
  it('says that the setting could not be read — not that the feature is off — and Retry reads it', async () => {
    getRemediationPolicy.mockRejectedValueOnce(new Error('Network Error'));
    show();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/Could not reach the server/);
    expect(screen.queryByText(/not turned on for this installation/)).not.toBeInTheDocument();
    expect(listRemediation).not.toHaveBeenCalled();

    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('table', { name: /remediation/i })).toBeInTheDocument();
    expect(getRemediationPolicy).toHaveBeenCalledTimes(2);
  });

  it('previews the deadline before saving, and starts the clock today when a contact is first named', async () => {
    show();
    const table = await screen.findByRole('table', { name: /remediation/i });
    fireEvent.click(within(table).getByRole('button', { name: 'Assign' }));
    const dialog = await screen.findByRole('dialog');
    const assigned = within(dialog).getByLabelText('Assigned on') as HTMLInputElement;
    expect(assigned.value).toBe('');
    fireEvent.change(within(dialog).getByLabelText('Contact email'), { target: { value: 'jane@example.com' } });
    expect(assigned.value).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    fireEvent.change(assigned, { target: { value: '2026-10-05' } });
    // The date in force and where it comes from, in the dialog's Due date part.
    expect(within(dialog).getByTestId('rem-due-in-force')).toHaveTextContent(/ — high, 30 days from /);
  });

  it('by contact: the message is prepared, copied by hand, and the follow-up recorded', async () => {
    listRemediationContacts.mockResolvedValue([
      { contact_email: 'roger@example.com', contact_name: 'Roger Smith', total: 3, open: 3, overdue: 2, due_soon: 1,
        on_track: 0, deferred: 0, closed: 0, last_follow_up_on: null, projects: 1 },
      { contact_email: 'jane@example.com', contact_name: null, total: 1, open: 1, overdue: 0, due_soon: 0,
        on_track: 1, deferred: 0, closed: 0, last_follow_up_on: null, projects: 1 },
    ]);
    getRemediationFollowUp.mockResolvedValue({
      contact_email: 'roger@example.com', contact_name: 'Roger Smith', as_of: '2026-11-10', overdue: 2, due_soon: 1,
      items: [row(1)], has_more: false, project_ids: [1], text: 'Hello Roger Smith,\n- 10.0.0.1: Finding 1',
    });
    recordRemediationFollowUp.mockResolvedValue({ recorded: 3, already_recorded: 0, followed_up_on: '2026-11-10', finding_host_ids: [1] });
    show('/remediation?view=contacts');
    const table = await screen.findByRole('table', { name: /contacts/i });
    const [roger, jane] = within(table).getAllByRole('row').slice(1);
    expect(within(roger).getByText('Not yet')).toBeInTheDocument();
    // Nothing at risk: no follow-up to offer, but a reminder of what is coming.
    expect(within(jane).queryByRole('button', { name: 'Follow up' })).not.toBeInTheDocument();
    expect(within(jane).getByRole('button', { name: 'Remind' })).toBeInTheDocument();
    expect(within(roger).queryByRole('button', { name: 'Remind' })).not.toBeInTheDocument();
    fireEvent.click(within(roger).getByRole('button', { name: 'Follow up' }));
    const dialog = await screen.findByRole('dialog');
    await waitFor(() => expect(within(dialog).getByLabelText('Message')).toHaveValue('Hello Roger Smith,\n- 10.0.0.1: Finding 1'));
    expect(within(dialog).getByText(/2 overdue and 1 due soon\. Copy the message and send it your own way/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Record follow-up' }));
    await waitFor(() => expect(recordRemediationFollowUp).toHaveBeenCalledTimes(1));
    expect(recordRemediationFollowUp.mock.calls[0][0]).toMatchObject({ contact_email: 'roger@example.com' });
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Follow-up recorded on 3 findings on hosts.'));
  });

  it('keeps worst-case text inside its cell', async () => {
    show();
    const table = await screen.findByRole('table', { name: /remediation/i });
    expect(table.className).toContain('table-fixed');
    const link = within(table).getByRole('link', { name: LONG });
    expect(link.className).toContain('line-clamp-2');
    expect(link.className).toContain('break-words');
    expect(link).toHaveAttribute('title', LONG);
  });

  it('reads its filters from the address and sends them', async () => {
    show('/remediation?state=closed&contact=roger&group=contact');
    await waitFor(() => expect(listRemediation).toHaveBeenCalled());
    expect(listRemediation.mock.calls[0][0]).toMatchObject({ state: ['closed'], contact: 'roger', group: 'contact', offset: 0 });
    expect(screen.getByLabelText('Contact')).toHaveValue('roger');
  });

  it('treats a filter value it does not know as the default, not as a request to refuse', async () => {
    show('/remediation?state=fixed&group=severity');
    await waitFor(() => expect(listRemediation).toHaveBeenCalled());
    expect(listRemediation.mock.calls[0][0]).toMatchObject({ state: undefined, group: 'due' });
  });

  it('a status chip narrows the list and starts from the first page', async () => {
    show();
    await screen.findByRole('table', { name: /remediation/i });
    fireEvent.click(screen.getByRole('button', { name: /Reported fixed\s*1/ }));
    await waitFor(() => expect(listRemediation).toHaveBeenLastCalledWith(
      expect.objectContaining({ state: ['closed'], offset: 0 }), expect.anything(),
    ));
  });

  it('says the list could not be loaded, never that it is empty', async () => {
    listRemediation.mockRejectedValue(new Error('boom'));
    show();
    expect(await screen.findByRole('alert')).toHaveTextContent(/could not be loaded/i);
    expect(screen.queryByText(/Nothing here/)).not.toBeInTheDocument();
  });

  it.each(['auditor', 'analyst'])('shows a project %s the list without any write control', async (role) => {
    projectRole.value = role;
    show();
    const table = await screen.findByRole('table', { name: /remediation/i });
    expect(within(table).queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument();
    fireEvent.click(within(table).getByRole('button', { name: 'Timeline for 10.0.0.1' }));
    await waitFor(() => expect(listRemediationEvents).toHaveBeenCalledWith(101, { limit: 50 }, expect.any(AbortSignal), undefined));
    expect(screen.queryByLabelText('Add a note')).not.toBeInTheDocument();
  });

  it('editing one row sends only the field that changed, as an overwrite', async () => {
    show();
    const table = await screen.findByRole('table', { name: /remediation/i });
    fireEvent.click(within(table).getAllByRole('button', { name: 'Edit' })[0]);
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByLabelText('Contact email')).toHaveValue('roger@example.com');
    expect(within(dialog).getByRole('button', { name: 'Save' })).toBeDisabled();   // nothing changed yet
    fireEvent.change(within(dialog).getByLabelText('Contact name'), { target: { value: 'R. Smith' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(applyRemediation).toHaveBeenCalledTimes(1));
    expect(applyRemediation.mock.calls[0]).toEqual([
      [{ finding_host_id: 1, contact_name: 'R. Smith' }], { overwrite: true },
    ]);
    // The page on screen is re-read in place.
    await waitFor(() => expect(listRemediation.mock.calls.length).toBeGreaterThan(1));
  });

  it('a bulk edit touches only the selected rows and leaves fields they differ on alone', async () => {
    show();
    const table = await screen.findByRole('table', { name: /remediation/i });
    fireEvent.click(within(table).getByRole('checkbox', { name: 'Select Finding 1 on 10.0.0.1' }));
    fireEvent.click(within(table).getByRole('checkbox', { name: 'Select Finding 3 on 10.0.0.3' }));
    fireEvent.click(screen.getByRole('button', { name: 'Assign or update' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByLabelText('Contact email')).toHaveValue('');   // they differ
    fireEvent.change(within(dialog).getByLabelText('Assigned on'), { target: { value: '2026-10-05' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(applyRemediation).toHaveBeenCalledTimes(1));
    expect(applyRemediation.mock.calls[0][0]).toEqual([
      { finding_host_id: 1, notified_on: '2026-10-05' },
      { finding_host_id: 3, notified_on: '2026-10-05' },
    ]);
  });

  it('refuses a contact that is not an address before anything is sent', async () => {
    show();
    const table = await screen.findByRole('table', { name: /remediation/i });
    fireEvent.click(within(table).getByRole('button', { name: 'Assign' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Contact email'), { target: { value: 'Roger Smith' } });
    expect(within(dialog).getByRole('alert')).toHaveTextContent(/email address/);
    expect(within(dialog).getByRole('button', { name: 'Save' })).toBeDisabled();
    expect(applyRemediation).not.toHaveBeenCalled();
  });

  it('a failed save says so and keeps the dialog open', async () => {
    applyRemediation.mockRejectedValue(new Error('nope'));
    show();
    const table = await screen.findByRole('table', { name: /remediation/i });
    fireEvent.click(within(table).getByRole('button', { name: 'Assign' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Contact email'), { target: { value: 'jane@example.com' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });

  it('adding a note to a timeline already showing 200 entries re-reads 200, never 201', async () => {
    // External review 2026-10-08: the refresh asked for one more than was
    // shown, the route's ceiling is 200, and the 422 left stale entries and
    // an error under a cleared note field.
    const entry = (id: number) => ({
      id, host_id: 101, kind: 'note' as const, finding_id: null, finding_host_id: null, finding_title: null,
      field: null, from: null, to: null, body: `Note ${id}`, occurred_at: '2026-10-01T09:00:00Z',
      recorded_at: '2026-10-01T09:00:00Z', edited_at: null, author: 'Ana', agent_session_id: null, can_modify: false,
    });
    listRemediationEvents.mockImplementation(async (_host: number, q: { limit: number }) => {
      if (q.limit > 200) throw new Error('422');
      return { items: Array.from({ length: Math.min(q.limit, 260) }, (_, i) => entry(i + 1)), total: 260, has_more: true };
    });
    addRemediationNote.mockResolvedValue({});
    show();
    const table = await screen.findByRole('table', { name: /remediation/i });
    fireEvent.click(within(table).getByRole('button', { name: 'Timeline for 10.0.0.1' }));
    for (let shown = 50; shown < 200; shown += 50) {
      fireEvent.click(await screen.findByRole('button', { name: /Show older entries/ }));
      await screen.findByText(`Note ${shown + 50}`);
    }
    expect(screen.getByText(/The newest 200 of 260 entries/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Add a note'), { target: { value: 'One more' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add note' }));
    await waitFor(() => expect(addRemediationNote).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByLabelText('Add a note')).toHaveValue(''));
    expect(listRemediationEvents.mock.calls.every(([, q]) => q.limit <= 200)).toBe(true);
    expect(listRemediationEvents).toHaveBeenLastCalledWith(101, { limit: 200 }, expect.any(AbortSignal), undefined);
    expect(within(screen.getByRole('dialog')).queryByRole('alert')).not.toBeInTheDocument();
  });

  it('the timeline shows changes and notes, and an admin adds a note to it', async () => {
    listRemediationEvents.mockResolvedValue({
      total: 2, has_more: false,
      items: [
        { id: 2, host_id: 101, kind: 'change', finding_id: 10, finding_host_id: 1, finding_title: 'Finding 1',
          field: 'status', from: 'open', to: 'deferred', body: null, occurred_at: '2026-10-06T10:00:00Z',
          recorded_at: '2026-10-06T10:00:00Z', edited_at: null, author: 'Ana', agent_session_id: 7, can_modify: false },
        { id: 3, host_id: 101, kind: 'change', finding_id: 10, finding_host_id: 1, finding_title: 'Finding 1',
          field: 'contact_email', from: null, to: 'roger@example.com', body: null, occurred_at: '2026-10-06T10:00:00Z',
          recorded_at: '2026-10-06T10:00:00Z', edited_at: null, author: 'Ana', agent_session_id: 7, can_modify: false },
        { id: 1, host_id: 101, kind: 'note', finding_id: null, finding_host_id: null, finding_title: null,
          field: null, from: null, to: null, body: 'Contacted owner, will respond on Friday',
          occurred_at: '2026-10-01T09:00:00Z', recorded_at: '2026-10-03T09:00:00Z', edited_at: null,
          author: 'Ana', agent_session_id: null, can_modify: true },
      ],
    });
    addRemediationNote.mockResolvedValue({});
    show();
    const table = await screen.findByRole('table', { name: /remediation/i });
    fireEvent.click(within(table).getByRole('button', { name: 'Timeline for 10.0.0.1' }));
    expect(await screen.findByText('Contacted owner, will respond on Friday')).toBeInTheDocument();
    expect(screen.getByText(/Open → Deferred/)).toBeInTheDocument();
    expect(screen.getByText(/none → roger@example.com/)).toBeInTheDocument();
    // One save's changes to one finding are ONE entry, its title said once.
    expect(within(screen.getByRole('dialog')).getAllByText('Finding 1')).toHaveLength(1);
    expect(screen.getByText(/through agent session #7/)).toBeInTheDocument();
    expect(screen.getAllByText(/^recorded /)).toHaveLength(1);   // only the backdated note says when it was recorded
    expect(screen.getAllByRole('button', { name: 'Remove' })).toHaveLength(1);   // a change cannot be removed

    fireEvent.change(screen.getByLabelText('Add a note'), { target: { value: 'Owner on leave until Monday' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add note' }));
    await waitFor(() => expect(addRemediationNote).toHaveBeenCalledWith({ host_id: 101, body: 'Owner on leave until Monday' }, undefined));
  });

  it('the timeline says a removed finding in words, with what its record held', async () => {
    const gone = { host_id: 101, kind: 'change', finding_id: null, finding_host_id: null, field: 'finding',
      body: null, edited_at: null, author: 'Ana', agent_session_id: null, can_modify: false };
    listRemediationEvents.mockResolvedValue({
      total: 2, has_more: false,
      items: [
        { ...gone, id: 5, finding_title: 'Weak TLS', from: 'contact Roger Smith roger@example.com, status deferred',
          to: 'removed from this host', occurred_at: '2026-10-07T10:00:00Z', recorded_at: '2026-10-07T10:00:00Z' },
        { ...gone, id: 4, finding_title: 'Old SMB', from: 'status closed on 2026-09-30',
          to: 'finding deleted', occurred_at: '2026-10-06T10:00:00Z', recorded_at: '2026-10-06T10:00:00Z' },
      ],
    });
    show();
    const table = await screen.findByRole('table', { name: /remediation/i });
    fireEvent.click(within(table).getByRole('button', { name: 'Timeline for 10.0.0.1' }));
    const removed = (await screen.findByText('Finding removed from this host')).closest('li')!;
    expect(removed).toHaveTextContent(
      'Finding removed from this host. Its remediation record held: contact Roger Smith roger@example.com, status deferred.');
    expect(screen.getByText('Finding deleted').closest('li')).toHaveTextContent(
      // The stored text says "status closed"; the page says it in today's words.
      'Finding deleted. Its remediation record held: status reported fixed on 2026-09-30.');
    // Not the field-change form ("Finding: … → removed from this host").
    expect(removed).not.toHaveTextContent('→');
    expect(removed).not.toHaveTextContent('Finding:');
  });

  it('a bulk note goes once on each host’s timeline, however many of its findings are ticked', async () => {
    // Five findings on one host stored five identical notes on that host's
    // timeline while the dialog said "each of the 1 host's timelines".
    listRemediation.mockResolvedValue(page([
      row(1, { host_id: 500, ip_address: '10.0.5.0' }), row(2, { host_id: 500, ip_address: '10.0.5.0' }),
      row(3, { host_id: 600, ip_address: '10.0.6.0' }),
    ]));
    applyRemediation.mockResolvedValue({
      dry_run: false, overwrite: true, rows: [],
      summary: { targets: 2, changed: 0, unchanged: 2, conflicts: 0, notes_added: 2, notes_already_recorded: 0 },
    });
    show();
    const table = await screen.findByRole('table', { name: /remediation/i });
    fireEvent.click(within(table).getByRole('checkbox', { name: 'Select every row on this page' }));
    fireEvent.click(screen.getByRole('button', { name: 'Assign or update' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('heading')).toHaveTextContent('Edit 3 findings on 2 hosts');
    fireEvent.change(within(dialog).getByLabelText(/Note for the timeline/), { target: { value: 'Called the owner' } });
    expect(within(dialog).getByText(/added to each of the 2 hosts’ timelines/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(applyRemediation).toHaveBeenCalledTimes(1));
    const sent = applyRemediation.mock.calls[0][0] as Array<{ finding_host_id: number; notes?: unknown[] }>;
    expect(sent.filter((r) => r.notes).map((r) => r.finding_host_id)).toEqual([1, 3]);
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Note added to 2 timelines.'));
  });

  it('an older timeline read that answers late never puts the list back without the new note', async () => {
    const entry = (id: number, body = `Note ${id}`) => ({
      id, host_id: 101, kind: 'note' as const, finding_id: null, finding_host_id: null, finding_title: null,
      field: null, from: null, to: null, body, occurred_at: '2026-10-01T09:00:00Z',
      recorded_at: '2026-10-01T09:00:00Z', edited_at: null, author: 'Ana', agent_session_id: null, can_modify: false,
    });
    const before = Array.from({ length: 120 }, (_, i) => entry(i + 1));
    let landOlder!: (v: unknown) => void;
    listRemediationEvents.mockImplementation((_host: number, q: { limit: number }) => {
      if (q.limit === 100) return new Promise((resolve) => { landOlder = resolve; });   // "Show older entries"
      if (q.limit === 50) return Promise.resolve({ items: before.slice(0, 50), total: 120, has_more: true });
      return Promise.resolve({ items: [entry(999, 'The new note'), ...before.slice(0, q.limit - 1)], total: 121, has_more: true });
    });
    addRemediationNote.mockResolvedValue({});
    show();
    const table = await screen.findByRole('table', { name: /remediation/i });
    fireEvent.click(within(table).getByRole('button', { name: 'Timeline for 10.0.0.1' }));
    fireEvent.click(await screen.findByRole('button', { name: /Show older entries/ }));
    fireEvent.change(screen.getByLabelText('Add a note'), { target: { value: 'The new note' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add note' }));
    expect(await screen.findByText('The new note')).toBeInTheDocument();

    await act(async () => { landOlder({ items: before.slice(0, 100), total: 120, has_more: true }); });
    expect(screen.getByText('The new note')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Show older entries \(70 more\)/ })).toBeInTheDocument();
  });

  it('a note still being saved for one host does not hold the next host’s Add note', async () => {
    addRemediationNote.mockImplementation(() => new Promise(() => {}));
    show();
    const table = await screen.findByRole('table', { name: /remediation/i });
    fireEvent.click(within(table).getByRole('button', { name: 'Timeline for 10.0.0.1' }));
    fireEvent.change(await screen.findByLabelText('Add a note'), { target: { value: 'Slow one' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add note' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Add note' })).toBeDisabled());
    // The reader moves to another host's timeline while the save is in flight.
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    fireEvent.click(within(table).getByRole('button', { name: 'Timeline for 10.0.0.3', hidden: true }));
    await waitFor(() => expect(listRemediationEvents).toHaveBeenCalledWith(103, { limit: 50 }, expect.any(AbortSignal), undefined));
    fireEvent.change(await screen.findByLabelText('Add a note'), { target: { value: 'For the other host' } });
    expect(screen.getByRole('button', { name: 'Add note' })).toBeEnabled();
  });

  it('offers 25, 100 or 200 rows a page, kept in the address', async () => {
    show('/remediation?per=100');
    await screen.findByRole('table', { name: /remediation/i });
    expect(listRemediation.mock.calls[0][0]).toMatchObject({ limit: 100, offset: 0 });
    expect(screen.getByLabelText('Rows per page')).toHaveTextContent('100');
  });

  it('a page size the list does not offer is the default 25', async () => {
    show('/remediation?per=5000');
    await screen.findByRole('table', { name: /remediation/i });
    expect(listRemediation.mock.calls[0][0]).toMatchObject({ limit: 25 });
  });

  it('narrows to a host or a finding from the address, names it, and lets the reader clear it', async () => {
    show('/remediation?host=101&finding=10');
    await screen.findByRole('table', { name: /remediation/i });
    expect(listRemediation.mock.calls[0][0]).toMatchObject({ host_id: 101, finding_id: 10 });
    expect(screen.getByText(/in this selection/)).toBeInTheDocument();
    const chip = screen.getByRole('button', { name: 'Host 10.0.0.1: remove this filter' });
    expect(screen.getByRole('button', { name: 'Finding Finding 1: remove this filter' })).toBeInTheDocument();
    fireEvent.click(chip);
    await waitFor(() => expect(listRemediation).toHaveBeenLastCalledWith(
      expect.objectContaining({ host_id: undefined, finding_id: 10, offset: 0 }), expect.anything(),
    ));
    expect(screen.queryByRole('button', { name: /^Host .*remove this filter/ })).not.toBeInTheDocument();
  });

  it('an id in the address that is not one is ignored, not sent', async () => {
    show('/remediation?host=abc&finding=-4');
    await screen.findByRole('table', { name: /remediation/i });
    expect(listRemediation.mock.calls[0][0]).toMatchObject({ host_id: undefined, finding_id: undefined });
    expect(screen.queryByRole('button', { name: /remove this filter/ })).not.toBeInTheDocument();
  });

  it('j / k move a row cursor and Enter opens the editor for an admin', async () => {
    show();
    const table = await screen.findByRole('table', { name: /remediation/i });
    fireEvent.keyDown(window, { key: 'j' });
    fireEvent.keyDown(window, { key: 'j' });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows[1]).toHaveAttribute('data-list-cursor', 'true');
    expect(rows[1].className).toContain(LIST_CURSOR_CLASS);
    expect(rows[0]).not.toHaveAttribute('data-list-cursor');
    fireEvent.keyDown(window, { key: 'Enter' });
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('heading')).toHaveTextContent('10.0.0.2');
    expect(within(dialog).getByLabelText('Contact email')).toBeInTheDocument();
  });

  it('Enter opens the timeline for someone who cannot edit', async () => {
    projectRole.value = 'auditor';
    show();
    await screen.findByRole('table', { name: /remediation/i });
    fireEvent.keyDown(window, { key: 'j' });
    fireEvent.keyDown(window, { key: 'Enter' });
    await waitFor(() => expect(listRemediationEvents).toHaveBeenCalledWith(101, { limit: 50 }, expect.any(AbortSignal), undefined));
    expect(screen.queryByLabelText('Contact email')).not.toBeInTheDocument();
  });

  it('opens on the page the address names, and a filter starts from the first again', async () => {
    listRemediation.mockResolvedValue(page([row(1), row(2)], 80));
    show('/remediation?page=3');
    await screen.findByRole('table', { name: /remediation deadlines$/i });
    expect(listRemediation).toHaveBeenCalledTimes(1);
    expect(listRemediation).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 50, limit: 25 }), expect.anything());
    fireEvent.click(screen.getByRole('button', { name: '1 high overdue: show them' }));
    await waitFor(() => expect(listRemediation).toHaveBeenLastCalledWith(
      expect.objectContaining({ severity: 'high', offset: 0 }), expect.anything()));
    expect(listRemediation.mock.calls.filter(([q]) => q.severity === 'high').every(([q]) => q.offset === 0)).toBe(true);
  });

  it('answers the manager’s questions above the list, each number opening its rows', async () => {
    show();
    await screen.findByRole('table', { name: /remediation deadlines$/i });
    fireEvent.click(screen.getByRole('button', { name: '1 high overdue: show them' }));
    await waitFor(() => expect(listRemediation).toHaveBeenLastCalledWith(
      expect.objectContaining({ severity: 'high', state: ['overdue'], offset: 0 }), expect.anything()));
    fireEvent.click(screen.getByRole('button', { name: '1 overdue by 8–30 days: show them' }));
    await waitFor(() => expect(listRemediation).toHaveBeenLastCalledWith(
      expect.objectContaining({ overdue_band: '8-30', severity: undefined, state: undefined }), expect.anything()));
    fireEvent.click(screen.getByRole('button', { name: /1 overdue or due soon with no recent follow-up/ }));
    await waitFor(() => expect(listRemediation).toHaveBeenLastCalledWith(
      expect.objectContaining({ no_follow_up_days: 7, overdue_band: undefined }), expect.anything()));
    // The filter is said, and removable.
    expect(screen.getByRole('button', { name: /no follow-up in 7 days: remove this filter/ })).toBeInTheDocument();
  });

  it('by team: a team opens its rows, and the edit dialog sets one', async () => {
    listRemediationTeams.mockResolvedValue([
      { team: 'Platform', total: 4, open: 3, overdue: 2, due_soon: 0, on_track: 1, deferred: 0, closed: 1, contacts: 2, projects: 1 },
      { team: null, total: 1, open: 1, overdue: 0, due_soon: 0, on_track: 1, deferred: 0, closed: 0, contacts: 1, projects: 1 },
    ]);
    show('/remediation?view=teams');
    const table = await screen.findByRole('table', { name: /teams/i });
    expect(within(table).getByText('A contact, no team')).toBeInTheDocument();
    fireEvent.click(within(table).getByRole('button', { name: 'Platform' }));
    await waitFor(() => expect(listRemediation).toHaveBeenLastCalledWith(
      expect.objectContaining({ team: 'Platform' }), expect.anything()));
    const list = await screen.findByRole('table', { name: /remediation deadlines$/i });
    fireEvent.click(within(list).getAllByRole('button', { name: 'Edit' })[0]);
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Team'), { target: { value: 'Web' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(applyRemediation).toHaveBeenCalledTimes(1));
    expect(applyRemediation.mock.calls[0][0]).toEqual([{ finding_host_id: 1, team: 'Web' }]);
  });

  it('starts the clock on the SERVER’s day, not the reader’s calendar', async () => {
    show();
    const table = await screen.findByRole('table', { name: /remediation deadlines$/i });
    fireEvent.click(within(table).getByRole('button', { name: 'Assign' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Contact email'), { target: { value: 'jane@example.com' } });
    expect(within(dialog).getByLabelText('Assigned on')).toHaveValue('2026-11-10');     // the list's as_of
  });

  it('prepares a contact’s list as a document, waits for the worker, and downloads it', async () => {
    listRemediationContacts.mockResolvedValue([
      { contact_email: 'roger@example.com', contact_name: 'Roger Smith', total: 3, open: 3, overdue: 2, due_soon: 1,
        on_track: 0, deferred: 0, closed: 0, last_follow_up_on: null, projects: 1 },
      { contact_email: 'done@example.com', contact_name: null, total: 2, open: 0, overdue: 0, due_soon: 0,
        on_track: 0, deferred: 0, closed: 2, last_follow_up_on: null, projects: 1 },
    ]);
    const job = { id: 7, status: 'queued', format: 'contact-docx', message: null, error: null, filename: null,
      contact_email: 'roger@example.com', created_at: null, images_withheld: 0, ready: false };
    prepareContactReport.mockReset().mockResolvedValue(job);
    getContactReport.mockReset().mockResolvedValue({ ...job, status: 'completed', ready: true, filename: 'remediation-roger-2026-11-10.docx' });
    downloadContactReport.mockReset().mockResolvedValue(new Blob(['x']));
    saveBlob.mockReset();
    show('/remediation?view=contacts');
    const table = await screen.findByRole('table', { name: /contacts/i });
    const [roger, done] = within(table).getAllByRole('row').slice(1);
    // Nothing open: nothing to list.
    expect(within(done).queryByRole('button', { name: 'Document' })).not.toBeInTheDocument();
    fireEvent.click(within(roger).getByRole('button', { name: 'Document' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Prepare' }));
    await waitFor(() => expect(prepareContactReport).toHaveBeenCalledWith(
      { contact_email: 'roger@example.com', format: 'contact-docx' }, undefined));
    expect(await within(dialog).findByText('Waiting for the report worker…')).toBeInTheDocument();
    expect(await within(dialog).findByText('remediation-roger-2026-11-10.docx', undefined, { timeout: 4000 })).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Download' }));
    await waitFor(() => expect(saveBlob).toHaveBeenCalledWith(expect.any(Blob), 'remediation-roger-2026-11-10.docx'));
  });

  // 5.352.0 (owner) — a status poll that fails was silent; the dialog now says
  // the status may be stale (as the inventory download does) and keeps asking.
  it('says the status may be stale when the document cannot be re-read, and carries on', async () => {
    listRemediationContacts.mockResolvedValue([
      { contact_email: 'roger@example.com', contact_name: 'Roger Smith', total: 3, open: 3, overdue: 2, due_soon: 1,
        on_track: 0, deferred: 0, closed: 0, last_follow_up_on: null, projects: 1 },
    ]);
    const job = { id: 9, status: 'queued', format: 'contact-docx', message: null, error: null, filename: null,
      contact_email: 'roger@example.com', created_at: null, images_withheld: 0, ready: false };
    prepareContactReport.mockReset().mockResolvedValue(job);
    getContactReport.mockReset()
      .mockRejectedValueOnce(Object.assign(new Error('boom'), {
        isAxiosError: true, response: { status: 503, data: { detail: 'The server is busy.' } },
      }))
      .mockResolvedValue({ ...job, status: 'completed', ready: true, filename: 'remediation-roger.docx' });
    show('/remediation?view=contacts');
    const table = await screen.findByRole('table', { name: /contacts/i });
    fireEvent.click(within(within(table).getAllByRole('row')[1]).getByRole('button', { name: 'Document' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Prepare' }));

    const stale = await within(dialog).findByText(/Status may be stale/, undefined, { timeout: 4000 });
    expect(stale).toHaveTextContent('Status may be stale — The server is busy.');
    expect(within(dialog).getByText('Waiting for the report worker…')).toBeInTheDocument();

    expect(await within(dialog).findByText('remediation-roger.docx', undefined, { timeout: 8000 })).toBeInTheDocument();
    expect(within(dialog).queryByText(/Status may be stale/)).toBeNull();
  }, 15000);

  // 5.346.0 — the worker's status while it renders is `processing`; the
  // dialog waited on `running`, so a poll that landed mid-render stopped the
  // poll and said "The document could not be prepared." with no reason.
  it('keeps waiting while the worker renders, then says why a render failed', async () => {
    listRemediationContacts.mockResolvedValue([
      { contact_email: 'roger@example.com', contact_name: 'Roger Smith', total: 3, open: 3, overdue: 2, due_soon: 1,
        on_track: 0, deferred: 0, closed: 0, last_follow_up_on: null, projects: 1 },
    ]);
    const job = { id: 7, status: 'queued', format: 'contact-docx', message: null, error: null, filename: null,
      contact_email: 'roger@example.com', created_at: null, images_withheld: 0, ready: false };
    prepareContactReport.mockReset().mockResolvedValue(job);
    getContactReport.mockReset()
      .mockResolvedValueOnce({ ...job, status: 'processing' })
      .mockResolvedValue({ ...job, status: 'failed', error: 'Quarto render failed' });
    show('/remediation?view=contacts');
    const table = await screen.findByRole('table', { name: /contacts/i });
    fireEvent.click(within(table).getByRole('button', { name: 'Document' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Prepare' }));
    expect(await within(dialog).findByText('Preparing the document…', undefined, { timeout: 4000 })).toBeInTheDocument();
    expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Prepare' })).toBeDisabled();
    expect(await within(dialog).findByRole('alert', undefined, { timeout: 8000 }))
      .toHaveTextContent('The document could not be prepared: Quarto render failed.');
    expect(getContactReport).toHaveBeenCalledTimes(2);
    expect(within(dialog).getByRole('button', { name: 'Try again' })).toBeEnabled();
  }, 15000);

  it('a prepared document whose file is gone says so, not that the render failed', async () => {
    listRemediationContacts.mockResolvedValue([
      { contact_email: 'roger@example.com', contact_name: 'Roger Smith', total: 3, open: 3, overdue: 2, due_soon: 1,
        on_track: 0, deferred: 0, closed: 0, last_follow_up_on: null, projects: 1 },
    ]);
    prepareContactReport.mockReset().mockResolvedValue({
      id: 7, status: 'completed', format: 'contact-docx', message: null, error: null, filename: 'list.docx',
      contact_email: 'roger@example.com', created_at: null, images_withheld: 0, ready: false,
    });
    show('/remediation?view=contacts');
    const table = await screen.findByRole('table', { name: /contacts/i });
    fireEvent.click(within(table).getByRole('button', { name: 'Document' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Prepare' }));
    expect(await within(dialog).findByRole('alert'))
      .toHaveTextContent('The document was prepared, but its file is no longer available. Prepare it again.');
  });

  it.each([
    [0, null],
    [1, '1 image was left out because its finding also affects other contacts’ systems.'],
    [3, '3 images were left out because their findings also affect other contacts’ systems.'],
  ])('a finished document with %i images withheld says so only when there are some', async (withheld, sentence) => {
    listRemediationContacts.mockResolvedValue([
      { contact_email: 'roger@example.com', contact_name: 'Roger Smith', total: 3, open: 3, overdue: 2, due_soon: 1,
        on_track: 0, deferred: 0, closed: 0, last_follow_up_on: null, projects: 1 },
    ]);
    prepareContactReport.mockReset().mockResolvedValue({
      id: 7, status: 'completed', format: 'contact-docx', message: null, error: null, filename: 'list.docx',
      contact_email: 'roger@example.com', created_at: null, images_withheld: withheld, ready: true,
    });
    show('/remediation?view=contacts');
    const table = await screen.findByRole('table', { name: /contacts/i });
    fireEvent.click(within(table).getByRole('button', { name: 'Document' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Prepare' }));
    expect(await within(dialog).findByText('list.docx')).toBeInTheDocument();
    if (sentence) expect(within(dialog).getByText(sentence)).toBeInTheDocument();
    else expect(within(dialog).queryByText(/left out/)).not.toBeInTheDocument();
  });

  it('offers no document to someone who cannot write', async () => {
    projectRole.value = 'auditor';
    listRemediationContacts.mockResolvedValue([
      { contact_email: 'roger@example.com', contact_name: 'Roger Smith', total: 3, open: 3, overdue: 2, due_soon: 1,
        on_track: 0, deferred: 0, closed: 0, last_follow_up_on: null, projects: 1 },
    ]);
    show('/remediation?view=contacts');
    const table = await screen.findByRole('table', { name: /contacts/i });
    expect(within(table).queryByRole('button', { name: 'Document' })).not.toBeInTheDocument();
    expect(within(table).getByRole('button', { name: 'Follow up' })).toBeInTheDocument();
  });

  describe('the contact’s record beside the assessment’s conclusion', () => {
    const gaps = (items: RemediationRow[], counts: RemediationPage['verification_counts']): RemediationPage => ({
      ...page(items), verification_counts: counts,
    });
    const showWithTips = (path = '/remediation') => render(
      <TooltipProvider><MemoryRouter initialEntries={[path]}><Remediation /></MemoryRouter></TooltipProvider>,
    );

    it('says the gap in the row’s Deadline cell, and "Remediated" where the assessment concluded so', async () => {
      listRemediation.mockResolvedValue(gaps([
        row(1, { status: 'closed', state: 'closed', closed_on: '2026-10-06', closed_days_late: 0,
          endpoint_status: 'retest', verification: 'reported_fixed_not_retested' }),
        row(2, { endpoint_status: 'remediated', verification: 'remediated_record_open' }),
        row(3, { status: 'closed', state: 'closed', closed_on: '2026-10-06', endpoint_status: 'remediated' }),
        row(4),
      ], { reported_fixed_not_retested: 1, remediated_record_open: 1 }));
      showWithTips();
      const table = await screen.findByRole('table', { name: /remediation deadlines$/i });
      const rows = within(table).getAllByRole('row').slice(1);
      expect(within(rows[0]).getByText('Reported fixed on time')).toBeInTheDocument();
      const notRetested = within(rows[0]).getByText('Not retested');
      expect(notRetested).toHaveAttribute('title', expect.stringMatching(/contact reported it fixed/));
      expect(notRetested.className).toContain('truncate');
      expect(within(rows[1]).getByText('Remediated, record still open')).toBeInTheDocument();
      // The two agree: the row says both, and names no gap.
      expect(within(rows[2]).getByText('Reported fixed')).toBeInTheDocument();
      expect(within(rows[2]).getByText('Remediated')).toBeInTheDocument();
      expect(within(rows[2]).queryByText('Not retested')).not.toBeInTheDocument();
      // Nothing to relate: no extra line, and no column was added for it.
      expect(within(rows[3]).queryByText(/Remediated|Not retested/)).not.toBeInTheDocument();
      expect(within(table).getAllByRole('columnheader').map((h) => h.textContent))
        .toEqual(['', 'Deadline', 'Host', 'Finding', 'Contact', 'Assigned', 'Actions']);
    });

    it('each gap count opens exactly its rows through the address, as a chip that clears', async () => {
      listRemediation.mockResolvedValue(gaps([row(1)], { reported_fixed_not_retested: 5, remediated_record_open: 2 }));
      showWithTips('/remediation?state=overdue&severity=high&band=8-30');
      await screen.findByRole('table', { name: /remediation deadlines$/i });
      fireEvent.click(screen.getByRole('button', { name: '5 reported fixed, not retested: show them' }));
      // The count is taken before the state filters, so those go; severity stays.
      await waitFor(() => expect(listRemediation).toHaveBeenLastCalledWith(
        expect.objectContaining({
          verification: 'reported_fixed_not_retested', state: undefined, overdue_band: undefined,
          severity: 'high', offset: 0,
        }), expect.anything()));
      expect(screen.getByText(/in this selection/)).toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: '2 remediated, record still open: show them' }));
      await waitFor(() => expect(listRemediation).toHaveBeenLastCalledWith(
        expect.objectContaining({ verification: 'remediated_record_open' }), expect.anything()));
      fireEvent.click(screen.getByRole('button', { name: 'Remediated, record still open: remove this filter' }));
      await waitFor(() => expect(listRemediation).toHaveBeenLastCalledWith(
        expect.objectContaining({ verification: undefined, severity: 'high' }), expect.anything()));
    });

    it('reads the filter from the address, ignores a value it does not know, and the CSV follows it', async () => {
      listRemediation.mockResolvedValue(gaps([row(1)], { reported_fixed_not_retested: 1, remediated_record_open: 0 }));
      const first = showWithTips('/remediation?verification=reported_fixed_not_retested');
      await screen.findByRole('table', { name: /remediation deadlines$/i });
      expect(listRemediation.mock.calls[0][0]).toMatchObject({ verification: 'reported_fixed_not_retested' });
      // A gap with no rows is said as 0, not as a button.
      expect(screen.queryByRole('button', { name: /remediated, record still open: show them/ })).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole('button', { name: /CSV/ }));
      await waitFor(() => expect(saveBlob).toHaveBeenCalled());
      expect(listRemediation).toHaveBeenLastCalledWith(
        expect.objectContaining({ verification: 'reported_fixed_not_retested', limit: 200 }));
      first.unmount();
      listRemediation.mockClear();
      showWithTips('/remediation?verification=closed');
      await screen.findByRole('table', { name: /remediation deadlines$/i });
      expect(listRemediation.mock.calls[0][0]).toMatchObject({ verification: undefined });
    });

    // Code review 2026-10-09: the export pages through the list, and each
    // page's address is the project that is current when it is asked for — a
    // switch between two pages put two projects' rows in one file.
    it('the CSV stops, and saves nothing, when the project changes between two of its pages', async () => {
      const scopeBefore = getQueryScope();
      setQueryScope({ userId: 1, projectId: 1 });
      listRemediation.mockResolvedValue(gaps([row(1)], { reported_fixed_not_retested: 0, remediated_record_open: 0 }));
      showWithTips('/remediation');
      await screen.findByRole('table', { name: /remediation deadlines$/i });
      saveBlob.mockReset();
      listRemediation.mockClear();
      // The export's first page says there are more; the reader switches before it answers.
      let answer!: (page: unknown) => void;
      listRemediation.mockImplementationOnce(() => new Promise((resolve) => { answer = resolve; }));
      fireEvent.click(screen.getByRole('button', { name: /CSV/ }));
      await waitFor(() => expect(answer).toBeDefined());
      setQueryScope({ userId: 1, projectId: 2 });
      answer({ ...gaps([row(1)], { reported_fixed_not_retested: 0, remediated_record_open: 0 }), total: 500 });

      await waitFor(() => expect(toast.error).toHaveBeenCalledWith('The CSV could not be built. Nothing was saved.'));
      expect(saveBlob).not.toHaveBeenCalled();
      // No second page of the export (200 rows a call) was asked of the other project.
      const exportCalls = listRemediation.mock.calls.filter(([query]) => (query as { limit?: number }).limit === 200);
      expect(exportCalls).toHaveLength(1);
      setQueryScope(scopeBefore);
    });

    it('shows no gap line when neither has a row', async () => {
      show();
      await screen.findByRole('table', { name: /remediation deadlines$/i });
      expect(screen.queryByLabelText('Where the remediation record and the assessment disagree')).not.toBeInTheDocument();
      expect(screen.queryByText('Reported fixed, not retested')).not.toBeInTheDocument();
    });

    it('the editor offers "Reported fixed" and dates it "Reported fixed on"; the timeline says the status so', async () => {
      listRemediation.mockResolvedValue(page([
        row(1, { status: 'closed', state: 'closed', closed_on: '2026-10-06', contact_email: 'roger@example.com' }),
      ]));
      listRemediationEvents.mockResolvedValue({
        total: 1, has_more: false,
        items: [{ id: 9, host_id: 101, kind: 'change', finding_id: 10, finding_host_id: 1, finding_title: 'Finding 1',
          field: 'status', from: 'open', to: 'closed', body: null, edited_at: null, author: 'Ana',
          agent_session_id: null, can_modify: false,
          occurred_at: '2026-10-06T10:00:00Z', recorded_at: '2026-10-06T10:00:00Z' }],
      });
      show();
      const table = await screen.findByRole('table', { name: /remediation deadlines$/i });
      fireEvent.click(within(table).getByRole('button', { name: 'Timeline for 10.0.0.1' }));
      expect((await screen.findByText('Status')).closest('li')).toHaveTextContent('Status: Open → Reported fixed');
      fireEvent.keyDown(document.body, { key: 'Escape' });
      fireEvent.click(within(table).getByRole('button', { name: 'Edit' }));
      const dialog = await screen.findByRole('dialog');
      expect(within(dialog).getByLabelText('Reported fixed on')).toHaveValue('2026-10-06');
      expect(within(dialog).queryByText(/Closed/)).not.toBeInTheDocument();
    });
  });
});
