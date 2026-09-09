/**
 * UX review C1/C3 — the note composer's draft is bound to the host it was
 * written for, and a failed screenshot upload is kept for retry against the
 * note that was created rather than silently dropped.
 */
import React from 'react';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});

const hostFixture = (id: number) => ({
  id, ip_address: `10.0.0.${id}`, hostname: `h${id}`, state: 'up',
  ports: [], assignees: [], tags: [], vulnerabilities: [], notes: [],
  discoveries: [], follow: null,
  os_name: null, os_family: null, os_type: null, os_generation: null,
  os_vendor: null, os_accuracy: null, smb_signing: null,
  web_interface_count: 0, netexec_result_count: 0, dns_record_count: 0,
  first_seen: '2026-06-14T00:00:00Z', last_seen: '2026-06-14T00:00:00Z',
});

const api = vi.hoisted(() => ({
  createAnnotation: vi.fn(),
  uploadNoteAttachment: vi.fn(),
}));

vi.mock('../../services/api', () => ({
  getHost: vi.fn().mockImplementation((id: number) => Promise.resolve(hostFixture(id))),
  getHostConflicts: vi.fn().mockResolvedValue([]),
  getHostTestPlanEntries: vi.fn().mockResolvedValue([]),
  getHostFollowers: vi.fn().mockResolvedValue([]),
  recordHostView: vi.fn().mockResolvedValue(undefined),
  listProjectMembers: vi.fn().mockResolvedValue([]),
  followHost: vi.fn(), unfollowHost: vi.fn(), assignHost: vi.fn(), unassignHost: vi.fn(),
  createNote: vi.fn(), updateAnnotation: vi.fn(), deleteAnnotation: vi.fn(),
  createAnnotation: api.createAnnotation,
  uploadNoteAttachment: api.uploadNoteAttachment,
  promoteAnnotation: vi.fn(),
  promoteVulnerability: vi.fn(), previewPromoteVulnerability: vi.fn(),
  updateTestPlanEntry: vi.fn(), getHostNotes: vi.fn().mockResolvedValue([]),
}));

vi.mock('../../components/WebInterfacesCard', () => ({ default: () => null }));
vi.mock('../../components/NseScriptsCard', () => ({ default: () => null }));
vi.mock('../../components/NetExecCard', () => ({ default: () => null }));
vi.mock('../../components/HostFindingsCard', () => ({ default: () => null }));
vi.mock('../../components/HostDnsRecordsCard', () => ({ default: () => null }));
vi.mock('../../components/HostNamesCard', () => ({ default: () => null }));
vi.mock('../../components/HostLineagePanel', () => ({ default: () => null }));
vi.mock('../../components/host-inspector/PortDetailsCard', () => ({ default: () => null }));

import HostInspector from '../../components/HostInspector';

const createObjectURL = vi.fn();
const revokeObjectURL = vi.fn();

const pasteImage = (textarea: HTMLElement, file: File) => {
  fireEvent.paste(textarea, {
    clipboardData: {
      items: [{ kind: 'file', type: file.type, getAsFile: () => file }],
      getData: () => '',
    },
  });
};

