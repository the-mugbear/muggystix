/**
 * Agent Sessions (`/agent-activity`, pages/AgentSessions.tsx) — v5.312.0,
 * session-first: what is live (with its work and its controls), what an ended
 * session left open, then the history, then the analytics.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, useSearchParams } from 'react-router-dom';

import ProjectActivity, { fillCallDays, HISTORY_COLUMN_WIDTHS } from '../../pages/AgentSessions';

const emptyActivity = {
  window_days: 14,
  total_calls: 0,
  distinct_agents: 0,
  first_call_at: null,
  last_call_at: null,
  status_breakdown: { success: 0, client_error: 0, server_error: 0, other: 0 },
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

/** A session; live key unless overridden. */
const session = (overrides: Record<string, unknown> = {}) => ({
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
  purpose: 'map the DMZ',
  key_expires_at: ahead(20 * HOUR),
  key_state: 'live',
  renewable_until: ahead(6 * 24 * HOUR),
  end_reason: null,
  feedback_count: 0,
  last_activity_at: ago(5 * 60 * 1000),
  operator_role: 'analyst',
  host_test_count: 3,
  evidence_count: 1,
  can_end: true,
  can_resume: true,
  ...overrides,
});

/** A session from before one session did all the work, as the server sends it
 *  now: the same fields as any other (5.383.0 — no `kind`), with the sparse
 *  values such an old row has (no purpose, none of the later counts). */
const legacyRun = (overrides: Record<string, unknown> = {}) => ({
  id: 42,
  project_id: 1,
  agent_id: 7,
  agent_name: "alice's-agent",
  user_id: 3,
  user_username: 'alice',
  status: 'ended',
  started_at: ago(30 * HOUR),
  completed_at: ago(29 * HOUR),
  generated_by_model: 'claude-opus-4-7',
  generated_by_tool: 'claude-code',
  prompt_version: '1.13.0',
  ...overrides,
});

/** The live call is `{status: 'active', limit: 100}`; everything else is the
 *  history (which never names a status). */
const isLiveCall = (filters: Record<string, unknown> = {}) => filters.status === 'active';
const serve = (live: unknown[], history: unknown[], total = history.length) => {
  mockedApi.listAgentSessions.mockImplementation(async (_projectId: number, filters: Record<string, unknown> = {}) =>
    isLiveCall(filters)
      ? { project_id: 1, sessions: live, total: live.length }
      : { project_id: 1, sessions: history, total });
};

/** The roll-up that names a model, so the history offers the model filter —
 *  the filter these tests change (there is no filter by kind of session). */
