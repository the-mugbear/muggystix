/**
 * One agent session's page (`/agent-sessions/:sessionId`, v5.312.0): where it
 * stands, its controls, the tests it proposed, the notes it wrote and its calls —
 * all read by the session id, its only id (5.328.0; notes and calls were read
 * by a second id, the session's detail row).
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
const getAgentSessionNotes = vi.fn();
const getAgentSessionApiActivity = vi.fn();
const endAgentSession = vi.fn();
const listHostTests = vi.fn();
vi.mock('../../services/api', () => ({
  getAgentSession: (...a: unknown[]) => getAgentSession(...a),
  getAgentSessionNotes: (...a: unknown[]) => getAgentSessionNotes(...a),
  endAgentSession: (...a: unknown[]) => endAgentSession(...a),
  resumeAgentSession: vi.fn(),
  getAgentSessionApiActivity: (...a: unknown[]) => getAgentSessionApiActivity(...a),
  listHostTests: (...a: unknown[]) => listHostTests(...a),
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));
// The project the page is shown in (the session's own): every request names
// it first.
vi.mock('../../contexts/ProjectContext', () => ({ useProject: () => ({ currentProject: { id: 1 } }) }));

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
  prompt_version: '2.13.1',
  call_count: 14,
  note_count: 1,
  connection: 'mcp',
  first_call_at: ago(HOUR),
  host_test_count: 1,
  evidence_count: 4,
  can_end: true,
  can_resume: true,
  ...over,
});

const sessionTest = {
  id: 31, host_id: 5, host_ip: '10.0.0.5', tool: 'netexec', description: 'SMB signing',
  command: 'nxc smb {ip}', rationale: 'r', expected_result: null, references: null, target_fqdn: null,
  priority: 'high', label: 'SMB sweep', status: 'in_progress', assigned_to_id: null, assigned_to: null,
  created_by: 'Alice Analyst', source: 'agent', agent_session_id: 72, agent_model: null, agent_client: null,
  tester_summary: null, dismissed_reason: null, revision: 2, evidence_count: 4, created_at: ago(HOUR),
};

const notes = {
  total: 1,
  items: [{
    id: 501, host_id: 88, host_ip: '10.0.0.9', hostname: 'ftp01',
    body: 'Anonymous FTP login accepted on 21.', created_at: ago(HOUR),
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
    getAgentSessionNotes.mockReset().mockResolvedValue(notes);
    getAgentSessionApiActivity.mockReset().mockResolvedValue({ total: 0, items: [] });
    endAgentSession.mockReset().mockResolvedValue(undefined);
    listHostTests.mockReset().mockResolvedValue({ items: [sessionTest], total: 1, has_more: false });
  });

  it('reads the session, its notes and its calls by the one session id', async () => {
    renderAt('72');
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent('Agent session #72');
    expect(getAgentSession).toHaveBeenCalledWith(1, 72, expect.any(AbortSignal));
    await waitFor(() => expect(getAgentSessionNotes).toHaveBeenCalledWith(1, 72, undefined, expect.any(AbortSignal)));
    await waitFor(() => expect(getAgentSessionApiActivity).toHaveBeenCalled());
    expect(getAgentSessionApiActivity.mock.calls.every((c) => c[0] === 1 && c[1] === 72)).toBe(true);
    expect(await screen.findByText('Anonymous FTP login accepted on 21.')).toBeInTheDocument();
    expect(screen.getByText('map the DMZ')).toBeInTheDocument();
    // The call count is the row's own.
    expect(screen.getByText('API calls').parentElement).toHaveTextContent('14');
  });

  it('says how many notes there are when it shows only the newest', async () => {
    getAgentSessionNotes.mockResolvedValue({ ...notes, total: 73 });
    renderAt('72');
    expect(await screen.findByText('Showing the 1 most recent of 73.')).toBeInTheDocument();
  });

  it('shows a legacy assist session on the same page, with nothing to end or resume', async () => {
    getAgentSession.mockResolvedValue(row({
      kind: 'assist', id: 31, status: 'ended', key_expires_at: null, renewable_until: null,
      completed_at: ago(HOUR), can_end: false, can_resume: false, connection: 'curl',
    }));
    renderAt('31');
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent('Agent session #31');
    await waitFor(() => expect(getAgentSessionNotes).toHaveBeenCalledWith(1, 31, undefined, expect.any(AbortSignal)));
    expect(screen.getByText('Connected via curl')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^End$/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Resume/ })).not.toBeInTheDocument();
  });

  it('says where it stands', async () => {
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

  // 5.320.0 — the work a session leaves is the tests it proposed (each on
  // its host's page) and the evidence it recorded; it opened runs and plans
  // before.
  it('lists the tests it proposed, each opening the test on its host', async () => {
    renderAt('72');
    const table = await screen.findByTestId('session-tests');
    expect(listHostTests).toHaveBeenCalledWith(1, expect.objectContaining({ agent_session_id: 72 }), expect.any(AbortSignal));
    expect(within(table).getByRole('link', { name: '10.0.0.5' })).toHaveAttribute('href', '/hosts/5#host-test-31');
    expect(within(table).getByText('SMB signing')).toBeInTheDocument();
    expect(within(table).getByText('In progress')).toBeInTheDocument();
    expect(screen.getByText(/It recorded 4 evidence records\./)).toBeInTheDocument();
  });

  it('ends the session from its own page', async () => {
    const user = userEvent.setup();
    renderAt('72');
    await user.click(await screen.findByRole('button', { name: /^End$/ }));
    await screen.findByText(/Agent still connected\? Paste this to it first/);
    await user.click(screen.getByRole('button', { name: 'End session' }));
    await waitFor(() => expect(endAgentSession).toHaveBeenCalledWith(1, 72));
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

  it('reads an ended session as ended, with how long it ran', async () => {
    getAgentSession.mockResolvedValue(row({
      status: 'ended', end_reason: 'operator', key_expires_at: null,
      completed_at: ago(HOUR), can_end: false, can_resume: false,
    }));
    renderAt('72');
    expect(await screen.findByText('Ran for')).toBeInTheDocument();
    expect(screen.getByText('ended by operator · 1 feedback')).toBeInTheDocument();
    // Nothing is "left open" by an ended session any more: its tests and
    // evidence are project data, not stranded runs.
    expect(screen.queryByText(/still open|abandon/i)).not.toBeInTheDocument();
  });

  it('says so when the session proposed nothing', async () => {
    getAgentSession.mockResolvedValue(row({ host_test_count: 0, evidence_count: 0 }));
    listHostTests.mockResolvedValue({ items: [], total: 0, has_more: false });
    renderAt('72');
    expect(await screen.findByText(/this session has proposed no tests so far/)).toBeInTheDocument();
    expect(screen.getByText(/It recorded 0 evidence records\./)).toBeInTheDocument();
  });

  it('never presents the agent’s name as its model', async () => {
    getAgentSession.mockResolvedValue(row({ generated_by_model: null }));
    renderAt('72');
    const model = (await screen.findByText('Model')).parentElement!;
    expect(model).toHaveTextContent('not reported');
    expect(model).not.toHaveTextContent("alice's-agent");
  });

  it('never shows the previous session’s notes under the next session', async () => {
    const view = renderAt('72');
    expect(await screen.findByText('Anonymous FTP login accepted on 21.')).toBeInTheDocument();

    // Back/Forward to another session whose notes fail to load.
    getAgentSession.mockResolvedValue(row({ id: 80 }));
    getAgentSessionNotes.mockRejectedValue(new Error('boom'));
    params.current = { sessionId: '80' };
    view.rerender(
      <MemoryRouter initialEntries={['/agent-sessions/80']}>
        <TooltipProvider>
          <AgentSessionDetail />
        </TooltipProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByRole('heading', { level: 1 })).toHaveTextContent('Agent session #80');
    await waitFor(() => expect(getAgentSessionNotes).toHaveBeenCalledWith(1, 80, undefined, expect.any(AbortSignal)));
    await screen.findByText(/Could not load the session’s notes/);
    expect(screen.queryByText('Anonymous FTP login accepted on 21.')).not.toBeInTheDocument();
    // The calls are a separate read: a failed notes read does not hide them.
    await waitFor(() => expect(getAgentSessionApiActivity.mock.calls.some((c) => c[0] === 1 && c[1] === 80)).toBe(true));
  });

  // Defect 1.17 — a failed Refresh replaced the whole page with the error.
  it('keeps the session on screen when a Refresh fails, and says so with Retry', async () => {
    const user = userEvent.setup();
    renderAt('72');
    expect(await screen.findByText('map the DMZ')).toBeInTheDocument();

    getAgentSession.mockRejectedValue(new Error('upstream timed out'));
    await user.click(screen.getByRole('button', { name: /Refresh agent session/i }));

    const failure = await screen.findByTestId('session-refresh-error');
    expect(failure).toHaveTextContent('Could not load this agent session. The session below is as it was last read.');
    // The session that was read is still there, with its controls.
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Agent session #72');
    expect(screen.getByText('map the DMZ')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^End$/ })).toBeInTheDocument();
    expect(screen.getByTestId('session-tests')).toBeInTheDocument();

    // Retry reads it again; a success clears the message.
    getAgentSession.mockResolvedValue(row({ purpose: 'map the DMZ, again' }));
    await user.click(within(failure).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('map the DMZ, again')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId('session-refresh-error')).not.toBeInTheDocument());
  });

  it('reports a session that is not found', async () => {
    getAgentSession.mockRejectedValue(new Error('Agent session not found in this project'));
    renderAt('9999');
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(getAgentSessionNotes).not.toHaveBeenCalled();
    expect(getAgentSessionApiActivity).not.toHaveBeenCalled();
  });
});
