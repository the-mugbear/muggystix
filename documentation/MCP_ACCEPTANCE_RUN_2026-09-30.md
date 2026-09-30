# MCP acceptance run — 2026-09-30

The redesigned MCP supports useful project investigation without first creating a plan, and accepts uncatalogued tools in optional execution records. Acceptance is **partial**: the most consequential design gap is the absence of a complete, independently readable custom-tool evidence path; four reproducible correctness defects also undermine answer fidelity. The strongest area is the tested permission enforcement, combined with finding-level evidence, endpoint dispositions, and explicit distinction between unobserved and closed ports.

## Context, method, and invariants

- Live endpoint: `https://127.0.0.1/api/v1/mcp`; backend **2.434.0**, prompt **3.2.0**. Local source HEAD at inspection: `ff803c5f7d1c86dc64bdf8c4fc8da879396943d8`. The running image's commit was not independently established. Other frontend edits were present and untouched; a concurrent MCP feedback-description edit also appeared during the run. Source locations below reflect inspection time.
- Existing service: Python/FastAPI/Pydantic/SQLAlchemy, PostgreSQL, Docker Compose; React/TypeScript client. This evaluates the evolving MCP adapter and its existing agent HTTP/services, not a new standalone library. No schema migration or application code changed in this run.
- Project **3**, `Demo — Insights Eval`: independently counted **419 hosts, 48 scans, 53 findings** before writes. This is synthetic evidence, not an assessment of real targets. Relevant records include hosts/ports, scan discoveries, vulnerability observations, names and resolutions, scope/subnet mappings, annotations, canonical findings, plans, execution results, ingestion jobs and reports.
- Initial orientation used the supplied administrator-bound session **74**. Subsequent permission checks and all new project writes used existing fixture users: analyst session **71**, auditor **72**, viewer **70**. Their expired keys renewed through `session_renew`. Admin-only orientation results do not establish ordinary-user authorization.
- Actual JSON-RPC MCP calls used a local Python transport; intentional file transfers used the advertised agent HTTP routes. System TLS trust passed; later calls verified certificates. This is **not** a native Codex integration test, second-client test, or blind tool-discovery benchmark. The evaluator inspected schemas and source to diagnose behavior.
- No target scans or external checks ran. A local manifest-only validator produced explicitly synthetic `not_tested` output. Database reads provided independent fixture checks; no direct database writes were made. Browser/UI and original scanner-file verification were not completed for every seeded fact.
- Existing shared building blocks inspected include `exploit_count_maps`, `_serialize_note`, `scan_time_for_api`, `ReportGenerator.iter_host_records`, and the existing scope/name serializers. Findings about reuse below name these visible implementations.
- Scale assumption: a single operator on a small project. The run does not establish an SLO, production capacity, snapshot consistency, or concurrency behavior.

Invariants checked: every read/write stays within the key's project and current role; counts agree with qualifying observations; name authorization never expands to arbitrary services on its shared address; exports preserve selection and disclose incompleteness; queued uploads are not called parsed; missing evidence is not called remediation; authorship, time semantics, and original output survive recording and handoff; custom tools need no catalogue admission or approval phase.

Evidence: [selected exact responses](acceptance/2026-09-30/evidence.json), [112-request index](acceptance/2026-09-30/request-index.json), and the artifacts below. Index entries preserve arguments where recorded, status, timing, and size. Full sanitized transport responses remain locally at `/tmp/bluestick-acceptance-20260930/ledger.jsonl`; credentials are excluded from repository artifacts. A JSON-RPC HTTP 200 with `isError: true` is a failed tool operation, not a pass.

## Practitioner outcomes

### Pentester: prioritize, select, record, hand off

`has:critical_exploit` selected **7 hosts**; `has:critical AND has:exploit` selected **31**. These are different questions: the latter allows severity and exploit availability on different observations. The seven-host [manifest](acceptance/2026-09-30/targets.json) reconciles to the filtered export. Its aggregate scope label refers to eight /24 subnet declarations within scope 2, not eight separate scope records; the artifact is retained byte-for-byte because the result records its hash. Exploit availability is not proof of successful exploitation; host 401's qualifying observation 1054 is explicitly synthetic/manual and dated August 20.

