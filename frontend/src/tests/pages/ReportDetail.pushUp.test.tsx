/**
 * A draft's details pushed up to the project's report defaults (owner,
 * 2026-10-14: "if a user modifies a draft they may want that change to become
 * the standard for future drafts") — in the report workspace (5.394.0) the
 * ONE way a draft writes the project's defaults is "Update project
 * defaults…" (the proposal, revised after its second review):
 *
 * - offered only when the draft has details of its own (saved or pending);
 * - the dialog lists each with the project's value now (and where it comes
 *   from) → this report's, and how many other drafts follow it;
 * - the ticked ones are promoted in one call; ONLY they leave the page's
 *   pending edits — every other pending edit stays, unsaved, and a later
 *   Save never sends a promoted key back; a failure keeps everything.
 *
 * Removed on purpose, with the inline project-default editing they guarded:
 * "Set for the project" / "Edit the project's default" on a draft (the
 * project's defaults are edited on /reports/defaults — ReportDefaults.test),
 * the per-detail "Make this the project's default" menu item, and the page
 * holding Issue while a project default was half-edited (review 2026-10-14,
 * H1 — the state that caused it no longer exists: nothing on the draft writes
 * a single project default).
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', async () => vi.importActual('react-router-dom'));
vi.mock('../../services/api', () => ({
  getClientReport: vi.fn(),
  updateClientReport: vi.fn(),
  deleteClientReport: vi.fn(),
  previewClientReport: vi.fn(),
  issueClientReport: vi.fn(),
  rerenderClientReport: vi.fn(),
  reviseClientReport: vi.fn(),
  listReportTemplates: vi.fn(),
  patchReportProfile: vi.fn(),
  promoteClientReportDetails: vi.fn(),
  getClientReportFollowers: vi.fn(),
  listReportTextImages: vi.fn(() => Promise.resolve([])),
  uploadReportTextImage: vi.fn(),
  downloadClientReportScope: vi.fn(),
  listProjectMembers: vi.fn(),
  getProjectReportTeam: vi.fn(),
  getReportJob: vi.fn(),
  downloadReportJob: vi.fn(),
  fetchReportJobBlob: vi.fn(),
  draftReportWithAI: vi.fn(),
  listLLMProviders: vi.fn(),
}));
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, vi.fn()] }));
const toastMock = { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 3, username: 'ana', full_name: 'Ana', role: 'member' } }),
}));
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => ({ currentProject: { id: 1, name: 'P' } }) }));

import * as api from '../../services/api';
import ReportDetail from '../../pages/ReportDetail';
import { resetProjectMembersCache } from '../../hooks/useProjectMembers';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const followed = {
  executive_summary: 'The project’s summary.',
  client_name: 'Example Corp', classification: 'Internal', engagement_type: null, testers: [], distribution: [],
  system_description: null, applications: null, thick_clients: null, other_targets: null,
  author_name: 'Installation Ltd', disclaimer: null,
};
const from = {
  executive_summary: 'project', client_name: 'project', classification: 'project', engagement_type: null, testers: null,
  distribution: null, system_description: null, applications: null, thick_clients: null, other_targets: null,
  author_name: 'installation', disclaimer: null,
};
const report = (over: Record<string, unknown> = {}) => ({
  id: 5, project_id: 1, kind: 'full', status: 'draft', title: 'Example report', number: null,
  template: 'pentest', baseline: null, revision_of: null, superseded_by: null, settings: {},
  executive_summary: null, template_fingerprint: null, quarto_version: null,
  render_status: null, render_error: null, files: [], created_by_name: 'Ana', issued_by_name: null,
  created_at: '2026-09-20T00:00:00Z', updated_at: null, issued_at: null,
  summary: { counts: { critical: 0, high: 0, medium: 0, low: 0, info: 0, total: 0 }, missing_text: [] },
  followed, followed_from: from, effective: followed,
  can_edit: true, can_issue: false, can_discard: true, ...over,
});

const renderDetail = () => render(
  <MemoryRouter initialEntries={['/reports/5']}>
    <Routes><Route path="/reports/:reportId" element={<ReportDetail />} /></Routes>
  </MemoryRouter>,
);
const section = (id: string) => within(screen.getByTestId(`report-section-${id}`));
const editClient = async (value: string) => {
  fireEvent.click(await screen.findByRole('button', { name: 'Edit Client & engagement' }));
  fireEvent.change(screen.getByLabelText('Client'), { target: { value } });
  fireEvent.click(section('engagement').getByRole('button', { name: 'Done' }));
};

beforeEach(() => {
  vi.clearAllMocks();
  resetProjectMembersCache();
  mocked.listReportTemplates.mockResolvedValue([
    { name: 'pentest', title: 'Penetration test report', description: '', formats: ['html'] },
    { name: 'brief', title: 'Brief report', description: '', formats: ['html'] },
  ]);
  mocked.listProjectMembers.mockResolvedValue([]);
  mocked.getClientReportFollowers.mockResolvedValue({ client_name: 3, classification: 0, executive_summary: 2 });
});

describe('Update project defaults…', () => {
  it('is offered only once the draft has a detail of its own', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    renderDetail();
    await screen.findByTestId('report-section-engagement');
    expect(screen.queryByRole('button', { name: 'Update project defaults…' })).toBeNull();
    await editClient('Renamed Corp');                                    // pending, not saved
    expect(screen.getByRole('button', { name: 'Update project defaults…' })).toBeInTheDocument();
  });

  it('an untick does not survive Cancel (review 2026-10-14, R13)', async () => {
    mocked.getClientReport.mockResolvedValue(report({ settings: { client_name: 'Mine', classification: 'Ours' } }));
    renderDetail();
    fireEvent.click(await screen.findByRole('button', { name: 'Update project defaults…' }));
    let dialog = within(await screen.findByRole('dialog'));
    fireEvent.click(within(dialog.getByTestId('promote-classification')).getByRole('checkbox'));
    expect(dialog.getByRole('button', { name: 'Update 1 default' })).toBeInTheDocument();
    fireEvent.click(dialog.getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    fireEvent.click(screen.getByRole('button', { name: 'Update project defaults…' }));
    dialog = within(await screen.findByRole('dialog'));
    expect(dialog.getByRole('button', { name: 'Update 2 defaults' })).toBeInTheDocument();
  });

  it('an empty team cannot be pushed up: it is said, and never sent (review 2026-10-14, H3)', async () => {
    const team = [{ user_id: 3, name: 'Ana', role: null, email: null }];
    mocked.getClientReport.mockResolvedValue(report({
      settings: { client_name: 'Mine', testers: [] },
      followed: { ...followed, testers: team }, effective: { ...followed, client_name: 'Mine', testers: [] },
    }));
    mocked.promoteClientReportDetails.mockResolvedValue(report({ settings: { testers: [] } }));
    renderDetail();
    fireEvent.click(await screen.findByRole('button', { name: 'Update project defaults…' }));
    const dialog = within(await screen.findByRole('dialog'));
    expect(dialog.getByTestId('promote-testers')).toHaveTextContent('The project’s team cannot be empty');
    fireEvent.click(dialog.getByRole('button', { name: 'Update 1 default' }));
    await waitFor(() => expect(mocked.promoteClientReportDetails).toHaveBeenCalledWith(
      1, 5, { client_name: 'Mine' }, { client_name: 'Mine' },
      // …and the project's value it showed (third review 2026-10-15, R9).
      { client_name: 'Example Corp' },
    ));
  });

  it('lists each detail of its own with the project’s value → this report’s, and the drafts that follow it', async () => {
    mocked.getClientReport.mockResolvedValue(report({
      settings: { classification: 'Restricted' }, effective: { ...followed, classification: 'Restricted' },
    }));
    renderDetail();
    await editClient('Renamed Corp');
    fireEvent.click(screen.getByRole('button', { name: 'Update project defaults…' }));
    const dialog = await screen.findByRole('dialog');
    const client = within(dialog).getByTestId('promote-client_name');
    expect(client).toHaveTextContent('The project’s now: “Example Corp”');
    expect(client).toHaveTextContent('This report’s: “Renamed Corp”');
    expect(await within(client).findByText('3 other drafts follow it and will print the new value.')).toBeInTheDocument();
    const classification = within(dialog).getByTestId('promote-classification');
    expect(classification).toHaveTextContent('This report’s: “Restricted”');
    expect(await within(classification).findByText('No other draft follows it.')).toBeInTheDocument();
    expect(within(dialog).queryByTestId('promote-executive_summary')).toBeNull();   // not its own
  });

  it('promotes the ticked ones only; every other pending edit stays, and a later Save never sends them back', async () => {
    mocked.getClientReport.mockResolvedValue(report({
      settings: { classification: 'Restricted' }, effective: { ...followed, classification: 'Restricted' },
    }));
    // After the push-up the draft follows the project for Client.
    mocked.promoteClientReportDetails.mockResolvedValue(report({
      settings: { classification: 'Restricted' },
      followed: { ...followed, client_name: 'Renamed Corp' },
      effective: { ...followed, client_name: 'Renamed Corp', classification: 'Restricted' },
    }));
    mocked.updateClientReport.mockImplementation(async () => report());
    renderDetail();
    await editClient('Renamed Corp');
    // Another pending edit, in another section.
    fireEvent.click(screen.getByRole('button', { name: 'Edit Executive summary' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Executive summary' }), { target: { value: 'Ours.' } });
    fireEvent.click(section('summary').getByRole('button', { name: 'Done' }));

    fireEvent.click(screen.getByRole('button', { name: 'Update project defaults…' }));
    const dialog = await screen.findByRole('dialog');
    // Keep Classification as this report's own; push up Client and the Summary.
    fireEvent.click(within(within(dialog).getByTestId('promote-classification')).getByRole('checkbox'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Update 2 defaults' }));
    // With each value, what the page saw the draft print before the edit —
    // the server refuses the push-up if that changed (second review, H1).
    await waitFor(() => expect(mocked.promoteClientReportDetails).toHaveBeenCalledWith(1, 5, {
      executive_summary: 'Ours.', client_name: 'Renamed Corp',
    }, {
      executive_summary: 'The project’s summary.', client_name: 'Example Corp',
    }, {
      // The project's values it showed, checked under the profile's lock (R9).
      executive_summary: 'The project’s summary.', client_name: 'Example Corp',
    }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(mocked.patchReportProfile).not.toHaveBeenCalled();
    // The promoted keys left the pending edits; nothing else was pending, so the draft is clean.
    expect(screen.getByTestId('report-save-status')).toHaveTextContent('All changes saved');
  });

  it('keeps an unrelated pending edit — the template — unsaved, and Save sends it without the promoted key', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    mocked.promoteClientReportDetails.mockResolvedValue(report({
      followed: { ...followed, client_name: 'Renamed Corp' }, effective: { ...followed, client_name: 'Renamed Corp' },
    }));
    mocked.updateClientReport.mockImplementation(async () => report({ template: 'brief' }));
    renderDetail();
    await editClient('Renamed Corp');
    fireEvent.keyDown(screen.getByRole('combobox', { name: 'Template' }), { key: 'Enter' });
    fireEvent.click(await screen.findByRole('option', { name: 'Brief report' }));

    fireEvent.click(screen.getByRole('button', { name: 'Update project defaults…' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Update 1 default' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(screen.getByTestId('report-save-status')).toHaveTextContent('Unsaved changes — Report');

    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
    await waitFor(() => expect(mocked.updateClientReport).toHaveBeenCalledWith(1, 5, {
      template: 'brief', settings: {},
    }));
  });

  // Third review 2026-10-15, N2: a save refused because the report changed
  // (409 — issued meanwhile) only toasted; the page kept offering the save.
  it('a save refused with 409 reads the report again; edits stay pending', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    mocked.updateClientReport.mockRejectedValue(Object.assign(new Error('409'), {
      isAxiosError: true, response: { status: 409, data: { detail: 'This report is issued.' } },
    }));
    renderDetail();
    await editClient('Renamed Corp');
    const reads = mocked.getClientReport.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: 'Save draft' }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
    await waitFor(() => expect(mocked.getClientReport.mock.calls.length).toBeGreaterThan(reads));
    expect(screen.getByTestId('report-save-status')).toHaveTextContent('Unsaved changes — Client & engagement');
  });

  it('a failed push-up changes nothing: the edits stay pending', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    mocked.promoteClientReportDetails.mockRejectedValue(new Error('down'));
    renderDetail();
    await editClient('Renamed Corp');
    fireEvent.click(screen.getByRole('button', { name: 'Update project defaults…' }));
    fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Update 1 default' }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByTestId('report-save-status')).toHaveTextContent('Unsaved changes — Client & engagement');
  });

  // Walkthrough 2026-10-15: after the stale-page refusal the dialog kept the
  // values it was refused for until the reader reloaded the page.
  it('refused because the draft changed (409), the draft is read again and the dialog shows what it prints now', async () => {
    mocked.getClientReport
      .mockResolvedValueOnce(report({ settings: { classification: 'Restricted' } }))
      .mockResolvedValue(report({ settings: { classification: 'Changed by B' } }));
    mocked.promoteClientReportDetails.mockRejectedValue(
      Object.assign(new Error('409'), { isAxiosError: true, response: { status: 409, data: { detail: 'This draft changed since you opened it (classification): reload it, then update the defaults.' } } }),
    );
    renderDetail();
    fireEvent.click(await screen.findByRole('button', { name: 'Update project defaults…' }));
    const dialog = within(await screen.findByRole('dialog'));
    expect(dialog.getByTestId('promote-classification')).toHaveTextContent('This report’s: “Restricted”');
    fireEvent.click(dialog.getByRole('button', { name: 'Update 1 default' }));
    await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
    await waitFor(() => expect(dialog.getByTestId('promote-classification')).toHaveTextContent('This report’s: “Changed by B”'));
  });
});

describe('Who sees it', () => {
  it('someone who may not edit the draft reads its details with no action at all', async () => {
    mocked.getClientReport.mockResolvedValue(report({
      can_edit: false, settings: { classification: 'Restricted' }, effective: { ...followed, classification: 'Restricted' },
    }));
    renderDetail();
    await screen.findByTestId('report-section-engagement');
    expect(screen.queryByRole('button', { name: /^Edit / })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Update project defaults…' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Use the project’s/ })).toBeNull();
    expect(screen.queryByTestId('report-action-bar')).not.toBeNull();       // status, no save
    expect(screen.queryByRole('button', { name: 'Save draft' })).toBeNull();
    // The report's images are read for them too, so a placed diagram shows
    // as itself, not as a text marker (third review 2026-10-15, N4).
    await waitFor(() => expect(mocked.listReportTextImages).toHaveBeenCalled());
  });

  it('an addendum’s summary of changes is its own: never offered as a project default', async () => {
    mocked.getClientReport.mockResolvedValue(report({
      kind: 'addendum', baseline: { id: 1, number: 1, title: 'R1', status: 'issued', issued_at: null },
      settings: { executive_summary: 'One new finding.' },
      followed: { ...followed, executive_summary: null }, effective: { ...followed, executive_summary: 'One new finding.' },
      followed_from: { ...from, executive_summary: null },
    }));
    renderDetail();
    await screen.findByRole('heading', { name: 'Summary of changes' });
    expect(screen.queryByTestId('report-section-summary-status')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Update project defaults…' })).toBeNull();
  });
});
