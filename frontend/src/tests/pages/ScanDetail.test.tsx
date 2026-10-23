/**
 * The scan page (/scans/:id) — screenshot review 2026-09-23 (v5.288.0): the
 * summary was four bordered stat cards plus a card for the import result and
 * one for the tabs; a missing hostname read "N/A" here and "No hostname"
 * everywhere else.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
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

const Address = () => <output data-testid="address">{useLocation().search}</output>;

const renderPage = (entry = '/scans/9') =>
  render(
    <MemoryRouter initialEntries={[entry]}>
      <TooltipProvider>
        <Routes>
          <Route path="/scans/:scanId" element={<><ScanDetail /><Address /></>} />
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
    total: 1,
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

  // Review 2026-10-22, B3-4 — the batch a file arrived in was sent and shown nowhere.
  it('names the upload batch the file arrived in', async () => {
    api.getScan.mockResolvedValue({
      id: 9, filename: 'sweep.xml', tool_name: 'nmap', created_at: '2026-09-19T10:00:00Z',
      total_hosts: 2, up_hosts: 0, total_ports: 4, open_ports: 3, batch_id: 4, batch_label: 'Internal sweep, day 2',
    });
    renderPage();
    expect(await screen.findByTestId('scan-batch')).toHaveTextContent('In upload batch: Internal sweep, day 2');
  });

  it('names an unlabelled batch by its number, and says nothing for a file that came alone', async () => {
    api.getScan.mockResolvedValue({
      id: 9, filename: 'sweep.xml', tool_name: 'nmap', created_at: '2026-09-19T10:00:00Z',
      total_hosts: 2, up_hosts: 0, total_ports: 4, open_ports: 3, batch_id: 4, batch_label: null,
    });
    const { unmount } = renderPage();
    expect(await screen.findByTestId('scan-batch')).toHaveTextContent('In upload batch: #4');
    unmount();
    api.getScan.mockResolvedValue({
      id: 9, filename: 'sweep.xml', tool_name: 'nmap', created_at: '2026-09-19T10:00:00Z',
      total_hosts: 2, up_hosts: 0, total_ports: 4, open_ports: 3, batch_id: null,
    });
    renderPage();
    await screen.findByTestId('scan-measures');
    expect(screen.queryByTestId('scan-batch')).toBeNull();
  });

  // Review 2026-10-01 — host_scan_history.credentialed was stored and shown nowhere.
  it('has no Authenticated column when the scan said nothing about logging in', async () => {
    renderPage();
    await screen.findByText('No hostname');
    expect(screen.queryByRole('columnheader', { name: 'Authenticated' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('scanned-ports')).not.toBeInTheDocument();
  });

  it('says per host whether the scan authenticated, and leaves "did not say" blank', async () => {
    api.getScanHostSnapshots.mockResolvedValue({
      items: [
        { host_id: 1, ip_address: '10.0.0.1', hostname_at_scan: 'a.corp', state_at_scan: 'up', host_created: false, credentialed: true, open_port_count: 0, observed_port_count: 0 },
        { host_id: 2, ip_address: '10.0.0.2', hostname_at_scan: 'b.corp', state_at_scan: 'up', host_created: false, credentialed: false, open_port_count: 0, observed_port_count: 0 },
        { host_id: 3, ip_address: '10.0.0.3', hostname_at_scan: 'c.corp', state_at_scan: 'up', host_created: false, credentialed: null, open_port_count: 0, observed_port_count: 0 },
      ],
      total: 3,
    });
    renderPage();
    expect(await screen.findByRole('columnheader', { name: 'Authenticated' })).toBeInTheDocument();
    const rowOf = (ip: string) => screen.getByRole('button', { name: ip }).closest('tr')!;
    expect(rowOf('10.0.0.1')).toHaveTextContent('Credentialed');
    expect(rowOf('10.0.0.1')).not.toHaveTextContent('Not credentialed');
    expect(rowOf('10.0.0.2')).toHaveTextContent('Not credentialed');
    expect(rowOf('10.0.0.3')).not.toHaveTextContent(/credentialed/i);
  });

  it('says per host what the scan did to it: new here, renamed here, or seen before', async () => {
    api.getScanHostSnapshots.mockResolvedValue({
      items: [
        { host_id: 1, ip_address: '10.0.0.1', hostname_at_scan: 'a.corp', state_at_scan: 'up', host_created: true, hostname_changed: false, open_port_count: 0, observed_port_count: 0 },
        { host_id: 2, ip_address: '10.0.0.2', hostname_at_scan: 'b.corp', state_at_scan: null, host_created: false, hostname_changed: true, open_port_count: 0, observed_port_count: 0 },
        { host_id: 3, ip_address: '10.0.0.3', hostname_at_scan: 'c.corp', state_at_scan: null, host_created: false, hostname_changed: false, open_port_count: 0, observed_port_count: 0 },
        // An older server does not say.
        { host_id: 4, ip_address: '10.0.0.4', hostname_at_scan: null, state_at_scan: null, host_created: false, open_port_count: 0, observed_port_count: 0 },
      ],
      total: 4,
    });
    renderPage();
    const rowOf = (ip: string) => screen.getByRole('button', { name: ip }).closest('tr')!;
    await screen.findByRole('button', { name: '10.0.0.1' });
    expect(rowOf('10.0.0.1')).toHaveTextContent('New here');
    expect(rowOf('10.0.0.2')).toHaveTextContent('Renamed here');
    expect(rowOf('10.0.0.3')).toHaveTextContent('Seen before');
    expect(rowOf('10.0.0.4')).toHaveTextContent('Seen before');
  });

  // Review 2026-10-16, A7 / A9: counts from the scan, lists paged, the tab
  // and the view in the address.
  it('pages the as-scanned hosts and reads no current host records for it', async () => {
    api.getScan.mockResolvedValue({
      id: 9, filename: 'sweep.xml', tool_name: 'nmap', created_at: '2026-09-19T10:00:00Z',
      total_hosts: 120, up_hosts: 100, total_ports: 4, open_ports: 3,
    });
    api.getScanHostSnapshots.mockImplementation(async (_p: number, _s: number, page: { offset: number; limit: number }) => ({
      items: Array.from({ length: page.limit }, (_, i) => ({
        host_id: page.offset + i + 1, ip_address: `10.0.1.${page.offset + i + 1}`, hostname_at_scan: null,
        state_at_scan: 'up', host_created: false, open_port_count: 0, observed_port_count: 0,
      })),
      total: 120,
    }));
    renderPage('/scans/9?page=2');
    expect(await screen.findByText('51–100 of 120')).toBeInTheDocument();
    expect(api.getScanHostSnapshots).toHaveBeenCalledWith(1, 9, { offset: 50, limit: 50 }, expect.anything());
    expect(screen.getByTestId('scan-measures')).toHaveTextContent('100/120');
    expect(api.getHostsByScan).not.toHaveBeenCalled();
    expect(api.getScanPorts).not.toHaveBeenCalled();
  });

  it('lists the ports as the scan recorded them, under the scan\'s own port count', async () => {
    api.getScanPorts.mockResolvedValue({
      items: [
        { host_id: 1, ip_address: '10.0.0.1', port_number: 443, protocol: 'tcp', state_at_scan: 'open', service_name: 'https', service_product: 'nginx', service_version: '1.25' },
        { host_id: 1, ip_address: '10.0.0.1', port_number: 8080, protocol: 'tcp', state_at_scan: 'filtered', service_name: null, service_product: null, service_version: null },
      ],
      total: 4,
    });
    renderPage('/scans/9?tab=ports');
    expect(await screen.findByText('nginx 1.25')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Ports \(4\)/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('filtered')).toBeInTheDocument();
    expect(screen.getByText('1–2 of 4')).toBeInTheDocument();
    expect(api.getScanHostSnapshots).not.toHaveBeenCalled();
    expect(api.getHostsByScan).not.toHaveBeenCalled();
  });

  // Found in the browser: the count arriving with the scan sent a named page
  // of the current view back to the first.
  it('opens the page of the current view that the address names', async () => {
    api.getScan.mockResolvedValue({
      id: 9, filename: 'sweep.xml', tool_name: 'nmap', created_at: '2026-09-19T10:00:00Z',
      total_hosts: 120, up_hosts: 100, total_ports: 4, open_ports: 3,
    });
    api.getHostsByScan.mockImplementation(async (_p: number, _s: number, q: { skip: number; limit: number }) =>
      Array.from({ length: Math.min(q.limit, 120 - q.skip) }, (_, i) => ({
        id: q.skip + i + 1, ip_address: `10.0.2.${q.skip + i + 1}`, hostname: null, state: 'up', os_name: null, ports: [],
      })));
    renderPage('/scans/9?view=current&page=3');
    expect(await screen.findByText('101–120 of 120')).toBeInTheDocument();
    expect(api.getHostsByScan).toHaveBeenCalledWith(1, 9, { skip: 100, limit: 50 }, expect.anything());
    expect(api.getHostsByScan).not.toHaveBeenCalledWith(1, 9, { skip: 0, limit: 50 }, expect.anything());
    expect(screen.getByTestId('address')).toHaveTextContent('page=3');
  });

  it('keeps the tab and the view in the address, each choice starting on page one', async () => {
    renderPage('/scans/9?page=3');
    await screen.findByRole('button', { name: 'As scanned' });
    fireEvent.click(screen.getByRole('button', { name: 'Current inventory' }));
    await waitFor(() => expect(screen.getByTestId('address')).toHaveTextContent('?view=current'));
    expect(await screen.findByText('db.corp')).toBeInTheDocument();
    expect(api.getHostsByScan).toHaveBeenCalledWith(1, 9, { skip: 0, limit: 50 }, expect.anything());
    fireEvent.mouseDown(screen.getByRole('tab', { name: /Ports/ }));
    await waitFor(() => expect(screen.getByTestId('address')).toHaveTextContent('?view=current&tab=ports'));
  });

  // nmap's scanned port list: returned by GET /scans/{id} since forever, read by nothing.
  it('prints what the scan was asked to probe, one entry per protocol', async () => {
    api.getScan.mockResolvedValue({
      id: 9, filename: 'sweep.xml', tool_name: 'nmap', created_at: '2026-09-19T10:00:00Z',
      total_hosts: 2, up_hosts: 0, total_ports: 4, open_ports: 3,
      scan_info: [
        { id: 1, scan_id: 9, type: 'syn', protocol: 'tcp', numservices: 1000, services: '1-1000' },
        { id: 2, scan_id: 9, type: 'udp', protocol: 'udp', numservices: 2, services: '53,161' },
      ],
    });
    renderPage();
    const line = await screen.findByTestId('scanned-ports');
    expect(line).toHaveTextContent('Scanned: tcp 1-1000 · udp 53,161');
    expect(screen.queryByRole('button', { name: 'Show the whole list' })).not.toBeInTheDocument();
  });

  it('clamps a long port list behind an expander and survives empty rows', async () => {
    const long = Array.from({ length: 400 }, (_, i) => String(i * 7 + 1)).join(',');
    api.getScan.mockResolvedValue({
      id: 9, filename: 'sweep.xml', tool_name: 'nmap', created_at: '2026-09-19T10:00:00Z',
      total_hosts: 2, up_hosts: 0, total_ports: 4, open_ports: 3,
      scan_info: [
        { id: 1, scan_id: 9, type: 'syn', protocol: 'tcp', numservices: 400, services: long },
        { id: 2, scan_id: 9, type: null, protocol: null, numservices: null, services: null },
        { id: 3, scan_id: 9, type: 'udp', protocol: 'udp', numservices: 5, services: null },
      ],
    });
    renderPage();
    const line = await screen.findByTestId('scanned-ports');
    expect(line).toHaveTextContent('· udp 5 ports');
    expect(line.querySelector('p')).toHaveClass('line-clamp-2', 'break-all');
    const toggle = screen.getByRole('button', { name: 'Show the whole list' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    expect(line.querySelector('p')).not.toHaveClass('line-clamp-2');
    expect(screen.getByRole('button', { name: 'Show less' })).toHaveAttribute('aria-expanded', 'true');
  });
});
