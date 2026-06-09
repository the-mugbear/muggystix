# BlueStick API Guide

> **Last verified against:** backend 2.115.0 / frontend 5.25.1 (2026-06-07)

Base path: `/api/v1`

BlueStick exposes two parallel REST surfaces under the same versioned base path:

1. **JWT user API** — everything humans touch. Login, browse, upload, approve, export, manage. Nested under `/projects/{project_id}/...` for all data-bearing routes (scans, hosts, test plans, etc.).
2. **Agent API (`/agent/*`)** — terminal-side agents. Authenticates with `X-API-Key: nm_agent_...` headers minted on a per-project, per-test-plan basis. Separate dependency chain; fully isolated from the JWT surface.

The live interactive spec is always at `GET /docs` (Swagger UI) and `GET /openapi.json`. Use this guide for architectural context and high-value shape references; use OpenAPI for field-level truth.

---

## 1. Authentication

### 1.1 JWT user tokens

```
POST /api/v1/auth/login
Content-Type: application/json

{"username": "admin", "password": "admin"}
```

Returns a JWT in the response body plus a bearer token expected on every subsequent request. Sessions are tracked server-side in `user_sessions` — logout revokes the JWT so a stolen token stops working at the next request. The default admin account (`admin` / `admin`) is seeded on first boot with `must_change_password=True`; every authenticated endpoint except `/auth/*` is gated behind `require_password_changed` until the user rotates.

**Brute-force defenses (v2.41.0).** Three layers stack:

- *Per-account 5-strike lockout* — five consecutive failed attempts on the same account row trigger a 30-minute `locked_until`.
- *Per-username window throttle* — ≥10 failed attempts on the same username in any 15-minute window (across all source IPs) → HTTP 429. Defeats the "rotate IPs to defeat the per-account lockout" attack.
- *Per-IP window throttle* — ≥20 failed attempts from the same source IP in any 15-minute window (across all usernames) → HTTP 429. Defeats credential stuffing.

The throttle reads `audit_logs` (so the 429 response body says "Try again in 15 minutes"). Timing of the bcrypt path is equalized between "unknown username", "inactive user", "locked account", and "valid user, wrong password" — all four branches pay the same bcrypt cost via a precomputed dummy hash. Username enumeration via login timing is closed.

Expired user sessions are reaped hourly by a background task in the API process; `GET /auth/sessions` only returns rows where `expires_at > now() AND revoked_at IS NULL`.

Roles: `admin`, `analyst`, `auditor`, `viewer`. Project membership adds a per-project role that can be the same as or stricter than the global role.

### 1.2 Agent API keys

Agents get a short-lived key via one of these flows:

- **Recon** — user clicks "Start Agentic Recon" on the Scopes page → `POST /api/v1/projects/{id}/scopes/{scope_id}/recon/start` mints a scope-bound `reconnaissance` key.
- **Plan generation** — user clicks "Generate with AI" on the Test Plans page → `POST /api/v1/projects/{id}/test-plans/generate` mints a `plan_generation`-scoped key.
- **Execution** — user approves a plan and clicks "Execute with AI" → `POST /api/v1/projects/{id}/test-plans/{plan_id}/execute` mints a plan-scoped `execution` key bound to that exact plan.
- **Assist** — user starts a read-only Q&A session → `POST /api/v1/projects/{id}/assist/start` mints an `assist`-scoped key (v2.64.0). Read-only; rejected on plan/recon/execution endpoints.
- **Rotate** — when a per-plan key expires (24h TTL) but the user wants to continue, `POST /api/v1/projects/{id}/test-plans/{plan_id}/rotate-key` mints a fresh key and revokes the prior ones (v2.19.0).

Keys are:
- **Hashed at rest** in `agent_api_keys` (the plaintext is returned to the user **exactly once**, never stored).
- **Time-bound** — default 24h TTL, controlled by `settings.AGENT_API_KEY_TTL_HOURS`.
- **Scoped** — each key declares its workflow (`plan_generation`, `execution`, `reconnaissance`, `assist`) and, for execution keys, a specific `test_plan_id`. Scope-mismatched calls return 403.

Every `/api/v1/agent/*` request must include:

```
X-API-Key: nm_agent_<plaintext>
```

The `require_plan_scope` dependency validates the key, loads the bound agent + plan, and makes them available to the handler. `deny_scoped_keys` is the inverse — used on endpoints that must not accept plan-scoped keys (e.g. listing every plan in the project).

### 1.3 Sessions

- `GET /auth/sessions` — list active sessions for the current user.
- `DELETE /auth/sessions/{session_id}` — revoke a specific session (logs the user out on that device).
- `POST /auth/logout` — revoke the current session.
- `POST /auth/change-password` — required on first login when `must_change_password=True`.
- `GET /auth/profile` — returns the current user's profile including the `must_change_password` flag.

---

## 2. Route map

```
/api/v1
├── /auth                     # login, logout, profile, sessions, change-password
├── /users                    # admin CRUD
│   └── /users/directory      # minimal picker — open to any authenticated user
├── /audit                    # admin: audit log browsing
├── /projects                 # project list, create, update, delete
│   ├── /{id}/members         # project membership (admin or project-admin)
│   └── /{id}/...             # PROJECT-SCOPED SUBTREE — see §4
├── /portfolio                # cross-project dashboard
├── /activity                 # cross-project note/activity feed
├── /notifications            # read/unread, mark-seen
├── /llm-providers            # per-user LLM credentials + /{id}/complete
├── /integrations             # per-user scanner tool credentials
├── /feedback                 # admin triage of agent feedback rows
├── /references               # SBOM, tool catalog (live, public reads)
└── /agent                    # AGENT API — see §5
```

