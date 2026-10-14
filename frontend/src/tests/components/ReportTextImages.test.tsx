/**
 * The images of the project's own report text (the System description's):
 * listed with caption and where the saved text places each, uploaded with an
 * optional caption, captioned in place, deleted — and the server's refusal of
 * a delete (it names the text that still places the image) is shown on the
 * row.  Writes are an analyst's, and on one image its uploader's or a project
 * admin's; a reader sees the list and no control.
 */
import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  listReportTextImages: vi.fn(),
  uploadReportTextImage: vi.fn(),
  getNoteAttachmentBlob: vi.fn(),
  setNoteAttachmentCaption: vi.fn(),
  deleteNoteAttachment: vi.fn(),
}));
vi.mock('../../services/api', () => api);
const who = vi.hoisted(() => ({ role: 'member', projectRole: 'analyst' as string }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 7, username: 'ana', role: who.role } }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'P', my_role: who.projectRole } }),
}));
const confirmMock = vi.hoisted(() => vi.fn());
vi.mock('../../hooks/useConfirm', () => ({ useConfirm: () => [null, confirmMock] }));

import ReportTextImages, { placedInLabel, reportImageProblem } from '../../components/reports/ReportTextImages';
import { useReportTextImages } from '../../hooks/useReportTextImages';
import { blobFor, installBlobUrls } from '../helpers/blobUrls';

const LONG = 'network-diagram-'.repeat(20);
const image = (id: number, over: Record<string, unknown> = {}) => ({
  id, filename: `diagram-${id}.png`, caption: null, content_type: 'image/png', size_bytes: 10,
  uploaded_by_id: 7, created_at: '2026-10-13T00:00:00Z', placed_in: [], can_modify: true, ...over,
});

const Harness: React.FC<{ disabled?: boolean }> = ({ disabled }) => {
  const state = useReportTextImages();
  return <ReportTextImages idPrefix="t" state={state} disabled={disabled} />;
};
const pick = (file: File) => fireEvent.change(screen.getByTestId('t-system-image-file'), { target: { files: [file] } });
const png = (name = 'net.png', size = 10) => {
  const file = new File(['x'], name, { type: 'image/png' });
  Object.defineProperty(file, 'size', { value: size });
  return file;
};

beforeEach(() => {
  vi.clearAllMocks();
  installBlobUrls();
  URL.revokeObjectURL = vi.fn();
  who.role = 'member';
  who.projectRole = 'analyst';
  api.getNoteAttachmentBlob.mockImplementation((_project: number, id: number) => Promise.resolve(blobFor(`blob:image-${id}`)));
});

