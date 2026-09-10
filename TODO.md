# TODO

Forward-looking / deferred work. (`CHANGELOG.md` records what changed; this records
what's intentionally left for later.)

---

## Scans — upload contribution and parsing metrics review, 2026-09-09

- [x] **Review actionability before implementation.** A user wants to see the
  specific new data an upload contributed, with a summary of what was parsed
  and associated metrics. The assessment below came from a read-only source
  review, not live uploads or executed tests. Another agent should validate the
  findings against current code, identify existing reusable functionality,
  separate corrective fixes from new features, and propose bounded work items
  with dependencies, effort/risk, and acceptance criteria. Treat the complexity
  ratings as preliminary, not implementation commitments.

### Validation outcome — 2026-09-10 (code-verified; corrective release shipped as 2.332.0 / 5.204.0)

**Verdicts on the seven findings.** Six confirmed, one half-stale:
1 Modified label — confirmed (`updated_hosts = total − new`, request-time arithmetic).
2 Web-parser attribution — confirmed (bare membership rows; httpx/WhatWeb/EyeWitness/testssl always 0 new, 0 up).
3 Mutable attribution — confirmed; nuance: port service info IS preserved per scan (`PortScanHistory.service_info`), script output is not.
4 Quality metrics — confirmed and worse: only 8 of 20 parsers set stats; `partial` was published but never read.
5 Compare closed ports — confirmed on logic (absent-in-B counted as closed).
6 Snapshot fidelity — confirmed (service from live Port row; `service_info` had no reader).
7 Format drift — UI list confirmed; Amass claim stale in PARSERS.md only (parser keeps name-only rows).
Extra defects found: NetExec duplicate rows for repeated lines within one file; vulnerability CASCADE deleting old findings when the newest scan is deleted; out-of-scope host table is dead (endpoints always 0).

**Shipped (corrective, 2.332.0 / 5.204.0):** label fix; web-parser `host_created`/state snapshot; snapshot service from `service_info`; compare split into `closed_ports` vs `not_observed_ports`; `ingestion_jobs.partial` + nmap/testssl skipped semantics; formats list + doc pin test + PARSERS.md; NetExec within-scan dedupe; **D2** — `vulnerabilities.scan_id` = first-recorded-by (SET NULL) + `last_seen_scan_id`.

**Not done (mechanical follow-up):** `last_parse_stats` for the 12 parsers that still publish nothing (amass, bloodhound, dirbuster, dns, masscan, naabu, nessus, netexec, nikto, openvas, rustscan, smbmap). Effort M, no design.

**Decisions taken (do not reopen without new evidence):**
- **D1 Changed = allowlisted fields, decided at write time from the dedup precedence branch.** Hosts: state, OS name, OS family (hostname is a name observation, not a host change). Ports: state, service name, product, version (not extrainfo/method/confidence). Script output: re-observed only, never "changed". Timestamps, confidence, attribution: never. Gives Added / Changed / Re-observed / Not applied from the same branch; no whole-row diff heuristics.
- **D2 Vulnerability attribution: `scan_id` = first recorded by, never moves; `last_seen_scan_id` = latest re-observation. SHIPPED.** Per-scan severity rollup on /scans now means "introduced by this scan". History before the migration is unrecoverable (kept as last-touched).
- **D3 Baseline = import order.** Contribution is computed against project knowledge at ingestion time and recorded then, never recomputed. Observation time is displayed beside import time so out-of-order uploads are visible; it is never used to re-baseline.
- **D4 Keep the compare page**, relabelled (done). It answers "what differs between two observations", Import results answers "what did this upload add". Once `port_created` exists, compare gains "first observed in B" for free.

**Reusable substrate for the Import results feature (verified):** `HostScanHistory.host_created` (ground-truth create decision), `PortScanHistory.service_info` (per-scan service), `DNSRecord` (immutable per scan by design), `WebInterface` (scan-owned, unique per scan+url+source), `NetexecResult` (append-only per scan), `ConflictHistory.previous_scan_id/new_scan_id` (host state + OS changes only), `IngestionJob` quality trio. Gaps: no `port_created`, no merge-outcome column, no vulnerability observation history, no script-output history.

**Smallest useful first release (next):** an Import results panel on ScanDetail built only from the substrate above — hosts added / existing observed, ports observed with as-scanned service, DNS observations by kind, web interfaces, findings first recorded, quality (skipped / warnings / partial). Show Changed and Re-observed as *not measured* until the step below lands. No new tables.
Then, in order: (1) `port_created` + merge-outcome column on `PortScanHistory` written at dedup time (M) → real Added/Changed/Re-observed for ports; (2) vulnerability observation table (L) — needed for EITHER D1's itemised finding changes OR an immutable per-upload severity summary. Until it exists the /scans rollup means "first recorded by this scan, at current severity" (stated in the tooltip; pinned by `test_first_scan_attribution_counts_current_severity`, 2.332.1) — two scan pointers cannot represent severity history.
Perf note (2.332.1 review): `/scans/{id}/host-snapshots` now caps snapshot construction per host, but still fetches every observed port row for the page's hosts; move the per-host LIMIT into SQL (grouped counts + bounded detail query) if measured request memory on broad scans warrants it. Skip: before/after project-total subtraction; BloodHound edges, SMBMap shares, structured directory paths (separate features).

### Proposed meaning of “unique”

Compare an upload with project knowledge immediately before ingestion. Define
the ingestion ordering/baseline explicitly, including concurrent writes and
older scan files uploaded later; source observation time and import time are
different facts.

| Classification | Meaning |
| --- | --- |
| Added | Previously unknown host, endpoint, DNS fact, finding, or other supported item. |
| Changed | Meaningful stored value changed, such as a service version. |
| Re-observed | Existing item confirmed without a meaningful change; timestamp refresh alone does not count as changed. |
| Not applied | Parsed value did not replace existing data because of merge/precedence rules. |
| Skipped / unsupported | Record could not be imported, or the format's data category is not supported. Keep these reasons distinct. |

Count duplicates **within the file** separately. Input rows, accepted records,
distinct observations, and resulting entities are different units and need not
have equal counts. “First introduced by this upload” is historical;
“only ever observed by this upload” is a separate metric that changes as later
uploads arrive. Avoid one combined “unique data” total across unlike units.

### Findings to validate

- [ ] **Misleading “Modified” label:** `frontend/src/pages/Scans.tsx` displays
  `updated_hosts` as Modified, but this counts already-known hosts observed by
  the scan, not necessarily meaningful changes. Consider “Existing hosts
  observed” until actual changes are measured.
- [ ] **Web-parser new-host attribution:**
  `backend/app/parsers/parser_utils.py::resolve_host_cached` can create hosts,
  while `record_hosts_in_scan` records membership without `host_created` or host
  snapshot fields. Check httpx, WhatWeb, EyeWitness, and testssl for undercounted
  new hosts and incomplete snapshots.
- [ ] **Mutable vulnerability/script attribution:**
  `parser_utils.py::upsert_vulnerability` and
  `backend/app/services/vulnerability_service.py` move existing findings'
  `scan_id` to the latest upload. `/scans` aggregates vulnerabilities by that
  field, so earlier upload counts can change. The host deduplication service
  similarly overwrites script output and scan attribution. Determine the
  historical observation model needed before promising immutable results.
- [ ] **Inconsistent quality metrics:** `last_parse_stats` is optional;
  `backend/app/services/ingestion_service.py` defaults absent stats to zero
  skipped. Nmap uses warning count in its skipped field; testssl can log a
  skipped target while returning zero skipped. Audit all parsers for accurate
  denominators, partial imports, unsupported content, and truncation reasons.
- [ ] **Scan comparison is not contribution accounting:**
  `backend/app/api/v1/endpoints/scans.py::compare_scans` compares two scans'
  host/port observations, not prior project knowledge. Its closed-port set
  includes ports absent from the second scan's open set. Distinguish explicitly
  closed from not observed/tested; absence is not remediation evidence.
- [ ] **Snapshot fidelity:** `get_scan_host_snapshots` reads service names from
  current `Port` rows despite the as-scanned presentation; inspect use of
  `PortScanHistory.service_info` before treating service details as immutable.
- [ ] **Format guidance drift:** the `/scans` supported-format list omits
  WhatWeb, testssl, and RDAP. `documentation/PARSERS.md` says Amass name-only
  rows are dropped, but the current parser retains unresolved names. Reconcile
  the UI, dispatcher, `UPLOAD_FORMATS.md`, and parser reference; an extension
  allowlist test alone does not validate per-format capabilities.

### Per-format assessment

Effort below concerns reliable Added / Changed / Re-observed **item detail**,
beyond basic parsed totals. Verify exact extension/variant coverage through
the dispatcher as well as the parser; do not expand support based on this table.

| Format | Useful metrics / retained data | Preliminary complexity and gaps |
| --- | --- | --- |
| Nmap XML | Hosts, names, states, ports, service fingerprints, OS facts, host/port scripts, incomplete-file warnings. | Medium–high: host/port history is a foundation; need merge outcomes and immutable script evidence. |
| Nmap GNMAP | Hosts, names, ports/states, supplied service/version strings. | Medium: shared host/port path; less source detail than XML. |
| Masscan XML / JSON / TXT | Distinct hosts and host/protocol/port combinations, repeated rows. | Medium: separate bulk-SQL upserts need equivalent instrumentation without sacrificing throughput. |
| Naabu JSON / TXT | Hosts and discovered endpoints. | Low–medium: simple identities/shared persistence; add created/existing outcomes and quality counts. |
| RustScan TXT | Hosts and discovered endpoints. | Low–medium: distinguish ordinary console lines from malformed observations. |
| Nessus XML / .nessus | Hosts, findings by service/severity, CVEs, plugin evidence, host attributes, write failures. | High: separate batched persistence and mutable finding attribution; need immutable observations and creation/change outcomes. |
| OpenVAS / Greenbone XML | Hosts, ports, NVT findings, severity/CVSS, CVEs, malformed results. | High: shared finding updates overwrite attribution; identity includes source identifier and affected host/service. |
| Nikto JSON / CSV / TXT | Tested endpoints, findings, supplied severity/CVEs. | High: finding-history gap; named virtual hosts must remain distinct on shared IPs. |
| httpx JSON / JSONL | Web endpoints, status/title, technologies, headers, favicon/TLS metadata. | Medium: scan-owned web observations exist; need endpoint identity, semantic comparisons, and corrected host attribution. |
| WhatWeb JSON / JSONL | Web endpoints, technologies, titles, server headers/status. | Medium: similar web path; compare supported fields only, not absent TLS fields. |
| EyeWitness JSON / CSV / ZIP | Web observations, screenshots actually stored, missing assets, titles/statuses, skipped records. | Medium–high: distinguish metadata from assets; hashes detect identical images, but visual differences are not automatically new security facts. |
| testssl JSON | TLS targets, weak protocols, certificate expiry/self-signed information. | Medium for retained fields; high for full checks: many source checks are folded into selected web-interface fields, not individual findings. |
| dnsx JSON / JSONL | Names, DNS observations by type/resolver, addresses, PTR enrichment, resolution failures. | Medium: scan-owned observations help; separate new facts, another resolver's confirmation, and TTL changes. |
| DNS CSV | Names, types/values, address bindings, hostname enrichment. | Medium: standardize counters and comparison through shared DNS observations. |
| Amass / Subfinder JSON / TXT | New/unresolved/resolved names, address bindings, hosts introduced. | Medium: useful name-only imports can legitimately create zero hosts. |
| NetExec JSON / TXT | Host/domain/OS/signing facts, protocol/authentication results, supplied shares, conflicts. | High: scan-owned results exist, but comparisons span structured data, raw output, confidence, and precedence. |
| SMBMap JSON / TXT | Hosts and SMB endpoint observations. | Low–medium for current output; share details are not retained. New-share metrics require expanded ingestion/storage. |
| BloodHound / SharpHound JSON | Computer hosts/names, objects lacking usable addresses, unsupported categories. | Low–medium for current inventory import. AD edges/ACLs/users/groups/attack paths are outside current coverage; adding them is a separate large feature. |
| DirBuster / Gobuster / Feroxbuster / ffuf / Dirsearch JSON / CSV / TXT | Endpoints, discovered paths, response codes/sizes, retained versus truncated paths. | High for specific new paths: flattened into service_extrainfo with a 50-entry cap; require structured path observations. |
| RDAP JSON / NDJSON | Netblocks, organizations/ASNs, refreshed registrations, host associations. | High: registration rows are overwritten; scan_id reaches the parser but is not persisted on attribution rows. Can enrich many hosts while reporting zero scanned hosts. |

Subnet CSV import belongs to the Scope workflow and does not create a Scan.
Consider the same vocabulary later, but keep it outside this initial scope.

### Proposed user experience

- [ ] A persistent **Import results** view linked from the completed ingestion
  job and scan row, showing detected format, parser version, import status,
  parsing totals, limitations, and format-appropriate contribution categories.
- [ ] Clickable Added / Changed / Re-observed / Not applied counts for hosts,
  endpoints, DNS facts, findings, and other applicable entities. Drill-down
  shows specific items, prior/imported values, applied result, source evidence,
  filters, pagination, and an export.
- [ ] Keep input-record quality separate from entity contributions. Distinguish
  **zero**, **not supported**, and **not measured**. Partial results must remain
  visibly partial; a successful import does not imply full format coverage.
- [ ] Expose parser limitations at upload and in results, especially BloodHound
  inventory-only support, SMBMap share omission, directory-path truncation,
  and testssl's selected-field extraction.

### Candidate implementation sequence — reviewer to refine

1. Correct misleading labels and attribution/quality defects; standardize
   format-specific parsed summaries and the capability catalog.
2. Capture merge outcomes during persistence for hosts, ports, DNS, and web
   observations. Avoid before/after project-total subtraction: it cannot
   identify individual contributions or reliably handle concurrent changes.
3. Extend existing history where appropriate and add missing immutable
   observations for findings, scripts, structured paths, and RDAP attribution.
   Preserve source-specific identities; a CVE alone is not a finding identity.
4. Persist cheap list-page aggregates alongside paginated detail. Review typed
   schema versus opaque evidence storage, indexing, streaming/bulk performance,
   retry idempotency, savepoint rollbacks, partial batch commits, and deletion
   semantics. Do not persist a counter for a write that rolled back.
5. Mark historical coverage honestly. Reconstruct only what retained evidence
   supports; prior values and original contributions may be unrecoverable.

### Required review deliverable and validation

- [ ] Produce an actionability assessment: confirmed versus stale findings,
  smallest useful first release, per-format coverage, schema/API/UI changes,
  dependencies, effort/risk, migration limits, and proposed implementation
  tickets. Identify product decisions that remain unresolved instead of
  silently selecting meanings for “new” or “changed.”
- [ ] Define representative fixtures for every supported parser/variant and
  test **upload A → repeat A → upload B with one controlled change**. A's
  historical results stay stable, the repeat adds no semantic novelty, and B
  identifies the exact changed item.
- [ ] Cover within-file duplicates, unresolved names, shared-IP virtual hosts,
  malformed/truncated files, unsupported categories, merge-rejected values,
  retries/partial commits, concurrent writes, deletion, and large-file query
  budgets. Verify absent observations are not reported as closed/resolved.

---

## AI Assist tool surface — plan, 2026-08-19

The assist tool set is now derived from the analyst's job rather than added
question by question: **[documentation/ASSIST_TOOLS.md](documentation/ASSIST_TOOLS.md)**.

**P1 shipped in 2.294.0** (prompt 1.55.0) — as three tools, not four:

- [x] **`assist_get_patterns`** — systemic insights (blind spots, segment
  outliers, condition spread, per-family root cause). The "which subnet is
  worse than the others" analysis already existed in
  `systemic_insight_service`; assist simply could not reach it.
- [x] **`assist_get_finding`** — one finding with its evidence note, comment
  thread and attachment references, plus `GET /assist/attachments/{id}` so the
  agent can actually fetch the screenshots with its key (the operator-facing
  download needs a JWT).
- [x] **`assist_get_posture`** — headline condition + signals + remediation
  flow, from `posture_service`.
- [x] ~~**`assist_get_attention`**~~ — **dropped, not deferred.**
  `compute_posture` already folds `compute_project_attention` and
  `compute_site_attention` in, so this would have been the same numbers under a
  second name — the "tools the agent has to combine" failure the plan's own
  review rule exists to prevent.

Still queued:

- [ ] **P2** — subnet insights (per-subnet EOL / TLS / SMB-signing / weak-auth
  detail), ingestion issues (so "no data" can be told from "the upload didn't
  parse"), web-interface screenshot references.
- [ ] **P3 (build, don't wrap)** — time-series. "What changed since last week"
  has no implementation for humans either; the existing insight services are
  deliberately cross-sectional because engagements run 6–8 weeks.

Rule of thumb recorded there: a new tool must be a distinct *question shape*.
`q=` filters, file-shaped downloads, and rollups an existing service computes
are not tools.

---

## Deferred from the MCP / assist work — 2026-08-19

Each of these was found while building something else, judged real, and left
alone deliberately. Recorded here so the reasoning survives the conversation
that produced it.

### Test-harness

- [ ] **`setupTests.ts` mocks `useParams` globally to `{ id: '1' }`.** Any page keyed
  on its own route param therefore reads as "no param supplied" in tests and
  silently falls through to its other branch — `AssistSessions` needed a local
  override to test its detail view at all. Route-param behaviour is effectively
  untested app-wide, and a page test can pass for the wrong reason. Fixing it
  means auditing every suite that currently depends on the fixed `{ id: '1' }`,
  which is why it isn't folded into a feature commit.
- [ ] **Backend tests share one Postgres.** Parallel or concurrent runs interfere;
  an external reviewer hit it too and their isolated rerun passed. Wants a
  per-run database (or a transaction-per-test harness), not a retry.

### Schema

- [ ] **`assist_sessions.status` is free-text `String(20)` with no CHECK.** `"actve"`
  is storable. Status semantics became load-bearing in 2.283.0 (the derived
  active/ended rule), so this is the moment it earns a constraint.
- [ ] **Stored vs. derived session status.** The column is *eventually* correct —
  the hourly sweep converges it, the API derives the truth per request. Anything
  querying `assist_sessions.status` directly (a future report, an ad-hoc SQL
  check) gets a different answer from the API for up to an hour. The model needs
  a comment saying the API is authoritative; the endpoints already say it, but
  the next person to write a query won't read them.

### Frontend

- [ ] **Relative-time consolidation is done for the ten timestamp formatters**
  (`utils/relativeTime.ts`, v5.179.0), but four surfaces were deliberately left
  out because they answer different questions — calendar-day bucketing
  (`MyActivityCard`), day/month scan age (`ProvenanceCard`), server-provided day
  counts (`SecurityPosture` / `PortfolioDashboard` / `Operations`), and a
  countdown (`TestPlanLayout.formatTimeLeft`). Listed in the util's docstring so
  they don't read as misses. No action expected — this entry exists to stop the
  next sweep "finishing the job" and breaking them.

### Product

- [ ] **Assist is absent from the unified `/agent-activity` timeline**, which covers
  recon, plan generation and execution only. Widening it means touching the
  shared session-kind service (`agent_sessions.py`, `SessionKindLiteral`), which
  is a different change from giving assist its own page — that shipped as
  `/assist-sessions` in 2.284.0.

---