---

## 3. Global (non-project) endpoints

### 3.1 Authentication

| Method | Path | Notes |
|---|---|---|
| POST | `/auth/login` | Body: `{username, password}`. Returns JWT + profile. |
| POST | `/auth/logout` | Revokes the current session. |
| POST | `/auth/change-password` | Required when `must_change_password=True`. |
| GET | `/auth/profile` | Current user + `must_change_password` flag. |
| GET | `/auth/sessions` | List active sessions for current user. |
| DELETE | `/auth/sessions/{session_id}` | Revoke a specific session. |

### 3.2 Users (admin)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/users/` | admin | List all users (admin fields). |
| GET | `/users/{user_id}` | admin | User detail. |
| POST | `/users/` | admin | Create user. |
| PUT | `/users/{user_id}` | admin | Update role, active state, etc. |
| DELETE | `/users/{user_id}` | admin | Delete (or deactivate). |
| POST | `/users/{user_id}/reset-password` | admin | Force-reset another user's password. |
| PUT | `/users/profile` | self | Self-service profile update. |
| GET | `/users/directory` | any auth | **v2.10.0** — minimal `{id, username, full_name}` list of active users. Used by the Add Member picker. Open to any authenticated user so project admins without global admin can populate dropdowns. |

### 3.3 Projects

| Method | Path | Notes |
|---|---|---|
| GET | `/projects/` | List projects the current user can see. |
| POST | `/projects/` | Create project. |
| GET | `/projects/{id}` | Project detail. |
| PUT | `/projects/{id}` | Update metadata. |
| DELETE | `/projects/{id}` | Delete (cascades to owned data). |
| GET | `/projects/{id}/members` | List membership. |
| POST | `/projects/{id}/members` | Add member — body: `{user_id: int, role: str}`. |
| PUT | `/projects/{id}/members/{user_id}` | Change role. |
| DELETE | `/projects/{id}/members/{user_id}` | Remove member. |

`MembershipCreate` takes **`user_id`**, not `username`. Clients should use `/users/directory` to pick the ID.

### 3.4 Notifications

| Method | Path | Notes |
|---|---|---|
| GET | `/notifications/` | Supports `?unread_only=true&limit=20`. |
| GET | `/notifications/unread-count` | Lightweight polling endpoint. |
| POST | `/notifications/{id}/mark-read` | |
| POST | `/notifications/mark-all-read` | |

### 3.5 Portfolio

| Method | Path | Notes |
|---|---|---|
| GET | `/portfolio/dashboard` | Cross-project summary (host count, open criticals, recent scans, attention items) scoped to projects the user can see. |

### 3.6 LLM providers (self-service)

Per-user credentials for OpenAI, Anthropic, Azure OpenAI, Ollama, and OpenAI-compatible endpoints. API keys are Fernet-encrypted at rest; never returned in responses.

| Method | Path | Notes |
|---|---|---|
| GET | `/llm-providers/` | List providers owned by current user. |
| POST | `/llm-providers/` | Create provider. Body: `{name, provider_type, base_url?, api_key?, model_id?, is_default?}`. `base_url` validated via SSRF check. |
| PATCH | `/llm-providers/{id}` | Update. Setting `api_key=null` does not clear it; pass `clear_api_key=true` to remove. |
| DELETE | `/llm-providers/{id}` | |
| POST | `/llm-providers/{id}/test` | Non-destructive connectivity test via the provider's model-list endpoint (or a one-token completion for Anthropic). Uses `safe_http_client` with DNS re-validation. |
| GET | `/llm-providers/default` | Returns the user's default provider (if any). |
| POST | `/llm-providers/{id}/complete` | Chat completion. Body: `{system?, messages, max_tokens?, temperature?}`. **Server-side prompt sanitization** runs before forwarding — see §8. |

### 3.7 Integrations (scanner credentials)

Per-user credentials for Nessus, OpenVAS, Nuclei, Burp, PDCP, and generic-API integrations. Dual-secret support (Nessus needs both Access Key and Secret Key). All secrets Fernet-encrypted.

| Method | Path | Notes |
|---|---|---|
| GET | `/integrations/` | List integrations visible to current user. Supports `?project_id=` to return project-scoped + user-global. |
| GET | `/integrations/{id}` | |
| POST | `/integrations/` | Create. `base_url` validated via SSRF check with the `is_integration_private_allowed()` carve-out (currently Ollama only). |
| PATCH | `/integrations/{id}` | Update. `clear_secret` / `clear_secret2` flags remove encrypted material. |
| DELETE | `/integrations/{id}` | |

