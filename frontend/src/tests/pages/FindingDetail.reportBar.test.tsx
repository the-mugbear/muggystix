/**
 * Walkthrough 2026-10-14: a draft report's checklist opens a finding's report
 * text with `?report=<id>`; the finding page then shows which draft, where
 * this finding stands among those still missing text, Previous / Next through
 * them, and the way back — built from the report's own summary.
 */
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const navigateSpy = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigateSpy };
});
vi.mock('../../services/api', () => ({
  listEvidenceRecords: vi.fn().mockResolvedValue({ items: [], total: 0, has_more: false }),
  getFinding: vi.fn(),
  getFindingHistory: vi.fn(),
  setFindingStatus: vi.fn(),
  updateFinding: vi.fn(),
  deleteFinding: vi.fn(),
  removeFindingEndpoint: vi.fn(),
  addFindingHosts: vi.fn(),
  getHosts: vi.fn(),
  getHostNotes: vi.fn(),
  listProjectMembers: vi.fn(),
  getFindingNotes: vi.fn(),
  createFindingNote: vi.fn(),
  uploadFindingNoteAttachment: vi.fn(),
  getFindingImages: vi.fn().mockResolvedValue({ items: [], caption_max: 2000 }),
  getNoteAttachmentBlob: vi.fn().mockResolvedValue(new Blob(['img'])),
  setNoteAttachmentCaption: vi.fn(),
  setNoteAttachmentInReport: vi.fn(),
  deleteNoteAttachment: vi.fn(),
  listProposals: vi.fn().mockResolvedValue({ items: [], total: 0, has_more: false }),
  acceptProposal: vi.fn(),
  rejectProposal: vi.fn(),
  getClientReport: vi.fn(),
}));
const confirmMock = vi.fn();
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, confirmMock] }));
const toastMock = { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, username: 'tester', role: 'member' }, hasPermission: () => true }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'P', my_role: 'analyst' } }),
}));

import * as api from '../../services/api';
import FindingDetail from '../../pages/FindingDetail';
import { resetProjectMembersCache } from '../../hooks/useProjectMembers';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const reportText = (over: Record<string, unknown> = {}) => ({
  description: 'TLS 1.0 is enabled.', impact: null, recommendation: null, references: null,
  steps_to_reproduce: null, cvss_vector: null, cvss_score: null, cvss_score_from_vector: false, ...over,
});
const finding = (over: Record<string, unknown> = {}) => ({
  id: 7, project_id: 1, title: 'Weak TLS on portal', severity: 'high', status: 'confirmed', source: 'manual',
  owner_id: null, owner_name: null, evidence_annotation_id: null, vuln_id: null,
  host_count: 0, hosts: [], created_at: '2026-08-01T00:00:00Z', updated_at: null,
  can_modify: true, report_text: reportText(), ...over,
});
const missing = (id: number, title: string) => ({ id, ref: `F-${id}`, title, missing: ['impact'] });
const draft = (missingText: unknown[]) => ({
  id: 24, project_id: 1, kind: 'full', status: 'draft', number: null, title: 'Q3 internal',
  summary: { missing_text: missingText },
});
const THREE = [missing(6, 'Open SMB'), missing(7, 'Weak TLS on portal'), missing(8, 'Default credentials')];

const renderAt = (url: string) =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <Routes><Route path="/findings/:findingId" element={<FindingDetail />} /></Routes>
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  URL.createObjectURL = vi.fn(() => 'blob:img');
  resetProjectMembersCache();
  mocked.getFinding.mockResolvedValue(finding());
  mocked.getFindingHistory.mockResolvedValue([]);
  mocked.getFindingNotes.mockResolvedValue({ items: [], total: 0 });
  mocked.listProjectMembers.mockResolvedValue([]);
  mocked.getClientReport.mockResolvedValue(draft(THREE));
});

