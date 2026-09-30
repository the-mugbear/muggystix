/**
 * One agent session's page (`/agent-sessions/:sessionId`, v5.312.0): where it
 * stands, its controls, the work it opened, the notes it wrote and its calls —
 * keyed by the session id, reading notes by the detail row's id it names.
 */
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import AgentSessionDetail from '../../pages/AgentSessionDetail';
import { TooltipProvider } from '../../components/ui/tooltip';

const params = vi.hoisted(() => ({ current: {} as Record<string, string | undefined> }));
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useParams: () => params.current };
});

const getAgentSession = vi.fn();
const getAssistSession = vi.fn();
const endAgentSession = vi.fn();
vi.mock('../../services/api', () => ({
  getAgentSession: (...a: unknown[]) => getAgentSession(...a),
  getAssistSession: (...a: unknown[]) => getAssistSession(...a),
  endAgentSession: (...a: unknown[]) => endAgentSession(...a),
  resumeAgentSession: vi.fn(),
  getAssistSessionApiActivity: vi.fn().mockResolvedValue({ total: 0, items: [] }),
  getPlanApiActivity: vi.fn(),
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));

const HOUR = 1000 * 60 * 60;
const ago = (ms: number) => new Date(Date.now() - ms).toISOString();
const ahead = (ms: number) => new Date(Date.now() + ms).toISOString();

const row = (over: Record<string, unknown> = {}) => ({
  kind: 'project',
  id: 72,
  project_id: 1,
  agent_id: 7,
  agent_name: "alice's-agent",
  user_id: 3,
  user_username: 'alice',
  user_full_name: 'Alice Analyst',
  status: 'active',
  started_at: ago(2 * HOUR),
  completed_at: null,
  generated_by_model: 'claude-opus-5-5',
  generated_by_tool: 'claude-code',
  purpose: 'map the DMZ',
  key_expires_at: ahead(10 * HOUR),
  renewable_until: ahead(5 * 24 * HOUR),
  end_reason: null,
  feedback_count: 1,
  last_activity_at: ago(60 * 1000),
  operator_role: 'analyst',
  assist_session_id: 52,
  phases: [
    { kind: 'plan', id: 17, status: 'completed', label: 'SMB sweep', scope_id: null, test_plan_id: 17, started_at: ago(HOUR) },
    { kind: 'execution', id: 46, status: 'paused', label: 'SMB sweep', scope_id: null, test_plan_id: 17, started_at: ago(HOUR / 2) },
  ],
  can_end: true,
  can_resume: true,
  ...over,
});

const review = {
  id: 52,
  agent_session_id: 72,
  project_id: 1,
  purpose: 'map the DMZ',
  status: 'ended', // the detail row's own status is NOT what the page shows
  started_by_id: 3,
  started_by_username: 'alice',
  started_at: ago(2 * HOUR),
  ended_at: null,
  last_activity_at: ago(60 * 1000),
  key_expires_at: null,
  call_count: 14,
  note_count: 1,
  connection: 'mcp',
  first_call_at: ago(HOUR),
  agent_model: null,
  agent_tool: null,
  prompt_version: '2.13.1',
  feedback_count: 1,
  notes: [{
    id: 501, host_id: 88, host_ip: '10.0.0.9', hostname: 'ftp01',
    body: 'Anonymous FTP login accepted on 21.', status: 'open', created_at: ago(HOUR),
  }],
};

const renderAt = (sessionId: string) => {
  params.current = { sessionId };
  return render(
    <MemoryRouter initialEntries={[`/agent-sessions/${sessionId}`]}>
      <TooltipProvider>
        <AgentSessionDetail />
      </TooltipProvider>
    </MemoryRouter>,
  );
};

describe('AgentSessionDetail', () => {
  beforeEach(() => {
    getAgentSession.mockReset().mockResolvedValue(row());
    getAssistSession.mockReset().mockResolvedValue(review);
    endAgentSession.mockReset().mockResolvedValue(undefined);
  });

  it('reads the session by its id and its notes by the detail row it names', async () => {
    renderAt('72');
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent('Agent session #72');
    expect(getAgentSession).toHaveBeenCalledWith(72);
    await waitFor(() => expect(getAssistSession).toHaveBeenCalledWith(52));
    expect(await screen.findByText('Anonymous FTP login accepted on 21.')).toBeInTheDocument();
    expect(screen.getByText('map the DMZ')).toBeInTheDocument();
  });

  it('says where it stands from the session, not the detail row', async () => {
    renderAt('72');
    await screen.findByText('Live');
    expect(screen.queryByText('Ended')).not.toBeInTheDocument();
    expect(screen.getByText(/key valid until/)).toBeInTheDocument();
    expect(screen.getByText('Analyst role')).toBeInTheDocument();
    expect(await screen.findByText('MCP verified')).toBeInTheDocument();
    expect(screen.getByText('Running for')).toBeInTheDocument();
    // Attribution: the model the agent reported, and its client (from the
    // MCP handshake). The operator's machine is no longer a fact.
    expect(screen.getByText('claude-opus-5-5')).toBeInTheDocument();
    expect(screen.getByText('claude-code')).toBeInTheDocument();
    expect(screen.queryByText(/Operator’s machine/)).not.toBeInTheDocument();
  });

  it('lists the work it opened, each linking to its page', async () => {
    renderAt('72');
    const table = await screen.findByTestId('session-phases');
    expect(within(table).getByRole('link', { name: 'Plan #17' })).toHaveAttribute('href', '/test-plans/17');
    expect(within(table).getByRole('link', { name: 'Execution #46' })).toHaveAttribute('href', '/executions/46');
    expect(within(table).getAllByText('SMB sweep')).toHaveLength(2);
  });

  it('ends the session from its own page', async () => {
    const user = userEvent.setup();
    renderAt('72');
    await user.click(await screen.findByRole('button', { name: /^End$/ }));
    await screen.findByText(/Agent still connected\? Paste this to it first/);
    await user.click(screen.getByRole('button', { name: 'End session' }));
    await waitFor(() => expect(endAgentSession).toHaveBeenCalledWith(72));
    // Re-read after ending.
    await waitFor(() => expect(getAgentSession).toHaveBeenCalledTimes(2));
  });

  it('explains who can act when the caller cannot', async () => {
    getAgentSession.mockResolvedValue(row({ can_end: false, can_resume: false }));
    renderAt('72');
    await screen.findByText(/Only Alice Analyst or a project admin can end this session/);
    expect(screen.queryByRole('button', { name: /^End$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Resume/ })).not.toBeInTheDocument();
  });

  it('says an ended session left work open', async () => {
    getAgentSession.mockResolvedValue(row({
      status: 'ended', end_reason: 'operator', key_expires_at: null,
      completed_at: ago(HOUR), can_end: false, can_resume: false,
    }));
    renderAt('72');
    expect(await screen.findByText(/an execution run it opened is still open/)).toBeInTheDocument();
    expect(screen.getByText('Ran for')).toBeInTheDocument();
    expect(screen.getByText('ended by operator · 1 feedback')).toBeInTheDocument();
  });

  // 5.314.1 — seen in the walkthrough: session #73 left a draft plan and the
  // page called it stranded work to abandon. A draft is a resting state.
  it('does not call a draft plan left by an ended session stranded', async () => {
    getAgentSession.mockResolvedValue(row({
      status: 'ended', end_reason: 'agent', key_expires_at: null,
      completed_at: ago(HOUR), can_end: false, can_resume: false,
      phases: [{ kind: 'plan', id: 63, status: 'draft', label: 'draft', scope_id: null, test_plan_id: 63, started_at: ago(HOUR) }],
    }));
    renderAt('72');
    expect(await screen.findByText('The plans and execution runs this session opened, each on its own page.')).toBeInTheDocument();
    expect(screen.queryByText(/still open/)).not.toBeInTheDocument();
  });

  it('never presents the agent’s name as its model', async () => {
    getAgentSession.mockResolvedValue(row({ generated_by_model: null }));
    getAssistSession.mockResolvedValue({ ...review, agent_model: null });
    renderAt('72');
    const model = (await screen.findByText('Model')).parentElement!;
    expect(model).toHaveTextContent('not reported');
    expect(model).not.toHaveTextContent("alice's-agent");
  });

  it('never shows the previous session’s notes under the next session', async () => {
    const view = renderAt('72');
    expect(await screen.findByText('Anonymous FTP login accepted on 21.')).toBeInTheDocument();

    // Back/Forward to another session whose notes fail to load.
    getAgentSession.mockResolvedValue(row({ id: 80, assist_session_id: 60 }));
    getAssistSession.mockRejectedValue(new Error('boom'));
    params.current = { sessionId: '80' };
    view.rerender(
      <MemoryRouter initialEntries={['/agent-sessions/80']}>
        <TooltipProvider>
          <AgentSessionDetail />
        </TooltipProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent('Agent session #80');
    await waitFor(() => expect(getAssistSession).toHaveBeenCalledWith(60));
    await screen.findByText(/Could not load the session’s notes and calls/);
    expect(screen.queryByText('Anonymous FTP login accepted on 21.')).not.toBeInTheDocument();
  });

  it('reports a session that is not found', async () => {
    getAgentSession.mockRejectedValue(new Error('Agent session not found in this project'));
    renderAt('9999');
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(getAssistSession).not.toHaveBeenCalled();
  });
});
