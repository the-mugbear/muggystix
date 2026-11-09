---
paths:
  - "backend/app/api/v1/endpoints/agent_*.py"
  - "backend/app/api/v1/endpoints/mcp_*.py"
  - "backend/app/api/v1/endpoints/assist.py"
  - "backend/app/api/v1/endpoints/feedback.py"
  - "backend/app/services/agent_*.py"
  - "backend/app/services/agents_guide_service.py"
  - "backend/app/services/mcp_*.py"
  - "backend/app/services/scope_targets_service.py"
  - "backend/app/services/tool_registry_service.py"
  - "backend/app/data/tool_registry_seed.json"
  - "backend/app/db/models_agent.py"
  - "backend/app/api/deps.py"
  - "documentation/AGENT_GUIDE.md"
  - "documentation/MCP.md"
  - "documentation/ASSIST_TOOLS.md"
  - "frontend/src/components/agent-sessions/**"
  - "frontend/src/components/StartAssistDialog.tsx"
  - "frontend/src/components/mcp/**"
  - "frontend/src/pages/AgentSessionDetail.tsx"
  - "frontend/src/utils/agentRuns.ts"
---

# The agent surface and MCP

`/api/v1/agent/*`: `X-API-Key`, a router-level gate (`enforce_agent_operator_access`), its own routers; `agent_activity` is the JWT viewer, not agent-facing. Authority, proposals, no rails, MCP authorizing nothing: the root `AGENTS.md`.

## Sessions and keys

