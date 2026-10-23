import React from 'react';
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
import { readsOnScreen } from '../helpers/readsOnScreen';
import { installBlobUrls } from '../helpers/blobUrls';
import FindingCommentThread from '../../components/FindingCommentThread';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const note = (id: number, body = `note ${id}`) => ({
  id, body, status: 'open', author_id: 1, author_name: 'ana', parent_id: null, created_at: '2026-08-01T00:00:00Z',
});

/** A read of the thread: its comments (the server sends the newest first; the
 *  thread orders what it shows itself) and how many the thread holds. */
const page = (items: unknown[], total = items.length) => ({ items, total });

const renderThread = () => render(<FindingCommentThread findingId={7} canManage />);

/** Paste one image into the composer. */
const pasteImage = (name: string) => {
  const file = new File(['png'], name, { type: 'image/png' });
  fireEvent.paste(screen.getByLabelText('New comment'), { clipboardData: { files: [file], getData: () => '' } });
  return file;
};

beforeEach(() => {
  vi.clearAllMocks();
  mocked.getFindingNotes.mockResolvedValue(page([]));
  // A pasted screenshot shows its preview (the one composer of both threads,
  // review 2026-10-23 R-FC1); jsdom makes no object URLs.
  installBlobUrls();
  URL.revokeObjectURL = vi.fn();
});

describe('FindingCommentThread — H1: unavailable is not empty', () => {
  it('initial fetch failure shows an error + Retry, never "No comments yet"', async () => {
    mocked.getFindingNotes.mockRejectedValueOnce(new Error('503'));
    renderThread();
    expect(await screen.findByText(/Comments couldn't load/)).toBeInTheDocument();
    expect(screen.queryByText(/No comments yet/)).toBeNull();

    mocked.getFindingNotes.mockResolvedValueOnce(page([note(1)]));
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }));
    expect(await screen.findByText('note 1')).toBeInTheDocument();
  });

  it('refresh failure keeps the loaded comments and shows a stale-data notice', async () => {
    mocked.getFindingNotes.mockResolvedValueOnce(page([note(1)]));
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
    const retry = screen.getByRole('button', { name: 'Retry uploading pasted image 1' });

    mocked.uploadFindingNoteAttachment.mockResolvedValueOnce({ id: 1 });
    fireEvent.click(retry);

    await waitFor(() => expect(mocked.uploadFindingNoteAttachment).toHaveBeenCalledTimes(2));
    // Same note id both times, and still exactly one comment created.
    expect(mocked.uploadFindingNoteAttachment.mock.calls[1]).toEqual([1, 7, 42, file]);
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

    fireEvent.click(screen.getByRole('button', { name: 'Remove pasted image 1' }));
    expect(screen.queryByText('shot.png')).toBeNull();
    expect(mocked.createFindingNote).toHaveBeenCalledTimes(1);
  });

  it('regression: Remove during an in-flight retry never drops a different pending file', async () => {
    (api.getFindingNotes as ReturnType<typeof vi.fn>).mockResolvedValue(page([]));
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
    fireEvent.click(screen.getByRole('button', { name: 'Retry uploading pasted image 1' }));
    fireEvent.click(screen.getByRole('button', { name: 'Remove pasted image 1' }));
    expect(screen.queryByText('A.png')).toBeNull();
    expect(screen.getByText('B.png')).toBeInTheDocument();

    resolveRetry({});
    // A's completion must not touch B.
    await waitFor(() => expect(api.uploadFindingNoteAttachment).toHaveBeenCalledTimes(3));
    expect(screen.getByText('B.png')).toBeInTheDocument();
  });
});

