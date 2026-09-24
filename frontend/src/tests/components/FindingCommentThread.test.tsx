import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/api', () => ({
  getFindingNotes: vi.fn(),
  createFindingNote: vi.fn(),
  updateFindingNote: vi.fn(),
  deleteFindingNote: vi.fn(),
  uploadFindingNoteAttachment: vi.fn(),
}));
const toastMock = { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));
vi.mock('../../contexts/AuthContext', () => ({ useAuth: () => ({ user: { id: 1, username: 'ana' } }) }));
const confirmMock = vi.fn();
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, confirmMock] }));
// Attachment thumbnails fetch blobs; not under test here.
vi.mock('../../components/host-inspector/NoteAttachments', () => ({ default: () => null }));

import * as api from '../../services/api';
import FindingCommentThread from '../../components/FindingCommentThread';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const note = (id: number, body = `note ${id}`) => ({
  id, body, status: 'open', author_id: 1, author_name: 'ana', parent_id: null, created_at: '2026-08-01T00:00:00Z',
});

const renderThread = () => render(<FindingCommentThread findingId={7} canManage />);

/** Paste one image into the composer. */
const pasteImage = (name: string) => {
  const file = new File(['png'], name, { type: 'image/png' });
  fireEvent.paste(screen.getByLabelText('New comment'), { clipboardData: { files: [file], getData: () => '' } });
  return file;
};

beforeEach(() => {
  vi.clearAllMocks();
  mocked.getFindingNotes.mockResolvedValue([]);
});

describe('FindingCommentThread — H1: unavailable is not empty', () => {
  it('initial fetch failure shows an error + Retry, never "No comments yet"', async () => {
    mocked.getFindingNotes.mockRejectedValueOnce(new Error('503'));
    renderThread();
    expect(await screen.findByText(/Comments couldn't load/)).toBeInTheDocument();
    expect(screen.queryByText(/No comments yet/)).toBeNull();

    mocked.getFindingNotes.mockResolvedValueOnce([note(1)]);
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }));
    expect(await screen.findByText('note 1')).toBeInTheDocument();
  });

  it('refresh failure keeps the loaded comments and shows a stale-data notice', async () => {
    mocked.getFindingNotes.mockResolvedValueOnce([note(1)]);
    mocked.createFindingNote.mockResolvedValue(note(2));
    mocked.getFindingNotes.mockRejectedValueOnce(new Error('503'));
    renderThread();
    await screen.findByText('note 1');

    fireEvent.change(screen.getByLabelText('New comment'), { target: { value: 'second' } });
    fireEvent.click(screen.getByRole('button', { name: /Comment/ }));

    expect(await screen.findByText(/refresh failed/)).toBeInTheDocument();
    expect(screen.getByText('note 1')).toBeInTheDocument();
  });

  it('shows the empty-state copy only after a successful fetch with zero notes', async () => {
    renderThread();
    expect(await screen.findByText(/No comments yet/)).toBeInTheDocument();
  });
});

describe('FindingCommentThread — v5.290.0: the author is told who a mention reached', () => {
  it('toasts who was notified and which @names matched nobody', async () => {
    mocked.createFindingNote.mockResolvedValue({
      ...note(3, '@eval-ben @eval-ana please retest'),
      mentions_notified: [{ username: 'eval-ben', name: 'Ben Okafor' }],
      unmatched_mentions: ['eval-ana'],
    });
    renderThread();
    await screen.findByText(/No comments yet/);
    fireEvent.change(screen.getByLabelText('New comment'), { target: { value: '@eval-ben @eval-ana please retest' } });
    fireEvent.click(screen.getByRole('button', { name: /Comment/ }));

    await waitFor(() => expect(toastMock.success).toHaveBeenCalledWith('Notified Ben Okafor'));
    expect(toastMock.warning).toHaveBeenCalledWith(
      "@eval-ana isn't a member of this project — they were not notified",
    );
  });

  it('a mention that reaches nobody says so', async () => {
    mocked.createFindingNote.mockResolvedValue({
      ...note(4, '@eval-ana please retest'), mentions_notified: [], unmatched_mentions: ['eval-ana'],
    });
    renderThread();
    await screen.findByText(/No comments yet/);
    fireEvent.change(screen.getByLabelText('New comment'), { target: { value: '@eval-ana please retest' } });
    fireEvent.click(screen.getByRole('button', { name: /Comment/ }));
    await waitFor(() =>
      expect(toastMock.warning).toHaveBeenCalledWith("@eval-ana isn't a member of this project — nobody was notified"),
    );
    expect(toastMock.success).not.toHaveBeenCalled();
  });
});

