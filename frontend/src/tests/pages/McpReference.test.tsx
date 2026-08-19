/**
 * The MCP reference page.
 *
 * Its whole point is that the tool table is read off the live server registry
 * rather than hand-written, so the two things worth pinning are: the split
 * between reads and writes is driven by the server's `kind` (a write must never
 * be presented as safe-to-always-allow), and a failed catalog fetch degrades to
 * the rest of the page instead of a blank screen.
 */
import React from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import McpReference from '../../pages/McpReference';
import { TooltipProvider } from '../../components/ui/tooltip';
import type { McpCatalog } from '../../services/api';

const getMcpTools = vi.fn();
vi.mock('../../services/api', () => ({
  getMcpTools: () => getMcpTools(),
}));

const catalog = (): McpCatalog => ({
  server_name: 'bluestick-assist',
  protocol_version: '2025-06-18',
  endpoint: 'https://bluestick.example/api/v1/mcp',
  max_request_bytes: 1048576,
  max_batch_messages: 50,
  tools: [
    {
      name: 'assist_list_hosts',
      description: 'List/filter hosts in the project.',
      kind: 'read',
      capability: null,
      method: 'GET',
      path: '/api/v1/agent/assist/hosts',
      input_schema: { type: 'object', properties: { q: { type: 'string' } }, required: [] },
    },
    {
      name: 'assist_add_note',
      description: 'Add a note to a host.',
      kind: 'write',
      capability: 'write:notes',
      method: 'POST',
      path: '/api/v1/agent/hosts/{host_id}/notes',
      input_schema: {
        type: 'object',
        properties: { host_id: { type: 'integer' }, body: { type: 'string' } },
        required: ['host_id', 'body'],
      },
    },
  ],
});

const renderPage = () =>
  render(
    <MemoryRouter>
      <TooltipProvider>
        <McpReference />
      </TooltipProvider>
    </MemoryRouter>,
  );

describe('McpReference', () => {
  beforeEach(() => {
    getMcpTools.mockReset();
    getMcpTools.mockResolvedValue(catalog());
  });

  it('separates reads from capability-gated writes', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('assist_list_hosts')).toBeInTheDocument());

    // The write tool is listed with the capability it needs — the operator
    // should never read "always allow this" next to a mutation.
    expect(screen.getByText('assist_add_note')).toBeInTheDocument();
    expect(screen.getByText('write:notes')).toBeInTheDocument();

    // Required params are marked; optional ones are not.
    expect(screen.getByTitle('host_id (required)')).toBeInTheDocument();
    expect(screen.getByTitle('q')).toBeInTheDocument();
  });

  it('shows the transport facts the server reported', async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByTitle('https://bluestick.example/api/v1/mcp')).toBeInTheDocument(),
    );
    expect(screen.getByText('2025-06-18')).toBeInTheDocument();
    expect(screen.getByText(/1 MiB body · 50-message batch/)).toBeInTheDocument();
  });

  it('keeps the connect instructions distinct per client', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('assist_list_hosts')).toBeInTheDocument());
    // Default tab is VS Code, which is the one client using `servers`.
    const vscode = screen.getByText(/"servers"/);
    expect(vscode).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Claude Code' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Cursor' })).toBeInTheDocument();
  });

  it('degrades to the static guidance when the catalog cannot be loaded', async () => {
    getMcpTools.mockRejectedValue(new Error('boom'));
    renderPage();
    await waitFor(() => expect(screen.getByText(/Could not load the tool catalog/)).toBeInTheDocument());
    // The rest of the page — the reason to visit it — is still there.
    expect(screen.getByRole('heading', { name: 'Connecting a client' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'What a session may do' })).toBeInTheDocument();
  });
});
