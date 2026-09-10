/**
 * The upload formats the Scans page advertises.
 *
 * v5.204.0 — moved out of Scans.tsx so a test can pin it to
 * documentation/UPLOAD_FORMATS.md (the parser-facing table), which is itself
 * pinned to the dispatcher by the backend detection↔dispatch contract test.
 * Before that the list drifted silently: WhatWeb, testssl.sh and RDAP were
 * detected and dispatched for months while this page never mentioned them.
 *
 * Alphabetised within sections so operators can scan; recent additions are
 * tagged so the auto-detect contract is clear.  Filename hints help the
 * content-detection dispatcher when the upload's extension is ambiguous
 * (e.g. ``out.json`` could be many tools).
 */
export interface SupportedFormat {
  tool: string;
  formats: string;
  desc: string;
}

export const SUPPORTED_FORMATS: SupportedFormat[] = [
  { tool: 'Nmap', formats: '.xml / .gnmap', desc: 'XML and grepable output.' },
  { tool: 'Masscan', formats: '.xml / .json / .txt', desc: 'High-speed port scan; XML/JSON exports or --output-filename list.' },
  { tool: 'RustScan', formats: '.txt', desc: 'Bracketed-list output (e.g. "10.0.0.1 -> [22,80]"). Include "rustscan" in filename for auto-detect.' },
  { tool: 'Naabu', formats: '.json / .txt', desc: 'Host:port discovery output.' },
  { tool: 'Nessus', formats: '.nessus / .xml', desc: 'Vulnerability scan exports.' },
  { tool: 'OpenVAS / Greenbone', formats: '.xml', desc: 'Streaming-parsed for large reports (v2.86.11+).' },
  { tool: 'httpx (ProjectDiscovery)', formats: '.json / .jsonl', desc: 'Web fingerprint output; feeds the web_interfaces view alongside EyeWitness.' },
  { tool: 'WhatWeb', formats: '.json / .jsonl', desc: '--log-json web tech fingerprint; feeds web_interfaces (title, server header, tech stack). No favicon hash or TLS detail — WhatWeb does not emit them.' },
  { tool: 'testssl.sh', formats: '.json', desc: '--jsonfile TLS assessment, folded into one web_interfaces row per ip:port (weak-protocol flag, cert expiry, self-signed). Individual checks are not stored as findings.' },
  { tool: 'dnsx (ProjectDiscovery)', formats: '.json / .jsonl', desc: 'DNS resolution against operator-supplied resolvers. PTR answers populate Host.hostname; per-record resolver attribution is preserved (v2.89.0).' },
  { tool: 'Amass / Subfinder', formats: '.json / .txt', desc: 'Subdomain discovery. Rows with a resolved IP create hosts; name-only rows become unresolved names in the Names inventory.' },
  { tool: 'RDAP', formats: '.json / .ndjson', desc: 'Netblock registration (org, country, ASN) from scripts/rdap-lookup.py. Enriches hosts under each block; creates no hosts, so the scan reports 0 hosts by design.' },
  { tool: 'EyeWitness', formats: '.json / .csv / .zip', desc: 'Web screenshot metadata. ZIP bundle accepted; bomb-caps applied (≤50MB/file, ≤500MB/bundle).' },
  { tool: 'Nikto', formats: '.json / .csv / .txt', desc: 'Web findings exports.' },
  { tool: 'NetExec (NXC)', formats: '.json / .txt', desc: 'SMB/LDAP/WMI/WinRM enumeration via Spider or standard text report.' },
  { tool: 'SMBMap', formats: '.json / .txt', desc: 'SMB enumeration output; preserves standard "[+] <ip>" host lines. Share details are not retained.' },
  { tool: 'BloodHound / SharpHound', formats: '.json', desc: 'Extracted JSON (not the ZIP bundle). Computer inventory only — edges, ACLs, users and groups are not imported. Files ≥50MB stream via ijson.' },
  { tool: 'DirBuster / Gobuster / Feroxbuster / ffuf / Dirsearch', formats: '.json / .csv / .txt', desc: 'Directory brute-force output (unified parser). Paths are folded into the service description (first 50 kept). Include tool name in filename for best auto-detect.' },
  { tool: 'DNS records (CSV)', formats: '.csv', desc: 'Columns: record_type, name, address. Used for ad-hoc DNS enrichment.' },
];
