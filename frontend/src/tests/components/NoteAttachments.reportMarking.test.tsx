import { act, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/api', () => ({
  uploadNoteAttachment: vi.fn(),
  deleteNoteAttachment: vi.fn(),
  getNoteAttachmentObjectUrl: vi.fn(),
  setNoteAttachmentInReport: vi.fn(),
  setNoteAttachmentCaption: vi.fn(),
}));
const toastMock = { success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({ useToast: () => toastMock }));

import * as api from '../../services/api';
import { readsOnScreen } from '../helpers/readsOnScreen';
import NoteAttachments from '../../components/host-inspector/NoteAttachments';

const mocked = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const att = (id: number, over: Record<string, unknown> = {}) => ({
  id, filename: `shot-${id}.png`, content_type: 'image/png', size_bytes: 10,
  created_at: '2026-09-01T00:00:00Z', include_in_report: false, uploaded_by_id: 1, ...over,
});

beforeEach(() => {
  vi.clearAllMocks();
  // jsdom has no object URLs; the component revokes its own on unmount.
  URL.revokeObjectURL = vi.fn();
  mocked.getNoteAttachmentObjectUrl.mockResolvedValue('blob:x');
});

describe('NoteAttachments — report marking (v5.260.0)', () => {
  it('marks an image the viewer may mark, and shows the others read-only', async () => {
    mocked.setNoteAttachmentInReport.mockResolvedValue(att(1, { include_in_report: true }));
    const onChanged = vi.fn();
    render(
      <NoteAttachments
        noteId={5}
        attachments={[att(1), att(2, { uploaded_by_id: 9, include_in_report: true })]}
        canManage
        onChanged={onChanged}
        reportMarking={{ canMark: (a) => a.uploaded_by_id === 1 }}
      />,
    );
    const box = screen.getByRole('checkbox', { name: 'Include shot-1.png in the report' });
    // Someone else's image that is already in: stated, not editable.
    expect(screen.queryByRole('checkbox', { name: 'Include shot-2.png in the report' })).not.toBeInTheDocument();
    expect(screen.getAllByText('In report')).toHaveLength(2);

    fireEvent.click(box);
    await waitFor(() => expect(mocked.setNoteAttachmentInReport).toHaveBeenCalledWith(1, 1, true));
    expect(onChanged).toHaveBeenCalled();
    expect(box).toHaveAttribute('data-state', 'checked');
  });

  // 5.366.0 — the flip was a bare boolean kept for the life of the component:
  // once the reader had ticked an image, a later change made by someone else
  // (the thread read again with another value) stayed hidden behind it.
  it('after a mark, what the thread says next is shown — a later change by someone else is not hidden', async () => {
    mocked.setNoteAttachmentInReport.mockResolvedValue(att(1, { include_in_report: true }));
    const props = { noteId: 5, canManage: true, onChanged: vi.fn(), reportMarking: { canMark: () => true } };
    const { rerender } = render(<NoteAttachments {...props} attachments={[att(1)]} />);
    const box = () => screen.getByRole('checkbox', { name: 'Include shot-1.png in the report' });
    fireEvent.click(box());
    await waitFor(() => expect(mocked.setNoteAttachmentInReport).toHaveBeenCalledTimes(1));
    expect(box()).toHaveAttribute('data-state', 'checked');

    // The thread is read again and agrees.
    rerender(<NoteAttachments {...props} attachments={[att(1, { include_in_report: true })]} />);
    expect(box()).toHaveAttribute('data-state', 'checked');
    // Later, someone else took it out of the report.
    rerender(<NoteAttachments {...props} attachments={[att(1, { include_in_report: false })]} />);
    expect(box()).toHaveAttribute('data-state', 'unchecked');
  });

  it('puts the mark back when the server refuses', async () => {
    mocked.setNoteAttachmentInReport.mockRejectedValue(new Error('403'));
    render(
      <NoteAttachments noteId={5} attachments={[att(1)]} canManage onChanged={vi.fn()}
        reportMarking={{ canMark: () => true }} />,
    );
    const box = screen.getByRole('checkbox', { name: 'Include shot-1.png in the report' });
    fireEvent.click(box);
    await waitFor(() => expect(toastMock.error).toHaveBeenCalled());
    expect(box).toHaveAttribute('data-state', 'unchecked');
  });

  it('shows no marks where the surface is not a finding', () => {
    render(<NoteAttachments noteId={5} attachments={[att(1, { include_in_report: true })]} canManage={false} onChanged={vi.fn()} />);
    expect(screen.queryByText('In report')).not.toBeInTheDocument();
  });
});