const MODEL = 'claude-opus-4-7';
const MODEL_SUMMARY = {
  project_id: 1,
  summary: [{ generated_by_model: MODEL, generated_by_tool: 'claude-code', total: 1 }],
};
const chooseModel = async (user: ReturnType<typeof userEvent.setup>) => {
  await user.click(await screen.findByLabelText('Filter sessions by model'));
  await user.click(await screen.findByRole('option', { name: MODEL }));
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
    // Its work — the tests it proposed and the evidence it recorded — opens
    // the session page, which lists them.
    expect(within(item).getByRole('link', { name: '3 tests proposed · 1 evidence record' }))
      .toHaveAttribute('href', '/agent-sessions/72');
    expect(within(item).queryByText(/Execution #|Plan #/)).not.toBeInTheDocument();
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
    const stale = session({ key_expires_at: ago(6 * HOUR), key_state: 'resumable', last_activity_at: null, host_test_count: 0, evidence_count: 0 });
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
    await waitFor(() => expect(mockedApi.endAgentSession).toHaveBeenCalledWith(1, 72));
  });

  it('opens the resume dialog on the same session', async () => {
    const user = userEvent.setup();
    const live = session({ key_expires_at: ago(HOUR), key_state: 'resumable' });
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
    // Said once: the empty state carries it, the lead does not repeat it.
    expect(screen.queryByText('No agent session is live on this project.')).toBeNull();
    for (const link of screen.getAllByRole('link', { name: /Start agent session/ })) {
      expect(link).toHaveAttribute('href', '/operations?start=agent-session');
    }
    expect(screen.getByText('No agent has run against this project yet.')).toBeInTheDocument();
  });

  it('leaves the Session column a real share of the History table (U1)', async () => {
    // Tailwind's w-N is N × 4px. The fixed columns once summed to the whole
    // content width at a 1,126px window, so SESSION was 0px wide.
    const fixedPx = Object.values(HISTORY_COLUMN_WIDTHS)
      .filter(Boolean)
      .reduce((sum, cls) => sum + Number(/^w-(\d+)$/.exec(cls)![1]) * 4, 0);
    expect(HISTORY_COLUMN_WIDTHS.session).toBe('');
    serve([], [legacyRun()]);
    renderPage();
    const table = await screen.findByTestId('runs-table');
    const minRem = Number(/min-w-\[(\d+)rem\]/.exec(table.className)![1]);
    expect(minRem * 16 - fixedPx).toBeGreaterThanOrEqual(160);
    // Every truncated cell says what it cut.
    for (const el of Array.from(table.querySelectorAll('.truncate'))) {
      const titled = el.closest('[title]') ?? el.querySelector('[title]');
      expect(titled, el.textContent ?? '').not.toBeNull();
    }
  });

  it('describes a session by what an agent does now, not plans or runs (U6)', async () => {
    serve([], []);
    renderPage();
    const empty = await screen.findByText(/A session lets an agent query this project/);
    expect(empty.textContent).toMatch(/propose tests on hosts/);
    expect(empty.textContent).not.toMatch(/plan or execution/);
  });

  it('lists one history row per session that opens the session page — every row the same, whenever it started', async () => {
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
    // The table is there before its rows are (5.351.0: the test read them in
    // the same tick as the table).
    const rows = await within(table).findAllByTestId('run-row');
    for (const link of within(rows[0]).getAllByRole('link', { name: 'Open agent session 60' })) {
      expect(link).toHaveAttribute('href', '/agent-sessions/60');
    }
    expect(within(rows[0]).getByText('ended by agent · 2 feedback')).toBeInTheDocument();
    expect(within(rows[0]).getByText('Ended')).toBeInTheDocument();
    expect(within(rows[0]).getByRole('link', { name: '3 tests proposed · 1 evidence record' }))
      .toHaveAttribute('href', '/agent-sessions/60');
    // A session from before the consolidation is a row like any other
    // (5.383.0): no kind badge, the same name for a session with no purpose,
    // the same state badge and the same work cell — it read "Assist",
    // "Project-wide", its raw status and a dash.
    expect(within(rows[1]).queryByText('Assist')).toBeNull();
    expect(within(rows[1]).queryByText('Project-wide')).toBeNull();
    expect(within(rows[1]).getByText('Agent session')).toBeInTheDocument();
    expect(within(rows[1]).getByText('Ended')).toBeInTheDocument();
    const olderLinks = within(rows[1]).getAllByRole('link');
    expect(olderLinks.length).toBeGreaterThan(0);
    for (const link of olderLinks) expect(link).toHaveAttribute('href', '/agent-sessions/42');
    expect(within(rows[1]).getAllByRole('link', { name: 'Open agent session 42' }).length).toBeGreaterThan(0);
    expect(within(rows[1]).getByText('claude-opus-4-7 · claude-code')).toBeInTheDocument();
    // The two rows have the same cells.
    expect(rows[1].querySelectorAll('td')).toHaveLength(rows[0].querySelectorAll('td').length);
    // And the history offers no filter by kind.
    expect(screen.queryByLabelText('Filter sessions by kind')).toBeNull();
  });

  it('an old session still stored active, with nothing left to act on it, reads Expired like any other', async () => {
    const stuck = legacyRun({
      status: 'active', completed_at: null, key_expires_at: null, renewable_until: ago(24 * HOUR), key_state: 'ended',
      can_end: true, can_resume: false,
    });
    serve([], [stuck]);
    renderPage();
    const [row] = await within(await screen.findByTestId('runs-table')).findAllByTestId('run-row');
    expect(within(row).getByText('Expired')).toBeInTheDocument();
    expect(within(row).getByText('key expired · past its lifetime')).toBeInTheDocument();
    expect(within(row).queryByText('Stalled')).toBeNull();
    // What the server says the routes accept is what is offered: End, no Resume.
    expect(within(row).getByRole('button', { name: /End/ })).toBeInTheDocument();
    expect(within(row).queryByRole('button', { name: /Resume/ })).toBeNull();
  });

  it('offers the model filter once an agent has reported one, and passes it to the API', async () => {
    const user = userEvent.setup();
    serve([], [legacyRun()]);
    renderPage();
    await screen.findByTestId('runs-table');
    expect(screen.queryByLabelText('Filter sessions by model')).not.toBeInTheDocument();

    mockedApi.getAgentSessionSummary.mockResolvedValue(MODEL_SUMMARY);
    await user.click(screen.getByRole('button', { name: /Refresh agent sessions/i }));
    await chooseModel(user);
    // The roll-up is one count per model and client: no column per kind.
    const rollup = screen.getByText('Activity by agent / model').closest('section') ?? document.body;
    expect(within(rollup as HTMLElement).queryByText('Older assist')).toBeNull();
    await waitFor(() =>
      expect(mockedApi.listAgentSessions).toHaveBeenCalledWith(
        1, expect.objectContaining({ model: 'claude-opus-4-7' }), { signal: expect.any(AbortSignal) },
      ),
    );
  });

  // N7 — a slow response for an older filter must not overwrite the newer one.
  it('keeps the newest filter’s history when an older response arrives last', async () => {
    const user = userEvent.setup();
    let releaseOld!: () => void;
    const oldDone = new Promise<void>((res) => { releaseOld = res; });
    mockedApi.getAgentSessionSummary.mockResolvedValue(MODEL_SUMMARY);
    mockedApi.listAgentSessions.mockImplementation(async (_projectId: number, filters: Record<string, unknown> = {}) => {
      if (isLiveCall(filters)) {
        return { project_id: 1, sessions: [], total: 0 };
      }
      if (filters.model === MODEL) {
        return { project_id: 1, sessions: [legacyRun({ id: 43, purpose: 'Newer filter' })], total: 1 };
      }
      await oldDone;
      return { project_id: 1, sessions: [legacyRun({ purpose: 'Older filter' })], total: 1 };
    });
    renderPage();

    await chooseModel(user);
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
      busiest_sessions: [{ session_id: 72, calls: 199 }],
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
      summary: [{ generated_by_model: null, generated_by_tool: null, total: 5 }],
    });
    renderPage();
    await screen.findByText(/No agent has reported its model or client yet/);
    expect(screen.queryByText('Activity by agent / model')).not.toBeInTheDocument();
  });

  // Defect 1.7 — the history read "0 of 0 shown" before its first answer.
  it('does not count the history before it has answered: "…", then the count', async () => {
    let answer: (value: unknown) => void = () => undefined;
    mockedApi.listAgentSessions.mockImplementation((_projectId: number, filters: Record<string, unknown> = {}) => (
      isLiveCall(filters)
        ? Promise.resolve({ project_id: 1, sessions: [], total: 0 })
        : new Promise((resolve) => { answer = resolve; })
    ));
    renderPage();

    await screen.findByText('No agent session is live');
    expect(screen.getByTestId('history-count')).toHaveTextContent('…');
    expect(screen.queryByText(/0 of 0 shown/)).not.toBeInTheDocument();

    answer({ project_id: 1, sessions: [legacyRun()], total: 7 });
    await waitFor(() => expect(screen.getByTestId('history-count')).toHaveTextContent('1 of 7 shown'));
  });

  it('says "—", not a count, for a history that could not be read', async () => {
    mockedApi.listAgentSessions.mockImplementation(async (_projectId: number, filters: Record<string, unknown> = {}) => {
      if (isLiveCall(filters)) return { project_id: 1, sessions: [], total: 0 };
      throw new Error('boom');
    });
    renderPage();

    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.getByTestId('history-count')).toHaveTextContent('—');
    expect(screen.queryByText(/0 of 0 shown/)).not.toBeInTheDocument();
  });
});

