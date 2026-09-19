import React from 'react';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const api = vi.hoisted(() => ({ getHostWebInterfaces: vi.fn() }));
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
