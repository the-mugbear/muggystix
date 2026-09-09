import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// setupTests mocks useNavigate to a throwaway fn; give this file a spy so the
// return target the detail navigates to is observable.
const navigateSpy = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigateSpy };
});
vi.mock('../../services/api', () => ({
  getFinding: vi.fn(),
  getFindingHistory: vi.fn(),
  setFindingStatus: vi.fn(),
  updateFinding: vi.fn(),
  removeFindingEndpoint: vi.fn(),
  addFindingHosts: vi.fn(),
  getHostNotes: vi.fn(),
  listProjectMembers: vi.fn(),
  getFindingNotes: vi.fn(),
  createFindingNote: vi.fn(),
  uploadFindingNoteAttachment: vi.fn(),
}));
const confirmMock = vi.fn();
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, confirmMock] }));
const toastMock = { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 1, username: 'tester' }, hasPermission: () => true }),
}));

import * as api from '../../services/api';
import FindingDetail from '../../pages/FindingDetail';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const finding = (over: Record<string, unknown> = {}) => ({
  id: 7, project_id: 1, title: 'Weak TLS on portal', severity: 'high', status: 'open', source: 'manual',
  owner_id: null, owner_name: null, evidence_annotation_id: null, vuln_id: null, exec_result_id: null,
  host_count: 0, hosts: [], created_at: '2026-08-01T00:00:00Z', updated_at: null, ...over,
});

const renderAt = (url: string) =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/findings/:findingId" element={<FindingDetail />} />
      </Routes>
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  mocked.getFinding.mockResolvedValue(finding());
  mocked.getFindingHistory.mockResolvedValue([]);
  mocked.getFindingNotes.mockResolvedValue([]);
  mocked.listProjectMembers.mockResolvedValue([]);
  mocked.setFindingStatus.mockResolvedValue(undefined);
});

describe('FindingDetail — C2: metadata edits keep the comment draft', () => {
  it('a status change does not unmount the comment thread', async () => {
    const user = userEvent.setup();
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');

    const composer = screen.getByLabelText('New comment');
    fireEvent.change(composer, { target: { value: 'repro: openssl s_client …' } });

    // After the change, the refresh returns the new status.
    mocked.getFinding.mockResolvedValue(finding({ status: 'confirmed' }));
    await user.click(screen.getByLabelText('Finding status'));
    await user.click(await screen.findByRole('option', { name: 'Confirmed' }));

    await waitFor(() => expect(mocked.setFindingStatus).toHaveBeenCalledWith(7, 'confirmed', undefined));
    // Refresh happened (finding + history re-fetched) …
    await waitFor(() => expect(mocked.getFinding).toHaveBeenCalledTimes(2));
    // … and the draft the analyst typed is still in the still-mounted composer.
    expect(screen.getByLabelText('New comment')).toHaveValue('repro: openssl s_client …');
    // The whole-page skeleton never replaced the content.
    expect(screen.getByText('Weak TLS on portal')).toBeInTheDocument();
  });
});

describe('FindingDetail — M2: history is not a prerequisite', () => {
  it('renders the finding when history fails, with an explicit unavailable message and Retry', async () => {
    mocked.getFindingHistory.mockRejectedValueOnce(new Error('boom'));
    renderAt('/findings/7');

    await screen.findByText('Weak TLS on portal');
    expect(await screen.findByText(/History unavailable/)).toBeInTheDocument();
    // Not presented as "nothing happened".
    expect(screen.queryByText(/No status changes recorded yet/)).toBeNull();

    mocked.getFindingHistory.mockResolvedValueOnce([
      { id: 1, from_status: 'open', to_status: 'confirmed', changed_by_id: 1, changed_by_name: 'ana', summary: null, created_at: '2026-08-02T00:00:00Z' },
    ]);
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }));
    await waitFor(() => expect(screen.getByText('Confirmed')).toBeInTheDocument());
    expect(screen.queryByText(/History unavailable/)).toBeNull();
  });
});

describe('FindingDetail — M1: return to the queue it came from', () => {
  it('the Findings action returns to the carried list URL (filters + page + sort)', async () => {
    const from = '/findings?status=all&severity=high&page=3&sort=severity&dir=desc';
    renderAt(`/findings/7?from=${encodeURIComponent(from)}`);
    await screen.findByText('Weak TLS on portal');
    fireEvent.click(screen.getByRole('button', { name: /Findings/ }));
    expect(navigateSpy).toHaveBeenCalledWith(from);
  });

  it.each([
    'https://evil.example/findings',
    '//evil.example/findings',
    '/hosts?x=1',
    '/findings/9',
  ])('falls back to the bare list for an unsafe or foreign return target: %s', async (bad) => {
    renderAt(`/findings/7?from=${encodeURIComponent(bad)}`);
    await screen.findByText('Weak TLS on portal');
    fireEvent.click(screen.getByRole('button', { name: /Findings/ }));
    expect(navigateSpy).toHaveBeenCalledWith('/findings');
  });

  it('a direct link with no return state goes to the bare list', async () => {
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    fireEvent.click(screen.getByRole('button', { name: /Findings/ }));
    expect(navigateSpy).toHaveBeenCalledWith('/findings');
  });
});
