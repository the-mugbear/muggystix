import React from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import ProjectActivity from '../../pages/ProjectActivity';

vi.mock('../../services/api', () => ({
  listAgentSessions: vi.fn(),
  getAgentSessionSummary: vi.fn(),
  // v4.59.0 (NEW I) — page also calls getAgentActivitySummary for
  // the ApiCallSummaryCard.  Pre-fix the mock omitted it; the
  // page accessed summary.daily.map(...) which threw and broke
  // render.  Default to an empty-but-shape-correct summary so the
  // card renders its zero-state cleanly.
  getAgentActivitySummary: vi.fn().mockResolvedValue({
    window_days: 14,
    total_calls: 0,
    distinct_agents: 0,
    first_call_at: null,
    last_call_at: null,
    status_breakdown: {
      success: 0,
      client_error: 0,
      server_error: 0,
      other: 0,
    },
    by_workflow: [],
    daily: [],
    busiest_sessions: [],
  }),
  // Anything pulled transitively by other consumers in the page tree.
  getCurrentProjectId: vi.fn(() => 1),
  setCurrentProjectId: vi.fn(),
}));

import * as api from '../../services/api';
const mockedApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;


const renderPage = () =>
  render(
    <MemoryRouter>
      <ProjectActivity />
</MemoryRouter>,
  );

const sampleSessions = [
  {
    kind: 'execution' as const,
    id: 42,
    project_id: 1,
    agent_id: 7,
    agent_name: "alice's-agent",
    user_id: 3,
    user_username: 'alice',
    status: 'active',
    started_at: new Date(Date.now() - 1000 * 60 * 30).toISOString(),
    completed_at: null,
    generated_by_model: 'claude-opus-4-7',
    generated_by_tool: 'claude-code',
    prompt_version: '1.13.0',
    scope_id: null,
    test_plan_id: 17,
  },
  {
    kind: 'recon' as const,
    id: 3,
    project_id: 1,
    agent_id: 9,
    agent_name: "bob's-agent",
    user_id: 4,
    user_username: 'bob',
    status: 'completed',
    started_at: new Date(Date.now() - 1000 * 60 * 60 * 5).toISOString(),
    completed_at: new Date(Date.now() - 1000 * 60 * 60 * 4).toISOString(),
    generated_by_model: 'gpt-5-codex',
    generated_by_tool: 'codex',
    prompt_version: '1.13.0',
    scope_id: 12,
    test_plan_id: null,
  },
];

const sampleSummary = [
  {
    generated_by_model: 'claude-opus-4-7',
    generated_by_tool: 'claude-code',
    recon: 1,
    plan_generation: 2,
    execution: 5,
    total: 8,
  },
  {
    generated_by_model: 'gpt-5-codex',
    generated_by_tool: 'codex',
    recon: 3,
    plan_generation: 0,
    execution: 1,
    total: 4,
  },
];

describe('ProjectActivity', () => {
  beforeEach(() => {
    mockedApi.listAgentSessions.mockReset();
    mockedApi.getAgentSessionSummary.mockReset();
    // v4.59.0 (NEW I) — restore the default empty rollup after
    // mockReset so tests that don't override it don't blow up the
    // ModelRollupCard.
    mockedApi.getAgentActivitySummary.mockReset();
    mockedApi.getAgentActivitySummary.mockResolvedValue({
      window_days: 14,
      total_calls: 0,
      distinct_agents: 0,
      first_call_at: null,
      last_call_at: null,
      status_breakdown: {
        success: 0,
        client_error: 0,
        server_error: 0,
        other: 0,
      },
      by_workflow: [],
      daily: [],
      busiest_sessions: [],
    });
  });

  it('renders both workflows side by side with model + user attribution', async () => {
    mockedApi.listAgentSessions.mockResolvedValueOnce({
      project_id: 1,
      sessions: sampleSessions,
      total: 2,
    });
    mockedApi.getAgentSessionSummary.mockResolvedValueOnce({
      project_id: 1,
      summary: sampleSummary,
    });

    renderPage();

    // Wait for the table rows to appear.
    await screen.findByText(/Plan #17/);
    expect(screen.getByText(/Scope #12/)).toBeInTheDocument();
    // Models surface in BOTH the rollup card and the timeline table,
    // so getAllByText (≥ 1 match).
    expect(screen.getAllByText('claude-opus-4-7').length).toBeGreaterThan(0);
    expect(screen.getAllByText('gpt-5-codex').length).toBeGreaterThan(0);
    // User attribution lives only in the timeline rows.
    expect(screen.getByText('alice')).toBeInTheDocument();
    expect(screen.getByText('bob')).toBeInTheDocument();
  });

  it('renders the model rollup card with per-(model, tool) counts', async () => {
    mockedApi.listAgentSessions.mockResolvedValueOnce({
      project_id: 1,
      sessions: [],
      total: 0,
    });
    mockedApi.getAgentSessionSummary.mockResolvedValueOnce({
      project_id: 1,
      summary: sampleSummary,
    });

    renderPage();

    await screen.findByText('Activity by agent / model');
    // Both model rows in the rollup.
    expect(screen.getAllByText('claude-opus-4-7').length).toBeGreaterThan(0);
    expect(screen.getAllByText('gpt-5-codex').length).toBeGreaterThan(0);
    // Totals column.
    expect(screen.getByText('8')).toBeInTheDocument();
    expect(screen.getByText('4')).toBeInTheDocument();
  });

  it('passes the model filter to the API when the user picks a model', async () => {
    const user = userEvent.setup();
    mockedApi.listAgentSessions.mockResolvedValue({
      project_id: 1,
      sessions: sampleSessions,
      total: 2,
    });
    mockedApi.getAgentSessionSummary.mockResolvedValueOnce({
      project_id: 1,
      summary: sampleSummary,
    });

    renderPage();

    await screen.findByText(/Plan #17/);

    // Open the Model dropdown and pick claude-opus-4-7.  MUI Select
    // renders as a button — click it then click the menu item.
    const modelSelect = screen.getByLabelText('Model');
    await user.click(modelSelect);
    const claude = await screen.findByRole('option', { name: 'claude-opus-4-7' });
    await user.click(claude);

    await waitFor(() => {
      const calls = mockedApi.listAgentSessions.mock.calls;
      const lastCall = calls[calls.length - 1];
      expect(lastCall?.[0]).toEqual(expect.objectContaining({
        model: 'claude-opus-4-7',
      }));
    });
  });

  it('shows an empty-state when no sessions match', async () => {
    mockedApi.listAgentSessions.mockResolvedValueOnce({
      project_id: 1,
      sessions: [],
      total: 0,
    });
    mockedApi.getAgentSessionSummary.mockResolvedValueOnce({
      project_id: 1,
      summary: [],
    });

    renderPage();

    await screen.findByText(/No agent sessions match the current filters\./);
  });
});
