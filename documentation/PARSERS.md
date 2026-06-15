# Parser Reference & Contributor Guide

> **Last verified against:** backend 2.228.0 / frontend 5.128.0 (2026-06-14)
>
> Companion to [`UPLOAD_FORMATS.md`](./UPLOAD_FORMATS.md). That doc is the
> *user-facing* "what can I upload" table. **This** doc is for operators and
> maintainers: **Part 1** is the field-by-field reference for what each parser
> actually extracts and where it lands in the schema; **Part 2** is the guide
> to adding a new parser.

---

## Part 0 — How ingestion routes a file to a parser

A single upload becomes an `IngestionJob`; a background worker
(`app/worker.py`) claims it and runs `IngestionService._process_job`. Routing:

1. **Sample read** — `_read_sample()` reads the first **64 KB** of the file.
2. **Attempt list** — `_build_parsing_attempts(job, sample)`
   (`app/services/ingestion_service.py`) builds an **ordered** list of
   `(label, ParserClass, description)` tuples, branched first on the file
   **extension** (`.xml`/`.gnmap`/`.json`/`.jsonl`/`.zip`/`.csv`/`.txt`) and
   then gated by per-tool **content detectors** (`looks_like_*` in
   `app/parsers/content_detection.py`). Ordering is by structural specificity
   — e.g. for `.xml`, `looks_like_masscan_xml` (scanner attr) is tried before
   nmap, OpenVAS before nmap, and Nessus is always appended last-ditch.
3. **First success wins** — each attempt's parser runs in turn; the first that
   parses without raising produces the `Scan` and the job completes. If the
   attempt list is **empty**, the job fails with an `Unsupported file type or
   format` parse error (there is intentionally **no** silent "treat unknown
   `.txt` as masscan" fallback anymore).
4. **Post-processing** — the orchestrator commits, tags `scan.project_id`,
   backfills `scan.command_line` from the agent's `command_run` when the parser
   left it blank, optionally runs DNS enrichment, and records
   `skipped_count` / `parser_warnings` on the job (see Part 2).

> **Two-place registration.** Every parser is referenced **twice** in
> `ingestion_service.py`: once in `_build_parsing_attempts` (routing) and once
> in `parser_map` / `_extra_parsers` (instantiation). The dispatcher raises
> `Unsupported parser class` if a routed class isn't in the map. (Details in
> Part 2.)

### The data model parsers write to

| Model | Owner table | Written by |
| --- | --- | --- |
| `Scan` | `scans` | every parser (the container; carries `tool_name`, `scan_type`, `command_line`, `version`, `start_time`/`end_time`) |
| `Host` | `hosts_v2` | every host-bearing parser (deduped: one row per `(project_id, ip_address)`) |
| `Port` | `ports_v2` | port scanners + web/dir/vuln parsers |
| `HostScanHistory` / `PortScanHistory` | per-scan observation audit trail |
| `Vulnerability` | `vulnerabilities` | **Nessus, OpenVAS, Nikto only** |
| `WebInterface` | `web_interfaces` | **httpx, whatweb, eyewitness** (unified web view, keyed by `source`) |
| `DNSRecord` | `dns_records` | **dnsx, dns CSV, amass** (columns are `domain` + `value`, *not* `hostname`/`ip_address`) |
| `Subnet` / `SubnetLabel` | scope tables | `subnet_parser` (Scope import page only) |
| `ScanInfo`, `Script`/`HostScript` | nmap |
| `HostAttribute` | Nessus (confidence-tracked host facts) |
| `NetexecResult`, `HostConfidence`/`PortConfidence`, `ConflictHistory` | netexec |

---

## Part 1 — Per-tool field-extraction reference

Conventions used below: a field is listed only if the parser **actually
assigns it**; "—" means the model column exists but this parser leaves it
default/NULL. Most parsers write through the **deduplication service**
(`find_or_create_host` / `find_or_create_port`), which merges fields across
scans via conflict-resolution rules; the fields listed are what the parser
*supplies*.

### Port / host scanners

