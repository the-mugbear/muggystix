/**
 * The Start Agent Session dialog.
 *
 * v2.269.0 fixed a defect this pins: the dialog used to show ONE config, in
 * VS Code's `servers` shape, while telling the operator it worked for several
 * clients. Claude Code reads `mcpServers`, so pasting that JSON produced a
 * server the client silently ignored. Each client keeps its own recipe.
 *
 * 5.309.0 — the dialog was ~770 words and scrolled: the same explanation four
 * times, the key plus two levels of tabs, every note expanded, and an "I
 * copied the key" checkbox. Now: one sentence and one field to start; then the
 * operator picks their agent (remembered), sees that one recipe with one copy
 * button, and Done warns only when nothing holding the key was copied.
 */
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { MemoryRouter } from 'react-router-dom';

import { StartAssistDialog } from '../../components/StartAssistDialog';
import { TooltipProvider } from '../../components/ui/tooltip';
import type { AgentSessionRow, StartAssistResponse } from '../../services/api';
import { copyToClipboard } from '../../utils/clipboard';

const startAssistSession = vi.fn();
vi.mock('../../services/api', () => ({
  startAssistSession: (...args: unknown[]) => startAssistSession(...args),
  endAgentSession: vi.fn(),
  getMcpTools: vi.fn(() => new Promise(() => {})),
}));
vi.mock('../../utils/clipboard', () => ({ copyToClipboard: vi.fn(() => Promise.resolve(true)) }));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

const KEY = 'nm_agent_testkey';
const URL = 'https://bluestick.example/api/v1/mcp';
const entry = { 'bluestick-assist': { type: 'http', url: URL, headers: { 'X-API-Key': KEY } } };

const result = (): StartAssistResponse => ({
  agent_session_id: 21,
  project_id: 1,
  project_name: 'engagement',
  agent_id: 9,
  api_key: KEY,
  instructions: 'prompt text',
  mcp_url: URL,
  mcp_clients: [
    {
      id: 'vscode',
      label: 'VS Code Copilot',
      kind: 'file',
      path: '.vscode/mcp.json',
      payload: JSON.stringify({ servers: entry }, null, 2),
      hint: 'Save as .vscode/mcp.json in your workspace.',
    },
    {
      id: 'claude_code',
      label: 'Claude Code',
      kind: 'command',
      path: '',
      payload: `claude mcp add --transport http bluestick-assist ${URL} --header "X-API-Key: ${KEY}"`,
      hint: 'Run in your project directory.',
      verify_check: '`claude mcp list` should report bluestick-assist as Connected.',
      verify_prompt: 'Using the bluestick-assist MCP server, call agent_identity.',
      verify_expected: 'A working connection answers with project “engagement”, assist session #12.',
    },
    {
      id: 'codex',
      label: 'Codex',
      kind: 'command',
      path: '',
      payload: `read -rs BLUESTICK_ASSIST_KEY && export BLUESTICK_ASSIST_KEY\ncodex mcp add bluestick-assist --url ${URL} --bearer-token-env-var BLUESTICK_ASSIST_KEY`,
      hint: 'Codex reads the env var at run time.',
    },
  ],
  key_ttl_hours: 24,
});

const onOpenChange = vi.fn();
const openAndStart = async () => {
  render(
    <MemoryRouter>
      <TooltipProvider>
        <StartAssistDialog open onOpenChange={onOpenChange} />
      </TooltipProvider>
    </MemoryRouter>,
  );
  await act(async () => {
    await userEvent.click(screen.getByRole('button', { name: /start session/i }));
  });
  await screen.findByRole('radiogroup', { name: 'Your agent' });
};

const choose = async (label: string) => {
  await act(async () => {
    await userEvent.click(screen.getByRole('radio', { name: label }));
  });
};