The agent-facing `/agent/integrations` endpoint was **removed** in v2.9.5 (audit finding C#2). If a future agent workflow needs programmatic scanner credentials, it must come back as a capability-scoped endpoint with per-plan grants and audit logging. Today, recon prompts inline the credentials the agent needs directly from `agent_prompt_service._integration_block`.

### 3.8 Feedback (admin triage)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/feedback/` | admin | List all agent feedback rows. Supports `?status=new|triaged|reviewed&source=plan_generation|execution|reconnaissance`. |
| GET | `/feedback/{id}` | admin | Feedback detail with `api_critiques`, `tool_suggestions`, `friction_notes`, `agent_metrics`. |
| GET | `/feedback/stats` | admin | Aggregate counts by source, status, average `overall_rating`. |
| PATCH | `/feedback/{id}` | admin | Update status + `reviewer_notes`. |

Feedback **ingest** (the agent-facing path) lives under `/agent/feedback` — see §5.

### 3.9 References (live, public reads)

| Method | Path | Notes |
|---|---|---|
| GET | `/references/` | Index of the reference endpoints below, with descriptions. |
| GET | `/references/sbom` | **v2.20.0** — Software Bill of Materials reflecting the deployed build's resolved dependency tree. Reads `requirements.txt` + `frontend/package-lock.json`. Memoised by manifest mtimes + `app_version` (so a release bump invalidates the cache even if dependencies didn't change). Classifies each component as direct (listed in `requirements.txt` / `package.json` root) or transitive. |
| GET | `/references/preflight-script` | Returns `scripts/preflight.sh` (text/x-shellscript) — the recon-workflow environment probe agents run to check which tools the host has. Supports `--json`, `--strict`, `--help`. |
| GET | `/references/tool-readiness` | **Authenticated** — the agent tool catalog checked against the current user's most recent environment probe: per-tool `installed`/`missing`/`warn`/`unknown` status + install hints. Returns `has_probe: false` (all `unknown`) when the user hasn't probed yet. Powers the ToolReference page's Host Readiness panel. |

---

## 4. Project-scoped endpoints (`/api/v1/projects/{project_id}/...`)

Every endpoint in this subtree requires JWT auth AND project membership with the appropriate role. The project is loaded and validated by `get_current_project`; cross-project reads return 404 for anything not visible to the user.

### 4.1 Upload & ingestion jobs

| Method | Path | Notes |
|---|---|---|
| POST | `/upload/` | Multipart file upload. Returns a queued `IngestionJob` — poll for results. Magic-byte validation on the first 1 KB rejects disguised binaries. |
| GET | `/upload/jobs` | List recent jobs. Supports `?skip=0&limit=25&status=failed`. Returns a plain array — v2.9.8 briefly wrapped this in an envelope, reverted because the Scans page consumes it directly. |
| GET | `/upload/jobs/{job_id}` | Job detail. |
| POST | `/upload/jobs/{job_id}/cancel` | Cancel a queued or processing job. Only the owner or a global admin. |

The `IngestionJobSchema` includes `retry_count` and `last_error` for dead-letter surfacing (UI view deferred to v2.11.0).

### 4.2 Scans

| Method | Path | Notes |
|---|---|---|
| GET | `/scans/` | List scans. Supports pagination. |
| GET | `/scans/{scan_id}` | Scan detail. |
| DELETE | `/scans/{scan_id}` | Admin. Deletes scan + history rows; hosts seen in other scans are preserved. |
| GET | `/scans/{scan_id}/hosts/count` | Host count only (lightweight for list views). |
| GET | `/scans/{scan_id}/command-explanation` | Human-readable explanation of the scan's command line. |
| GET | `/scans/{scan_id}/eyewitness` | EyeWitness screenshot entries for a scan. |
| GET | `/scans/{scan_id}/eyewitness/count` | Count only. |
| GET | `/scans/out-of-scope` | Hosts parsed but not matching any scope. |
| DELETE | `/scans/out-of-scope` | Admin. Bulk removal of out-of-scope records. |
| GET | `/scans/{scan_id}/out-of-scope` | Per-scan OOS hosts. |
| GET | `/scans/{scan_id}/out-of-scope/count` | |

### 4.3 Hosts

| Method | Path | Notes |
|---|---|---|
| GET | `/hosts/` | Deduplicated hosts with rich filter support: `state`, `ports`, `services`, `subnets`, `scan_ids`, `os_filter`, `min_risk_score`, `critical/high/medium/low_vuln_min`, `has_vuln`, `follow_status`, `search`. Supports `sort_by`, `sort_order`, `skip`, `limit`, `include_total`. |
| GET | `/hosts/{host_id}` | Host detail with ports, scripts, vulnerabilities, follow state, notes, discoveries. |
| GET | `/hosts/{host_id}/conflicts` | Confidence + conflict metadata across scan history. |
| GET | `/hosts/scan/{scan_id}` | Hosts seen in a specific scan. |
| GET | `/hosts/filters/data` | Filter metadata (ports, services, OS, subnets, scans). Supports cascading — pass active filter params to scope the returned metadata. |
| GET | `/hosts/tool-ready/{format}` | Export filtered host list as a tool-ready target file (nmap list, masscan range, newline-delimited IPs, etc.). |
| GET | `/hosts/views` | Saved filter/view state for the current user. |

#### Host follow state

| Method | Path | Notes |
|---|---|---|
| POST | `/hosts/{host_id}/follow` | Set status: `watching`, `in_review`, `reviewed`. Per-user. |
| DELETE | `/hosts/{host_id}/follow` | Unfollow. |
| POST | `/hosts/{host_id}/view` | Update `last_viewed_at` (only if a follow record exists). |

#### Host notes

| Method | Path | Notes |
|---|---|---|
| GET | `/hosts/{host_id}/notes` | List notes on a host. |
| POST | `/hosts/{host_id}/notes` | Create. Body: `{body, status?, parent_id?}`. `body` is capped at 16 KB. Returns `mention_warning` if mention notification dispatch failed. |
| PATCH | `/hosts/{host_id}/notes/{note_id}` | Author edit only. |
| DELETE | `/hosts/{host_id}/notes/{note_id}` | Author delete only. |
| GET | `/hosts/notes/activity` | Activity-grouped feed for the Activity page with host enrichment. |
| GET | `/hosts/notes/unread-count` | Count of notes updated since `last_viewed_at`. |
| POST | `/hosts/notes/mark-seen` | Mark all activity as seen. |

### 4.4 Dashboard

