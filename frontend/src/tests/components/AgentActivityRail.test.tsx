/**
 * The agent-activity rail in the top bar: a small trigger whose label says
 * how many agent sessions are live, and a popover with the latest sessions.
 * It is the one thing on every page that POLLS the sessions.
 *
 * Pinned: the two questions it asks (project first, the filters, the query's
 * signal) and how often — every minute while a session is live or the popover
 * is open, every five minutes otherwise, never in a hidden tab, once on
 * return —, what the reader sees from the answers, what a failed poll leaves
 * on screen, and that nothing is asked without a project or a signed-in user.
 *
 * Time is faked: `pass(ms)` moves it.  Boundaries are asserted with slack
 * (55 s "not yet", 65 s "by now"), never to the millisecond.
 */
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { AgentSessionFilters, AgentSessionListResponse, AgentSessionRow } from '../../services/api';

const api = vi.hoisted(() => ({ listAgentSessions: vi.fn() }));
vi.mock('../../services/api', () => api);

const navigate = vi.hoisted(() => vi.fn());
vi.mock('react-router-dom', async () => ({
  ...(await vi.importActual<typeof import('react-router-dom')>('react-router-dom')),
  useNavigate: () => navigate,
}));
const project = vi.hoisted(() => ({ current: { id: 1, name: 'Demo' } as { id: number; name: string } | null }));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: project.current }),
}));
const auth = vi.hoisted(() => ({ isAuthenticated: true }));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ isAuthenticated: auth.isAuthenticated, user: { id: 1, role: 'member' } }),
}));

import AgentActivityRail from '../../components/AgentActivityRail';

const NOW = new Date('2026-10-09T12:00:00Z');
const SECOND = 1000;
const MINUTE = 60 * SECOND;
const ACTIVE_Q: AgentSessionFilters = { status: 'active', kind: 'project', limit: 1 };
const RECENT_Q: AgentSessionFilters = { limit: 8 };
const LONG_PURPOSE = `Check ${'p'.repeat(194)}`;                // 200 characters

const row = (over: Partial<AgentSessionRow> = {}): AgentSessionRow => ({
  kind: 'project', id: 7, project_id: 1, status: 'active', purpose: 'Sweep the DMZ',
  generated_by_model: 'claude-opus', user_username: 'ana', started_at: '2026-10-09T11:55:00Z',
  key_expires_at: '2026-10-10T12:00:00Z', renewable_until: '2026-10-12T12:00:00Z', ...over,
});

/** The server, as far as the rail is concerned. */
const server = {
  live: 0,
  recent: [] as AgentSessionRow[],
  fail: false,
};
const isActiveQuestion = (filters: AgentSessionFilters) => filters.status === 'active';
const answerFor = (projectId: number, filters: AgentSessionFilters): AgentSessionListResponse => (
  isActiveQuestion(filters)
    ? { project_id: projectId, sessions: server.recent.filter((r) => r.status === 'active').slice(0, 1), total: server.live }
    : { project_id: projectId, sessions: server.recent, total: server.recent.length }
);

/** How many times each question was asked so far. */
const asked = () => {
  const calls = api.listAgentSessions.mock.calls as Array<[number, AgentSessionFilters, unknown]>;
  return {
    active: calls.filter(([, filters]) => isActiveQuestion(filters)).length,
    recent: calls.filter(([, filters]) => !isActiveQuestion(filters)).length,
  };
};
const pass = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms); }); };
const setVisibility = (state: 'visible' | 'hidden') => {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true });
  act(() => { window.dispatchEvent(new Event('visibilitychange')); });
};

/** Mounts the rail and waits for its first answers. */
const show = async () => {
  const page = render(<AgentActivityRail />);
  await pass(50);
  return page;
};
const trigger = () => screen.queryByRole('button', { name: /^Agent activity/ });
const openPopover = async () => {
  fireEvent.click(trigger() as HTMLElement);
  await pass(50);
  return screen.getByRole('dialog');
};
/** The popover's session rows, as the reader reads them. */
const rowTexts = (popover: HTMLElement): string[] => within(popover).queryAllByRole('listitem')
  .map((li) => li.textContent ?? '');

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  vi.clearAllMocks();
  project.current = { id: 1, name: 'Demo' };
  auth.isAuthenticated = true;
  server.live = 0;
  server.recent = [];
  server.fail = false;
  setVisibility('visible');
  api.listAgentSessions.mockImplementation(async (projectId: number, filters: AgentSessionFilters) => {
    if (server.fail) throw new Error('sessions unavailable');
    return answerFor(projectId, filters);
  });
});
afterEach(() => {
  setVisibility('visible');
  vi.useRealTimers();
});