// Review 2026-10-23, H-FC2 — a post's answer set the queue from what it held
// when the post was SENT: a screenshot pasted while the post ran was wiped,
// and a failed one retried meanwhile came back.  The composer is now disabled
// while a post runs, and the answer changes only the images that post sent.
describe('FindingCommentThread — a post changes only what it sent', () => {
  const deferred = () => {
    let resolve!: (v: unknown) => void;
    const promise = new Promise((r) => { resolve = r; });
    return { promise, resolve };
  };

  it('a screenshot pasted while a post runs is still waiting when the post answers', async () => {
    mocked.createFindingNote.mockResolvedValue(note(42, 'with a shot'));
    const upload = deferred();
    mocked.uploadFindingNoteAttachment.mockImplementationOnce(() => upload.promise);
    renderThread();
    await screen.findByText(/No comments yet/);
    fireEvent.change(screen.getByLabelText('New comment'), { target: { value: 'with a shot' } });
    pasteImage('first.png');
    fireEvent.click(screen.getByRole('button', { name: /Comment/ }));
    await waitFor(() => expect(mocked.uploadFindingNoteAttachment).toHaveBeenCalledTimes(1));

    // While it runs, the composer and the image's Remove are disabled …
    expect(screen.getByLabelText('New comment')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Remove pasted image 1' })).toBeDisabled();
    // … and an image that reaches the queue anyway is not the post's.
    pasteImage('second.png');
    expect(screen.getByText('second.png')).toBeInTheDocument();

    upload.resolve({ id: 1 });
    await waitFor(() => expect(screen.queryByText('first.png')).toBeNull());
    expect(screen.getByText('second.png')).toBeInTheDocument();
    expect(screen.getByLabelText('New comment')).toBeEnabled();
    expect(mocked.uploadFindingNoteAttachment).toHaveBeenCalledTimes(1);
  });

  it('an earlier failure retried while a post runs is not brought back by the post', async () => {
    mocked.createFindingNote
      .mockResolvedValueOnce(note(41, 'first'))
      .mockResolvedValueOnce(note(42, 'second'));
    const retried = deferred();
    const second = deferred();
    mocked.uploadFindingNoteAttachment
      .mockRejectedValueOnce(new Error('413'))
      .mockImplementationOnce(() => retried.promise)
      .mockImplementationOnce(() => second.promise);
    renderThread();
    await screen.findByText(/No comments yet/);

    fireEvent.change(screen.getByLabelText('New comment'), { target: { value: 'first' } });
    pasteImage('a.png');
    fireEvent.click(screen.getByRole('button', { name: /Comment/ }));
    await screen.findByText(/Comment saved · 1 attachment failed/);

    // A's retry is on its way when the next comment is posted with B.
    fireEvent.click(screen.getByRole('button', { name: 'Retry uploading pasted image 1' }));
    fireEvent.change(screen.getByLabelText('New comment'), { target: { value: 'second' } });
    pasteImage('b.png');
    fireEvent.click(screen.getByRole('button', { name: /Comment/ }));
    await waitFor(() => expect(mocked.uploadFindingNoteAttachment).toHaveBeenCalledTimes(3));

    // A lands on its comment and leaves the queue …
    retried.resolve({ id: 7 });
    await waitFor(() => expect(screen.queryByText('a.png')).toBeNull());
    // … and the post's answer does not put it back.
    second.resolve({ id: 8 });
    await waitFor(() => expect(screen.queryByText('b.png')).toBeNull());
    expect(screen.queryByText('a.png')).toBeNull();
    expect(mocked.uploadFindingNoteAttachment.mock.calls[1].slice(0, 3)).toEqual([1, 7, 41]);
    expect(mocked.uploadFindingNoteAttachment.mock.calls[2].slice(0, 3)).toEqual([1, 7, 42]);
  });
});

describe('FindingCommentThread — v5.256.0: a comment is its author\'s', () => {
  const others = (id: number, body: string) => ({ ...note(id, body), author_id: 2, author_name: 'bo' });

  it('offers Edit and Delete on my comments only', async () => {
    mocked.getFindingNotes.mockResolvedValue(page([note(1, 'mine'), others(2, 'theirs')]));
    renderThread();
    await screen.findByText('theirs');
    expect(screen.getAllByRole('button', { name: /Edit/ })).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: /Delete comment/ })).toHaveLength(1);
    expect(screen.getAllByRole('button', { name: /Reply/ })).toHaveLength(2);
  });

  it('edits in place and marks the comment edited', async () => {
    mocked.getFindingNotes.mockResolvedValue(page([note(1, 'frist draft')]));
    mocked.updateFindingNote.mockResolvedValue({
      ...note(1, 'first draft'), updated_at: '2026-08-01T01:00:00Z',
    });
    renderThread();
    await screen.findByText('frist draft');
    fireEvent.click(screen.getByRole('button', { name: /Edit/ }));
    fireEvent.change(screen.getByLabelText('Edit comment'), { target: { value: ' first draft ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(mocked.updateFindingNote).toHaveBeenCalledWith(1, 7, 1, 'first draft'));
    expect(await screen.findByText('first draft')).toBeInTheDocument();
    expect(screen.getByText(/edited/)).toBeInTheDocument();
  });

  it('deletes after confirmation', async () => {
    // The thread is read again after the delete (its count is the server's).
    mocked.getFindingNotes.mockResolvedValueOnce(page([note(1, 'mine')]));
    mocked.deleteFindingNote.mockResolvedValue(undefined);
    confirmMock.mockResolvedValueOnce(true);
    renderThread();
    await screen.findByText('mine');
    expect(screen.getByRole('heading')).toHaveTextContent('(1)');
    fireEvent.click(screen.getByRole('button', { name: /Delete comment/ }));
    await waitFor(() => expect(mocked.deleteFindingNote).toHaveBeenCalledWith(1, 7, 1));
    await waitFor(() => expect(screen.queryByText('mine')).toBeNull());
    expect(mocked.getFindingNotes).toHaveBeenCalledTimes(2);
    expect(await screen.findByText(/No comments yet/)).toBeInTheDocument();
  });

  it('a comment with replies is not deleted — it says why instead of asking', async () => {
    mocked.getFindingNotes.mockResolvedValue(page([note(1, 'root'), { ...others(2, 'reply'), parent_id: 1 }]));
    renderThread();
    await screen.findByText('reply');
    fireEvent.click(screen.getByRole('button', { name: /Delete comment/ }));
    expect(toastMock.info).toHaveBeenCalledWith(expect.stringMatching(/has replies/));
    expect(confirmMock).not.toHaveBeenCalled();
    expect(mocked.deleteFindingNote).not.toHaveBeenCalled();
  });
});

// 5.351.0 — a screenshot on a comment is one of the FINDING's images (the
// report text's "Insert image", each image's caption and placement).  The
// thread re-read itself, but the finding's images stayed as first read until
// the reader pressed Refresh.  This stands in for the page's read of them.
describe('FindingCommentThread — a screenshot is one of the finding’s images', () => {
  const images = () => readsOnScreen({ getFindingImages: 'the finding’s images' });
  const renderWith = (ReadsOnScreen: React.FC) =>
    render(<><ReadsOnScreen /><FindingCommentThread findingId={7} canManage /></>);

  it('a comment posted with a screenshot reads them again; one without does not', async () => {
    const { reread, ReadsOnScreen } = images();
    mocked.createFindingNote.mockResolvedValue(note(42, 'plain'));
    mocked.uploadFindingNoteAttachment.mockResolvedValue({ id: 1 });
    renderWith(ReadsOnScreen);
    await screen.findByText(/No comments yet/);

    fireEvent.change(screen.getByLabelText('New comment'), { target: { value: 'plain' } });
    fireEvent.click(screen.getByRole('button', { name: /Comment/ }));
    await waitFor(() => expect(mocked.getFindingNotes).toHaveBeenCalledTimes(2));   // the thread re-read
    expect(reread).not.toHaveBeenCalled();

    pasteImage('shot.png');
    fireEvent.click(screen.getByRole('button', { name: /Comment/ }));
    await waitFor(() => expect(mocked.uploadFindingNoteAttachment).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(reread).toHaveBeenCalledTimes(1));
  });

  it('a screenshot that failed to attach reads nothing again until its retry lands', async () => {
    const { reread, ReadsOnScreen } = images();
    mocked.createFindingNote.mockResolvedValue(note(42));
    mocked.uploadFindingNoteAttachment.mockRejectedValueOnce(new Error('413'));
    renderWith(ReadsOnScreen);
    await screen.findByText(/No comments yet/);
    pasteImage('shot.png');
    fireEvent.click(screen.getByRole('button', { name: /Comment/ }));
    await screen.findByText(/attachment failed/);
    expect(reread).not.toHaveBeenCalled();

    mocked.uploadFindingNoteAttachment.mockResolvedValueOnce({ id: 1 });
    fireEvent.click(screen.getByRole('button', { name: 'Retry uploading pasted image 1' }));
    await waitFor(() => expect(reread).toHaveBeenCalledTimes(1));
  });

  it('deleting a comment that carried screenshots reads them again; a bare one does not', async () => {
    const { reread, ReadsOnScreen } = images();
    // What the server holds: each delete takes its comment out of the next read.
    let held: Array<ReturnType<typeof note> & { attachments?: unknown[] }> = [
      note(1, 'bare'), { ...note(2, 'with shots'), attachments: [{ id: 9, filename: 'shot.png' }] },
    ];
    mocked.getFindingNotes.mockImplementation(async () => page(held));
    mocked.deleteFindingNote.mockImplementation(async (_project: number, _finding: number, id: number) => {
      held = held.filter((n) => n.id !== id);
    });
    confirmMock.mockResolvedValue(true);
    renderWith(ReadsOnScreen);
    await screen.findByText('with shots');
    const [bare, withShots] = screen.getAllByRole('button', { name: /Delete comment/ });

    fireEvent.click(bare);
    await waitFor(() => expect(screen.queryByText('bare')).toBeNull());
    expect(reread).not.toHaveBeenCalled();

    fireEvent.click(withShots);
    await waitFor(() => expect(screen.queryByText('with shots')).toBeNull());
    await waitFor(() => expect(reread).toHaveBeenCalledTimes(1));
  });
});

// v5.264.0 — a text-message conversation: the viewer's comments on the right,
// others' on the left (v5.268.0), oldest first, a reply quoting what it answers.
describe('FindingCommentThread — conversation layout', () => {
  it('sides, order and reply quotes', async () => {
    // As the server sends them: newest first.  The thread reads oldest first.
    mocked.getFindingNotes.mockResolvedValue(page([
      { ...note(3, 'Thanks.'), created_at: '2026-08-01T02:00:00Z' },
      { ...note(2, 'Confirmed on my side.'), author_id: 2, author_name: 'ben', parent_id: 1, created_at: '2026-08-01T01:00:00Z' },
      { ...note(1, 'Found it.'), created_at: '2026-08-01T00:00:00Z', updated_at: '2026-08-01T00:00:01Z' },
    ]));
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

// Review 2026-10-10 H9 — the thread was the OLDEST 100 comments and nothing
// else: the 101st saved, notified and never appeared, for its author too.
// The server here holds more than two reads' worth.
describe('FindingCommentThread — a thread longer than one read', () => {
  const HELD = 230;
  /** Comment n of the thread (1 = the first written), a minute apart. */
  const nth = (n: number) => ({
    ...note(n, `comment ${n}`), author_id: 2, author_name: 'bo',
    created_at: new Date(Date.UTC(2026, 7, 1, 0, n)).toISOString(),
  });
  let held: Array<ReturnType<typeof nth>>;
  /** The server's rule: newest first, `offset` counted from the newest. */
  const serve = async (_project: number, _finding: number, { offset = 0, limit = 100 } = {}) => {
    const newestFirst = [...held].reverse();
    return page(newestFirst.slice(offset, offset + limit), held.length);
  };
  // The thread as someone who only reads it sees it.  (Queried by element,
  // not by role: a role query over hundreds of comments is what makes a
  // test of a long thread slow.)
  const renderReader = () => render(<FindingCommentThread findingId={7} canManage={false} />);
  const shown = () => [...document.querySelectorAll('[id^="note-"]')].map((el) => el.id);
  const heading = () => document.querySelector('h2')?.textContent ?? '';
  const earlier = () => [...document.querySelectorAll('button')]
    .find((b) => /Show earlier comments/.test(b.textContent ?? '')) ?? null;

  beforeEach(() => {
    held = Array.from({ length: HELD }, (_v, i) => nth(i + 1));
    mocked.getFindingNotes.mockImplementation(serve);
    window.location.hash = '';
  });

  it('opens on the newest comments, says how many came before, and walks back to the first', async () => {
    renderReader();
    await screen.findByText(`comment ${HELD}`);
    // The newest 100, oldest of them first; the count is the thread's.
    expect(shown()).toHaveLength(100);
    expect(shown()[0]).toBe('note-131');
    expect(shown()[99]).toBe(`note-${HELD}`);
    expect(heading()).toContain(`(${HELD})`);

    expect(earlier()).toHaveTextContent('Show earlier comments (130 earlier)');
    fireEvent.click(earlier() as HTMLElement);
    await screen.findByText('comment 31');
    expect(mocked.getFindingNotes).toHaveBeenLastCalledWith(1, 7, { offset: 100, limit: 100 }, expect.anything());
    expect(earlier()).toHaveTextContent('Show earlier comments (30 earlier)');
    fireEvent.click(earlier() as HTMLElement);
    await screen.findByText('comment 1');
    expect(shown()).toHaveLength(HELD);
    expect(shown()[0]).toBe('note-1');
    expect(shown()[HELD - 1]).toBe(`note-${HELD}`);
    // Every comment is on screen: nothing earlier is offered.
    expect(earlier()).toBeNull();
  });

  it('shows a comment the reader just posted, at the end', async () => {
    mocked.createFindingNote.mockImplementation(async (_p: number, _f: number, body: string) => {
      const made = { ...nth(HELD + 1), body, author_id: 1, author_name: 'ana' };
      held.push(made);
      return made;
    });
    renderThread();
    await screen.findByText(`comment ${HELD}`);
    fireEvent.change(screen.getByLabelText('New comment'), { target: { value: 'the one I just wrote' } });
    fireEvent.click(screen.getByText('Comment', { selector: 'button' }));
    expect(await screen.findByText('the one I just wrote')).toBeInTheDocument();
    expect(heading()).toContain(`(${HELD + 1})`);
    // At the end of what is on screen, where a conversation's newest is.
    const ids = shown();
    expect(ids[ids.length - 1]).toBe(`note-${HELD + 1}`);
  });

  it('a link to an earlier comment walks back until that comment is on screen', async () => {
    window.location.hash = '#note-5';
    renderReader();
    expect(await screen.findByText('comment 5')).toBeInTheDocument();
    // Three reads reach it: the newest 100, then two more.
    expect(mocked.getFindingNotes).toHaveBeenCalledTimes(3);
  });

  it('a failed "Show earlier comments" is said beside it and keeps the thread', async () => {
    renderReader();
    await screen.findByText(`comment ${HELD}`);
    mocked.getFindingNotes.mockRejectedValueOnce(new Error('503'));
    fireEvent.click(earlier() as HTMLElement);
    expect(await screen.findByText(/Comments couldn't load/)).toBeInTheDocument();
    expect(shown()).toHaveLength(100);
    expect(earlier()).toHaveTextContent('Show earlier comments (130 earlier)');
    expect(earlier()).toBeEnabled();
  });
});
