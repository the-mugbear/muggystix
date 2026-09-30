/**
 * Agent Sessions (`/agent-activity`, pages/ProjectActivity.tsx) — v5.312.0,
 * session-first: what is live (with its work and its controls), what an ended
 * session left open, then the history, then the analytics.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';

import ProjectActivity, { fillCallDays } from '../../pages/ProjectActivity';

const emptyActivity = {
  window_days: 14,
  total_calls: 0,
  distinct_agents: 0,
  first_call_at: null,
  last_call_at: null,
  status_breakdown: { success: 0, client_error: 0, server_error: 0, other: 0 },
  by_workflow: [],
  daily: [],
  busiest_sessions: [],
};

vi.mock('../../services/api', () => ({
  listAgentSessions: vi.fn(),
  getAgentSessionSummary: vi.fn(),
  resumeAgentSession: vi.fn(),
  endAgentSession: vi.fn(),
  getAgentActivitySummary: vi.fn(),
  getCurrentProjectId: vi.fn(() => 1),
  setCurrentProjectId: vi.fn(),
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));
const project = vi.hoisted(() => ({ my_role: 'analyst' as string | null }));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'Demo', my_role: project.my_role } }),
}));

import * as api from '../../services/api';
const mockedApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;

const HOUR = 1000 * 60 * 60;
const ago = (ms: number) => new Date(Date.now() - ms).toISOString();
const ahead = (ms: number) => new Date(Date.now() + ms).toISOString();

/** A consolidated session; live key unless overridden. */
const session = (overrides: Record<string, unknown> = {}) => ({
  kind: 'project' as const,
  id: 72,
  project_id: 1,
  agent_id: 7,
  agent_name: "alice's-agent",
  user_id: 3,
  user_username: 'alice',
  user_full_name: 'Alice Analyst',
  status: 'active',
  started_at: ago(HOUR),
  completed_at: null,
  generated_by_model: null,
  generated_by_tool: null,
  prompt_version: '2.13.1',
  scope_id: null,
  test_plan_id: null,
  purpose: 'map the DMZ',
  key_expires_at: ahead(20 * HOUR),
  renewable_until: ahead(6 * 24 * HOUR),
  end_reason: null,
  feedback_count: 0,
  last_activity_at: ago(5 * 60 * 1000),
  operator_role: 'analyst',
  assist_session_id: 52,
  phases: [
    { kind: 'execution', id: 46, status: 'paused', label: 'SMB sweep', scope_id: null, test_plan_id: 17, started_at: ago(HOUR / 2) },
  ],
  can_end: true,
  can_resume: true,
  ...overrides,
});

const legacyRun = (overrides: Record<string, unknown> = {}) => ({
  kind: 'execution' as const,
  id: 42,
  project_id: 1,
  agent_id: 7,
  agent_name: "alice's-agent",
  user_id: 3,
  user_username: 'alice',
  status: 'completed',
  started_at: ago(30 * HOUR),
  completed_at: ago(29 * HOUR),
  generated_by_model: 'claude-opus-4-7',
  generated_by_tool: 'claude-code',
  prompt_version: '1.13.0',
  scope_id: null,
  test_plan_id: 17,
  target_label: 'Legacy plan',
  ...overrides,
});

/** The live call is `{kind: 'project', status: 'active'}`; everything else is
 *  the history. */
const serve = (live: unknown[], history: unknown[], total = history.length) => {
  mockedApi.listAgentSessions.mockImplementation(async (filters: Record<string, unknown> = {}) =>
    filters.kind === 'project' && filters.status === 'active'
      ? { project_id: 1, sessions: live, total: live.length }
      : { project_id: 1, sessions: history, total });
};

const renderPage = () =>
  render(
    <MemoryRouter>
      <ProjectActivity />
    </MemoryRouter>,
  );

