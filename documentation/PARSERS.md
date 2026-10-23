# Parser Reference & Contributor Guide

> **Last verified against:** backend 2.450.0 / frontend 5.329.0 (2026-10-02)
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
   (`app/services/ingestion_service.py`) returns an **ordered** list of
   `(file_type, ParserClass, description)` descriptors, built by
   `format_registry.detect` from the one parser registry
   (`app/services/format_registry.py`). `file_type` is a registry key
   (`nmap_xml`, `masscan_list`, `dirbuster_csv`, …), not display text — the label
   and family live in `FORMATS`. The list is branched first on the file
   **extension** (`DETECTION`: `.gnmap`/`.json`/`.jsonl`/`.ndjson`/`.zip`/`.csv`/
   `.txt`; `.xml` is the decision tree `XML_DECISIONS`; `.nessus`, or an `.xml`
   that `is_nessus_sample()` recognises, is a pre-branch check) and then gated by
   each format's **content detector** (its `sniffer`: a `looks_like_*` in
   `app/parsers/content_detection.py`, or httpx's, whatweb's and testssl's in
   their own parser module) or a filename `hint`. Ordering is by structural
   specificity — e.g. for `.xml`, `looks_like_masscan_xml` (scanner attr) is
   tried before nmap, OpenVAS before nmap, and Nessus is appended last-ditch.
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
   mark the job partial. A file that ends early — in any format whose reader
   can tell — is a failure of its own kind: see "A file that was cut short"
   below.

### Detection basis — what the review shows the operator

Detection is **structural first, filename second**. The staged review runs
detection twice — with a neutral filename and with the real one — and the
difference is each candidate's *basis*:

| Basis | Meaning | Ready to import without the operator? |
|---|---|---|
| `structure` | the content alone selects this parser | yes |
| `filename` | only the name does — a hint | no — confirm or choose |
| `fallback` | the dispatcher would merely *try* it, having recognised nothing | no — never even suggested |

A try-anyway attempt is marked at the source: a `*` after its key in
`format_registry.XML_DECISIONS` makes it a `FallbackAttempt` (still a 3-tuple
to every consumer). Surviving the neutral-filename pass is not evidence by itself:
before `fallback` existed, any unrelated `.xml` read "Nmap XML · recognised by
structure".

Parsers also receive `source_tool=` (the tool the operator named at import) in
`parse_file(**kwargs)`. `amass_parser.attribute_tool` is the reference order —
named tool, then JSON shape, then filename hint, else the honest generic label.
Never invent attribution from a filename alone.

> **One registry.** A parser is registered in ONE place,
> `app/services/format_registry.py`: its `FORMATS` row (the `file_type` key, label,
> family, parser class, detector) and its key in the `DETECTION` order of the
> extensions it arrives with. Routing (`detect`), instantiation
> (`build_parser_dispatch_map()` — the classes the registry names, so a routed
> class can no longer be missing from the map, which is how RDAP and testssl
> once broke), the public sniffer surface, the review, the format chooser and
> the job's format chain are all read from it. The tool's entry in
> `app/data/parser_coverage.json` (the "What BlueStick reads" page) must name
> every key (`tests/test_parser_coverage.py`). (Details in Part 2.)

### A file that was cut short

A scanner that was interrupted, or a copy that did not finish, leaves a file
that stops before it is complete. **One rule for every format in which that
can be seen** (owner decisions 2026-10-11): the operator is **asked** — nothing
is kept until they say so. The rule and its one mechanism live in
`app/parsers/cut_short.py`; `tests/test_every_format_at_a_cut.py` holds every
format of the registry to it, or to a stated reason why a cut cannot be told.

1. **The import fails, as that, and leaves nothing.** The reader reports the
   cut in the one place (`cut_short.settle_cut(records_read, …)`), which raises
   `TruncatedFile`. The failed-import cleanup runs at once, as for every
   failure; no half-imported scan waits for an answer. The job's parse error
   has `error_type = truncated_file`, served on every job read as
   `failure_kind`, and the reason says how much the file holds, in the
   format's own name (`format_registry`) and unit: "This WhatWeb JSON file was
   cut short: it ends after 28,253 complete records, before the end of the
   document." · "This Nmap XML file was cut short: it ends after 412 complete
   hosts, …" · for JSON lines "…, part-way through the line that follows."
   The count is also in `parse_errors.error_details.records_read`.
2. **The row offers "Import what the file holds"** (the upload's row in the
   Scans import history; analysts and above) beside the retry. A plain retry reads
   the same bytes and fails the same way.
3. **That action re-queues the job with one attempt's instruction**
   (`POST /upload/jobs/{id}/retry {"keep_truncated": true}` →
   `job.options.keep_truncated`, set and cleared only by
   `staged_import_service.queue_job`). The dispatcher opens the read report
   around the parser with it (`cut_short.begin_read(keep_truncated=True,
   label=…)`, a ContextVar); `settle_cut` then **returns** instead of raising
   and the reader simply stops. No parser takes the instruction as an
   argument, and none decides anything: a reader calls `settle_cut` and, if it
   comes back, ends its records. The record the cut fell in is never
   delivered — a reader only ever hands over a closed record, element or
   line — so no half record is written.
