/**
 * Compare scans (/scans/compare) — the page's first test (5.410.0;
 * comprehensive review 2026-10-16, C1 / A8): the counts open their exact
 * lists, every Open and View is a link, a list says "the first N of M", and a
 * failed read says so with Retry.
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({ compareScans: vi.fn(), getScans: vi.fn() }));
vi.mock('../../services/api', () => api);

import ScanDiff from '../../pages/ScanDiff';
import { TooltipProvider } from '../../components/ui/tooltip';

const side = (id: number, over: Record<string, unknown> = {}) => ({
  scan_id: id, filename: `scan-${id}.xml`, tool_name: 'nmap', scan_type: 'nmap_xml',
  created_at: '2026-10-01T10:00:00Z', total_hosts: 10, up_hosts: 9, total_ports: 40, open_ports: 30, ...over,
});
const host = (id: number, ip: string) => ({ host_id: id, ip_address: ip, hostname: null });

const DIFF = {
  scan_a: side(1),
  scan_b: side(2, { total_hosts: 13, open_ports: 33 }),
  counts: { new_hosts: 5, dropped_hosts: 2, host_state_changes: 0, newly_open_ports: 3, closed_ports: 0, not_observed_ports: 0 },
  row_cap: 2,
  new_hosts: [host(11, '10.0.0.11'), host(12, '10.0.0.12')],
  dropped_hosts: [host(21, '10.0.1.1'), host(22, '10.0.1.2')],
  host_state_changes: [],
  newly_open_ports: [{ host_id: 11, ip_address: '10.0.0.11', port_number: 22, protocol: 'tcp', service_name: 'ssh', state_a: null, state_b: 'open' }],
  closed_ports: [],
  not_observed_ports: [],
};

const renderAt = (url: string) => render(
  <TooltipProvider>
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/scans/compare" element={<ScanDiff />} />
      </Routes>
    </MemoryRouter>
  </TooltipProvider>,
);

/** The Hosts query a link opens. */
const hostsQuery = (el: HTMLElement) => {
  const href = el.getAttribute('href') ?? '';
  expect(href.startsWith('/hosts?')).toBe(true);
  return new URLSearchParams(href.split('?')[1]).get('q');
};

beforeEach(() => vi.clearAllMocks());

describe('Compare scans', () => {
  it('new and dropped hosts open their exact list on Hosts — never every host of a scan (C1)', async () => {
    api.compareScans.mockResolvedValue(DIFF);
    renderAt('/scans/compare?a=1&b=2');

    const all = await screen.findByTestId('compare-new-hosts-all');
    expect(all).toHaveTextContent('All 5 hosts on Hosts');
    expect(hostsQuery(all)).toBe('scan:2 AND NOT scan:1');
    expect(hostsQuery(screen.getByTestId('compare-dropped-hosts-all'))).toBe('scan:1 AND NOT scan:2');
    // The strip's numbers open the same lists.
    const strip = screen.getByTestId('compare-measures');
    expect(hostsQuery(within(strip).getByRole('link', { name: 'Show the hosts B saw that A did not' }))).toBe('scan:2 AND NOT scan:1');
    expect(api.compareScans).toHaveBeenCalledWith(expect.any(Number), 1, 2, expect.any(AbortSignal));
  });

  it('a list says it is the first N of M; every Open is a link to the host', async () => {
    api.compareScans.mockResolvedValue(DIFF);
    renderAt('/scans/compare?a=1&b=2');

    const table = await screen.findByTestId('compare-new-hosts');
    expect(screen.getAllByText('The first 2 of 5, by address.').length).toBeGreaterThan(0);
    const opens = within(table).getAllByRole('link', { name: 'Open' });
    expect(opens.map((a) => a.getAttribute('href'))).toEqual(['/hosts/11', '/hosts/12']);
    // Each scan opens its own page, with its tool and type said.
    const sides = screen.getByTestId('compare-sides');
    expect(within(sides).getByRole('link', { name: /#2 · scan-2\.xml/ })).toHaveAttribute('href', '/scans/2');
    expect(sides).toHaveTextContent('nmap · nmap_xml');
    expect(sides).toHaveTextContent('+3');   // hosts, B minus A
  });

  it('an empty list says so and offers no link to an empty Hosts list', async () => {
    api.compareScans.mockResolvedValue({ ...DIFF, counts: { ...DIFF.counts, dropped_hosts: 0 }, dropped_hosts: [] });
    renderAt('/scans/compare?a=1&b=2');
    expect(await screen.findByText('B saw every host A saw.')).toBeInTheDocument();
    expect(screen.queryByTestId('compare-dropped-hosts-all')).toBeNull();
  });

  it('a failed comparison says so, and Retry asks again', async () => {
    api.compareScans
      .mockRejectedValueOnce({ response: { status: 500, data: { detail: 'Server error' } } })
      .mockResolvedValueOnce(DIFF);
    renderAt('/scans/compare?a=1&b=2');
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByTestId('compare-new-hosts')).toBeInTheDocument();
    expect(api.compareScans).toHaveBeenCalledTimes(2);
  });

  it('without two different scans in the address, the picker; it says how many scans it lists', async () => {
    api.getScans.mockResolvedValue(Array.from({ length: 200 }, (_, i) => ({ id: i + 1, filename: `s${i}.xml`, created_at: null })));
    renderAt('/scans/compare?a=3&b=3');
    expect(await screen.findByTestId('compare-picker')).toBeInTheDocument();
    expect(screen.getByText(/The lists hold the newest 200 scans\./)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Compare/ })).toBeDisabled();
    expect(api.compareScans).not.toHaveBeenCalled();
  });

  it('a failed scan list says so with Retry; one scan is not enough', async () => {
    api.getScans.mockRejectedValueOnce({ response: { status: 500, data: { detail: 'Server error' } } })
      .mockResolvedValueOnce([{ id: 1, filename: 'only.xml', created_at: null }]);
    renderAt('/scans/compare');
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('This project needs at least two scans to compare.')).toBeInTheDocument();
  });
});
