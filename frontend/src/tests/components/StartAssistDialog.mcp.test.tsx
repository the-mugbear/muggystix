/**
 * The MCP setup block in the Start Assist dialog.
 *
 * v2.269.0 fixed a defect this pins: the dialog used to show ONE config, in
 * VS Code's `servers` shape, while telling the operator it worked for VS Code,
 * Claude Code, and Cursor. The latter two read `mcpServers`, so pasting that
 * JSON produced a server the client silently ignored — no error, the tools just
 * never appeared. Each client now gets its own tab, and this asserts the
 * operator can reach each one and that the payloads stay distinct.
 */
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
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
      id: 'cursor',
      label: 'Cursor',
      kind: 'file',
      path: '.cursor/mcp.json',
      payload: JSON.stringify({ mcpServers: entry }, null, 2),
      hint: 'Save as .cursor/mcp.json in your project.',
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
  fireEvent.click(screen.getByRole('button', { name: /start session/i }));
  await waitFor(() => expect(screen.getByText('VS Code Copilot')).toBeInTheDocument());
};

describe('StartAssistDialog — MCP setup', () => {
  beforeEach(() => {
    startAssistSession.mockReset();
    startAssistSession.mockResolvedValue(result());
  });

  it('offers a tab per client and defaults to the first', async () => {
    await openAndStart();
    for (const label of ['VS Code Copilot', 'Claude Code', 'Cursor']) {
      expect(screen.getByRole('tab', { name: label })).toBeInTheDocument();
    }
    // Default tab is VS Code — `servers`, and the file path is stated.
    expect(screen.getByText(/"servers"/)).toBeInTheDocument();
    expect(screen.getByText('.vscode/mcp.json')).toBeInTheDocument();
  });

  it('shows Cursor the mcpServers wrapper, not VS Code’s servers', async () => {
    await openAndStart();
    await userEvent.click(screen.getByRole('tab', { name: 'Cursor' }));
    await waitFor(() => expect(screen.getByText('.cursor/mcp.json')).toBeInTheDocument());
    const shown = screen.getByText(/"mcpServers"/).textContent ?? '';
    expect(shown).toContain('"mcpServers"');
    expect(shown).not.toContain('"servers"');
  });

  it('gives Claude Code a command instead of a file to place', async () => {
    await openAndStart();
    await userEvent.click(screen.getByRole('tab', { name: 'Claude Code' }));
    await waitFor(() => expect(screen.getByText('Run this command')).toBeInTheDocument());
    expect(screen.getByText(/^claude mcp add --transport http/)).toBeInTheDocument();
  });
});