- A key binds to ONE project-scoped `AgentSession`, started only by `POST /projects/{id}/assist/start`; a session has ONE id (the `agent_sessions` row's), never a second row.
- The gate stamps `request.state.key_operator_id` / `request.state.agent_project_id`; handlers read them (`agent_common.load_operator`). Auth = one joined select + the rate-limit upsert (+ the minutely spent-window delete); add no query (hazard — removable: a query-count test on the gate).
- One limiter, `deps.admit_agent_request` (`/agent/session/renew` counts; a window's first request deletes spent ones). A 429 says "Retry in N s" with `Retry-After` (`test_phase1_regressions.py`, `test_review_2026_10_23_agents.py`). It fails open ONLY when it cannot exist (`deps._rate_buckets_missing`: no ON CONFLICT, `agent_rate_buckets` 42P01); a timeout raises.
- A key issued before `password_changed_at` → 401 `{"error": "operator_credentials_changed", "recoverable": false}`. Admin reset-password, self change-password, admin reset-2fa end the user's agent sessions (`agent_session_service.end_sessions_of_operator`; admin resets also browser sessions); so must a new recovery action (hazard — removable: a test enumerating recovery routes). Self-service 2FA disable does not.
- `mint_session_key`, the one mint (hazard — removable: a test that nothing else creates an agent key), caps expiry at `session_renewal_deadline`; Resume's `ttl_hours` ≤ 24. No `expires_at` = expired (`deps._key_expired`).
- Renewal refuses an inactive operator (401 `operator_inactive`) or a non-member non-admin (401 `operator_not_a_member`) and ENDS the session (`deps.end_session_of_departed_operator`), as does the gate's non-member 403 — a re-added operator's old key stays dead. Removing a member ends their sessions there (`remove_member` → `end_sessions_of_operator(project_id=)`) (`test_review_2026_10_22_member_removal.py`). Deactivation: `backend-db-and-tests.md`.
- `large_upload_refusal` refuses an expired key, renewable or not, a key issued before its operator's password change, and whatever `operator_access_refusal` (the gate's own decision, one function) refuses for a project write — ending a departed operator's session as the gate does (`test_review_2026_10_23b_platform.py`).
- Writes carry `agent_session_id` (row: `host_test_count` / `evidence_count`). Ending never refuses or loses work; `POST /agent-sessions/{sid}/resume` rotates the key on the SAME session; `can_end` / `can_resume` mirror the routes. "Live" is `key_state` (`agent_session_service.session_key_state`; `utils/agentRuns.keyState` / `hasLiveKey` only word it).
- A session has no kind. `agent_session_service.LISTED_WORKFLOWS` (`project`, `assist`) is the ONE list of stored `agent_sessions.workflow` values listed and counted; Resume (and `can_resume`) refuses a non-`project` row.
- A dropped tool argument is `-32602` (`test_no_tool_keeps_an_argument_it_used_to_take`). Feedback has no `source` (`test_feedback_takes_no_source`).
- Direct, never gated: notes, review status, host corrections, uploads, host tests, feedback, evidence, remediation writes. Proposals (`POST /agent/proposals/*`, `triage-tests-proposals.md`) include `propose_subnet_details`; never a direct route for a proposal kind.
- Evidence (`POST /agent/evidence`) is immutable; raw output deferred with `raw_output_sha256`, read at `/raw` / `/raw-chunk` (MCP `read_evidence_raw`). No `host_id` only for an `info` record (`ck_evidence_host_or_info`).

## Parity with the pages

- A new page or field needs an agent read wrapping the SAME service at the page's role. `test_agent_host_enrichment.py` pins host counts. `/agent/assist/notes` = `note_activity_service.project_notes_page(every_target=True)`; `/agent/assist/names` = `names_list_service.names_query` (`state` typed `NameState`).
- A read declares its floor ON THE ROUTE or its `APIRouter(...)`: `dependencies=[Depends(agent_read_floor(ProjectRole.AUDITOR))]`, or `Depends(agent_session_metadata_write)`. The gate reads the matched route (`deps.agent_route_access`); `include_router(dependencies=)` is invisible. Defaults: read VIEWER, write ANALYST. List it in `test_agent_role_route_matrix.py`.
- Host filter = `host_query.build_filtered_host_query`. `services=` is the service identified on an open port, any number, never name→ports; other words: `inventory-reads.md`.
- An unparseable filter is a 422 naming it on both doors (`host_query.parse_port_list` / `parse_id_list`, `host_query.check_discrete_filters` for `state`, `subnets`, a list naming nothing; `/agent/assist/scans?created_after=`) — never dropped.
- A filter naming a record outside the project is a 404 from the SERVICE (`filter_targets.require_in_project`, in `agent_evidence_service.list_evidence`, `FindingService.list_findings`; `agent_common.require_project_host` delegates). NetExec rows / `assist_list_host_access` = `host_access_service`; TLS facts = `cert_fields.tls_summary` (`WebInterfacesCard` only words it). `test_review_2026_10_23b_agents.py`.
- Review status is ONE type, `schemas.ReviewStateToSet` (`engagement_metrics_service.REVIEW_STATES`), on `POST /hosts/{id}/follow` / `/hosts/bulk/follow`; `AgentFollowRequest.status` adds `none` (`clear` unadvertised); `assist_set_follow` derives it; `watching` is a 422 (`test_the_agents_review_status_takes_the_offered_states_and_refuses_watching`, `test_the_peoples_review_status_refuses_watching`). The status is the whole request: `HostFollowService.apply_review_status` on every door, no conclusion (`tests/test_review_one_click.py`). `PATCH /hosts/{id}/follow`'s note is people-only (agents: `assist_add_note`).
- Pageable lists take `limit` / `offset`, answer `{items, total, limit, offset, returned, has_more}` via `services/paging.page_envelope` (= `agent_schemas.agent_page`; other `has_more` = `paging.rows_remain`; `test_paging_one_envelope.py`); page routes keep theirs, bar the host-test and remediation factories. `tests/test_agent_list_paging.py` lists each paged read or `NOT_PAGED` with a reason (evidence gaps, scan compare, the client report's findings, the raw-chunk read).
- `?skip=` → 422 `deps.AGENT_SKIP_REFUSAL`; other unknown query parameters are ignored.
- An agent's note is announced like a person's (`HostFollowService.post_note` on both doors; the operator-author is not told). One function says it: `NotificationService.announce_note` (host note and finding comment, posted or edited) (hazard — removable: a test that every note/comment write goes through it).
- Agent curl never uses `-k` (hazard — removable: a test that no agent-facing text — prompt, MCP opening, guide — carries `curl -k` / `--insecure`); the key goes by `-K .bluestick.curl` (`agent_policy.KEY_FILE`, `render_key_file`), never `-H "X-API-Key: $KEY"` (sandboxes refuse expansion; `test_agent_safety_policy.py`).
- A write answers what it changed, never an empty 204: `POST /agent/hosts/{id}/follow` → `{host_id, status}`.
- A selection handed to an agent is a FIXED id list resolved at the click (`ProposeTestsDialog.tsx`: ≤ 200, never truncated; `agentInstruction.proposeTests` / `proposeTestForObservation` / `proposeTestsForIssue` / `collectEvidence` — only with no listed host outside a declared scope; names no tool).

## Scope reads and uploads (`agent_recon.py`, tag `agent-scope`)

- No agent route, tool or answer names a scope: `/agent/scope/subnets`, `/agent/scope/domains`, four target files, `GET /agent/assist/scope` (MCP `assist_get_scope`: 100 each, totals, `*_truncated`); `scope_list_subnets` / `scope_list_domains` take no `scope_id`. All use `default_scope.project_scope`, never creating; nothing declared = 200 empty. `/agent/assist/context`: `totals.subnet_count` / `domain_count`.
- `scope_targets_service` bounds target files by subnet membership, own project only (`test_scope_targets.py`); auditor floor. `named-targets.ndjson`: per in-scope name, its addresses and each one's subnet-scope flag; a name never puts an address or other name in scope.
- `web-targets.txt` (`web_targets_from_hosts`): an HTTP service name (https if TLS-wrapped or so named); the web-port table only for an unidentified port (no name, "unknown", nmap `method="table"` — `Port.service_method`). Never TLS alone (imaps) or a number under a non-HTTP service (ssh on 443). IPv6 bracketed.
- `live-hosts.txt` reads the address column, never `iter_scope_hosts`. `POST /agent/uploads` (poll `GET /agent/uploads/{job_id}`, own session); batches keyed `(agent_session_id, label)`.
- **An agent's own findings, or a tool with no parser, enter through the BlueStick observation format** (`ingestion.md`; `observations_jsonl`) uploaded like any file, or as JSON through `POST /agent/observations` (MCP `record_observations`, `agent_recon.record_observations`: the objects become a `.jsonl` `UploadFile` handed to `ingestion_service.create_job` with `format_override` — never a second parser or writer; the duplicate guard, batch and job are the upload's; in the role matrix as a project write) — scanner observations, direct and attributed to the session (not conclusions, so not proposals). The guide's "Uploading" section and the session prompt say how and that only what was established is written (absent = not stated); `source_statements` declares EXTERNAL may state anything. Agent rows carry `exploitable` tri-state, `cvss_version`, severity `unknown` (`AssistFinding`; the guide's `/vulnerabilities` row).
- Tested evidence with `observed_ip` on a name-aimed test writes DNS `TESTED` keyed by `evidence_record_id` via `dns_name_service.record_observation`. Anyone may work another session's test (it keeps its proposer; evidence names the runner).

