/**
 * Every object URL that is made is revoked — counted (plan B1).
 *
 * An object URL pins its bytes in the browser until it is revoked, and nothing
 * shows one that was missed.  For each place that shows authenticated bytes
 * (the finding page's image cache, a note's thumbnails, a web interface's
 * screenshot, a report template's image) this walks mount → replace → unmount
 * with `URL.createObjectURL` and `URL.revokeObjectURL` counted, and asks that
 * each URL made was revoked exactly once by the end — the ones on show, the
 * ones replaced, and the one that arrives after its reader has gone.
 *
 * (The note composer's pasted previews are counted in
 * `components/HostInspector.notes.test.tsx`, where the inspector's mocks are.)
 */
import { act, fireEvent, render, renderHook, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  getFindingImages: vi.fn(),
  getNoteAttachmentObjectUrl: vi.fn(),
  fetchWebInterfaceScreenshot: vi.fn(),
  getHostWebInterfaces: vi.fn(),
  getWebInterfaceRecord: vi.fn(),
  fetchReportTemplateAssetPreview: vi.fn(),
  uploadReportTemplateAsset: vi.fn(),
  removeReportTemplateAsset: vi.fn(),
  uploadNoteAttachment: vi.fn(),
  deleteNoteAttachment: vi.fn(),
  setNoteAttachmentInReport: vi.fn(),
  setNoteAttachmentCaption: vi.fn(),
}));
vi.mock('../services/api', () => api);
vi.mock('../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));

import NoteAttachments from '../components/host-inspector/NoteAttachments';
import TemplateImages from '../components/reports/TemplateImages';
import WebInterfacesCard from '../components/WebInterfacesCard';
import { TooltipProvider } from '../components/ui/tooltip';
import { useFindingImages } from '../hooks/useFindingImages';

/** Counts what the browser would: each URL made, each URL revoked. */
const made: string[] = [];
const revoked: string[] = [];
const outstanding = () => made.filter((url) => !revoked.includes(url));
/** Every URL made has been revoked, and none of them twice. */
const expectAllRevokedOnce = () => {
  expect(outstanding()).toEqual([]);
  expect([...revoked].sort()).toEqual([...made].sort());
};
/** What the API layer does with the bytes it fetched. */
const objectUrl = () => URL.createObjectURL(new Blob(['bytes']));

beforeEach(() => {
  vi.clearAllMocks();
  made.length = 0;
  revoked.length = 0;
  let n = 0;
  URL.createObjectURL = vi.fn(() => {
    n += 1;
    made.push(`blob:made-${n}`);
    return `blob:made-${n}`;
  });
  URL.revokeObjectURL = vi.fn((url: string) => { revoked.push(url); });
});

const image = (id: number) => ({
  id, note_id: 1, filename: `shot-${id}.png`, caption: null, content_type: 'image/png', size_bytes: 10,
  in_report: true, printable: true, placed_in: [], uploaded_by_id: 1, by_agent: false, created_at: null,
  can_edit: true,
});

describe('object URLs: made == revoked', () => {
  it('the finding page’s image cache: two images, another finding, one that arrives late, then gone', async () => {
    api.getFindingImages.mockImplementation((_projectId: number, findingId: number) => Promise.resolve({
      items: findingId === 7 ? [image(1), image(2)] : [image(3), image(4)], caption_max: 2000,
    }));
    let late!: () => void;
    api.getNoteAttachmentObjectUrl.mockImplementation((_projectId: number, id: number) => (id === 4
      ? new Promise<string>((resolve) => { late = () => resolve(objectUrl()); })
      : Promise.resolve(objectUrl())));
    const { result, rerender, unmount } = renderHook(({ id }) => useFindingImages(id), { initialProps: { id: 7 } });
    await waitFor(() => expect(result.current.images).toHaveLength(2));
    act(() => {
      result.current.resolver.ensure(1);
      result.current.thumbnails.ensure(2);
    });
    await waitFor(() => expect(Object.keys(result.current.urls)).toHaveLength(2));
    expect(made).toHaveLength(2);

    // Another finding: its images are not the first one's.
    rerender({ id: 8 });
    await waitFor(() => expect(result.current.images.map((i) => i.id)).toEqual([3, 4]));
    act(() => {
      result.current.thumbnails.ensure(3);
      result.current.thumbnails.ensure(4);
    });
    await waitFor(() => expect(result.current.urls[3]).toBeDefined());
    expect(result.current.urls[1]).toBeUndefined();
    await waitFor(() => expect(late).toBeDefined());

    unmount();
    await act(async () => { late(); await Promise.resolve(); });
    await waitFor(() => expect(made).toHaveLength(4));
    await waitFor(() => expect(outstanding()).toEqual([]));
    expectAllRevokedOnce();
  });

  it('a note’s own thumbnails: two images, one removed and one added, a failed one retried, then gone', async () => {
    const att = (id: number) => ({
      id, filename: `shot-${id}.png`, content_type: 'image/png', size_bytes: 10,
      created_at: '2026-09-01T00:00:00Z', include_in_report: false, uploaded_by_id: 1,
    });
    const refused = new Set([3]);
    api.getNoteAttachmentObjectUrl.mockImplementation((_projectId: number, id: number) => {
      if (refused.delete(id)) return Promise.reject(new Error('503'));
      return Promise.resolve(objectUrl());
    });
    const { rerender, unmount } = render(
      <NoteAttachments noteId={5} canManage={false} attachments={[att(1), att(2)]} />,
    );
    await waitFor(() => expect(screen.getByRole('button', { name: 'View shot-2.png' }).querySelector('img')).not.toBeNull());
    await waitFor(() => expect(screen.getByRole('button', { name: 'View shot-1.png' }).querySelector('img')).not.toBeNull());
    expect(made).toHaveLength(2);

    rerender(<NoteAttachments noteId={5} canManage={false} attachments={[att(2), att(3)]} />);
    fireEvent.click(await screen.findByRole('button', { name: 'shot-3.png could not be loaded — try again' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'View shot-3.png' }).querySelector('img')).not.toBeNull());
    expect(made).toHaveLength(3);

    unmount();
    expectAllRevokedOnce();
  });

  it('a web interface’s screenshot: one shown, replaced by the next, then gone', async () => {
    const row = {
      id: 7, source: 'httpx', url: 'https://app.example.com', protocol: 'https', port: 443,
      technologies: [], has_screenshot: true, scan_id: 3, port_id: 443,
    };
    api.fetchWebInterfaceScreenshot.mockImplementation(() => Promise.resolve(objectUrl()));
    const { unmount } = render(
      <MemoryRouter><TooltipProvider>
        <WebInterfacesCard hostId={1} count={1} rows={[row] as never} embedded />
      </TooltipProvider></MemoryRouter>,
    );
    const open = screen.getByRole('button', { name: `View screenshot of ${row.url}` });
    fireEvent.click(open);
    await waitFor(() => expect(document.querySelector('img[src="blob:made-1"]')).not.toBeNull());
    expect(revoked).toEqual([]);

    // The next one: the first is no longer on show, so it is released.
    fireEvent.click(open);
    await waitFor(() => expect(document.querySelector('img[src="blob:made-2"]')).not.toBeNull());
    expect(revoked).toEqual(['blob:made-1']);

    unmount();
    expectAllRevokedOnce();
    expect(made).toHaveLength(2);
  });

  it('a report template’s image: shown, replaced when the file changes, then gone', async () => {
    const template = (sha256: string) => ({
      name: 'pentest', title: 'Pentest', description: '', formats: ['html' as const],
      assets: [{
        id: 'logo', path: 'branding/logo.png', label: 'Logo', description: '', note: '', required: false,
        formats: ['html' as const], present: true, installed: false, source: 'uploaded' as const, kind: 'png',
        uploadable: true, upload: { sha256 },
      }],
    });
    api.fetchReportTemplateAssetPreview.mockImplementation(() => Promise.resolve(new Blob(['png'])));
    const show = (sha256: string) => (
      <MemoryRouter><TemplateImages template={template(sha256)} templateName="pentest" /></MemoryRouter>
    );
    const { rerender, unmount } = render(show('aaa'));
    await waitFor(() => expect(screen.getByAltText('Logo as it will be used')).toHaveAttribute('src', 'blob:made-1'));
    expect(revoked).toEqual([]);

    rerender(show('bbb'));
    await waitFor(() => expect(screen.getByAltText('Logo as it will be used')).toHaveAttribute('src', 'blob:made-2'));
    expect(revoked).toEqual(['blob:made-1']);

    unmount();
    expectAllRevokedOnce();
    expect(made).toHaveLength(2);
  });
});
