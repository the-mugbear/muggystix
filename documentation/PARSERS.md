# Parser Reference & Contributor Guide

> **Last verified against:** backend 2.427.1 / frontend 5.309.1 (2026-09-26)
>
> The **"What BlueStick reads"** page in the app (Reference → `/reference/tool-coverage`,
> data in `backend/app/data/parser_coverage.json`) is the operator-facing,
> test-pinned summary of the same ground: per tool, what it reports, the level
> BlueStick takes each item to, where it is shown and the known gaps.
>
> Companion to [`UPLOAD_FORMATS.md`](./UPLOAD_FORMATS.md). That doc is the
> *user-facing* "what can I upload" table. **This** doc is for operators and
> maintainers: **Part 1** is the field-by-field reference for what each parser
> actually extracts and where it lands in the schema; **Part 2** is the guide
> to adding a new parser.

---

## Part 0 — How ingestion routes a file to a parser

A single upload becomes an `IngestionJob`. From the UI it is first **staged**
(`create_job(..., stage=True)` → status `staged`): the file is on disk, no
worker touches it, and the operator reviews the detected format before
pressing Import (`staged_import_service.detect_for_job` → `POST
/upload/jobs/{id}/start`). Agents and direct API callers queue immediately.
Once queued, a background worker (`app/worker.py`) claims the job and runs
`IngestionService._process_job`. Routing:

0. **Operator override** — if the job carries a `format_override` (chosen in the
   review, or on a retry), the attempt list is **exactly that parser and nothing
   else**, resolved through `format_registry.resolve_parser`. No fallbacks, on
   purpose: a wrong choice fails visibly ("Review the format and retry") rather
   than silently routing elsewhere. An unknown override fails with "The chosen
   format '…' is not one this deployment can parse." Detection still runs in
   shadow (`_detect_without_override`) so the job records the whole chain —
   `detected_file_type` → `format_override` → `final_file_type`.
1. **Sample read** — `_read_sample()` reads the first **64 KB** of the file.
2. **Attempt list** — `_build_parsing_attempts(job, sample)`
   (`app/services/ingestion_service.py`) builds an **ordered** list of
   `(file_type, ParserClass, description)` descriptors. `file_type` is a registry
   key (`nmap_xml`, `masscan_list`, `dirbuster_csv`, …), not display text — the
   label and family live in `format_registry.FORMATS`. The list is branched first
   on the file **extension** (`.xml`/`.gnmap`/`.json`/`.jsonl`/`.ndjson`/`.zip`/
   `.csv`/`.txt`; `.nessus`, or an `.xml` that `is_nessus_sample()` recognises, is
   a pre-branch check) and then gated by per-tool **content detectors**
   (`looks_like_*` in `app/parsers/content_detection.py`; httpx, whatweb and
   testssl keep theirs in their own parser module). Ordering is by
   structural specificity — e.g. for `.xml`, `looks_like_masscan_xml` (scanner
   attr) is tried before nmap, OpenVAS before nmap, and Nessus is appended
   last-ditch.
