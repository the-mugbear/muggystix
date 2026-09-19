/**
 * The upload formats the Scans page advertises.
 *
 * v5.204.0 — moved out of Scans.tsx so a test can pin it to
 * documentation/UPLOAD_FORMATS.md (the parser-facing table), which is itself
 * pinned to the dispatcher by the backend detection↔dispatch contract test.
 * Before that the list drifted silently: WhatWeb, testssl.sh and RDAP were
 * detected and dispatched for months while this page never mentioned them.
 *
 * v5.219.2 — every row re-checked against `_build_parsing_attempts` and the
 * `looks_like_*` detectors in `content_detection.py`: the extensions are
 * what the dispatcher routes, and each `hint` is the filename token (or
 * content marker) that detector actually keys on.  ACCEPTED_EXTENSIONS is
 * the dropzone allowlist, mirroring the backend's ALLOWED_UPLOAD_EXTENSIONS.
 *
 * Grouped by what the output describes (ports → vulnerabilities → web →
 * names/DNS → AD/SMB → enrichment) so an operator scanning for their tool
 * finds it next to its neighbours.
 */
export interface SupportedFormat {
  tool: string;
  formats: string;
  desc: string;
  /** How auto-detect recognises the file when the extension is ambiguous. */
  hint?: string;
}

/**
 * Extensions the dropzone accepts, keyed by MIME type the way react-dropzone
 * wants them.  Keep in lockstep with the backend ALLOWED_UPLOAD_EXTENSIONS.
 * Line-delimited JSON arrives as both .jsonl (httpx/dnsx) and .ndjson
 * (rdap-lookup.py); ZIP is the EyeWitness bundle.
 */
export const ACCEPTED_EXTENSIONS: Record<string, string[]> = {
  'text/xml': ['.xml', '.nessus'],
  'application/json': ['.json', '.jsonl', '.ndjson'],
  'text/csv': ['.csv'],
  'text/plain': ['.txt', '.gnmap'],
  'application/zip': ['.zip'],
};

/** Flat, display-ordered list of every accepted extension. */
export const ACCEPTED_EXTENSION_LIST: string[] = Object.values(ACCEPTED_EXTENSIONS).flat();

