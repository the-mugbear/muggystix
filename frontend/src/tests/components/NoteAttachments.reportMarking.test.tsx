import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../../services/api', () => ({
  uploadNoteAttachment: vi.fn(),
  deleteNoteAttachment: vi.fn(),
  getNoteAttachmentObjectUrl: vi.fn(),
  setNoteAttachmentInReport: vi.fn(),
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