describe('AgentActivityRail — what it asks', () => {
  it('two questions on mount, for the project on screen: the live count and the latest eight', async () => {
    server.live = 2;
    server.recent = [row(), row({ id: 8 })];
    await show();

    expect(api.listAgentSessions).toHaveBeenCalledTimes(2);
    expect(api.listAgentSessions).toHaveBeenCalledWith(1, ACTIVE_Q, { signal: expect.any(AbortSignal) });
    expect(api.listAgentSessions).toHaveBeenCalledWith(1, RECENT_Q, { signal: expect.any(AbortSignal) });
  });

  it('nothing with no project selected — not on mount, not later, not on return to the tab', async () => {
    project.current = null;
    await show();
    await pass(11 * MINUTE);
    setVisibility('hidden');
    setVisibility('visible');
    await pass(50);

    expect(api.listAgentSessions).not.toHaveBeenCalled();
    expect(trigger()).toBeNull();
  });

  it('nothing while nobody is signed in', async () => {
    auth.isAuthenticated = false;
    await show();
    await pass(11 * MINUTE);
    expect(api.listAgentSessions).not.toHaveBeenCalled();
    expect(trigger()).toBeNull();
  });

  it('another project is asked about by its own id, and the previous project’s count is not shown for it', async () => {
    server.live = 2;
    server.recent = [row()];
    const page = await show();
    expect(trigger()).toHaveAccessibleName('Agent activity — 2 active sessions in this project');

    // Project 2 has not answered yet.
    api.listAgentSessions.mockImplementation(() => new Promise(() => {}));
    project.current = { id: 2, name: 'Other' };
    page.rerender(<AgentActivityRail />);
    await pass(50);

    expect(api.listAgentSessions).toHaveBeenCalledWith(2, ACTIVE_Q, { signal: expect.any(AbortSignal) });
    expect(api.listAgentSessions).toHaveBeenCalledWith(2, RECENT_Q, { signal: expect.any(AbortSignal) });
    expect(trigger()).toHaveAccessibleName('Agent activity');
  });
});

describe('AgentActivityRail — how often', () => {
  it('every minute while a session is live', async () => {
    server.live = 1;
    server.recent = [row()];
    await show();
    expect(asked()).toEqual({ active: 1, recent: 1 });

    await pass(55 * SECOND);
    expect(asked()).toEqual({ active: 1, recent: 1 });
    await pass(10 * SECOND);
    expect(asked()).toEqual({ active: 2, recent: 2 });
    await pass(MINUTE);
    expect(asked()).toEqual({ active: 3, recent: 3 });
  });

  it('every five minutes while none is live', async () => {
    server.recent = [row({ status: 'completed' })];
    await show();

    await pass(4 * MINUTE + 55 * SECOND);
    expect(asked()).toEqual({ active: 1, recent: 1 });
    await pass(10 * SECOND);
    expect(asked()).toEqual({ active: 2, recent: 2 });
  });

  it('a project with no session on file is still asked about every five minutes, so a first session shows up', async () => {
    await show();
    expect(trigger()).toBeNull();

    server.live = 1;
    server.recent = [row()];
    await pass(5 * MINUTE + 5 * SECOND);
    expect(asked()).toEqual({ active: 2, recent: 2 });
    expect(trigger()).toHaveAccessibleName('Agent activity — 1 active session in this project');
  });

  it('a session that went live is noticed at the next slow poll, and polling then tightens to a minute', async () => {
    server.recent = [row({ status: 'completed' })];
    await show();
    server.live = 1;
    await pass(5 * MINUTE + 5 * SECOND);
    expect(trigger()).toHaveAccessibleName('Agent activity — 1 active session in this project');
    const before = asked();

    await pass(MINUTE + 5 * SECOND);
    expect(asked()).toEqual({ active: before.active + 1, recent: before.recent + 1 });
  });

  it('nothing in a hidden tab, and one read of each on return', async () => {
    server.live = 1;
    server.recent = [row()];
    await show();
    expect(asked()).toEqual({ active: 1, recent: 1 });

    setVisibility('hidden');
    await pass(10 * MINUTE);
    expect(asked()).toEqual({ active: 1, recent: 1 });

    setVisibility('visible');
    await pass(50);
    expect(asked()).toEqual({ active: 2, recent: 2 });
    // …and then the ordinary minute again, not a burst.
    await pass(50 * SECOND);
    expect(asked()).toEqual({ active: 2, recent: 2 });
    await pass(20 * SECOND);
    expect(asked()).toEqual({ active: 3, recent: 3 });
  });

  it('opening the popover reads both at once, and keeps a minute’s pace while it is open even with none live', async () => {
    server.recent = [row({ status: 'completed' })];
    await show();
    expect(asked()).toEqual({ active: 1, recent: 1 });

    await openPopover();
    expect(asked()).toEqual({ active: 2, recent: 2 });

    await pass(MINUTE + 5 * SECOND);
    expect(asked()).toEqual({ active: 3, recent: 3 });
  });

  it('Refresh in the popover reads both again', async () => {
    server.live = 1;
    server.recent = [row()];
    await show();
    const popover = await openPopover();
    const before = asked();

    server.recent = [row(), row({ id: 9, purpose: 'A second one' })];
    fireEvent.click(within(popover).getByRole('button', { name: 'Refresh agent activity' }));
    await pass(50);
    expect(asked()).toEqual({ active: before.active + 1, recent: before.recent + 1 });
    expect(rowTexts(popover)).toHaveLength(2);
  });

  it('stops asking once it is gone from the page', async () => {
    server.live = 1;
    server.recent = [row()];
    const page = await show();
    page.unmount();
    await pass(10 * MINUTE);
    expect(asked()).toEqual({ active: 1, recent: 1 });
  });
});