3. **First success wins** — each attempt's parser runs in turn; the first that
   parses without raising produces the `Scan` and the job completes. If the
   attempt list is **empty**, the job fails with an `Unsupported file type or
   format` parse error (there is intentionally **no** silent "treat unknown
   `.txt` as masscan" fallback anymore).
4. **Post-processing** — the orchestrator commits, tags `scan.project_id`,
   backfills `scan.command_line` from the agent's `command_run` when the parser
   left it blank, and records `skipped_count`, `parser_warnings`, `partial`,
   the progress summary and `uninterpreted_lines` on the job (see Part 2).
   JSON lines the shared reader could not decode add to the skipped count and
   mark the job partial.

### Detection basis — what the review shows the operator

Detection is **structural first, filename second**. The staged review runs
detection twice — with a neutral filename and with the real one — and the
difference is each candidate's *basis*:

| Basis | Meaning | Ready to import without the operator? |
|---|---|---|
| `structure` | the content alone selects this parser | yes |
| `filename` | only the name does — a hint | no — confirm or choose |
| `fallback` | the dispatcher would merely *try* it, having recognised nothing | no — never even suggested |

A try-anyway attempt must be marked at the source: wrap it in
`ingestion_service._fallback(...)` (a `FallbackAttempt` — still a 3-tuple to
every consumer). Surviving the neutral-filename pass is not evidence by itself:
before `fallback` existed, any unrelated `.xml` read "Nmap XML · recognised by
structure".

Parsers also receive `source_tool=` (the tool the operator named at import) in
`parse_file(**kwargs)`. `amass_parser.attribute_tool` is the reference order —
named tool, then JSON shape, then filename hint, else the honest generic label.
Never invent attribution from a filename alone.

> **Four-place registration.** A parser is named in **four** places, and four
> tests fail if they disagree: `_build_parsing_attempts` (routing),
> `build_parser_dispatch_map()` (instantiation — the dispatcher raises
> `Unsupported parser class` for a routed class that is not in the map; this is
> exactly how RDAP and testssl once broke), and `FORMATS` in
> `app/services/format_registry.py` (the `file_type` key with its label and
> family, which the review, the format chooser and the job's format chain all
> read), and the tool's entry in `app/data/parser_coverage.json` (the "What
> BlueStick reads" page, pinned by `tests/test_parser_coverage.py`). (Details in
> Part 2.)

### The data model parsers write to

| Model | Owner table | Written by |
| --- | --- | --- |
| `Scan` | `scans` | every parser (the container; carries `tool_name`, `scan_type`, `command_line`, `version`, `start_time`/`end_time`) |
| `Host` | `hosts_v2` | every host-bearing parser (deduped: one row per `(project_id, ip_address)`) |
| `Port` | `ports_v2` | port scanners + web/dir/vuln parsers |
| `HostScanHistory` / `PortScanHistory` | `host_scan_history` / `port_scan_history` | per-scan observation audit trail |
| `Vulnerability` | `vulnerabilities` | **Nessus, OpenVAS, Nikto, Nuclei, nmap** (NSE vuln scripts, vulners, catalog checks), **testssl** (rated checks), **NetExec** and **SMBMap** (catalog checks) — the "scanner observations" |
| `WebInterface` | `web_interfaces` | **httpx, whatweb, eyewitness, testssl, nmap** (TLS scripts on web ports) — unified web view, keyed by `source` |
| `WebPath` | `web_paths` | **dirbuster family** — one row per discovered path (url, path, status, size, tool) |
| `DNSRecord` | `dns_records` | The general name-evidence table: one immutable observation about a name per scan. Written by **dnsx, dns CSV, amass, httpx**, the HTTP observations of the name tested by **nikto, nuclei, testssl, dirbuster** (`dns_name_service.bind_hostname`), and by EVERY host parser through `host_deduplication_service` (a scanner-reported hostname becomes a `SCANNER` observation). `record_type` is a real RR type or one of the `IMPORT`/`DISCOVERED`/`SCANNER`/`HTTP`/`CERT` kinds. Columns are `domain` + `value` (*not* `hostname`/`ip_address`), plus `name_id` → `dns_names`. Single write path: `dns_name_service.record_observation`. |
| `DNSName` | `dns_names` | The named asset itself (FQDN, unique per project + normalised name) — an identity, never merged into a `Host`. Created through `dns_name_service.get_or_create_name`. |
| `NetworkAttribution` | `network_attributions` | **rdap** only — registration data for an address block (cidr, org, country, handle, ASN), correlated to hosts after ingest. |
| `HostNetworkAttribution` | `host_network_attributions` | **rdap** — the host ↔ block links |
| `Subnet` / `SubnetLabel` | scope tables | `subnet_parser` (Scope import page only) |
| `ScanInfo`, `Script`/`HostScript` | `scan_info`, `scripts_v2` / `host_scripts_v2` | nmap (masscan `--banners` also become port script rows) |
| `HostAttribute` | `host_attributes` | Nessus (confidence-tracked host facts) |
| `NetexecResult` | `netexec_results` | netexec, smbmap (`tool="smbmap"`) |
| `HostConfidence`/`PortConfidence`, `ConflictHistory` | confidence tables | netexec |

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
| `mac_address` / `mac_vendor` | ✓ | — | — | — | — |
| `smb_signing` | ✓ (from `smb-security-mode` host script) | — | — | — | — |
| **Port** `port_number` / `protocol` / `state` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `service_name` | ✓ | ✓ (if present) | greppable lines only | from URL scheme | — |
| `service_product` / `_version` / `_extrainfo` / `_method` / `_conf` | ✓ (full set) | `_version` only | — | — | — |
| **Other** | `ScanInfo` row; port + host **`Script`** rows (`script_id`, `output`); scanner observations (see notes); `WebInterface` rows for web ports with TLS scripts | `open\|filtered` ports kept | `--banners` output as port script rows | — | — |