| Field → | nmap (XML) | gnmap | masscan | naabu | rustscan |
| --- | --- | --- | --- | --- | --- |
| **Host** `ip_address` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `hostname` | ✓ | ✓ | — | — | — |
| `state` / `state_reason` | ✓ / ✓ | ✓ / (always `""`) | `up` (hard-coded) | `up` | `up` |
| `os_name` / `os_accuracy` | ✓ / ✓ | — | — | — | — |
| `os_family` / `os_generation` / `os_type` / `os_vendor` | ✓ | — | — | — | — |
| `smb_signing` | ✓ (from `smb-security-mode` host script) | — | — | — | — |
| **Port** `port_number` / `protocol` / `state` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `service_name` | ✓ | ✓ (if present) | greppable lines only | from URL scheme | — |
| `service_product` / `_version` / `_extrainfo` / `_method` / `_conf` | ✓ (full set) | `_version` only | — | — | — |
| **Other** | `ScanInfo` row; port + host **`Script`** rows (`script_id`, `output`) | — | — | — | — |

Notes:
- **nmap** streams XML via `iterparse_safe`; relabels itself masscan if
  `<nmaprun scanner="masscan">`. Down hosts with no ports/OS/scripts are
  dropped. Sets `Scan.version`, `command_line` (`@args`), `start_time`.
- **gnmap** parses grepable `Host:`/`Ports:`/`Status:` lines; incrementally
  fills `Scan` metadata (tool, version, command line, start/end) from header
  comments; detects masscan-vs-nmap origin.
- **masscan** is the **only** parser that bypasses the dedup service — it
  writes **raw bulk SQL** to `hosts_v2`/`ports_v2` (`ON CONFLICT` upserts) for
  throughput on tens-of-thousands-of-host scans. Accepts `.xml` / `.json` /
  list. Service name (greppable only) merges via an empty-or-longer-wins CASE.
  **Fails closed** (raises if 0 hosts).
- **naabu** / **rustscan** funnel through the shared `persist_host_observation`
  helper. naabu accepts `.json`/`.jsonl`/text and **fails closed**; rustscan is
  text-only and does **not** fail closed.

### Vulnerability / AD / credential scanners

**Only Nessus, OpenVAS, and Nikto create `Vulnerability` rows.** All vuln rows
go through `upsert_vulnerability` (app-level dedup on
`(host_id, source, plugin_id|title, port_id)`; `db.flush()` after add).

**Nessus** (`.nessus`/`.xml`; streamed via defusedxml; persistence done by
`NessusIntegrationService` + `VulnerabilityService`, committed in batches):
- **Host:** `ip_address`, `hostname` (`host-fqdn`→`netbios-name`),
  `os_name` (`operating-system`), `state=up`. Also writes `HostAttribute` rows
  (hostname / netbios_name / os_name with per-field confidence).
- **Port:** created only when the finding's port ≠ 0.
- **Vulnerability columns written:** `plugin_id`, `title` (plugin name),
  `description` (`description` → `synopsis`), `severity` (0–4 → INFO/LOW/
  MEDIUM/HIGH/CRITICAL), `source=NESSUS`, `solution`, `references` (CVE-MITRE
  URLs), `exploitable`, `cve_id` (first CVE only).
- **Parsed but dropped at persistence:** `cvss_base_score`/`cvss3_*`/
  `cvss_vector`, `plugin_output`, `risk_factor`, publication dates,
  `service_name`. (So `cvss_score` is *not* populated on the Nessus path.)
- **`exploitable`** is derived from `exploit_available` /
  `metasploit_name` / `core_impact_name` / `canvas_package` /
  `exploit_code_maturity ∈ {functional, high, proof-of-concept}`. (Re-upload
  old scans to backfill; only emitted since v2.83.2.)

**OpenVAS / Greenbone** (`.xml`; streamed per `<result>`, savepoint per result):
- **Host:** `ip_address` only (`state=up`). **Port:** `port_number`/`protocol`.
- **Vulnerability columns written:** `title` (`name`), `severity`,
  `plugin_id` (NVT `oid`), `description`, **`cvss_score`** (← `<severity>` /
  `cvss_base`), `cve_id` (first CVE), `solution`, `source=OPENVAS`. Severity
  from CVSS numeric, falling back to `<threat>` text. (OpenVAS *does* persist
  `cvss_score`, unlike Nessus.)

