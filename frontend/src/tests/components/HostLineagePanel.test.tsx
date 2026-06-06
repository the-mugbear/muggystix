/**
 * Tests for the v3 alpha.9 HostLineagePanel component.
 *
 * Pins the three-section contract (recons / plans / executions),
 * cross-page navigation, and empty-state handling.
 */
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
  getHostLineage: vi.fn(),
  getCurrentProjectId: vi.fn(() => 1),
  setCurrentProjectId: vi.fn(),
}));

import * as api from '../../services/api';
import HostLineagePanel from '../../components/HostLineagePanel';

const mockedApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

function renderPanel() {
  return render(
    <MemoryRouter>
      <HostLineagePanel hostId={500} />
</MemoryRouter>,
  );
}

const fullLineage = {
  host_id: 500,
  ip_address: '10.0.0.5',
  recon_sessions: [
    {
      session_id: 42, scope_id: 7, scope_name: 'Internal /24',
      status: 'completed', started_at: '2026-05-15T10:00:00Z',
      completed_at: '2026-05-15T11:00:00Z',
      generated_by_model: 'claude-opus-4-7',
      generated_by_tool: 'claude-code',
      started_by_username: 'alice',
    },
  ],
  plan_entries: [
    {
      plan_id: 11, title: 'Pen-test plan', status: 'proposed',
      version: 1, entry_id: 200, entry_status: 'proposed',
      created_at: '2026-05-15T12:00:00Z',
      generated_by_model: 'claude-opus-4-7',
      source_kind: 'recon_session',
    },
  ],
  execution_sessions: [
    {
      execution_session_id: 77, plan_id: 11, plan_title: 'Pen-test plan',
      status: 'completed',
      started_at: '2026-05-15T14:00:00Z',
      completed_at: '2026-05-15T15:00:00Z',
      generated_by_model: 'gpt-5-codex',
      started_by_username: 'bob',
      test_count: 5,
      finding_count: 2,
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  navigateSpy.mockReset();
});

describe('HostLineagePanel', () => {
  it('renders three sections from the lineage response', async () => {
    mockedApi.getHostLineage.mockResolvedValue(fullLineage);
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText('Workflow lineage')).toBeInTheDocument();
    });
    expect(screen.getByText('Recon sessions')).toBeInTheDocument();
    expect(screen.getByText('Plan entries')).toBeInTheDocument();
    expect(screen.getByText('Execution sessions')).toBeInTheDocument();

    // Recon row surfaces scope name.
    expect(screen.getByText(/Internal \/24/)).toBeInTheDocument();
    // Plan row surfaces the plan title.
    expect(screen.getByText(/Pen-test plan/)).toBeInTheDocument();
    // Execution row surfaces the per-host test/finding counts.
    expect(screen.getByText(/5 tests/)).toBeInTheDocument();
    expect(screen.getAllByText(/2 findings/).length).toBeGreaterThan(0);
  });

  it('Open buttons navigate to the right per-session pages', async () => {
    mockedApi.getHostLineage.mockResolvedValue(fullLineage);
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText(/Internal \/24/)).toBeInTheDocument();
    });
    const openButtons = screen.getAllByRole('button', { name: /Open/i });
    // Three rows, three Open buttons.  Order: recon row, plan row, execution row.
    fireEvent.click(openButtons[0]);
    expect(navigateSpy).toHaveBeenCalledWith('/recon/runs/42');
    fireEvent.click(openButtons[1]);
    expect(navigateSpy).toHaveBeenCalledWith('/test-plans/11');
    fireEvent.click(openButtons[2]);
    expect(navigateSpy).toHaveBeenCalledWith('/executions/77');
  });

  it('renders explicit empty state per section when nothing recorded', async () => {
    mockedApi.getHostLineage.mockResolvedValue({
      host_id: 500,
      ip_address: '10.0.0.5',
      recon_sessions: [],
      plan_entries: [],
      execution_sessions: [],
    });
    renderPanel();
    await waitFor(() => {
      expect(screen.getByText('Recon sessions')).toBeInTheDocument();
    });
    expect(
      screen.getByText(/No agent-attributed recon sessions/),
    ).toBeInTheDocument();
    expect(screen.getByText(/No plan includes this host/)).toBeInTheDocument();
    expect(
      screen.getByText(/No execution session has tested this host/),
    ).toBeInTheDocument();
  });

  it('surfaces an error alert when the API rejects', async () => {
    mockedApi.getHostLineage.mockRejectedValue(new Error('boom'));
    renderPanel();
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
  });
});
