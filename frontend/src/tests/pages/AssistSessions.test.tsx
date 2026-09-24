/**
 * The AI Assist review page.
 *
 * Assist was the one workflow with no way to look back at it: recon runs and
 * test plans each have a list and a detail view, assist had a start dialog that
 * showed live sessions and nothing else. Every call was already audited;
 * nothing read them back.
 *
 * These pin what makes the page worth opening — the list distinguishes a
 * session that did work from one that was never used, and the detail leads with
 * the notes the agent wrote, since those are its only durable output and the
 * thing that carries the operator's name.
 */
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import AssistSessions from '../../pages/AssistSessions';
import { TooltipProvider } from '../../components/ui/tooltip';
import type { AssistSessionDetail, AssistSessionRow } from '../../services/api';

// setupTests.ts mocks react-router-dom's useParams to a fixed `{ id: '1' }`
// for every suite, so a page keyed on its own param reads as "no param
// supplied" no matter what route is rendered. Override it here with something
// each test controls — otherwise the detail view is untestable and would look
// like it silently falls through to the list.
const params = vi.hoisted(() => ({ current: {} as Record<string, string | undefined> }));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => vi.fn(),
    useParams: () => params.current,
    useLocation: () => ({ pathname: '/', search: '', hash: '', state: null }),
  };
});

const listAssistSessions = vi.fn();
const getAssistSession = vi.fn();
const getAssistSessionApiActivity = vi.fn();
vi.mock('../../services/api', () => ({
  listAssistSessions: (...a: unknown[]) => listAssistSessions(...a),
  getAssistSession: (...a: unknown[]) => getAssistSession(...a),
  getAssistSessionApiActivity: (...a: unknown[]) => getAssistSessionApiActivity(...a),
  getPlanApiActivity: vi.fn(),
  getReconSessionApiActivity: vi.fn(),
}));

const row = (over: Partial<AssistSessionRow> = {}): AssistSessionRow => ({
  id: 12,
  project_id: 1,
  purpose: 'Looking for FTP exposure',
  status: 'ended',
  started_by_id: 7,
  started_by_username: 'alice',
  started_at: '2026-08-19T10:00:00Z',
  ended_at: '2026-08-19T11:30:00Z',
  last_activity_at: '2026-08-19T11:20:00Z',
  environment_probed: true,
  key_expires_at: null,
  call_count: 14,
  note_count: 2,
  connection: 'mcp',
  first_call_at: '2026-08-19T10:02:00Z',
  ...over,
});

const detail = (over: Partial<AssistSessionDetail> = {}): AssistSessionDetail => ({
  ...row(),
  environment: { os_family: 'linux', shell: 'bash' },
  environment_probed_at: '2026-08-19T10:01:00Z',
  agent_model: 'claude-opus-5',
  agent_tool: 'claude-code',
  prompt_version: '1.51.0',
  feedback_count: 1,
  notes: [
    {
      id: 501,
      host_id: 88,
      host_ip: '10.0.0.9',
      hostname: 'ftp01',
      body: 'Anonymous FTP login accepted on 21.',
      status: 'open',
      created_at: '2026-08-19T11:00:00Z',
    },
  ],
  ...over,
});

/** `sessionId` undefined renders the list; set, the detail — the same split
 *  the two routes produce in the app. */
const renderPage = (sessionId?: string) => {
  params.current = sessionId ? { sessionId } : {};
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <AssistSessions />
      </TooltipProvider>
    </MemoryRouter>,
  );
};

