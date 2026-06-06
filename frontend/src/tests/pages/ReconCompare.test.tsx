/**
 * Tests for the v2.52.0 recon comparison page.
 *
 * Pins the diff contract after the alpha.8→2.52.0 rewrite:
 *   - 422 when either ?a= or ?b= is missing or they collide
 *   - side-by-side session cards render attribution + host counts
 *     from each side's ``host_stats``
 *   - stats delta panel renders A/B/Δ rows for counts + by-tool +
 *     top services
 *   - "only in A" / "only in B" sample cards render the capped lists
 *     from the new diff endpoint with deep-link CTAs to Inventory
 */
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Routes, Route } from 'react-router-dom';

const navigateSpy = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>(
    'react-router-dom',
  );
  return {
    ...actual,
    useNavigate: () => navigateSpy,
    useParams: () => ({}),
  };
});

vi.mock('../../services/api', () => ({
  getReconSession: vi.fn(),
  diffReconSessions: vi.fn(),
  getCurrentProjectId: vi.fn(() => 1),
  setCurrentProjectId: vi.fn(),
}));

import * as api from '../../services/api';
import ReconCompare from '../../pages/ReconCompare';

const mockedApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;


function renderAt(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
          <Route path="/recon/compare" element={<ReconCompare />} />
        </Routes>
</MemoryRouter>,
  );
}

// v2.52.0 — fixtures use host_stats (not the per-host array).  The
// page no longer iterates hosts client-side; the comparison reads
// stats + a small server-computed sample of differing hosts.
const baseSession = (
  id: number,
  model: string,
  scanId: number,
  stats: {
    host_count: number;
    host_count_with_open_ports: number;
    by_tool: { tool_name: string; scan_count: number; host_count: number; port_count: number }[];
    top_services: { service_name: string; host_count: number }[];
  },
) => ({
  summary: {
    id,
    project_id: 1,
    scope_id: 7,
    scope_name: 'Internal /24',
    status: 'completed',
    started_at: '2026-05-15T10:00:00Z',
    completed_at: '2026-05-15T11:00:00Z',
    generated_by_model: model,
    generated_by_tool: 'claude-code',
    uploads_submitted: 2,
    scans_ingested: 2,
    hosts_discovered: stats.host_count,
    ports_discovered: stats.by_tool.reduce((acc, t) => acc + t.port_count, 0),
  },
  uploads: [
    {
      job_id: id * 10,
      filename: `scan-${id}.xml`,
      status: 'completed',
      scan_id: scanId,
      created_at: '2026-05-15T10:00:00Z',
      completed_at: '2026-05-15T10:05:00Z',
      skipped_count: 0,
    },
  ],
  host_stats: {
    ...stats,
    top_open_ports: [],
  },
  hosts: [],
  plans_generated: [],
});

const detailA = baseSession(42, 'claude-opus-4-7', 100, {
  host_count: 10,
  host_count_with_open_ports: 8,
  by_tool: [
    { tool_name: 'nmap', scan_count: 1, host_count: 10, port_count: 30 },
  ],
  top_services: [
    { service_name: 'http', host_count: 8 },
    { service_name: 'ssh', host_count: 5 },
  ],
});

const detailB = baseSession(43, 'gpt-5-codex', 200, {
  host_count: 12,
  host_count_with_open_ports: 9,
  by_tool: [
    { tool_name: 'nmap', scan_count: 1, host_count: 12, port_count: 35 },
    { tool_name: 'httpx', scan_count: 1, host_count: 9, port_count: 14 },
  ],
  top_services: [
    { service_name: 'http', host_count: 9 },
    { service_name: 'ssh', host_count: 5 },
    { service_name: 'https', host_count: 4 },
  ],
});

const diffFixture = {
  session_a_id: 42,
  session_b_id: 43,
  stats_a: detailA.host_stats,
  stats_b: detailB.host_stats,
  in_a_not_b_count: 1,
  in_b_not_a_count: 3,
  shared_count: 9,
  in_a_not_b_sample: [
    { host_id: 101, ip_address: '10.0.0.6', hostname: 'db01' },
  ],
  in_b_not_a_sample: [
    { host_id: 200, ip_address: '10.0.0.7', hostname: 'mail01' },
    { host_id: 201, ip_address: '10.0.0.8', hostname: 'new-host' },
    { host_id: 202, ip_address: '10.0.0.9', hostname: null },
  ],
  limit: 50,
};

