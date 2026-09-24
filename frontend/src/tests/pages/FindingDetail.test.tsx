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
  setFindingEndpointStatus: vi.fn(),
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

describe('FindingDetail — item 7: each endpoint has its own state', () => {
  const twoHosts = () => finding({
    status: 'confirmed',
    host_count: 2,
    endpoint_status_counts: { open: 1, remediated: 1 },
    hosts: [
      { id: 31, host_id: 5, ip_address: '10.0.0.5', hostname: null, name_id: null, fqdn: null, host_status: 'open' },
      { id: 32, host_id: 6, ip_address: '10.0.0.6', hostname: 'web2', name_id: null, fqdn: null, host_status: 'remediated' },
    ],
  });

  it('says the status is the issue\'s and shows how the endpoints stand', async () => {
    mocked.getFinding.mockResolvedValue(twoHosts());
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    expect(screen.getByText(/The status above is the issue/)).toBeInTheDocument();
    expect(screen.getByText('still present on 1 of 2 · 1 remediated')).toBeInTheDocument();
  });

  it('v5.290.0 — an open endpoint reads "Still present", never "Open", beside a Confirmed finding', async () => {
    mocked.getFinding.mockResolvedValue(twoHosts());
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    expect(screen.getAllByText('Still present').length).toBeGreaterThan(0);
    expect(screen.queryByText('Open here')).not.toBeInTheDocument();
  });

  it('says "all still present" when every endpoint is', async () => {
    mocked.getFinding.mockResolvedValue(finding({
      status: 'confirmed', host_count: 1, endpoint_status_counts: { open: 1 },
      hosts: [{ id: 31, host_id: 5, ip_address: '10.0.0.5', hostname: null, name_id: null, fqdn: null, host_status: 'open' }],
    }));
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    expect(screen.getByText(/all still present\./)).toBeInTheDocument();
    expect(screen.queryByText(/all open/)).not.toBeInTheDocument();
  });

  it('changing one endpoint\'s state calls the endpoint route, not the finding status', async () => {
    const user = userEvent.setup();
    mocked.getFinding.mockResolvedValue(twoHosts());
    mocked.setFindingEndpointStatus.mockResolvedValue(undefined);
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');

    await user.click(screen.getByLabelText('State of 10.0.0.5'));
    await user.click(await screen.findByRole('option', { name: 'Retest here' }));

    await waitFor(() => expect(mocked.setFindingEndpointStatus).toHaveBeenCalledWith(7, 31, 'retest'));
    expect(mocked.setFindingStatus).not.toHaveBeenCalled();
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

describe('FindingDetail — v5.256.0: the author renames or deletes', () => {
  it('offers neither to someone who may not modify it', async () => {
    mocked.getFinding.mockResolvedValue(finding({ can_modify: false, created_by_name: 'Alice' }));
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    expect(screen.getByText('Alice')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Rename/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Delete/ })).toBeNull();
  });

  it('renames in place', async () => {
    mocked.getFinding.mockResolvedValue(finding({ can_modify: true }));
    mocked.updateFinding.mockResolvedValue(finding({ can_modify: true, title: 'TLS 1.0 on portal' }));
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');
    fireEvent.click(screen.getByRole('button', { name: /Rename/ }));
    fireEvent.change(screen.getByLabelText('Finding title'), { target: { value: '  TLS 1.0 on portal ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mocked.updateFinding).toHaveBeenCalledWith(7, { title: 'TLS 1.0 on portal' }));
    expect(await screen.findByText('TLS 1.0 on portal')).toBeInTheDocument();
  });

  it('deletes only after confirmation, then returns to the list', async () => {
    mocked.getFinding.mockResolvedValue(finding({ can_modify: true }));
    mocked.deleteFinding.mockResolvedValue(undefined);
    renderAt('/findings/7');
    await screen.findByText('Weak TLS on portal');

    confirmMock.mockResolvedValueOnce(false);
    fireEvent.click(screen.getByRole('button', { name: /Delete/ }));
    await waitFor(() => expect(confirmMock).toHaveBeenCalledTimes(1));
    expect(mocked.deleteFinding).not.toHaveBeenCalled();

    confirmMock.mockResolvedValueOnce(true);
    fireEvent.click(screen.getByRole('button', { name: /Delete/ }));
    await waitFor(() => expect(mocked.deleteFinding).toHaveBeenCalledWith(7));
    await waitFor(() => expect(navigateSpy).toHaveBeenCalledWith('/findings'));
  });
});

describe('FindingDetail — report text (v5.260.0)', () => {
  const reportText = (over: Record<string, unknown> = {}) => ({
    description: 'TLS 1.0 is enabled.', impact: null, recommendation: null, references: null,
    steps_to_reproduce: null, cvss_vector: null, cvss_score: null, cvss_score_from_vector: false, ...over,
  });

  it('shows the text as written and names what is still empty', async () => {
    mocked.getFinding.mockResolvedValue(finding({ report_text: reportText() }));
    renderAt('/findings/7');
    expect(await screen.findByText('TLS 1.0 is enabled.')).toBeInTheDocument();
    expect(screen.getByText('impact, recommendation')).toBeInTheDocument();
    // Not the author and not an admin: no editor.
    expect(screen.queryByRole('button', { name: /^Edit$/ })).not.toBeInTheDocument();
  });

  it('saves only the fields that changed', async () => {
    mocked.getFinding.mockResolvedValue(finding({ can_modify: true, report_text: reportText() }));
    mocked.updateFinding.mockResolvedValue(
      finding({ can_modify: true, report_text: reportText({ impact: 'Traffic can be read.' }) }),
    );
    renderAt('/findings/7');
    await screen.findByText('TLS 1.0 is enabled.');
    fireEvent.click(screen.getByRole('button', { name: /^Edit$/ }));
    fireEvent.change(screen.getByLabelText('Impact'), { target: { value: 'Traffic can be read.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mocked.updateFinding).toHaveBeenCalledWith(7, { impact: 'Traffic can be read.' }));
    expect(await screen.findByText('Traffic can be read.')).toBeInTheDocument();
  });

  it('refuses an out-of-range score before sending', async () => {
    mocked.getFinding.mockResolvedValue(finding({ can_modify: true, report_text: reportText() }));
    renderAt('/findings/7');
    await screen.findByText('TLS 1.0 is enabled.');
    fireEvent.click(screen.getByRole('button', { name: /^Edit$/ }));
    fireEvent.change(screen.getByLabelText('Score'), { target: { value: '12' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('A CVSS score is a number from 0.0 to 10.0.')).toBeInTheDocument();
    expect(mocked.updateFinding).not.toHaveBeenCalled();
  });
});

describe('FindingDetail — add affected hosts', () => {
  const attached = {
    id: 90, host_id: 11, ip_address: '10.0.0.11', hostname: 'dc01', host_status: 'open', name_id: null, fqdn: null,
  };
  const hostRow = (id: number, ip: string, hostname: string | null = null) => ({ id, ip_address: ip, hostname });

  it('searches, picks several hosts across searches, and adds them in one request', async () => {
    const user = userEvent.setup();
    mocked.getFinding.mockResolvedValue(finding({ host_count: 1, hosts: [attached] }));
    mocked.getHosts.mockImplementation(async ({ search }: { search?: string }) => ({
      items: search === 'web'
        ? [hostRow(13, '10.0.0.13', 'web01.' + 'x'.repeat(200))]
        : [hostRow(11, '10.0.0.11', 'dc01'), hostRow(12, '10.0.0.12', 'dc02')],
      total: null,
    }));
    mocked.addFindingHosts.mockResolvedValue(finding({
      host_count: 3,
      hosts: [
        attached,
        { ...attached, id: 91, host_id: 12, ip_address: '10.0.0.12' },
        { ...attached, id: 92, host_id: 13, ip_address: '10.0.0.13' },
      ],
    }));

    renderAt('/findings/7');
    await user.click(await screen.findByRole('button', { name: /Add hosts/ }));

    // The host already on the finding is shown but cannot be picked.
    const dc01 = await screen.findByRole('checkbox', { name: 'Add 10.0.0.11' });
    expect(dc01).toBeDisabled();
    expect(screen.getByText('already affected')).toBeInTheDocument();

    await user.click(screen.getByRole('checkbox', { name: 'Add 10.0.0.12' }));
    // A second search keeps the first pick.
    await user.type(screen.getByLabelText('Search hosts'), 'web');
    await user.click(await screen.findByRole('checkbox', { name: 'Add 10.0.0.13' }));
    expect(screen.getByLabelText('Selected hosts')).toHaveTextContent('10.0.0.12');

    await user.click(screen.getByRole('button', { name: 'Add 2 hosts' }));
    await waitFor(() => expect(mocked.addFindingHosts).toHaveBeenCalledWith(7, [12, 13]));
    expect(await screen.findByText('Affected hosts (3)')).toBeInTheDocument();
    expect(toastMock.success).toHaveBeenCalledWith('Added 2 hosts.');
    expect(mocked.getFindingHistory).toHaveBeenCalledTimes(2); // refreshed after the add
  });

  it('keeps the dialog open with the reason when the add fails', async () => {
    const user = userEvent.setup();
    mocked.getHosts.mockResolvedValue({ items: [hostRow(12, '10.0.0.12')], total: null });
    mocked.addFindingHosts.mockRejectedValue(new Error('Hosts [12] are not in this project.'));
    renderAt('/findings/7');
    await user.click(await screen.findByRole('button', { name: /Add hosts/ }));
    await user.click(await screen.findByRole('checkbox', { name: 'Add 10.0.0.12' }));
    await user.click(screen.getByRole('button', { name: 'Add 1 host' }));
    expect(await screen.findByText(/not in this project|could not be added/)).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
  });
});
