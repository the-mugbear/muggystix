import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const api = vi.hoisted(() => ({
  getHostWebInterfaces: vi.fn(),
  getHostNetexecResults: vi.fn(),
  getHostWebPaths: vi.fn(),
}));
vi.mock('../../services/api', () => api);
vi.mock('../../contexts/ToastContext', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}));

import PortDetailsCard from '../../components/host-inspector/PortDetailsCard';
import { TooltipProvider } from '../../components/ui/tooltip';
import { getConnectionHelpers } from '../../utils/connectionHelpers';
import type { Port } from '../../services/api';

const day = 86_400_000;
const iso = (offsetDays: number) => new Date(Date.now() + offsetDays * day).toISOString();

const https = { id: 443, port_number: 443, protocol: 'tcp', state: 'open', service_name: 'https' } as Port;
const alt = { id: 8443, port_number: 8443, protocol: 'tcp', state: 'open', service_name: 'https' } as Port;

const renderCard = (ports: Port[]) =>
  render(
    <TooltipProvider>
      <PortDetailsCard
        hostId={1}
        hostIp="10.0.0.5"
        openPorts={ports}
        closedPorts={[]}
        filteredPorts={[]}
        connectionHelpersByPort={new Map(ports.map((p) => [p.id, getConnectionHelpers('10.0.0.5', p, null)]))}
      />
    </TooltipProvider>,
  );

beforeEach(() => {
  vi.clearAllMocks();
});

describe('PortDetailsCard — named endpoints', () => {
  it('a shared-hosting port rolls up counts and keeps each website\'s TLS evidence separate', async () => {
    api.getHostWebInterfaces.mockResolvedValue([
      { id: 1, source: 'httpx', url: 'https://shop.example.com/', fqdn: 'shop.example.com', port_id: 443, cert_not_after: iso(200), last_seen: iso(-1), has_screenshot: false, scan_id: 1 },
      { id: 2, source: 'nikto', url: 'https://legacy.example.com/', fqdn: 'legacy.example.com', port_id: 443, cert_not_after: iso(-10), tls_weak_protocol: true, last_seen: iso(-3), has_screenshot: false, scan_id: 2 },
    ]);
    renderCard([https]);

    // The row says how many, never whose: the old cell showed only the
    // newest interface's cert as if it were the port's.
    const trigger = await screen.findByRole('button', { name: /TLS evidence for 2 endpoints on port 443/ });
    expect(trigger).toHaveTextContent('2 endpoints');
    expect(trigger).toHaveTextContent('1 weak TLS · 1 expired');
    expect(screen.queryByText(/cert expires/)).not.toBeInTheDocument();

    fireEvent.click(trigger);
    const items = await screen.findAllByRole('listitem');
    const legacy = items.find((li) => within(li).queryByText('legacy.example.com'))!;
    const shop = items.find((li) => within(li).queryByText('shop.example.com'))!;
    expect(within(legacy).getByText(/cert expired 10d ago/)).toBeInTheDocument();
    expect(within(legacy).getByText('weak TLS')).toBeInTheDocument();
    expect(within(legacy).getByText(/nikto/)).toBeInTheDocument();
    expect(within(shop).getByText(/cert expires/)).toBeInTheDocument();
    expect(within(shop).queryByText('weak TLS')).not.toBeInTheDocument();
  });

  it('a single endpoint shows its facts with the name they belong to', async () => {
    api.getHostWebInterfaces.mockResolvedValue([
      { id: 1, source: 'httpx', url: 'https://portal.example.com:8443/', fqdn: 'portal.example.com', port_id: 8443, cert_self_signed: true, last_seen: iso(-2), has_screenshot: false, scan_id: 1 },
    ]);
    renderCard([alt]);
    expect(await screen.findByText('self-signed')).toBeInTheDocument();
    expect(screen.getByText(/^portal\.example\.com ·/)).toBeInTheDocument();
  });

  it('connection helpers address the named endpoint, and can be switched back to the address', async () => {
    api.getHostWebInterfaces.mockResolvedValue([
      { id: 1, source: 'httpx', url: 'https://shop.example.com/', fqdn: 'shop.example.com', port_id: 443, last_seen: iso(-1), has_screenshot: false, scan_id: 1 },
      { id: 2, source: 'httpx', url: 'https://x/', fqdn: 'evil.example.com; rm -rf ~', port_id: 443, last_seen: iso(-1), has_screenshot: false, scan_id: 1 },
    ]);
    renderCard([https]);
    await waitFor(() => expect(api.getHostWebInterfaces).toHaveBeenCalled());
    fireEvent.click(await screen.findByRole('button', { name: 'Connection helpers for port 443' }));

    const select = await screen.findByLabelText(/Endpoint —/);
    // A name that is not plainly a hostname is never offered for a command.
    expect(within(select as HTMLElement).queryByText(/rm -rf/)).not.toBeInTheDocument();
    expect(select).toHaveValue('shop.example.com');
    expect(screen.getByText('curl -ik --resolve shop.example.com:443:10.0.0.5 https://shop.example.com/')).toBeInTheDocument();

    fireEvent.change(select, { target: { value: '' } });
    expect(await screen.findByText('curl -ik https://10.0.0.5/')).toBeInTheDocument();
    expect(screen.queryByText(/--resolve/)).not.toBeInTheDocument();
  });
});