| Method | Path | Notes |
|---|---|---|
| GET | `/dashboard/stats` | Aggregated host/port/subnet/vuln counts + recent scans + note activity. |
| GET | `/dashboard/port-stats` | Port frequency breakdown. |
| GET | `/dashboard/os-stats` | OS distribution. |
| GET | `/dashboard/risk-insights` | Attention queue, vulnerability hotspots, risk exposure. |
| GET | `/dashboard/my-attention?limit=10` | Per-user attention list. |
| GET | `/dashboard/my-tasks?limit=15` | Per-user open tasks. |
| GET | `/dashboard/new-scans-since` | Recent scans since user's last visit. |

### 4.5 Risk

| Method | Path | Notes |
|---|---|---|
| POST | `/risk/hosts/{host_id}/assess-risk` | Trigger fresh risk assessment. |
| GET | `/risk/hosts/{host_id}/risk-assessment` | Current risk assessment. |
| DELETE | `/risk/hosts/{host_id}/risk-assessment` | Clear the assessment. |
| GET | `/risk/hosts/{host_id}/vulnerabilities` | Host's vulnerability list. |
| GET | `/risk/hosts/{host_id}/security-findings` | Non-vuln security findings. |
| GET | `/risk/hosts/high-risk` | Hosts with highest risk scores. |
| GET | `/risk/hosts/risk-summary` | Aggregate risk stats. |
| GET | `/risk/vulnerability-stats` | Vuln counts by severity. |

### 4.6 Scopes & subnets

| Method | Path | Notes |
|---|---|---|
| GET | `/scopes/default` | The project's default scope. |
| GET | `/scopes/` | List scopes. |
| POST | `/scopes/` | Create scope. |
| GET | `/scopes/{scope_id}` | Scope detail. |
| DELETE | `/scopes/{scope_id}` | |
| POST | `/scopes/upload-subnets` | Analyst+. Upload a CIDR list file. 2 MB cap, 10 000 entry cap. |
| POST | `/scopes/correlate-all` | Analyst+. Re-run host ↔ subnet correlation for the project. |
| GET | `/scopes/coverage?limit=25` | Scope coverage rollups. |
| GET | `/scopes/{scope_id}/host-mappings` | Host-to-subnet mappings for a scope. |
| POST | `/scopes/{scope_id}/subnets` | Add CIDR entries. Each CIDR capped at 64 chars. |
| PATCH | `/scopes/{scope_id}/subnets/{subnet_id}` | Update a CIDR or description. |
| DELETE | `/scopes/{scope_id}/subnets/{subnet_id}` | Remove. |

### 4.7 Export & reports

| Method | Path | Notes |
|---|---|---|
| GET | `/export/scope/{scope_id}?format_type=csv|html|json|pdf` | Hosts + ports within a scope. |
| GET | `/export/scan/{scan_id}?format_type=...` | Scan-level export. |
| GET | `/export/out-of-scope` | Out-of-scope host export. |
| GET | `/reports/hosts/csv\|html\|json` | Host listing exports. |

### 4.8 DNS

| Method | Path | Notes |
|---|---|---|
| GET | `/dns/records` | Stored DNS records. |
| POST | `/dns/lookup/{hostname}` | Live lookup + store. |
| POST | `/dns/zone-transfer/{domain}` | Analyst+. Attempt AXFR. |

### 4.9 Parse errors

| Method | Path | Notes |
|---|---|---|
| GET | `/parse-errors/` | Filterable list. |
| GET | `/parse-errors/{error_id}` | Detail with `user_message` + full file preview. |
| GET | `/parse-errors/stats/summary` | Aggregate counts by type, status, file. |
| PUT | `/parse-errors/{error_id}/status` | Update status (acknowledged, resolved). |
| DELETE | `/parse-errors/{error_id}` | Analyst+. |

### 4.10 Agents (project-scoped)

| Method | Path | Notes |
|---|---|---|
| GET | `/agents/` | List agents for this project. |
| POST | `/agents/` | Create an agent. Mints a one-time API key and returns the plaintext **exactly once**. |
| GET | `/agents/{agent_id}` | Agent detail. |
| PATCH | `/agents/{agent_id}` | Update metadata. |
| DELETE | `/agents/{agent_id}` | Deactivate agent and revoke all keys. |
| POST | `/agents/{agent_id}/rotate-key` | Mint a new key and revoke the prior one. |

### 4.11 Test plans

| Method | Path | Notes |
|---|---|---|
| POST | `/test-plans/` | Create an empty plan. Body: `{title, description?}`. |
| POST | `/test-plans/generate` | Generate a reconnaissance plan from a scope via the agent prompt builder. Mints a plan-generation-scoped API key and returns instructions + key (plaintext, shown once). |
| GET | `/test-plans/` | List plans visible to the user. Filterable by status. |
| GET | `/test-plans/{plan_id}` | Plan detail with entries. |
| POST | `/test-plans/{plan_id}/approve` | Transition draft → approved. Analyst+. |
| POST | `/test-plans/{plan_id}/reject` | Reject with reason. |
| PATCH | `/test-plans/{plan_id}` | Update title/description. |
| PATCH | `/test-plans/{plan_id}/entries/{entry_id}` | Update an entry (status, rationale, findings, notes). Supports `expected_updated_at` for optimistic locking. |
| GET | `/test-plans/{plan_id}/progress` | Progress rollup. |
| GET | `/test-plans/{plan_id}/history` | Audit trail of plan changes. |
| DELETE | `/test-plans/{plan_id}` | Delete. Only allowed if no execution sessions exist. |
| POST | `/test-plans/{plan_id}/execute` | Mint a plan-scoped execution API key and return instructions. |
| POST | `/test-plans/{plan_id}/rotate-key` | **v2.19.0** — Mint a fresh per-plan agent key and revoke the prior ones. For the case where the original 24h key expired but the user wants to keep working on the same plan. |
| GET | `/test-plans/{plan_id}/report?format_type=html\|pdf\|json\|csv` | Download an execution report from the latest (or a specified) session. `raw_output` trimmed to 16 KB per test result. |
| POST | `/test-plans/{plan_id}/bundle` | Export a plan as an offline zip bundle. Creates an `ExecutionSession` in exported mode and transitions the plan to `in_progress`. Returns the zip bytes + `bundle_id` + `execution_session_id`. |
| POST | `/test-plans/{plan_id}/import-results` | Import a results file from a terminal-run agent. JSON-depth-guarded, idempotent, cross-plan-safe. |
| GET | `/test-plans/{plan_id}/api-activity` | **v2.24.0** — JWT-authenticated read of the agent API call log scoped to this plan. Filters: `method`, `status_min`, `status_max`, `host_id`, `target_ip`, `since`, `until`, `limit`, `offset`. Returns `{total, items[]}`. See §6.7. |