Five useful investigation leads from this fixture are:

1. Host **358**, `10.10.3.49`: critical exploit-available observation and recorded administrative/database services; last observed July 11. Refresh and inspect the exact observation before attempting validation.
2. Host **439**, `10.10.4.59`: critical exploit-available observation, SMB, old Windows label; July 11 evidence. Verify current identity/configuration, not merely the historical OS label.
3. Host **191**, `10.10.4.28`: critical exploit-available observation, but unknown state and April 22 last observation. Freshness is the first investigation question.
4. Host **43**, `10.10.0.10`: SMB/configuration leads and existing team review. Inspect direct protocol evidence and coordinate before duplicating work; the inventory is not proof of a relay path or successful exploitation.
5. Host **472**, `203.0.113.20`: three authorized HTTPS virtual hosts, with scan-backed name observations, but no address-wide scope. Preserve each hostname/SNI in targeted validation; do not scan the entire shared IP because one name is authorized.

The [named manifest](acceptance/2026-09-30/named-targets.json) retains portal/API/admin sites separately. The scope web export alone omitted them. Certificate SAN membership does not authorize `shop.example-corp.com`.

The [local validator](acceptance/2026-09-30/custom_validator.py) processed the seven-host manifest and produced [custom JSON](acceptance/2026-09-30/custom-results.json), without target traffic. Its upload failed as unsupported. A host note and optional plan could preserve prose/structured test summaries, but another auditor could not recover the submitted raw result through the tested history routes. Therefore the end-to-end custom-tool evidence task is **partial**, not complete.

Finding **1** provided three endpoint dispositions. The [retest manifest](acceptance/2026-09-30/retest-targets.json) includes hosts **132/142**, excludes remediated host **227**, and explicitly does not claim to have applied a team-ownership exclusion. No endpoint disposition was changed.

### SOC analyst: correlate, distinguish time and coverage

A [synthetic alert file](acceptance/2026-09-30/soc-alerts.json) was joined locally with exported inventory/name data. The [join](acceptance/2026-09-30/soc-join.json) retains host **401**, host **472** plus name **114**, and an unmatched `198.51.100.250` event. This demonstrates composition with a user-provided file, **not** an EDR/SIEM connector. Matches are current inventory matches; historical IP/name ownership at alert time remains unverified.

Comparing scans **287 → 290** returned one dropped host, zero closed ports, and two **not-observed** ports (445/9443). That is a useful rejection of the hypothesis “absence means remediated.” The scans' 2024 observation dates must not be confused with their September 2026 import dates; timezone semantics in the scan list are nevertheless defective below. No evidence established Internet reachability or scanner vantage.

Issued report **8**, finding comments/thread **130/131**, attachment reference **4**, and endpoint status history were readable. The issued report was kept distinct from current finding text. Full report binary/rendered-content comparison was not exercised.

## Critical

None confirmed in the exercised scenarios. This does not certify untested revocation, concurrent writes, or every cross-project object route.

## High

### H1. Host detail silently resets exploit counts to zero

**Status:** Confirmed. **Location:** `backend/app/api/v1/endpoints/agent_assist.py:1186–1230`. The constructor populates severity counts but omits both exploit counts:

```python
return AssistHostDetail(
    ...
    open_port_count=open_count,
    vuln_summary=VulnCounts(
        critical=vc.get("critical", 0),
```

**Trigger:** List host 401 through `has:critical_exploit`, then fetch its detail. List/export show `exploitable_count: 1, critical_exploitable_count: 1`; detail shows `0, 0`. Vulnerability **1054** is critical and exploitable. Auditor read-back reproduced the discrepancy.

**Impact:** A normal drill-down can reverse the prioritization conclusion without any evidence change.

**Fix:** Hydrate both fields with existing `host_serialization.exploit_count_maps(db, [host.id])`, as the list path does. **Test:** a host with one exploitable critical must return identical counts in list, detail, and filtered export. **Single source of truth / why now:** reuse the existing aggregate; fix now because this is the core investigation path.

### H2. MCP advertises an outcome the recording API rejects

