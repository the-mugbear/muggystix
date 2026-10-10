/**
 * Finishing a host's review is ONE click — there is no "Complete review"
 * dialog and no conclusion (owner decision, 2026-10-10: "Once a host review
 * is complete they should be able to set review complete").
 *
 * This file pinned that dialog (it opened with no conclusion preselected and
 * could not be saved without one).  The dialog is gone, so what it guarded —
 * that a conclusion nobody chose is never recorded — holds by construction:
 * nothing is sent but the status.  It now pins what replaced it:
 *
 *  - "Mark reviewed" sends the status alone and opens no dialog;
 *  - "Reviewed, next unreviewed" (inside a queue only) moves on after the
 *    save succeeded, and stays on a failed save;
 *  - a conclusion an OLDER review recorded is still shown;
 *  - the optional note on a finished review: shown to everyone, written by
 *    writers only, never asked for.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});

const role = vi.hoisted(() => ({ value: 'analyst' as string }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 9, username: 'ana', role: 'member' }, hasPermission: (r: string) => r !== 'admin' }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Demo', my_role: role.value } }),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));

const host = {
  id: 1, ip_address: '10.0.0.1', hostname: 'h1', state: 'up',
  ports: [], assignees: [], tags: [], vulnerabilities: [], notes: [],
  discoveries: [], follow: null as Record<string, unknown> | null,
  os_name: null, os_family: null, os_type: null, os_generation: null,
  os_vendor: null, os_accuracy: null, smb_signing: null,
  web_interface_count: 0, netexec_result_count: 0, dns_record_count: 0,
  first_seen: '2026-06-14T00:00:00Z', last_seen: '2026-06-14T00:00:00Z',
  informational_count: 0, informational_included: false,
};
const reviewed = (over: Record<string, unknown> = {}) => ({
  status: 'reviewed', created_at: '2026-06-14T00:00:00Z', updated_at: '2026-06-15T00:00:00Z',
  last_viewed_at: null, review_conclusion: null, review_summary: null, ...over,
});

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
  followHost: vi.fn(), unfollowHost: vi.fn(), setReviewNote: vi.fn(),
  assignHost: vi.fn(), unassignHost: vi.fn(),
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

const renderIt = (props: { onNextUnreviewed?: () => void } = {}) =>
  render(<MemoryRouter><HostInspector hostId={1} {...props} /></MemoryRouter>);
const withFollow = (follow: Record<string, unknown> | null) => mocked.getHost.mockResolvedValue({ ...host, follow });

beforeEach(() => {
  vi.clearAllMocks();
  URL.createObjectURL = vi.fn(() => 'blob:x');
  URL.revokeObjectURL = vi.fn();
  role.value = 'analyst';
  withFollow(null);
  mocked.followHost.mockResolvedValue(reviewed());
});

describe('HostInspector — "Reviewed" is one click', () => {
  it('"Mark reviewed" sends the status alone, opens no dialog, and the host reads Reviewed', async () => {
    renderIt();
    fireEvent.click(await screen.findByRole('button', { name: 'Mark reviewed' }));

    await waitFor(() => expect(mocked.followHost).toHaveBeenCalledTimes(1));
    // The status and nothing else: no conclusion, no summary, no fourth argument.
    expect(mocked.followHost.mock.calls[0]).toEqual([1, 1, 'reviewed']);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.queryByText('Complete review')).toBeNull();
    expect(screen.queryByRole('combobox', { name: 'Conclusion' })).toBeNull();
    // The server's answer is on the host: the control is now the reviewed one.
    expect(await screen.findByRole('button', { name: /Re-open review/ })).toBeInTheDocument();
    expect(toast.success).toHaveBeenCalledWith('Marked as Reviewed', expect.anything());
    // A note is never asked for, and none is written by marking.
    expect(mocked.setReviewNote).not.toHaveBeenCalled();
  });

  it('a host In review is finished the same way — one click on its primary action', async () => {
    withFollow({ ...reviewed(), status: 'in_review' });
    renderIt();
    fireEvent.click(await screen.findByRole('button', { name: 'Mark reviewed' }));
    await waitFor(() => expect(mocked.followHost.mock.calls[0]).toEqual([1, 1, 'reviewed']));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('the old "Mark reviewed…" (which opened the dialog) is gone', async () => {
    renderIt();
    await screen.findByRole('button', { name: 'Mark reviewed' });
    expect(screen.queryByRole('button', { name: 'Mark reviewed…' })).toBeNull();
  });
});

describe('HostInspector — "Reviewed, next unreviewed"', () => {
  it('is offered only inside a queue', async () => {
    const { unmount } = renderIt();
    await screen.findByRole('button', { name: 'Mark reviewed' });
    expect(screen.queryByRole('button', { name: 'Reviewed, next unreviewed' })).toBeNull();
    unmount();

    renderIt({ onNextUnreviewed: vi.fn() });
    expect(await screen.findByRole('button', { name: 'Reviewed, next unreviewed' })).toBeInTheDocument();
    // "Mark reviewed" stays beside it: marking without moving on.
    expect(screen.getByRole('button', { name: 'Mark reviewed' })).toBeInTheDocument();
  });

  it('moves on only AFTER the save succeeded', async () => {
    let answer: (follow: unknown) => void = () => {};
    mocked.followHost.mockReturnValue(new Promise((resolve) => { answer = resolve; }));
    const next = vi.fn();
    withFollow({ ...reviewed(), status: 'in_review' });
    renderIt({ onNextUnreviewed: next });

    fireEvent.click(await screen.findByRole('button', { name: 'Reviewed, next unreviewed' }));
    await waitFor(() => expect(mocked.followHost.mock.calls[0]).toEqual([1, 1, 'reviewed']));
    // Sent, not yet answered: the reader is still on this host.
    expect(next).not.toHaveBeenCalled();

    answer(reviewed());
    await waitFor(() => expect(next).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('stays on the host when the save fails, and says so', async () => {
    mocked.followHost.mockRejectedValue({ response: { status: 503, data: { detail: 'The database is busy.' } } });
    const next = vi.fn();
    renderIt({ onNextUnreviewed: next });

    fireEvent.click(await screen.findByRole('button', { name: 'Reviewed, next unreviewed' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(next).not.toHaveBeenCalled();
    // Still unreviewed, and both actions are still there to try again.
    expect(screen.getByRole('button', { name: 'Mark reviewed' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reviewed, next unreviewed' })).toBeEnabled();
  });

  it('is not offered on a host already Reviewed', async () => {
    withFollow(reviewed());
    renderIt({ onNextUnreviewed: vi.fn() });
    await screen.findByRole('button', { name: /Re-open review/ });
    expect(screen.queryByRole('button', { name: 'Reviewed, next unreviewed' })).toBeNull();
  });
});

describe('HostInspector — a conclusion an older review recorded', () => {
  it.each([
    ['needs_evidence', 'Needs more evidence'],
    ['no_issue', 'No actionable issue'],
    ['finding_created', 'Finding created'],
    ['out_of_scope', 'Out of scope'],
    ['duplicate', 'Duplicate asset'],
    // Older still, and present in stored data.
    ['no_action', 'No action needed'],
    // A value nobody listed is shown as stored, never dropped.
    ['something_else', 'something_else'],
  ])('%s is still shown, as "%s"', async (stored, label) => {
    withFollow(reviewed({ review_conclusion: stored }));
    renderIt();
    expect(await screen.findByText(label)).toBeInTheDocument();
    // Displayed, not offered: nothing lets the reader choose one.
    expect(screen.queryByRole('combobox', { name: 'Conclusion' })).toBeNull();
  });

  it('is not shown once the review is open again', async () => {
    withFollow({ ...reviewed({ review_conclusion: 'needs_evidence' }), status: 'in_review' });
    renderIt();
    await screen.findByRole('button', { name: 'Mark reviewed' });
    expect(screen.queryByText('Needs more evidence')).toBeNull();
  });
});

describe('HostInspector — the optional note on a finished review', () => {
  it('a writer adds one with one input; the server’s answer is what shows', async () => {
    withFollow(reviewed());
    mocked.setReviewNote.mockResolvedValue(reviewed({ review_summary: 'RDP only, patched in June' }));
    renderIt();

    fireEvent.click(await screen.findByRole('button', { name: 'Add a note' }));
    const input = screen.getByRole('textbox', { name: 'Note on your review' });
    fireEvent.change(input, { target: { value: '  RDP only, patched in June  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(mocked.setReviewNote).toHaveBeenCalledWith(1, 1, 'RDP only, patched in June'));
    expect(await screen.findByTestId('review-note')).toHaveTextContent('RDP only, patched in June');
    expect(screen.queryByRole('textbox', { name: 'Note on your review' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Edit note' })).toBeInTheDocument();
    // Writing a note is not a review-status change.
    expect(mocked.followHost).not.toHaveBeenCalled();
  });

  it('Cancel writes nothing; saving it empty removes the note', async () => {
    withFollow(reviewed({ review_summary: 'looked at ssh' }));
    mocked.setReviewNote.mockResolvedValue(reviewed());
    renderIt();

    fireEvent.click(await screen.findByRole('button', { name: 'Edit note' }));
    const input = screen.getByRole('textbox', { name: 'Note on your review' });
    expect(input).toHaveValue('looked at ssh');
    fireEvent.change(input, { target: { value: 'something else' } });
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(mocked.setReviewNote).not.toHaveBeenCalled();
    expect(screen.getByTestId('review-note')).toHaveTextContent('looked at ssh');

    fireEvent.click(screen.getByRole('button', { name: 'Edit note' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Note on your review' }), { target: { value: '   ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mocked.setReviewNote).toHaveBeenCalledWith(1, 1, null));
    await waitFor(() => expect(screen.queryByTestId('review-note')).toBeNull());
    expect(screen.getByRole('button', { name: 'Add a note' })).toBeInTheDocument();
  });

  it('a failed save keeps what was typed, and says so', async () => {
    withFollow(reviewed());
    mocked.setReviewNote.mockRejectedValue({ response: { status: 503, data: { detail: 'The database is busy.' } } });
    renderIt();

    fireEvent.click(await screen.findByRole('button', { name: 'Add a note' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Note on your review' }), { target: { value: 'half a thought' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(screen.getByRole('textbox', { name: 'Note on your review' })).toHaveValue('half a thought');
  });

  it('a reader who cannot write sees the note and no control to change it', async () => {
    role.value = 'viewer';
    withFollow(reviewed({ review_summary: 'looked at ssh', review_conclusion: 'no_issue' }));
    renderIt();
    expect(await screen.findByTestId('review-note')).toHaveTextContent('looked at ssh');
    expect(screen.getByText('No actionable issue')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Edit note' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Add a note' })).toBeNull();
  });

  it('is not offered before the review is finished', async () => {
    withFollow({ ...reviewed(), status: 'in_review' });
    renderIt();
    await screen.findByRole('button', { name: 'Mark reviewed' });
    expect(screen.queryByRole('button', { name: 'Add a note' })).toBeNull();
  });

  it('a long note is clamped, and the whole of it is one click away', async () => {
    const long = `Reviewed in three sittings. ${'x'.repeat(900)} END-OF-NOTE`;
    withFollow(reviewed({ review_summary: long }));
    renderIt();
    const note = await screen.findByTestId('review-note');
    // The text is all in the document (clamped by CSS, never cut in data)…
    expect(note).toHaveTextContent('END-OF-NOTE');
    expect(note).toHaveClass('line-clamp-2', 'break-words');
    // …and "Show all" lifts the clamp.
    fireEvent.click(screen.getByRole('button', { name: 'Show all' }));
    expect(note).not.toHaveClass('line-clamp-2');
    expect(note).toHaveClass('break-words');
    expect(screen.getByRole('button', { name: 'Show less' })).toBeInTheDocument();
  });
});