Notes:
- **`os_family`** — any parser that supplies an OS name (Nessus, NetExec …)
  gets `os_family` derived from it at ingest (`os_family_from_name` in the
  dedup service, v2.421.0) when no scanner reported one.
- **nmap scanner observations** (v2.413.0+) — vulners and vuln-category NSE
  results become `NMAP` `Vulnerability` rows (CVEs, exploitability), and
  catalog checks are recorded through `record_misconfig` (VNC without auth, SMB
  signing not required, SMBv1, deprecated TLS, an expired certificate).
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
  text-only and also **fails closed** — and warns when the file holds an nmap
  report it does not read.

### Vulnerability / AD / credential scanners

**Nessus, OpenVAS, Nikto, Nuclei, nmap, testssl, NetExec and SMBMap create
`Vulnerability` rows** (the *scanner observations*). Vuln rows go through
`upsert_vulnerability` (app-level dedup on `(host_id, source, plugin_id|title,
port_id)`; `db.flush()` after add); a weakness from the misconfiguration catalog
goes through `record_misconfig` (`app/services/misconfig_checks.py`), which
stamps its `check_id` so the same weakness reported by different tools is one
issue.

**Nessus** (`.nessus`/`.xml`; streamed via defusedxml; persistence done by
`NessusIntegrationService` + `VulnerabilityService`, committed in batches):
- **Host:** `ip_address`, `hostname` (`host-fqdn`→`netbios-name`),
  `os_name` (`operating-system`), `state=up`. Also writes `HostAttribute` rows
  (hostname / netbios_name / os_name with per-field confidence).
- **Port:** created only when the finding's port ≠ 0, and keyed by **`(port, protocol)`** —
  UDP and SCTP findings keep their protocol (v2.365.0). Before that every Nessus
  finding was attached to a TCP port, so an SNMP/NTP/IKE/DNS-over-UDP finding
  created or joined a phantom open TCP port; scans imported earlier need a
  re-import to correct (the vulnerability row never stored the protocol).
- **Informational (severity 0) rows** can be skipped at ingest — the upload form's
  switch wins, then the project's setting, then the deployment default
  (`resolve_skip_informational`).
- **Vulnerability columns written:** `plugin_id`, `title` (plugin name),
  `description` (`description` → `synopsis`), `severity` (0–4 → INFO/LOW/
  MEDIUM/HIGH/CRITICAL), `source=NESSUS`, `solution`, `references` (CVE-MITRE
  URLs, see-also links, exploit frameworks), `exploitable`, `cve_id` (first
  CVE), **`cvss_score`/`cvss_vector`** (CVSSv3 preferred over v2),
  **`plugin_output`** (raw plugin evidence, pre-truncated to
  `NESSUS_PLUGIN_OUTPUT_MAX_CHARS`), `cpe` / `installed_version` /
  `fixed_version`, and `check_id` for plugins mapped to the catalog. The host
  also gets `netbios_name` / `mac_address`, and Nessus's `svc_name` names the
  port's service (v2.416.0).
- **Parsed but dropped at persistence:** `risk_factor`, publication dates.
  (CVSS + `plugin_output` are persisted as of the plugin-output
  migration; re-upload old scans to backfill — the columns are populated only on
  (re-)ingest.)