// B15 — the history's filters are in the URL.
describe('Agent Sessions — filters in the URL', () => {
  const Where = () => <output data-testid="where">{useSearchParams()[0].toString()}</output>;
  const renderAt = (url: string) => render(
    <MemoryRouter initialEntries={[url]}><ProjectActivity /><Where /></MemoryRouter>,
  );

  beforeEach(() => {
    project.my_role = 'analyst';
    mockedApi.listAgentSessions.mockReset();
    mockedApi.getAgentSessionSummary.mockReset();
    mockedApi.getAgentSessionSummary.mockResolvedValue(MODEL_SUMMARY);
    mockedApi.getAgentActivitySummary.mockReset();
    mockedApi.getAgentActivitySummary.mockResolvedValue(emptyActivity);
  });

  it('a link with model and tool restores them and asks the server for them', async () => {
    serve([], [legacyRun()]);
    renderAt('/agent-activity?model=claude-opus-4-7&tool=claude-code');
    await screen.findByTestId('runs-table');
    expect(mockedApi.listAgentSessions).toHaveBeenCalledWith(
      1,
      { model: 'claude-opus-4-7', tool: 'claude-code', limit: 200 },
      { signal: expect.any(AbortSignal) },
    );
    // The chosen model's name is an option once the roll-up has named it
    // (5.351.0: the test read it in the same tick as the table).
    await waitFor(() => expect(screen.getByLabelText('Filter sessions by model')).toHaveTextContent('claude-opus-4-7'));
    expect(screen.getByLabelText('Filter sessions by client')).toHaveTextContent('claude-code');
  });

  it('choosing a filter writes it, and Clear filters removes both at once', async () => {
    const user = userEvent.setup();
    serve([], []);
    renderAt('/agent-activity?model=claude-opus-4-7&tool=claude-code');
    await user.click(await screen.findByRole('button', { name: 'Clear filters' }));
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe(''));

    await user.click(await screen.findByLabelText('Filter sessions by model'));
    await user.click(await screen.findByRole('option', { name: 'claude-opus-4-7' }));
    await waitFor(() => expect(screen.getByTestId('where').textContent).toBe('model=claude-opus-4-7'));
  });

  // 5.383.0 — a session has no kind: an older link's `?kind=` is not read
  // (it selected "Legacy assist" or the consolidated sessions), so no request
  // carries one and the history is not said to be filtered.
  it.each(['assist', 'project', 'recon'])('does not read ?kind=%s from an older link', async (kind) => {
    serve([], [legacyRun()]);
    renderAt(`/agent-activity?kind=${kind}`);
    await within(await screen.findByTestId('runs-table')).findAllByTestId('run-row');
    expect(mockedApi.listAgentSessions.mock.calls.every((c) => c[0] === 1)).toBe(true);
    const asked = mockedApi.listAgentSessions.mock.calls.map((c) => c[1]);
    expect(asked.length).toBeGreaterThan(0);
    expect(asked.every((f) => !('kind' in f))).toBe(true);
    expect(asked.filter((f) => f.status !== 'active')).toEqual([{ limit: 200 }]);
    expect(screen.queryByRole('button', { name: 'Clear filters' })).toBeNull();
  });
});

