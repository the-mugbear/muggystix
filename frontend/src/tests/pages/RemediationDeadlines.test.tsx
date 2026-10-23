/**
 * 5.340.0 — the cross-project Remediation deadlines page, the installation's
 * settings section, and where a deadline alert opens.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const getRemediationPolicy = vi.fn();
const updateRemediationPolicy = vi.fn();
const listRemediationProjects = vi.fn();
const listRemediationOverview = vi.fn();
const applyRemediation = vi.fn();
const listRemediationTeams = vi.fn();
const getRemediationTrend = vi.fn();
const getRemediationDefectMetrics = vi.fn();
vi.mock('../../services/api', () => ({
  getRemediationDefectMetrics: (...a: unknown[]) => getRemediationDefectMetrics(...a),
  listRemediationTeams: (...a: unknown[]) => Promise.resolve(listRemediationTeams(...a)).then(asCapped),
  getRemediationTrend: (...a: unknown[]) => getRemediationTrend(...a),
  getRemediationPolicy: (...a: unknown[]) => getRemediationPolicy(...a),
  updateRemediationPolicy: (...a: unknown[]) => updateRemediationPolicy(...a),
  listRemediationProjects: (...a: unknown[]) => listRemediationProjects(...a),
  listRemediationOverview: (...a: unknown[]) => listRemediationOverview(...a),
  applyRemediation: (...a: unknown[]) => applyRemediation(...a),
  listRemediation: vi.fn(),
  listRemediationContacts: vi.fn(),
  getRemediationFollowUp: vi.fn(),
  recordRemediationFollowUp: vi.fn(),
  // Never answers: these tests are not about the effect of a timeline change.
  previewRemediationPolicy: vi.fn(() => new Promise(() => undefined)),
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
const navigateMock = vi.hoisted(() => vi.fn());
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigateMock };
});
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));

import RemediationDeadlines from '../../pages/RemediationDeadlines';
import RemediationSettingsSection, { daysProblem, zoneProblem } from '../../components/remediation/RemediationSettingsSection';
import { notificationHref } from '../../utils/notificationLinks';
import type { RemediationRow, RemediationState } from '../../services/api';
import { asCapped } from '../helpers/cappedList';

const POLICY = { enabled: true, due_soon_days: 7, days: { critical: 30, high: 30, medium: 90, low: 120, info: null } };
const states = (over: Partial<Record<RemediationState, number>> = {}): Record<RemediationState, number> => ({
  overdue: 0, due_soon: 0, on_track: 0, not_assigned: 0, no_deadline: 0, deferred: 0, closed: 0, ...over,
});
const row = (id: number, projectId: number, projectName: string): RemediationRow => ({
  finding_host_id: id, finding_id: 10, project_id: projectId, project_name: projectName, finding_title: `Finding ${id}`,
  severity: 'high', finding_status: 'confirmed', endpoint_status: 'open', host_id: 100 + id,
  ip_address: `10.0.0.${id}`, hostname: null, contact_email: 'roger@example.com', contact_name: null, team: null,
  notified_on: '2026-10-01', status: 'open', closed_on: null, updated_at: null,
  state: 'overdue', due_on: '2026-10-31', days_left: -10, closed_days_late: null, last_follow_up_on: null,
  verification: null,
});

const metricRow = (key: string, label: string, severities: string[] | null, over: Record<string, number | null> = {}) => ({
  key, label, severities, defects: 0, open: 0, remediated: 0, deferred: 0, remediation_rate: null,
  mttr_days: null, mttr_measured: 0, mttr_closed_days: null, mttr_closed_measured: 0, not_assigned: 0, not_measured: 0,
  ...over,
});
const METRICS = {
  project_id: null, projects: 1, as_of: '2026-11-10',
  rows: [
    metricRow('overall', 'Overall', null, { defects: 43, open: 25, remediated: 18, remediation_rate: 41.9, mttr_days: 43, mttr_closed_days: 15, not_assigned: 2 }),
    metricRow('critical_high', 'Critical & High', ['critical', 'high'], { defects: 28, open: 17, remediated: 11, remediation_rate: 39.3, mttr_days: 41, mttr_closed_days: 10 }),
    metricRow('critical', 'Critical', ['critical'], { defects: 5, open: 2, remediated: 3 }),
    metricRow('high', 'High', ['high']),
    metricRow('medium', 'Medium', ['medium']),
    metricRow('low', 'Low', ['low']),
  ],
};

const show = (path = '/remediation-deadlines') => render(
  <MemoryRouter initialEntries={[path]}><RemediationDeadlines /></MemoryRouter>,
);

beforeEach(() => {
  [getRemediationPolicy, updateRemediationPolicy, listRemediationProjects, listRemediationOverview, applyRemediation]
    .forEach((m) => m.mockReset());
  Object.values(toast).forEach((m) => m.mockReset());
  projectCtx.projects = [{ id: 4, name: 'Alpha' }];
  projectCtx.selectProject.mockReset();
  navigateMock.mockReset();
  listRemediationTeams.mockReset().mockResolvedValue([]);
  getRemediationTrend.mockReset().mockResolvedValue({ as_of: '2026-11-10', days: 90, daily: [], closed_by_month: [] });
  getRemediationPolicy.mockResolvedValue(POLICY);
  getRemediationDefectMetrics.mockReset().mockResolvedValue(METRICS);
  listRemediationProjects.mockResolvedValue({
    items: [
      { project_id: 4, name: 'Alpha', archived: false, states: states({ overdue: 2, not_assigned: 5 }), total: 7, remediated: 1 },
      { project_id: 9, name: 'Old engagement', archived: true, states: states({ overdue: 1, closed: 3 }), total: 7, remediated: 1 },
    ],
    totals: states({ overdue: 3, not_assigned: 5, closed: 3 }), defect_totals: { total: 7, remediated: 1 }, as_of: '2026-11-10', policy: POLICY,
  });
  listRemediationOverview.mockResolvedValue({
    items: [row(1, 4, 'Alpha'), row(2, 9, 'Old engagement')], total: 2, has_more: false, limit: 25, offset: 0,
    status_counts: { open: 8, closed: 3, deferred: 0 },
    state_counts: states({ overdue: 3, not_assigned: 5, closed: 3 }),
    severity_counts: { critical: { overdue: 1, due_soon: 0 }, high: { overdue: 2, due_soon: 0 }, medium: { overdue: 0, due_soon: 0 }, low: { overdue: 0, due_soon: 0 }, info: { overdue: 0, due_soon: 0 } },
    overdue_ages: { '1-7': 0, '8-30': 2, '31-90': 1, '90+': 0 }, not_followed_up: 3, not_followed_up_days: 7,
    as_of: '2026-11-10',
  });
  applyRemediation.mockResolvedValue({
    dry_run: false, overwrite: true, rows: [],
    summary: { targets: 1, changed: 1, unchanged: 0, conflicts: 0, notes_added: 0, notes_already_recorded: 0 },
  });
});

describe('Remediation deadlines (across projects)', () => {
  it('lists the reader’s projects — archived included — and a count opens exactly its rows', async () => {
    show();
    const projects = await screen.findByRole('table', { name: 'Remediation deadlines by project' });
    const [alpha, old] = within(projects).getAllByRole('row').slice(1);
    expect(within(old).getByText('Archived')).toBeInTheDocument();
    // A zero is text, not a link to an empty list.
    expect(within(alpha).queryByRole('button', { name: /due soon/i })).not.toBeInTheDocument();
    fireEvent.click(within(old).getByRole('button', { name: 'Old engagement: show overdue (1)' }));
    await waitFor(() => expect(listRemediationOverview).toHaveBeenLastCalledWith(
      expect.objectContaining({ project_id: 9, state: ['overdue'], offset: 0 }), expect.anything(),
    ));
    const lead = await screen.findByText(/due within 7 days/);
    expect(lead).toHaveTextContent('3 overdue and 0 due within 7 days, of 8 open findings on hosts');
  });

  // The lead is read where it is shown — from the list's answer on screen,
  // through the work list's `children` (it was page state an effect filled
  // through `onLoaded`).  What it says belongs to the ANSWER: "in this
  // selection" comes with the narrowed answer's counts, not with the click.
  it('the lead follows the answer on screen: its counts and "in this selection" arrive together', async () => {
    const { container } = show();
    const lead = await screen.findByText(/due within 7 days/);
    expect(lead).toHaveTextContent(
      '3 overdue and 0 due within 7 days, of 8 open findings on hosts across the projects you administer. '
      + '5 have not been assigned, so no deadline is running.');
    // The page is laid out as it was: the lead under the title, the projects,
    // the list, the trend — in that order.
    const order = (text: string) => container.textContent?.indexOf(text) ?? -1;
    expect(order('Remediation deadlines')).toBeLessThan(order('due within 7 days'));
    expect(screen.getAllByRole('heading').map((h) => h.textContent?.replace(/\d+$/, '')))
      .toEqual(['Remediation deadlines', 'Projects', 'Defect metrics', 'Findings on hosts', 'Over time']);

    let answer: (v: unknown) => void = () => undefined;
    const whole = await listRemediationOverview.mock.results[0].value;
    listRemediationOverview.mockImplementationOnce(() => new Promise((resolve) => { answer = resolve; }));
    const projects = screen.getByRole('table', { name: 'Remediation deadlines by project' });
    fireEvent.click(within(projects).getByRole('button', { name: 'Old engagement: show overdue (1)' }));
    await waitFor(() => expect(listRemediationOverview).toHaveBeenLastCalledWith(
      expect.objectContaining({ project_id: 9, state: ['overdue'] }), expect.anything()));
    // Still the answer that is on screen, said as what it was asked for.
    expect(lead).toHaveTextContent('of 8 open findings on hosts across the projects you administer.');
    await act(async () => {
      answer({ ...whole, items: [row(2, 9, 'Old engagement')], total: 1, state_counts: states({ overdue: 1, closed: 3 }) });
    });
    await waitFor(() => expect(lead).toHaveTextContent(
      '1 overdue and 0 due within 7 days, of 1 open finding on a host in this selection.'));
  });

  it('names each row’s project, links nowhere outside it, and saves through that project', async () => {
    show();
    const list = await screen.findByRole('table', { name: /remediation deadlines$/i });
    const [first, second] = within(list).getAllByRole('row').slice(1);
    expect(within(second).getByText('Old engagement')).toBeInTheDocument();
    // Never a plain link: it would open whichever project is selected.
    expect(within(first).queryByRole('link')).not.toBeInTheDocument();
    // A project the reader can switch to opens the finding IN it; one they
    // cannot (the archived one is not in their list) stays text.
    fireEvent.click(within(first).getByRole('button', { name: 'Finding 1' }));
    expect(projectCtx.selectProject).toHaveBeenCalledWith({ id: 4, name: 'Alpha' });
    expect(navigateMock).toHaveBeenCalledWith('/findings/10?endpoint=1#endpoints');
    expect(within(second).queryByRole('button', { name: 'Finding 2' })).not.toBeInTheDocument();
    expect(within(second).getByText('Finding 2')).toBeInTheDocument();
    fireEvent.click(within(second).getByRole('button', { name: 'Edit' }));
    const dialog = await screen.findByRole('dialog');
    fireEvent.change(within(dialog).getByLabelText('Contact name'), { target: { value: 'Roger' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(applyRemediation).toHaveBeenCalledTimes(1));
    // The row's project first — not the one selected in the app — through the
    // mount that serves archived projects.
    expect(applyRemediation.mock.calls[0]).toEqual([9, [{ finding_host_id: 2, contact_name: 'Roger' }], { overwrite: true }, 'overview']);
  });

  it('says so when the reader administers no project', async () => {
    listRemediationProjects.mockResolvedValue({ items: [], totals: states(), defect_totals: { total: 7, remediated: 1 }, as_of: '2026-11-10', policy: POLICY });
    show();
    expect(await screen.findByText(/You administer no project/)).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('asks for nothing on an installation that did not opt in', async () => {
    getRemediationPolicy.mockResolvedValue({ ...POLICY, enabled: false });
    show();
    expect(await screen.findByText(/not turned on for this installation/)).toBeInTheDocument();
    expect(listRemediationProjects).not.toHaveBeenCalled();
    expect(listRemediationOverview).not.toHaveBeenCalled();
  });
});

describe('System settings — remediation tracking', () => {
  it('is off by default, turns on at once, and saves only sensible timelines', async () => {
    getRemediationPolicy.mockResolvedValue({ ...POLICY, enabled: false });
    updateRemediationPolicy.mockImplementation(async (body: object) => ({ ...POLICY, ...body }));
    render(<RemediationSettingsSection />);
    const toggle = await screen.findByRole('switch', { name: 'Track remediation on this installation' });
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByLabelText('Critical')).toHaveValue('30');
    expect(screen.getByLabelText('Informational')).toHaveValue('');          // no deadline
    fireEvent.click(toggle);
    await waitFor(() => expect(updateRemediationPolicy).toHaveBeenCalledWith({ enabled: true }));

    // No Save until something changed; a bad number is said and not sent.
    expect(screen.queryByRole('button', { name: 'Save timelines' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('High'), { target: { value: '0' } });
    expect(screen.getByRole('alert')).toHaveTextContent(/High: a whole number of days from 1/);
    expect(screen.getByRole('button', { name: 'Save timelines' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('High'), { target: { value: '45' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save timelines' }));
    await waitFor(() => expect(updateRemediationPolicy).toHaveBeenLastCalledWith({
      days: { critical: 30, high: 45, medium: 90, low: 120, info: null }, due_soon_days: 7,
    }));
  });

  it('edits the time zone deadlines are counted in, and sends it only when it changed', async () => {
    getRemediationPolicy.mockResolvedValue({ ...POLICY, time_zone: 'UTC' });
    updateRemediationPolicy.mockImplementation(async (body: object) => ({ ...POLICY, time_zone: 'UTC', ...body }));
    render(<RemediationSettingsSection />);
    const zone = await screen.findByLabelText('Time zone');
    expect(zone).toHaveValue('UTC');
    fireEvent.change(zone, { target: { value: 'Mars/Olympus' } });
    expect(screen.getByRole('alert')).toHaveTextContent(/Time zone: an IANA name/);
    expect(screen.getByRole('button', { name: 'Save timelines' })).toBeDisabled();
    fireEvent.change(zone, { target: { value: ' America/Los_Angeles ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save timelines' }));
    await waitFor(() => expect(updateRemediationPolicy).toHaveBeenLastCalledWith({
      days: { critical: 30, high: 30, medium: 90, low: 120, info: null }, due_soon_days: 7,
      time_zone: 'America/Los_Angeles',
    }));
    await waitFor(() => expect(screen.getByLabelText('Time zone')).toHaveValue('America/Los_Angeles'));
  });

  it('knows a time zone name from something else', () => {
    expect(zoneProblem('UTC')).toBeNull();
    expect(zoneProblem('Europe/Paris')).toBeNull();
    expect(zoneProblem('')).toMatch(/^Time zone/);
    expect(zoneProblem('Mars/Olympus')).toMatch(/^Time zone/);
    expect(zoneProblem('../../etc/passwd')).toMatch(/^Time zone/);
    expect(zoneProblem(undefined)).toMatch(/^Time zone/);
  });

  it('checks every field', () => {
    const ok = { critical: '30', high: '30', medium: '90', low: '120', info: '', due_soon: '7' };
    expect(daysProblem(ok)).toBeNull();
    expect(daysProblem({ ...ok, low: '1.5' })).toMatch(/^Low/);
    expect(daysProblem({ ...ok, info: '4000' })).toMatch(/^Informational/);
    expect(daysProblem({ ...ok, due_soon: '' })).toMatch(/^Warning window/);
    expect(daysProblem({ ...ok, due_soon: '0' })).toBeNull();
  });
});

describe('a deadline alert opens the cross-project list on its project and state', () => {
  const alert = (source_type: string, source_id: number | null) =>
    notificationHref({ type: 'remediation', source_type, source_id, host_id: null, finding_id: null });

  it('overdue and due soon', () => {
    expect(alert('remediation_overdue', 9)).toBe('/remediation-deadlines?state=overdue&project=9');
    expect(alert('remediation_due_soon', 4)).toBe('/remediation-deadlines?state=due_soon&project=4');
    expect(alert('remediation_overdue', null)).toBe('/remediation-deadlines?state=overdue');
  });

  it('deferrals whose review date has come open their flag, in the alert’s project', () => {
    // The cross-project page, so a global administrator who is not a member
    // of the project — and an admin of one since archived — lands on its rows.
    expect(alert('remediation_deferral_review', 9)).toBe('/remediation-deadlines?flag=deferral_review_due&project=9');
    expect(alert('remediation_deferral_review', null)).toBe('/remediation-deadlines?flag=deferral_review_due');
    // A kind this page does not know is not a remediation link.
    expect(alert('remediation_something_else', 9)).toBeNull();
  });
});

describe('the project chooser (owner, 2026-10-08)', () => {
  it('lists only projects with open findings, each with its count', async () => {
    listRemediationProjects.mockResolvedValue({
      items: [
        { project_id: 4, name: 'Alpha', archived: false, states: states({ overdue: 2, not_assigned: 5 }), total: 7, remediated: 1 },
        { project_id: 9, name: 'Old engagement', archived: true, states: states({ overdue: 1, closed: 3 }), total: 7, remediated: 1 },
        { project_id: 12, name: 'All fixed', archived: false, states: states({ closed: 8, deferred: 1 }), total: 7, remediated: 1 },
        { project_id: 13, name: 'Empty', archived: false, states: states(), total: 7, remediated: 1 },
      ],
      totals: states({ overdue: 3, not_assigned: 5, closed: 11, deferred: 1 }), defect_totals: { total: 7, remediated: 1 }, as_of: '2026-11-10', policy: POLICY,
    });
    show();
    const chooser = await screen.findByRole('combobox', { name: 'Project' });
    expect(chooser).toHaveTextContent('All projects with open findings (2)');
    fireEvent.click(chooser);
    const options = (await screen.findAllByRole('option')).map((o) => o.textContent);
    expect(options).toEqual([
      'All projects with open findings (2)', 'Alpha · 7 open', 'Old engagement (archived) · 1 open',
    ]);
  });

  it('keeps the project named in the address even when nothing is open in it', async () => {
    listRemediationProjects.mockResolvedValue({
      items: [
        { project_id: 4, name: 'Alpha', archived: false, states: states({ overdue: 2 }), total: 7, remediated: 1 },
        { project_id: 12, name: 'All fixed', archived: false, states: states({ closed: 8 }), total: 7, remediated: 1 },
      ],
      totals: states({ overdue: 2, closed: 8 }), defect_totals: { total: 7, remediated: 1 }, as_of: '2026-11-10', policy: POLICY,
    });
    show('/remediation-deadlines?project=12');
    expect(await screen.findByRole('combobox', { name: 'Project' })).toHaveTextContent('All fixed · 0 open');
  });
});

// Owner, 2026-10-14: the projects table says each project's total and how
// many were REMEDIATED (the endpoint, after the retest); a defect metrics
// table, scoped to one project or every active one; "Copy summary" opens a
// dialog.  Every count opens exactly its rows.
describe('defects on the deadlines page (owner, 2026-10-14)', () => {
  it('Total and Remediated open the project’s rows and its remediated rows', async () => {
    show();
    const projects = await screen.findByRole('table', { name: 'Remediation deadlines by project' });
    const [alpha] = within(projects).getAllByRole('row').slice(1);
    fireEvent.click(within(alpha).getByRole('button', { name: 'Alpha: show remediated (1)' }));
    await waitFor(() => expect(listRemediationOverview).toHaveBeenLastCalledWith(
      expect.objectContaining({ project_id: 4, defect: ['remediated'] }), expect.anything(),
    ));
    fireEvent.click(within(alpha).getByRole('button', { name: 'Alpha: show every finding on a host (7)' }));
    await waitFor(() => {
      const query = listRemediationOverview.mock.lastCall?.[0];
      expect(query).toMatchObject({ project_id: 4 });
      expect(query.defect).toBeUndefined();
      expect(query.state).toBeUndefined();
    });
    expect(screen.getByRole('button', { name: 'What Remediated counts' })).toBeInTheDocument();
  });

  // Third review 2026-10-15, N7: it printed "0 not assigned".
  it('the MTTR note names only the parts that are not zero', async () => {
    getRemediationDefectMetrics.mockResolvedValue({
      ...METRICS, rows: [metricRow('overall', 'Overall', null, { defects: 5, not_assigned: 0, not_measured: 3 })],
    });
    show();
    const note = await screen.findByTestId('defect-metrics-unmeasured');
    expect(note).toHaveTextContent('Not in the MTTR figures: 3 with no recorded day or one before the assigned date.');
    expect(note).not.toHaveTextContent('not assigned');
  });

  // Review 2026-10-14, R14: the "All" row's numbers opened nothing.
  it('the All row’s counts open every project’s rows', async () => {
    show();
    const projects = await screen.findByRole('table', { name: 'Remediation deadlines by project' });
    const all = within(projects).getAllByRole('row').find((r) => r.textContent?.startsWith('All'))!;
    const remediated = within(all).getByRole('button', { name: /^All projects: show remediated/ });
    fireEvent.click(remediated);
    await waitFor(() => {
      const query = listRemediationOverview.mock.lastCall?.[0];
      expect(query.defect).toEqual(['remediated']);
      expect(query.project_id).toBeUndefined();
    });
  });

  it('the metrics cover every active project until one is chosen, and a cell opens its rows', async () => {
    show();
    const table = await screen.findByRole('table', { name: 'Defect metrics' });
    expect(getRemediationDefectMetrics).toHaveBeenLastCalledWith(null, expect.anything());
    // Both MTTR headings say how they are derived.
    expect(within(table).getByRole('button', { name: 'How MTTR is derived' })).toBeInTheDocument();
    expect(within(table).getByRole('button', { name: /How MTTR for remediated or deferred/ })).toBeInTheDocument();
    expect(within(table).getByText('41.9%')).toBeInTheDocument();
    expect(screen.getByText(/Not in the MTTR figures: 2 not assigned/)).toBeInTheDocument();
    fireEvent.click(within(table).getByRole('button', { name: 'Critical & High: show open defects (17)' }));
    await waitFor(() => expect(listRemediationOverview).toHaveBeenLastCalledWith(
      expect.objectContaining({ defect: ['open'], severity: ['critical', 'high'], active_only: true }),
      expect.anything(),
    ));
    expect(listRemediationOverview.mock.lastCall?.[0].project_id).toBeUndefined();
    // Several severities read as a chip (as well as the metrics' row name).
    await waitFor(() => expect(screen.getAllByText('Critical & High').length).toBeGreaterThan(1));
  });

  it('with a project chosen the metrics are that project’s, and its cells keep it', async () => {
    show('/remediation-deadlines?project=4');
    const table = await screen.findByRole('table', { name: 'Defect metrics' });
    expect(getRemediationDefectMetrics).toHaveBeenLastCalledWith(4, expect.anything());
    fireEvent.click(within(table).getByRole('button', { name: 'Overall: show defects (43)' }));
    await waitFor(() => expect(listRemediationOverview).toHaveBeenLastCalledWith(
      expect.objectContaining({ project_id: 4, defect: ['open', 'remediated', 'deferred'] }), expect.anything(),
    ));
    expect(listRemediationOverview.mock.lastCall?.[0].active_only).toBeUndefined();
  });

  it('Copy summary opens a dialog with the text, copied from there', async () => {
    show();
    const button = await screen.findByRole('button', { name: /Copy summary/ });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByRole('heading', { name: 'Summary' })).toBeInTheDocument();
    expect((within(dialog).getByLabelText('Summary') as HTMLTextAreaElement).value)
      .toContain('across the projects you administer');
    expect(toast.success).not.toHaveBeenCalled();             // nothing is copied until asked
    fireEvent.click(within(dialog).getAllByRole('button', { name: 'Close' })[0]);
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });
});
