/**
 * "What it contributed" cell on /scans (v5.205.0).
 *
 * Every block is computed server-side from the rows the scan itself wrote
 * (host/port history, first-seen findings, web interfaces, name observations,
 * netexec results), so a block appears whenever the data exists, whatever the
 * tool. The tool's family only decides the ORDER: a port scan leads with
 * ports, httpx with web interfaces, dnsx with names.
 */
import React from 'react';

import type { Scan } from '../../services/api';
import SeverityBar from '../ui/SeverityBar';

export type ToolFamily = 'port' | 'vuln' | 'web' | 'dns' | 'auth' | 'other';

const FAMILY_BY_TOOL: Record<string, ToolFamily> = {
  nmap: 'port',
  masscan: 'port',
  naabu: 'port',
  rustscan: 'port',
  nessus: 'vuln',
  openvas: 'vuln',
  nikto: 'vuln',
  httpx: 'web',
  whatweb: 'web',
  eyewitness: 'web',
  testssl: 'web',
  dirbuster: 'web',
  gobuster: 'web',
  feroxbuster: 'web',
  ffuf: 'web',
  dirsearch: 'web',
  dns: 'dns',
  dnsx: 'dns',
  amass: 'dns',
  subfinder: 'dns',
  rdap: 'dns',
  netexec: 'auth',
  smbmap: 'auth',
  bloodhound: 'auth',
};

export function toolFamily(tool?: string | null): ToolFamily {
  return FAMILY_BY_TOOL[(tool || '').trim().toLowerCase()] ?? 'other';
}

type RowKey = 'hosts' | 'ports' | 'findings' | 'web' | 'names' | 'auth';

const ORDER: Record<ToolFamily, RowKey[]> = {
  port: ['ports', 'hosts', 'findings', 'names', 'web', 'auth'],
  vuln: ['findings', 'hosts', 'ports', 'web', 'names', 'auth'],
  web: ['web', 'findings', 'names', 'hosts', 'ports', 'auth'],
  dns: ['names', 'hosts', 'web', 'ports', 'findings', 'auth'],
  auth: ['auth', 'hosts', 'ports', 'findings', 'names', 'web'],
  other: ['hosts', 'ports', 'findings', 'web', 'names', 'auth'],
};

export interface ContributionRow {
  key: RowKey;
  label: string;
  parts: string[];
  /** What the numbers mean — on hover. */
  hint: string;
}

export type ContributionScan = Pick<
  Scan,
  | 'tool_name'
  | 'total_hosts'
  | 'up_hosts'
  | 'open_ports'
  | 'os_fingerprinted'
  | 'port_breakdown'
  | 'vulnerability_summary'
  | 'web'
  | 'dns'
  | 'auth'
>;

const num = (n: number) => n.toLocaleString();
const plural = (n: number, one: string, many = `${one}s`) => `${num(n)} ${n === 1 ? one : many}`;
const added = (n: number) => `${n > 0 ? '+' : ''}${num(n)} new`;

// Observation kinds are not DNS answers — name them for what they are.
const DNS_TYPE_LABEL: Record<string, string> = {
  DISCOVERED: 'unresolved',
  SCANNER: 'scanner-reported',
  HTTP: 'via HTTP',
  CERT: 'via certificates',
  IMPORT: 'imported',
};
const DNS_TYPE_ORDER = [
  'A', 'AAAA', 'CNAME', 'PTR', 'MX', 'NS', 'TXT', 'SRV', 'SOA', 'CAA',
  'HTTP', 'CERT', 'SCANNER', 'IMPORT', 'DISCOVERED',
];
const MAX_DNS_TYPES = 4;

