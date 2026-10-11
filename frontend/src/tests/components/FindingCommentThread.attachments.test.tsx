/**
 * A change to an image of a finding's comment shows in the thread — with the
 * REAL `NoteAttachments` inside the real thread (the other thread test
 * replaces it with nothing).
 *
 * The thread's images come with its read (`getFindingNotes`), so a mark, a
 * caption, a delete or an upload made on one of them is seen only once that
 * read has been made again.  The writer (`NoteAttachments`) says the read is
 * out of date itself; the thread hands it no "reload".  A refused change
 * reads nothing again.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../services/api', () => ({
  getFindingNotes: vi.fn(),
  createFindingNote: vi.fn(),
  updateFindingNote: vi.fn(),
  deleteFindingNote: vi.fn(),
  uploadFindingNoteAttachment: vi.fn(),
  uploadNoteAttachment: vi.fn(),
  deleteNoteAttachment: vi.fn(),
  getNoteAttachmentBlob: vi.fn(),
  setNoteAttachmentInReport: vi.fn(),
  setNoteAttachmentCaption: vi.fn(),
}));
const toastMock = { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: { id: 1, username: 'ana' } }) }));
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, vi.fn()] }));

import * as api from '../../services/api';
import { blobFor, installBlobUrls } from '../helpers/blobUrls';
import { readsOnScreen } from '../helpers/readsOnScreen';
import FindingCommentThread from '../../components/FindingCommentThread';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const att = (over: Record<string, unknown> = {}) => ({
  id: 9, filename: 'shot.png', content_type: 'image/png', size_bytes: 10,
  created_at: '2026-09-01T00:00:00Z', include_in_report: false, uploaded_by_id: 1, caption: null, ...over,
});
const thread = (attachment: Record<string, unknown> | null) => ({
  items: [{
    id: 2, body: 'with a shot', status: 'open', author_id: 1, author_name: 'ana', parent_id: null,
    created_at: '2026-08-01T00:00:00Z', attachments: attachment ? [attachment] : [],
  }],
  total: 1,
});

const reportMarking = { canMark: () => true, captionMax: 2000 };
/** The thread, with the page's read of the finding's images beside it. */
const renderThread = () => {
  const { reread, ReadsOnScreen } = readsOnScreen({ getFindingImages: 'the finding’s images' });
  render(<><ReadsOnScreen /><FindingCommentThread findingId={7} canManage reportMarking={reportMarking} /></>);
  return reread;
};

beforeEach(() => {
  vi.clearAllMocks();
  URL.revokeObjectURL = vi.fn();
  installBlobUrls();
  mocked.getNoteAttachmentBlob.mockResolvedValue(blobFor('blob:x'));
  mocked.getFindingNotes.mockResolvedValue(thread(att()));
});

describe('FindingCommentThread — an image of a comment is changed', () => {
  it('a mark reads the thread again, and what the thread then says is shown', async () => {
    mocked.setNoteAttachmentInReport.mockResolvedValue(att({ include_in_report: true }));
    const reread = renderThread();
    const box = await screen.findByRole('checkbox', { name: 'Include shot.png in the report' });
    expect(mocked.getFindingNotes).toHaveBeenCalledTimes(1);

    // The server stored the mark and a caption someone else wrote meanwhile.
    mocked.getFindingNotes.mockResolvedValue(thread(att({ include_in_report: true, caption: 'Login page' })));
    fireEvent.click(box);
    await waitFor(() => expect(mocked.setNoteAttachmentInReport).toHaveBeenCalledWith(1, 9, true));
    await waitFor(() => expect(mocked.getFindingNotes).toHaveBeenCalledTimes(2));
    expect(mocked.getFindingNotes.mock.calls[1].slice(0, 2)).toEqual([1, 7]);
    expect(await screen.findByTestId('caption-9')).toHaveTextContent('Login page');
    expect(box).toHaveAttribute('data-state', 'checked');
    await waitFor(() => expect(reread).toHaveBeenCalledTimes(1));
  });

  it('a saved caption reads the thread again', async () => {
    mocked.setNoteAttachmentCaption.mockResolvedValue(att({ caption: 'New words' }));
    renderThread();
    fireEvent.click(await screen.findByRole('button', { name: 'Add caption for shot.png' }));
    mocked.getFindingNotes.mockResolvedValue(thread(att({ caption: 'New words' })));
    fireEvent.change(screen.getByRole('textbox', { name: 'Caption for shot.png' }), { target: { value: 'New words' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save caption' }));
    await waitFor(() => expect(mocked.getFindingNotes).toHaveBeenCalledTimes(2));
    expect(await screen.findByTestId('caption-9')).toHaveTextContent('New words');
  });

  it('a deleted image reads the thread again, and is gone', async () => {
    mocked.deleteNoteAttachment.mockResolvedValue(undefined);
    renderThread();
    const remove = await screen.findByRole('button', { name: 'Delete shot.png' });
    mocked.getFindingNotes.mockResolvedValue(thread(null));
    fireEvent.click(remove);
    await waitFor(() => expect(mocked.deleteNoteAttachment).toHaveBeenCalledWith(1, 9));
    await waitFor(() => expect(mocked.getFindingNotes).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'View shot.png' })).toBeNull());
  });

  it('a refused change reads nothing again', async () => {
    mocked.deleteNoteAttachment.mockRejectedValue({
      response: { status: 409, data: { detail: 'Cannot delete this image: it is placed in the Description.' } },
    });
    const reread = renderThread();
    fireEvent.click(await screen.findByRole('button', { name: 'Delete shot.png' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Cannot delete this image');
    expect(mocked.getFindingNotes).toHaveBeenCalledTimes(1);
    expect(reread).not.toHaveBeenCalled();
  });
});
