/**
 * The remediation timeline's notes (5.365.0, owner decision 2026-10-10): a
 * note can be EDITED from the page by whoever may remove it — the route
 * (`PATCH /remediation/events/{id}`) was there; the page only removed.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  listRemediationEvents: vi.fn(),
  addRemediationNote: vi.fn(),
  deleteRemediationNote: vi.fn(),
  updateRemediationNote: vi.fn(),
}));
vi.mock('../../services/api', () => api);
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toast }));

import RemediationTimeline from '../../components/remediation/RemediationTimeline';

const note = (over: Record<string, unknown> = {}) => ({
  id: 5, kind: 'note', host_id: 9, finding_host_id: null, finding_title: null, field: null, from: null, to: null,
  body: 'Called the owner', author: 'Ann Admin', agent_session_id: null, can_modify: true,
  occurred_at: '2026-10-01T10:00:00Z', recorded_at: '2026-10-01T10:00:00Z', edited_at: null, ...over,
});
const page = (...items: object[]) => ({ items, total: items.length, has_more: false, limit: 50, offset: 0 });
const host = { host_id: 9, ip_address: '10.0.0.9', hostname: null };
const open = (canWrite = true) => render(<RemediationTimeline host={host} canWrite={canWrite} onClose={vi.fn()} />);

beforeEach(() => {
  vi.clearAllMocks();
  api.listRemediationEvents.mockResolvedValue(page(note()));
});

describe('RemediationTimeline — editing a note', () => {
  it('the author edits a note in place: the text is sent, and the list is read again', async () => {
    open();
    await screen.findByText('Called the owner');
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const box = screen.getByLabelText('Edit note');
    expect(box).toHaveValue('Called the owner');

    api.updateRemediationNote.mockResolvedValue(note({ body: 'Called the owner twice', edited_at: '2026-10-02T09:00:00Z' }));
    api.listRemediationEvents.mockResolvedValue(page(note({ body: 'Called the owner twice', edited_at: '2026-10-02T09:00:00Z' })));
    fireEvent.change(box, { target: { value: '  Called the owner twice ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    // The project on screen first, then the note, the trimmed text, and no mount.
    await waitFor(() => expect(api.updateRemediationNote).toHaveBeenCalledWith(
      1, 5, { body: 'Called the owner twice' }, undefined,
    ));
    expect(await screen.findByText('Called the owner twice')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByLabelText('Edit note')).toBeNull());
    expect(api.listRemediationEvents).toHaveBeenCalledTimes(2);
  });

  it('a refused save is said beside the editor, and what was typed stays', async () => {
    open();
    await screen.findByText('Called the owner');
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    api.updateRemediationNote.mockRejectedValue({ response: { status: 403, data: { detail: 'Only its author may edit a note.' } } });
    fireEvent.change(screen.getByLabelText('Edit note'), { target: { value: 'A longer account' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    const alert = await screen.findByText('Only its author may edit a note.');
    expect(alert).toHaveAttribute('role', 'alert');
    expect(screen.getByLabelText('Edit note')).toHaveValue('A longer account');
    expect(toast.error).not.toHaveBeenCalled();
  });

  it('Cancel puts the note back as it was and sends nothing; an empty text cannot be saved', async () => {
    open();
    await screen.findByText('Called the owner');
    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    fireEvent.change(screen.getByLabelText('Edit note'), { target: { value: '   ' } });
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.getByText('Called the owner')).toBeInTheDocument();
    expect(api.updateRemediationNote).not.toHaveBeenCalled();
  });

  it('Edit is offered where Remove is: not on another person’s note, not to a reader, never on a recorded change', async () => {
    api.listRemediationEvents.mockResolvedValue(page(
      note({ id: 5, body: 'mine' }),
      note({ id: 6, body: 'someone else’s', can_modify: false }),
      note({ id: 7, kind: 'change', field: 'status', from: 'open', to: 'closed', body: null }),
    ));
    const { unmount } = open();
    const mine = (await screen.findByText('mine')).closest('li') as HTMLElement;
    expect(within(mine).getByRole('button', { name: 'Edit' })).toBeInTheDocument();
    expect(within(mine).getByRole('button', { name: 'Remove' })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: 'Edit' })).toHaveLength(1);
    unmount();

    open(false);
    await screen.findByText('mine');
    expect(screen.queryByRole('button', { name: 'Edit' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Remove' })).toBeNull();
  });
});