**NetExec (NXC)** (`.json` or console text; auto-detected): host
(`hostname`, `os_name`, `domain`, `smb_signing`) + port (`445`/SMB etc.) + a
`NetexecResult` row (`auth_success`, `username`, `domain_name`, `shares`,
truncated `raw_output`) + per-field `HostConfidence`/`PortConfidence` and
`ConflictHistory` rows. **No `Vulnerability` rows.** Credentials parsed from
`DOMAIN\user:pass (flag)`; empty username preserved as a weak-auth/guest
signal.

**SMBMap** (`.json`/`.txt`): host `ip_address` only + a hard-coded
`445/tcp/smb` port. **Fails closed.** Share *contents* are not stored.

**BloodHound / SharpHound** (`.json`; ≥50 MB streamed via `ijson`): reduces
**computer objects only** to `(ip_address, hostname)` host inventory. AD
edges / ACLs / sessions / groups / users / GPOs are **not** parsed. No vuln or
relationship model populated.

### Web fingerprint / directory tools

The three web fingerprinters share the unified `web_interfaces` table, keyed by
`source`, deduped on `(scan_id, url, source)`, with cert fields derived once at
ingest via `cert_fields.derive_cert_fields(tls_info)`.

| WebInterface field → | httpx (`source="httpx"`) | whatweb (`source="whatweb"`) | eyewitness (`source="eyewitness"`) |
| --- | --- | --- | --- |
| `url` / `protocol` / `port` / `ip_address` | ✓ | ✓ | ✓ |
| `status_code` | ✓ | ✓ | ✓ |
| `title` (≤500) / `server_header` (≤255) | ✓ | ✓ | ✓ |
| `content_length` | ✓ | — (whatweb has none) | ✓ |
| `technologies` | ✓ (`tech`/`technologies`, flattened) | ✓ (from `plugins` dict) | — |
| `favicon_hash` | ✓ | — | — |
| `tls_info` + `cert_not_after` + `cert_self_signed` | ✓ | — (no TLS block) | — |
| `screenshot_path` / `page_text` | — | — | ✓ |
| `raw` | ✓ | ✓ | ✓ |

- **httpx** / **whatweb** accept `.json`/`.jsonl`; **eyewitness** accepts
  `.json`/`.csv`/`.zip` (zip carries the screenshots, extracted under
  `uploads/web_screenshots/{scan_id}/`, with decompression-bomb caps: ≤50 MB
  per file, ≤500 MB total, ≤5000 entries). All three report
  `last_parse_stats` (`skipped` / `warnings` / `summary`).
- **Nikto** (`.json`/`.csv`/`.txt`): host + a `http`/`80` port + **`Vulnerability`
  rows** (`source=NIKTO`; `title`/`description` from the finding message,
  `plugin_id` from the Nikto/OSVDB id, `cve_id`, severity → defaults LOW). No
  WebInterface rows.
- **DirBuster family** (DirBuster/Gobuster/Feroxbuster/ffuf/Dirsearch;
  `.json`/`.csv`/`.txt`): host + port, with discovered paths aggregated into the
  port's **`service_extrainfo`** string (`"[code] path (sizeB)"`, capped at 50
  entries). No WebInterface and no Vulnerability rows — discovered paths are
  *not* findings.

### DNS / subdomain / scope

`DNSRecord` columns are `project_id, scan_id, domain, record_type, value, ttl,
resolver_name` (there is **no** `hostname`/`ip_address` column on `DNSRecord`).

| Field → | dnsx | dns CSV | amass / subfinder |
| --- | --- | --- | --- |
| Formats | `.json`/`.jsonl` | `.csv` | `.json`/`.jsonl`/`.txt` |
| `domain` | host or PTR name | `name` column | hostname (`name`/`host`/`domain`) |
| `record_type` | A/AAAA/CNAME/MX/NS/TXT/SOA/SRV/CAA/ANY/AXFR/PTR | from `type` column | always `A` |
| `value` | the answer / IP | `address` column (IP) | resolved IP |
| `ttl` | int rows only | recognized but **unused** | — |
| `resolver_name` | ✓ (**only parser that sets it**) | — | — |
| Feeds `Host.hostname` | PTR (overwrite) + A/AAAA (no overwrite) | PTR rows only | always (needs resolved IP) |

