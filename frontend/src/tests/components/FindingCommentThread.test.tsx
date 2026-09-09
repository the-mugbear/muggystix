import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/api', () => ({
  getFindingNotes: vi.fn(),
  createFindingNote: vi.fn(),
  uploadFindingNoteAttachment: vi.fn(),
}));
const toastMock = { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));
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
