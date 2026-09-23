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

const META = {
  open: { label: 'Open', badgeVariant: 'info' },
  in_progress: { label: 'In Progress', badgeVariant: 'warning' },
  resolved: { label: 'Resolved', badgeVariant: 'success' },
} as const;

const renderThread = (topLevel: Annotation[], replies: Record<number, Annotation[]> = {}, canManage = true) =>
  render(
    <MemoryRouter>
      <TooltipProvider>
        <NoteThread
          topLevel={topLevel}
          repliesByParent={replies}
          noteStatusMeta={META as never}
          replyTo={null}
          replyBody=""
          onReplyToChange={vi.fn()}
          onReplyBodyChange={vi.fn()}
          onSubmitReply={vi.fn()}
          noteSubmitting={false}
          noteActionId={null}
          onUpdateNoteStatus={vi.fn()}
          onDeleteNote={vi.fn()}
          hostId={1}
          canManageNotes={canManage}
          onAttachmentsChanged={vi.fn()}
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

  it('a root note states its status once — in the select; a reply keeps its badge', () => {
    renderThread([note()], { 1: [note({ id: 2, parent_id: 1, status: 'resolved', body: 'Patched.' })] });
    expect(screen.getByRole('combobox', { name: /Update status for note by Ada/ })).toHaveTextContent('Open');
    expect(screen.getAllByText('Open')).toHaveLength(1);
    expect(screen.getByText('Resolved')).toBeInTheDocument();
  });
});

// v5.264.0 — notes read like a text conversation: the viewer's on the left,
// everyone else's on the right; replies in the order written, quoting what
// they answer when it is not the thread's first note.
describe('NoteThread — conversation layout', () => {
  it("puts the viewer's notes on the left and others' on the right, in order", () => {
    const root = note({ id: 1, author_id: 7, author_name: 'Ada', body: 'Root.' } as Partial<Annotation>);
    const r1 = note({ id: 2, parent_id: 1, author_id: 9, author_name: 'Bo', body: 'First reply.', created_at: '2026-09-19T11:00:00Z' } as Partial<Annotation>);
    const r2 = note({ id: 3, parent_id: 2, author_id: 7, author_name: 'Ada', body: 'Answering Bo.', created_at: '2026-09-19T12:00:00Z' } as Partial<Annotation>);
    const { container } = render(
      <MemoryRouter>
        <TooltipProvider>
          <NoteThread
            topLevel={[root]} repliesByParent={{ 1: [r1], 2: [r2] }} noteStatusMeta={META as never}
            replyTo={null} replyBody="" onReplyToChange={vi.fn()} onReplyBodyChange={vi.fn()}
            onSubmitReply={vi.fn()} noteSubmitting={false} noteActionId={null}
            onUpdateNoteStatus={vi.fn()} onDeleteNote={vi.fn()} hostId={1} canManageNotes
            onAttachmentsChanged={vi.fn()} currentUserId={7}
          />
        </TooltipProvider>
      </MemoryRouter>,
    );
    const sides = [...container.querySelectorAll('[data-side]')].map((el) => [el.id, el.getAttribute('data-side')]);
    expect(sides).toEqual([['note-1', 'mine'], ['note-2', 'theirs'], ['note-3', 'mine']]);
    expect(container.querySelector('#note-1')).toHaveClass('items-start');
    expect(container.querySelector('#note-2')).toHaveClass('items-end');
    // A reply to a reply quotes it; a reply to the first note does not.
    expect(screen.getByText(/Replying to/)).toHaveTextContent('Replying to Bo: First reply.');
    expect(screen.getAllByText(/Replying to/)).toHaveLength(1);
    // No reply is indented.
    expect(container.querySelector('.border-l-2.ml-sm, .ml-md, .ml-lg')).toBeNull();
  });
});