- **dnsx** is the resolver-attribution path: it counts NXDOMAIN/SERVFAIL/REFUSED
  failures and per-resolver hits into `parser_warnings`, and **fails closed**.
  PTR answers authoritatively set `Host.hostname`; forward A/AAAA answers create
  assets but never clobber an existing hostname.
- **dns CSV** expects `record_type` + `name` + `address` columns (with aliases);
  gated by a header heuristic so an arbitrary CSV doesn't become a silent
  zero-record DNS scan.
- **amass** **requires a resolved IP** — hostname-only rows are silently
  dropped (the defining behavior). Tags the scan `subfinder` when the filename
  says so.
- **subnet_parser** is **not** a scan parser (no `parse_file`, no `Scan`). It's
  used by the **Scope import** page: `parse_subnet_csv` returns
  `(cidr, [labels], description, site)` tuples (labels ≤60 chars, site ≤255);
  `parse_cidr_list` returns normalized CIDRs. It also hosts the subnet-matching
  utilities (`find_matching_subnets`, IP-trie cache) used during correlation.

---

## Part 2 — Adding a new parser

A parser is a small class with one job: turn a file into a `Scan` plus the host
/ port / finding rows it implies, reusing the shared toolkit so dedup,
correlation, and quality-reporting come for free.

### The parser contract

```python
class MyToolParser:
    def __init__(self, db: Session):
        self.db = db
        self.dedup_service = HostDeduplicationService(db)

    def parse_file(self, file_path: str, filename: str, **kwargs) -> models.Scan:
        self._project_id = kwargs.get("project_id")
        # ensure_scan is keyword-only and `filename` is required.
        scan = ensure_scan(
            self.db, filename=filename, tool_name="mytool",
            scan_type="port_scan", project_id=self._project_id,
        )
        # ... read file_path, build hosts/ports/findings, persist ...
        correlate_scan(self.db, scan.id)   # map new hosts to scopes/subnets
        return scan
```

- **Constructor** takes the DB session; **`parse_file(file_path, filename,
  **kwargs)`** returns the `Scan` (read `project_id` from `kwargs`).
- **The orchestrator** commits, tags `project_id`, backfills `command_line`,
  and runs DNS enrichment — your parser should **not** commit the final
  transaction itself (parsers that do, like masscan, do so deliberately for
  batch memory reasons and accept the trade-off).