// v5.240.0 — the median host has three open ports; the table stopped spending
// columns and rows on things that say nothing.
describe('PortDetailsCard — density', () => {
  const ssh = {
    id: 22, port_number: 22, protocol: 'tcp', state: 'open', service_name: 'ssh',
    service_product: 'OpenSSH', service_version: '8.9', service_method: 'probed', service_conf: 10,
    reason: 'syn-ack',
  } as unknown as Port;
  const closed = { id: 81, port_number: 81, protocol: 'tcp', state: 'closed', service_name: 'http', reason: 'reset' } as unknown as Port;
  const filtered = { id: 445, port_number: 445, protocol: 'tcp', state: 'filtered', service_name: 'microsoft-ds' } as unknown as Port;

  const renderWith = (open: Port[], closedPorts: Port[] = [], filteredPorts: Port[] = []) =>
    render(
      <TooltipProvider>
        <PortDetailsCard
          hostId={1} hostIp="10.0.0.5" openPorts={open} closedPorts={closedPorts} filteredPorts={filteredPorts}
          connectionHelpersByPort={new Map()}
        />
      </TooltipProvider>,
    );

  it('draws no TLS or State column on a host with no TLS evidence, and keeps the row to one line', async () => {
    api.getHostWebInterfaces.mockResolvedValue([]);
    renderWith([ssh]);
    await waitFor(() => expect(api.getHostWebInterfaces).toHaveBeenCalled());
    expect(screen.queryByRole('columnheader', { name: 'TLS' })).not.toBeInTheDocument();
    expect(screen.queryByRole('columnheader', { name: 'State' })).not.toBeInTheDocument();
    // Why nmap called it open rides on the port cell; the column is gone.
    expect(screen.getByRole('cell', { name: /22\s*\/tcp/ })).toBeInTheDocument();
    expect(screen.getByTitle('open — syn-ack')).toHaveTextContent('22/tcp');
    expect(screen.getByText('OpenSSH 8.9')).toBeInTheDocument();
    // How it was detected moved to the tooltip; it used to double the row.
    expect(screen.queryByText(/conf 10/)).not.toBeInTheDocument();
    expect(screen.getByText('ssh')).toHaveAttribute('title', expect.stringContaining('nmap confidence 10/10'));
  });

  it('draws the TLS column as soon as one port has evidence', async () => {
    api.getHostWebInterfaces.mockResolvedValue([
      { id: 1, source: 'httpx', url: 'https://10.0.0.5/', fqdn: null, port_id: 443, cert_self_signed: true, last_seen: iso(-1), has_screenshot: false, scan_id: 1 },
    ]);
    renderWith([https, ssh]);
    expect(await screen.findByRole('columnheader', { name: 'TLS' })).toBeInTheDocument();
  });

  it('closed and filtered ports are one line until asked for', async () => {
    api.getHostWebInterfaces.mockResolvedValue([]);
    renderWith([ssh], [closed], [filtered]);
    const toggle = screen.getByRole('button', { name: '1 closed · 1 filtered · show' });
    expect(screen.queryByText('microsoft-ds')).not.toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.getByText('microsoft-ds')).toBeInTheDocument();
    expect(screen.getByText('reset')).toBeInTheDocument();
  });

  it('a failed evidence load says so instead of reading as "no TLS"', async () => {
    api.getHostWebInterfaces.mockRejectedValue(new Error('boom'));
    renderWith([ssh]);
    expect(await screen.findByText(/TLS evidence couldn’t be loaded/)).toBeInTheDocument();
  });

  // v5.297.0 — a service row summarises what is known about it and opens to
  // the evidence itself (it used to link to per-tool sections further down).
  it('summarises each service and opens it to its evidence', async () => {
    api.getHostWebInterfaces.mockResolvedValue([
      { id: 1, source: 'httpx', url: 'http://10.0.0.5/', fqdn: null, port_id: 443, last_seen: iso(-1), has_screenshot: false, scan_id: 1 },
    ]);
    const sshWithScripts = {
      ...ssh,
      scripts: [
        { id: 1, script_id: 'ssh-hostkey', output: 'rsa 3072' },
        { id: 2, script_id: 'ssh2-enum-algos', output: 'kex: ...' },
      ],
    } as unknown as Port;
    const vulns = [
      { id: 9, title: 'Exposed Tomcat Manager', severity: 'high', source: 'nessus', port_id: 443 },
      { id: 10, title: 'Missing header', severity: 'low', source: 'nikto', port_id: 443 },
    ];
    render(
      <MemoryRouter>
        <TooltipProvider>
          <PortDetailsCard hostId={1} hostIp="10.0.0.5" openPorts={[https, sshWithScripts]} closedPorts={[]}
            filteredPorts={[]} connectionHelpersByPort={new Map()} vulnerabilities={vulns as never} />
        </TooltipProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByRole('columnheader', { name: 'What’s here' })).toBeInTheDocument();
    expect(await screen.findByText('1 high +1')).toBeInTheDocument();
    expect(screen.getByText('web 1')).toBeInTheDocument();
    expect(screen.getByText('2 scripts')).toBeInTheDocument();

    // Collapsed until asked for (two open ports); the port cell opens it.
    expect(screen.queryByText('Tool output (2)')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show what is known about port 22' }));
    expect(screen.getByText('Tool output (2)')).toBeInTheDocument();
    expect(screen.getAllByText('raw text')).toHaveLength(2);

    fireEvent.click(screen.getByRole('button', { name: 'Show what is known about port 443' }));
    expect(screen.getByText('Weaknesses (2)')).toBeInTheDocument();
    expect(screen.getByText('Exposed Tomcat Manager')).toBeInTheDocument();
  });

  it('puts what logged in first and counts the failed attempts', async () => {
    api.getHostWebInterfaces.mockResolvedValue([]);
    const ftp = { id: 21, port_number: 21, protocol: 'tcp', state: 'open', service_name: 'ftp' } as Port;
    const nx = (id: number, auth: boolean | null, user: string | null, line: string) => ({
      id, scan_id: id, protocol: 'ftp', port: 21, auth_success: auth, username: user, raw_output: line,
      first_seen: iso(-1), tool: 'netexec',
    });
    api.getHostNetexecResults.mockResolvedValue([
      nx(1, null, null, 'FTP 10.0.0.5 21 10.0.0.5 [*] Banner: (vsFTPd 3.0.5)'),
      nx(2, true, '', 'FTP 10.0.0.5 21 10.0.0.5 [+] : - Anonymous Login!'),
      nx(3, false, 'admin', 'FTP 10.0.0.5 21 10.0.0.5 [-] admin (Response:530 Login incorrect.)'),
      nx(4, false, 'root', 'FTP 10.0.0.5 21 10.0.0.5 [-] root (Response:530 Login incorrect.)'),
    ]);
    render(
      <MemoryRouter>
        <TooltipProvider>
          <PortDetailsCard hostId={1} hostIp="10.0.0.5" openPorts={[ftp]} closedPorts={[]}
            filteredPorts={[]} connectionHelpersByPort={new Map()} netexecCount={4} />
        </TooltipProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByText('1 login worked')).toBeInTheDocument();
    // One open port: its panel is open.  FTP's word for it, not SMB's.
    expect(screen.getByText('Anonymous login')).toBeInTheDocument();
    const failed = screen.getByRole('button', { name: '2 failed attempts · show' });
    expect(screen.queryByText(/Login incorrect/)).not.toBeInTheDocument();
    fireEvent.click(failed);
    expect(screen.getAllByText(/Login incorrect/)).toHaveLength(2);
  });

  it('draws no summary column on a host with nothing recorded about its services', async () => {
    api.getHostWebInterfaces.mockResolvedValue([]);
    renderWith([ssh]);
    await waitFor(() => expect(api.getHostWebInterfaces).toHaveBeenCalled());
    expect(screen.queryByRole('columnheader', { name: 'What’s here' })).not.toBeInTheDocument();
  });

  // v5.299.0 — review 2026-09-25 R01: evidence was shown under OPEN ports
  // only, so a web-only import (httpx/WhatWeb/EyeWitness make no port) and
  // a port since closed hid what was collected.
  it('shows web evidence on a host with no port rows at all', async () => {
    api.getHostWebInterfaces.mockResolvedValue([
      { id: 1, source: 'httpx', url: 'https://app.example.com:8443/', fqdn: 'app.example.com', port_id: null, port: 8443,
        title: 'Staff portal', last_seen: iso(-1), has_screenshot: false, scan_id: 1 },
    ]);
    render(
      <MemoryRouter>
        <TooltipProvider>
          <PortDetailsCard hostId={1} hostIp="10.0.0.5" openPorts={[]} closedPorts={[]} filteredPorts={[]}
            connectionHelpersByPort={new Map()} />
        </TooltipProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByText('Evidence on no open port')).toBeInTheDocument();
    expect(screen.getByText(/not in the port list/)).toBeInTheDocument();
    expect(screen.getByText(/Staff portal/)).toBeInTheDocument();
  });

  it('keeps a closed port\'s paths reachable, labelled with its state', async () => {
    api.getHostWebInterfaces.mockResolvedValue([]);
    api.getHostWebPaths.mockResolvedValue([
      { url: 'http://10.0.0.5:81/backup.zip', path: '/backup.zip', status_code: 200, size: 10, source: 'ffuf', port: 81, scans: 1 },
    ]);
    render(
      <MemoryRouter>
        <TooltipProvider>
          <PortDetailsCard hostId={1} hostIp="10.0.0.5" openPorts={[ssh]} closedPorts={[closed]} filteredPorts={[]}
            connectionHelpersByPort={new Map()} webPathCount={1} />
        </TooltipProvider>
      </MemoryRouter>,
    );
    expect(await screen.findByText(':81 /backup.zip')).toBeInTheDocument();
    expect(screen.getByText(/closed now/)).toBeInTheDocument();
  });

  it('shows no such section when everything is on an open port', async () => {
    api.getHostWebInterfaces.mockResolvedValue([
      { id: 1, source: 'httpx', url: 'https://10.0.0.5/', fqdn: null, port_id: 443, last_seen: iso(-1), has_screenshot: false, scan_id: 1 },
    ]);
    renderWith([https]);
    await waitFor(() => expect(api.getHostWebInterfaces).toHaveBeenCalled());
    expect(screen.queryByText('Evidence on no open port')).not.toBeInTheDocument();
  });

  it('says so when nothing is open', async () => {
    api.getHostWebInterfaces.mockResolvedValue([]);
    renderWith([], [closed]);
    expect(screen.getByText('No open ports observed.')).toBeInTheDocument();
  });
});
