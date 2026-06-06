/**
 * Tests for the v3 alpha.6 Recon Run Detail page.
 *
 * Pins the four section contract: summary, uploads, hosts,
 * plans-generated-from.  Confirms cross-page navigation works
 * (open scan, open host, open plan).
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
    useParams: () => ({ sessionId: '42' }),
  };
});

vi.mock('../../services/api', () => ({
  getReconSession: vi.fn(),
  // v4.59.0 (NEW I) — ReconRunDetail also calls listReconSessions to
  // populate the "Compare with another recon" picker.  Pre-fix the
  // mock omitted it and every test that touched the page threw an
  // unhandled "No 'listReconSessions' export" rejection.  Return the
  // standard {items, total, skip, limit, has_more} envelope shape
  // the page consumes; an empty list is fine — the picker just
  // doesn't show a sibling option.
  listReconSessions: vi.fn(async () => ({
    items: [],
    total: 0,
    skip: 0,
    limit: 100,
    has_more: false,
  })),
  getCurrentProjectId: vi.fn(() => 1),
  setCurrentProjectId: vi.fn(),
}));

import * as api from '../../services/api';
import ReconRunDetail from '../../pages/ReconRunDetail';

const mockedApi = api as unknown as Record<string, ReturnType<typeof vi.fn>>;


function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/recon/runs/42']}>
      <Routes>
          <Route path="/recon/runs/:sessionId" element={<ReconRunDetail />} />
        </Routes>
</MemoryRouter>,
  );
}

const detailFixture = {
  summary: {
    id: 42,
    project_id: 1,
    scope_id: 7,
    scope_name: 'Internal /24',
    status: 'completed',
    started_at: '2026-05-15T10:00:00Z',
    completed_at: '2026-05-15T11:00:00Z',
    started_by_username: 'alice',
    agent_name: "alice's-agent",
    generated_by_model: 'claude-opus-4-7',
    generated_by_tool: 'claude-code',
    prompt_version: '1.13.0',
    uploads_submitted: 3,
    scans_ingested: 2,
    hosts_discovered: 12,
    ports_discovered: 47,
  },
  uploads: [
    {
      job_id: 100,
      filename: 'nmap.xml',
      status: 'completed',
      scan_id: 9,
      created_at: '2026-05-15T10:05:00Z',
      completed_at: '2026-05-15T10:07:00Z',
      skipped_count: 0,
    },
    {
      job_id: 101,
      filename: 'broken.json',
      status: 'failed',
      scan_id: null,
      created_at: '2026-05-15T10:10:00Z',
      completed_at: null,
      skipped_count: 0,
      last_error: 'masscan parser rejected empty body',
    },
  ],
  // v4.59.0 (NEW I) — v2.87.0 added paginated child lists; the page
  // reads uploads_total / plans_total for the "N of T" caption and
  // all_scan_ids for the Inventory deep-link.  Empty fixtures
  // omitted these and the page crashed mid-render.
  uploads_total: 2,
  uploads_skip: 0,
  uploads_limit: 50,
  plans_total: 1,
  plans_skip: 0,
  plans_limit: 50,
  all_scan_ids: [9],
  // v2.52.0 — Recon Run Detail no longer fetches the per-host array
  // by default.  The stats rollup is what the page renders; the host
  // array stays empty unless ?include_hosts=true is passed.
  host_stats: {
    host_count: 12,
    host_count_with_open_ports: 9,
    by_tool: [
      { tool_name: 'nmap', scan_count: 1, host_count: 12, port_count: 47 },
      { tool_name: 'httpx', scan_count: 1, host_count: 9, port_count: 14 },
    ],
    top_services: [
      { service_name: 'http', host_count: 9 },
      { service_name: 'ssh', host_count: 5 },
      { service_name: 'https', host_count: 4 },
    ],
    top_open_ports: [
      { port_number: 80, protocol: 'tcp', host_count: 9 },
      { port_number: 22, protocol: 'tcp', host_count: 5 },
      { port_number: 443, protocol: 'tcp', host_count: 4 },
    ],
  },
  hosts: [],
  plans_generated: [
    {
      plan_id: 11,
      title: 'Downstream plan',
      status: 'proposed',
      version: 1,
      entry_count: 8,
      created_at: '2026-05-15T11:30:00Z',
      generated_by_model: 'claude-opus-4-7',
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  navigateSpy.mockReset();
  mockedApi.getReconSession.mockResolvedValue(detailFixture);
});

describe('ReconRunDetail page', () => {
  it('renders the four sections from the detail endpoint', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/Recon run #42/)).toBeInTheDocument();
    });
    expect(screen.getByText(/Internal \/24/)).toBeInTheDocument();
    // ``claude-opus-4-7`` appears in summary AND on the plan row — accept
    // either; the point is it surfaced at all.
    expect(screen.getAllByText(/claude-opus-4-7/).length).toBeGreaterThan(0);
    // Tile labels — some collide with column headers further down the
    // page, so assert presence via getAllByText.
    expect(screen.getAllByText('Uploads').length).toBeGreaterThan(0);
    expect(screen.getByText('Scans ingested')).toBeInTheDocument();
    expect(screen.getAllByText('Hosts discovered').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Open ports').length).toBeGreaterThan(0);
    // v4.59.0 (NEW I) — 47 (ports_discovered) now appears in
    // multiple tiles after the host_stats rollup also surfaces it.
    // Use getAllByText since the exact-count uniqueness was lost.
    expect(screen.getAllByText('47').length).toBeGreaterThan(0);

    expect(screen.getByText(/Uploads \(2\)/)).toBeInTheDocument();
    expect(screen.getByText('nmap.xml')).toBeInTheDocument();
    expect(screen.getByText('broken.json')).toBeInTheDocument();
    expect(
      screen.getByText(/masscan parser rejected empty body/),
    ).toBeInTheDocument();

    // v2.52.0 — the Run Output panel replaces the old "Hosts
    // discovered" table.  Assert on the stats labels + a few
    // breakdown rows; the per-host table is gone by design.
    expect(screen.getByText('Run output')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /View 12 hosts in Inventory/i }),
    ).toBeInTheDocument();
    expect(screen.getByText('Distinct hosts')).toBeInTheDocument();
    expect(screen.getByText('Hosts with open ports')).toBeInTheDocument();
    expect(screen.getByText('Top services')).toBeInTheDocument();
    expect(screen.getByText('Top open ports')).toBeInTheDocument();
    // Per-tool row content
    expect(screen.getByText('httpx')).toBeInTheDocument();

    expect(
      screen.getByText('Plans generated from this recon'),
    ).toBeInTheDocument();
    expect(screen.getByText(/Downstream plan/)).toBeInTheDocument();
  });

  it('opens a scan via the upload row scan link', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('nmap.xml')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole('button', { name: /#9/ }));
    expect(navigateSpy).toHaveBeenCalledWith('/scans/9');
  });

  it('navigates to Inventory pre-filtered to this run\'s scans', async () => {
    renderPage();
    await waitFor(() => {
      expect(
        screen.getByRole('button', { name: /View 12 hosts in Inventory/i }),
      ).toBeInTheDocument();
    });
    fireEvent.click(
      screen.getByRole('button', { name: /View 12 hosts in Inventory/i }),
    );
    // Only upload with a scan_id is the nmap.xml one (scan_id=9);
    // broken.json had scan_id=null and is filtered out.
    expect(navigateSpy).toHaveBeenCalledWith('/hosts?scan_ids=9');
  });

  it('opens the test plan detail from a plan row', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText(/Downstream plan/)).toBeInTheDocument();
    });
    // v2.52.0 — the per-host Open buttons are gone with the hosts
    // table; the only "Open" buttons left are on plan rows.
    const openButtons = screen.getAllByRole('button', { name: /^Open/i });
    expect(openButtons.length).toBeGreaterThan(0);
    fireEvent.click(openButtons[0]);
    expect(navigateSpy).toHaveBeenCalledWith('/test-plans/11');
  });

  it('surfaces an error alert when the API rejects', async () => {
    mockedApi.getReconSession.mockRejectedValue(new Error('boom'));
    renderPage();
    await waitFor(() => {
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
  });
});
