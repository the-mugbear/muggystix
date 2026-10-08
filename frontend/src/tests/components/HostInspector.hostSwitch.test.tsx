/**
 * The inspector is one host's: stepping to another host starts a new one, so
 * nothing read or typed for the host that was left can be shown, or saved,
 * under the host now asked for.
 */
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});

const hostFixture = (id: number) => ({
  id, ip_address: `10.0.0.${id}`, hostname: `h${id}`, state: 'up',
  ports: [], assignees: [], tags: [], vulnerabilities: [], notes: [],
  discoveries: [], follow: null,
  os_name: null, os_family: null, os_type: null, os_generation: null,
  os_vendor: null, os_accuracy: null, smb_signing: null,
  web_interface_count: 0, netexec_result_count: 0, dns_record_count: 0,
  first_seen: '2026-06-14T00:00:00Z', last_seen: '2026-06-14T00:00:00Z',
});

const api = vi.hoisted(() => ({
  getHost: vi.fn(),
  getHostConflicts: vi.fn(),
  createAnnotation: vi.fn(),
  followHost: vi.fn(),
}));

vi.mock('../../services/api', () => ({
  getHost: api.getHost,
  getHostConflicts: api.getHostConflicts,
  listHostTests: vi.fn().mockResolvedValue({ items: [], total: 0, has_more: false }),
  listProposals: vi.fn().mockResolvedValue({ items: [], total: 0, has_more: false }),
  listAgentSessions: vi.fn().mockResolvedValue({ project_id: 1, sessions: [], total: 0 }),
  listEvidenceRecords: vi.fn().mockResolvedValue({ items: [], total: 0, has_more: false }),
  getHostFollowers: vi.fn().mockResolvedValue([]),
  recordHostView: vi.fn().mockResolvedValue(undefined),
  listProjectMembers: vi.fn().mockResolvedValue([]),
  followHost: api.followHost, unfollowHost: vi.fn(), assignHost: vi.fn(), unassignHost: vi.fn(),
  createNote: vi.fn(), updateAnnotation: vi.fn(), deleteAnnotation: vi.fn(),
  createAnnotation: api.createAnnotation,
  uploadNoteAttachment: vi.fn(),
  promoteVulnerability: vi.fn(), previewPromoteVulnerability: vi.fn(),
  updateHostTest: vi.fn(), getHostNotes: vi.fn().mockResolvedValue([]),
}));

const toastMock = vi.hoisted(() => ({
  success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn(), show: vi.fn(),
}));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));

vi.mock('../../components/WebInterfacesCard', () => ({ default: () => null }));
vi.mock('../../components/NseScriptsCard', () => ({ default: () => null }));
vi.mock('../../components/NetExecCard', () => ({ default: () => null }));
vi.mock('../../components/HostFindingsCard', () => ({ default: () => null }));
vi.mock('../../components/HostNamesCard', () => ({ default: () => null }));
vi.mock('../../components/host-inspector/PortDetailsCard', () => ({ default: () => null }));

import HostInspector from '../../components/HostInspector';

const inspector = (hostId: number) => <MemoryRouter><HostInspector hostId={hostId} /></MemoryRouter>;
const unavailable = () => Promise.reject({ isAxiosError: true, response: { status: 503 } });

describe('HostInspector — stepping to a host that cannot be loaded', () => {
  beforeEach(() => {
    Object.values(api).forEach((m) => m.mockReset());
    api.getHostConflicts.mockResolvedValue([]);
  });

  it('says the host could not be loaded, shows nothing of the host that was left, and offers nothing to save', async () => {
    api.getHost.mockImplementation((id: number) => (id === 1 ? Promise.resolve(hostFixture(1)) : unavailable()));
    const { rerender } = render(inspector(1));
    await waitFor(() => expect(screen.getByText('10.0.0.1')).toBeInTheDocument());
    // Something typed on the host being read.
    fireEvent.change(screen.getByLabelText('Note'), { target: { value: 'written while reading host one' } });

    rerender(inspector(2));
    expect(await screen.findByText('Unable to load host')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(screen.queryByText('10.0.0.1')).not.toBeInTheDocument();
    // No composer and no review control: nothing here can write to host 2.
    expect(screen.queryByLabelText('Note')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /save note/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Start review/ })).not.toBeInTheDocument();
    expect(api.createAnnotation).not.toHaveBeenCalled();
    expect(api.followHost).not.toHaveBeenCalled();
  });

  it('Retry reads the host that was asked for', async () => {
    let fail = true;
    api.getHost.mockImplementation((id: number) => (
      id === 2 && fail ? unavailable() : Promise.resolve(hostFixture(id))
    ));
    const { rerender } = render(inspector(1));
    await waitFor(() => expect(screen.getByText('10.0.0.1')).toBeInTheDocument());
    rerender(inspector(2));
    await screen.findByText('Unable to load host');
    fail = false;
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.getByText('10.0.0.2')).toBeInTheDocument());
    expect(screen.queryByText('10.0.0.1')).not.toBeInTheDocument();
  });

  it('a conflict count read for the host that was left is not shown on the next host', async () => {
    api.getHost.mockImplementation((id: number) => Promise.resolve(hostFixture(id)));
    api.getHostConflicts.mockImplementation((id: number) => (id === 1
      ? Promise.resolve({ confidence: [], conflict_history: [], conflict_count: 3 })
      : Promise.reject(new Error('unavailable'))));
    const { rerender } = render(inspector(1));
    await waitFor(() => expect(screen.getByText('10.0.0.1')).toBeInTheDocument());
    await waitFor(() => expect(screen.getAllByText(/3 conflicts/i).length).toBeGreaterThan(0));
    rerender(inspector(2));
    await waitFor(() => expect(screen.getByText('10.0.0.2')).toBeInTheDocument());
    await waitFor(() => expect(api.getHostConflicts).toHaveBeenCalledWith(2));
    expect(screen.queryByText(/3 conflicts/i)).not.toBeInTheDocument();
  });
});
