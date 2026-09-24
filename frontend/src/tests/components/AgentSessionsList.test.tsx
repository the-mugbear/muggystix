/**
 * The agent-session list — the "Sessions" view of Agent Runs since v5.294.0
 * (it was the Agent Sessions page; these pins moved with it).
 *
 * What makes it worth opening: it distinguishes a session that did work from
 * one that was never used, states the authority each acts with, and pages
 * rather than silently showing the newest 100.
 */
import type React from 'react';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import AgentSessionsList from '../../components/agent-sessions/AgentSessionsList';
import { TooltipProvider } from '../../components/ui/tooltip';
import type { AssistSessionRow } from '../../services/api';

const listAssistSessions = vi.fn();
vi.mock('../../services/api', () => ({
  listAssistSessions: (...a: unknown[]) => listAssistSessions(...a),
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

const renderList = (props: React.ComponentProps<typeof AgentSessionsList> = {}) =>
  render(
    <MemoryRouter>
      <TooltipProvider>
        <AgentSessionsList {...props} />
      </TooltipProvider>
    </MemoryRouter>,
  );

describe('AgentSessionsList', () => {
  beforeEach(() => {
    listAssistSessions.mockReset().mockResolvedValue([row()]);
  });

  it('separates a session that did work from one that was never used', async () => {
    listAssistSessions.mockResolvedValue([
      row(),
      row({ id: 13, purpose: 'Abandoned', call_count: 0, note_count: 0 }),
    ]);
    renderList();

    await waitFor(() => expect(screen.getByText('Looking for FTP exposure')).toBeInTheDocument());
    expect(screen.getByText('not used')).toBeInTheDocument();
    expect(screen.getByText(/14 · 2/)).toBeInTheDocument();
  });

  it('states the authority as the operator’s project role, not their name', async () => {
    listAssistSessions.mockResolvedValue([
      row({ started_by_username: 'alice', operator_role: 'analyst' }),
      row({ id: 13, started_by_username: 'bob', operator_role: 'global_admin' }),
      row({ id: 14, started_by_username: 'carol', operator_role: null }),
    ]);
    renderList();

    expect(await screen.findByText('Analyst role')).toBeInTheDocument();
    expect(screen.getByText('Global admin')).toBeInTheDocument();
    expect(screen.getByText('No project role')).toBeInTheDocument();
    expect(screen.queryByText(/^as /)).not.toBeInTheDocument();
  });

  it('keeps the authority chip inside its cell (B6)', async () => {
    // "NO PROJECT ROLE" ran on into Started by: the chip was nowrap with
    // nothing bounding it. It now fits its cell and truncates.
    listAssistSessions.mockResolvedValue([row({ operator_role: null })]);
    renderList();

    const text = await screen.findByText('No project role');
    expect(text).toHaveClass('truncate');
    const chip = text.parentElement!;
    expect(chip).toHaveClass('max-w-full');
    expect(chip).toHaveClass('overflow-hidden');
    expect(chip.closest('td')).toHaveClass('overflow-hidden');
  });

  it('fits the content width instead of setting a minimum and scrolling', async () => {
    renderList();
    const table = await screen.findByTestId('sessions-table');
    expect(table.className).not.toMatch(/min-w-/);
    expect(table.closest('.overflow-x-auto')).toBeNull();
  });

  it('pages rather than silently showing the newest 100', async () => {
    const full = Array.from({ length: 100 }, (_, i) => row({ id: 1000 + i }));
    listAssistSessions.mockResolvedValueOnce(full);
    renderList();
    await waitFor(() => expect(screen.getByText(/Showing the 100 most recent/)).toBeInTheDocument());

    listAssistSessions.mockResolvedValueOnce([row({ id: 2000, purpose: 'Older one' })]);
    await act(async () => {
      await userEvent.click(screen.getByRole('button', { name: /load more/i }));
    });

    expect(screen.getByText('Older one')).toBeInTheDocument();
    expect(listAssistSessions).toHaveBeenLastCalledWith(
      expect.objectContaining({ limit: 100, offset: 100 }),
    );
    expect(screen.getByText(/101 sessions — all of them/)).toBeInTheDocument();
  });

  it('explains the empty state instead of showing a bare table', async () => {
    listAssistSessions.mockResolvedValue([]);
    renderList();

    expect(await screen.findByText('No agent sessions yet')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Start Agent Session' })).toHaveAttribute(
      'href',
      '/operations?start=agent-session',
    );
  });

  it('links to where a session starts', async () => {
    renderList();
    expect(
      await screen.findByRole('link', { name: 'Start a session on Operations' }),
    ).toHaveAttribute('href', '/operations?start=agent-session');
  });

  it('shows who started a session by full name, falling back to the username', async () => {
    listAssistSessions.mockResolvedValue([
      row({ started_by_username: 'alice', started_by_full_name: 'Alice Liddell' }),
      row({ id: 13, started_by_username: 'bob', started_by_full_name: null }),
      row({ id: 14, started_by_username: 'carol', started_by_full_name: '  ' }),
    ]);
    renderList();

    await waitFor(() => expect(screen.getByText('Alice Liddell')).toBeInTheDocument());
    expect(screen.queryByText('alice')).not.toBeInTheDocument();
    expect(screen.getByText('bob')).toBeInTheDocument();
    expect(screen.getByText('carol')).toBeInTheDocument();
  });

  it('renders a missing purpose as a quiet fallback, not as content', async () => {
    listAssistSessions.mockResolvedValue([row({ purpose: null })]);
    renderList();

    const fallback = await screen.findByText('No stated purpose');
    expect(fallback).toHaveClass('text-muted-foreground');
    const cell = fallback.closest('td')!;
    expect(cell.className).not.toMatch(/px-sm|px-md/);
    expect(fallback.closest('a')!.className).toMatch(/\bpx-sm\b/);
  });

  it('draws the host page’s actions per row and reloads on its refresh', async () => {
    const { rerender } = renderList({ renderActions: (r) => <button type="button">End {r.id}</button> });
    expect(await screen.findByRole('button', { name: 'End 12' })).toBeInTheDocument();
    expect(listAssistSessions).toHaveBeenCalledTimes(1);

    rerender(
      <MemoryRouter>
        <TooltipProvider>
          <AgentSessionsList refreshNonce={1} renderActions={(r) => <button type="button">End {r.id}</button>} />
        </TooltipProvider>
      </MemoryRouter>,
    );
    await waitFor(() => expect(listAssistSessions).toHaveBeenCalledTimes(2));
  });

  it('surfaces a failed load rather than an empty list', async () => {
    listAssistSessions.mockRejectedValue(new Error('boom'));
    renderList();

    await waitFor(() =>
      expect(screen.getByText(/Could not load agent sessions/)).toBeInTheDocument(),
    );
    expect(screen.queryByText('No agent sessions yet')).not.toBeInTheDocument();
  });
});