describe('HostInspector note composer — draft bound to host, recoverable attachments', () => {
  beforeEach(() => {
    api.createAnnotation.mockReset();
    api.uploadNoteAttachment.mockReset();
    createObjectURL.mockReset();
    revokeObjectURL.mockReset();
    let n = 0;
    createObjectURL.mockImplementation(() => `blob:img-${++n}`);
    Object.defineProperty(globalThis.URL, 'createObjectURL', { value: createObjectURL, configurable: true });
    Object.defineProperty(globalThis.URL, 'revokeObjectURL', { value: revokeObjectURL, configurable: true });
  });

  it('C1: switching host clears pending screenshots and revokes their previews', async () => {
    const onDirtyChange = vi.fn();
    const { rerender } = render(
      <MemoryRouter><HostInspector hostId={1} onDirtyChange={onDirtyChange} /></MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByText('10.0.0.1')).toBeInTheDocument());

    const file = new File(['png'], 'shot.png', { type: 'image/png' });
    pasteImage(screen.getByLabelText('Note'), file);
    expect(await screen.findByAltText('Pasted image 1')).toBeInTheDocument();
    expect(onDirtyChange).toHaveBeenLastCalledWith(true);

    rerender(<MemoryRouter><HostInspector hostId={2} onDirtyChange={onDirtyChange} /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('10.0.0.2')).toBeInTheDocument());

    expect(screen.queryByAltText('Pasted image 1')).not.toBeInTheDocument();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:img-1');
    expect(onDirtyChange).toHaveBeenLastCalledWith(false);
  });

  it('C3: a failed attachment is kept with Retry, the note is created once, retry targets that note', async () => {
    api.createAnnotation.mockResolvedValue({ id: 77, body: 'evidence', status: 'open', attachments: [] });
    api.uploadNoteAttachment
      .mockRejectedValueOnce(new Error('boom'))
      .mockResolvedValueOnce({ id: 9, filename: 'shot.png', content_type: 'image/png', size: 3, url: '/a/9' });

    render(<MemoryRouter><HostInspector hostId={1} /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('10.0.0.1')).toBeInTheDocument());

    const file = new File(['png'], 'shot.png', { type: 'image/png' });
    const textarea = screen.getByLabelText('Note');
    pasteImage(textarea, file);
    await screen.findByAltText('Pasted image 1');
    fireEvent.change(textarea, { target: { value: 'evidence' } });
    fireEvent.click(screen.getByRole('button', { name: /save note/i }));

    // The note exists; the file did not upload — say so and keep it.
    expect(await screen.findByText(/Note saved · 1 attachment failed/)).toBeInTheDocument();
    expect(api.createAnnotation).toHaveBeenCalledTimes(1);
    expect(screen.getByAltText('Pasted image 1')).toBeInTheDocument();
    expect(revokeObjectURL).not.toHaveBeenCalledWith('blob:img-1');

    fireEvent.click(screen.getByRole('button', { name: /retry uploading pasted image 1/i }));
    await waitFor(() => expect(api.uploadNoteAttachment).toHaveBeenCalledTimes(2));
    // Same host, same note — never a second note.
    expect(api.uploadNoteAttachment).toHaveBeenLastCalledWith(1, 77, file);
    expect(api.createAnnotation).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByText(/attachment failed/)).not.toBeInTheDocument());
    expect(screen.queryByAltText('Pasted image 1')).not.toBeInTheDocument();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:img-1');
  });

  it('C3 regression: a retry targets the note the file failed on, not a later note', async () => {
    // Note 77 fails its attachment; note 88 (a second save) fails its own.
    api.createAnnotation
      .mockResolvedValueOnce({ id: 77, body: 'first', status: 'open', attachments: [] })
      .mockResolvedValueOnce({ id: 88, body: 'second', status: 'open', attachments: [] });
    api.uploadNoteAttachment
      .mockRejectedValueOnce(new Error('boom-77'))
      .mockRejectedValueOnce(new Error('boom-88'))
      .mockResolvedValueOnce({ id: 9, filename: 'a.png', content_type: 'image/png', size: 3, url: '/a/9' });

    render(<MemoryRouter><HostInspector hostId={1} /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText('10.0.0.1')).toBeInTheDocument());
    const textarea = screen.getByLabelText('Note');

    const fileA = new File(['a'], 'a.png', { type: 'image/png' });
    pasteImage(textarea, fileA);
    await screen.findByAltText('Pasted image 1');
    fireEvent.change(textarea, { target: { value: 'first' } });
    fireEvent.click(screen.getByRole('button', { name: /save note/i }));
    await screen.findByText(/Note saved · 1 attachment failed/);

    const fileB = new File(['b'], 'b.png', { type: 'image/png' });
    pasteImage(textarea, fileB);
    await screen.findByAltText('Pasted image 2');
    fireEvent.change(textarea, { target: { value: 'second' } });
    fireEvent.click(screen.getByRole('button', { name: /save note/i }));
    await screen.findByText(/Note saved · 2 attachments failed/);

    // Retry the FIRST file: it must go to note 77 even though 88 failed later.
    fireEvent.click(screen.getByRole('button', { name: /retry uploading pasted image 1/i }));
    await waitFor(() => expect(api.uploadNoteAttachment).toHaveBeenCalledTimes(3));
    expect(api.uploadNoteAttachment).toHaveBeenLastCalledWith(1, 77, fileA);
    expect(api.createAnnotation).toHaveBeenCalledTimes(2);
  });
});
