import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import ProjectActivity from '../../pages/ProjectActivity';

vi.mock('../../services/api', () => ({
  listAgentSessions: vi.fn(),
  getAgentSessionSummary: vi.fn(),
  // v5.214.0 — Resume on an owned active project row.
  resumeAgentSession: vi.fn(),
  endAgentSession: vi.fn(),
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

// v5.212.0 — the page reads the signed-in user (to offer End on the sessions
// they own) and toasts the outcome; neither provider is mounted here.
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ user: { id: 3, username: 'alice', role: 'member' } }),
}));
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }),
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

  // v5.214.0 — a project session the signed-in user owns, whose key has
  // lapsed but is still renewable: the row says so, offers Resume, and the
  // dialog rotates the key on the same session.
  it('offers Resume on an owned active project session and rotates its key', async () => {
    const user = userEvent.setup();
    const projectRow = {
      kind: 'project' as const,
      id: 77,
      project_id: 1,
      agent_id: 7,
      agent_name: "alice's-agent",
      user_id: 3,
      user_username: 'alice',
      status: 'active',
      started_at: new Date(Date.now() - 1000 * 60 * 60 * 30).toISOString(),
      completed_at: null,
      generated_by_model: null,
      generated_by_tool: null,
      prompt_version: '2.4.0',
      scope_id: null,
      test_plan_id: null,
      purpose: 'recon the DMZ',
      key_expires_at: new Date(Date.now() - 1000 * 60 * 60 * 6).toISOString(),
      renewable_until: new Date(Date.now() + 1000 * 60 * 60 * 24 * 5).toISOString(),
    };
    mockedApi.listAgentSessions.mockResolvedValue({
      project_id: 1,
      sessions: [projectRow, ...sampleSessions],
      total: 3,
    });
    mockedApi.getAgentSessionSummary.mockResolvedValue({ project_id: 1, summary: sampleSummary });
    mockedApi.resumeAgentSession.mockResolvedValue({
      session_id: 77,
      project_id: 1,
      project_name: 'demo',
      agent_id: 7,
      api_key: 'nm_agent_replacement',
      instructions: '> RESUMED SESSION. continue',
      mcp_clients: [],
      mcp_url: 'https://h/api/v1/mcp',
      key_ttl_hours: 24,
      key_expires_at: new Date(Date.now() + 1000 * 60 * 60 * 24).toISOString(),
      renewable_until: projectRow.renewable_until,
      active_recon_session_ids: [5],
      active_execution_session_ids: [],
    });

    renderPage();

    await screen.findByText(/recon the DMZ/);
    // The status cell says the key lapsed but the session is still renewable.
    expect(screen.getByText(/key expired · renewable until/)).toBeInTheDocument();
    // Resume is offered only on the owned project row; the execution row
    // (also active, also alice's) keeps its Open button.
    expect(screen.getByLabelText('Resume agent session 77')).toBeInTheDocument();
    expect(screen.queryByLabelText('Resume agent session 42')).not.toBeInTheDocument();

    await user.click(screen.getByLabelText('Resume agent session 77'));
    // Path 1 — reopen the client, hand the agent the resume line.
    await screen.findByText(/If the client that ran this session still has the key/);
    expect(screen.getByText(/Resume BlueStick agent session #77/)).toBeInTheDocument();
    // Path 2 — rotate.
    await user.click(screen.getByRole('button', { name: /Rotate key and get the prompt/ }));
    await waitFor(() => expect(mockedApi.resumeAgentSession).toHaveBeenCalledWith(77));
    await screen.findByText('nm_agent_replacement');
    expect(screen.getByText(/Still open:/)).toBeInTheDocument();
    // The key on screen must be acknowledged before the dialog can close.
    const close = screen.getByRole('button', { name: 'Close' });
    expect(close).toBeDisabled();
    await user.click(screen.getByLabelText('I copied the replacement agent API key'));
    expect(close).toBeEnabled();
  });

  // v5.219.0 — the End flow hands the operator a wrap-up prompt while the
  // agent is still reachable (feedback + clean exit come from the agent, not
  // from the UI), and ended rows say how they ended and whether they filed
  // feedback.
  it('offers a wrap-up prompt before ending a live session and labels ended rows', async () => {
    const user = userEvent.setup();
    const liveRow = {
      kind: 'project' as const,
      id: 78,
      project_id: 1,
      agent_id: 7,
      agent_name: "alice's-agent",
      user_id: 3,
      user_username: 'alice',
      status: 'active',
      started_at: new Date(Date.now() - 1000 * 60 * 30).toISOString(),
      completed_at: null,
      generated_by_model: null,
      generated_by_tool: null,
      prompt_version: '2.5.0',
      scope_id: null,
      test_plan_id: null,
      purpose: 'live one',
      key_expires_at: new Date(Date.now() + 1000 * 60 * 60 * 20).toISOString(),
      renewable_until: new Date(Date.now() + 1000 * 60 * 60 * 24 * 6).toISOString(),
      end_reason: null,
      feedback_count: 0,
    };
    const lapsedRow = {
      ...liveRow,
      id: 79,
      status: 'ended',
      purpose: 'lapsed one',
      completed_at: new Date(Date.now() - 1000 * 60 * 60).toISOString(),
      key_expires_at: null,
      end_reason: 'lapsed',
      feedback_count: 0,
    };
    const cleanRow = {
      ...liveRow,
      id: 80,
      status: 'ended',
      purpose: 'clean one',
      completed_at: new Date(Date.now() - 1000 * 60 * 60).toISOString(),
      key_expires_at: null,
      end_reason: 'agent',
      feedback_count: 2,
    };
    mockedApi.listAgentSessions.mockResolvedValue({
      project_id: 1,
      sessions: [liveRow, lapsedRow, cleanRow],
      total: 3,
    });
    mockedApi.getAgentSessionSummary.mockResolvedValue({ project_id: 1, summary: sampleSummary });
    mockedApi.getAgentActivitySummary.mockResolvedValue({
      window_days: 14,
      total_calls: 12,
      distinct_agents: 1,
      first_call_at: null,
      last_call_at: null,
      status_breakdown: { success: 12, client_error: 0, server_error: 0, other: 0 },
      by_workflow: [],
      daily: [],
      busiest_sessions: [],
      session_hygiene: {
        sessions_started: 3,
        sessions_active: 1,
        sessions_ended: 2,
        ended_by_agent: 1,
        ended_by_operator: 0,
        lapsed: 1,
        sessions_with_feedback: 1,
      },
    });

    renderPage();
    await screen.findByText(/live one/);

    // Ended rows say how they ended and whether they said anything.
    expect(screen.getByText('lapsed (never ended) · no feedback')).toBeInTheDocument();
    expect(screen.getByText('ended by agent · 2 feedback')).toBeInTheDocument();

    // The hygiene strip reads off the summary, not the page of rows.
    expect(screen.getByText('Session hygiene')).toBeInTheDocument();
    expect(screen.getByText('1 · 50%')).toBeInTheDocument(); // ended by the agent, of 2 ended
    expect(screen.getByText('1 · 33%')).toBeInTheDocument(); // filed feedback, of 3 started

    // End on the live row shows the wrap-up prompt first.
    await user.click(screen.getByLabelText('End agent session 78'));
    await screen.findByText(/Agent still connected\? Paste this to it first/);
    expect(screen.getByText(/We are done with this BlueStick session/)).toBeInTheDocument();
    expect(screen.getByText(/submit_feedback/)).toBeInTheDocument();
    expect(mockedApi.endAgentSession).not.toHaveBeenCalled();
  });

  // Review (v5.219.1): the card returned early on zero calls, before the
  // hygiene strip — hiding exactly the sessions whose agent never connected.
  it('shows session hygiene even when no API calls were recorded', async () => {
    mockedApi.listAgentSessions.mockResolvedValue({ project_id: 1, sessions: [], total: 0 });
    mockedApi.getAgentSessionSummary.mockResolvedValue({ project_id: 1, summary: [] });
    mockedApi.getAgentActivitySummary.mockResolvedValue({
      window_days: 14,
      total_calls: 0,
      distinct_agents: 0,
      first_call_at: null,
      last_call_at: null,
      status_breakdown: { success: 0, client_error: 0, server_error: 0, other: 0 },
      by_workflow: [],
      daily: [],
      busiest_sessions: [],
      session_hygiene: {
        sessions_started: 2,
        sessions_active: 0,
        sessions_ended: 2,
        ended_by_agent: 0,
        ended_by_operator: 0,
        lapsed: 2,
        sessions_with_feedback: 0,
      },
    });

    renderPage();
    await screen.findByText(/No agent API calls recorded/);
    expect(screen.getByText('Session hygiene')).toBeInTheDocument();
    expect(screen.getByText('Lapsed (never ended)')).toBeInTheDocument();
    // "0 · 0%" twice: agent exits of 2 ended, and feedback of 2 started.
    expect(screen.getAllByText('0 · 0%')).toHaveLength(2);
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

  // v5.267.0 — the Posture layout: a lead sentence, one strip of four
  // measures, sections over thin rules, no card anywhere.
  describe('Posture layout', () => {
    const hygieneSummary = (overrides: Record<string, unknown> = {}) => ({
      window_days: 14,
      total_calls: 0,
      distinct_agents: 0,
      first_call_at: null,
      last_call_at: null,
      status_breakdown: { success: 0, client_error: 0, server_error: 0, other: 0 },
      by_workflow: [],
      daily: [],
      busiest_sessions: [],
      session_hygiene: {
        sessions_started: 3,
        sessions_active: 1,
        sessions_ended: 2,
        ended_by_agent: 1,
        ended_by_operator: 0,
        lapsed: 1,
        sessions_with_feedback: 1,
      },
      ...overrides,
    });

    it('leads with the session facts and renders no cards', async () => {
      mockedApi.listAgentSessions.mockResolvedValue({ project_id: 1, sessions: sampleSessions, total: 2 });
      mockedApi.getAgentSessionSummary.mockResolvedValue({ project_id: 1, summary: sampleSummary });
      mockedApi.getAgentActivitySummary.mockResolvedValue(hygieneSummary());

      const { container } = renderPage();
      await screen.findByText(/Plan #17/);
      expect(
        screen.getByText('3 sessions started in the last 14 days; 1 still active; 1 lapsed without ending.'),
      ).toBeInTheDocument();
      expect(container.querySelector('.bg-card.shadow-raised')).toBeNull();
      // One strip of four measures — the fifth box (ended by operator) is gone.
      for (const label of ['Sessions started', 'Ended by the agent', 'Lapsed (never ended)', 'Filed feedback']) {
        expect(screen.getByText(label)).toBeInTheDocument();
      }
      expect(screen.queryByText('Ended by operator')).not.toBeInTheDocument();
      // The filters are one row inside the Runs section; user · agent share a line.
      expect(screen.getByTestId('runs-filters')).toHaveClass('border-b');
      expect(screen.getByText('alice').closest('p')).toHaveTextContent("alice · alice's-agent");
    });

    it('replaces an empty call chart with one caption line', async () => {
      mockedApi.listAgentSessions.mockResolvedValue({ project_id: 1, sessions: sampleSessions, total: 2 });
      mockedApi.getAgentSessionSummary.mockResolvedValue({ project_id: 1, summary: sampleSummary });
      mockedApi.getAgentActivitySummary.mockResolvedValue(hygieneSummary());

      renderPage();
      await screen.findByText('No agent API calls recorded in the last 14 days.');
      expect(screen.queryByText('API calls')).not.toBeInTheDocument();
      expect(screen.queryByText('Calls per day')).not.toBeInTheDocument();
    });

    it('renders the API-call section with its day bars and busiest sessions when there were calls', async () => {
      mockedApi.listAgentSessions.mockResolvedValue({ project_id: 1, sessions: sampleSessions, total: 2 });
      mockedApi.getAgentSessionSummary.mockResolvedValue({ project_id: 1, summary: sampleSummary });
      mockedApi.getAgentActivitySummary.mockResolvedValue(hygieneSummary({
        total_calls: 30,
        distinct_agents: 2,
        status_breakdown: { success: 27, client_error: 2, server_error: 1, other: 0 },
        by_workflow: [{ workflow: 'recon', calls: 30 }],
        daily: [{ day: '2026-09-20', calls: 10, errors: 0 }, { day: '2026-09-21', calls: 20, errors: 3 }],
        busiest_sessions: [{ workflow: 'recon', session_id: 3, calls: 30 }],
      }));

      renderPage();
      await screen.findByText('API calls');
      expect(screen.getByTestId('api-call-line')).toHaveTextContent('30 calls from 2 agents · 27 2xx · 2 4xx · 1 5xx');
      expect(screen.getByLabelText('2026-09-21: 20 calls, 3 errors')).toBeInTheDocument();
      expect(screen.getByText('Busiest sessions')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /^Open$/ })).toBeInTheDocument();
    });

    it('shows a one-line note instead of the model table when no model or tool was reported', async () => {
      mockedApi.listAgentSessions.mockResolvedValue({ project_id: 1, sessions: [], total: 0 });
      mockedApi.getAgentSessionSummary.mockResolvedValue({
        project_id: 1,
        summary: [{
          generated_by_model: null, generated_by_tool: null,
          project: 5, recon: 0, plan_generation: 0, execution: 0, assist: 0, total: 5,
        }],
      });

      renderPage();
      await screen.findByText(/No agent has reported its model or tool yet/);
      expect(screen.queryByText('Activity by agent / model')).not.toBeInTheDocument();
      expect(screen.queryByText('(not reported)')).not.toBeInTheDocument();
      // No hygiene from this backend: the lead falls back to the unfiltered rollup.
      expect(screen.getByText('5 agent sessions on record for this project.')).toBeInTheDocument();
    });
  });
});
