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
  trust_script_url: 'https://bluestick.example/api/v1/references/trust-cert-script',
  tls_certificate_url: 'https://bluestick.example/api/v1/references/tls-certificate',
  tls_fingerprint_sha256: 'AA:BB:CC:DD',
  tls_certificate: {
    fingerprint_sha256: 'AA:BB:CC:DD',
    self_signed: true,
    subject: 'CN=127.0.0.1',
    expires_at: '2027-04-08T20:19:47+00:00',
  },
  tools: [
    {
      name: 'assist_list_hosts',
      description: 'List/filter hosts in the project.',
      kind: 'read',
      capability: null,
      method: 'GET',
      path: '/api/v1/agent/assist/hosts',
      workflows: ['assist'],
      input_schema: { type: 'object', properties: { q: { type: 'string' } }, required: [] },
    },
    {
      name: 'assist_add_note',
      description: 'Add a note to a host.',
      kind: 'write',
      capability: 'write:notes',
      method: 'POST',
      path: '/api/v1/agent/hosts/{host_id}/notes',
      workflows: ['assist'],
      input_schema: {
        type: 'object',
        properties: { host_id: { type: 'integer' }, body: { type: 'string' } },
        required: ['host_id', 'body'],
      },
    },
    {
      name: 'plan_submit',
      description: 'Submit the draft for human approval.',
      kind: 'write',
      capability: null,
      method: 'POST',
      path: '/api/v1/agent/test-plans/{plan_id}/submit',
      workflows: ['plan_generation'],
      input_schema: { type: 'object', properties: {}, required: [] },
    },
    {
      name: 'suggest_tool',
      description: "Ask for a tool that isn't approved yet.",
      kind: 'write',
      capability: null,
      method: 'POST',
      path: '/api/v1/agent/tool-suggestions',
      workflows: ['assist', 'plan_generation', 'execution', 'recon'],
      input_schema: {
        type: 'object',
        properties: { name: { type: 'string' }, rationale: { type: 'string' } },
        required: ['name', 'rationale'],
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

  it('groups tools by the workflow whose key gets them', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('assist_list_hosts')).toBeInTheDocument());

    // A session only ever sees its own workflow's tools, so the page has to
    // answer "will my agent get this one?" — a flat read/write split can't.
    expect(screen.getByRole('heading', { name: 'Assist' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Plan generation' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Every workflow' })).toBeInTheDocument();
    // Workflows with no tools in this catalog aren't advertised as empty.
    expect(screen.queryByRole('heading', { name: 'Reconnaissance' })).not.toBeInTheDocument();

    // The cross-workflow tool is filed once, under "Every workflow" — not
    // repeated into each of the four groups it belongs to.
    expect(screen.getAllByText('suggest_tool')).toHaveLength(1);
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
    expect(screen.getByRole('tab', { name: 'Codex' })).toBeInTheDocument();
    // Cursor was dropped in v2.275.0 — it was the one recipe never verified
    // against a real install, and nobody here uses it.
    expect(screen.queryByRole('tab', { name: 'Cursor' })).not.toBeInTheDocument();
  });

  it('hands over a runnable certificate-trust command, not six manual steps', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('assist_list_hosts')).toBeInTheDocument());

    // Every client fails on the self-signed cert first, and each needs a
    // different variable — so the page leads with the script that does both.
    const block = screen.getByText(/curl -sk .*trust-cert-script -o trust-cert\.sh/);
    expect(block.textContent).toContain('bash trust-cert.sh --url https://bluestick.example');
    // Read-then-run, never piped: this one installs a trust anchor.
    expect(block.textContent).toContain('less trust-cert.sh');
    expect(block.textContent).not.toContain('| bash');

    // The fingerprint is what makes a downloaded certificate checkable.
    expect(screen.getByText('AA:BB:CC:DD')).toBeInTheDocument();
  });

  it('softens the pinning section when the deployment has a CA-issued cert', async () => {
    // Self-signed is this project's default, not an invariant — an operator can
    // mount an internal-CA or DNS-validated certificate, and telling them to pin
    // one their clients already trust is busywork.
    getMcpTools.mockResolvedValue({
      ...catalog(),
      tls_certificate: {
        fingerprint_sha256: 'AA:BB:CC:DD',
        self_signed: false,
        subject: 'CN=bluestick.internal',
        expires_at: '2027-01-01T00:00:00+00:00',
      },
    });
    renderPage();

    expect(await screen.findByText(/CA-issued/)).toBeInTheDocument();
    expect(screen.getByText(/try connecting first/)).toBeInTheDocument();
    // The recipe stays available — an internal CA the client doesn't know is
    // exactly the case where pinning is still needed.
    expect(screen.getByText(/bash trust-cert\.sh --url/)).toBeInTheDocument();
  });

  it('keeps the setup command runnable when the catalog fetch failed', async () => {
    // The fallback is a bare path, and `curl -sk /api/v1/...` has no host.
    getMcpTools.mockRejectedValue(new Error('boom'));
    renderPage();

    const block = await screen.findByText(/curl -sk .*trust-cert-script -o trust-cert\.sh/);
    // Absolute against the page's own origin, whatever that is.
    expect(block.textContent).toContain(
      `curl -sk ${window.location.origin}/api/v1/references/trust-cert-script`,
    );
    expect(block.textContent).toContain(`bash trust-cert.sh --url ${window.location.origin}`);
    expect(block.textContent).not.toMatch(/curl -sk \/api/);
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
