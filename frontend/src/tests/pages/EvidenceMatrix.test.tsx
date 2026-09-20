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
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
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
    await waitFor(() => expect(gapsMock).toHaveBeenCalledWith('web_tls', expect.objectContaining({ segment: 'unmapped' })));
    expect(await within(matrix).findByText('192.168.9.9')).toBeInTheDocument();
    expect(within(matrix).getByText(/18 of 18/)).toBeInTheDocument();
    // One host listed of 18: the buttons say what they act on.
    expect(within(matrix).getByText(/Showing the first 1 of 18 — Copy and Plan act on these 1/)).toBeInTheDocument();

    // The whole-project figure opens the domain without a segment.
    fireEvent.click(within(matrix).getByRole('button', { name: /Web \/ TLS, whole project: 9 of 30/ }));
    await waitFor(() => expect(gapsMock).toHaveBeenLastCalledWith('web_tls', expect.objectContaining({ segment: undefined })));
  });

  it('ranks the largest gaps by hosts missing and gives each its next step', async () => {
    await renderPage();
    const list = screen.getByText('Largest gaps').closest('section')!;
    const rows = within(list).getAllByRole('row').slice(1);
    expect(rows.map((r) => within(r).getAllByRole('cell')[1].textContent)).toEqual(['18 of 18', '11 of 11', '3 of 12']);
    expect(within(rows[0]).getByText('Collect Web / TLS evidence and upload it.')).toBeInTheDocument();
  });

  it('truncates a 220-character segment label inside a fixed table', async () => {
    await renderPage();
    const matrix = screen.getByText('Where the gaps are').closest('section')!;
    const header = within(matrix).getAllByTitle(LONG)[0];
    expect(header.className).toMatch(/truncate/);
    expect(matrix.querySelector('table')!.style.tableLayout).toBe('fixed');
  });
});