### 4.12 Recon sessions

| Method | Path | Notes |
|---|---|---|
| POST | `/scopes/{scope_id}/recon/start` | Mint a scope-bound `reconnaissance` API key and create an active `ReconSession`. Returns instructions + plaintext key shown exactly once. |
| GET | `/recon-sessions/{recon_session_id}/api-activity` | **v2.24.0** — JWT-authenticated read of the agent API call log scoped to this recon session. Same filter shape as the plan variant above. |

---

## 5. Agent API (`/api/v1/agent/*`) — X-API-Key auth

All endpoints in this section require `X-API-Key: nm_agent_<plaintext>` in the request headers. Project scope is implicit from the key. Plan-scoped keys are additionally restricted to their bound `plan_id`.

### 5.1 Project context (global-scope keys only)

| Method | Path | Notes |
|---|---|---|
| GET | `/agent/project` | Project metadata (name, description, status). |
| GET | `/agent/dashboard` | Stats summary for the bound project. |
| GET | `/agent/hosts` | Paginated host list. Supports `after_host_id`, `limit`, filters. |
| GET | `/agent/hosts/{host_id}` | Host detail with ports, services, vulns. |
| GET | `/agent/scans` | Scan list. |
| GET | `/agent/scopes` | Scope list with subnet counts. |
| POST | `/agent/hosts/{host_id}/notes` | Create a host note from the agent's identity. |
| POST | `/agent/hosts/{host_id}/follow` | Follow a host. |

### 5.2 Test plan CRUD

| Method | Path | Notes |
|---|---|---|
| POST | `/agent/test-plans` | Create a new plan under this agent. |
| GET | `/agent/test-plans` | List agent-owned plans. |
| GET | `/agent/test-plans/{plan_id}` | Plan detail. |
| PATCH | `/agent/test-plans/{plan_id}` | Update title/description. |

### 5.3 Plan generation workflow

| Method | Path | Notes |
|---|---|---|
| GET | `/agent/test-plans/{plan_id}/context` | Paginated candidate hosts. See §6.1 for the response shape. |
| POST | `/agent/test-plans/{plan_id}/entries` | Batch-create entries. Wraps response as `{"entries": [...]}`. Up to 500 entries per call. |
| PATCH | `/agent/test-plans/{plan_id}/entries/{entry_id}` | Update an entry. Supports optimistic locking via `expected_updated_at`. |
| GET | `/agent/test-plans/{plan_id}/validate` | **v2.10.0 split-coverage** response — see §6.2. |
| POST | `/agent/test-plans/{plan_id}/submit` | Submit for human approval. Transitions to `pending_review`. |

### 5.4 Execution workflow

| Method | Path | Notes |
|---|---|---|
| POST | `/agent/execution-sessions/{session_id}/environment` | **v2.23.0** — MANDATORY first call on a new session. Records the operator-host environment probe (OS, shell, PowerShell version + execution policy, real-vs-stub Python, WSL, tools on PATH). Re-POSTing replaces the probe. See §6.8. |
| GET | `/agent/test-plans/{plan_id}/execution-context` | Hosts + entries + tests with `{ip}` resolved. Response now carries an `environment` block echoing the probe so the agent translates intent → command for the right platform. |
| POST | `/agent/test-plans/{plan_id}/entries/{entry_id}/sanity-check` | Record a host sanity check result. |
| POST | `/agent/test-plans/{plan_id}/entries/{entry_id}/test-results` | Record test execution results. |
| POST | `/agent/test-plans/{plan_id}/entries/{entry_id}/complete` | **v2.22.0** — Refuses (400) if no passing `HostSanityCheck` AND no `override_reason` on the body. The override is intentional, audited, capped at 500 chars. |
| GET | `/agent/test-plans/{plan_id}/execution-progress` | Execution rollup. |

### 5.5 Recon workflow

| Method | Path | Notes |
|---|---|---|
| POST | `/agent/recon/sessions/{session_id}/environment` | **v2.23.0** — Same probe as execution but for recon sessions. |
| GET | `/agent/recon/context` | Scope CIDR list, scope-size analysis, recommended discovery sequence, known-host probe helper, tool catalog. Carries `environment` once probed. |
| POST | `/agent/recon/upload` | Multipart upload of scanner output. Stamped with the recon session ID. |
| GET | `/agent/recon/jobs/{job_id}` | Poll an ingestion job to completion. |
| GET | `/agent/recon/summary` | Per-host rollup of what's been discovered in this session. |
| POST | `/agent/recon/complete` | Mark the session complete. |