beforeEach(() => {
  vi.clearAllMocks();
  navigateSpy.mockReset();
  mockedApi.getReconSession.mockImplementation(async (id: number) => {
    if (id === 42) return detailA;
    if (id === 43) return detailB;
    throw new Error(`unknown session ${id}`);
  });
  mockedApi.diffReconSessions.mockResolvedValue(diffFixture);
});

describe('ReconCompare page', () => {
  it('warns when ?a= or ?b= is missing', async () => {
    renderAt('/recon/compare?a=42');
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
    expect(screen.getByRole('alert').textContent).toMatch(/both/i);
  });

  it('warns when ?a= and ?b= point at the same session', async () => {
    renderAt('/recon/compare?a=42&b=42');
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
    expect(screen.getByRole('alert').textContent).toMatch(/different/i);
  });

  it('renders both session cards with their attribution', async () => {
    renderAt('/recon/compare?a=42&b=43');
    await waitFor(() => {
      expect(screen.getByText(/Recon #42/)).toBeInTheDocument();
      expect(screen.getByText(/Recon #43/)).toBeInTheDocument();
    });
    expect(screen.getByText(/claude-opus-4-7/)).toBeInTheDocument();
    expect(screen.getByText(/gpt-5-codex/)).toBeInTheDocument();
  });

  it('renders the stats delta panel with by-tool and service rows', async () => {
    renderAt('/recon/compare?a=42&b=43');
    await waitFor(() => {
      expect(screen.getByText(/Delta/)).toBeInTheDocument();
    });
    // Counter rows from the stats delta table.
    expect(screen.getByText('Distinct hosts')).toBeInTheDocument();
    expect(screen.getByText('Hosts with open ports')).toBeInTheDocument();
    // by-tool section — httpx exists only in B, should render the
    // "new in B" badge.
    expect(screen.getByText('By tool')).toBeInTheDocument();
    expect(screen.getByText('httpx')).toBeInTheDocument();
    expect(screen.getByText(/new in B/i)).toBeInTheDocument();
    // top services union — https exists only in B
    expect(screen.getByText('Top services')).toBeInTheDocument();
    expect(screen.getByText('https')).toBeInTheDocument();
  });

  it('renders capped "only in A" / "only in B" sample cards from the diff endpoint', async () => {
    renderAt('/recon/compare?a=42&b=43');
    await waitFor(() => {
      expect(screen.getByText('Only in Run A')).toBeInTheDocument();
    });
    expect(screen.getByText('Only in Run B')).toBeInTheDocument();
    // Count chips at the top of the diff section
    expect(screen.getByText(/1 only in A/)).toBeInTheDocument();
    expect(screen.getByText(/3 only in B/)).toBeInTheDocument();
    expect(screen.getByText(/9 shared/)).toBeInTheDocument();
    // Sample rows render
    expect(screen.getByText('10.0.0.6')).toBeInTheDocument();
    expect(screen.getByText('10.0.0.7')).toBeInTheDocument();
    expect(screen.getByText('10.0.0.8')).toBeInTheDocument();
  });

  it('navigates to Inventory with each side\'s scan_ids when "View in Inventory" is clicked', async () => {
    renderAt('/recon/compare?a=42&b=43');
    await waitFor(() => {
      expect(screen.getByText('Only in Run A')).toBeInTheDocument();
    });
    // Two "View in Inventory" buttons — A's and B's
    const buttons = screen.getAllByRole('button', { name: /View in Inventory/i });
    expect(buttons).toHaveLength(2);
    fireEvent.click(buttons[0]);
    expect(navigateSpy).toHaveBeenCalledWith('/hosts?scan_ids=100');
    navigateSpy.mockReset();
    fireEvent.click(buttons[1]);
    expect(navigateSpy).toHaveBeenCalledWith('/hosts?scan_ids=200');
  });
});