describe('StartAssistDialog', () => {
  beforeEach(() => {
    startAssistSession.mockReset();
    startAssistSession.mockResolvedValue(result());
    onOpenChange.mockReset();
    window.localStorage.clear();
  });

  // 5.312.1 — the dialog is where operators look for their sessions; it must
  // lead to where they are managed, and name the new one by its SESSION id.
  it('points to Agent Sessions for resuming, and titles the new session by its session id', async () => {
    render(
      <MemoryRouter>
        <TooltipProvider>
          <StartAssistDialog open onOpenChange={onOpenChange} />
        </TooltipProvider>
      </MemoryRouter>,
    );
    const link = screen.getByRole('link', { name: 'Resume it from Agent Sessions' });
    expect(link).toHaveAttribute('href', '/agent-activity');
    await act(async () => {
      await userEvent.click(screen.getByRole('button', { name: /start session/i }));
    });
    expect(await screen.findByText('Connect your agent — session #21')).toBeInTheDocument();
    expect(screen.getByText(/Agents → Agent Sessions/)).toBeInTheDocument();
  });

  // The server cannot see the operator's terminal, so the dialog does not
  // promise that every command is shown: it says the agent is told to, and
  // names what holds it to that (the client's permission prompts).
  it('says who holds the agent to show-every-command, and promises no approval step', () => {
    render(
      <MemoryRouter>
        <TooltipProvider>
          <StartAssistDialog open onOpenChange={onOpenChange} />
        </TooltipProvider>
      </MemoryRouter>,
    );
    expect(screen.getByText(/It is told to show you every command first/)).toBeInTheDocument();
    expect(screen.getByText(/your client's permission prompts are what hold it to that/)).toBeInTheDocument();
    expect(screen.queryByText(/shows you every command it runs/)).toBeNull();
    expect(screen.queryByText(/approv/i)).toBeNull();
  });

  // 5.313.0 — the per-object entry points hand a task to the one session.
  it('shows a task to copy before and after starting', async () => {
    const task = 'Propose tests in BlueStick for these hosts only (host ids): 12, 14.';
    render(
      <MemoryRouter>
        <TooltipProvider>
          <StartAssistDialog open onOpenChange={onOpenChange} instruction={task} />
        </TooltipProvider>
      </MemoryRouter>,
    );
    expect(screen.getByText(task)).toBeInTheDocument();
    expect(screen.getByText(/Start a session, connect your agent, then give it this/)).toBeInTheDocument();
    await act(async () => {
      await userEvent.click(screen.getByRole('button', { name: /start session/i }));
    });
    expect(await screen.findByText('Once it is connected, give your agent this:')).toBeInTheDocument();
    expect(screen.getByText(task)).toBeInTheDocument();
  });

  // With a session already live the task is the point; a second session is
  // asked for explicitly.
  it('with a live session, copying the task is the primary action', async () => {
    const task = 'Propose tests in BlueStick for these hosts only (host ids): 12, 14.';
    const live = {
      id: 86, kind: 'project', status: 'active', can_end: true, can_resume: true,
      key_expires_at: new Date(Date.now() + 3 * 3_600_000).toISOString(),
    } as unknown as AgentSessionRow;
    render(
      <MemoryRouter>
        <TooltipProvider>
          <StartAssistDialog open onOpenChange={onOpenChange} instruction={task} mySessions={[live]} />
        </TooltipProvider>
      </MemoryRouter>,
    );
    expect(screen.queryByRole('button', { name: /^start session$/i })).toBeNull();
    expect(screen.queryByLabelText(/What is it for/)).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Copy task' }));
    expect(copyToClipboard).toHaveBeenCalledWith(task);
    expect(await screen.findByRole('button', { name: /Copied — paste it to your agent/ })).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Start another session' }));
    expect(screen.getByRole('button', { name: /^start session$/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/What is it for/)).toBeInTheDocument();
  });

  it('starts from one sentence and one field — no promised TTL before the server says', () => {
    render(
      <MemoryRouter>
        <TooltipProvider>
          <StartAssistDialog open onOpenChange={onOpenChange} />
        </TooltipProvider>
      </MemoryRouter>,
    );
    expect(screen.getByText(/Connect Claude Code, Codex or VS Code Copilot to this project/)).toBeInTheDocument();
    expect(screen.getByLabelText(/What is it for\?/)).toBeInTheDocument();
    // It said "4 h TTL" here while the server issued 24.
    expect(screen.queryByText(/TTL|\b4 h\b/)).not.toBeInTheDocument();
  });

  it('offers one choice per client plus any other agent, and shows only the chosen recipe', async () => {
    await openAndStart();
    for (const label of ['VS Code Copilot', 'Claude Code', 'Codex', 'Other agent']) {
      expect(screen.getByRole('radio', { name: label })).toBeInTheDocument();
    }
    expect(screen.getByText(/valid for 24 hours/)).toBeInTheDocument();
    // Default: the first client — VS Code's `servers`, its file path stated.
    expect(screen.getByText(/"servers"/)).toBeInTheDocument();
    expect(screen.getByText('.vscode/mcp.json')).toBeInTheDocument();
    expect(screen.queryByText(/^claude mcp add/)).not.toBeInTheDocument();
  });

  it('keeps each client’s payload distinct rather than reusing one shape', async () => {
    await openAndStart();
    await choose('Codex');
    const shown = screen.getByText(/^read -rs BLUESTICK_ASSIST_KEY/).textContent ?? '';
    expect(shown).toContain('--bearer-token-env-var');
    expect(shown).not.toContain('"servers"');
    await choose('Claude Code');
    expect(screen.getByText(/^claude mcp add --transport http/)).toBeInTheDocument();
    expect(screen.getByText('Run this command')).toBeInTheDocument();
  });

  it('remembers the chosen agent for next time', async () => {
    await openAndStart();
    await choose('Claude Code');
    expect(window.localStorage.getItem('bluestick.agentClient')).toBe('claude_code');
  });

  it('hands over a verification prompt, and folds the client’s notes', async () => {
    await openAndStart();
    await choose('Claude Code');
    expect(screen.getByText('Then verify it works')).toBeInTheDocument();
    expect(screen.getByText(/call agent_identity/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /copy verification prompt/i })).toBeInTheDocument();
    expect(screen.getByText(/assist session #12/)).toBeInTheDocument();
    expect(screen.getByText(/claude mcp list/)).toBeInTheDocument();
    expect(screen.getByText('Setup notes for Claude Code').closest('details')).not.toHaveAttribute('open');
  });

  it('"Other agent" is the pasted prompt; the key on its own is folded', async () => {
    await openAndStart();
    expect(screen.queryByText('prompt text')).not.toBeInTheDocument();
    await choose('Other agent');
    expect(await screen.findByText('prompt text')).toBeInTheDocument();
    expect(screen.getByText('The key on its own').closest('details')).not.toHaveAttribute('open');
  });

  it('falls back to the prompt alone when the server sent no MCP setup', async () => {
    startAssistSession.mockResolvedValue({ ...result(), mcp_clients: [] });
    await openAndStart();
    expect(await screen.findByText('prompt text')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Paste the prompt' })).toBeChecked();
  });

  it('Done warns once when nothing holding the key was copied, then closes anyway', async () => {
    await openAndStart();
    await userEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(screen.getByRole('alert')).toHaveTextContent(/Nothing was copied/);
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    await userEvent.click(screen.getByRole('button', { name: 'Close anyway' }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('closes on Done without asking once the setup was copied', async () => {
    await openAndStart();
    await userEvent.click(screen.getByRole('button', { name: /Copy VS Code Copilot MCP setup/ }));
    await waitFor(() => expect(screen.getByRole('button', { name: /Copy VS Code Copilot MCP setup/ })).toHaveTextContent('Copied'));
    await userEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  // 5.314.1 — Codex's recipe waits for the key to be pasted (`read -rs`), so
  // the key sits beside it rather than folded away.
  it('puts the key beside a recipe that waits for it to be pasted (Codex)', async () => {
    await openAndStart();
    await choose('Codex');
    expect(screen.getByText(/Paste this key when the first line waits for it/)).toBeInTheDocument();
    expect(screen.getByTestId('agent-key')).toHaveTextContent(KEY);
    expect(screen.queryByText('The key on its own')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Copy agent API key' }));
    expect(copyToClipboard).toHaveBeenLastCalledWith(KEY);
  });
});
