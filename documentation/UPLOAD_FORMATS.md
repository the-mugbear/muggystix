# Supported Upload Formats

> **Last verified against:** backend 2.201.0 / frontend 5.106.0 (2026-06-13)

BlueStick's ingestion pipeline automatically detects and routes uploads to the correct parser. The table below summarises the current parser coverage and what your source tools need to export.

| Tool | Accepted Extensions | Parser Module | Notes |
| --- | --- | --- | --- |
| Nmap | `.xml` (normal), `.gnmap` (grepable) | `backend/app/parsers/nmap_parser.py`, `backend/app/parsers/gnmap_parser.py` | Use `-oX` for XML or `-oG` for GNMAP. Multi-host files are supported. |
| Masscan | `.xml`, `.json`, `.txt` target/output lists | `backend/app/parsers/masscan_parser.py` | JSON/CSV exports must include port metadata. Raw `--output-filename` TXT is supported. |
| Naabu | `.json`, `.txt` | `backend/app/parsers/naabu_parser.py` | Supports `host:port` text and common JSON output. Include `naabu` in the filename for best auto-detection. |
| RustScan | `.txt` | `backend/app/parsers/rustscan_parser.py` | Supports common console output with `Open <ip>:<port>` or bracketed port lists. Include `rustscan` in the filename. |
| Nessus | `.nessus` XML | `backend/app/parsers/nessus_parser.py`, `backend/app/services/nessus_integration_service.py` | Large (~600 MB) exports are streamed; ensure `scan-results.nessus` style XML, not HTML reports. |
| OpenVAS / Greenbone | `.xml` | `backend/app/parsers/openvas_parser.py` | Expects XML reports with `<result>` entries containing host/port/finding data. |
| Amass / Subfinder | `.json`, `.txt` | `backend/app/parsers/amass_parser.py` | Best results come from exports that include resolved IPs. Hostname-only rows are ignored. |
| Nikto | `.json`, `.csv`, `.txt` | `backend/app/parsers/nikto_parser.py` | Text reports should preserve the standard `Target IP` / `Target Port` header lines. |
| SMBMap | `.json`, `.txt` | `backend/app/parsers/smbmap_parser.py` | Text reports should preserve the standard smbmap `[+] <ip>` host lines. |
| BloodHound / SharpHound | `.json` | `backend/app/parsers/bloodhound_parser.py` | Upload extracted JSON files, not the ZIP bundle. Computer objects should include resolved IPv4 data. Files ≥50 MB are streamed via `ijson` to avoid OOM-killing the worker (v2.41.0). |
| Eyewitness | `.json`, `.csv`, `.zip` | `backend/app/parsers/eyewitness_parser.py` | Works with `--results` JSON bundle or the generated CSV summary. ZIP bundles get decompression-bomb caps: ≤50 MB per file, ≤500 MB total, ≤5000 entries (v2.41.0). Skip counts + warnings persist on the ingestion job (v2.22.0). |
| httpx | `.json`, `.jsonl` | `backend/app/parsers/httpx_parser.py` | Project Discovery's `httpx` web probing — feeds the unified `web_interfaces` view alongside Eyewitness. Skip counts + warnings persist on the ingestion job. |
| whatweb | `.json`, `.jsonl` | `backend/app/parsers/whatweb_parser.py` | WhatWeb's `--log-json` web tech-fingerprint — feeds the unified `web_interfaces` view (`source="whatweb"`) with title / server header / detected tech stack. The apt-installable alternative when httpx (Go binary / Python-CLI collision) isn't available. No favicon hash or structured TLS (whatweb doesn't emit them). Example invocation: `whatweb -a 3 --input-file=targets.txt --log-json=whatweb.json --no-errors`. |
| DirBuster / Gobuster / Feroxbuster / ffuf / Dirsearch | `.json`, `.csv`, `.txt` | `backend/app/parsers/dirbuster_parser.py` | Unified parser for directory brute-force tools. Include tool name in filename for best auto-detection. |
| NetExec (NXC) | `.json`, `.txt` | `backend/app/parsers/netexec_parser.py` | Supports Spider and SMB/WMI transports. Use `--json` or the standard text report. |
| DNS inventories | `.csv` | `backend/app/parsers/dns_parser.py` | Expect columns like `hostname`, `record_type`, `value`. Used for DNS enrichment alongside uploads. |
| dnsx | `.json`, `.jsonl` | `backend/app/parsers/dnsx_parser.py` | Project Discovery's `dnsx` resolver — run terminal-side against operator-supplied DNS servers, upload the JSON output to ingest A/AAAA/CNAME/MX/NS/TXT/SOA/PTR records. PTR answers feed `Host.hostname`. Resolver attribution + resolution-failure tallies (NXDOMAIN, SERVFAIL) surface as parser warnings on the ingestion job. Example invocation: `dnsx -j -resp -l ips.txt -r resolvers.txt -ptr -a -aaaa -cname -mx -ns -txt -o dnsx-output.json`. |
| Subnet lists | `.csv` | `backend/app/parsers/subnet_parser.py` | Used on the Scope import page; requires `cidr` column, optional metadata. |

## File Size & Performance

- Single uploads are capped by `MAX_FILE_SIZE` (default 1 GB) and streamed to disk, so browser timeouts are avoided.
- Upload requests return quickly with an ingestion job ID; parsing continues in background workers and the UI polls job status.
- Nessus files commit in batches (`NESSUS_COMMIT_BATCH_SIZE`, default 50 hosts) to keep database pressure manageable.
- DNS enrichment can be toggled during upload; it runs after the main parser finishes.
- **Parser quality stats** (v2.22.0) — parsers that drop records report `skipped_count` and a free-text `parser_warnings` string on the ingestion job row. Visible on the Scans / Ingestion Jobs page; useful for spotting silent data loss in malformed inputs.

## Quick Tips

- Compressing files before upload is not required; submit raw exports so the auto-detection logic can inspect headers.
- If a file fails to parse, check **Scans → Parse Errors** for the structured error ID and troubleshooting details.
- When migrating a deployment, keep `.env` aligned with the host IP and CORS origins.