**Status:** Confirmed. **Location:** `backend/app/api/v1/endpoints/mcp_tools.py:1665–1667`:

```python
"status": {
    "type": "string",
    "description": "Outcome of running it (e.g. completed, failed, skipped).",
```

The adjacent entry schema at lines **1485–1487** also says only `"type": "string"` / `"Which phase of the engagement this belongs to."` for `test_phase`.

**Trigger:** Submit `status: "completed"` for plan 64/entry 94/test 0 exactly as advertised: HTTP 422, accepting `pending`, `pending_approval`, `executed`, `skipped`, `failed`, or `not_applicable`. A free-form `test_phase: "acceptance"` similarly passes the MCP shape but receives an enum 422. Retrying phase `reporting` and outcome `skipped` worked.

**Impact:** Even a schema-following agent must guess/retry normal writes. The stale `pending_approval` vocabulary also warrants review against the new design, but its continued presence alone is not a proven bug.

**Fix:** Expose accurate enums and examples, sourced from the request models where possible. **Test:** contract-test every advertised enum/example against HTTP validation, including custom tool names. **Net reduction / removal safety:** eliminate duplicated enum definitions if a small shared schema mapping suffices; do not replace the whole adapter speculatively. Verify intentional MCP-only restrictions remain intact. **Why now:** this is public tool-contract drift.

### H3. Report export changes agent authorship into human authorship

**Status:** Confirmed. **Location:** `backend/app/services/host_serialization.py:78–97`, `_serialize_note`, omits `actor_type` when constructing `Annotation`; `backend/app/schemas/schemas.py:152` supplies:

```python
actor_type: str = "user"
```

`backend/app/services/report_generator.py:2543–2544` uses:

```python
return _serialize_note(note).model_dump(mode="json")
```

**Trigger:** Analyst calls `assist_add_note` creating note **134**. Both its response and auditor `assist_get_host_notes` say `actor_type: "agent"`; the dossier export of the same note says `"user"`.

**Impact:** A client-facing evidence export misattributes automated work to a human.

**Fix:** Pass the stored actor type in the shared serializer. **Test:** create an agent note and compare direct read, shared serialization, and dossier export, while preserving human-note behavior. **Single source of truth / why now:** `Annotation.actor_type` in persistence; fix the shared serializer rather than adding another MCP-only patch. Existing MCP `_assist_notes` explicitly overrides this field, illustrating the drift.

### H4. Scan list erases the distinction between UTC instants and wall-clock times

**Status:** Confirmed. **Location:** `backend/app/api/v1/endpoints/agent_assist.py:2413–2416`:

```python
for s in scans:
    row = ScanBrief.model_validate(s)
    row.ingestion_job_id = jobs.get(s.id)
    out.append(row)
```

**Trigger:** Read scans **290** and **287**. Both return `2024-04-01T00:00:00` without timezone or time basis. Independent database reads identify 290 as `tool_run` and 287 as `tool_clock`: the first represents an instant, the second an unspecified local clock.

**Impact:** A SOC analyst cannot safely correlate these values with a UTC alert; treating them identically can produce a wrong timeline.

**Fix:** Apply existing `scan_time_for_api` and expose the basis when wall-clock/unknown. Do not simply append `Z` to everything. **Test:** epoch-derived XML timestamps serialize with UTC semantics while timezone-unknown GNMAP timestamps remain explicitly ambiguous. **Single source of truth / why now:** `services/scan_time.py:25` already handles the distinction and host discovery serialization already uses it; reuse now for consistent public evidence.

## Refactor

### R1. Make custom evidence a first-class project record with a readable artifact

**Status:** Confirmed missing capability in the exercised surface. **Location/evidence:** upload jobs **549/550**, result **22**, and `backend/app/api/v1/endpoints/agent_assist.py:1880–1887`:

```python
AssistTestResult(
    status=r.status.value if hasattr(r.status, "value") else str(r.status),
    command_run=r.command_run,
    findings_summary=r.findings_summary,
    severity=r.severity,
    is_finding=bool(r.is_finding),
    executed_at=r.executed_at,
)
```

