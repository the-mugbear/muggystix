/**
 * The MCP reference page.
 *
 * Its whole point is that the tool table is read off the live server registry
 * rather than hand-written, so the two things worth pinning are: the split
 * between reads and writes is driven by the server's `kind` (a write must never
 * be presented as safe-to-always-allow), and a failed catalog fetch degrades to
 * the rest of the page instead of a blank screen.
 */
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
  tls_certificate_url: 'https://bluestick.example/api/v1/references/tls-certificate',
  tls_fingerprint_sha256: 'AA:BB:CC:DD',
  tls_certificate: {
    fingerprint_sha256: 'AA:BB:CC:DD',
    self_signed: true,
    subject: 'CN=127.0.0.1',
    expires_at: '2027-04-08T20:19:47+00:00',
  },
  sample_key_placeholder: '<your-session-key>',
  // Server-built, so the page can't drift from what a session actually emits.
  sample_clients: [
    {
      id: 'vscode',
      label: 'VS Code Copilot',
      kind: 'file',
      path: '.vscode/mcp.json',
      payload: JSON.stringify(
        { servers: { 'bluestick-assist': { type: 'http', url: 'https://bluestick.example/api/v1/mcp' } } },
        null,
        2,
      ),
      hint: 'Save as .vscode/mcp.json in your workspace.',
    },
    {
      id: 'claude_code',
      label: 'Claude Code',
      kind: 'command',
      path: '',
      payload: 'claude mcp add --transport http bluestick-assist https://bluestick.example/api/v1/mcp',
      hint: 'Run in your project directory.',
    },
    {
      id: 'codex',
      label: 'Codex',
      kind: 'command',
      path: '',
      payload: 'codex mcp add bluestick-assist --url https://bluestick.example/api/v1/mcp',
      hint: 'Codex reads the env var at run time.',
    },
  ],
  tools: [
    {
      name: 'assist_list_hosts',
      description: 'List/filter hosts in the project.',
      kind: 'read',
      method: 'GET',
      path: '/api/v1/agent/assist/hosts',
      workflows: ['assist'],
      input_schema: { type: 'object', properties: { q: { type: 'string' } }, required: [] },
    },
    {
      name: 'assist_add_note',
      description: 'Add a note to a host.',
      kind: 'write',
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
      name: 'host_tests_propose',
      description: 'Propose tests on hosts.',
      kind: 'write',
      method: 'POST',
      path: '/api/v1/agent/host-tests',
      workflows: ['testing'],
      input_schema: { type: 'object', properties: {}, required: [] },
    },
    {
      name: 'suggest_tool',
      description: "Suggest a tool the catalogue lacks.",
      kind: 'write',
      method: 'POST',
      path: '/api/v1/agent/tool-suggestions',
      workflows: ['assist', 'testing', 'scope'],
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

  it('marks writes as writes so nothing reads as safe to auto-approve', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('assist_list_hosts')).toBeInTheDocument());

    // v5.190.0 — the per-tool capability badge is gone with the capability
    // system; the read/write badge is what the operator needs, and it must
    // survive. Nobody should read "always allow this" next to a mutation.
    expect(screen.getByText('assist_add_note')).toBeInTheDocument();
    expect(screen.queryByText('write:notes')).not.toBeInTheDocument();
    expect(screen.getAllByText('write').length).toBeGreaterThan(0);

    // Required params are marked; optional ones are not.
    expect(screen.getByTitle('host_id (required)')).toBeInTheDocument();
    expect(screen.getByTitle('q')).toBeInTheDocument();
  });

  // 5.313.0 — one session key reaches every tool (the key is the operator's
  // project role), so the groups are by capability, and the page never claims
  // a per-workflow key, an approval pipeline or a fixed order.
  it('groups tools by capability, not by a per-workflow key', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('assist_list_hosts')).toBeInTheDocument());

    expect(screen.getByRole('heading', { name: 'Read the inventory and write notes' })).toBeInTheDocument();
    // 5.320.0 — tests are proposed on hosts; there is no plan or run group.
    const testing = screen.getByRole('heading', { name: 'Propose and work host tests' });
    expect(testing).toBeInTheDocument();
    // In the catalogue, and in the audit note (which names the tools that take `agent_model`).
    expect(screen.getAllByText('host_tests_propose').length).toBeGreaterThan(0);
    // N2 — the audit note named two tools removed with test plans and runs.
    expect(screen.queryByText('create_test_plan')).toBeNull();
    expect(screen.queryByText('start_execution')).toBeNull();
    expect(screen.getByText('record_evidence')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /Write test plans|Record test results/ })).not.toBeInTheDocument();

    expect(screen.getByRole('heading', { name: 'One session, one key' })).toBeInTheDocument();
    expect(screen.queryByText(/belongs to one workflow/)).toBeNull();
    expect(screen.queryByRole('img', { name: /pipeline/ })).toBeNull();
    expect(screen.queryByText(/approved plan|for approval|human approval/)).toBeNull();
  });

  // The page runs to about nine screens: every section and tool group is a
  // jump target, under its own heading's words.
  it('has a jump picker over its sections and tool groups', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('assist_list_hosts')).toBeInTheDocument());
    const bar = await screen.findByRole('navigation', { name: 'Sections of this page' });
    expect(within(bar).getByRole('combobox', { name: 'Jump to a section' })).toBeInTheDocument();
    for (const [id, heading] of [
      ['section-session', 'One session, one key'],
      ['section-connecting', 'Connecting a client'],
      ['section-tools', 'Available tools'],
      ['section-tools-assist', 'Read the inventory and write notes'],
      ['section-authority', 'What a session may do'],
      ['section-limits', 'What these tools do not answer'],
    ]) {
      const section = document.getElementById(id)!;
      expect(within(section).getByRole('heading', { name: heading })).toBeInTheDocument();
      expect(section.style.scrollMarginTop).toMatch(/var\(--topbar-h/);
    }
  });

  // 5.320.0 — "every kind" follows the catalog's kinds (it was a literal 4).
  it('files a cross-cutting tool once, under Session and catalogue', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('assist_list_hosts')).toBeInTheDocument());
    expect(screen.getByRole('heading', { name: 'Session and catalogue' })).toBeInTheDocument();
    // Capabilities with no tools of their own aren't advertised as empty.
    expect(screen.queryByRole('heading', { name: 'Read a scope, upload scans' })).not.toBeInTheDocument();
    // The cross-cutting tool is filed once — not repeated into each group.
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

  it('renders the server-built recipes rather than a second copy of them', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText('assist_list_hosts')).toBeInTheDocument());
    // The page used to hold its own TypeScript copy of these, and the pair
    // drifted twice — on the config wrapper key and on the Codex TLS note.
    // Rendering what the server emits is what stops that recurring.
    const vscode = screen.getByText(/"servers"/);
    expect(vscode).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Claude Code' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Codex' })).toBeInTheDocument();
    // Cursor was dropped in v2.275.0 — it was the one recipe never verified
    // against a real install, and nobody here uses it.
    expect(screen.queryByRole('tab', { name: 'Cursor' })).not.toBeInTheDocument();
  });

  it('points at the local root CA and shows the fingerprint as a check, with no per-client pinning', async () => {
    // scripts/trust-cert.sh (per-client pinning) is retired: the certificate is
    // issued by the local root CA, installed once per machine.
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
    await waitFor(() => expect(screen.getByText('assist_list_hosts')).toBeInTheDocument());

    expect(screen.getByText(/local root CA/)).toBeInTheDocument();
    expect(screen.getByText('ca/local-ca.sh trust-help')).toBeInTheDocument();
    // The fingerprint stays — as a "right server?" check.
    expect(screen.getByText('AA:BB:CC:DD')).toBeInTheDocument();
    expect(screen.queryByText(/still presents a self-signed/)).not.toBeInTheDocument();
    // None of the retired pinning path survives.
    const text = document.body.textContent ?? '';
    expect(text).not.toContain('trust-cert');
    expect(text).not.toContain('NODE_EXTRA_CA_CERTS');
    expect(text).not.toContain('SSL_CERT_DIR');
  });

  it('says so when the deployment still presents a self-signed certificate', async () => {
    renderPage();
    expect(await screen.findByText(/still presents a self-signed certificate/)).toBeInTheDocument();
  });

  it('degrades to the static guidance when the catalog cannot be loaded', async () => {
    getMcpTools.mockRejectedValue(new Error('boom'));
    renderPage();
    await waitFor(() => expect(screen.getByText(/Could not load the tool catalog/)).toBeInTheDocument());
    // The rest of the page — the reason to visit it — is still there.
    expect(screen.getByRole('heading', { name: 'Connecting a client' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'What a session may do' })).toBeInTheDocument();
  });

  it('gives both file-shaped downloads, not just the dossier stream', async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: 'Writing the engagement up' })).toBeInTheDocument(),
    );
    // Screenshots are the second thing a report needs off disk, and the page
    // documented only the first — leaving an operator to guess how an agent
    // fetches evidence images with a key rather than a login.
    expect(screen.getByText(/report-context\.ndjson/)).toBeInTheDocument();
    expect(screen.getByText(/agent\/assist\/attachments/)).toBeInTheDocument();
  });

  it('states plainly that the analysis is not a trend', async () => {
    renderPage();
    await waitFor(() =>
      expect(
        screen.getByRole('heading', { name: 'What these tools do not answer' }),
      ).toBeInTheDocument(),
    );
    // The most likely way to misread this output is as change over time, and
    // the page should say so where someone reading the results will see it.
    expect(screen.getByText(/changed since last week/)).toBeInTheDocument();
  });
});
