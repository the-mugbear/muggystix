/**
 * Test Plans list (v5.288.0 — screenshot review): the workflow explainer is
 * collapsed by default and remembers the viewer's choice; the list is readable
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
  generateTestPlan: vi.fn(),
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Demo' } }),
}));

import * as api from '../../services/api';
const mockedApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const KEY = 'testPlans.workflowExplainer.expanded';

const plan = (over: Record<string, unknown> = {}) => ({
  id: 7,
  project_id: 1,
  version: 1,
  title: '[named-assets seed] Named endpoint exposure across the DMZ web tier',
  description: '',
  status: 'approved',
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
    mockedApi.getTestPlans.mockReset().mockResolvedValue([plan()]);
  });

  it('keeps the workflow explainer collapsed by default and remembers opening it', async () => {
    const user = userEvent.setup();
    const { unmount } = renderPage();
    await screen.findByText(/Named endpoint exposure/);

    const trigger = screen.getByRole('button', { name: /How the test plan workflow works/ });
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    // Visiting must not write a preference the viewer never chose.
    expect(window.localStorage.getItem(KEY)).toBeNull();

    await user.click(trigger);
    expect(trigger).toHaveAttribute('aria-expanded', 'true');
    expect(window.localStorage.getItem(KEY)).toBe('true');

    unmount();
    renderPage();
    await screen.findByText(/Named endpoint exposure/);
    expect(
      screen.getByRole('button', { name: /How the test plan workflow works/ }),
    ).toHaveAttribute('aria-expanded', 'true');
  });

  it('still renders when storage throws', async () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    renderPage();
    await screen.findByText(/Named endpoint exposure/);
    expect(
      screen.getByRole('button', { name: /How the test plan workflow works/ }),
    ).toHaveAttribute('aria-expanded', 'false');
    spy.mockRestore();
  });

  it('describes the current flow: an explicit submit, approval, no automatic close', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByText(/Named endpoint exposure/);
    await user.click(screen.getByRole('button', { name: /How the test plan workflow works/ }));

    const explainer = screen.getByTestId('workflow-explainer');
    expect(explainer).not.toHaveTextContent(/submits automatically/);
    expect(explainer).toHaveTextContent(/not an automatic one/);
    expect(explainer).toHaveTextContent(/Only an approved plan can be executed/);
    expect(explainer).toHaveTextContent(/the run does not close the plan itself/);
    // Sections, not cards: no bordered box around it, no alert boxes inside.
    expect(explainer.className).not.toMatch(/rounded-panel/);
    expect(explainer.querySelector('[role="alert"]')).toBeNull();
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
    expect(within(actions).getByRole('button', { name: /Generate with AI/ })).toBeInTheDocument();
    expect(within(actions).getByRole('button', { name: /Compare/ })).toBeInTheDocument();
    // The search and the status filter are no longer in the action group.
    expect(within(actions).queryByLabelText('Search test plans')).toBeNull();
    const status = screen.getByLabelText('Filter test plans by status');
    expect(status).toHaveTextContent('All statuses');
  });
});