**Impact:** Unsupported JSON yields parse failure, not a usable generic evidence record. Creating a plan preserves a result internally, but tested auditor history/plan/dossier reads omit `raw_output`; active execution reads return 409/400 after closure. This run does not claim stored raw bytes were deleted or inaccessible to administrators/UI. It establishes that the intended MCP handoff remains incomplete. No advertised generic observation/artifact, finding promotion, or endpoint-disposition mutation was found.

**Fix:** Initially expose a project-scoped result/artifact read with IDs, command, outcome, time, author/session, target identity and raw output/download reference. Then allow the same evidence to be recorded directly without a plan, with optional links to findings/endpoints. Unsupported parser output should be preservable without pretending it normalized into vulnerabilities. Keep bytes out of routine summaries.

**Net reduction:** remove the required client-side plan → entry → execution scaffolding for ad-hoc evidence; reuse one evidence contract for optional planned and unplanned work. **Removal safety:** retain existing plan-result history and permissions; replay this test from a different auditor session, including negative, error, and skipped outcomes. This is a product change, not a small serializer-only fix.

**When it bites / lever:** every unsupported custom format; large outputs additionally need bounded reads or downloads. Code/API design, not provisioning. A generic artifact layer adds server complexity but removes repeated workflow and client workarounds; justify it by this core requirement. **Why now:** evidence contracts and persisted links become expensive to retrofit after clients adopt incompatible workarounds.

### R2. Provide a name-preserving scope target export

**Status:** Confirmed design limitation, not a claim that the documented subnet-only implementation violates its contract. **Location:** `backend/app/services/scope_targets_service.py:65–76`:

```python
select(models.HostSubnetMapping.host_id)
.join(models.Subnet, models.Subnet.id == models.HostSubnetMapping.subnet_id)
.where(models.Subnet.scope_id == scope_id)
```

**Impact:** Scope 2 exported 400 hosts and 398 IP-based web URLs, omitting authorized portal/API/admin names on host 472. An IP-only file cannot express name-limited authorization; widening its host selection would be unsafe.

**Fix:** Offer a separate endpoint manifest joining declared name scope, observed name/address bindings, and web evidence; retain the existing explicitly subnet-only export. Include unresolved names and reasons for exclusions.

**Net reduction:** eliminate repeated client-side joins across scope domains, names, hosts and web interfaces. **Removal safety:** do not collapse address and name authorization; test shared-IP authorized/unauthorized names, unresolved names, IPv6 and nonstandard TLS ports. Only the shared-IP case was exercised here. **Existing truth / why now:** reuse existing name scope and web observation services; avoid creating a second scope fact. Fix before presenting “scope targets” as complete inputs for arbitrary tools.

**When it bites / lever:** domain-scoped projects today; per-host enrichment becomes expensive at thousands of sites. Code-level batching/export design, not more workers. A richer manifest adds a representation but simplifies clients.

### R3. Reduce mandatory onboarding and unify evidence projections

**Status:** Confirmed friction; high-scale impact unmeasured. **Location:** `backend/app/api/v1/endpoints/mcp_assist.py:145–146`:

```python
"workflow — is the authoritative how-to and is binding: read it once via "
"read_agent_guide before your first substantive call. Operators who paste a "
```

**Impact:** `tools/list` transferred **57,848 bytes / 61 tools**; the guide **100,446 bytes**. A small read task starts with substantial instructions about process. Meanwhile the concise vulnerability reader omits scan IDs and observation times that the existing dossier export successfully supplies. H1/H3/H4 demonstrate the correctness risk of divergent projections.

**Fix:** Provide short identity/capability/error conventions and link optional task documentation. Reuse shared evidence serializers for common fields; keep summary versus full-artifact views distinct. Do not force unrelated reporting and execution concepts into one generic abstraction.

**Net reduction:** remove mandatory workflow prose and duplicated fact mapping, not useful read tools merely to reduce their count. **Removal safety:** repeat discovery and permission-error recovery in fresh clients; retain necessary scope and untrusted-data semantics. **Existing reuse / why now:** `ReportGenerator.iter_host_records` already exposes provenance and uses chunked export; use it as a reference for fact parity. Fix repeated mapping bugs now, stage larger guide reduction.