describe('AssistSessions', () => {
  beforeEach(() => {
    listAssistSessions.mockReset().mockResolvedValue([row()]);
    getAssistSession.mockReset().mockResolvedValue(detail());
    getAssistSessionApiActivity
      .mockReset()
      .mockResolvedValue({ total: 0, items: [] });
  });

  it('separates a session that did work from one that was never used', async () => {
    listAssistSessions.mockResolvedValue([
      row(),
      row({ id: 13, purpose: 'Abandoned', call_count: 0, note_count: 0 }),
    ]);
    renderPage();

    await waitFor(() => expect(screen.getByText('Looking for FTP exposure')).toBeInTheDocument());
    // "Key minted, prompt never pasted" is the common dead end; without saying
    // so, a reviewer opens it to find out.
    expect(screen.getByText('not used')).toBeInTheDocument();
    expect(screen.getByText(/14 · 2/)).toBeInTheDocument();
  });

  it('states the authority as the operator’s project role, not their name', async () => {
    // v5.288.0 — the column read "as <operator>", repeating Started by and
    // wrapping on a long name. Authority is the operator's PROJECT ROLE — what
    // the agent gate checks on every call.
    listAssistSessions.mockResolvedValue([
      row({ started_by_username: 'alice', operator_role: 'analyst' }),
      row({ id: 13, started_by_username: 'bob', operator_role: 'global_admin' }),
      row({ id: 14, started_by_username: 'carol', operator_role: null }),
    ]);
    renderPage();

    const analyst = await screen.findByText('Analyst role');
    expect(analyst).toHaveClass('whitespace-nowrap');
    expect(screen.getByText('Global admin')).toBeInTheDocument();
    expect(screen.getByText('No project role')).toBeInTheDocument();
    expect(screen.queryByText(/^as /)).not.toBeInTheDocument();
  });

  it('says what it is for and links to Agent Runs', async () => {
    renderPage();
    expect(await screen.findByRole('link', { name: 'Agent Runs' })).toHaveAttribute(
      'href',
      '/agent-activity',
    );
  });

  it('leads the detail with the notes the agent wrote', async () => {
    renderPage('12');

    await waitFor(() =>
      expect(screen.getByText('Anonymous FTP login accepted on 21.')).toBeInTheDocument(),
    );
    // Resolved to a host the reviewer can open, not a bare id.
    const link = screen.getByRole('link', { name: 'ftp01' });
    expect(link).toHaveAttribute('href', '/hosts/88#note-501');
    // Provenance the audit answer needs: which machine, which agent.
    expect(screen.getByText(/linux · bash/)).toBeInTheDocument();
    expect(screen.getByText(/claude-opus-5 · claude-code/)).toBeInTheDocument();
  });

  it('pages rather than silently showing the newest 100', async () => {
    // A full page means there may be more. Showing it with no marker reads as
    // "these are all your sessions", and someone hunting an older one concludes
    // it isn't there.
    const full = Array.from({ length: 100 }, (_, i) => row({ id: 1000 + i }));
    listAssistSessions.mockResolvedValueOnce(full);
    renderPage();
    await waitFor(() => expect(screen.getByText(/Showing the 100 most recent/)).toBeInTheDocument());

    listAssistSessions.mockResolvedValueOnce([row({ id: 2000, purpose: 'Older one' })]);
    await act(async () => {
      await userEvent.click(screen.getByRole('button', { name: /load more/i }));
    });

    // Appended, not replaced — and the second call asked for the next offset.
    expect(screen.getByText('Older one')).toBeInTheDocument();
    expect(listAssistSessions).toHaveBeenLastCalledWith(
      expect.objectContaining({ limit: 100, offset: 100 }),
    );
    // A short page ends the list, and the page says so.
    expect(screen.getByText(/101 sessions — all of them/)).toBeInTheDocument();
  });

  it('says plainly when a session produced nothing', async () => {
    getAssistSession.mockResolvedValue(detail({ notes: [], note_count: 0 }));
    renderPage('12');

    expect(await screen.findByText('This session wrote no notes.')).toBeInTheDocument();
  });

  it('explains the empty state instead of showing a bare table', async () => {
    listAssistSessions.mockResolvedValue([]);
    renderPage();

    expect(await screen.findByText('No agent sessions yet')).toBeInTheDocument();
    // The recovery action goes where a session is actually started.
    expect(screen.getByRole('link', { name: 'Start Agent Session' })).toHaveAttribute(
      'href',
      '/operations?start=agent-session',
    );
  });

  it('is titled as the nav names it and links to where a session starts', async () => {
    renderPage();
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Agent Sessions' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'Start a session on Operations' }),
    ).toHaveAttribute('href', '/operations?start=agent-session');
  });

  it('shows who started a session by full name, falling back to the username', async () => {
    listAssistSessions.mockResolvedValue([
      row({ started_by_username: 'alice', started_by_full_name: 'Alice Liddell' }),
      row({ id: 13, started_by_username: 'bob', started_by_full_name: null }),
      row({ id: 14, started_by_username: 'carol', started_by_full_name: '  ' }),
    ]);
    renderPage();

    await waitFor(() => expect(screen.getByText('Alice Liddell')).toBeInTheDocument());
    expect(screen.queryByText('alice')).not.toBeInTheDocument();
    expect(screen.getByText('bob')).toBeInTheDocument();
    expect(screen.getByText('carol')).toBeInTheDocument();
  });

  it('renders a missing purpose as a quiet fallback, not as content', async () => {
    listAssistSessions.mockResolvedValue([row({ purpose: null })]);
    renderPage();

    const fallback = await screen.findByText('No stated purpose');
    expect(fallback).toHaveClass('text-muted-foreground');
    // The linked cell adds no padding of its own on top of the link's, so its
    // content lines up with the column header (both px-sm).
    const cell = fallback.closest('td')!;
    expect(cell.className).not.toMatch(/px-sm|px-md/);
    expect(fallback.closest('a')!.className).toMatch(/\bpx-sm\b/);
  });

  it('surfaces a failed load rather than an empty list', async () => {
    listAssistSessions.mockRejectedValue(new Error('boom'));
    renderPage();

    await waitFor(() =>
      expect(screen.getByText(/Could not load agent sessions/)).toBeInTheDocument(),
    );
  });
});