describe('Agent Sessions', () => {
  beforeEach(() => {
    project.my_role = 'analyst';
    mockedApi.listAgentSessions.mockReset();
    mockedApi.getAgentSessionSummary.mockReset();
    mockedApi.getAgentSessionSummary.mockResolvedValue({ project_id: 1, summary: [] });
    mockedApi.getAgentActivitySummary.mockReset();
    mockedApi.getAgentActivitySummary.mockResolvedValue(emptyActivity);
    mockedApi.endAgentSession.mockReset();
    mockedApi.resumeAgentSession.mockReset();
  });

  it('leads with what is live: its work, its last call and its controls', async () => {
    const live = session();
    serve([live], [live]);
    renderPage();

    const section = await screen.findByTestId('live-sessions');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Agent Sessions');
    expect(screen.getByText('1 session live now.')).toBeInTheDocument();
    const item = within(section).getByTestId('live-session');
    // The session opens its own page, keyed by the SESSION id.
    expect(within(item).getByRole('link', { name: /#72 · map the DMZ/ })).toHaveAttribute('href', '/agent-sessions/72');
    // Its runs link to their pages — they appear nowhere else on the page.
    expect(within(item).getByRole('link', { name: /Execution #46/ })).toHaveAttribute('href', '/executions/46');
    expect(within(item).getByText(/last call/)).toBeInTheDocument();
    expect(within(item).getByText('Live')).toBeInTheDocument();
    expect(within(item).getByText('Analyst role')).toBeInTheDocument();
    expect(within(item).getByRole('button', { name: /Resume/ })).toBeInTheDocument();
    expect(within(item).getByRole('button', { name: /End/ })).toBeInTheDocument();
    expect(within(item).getByRole('link', { name: 'Open agent session 72' })).toHaveAttribute('href', '/agent-sessions/72');
  });

  it('offers only the controls the caller has — a project admin may end, not resume', async () => {
    const other = session({ id: 73, user_id: 9, user_full_name: 'Bob', can_resume: false, can_end: true });
    const viewerSees = session({ id: 74, purpose: 'someone else', can_resume: false, can_end: false });
    serve([other, viewerSees], [other, viewerSees]);
    renderPage();

    const items = await screen.findAllByTestId('live-session');
    expect(within(items[0]).queryByRole('button', { name: /Resume/ })).not.toBeInTheDocument();
    expect(within(items[0]).getByRole('button', { name: /End/ })).toBeInTheDocument();
    expect(within(items[1]).queryByRole('button', { name: /End/ })).not.toBeInTheDocument();
    expect(within(items[1]).queryByRole('button', { name: /Resume/ })).not.toBeInTheDocument();
  });

  it('says a session is waiting to be resumed when its key ran out, without a spinner of work', async () => {
    const stale = session({ key_expires_at: ago(6 * HOUR), last_activity_at: null, phases: [] });
    serve([stale], [stale]);
    renderPage();

    const item = await screen.findByTestId('live-session');
    expect(within(item).getByText('Resumable')).toBeInTheDocument();
    expect(within(item).getByText(/key expired · resumable until/)).toBeInTheDocument();
    expect(within(item).getByText(/no call yet — the agent has not connected/)).toBeInTheDocument();
    expect(within(item).getByText(/inventory queries only/)).toBeInTheDocument();
    expect(screen.getByText(/0 sessions live now; 1 more waiting to be resumed/)).toBeInTheDocument();
  });

  it('hands over the wrap-up prompt before ending a connected session, then ends it', async () => {
    const user = userEvent.setup();
    const live = session();
    serve([live], [live]);
    mockedApi.endAgentSession.mockResolvedValue(undefined);
    renderPage();

    const item = await screen.findByTestId('live-session');
    await user.click(within(item).getByRole('button', { name: /End/ }));
    await screen.findByText(/Agent still connected\? Paste this to it first/);
    expect(screen.getByText(/submit_feedback/)).toBeInTheDocument();
    expect(mockedApi.endAgentSession).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'End session' }));
    await waitFor(() => expect(mockedApi.endAgentSession).toHaveBeenCalledWith(72));
  });

  it('opens the resume dialog on the same session', async () => {
    const user = userEvent.setup();
    const live = session({ key_expires_at: ago(HOUR) });
    serve([live], [live]);
    renderPage();

    const item = await screen.findByTestId('live-session');
    await user.click(within(item).getByRole('button', { name: /Resume/ }));
    await screen.findByText(/Resume BlueStick agent session #72/);
  });

  it('says so when nothing is live, with the way to start one', async () => {
    serve([], []);
    renderPage();

    expect(await screen.findByText('No agent session is live')).toBeInTheDocument();
    expect(screen.getByText('No agent session is live on this project.')).toBeInTheDocument();
    for (const link of screen.getAllByRole('link', { name: /Start agent session/ })) {
      expect(link).toHaveAttribute('href', '/operations?start=agent-session');
    }
    expect(screen.getByText('No agent has run against this project yet.')).toBeInTheDocument();
  });

  it('lists one history row per session that opens the session page, and legacy rows their own', async () => {
    const ended = session({
      id: 60,
      status: 'ended',
      end_reason: 'agent',
      feedback_count: 2,
      key_expires_at: null,
      can_end: false,
      can_resume: false,
    });
    serve([], [ended, legacyRun()]);
    renderPage();

    const table = await screen.findByTestId('runs-table');
    const rows = within(table).getAllByTestId('run-row');
    for (const link of within(rows[0]).getAllByRole('link', { name: 'Open agent session 60' })) {
      expect(link).toHaveAttribute('href', '/agent-sessions/60');
    }
    expect(within(rows[0]).getByText('ended by agent · 2 feedback')).toBeInTheDocument();
    expect(within(rows[0]).getByText('Ended')).toBeInTheDocument();
    expect(within(rows[0]).getByRole('link', { name: /Execution #46/ })).toHaveAttribute('href', '/executions/46');
    // Legacy: its own page, its own kind badge.
    for (const link of within(rows[1]).getAllByRole('link', { name: 'Open Execution 42' })) {
      expect(link).toHaveAttribute('href', '/executions/42');
    }
    expect(within(rows[1]).getByText('Legacy plan')).toBeInTheDocument();
    expect(within(rows[1]).getByText('claude-opus-4-7 · claude-code')).toBeInTheDocument();
  });

  it('offers the model filter once an agent has reported one, and passes it to the API', async () => {
    const user = userEvent.setup();
    serve([], [legacyRun()]);
    renderPage();
    await screen.findByTestId('runs-table');
    expect(screen.queryByLabelText('Filter sessions by model')).not.toBeInTheDocument();

    mockedApi.getAgentSessionSummary.mockResolvedValue({
      project_id: 1,
      summary: [{
        generated_by_model: 'claude-opus-4-7', generated_by_tool: 'claude-code',
        project: 0, plan_generation: 0, execution: 1, assist: 0, total: 1,
      }],
    });
    await user.click(screen.getByRole('button', { name: /Refresh agent sessions/i }));
    const trigger = await screen.findByLabelText('Filter sessions by model');
    await user.click(trigger);
    await user.click(await screen.findByRole('option', { name: 'claude-opus-4-7' }));
    await waitFor(() =>
      expect(mockedApi.listAgentSessions).toHaveBeenCalledWith(expect.objectContaining({ model: 'claude-opus-4-7' })),
    );
  });

  // N7 — a slow response for an older filter must not overwrite the newer one.
  it('keeps the newest filter’s history when an older response arrives last', async () => {
    const user = userEvent.setup();
    let releaseOld!: () => void;
    const oldDone = new Promise<void>((res) => { releaseOld = res; });
    mockedApi.listAgentSessions.mockImplementation(async (filters: Record<string, unknown> = {}) => {
      if (filters.kind === 'project' && filters.status === 'active') {
        return { project_id: 1, sessions: [], total: 0 };
      }
      if (filters.kind === 'execution') {
        return { project_id: 1, sessions: [legacyRun({ id: 43, target_label: 'Newer filter' })], total: 1 };
      }
      await oldDone;
      return { project_id: 1, sessions: [legacyRun({ target_label: 'Older filter' })], total: 1 };
    });
    renderPage();

    await user.click(screen.getByLabelText('Filter sessions by kind'));
    await user.click(await screen.findByRole('option', { name: 'Legacy execution' }));
    expect(await screen.findByText('Newer filter')).toBeInTheDocument();

    releaseOld();
    await oldDone;
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByText('Newer filter')).toBeInTheDocument();
    expect(screen.queryByText('Older filter')).toBeNull();
  });

  it('offers no way to start a session to a project viewer', async () => {
    project.my_role = 'viewer';
    serve([], []);
    renderPage();
    expect(await screen.findByText('No agent session is live')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /Start agent session/ })).toBeNull();
  });

  it('shows session hygiene even when no API calls were recorded, counts under five', async () => {
    serve([], []);
    mockedApi.getAgentActivitySummary.mockResolvedValue({
      ...emptyActivity,
      session_hygiene: {
        sessions_started: 2, sessions_active: 0, sessions_ended: 2,
        ended_by_agent: 0, ended_by_operator: 0, lapsed: 2, sessions_with_feedback: 0,
      },
    });
    renderPage();

    await screen.findByText(/No agent API calls recorded/);
    expect(screen.getByText('Session hygiene')).toBeInTheDocument();
    expect(screen.getAllByText('0 of 2')).toHaveLength(2);
    expect(screen.queryByText(/0%/)).not.toBeInTheDocument();
    expect(screen.getByText(/2 sessions in the last 14 days lapsed without ending/)).toBeInTheDocument();
  });

  it('keeps percentages once the sample is five sessions or more', async () => {
    serve([], []);
    mockedApi.getAgentActivitySummary.mockResolvedValue({
      ...emptyActivity,
      session_hygiene: {
        sessions_started: 8, sessions_active: 0, sessions_ended: 8,
        ended_by_agent: 6, ended_by_operator: 0, lapsed: 2, sessions_with_feedback: 2,
      },
    });
    renderPage();
    await screen.findByText('Session hygiene');
    expect(screen.getByText('6 · 75%')).toBeInTheDocument();
    expect(screen.getByText('2 · 25%')).toBeInTheDocument();
  });

  it('draws every day of the window, so one busy day is one bar and not the whole chart', async () => {
    serve([], []);
    const today = new Date().toISOString().slice(0, 10);
    mockedApi.getAgentActivitySummary.mockResolvedValue({
      ...emptyActivity,
      total_calls: 438,
      distinct_agents: 4,
      status_breakdown: { success: 416, client_error: 22, server_error: 0, other: 0 },
      daily: [{ day: today, calls: 438, errors: 22 }],
      busiest_sessions: [{ workflow: 'session', session_id: 72, calls: 199 }],
    });
    renderPage();

    const chart = await screen.findByTestId('calls-per-day');
    expect(chart.querySelectorAll('button')).toHaveLength(14);
    expect(screen.getByLabelText(`${today}: 438 calls, 22 errors`)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open session #72' })).toBeInTheDocument();
  });

  it('shows a one-line note instead of the model table when no model or tool was reported', async () => {
    serve([], []);
    mockedApi.getAgentSessionSummary.mockResolvedValue({
      project_id: 1,
      summary: [{
        generated_by_model: null, generated_by_tool: null,
        project: 5, plan_generation: 0, execution: 0, assist: 0, total: 5,
      }],
    });
    renderPage();
    await screen.findByText(/No agent has reported its model or tool yet/);
    expect(screen.queryByText('Activity by agent / model')).not.toBeInTheDocument();
  });
});

describe('fillCallDays', () => {
  it('fills the window with zero days, oldest first, ending today', () => {
    const days = fillCallDays([{ day: '2026-09-28', calls: 3, errors: 0 }], 3, new Date('2026-09-29T12:00:00Z'));
    expect(days.map((d) => [d.day, d.calls])).toEqual([
      ['2026-09-27', 0], ['2026-09-28', 3], ['2026-09-29', 0],
    ]);
  });

  it('never drops a day the server counted after the browser’s today', () => {
    const days = fillCallDays([{ day: '2026-09-30', calls: 1, errors: 0 }], 2, new Date('2026-09-29T23:00:00Z'));
    expect(days.map((d) => d.day)).toEqual(['2026-09-29', '2026-09-30']);
  });
});