describe('NoteAttachments — captions and placement on a finding', () => {
  const placement = (map: Record<number, string[]>) => (a: { id: number; include_in_report?: boolean }) => ({
    in_report: !!a.include_in_report, printable: true, placed_in: map[a.id] ?? [],
  });

  it('shows each image’s caption and where the report text places it', () => {
    render(
      <NoteAttachments noteId={5} canManage onChanged={vi.fn()}
        attachments={[
          att(1, { include_in_report: true, caption: 'The relayed session' }),
          att(2, { include_in_report: true }),
          att(3, { caption: null }),
        ]}
        reportMarking={{ canMark: () => true, placement: placement({ 1: ['description', 'impact'], 3: ['impact'] }) }}
      />,
    );
    expect(screen.getByTestId('caption-1')).toHaveTextContent('The relayed session');
    expect(screen.getByTestId('caption-2')).toHaveTextContent('No caption — the report prints “shot-2.png”');
    expect(screen.getByTestId('placement-1')).toHaveTextContent('In: Description, Impact');
    expect(screen.getByTestId('placement-2')).toHaveTextContent('Not placed — prints under Evidence');
    // Referenced by the text but not ticked: it will not print, and says so.
    expect(screen.getByTestId('placement-3')).toHaveTextContent('Referenced in Impact, but not ticked');
  });

  it('edits a caption in place and tells the page', async () => {
    mocked.setNoteAttachmentCaption = vi.fn().mockResolvedValue(att(1, { caption: 'New words' }));
    const onChanged = vi.fn();
    // 5.351.0 — the page was told through `reportMarking.onImagesChanged`
    // (which re-read the finding's images); the change now says that read is
    // out of date itself.  This stands in for the page's read of them.
    const { reread, ReadsOnScreen } = readsOnScreen({ getFindingImages: 'the finding’s images' });
    render(
      <>
        <ReadsOnScreen />
        <NoteAttachments noteId={5} canManage onChanged={onChanged} attachments={[att(1, { caption: 'Old words' })]}
          reportMarking={{ canMark: () => true, captionMax: 2000 }} />
      </>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Edit caption for shot-1.png' }));
    const box = screen.getByRole('textbox', { name: 'Caption for shot-1.png' });
    expect(box).toHaveValue('Old words');
    expect(box).toHaveAttribute('maxlength', '2000');
    fireEvent.change(box, { target: { value: '  New words  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save caption' }));
    await waitFor(() => expect(mocked.setNoteAttachmentCaption).toHaveBeenCalledWith(1, 1, 'New words'));
    await waitFor(() => expect(reread).toHaveBeenCalledWith('the finding’s images'));
    expect(reread).toHaveBeenCalledTimes(1);
    expect(onChanged).toHaveBeenCalled();
    expect(screen.queryByRole('textbox', { name: 'Caption for shot-1.png' })).not.toBeInTheDocument();
  });

  // Browser pass 2026-10-01 — "Add caption" opened the field without focusing
  // it, so the next keystrokes went to the page (and its shortcuts).
  describe('the caption editor takes the keyboard', () => {
    const setup = (caption: string | null) => {
      mocked.setNoteAttachmentCaption = vi.fn().mockResolvedValue(att(1, { caption: 'saved' }));
      render(
        <NoteAttachments noteId={5} canManage onChanged={vi.fn()} attachments={[att(1, { caption })]}
          reportMarking={{ canMark: () => true }} />,
      );
      fireEvent.click(screen.getByRole('button', { name: `${caption ? 'Edit' : 'Add'} caption for shot-1.png` }));
      return screen.getByRole('textbox', { name: 'Caption for shot-1.png' }) as HTMLTextAreaElement;
    };

    it('focuses the field when a caption is added', () => {
      expect(setup(null)).toHaveFocus();
    });

    it('selects the existing words when a caption is edited — once, not on every keystroke', () => {
      const box = setup('Old words');
      expect(box).toHaveFocus();
      expect([box.selectionStart, box.selectionEnd]).toEqual([0, 'Old words'.length]);
      fireEvent.change(box, { target: { value: 'Old words, and more' } });
      box.setSelectionRange(3, 3);
      fireEvent.change(box, { target: { value: 'Old words, and more.' } });
      expect(box).toHaveFocus();
      expect(box.selectionStart).not.toBe(0);
    });

    it('Enter saves; Shift+Enter and an Enter that confirms an IME composition do not', async () => {
      const box = setup(null);
      fireEvent.change(box, { target: { value: 'The relayed session' } });
      fireEvent.keyDown(box, { key: 'Enter', shiftKey: true });
      fireEvent.keyDown(box, { key: 'Enter', isComposing: true });
      fireEvent.keyDown(box, { key: 'Enter', keyCode: 229 });
      expect(mocked.setNoteAttachmentCaption).not.toHaveBeenCalled();
      fireEvent.keyDown(box, { key: 'Enter' });
      await waitFor(() => expect(mocked.setNoteAttachmentCaption).toHaveBeenCalledWith(1, 1, 'The relayed session'));
    });

    it('Escape cancels, saves nothing, and hands the keyboard back to the button', async () => {
      const box = setup('Old words');
      fireEvent.change(box, { target: { value: 'Half a thought' } });
      fireEvent.keyDown(box, { key: 'Escape' });
      expect(screen.queryByRole('textbox', { name: 'Caption for shot-1.png' })).not.toBeInTheDocument();
      expect(mocked.setNoteAttachmentCaption).not.toHaveBeenCalled();
      expect(screen.getByTestId('caption-1')).toHaveTextContent('Old words');
      await waitFor(() => expect(screen.getByRole('button', { name: 'Edit caption for shot-1.png' })).toHaveFocus());
    });
  });

  it('keeps the caption editor open, with the reason, when the server refuses it', async () => {
    mocked.setNoteAttachmentCaption = vi.fn().mockRejectedValue({
      response: { status: 422, data: { detail: 'A caption is at most 2000 characters (this one is 2001).' } },
    });
    render(
      <NoteAttachments noteId={5} canManage onChanged={vi.fn()} attachments={[att(1)]}
        reportMarking={{ canMark: () => true }} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Add caption for shot-1.png' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Caption for shot-1.png' }), { target: { value: 'x' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save caption' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('at most 2000 characters');
    expect(screen.getByRole('textbox', { name: 'Caption for shot-1.png' })).toHaveValue('x');
  });

  it('keeps the server’s refusal beside a placed image that was deleted or un-ticked', async () => {
    const refusal = 'Cannot delete this image: image 1 is placed in the Description of finding #7 "SMB relay". '
      + 'Remove the reference — ![…](evidence:1) — from that section first, then delete it.';
    mocked.deleteNoteAttachment.mockRejectedValue({ response: { status: 409, data: { detail: refusal } } });
    mocked.setNoteAttachmentInReport.mockRejectedValue({
      response: { status: 409, data: { detail: 'Cannot take this image out of the report: image 1 is placed in the Description.' } },
    });
    const onChanged = vi.fn();
    render(
      <NoteAttachments noteId={5} canManage onChanged={onChanged}
        attachments={[att(1, { include_in_report: true })]}
        reportMarking={{ canMark: () => true, placement: placement({ 1: ['description'] }) }} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Delete shot-1.png' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(refusal);
    expect(onChanged).not.toHaveBeenCalled();

    const box = screen.getByRole('checkbox', { name: 'Include shot-1.png in the report' });
    fireEvent.click(box);
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Cannot take this image out of the report'));
    expect(box).toHaveAttribute('data-state', 'checked');     // the mark went back
    expect(screen.getByTestId('placement-1')).toHaveTextContent('In: Description');
  });

  // Review 2026-10-01 S6 — a caption's save belongs to its image.
  describe('saving one caption while editing another', () => {
    const open = (name: string) => fireEvent.click(screen.getByRole('button', { name }));
    const setup = () => render(
      <NoteAttachments noteId={5} canManage onChanged={vi.fn()}
        attachments={[att(1, { caption: 'First' }), att(2, { caption: 'Second' })]}
        reportMarking={{ canMark: () => true }} />,
    );

    it('a save that succeeds leaves the other image’s editor, and its text, alone', async () => {
      let done!: (v: unknown) => void;
      mocked.setNoteAttachmentCaption = vi.fn().mockReturnValue(new Promise((resolve) => { done = resolve; }));
      setup();
      open('Edit caption for shot-1.png');
      fireEvent.change(screen.getByRole('textbox', { name: 'Caption for shot-1.png' }), { target: { value: 'First, better' } });
      fireEvent.click(screen.getByRole('button', { name: 'Save caption' }));
      // The first is still saving; the author moves on to the second.
      open('Edit caption for shot-2.png');
      const second = screen.getByRole('textbox', { name: 'Caption for shot-2.png' });
      fireEvent.change(second, { target: { value: 'Second, half typed' } });
      expect(second).not.toBeDisabled();
      expect(screen.getByText('Saving caption…')).toBeInTheDocument();

      await act(async () => { done(att(1, { caption: 'First, better' })); });
      expect(screen.getByRole('textbox', { name: 'Caption for shot-2.png' })).toHaveValue('Second, half typed');
      expect(screen.queryByText('Saving caption…')).toBeNull();
    });

    it('a save that fails says so beside ITS image, keeps the other editor, and offers the unsaved words again', async () => {
      let fail!: (e: unknown) => void;
      mocked.setNoteAttachmentCaption = vi.fn().mockReturnValue(new Promise((_, reject) => { fail = reject; }));
      setup();
      open('Edit caption for shot-1.png');
      fireEvent.change(screen.getByRole('textbox', { name: 'Caption for shot-1.png' }), { target: { value: 'First, refused' } });
      fireEvent.click(screen.getByRole('button', { name: 'Save caption' }));
      open('Edit caption for shot-2.png');
      fireEvent.change(screen.getByRole('textbox', { name: 'Caption for shot-2.png' }), { target: { value: 'Second, half typed' } });

      await act(async () => { fail({ response: { status: 422, data: { detail: 'Too long.' } } }); });
      expect(screen.getByRole('alert')).toHaveTextContent('Too long.');
      expect(screen.getByRole('textbox', { name: 'Caption for shot-2.png' })).toHaveValue('Second, half typed');
      expect(screen.queryByRole('textbox', { name: 'Caption for shot-1.png' })).toBeNull();
      // Back on the first image: what could not be saved is still there.
      open('Edit caption for shot-1.png');
      expect(screen.getByRole('textbox', { name: 'Caption for shot-1.png' })).toHaveValue('First, refused');
    });
  });

  // M6 — thumbnails: one cache for the page, and a failure that is said.
  describe('thumbnails', () => {
    const cache = (over: Record<string, unknown> = {}) => ({
      listStatus: 'ready' as const, has: (id: number) => id === 1, urls: { 1: 'blob:shared' },
      ensure: vi.fn(), failed: () => false, retry: vi.fn(), ...over,
    });

    it('shows an image on the finding’s list from the page’s cache, and fetches only what the list lacks', async () => {
      const thumbnails = cache();
      render(
        <NoteAttachments noteId={5} canManage={false} onChanged={vi.fn()} attachments={[att(1), att(2)]}
          reportMarking={{ canMark: () => false, thumbnails }} />,
      );
      expect(screen.getByRole('button', { name: 'View shot-1.png' }).querySelector('img')).toHaveAttribute('src', 'blob:shared');
      expect(thumbnails.ensure).toHaveBeenCalledWith(1);
      await waitFor(() => expect(screen.getByRole('button', { name: 'View shot-2.png' }).querySelector('img')).toHaveAttribute('src', 'blob:x'));
      // (the project, then the attachment)
      expect(mocked.getNoteAttachmentObjectUrl.mock.calls.map((c) => [c[0], c[1]])).toEqual([[1, 2]]);
    });

    it('fetches nothing of its own while the finding’s list is still being read', async () => {
      render(
        <NoteAttachments noteId={5} canManage={false} onChanged={vi.fn()} attachments={[att(1)]}
          reportMarking={{ canMark: () => false, thumbnails: cache({ listStatus: 'loading', has: () => false, urls: {} }) }} />,
      );
      await new Promise((r) => setTimeout(r, 10));
      expect(mocked.getNoteAttachmentObjectUrl).not.toHaveBeenCalled();
    });

    it('says when a thumbnail could not be loaded, and a click tries again', async () => {
      mocked.getNoteAttachmentObjectUrl.mockRejectedValueOnce(new Error('503'));
      render(<NoteAttachments noteId={5} canManage={false} onChanged={vi.fn()} attachments={[att(1)]} />);
      const failed = await screen.findByRole('button', { name: 'shot-1.png could not be loaded — try again' });
      expect(screen.getByTestId('thumbnail-failed-1')).toHaveTextContent('Retry');
      fireEvent.click(failed);
      await waitFor(() => expect(screen.getByRole('button', { name: 'View shot-1.png' }).querySelector('img')).toHaveAttribute('src', 'blob:x'));
    });
  });

  it('gives a reader the caption and the mark, and no control', () => {
    render(
      <NoteAttachments noteId={5} canManage={false} onChanged={vi.fn()}
        attachments={[att(1, { include_in_report: true, caption: 'c'.repeat(2000) })]}
        reportMarking={{ canMark: () => false, placement: placement({}) }} />,
    );
    const caption = screen.getByTestId('caption-1');
    expect(caption).toHaveTextContent('c'.repeat(2000));
    expect(caption.className).toContain('line-clamp-3');       // 2,000 characters do not push the page
    expect(screen.queryByRole('button', { name: /caption for/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Delete/ })).not.toBeInTheDocument();
    expect(screen.getByText('In report')).toBeInTheDocument();
  });
});