describe('ReportTextImages — the list', () => {
  it('shows each image with its caption and where the saved text places it', async () => {
    api.listReportTextImages.mockResolvedValue([
      image(3, { caption: 'The assessed network', placed_in: ['defaults', 'report 12', 'report 14'] }),
      image(4, { filename: `${LONG}.png` }),
    ]);
    render(<Harness />);
    const list = await screen.findByRole('list', { name: 'Images of the report text' });
    expect(within(list).getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByLabelText('Caption for image 3 (diagram-3.png)')).toHaveValue('The assessed network');
    expect(screen.getByTestId('report-image-meta-3')).toHaveTextContent('Placed in: report defaults, draft #12, draft #14');
    expect(screen.getByTestId('report-image-meta-4')).toHaveTextContent('Not placed in any saved text — not printed');
    // The one sentence of explanation.
    expect(screen.getByText(/they belong to the project, so its report defaults and every draft can place the same ones/))
      .toBeInTheDocument();
    // The pictures come through the attachment's own route, for the listed ids.
    await waitFor(() => expect(list.querySelector('img[src="blob:image-3"]')).not.toBeNull());
    expect(api.getNoteAttachmentBlob.mock.calls.map((c) => c[1]).sort()).toEqual([3, 4]);
    expect(api.listReportTextImages).toHaveBeenCalledWith(1, expect.anything());
  });

  // Owner, 2026-10-14: a 64-pixel thumbnail could not show which image was uploaded.
  it('opens an image full size from its thumbnail, with its caption, and closes again', async () => {
    api.listReportTextImages.mockResolvedValue([image(3, { caption: 'The assessed network' })]);
    render(<Harness />);
    const open = await screen.findByRole('button', { name: 'View image 3 (diagram-3.png) full size' });
    fireEvent.click(open);
    const viewer = await screen.findByRole('dialog');
    expect(viewer.querySelector('img[src="blob:image-3"]')).not.toBeNull();
    expect(viewer).toHaveTextContent('The assessed network');
    fireEvent.keyDown(viewer, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('says when there are none, and when the list could not be read (with a retry)', async () => {
    api.listReportTextImages.mockResolvedValueOnce([]);
    const first = render(<Harness />);
    expect(await screen.findByText('No image uploaded yet.')).toBeInTheDocument();
    first.unmount();

    api.listReportTextImages.mockRejectedValueOnce(new Error('HTTP 500')).mockResolvedValue([image(3)]);
    render(<Harness />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the report’s images.');
    expect(screen.queryByText('No image uploaded yet.')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByLabelText('Caption for image 3 (diagram-3.png)')).toBeInTheDocument();
  });

  it('maps the server’s placements to the page’s words', () => {
    expect(placedInLabel(['defaults'])).toBe('report defaults');
    expect(placedInLabel(['report 7', 'defaults'])).toBe('draft #7, report defaults');
    expect(placedInLabel(['elsewhere'])).toBe('elsewhere');
  });
});

describe('ReportTextImages — upload', () => {
  beforeEach(() => { api.listReportTextImages.mockResolvedValue([]); });

  it('sends the file with the caption typed for it, then reads the list again', async () => {
    let finish!: (value: unknown) => void;
    api.uploadReportTextImage.mockReturnValue(new Promise((resolve) => { finish = resolve; }));
    render(<Harness />);
    await screen.findByText('No image uploaded yet.');
    fireEvent.change(screen.getByLabelText('Caption for the next image (optional)'), { target: { value: 'The DMZ' } });
    const file = png();
    pick(file);
    // While it is sent: said, and no second upload.
    expect(await screen.findByRole('button', { name: 'Uploading…' })).toBeDisabled();
    expect(api.uploadReportTextImage).toHaveBeenCalledWith(1, file, 'The DMZ');

    api.listReportTextImages.mockResolvedValue([image(9, { caption: 'The DMZ' })]);
    finish(image(9, { caption: 'The DMZ' }));
    expect(await screen.findByLabelText('Caption for image 9 (diagram-9.png)')).toHaveValue('The DMZ');
    expect(screen.getByRole('button', { name: 'Upload image' })).toBeEnabled();
    expect(screen.getByLabelText('Caption for the next image (optional)')).toHaveValue('');
    expect(api.listReportTextImages).toHaveBeenCalledTimes(2);
  });

  it('refuses a file over 10 MB or of another type before sending it, in plain words', async () => {
    render(<Harness />);
    await screen.findByText('No image uploaded yet.');
    pick(png('huge.png', 10 * 1024 * 1024 + 1));
    expect(await screen.findByRole('alert')).toHaveTextContent('This image is larger than 10 MB. Choose a smaller one.');
    pick(new File(['x'], 'shot.webp', { type: 'image/webp' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Only PNG, JPEG or GIF images can be printed in a report.');
    expect(api.uploadReportTextImage).not.toHaveBeenCalled();
    expect(reportImageProblem({ type: 'image/gif', size: 10 * 1024 * 1024 })).toBeNull();
    expect(screen.getByTestId('t-system-image-file')).toHaveAttribute('accept', 'image/png,image/jpeg,image/gif');
  });

  it('shows what the server said when the upload fails', async () => {
    api.uploadReportTextImage.mockRejectedValue({ response: { status: 400, data: { detail: 'File content does not match an allowed image type.' } } });
    render(<Harness />);
    await screen.findByText('No image uploaded yet.');
    pick(png());
    expect(await screen.findByRole('alert')).toHaveTextContent('File content does not match an allowed image type.');
    expect(screen.getByRole('button', { name: 'Upload image' })).toBeEnabled();
  });
});

describe('ReportTextImages — caption and delete', () => {
  it('saves a changed caption on leaving the box or Enter, and nothing when it did not change', async () => {
    api.listReportTextImages.mockResolvedValue([image(3, { caption: 'Old' })]);
    api.setNoteAttachmentCaption.mockResolvedValue({});
    render(<Harness />);
    const box = await screen.findByLabelText('Caption for image 3 (diagram-3.png)');
    fireEvent.blur(box);
    expect(api.setNoteAttachmentCaption).not.toHaveBeenCalled();

    api.listReportTextImages.mockResolvedValue([image(3, { caption: 'The assessed network' })]);
    fireEvent.change(box, { target: { value: '  The assessed network ' } });
    fireEvent.blur(box);
    await waitFor(() => expect(api.setNoteAttachmentCaption).toHaveBeenCalledWith(1, 3, 'The assessed network'));
    // The list is read again and shows the stored caption.
    await waitFor(() => expect(screen.getByLabelText('Caption for image 3 (diagram-3.png)')).toHaveValue('The assessed network'));
    expect(api.listReportTextImages).toHaveBeenCalledTimes(2);
  });

  it('a caption the server refuses is said on the row, and what was typed stays', async () => {
    api.listReportTextImages.mockResolvedValue([image(3)]);
    api.setNoteAttachmentCaption.mockRejectedValue({ response: { status: 422, data: { detail: 'A caption is at most 2000 characters.' } } });
    render(<Harness />);
    const box = await screen.findByLabelText('Caption for image 3 (diagram-3.png)');
    fireEvent.change(box, { target: { value: 'Typed' } });
    fireEvent.blur(box);
    expect(await screen.findByRole('alert')).toHaveTextContent('A caption is at most 2000 characters.');
    expect(screen.getByLabelText('Caption for image 3 (diagram-3.png)')).toHaveValue('Typed');
  });

  it('deletes after a confirmation that names the image', async () => {
    api.listReportTextImages.mockResolvedValue([image(3, { caption: 'The assessed network' })]);
    api.deleteNoteAttachment.mockResolvedValue(undefined);
    render(<Harness />);
    const del = await screen.findByRole('button', { name: 'Delete image 3 (diagram-3.png)' });
    confirmMock.mockResolvedValueOnce(false);
    fireEvent.click(del);
    await waitFor(() => expect(confirmMock).toHaveBeenCalledWith(expect.objectContaining({
      title: 'Delete this image?', resourceName: 'The assessed network', severity: 'danger',
    })));
    expect(api.deleteNoteAttachment).not.toHaveBeenCalled();

    confirmMock.mockResolvedValueOnce(true);
    api.listReportTextImages.mockResolvedValue([]);
    fireEvent.click(del);
    await waitFor(() => expect(api.deleteNoteAttachment).toHaveBeenCalledWith(1, 3));
    expect(await screen.findByText('No image uploaded yet.')).toBeInTheDocument();
  });

  it('shows the server’s refusal — which names where the image is placed — on its row', async () => {
    const refusal = 'Cannot delete this image: the system description of the project\'s report defaults, draft report 12 '
      + 'places it. The report would be left with a reference to nothing. Remove the reference — ![…](evidence:3) — '
      + 'from that text first, then delete it.';
    api.listReportTextImages.mockResolvedValue([image(3, { placed_in: ['defaults', 'report 12'] }), image(4)]);
    api.deleteNoteAttachment.mockRejectedValue({ response: { status: 409, data: { detail: refusal } } });
    confirmMock.mockResolvedValue(true);
    render(<Harness />);
    fireEvent.click(await screen.findByRole('button', { name: 'Delete image 3 (diagram-3.png)' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(refusal);
    // On the image's own row, which stays.
    expect(alert.closest('li')).toContainElement(screen.getByLabelText('Caption for image 3 (diagram-3.png)'));
    expect(screen.getByRole('button', { name: 'Delete image 3 (diagram-3.png)' })).toBeEnabled();
  });
});

describe('ReportTextImages — who may change what', () => {
  it('a viewer or an auditor reads the list: no upload, no caption box, no delete', async () => {
    for (const projectRole of ['viewer', 'auditor']) {
      who.projectRole = projectRole;
      // `can_modify` is true for an image with no recorded uploader, whoever asks.
      api.listReportTextImages.mockResolvedValue([image(3, { caption: 'The assessed network', uploaded_by_id: null })]);
      const view = render(<Harness />);
      expect(await screen.findByText('The assessed network')).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Upload image' })).toBeNull();
      expect(screen.queryByTestId('t-system-image-file')).toBeNull();
      expect(screen.queryByRole('textbox')).toBeNull();
      expect(screen.queryByRole('button', { name: /Delete image/ })).toBeNull();
      view.unmount();
    }
  });

  it('an analyst uploads, and changes only the images the server says are theirs', async () => {
    api.listReportTextImages.mockResolvedValue([
      image(3, { caption: 'Mine' }),
      image(4, { caption: 'A colleague’s', uploaded_by_id: 8, can_modify: false }),
    ]);
    render(<Harness />);
    expect(await screen.findByLabelText('Caption for image 3 (diagram-3.png)')).toHaveValue('Mine');
    expect(screen.getByRole('button', { name: 'Upload image' })).toBeEnabled();
    expect(screen.getByText('A colleague’s')).toBeInTheDocument();
    expect(screen.queryByLabelText('Caption for image 4 (diagram-4.png)')).toBeNull();
    expect(screen.getByRole('button', { name: 'Delete image 3 (diagram-3.png)' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Delete image 4 (diagram-4.png)' })).toBeNull();
  });

  it('while the form saves, every control stays and is inert', async () => {
    api.listReportTextImages.mockResolvedValue([image(3)]);
    render(<Harness disabled />);
    expect(await screen.findByLabelText('Caption for image 3 (diagram-3.png)')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Upload image' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Delete image 3 (diagram-3.png)' })).toBeDisabled();
  });
});