## MCP

- `POST /api/v1/mcp` (`mcp_assist.py` transport, `mcp_tools.py` registry); `tools/call` loops in-process into the real `/agent/*` route with the caller's key. One `bluestick` entry; `tools/list` unfiltered (`mcp_tools.tool_groups` only groups). Bulk file endpoints stay curl.
- A tool authors only `description`, `method`, `path`, `params` (a description or a NARROWING), `hidden`, `defaults`, write flags, `path_alternatives`. Everything else comes from OpenAPI (`derive_tool`, warmed by `main._warm_mcp_registry` or first use); a new route parameter appears unless `hidden`. `test_mcp_enum_contract.py`: never wider than the endpoint.
- `read_agent_guide`: no `part` / `section` = whole; `section=contents` lists `## ` sections; `section=<name>` one (`agents_guide_service.guide_sections`).
- After a bare 401 some clients probe `/.well-known/oauth-*` and `POST /register`: 404 (never advertise discovery) answering `agent_policy.OAUTH_NOT_USED` in the OAuth error shape (`main.oauth_is_not_used`; nginx `= /register`) (`test_well_known.py::test_a_client_that_goes_looking_for_oauth_is_told_what_to_do`, `proxyHeaders.test.ts`).

## Safety rules and claims

- `agent_policy.SAFETY_RULES` are unenforceable (the client sandbox is the boundary); the server adds the record and the read-back.
- `GET /.well-known/networkmapper.json` `safety_properties` separates enforced (`server_executes_commands: false`, `agent_authority: operator_project_role`, `agent_key_binding: project_session`, `audit_trail_retention_days` — never "persistent", rows purge after `AGENT_API_CALL_RETENTION_DAYS`) from not (`command_approval_enforced_by: agent_and_client_sandbox`). `test_well_known.py` pins every claim.

## Ask-first: scanner credentials, a truncated upload