- **Fail closed:** raise `ValueError` when the file yields **zero** records, so
  a misrouted or malformed file surfaces a parse error instead of a silent
  empty scan. (Most parsers do this; it's the expected convention.)
- **Set `scan.tool_name`** — it drives display *and* the
  `tool_name_hint` mismatch warning for agent uploads.
- **Optional quality stats:** set `self.last_parse_stats = {"skipped": N,
  "warnings": "...", "summary": "..."}`. These land on the `IngestionJob` as
  `skipped_count` / `parser_warnings` (visible on Scans → Ingestion Jobs).
  *(Note: several older parsers only log skips locally and don't set this —
  prefer setting it in new parsers.)*

### Reuse the shared toolkit (don't reinvent)

`app/parsers/parser_utils.py`:
- `ensure_scan(db, *, filename, tool_name, scan_type, command_line=None,
  project_id=None)` — create the `Scan` row (all args after `db` are
  keyword-only; `filename`, `tool_name`, `scan_type` are required).
- `persist_host_observation(*, dedup_service, scan_id, ip_address,
  hostname=None, state="up", ports=[...], host_data=None, project_id=None,
  isolate=False)` — the one-stop "I saw this host with these ports" writer
  (keyword-only; wraps the dedup service you constructed in `__init__`;
  `isolate=True` puts each host in its own savepoint so one bad record can't
  fail the batch).
- `upsert_vulnerability(db, host_id, scan_id, source, title, severity, ...)` —
  the **only** correct way to create a `Vulnerability` (handles app-level dedup
  and the required `db.flush()`).
- `map_numeric_severity(score)` / `map_text_severity(text)` — canonical
  severity mapping; reuse these instead of hand-rolling thresholds.
- `extract_first_ip(text)` / `normalize_ip(value)` /
  `parse_host_port_token(token)` — robust IP/host:port extraction.
- `resolve_host_cached` / `resolve_port_cached` — per-file caches for the
  web-interface parsers.
- `correlate_scan(db, scan_id)` — map newly-seen hosts to scopes/subnets
  (call once at the end).

`app/parsers/streaming_json.py` → `iter_json_records(file_path, tool_label)`
streams `.json` arrays, single objects, and `.jsonl` uniformly (use this, not
`json.load`, so large files don't OOM the worker).

`app/parsers/xml_stream_helpers.py` → `iterparse_safe()` (XXE/billion-laughs/
huge-tree-hardened lxml iterparse), `clear_element()`, `strip_namespace()`.

`app/services/cert_fields.py` → `derive_cert_fields(tls_info)` for the typed
`cert_not_after` / `cert_self_signed` columns.

### Choose a persistence path

1. **Dedup service** (`persist_host_observation` or `find_or_create_host` /
   `find_or_create_port` directly) — the default for almost everything.
2. **`web_interfaces` upsert** — for web fingerprinters; copy the
   httpx/whatweb pattern (`source="yourtool"`, dedup on `(scan_id, url,
   source)`).
3. **`DNSRecord`** — for resolvers; copy dnsx/amass.
4. **Raw bulk SQL** — only if you genuinely have masscan-scale throughput
   needs; this gives up the dedup service's conflict-resolution and concurrency
   handling, so justify it.

### Wire up detection + registration (TWO places + the detector)

1. **Detector** — add `looks_like_mytool(sample: bytes, filename: str) -> bool`
   to `app/parsers/content_detection.py`. Make the signature **specific** (a
   distinctive key combo or root tag); filename substring matching is a cheap
   tie-breaker but shouldn't be the only signal. Watch the
   ordering caveats baked into `_build_parsing_attempts` (e.g. don't match a
   string that also appears in another tool's nested output — that's the bug
   the OpenVAS/nmap ordering comment documents).
2. **Routing** — in `IngestionService._build_parsing_attempts`, under the right
   extension branch, `attempts.append(("mytool_json", MyToolParser, "My Tool
   output"))` gated by your detector. Order it by specificity relative to the
   neighbours.
3. **Instantiation** — register the class in `parser_map` (or `_extra_parsers`
   for a lazily/optionally-imported one). **If you skip this, the dispatcher
   raises `Unsupported parser class` at runtime** — the routing and the map
   must agree.

### Schema discipline (read before adding columns)

When your tool produces a value, decide deliberately where it lands (see the
**Column-vs-blob policy** in `CLAUDE.md`):
- Give it a **typed column** if any view, filter, DSL predicate, dashboard, or
  insight needs to query/sort/aggregate on it across hosts (e.g. a new cert
  attribute, a TLS version, a signing flag). Promote it at ingest like
  `derive_cert_fields` does.
- Keep it in a **`raw`/JSON blob** only if it's opaque provenance retained for
  re-processing and never queried by a column predicate. A blob field that ends
  up in a `WHERE`/`GROUP BY` is a signal to promote it, not to add a functional
  index.

### Testing

Add a test under `backend/tests/` (run via
`docker compose run --rm --no-deps -v "$PWD/backend:/app" backend python -m
pytest tests/test_mytool_parser.py`). Cover at minimum:
- a representative happy-path file → asserts the host/port/finding fields you
  documented above actually persist;
- the **fail-closed** path (empty / wrong-format input raises `ValueError`);
- if you set `last_parse_stats`, a malformed-record file → asserts
  `skipped` / `parser_warnings`.

Then add a row to [`UPLOAD_FORMATS.md`](./UPLOAD_FORMATS.md) and the field
tables in this file, and bump the backend version per `CLAUDE.md`.
