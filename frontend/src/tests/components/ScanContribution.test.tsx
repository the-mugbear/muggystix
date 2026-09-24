import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';

import {
  ScanContribution,
  contributionRows,
  type ContributionScan,
} from '../../components/scans/ScanContribution';

const blank: ContributionScan = {
  tool_name: 'nmap',
  total_hosts: 0,
  up_hosts: 0,
  open_ports: 0,
  os_fingerprinted: 0,
  port_breakdown: null,
  vulnerability_summary: null,
  web: null,
  dns: null,
  auth: null,
};

describe('contributionRows', () => {
  it('leads a port scan with what it found on the wire', () => {
    const rows = contributionRows({
      ...blank,
      total_hosts: 12,
      up_hosts: 10,
      open_ports: 45,
      os_fingerprinted: 5,
      port_breakdown: {
        unique_ports: 9,
        open_tcp_ports: 40,
        open_udp_ports: 5,
        new_open_ports: 8,
        open_with_service: 30,
      },
    });
    expect(rows.map((r) => r.key)).toEqual(['ports', 'hosts']);
    expect(rows[0].parts).toEqual(['45 open', '+8 new', '30 with a service name', '40 TCP / 5 UDP', 'on 9 port numbers']);
    expect(rows[1].parts).toEqual(['10 up', '5 OS fingerprinted']);
  });

  // Screenshot 2026-09-23: "Hosts 0 up" beside "+146 new of 146 seen".
  it('says "none reported up" instead of "0 up" when hosts exist', () => {
    const rows = contributionRows({ ...blank, total_hosts: 146, up_hosts: 0 });
    expect(rows.find((r) => r.key === 'hosts')?.parts).toEqual(['none reported up']);
  });

  it('lets a long contribution line wrap instead of truncating it', () => {
    render(
      <ScanContribution
        scan={{
          ...blank, tool_name: 'nessus', total_hosts: 123, up_hosts: 0,
          vulnerability_summary: { total: 421, hosts_affected: 123, hosts_critical_high: 63, exploitable: 12 } as never,
        }}
      />,
    );
    const line = screen.getByText(/421 new · on 123 hosts · 63 hosts critical\/high/);
    expect(line.className).not.toMatch(/truncate/);
    expect(line.className).toMatch(/break-words/);
  });

  it('leads a web scan with interfaces and omits zero buckets', () => {
    const rows = contributionRows({
      ...blank,
      tool_name: 'httpx',
      total_hosts: 3,
      up_hosts: 3,
      web: {
        interfaces: 6,
        new_urls: 2,
        hosts: 3,
        https: 4,
        status_2xx: 5,
        status_3xx: 0,
        status_4xx: 1,
        status_5xx: 0,
        cert_expired: 1,
        cert_self_signed: 0,
        weak_tls: 0,
        screenshots: 0,
      },
    });
    expect(rows[0].key).toBe('web');
    expect(rows[0].parts).toEqual(['6 interfaces', '+2 new', '5 2xx', '1 4xx', '4 HTTPS', '1 expired cert']);
    // "Up" means nothing for a web fingerprinter, so there is no hosts row.
    expect(rows.find((r) => r.key === 'hosts')).toBeUndefined();
  });

  it('names DNS observation kinds for what they are', () => {
    const rows = contributionRows({
      ...blank,
      tool_name: 'dnsx',
      dns: { records: 60, names: 50, new_names: 0, by_type: { DISCOVERED: 5, A: 40, PTR: 12, CERT: 3, MX: 1 } },
    });
    expect(rows[0].key).toBe('names');
    expect(rows[0].parts).toEqual(['50 names', '0 new', '40 A', '12 PTR', '1 MX', '3 via certificates']);
  });

  it('reports findings the scan recorded first, with their spread', () => {
    const rows = contributionRows({
      ...blank,
      tool_name: 'Nessus',
      total_hosts: 4,
      vulnerability_summary: {
        total: 20, critical: 1, high: 3, medium: 6, low: 4, info: 6,
        hosts_affected: 4, hosts_critical_high: 2, exploitable: 1,
      },
    });
    expect(rows[0].key).toBe('findings');
    expect(rows[0].parts).toEqual(['20 new', 'on 4 hosts', '2 hosts critical/high', '1 exploitable']);
  });
});

describe('ScanContribution', () => {
  it('renders labelled rows', () => {
    render(
      <ScanContribution
        scan={{
          ...blank,
          tool_name: 'netexec',
          total_hosts: 2,
          auth: { hosts: 2, protocols: ['ldap', 'smb'], valid_accounts: 1 },
        }}
      />,
    );
    expect(screen.getByText('Auth')).toBeInTheDocument();
    expect(screen.getByText('2 hosts · ldap/smb · 1 valid account')).toBeInTheDocument();
  });

  // v5.270.0 — one line per kind; the severity bar repeated the counts.
  it('keeps each kind to one line and draws no severity bar', () => {
    const { container } = render(
      <ScanContribution
        scan={{
          ...blank,
          tool_name: 'Nessus',
          total_hosts: 4,
          vulnerability_summary: {
            total: 2, critical: 1, high: 1, medium: 0, low: 0, info: 0,
            hosts_affected: 1, hosts_critical_high: 1, exploitable: 0,
          },
        }}
      />,
    );
    expect(screen.getByText('Scanner observations')).toHaveClass('whitespace-nowrap');
    // v5.288.0 — the figures wrap (they used to truncate mid-list).
    expect(screen.getByText(/^2 new/)).toHaveClass('break-words');
    expect(container.querySelector('[role="img"], [aria-label*="everity"]')).toBeNull();
  });

  it('says so when a scan recorded nothing', () => {
    render(<ScanContribution scan={blank} />);
    expect(screen.getByText('Nothing recorded')).toBeInTheDocument();
  });
});