**When it bites / lever:** repeated fresh chats and clients with small context budgets; measured bytes, not a measured token/latency benchmark. Simplification/code-level response design first. No provisioning change justified.

### R4. Add bounded history navigation before long-lived projects exceed the scan cap

**Status:** Confirmed API limitation; volume-dependent impact not load-tested. **Location:** `backend/app/api/v1/endpoints/agent_assist.py:2383,2401–2403`:

```python
limit: int = Query(100, ge=1, le=500)
...
.order_by(models.Scan.created_at.desc())
.limit(limit)
.all()
```

**Impact:** The exposed scan list has no offset/cursor or date window. MCP rejects `offset`; after more than 500 matching scans a previously unknown baseline cannot be discovered through this list. This fixture had only 48 scans initially, so no truncation-induced wrong answer was claimed.

**Fix:** Add stable cursor/date navigation and explicit completeness metadata. **Net reduction:** removes manual baseline-ID discovery; adds small pagination state rather than eliminating server code. **Removal safety:** test equal timestamps and inserts between pages; use an ID tiebreaker. **When it bites / lever:** more than 500 scans per selected tool/project; code-level bounded pagination, not scaling. **Why now vs. later:** track before sustained daily ingestion; this is a public contract extension, not an urgent capacity incident in the current fixture.

## Nitpick

None worth elevating from this run.

## Scenario coverage against the acceptance document

“Partial” includes useful paths with missing capabilities or incompletely exercised fixtures. “Not exercised” is not a pass. Answer integrity was maintained by qualifying the limitations below; this does not turn partial task capability into complete acceptance.

| Scenario | Result and boundary |
|---|---|
| A1 | Complete for protocol identity, project counts, role capabilities and expiry; no plan prerequisite. Native-client discovery not tested. |
| A2 | Partial: ranked leads and provenance inspected; detail counts are wrong and seeded evidence is synthetic. |
| A3 | Failed parity: exact predicate/export work; detail counts disagree (H1). |
| A4 | Partial: personal/team queues and ownership read; no exhaustive current-role queue audit. |
| A5 | Complete for exercised absent tag and invalid-query cases: vocabulary had no tags; invalid predicate rejected. |
| B1 | Partial: configuration leads inspected; no attack-path validation or inferred connectivity. |
| B2 | Partial: raw observations and dossier provenance read; no controlled authenticated/version-only disagreement fixture. |
| B3 | Complete for target preparation: seven exact hosts, uncatalogued local stub, no approval. |
| B4 | Partial: named HTTPS sites and SANs preserved; nonstandard-port/IPv6 matrix not completed. |
| B5 | Complete as an evidence-based proposal only; no automatic scan. |
| C1 | Not exercised: no supplied advisory/version-boundary fixture. |
| C2 | Partial: current matches and historical uncertainty explicit; scan time basis defect H4. |
| C3 | Partial: temporal scan comparison works; external vantage/reachability not established. |
| C4 | Complete for local synthetic join, including unmatched row; not a real EDR connector or point-in-time join. |
| C5 | Partial: not-observed versus closed verified; failed-auth/remediation matrix not exercised. |
| D1 | Partial: open finding endpoints exported; remediated endpoint excluded, team-work exclusion not completed. |
| D2 | Partial: named scope omission reproduced; full IPv6/nonstandard-port matrix not exercised. |
| D3 | Not exercised: concurrent-import snapshot/pagination consistency. |
| D4 | Not exercised at 10,000 endpoints; only bounded fixture exports measured. |
| D5 | Complete for shared-IP scope reasoning, with no target traffic. |
| E1 | Complete for empty recognized XML: no plan, job 548 → scan 557. Nonempty parser fact fidelity not established by this upload. |
| E2 | Partial: custom JSON rejected explicitly; raw artifact handoff incomplete (R1). |
| E3 | Partial: ad-hoc host note works; richer evidence required manufactured optional workflow. |
| E4 | Partial: existing finding threads/artifacts readable; no MCP evidence-link mutation demonstrated. |
| E5 | Blocked through advertised tools: promotion/endpoint disposition mutation absent; no change claimed. |
| E6 | Partial: successful scan duplicate rejected with existing scan ID; failed custom retry produces another failed job. Note timeout/idempotency not tested. |
| E7 | Partial: existing ingestion issues/uninterpreted lines inspected; controlled 99/100 import not executed. |
| E8 | Partial: custom plan/execution works with no approval/target gate; advertised enums fail (H2). Test-edit/reorder safety not tested. |
| F1 | Complete for existing endpoint dispositions and two-host retest file. |
| F2 | Partial: different auditor reads notes, commands, summaries; raw execution output missing (R1). |
| F3 | Partial: current queues/history read; since-last-review notification/delta reconstruction not established. |
| F4 | Partial: issued metadata/snapshot distinguished; rendered report binary comparison not performed. |
| F5 | Partial: qualified brief produced above; negative custom evidence still lacks complete handoff. |
| G1 | Complete for tested matrix: all three roles read counts; viewer/auditor writes denied, viewer reports/exports denied, auditor report/export allowed, ingestion diagnostics restricted. |
| G2 | Partial: expiry and renewal exercised; live demotion/removal/archive/revocation not performed. |
| G3 | Partial: independently known foreign host 1, scope 1, job 1 rejected with 404. Foreign finding/attachment and overlapping-IP matrix not completed. |
| G4 | Limited pass for this evaluator: instruction-like note text read as evidence; no unrelated action followed. Not a multi-model injection benchmark. |
| G5 | Not exercised: shell metacharacter argument-boundary fixture. Local validator used JSON and did not invoke target commands. |
| G6 | Not exercised: shell-less/second/native MCP client. HTTP bulk dependency remains a client capability requirement. |
| G7 | Partial: another session located committed note/plan; no actual transport interruption or lost-response injection. |
| G8 | Not exercised: project-session end/revocation. Optional execution 32 closed; supplied session deliberately remains active. |

