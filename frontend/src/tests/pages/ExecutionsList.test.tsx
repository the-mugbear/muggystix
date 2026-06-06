/**
 * Tests for the v3 alpha.12 Executions list page.
 *
 * Cross-execution compare needs same-plan; the page disables the
 * Compare button when two selected rows belong to different plans.
 */
import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

const navigateSpy = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>(
    'react-router-dom',
  );
  return {
    ...actual,
    useNavigate: () => navigateSpy,
    useParams: () => ({}),
  };
});

vi.mock('../../services/api', () => ({
  listExecutionSessionsProjectWide: vi.fn(),
  getCurrentProjectId: vi.fn(() => 1),
  setCurrentProjectId: vi.fn(),
}));

import * as api from '../../services/api';
import ExecutionsList from '../../pages/ExecutionsList';

const mockedApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

function renderPage() {
  return render(
    <MemoryRouter>
      <ExecutionsList />
</MemoryRouter>,
  );
}

const execs = [
  {
    id: 100, test_plan_id: 7, plan_title: 'Pen-test', plan_version: 1,
    status: 'completed', started_at: '2026-05-15T10:00:00Z',
    completed_at: '2026-05-15T11:00:00Z',
    generated_by_model: 'claude-opus-4-7', generated_by_tool: 'claude-code',
    started_by_username: 'alice',
    result_count: 12, finding_count: 3,
  },
  {
    id: 101, test_plan_id: 7, plan_title: 'Pen-test', plan_version: 1,
    status: 'completed', started_at: '2026-05-15T09:00:00Z',
    completed_at: '2026-05-15T10:00:00Z',
    generated_by_model: 'gpt-5-codex', generated_by_tool: 'codex',
    started_by_username: 'bob',
    result_count: 10, finding_count: 1,
  },
  {
    id: 200, test_plan_id: 8, plan_title: 'Other plan', plan_version: 1,
    status: 'completed', started_at: '2026-05-15T08:00:00Z',
    completed_at: null,
    generated_by_model: 'claude-opus-4-7', generated_by_tool: 'claude-code',
    started_by_username: 'alice',
    result_count: 5, finding_count: 0,
  },
];

beforeEach(() => {
  vi.clearAllMocks();
  navigateSpy.mockReset();
  // v2.86.10 — wrapper returns {items, total} now.
  mockedApi.listExecutionSessionsProjectWide.mockResolvedValue({ items: execs, total: execs.length });
});

describe('ExecutionsList page', () => {
  it('renders one row per execution session', async () => {
    renderPage();
    await waitFor(() => {
      expect(within(screen.getByRole('table')).getByText('#100')).toBeInTheDocument();
      expect(within(screen.getByRole('table')).getByText('#101')).toBeInTheDocument();
      expect(within(screen.getByRole('table')).getByText('#200')).toBeInTheDocument();
    });
  });

  it('Compare button stays disabled when two selected rows are from different plans', async () => {
    renderPage();
    await waitFor(() => {
      expect(within(screen.getByRole('table')).getByText('#100')).toBeInTheDocument();
    });
    const checkboxes = within(screen.getByRole('table')).getAllByRole('checkbox');
    // Tick #100 (plan 7) and #200 (plan 8).
    // Default sort: completed sessions by started_at desc → [100, 101, 200].
    fireEvent.click(checkboxes[0]);  // #100
    fireEvent.click(checkboxes[2]);  // #200
    const compareBtn = screen.getByRole('button', { name: /Compare/ });
    expect(compareBtn).toBeDisabled();
  });

  it('Compare button enables and navigates when two from the same plan are selected', async () => {
    renderPage();
    await waitFor(() => {
      expect(within(screen.getByRole('table')).getByText('#100')).toBeInTheDocument();
    });
    const checkboxes = within(screen.getByRole('table')).getAllByRole('checkbox');
    fireEvent.click(checkboxes[0]);  // #100, plan 7
    fireEvent.click(checkboxes[1]);  // #101, plan 7
    const compareBtn = screen.getByRole('button', { name: /Compare selected/ });
    await waitFor(() => expect(compareBtn).toBeEnabled());
    fireEvent.click(compareBtn);
    expect(navigateSpy).toHaveBeenCalledWith('/test-plans/7/compare?a=100&b=101');
  });

  it('clicking a row opens the execution detail page', async () => {
    renderPage();
    await waitFor(() => {
      expect(within(screen.getByRole('table')).getByText('#100')).toBeInTheDocument();
    });
    // Same NavigableTableRow / <Link> pattern as ReconRunsList — assert
    // on the link's href instead of relying on the useNavigate spy.
    const link = screen.getByRole('link', { name: /Open execution 100|open execution session 100|execution #100/i });
    expect(link).toHaveAttribute('href', '/executions/100');
  });
});