- Scanner credentials are in NOTHING an agent reads by default (`build_session_instructions` takes no `integrations`). `GET /agent/assist/scanner-integrations` (MCP `list_scanner_integrations`, VIEWER) serializes active ones through `integration_service.describe_for_agent` only — never a secret or a flag from one.
- ONE source each, rendered into both doors: `agent_policy.render_scanner_integrations_rule(over_mcp=)` (ask, request one scanner's only, keep them out of what the agent writes; never which scanner or how) with `SCANNER_ASK_FIRST_REFUSAL`, `SCANNER_LIST_NOTE`, `SCANNER_CREDENTIALS_NOTE` on `SCANNER_CONSENT_MEANING`; `agent_policy.render_truncated_upload_rule(over_mcp=)`, `TRUNCATED_UPLOAD_ASK_FIRST` on `TRUNCATED_UPLOAD_CONSENT_MEANING`. Tool descriptions repeat them (the one sanctioned restatement).
- `POST /agent/scanner-integrations/{integration_id}/credentials` (MCP `request_scanner_credentials`; any session, any active integration, else 404) and `POST /agent/uploads/{job_id}/keep-truncated` (MCP `keep_truncated_upload`; own session's `failure_kind: truncated_file` job, else 404 / 409) take `{"operator_agreed": true}` (`StrictBool`, default false → the 422 saying what to ask) and declare NOTHING (write default, analyst+; never session-metadata). No body has `keep_truncated`.
- `integration_service.share_credentials_with_agent` is the ONE decrypt for an agent, staging `scanner_credentials_shared` (no secret) with `commit=False`; `staged_import_service.keep_truncated_for_agent` stages `truncated_upload_kept` (no content) and `queue_job` commits both. `agent_api_calls` keeps the answer's SIZE only, never a body (`test_the_api_call_log_has_nowhere_to_keep_an_answer`).
- That the operator said yes is NOT enforced: never call it approved, enforced, verified or gated; no `safety_properties` claim. `tests/test_scanner_integrations_agent.py`, `tests/test_truncated_upload_agent.py`, `test_agent_safety_policy.py::test_the_scanner_rule_asks_first_and_claims_nothing_the_server_cannot_back`.

## Attribution and the call log

- `generated_by_tool`: the MCP `initialize` `clientInfo` (`record_mcp_client`, only for a key the gate would accept: `deps.live_agent_session_id` / `agent_key_may_act`), else the first `User-Agent` (`note_agent_harness`; loopback `python-httpx` ignored). `generated_by_model`: optional `agent_model` on the host-test, evidence, proposal and session-end writes (`note_agent_model`). Work snapshots both at creation (`proposal_service.Attribution`; `test_agent_attribution.py`).
- Middleware (`agent_api_log_service.py`) writes every authenticated `/agent/*` call to `agent_api_calls` AFTER the response, fresh session; parses `host_ids` / `target_ips`. `agent_api_calls.agent_id` SET NULL, never CASCADE; the check needs a project or error class (`test_user_delete_keeps_agent_record.py`).
- `/agent/remediation` calls keep no body or query values (`agent_api_log_service.PRIVATE_CONTENT_PREFIXES`, at write and read): the log is every member's, remediation the auditor's (`test_review_2026_10_10_call_log_remediation.py`).
- An unmatched `/agent/` path is a 404 with `hint` / `guide` / `feedback`, recorded (`path_template` NULL = "(unmatched route)" in `collect-logs.sh`). Read: `GET /projects/{id}/agent-sessions/{sid}/api-activity`, `AgentActivityLog`.

## Tool registry — a catalogue, never a permission

- For PEOPLE (`/tool-reference`). Agents may read it (`list_tools`, `GET /references/tools`); no agent text sources or cites it; no product-written "use this tool for that".
- `tool_registry` `status`: `reference` / `suggested` / `rejected` (no `approved`, `test_tool_registry.py`); `ingestible` is independent.
- A parsed tool's row carries `run_command` / `run_note`, pinned to the output contract (`test_tool_command_consistency.py`), not `PATCH`-editable; note-only rows where no honest one-liner exists (gvm; `b3d9f1a7c5e2` fills only empty rows).
- Seeding (`app/data/tool_registry_seed.json`) is additive: fix a shipped row by migration (like `c9a4e70b5d18`) plus the seed.
- Anonymous `GET /references/tools`: `reference` rows, no `suggested_rationale`.
- `suggest_tool` adds; admins curate (`PATCH /references/tools/{name}`); accepting needs a person's `description`, not `SUGGESTED_DESCRIPTION_PREFIX` (`tool_registry_service.accept_description_problem`, 422; `test_tool_registry.py`).

## The agent contract and prompt version

- `documentation/AGENT_GUIDE.md` (mounted `/app/AGENT_GUIDE.md`, read only by `agents_guide_service.read_agent_guide()` (hazard — removable: a source scan that no other module under `app/` opens `AGENT_GUIDE.md`)), sliced by `<!-- agents:section tags="..." -->` into `GET /api/v1/agents-guide?part=testing|reconnaissance|assist|remediation` (`agents_guide_service.GUIDE_PARTS`): reading aids, not kinds or permissions. None / `project` = whole; else 422 (`recon`, `plan`… included).
- `part` is the only name: MCP `workflow` is `-32602`, HTTP selects nothing (`test_the_guide_part_argument_is_part_and_nothing_else`); `agent_identity` has no workflow. The guide is by task, never lettered workflows.
- Never the product contract in the root `AGENTS.md` (coding agents load it as repo rules): that file IS the repo rules for contributors, and it says so in its first lines.
- `PROMPT_VERSION` (`app/services/agent_prompt_service.py`) stands alone (`test_the_prompt_version_is_a_dotted_number_and_nothing_beside_it`).
- Prompt = identity + skeleton (verification, certificate, safety and read-back, key expiry, long commands, feedback, ending), guide = how-to; each rule ONCE. No version history or tool/flag advice in agent text.
- The pasted prompt and `mcp_assist._server_instructions` (which stands alone) render the same `agent_policy` functions (`render_safety_rules`, `render_read_back`, `render_key_file`, `render_key_expiry_guidance`, `render_scanner_integrations_rule`; `test_agent_safety_policy.py`) — never a hand paraphrase. Verification asks for `agent_identity` only; both ask for `submit_feedback` at friction.

## Frontend

- The sessions list is `/agent-activity`; a session is `/agent-sessions/{id}` (bare `/agent-sessions` and `/assist-sessions/…` are not-found). Hub "Agents" (id `agents`) at `/agents`.
- `StartAssistDialog`: a live session makes "Copy task" primary; nothing is asked first, no purpose sent (`test_the_session_prompt_carries_no_stated_purpose`); the `purpose` column / optional start field stay for `scripts/seed_acceptance_fixtures.py`.

## Decided — do not ask again

- No idle key expiry (`AGENT_SESSION_IDLE_HOURS` declined): 24 h TTL, renewable within the 7-day session.
- No old MCP clients: no retired or accepted-and-ignored arguments.
- Subnet description / site is a proposal; a remediation "Reported fixed" moving its host to Retest pending is direct.
- No agent read of release notes unless asked (then wrap `release_notes.releases()`, analyst-somewhere).
- Also decided, as built above: `watching`, `?skip=`, no agent scope, kept calls, the catalogue, `part`, no purpose box, ask-first uploads.
- The operator's agent makes scanner connections (the design); no server-side scanner proxy; a server-side external call is a rare per-case approval via `safe_request`, never toward targets.
- No masking / reveal toggles (keys, found credentials).

## Removed on purpose

- `/agent/scopes/{scope_id}/…`, `GET /agent/scopes`, `GET /agent/assist/scopes` / `assist_list_scopes`, `scope_name`, `agent_api_calls.scope_id`; the legacy assist id, `assist_sessions` (`b8e2a5c7d1f3`); `trust-cert.sh` (the local root CA).
- Allowlist, required order, approval / sanity gates, phases, recon runs, test plans, execution runs, call-log `entry_ids` (`f5c2a0d7b4e6`); `POST /agent/session/environment` / `record_environment`, `recon_planning_service`, host readiness, `scripts/preflight.sh`.
- `deps.AGENT_READ_ROLE_OVERRIDES` / `AGENT_SESSION_METADATA_WRITES`, `agent_common.apply_agent_host_filters`, `auto_params` / `retired_params`, `?workflow=`, `PROMPT_VERSION_HISTORY` / `agent_prompt_history.py`, the note beside `PROMPT_VERSION`; feedback `source` (`a6d3b1e8c5f7`).
- Prompt scanner credentials (`agent_prompt_service._integration_block`, `integration_service.active_integrations_for_prompt` / `decrypt_integration`) and their `prompt_sanitizer` rule (agent-key and token rules stay).
- `GET /agent/project`, `/agent/dashboard`, `/agent/hosts`, `/agent/hosts/{id}`, `/agent/hosts/{id}/notes`, `/agent/scans` (`test_review_2026_10_23b_agents.py`).
- Claims `all_commands_require_user_approval`, `no_autonomous_execution`, `agent_keys_scope_bound`, `plan_execution_requires_human_approval`.