describe('AgentActivityRail — what the reader sees', () => {
  it('says how many sessions are live — one, or several — from the count, not from the rows listed', async () => {
    server.live = 1;
    server.recent = [row()];
    await show();
    expect(trigger()).toHaveAccessibleName('Agent activity — 1 active session in this project');

    // Twelve live, of which the list shows its eight.
    server.live = 12;
    server.recent = Array.from({ length: 8 }, (_, i) => row({ id: 100 + i }));
    await pass(MINUTE + 5 * SECOND);
    expect(trigger()).toHaveAccessibleName('Agent activity — 12 active sessions in this project');
    const popover = await openPopover();
    expect(within(popover).getByText('12 active')).toBeInTheDocument();
    expect(rowTexts(popover)).toHaveLength(8);
  });

  it('with sessions on file but none live, the trigger is there without a count', async () => {
    server.recent = [row({ status: 'completed' })];
    await show();
    expect(trigger()).toHaveAccessibleName('Agent activity');
    const popover = await openPopover();
    expect(within(popover).queryByText(/\d+ active/)).toBeNull();
  });

  it('a project with no agent session at all shows nothing in the top bar', async () => {
    await show();
    expect(asked()).toEqual({ active: 1, recent: 1 });
    expect(trigger()).toBeNull();
  });

  it('lists each session: what it is, its state, its purpose, the model, who started it and how long ago', async () => {
    server.live = 1;
    server.recent = [
      row(),
      row({ id: 8, status: 'completed', purpose: LONG_PURPOSE, generated_by_model: null, user_username: 'ben', started_at: '2026-10-09T10:00:00Z' }),
    ];
    await show();
    const popover = await openPopover();

    expect(rowTexts(popover)).toEqual([
      'Session #7· activeSweep the DMZclaude-opus · by ana · 5m ago',
      `Session #8· completed${LONG_PURPOSE}by ben · 2h ago`,
    ]);
    // A purpose too long for its line can still be read in full.
    expect(within(popover).getByTitle(LONG_PURPOSE)).toBeInTheDocument();
  });

  it('a row with nothing but its id and state — every optional value null — is still one clean line', async () => {
    server.recent = [
      row({ id: 3, kind: 'assist', status: 'ended', purpose: null, generated_by_model: null, user_username: null, started_at: null, key_expires_at: null, renewable_until: null }),
      // An assist row's purpose is not shown, whatever it holds.
      row({ id: 4, kind: 'assist', status: 'ended', purpose: 'not for this row', generated_by_model: null, user_username: null, started_at: null }),
    ];
    await show();
    const popover = await openPopover();
    expect(rowTexts(popover)).toEqual(['Assist #3· ended', 'Assist #4· ended']);
    expect(popover).not.toHaveTextContent(/null|undefined/);
  });

  it('a row opens its session’s page and closes the popover; the footer opens the sessions list', async () => {
    server.live = 1;
    server.recent = [row(), row({ id: 8 })];
    await show();
    let popover = await openPopover();
    fireEvent.click(within(popover).getByRole('button', { name: /Session #8/ }));
    await pass(50);
    expect(navigate).toHaveBeenCalledTimes(1);
    expect(navigate).toHaveBeenCalledWith('/agent-sessions/8');
    expect(screen.queryByRole('dialog')).toBeNull();

    popover = await openPopover();
    fireEvent.click(within(popover).getByRole('button', { name: 'Manage agent sessions' }));
    await pass(50);
    expect(navigate).toHaveBeenLastCalledWith('/agent-activity');
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('a live count with an empty list (the count and the list are two answers) keeps the trigger and says none are recent', async () => {
    server.live = 1;
    server.recent = [];
    await show();
    expect(trigger()).toHaveAccessibleName('Agent activity — 1 active session in this project');
    const popover = await openPopover();
    expect(within(popover).getByText('No recent agent sessions.')).toBeInTheDocument();
    expect(within(popover).getByText('1 active')).toBeInTheDocument();
  });
});

describe('AgentActivityRail — when a read fails', () => {
  it('a failed poll leaves the last reading on screen, and the next good one replaces it', async () => {
    server.live = 2;
    server.recent = [row(), row({ id: 8 })];
    await show();
    const popover = await openPopover();
    expect(rowTexts(popover)).toHaveLength(2);
    const before = asked();

    server.fail = true;
    await pass(MINUTE + 5 * SECOND);
    expect(asked()).toEqual({ active: before.active + 1, recent: before.recent + 1 });
    expect(trigger()).toHaveAccessibleName('Agent activity — 2 active sessions in this project');
    expect(within(popover).getByText('2 active')).toBeInTheDocument();
    expect(rowTexts(popover)).toHaveLength(2);
    expect(within(popover).queryByText('No recent agent sessions.')).toBeNull();

    server.fail = false;
    server.live = 3;
    server.recent = [row(), row({ id: 8 }), row({ id: 9 })];
    await pass(2 * MINUTE + 5 * SECOND);
    expect(trigger()).toHaveAccessibleName('Agent activity — 3 active sessions in this project');
    expect(rowTexts(popover)).toHaveLength(3);
  });

  it('asks half as often while the server is failing', async () => {
    server.live = 1;
    server.recent = [row()];
    await show();
    server.fail = true;
    await pass(MINUTE + 5 * SECOND);
    expect(asked()).toEqual({ active: 2, recent: 2 });           // this poll failed

    await pass(MINUTE + 30 * SECOND);
    expect(asked()).toEqual({ active: 2, recent: 2 });           // a minute later: not yet
    await pass(MINUTE);
    expect(asked()).toEqual({ active: 3, recent: 3 });           // two minutes after the failure
  });

  it('a first read that fails is not taken for "no sessions on file": the trigger stays, and Refresh asks again', async () => {
    server.fail = true;
    await show();
    expect(asked()).toEqual({ active: 1, recent: 1 });
    expect(trigger()).toHaveAccessibleName('Agent activity');

    server.fail = false;
    server.live = 1;
    server.recent = [row()];
    const popover = await openPopover();
    expect(trigger()).toHaveAccessibleName('Agent activity — 1 active session in this project');
    expect(rowTexts(popover)).toEqual(['Session #7· activeSweep the DMZclaude-opus · by ana · 5m ago']);
  });

  // DEFECT (AgentActivityRail.tsx:219-222): the popover prints "No recent
  // agent sessions." whenever it has no rows — also when the list could NOT
  // be read (and while the first read is still out).  "Could not be read" is
  // shown as "none" (frontend rules: "a failed section says so, never renders
  // as empty").  This is the correct behaviour; it fails today.
  it('when the sessions could not be read, the popover does not say there are none', async () => {
    server.fail = true;
    await show();
    const popover = await openPopover();
    expect(within(popover).queryByText('No recent agent sessions.')).toBeNull();
  });
});
