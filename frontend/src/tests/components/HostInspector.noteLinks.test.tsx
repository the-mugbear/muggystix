/**
 * Code review 2026-09-19, finding 19 — `#note-<id>` links must REVEAL the note.
 *
 * The density pass (v5.241.0) mounts only a preview of the thread and unmounts
 * a collapsed section's children; the deep-link effect only looked for an
 * element that was already there. Links from My work, the activity feed and a
 * finding's evidence then landed on a page that did not show what they named.
 */
import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn() };
});

const root = (id: number, over: Record<string, unknown> = {}) => ({
  id, body: `root ${id}`, status: 'open', parent_id: null, pinned: false, author_name: 'Ada',
  created_at: `2026-09-${String(id).padStart(2, '0')}T00:00:00Z`, updated_at: null, attachments: [],
  ...over,
});
// Five roots (1 oldest … 5 newest); note 60 is a reply under the OLDEST root.
const NOTES = [5, 4, 3, 2, 1].map((id) => root(id)).concat([root(60, { parent_id: 1, body: 'reply under the oldest' })]);

vi.mock('../../services/api', () => ({
  getHost: vi.fn().mockImplementation((id: number) => Promise.resolve({
    id, ip_address: `10.0.0.${id}`, hostname: `h${id}`, state: 'up',
    ports: [], assignees: [], tags: [], vulnerabilities: [], notes: NOTES,
    discoveries: [], follow: null,
    os_name: null, os_family: null, os_type: null, os_generation: null,
    os_vendor: null, os_accuracy: null, smb_signing: null,
    web_interface_count: 0, netexec_result_count: 0, dns_record_count: 0,
    first_seen: '2026-06-14T00:00:00Z', last_seen: '2026-06-14T00:00:00Z',
  })),
  getHostConflicts: vi.fn().mockResolvedValue([]),
  getHostTestPlanEntries: vi.fn().mockResolvedValue([]),
  getHostFollowers: vi.fn().mockResolvedValue([]),
  recordHostView: vi.fn().mockResolvedValue(undefined),
  listProjectMembers: vi.fn().mockResolvedValue([]),
  followHost: vi.fn(), unfollowHost: vi.fn(), assignHost: vi.fn(), unassignHost: vi.fn(),
  createNote: vi.fn(), updateAnnotation: vi.fn(), deleteAnnotation: vi.fn(),
  createAnnotation: vi.fn(), uploadNoteAttachment: vi.fn(), deleteNoteAttachment: vi.fn(),
  getNoteAttachmentObjectUrl: vi.fn(), promoteAnnotation: vi.fn(),
  promoteVulnerability: vi.fn(), previewPromoteVulnerability: vi.fn(),
  updateTestPlanEntry: vi.fn(), getHostNotes: vi.fn().mockResolvedValue([]),
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

vi.mock('../../components/WebInterfacesCard', () => ({ default: () => null }));
vi.mock('../../components/NseScriptsCard', () => ({ default: () => null }));
vi.mock('../../components/NetExecCard', () => ({ default: () => null }));
vi.mock('../../components/HostFindingsCard', () => ({ default: () => null }));
vi.mock('../../components/HostNamesCard', () => ({ default: () => null }));
vi.mock('../../components/HostLineagePanel', () => ({ default: () => null }));
vi.mock('../../components/host-inspector/PortDetailsCard', () => ({ default: () => null }));

import HostInspector from '../../components/HostInspector';
import { TooltipProvider } from '../../components/ui/tooltip';

const scrolled: string[] = [];
const renderInspector = () =>
  render(<MemoryRouter><TooltipProvider><HostInspector hostId={1} /></TooltipProvider></MemoryRouter>);

beforeEach(() => {
  scrolled.length = 0;
  window.localStorage.clear();
  URL.revokeObjectURL = vi.fn();
  Element.prototype.scrollIntoView = function scrollIntoView(this: Element) { scrolled.push(this.id); };
});
afterEach(() => { window.location.hash = ''; });

describe('HostInspector — #note- links reveal what they name', () => {
  it('without a link, only the preview is mounted (the premise of the bug)', async () => {
    renderInspector();
    expect(await screen.findByText('root 5')).toBeInTheDocument();
    expect(screen.queryByText('root 1')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Show 2 earlier threads/ })).toBeInTheDocument();
  });

  it('a link to the OLDEST root mounts it and scrolls to it', async () => {
    window.location.hash = '#note-1';
    renderInspector();
    expect(await screen.findByText('root 1')).toBeInTheDocument();
    await waitFor(() => expect(scrolled).toContain('note-1'));
  });

  it('a link to a REPLY reveals the thread it sits in', async () => {
    window.location.hash = '#note-60';
    renderInspector();
    expect(await screen.findByText('reply under the oldest')).toBeInTheDocument();
    expect(screen.getByText('root 1')).toBeInTheDocument();
    await waitFor(() => expect(scrolled).toContain('note-60'));
  });

  it('a link re-opens a Notes section the viewer had collapsed', async () => {
    window.localStorage.setItem('bluestick.inspector.collapsed', JSON.stringify(['host-detail-notes']));
    window.location.hash = '#note-5';
    renderInspector();
    expect(await screen.findByText('root 5')).toBeInTheDocument();
    await waitFor(() => expect(scrolled).toContain('note-5'));
  });

  it('a hash naming a note this host does not have changes nothing', async () => {
    window.location.hash = '#note-999';
    renderInspector();
    expect(await screen.findByText('root 5')).toBeInTheDocument();
    expect(screen.queryByText('root 1')).not.toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 50));
    expect(scrolled).toEqual([]);
  });
});
