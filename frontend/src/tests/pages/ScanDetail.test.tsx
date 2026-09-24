/**
 * The scan page (/scans/:id) — screenshot review 2026-09-23 (v5.288.0): the
 * summary was four bordered stat cards plus a card for the import result and
 * one for the tabs; a missing hostname read "N/A" here and "No hostname"
 * everywhere else.
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const api = vi.hoisted(() => {
  const named: Record<string, ReturnType<typeof vi.fn>> = {};
  return new Proxy(named, {
    get(target, prop: string) {
      if (prop === '__esModule') return true;
      if (prop === 'then') return undefined;
      if (!(prop in target)) target[prop] = vi.fn().mockResolvedValue([]);
      return target[prop];
    },
    has: () => true,
  });
});
vi.mock('../../services/api', () => api);
// setupTests stubs useParams with `{ id: '1' }`; this route's param is scanId.
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => vi.fn(), useParams: () => ({ scanId: '9' }) };
});
vi.mock('../../components/CommandExplanation', () => ({ default: () => null }));

import ScanDetail from '../../pages/ScanDetail';
import { TooltipProvider } from '../../components/ui/tooltip';

const renderPage = () =>
  render(
    <MemoryRouter initialEntries={['/scans/9']}>
      <TooltipProvider>
        <Routes>
          <Route path="/scans/:scanId" element={<ScanDetail />} />
        </Routes>
      </TooltipProvider>
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  api.getScan.mockResolvedValue({
    id: 9, filename: 'sweep.xml', tool_name: 'nmap', created_at: '2026-09-19T10:00:00Z',
    total_hosts: 2, up_hosts: 0, total_ports: 4, open_ports: 3,
  });
  api.getHostsByScan.mockResolvedValue([
    { id: 1, ip_address: '10.0.0.1', hostname: null, state: 'up', os_name: null, ports: [] },
    { id: 2, ip_address: '10.0.0.2', hostname: 'db.corp', state: 'up', os_name: null, ports: [] },
  ]);
  api.getScanDnsRecords.mockResolvedValue({ items: [], total: 0 });
  api.getScanHostSnapshots.mockResolvedValue({
    items: [
      { host_id: 1, ip_address: '10.0.0.1', hostname_at_scan: null, state_at_scan: null, host_created: true, open_port_count: 0, observed_port_count: 0 },
    ],
  });
  api.getScans.mockResolvedValue([]);
});

describe('ScanDetail', () => {
  it('says "No hostname", never "N/A"', async () => {
    renderPage();
    expect(await screen.findByText('No hostname')).toBeInTheDocument();
    expect(screen.queryByText('N/A')).not.toBeInTheDocument();
  });

  it('shows its summary as one strip of measures and sections, not bordered cards', async () => {
    renderPage();
    const strip = await screen.findByTestId('scan-measures');
    expect(strip).toHaveTextContent('Hosts up');
    expect(strip).toHaveTextContent('Open ports');
    expect(strip).toHaveTextContent('Scan window');
    // "0/2" beside hosts that exist says why.
    expect(strip).toHaveTextContent('none reported up');
    expect(document.querySelector('.rounded-panel.border.bg-card')).toBeNull();
    expect(screen.getByRole('heading', { name: 'What this scan recorded' })).toBeInTheDocument();
  });
});