export function contributionRows(scan: ContributionScan): ContributionRow[] {
  const family = toolFamily(scan.tool_name);
  const rows: Partial<Record<RowKey, ContributionRow>> = {};

  // Hosts: only what the New hosts column doesn't already say.
  if (scan.total_hosts > 0) {
    const parts: string[] = [];
    // "Up" only means something for tools that probe liveness.
    if (family === 'port' || family === 'other') parts.push(`${num(scan.up_hosts)} up`);
    const os = scan.os_fingerprinted ?? 0;
    if (os > 0) parts.push(`${num(os)} OS fingerprinted`);
    if (parts.length) {
      rows.hosts = {
        key: 'hosts',
        label: 'Hosts',
        parts,
        hint: 'Of the hosts this scan observed: how many it reported up, and how many it supplied operating-system details for.',
      };
    }
  }

  const ports = scan.port_breakdown;
  if (ports && scan.open_ports > 0) {
    const parts = [`${num(scan.open_ports)} open`, added(ports.new_open_ports ?? 0)];
    const named = ports.open_with_service ?? 0;
    if (named > 0) parts.push(`${num(named)} with a service name`);
    if (ports.open_udp_ports > 0) {
      parts.push(`${num(ports.open_tcp_ports)} TCP / ${num(ports.open_udp_ports)} UDP`);
    }
    rows.ports = {
      key: 'ports',
      label: 'Ports',
      parts,
      hint: 'Open ports this scan observed. "New" = ports no earlier scan had recorded on that host. "With a service name" = the scan identified what is listening.',
    };
  }

  const findings = scan.vulnerability_summary;
  if (findings && findings.total > 0) {
    const parts = [`${num(findings.total)} new`];
    const hosts = findings.hosts_affected ?? 0;
    if (hosts > 0) parts.push(`on ${plural(hosts, 'host')}`);
    const severe = findings.hosts_critical_high ?? 0;
    if (severe > 0) parts.push(`${plural(severe, 'host')} critical/high`);
    const exploitable = findings.exploitable ?? 0;
    if (exploitable > 0) parts.push(`${num(exploitable)} exploitable`);
    rows.findings = {
      key: 'findings',
      label: 'Findings',
      parts,
      hint: 'Findings this scan recorded first (re-observations of known findings are not counted), at their current severity.',
    };
  }

  const web = scan.web;
  if (web && web.interfaces > 0) {
    const parts = [plural(web.interfaces, 'interface'), added(web.new_urls)];
    const statuses: Array<[number, string]> = [
      [web.status_2xx, '2xx'],
      [web.status_3xx, '3xx'],
      [web.status_4xx, '4xx'],
      [web.status_5xx, '5xx'],
    ];
    for (const [count, bucket] of statuses) if (count > 0) parts.push(`${num(count)} ${bucket}`);
    if (web.https > 0) parts.push(`${num(web.https)} HTTPS`);
    if (web.cert_expired > 0) parts.push(plural(web.cert_expired, 'expired cert'));
    if (web.cert_self_signed > 0) parts.push(`${num(web.cert_self_signed)} self-signed`);
    if (web.weak_tls > 0) parts.push(`${num(web.weak_tls)} weak TLS`);
    if (web.screenshots > 0) parts.push(plural(web.screenshots, 'screenshot'));
    rows.web = {
      key: 'web',
      label: 'Web',
      parts,
      hint: 'Web interfaces this scan recorded. "New" = URLs no earlier scan had recorded. Expired = certificate had expired by the time of upload.',
    };
  }

  const dns = scan.dns;
  if (dns && dns.records > 0) {
    const parts = [plural(dns.names, 'name'), added(dns.new_names)];
    const types = Object.entries(dns.by_type)
      .filter(([, count]) => count > 0)
      .sort(([a], [b]) => {
        const ia = DNS_TYPE_ORDER.indexOf(a);
        const ib = DNS_TYPE_ORDER.indexOf(b);
        return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib) || a.localeCompare(b);
      })
      .slice(0, MAX_DNS_TYPES);
    for (const [type, count] of types) parts.push(`${num(count)} ${DNS_TYPE_LABEL[type] ?? type}`);
    rows.names = {
      key: 'names',
      label: 'Names',
      parts,
      hint: 'DNS names this scan observed and the record types behind them. "New" = names no earlier scan had observed. "Unresolved" = named, but the scan saw no address for it.',
    };
  }

  const auth = scan.auth;
  if (auth && auth.hosts > 0) {
    const parts = [plural(auth.hosts, 'host')];
    if (auth.protocols.length) parts.push(auth.protocols.join('/'));
    if (auth.valid_accounts > 0) parts.push(plural(auth.valid_accounts, 'valid account'));
    rows.auth = {
      key: 'auth',
      label: 'Auth',
      parts,
      hint: 'Hosts this scan enumerated, over which protocols, and how many distinct accounts authenticated successfully.',
    };
  }

  return ORDER[family].map((key) => rows[key]).filter((row): row is ContributionRow => !!row);
}

export const ScanContribution: React.FC<{ scan: ContributionScan }> = ({ scan }) => {
  const rows = contributionRows(scan);
  if (!rows.length) {
    return (
      <span className="text-caption text-muted-foreground">
        {scan.total_hosts > 0 ? 'Hosts only' : 'Nothing recorded'}
      </span>
    );
  }
  const findings = scan.vulnerability_summary;
  return (
    <dl className="grid grid-cols-[4.25rem_minmax(0,1fr)] gap-x-xs gap-y-xxs">
      {rows.map((row) => (
        <React.Fragment key={row.key}>
          <dt className="text-caption text-muted-foreground" title={row.hint}>
            {row.label}
          </dt>
          <dd className="min-w-0 break-words text-caption tabular-nums text-foreground" title={row.hint}>
            {row.key === 'findings' && findings && (
              <SeverityBar counts={findings} variant="compact" className="mb-xxs" />
            )}
            {row.parts.join(' · ')}
          </dd>
        </React.Fragment>
      ))}
    </dl>
  );
};

export default ScanContribution;
