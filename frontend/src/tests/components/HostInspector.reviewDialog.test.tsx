/**
 * "Complete review" preselects nothing (owner decision 27, 2026-10-10).
 *
 * The dialog opened on "No actionable issue", so one click on "Mark reviewed"
 * recorded a conclusion nobody had chosen.  It opens with no conclusion, the
 * save is unavailable until one is chosen, and what is sent is the choice.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});

vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 9, username: 'ana', role: 'member' }, hasPermission: (r: string) => r !== 'admin' }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Demo', my_role: 'analyst' } }),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));

const host = {
  id: 1, ip_address: '10.0.0.1', hostname: 'h1', state: 'up',
  ports: [], assignees: [], tags: [], vulnerabilities: [], notes: [],
  discoveries: [], follow: null,
  os_name: null, os_family: null, os_type: null, os_generation: null,
  os_vendor: null, os_accuracy: null, smb_signing: null,
  web_interface_count: 0, netexec_result_count: 0, dns_record_count: 0,
  first_seen: '2026-06-14T00:00:00Z', last_seen: '2026-06-14T00:00:00Z',
  informational_count: 0, informational_included: false,
};

vi.mock('../../services/api', () => ({
  getHost: vi.fn(),
  getHostConflicts: vi.fn().mockResolvedValue([]),
  listHostTests: vi.fn().mockResolvedValue({ total: 0, has_more: false, items: [] }),
  listProposals: vi.fn().mockResolvedValue({ items: [], total: 0, has_more: false }),
  listAgentSessions: vi.fn().mockResolvedValue({ project_id: 1, sessions: [], total: 0 }),
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

const openDialog = async () => {
  fireEvent.click(await screen.findByRole('button', { name: 'Mark reviewed…' }));
  return screen.findByRole('dialog', { name: 'Complete review' });
};
const conclusion = () => screen.getByRole('combobox', { name: 'Conclusion' });
const choose = async (name: string) => {
  fireEvent.keyDown(conclusion(), { key: 'Enter' });
  fireEvent.click(await screen.findByRole('option', { name }));
};

beforeEach(() => {
  vi.clearAllMocks();
  URL.createObjectURL = vi.fn(() => 'blob:x');
  URL.revokeObjectURL = vi.fn();
  mocked.getHost.mockResolvedValue(host);
  mocked.followHost.mockResolvedValue({ status: 'reviewed', host_id: 1, review_conclusion: 'needs_evidence' });
});

describe('HostInspector — Complete review', () => {
  it('opens with no conclusion chosen, and cannot be saved until one is', async () => {
    render(<MemoryRouter><HostInspector hostId={1} /></MemoryRouter>);
    const dialog = await openDialog();

    expect(conclusion()).toHaveTextContent('Choose a conclusion');
    expect(conclusion()).not.toHaveTextContent('No actionable issue');
    const saveButton = within(dialog).getByRole('button', { name: 'Mark reviewed' });
    expect(saveButton).toBeDisabled();
    fireEvent.click(saveButton);
    expect(mocked.followHost).not.toHaveBeenCalled();

    await choose('Needs more evidence');
    expect(conclusion()).toHaveTextContent('Needs more evidence');
    expect(saveButton).toBeEnabled();
    fireEvent.click(saveButton);
    await waitFor(() => expect(mocked.followHost).toHaveBeenCalledWith(1, 1, 'reviewed', {
      review_conclusion: 'needs_evidence', review_summary: undefined,
    }));
  });

  it('"No actionable issue" is recorded only when it is chosen', async () => {
    render(<MemoryRouter><HostInspector hostId={1} /></MemoryRouter>);
    const dialog = await openDialog();
    await choose('No actionable issue');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Mark reviewed' }));
    await waitFor(() => expect(mocked.followHost).toHaveBeenCalledWith(1, 1, 'reviewed', {
      review_conclusion: 'no_issue', review_summary: undefined,
    }));
  });

  it('a later opening starts with no conclusion again', async () => {
    render(<MemoryRouter><HostInspector hostId={1} /></MemoryRouter>);
    const dialog = await openDialog();
    await choose('Out of scope');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());

    const again = await openDialog();
    expect(conclusion()).toHaveTextContent('Choose a conclusion');
    expect(within(again).getByRole('button', { name: 'Mark reviewed' })).toBeDisabled();
    expect(mocked.followHost).not.toHaveBeenCalled();
  });
});
