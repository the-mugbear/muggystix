/**
 * A draft's detail pushed up to the project's report defaults (owner,
 * 2026-10-14: "if a user modifies a draft they may want that change to become
 * the standard for future drafts").
 *
 * - "Make this the project's default" on an overridden detail: one call
 *   (`promoteClientReportDetails`) writes the project's default and drops the
 *   override; confirmed only when it replaces a different value the project
 *   holds.
 * - "Set for the project" on a followed detail: a PATCH of that one key of
 *   the project's defaults (`patchReportProfile`) — never the whole profile —
 *   then the draft is read again.
 * - Someone who may not edit the draft sees neither.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// The page reads its report id from the address (setupTests stubs the router).
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
  listReportTextImages: vi.fn(() => Promise.resolve([])),
  downloadClientReportScope: vi.fn(),
  listProjectMembers: vi.fn(),
  getProjectReportTeam: vi.fn(),
  getReportJob: vi.fn(),
  downloadReportJob: vi.fn(),
  fetchReportJobBlob: vi.fn(),
  draftReportWithAI: vi.fn(),
  listLLMProviders: vi.fn(),
}));
const confirmMock = vi.fn();
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, confirmMock] }));
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
  client_name: 'Example Corp', classification: null, engagement_type: null, testers: [], distribution: [],
  system_description: null, applications: null, thick_clients: null, other_targets: null,
  author_name: 'Installation Ltd', disclaimer: null,
};
const from = {
  client_name: 'project', classification: null, engagement_type: null, testers: null, distribution: null,
  system_description: null, applications: null, thick_clients: null, other_targets: null,
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
const button = (name: string) => screen.getByRole('button', { name });

beforeEach(() => {
  vi.clearAllMocks();
  resetProjectMembersCache();
  mocked.listReportTemplates.mockResolvedValue([
    { name: 'pentest', title: 'Penetration test report', description: '', formats: ['html'] },
  ]);
  mocked.listProjectMembers.mockResolvedValue([]);
});

describe('Make this the project’s default', () => {
  it('replacing a different value the project holds is confirmed first, naming both', async () => {
    mocked.getClientReport.mockResolvedValue(report({
      settings: { client_name: 'Renamed Corp' }, effective: { ...followed, client_name: 'Renamed Corp' },
    }));
    mocked.promoteClientReportDetails.mockResolvedValue(report({
      followed: { ...followed, client_name: 'Renamed Corp' }, effective: { ...followed, client_name: 'Renamed Corp' },
    }));
    renderDetail();
    confirmMock.mockResolvedValueOnce(false);
    fireEvent.click(await screen.findByRole('button', { name: 'Make Client the project’s default' }));
    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    const asked = confirmMock.mock.calls[0][0];
    expect(asked.title).toBe('Replace the project’s Client?');
    const body = render(<div>{asked.body}</div>);
    expect(body.container).toHaveTextContent('The project’s default is “Example Corp”. It becomes “Renamed Corp”.');
    expect(body.container).toHaveTextContent(/Every other draft that follows the project’s Client changes with it/);
    body.unmount();
    expect(mocked.promoteClientReportDetails).not.toHaveBeenCalled();

    confirmMock.mockResolvedValueOnce(true);
    fireEvent.click(button('Make Client the project’s default'));
    await waitFor(() => expect(mocked.promoteClientReportDetails).toHaveBeenCalledWith(1, 5, { client_name: 'Renamed Corp' }));
    // The answer replaces the draft: it follows the new default.
    expect(await screen.findByRole('group', { name: 'Client' })).toHaveTextContent('Renamed Corp');
    expect(toastMock.success).toHaveBeenCalledWith('Client is now the project’s default, and this draft follows it again.');
  });

  it('a value the project does not hold is made its default at once', async () => {
    mocked.getClientReport.mockResolvedValue(report({ settings: { classification: 'Secret' } }));
    mocked.promoteClientReportDetails.mockResolvedValue(report());
    renderDetail();
    fireEvent.click(await screen.findByRole('button', { name: 'Make Classification the project’s default' }));
    await waitFor(() => expect(mocked.promoteClientReportDetails).toHaveBeenCalledWith(1, 5, { classification: 'Secret' }));
    expect(confirmMock).not.toHaveBeenCalled();
  });

  it('an unsaved edit of another detail survives it', async () => {
    mocked.getClientReport.mockResolvedValue(report({ settings: { classification: 'Secret', client_name: 'Mine' } }));
    mocked.promoteClientReportDetails.mockResolvedValue(report({ settings: { client_name: 'Mine' } }));
    renderDetail();
    fireEvent.change(await screen.findByRole('textbox', { name: 'Client' }), { target: { value: 'Mine, edited' } });
    fireEvent.click(button('Make Classification the project’s default'));
    await waitFor(() => expect(mocked.promoteClientReportDetails).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByRole('textbox', { name: 'Classification' })).toBeNull());
    expect(screen.getByRole('textbox', { name: 'Client' })).toHaveValue('Mine, edited');
  });
});

describe('Set for the project', () => {
  it('writes only that key of the project’s defaults, then reads the draft again', async () => {
    mocked.getClientReport
      .mockResolvedValueOnce(report())
      .mockResolvedValue(report({ followed: { ...followed, engagement_type: 'Internal' }, followed_from: { ...from, engagement_type: 'project' } }));
    mocked.patchReportProfile.mockResolvedValue({});
    renderDetail();
    fireEvent.click(await screen.findByRole('button', { name: 'Set Engagement type for the project' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Engagement type' }), { target: { value: 'Internal' } });
    fireEvent.click(button('Save Engagement type for the project'));
    await waitFor(() => expect(mocked.patchReportProfile).toHaveBeenCalledWith(1, { engagement_type: 'Internal' }));
    await waitFor(() => expect(mocked.getClientReport).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.getByRole('group', { name: 'Engagement type' })).toHaveTextContent('Internal'));
    expect(mocked.updateClientReport).not.toHaveBeenCalled();      // never an override
    expect(toastMock.success).toHaveBeenCalledWith(
      'Engagement type is saved to the project’s defaults: every draft that follows them prints it.');
  });

  it('the author from the installation becomes a project value', async () => {
    mocked.getClientReport.mockResolvedValue(report());
    mocked.patchReportProfile.mockResolvedValue({});
    renderDetail();
    expect(await screen.findByRole('group', { name: 'Author' })).toHaveAccessibleDescription(/From the installation’s defaults/);
    fireEvent.click(button('Set Author for the project'));
    fireEvent.click(button('Save Author for the project'));
    await waitFor(() => expect(mocked.patchReportProfile).toHaveBeenCalledWith(1, { author_name: 'Installation Ltd' }));
  });
});

describe('Who sees the actions', () => {
  it('someone who may not edit the draft reads its details with no action at all', async () => {
    mocked.getClientReport.mockResolvedValue(report({ can_edit: false, can_discard: false, settings: { client_name: 'Mine' } }));
    renderDetail();
    await screen.findByRole('heading', { name: /Report details/ });
    expect(screen.queryByRole('button', { name: /project’s default|for the project|Override|Revert/ })).toBeNull();
  });
});
