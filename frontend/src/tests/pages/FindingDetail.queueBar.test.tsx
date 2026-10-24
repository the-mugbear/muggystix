/**
 * The finding page's Previous / Next through the Findings list it was opened
 * from (owner, 2026-10-23): the list decides what is walked.  The page asks
 * the server with the `from` list's own filters and order
 * (`findingQueueFilters`, the list's reading of its address), keeps `from`
 * on each step, steps through the unsaved-text guard, and answers `j` / `k`.
 */
import { render, screen, fireEvent, within } from '@testing-library/react';
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
  getFindingNeighbours: vi.fn().mockResolvedValue({ position: 1, total: 1, previous: null, next: null }),
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
const renderAt = (url: string) =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <Routes><Route path="/findings/:findingId" element={<FindingDetail />} /></Routes>
    </MemoryRouter>,
  );


const neighbours = (over: Record<string, unknown> = {}) => ({
  position: 2, total: 3,
  previous: { id: 6, title: 'Open SMB' }, next: { id: 8, title: 'Default credentials' }, ...over,
});
const FROM = '/findings?owner=me&severity=high&sort=title&dir=desc&page=2';
const at = (id: number) => `/findings/${id}?from=${encodeURIComponent(FROM)}`;

beforeEach(() => {
  vi.clearAllMocks();
  URL.createObjectURL = vi.fn(() => 'blob:img');
  mocked.getFinding.mockResolvedValue(finding({ revision: 4 }));
  mocked.getFindingHistory.mockResolvedValue([]);
  mocked.getFindingNotes.mockResolvedValue({ items: [], total: 0 });
  mocked.listProjectMembers.mockResolvedValue([]);
  mocked.getFindingNeighbours.mockResolvedValue(neighbours());
});

describe('Previous / Next through the Findings list', () => {
  it('asks for the list it came from — its filters and order, never its page', async () => {
    renderAt(at(7));
    const bar = await screen.findByRole('navigation', { name: 'Findings list' });
    expect(await within(bar).findByTestId('findings-queue-position')).toHaveTextContent('Finding 2 of 3 in the list');
    expect(mocked.getFindingNeighbours).toHaveBeenCalledWith(1, 7, {
      status: 'active', severity: 'high', owner_id: 1, sort: 'title', dir: 'desc',
    }, expect.anything());
  });

  it('with no list to return to, walks the default list', async () => {
    renderAt('/findings/7');
    await screen.findByTestId('findings-queue-bar');
    expect(mocked.getFindingNeighbours).toHaveBeenCalledWith(1, 7, {
      status: 'active', sort: 'severity', dir: 'asc',
    }, expect.anything());
  });

  it('steps keep the list, go through the page guard, and answer j / k', async () => {
    renderAt(at(7));
    const bar = await screen.findByRole('navigation', { name: 'Findings list' });
    const prev = await within(bar).findByRole('link', { name: 'Previous: Open SMB' });
    const next = within(bar).getByRole('link', { name: 'Next: Default credentials' });
    expect(prev).toHaveAttribute('href', at(6));
    expect(next).toHaveAttribute('href', at(8));

    fireEvent.click(next);
    expect(navigateSpy).toHaveBeenLastCalledWith(at(8));
    fireEvent.keyDown(window, { key: 'k' });
    expect(navigateSpy).toHaveBeenLastCalledWith(at(6));
    fireEvent.keyDown(window, { key: 'j' });
    expect(navigateSpy).toHaveBeenLastCalledWith(at(8));
  });

  it('the first has no Previous, the last no Next', async () => {
    mocked.getFindingNeighbours.mockResolvedValue(neighbours({ position: 1, total: 1, previous: null, next: null }));
    renderAt(at(7));
    const bar = await screen.findByRole('navigation', { name: 'Findings list' });
    await within(bar).findByText('Finding 1 of 1 in the list');
    expect(within(bar).queryByRole('link')).toBeNull();
    fireEvent.keyDown(window, { key: 'j' });
    expect(navigateSpy).not.toHaveBeenCalled();
  });

  it('a finding that left the list says so and steps from where it sat', async () => {
    mocked.getFindingNeighbours.mockResolvedValue(neighbours({ position: null, total: 2 }));
    renderAt(at(7));
    const bar = await screen.findByRole('navigation', { name: 'Findings list' });
    expect(await within(bar).findByTestId('findings-queue-position')).toHaveTextContent('No longer in the list (2 remain)');
    expect(within(bar).getByRole('link', { name: 'Next: Default credentials' })).toBeInTheDocument();
  });

  it('a failed read says so; the page stays', async () => {
    mocked.getFindingNeighbours.mockRejectedValue(new Error('boom'));
    renderAt(at(7));
    const bar = await screen.findByRole('navigation', { name: 'Findings list' });
    expect(bar).toHaveTextContent(/boom|Could not read the list/);
    expect(screen.getByText('Weak TLS on portal', { selector: 'h1' })).toBeInTheDocument();
  });
});
