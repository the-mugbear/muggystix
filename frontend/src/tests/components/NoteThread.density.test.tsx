import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const api = vi.hoisted(() => ({
  uploadNoteAttachment: vi.fn(),
  deleteNoteAttachment: vi.fn(),
  getNoteAttachmentObjectUrl: vi.fn(),
}));
vi.mock('../../services/api', () => api);
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import { NoteThread } from '../../components/host-inspector/NoteThread';
import { TooltipProvider } from '../../components/ui/tooltip';
import type { Annotation } from '../../services/api';

const note = (over: Partial<Annotation> = {}): Annotation => ({
  id: 1, body: 'Confirmed exposed.', status: 'open', author_name: 'Ada', parent_id: null,
  created_at: '2026-09-19T10:00:00Z', updated_at: null, attachments: [], ...over,
} as unknown as Annotation);

const renderThread = (topLevel: Annotation[], replies: Record<number, Annotation[]> = {}, canManage = true) =>
  render(
    <MemoryRouter>
      <TooltipProvider>
        <NoteThread
          topLevel={topLevel}
          repliesByParent={replies}
          replyTo={null}
          replyBody=""
          onReplyToChange={vi.fn()}
          onReplyBodyChange={vi.fn()}
          onSubmitReply={vi.fn()}
          noteSubmitting={false}
          noteActionId={null}
          onDeleteNote={vi.fn()}
          hostId={1}
          canManageNotes={canManage}
        />
      </TooltipProvider>
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  // jsdom has no object URLs; NoteAttachments revokes its own on unmount.
  URL.revokeObjectURL = vi.fn();
});

// v5.241.0 — every note used to spend a row of its own on "Attach image" and
// say its status twice (a badge beside the select that sets it).
describe('NoteThread — row density', () => {
  it('attaching is one of the note\'s actions, not a row under every note', () => {
    const { container } = renderThread([note()]);
    expect(screen.queryByText('Attach image')).not.toBeInTheDocument();
    const attach = screen.getByRole('button', { name: 'Attach image' });
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const click = vi.spyOn(input, 'click');
    fireEvent.click(attach);
    expect(click).toHaveBeenCalled();
  });

  // Code review finding 22 — the external button had no busy rule: a second
  // pick started a second upload over the first, and they shared one flag.
  it('refuses a second pick while an upload is in flight, and says it is uploading', async () => {
    let finishFirst: () => void = () => undefined;
    api.uploadNoteAttachment.mockImplementationOnce(() => new Promise<void>((resolve) => { finishFirst = resolve; }));
    api.uploadNoteAttachment.mockResolvedValue(undefined);
    const { container } = renderThread([note()]);
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    const pick = (name: string) =>
      fireEvent.change(input, { target: { files: [new File(['x'], name, { type: 'image/png' })] } });
    const opened = vi.spyOn(input, 'click');

    pick('first.png');
    const button = await screen.findByRole('button', { name: 'Uploading image…' });
    expect(button).toBeDisabled();

    // Neither the control nor the handler lets a second one through.
    pick('second.png');
    fireEvent.click(button);
    expect(opened).not.toHaveBeenCalled();
    expect(api.uploadNoteAttachment).toHaveBeenCalledTimes(1);

    finishFirst();
    expect(await screen.findByRole('button', { name: 'Attach image' })).toBeEnabled();
    pick('third.png');
    await waitFor(() => expect(api.uploadNoteAttachment).toHaveBeenCalledTimes(2));
  });

  it('a viewer who cannot manage notes gets no attach action', () => {
    renderThread([note()], {}, false);
    expect(screen.queryByRole('button', { name: 'Attach image' })).not.toBeInTheDocument();
  });

  // A note body is unbounded — an agent's assessment, or a pasted paragraph,
  // ran to a screen and buried everything under it.
  it('clamps a long body until asked for, and leaves a short one alone', () => {
    const long = 'Lorem ipsum dolor sit amet. '.repeat(40);
    renderThread([note({ id: 1, body: long }), note({ id: 2, body: 'Short.' })]);
    const body = screen.getByText(long.trim());
    expect(body).toHaveClass('line-clamp-4');
    expect(screen.getByText('Short.')).not.toHaveClass('line-clamp-4');
    expect(screen.getAllByRole('button', { name: 'Show full note' })).toHaveLength(1);

    fireEvent.click(screen.getByRole('button', { name: 'Show full note' }));
    expect(body).not.toHaveClass('line-clamp-4');
    expect(screen.getByRole('button', { name: 'Show less' })).toHaveAttribute('aria-expanded', 'true');
  });

  it('clamps a body of many short lines too (agent markdown)', () => {
    renderThread([note({ body: ['## Assessment', '', '- a', '- b', '- c', '- d'].join('\n') })]);
    expect(screen.getByRole('button', { name: 'Show full note' })).toBeInTheDocument();
  });

  // 5.325.0 — a note is discussion: it has no status to set, cannot be
  // assigned or resolved, and is not a way to make a finding. (This replaces
  // "a root note states its status once — in the select; a reply keeps its
  // badge": the control it pinned is gone on purpose.)
  it('offers no status control, no work fields and no promotion — even if an old payload carries them', () => {
    renderThread(
      [note({ status: 'open', assignee_name: 'Bo', due_at: '2026-10-01T00:00:00Z', resolution_summary: 'Patched.' } as never)],
      { 1: [note({ id: 2, parent_id: 1, status: 'resolved', body: 'Patched it.' } as never)] },
    );
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(screen.queryByText('Open')).not.toBeInTheDocument();
    expect(screen.queryByText('Resolved')).not.toBeInTheDocument();
    expect(screen.queryByText(/Assigned to/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Resolution:/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Promote note to finding/ })).not.toBeInTheDocument();
  });

  it('still says which finding an old thread was promoted to', () => {
    renderThread([note({ finding_id: 9 } as Partial<Annotation>)]);
    expect(screen.getByRole('link', { name: 'View the finding promoted from this note' })).toHaveAttribute('href', '/findings/9');
  });
});