describe('FindingCommentThread — C3: failed attachments are kept and retried against the same comment', () => {
  it('keeps a failed file with Retry; retry uploads to the saved note without creating another comment', async () => {
    mocked.createFindingNote.mockResolvedValue(note(42, 'with screenshot'));
    mocked.uploadFindingNoteAttachment.mockRejectedValueOnce(new Error('413'));
    renderThread();
    await screen.findByText(/No comments yet/);

    fireEvent.change(screen.getByLabelText('New comment'), { target: { value: 'with screenshot' } });
    const file = pasteImage('shot.png');
    expect(screen.getByText('shot.png')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Comment/ }));

    // Comment posted once; the file stayed, flagged, with a Retry.
    await waitFor(() => expect(mocked.createFindingNote).toHaveBeenCalledTimes(1));
    expect(await screen.findByText(/Comment saved · 1 attachment failed/)).toBeInTheDocument();
    expect(screen.getByText('shot.png')).toBeInTheDocument();
    expect(screen.getByLabelText('New comment')).toHaveValue(''); // the comment itself did post
    const retry = screen.getByRole('button', { name: 'Retry shot.png' });

    mocked.uploadFindingNoteAttachment.mockResolvedValueOnce({ id: 1 });
    fireEvent.click(retry);

    await waitFor(() => expect(mocked.uploadFindingNoteAttachment).toHaveBeenCalledTimes(2));
    // Same note id both times, and still exactly one comment created.
    expect(mocked.uploadFindingNoteAttachment.mock.calls[1]).toEqual([7, 42, file]);
    expect(mocked.createFindingNote).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByText('shot.png')).toBeNull());
  });

  it('Remove drops a failed file without touching the saved comment', async () => {
    mocked.createFindingNote.mockResolvedValue(note(42));
    mocked.uploadFindingNoteAttachment.mockRejectedValueOnce(new Error('413'));
    renderThread();
    await screen.findByText(/No comments yet/);
    pasteImage('shot.png');
    fireEvent.click(screen.getByRole('button', { name: /Comment/ }));
    await screen.findByText(/attachment failed/);

    fireEvent.click(screen.getByRole('button', { name: 'Remove shot.png' }));
    expect(screen.queryByText('shot.png')).toBeNull();
    expect(mocked.createFindingNote).toHaveBeenCalledTimes(1);
  });

  it('regression: Remove during an in-flight retry never drops a different pending file', async () => {
    (api.getFindingNotes as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (api.createFindingNote as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 5, body: 'x', attachments: [] });
    let resolveRetry: (v: unknown) => void = () => {};
    (api.uploadFindingNoteAttachment as ReturnType<typeof vi.fn>)
      .mockRejectedValueOnce(new Error('A failed'))
      .mockRejectedValueOnce(new Error('B failed'))
      .mockImplementationOnce(() => new Promise((r) => { resolveRetry = r; }));

    render(<FindingCommentThread findingId={1} canManage />);
    await waitFor(() => expect(api.getFindingNotes).toHaveBeenCalled());
    const textarea = screen.getByLabelText('New comment');
    const a = new File(['a'], 'A.png', { type: 'image/png' });
    const b = new File(['b'], 'B.png', { type: 'image/png' });
    fireEvent.paste(textarea, { clipboardData: { files: [a, b], getData: () => '' } });
    fireEvent.change(textarea, { target: { value: 'evidence' } });
    fireEvent.click(screen.getByRole('button', { name: /Comment/ }));
    await screen.findByText(/2 attachments? failed/);

    // Retry A (deferred), then remove A while it is still uploading.
    fireEvent.click(screen.getByRole('button', { name: 'Retry A.png' }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove A.png' }));
    expect(screen.queryByText('A.png')).toBeNull();
    expect(screen.getByText('B.png')).toBeInTheDocument();

    resolveRetry({});
    // A's completion must not touch B.
    await waitFor(() => expect(api.uploadFindingNoteAttachment).toHaveBeenCalledTimes(3));
    expect(screen.getByText('B.png')).toBeInTheDocument();
  });
});