4. **The import completes PARTIAL**, with the warning "… it ends after N
   complete records, before the end of the document. Those records were
   imported; anything the tool wrote after that point is not in the file."
   (Nmap adds its own: "Incomplete XML — parsing stopped early, so hosts after
   this point are MISSING, not down.") It carries its upload identity like any
   completed import (an identical upload is then a duplicate), counts as an
   import that needs someone until it is dismissed, and is never retried by
   itself. A cancel, the timeout or a worker shutdown during that attempt
   cleans up like any other.

**An agent's own upload** can be kept by the agent after it asked its
operator: `POST /agent/uploads/{job_id}/keep-truncated {"operator_agreed":
true}` (MCP `keep_truncated_upload`) re-queues through the same
`queue_job`, and the request is written to the audit log
(`truncated_upload_kept`). The server never sees that conversation, so this
is not an approval by BlueStick: the agent is told to ask, the request states
that the operator agreed, the operator must be able to write to the project,
and every request is recorded (`documentation/AGENT_GUIDE.md`).

**Cut short vs invalid.** The two are told apart by *where* the reader fails,
never by a library's exception type, code or words. A failure reported only
after the whole file was handed over means every byte was acceptable and more
were expected (cut short); a failure before that is invalid content.

- *JSON* (ijson, yajl2_c): the library raises `IncompleteJSONError` for every
  error, and says "premature EOF" only for some cuts — a cut inside `tru`, an
  escape, a `\u` escape or a multi-byte character is a "lexical error". The
  reader's handle remembers having been read to its end
  (`cut_short.EndTracking`).
- *XML* (`cut_short.xml_cut_short`): libxml2 (lxml `iterparse`: nmap, masscan,
  OpenVAS) reports a cut with dozens of different codes ("AttValue: '
  expected", "Couldn't find end of Start Tag", "Premature end of data in tag
  …"), and it holds unfinished text back, so some INVALID content is also
  reported only at the end of input (a bare `&` near the end of a file).
  "Failed after the last read" is therefore necessary and not sufficient: the
  file is fed to expat again, which fails on an invalid byte at the feed that
  holds it and says "no element found" / "unclosed token" / "partial
  character" only when told the input is over. Cut short = both. A document
  that declares entities is never called cut short (it is not expanded to
  find out). `tests/test_cut_short_detection.py` cuts a document at every
  byte and runs the invalid shapes through both readers.
- *JSON lines*: only a file's LAST line can be cut. It is, when it does not
  decode, no line ending follows it, and it is the beginning of a JSON value
  by the JSON test above (`streaming_json.is_cut_last_line`). A line that does
  not decode anywhere else — or a last line that is simply not JSON, or one a
  line ending follows — is invalid content: skipped and counted, the import
  completes partial ("1 line of the file were not valid JSON and were
  skipped"), as before and without asking. A JSON-lines file that stops
  BETWEEN two lines is a complete file of fewer lines.

**An invalid file never gets the kind, is never offered the action, and keeps
nothing** — with the instruction forced through the API it fails again. A file
cut **inside its first record** has nothing to keep: it fails as an ordinary
`parsing_error` whose reason says so ("… was cut short before its first host
was complete: there is nothing in it to import"), and nothing is offered.

**Formats in which a cut is detected** — all of them fail-and-ask, and keep
the complete records when asked (`CUT_IS_DETECTABLE` in
`tests/test_every_format_at_a_cut.py`):

| Format | How a cut is seen | What is complete before it |
|---|---|---|
| Nmap XML | The XML rule. An interrupted run has no `</nmaprun>` (and no `<runstats>`). | Every closed `<host>`; the host element the cut fell in is dropped whole. Unit: hosts. |
| Masscan XML | The XML rule. | Every closed `<host>` (masscan writes one per port). |
| Nessus (.nessus) | The XML rule (expat is its reader). | Every closed `<ReportHost>`; the one the cut fell in is dropped whole. Unit: hosts. No port history is written for Nessus, as before. |
| OpenVAS / Greenbone XML | The XML rule. | Every closed `<result>` (the report's per-host blocks, which follow the results, are then usually missing). |
| amass / subfinder JSON, directory brute-force JSON, dnsx, EyeWitness JSON, httpx, naabu JSON, Nikto JSON, Nuclei, SMBMap JSON, testssl, WhatWeb | The shared reader (`streaming_json.iter_json_records`): the JSON rule for a document, the JSON-lines rule for one object per line. | Every closed record of the array; every complete line. A single Nikto host object keeps the complete items of its `vulnerabilities` array, with the object's target (written before the array). |
| Masscan JSON | Its own record-by-record reader: the text left after the last complete record is the beginning of a JSON value, or the file opened `[` and never closed it. The old closing line `{finished: 1}` is a complete file. | Every complete record. |
| BloodHound JSON | Its loader asks the shared rule (`streaming_json.iter_document`) at any size. | Every closed object of the array. |
| NetExec JSON | When the object does not load, the shared rule over its top-level members. | Every complete member (a host, or a share of the one host a spider_plus file is about). A file whose hosts sit under ONE wrapping key holds no complete member. |
| RDAP JSON / NDJSON | An array of responses: the JSON rule. NDJSON: the JSON-lines rule. A single response cut short holds nothing complete. | Every complete response; still written in ONE transaction. NDJSON is streamed into the writes (review 2026-10-23), so a cut last line is found after the rows before it were written — uncommitted, and rolled back with the rest when the import fails. |

**Formats in which a cut cannot be told** — read exactly as before, nothing is
asked (`A_CUT_CANNOT_BE_TOLD` in the same test, with these reasons):

| Format | Why |
|---|---|
| Nmap grepable (`.gnmap`, `.txt`) | One host per line: a file cut between lines is a shorter valid file, a cut last line a line with fewer ports. nmap ends the file with a `# Nmap done` comment, but `masscan -oG` and a grep of the file do not, so its absence does not say the file was cut. |
| Masscan list (`-oL`) | One `open tcp PORT IP EPOCH` line per port; a cut last line is a shorter line (counted as skipped when it has under four fields). Nothing closes the file. |
| Naabu text, RustScan text | One `host:port` / `Open ip:port` line per result: a cut last line is another valid, shorter line. |
| Nikto text, directory brute-force text, hostname list, NetExec console text, SMBMap text | Line by line; a cut last line is a line that matches no pattern, or a shorter valid one. |
| CSV (Nikto, EyeWitness, directory brute-force, DNS records) | Row by row; a cut last row is a short row (skipped or read with empty cells, per parser). |
| Masscan JSON with no opening `[` (before 1.0.4), stopped between two records | Indistinguishable from a complete file when its closing `{finished: 1}` line is simply absent. (Stopped INSIDE a record it is detected.) |
| EyeWitness ZIP | A zip's directory is at its end: a cut archive cannot be opened at all, so it fails like any unreadable archive and keeps nothing. |

**Invalid content is not this rule's, and each format does with it what it
always did.** The rule is only about a file that ENDS ABRUPTLY (owner,
2026-10-11); a file that is invalid part-way is never called cut short, never
offered the action, and the instruction changes nothing about it. Three
behaviours, pinned per format by the same test:

- *Keeps what precedes it, partial* (`INVALID_CONTENT_KEEPS_WHAT_PRECEDES`):
  **Nmap XML** — any reader error keeps the hosts read before it, with
  "Incomplete XML — parsing stopped early, so hosts after this point are
  MISSING, not down. … Re-upload a complete scan file." This is the path of a
  COMPLETE nmap file that holds a character XML does not allow: nmap writes a
  control character from a banner as a reference such as `&#x1b;`, the reader
  stops there, and the hosts before it are imported
  (`test_nmap_truncated_xml_quality.py` pins it equal to before). **Masscan
  XML** — the same, with "The XML ended early (near line N); …". (Both
  sentences speak of an incomplete file although the cause here is invalid
  content: the wording is unchanged for now.) **Masscan JSON** — the records
  before text that is not a record are imported, as before; the one change is
  that the import is now marked partial with "After N records the file holds
  text that is not a masscan record; it, and anything after it, could not be
  read and is not in the inventory." — it used to drop them with a log line
  and look complete. A record the strict JSON decoder refuses is such text,
  e.g. one whose banner holds a RAW control character (masscan writes them
  escaped, `\u0001`, which is read).
- *Skips the line and counts it*: JSON lines (above).
- *Fails and keeps nothing*: every other format — Nessus, OpenVAS, BloodHound,
  RDAP documents, and a streamed JSON document of the shared reader. NetExec
  JSON that does not load falls back to console lines, as before. A small JSON
  document that is invalid for a reason other than its end is still handed to
  the line reader, as it always was.

### The data model parsers write to

| Model | Owner table | Written by |
| --- | --- | --- |
| `Scan` | `scans` | every parser (the container; carries `tool_name`, `scan_type`, `command_line`, `version`, `start_time`/`end_time`) |
| `Host` | `hosts_v2` | every host-bearing parser (deduped: one row per `(project_id, ip_address)`). `HostDeduplicationService.find_or_create_host` refuses a value that is not an IP address (`ValueError`; the record is skipped) and stores the address in one spelling (`parser_utils.normalize_ip`), still finding a row stored earlier in another spelling — an IPv6 address written two ways is one host (backlog 2026-10-22, S5) |
| `Port` | `ports_v2` | port scanners + web/dir/vuln parsers |
| `HostScanHistory` / `PortScanHistory` | `host_scan_history` / `port_scan_history` | per-scan observation audit trail |
| `Vulnerability` | `vulnerabilities` | **Nessus, OpenVAS, Nikto, Nuclei, nmap** (NSE vuln scripts, vulners, catalog checks), **testssl** (rated checks), **NetExec**, **SMBMap** and **dnsx** (catalog checks) — the "scanner observations" |
| `WebInterface` | `web_interfaces` | **httpx, whatweb, eyewitness, testssl, nmap** (TLS scripts on web ports) — unified web view, keyed by `source` |
| `WebPath` | `web_paths` | **dirbuster family** — one row per discovered path (url, path, status, size, tool) |
| `DNSRecord` | `dns_records` | The general name-evidence table: one immutable observation about a name per scan. Written by **dnsx, dns CSV, amass, httpx**, the HTTP observations of the name tested by **nikto, nuclei, testssl, dirbuster** (`dns_name_service.bind_hostname`), and by EVERY host parser through `host_deduplication_service` (a scanner-reported hostname becomes a `SCANNER` observation). `record_type` is a real RR type or one of the `IMPORT`/`DISCOVERED`/`SCANNER`/`HTTP`/`CERT`/`TESTED` kinds (`TESTED` is not a parser's: evidence with a tested outcome and an `observed_ip`, recorded for a test aimed at the name, writes it). Columns are `domain` + `value` (*not* `hostname`/`ip_address`), plus `name_id` → `dns_names`. Single write path: `dns_name_service.record_observation`. |
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
| `reason` | ✓ | — | ✓ (XML / JSON; the list format has none) | — | — |
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
  dropped. Sets `Scan.version`, `command_line` (`@args`), `start_time`. Each
  `<scaninfo>` element is one `ScanInfo` row (type, protocol, port list), so a
  TCP + UDP run keeps both lists; `GET /scans/{id}` returns them as
  `scan_info`, the scan page prints them as its "Scanned:" line, and an agent
  reads them on each `assist_list_scans` row.
- **gnmap** parses grepable `Host:`/`Ports:`/`Status:` lines; incrementally
  fills `Scan` metadata (tool, version, command line, start/end) from header
  comments; detects masscan-vs-nmap origin.
- **masscan** writes through the dedup service's **set-based form**
  (`HostDeduplicationService.upsert_hosts_bulk` / `upsert_ports_bulk`: `ON
  CONFLICT` upserts, a few statements per 500 hosts) for throughput on
  tens-of-thousands-of-host scans — the same rules as `find_or_create_host` /
  `find_or_create_port`, held to them by
  `tests/test_review_2026_10_23b_ingestion.py` (review 2026-10-23b, ING-7: it
  was a second copy in the parser and had drifted). Accepts `.xml` / `.json` /
  list. Every address goes through `HostDeduplicationService.host_address`: an
  IPv6 address written two ways is one host, a row stored in another spelling
  is still found, and a record whose address is not an IP address is counted
  as skipped and marks the import partial (ING-1: it was inserted as written,
  so one bad token failed the whole import). A host held in another state
  than `up` records the conflict, as the dedup service does. Service name
  (greppable only) follows `should_replace_service` (empty-or-longer-wins
  over an unconfident name; the whole service block moves with it), within
  a file too. `Port.reason` (`syn-ack`, from XML / JSON) follows the dedup
  service's rule: a scan that gives none keeps the stored reason while the
  state is unchanged and clears one that described another state; a
  `--banners` record's `reason="response"` is the banner arriving, not the
  port's reason. Banners (`masscan-<kind>` script rows) are written after
  each flush of the collected ports, a heartbeat per 1,000 addresses; a
  banner whose port is not stored yet waits for the next flush, and one
  whose port never opens is dropped at the end (review 2026-10-22, A-R6:
  they were held for the whole file and written in one loop at its end).
  **Fails closed** (raises if 0 hosts). Masscan writes nmap's XML dialect
  (`<nmaprun scanner="masscan">`), so an XML root that names ANOTHER scanner is
  refused — an nmap file can never be imported as masscan (open ports only) —
  while a root naming none is still read. List lines that are not records
  (fewer than four fields, a non-numeric port, an address that is not one)
  and JSON / XML records whose address is not one are counted as skipped and
  the import is marked partial.
- **naabu** / **rustscan** funnel through the shared `persist_host_observation`
  helper. naabu accepts `.json`/`.jsonl`/text and **fails closed**; rustscan is
  text-only and also **fails closed** — and warns when the file holds an nmap
  report it does not read. RustScan's IPv6 lines are read (`Open
  [2001:db8::1]:443`, `2001:db8::1 -> [443]`; the candidate is validated as an
  address first).

### Vulnerability / AD / credential scanners

**Nessus, OpenVAS, Nikto, Nuclei, nmap, testssl, NetExec, SMBMap and dnsx (an
allowed zone transfer) create `Vulnerability` rows** (the *scanner observations*). Vuln rows go through
`upsert_vulnerability` (app-level dedup on `(host_id, source, plugin_id|title,
port_id)`; `db.flush()` after add); a weakness from the misconfiguration catalog
goes through `record_misconfig` (`app/services/misconfig_checks.py`), which
stamps its `check_id` so the same weakness reported by different tools is one
issue.

**Nessus** (`.nessus`/`.xml`; streamed via defusedxml; persistence done by
`NessusIntegrationService` + `VulnerabilityService`, committed in batches):
- **An import that does not finish keeps nothing:** a cancel, the timeout or a
  worker restart stops it at the next batch (a restart re-queues the job), and
  a truncated export or one with no processable host fails the job. In each
  case the batches already committed are removed with the scan. A report item
  the parser cannot read, a host that could not be written and a finding that
  could not be written are each counted as skipped and named in the import's
  warnings, and mark it partial — so it is listed under Needs attention like
  any partial import (review 2026-10-23b, ING-2: only the warning text said
  so).
- **Host:** `ip_address`, `hostname` (`host-fqdn`→`netbios-name`),
  `os_name` (`operating-system`), `state=up`. Also writes `HostAttribute` rows
  (hostname / netbios_name / os_name with per-field confidence).
- **Credentialed or not:** whether the scan authenticated to the host is
  stored on the host's row in the scan, `host_scan_history.credentialed`
  (true / false; NULL when the file does not say, and for every other tool).
  The host's `Credentialed_Scan` tag answers it; a file without the tag is read
  from plugin 19506's `Credentialed checks : yes|no` line
  (`nessus_parser.credentialed_status`), also when informational items are
  skipped. The import's message counts both. The scan page's "As scanned"
  hosts table shows it per host (Credentialed / Not credentialed, blank when
  the scan did not say; the column appears only when some listed host has a
  value), and an agent reads it with `assist_list_scan_hosts`. It does not
  change what Evidence counts as assessed; it is shown beside it. The host
  inspector's Vulnerabilities assessment reads "credentialed", "not
  credentialed" or "credentials not stated" (`assessment.vuln_scan_credentialed`:
  `yes` if any vulnerability-scanner scan of the host authenticated, `no` if
  one says it did not and none did, `not_stated` otherwise), the Evidence page
  counts the assessed hosts in each state, and the Hosts query
  `vulnscan:credentialed|uncredentialed|unstated` lists them. One definition:
  `evidence_service.vuln_scan_credentialed_condition`.
- **Port:** created only when the finding's port ≠ 0, and keyed by **`(port, protocol)`** —
  UDP and SCTP findings keep their protocol (v2.365.0). Before that every Nessus
  finding was attached to a TCP port, so an SNMP/NTP/IKE/DNS-over-UDP finding
  created or joined a phantom open TCP port; scans imported earlier need a
  re-import to correct (the vulnerability row never stored the protocol).
- **Informational (severity 0) rows** can be skipped at ingest — a value sent
  with the upload request wins, then the project's setting, then the deployment
  default (`resolve_skip_informational`). The upload dialog has no switch of its
  own: it states the project's setting and links to Project settings, where it
  is changed.
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
- **Host:** `ip_address` (`state=up`); `hostname` from the result's
  `<host>…<hostname>` and from the report-level `<host><ip>…<detail>` block's
  `hostname` detail; `os_name` from that block's `best_os_txt` detail (fills a
  host that has no OS; a host the report lists without a result is still
  created). **Port:** `port_number`/`protocol`.
- **Vulnerability columns written:** `title` (`name`), `severity`,
  `plugin_id` (NVT `oid`), `description` (from the NVT tags — summary,
  insight, impact, affected — falling back to the result's description),
  **`cvss_score`** (← `<severity>` / `cvss_base`), **`cvss_vector`** (the
  NVT's `<severities>` block, a v3 entry before a v2 one, else the
  `cvss_base_vector=` tag), `cve_id` (first CVE),
  `solution`, `references` (URLs, CERT-Bund/DFN-CERT ids, further CVEs),
  `plugin_output` (the detection result and QoD), `source=OPENVAS`. Severity
  from CVSS numeric, falling back to `<threat>` text.
- **`exploitable`** is set only when the stored vector's exploit-maturity
  metric says exploit code exists (`E:P` / `E:F` / `E:H`, 2.0 `E:POC`, 4.0
  `E:A` — `openvas_parser.vector_states_exploit`). A GVM report has no other
  statement that an exploit exists: nothing is inferred from severity, and
  EPSS is not read as one. Most feed vectors are base-only, so most OpenVAS
  rows stay not-exploitable.
- None of these element names has been checked against a real GVM export (the
  suite has none); both vector shapes are accepted for that reason.

**NetExec (NXC)** (`.json` or console text; auto-detected; read with
`read_tool_text`, so a UTF-16 capture from PowerShell's `>` and NUL bytes are
handled, v2.420.0; read WHOLE — its JSON is one object and console lines are
grouped by host across the file — so a file over 100 MB, `NETEXEC_MAX_BYTES`,
is refused before it is read, terminally, asking for the capture to be split;
review 2026-10-23): host (`hostname`, `os_name`, `domain`, `smb_signing`) +
port (`445`/SMB etc.) + a `NetexecResult` row (`auth_success`, `username`,
`domain_name`, `shares`, `local_admin`, `smbv1`, `writable_share`, truncated
`raw_output`) + per-field `HostConfidence`/`PortConfidence` and
`ConflictHistory` rows. **Catalog observations** through `record_misconfig`:
SMB signing not required, SMBv1 enabled, SMB null session, VNC without
authentication, anonymous FTP, and (v2.430.0) NFS root escape and NFS export
open to any host. Share tables are read per (address, port): SMB's
`Share / Permissions / Remark` and NFS's `UID / Perms / Storage Usage /
Share / Access List` (each export's r/w/x, storage use and the hosts it may
be mounted from; a `w` sets `writable_share`). **An NFS line's port is the
mount daemon's, not NFS's** — nxc connects to the portmapper and logs with
mountd's dynamic port (20048, 32767, …; `nxc/protocols/nfs.py`
`create_conn_obj`) — so it is recorded as service `mountd` (2049 stays
`nfs`), and NFS observations go on 2049/tcp when the host has it recorded
open, else on that mountd port (v2.430.1). Credentials parsed from
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
| Formats | `.json`/`.jsonl`/`.ndjson` | `.csv` | `.json`/`.jsonl`/`.ndjson`/`.txt` |
| `domain` | host or PTR name | `name` column | hostname (`name`/`host`/`domain`) |
| `record_type` | A/AAAA/CNAME/MX/NS/TXT/SOA/SRV/CAA/PTR, any other type listed in `all`, and `AXFR` (the transfer itself) | from `type` column (an unknown type is rejected) | A/AAAA (`DISCOVERED` for unresolved names) |
| `value` | the answer / IP | an IP for A/AAAA/PTR, the record data otherwise | resolved IP |
| `ttl` | int rows only | ✓ (when numeric) | — |
| `resolver_name` | ✓ (**only parser that sets it**) | — | — |
| Feeds `Host.hostname` | PTR (overwrite) + A/AAAA (no overwrite) | PTR rows only | always (needs resolved IP) |
| Notes an existing host as addressed | PTR + A/AAAA | PTR + A/AAAA | always (via the dedup service) |
| Creates a host for an unknown address | PTR + A/AAAA | PTR rows only | always (needs resolved IP) |

- **dnsx** is the resolver-attribution path: it counts NXDOMAIN/SERVFAIL/REFUSED
  failures and per-resolver hits into `parser_warnings`, and **fails closed**.
  PTR answers authoritatively set `Host.hostname`; forward A/AAAA answers create
  assets but never clobber an existing hostname.
- **dnsx and the dns CSV record every host an answer addresses** (a
  `host_scan_history` row per host and scan — what the Scans row's "hosts
  added / already known", the scan page's host table, the Hosts filter by scan
  and the scan deletes all read). A host the import CREATES gets its row from
  the dedup service (`find_or_create_host`, `host_created`, state `unknown`);
  a host that ALREADY existed — PTR or forward, dnsx's transferred zones
  included, and for the CSV its PTR rows and (since 2026-10-11, owner
  decision) its A / AAAA rows whose value is that host's address — is noted
  in a `ScanHostObservations` and
  written by `parser_utils.record_addressed_hosts` (the web parsers'
  `record_hosts_in_scan`, with the flushes two writers of one row need),
  ahead of each heartbeat and after the last record: `host_created` false,
  `hostname_at_scan` = the name the file gave, **no** `state_at_scan` (a DNS
  answer does not say the host is up), and nothing else about the host is
  written — it is noted whether or not its display name changed. A record
  rolled back in its savepoint leaves no note. Before this, only created hosts
  had a row, so an import that named 5,000 known hosts showed "no hosts" and
  had no Hosts list. Imports made before it are not back-filled. The job's
  result line counts the three apart (`parser_utils.dns_import_summary`):
  "N DNS records, A hosts addressed, C created, M display names changed" — the
  last is existing hosts whose display name the import replaced or filled. It
  is counted from the notes' own `ScanHostObservations.renamed_host_ids`, the
  set that sets `host_scan_history.hostname_changed` (next bullet), so the
  sentence, the Scans row's "M renamed" and the Hosts list it opens cannot
  disagree. `host_change_service` ("Changed" on the Hosts list, the
  queue's "changed at its latest scan") compares the two latest history rows
  that STATE a liveness, so a row with none is not a state flip.
- **The dns CSV's forward rows and dnsx still differ in two things** (not
  decided — the owner agreed only to the note): for an A / AAAA answer dnsx
  also offers the name as the WEAKEST display-name candidate (`forward`: it
  fills an empty `Host.hostname`, never replaces one) and CREATES the host
  when the address is unknown; the CSV's A / AAAA row does neither — it stores
  the record, and notes the host only when the address is already one.
- **Every import records the hosts whose display name it changed**
  (`host_scan_history.hostname_changed`, revision `d7b3e9a1c5f2`): true on the
  scan's row for a host that ALREADY existed when the scan filled its empty
  `Host.hostname` or replaced it under the one precedence rule
  (`dns_name_service.apply_hostname_candidate`). It is set where the name is
  changed and written by the two writers of the history row, no third: the
  dedup service (`_update_and_record_existing_host` compares the name across
  the update — nmap, gnmap, Nessus, OpenVAS, nikto, nuclei, NetExec, smbmap,
  BloodHound, amass, naabu, rustscan, dirbuster; `note_hostname_changed` for
  NetExec's one direct fill from a later line of the same host) and
  `record_hosts_in_scan` from `ScanHostObservations.note(..., renamed=…)`
  (httpx, WhatWeb, testssl through `resolve_host_cached`'s `renamed`; dnsx and
  the dns CSV from the rule's own answer). masscan and EyeWitness write no
  display name. Never true on the row of the scan that CREATED the host (a
  host created with a name, or created and named later in the same file, is
  "added", not "renamed"), never turned back to false by a later record of
  the same scan, never written by an operator's edit. Which name wins and
  when is unchanged — this only records that a change happened. Read through
  ONE condition, `host_query_predicates.renamed_in_scan_condition`
  (`hostname_changed AND NOT host_created`): a scan's and a batch's
  `renamed_hosts`, the Hosts filter `scan_ids=…&renamed_in_scan=true`, and
  `hostname_changed` on the scan page's rows. Imports made before the revision
  read false (not back-filled). `tests/test_scan_renamed_hosts.py` holds every
  format to "a known unnamed host carries a name afterwards if and only if
  the row says so".
- **dnsx `all` and `-axfr`** are read in dnsx's own shapes (retryabledns
  `DNSData` / `AXFRData`). `all` is an array of resource records as text,
  `owner<TAB>ttl<TAB>IN<TAB>TYPE<TAB>rdata` (`parse_rr_text`); on an ordinary
  row it repeats the typed arrays, so only the types with no array of their
  own (HINFO, DNSKEY, TLSA…) are stored from it. `axfr` is an object,
  `{"host": zone, "chain": [...]}`, one chain entry per name server that
  handed the zone over: every record is stored under ITS owner name and type
  from the entry's `all` (the entry's typed arrays have no owner names), A/AAAA
  records discover hosts, PTR records of a reverse zone name them, and the zone
  gets one `AXFR` observation per server ("zone transfer allowed (N records)",
  resolver = that server) plus a line in the import's warnings. An entry with
  no `all` keeps its values on the zone as `AXFR` rows rather than inventing
  owners. Arrays of strings under `axfr` / `any` (not dnsx's shapes) are still
  stored as before. **The transfer is a weakness of the name server:** the
  catalog check `dns_zone_transfer_allowed` ("DNS zone transfer allowed",
  medium, source `dnsx`) is recorded through `record_misconfig` on the host
  whose address is the chain entry's resolver, on the resolver's port (53/tcp)
  when the host has that port row, else on the host, with the zone and record
  count as its output (`_record_zone_transfer_check`). Only when that address
  is already a host of the project — known before, or discovered by the same
  import (usually the zone's own A record for the server): a resolver is where
  the operator pointed dnsx and is never made a host. Without a host the fact
  stays on the zone's `AXFR` row and in the import's warnings. It is written
  inside the row's savepoint. There is no backfill for earlier imports
  (`backfill_misconfigs.py`): the `AXFR` rows that would feed it have only been
  written since the same release.
- **dns CSV** expects `record_type` + `name` + `address` columns (with aliases);
  gated by a header heuristic so an arbitrary CSV doesn't become a silent
  zero-record DNS scan. Rows are stored by record type (v2.416.0): a type that
  is not a real RR type, or an A/AAAA/PTR row whose value is not an IP of that
  family, is rejected and counted. The file is read with `read_tool_text`
  (UTF-16 and a UTF-8 byte-order mark accepted, NUL removed), and each row is
  its own savepoint: a row the database refuses is one rejected row, not the
  end of the import.
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
  before it can fail — the dispatcher deletes a failed import's scan, so a
  row the scan re-observed keeps its `scan_id` and gains a sighting instead
  (see "What a failed import leaves" below).
- **Record isolation:** a parser that skips a failed record and carries on
  writes it with `write_record(db, write, observed=…, on_rollback=…)` — the
  ONE per-record isolation (review 2026-10-22, B1-1; it had been written out
  by hand in eight parsers and the copies had drifted). It runs `write`
  inside `record_savepoint` and answers a `RecordOutcome(value, error)`:
  without the savepoint the failed flush poisons the session and the whole
  import fails after logging "skipped" (v2.419.0). The helper rolls the
  record back, restores the scan's host notes (`observed`), calls
  `on_rollback` (pass `dedup_service.discard_rolled_back_state`, or a reset
  that calls it, for a parser that writes through the dedup service), and
  survives a savepoint an inner frame already rolled back. A cancel, a
  shutdown or a stall (`ParseFailure`) inside a record is RAISED — never a
  skipped record — in every parser. `retry_races=True` writes the record once
  more after an `IntegrityError` or a deadlock (40P01), for a `write` that
  may run twice (nmap; a lost race with another import of the project).
  Since 2.515.0 NetExec (per host), Nikto (per finding), RustScan, SMBMap and
  BloodHound (per host) do too: one record the database refuses is skipped
  and counted, the import completes partial, and only a file whose every
  record failed fails.
- **Name your scan:** create the `Scan` with `ensure_scan`, or call
  `announce_scan(db, scan)` right after flushing a row you built yourself —
  once, OUTSIDE any record savepoint. Under a job this stamps
  `ingestion_jobs.in_progress_scan_id` and commits it with the scan row, which
  is how a failed, cancelled or killed import's partial scan is found and
  deleted (review 2026-10-01). That column is the only record of an attempt's
  scan — nothing reads an attribute of the parser for it (the Nessus service,
  which creates its scan itself, calls `ingestion_service.note_scan_created`).
- **Heartbeat:** a record loop calls `ProgressBeat(...).tick()` once per
  record, OUTSIDE the record's savepoint (nmap / gnmap / masscan / NetExec /
  Nessus call `report_progress` themselves). The heartbeat keeps the job's
  lease, is where a cancel, the timeout and a worker shutdown stop the parse
  (it raises `ParseFailure` — never catch it as a bad record), and it COMMITS.
  ORM rows you hold stay usable (they are expired and reload); a parser that
  writes its host history at the end (`record_hosts_in_scan`) passes it as
  `before=` so each commit also records which hosts the scan created. Each
  call writes only the hosts noted or changed since the previous one, and
  updates a row it already wrote with what the file has said since (state,
  hostname) — so calling it before every heartbeat costs what changed, not
  the whole file. A heartbeat that finds the job is no longer this attempt's
  (cancelled from the API, or re-claimed) rolls the pending batch back and
  raises; it never commits it. The lease write is one UPDATE conditioned on
  the attempt's claim, with no read of the job row, and a cancel is decided
  from the job row alone.
  A parser that COLLECTS the file before it writes wraps the read loop in
  `beat_while_reading(items)` (every 20,000 by default): the read phase holds
  nothing half-built in the session, so its commit is safe and a cancel there
  leaves nothing. Do not heartbeat between writes of rows the cleanup cannot
  find — rdap's attribution rows belong to the project's address block, not to
  the scan, so it heartbeats while reading, once more before writing, and
  then writes in one transaction.
- **What a failed import leaves:** no scan, and nothing that only it
  created. The dispatcher (`delete_partial_scan`) deletes the scan and, with
  it, the hosts and ports ONLY that attempt created (their history says
  `host_created` / `port_created` for this scan and no other scan saw them),
  the scanner observations, scripts, host scripts and host attributes that
  no other scan reported, its web rows, name observations (`DNSRecord`), the
  names (`DNSName`) only it observed that nothing else refers to, its
  history, and the
  EyeWitness screenshots it extracted (`uploads/web_screenshots/<scan id>/`
  and the `report-<scan id>.json|csv` beside it, removed after the delete
  commits). It never deletes a host someone has worked on (`host_work.work_kinds`,
  the one list: a note, a review, a tag, a finding endpoint, a test, an
  evidence record, a remediation entry, a proposal about one of its
  observations, an operator's name for it) or that
  anything from another scan or source hangs on; when in doubt the host
  stays.
  **Names.** `dns_name_service.delete_scan_observations` — the step a scan
  delete by hand runs too — removes the scan's observations and then a
  `DNSName` whose every observation was that scan's, unless something else
  holds it: a person added it (`created_by_id`), a host test, a finding
  endpoint or a scanner observation names it, another scan's web interface
  does, or a scope domain covers it (exactly, or as a descendant). A name
  with an observation from another scan, an import or a test is not touched.
  **What it does not undo:** changes the attempt made to hosts and ports that
  ALREADY existed (state, service, OS, hostname, `last_seen`, a filled-in
  field — the previous value is not kept anywhere to restore) and the
  confidence conflict history
  (`conflict_history`, whose scan pointers become NULL). A retry of the same file
  writes the same values again; a failed import that is never retried leaves
  those updates in place.
  **A cleanup can run long after the attempt died** (a killed worker's job is
  reaped or re-claimed after the stale window; another import of the same
  hosts may have completed in between). So a row the dead scan first recorded
  that ANOTHER scan also reported is kept, not deleted. Which scans reported
  a row is recorded, not guessed: a script, host script, host attribute or
  vulnerability is one row per thing, overwritten by each scan that reports
  it, and every such report writes a **sighting** — one row per (thing, scan)
  in `script_sightings`, `host_script_sightings`, `host_attribute_sightings`
  or `vulnerability_sightings` (`scan_sightings.see` / `see_many`, called by
  the writer in the same savepoint as the row, on the insert and on every
  re-observation; Nessus writes one statement per batch of 100 findings).
  The row's own `scan_id` stays "first recorded by" (nullable, `ON DELETE
  SET NULL` — never a cascade).
  **`scan_sightings.release_scan` is the one step before any scan row is
  deleted** — by this cleanup and by `DELETE /scans/{id}`: scripts, host
  scripts and host attributes only that scan reported are deleted; a row
  another scan also reported is handed to the EARLIEST other scan that
  reported it (a vulnerability's `last_seen_scan_id` to the latest); a
  vulnerability no other scan reported is deleted — by both paths — unless a
  finding or a proposal refers to it, in which case it is kept without a
  scan; a confidence row (the current winner, not an observation) goes to
  a later scan that observed its host or port; the host or port is then
  "introduced" by the first scan left that saw it; and a host or port whose
  "last updated by" names the scan names the newest other scan with history
  on it, or nothing (one correlated UPDATE per table, over the rows naming
  the scan, the same on both paths; the hand delete used to do it alone, over
  every project's history). A port or host a later
  scan updated (`last_updated_scan_id`) or re-observed a vulnerability on is
  not the attempt's alone either. A new table that merges a thing with the
  scan that saw it gets a sighting table and a line in `release_scan`, never
  a cascading `scan_id`. Rows that existed before sightings (revision
  `c2f9d7a4e1b3`) were given one for the scan they name, one for a
  vulnerability's `last_seen_scan_id`, and — scripts, host scripts,
  attributes — one for the scan the old rule would have moved them to (the
  newest other scan with history on their port or host that is later, or any
  such scan when the row was touched again), so they survive the deletes
  they survived before.
  **One cleanup at a time per project, and never beside an open batch:** every
  transaction of an import holds the project's import lock in shared mode
  (`project_import_lock`, a PostgreSQL advisory lock taken when the
  transaction begins — imports do not wait for each other); the cleanup takes
  it exclusively, so with several workers it cannot delete a host a
  concurrent import is re-observing. A cleanup that fails leaves the scan
  named on the job row: the job is not parsed again until it is gone (a claim
  that cannot remove it fails the job; a parser failure whose scan cannot be
  removed ends the fallback chain), and the reaper retries the cleanup of
  failed jobs once their attempt is over — its worker no longer holds the
  attempt's liveness lock, or its lease is past the stale window — which is
  also how a job cancelled while its worker was dead, or while it waited
  with a dead attempt's scan on it, is cleaned: the cancel request itself
  removes nothing (it is the job row only; the sweep runs on the worker's
  next poll).
  **Ports carry the scan that created them.** Every code path that INSERTS a
  port stamps `ports_v2.created_scan_id` (`HostDeduplicationService._create_new_port`,
  the set-based `upsert_ports_bulk` masscan writes through — its `DO UPDATE`
  never touches the stamp —, and
  `VulnerabilityService._get_or_create_port` for Nessus); **a new insert site
  must do the same**, or a failed import leaves its ports behind. The cleanup
  deletes the ports stamped with the attempt's scan — and, beside them, those
  `port_scan_history` says it created — under the same guards (no other scan
  saw it, nothing refers to it). For an import path that writes no
  `PortScanHistory` (Nessus — and it must not start to: that would change the
  port counts on the Scans page, the dashboard and the scan diff) the stamp is
  the only record. It is read by the two scan deletes and the hand delete's
  preview, never by a page, a list or a count, and a NULL
  stamp (every port from before v2.465.0) is never a scan's own. Deleting
  a scan by hand from the Scans page — finished or not — runs the same port
  step in its transaction (`delete_ports_scan_created`, the one function both
  paths call, same guards): the ports that scan created on hosts that stay,
  that no other scan saw and nothing refers to, go with it. A port that
  carries a scanner observation kept because a finding or a proposal refers
  to it stays, on both paths: a kept observation keeps its port. The delete clears
  the stamp (`ON DELETE SET NULL`), so it is the only moment they can be
  found.
  **A scan deleted by hand takes everything it brought** (owner decision: a
  scan imported into the wrong project, or an invalid one, must not stay in
  the project's data): the hosts only it brought — under the cleanup's
  provenance guards, so a host another scan or source attached anything to is
  kept —, and on the hosts that stay its ports (above) and the scripts, host
  attributes and scanner observations only it reported (an observation a
  finding or a proposal refers to is kept, without a scan), and its name
  observations with the names only it observed (**Names**, above). Unlike the
  cleanup it also removes a host with work on it, so it asks first: the
  preview (`GET /scans/{id}/deletion-impact`) lists those hosts and the
  delete answers 409 `hosts_with_work` until the request says
  `confirm_hosts_with_work=true`. It is refused while
  an import of the project is running (409 `import_running`) and holds the
  project's cleanup lock otherwise. What no delete can undo: a value the scan
  OVERWROTE on a host, port, script or observation that already existed (OS,
  host name, service, script output, severity) — no previous value is kept.
- **A re-observed vulnerability is refreshed in one place:**
  `parser_utils.refresh_reobserved_vulnerability`, called by
  `upsert_vulnerability` and by the Nessus path. Severity is always taken from
  the newer observation; title, description, solution and references when the
  newer value is not empty. `scan_id` ("first recorded by") never moves; the
  caller records the scan's sighting of the row.
- **Fail closed:** raise `ValueError` when the file yields **zero** records, so
  a misrouted or malformed file surfaces a parse error instead of a silent
  empty scan. (Most parsers do this; it's the expected convention.)
- **Set `scan.tool_name`** — it drives display *and* the
  `tool_name_hint` mismatch warning for agent uploads.
- **Optional quality stats:** set `self.last_parse_stats = {"skipped": N,
  "warnings": "...", "summary": "...", "partial": bool, "uninterpreted": [...]}`.
  These land on the `IngestionJob` as `skipped_count` / `parser_warnings` /
  `partial` / `uninterpreted_lines` (visible on the upload's row of Inventory → Scans, the import history).
  `uninterpreted` is the lines you dropped or kept only as text, as REDACTED
  shapes from `app/services/line_shapes.py` (`ShapeTally`) — never a raw line,
  a credential or a cell value (v2.418.0; NetExec is the reference).
  *(Note: several older parsers only log skips locally and don't set this —
  prefer setting it in new parsers.)*

### Reuse the shared toolkit (don't reinvent)

`app/parsers/parser_utils.py`:
- `ensure_scan(db, *, filename, tool_name, scan_type, command_line=None,
  project_id=None)` — create the `Scan` row (all args after `db` are
  keyword-only; `filename`, `tool_name`, `scan_type` are required) and name it
  on the ingestion job (`announce_scan`).
- `persist_host_observation(*, dedup_service, scan_id, ip_address,
  hostname=None, state="up", ports=[...], host_data=None, project_id=None,
  isolate=False)` — the one-stop "I saw this host with these ports" writer
  (keyword-only; wraps the dedup service you constructed in `__init__`;
  `isolate=True` writes each host as one skippable record through
  `write_record`, so one bad record can't fail the batch).
- `upsert_vulnerability(*, db, host_id, scan_id, source, title, severity, ...)` —
  **keyword-only** (the positional form is a `TypeError`); also takes
  `plugin_id`, `port_id`, `description`, `cvss_score`, `cve_id`, `solution`,
  `references`, `name_id` (the named endpoint a web finding was observed at),
  `exploitable`, `check_id`, `key_on_title` and `plugin_output` — read the
  signature. The way to create a scanner `Vulnerability` (handles app-level
  dedup and the required `db.flush()`); a **catalog weakness** goes through
  `record_misconfig` (`app/services/misconfig_checks.py`) instead.
- `write_record(db, write, *, observed=None, on_rollback=None,
  retry_races=False) -> RecordOutcome` — one input record that may be
  skipped; `record_savepoint(db, observed=None, on_rollback=None)` is the
  savepoint it opens, for a caller that does not skip (see "Record isolation"
  above).
- `read_tool_text(path) -> ToolText` — read a tool's text output with UTF-16
  (PowerShell `>`) decoded and NUL removed; required when text lines reach a
  Text column (PostgreSQL rejects NUL; v2.420.0). `without_nul(handle)` is the
  streaming form, for a parser that reads a large file line by line.
- `announce_scan(db, scan)` / `ProgressBeat(label, every=500, before=None)` —
  name the scan on the job, and heartbeat a record loop (see the contract);
  `beat_while_reading(items, label, every=20000)` is the read-phase form.
- `ScanClock` — accumulates the scan window from timestamps in the tool's
  output and writes it onto the `Scan` (never over a window the parser set from
  an explicit run start/finish).
- `ScanHostObservations` / `record_hosts_in_scan` — the hosts a scan observed,
  for the per-scan history. `record_addressed_hosts` is the same writer for a
  parser whose NEW hosts go through the dedup service (dnsx, the dns CSV): it
  flushes around the call so the two writers find each other's row.
- `map_numeric_severity(score)` / `map_text_severity(text)` — canonical
  severity mapping; reuse these instead of hand-rolling thresholds.
- `extract_first_ip(text)` / `normalize_ip(value)` /
  `parse_host_port_token(token)` — robust IP/host:port extraction.
- `ProjectHostsByAddress(db, project_id)` — the project's hosts by address,
  read once per import as ids (never ORM rows held for the whole file); a
  host is loaded by its key when an answer names it, and one a rolled-back
  record created is found gone. The DNS parsers use it directly;
  `resolve_host_cached(db, hosts, ip, *, hostname=None)` is the web parsers'
  (httpx, WhatWeb, EyeWitness, testssl) find-or-create on it — an address the
  map misses is looked up once (another spelling, `HostDeduplicationService
  .host_address`; or a host another import added since), and a new host is
  stored under `normalize_ip`'s spelling (review 2026-10-22, B1-2).
  `resolve_port_cached` memoizes ports per file.
- `correlate_scan(db, scan_id)` — map newly-seen hosts to scopes/subnets
  (call once at the end).

`app/parsers/streaming_json.py` → `iter_json_records(file_path, *, array_keys=(),
tool_label="JSON")` streams `.json` arrays, single objects, and `.jsonl` /
`.ndjson` uniformly; `array_keys` points it at a wrapped payload such as
`{"results": [...]}` (use this, not
`json.load`, so large files don't OOM the worker). The orchestrator opens a
report around each parser run (`cut_short.begin_read` / `end_read`): JSON
lines the reader cannot decode are tallied there and reported as skipped, and
a file that ends early is noted there and raised as `TruncatedFile` — or, when
the import was asked to keep what a cut-short file holds, simply ends the
records ("A file that was cut short", Part 0). A parser on this reader has
nothing to do for either: do not catch the reader's exception, and do not
read the file a second way. **A parser with a reader of its own** reports a
premature end the same way and decides nothing: `cut_short.settle_cut(
records_read, label=…)` (it raises, or returns and the parser stops reading),
told from invalid content by `cut_short.xml_cut_short` for XML and
`streaming_json.iter_document` / `is_cut_last_line` for JSON — and gets its
row in `tests/test_every_format_at_a_cut.py`, which fails for a format it
does not decide.

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
4. **The dedup service's set-based form** (`upsert_hosts_bulk` /
   `upsert_ports_bulk`) — only if you genuinely have masscan-scale throughput
   needs and your observation carries no more than a state and a service
   name. Never a second copy of the rules in a parser.

### Wire up detection + registration (the detector + ONE registry + the docs)

1. **Detector** — add `looks_like_mytool(sample: bytes, filename: str) -> bool`
   (or `(sample)` alone) to `app/parsers/content_detection.py`. Make the
   signature **specific** (a distinctive key combo or root tag); filename
   substring matching is a cheap tie-breaker but shouldn't be the only signal.
   Watch the ordering caveats noted on `format_registry.DETECTION` and
   `XML_DECISIONS` (e.g. don't match a string that also appears in another
   tool's nested output — the OpenVAS/nmap bug).
2. **Registry** — in `app/services/format_registry.py`, add a
   `_spec("mytool_json", "My Tool JSON", family, module, class_name,
   description, "looks_like_mytool")` row to `FORMATS` (`family` is one of
   `port | vuln | web | dns | auth | other`; a sniffer beside its parser is
   named `"app.parsers.mytool_parser:looks_like_mytool"`; filename tokens that
   recognise it go in `hints`), and put `"mytool_json"` in the `DETECTION`
   order of each extension it arrives with, by specificity relative to the
   neighbours. That is all: routing, the dispatch map
   (`build_parser_dispatch_map()`), the staged review, the "parse this as…"
   chooser, the job's format chain and an operator's override all read it.
   A parser tried WITHOUT having recognised anything is a fallback (`*` after
   its key in `XML_DECISIONS`) — that is what stops the review showing it as
   "recognised by structure".
3. **Tests** — the guards read the registry rather than a list of their own:
   `tests/test_parser_dispatch_contract.py` (detected classes are dispatchable,
   the map is the registry's classes), `tests/test_ingestion_format_chain.py`
   (every key detection names is a format, every format is reachable), the
   content-detection surface test in `tests/test_phase1_regressions.py` (the
   public `looks_like_*` predicates are exactly the registry's sniffers), and
   `tests/test_parser_coverage.py` (every format has an entry in
   `app/data/parser_coverage.json`). `tests/test_parser_registry_old_equals_new.py`
   pins every attempt list for a corpus of samples: if your detector also fires
   on one of them, the test names it — when that is intended, regenerate
   `tests/fixtures/parser_attempts_before_registry.json`
   (`python -m tests.test_parser_registry_old_equals_new` in the backend
   container) and the diff shows what moved.
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