### 5.6 Feedback ingest

| Method | Path | Notes |
|---|---|---|
| POST | `/agent/feedback` | Record structured feedback at the end of a run. Body includes `source`, `prompt_version`, `overall_rating` (1–5), `api_critiques[]`, `tool_suggestions[]`, `friction_notes`, `agent_metrics{}`. Session and plan binding are inferred from the API key. |

### 5.7 Assist workflow (read-only Q&A — v2.64.0)

For "ask questions about this project" agents that shouldn't trigger the plan-approval ceremony. All endpoints require an `assist`-scoped key (`require_assist_scope`); plan/recon/execution keys are rejected here, and assist keys are rejected on the other agent surfaces.

| Method | Path | Notes |
|---|---|---|
| POST | `/agent/assist/sessions/{session_id}/environment` | Per-session environment probe, same shape as execution/recon. |
| GET | `/agent/assist/context` | Project + assist-session context. |
| GET | `/agent/assist/hosts` | Paginated, filterable host list (read-only). |
| GET | `/agent/assist/hosts/{host_id}` | Host detail with ports, services, vulns. |
| GET | `/agent/assist/scopes` | Scope list. |
| GET | `/agent/assist/scans` | Scan list. |
| GET | `/agent/assist/session` | Current assist-session metadata. |

The JWT side (operator) lives under `/projects/{id}/assist/*`: `POST /assist/start` opens a session and returns a fresh key + prompt (shown once); `POST /assist/sessions/{session_id}/end` revokes the key (session row kept for audit); `GET /assist/sessions` lists recent sessions.

---

## 6. Important response shapes

### 6.1 `GET /agent/test-plans/{plan_id}/context`

```json
{
  "plan": { "id": 42, "status": "draft", "title": "...", ... },
  "filter_criteria": null,
  "agent_name": "codex",
  "selection_policy": "Create entries for all hosts with critical or high vulnerabilities. Include hosts with medium vulnerabilities if they expose multiple services or high-value ports (SMB, RDP, databases). Skip hosts with zero open ports...",
  "summary": {
    "total_hosts": 412,
    "matching_filter": 412,
    "policy_match_count": 87,
    "candidates_reviewed": 412,
    "has_more": true,
    "next_after_host_id": 1843
  },
  "candidate_hosts": [
    {
      "id": 1842,
      "ip_address": "10.0.1.5",
      "hostname": "web01.example.com",
      "os_name": "Linux",
      "open_port_count": 6,
      "vuln_summary": {"critical": 1, "high": 3, "medium": 2, "low": 0},
      "top_vulnerabilities": [
        {"title": "Apache Struts RCE", "severity": "critical", "cve_id": "CVE-2017-5638"}
      ],
      "services": ["http", "https", "ssh"],
      "ports": [
        {"port": 22, "protocol": "tcp", "state": "open", "service": "ssh", "product": "OpenSSH", "version": "8.0"},
        {"port": 445, "protocol": "tcp", "state": "open", "service": null, "product": null, "version": null}
      ],
      "meets_policy": true,
      "inferred_service_hints": [
        {"port": 445, "protocol": "tcp", "inferred_service": "smb", "source": "port_number_heuristic"}
      ]
    }
  ]
}
```

**v2.10.0 field:** `inferred_service_hints` — populated only when an open high-value port has a null/unknown service. `ports[].service` remains authoritative when it's present.

### 6.2 `GET /agent/test-plans/{plan_id}/validate`

```json
{
  "plan_id": 42,
  "ready": true,
  "total_entries": 87,
  "by_priority": {"critical": 1, "high": 12, "medium": 48, "low": 26, "info": 0},
  "by_phase": {"reconnaissance": 12, "enumeration": 60, "exploitation": 15},
  "warnings": [],
  "coverage": {
    "entries_in_plan": 87,
    "policy_matching_remaining": 0,
    "non_policy_with_open_ports": 325,
    "eligible_hosts_remaining": 325,
    "coverage_pct": 21.1,
    "note": "Plan covers all 87 policy-matching host(s). 325 additional host(s) have open ports but don't meet the selection policy..."
  }
}
```

**v2.10.0 split-coverage:** read `policy_matching_remaining` (actionable — real missed scope) instead of the conflated `eligible_hosts_remaining` (retained for v2.9.x compatibility; equals the sum of the two buckets). A non-zero `non_policy_with_open_ports` is **normal** — those are hosts the agent correctly skipped per the selection rubric.

### 6.3 `POST /projects/{id}/test-plans/{plan_id}/execute` (response)

```json
{
  "plan_id": 42,
  "plan_title": "Q2 external recon",
  "plan_status": "in_progress",
  "api_key": "nm_agent_<plaintext shown once>",
  "instructions": "...<markdown block pasted into the user's terminal agent>..."
}
```

`api_key` is the plaintext, shown exactly once — the caller must display/copy it before dismissing the response. The hash lives in `agent_api_keys`; subsequent recovery is not possible.

### 6.4 `POST /projects/{id}/test-plans/{plan_id}/bundle` (response)

```
Content-Type: application/zip
Content-Disposition: attachment; filename="plan-42-bundle.zip"
X-Bundle-Id: <uuid>
X-Execution-Session-Id: 17
```

Body is the zip bytes. Zip contents:

```
manifest.json       bundle_id, plan_id, execution_session_id, prompt_version, entry_count
plan.json           plan metadata + host-context snapshot
instructions.md     markdown prompt (NO API key — offline bundles have no network identity)
results_schema.json JSON Schema the agent's results file must conform to
```

