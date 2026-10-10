/**
 * Evidence page (5.255.0) — the domain × segment matrix: three cell states,
 * a cell opens exactly its hosts, and nothing is judged by age.
 */
import { render, screen, within, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const coverageMock = vi.fn();
const gapsMock = vi.fn();
vi.mock('../../services/api', () => ({
  getEvidenceCoverage: (...a: unknown[]) => coverageMock(...a),
  getEvidenceGaps: (...a: unknown[]) => gapsMock(...a),
}));
vi.mock('../../contexts/ProjectContext', () => ({
  useProject: () => ({ currentProject: { id: 1, name: 'P' } }),
}));
const toast = { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() };
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => toast,
}));
const navigateMock = vi.fn();
vi.mock('react-router-dom', async (importOriginal) => ({
  ...(await importOriginal<typeof import('react-router-dom')>()),
  useNavigate: () => navigateMock,
}));
// "Propose tests" opens the Start Agent Session dialog, which reads the
// operator's live sessions.
vi.mock('../../hooks/useMyAssistSessions', () => ({
  useMyAssistSessions: () => ({ sessions: [], loading: false, failed: false, refresh: vi.fn() }),
}));

import Evidence from '../../pages/Evidence';
import { TooltipProvider } from '../../components/ui/tooltip';

const LONG = 'very-long-site-name-'.repeat(11);   // ~220 chars

const domain = (key: string, label: string, numerator: number, denominator: number) => ({
  key, label, note: `How ${label} is judged.`,
  coverage: { value: denominator ? numerator / denominator : 0, numerator, denominator },
  action: { kind: 'collect', text: `Collect ${label} evidence and upload it.` },
});
const c = (segment: string, eligible: number, assessed: number) => ({ segment, eligible, assessed, gap: eligible - assessed });

const coverage = {
  total_hosts: 87,
  domains: [domain('web_tls', 'Web / TLS', 9, 30), domain('auth_smb_ad', 'Authentication / SMB / AD', 10, 21)],
  matrix: {
    group_by: 'subnet',
    segments: [
      { key: 'subnet:1', label: LONG, hosts: 40 },
      { key: 'subnet:2', label: '10.8.0.0/24', hosts: 19 },
      { key: 'unmapped', label: 'Outside scoped subnets', hosts: 28 },
    ],
    rows: [
      { domain: 'web_tls', label: 'Web / TLS', cells: [c('subnet:1', 12, 9), c('subnet:2', 0, 0), c('unmapped', 18, 0)] },
      { domain: 'auth_smb_ad', label: 'Authentication / SMB / AD', cells: [c('subnet:1', 10, 10), c('subnet:2', 11, 0), c('unmapped', 0, 0)] },
    ],
  },
  contributing_tools: [{ tool: 'nmap', scans: 49 }],
  data_quality: { scans: 182, parse_errors_unresolved: 0 },
};

const renderPage = async () => {
  render(<MemoryRouter><TooltipProvider><Evidence /></TooltipProvider></MemoryRouter>);
  await screen.findByText('Where the gaps are');
};