export const SUPPORTED_FORMATS: SupportedFormat[] = [
  // ── Port scanners ──────────────────────────────────────────────────────
  {
    tool: 'Nmap',
    formats: '.xml / .gnmap / .txt',
    desc: 'XML (-oX) and grepable (-oG) output. Grepable output saved as .txt is recognised too.',
    hint: 'XML root <nmaprun>; grepable "Host: … Ports:" lines.',
  },
  {
    tool: 'Masscan',
    formats: '.xml / .json / .txt',
    desc: 'XML (-oX), JSON (-oJ) or list (-oL) output.',
    hint: 'scanner="masscan" in XML; "masscan" in the filename for JSON; "Timestamp: … Host: … Ports:" lines in a list.',
  },
  {
    tool: 'Naabu',
    formats: '.json / .txt',
    desc: 'JSON records or host:port text output.',
    hint: 'Put "naabu" in the filename; JSON is otherwise recognised by its flat {ip, port} records.',
  },
  {
    tool: 'RustScan',
    formats: '.txt',
    desc: 'Console output: "Open 10.0.0.1:22" lines or bracketed lists ("10.0.0.1 -> [22,80]").',
    hint: 'Put "rustscan" in the filename or keep the banner in the file.',
  },
  // ── Vulnerability scanners ─────────────────────────────────────────────
  {
    tool: 'Nessus',
    formats: '.nessus / .xml',
    desc: 'Nessus export (.nessus, not the HTML report). Severity-0 findings are skipped when the switch above is on.',
    hint: '.nessus routes directly; .xml is checked for NessusClientData content.',
  },
  {
    tool: 'OpenVAS / Greenbone',
    formats: '.xml',
    desc: 'XML report with <result> entries; large reports are streamed.',
    hint: 'XML root <report>; or "openvas", "greenbone" or "gvm" in the filename.',
  },
  // ── Web probing ────────────────────────────────────────────────────────
  {
    tool: 'httpx (ProjectDiscovery)',
    formats: '.json / .jsonl',
    desc: 'Web probe output (-json). Feeds the Web Interfaces view alongside EyeWitness.',
    hint: '"httpx" in the filename, or records carrying url + tech / webserver.',
  },
  {
    tool: 'WhatWeb',
    formats: '.json / .jsonl',
    desc: '--log-json fingerprint: title, server header and detected tech stack. No favicon hash or TLS detail (WhatWeb does not emit them).',
    hint: '"whatweb" in the filename, or records carrying target + plugins.',
  },
  {
    tool: 'testssl.sh',
    formats: '.json',
    desc: '--jsonfile TLS assessment, folded into one web-interface row per ip:port (weak-protocol flag, cert expiry, self-signed). Individual checks are not stored as findings.',
    hint: '"testssl" in the filename, or the id + finding + severity record shape.',
  },
  {
    tool: 'EyeWitness',
    formats: '.json / .csv / .zip',
    desc: 'Screenshot metadata from the JSON or CSV report, or the whole ZIP bundle (limits: 50 MB per file, 500 MB per bundle, 5000 entries).',
    hint: '"eyewitness" or "report" in the filename; JSON is also recognised by screenshot_path records.',
  },
  {
    tool: 'Nikto',
    formats: '.json / .csv / .txt',
    desc: 'Web server findings. Text reports must keep the "Target IP:" header lines.',
    hint: '"nikto" in the filename or in the report banner.',
  },
  {
    tool: 'DirBuster / Gobuster / Feroxbuster / ffuf / Dirsearch',
    formats: '.json / .csv / .txt',
    desc: 'Directory brute-force output. Discovered paths are folded into the service description (first 50 kept).',
    hint: 'Put the tool name in the filename; ffuf/feroxbuster/dirsearch JSON and gobuster "(Status: NNN)" text are also recognised by shape.',
  },
  // ── Names and DNS ──────────────────────────────────────────────────────
  {
    tool: 'Amass / Subfinder',
    formats: '.json / .txt',
    desc: 'Subdomain discovery. Rows with a resolved IP create hosts; name-only rows become unresolved names in the Names inventory.',
    hint: '"amass" or "subfinder" in the filename, or records carrying name + addresses.',
  },
  {
    tool: 'dnsx (ProjectDiscovery)',
    formats: '.json / .jsonl',
    desc: 'DNS resolution run locally against your resolvers (-j -resp). A/AAAA/CNAME/MX/NS/TXT/SOA/PTR records are ingested; PTR answers set the host name. Resolution failures are reported as parser warnings.',
    hint: '"dnsx" in the filename, or records carrying host + a record-type array.',
  },
  {
    tool: 'DNS records (CSV)',
    formats: '.csv',
    desc: 'One row per record: record type, name, address. Header aliases accepted (type / hostname / ip / value); comma, tab or semicolon delimited.',
    hint: 'Recognised by the header row alone.',
  },
  // ── Active Directory and SMB ───────────────────────────────────────────
  {
    tool: 'NetExec (NXC)',
    formats: '.json / .txt',
    desc: 'SMB and LDAP enumeration lines from the console output (SMB signing posture is recorded), or spider_plus JSON.',
    hint: '"netexec" or "nxc" in the filename, or the SMB / LDAP column layout in the text.',
  },
  {
    tool: 'SMBMap',
    formats: '.json / .txt',
    desc: 'SMB host enumeration. Text output must keep the "[+] <ip>" host lines. Share details are not retained.',
    hint: '"smbmap" in the filename, or "[+]" host lines with a Disk column.',
  },
  {
    tool: 'BloodHound / SharpHound',
    formats: '.json',
    desc: 'Extracted computers JSON (not the ZIP bundle). Computer inventory only: edges, ACLs, users and groups are not imported. Files of 50 MB and over are streamed.',
    hint: '"bloodhound", "sharphound" or "computers" in the filename, or node records carrying Properties.',
  },
  // ── Enrichment ─────────────────────────────────────────────────────────
  {
    tool: 'RDAP',
    formats: '.json / .ndjson',
    desc: 'Netblock registration (org, country, ASN) from scripts/rdap-lookup.py. Enriches hosts under each block and creates none, so the scan reports 0 hosts by design.',
    hint: '"rdap" in the filename, or ip-network objects (startAddress / endAddress).',
  },
];