/**
 * The history as a LIST: 200 sessions at a time, "Load older sessions" for
 * the next 200.  What the reader sees was pinned here while the page kept a
 * growing `limit` of its own, and held through its move onto
 * `hooks/useListQuery` (plan B27) — the server below answers either way of
 * asking (a longer `limit`, or the next `offset`).
 */
describe('Agent Sessions — the history a page at a time', () => {
  type Filters = { model?: string; status?: string; limit?: number; offset?: number };
  const ended = (n: number) => session({
    id: 5000 - n, status: 'ended', end_reason: 'agent', key_expires_at: null, can_end: false, can_resume: false,
    purpose: `session ${n}`,
  });
  /** `count` ended sessions, newest first, cut by `limit` / `offset`. */
  const historyOf = (count: number, filters: Filters) => {
    const from = filters.offset ?? 0;
    const sessions = Array.from({ length: count }, (_, i) => ended(i + 1)).slice(from, from + (filters.limit ?? 200));
    return { project_id: 1, sessions, total: count };
  };
  const isLive = (filters: Filters) => isLiveCall(filters);
  const count = () => screen.getByTestId('history-count').textContent;
  const older = () => screen.queryByRole('button', { name: 'Load older sessions' });

  beforeEach(() => {
    project.my_role = 'analyst';
    mockedApi.listAgentSessions.mockReset();
    mockedApi.getAgentSessionSummary.mockReset();
    mockedApi.getAgentSessionSummary.mockResolvedValue({ project_id: 1, summary: [] });
    mockedApi.getAgentActivitySummary.mockReset();
    mockedApi.getAgentActivitySummary.mockResolvedValue(emptyActivity);
  });

  it('lists the 200 newest, and "Load older sessions" adds the rest beneath them — then is gone', async () => {
    mockedApi.listAgentSessions.mockImplementation(async (_projectId: number, filters: Filters = {}) => (
      isLive(filters) ? { project_id: 1, sessions: [], total: 0 } : historyOf(250, filters)
    ));
    renderPage();
    await waitFor(() => expect(count()).toBe('200 of 250 shown'));
    expect(screen.getAllByTestId('run-row')).toHaveLength(200);

    await userEvent.setup().click(await screen.findByRole('button', { name: 'Load older sessions' }));
    await waitFor(() => expect(count()).toBe('250 of 250 shown'));
    const rows = screen.getAllByTestId('run-row');
    expect(rows).toHaveLength(250);
    // In the server's order, the older ones after the ones that were shown.
    expect(within(rows[0]).getByText('session 1')).toBeInTheDocument();
    expect(within(rows[199]).getByText('session 200')).toBeInTheDocument();
    expect(within(rows[249]).getByText('session 250')).toBeInTheDocument();
    await waitFor(() => expect(older()).toBeNull());
  });

  it('offers nothing older when every session is listed', async () => {
    mockedApi.listAgentSessions.mockImplementation(async (_projectId: number, filters: Filters = {}) => (
      isLive(filters) ? { project_id: 1, sessions: [], total: 0 } : historyOf(3, filters)
    ));
    renderPage();
    await waitFor(() => expect(count()).toBe('3 of 3 shown'));
    expect(older()).toBeNull();
  });

  it('the rows on screen stay while another filter loads, with their count, then give way to its own', async () => {
    const user = userEvent.setup();
    let answer: (value: unknown) => void = () => undefined;
    mockedApi.getAgentSessionSummary.mockResolvedValue(MODEL_SUMMARY);
    mockedApi.listAgentSessions.mockImplementation((_projectId: number, filters: Filters = {}) => {
      if (isLive(filters)) return Promise.resolve({ project_id: 1, sessions: [], total: 0 });
      if (filters.model === MODEL) return new Promise((resolve) => { answer = resolve; });
      return Promise.resolve(historyOf(2, filters));
    });
    renderPage();
    await waitFor(() => expect(count()).toBe('2 of 2 shown'));

    await chooseModel(user);
    await waitFor(() => expect(mockedApi.listAgentSessions).toHaveBeenCalledWith(
      1, expect.objectContaining({ model: MODEL }), { signal: expect.any(AbortSignal) },
    ));
    expect(screen.getByText('session 1')).toBeInTheDocument();
    expect(count()).toBe('2 of 2 shown');

    answer({ project_id: 1, sessions: [legacyRun({ purpose: 'Only that model' })], total: 1 });
    expect(await screen.findByText('Only that model')).toBeInTheDocument();
    expect(screen.queryByText('session 1')).toBeNull();
    expect(count()).toBe('1 of 1 shown');
  });

  // --- What the move onto the list hook changed ------------------------------
  const historyCalls = () => mockedApi.listAgentSessions.mock.calls
    .map((call) => call[1] as Filters).filter((filters) => !isLive(filters));

  // It grew ONE answer by 200 a click; the fifth asked for 1,200 and the
  // route refuses more than 1,000.
  it('asks for older sessions by offset, 200 at a time — never one longer answer', async () => {
    mockedApi.listAgentSessions.mockImplementation(async (_projectId: number, filters: Filters = {}) => (
      isLive(filters) ? { project_id: 1, sessions: [], total: 0 } : historyOf(450, filters)
    ));
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(count()).toBe('200 of 450 shown'));
    await user.click(await screen.findByRole('button', { name: 'Load older sessions' }));
    await waitFor(() => expect(count()).toBe('400 of 450 shown'));
    await user.click(await screen.findByRole('button', { name: 'Load older sessions' }));
    await waitFor(() => expect(count()).toBe('450 of 450 shown'));
    expect(historyCalls()).toEqual([{ limit: 200 }, { limit: 200, offset: 200 }, { limit: 200, offset: 400 }]);
    expect(screen.queryByText(/newest are listed/)).toBeNull();
  });

  it('a failed "Load older" is said, and the sessions already listed stay; the next attempt adds the older ones', async () => {
    let fail = true;
    mockedApi.listAgentSessions.mockImplementation(async (_projectId: number, filters: Filters = {}) => {
      if (isLive(filters)) return { project_id: 1, sessions: [], total: 0 };
      if (filters.offset && fail) throw new Error('boom');
      return historyOf(250, filters);
    });
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(count()).toBe('200 of 250 shown'));

    await user.click(await screen.findByRole('button', { name: 'Load older sessions' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Failed to load agent sessions.');
    expect(count()).toBe('200 of 250 shown');
    expect(screen.getAllByTestId('run-row')).toHaveLength(200);

    fail = false;
    await user.click(await screen.findByRole('button', { name: 'Load older sessions' }));
    await waitFor(() => expect(count()).toBe('250 of 250 shown'));
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('another filter starts from its own newest 200, however far the reader had gone', async () => {
    mockedApi.getAgentSessionSummary.mockResolvedValue(MODEL_SUMMARY);
    mockedApi.listAgentSessions.mockImplementation(async (_projectId: number, filters: Filters = {}) => (
      isLive(filters) ? { project_id: 1, sessions: [], total: 0 } : historyOf(450, filters)
    ));
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(count()).toBe('200 of 450 shown'));
    await user.click(await screen.findByRole('button', { name: 'Load older sessions' }));
    await waitFor(() => expect(count()).toBe('400 of 450 shown'));

    await chooseModel(user);
    await waitFor(() => expect(historyCalls()[historyCalls().length - 1]).toEqual({ model: MODEL, limit: 200 }));
    await waitFor(() => expect(count()).toBe('200 of 450 shown'));
  });

  it('a failed read on another filter is said, counted as "—", and leaves none of the rows of the filter that was left', async () => {
    const user = userEvent.setup();
    mockedApi.getAgentSessionSummary.mockResolvedValue(MODEL_SUMMARY);
    mockedApi.listAgentSessions.mockImplementation(async (_projectId: number, filters: Filters = {}) => {
      if (isLive(filters)) return { project_id: 1, sessions: [], total: 0 };
      if (filters.model === MODEL) throw new Error('boom');
      return historyOf(2, filters);
    });
    renderPage();
    await waitFor(() => expect(count()).toBe('2 of 2 shown'));

    await chooseModel(user);
    expect(await screen.findByRole('alert')).toHaveTextContent('Failed to load agent sessions.');
    expect(count()).toBe('—');
    expect(screen.queryByText('session 1')).toBeNull();
    expect(screen.queryAllByTestId('run-row')).toHaveLength(0);
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