### 6.5 `POST /projects/{id}/test-plans/{plan_id}/import-results` (request)

```json
{
  "bundle_id": "<uuid from manifest>",
  "plan_id": 42,
  "execution_session_id": 17,
  "is_final": false,
  "results": [
    {
      "entry_id": 501,
      "test_index": 0,
      "status": "executed",
      "command_run": "nmap -sV 10.0.1.5",
      "raw_output": "...",
      "findings_summary": "SSH exposed",
      "severity": "medium",
      "is_finding": true
    }
  ],
  "sanity_checks": [
    {
      "entry_id": 501,
      "method": "banner_grab",
      "target_ip": "10.0.1.5",
      "port_checked": 22,
      "expected_value": "OpenSSH",
      "actual_value": "OpenSSH 8.0",
      "passed": true
    }
  ],
  "feedback": { ... optional AgentFeedback payload ... }
}
```

**Validation rules:**
- Missing `bundle_id` → 400.
- `plan_id` or `execution_session_id` mismatch → 400 with clear detail.
- Unknown `bundle_id` → 404.
- `is_final=true` with empty `results` → 400.
- `is_final` must be a strict bool; the string `"true"` does NOT flip the flag.
- `is_finding` must be a strict bool; `"false"` does NOT become `True`.
- JSON depth > 20 levels → 400 (depth bomb guard).
- File > configured max size → 413.
- Re-importing the same file is idempotent — upserts on `(session, entry, test_index)` and `(session, entry)` keys; does not duplicate rows.

### 6.6 `POST /agent/feedback` (request)

```json
{
  "source": "plan_generation",
  "prompt_version": "1.0.0",
  "overall_rating": 4,
  "api_critiques": [
    {
      "endpoint": "/agent/test-plans/{id}/validate",
      "issue": "...",
      "suggestion": "..."
    }
  ],
  "tool_suggestions": [
    {
      "name": "nuclei",
      "category": "enum",
      "rationale": "..."
    }
  ],
  "friction_notes": "Free-text summary of friction points encountered.",
  "agent_metrics": {
    "agent_name": "codex",
    "model": "gpt-5.4-codex",
    "context_used_tokens": null,
    "tool_calls_total": 11,
    "wall_clock_seconds": null,
    "notes": "Some metrics may be null when the agent sandbox doesn't expose them."
  }
}
```

Null metrics are acceptable — the guide explicitly notes that agents running in restricted sandboxes may not see their own token/cost/wall-clock numbers. `source`, `project_id`, `test_plan_id`, and `execution_session_id` are inferred from the API key binding.

### 6.7 `GET /api/v1/projects/{project_id}/test-plans/{plan_id}/api-activity` (v2.24.0)

```json
{
  "total": 87,
  "items": [
    {
      "id": 5821,
      "created_at": "2026-05-15T09:42:11.123Z",
      "agent_id": 17,
      "api_key_prefix": "nm_agent_4f3a",
      "source_ip": "192.168.10.55",
      "method": "POST",
      "path": "/api/v1/agent/test-plans/42/entries/501/sanity-check",
      "path_template": "/api/v1/agent/test-plans/{plan_id}/entries/{entry_id}/sanity-check",
      "path_params": {"plan_id": 42, "entry_id": 501},
      "query_params": null,
      "request_body_summary": {
        "method": "banner_grab",
        "target_ip": "10.0.1.5",
        "passed": true
      },
      "status_code": 201,
      "response_bytes": 184,
      "duration_ms": 22,
      "test_plan_id": 42,
      "execution_session_id": 17,
      "recon_session_id": null,
      "scope_id": null,
      "referenced_host_ids": [],
      "referenced_entry_ids": [501],
      "referenced_target_ips": ["10.0.1.5"]
    }
  ]
}
```

- **Request bodies are summarised, not raw.** Captured for mutations only (POST/PATCH/DELETE), capped at 8 KiB; multipart bodies record metadata only (no file payload). Sensitive-shaped fields (`api_key`, `authorization`, `password`, `secret`, `token`) are stripped as defence-in-depth.
- **Response bodies are NOT captured.** Only the `Content-Length` header value (when set) lands in `response_bytes`.
- **The `referenced_*` lists** are parsed out of path + query + body so a filter like `?target_ip=10.0.0.5` is a single indexed query.
- Authentication: **JWT only** — agents cannot read their own audit log.

### 6.8 `POST /api/v1/agent/execution-sessions/{session_id}/environment` (v2.23.0)

Request body (`EnvironmentProbeRequest` — `extra="allow"`, so the agent can attach observed facts beyond the fixed shape):

```json
{
  "os_family": "linux",
  "os_release": "Kali rolling",
  "arch": "x86_64",
  "shell": "bash",
  "powershell_version": null,
  "powershell_execution_policy": null,
  "python": "/usr/bin/python3",
  "python_version": "Python 3.11.4",
  "wsl_available": false,
  "tools_available": {
    "nmap": true, "masscan": true, "httpx": true, "dig": true, "curl": true, "jq": true
  },
  "notes": "running from a fresh Kali VM, no AV"
}
```

- For Windows operators, `python` may be the literal string `"microsoft-store-stub"` — the Win10/11 trap where `python` on PATH opens a Microsoft Store page instead of running. Treat that as "Python not available."
- `powershell_execution_policy` is critical for the agent's command-flavour choice: inline `powershell -Command "..."` works under `RemoteSigned` because the unsigned-script gate fires on `.ps1` files, not inline strings.

Response is the same shape echoed back plus the audit-trail fields:

```json
{
  "session_id": 17,
  "session_type": "execution",
  "probed_at": "2026-05-15T09:30:00.000Z",
  "probed_by_user_id": 42,
  "probed_from_ip": "192.168.10.55",
  "environment": { ... same shape as the request ... }
}
```

Subsequent `/execution-context` responses carry an `environment` block reflecting this probe so the agent doesn't have to re-send. The recon equivalent (`POST /agent/recon/sessions/{session_id}/environment`) uses the same shape.

---

## 7. Error shapes

FastAPI returns Pydantic validation errors in the shape:

```json
{
  "detail": [
    {"type": "missing", "loc": ["body", "user_id"], "msg": "Field required", "input": {"username": "bob"}}
  ]
}
```

**Clients must flatten this array before rendering.** Passing the object array directly into a React child will crash with error #31 ("Objects are not valid as a React child, found object with keys {type, loc, msg, input}"). The frontend helper `utils/apiErrors.ts::formatApiError` handles this; new UI code should always route error messages through it.

Non-validation errors use the simpler shape:

```json
{"detail": "User is already a member of this project"}
```

---

## 8. Security contracts

Callers should be aware of these enforced constraints — they're documented here so clients don't have to reverse-engineer 422s.

- **Prompt sanitization** is applied to `POST /llm-providers/{id}/complete` on the server before forwarding. Patterns stripped: `X-API-Key: nm_agent_*` lines, credential bullets (Access key / Secret key / Password / Username / API key / PDCP token / Secret), and bare `nm_agent_` tokens ≥20 chars. The frontend runs the same sanitizer in `utils/promptSanitizer.ts` as defense-in-depth. **If you change the bullet shape in `agent_prompt_service._integration_block`, update both sanitizers in lockstep or secrets will leak on one of the two paths.**
- **SSRF validation** runs on every `base_url` accepted by `/llm-providers/` and `/integrations/`. The validator resolves the hostname and rejects RFC1918, CGNAT, loopback, link-local, and IPv6 equivalents (including `169.254.169.254` metadata). Ollama is the sole integration type with `allow_private=True`.
- **IP-pinning transport** re-resolves hostnames at connect time inside every outbound LLM provider call, closing the DNS rebinding TOCTOU window between the validator and the actual request. Redirects are disabled so a 302 can't land on a private IP.
- **Max-length caps** on high-risk text fields: HostNote body 16 KB, plan title 200 chars, plan description 4 KB, entry rationale 4 KB, entry notes 8 KB, entry findings 16 KB, reject reason 2 KB.
- **File uploads** enforce per-extension magic-byte checks. `.xml`/`.nessus` must start with `<`, `.json` with `{` or `[`, `.gnmap` with `#` or `Host:`, text files may not contain NUL bytes. Filenames are slugified before filesystem use. Chunk-level size cap prevents unbounded streams.
- **JSON depth guard** on `/import-results` rejects payloads nested deeper than 20 levels via a byte-level pre-scan.
- **XML parsing.** Two-pronged defense (v2.41.0): `nessus_parser.py` and `openvas_parser.py` use `defusedxml.ElementTree`; `nmap_parser.py` and `masscan_parser.py` use `lxml.etree.iterparse` with `resolve_entities=False, no_network=True, huge_tree=False`. Both approaches disable external entities, DTD fetching, and entity expansion at parse time.
- **EyeWitness ZIPs** (v2.41.0) have per-file (50 MB), running-total (500 MB), and entry-count (5000) decompression-bomb caps. The streaming extractor counts bytes mid-stream and aborts + unlinks the partial file if either cap is exceeded, so a spoofed central-directory size field can't defeat the check.
- **BloodHound JSON ≥50 MB** streams via `ijson` instead of `json.load` (v2.41.0); the structure (`[…]`, `{"data": […]}`, `{"computers": […]}`) is auto-detected by peeking the first 64 KB.

---

## 9. Operational notes

- **Schema management.** Tables are owned by **Alembic**. Every backend boot runs `alembic upgrade head` before serving traffic. Migrations live in `backend/alembic/versions/`; baseline at `b46cd59c17f5_baseline_schema`. The previous startup-DDL path has been retired — the model is the schema, and Alembic enforces it.
- **Upload flow is async.** `POST /upload/` returns a queued `IngestionJob` — poll `GET /upload/jobs/{id}` for status. Don't expect a parsed scan in the upload response.
- **API keys are shown once.** Every `/generate`, `/execute`, and `/agents/{id}/rotate-key` response includes the plaintext key exactly once. Store it or discard it immediately; recovery is not possible. The hash lives in `agent_api_keys`.
- **Orphan jobs get reaped.** Jobs stuck in `processing` with a heartbeat older than 3× `INGESTION_JOB_TIMEOUT` are transitioned to `failed` by the worker's reaper loop (~1 min cadence). Users see a clear "worker likely crashed" message in the UI with `retry_count` incremented.
- **Workflow-scoped AGENTS.md.** Agents should fetch `GET /api/v1/agents-guide?workflow=plan_generation` (or `execution` or `reconnaissance`) to get the workflow-sliced subset. The server parses HTML-comment section markers so one source file emits multiple slices — meaningful token savings (~35% on execution, ~24% on plan/recon).
- **Health probes.** `GET /health` on the backend; `/health.html` on the nginx frontend.
- **Version visibility.** `GET /` returns `{message, version, frontend_version, cors_origins}`. Every UI page renders the VersionFooter in the bottom-right. Backend and frontend stay in lockstep per-release; always update both.

---

This document reflects the v2.115.0 state of the API. Use `/docs` (Swagger UI) for interactive exploration and field-level schemas — this guide is architectural context and high-signal shape references, not a replacement for OpenAPI.