// v5.264.0 — notes read like a text conversation: the viewer's on the right,
// everyone else's on the left (the phone convention, v5.268.0); replies in the
// order written, quoting what they answer when it is not the first note.
describe('NoteThread — conversation layout', () => {
  it("puts the viewer's notes on the right and others' on the left, in order", () => {
    const root = note({ id: 1, author_id: 7, author_name: 'Ada', body: 'Root.' } as Partial<Annotation>);
    const r1 = note({ id: 2, parent_id: 1, author_id: 9, author_name: 'Bo', body: 'First reply.', created_at: '2026-09-19T11:00:00Z' } as Partial<Annotation>);
    const r2 = note({ id: 3, parent_id: 2, author_id: 7, author_name: 'Ada', body: 'Answering Bo.', created_at: '2026-09-19T12:00:00Z' } as Partial<Annotation>);
    const { container } = render(
      <MemoryRouter>
        <TooltipProvider>
          <NoteThread
            topLevel={[root]} repliesByParent={{ 1: [r1], 2: [r2] }}
            replyTo={null} replyBody="" onReplyToChange={vi.fn()} onReplyBodyChange={vi.fn()}
            onSubmitReply={vi.fn()} noteSubmitting={false} noteActionId={null}
            onDeleteNote={vi.fn()} hostId={1} canManageNotes
            currentUserId={7}
          />
        </TooltipProvider>
      </MemoryRouter>,
    );
    const sides = [...container.querySelectorAll('[data-side]')].map((el) => [el.id, el.getAttribute('data-side')]);
    expect(sides).toEqual([['note-1', 'mine'], ['note-2', 'theirs'], ['note-3', 'mine']]);
    expect(container.querySelector('#note-1')).toHaveClass('items-end');
    expect(container.querySelector('#note-2')).toHaveClass('items-start');
    // A reply to a reply quotes it; a reply to the first note does not.
    expect(screen.getByText(/Replying to/)).toHaveTextContent('Replying to Bo: First reply.');
    expect(screen.getAllByText(/Replying to/)).toHaveLength(1);
    // No reply is indented.
    expect(container.querySelector('.border-l-2.ml-sm, .ml-md, .ml-lg')).toBeNull();
  });
});
