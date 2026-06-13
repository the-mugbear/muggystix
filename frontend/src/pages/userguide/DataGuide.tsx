import React from 'react';
import { FileUp, Workflow, Network } from 'lucide-react';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '../../components/ui/table';
import {
  UserGuideShell,
  GuidePage,
  GuideSection,
  Para,
  Subhead,
  OrderedList,
  UnorderedList,
  Mono,
} from './UserGuideShell';

// Mirrors documentation/UPLOAD_FORMATS.md — keep in sync when a parser lands.
const FORMAT_ROWS: { tool: string; formats: string; notes: string }[] = [
  { tool: 'Nmap', formats: '.xml, .gnmap', notes: 'XML (-oX) gives the richest data — OS detection, scripts, service versions. Grepable (-oG) also supported.' },
  { tool: 'Masscan', formats: '.xml, .json, .txt', notes: 'All three output formats. XML gives the best results.' },
  { tool: 'Naabu', formats: '.json, .txt', notes: 'Fast port scanner. host:port text or JSON; include "naabu" in the filename for auto-detection.' },
  { tool: 'RustScan', formats: '.txt', notes: 'Console output with Open <ip>:<port> lines. Include "rustscan" in the filename.' },
  { tool: 'Nessus', formats: '.nessus', notes: 'Vulnerability data with severity + plugin detail. Large (~600 MB) exports are streamed.' },
  { tool: 'OpenVAS / Greenbone', formats: '.xml', notes: 'XML reports with <result> host/port/finding entries.' },
  { tool: 'httpx', formats: '.json, .jsonl', notes: 'Web fingerprinting — status, title, server header, tech stack, TLS, favicon. Feeds the unified web-interfaces view.' },
  { tool: 'whatweb', formats: '.json, .jsonl', notes: 'WhatWeb --log-json web tech fingerprint. The apt-installable alternative when httpx (Go binary / Python-CLI collision) won’t install.' },
  { tool: 'Eyewitness', formats: '.json, .csv, .zip', notes: 'Web screenshots for visual triage. ZIP bundles have decompression-bomb caps.' },
  { tool: 'Nikto', formats: '.json, .csv, .txt', notes: 'Web server vulnerability scanner. Preserve the Target IP / Target Port header lines in text reports.' },
  { tool: 'NetExec (NXC)', formats: '.json, .txt', notes: 'SMB/WinRM/RDP/MSSQL/SSH enumeration. --json or the standard text report.' },
  { tool: 'SMBMap', formats: '.json, .txt', notes: 'SMB share enumeration. Preserve the standard [+] <ip> host lines in text reports.' },
  { tool: 'DirBuster family', formats: '.json, .csv, .txt', notes: 'Gobuster / Feroxbuster / ffuf / Dirsearch content discovery. Include the tool name in the filename.' },
  { tool: 'Amass / Subfinder', formats: '.json, .txt', notes: 'Subdomain discovery. Best results when the export includes resolved IPs (hostname-only rows are ignored).' },
  { tool: 'dnsx', formats: '.json, .jsonl', notes: 'DNS resolver output — A/AAAA/CNAME/MX/NS/TXT/SOA/PTR. PTR answers feed host hostnames.' },
  { tool: 'BloodHound / SharpHound', formats: '.json', notes: 'AD enumeration. Upload extracted JSON (not the ZIP); files ≥50 MB are streamed.' },
  { tool: 'DNS inventory', formats: '.csv', notes: 'Columns like hostname, record_type, value. For DNS enrichment.' },
  { tool: 'Subnet scopes', formats: '.csv', notes: 'Used on the Scope import page; requires a cidr column, optional metadata.' },
];

