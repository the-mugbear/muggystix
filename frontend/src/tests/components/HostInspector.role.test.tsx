/**
 * Review 2026-10-01 R32 — the inspector's write controls follow the PROJECT
 * role (every member passed the old account-role check): a viewer reads the
 * host without a note composer, tag editing, or the test actions.  R34 — a
 * failed "show informational" is said.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});

const role = vi.hoisted(() => ({ value: 'viewer' as string }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 9, username: 'reader', role: 'member' }, hasPermission: (r: string) => r !== 'admin' }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Demo', my_role: role.value } }),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));

const host = {
  id: 1, ip_address: '10.0.0.1', hostname: 'h1', state: 'up',
  ports: [], assignees: [], tags: [{ id: 3, name: 'owned', color: null }], vulnerabilities: [],
  notes: [{
    id: 50, host_id: 1, body: 'seen on the DMZ sweep', parent_id: null, author_id: 2, author_name: 'ana',
    note_type: null, pinned: false, attachments: [], created_at: '2026-06-14T00:00:00Z', updated_at: null,
  }],
  discoveries: [], follow: null,
  os_name: null, os_family: null, os_type: null, os_generation: null,
  os_vendor: null, os_accuracy: null, smb_signing: null,
  web_interface_count: 0, netexec_result_count: 0, dns_record_count: 0,
  first_seen: '2026-06-14T00:00:00Z', last_seen: '2026-06-14T00:00:00Z',
  informational_count: 4, informational_included: false,
};
const test = {
  id: 2, host_id: 1, host_ip: '10.0.0.1', tool: 'nmap', description: 'todo-test',
  command: 'nmap -sV {ip}', rationale: 'because', expected_result: null, references: null,
  target_fqdn: null, priority: 'high', label: null, status: 'proposed',
  assigned_to_id: null, assigned_to: null, created_by: 'ann', source: 'person',
  agent_session_id: null, agent_model: null, agent_client: null, tester_summary: null,
  dismissed_reason: null, revision: 1, evidence_count: 0, created_at: '2026-06-14T00:00:00Z',
};

vi.mock('../../services/api', () => ({
  getHost: vi.fn(),
  getHostConflicts: vi.fn().mockResolvedValue([]),
  listHostTests: vi.fn(),
  listProposals: vi.fn().mockResolvedValue({ items: [], total: 0, has_more: false }),
  listAssistSessions: vi.fn().mockResolvedValue([]),
  listEvidenceRecords: vi.fn().mockResolvedValue({ items: [], total: 0, has_more: false }),
  getHostFollowers: vi.fn().mockResolvedValue([]),
  recordHostView: vi.fn().mockResolvedValue(undefined),
  listProjectMembers: vi.fn().mockResolvedValue([]),
  listHostTags: vi.fn().mockResolvedValue([]),
  followHost: vi.fn(), unfollowHost: vi.fn(), assignHost: vi.fn(), unassignHost: vi.fn(),
  createNote: vi.fn(), updateAnnotation: vi.fn(), deleteAnnotation: vi.fn(),
  uploadNoteAttachment: vi.fn(),
  promoteVulnerability: vi.fn(), previewPromoteVulnerability: vi.fn(),
  updateHostTest: vi.fn(), getHostNotes: vi.fn().mockResolvedValue([]),
}));
vi.mock('../../components/WebInterfacesCard', () => ({ default: () => null }));
vi.mock('../../components/NseScriptsCard', () => ({ default: () => null }));
vi.mock('../../components/NetExecCard', () => ({ default: () => null }));
vi.mock('../../components/HostFindingsCard', () => ({ default: () => null }));
vi.mock('../../components/HostNamesCard', () => ({ default: () => null }));
vi.mock('../../components/host-inspector/PortDetailsCard', () => ({ default: () => null }));

import HostInspector from '../../components/HostInspector';
import * as api from '../../services/api';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;
const NOTE_PLACEHOLDER = /Add a note — a question, context for the team/;

beforeEach(() => {
  vi.clearAllMocks();
  // jsdom has neither; the note thread releases its image URLs on unmount.
  URL.createObjectURL = vi.fn(() => 'blob:x');
  URL.revokeObjectURL = vi.fn();
  role.value = 'viewer';
  mocked.getHost.mockResolvedValue(host);
  mocked.listHostTests.mockResolvedValue({ total: 1, has_more: false, items: [test] });
});

describe('HostInspector — a project viewer reads the host', () => {
  it('shows the notes, the tags and the tests without the controls that change them', async () => {
    render(<MemoryRouter><HostInspector hostId={1} /></MemoryRouter>);
    expect((await screen.findAllByText('todo-test')).length).toBeGreaterThan(0);
    expect(screen.getByText('seen on the DMZ sweep')).toBeInTheDocument();
    expect(screen.getByText('owned')).toBeInTheDocument();

    expect(screen.queryByPlaceholderText(NOTE_PLACEHOLDER)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Reply to note' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Add a tag' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Remove tag owned' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Assign this host' })).toBeNull();
    expect(screen.queryByRole('button', { name: /Add test/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Record result/ })).toBeNull();
  });

  it('an auditor is a reader too; an analyst gets the composer and the test actions', async () => {
    role.value = 'auditor';
    const { unmount } = render(<MemoryRouter><HostInspector hostId={1} /></MemoryRouter>);
    expect((await screen.findAllByText('todo-test')).length).toBeGreaterThan(0);
    expect(screen.queryByPlaceholderText(NOTE_PLACEHOLDER)).toBeNull();
    expect(screen.queryByRole('button', { name: /Add test/ })).toBeNull();
    unmount();

    role.value = 'analyst';
    render(<MemoryRouter><HostInspector hostId={1} /></MemoryRouter>);
    expect((await screen.findAllByText('todo-test')).length).toBeGreaterThan(0);
    expect(screen.getByPlaceholderText(NOTE_PLACEHOLDER)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reply to note' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Add a tag' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Add test/ })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /Record result/ }).length).toBeGreaterThan(0);
  });
});

describe('HostInspector — "show informational" failed (R34)', () => {
  it('says so instead of stopping silently', async () => {
    render(<MemoryRouter><HostInspector hostId={1} /></MemoryRouter>);
    const show = await screen.findByRole('button', { name: 'Show 4 informational findings' });
    mocked.getHost.mockRejectedValueOnce({ response: { status: 503, data: { detail: 'The database is busy.' } } });
    fireEvent.click(show);
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('The database is busy.'));
  });
});