describe('Evidence — domain × segment matrix', () => {
  beforeEach(() => {
    coverageMock.mockReset().mockResolvedValue(coverage);
    gapsMock.mockReset().mockResolvedValue({
      domain: 'web_tls', label: 'Web / TLS', segment: 'unmapped', segment_label: 'Outside scoped subnets', total: 18,
      items: [{ host_id: 7, ip_address: '192.168.9.9', hostname: null, ports: [443] }],
      action: { kind: 'collect', text: 'Probe these hosts with httpx and upload the JSON.' },
    });
  });

  it('gives every cell one of three states — and hosts outside every scoped subnet are a column', async () => {
    await renderPage();
    const matrix = screen.getByText('Where the gaps are').closest('section')!;
    const states = Array.from(matrix.querySelectorAll('[data-state]')).map((el) => el.getAttribute('data-state'));
    expect(states).toEqual(['partial', 'na', 'none', 'assessed', 'none', 'na']);
    expect(within(matrix).getByText('Outside scoped subnets')).toBeInTheDocument();
    expect(within(matrix).getByText(/grouped by their most-specific subnet/)).toBeInTheDocument();
    // A project is one assessment window: nothing here is judged by age.
    expect(screen.queryByText(/\bstale\b|\bfresh\b|days ago/i)).toBeNull();
  });

  it('opens exactly the selected cell — domain AND segment go to the server', async () => {
    await renderPage();
    const matrix = screen.getByText('Where the gaps are').closest('section')!;
    fireEvent.click(within(matrix).getByRole('button', { name: /Web \/ TLS · Outside scoped subnets: 0 of 18 .* show the 18 not assessed/ }));
    await waitFor(() => expect(gapsMock).toHaveBeenCalledWith(1, 'web_tls', expect.objectContaining({ segment: 'unmapped' })));
    expect(await within(matrix).findByText('192.168.9.9')).toBeInTheDocument();
    expect(within(matrix).getByText(/18 of 18/)).toBeInTheDocument();
    // One host listed of 18: the buttons say what they act on.
    expect(within(matrix).getByText(/Showing the first 1 of 18 — Copy and Propose tests act on these 1/)).toBeInTheDocument();

    // The whole-project figure opens the domain without a segment.
    fireEvent.click(within(matrix).getByRole('button', { name: /Web \/ TLS, whole project: 9 of 30/ }));
    await waitFor(() => expect(gapsMock).toHaveBeenLastCalledWith(1, 'web_tls', expect.objectContaining({ segment: undefined })));
  });

  // From the first real screenshot: "Vulnerability assessment · Outside scoped
  // subnets → Run a vulnerability scan against these hosts" ranked second. In a
  // project with a declared scope that tells an analyst to scan hosts nobody
  // confirmed are authorized.
  // 2.374.4 review H8: the hand-off must name exactly the listed hosts —
  // never an unrestricted task over the whole project. 5.313.0 — the hosts go
  // to the operator's agent session as a task, not to a generate dialog.
  it('hands exactly the listed hosts to your agent session', async () => {
    await renderPage();
    const matrix = screen.getByText('Where the gaps are').closest('section')!;
    fireEvent.click(within(matrix).getByRole('button', { name: /Web \/ TLS · Outside scoped subnets/ }));
    await within(matrix).findByText('192.168.9.9');

    navigateMock.mockClear();
    fireEvent.click(within(matrix).getByRole('button', { name: /Propose tests/ }));
    expect(await screen.findByText('Start Agent Session')).toBeInTheDocument();
    expect(screen.getByText(/^Propose tests in BlueStick for these hosts only \(host ids\): 7\./)).toBeInTheDocument();
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it('a Largest-gaps count opens the same detail as its matrix cell', async () => {
    await renderPage();
    const list = screen.getByText('Largest gaps').closest('section')!;
    fireEvent.click(within(list).getByRole('button', { name: /^18 of 18 hosts not assessed — Web \/ TLS · Outside scoped subnets/ }));
    await waitFor(() => expect(gapsMock).toHaveBeenCalledWith(1, 'web_tls', expect.objectContaining({ segment: 'unmapped' })));
  });

  it('counts imports by the needs-attention rule, never parse-error rows', async () => {
    coverageMock.mockResolvedValue({
      ...coverage,
      data_quality: { scans: 51, parse_errors_unresolved: 16, imports_needing_attention: 2, imports_dismissed: 14 },
    });
    await renderPage();
    const link = screen.getByRole('link', { name: /2 imports need attention/ });
    expect(link).toHaveAttribute('href', '/parse-errors?status=needs_attention');
    expect(screen.getByText(/14 failed or partial imports dismissed/)).toBeInTheDocument();
    expect(screen.queryByText(/16/)).toBeNull();
  });

  it('says the import count is unknown when the server did not send it', async () => {
    await renderPage();
    expect(screen.getByText(/imports needing attention could not be counted/)).toBeInTheDocument();
  });

  it('never recommends collecting against hosts outside the declared scope, and ranks them last', async () => {
    await renderPage();
    const list = screen.getByText('Largest gaps').closest('section')!;
    const rows = within(list).getAllByRole('row').slice(1);
    // In-scope gaps by hosts missing, THEN the out-of-scope one — though it is the largest.
    expect(rows.map((r) => within(r).getAllByRole('cell')[1].textContent)).toEqual(['11 of 11', '3 of 12', '18 of 18']);
    expect(within(rows[1]).getByText('Collect Web / TLS evidence and upload it.')).toBeInTheDocument();
    expect(within(rows[2]).getByText(/Confirm these hosts are in scope/)).toBeInTheDocument();
    expect(within(rows[2]).queryByText(/Collect Web/)).toBeNull();
  });

  // 2.374.4 review H7: the SERVER decides, host by host, from the declared
  // scope (subnets and names); the panel shows its advice and caution, and
  // the caution travels into the task.
  it('shows the server\'s scope advice — all outside, or a caution for some — and hands the caution to the agent task', async () => {
    const confirm = 'Outside every scoped subnet. Confirm these hosts are in scope before collecting anything more against them.';
    gapsMock.mockResolvedValueOnce({
      domain: 'web_tls', label: 'Web / TLS', segment: 'unmapped', segment_label: 'Outside scoped subnets', total: 18,
      items: [{ host_id: 7, ip_address: '192.168.9.9', hostname: null, ports: [443] }],
      action: { kind: 'confirm_scope', text: confirm }, project_has_scope: true, outside_scope: 18, scope_caution: null,
    });
    await renderPage();
    const matrix = screen.getByText('Where the gaps are').closest('section')!;
    fireEvent.click(within(matrix).getByRole('button', { name: /Web \/ TLS · Outside scoped subnets/ }));
    expect(await within(matrix).findByText(confirm)).toBeInTheDocument();
    expect(within(matrix).queryByText(/Probe these hosts with httpx/)).toBeNull();

    const caution = '3 of these 30 hosts are outside the declared scope (no scoped subnet and no in-scope name). Confirm them before collecting anything against them.';
    gapsMock.mockResolvedValueOnce({
      domain: 'web_tls', label: 'Web / TLS', segment: null, segment_label: null, total: 30,
      items: [{ host_id: 7, ip_address: '192.168.9.9', hostname: null, ports: [443] }],
      action: { kind: 'collect', text: 'Probe these hosts with httpx and upload the JSON.' },
      project_has_scope: true, outside_scope: 3, scope_caution: caution,
    });
    fireEvent.click(within(matrix).getByRole('button', { name: /Web \/ TLS, whole project/ }));
    expect(await within(matrix).findByText(caution)).toBeInTheDocument();
    expect(within(matrix).getByText(/Probe these hosts with httpx/)).toBeInTheDocument();

    // Some of the listed hosts are outside the scope: no collection task names them.
    expect(within(matrix).queryByRole('button', { name: 'Collect with your agent' })).toBeNull();

    fireEvent.click(within(matrix).getByRole('button', { name: /Propose tests/ }));
    const task = await screen.findByText(/^Propose tests in BlueStick for these hosts only/);
    expect(task.textContent).toContain(caution);
  });

  // Plan A5: the collection step could only be copied as IPs; the agent could
  // be asked to propose tests but not to collect what the gap is about.
  it('hands an in-scope collection gap to your agent — the listed hosts, no tool named', async () => {
    gapsMock.mockResolvedValue({
      domain: 'web_tls', label: 'Web / TLS', segment: null, segment_label: null, total: 9,
      items: [{ host_id: 7, ip_address: '10.0.0.9', hostname: null, ports: [443] }, { host_id: 8, ip_address: '10.0.0.10', hostname: null, ports: [8443] }],
      action: { kind: 'collect', text: 'Probe these hosts with httpx and upload the JSON.' },
      project_has_scope: true, outside_scope: 0, scope_caution: null,
    });
    await renderPage();
    const matrix = screen.getByText('Where the gaps are').closest('section')!;
    fireEvent.click(within(matrix).getByRole('button', { name: /Web \/ TLS, whole project/ }));
    fireEvent.click(await within(matrix).findByRole('button', { name: 'Collect with your agent' }));
    const task = await screen.findByText(/^Collect the missing "Web \/ TLS" evidence for these hosts only \(host ids\): 7, 8\./);
    expect(task.textContent).not.toMatch(/httpx/);
  });

  it('offers no collection task where no scope is declared, or the gap is one to test rather than collect', async () => {
    await renderPage();
    const matrix = screen.getByText('Where the gaps are').closest('section')!;
    // The default answer states no scope facts at all.
    fireEvent.click(within(matrix).getByRole('button', { name: /Web \/ TLS, whole project/ }));
    await within(matrix).findByText('192.168.9.9');
    expect(within(matrix).queryByRole('button', { name: 'Collect with your agent' })).toBeNull();
  });

  // UX review 2026-09-24: the outside-scope column was tinted and hatched like
  // every gap, which read as "collect evidence here" for unauthorized hosts.
  it('draws the outside-scope column neutral and says to confirm scope', async () => {
    await renderPage();
    const matrix = screen.getByText('Where the gaps are').closest('section')!;
    const outside = Array.from(matrix.querySelectorAll<HTMLElement>('[data-outside-scope]'));
    expect(outside).toHaveLength(2);
    for (const cell of outside) {
      expect(cell.style.backgroundColor).toBe('');
      expect(cell.style.backgroundImage).toBe('');
    }
    // An in-scope hatched cell still reads as a gap.
    const inScopeNone = Array.from(matrix.querySelectorAll<HTMLElement>('[data-state="none"]'))
      .find((el) => !el.hasAttribute('data-outside-scope'))!;
    expect(inScopeNone.style.backgroundImage).not.toBe('');
    expect(within(matrix).getByText('confirm in scope')).toBeInTheDocument();
    expect(within(matrix).getByText(/left untinted: they are not a gap to close/)).toBeInTheDocument();
  });

  it('treats unmapped hosts as ordinary when the project declares no scope at all', async () => {
    coverageMock.mockResolvedValue({
      ...coverage,
      matrix: {
        group_by: 'site',
        segments: [{ key: 'unmapped', label: 'Outside scoped subnets', hosts: 87 }],
        rows: [{ domain: 'web_tls', label: 'Web / TLS', cells: [c('unmapped', 30, 9)] }],
      },
    });
    await renderPage();
    const list = screen.getByText('Largest gaps').closest('section')!;
    expect(within(list).getByText('Collect Web / TLS evidence and upload it.')).toBeInTheDocument();
    expect(within(list).queryByText(/Confirm these hosts are in scope/)).toBeNull();
  });

  it('truncates a 220-character segment label inside a fixed table', async () => {
    await renderPage();
    const matrix = screen.getByText('Where the gaps are').closest('section')!;
    const header = within(matrix).getAllByTitle(LONG)[0];
    // Two lines, then clamped — "Outside scoped subnets" must fit; 220 chars must not.
    expect(header.className).toMatch(/line-clamp-2/);
    expect(header.className).toMatch(/break-words/);
    expect(matrix.querySelector('table')!.style.tableLayout).toBe('fixed');
  });
});

describe('Evidence — the lead (5.262.0)', () => {
  beforeEach(() => { coverageMock.mockReset().mockResolvedValue(coverage); });

  it('opens with how many domains are complete and the largest in-scope gap', async () => {
    await renderPage();
    // The 18 hosts outside every scoped subnet are the biggest number, but they
    // are not a collection task — the lead names the largest IN-SCOPE gap.
    expect(screen.getByText(/0 of 2 assessment domains cover every eligible host/)).toHaveTextContent(
      'the largest gap is Authentication / SMB / AD in 10.8.0.0/24 — 11 of 11 hosts not assessed.',
    );
  });
});

describe('Evidence — whether the vulnerability scan authenticated', () => {
  const withVulns = (credentialed?: object, numerator = 412) => ({
    ...coverage,
    domains: [
      ...coverage.domains,
      { ...domain('vuln_assessment', 'Vulnerability assessment', numerator, 500), ...(credentialed ? { credentialed } : {}) },
    ],
  });

  it('says how many assessed hosts were scanned with credentials, and each count opens its hosts', async () => {
    coverageMock.mockReset().mockResolvedValue(
      withVulns({ credentialed: 300, not_credentialed: 40, credentials_not_stated: 72 }),
    );
    await renderPage();
    const line = screen.getByTestId('credentialed-line');
    expect(line).toHaveTextContent(
      'Vulnerability assessment — 412 assessed: 300 credentialed, 40 not credentialed, 72 not stated.',
    );
    // The number is there to support a judgment, and all three stay "assessed".
    expect(line).toHaveTextContent('weaker evidence');
    expect(line).toHaveTextContent('all three count as assessed.');
    const href = (name: RegExp) => within(line).getByRole('link', { name }).getAttribute('href');
    expect(href(/300 credentialed/)).toBe('/hosts?q=vulnscan%3Acredentialed');
    expect(href(/40 not credentialed/)).toBe('/hosts?q=vulnscan%3Auncredentialed');
    expect(href(/72 not stated/)).toBe('/hosts?q=vulnscan%3Aunstated');
  });

  it('a zero count is a plain number, not a link to an empty list', async () => {
    coverageMock.mockReset().mockResolvedValue(
      withVulns({ credentialed: 0, not_credentialed: 0, credentials_not_stated: 5 }, 5),
    );
    await renderPage();
    expect(within(screen.getByTestId('credentialed-line')).getAllByRole('link')).toHaveLength(1);
  });

  it('shows nothing when no host is assessed or the server sends no counts', async () => {
    coverageMock.mockReset().mockResolvedValue(withVulns(undefined));
    await renderPage();
    expect(screen.queryByTestId('credentialed-line')).toBeNull();
  });
});
