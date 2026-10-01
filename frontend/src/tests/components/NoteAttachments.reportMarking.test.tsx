import { render, screen, fireEvent, waitFor } from '@testing-library/react';
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
    await waitFor(() => expect(mocked.setNoteAttachmentInReport).toHaveBeenCalledWith(1, true));
    expect(onChanged).toHaveBeenCalled();
    expect(box).toHaveAttribute('data-state', 'checked');
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
    const onImagesChanged = vi.fn();
    render(
      <NoteAttachments noteId={5} canManage onChanged={onChanged} attachments={[att(1, { caption: 'Old words' })]}
        reportMarking={{ canMark: () => true, captionMax: 2000, onImagesChanged }} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Edit caption for shot-1.png' }));
    const box = screen.getByRole('textbox', { name: 'Caption for shot-1.png' });
    expect(box).toHaveValue('Old words');
    expect(box).toHaveAttribute('maxlength', '2000');
    fireEvent.change(box, { target: { value: '  New words  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save caption' }));
    await waitFor(() => expect(mocked.setNoteAttachmentCaption).toHaveBeenCalledWith(1, 'New words'));
    await waitFor(() => expect(onImagesChanged).toHaveBeenCalled());
    expect(onChanged).toHaveBeenCalled();
    expect(screen.queryByRole('textbox', { name: 'Caption for shot-1.png' })).not.toBeInTheDocument();
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
