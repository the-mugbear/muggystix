/**
 * The MCP setup block in the Start Assist dialog.
 *
 * v2.269.0 fixed a defect this pins: the dialog used to show ONE config, in
 * VS Code's `servers` shape, while telling the operator it worked for several
 * clients. Claude Code reads `mcpServers`, so pasting that JSON produced a
 * server the client silently ignored — no error, the tools just never appeared.
 * Each client now gets its own tab, and this asserts the operator can reach
 * each one and that the payloads stay distinct.
 */
import React from 'react';
import { act, render, screen } from '@testing-library/react';
// Radix tab triggers activate on pointer events, not the synthetic click
// fireEvent dispatches — use userEvent so the switch actually happens.
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { StartAssistDialog } from '../../components/StartAssistDialog';
import { TooltipProvider } from '../../components/ui/tooltip';
import type { StartAssistResponse } from '../../services/api';

const startAssistSession = vi.fn();
vi.mock('../../services/api', () => ({
  startAssistSession: (...args: unknown[]) => startAssistSession(...args),
  endAssistSession: vi.fn(),
}));

vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

const KEY = 'nm_agent_testkey';
const URL = 'https://bluestick.example/api/v1/mcp';
const entry = { 'bluestick-assist': { type: 'http', url: URL, headers: { 'X-API-Key': KEY } } };

const result = (): StartAssistResponse => ({
  assist_session_id: 3,
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
  capabilities: [],
  capability_constraint: null,
  key_ttl_hours: 4,
});

const openAndStart = async () => {
  render(
    <TooltipProvider>
      <StartAssistDialog open onOpenChange={vi.fn()} />
    </TooltipProvider>,
  );
  // findBy* is act-aware, so the async state update that follows the start
  // call settles inside act() and the run stays free of act() warnings — the
  // rest of this suite is clean, and noise here would hide a real one.
  await act(async () => {
    await userEvent.click(screen.getByRole('button', { name: /start session/i }));
  });
  await screen.findByText('VS Code Copilot');
};


/** Radix activates a tab on pointer events, which cascade into controlled-state
 *  updates; act() keeps those inside React's batch so the run stays warning-free. */
const switchTab = async (label: string) => {
  await act(async () => {
    await userEvent.click(screen.getByRole('tab', { name: label }));
  });
};

describe('StartAssistDialog — MCP setup', () => {
  beforeEach(() => {
    startAssistSession.mockReset();
    startAssistSession.mockResolvedValue(result());
  });

  it('offers a tab per client and defaults to the first', async () => {
    await openAndStart();
    for (const label of ['VS Code Copilot', 'Claude Code', 'Codex']) {
      expect(screen.getByRole('tab', { name: label })).toBeInTheDocument();
    }
    // Default tab is VS Code — `servers`, and the file path is stated.
    expect(screen.getByText(/"servers"/)).toBeInTheDocument();
    expect(screen.getByText('.vscode/mcp.json')).toBeInTheDocument();
  });

  it('keeps each client’s payload distinct rather than reusing one shape', async () => {
    await openAndStart();
    // VS Code's tab shows `servers`; Codex gets a command with no wrapper key
    // at all. Reusing one shape across clients is the bug this file exists for.
    await switchTab('Codex');
    await screen.findByText('Run this command');
    const shown = screen.getByText(/^read -rs BLUESTICK_ASSIST_KEY/).textContent ?? '';
    expect(shown).toContain('--bearer-token-env-var');
    expect(shown).not.toContain('"servers"');
  });

  it('gives Claude Code a command instead of a file to place', async () => {
    await openAndStart();
    await switchTab('Claude Code');
    await screen.findByText('Run this command');
    expect(screen.getByText(/^claude mcp add --transport http/)).toBeInTheDocument();
  });
});