## Performance and remaining validation

The ledger contains **112 requests**, including intentional error probes and HTTP transfers: median **13.45 ms**, p95 **44.4 ms**, max **169.1 ms**, measured by the local transport. These are sequential, mixed-operation observations, not a throughput benchmark. Scope export was about **215 KB** for 400 hosts; the guide/tool catalogue are substantial even before task data. No CPU, memory, query-plan, worker-pool or database saturation measurements support a provisioning recommendation.

Prioritize complete evidence contracts, shared serializers and precise bulk exports. Before production sign-off, separately exercise 10,000-host exports, concurrent ingestion/paging, permission changes while connected, interrupted writes, foreign finding/attachment IDs, native clients and a shell-less client. Scaling resources would not repair the demonstrated schema, attribution or missing-evidence problems.

## Persistent changes made by this test

- Renewed existing fixture sessions **70/71/72**; supplied session **74** was not ended.
- Created upload batch **17** (`MCP-acceptance-20260930-synthetic-only`), supported job **548 / scan 557**, and unsupported jobs **549/550** (parse errors **34/35**). Exact successful upload retry returned 409 referencing scan 557.
- Created agent note **134**, plan **64**, entry **94**, execution **32**, result **22**. All are explicitly synthetic. Result is **skipped**, `is_finding=false`; closure reports **0 executed / 1 skipped / 0 findings**. Execution is completed; the plan remains `in_progress` at 100% entries completed. This is recorded state, not asserted as another lifecycle bug.
- Host count remained **419** after writes. Existing vulnerability/disposition data was not modified. No target checks were performed.
- Added this report and sanitized evidence/artifact files only; no application fix, commit, deployment, or client configuration change.
- **Afterwards (2026-09-30, not part of the run):** sessions 70/71/72/74 were ended and their keys revoked, and the synthetic records above (note 134, plan 64 with entry 94 / execution 32 / result 22, batch 17, scan 557, jobs 548–550, parse errors 34/35) were deleted from the dev instance. H1–H4 were fixed in 2.434.1 (commit b44aabb7), with regression tests that fail against 2.434.0; H2's contract test found three further MCP enum drifts (entry status, the second `test_phase`, the target-check method). `pending_approval` was kept: it records a command waiting for the operator's go-ahead, not plan approval.