const sections: GuideSection[] = [
  {
    id: 'formats',
    title: 'Supported file formats',
    Icon: FileUp,
    summary: 'What you can upload, and which output flag gives the best data.',
    content: (
      <div>
        <Para>
          BlueStick auto-detects formats by extension <em>and</em> content inspection, so you can
          drop a mixed batch and let it sort them out. Prefer machine-readable output (XML/JSON) over
          plain text wherever a tool offers it.
        </Para>
        <div className="overflow-x-auto rounded-panel border border-border">
          <Table className="min-w-[760px]">
            <TableHeader>
              <TableRow>
                <TableHead className="w-1/5">Tool</TableHead>
                <TableHead className="w-1/5">Formats</TableHead>
                <TableHead>Notes</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {FORMAT_ROWS.map((row) => (
                <TableRow key={row.tool}>
                  <TableCell><strong>{row.tool}</strong></TableCell>
                  <TableCell><code className="font-mono text-caption break-words">{row.formats}</code></TableCell>
                  <TableCell className="text-body">{row.notes}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <Para>
          Anything else is rejected by a magic-byte check on upload. If a file fails to parse, check{' '}
          <strong>Settings → Ingestion Results</strong> for the structured error ID and troubleshooting
          detail.
        </Para>
      </div>
    ),
  },
  {
    id: 'ingestion',
    title: 'Uploading & ingestion',
    Icon: Workflow,
    summary: 'How an upload becomes deduplicated hosts, ports, and findings.',
    content: (
      <div>
        <Para>
          On <strong>Inventory → Scans</strong>, drag-and-drop or select files (multiple at once).
          Each upload creates an ingestion job processed asynchronously — the request returns quickly
          with a job ID and the UI polls status.
        </Para>
        <Subhead>The pipeline</Subhead>
        <OrderedList>
          <li><strong>Upload</strong> — file received and queued.</li>
          <li><strong>Format detection</strong> — parser auto-selected by extension + content.</li>
          <li><strong>Parsing</strong> — host, port, service, and vulnerability data extracted.</li>
          <li><strong>Deduplication</strong> — hosts deduped by IP within the project; ports and services merged with conflict tracking.</li>
          <li><strong>Correlation</strong> — hosts automatically mapped to scopes/subnets.</li>
        </OrderedList>
        <Para>
          A long-running job can be <strong>cancelled</strong> from the Scans queue. Parsers that drop
          records report a skipped count and warnings on the job row, so silent data loss in malformed
          input is visible rather than hidden.
        </Para>
        <Subhead>Viewing scan results</Subhead>
        <Para>
          Open a scan to see its hosts, ports, and the command that produced it.{' '}
          <strong>DNS-resolution scans</strong> (e.g. <Mono>dnsx</Mono>) also get a{' '}
          <strong>DNS Records</strong> tab: only A / AAAA answers become hosts, so CNAME / MX / NS /
          TXT records are listed there with their resolver and TTL — the full answer set, with the
          true record count and a note when a very large set is truncated.
        </Para>
      </div>
    ),
  },
  {
    id: 'scopes',
    title: 'Scopes, subnets & sites',
    Icon: Network,
    summary: 'Define engagement boundaries; group subnets into sites for roll-ups.',
    content: (
      <div>
        <Para>
          Scopes define the network boundaries of your engagement. Upload a CSV of CIDR ranges or add
          them manually; hosts are automatically classified in-scope or out-of-scope by IP.
        </Para>
        <Subhead>How it works</Subhead>
        <OrderedList>
          <li>Go to <strong>Inventory → Scopes</strong> and create a scope (e.g. "Client Network").</li>
          <li>Add subnets via CSV upload or manual entry in CIDR notation (<Mono>10.0.0.0/24</Mono>).</li>
          <li>BlueStick maps each host to matching subnets automatically.</li>
          <li>Use the out-of-scope filter on the Hosts page to find hosts outside your boundaries.</li>
        </OrderedList>
        <Subhead>Sites &amp; labels</Subhead>
        <Para>
          Subnets can be tagged with <strong>labels</strong> (e.g. "PCI") and grouped into{' '}
          <strong>sites</strong> (e.g. "London DC") with importance tiers. These power the{' '}
          <strong>Posture</strong> roll-ups and let you filter hosts by{' '}
          <Mono>label:</Mono> / <Mono>site:</Mono> on the Hosts page. Per-subnet exposure and neglect
          are summarised on <strong>Posture → Insights</strong>.
        </Para>
      </div>
    ),
  },
];

const DataGuide: React.FC = () => (
  <UserGuideShell activePath="/reference/user-guide/data">
    <GuidePage
      intro={
        <span>
          Everything that gets data <em>into</em> BlueStick: what you can upload, what happens to it,
          and how to carve the network into scopes and sites.
        </span>
      }
      sections={sections}
    />
  </UserGuideShell>
);

export default DataGuide;