describe('The report draft bar on a finding', () => {
  it('is not there without `report=`, and the report is not read', async () => {
    renderAt('/findings/7?edit=report-text');
    await screen.findByText('Weak TLS on portal', { selector: 'h1' });
    expect(screen.queryByTestId('report-draft-bar')).toBeNull();
    expect(mocked.getClientReport).not.toHaveBeenCalled();
  });

  it('says where this finding stands, and steps to the previous, the next and back', async () => {
    renderAt('/findings/7?edit=report-text&report=24');
    const bar = await screen.findByRole('navigation', { name: 'Report draft' });
    expect(await within(bar).findByTestId('report-draft-position'))
      .toHaveTextContent('finding 2 of 3 findings still missing text');
    expect(bar).toHaveTextContent('Report draft #24 · Q3 internal');
    expect(mocked.getClientReport).toHaveBeenCalledWith(1, 24, expect.anything());

    const prev = within(bar).getByRole('link', { name: 'Previous: Open SMB' });
    const next = within(bar).getByRole('link', { name: 'Next: Default credentials' });
    const back = within(bar).getByRole('link', { name: 'Back to the report' });
    // Previous and Next keep the report and the editor open.
    expect(prev).toHaveAttribute('href', '/findings/6?edit=report-text&report=24');
    expect(next).toHaveAttribute('href', '/findings/8?edit=report-text&report=24');
    expect(back).toHaveAttribute('href', '/reports/24#report-checklist');

    fireEvent.click(next);
    expect(navigateSpy).toHaveBeenCalledWith('/findings/8?edit=report-text&report=24');
    fireEvent.click(back);
    expect(navigateSpy).toHaveBeenCalledWith('/reports/24#report-checklist');
  });

  it('the first has no Previous, the last no Next', async () => {
    mocked.getClientReport.mockResolvedValue(draft([missing(7, 'Weak TLS on portal'), missing(8, 'Default credentials')]));
    renderAt('/findings/7?report=24');
    const bar = await screen.findByRole('navigation', { name: 'Report draft' });
    await within(bar).findByText('finding 1 of 2 findings still missing text');
    expect(within(bar).queryByRole('link', { name: /^Previous/ })).toBeNull();
    expect(within(bar).getByRole('link', { name: /^Next/ })).toBeInTheDocument();
  });

  it('a finding not in the list says so, and Next is the first that is', async () => {
    mocked.getClientReport.mockResolvedValue(draft([missing(6, 'Open SMB'), missing(8, 'Default credentials')]));
    renderAt('/findings/7?report=24');
    const bar = await screen.findByRole('navigation', { name: 'Report draft' });
    expect(await within(bar).findByTestId('report-draft-position'))
      .toHaveTextContent('this finding is not among the 2 findings still missing text');
    expect(within(bar).queryByRole('link', { name: /^Previous/ })).toBeNull();
    expect(within(bar).getByRole('link', { name: 'Next: Open SMB' }))
      .toHaveAttribute('href', '/findings/6?edit=report-text&report=24');
  });

  it('after its text is saved, the report is read again; Next is the finding that took its place', async () => {
    mocked.getClientReport
      .mockResolvedValueOnce(draft(THREE))
      .mockResolvedValue(draft([missing(6, 'Open SMB'), missing(8, 'Default credentials')]));
    mocked.updateFinding.mockResolvedValue(finding({ report_text: reportText({ impact: 'Traffic can be read.' }) }));
    renderAt('/findings/7?report=24');
    const bar = await screen.findByRole('navigation', { name: 'Report draft' });
    await within(bar).findByText('finding 2 of 3 findings still missing text');

    fireEvent.click(screen.getByRole('button', { name: /^Edit$/ }));
    fireEvent.change(screen.getByLabelText('Impact'), { target: { value: 'Traffic can be read.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save report text' }));
    await waitFor(() => expect(mocked.getClientReport).toHaveBeenCalledTimes(2));

    expect(await within(bar).findByText('this finding has its report text now; 2 findings still missing text'))
      .toBeInTheDocument();
    expect(within(bar).getByRole('link', { name: 'Next: Default credentials' }))
      .toHaveAttribute('href', '/findings/8?edit=report-text&report=24');
    expect(within(bar).getByRole('link', { name: 'Previous: Open SMB' }))
      .toHaveAttribute('href', '/findings/6?edit=report-text&report=24');
  });

  it('leaving with unsaved report text asks first', async () => {
    renderAt('/findings/7?edit=report-text&report=24');
    const bar = await screen.findByRole('navigation', { name: 'Report draft' });
    await within(bar).findByText('finding 2 of 3 findings still missing text');
    fireEvent.change(await screen.findByLabelText('Impact'), { target: { value: 'Not saved yet.' } });
    confirmMock.mockResolvedValueOnce(false);
    fireEvent.click(within(bar).getByRole('link', { name: /^Next/ }));
    await waitFor(() => expect(confirmMock).toHaveBeenCalled());
    expect(navigateSpy).not.toHaveBeenCalled();
  });
});