- **`exploitable`** is derived from `exploit_available` /
  `metasploit_name` / `core_impact_name` / `canvas_package` /
  `exploit_code_maturity ∈ {functional, high, proof-of-concept}`. (Re-upload
  old scans to backfill; only emitted since v2.83.2.)

**OpenVAS / Greenbone** (`.xml`; streamed per `<result>`, savepoint per result):
- **Host:** `ip_address` only (`state=up`). **Port:** `port_number`/`protocol`.
- **Vulnerability columns written:** `title` (`name`), `severity`,
  `plugin_id` (NVT `oid`), `description` (from the NVT tags — summary,
  insight, impact, affected — falling back to the result's description),
  **`cvss_score`** (← `<severity>` / `cvss_base`), `cve_id` (first CVE),
  `solution`, `references` (URLs, CERT-Bund/DFN-CERT ids, further CVEs),
  `plugin_output` (the detection result and QoD), `source=OPENVAS`. Severity
  from CVSS numeric, falling back to `<threat>` text.

**NetExec (NXC)** (`.json` or console text; auto-detected; read with
`read_tool_text`, so a UTF-16 capture from PowerShell's `>` and NUL bytes are
handled, v2.420.0): host (`hostname`, `os_name`, `domain`, `smb_signing`) +
port (`445`/SMB etc.) + a `NetexecResult` row (`auth_success`, `username`,
`domain_name`, `shares`, `local_admin`, `smbv1`, `writable_share`, truncated
`raw_output`) + per-field `HostConfidence`/`PortConfidence` and
`ConflictHistory` rows. **Catalog observations** through `record_misconfig`:
SMB signing not required, SMBv1 enabled, SMB null session, VNC without
authentication, anonymous FTP. Credentials parsed from
`DOMAIN\user:pass (flag)`; empty username preserved as a weak-auth/guest
signal. The lines it drops or keeps only as text are published as redacted
shapes (`last_parse_stats["uninterpreted"]`, `app/services/line_shapes.py`,
v2.418.0) — NetExec is currently the only parser that does. Large imports
report progress and stop on cancel (v2.422.0).

**SMBMap** (`.json`/`.txt`): host (+ name) and the SMB port from the report's
host line (445 when none is given); a `NetexecResult` row (`tool="smbmap"`)
with the share table, the session (NULL / guest / authenticated) and the
writable flag; a NULL or guest session is an `smb_null_session` catalog
observation. **Fails closed.** Share *contents* are not stored.

**BloodHound / SharpHound** (`.json`; ≥50 MB streamed via `ijson`): reduces
**computer objects only** to `(ip_address, hostname)` host inventory. AD
edges / ACLs / sessions / groups / users / GPOs are **not** parsed. No vuln or
relationship model populated. Computers without an address are counted as
skipped, and a file with no addressed computer **fails closed** (v2.417.0).

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

- **httpx** / **whatweb** accept `.json`/`.jsonl`/`.ndjson`; **eyewitness** accepts
  `.json`/`.csv`/`.zip` (zip carries the screenshots, extracted under
  `uploads/web_screenshots/{scan_id}/`, with decompression-bomb caps: ≤50 MB
  per file, ≤500 MB total, ≤5000 entries). All three report
  `last_parse_stats` (`skipped` / `warnings` / `summary`).
- **Nikto** (`.json`/`.csv`/`.txt`): host + the target's http port from the
  report (80 when none is given) + **`Vulnerability` rows** (`source=NIKTO`;
  `title`/`description` from the finding message, `plugin_id` from the
  Nikto/OSVDB id, `cve_id`, `references` and the request evidence as
  `plugin_output`, severity → defaults LOW). Missing-security-header results
  are catalog checks (`record_misconfig`, v2.414.0), and the name tested is
  bound to the row (`name_id`). No WebInterface rows.
- **DirBuster family** (DirBuster/Gobuster/Feroxbuster/ffuf/Dirsearch;
  `.json`/`.csv`/`.txt`): host + port, and each discovered path is a
  **`web_paths`** row (url, path, status, size, tool — v2.390.0), shown in the
  host's "Discovered paths" section and matched by `path:`. The port is named
  http/https only when nothing had named it. No WebInterface and no
  Vulnerability rows — discovered paths are *not* findings.

### DNS / subdomain / scope

`DNSRecord` columns are `project_id, scan_id, domain, record_type, value, ttl,
resolver_name` (there is **no** `hostname`/`ip_address` column on `DNSRecord`).

| Field → | dnsx | dns CSV | amass / subfinder |
| --- | --- | --- | --- |
| Formats | `.json`/`.jsonl` | `.csv` | `.json`/`.jsonl`/`.txt` |
| `domain` | host or PTR name | `name` column | hostname (`name`/`host`/`domain`) |
| `record_type` | A/AAAA/CNAME/MX/NS/TXT/SOA/SRV/CAA/ANY/AXFR/PTR | from `type` column (an unknown type is rejected) | A/AAAA (`DISCOVERED` for unresolved names) |
| `value` | the answer / IP | an IP for A/AAAA/PTR, the record data otherwise | resolved IP |
| `ttl` | int rows only | ✓ (when numeric) | — |
| `resolver_name` | ✓ (**only parser that sets it**) | — | — |
| Feeds `Host.hostname` | PTR (overwrite) + A/AAAA (no overwrite) | PTR rows only | always (needs resolved IP) |

- **dnsx** is the resolver-attribution path: it counts NXDOMAIN/SERVFAIL/REFUSED
  failures and per-resolver hits into `parser_warnings`, and **fails closed**.
  PTR answers authoritatively set `Host.hostname`; forward A/AAAA answers create
  assets but never clobber an existing hostname.
- **dns CSV** expects `record_type` + `name` + `address` columns (with aliases);
  gated by a header heuristic so an arbitrary CSV doesn't become a silent
  zero-record DNS scan. Rows are stored by record type (v2.416.0): a type that
  is not a real RR type, or an A/AAAA/PTR row whose value is not an IP of that
  family, is rejected and counted.
- **amass** creates a host only for rows with a resolved IP; hostname-only
  rows are **kept** as unresolved names in the Names inventory (a `DNSName` +
  `DISCOVERED` observation with no host — pre-v2.322.0 they were dropped).
  The scan's tool is `attribute_tool`'s answer: the source tool the operator
  named, else the JSON shape (amass / subfinder), else a filename hint, else
  `hostname-list`.
- **subnet_parser** is **not** a scan parser (no `parse_file`, no `Scan`). It's
  used by the **Scope import** page: it parses SCOPE, not just subnets —
  `parse_scope_csv(...)` returns `(subnets, domains, ignored_columns)` where a
  subnet row is `(cidr, [labels], description, site)` (labels ≤60 chars, site
  ≤255) and a domain row carries the name and whether it is a wildcard;
  `parse_scope_list(...)` returns `(cidrs, domains)` for a flat list. Any row that
  is neither raises, so a typo is never silently dropped. Subnet matching is NOT
  here: the IP trie is `app/services/ip_trie.py` and correlation is
  `SubnetCorrelationService` (the parser's old DB-backed trie and lookup helpers
  were deleted in v2.369.0 — nothing called them and they ignored project
  boundaries).

### Enrichment, TLS posture and template scanners

**rdap** (`rdap_parser.py`; `.json`/`.ndjson` from the bundled
`scripts/rdap-lookup.py`) is not a host scanner. It writes **`NetworkAttribution`**
rows — registration data for an address block: `cidr`, organisation, country,
registry handle, ASN — then `correlate_hosts` links each host to its most specific
block (`HostNetworkAttribution`). A record gives one block per `cidr0_cidrs`
entry, else the exact blocks summarising its start–end range (v2.416.0). It builds its `Scan` directly rather than through `ensure_scan`, commits
itself (see the contract below) and reports `last_parse_stats`. The lookup runs
terminal-side: the server never queries a registry.

**testssl** (`testssl_parser.py`; testssl.sh JSON) writes one **`web_interfaces`**
row per endpoint — the SNI name testssl probed, else the IP, and the port
(v2.416.0) — with `source="testssl"`: no `title` / `technologies` /
`screenshot_path`, but the promoted TLS columns — `tls_weak_protocol`
(SSLv2/SSLv3/TLS 1.0/1.1 offered), certificate expiry and self-signed. Every
rated check (LOW … CRITICAL) is also a scanner observation (`source=TESTSSL`),
TLS results under the catalog's titles; the letter grade and the per-cipher
rows are not stored.

**nuclei** (`nuclei_parser.py`; `.json` / `.jsonl` / `.ndjson` — `-je` array or
`-jsonl`/`-j` lines; recognised by `template-id` beside `info` / `matched-at`)
turns each template match into a `NUCLEI` scanner observation with Nuclei's own
severity, on the host of its `ip` and the port it matched (recorded open). The
matcher name is part of the title and the row keys on title, so one template's
several matchers stay separate. Description, remediation, references, CVE and
CVSS come from the template's `info`; `matched-at` and `extracted-results` are
the evidence. Missing-security-header matchers are catalog checks
(`record_misconfig`). A result with no `ip` (DNS / file templates, `-omit-ip`)
cannot be placed and is counted as skipped (the import is then partial). Fails
closed on an empty or non-Nuclei file.

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
- **The orchestrator** commits, tags `project_id`, backfills `command_line`.
  Many parsers also commit themselves (in batches for memory, or before a pass
  that needs committed rows, as rdap's `correlate_hosts`); `grep db.commit
  app/parsers` for the current set. A committing parser writes part of a scan
  before it can fail — the dispatcher deletes a failed import's scan, so never
  let a re-observed row's cascading `scan_id` move (see CLAUDE.md, record
  isolation).
- **Record isolation:** a parser that catches a record's exception and carries
  on MUST wrap the record in `record_savepoint(db, observed, on_rollback)`;
  without it the failed flush poisons the session and the whole import fails
  after logging "skipped" (v2.419.0).
- **Fail closed:** raise `ValueError` when the file yields **zero** records, so
  a misrouted or malformed file surfaces a parse error instead of a silent
  empty scan. (Most parsers do this; it's the expected convention.)
- **Set `scan.tool_name`** — it drives display *and* the
  `tool_name_hint` mismatch warning for agent uploads.
- **Optional quality stats:** set `self.last_parse_stats = {"skipped": N,
  "warnings": "...", "summary": "...", "partial": bool, "uninterpreted": [...]}`.
  These land on the `IngestionJob` as `skipped_count` / `parser_warnings` /
  `partial` / `uninterpreted_lines` (visible on Inventory → Ingestion Results).
  `uninterpreted` is the lines you dropped or kept only as text, as REDACTED
  shapes from `app/services/line_shapes.py` (`ShapeTally`) — never a raw line,
  a credential or a cell value (v2.418.0; NetExec is the reference).
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
- `upsert_vulnerability(*, db, host_id, scan_id, source, title, severity, ...)` —
  **keyword-only** (the positional form is a `TypeError`); also takes
  `plugin_id`, `port_id`, `description`, `cvss_score`, `cve_id`, `solution`,
  `references`, `name_id` (the named endpoint a web finding was observed at),
  `exploitable`, `check_id`, `key_on_title` and `plugin_output` — read the
  signature. The way to create a scanner `Vulnerability` (handles app-level
  dedup and the required `db.flush()`); a **catalog weakness** goes through
  `record_misconfig` (`app/services/misconfig_checks.py`) instead.
- `record_savepoint(db, observed=None, on_rollback=None)` — one input record's
  writes as a unit (see "Record isolation" above).
- `read_tool_text(path) -> ToolText` — read a tool's text output with UTF-16
  (PowerShell `>`) decoded and NUL removed; required when text lines reach a
  Text column (PostgreSQL rejects NUL; v2.420.0).
- `ScanClock` — accumulates the scan window from timestamps in the tool's
  output and writes it onto the `Scan` (never over a window the parser set from
  an explicit run start/finish).
- `ScanHostObservations` / `record_hosts_in_scan` — the hosts a scan observed,
  for the per-scan history.
- `map_numeric_severity(score)` / `map_text_severity(text)` — canonical
  severity mapping; reuse these instead of hand-rolling thresholds.
- `extract_first_ip(text)` / `normalize_ip(value)` /
  `parse_host_port_token(token)` — robust IP/host:port extraction.
- `resolve_host_cached` / `resolve_port_cached` — per-file caches for the
  web-interface parsers.
- `correlate_scan(db, scan_id)` — map newly-seen hosts to scopes/subnets
  (call once at the end).

`app/parsers/streaming_json.py` → `iter_json_records(file_path, *, array_keys=(),
tool_label="JSON")` streams `.json` arrays, single objects, and `.jsonl` /
`.ndjson` uniformly; `array_keys` points it at a wrapped payload such as
`{"results": [...]}` (use this, not
`json.load`, so large files don't OOM the worker). Lines it cannot decode are
tallied (`begin_rejection_tally` / `end_rejection_tally`) and reported as
skipped by the orchestrator.

`app/services/dns_name_service.py` → `bind_hostname(...)` records the name a
web tool tested as an HTTP observation and returns its `name_id` for the rows
you write.

`app/parsers/xml_stream_helpers.py` → `iterparse_safe()` (XXE/billion-laughs/
huge-tree-hardened lxml iterparse), `clear_element()`, `strip_namespace()`.

`app/services/cert_fields.py` → `derive_cert_fields(tls_info)` for the typed
`cert_not_after` / `cert_self_signed` columns, `derive_cert_orgs(tls_info)` for
the subject/issuer organisation, and `derive_weak_protocol(tls_info)` for
`tls_weak_protocol` from a TLS block (used by httpx; testssl and nmap classify
each protocol with `_classify_tls_version`). The `has:weak_tls` DSL predicate
reads the column.

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

### Wire up detection + registration (the detector + FOUR places + the docs)

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
   A parser the dispatcher would try WITHOUT having recognised anything must be
   wrapped: `attempts.append(_fallback("mytool_json", MyToolParser, "…"))` — that
   is what stops the review showing it as "recognised by structure".
3. **Instantiation** — add the class to `build_parser_dispatch_map()` in
   `ingestion_service.py`: the `dispatch` dict for an always-imported parser, or
   the `(module_path, class_name)` tuple below it for a lazily/optionally imported
   one. **If you skip this, the dispatcher raises `Unsupported parser class` at
   runtime** — the routing and the map must agree.
4. **Format registry** — add a `_spec("mytool_json", "My Tool JSON", family,
   module, class_name, description)` row to `FORMATS` in
   `app/services/format_registry.py`. `family` is one of `port | vuln | web | dns
   | auth | other`. Every `file_type` the dispatcher can emit must be here: it is
   the one list the staged review, the "parse this as…" chooser and the job's
   format chain read, and it is how an operator's override resolves to your class.
5. **Tests that pin all of the above** — update them with the change:
   `tests/test_parser_dispatch_contract.py` (every detected class is
   dispatchable), `tests/test_ingestion_format_chain.py` (every emitted
   `file_type` is in the registry), and
   `tests/test_phase1_regressions.py::test_v2_27_0_content_detection_module_surface`
   (the public `looks_like_*` list), and `tests/test_parser_coverage.py` (every
   format has an entry in `app/data/parser_coverage.json`).
6. **What users are told** — add the tool's entry to
   `app/data/parser_coverage.json` (the "What BlueStick reads" page: what the
   tool reports, the level BlueStick takes it to, where it is shown, the known
   gaps; change it in the same commit as the parser), and add the tool to
   `documentation/UPLOAD_FORMATS.md`
   AND `frontend/src/data/uploadFormats.ts` (the upload dialog's list);
   `uploadFormats.test.ts` fails if they disagree, and
   `uploadFormatContract.test.ts` pins the accepted extensions to the backend
   allowlist (`ALLOWED_UPLOAD_EXTENSIONS` in `ingestion_service.py`).

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