describe('FindingCommentThread — v5.256.0: a comment is its author\'s', () => {
  const others = (id: number, body: string) => ({ ...note(id, body), author_id: 2, author_name: 'bo' });

  it('offers Edit and Delete on my comments only', async () => {
    mocked.getFindingNotes.mockResolvedValue([note(1, 'mine'), others(2, 'theirs')]);
    renderThread();
    await screen.findByText('theirs');
    expect(screen.getAllByRole('button', { name: /Edit/ })).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: /Delete comment/ })).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: /Reply/ })).toHaveLength(2);
  });

  it('edits in place and marks the comment edited', async () => {
    mocked.getFindingNotes.mockResolvedValue([note(1, 'frist draft')]);
    mocked.updateFindingNote.mockResolvedValue({
      ...note(1, 'first draft'), updated_at: '2026-08-01T01:00:00Z',
    });
    renderThread();
    await screen.findByText('frist draft');
    fireEvent.click(screen.getByRole('button', { name: /Edit/ }));
    fireEvent.change(screen.getByLabelText('Edit comment'), { target: { value: ' first draft ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mocked.updateFindingNote).toHaveBeenCalledWith(7, 1, 'first draft'));
    expect(await screen.findByText('first draft')).toBeInTheDocument();
    expect(screen.getByText(/edited/)).toBeInTheDocument();
  });

  it('deletes after confirmation', async () => {
    mocked.getFindingNotes.mockResolvedValue([note(1, 'mine')]);
    mocked.deleteFindingNote.mockResolvedValue(undefined);
    confirmMock.mockResolvedValueOnce(true);
    renderThread();
    await screen.findByText('mine');
    fireEvent.click(screen.getByRole('button', { name: /Delete comment/ }));
    await waitFor(() => expect(mocked.deleteFindingNote).toHaveBeenCalledWith(7, 1));
    await waitFor(() => expect(screen.queryByText('mine')).toBeNull());
  });

  it('a comment with replies is not deleted — it says why instead of asking', async () => {
    mocked.getFindingNotes.mockResolvedValue([note(1, 'root'), { ...others(2, 'reply'), parent_id: 1 }]);
    renderThread();
    await screen.findByText('reply');
    fireEvent.click(screen.getByRole('button', { name: /Delete comment/ }));
    expect(toastMock.info).toHaveBeenCalledWith(expect.stringMatching(/has replies/));
    expect(confirmMock).not.toHaveBeenCalled();
    expect(mocked.deleteFindingNote).not.toHaveBeenCalled();
  });
});

// v5.264.0 — a text-message conversation: the viewer's comments on the right,
// others' on the left (v5.268.0), oldest first, a reply quoting what it answers.
describe('FindingCommentThread — conversation layout', () => {
  it('sides, order and reply quotes', async () => {
    mocked.getFindingNotes.mockResolvedValue([
      { ...note(1, 'Found it.'), created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-01T00:00:01Z' },
      { ...note(2, 'Confirmed on my side.'), author_id: 2, author_name: 'ben', parent_id: 1, created_at: '2026-08-01T01:00:00Z' },
      { ...note(3, 'Thanks.'), created_at: '2026-08-01T02:00:00Z' },
    ]);
    const { container } = renderThread();
    await screen.findByText('Thanks.');
    const sides = [...container.querySelectorAll('[data-side]')].map((el) => el.getAttribute('data-side'));
    expect(sides).toEqual(['mine', 'theirs', 'mine']);
    const bubbles = container.querySelectorAll('[data-side]');
    expect(bubbles[0]).toHaveClass('items-end');
    expect(bubbles[1]).toHaveClass('items-start');
    // The quoted comment is the viewer's own, so the quote says "you" (5.268.1).
    expect(screen.getByText(/Replying to/)).toHaveTextContent('Replying to you: Found it.');
    // Not "edited": the thread root is stamped in a second write at creation.
    expect(screen.queryByText(/edited/)).not.toBeInTheDocument();
    // The viewer's own messages read "You"; no Card wraps the thread.
    expect(screen.getAllByText('You')).toHaveLength(2);
    expect(container.querySelector('.bg-card.shadow-raised')).toBeNull();
  });
});
