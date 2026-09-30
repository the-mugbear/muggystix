/**
 * Test Plans list (v5.288.0 — screenshot review): the list is readable
 * (titles and authors wrap, author by full name) and progress says what it
 * counts ("1 of 4 entries done") instead of a bare "0%".
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import TestPlans from '../../pages/TestPlans';

vi.mock('../../services/api', () => ({
  getTestPlans: vi.fn(),
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
const project = vi.hoisted(() => ({ my_role: 'analyst' as string | null }));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Demo', my_role: project.my_role } }),
}));

import * as api from '../../services/api';
const mockedApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const plan = (over: Record<string, unknown> = {}) => ({
  id: 7,
  project_id: 1,
  version: 1,
  title: '[named-assets seed] Named endpoint exposure across the DMZ web tier',
  description: '',
  status: 'in_progress',
  agent_name: '[named-assets seed] agent',
  created_by_username: 'admin',
  created_by_full_name: 'Ada Administrator',
  entry_count: 4,
  entries_done: 1,
  completion_pct: 25,
  created_at: '2026-09-20T10:00:00Z',
  updated_at: '2026-09-20T10:00:00Z',
  ...over,
});

const renderPage = () =>
  render(
    <MemoryRouter>
      <TestPlans />
    </MemoryRouter>,
  );

describe('TestPlans', () => {
  beforeEach(() => {
    window.localStorage.clear();
    project.my_role = 'analyst';
    mockedApi.getTestPlans.mockReset().mockResolvedValue([plan()]);
  });

  // N3 — `POST /assist/start` needs project auditor, so a viewer is not
  // offered a dialog whose start the server refuses.
  it('offers the agent to an auditor but not to a project viewer', async () => {
    project.my_role = 'auditor';
    const { unmount } = renderPage();
    await screen.findByText(/Named endpoint exposure/);
    expect(screen.getByRole('button', { name: /Draft with your agent/ })).toBeInTheDocument();
    unmount();

    project.my_role = 'viewer';
    renderPage();
    await screen.findByText(/Named endpoint exposure/);
    expect(screen.queryByRole('button', { name: /Draft with your agent/ })).toBeNull();
  });

  // 5.313.0 — no approval step: a plan is a record you or your agent write
  // and work. The page says so, offers the agent session, and filters only
  // by the statuses a plan can have.
  it('reads as a plan you or your agent write and work — nothing about approval', async () => {
    renderPage();
    await screen.findByText(/Named endpoint exposure/);
    expect(screen.getByText(/What you or your agent intend to test and what came of it/)).toBeInTheDocument();
    expect(screen.queryByText(/approv/i)).toBeNull();
    expect(screen.queryByRole('button', { name: /How the test plan workflow works/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Generate with AI/ })).toBeNull();
    expect(screen.getByText('In Progress')).toBeInTheDocument();
  });

  it('offers the status filter only for the statuses a plan can have', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText(/Named endpoint exposure/);
    await user.click(screen.getByLabelText('Filter test plans by status'));
    const options = screen.getAllByRole('option').map((o) => o.textContent);
    expect(options).toEqual(['All statuses', 'Draft', 'In Progress', 'Completed', 'Archived']);
  });

  it('shows progress as a fraction, and the author by full name', async () => {
    mockedApi.getTestPlans.mockResolvedValue([
      plan(),
      plan({ id: 8, title: 'Empty draft', status: 'draft', entry_count: 0, entries_done: 0, completion_pct: 0 }),
      plan({ id: 9, title: 'Untouched', entry_count: 4, entries_done: 0, completion_pct: 0 }),
    ]);
    renderPage();
    await screen.findByText('Empty draft');

    expect(screen.getByText('1 of 4 entries done')).toBeInTheDocument();
    expect(screen.getByText('0 of 4 entries done')).toBeInTheDocument();
    expect(screen.getByText('No entries yet')).toBeInTheDocument();
    expect(screen.queryByText('0%')).not.toBeInTheDocument();

    expect(screen.getAllByText('Ada Administrator').length).toBeGreaterThan(0);
    expect(screen.queryByText(/via admin/)).not.toBeInTheDocument();
  });

  it('lets titles and authors wrap in a fixed-layout table with a short search placeholder', async () => {
    renderPage();
    const title = await screen.findByText(/Named endpoint exposure/);
    expect(title).toHaveClass('line-clamp-2');
    expect(title).not.toHaveClass('truncate');
    expect(screen.getByText('Ada Administrator')).toHaveClass('break-words');
    // The Table primitive is table-fixed by default (style guide §8).
    expect(screen.getByRole('table')).toHaveClass('table-fixed');
    expect(screen.getByLabelText('Search test plans')).toHaveAttribute(
      'placeholder',
      'Search title or author',
    );
    await waitFor(() => expect(mockedApi.getTestPlans).toHaveBeenCalled());
  });

  it('fits the table to the content width (B2)', async () => {
    // A 960px minimum inside a scroller: 34px of sideways scroll at a 1246px
    // viewport, the Created column behind it.
    renderPage();
    await screen.findByText(/Named endpoint exposure/);
    const table = screen.getByTestId('plans-table');
    expect(table.className).not.toMatch(/min-w-/);
    expect(table.closest('.overflow-x-auto')).toBeNull();
  });

  it('puts the page actions top-right and the filters on the shared filter row', async () => {
    renderPage();
    await screen.findByText(/Named endpoint exposure/);
    const actions = screen.getByTestId('page-actions');
    // 5.313.0 — the agent session, handed the task, replaced Generate with AI.
    expect(within(actions).getByRole('button', { name: /Draft with your agent/ })).toBeInTheDocument();
    expect(within(actions).getByRole('button', { name: /Compare/ })).toBeInTheDocument();
    // The search and the status filter are no longer in the action group.
    expect(within(actions).queryByLabelText('Search test plans')).toBeNull();
    const status = screen.getByLabelText('Filter test plans by status');
    expect(status).toHaveTextContent('All statuses');
  });
});
