# BlueStick API Guide

> **Last verified against:** backend 2.429.0 / frontend 5.310.1 (2026-09-26) — and see the note at the end: the live OpenAPI is the authority for the full route list.

Base path: `/api/v1`

BlueStick exposes two parallel REST surfaces under the same versioned base path:

1. **JWT user API** — everything humans touch. Login, browse, upload, triage, export, manage. Nested under `/projects/{project_id}/...` for all data-bearing routes (scans, hosts, host tests, etc.).
2. **Agent API (`/agent/*`)** — terminal-side agents. Authenticates with `X-API-Key: nm_agent_...` headers — one key per project-scoped agent SESSION (not per plan or per workflow). Separate dependency chain; fully isolated from the JWT surface.

The live interactive spec is always at `GET /docs` (Swagger UI) and `GET /openapi.json`. Use this guide for architectural context and high-value shape references; use OpenAPI for field-level truth.

---

## 1. Authentication

### 1.1 JWT user tokens

```
POST /api/v1/auth/login
Content-Type: application/json

{"username": "admin", "password": "admin"}
```

Returns a JWT in the response body plus a bearer token expected on every subsequent request. When the account has 2FA enabled, `/auth/login` instead returns `{two_factor_required: true, challenge_token, expires_in}`; finish with `POST /auth/login/2fa` (`{challenge_token, code}`). Sessions are tracked server-side in `user_sessions` — logout revokes the JWT so a stolen token stops working at the next request.

The first-boot admin (`DEFAULT_ADMIN_USERNAME`, default `admin`) gets `DEFAULT_ADMIN_PASSWORD` when that is set to something other than the literal `admin`; otherwise a random password is generated, never logged, and written mode 0600 to `uploads/initial-admin-password.txt`. It starts with `must_change_password=True`. Every JWT endpoint except `/auth/*` is gated until the user rotates (403 `password_change_required`) — and, under `REQUIRE_2FA` (the default), until TOTP is enrolled (403 `two_factor_setup_required`). `/agent/*`, `/mcp` and `/references` are not behind that gate.

**Brute-force defenses (v2.41.0).** Three layers stack:

- *Per-account 5-strike lockout* — five consecutive failed attempts on the same account row trigger a 30-minute `locked_until`.
- *Per-username window throttle* — ≥10 failed attempts on the same username in any 15-minute window (across all source IPs) → HTTP 429. Defeats the "rotate IPs to defeat the per-account lockout" attack.
- *Per-IP window throttle* — ≥20 failed attempts from the same source IP in any 15-minute window (across all usernames) → HTTP 429. Defeats credential stuffing.

The throttle reads `audit_logs` (so the 429 response body says "Try again in 15 minutes"). Timing of the bcrypt path is equalized between "unknown username", "inactive user", "locked account", and "valid user, wrong password" — all four branches pay the same bcrypt cost via a precomputed dummy hash. Username enumeration via login timing is closed.

Expired user sessions are reaped hourly by a background task in the API process; `GET /auth/sessions` only returns rows where `expires_at > now() AND revoked_at IS NULL`.

The **global** role is binary — `admin` (user management, system settings, audit log) or `member`. Capability tiers — `admin` > `analyst` > `auditor` > `viewer` — live on the **project membership** (`ProjectMembership.role`) and are checked by `require_project_role`. There is no global analyst/auditor/viewer.

### 1.2 Agent sessions and keys

An operator starts **one project-scoped agent session**; its key does whatever that operator's project role allows. There are no per-workflow keys (v2.337.0) and no capability grants (v2.309.0).

There is ONE operator entry point (v2.433.0): `POST /api/v1/projects/{id}/assist/start` (floor `auditor`), the **Operations → Start Agent Session** dialog. Pages about one object (a scope, a host, a host selection) open the same dialog with a one-line task to hand the agent; they mint nothing of their own. The per-object minting routes — `POST /scopes/{scope_id}/recon/start`, `POST /test-plans/generate`, `POST /test-plans/{plan_id}/execute`, `/rotate-key`, `/resume-generation` and the per-run resume routes — were removed with the "agent on rails" model.

The agent opens whatever work it needs itself, with the same key and in whatever order the work needs: it reads a scope (`GET /agent/scopes`, `/agent/scopes/{scope_id}/subnets|domains` and the target files), uploads scanner output (`POST /agent/uploads`, nothing to open first), proposes tests on hosts (`POST /agent/host-tests`) and records what it ran as evidence (`POST /agent/evidence`, with `host_test_id` when it answers a test) — nothing waits on approval. Test plans and execution runs were removed in v2.442.0 (§4.11). Operators list, end and resume sessions at `GET /projects/{id}/agent-sessions`, `POST …/agent-sessions/{sid}/end`, `POST …/agent-sessions/{sid}/resume` (resume rotates the key and re-issues the prompt; the previous key is revoked).

- **Renew (agent-facing, v2.304.0)** — `POST /api/v1/agent/session/renew`, called by the agent with its **own** key. Same key, later deadline. It deliberately **accepts an already-expired key** while the session is active and under `AGENT_SESSION_MAX_LIFETIME_HOURS` (168h), because the failure it exists for is discovered late: an agent blocks for hours on nmap / masscan / Nessus and only learns its key lapsed when it tries to upload, with the scanning already done. No path parameter — the key identifies its own session.
- **End (agent-facing)** — `POST /api/v1/agent/session/end {notes?, agent_model?}`. Revokes the key; `409` only when the session is not active. Nothing else needs closing, however a session ends (this call, the operator's End, or the hourly lapse sweep): the host tests it proposed and the evidence it recorded are project data, and another session or a person carries them on. Agents are told to call it only when the operator says they are finished (prompt 2.13.1, v2.430.0): an agent that ended its own session after setup left the operator's next question with an unrecoverable `401`.

Keys are:
- **Hashed at rest** in `api_keys` (`APIKey`, `app/db/models_auth.py`); the plaintext is returned to the operator **exactly once**, never stored.
- **Time-bound but renewable** — default 24h TTL (`settings.AGENT_KEY_TTL_HOURS`), extendable by the agent itself while the session lives. **Ending the session, not expiry, is the revocation control**: an open session can renew past its key's deadline, so waiting for expiry is not a revocation.
- **Bounded by their operator (v2.305.0)** — a key carries the permissions of the user who started its session, resolved **per request**. A role change, a removed project membership, or a deactivated account reaches keys already in the field immediately. Mutating routes require the operator to hold `analyst` on the project; an auditor's or viewer's agent is read-only. The exception is session-metadata writes (key renewal, session end, feedback, tool suggestions), which record something about the session rather than project data and stay open to any member.

  **Reads are not uniform (v2.308.0).** Most need only project membership, but bulk exports — `/assist/report-context.ndjson`, `/assist/hosts.ndjson`, `/scopes/{scope_id}/hosts.ndjson`, the scope target lists (`live-hosts.txt`, `web-targets.txt`, `named-targets.ndjson`), and evidence downloads — require `auditor`, the same floor `export.py` and `reports.py` place on their JWT equivalents.
- **Bound to ONE session, not to a workflow or a scope.** What the session writes carries its id; a `403` is always about the operator's standing.

A 401 from an expired key carries a **structured body**: `recoverable: true` means renew with the same key and retry the failed request, `false` means the session is finished and the output should be saved to a file. "Expired" and "revoked" are the same status code but opposite situations, and the caller is usually holding output it cannot cheaply reproduce.

Every `/api/v1/agent/*` request must include:

```
X-API-Key: nm_agent_<plaintext>
```

`get_current_agent` (`app/api/deps.py`) authenticates the key and resolves its session; `enforce_agent_operator_access`, mounted on every `/agent/*` router, then re-checks the **operator's** project role on that request. There are no workflow guards: `require_plan_scope`, `require_recon_scope`, `require_assist_scope`, `require_execution_session_scope` and `deny_scoped_keys` were all removed.

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
│   └── /auth/2fa/*           # TOTP setup/enable/disable + recovery codes
├── /users                    # admin CRUD
│   └── /users/directory      # minimal picker — open to any authenticated user
├── /audit                    # admin: audit log browsing
├── /system/queue-metrics     # admin: durable job-queue operational metrics
├── /projects                 # project list, create, update, delete
│   ├── /{id}/members         # project membership (admin or project-admin)
│   └── /{id}/...             # PROJECT-SCOPED SUBTREE — see §4
├── /portfolio                # cross-project dashboard (member's own projects)
├── /oversight                # global admin: programme dashboard over any subset of projects
├── /activity                 # scan-correlation timeline ("what was scanning at time X") — NOT the note feed, which is /hosts/notes/activity
├── /notifications            # read/unread, mark-seen
├── /llm-providers            # per-user LLM credentials + /{id}/complete
├── /integrations             # scanner credentials (global admin writes; list/types any user)
├── /feedback                 # admin triage of agent feedback rows
├── /references               # SBOM, tool registry, parser coverage, MCP catalog, TLS trust (public reads)
├── /agents-guide             # the agent guide (documentation/AGENT_GUIDE.md), optionally sliced by ?workflow=
├── /mcp                      # MCP transport (JSON-RPC 2.0) — see §5.9
├── /mcp-telemetry/summary    # admin: per-tool MCP call outcomes
└── /agent                    # AGENT API — see §5
```

---

## 3. Global (non-project) endpoints

### 3.1 Authentication

| Method | Path | Notes |
|---|---|---|
| POST | `/auth/login` | Body: `{username, password}`. Returns JWT + profile, or a 2FA challenge (`two_factor_required`, `challenge_token`). |
| POST | `/auth/login/2fa` | Body: `{challenge_token, code}` (TOTP or recovery code). Returns JWT + profile. |
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
| POST | `/auth/register` | admin | Create a user. (There is no `POST /users/` — creation lives on the auth router.) |
| PUT | `/users/{user_id}` | admin | Update role, active state, etc. |
| DELETE | `/users/{user_id}` | admin | Delete (or deactivate). |
| POST | `/users/{user_id}/reset-password` | admin | Force-reset another user's password. |
| PUT | `/users/profile` | self | Self-service profile update. |
| GET | `/users/directory` | any auth | **v2.10.0** — minimal `{id, username, full_name}` list of active users. Used by the Add Member picker. Open to any authenticated user so project admins without global admin can populate dropdowns. |

### 3.3 Projects

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/projects/` | any auth | List projects the current user can see. |
| POST | `/projects/` | global admin | Create project. |
| GET | `/projects/{id}` | member | Project detail. |
| PUT | `/projects/{id}` | project admin / global admin | Update metadata. |
| PATCH | `/projects/{id}/ingest-settings` | analyst+ | Import settings (e.g. skip informational Nessus observations). |
| DELETE | `/projects/{id}` | global admin | Delete (cascades to owned data). |
| GET | `/projects/{id}/members` | member | List membership. |
| POST | `/projects/{id}/members` | project admin / global admin | Add member — body: `{user_id: int, role: str}`. |
| PUT | `/projects/{id}/members/{user_id}` | project admin / global admin | Change role. |
| DELETE | `/projects/{id}/members/{user_id}` | project admin / global admin | Remove member. |

`MembershipCreate` takes **`user_id`**, not `username`. Clients should use `/users/directory` to pick the ID.

### 3.4 Notifications

| Method | Path | Notes |
|---|---|---|
| GET | `/notifications/` | Supports `?unread_only=true&limit=20`. |
| GET | `/notifications/unread-count` | Lightweight polling endpoint. |
| POST | `/notifications/mark-read` | Body `{notification_ids: [...]}`; returns `{marked_read: n}`. |
| POST | `/notifications/mark-all-read` | |

### 3.5 Portfolio

| Method | Path | Notes |
|---|---|---|
| GET | `/portfolio/dashboard` | Cross-project summary (host count, open criticals, recent scans, attention items) scoped to projects the user can see. A project's `active_sessions` counts its open agent sessions (`agent_sessions.status = 'active'`), not execution runs. |
| GET | `/oversight/dashboard` | **Global admin** — the programme dashboard over any subset of projects. Counts come from `engagement_metrics_service`, the same as Portfolio's. |

### 3.6 LLM providers (self-service)

Per-user credentials for OpenAI, Anthropic, Azure OpenAI, Ollama, and OpenAI-compatible endpoints. API keys are Fernet-encrypted at rest; never returned in responses.

| Method | Path | Notes |
|---|---|---|
| GET | `/llm-providers/` | List providers owned by current user. |
| POST | `/llm-providers/` | Create provider. Body: `{name, provider_type, base_url?, api_key?, model_id?, is_default?}`. `base_url` validated via SSRF check. |
| PATCH | `/llm-providers/{id}` | Update. Setting `api_key=null` does not clear it; pass `clear_api_key=true` to remove. |
| DELETE | `/llm-providers/{id}` | |
| POST | `/llm-providers/{id}/test` | Non-destructive connectivity test via the provider's model-list endpoint (or a one-token completion for Anthropic). Uses `safe_http_client` with DNS re-validation. |
| GET | `/llm-providers/types` | The provider types this deployment supports. |
| POST | `/llm-providers/{id}/complete` | Completion. Body: `{system?, prompt, max_tokens?, temperature?}` (`max_tokens` 1–16384, default 2048; `temperature` 0–2). **Server-side prompt sanitization** runs before forwarding — see §8. |

### 3.7 Integrations (scanner credentials)

Scanner credentials for Nessus, OpenVAS, Nuclei, Burp and generic-API integrations. Dual-secret support (Nessus needs both Access Key and Secret Key). All secrets Fernet-encrypted. Only **global admins** create, update, delete or test them.

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/integrations/` | any auth | List integrations visible to current user. Supports `?project_id=` to return project-scoped + global. |
| GET | `/integrations/types` | any auth | The integration types available. |
| POST | `/integrations/test` | global admin | Connectivity test BEFORE creating one. |
| POST | `/integrations/` | global admin | Create. `base_url` validated via SSRF check; the `is_integration_private_allowed()` carve-out lets ollama, nessus, openvas, nuclei, burp and generic_api use private addresses (metadata / link-local are always refused). |
| PATCH | `/integrations/{id}` | global admin | Update. `clear_secret` / `clear_secret2` flags remove encrypted material. |
| DELETE | `/integrations/{id}` | global admin | |

The agent-facing `/agent/integrations` endpoint was **removed** in v2.9.5 (audit finding C#2). If a future agent workflow needs programmatic scanner credentials, it must come back as a per-plan-scoped endpoint with audit logging. Today, the session prompt inlines the credentials the agent needs directly from `agent_prompt_service._integration_block`.

### 3.8 Feedback (admin triage)

| Method | Path | Auth | Notes |
|---|---|---|---|
| GET | `/feedback/` | admin | List all agent feedback rows. Supports `?status=new\|reviewed\|actioned\|dismissed`, `?source=assist\|reconnaissance\|testing` (rows filed before v2.442.0 may carry `plan_generation`, `in_session_execution` or `exported_execution`), plus `min_rating`, `has_tool_suggestions`, `has_api_critiques`, `search`, `project_id`, `skip`, `limit`. Returns the standard `Paginated` envelope (`{items, total, skip, limit, has_more}`; v2.428.2 — it was a bare array). Each row carries who and where: `project_name`, `agent_name`, `client_name` (the MCP client the session connected with), `agent_session_id`, `session_page_id` (the `/assist-sessions/{id}` page listing the session's API calls) and `session_api_calls`. |
| GET | `/feedback/{id}` | admin | Feedback detail with `api_critiques`, `tool_suggestions`, `friction_notes`, `agent_metrics`, and the same who-and-where fields as the list. |
| GET | `/feedback/stats` | admin | Aggregate counts by source, status and prompt version, average `overall_rating`, top tool suggestions, and `with_api_critiques` / `with_tool_suggestions`. |
| PATCH | `/feedback/{id}` | admin | Update status + `reviewer_notes`. |

Feedback **ingest** (the agent-facing path) lives under `/agent/feedback` — see §5.

### 3.9 References (live, public reads)

| Method | Path | Notes |
|---|---|---|
| GET | `/references/` | Index of the reference endpoints below, with descriptions. |
| GET | `/references/sbom` | **v2.20.0** — Software Bill of Materials reflecting the deployed build's resolved dependency tree. Walks the installed Python distributions (`importlib.metadata`) and reads `frontend/package-lock.json`; `requirements.txt` only marks which are direct. Memoised by manifest mtimes + `app_version` (so a release bump invalidates the cache even if dependencies didn't change). Classifies each component as direct (listed in `requirements.txt` / `package.json` root) or transitive. |
| GET | `/references/parser-coverage` | **v2.411.0** — "What BlueStick reads": per tool, what its output reports, the level BlueStick takes each item to (observation / field / text / stored / discarded), where it is shown, and the known gaps. Data in `app/data/parser_coverage.json`; the `/reference/tool-coverage` page. |
| GET | `/references/tools` | **v2.277.0** — the tool catalogue: every tool BlueStick knows about, with install/usage knowledge, phases, intrusiveness and whether BlueStick parses its output (`ingestible`). `?status=reference\|suggested\|rejected`, `?category=`. One source for the Tool Reference page and the agent's `list_tools`. Since v2.433.0 no status is a permission — the `approved` allowlist was retired (its rows became `reference`); the operator decides what their agent runs. |
| PATCH | `/references/tools/{name}` | **Admin** — curate a tool: set `status` (`reference` = take a suggestion into the catalogue / `rejected` = decline it) and the human-facing prose. `ingestible` is deliberately not editable (it records whether a parser exists, not an operator decision), and `suggested` cannot be *set* — it means an agent proposed it. |
| GET | `/references/mcp-tools` | The live MCP tool catalog: every tool, its input schema, whether it reads or writes, and which workflows see it — plus the per-client connect recipes (with a placeholder key) and this deployment's certificate fingerprint / self-signed status. Read off the server registry, so it cannot drift from what is served. |
| GET | `/references/tls-certificate` | The deployment's public TLS certificate (PEM), for inspection and the fingerprint check. Clients trust BlueStick through the local root CA (`ca/local-ca.sh trust-help`), installed once per machine; the per-client pinning installer (`/references/trust-cert-script`, `scripts/trust-cert.sh`) is retired. |

### 3.10 Two-factor auth (TOTP)

Self-service TOTP enrollment, sharing the `/auth` prefix (no password-change gate).

| Method | Path | Notes |
|---|---|---|
| GET | `/auth/2fa/status` | Whether 2FA is enabled for the current user. |
| POST | `/auth/2fa/setup` | Begin enrollment — returns the secret + provisioning URI. |
| POST | `/auth/2fa/enable` | Confirm a TOTP code to turn 2FA on; returns recovery codes. |
| POST | `/auth/2fa/recovery-codes` | Regenerate recovery codes. |
| POST | `/auth/2fa/disable` | Turn 2FA off. |

### 3.11 System metrics (admin)

| Method | Path | Notes |
|---|---|---|
| GET | `/system/queue-metrics` | Deployment-wide durable job-queue operational metrics (ingestion + report queues): depth, in-flight, failed (undismissed; ingestion also `failed_by_project`), oldest-pending age. Admin only. |

---

## 4. Project-scoped endpoints (`/api/v1/projects/{project_id}/...`)

Every endpoint in this subtree requires JWT auth AND project membership with the appropriate role. The project is loaded and validated by `get_current_project`; cross-project reads return 404 for anything not visible to the user.

### 4.1 Upload & ingestion jobs

| Method | Path | Notes |
|---|---|---|
| POST | `/upload/` | Multipart file upload (analyst). Form fields: `file`, `stage` (register the job as `staged` instead of queuing — what the UI does), `batch_id`, `allow_duplicate`, `skip_informational`. Magic-byte validation on the first 1 KB rejects disguised binaries. **409 `duplicate_scan`** (`detail: {code, message, scan_id \| job_id}`) when this exact file (SHA-256) is already a scan, or a staged / queued / processing job. |
| GET | `/upload/formats` | Every format an operator can choose — independent of any one file's detection. |
| GET | `/upload/jobs/{job_id}/detection` | The staged review: candidate formats, each with its `basis` (`structure` \| `filename` \| `fallback`), a read-only sample, and `needs_choice`. 409 if the retained file is gone. |
| POST | `/upload/jobs/{job_id}/start` | Queue a **staged or failed** job, optionally as a chosen format: `{format_override?, source_tool?}`. The override is the WHOLE attempt list — a wrong choice fails visibly. Status, file presence and the duplicate check are re-run under a row lock: **409** if the job is no longer staged/failed, its file is gone, or it is now a duplicate (`duplicate_scan`); 422 for an unknown format. |
| POST | `/upload/jobs/{job_id}/discard` · `/upload/jobs/discard-staged` | Discard a staged job (file removed, row kept as a dismissed failure). The bulk form takes `{job_ids}` — exactly the jobs the operator was shown; there is no "discard everything". |
| POST | `/upload/jobs/{job_id}/reprocess` | Re-import a FINISHED job's retained file as a NEW job (prior scan untouched, duplicate guard bypassed on purpose). |
| POST | `/upload/jobs/{job_id}/retry` · `/dismiss` | Retry a failed job; dismiss a failed or partial one so it leaves the live queue (the row stays). |
| GET | `/upload/jobs` | List recent jobs. `?skip=0&limit=25&status=failed&include_dismissed=false`. **`?ids=1,2,3`** (≤200) returns exactly those jobs the caller may see — dismissed included, status and pagination ignored, unknown ids simply absent; 422 for non-integers or more than 200. Returns a plain array. |
| GET | `/upload/jobs/{job_id}` | Job detail. |
| POST | `/upload/jobs/{job_id}/cancel` | Cancel a queued or processing job. Only the owner or a global admin. |

The `IngestionJobSchema` includes `retry_count` and `last_error` for dead-letter surfacing (shown on **Ingestion Results**, §4.9).

### 4.2 Scans

| Method | Path | Notes |
|---|---|---|
| GET | `/scans/` | List scans. Supports pagination; `?ids=` fetches specific scans with the full per-scan import summary. |
| GET | `/scans/history` | The ORDER of the import history — upload batches and individually uploaded files interleaved, newest first, paginated over both kinds at once (`skip`, `limit` ≤200, `search`, `tool`, `created_after`, `uploaded_by`). Hydrate rows through `GET /scans/?ids=` and `GET /scans/batches?ids=`. |
| GET · POST · PATCH | `/scans/batches` · `/scans/batches/{id}` | Upload batches (create: analyst). `PATCH` names an operator's batch; an agent's batch refuses with 409. |
| GET | `/scans/{scan_id}` | Scan detail. |
| DELETE | `/scans/{scan_id}` | Admin. Deletes scan + history rows; hosts seen in other scans are preserved. |
| GET | `/scans/{scan_id}/hosts/count` | Host count only (lightweight for list views). |
| GET | `/scans/{scan_id}/command-explanation` | Human-readable explanation of the scan's command line. |
| GET | `/scans/out-of-scope` | Hosts outside every scope CIDR (derived from subnet correlation, paginated). |

### 4.3 Hosts

| Method | Path | Notes |
|---|---|---|
| GET | `/hosts/` | Deduplicated hosts with rich filter support: `state`, `search`, `ports`, `services`, `port_states`, `has_open_ports`, `os_filter`, `subnets`, `has_critical_vulns` / `has_high_vulns` / `has_medium_vulns` / `has_low_vulns`, `has_exploit_available`, `follow_status`, `scan_ids`, `tags`, `sites`, `assigned_to`, `orgs` / `asns` / `countries`, **`weaknesses=`** (comma-separated DSL `has:` flags — smb_unsigned, weak_tls, local_admin, writable_share…; OR), **`checks=`** (misconfiguration check ids; OR), and **`q=`** (the query DSL, §5.7). `sort_by` ∈ critical_vulns · high_vulns · exploitable_vulns · open_ports · note_count · discovery_count · ip_address · hostname · last_seen; also `sort_order`, `skip`, `limit` (≤500), `include_total`. |
| GET | `/hosts/{host_id}` | Host detail with ports, scripts, vulnerabilities, follow state, notes, discoveries. |
| GET | `/hosts/{host_id}/conflicts` | `conflict_count` (the same number as the list badge — host-level disagreements), `confidence` (source ranking per field) and `conflict_history`. Each history row carries both values, both scan ids AND `previous_scan_filename` / `new_scan_filename` / `current_value` — a conflict is recorded whether or not the reported value was adopted, so `current_value` is what says which one the host shows today. A blank being filled in (`state: unknown → up`) is not recorded as a conflict (v2.367.0). |
| GET | `/hosts/scan/{scan_id}` | Hosts seen in a specific scan: host fields and ports only (`ScanHost`, v2.424.1). Observations, notes and history are on `GET /hosts/{id}`. |
| GET | `/hosts/filters/data` | Filter metadata (ports, services, OS, subnets, scans). Supports cascading — pass active filter params to scope the returned metadata. |
| GET | `/hosts/tool-ready/{format}` | Auditor+ (data egress). Export filtered host list as a tool-ready target file (nmap list, masscan range, newline-delimited IPs, etc.). |
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
| POST | `/hosts/{host_id}/notes` | Analyst+. Body: `{body, parent_id?}` (a note has no status since v2.446.0; a `status` sent by an old client is ignored). `body` is capped at 16 KB. `@username` notifies that project member, then the thread's writers on a reply. Returns `mention_warning` if mention notification dispatch failed. |
| PATCH | `/hosts/{host_id}/notes/{note_id}` | Analyst+. `body` is author-only; the thread's labels `note_type` (`observation` \| `question` \| `decision` \| `handoff`, or `null` to clear; `finding` and `action` are refused since v2.447.0) and `pinned` (stored on the thread root) can be changed by any analyst. Notes are discussion: status, assignee, due date, resolution and the `/history` route were removed in v2.446.0 (work is a host test and its evidence). |
| DELETE | `/hosts/{host_id}/notes/{note_id}` | Analyst+; author delete only. |
| GET | `/hosts/notes/activity` | Activity-grouped feed for the Activity page with host enrichment. Filters: `author_id`, `search`. No note status since v2.446.0 (`status` filter, `status_counts` and `thread_root_status` are gone). |
| GET | `/hosts/notes/unread-count` | Count of notes updated since `last_viewed_at`. |
| POST | `/hosts/notes/mark-seen` | Mark all activity as seen. |

### 4.4 Dashboard

| Method | Path | Notes |
|---|---|---|
| GET | `/dashboard/stats` | Aggregated host/port/subnet/vuln counts + recent scans + note activity. |

That is the whole `/dashboard` router: the personal routes (`my-attention`, `my-tasks`, `new-scans-since`) were folded into `GET /workbench` in v2.244.0, and the port/OS/risk breakdowns are gone. See `/workbench` in §4.13.

### 4.5 Findings

Project-level finding records (the SPINE entity that correlates vulnerabilities across hosts). Mounted at the project root, so paths read `/projects/{id}/findings...`.

| Method | Path | Notes |
|---|---|---|
| GET | `/findings` | List findings. Filterable. |
| GET | `/findings/{finding_id}` | Finding detail. |
| POST | `/findings` | Create a finding. |
| PATCH | `/findings/{finding_id}` | Update metadata. |
| POST | `/findings/{finding_id}/status` | Transition disposition (terminal determinations require a justification). |
| POST | `/findings/{finding_id}/hosts` | Attach a host to the finding. |
| DELETE | `/findings/{finding_id}/hosts/{host_id}` | Detach a host. |
| PATCH · DELETE | `/findings/{finding_id}/endpoints/{finding_host_id}` | ONE endpoint row's own state (`open` / `remediated` / `retest` / `false_positive`). The finding's status is the ISSUE's; never read it as the state of a given host. Responses carry `endpoint_status_counts`. |
| GET | `/vulnerabilities/{vuln_id}/promote-preview` | What promoting this scanner observation would do — the finding it would join, the hosts carrying the issue. |
| POST | `/vulnerabilities/{vuln_id}/promote` | Promote or dismiss a scanner observation: `{vuln_id, status?, severity?, owner_id?, summary?, scope?}` — `vuln_id` is required and must equal the path id (400). **`scope: "host"`** acts on the inspected host only — a `false_positive` dismissal defaults to it; a promotion may use it (v2.366.0: the finding is still the issue's, one per `dedup_key`, but only this host is attached, so "confirmed" is never recorded for hosts nobody verified). `scope: "issue"` is every host carrying it, and is the API default for a promotion. `accepted_risk` is issue-wide only: with `scope: "host"` → **422**. |
| GET | `/findings/{finding_id}/history` · `GET`/`POST /findings/{finding_id}/notes` | Status history; the comment / evidence thread (terminal determinations need a justification). A same-status history row records a change that is not a disposition: an endpoint's state, added hosts, or report text from an accepted proposal. An accepted proposal names itself in the row's `summary` (2.439.1). |
| POST | `/findings/bulk/status` · `/findings/bulk/assign` | Bulk transitions and assignment. |
| GET | `/scanner-observations` | **v2.386.0** — scanner rows grouped by ISSUE (`Vulnerability.issue_key`) across the project's hosts, with `host_count` and `judged_host_count`. `search`, `severity`, `kind` (misconfiguration \| vulnerability \| informational), `include_judged`, `min_hosts`, `sort` (`severity` default \| `hosts` = most widespread first, v2.429.0), `skip`, `limit` (≤200). Drives the Findings page's *Scanner observations* view. |
| GET | `/scanner-observations/hosts?issue_key=` | The hosts carrying one issue (`limit` ≤5000). |
| POST | `/scanner-observations/promote` | Analyst+. `{items: [{issue_key, host_ids?}]}` — promote several issues at once, each on every host carrying it or exactly the named ones (validated all-or-nothing; 422). An issue that already has a finding JOINS it, and the bulk path never changes that finding's status. |

**Agent proposals and evidence (v2.436.0).** An agent never changes what the team concluded directly: it proposes, and a person decides.

| Method | Path | Notes |
|---|---|---|
| GET | `/proposals` | Proposals, newest first. `status` (default `pending`; `accepted` · `rejected` · `superseded`), `kind` (`finding_text` · `finding_create` · `observation_promote` · `observation_dismiss` · `endpoint_status`), `finding_id`, `host_id` (its observations and endpoints), `agent_session_id`, `limit` ≤500, `offset`. A `finding_text` row carries `current_value` (the finding's text now) beside the proposed `payload.value`; every row carries its session, `agent_model`, `agent_client`, `prompt_version`, `source` (`agent` · `llm_draft`), and `target` {`finding_title`, `observation_title`, `host_id`, `host_ip`} (v2.437.0). `finding_id` also matches promote / dismiss proposals on a scanner observation that evidences the finding (2.439.0). The same rule decides the report's `pending_proposals`, because dismissing one drops an endpoint from the report. |
| GET | `/proposals/summary` | `{pending, by_kind, pending_mine, by_kind_mine, viewer_is_project_admin}` — the project's pending count and the caller's own (proposals about findings they authored or own, the ones they are notified about). The top bar shows a person their own, and a project admin the project's (2.440.0: an admin's review of every finding had put the whole run in every member's top bar). `GET /proposals?mine=true` is the list narrowed the same way; the Proposals page's "Findings: mine / everyone's" and a proposal notification's link (`scope=mine`) use it. |
| POST | `/proposals/{id}/accept` | Analyst+. `{note?, edited_value?}` (`edited_value`: report text only — accept with your edit). Runs the SAME code as the equivalent click, as you: report text needs the finding's author or a project admin (403 otherwise, the proposal stays pending). Accepting report text marks that field's other pending proposals `superseded`. A refusal from the underlying action (e.g. the target changed) is kept on the proposal's `error` and the proposal stays pending; a decided one → 409. |
| POST | `/proposals/{id}/reject` | Analyst+. `{note?}`. |
| POST | `/proposals/bulk` | Analyst+. `{ids (≤200), action: accept|reject, note?}` — each decided on its own; returns `decided` and `failed` [{id, status_code, detail}]. An accept never chooses between several pending drafts of one finding's field in the same batch: those fail with 409 "choose one on the finding" (2.439.0). Deciding locks the proposal row, so two concurrent decisions on one proposal apply it once; the other gets 409. |
| POST | `/evidence/{id}/finding` | Analyst (v2.443.0; joins the issue since v2.445.0). `{title?, severity?, status?: open\|confirmed (default confirmed)}` — makes a finding of an evidence record whose outcome is `finding`. **When the record's test names a scanner observation still on the host** (`issue_key`), this IS that observation's promotion on this host (`promote_or_dismiss_vulnerability`, `scope: host`): it joins the issue's finding if one exists, takes the issue's title and severity — the `severity` sent is ignored on this path (v2.445.1; it used to override the scanner's) — and returns `joined_issue: true`. **Otherwise** it creates a finding from `title` + `severity` (both then required, `422` without), attached to the record's host (at the name its test was aimed at). Either way the record is linked to the finding. `409` when the record already belongs to a finding, `422` for any other outcome. Returns `{finding_id, …}`. |
| GET | `/evidence` · `/evidence/{id}/raw` | Evidence records (an agent's, or a person's test result) (`host_id`, `host_test_id`, `finding_id`, `agent_session_id`, `unlinked` — `true` keeps records that answer no test, the host page's "Other evidence" — `limit`, `offset`) with a 2,000-character preview · the whole raw output (text). Any member. |

**Notifications (v2.437.0).** A proposal on a finding notifies its author and its owner (`type: "proposal"`), except the person whose agent or draft it is: ONE unread notification per person per agent session (per finding for an in-app draft), updated as the run proposes more. `finding_id` is set when it covers one finding; otherwise `source_type: "agent_session"` + `source_id` point at the session's proposals. The client report's summary carries `pending_proposals` [{id, ref, title, count}] and `agent_images` (images from agent-written notes) — warnings before issuing, never blocks.

Renaming a finding (a changed `title`) and deleting it need the finding's author, a project admin or a global admin (responses carry `can_modify`); severity / owner / status are any analyst's. A comment is its author's only, and one with replies is kept (409).

### 4.6 Scopes & subnets

| Method | Path | Notes |
|---|---|---|
| GET | `/scopes/default` | The project's default scope. |
| GET | `/scopes/` | List scopes. |
| GET | `/scopes/{scope_id}` | Scope detail. |
| DELETE | `/scopes/{scope_id}` | |
| POST | `/scopes/upload-subnets` | Analyst+. Upload a scope file: CIDR/IP rows → subnets, domain rows (`*.example.com` = include subdomains) → scope domains. 2 MB cap, 50 000 entry cap. Scopes are created through import; there is no bare `POST /scopes/`. |
| GET · POST | `/scopes/{scope_id}/domains` · `DELETE …/domains/{domain_id}` | Declared domain scope (analyst+ to change). Name scope never confers subnet scope. |
| POST | `/scopes/correlate-all` | Analyst+. Re-run host ↔ subnet correlation for the project. A repair tool only: imports, subnet adds / CIDR changes and scope-file uploads correlate by themselves, so the Scope page no longer offers it (5.269.0). |
| GET | `/scopes/coverage?limit=25` | Scope coverage rollups. |
| GET | `/scopes/{scope_id}/host-mappings` | Host-to-subnet mappings for a scope. |
| POST | `/scopes/{scope_id}/subnets` | Add CIDR entries. Each CIDR capped at 64 chars. |
| PATCH | `/scopes/{scope_id}/subnets/{subnet_id}` | Update a CIDR or description. |
| DELETE | `/scopes/{scope_id}/subnets/{subnet_id}` | Remove. |

### 4.7 Export & reports

| Method | Path | Notes |
|---|---|---|
| GET | `/export/scope/{scope_id}?format_type=txt\|csv\|json` | Auditor+. Hosts + ports within a scope (default `txt`). |
| GET | `/export/scan/{scan_id}?format_type=txt\|csv\|json` | Auditor+. Scan-level export. |
| GET | `/export/out-of-scope?format_type=txt\|csv\|json` | Auditor+. Out-of-scope host export. |
| GET | `/reports/hosts/csv` | Auditor+ (the whole `/reports` router). Host listing as CSV. |
| GET | `/reports/hosts/html` | Host listing as HTML. |
| GET | `/reports/systemic.html` | The systemic-insights report as HTML. |

JSON host export is **not** a `/reports/hosts/*` path — use `/export/...` or `/hosts/tool-ready/{format}`.

#### Reports jobs (async — the comprehensive/heavy formats)

Since v2.196.0 the heavy report formats (JSON, agent package, markdown bundle; there is no PDF) run on a dedicated report-worker container, mirroring the ingestion pipeline. Submit a job and poll, rather than blocking the request.

| Method | Path | Notes |
|---|---|---|
| POST | `/reports/jobs` | Queue a report job. Returns **202** with the queued `ReportJob`. |
| GET | `/reports/jobs` | List recent report jobs for the project. |
| GET | `/reports/jobs/{job_id}` | Job detail (status, progress, error). |
| GET | `/reports/jobs/{job_id}/download` | Stream the finished artifact (only once `status=complete`). |
| POST | `/reports/jobs/{job_id}/dismiss` | Hide a finished/failed job from the report-jobs tray. |
| POST | `/reports/jobs/{job_id}/retry` · `/cancel` | Re-queue a failed job; cancel a queued job before the worker claims it (409 otherwise). |
| GET | `/reports/limits` | Host cap per format (`null` = every matching host). CSV, JSON and the agent package are uncapped (v2.394.0); HTML is capped at `ReportGenerator.MAX_REPORT_HOSTS`; the markdown bundle at `REPORT_MAX_INMEMORY_HOSTS`. |
| POST | `/reports/draft/finding-text` | `{finding_id, fields?, provider_id?}` → `{proposals, provider_id, provider_type, model_id, usage}`: the operator's LLM provider drafts Markdown for one finding's empty report sections (default: the empty ones of description / impact / recommendation). **Since v2.437.0 the draft is a set of `finding_text` proposals** (`source: llm_draft`, the provider's model), reviewed and accepted like an agent's (§4.5); nothing is written until one is accepted. Analyst+ (proposing changes nothing; accepting still needs the author or a project admin); the finding's author and owner are notified when someone else drafts. 400 no provider / nothing empty; 502 provider failed or answered unreadably. |

### 4.8 DNS

BlueStick **never originates DNS (or any other) network queries** — the server
must not be usable as a recon proxy. DNS data is ingested only from files the
operator produced on their own host (dnsx JSON, DNS CSV, amass). There is no
server-side lookup/AXFR endpoint. Stored DNS records are read per host/scan via
`GET /hosts/{id}/dns-records` and `GET /scans/{id}/dns-records`.

**Named assets — `/names` (v2.322.0).** An FQDN is an identity of its own (`DNSName`), never merged into a host; it is linked to addresses only through observations. `GET /names/` (list), `GET /names/summary`, `GET /names/export`, `POST /names/import`, `GET /names/by-host/{host_id}` (names bound to one address), `GET /names/{name_id}` (addresses, evidence, siblings), `DELETE /names/{name_id}`. "Currently resolves to" is derived from the latest A/AAAA observations, never stored.

### 4.9 Parse errors

| Method | Path | Notes |
|---|---|---|
Every `/parse-errors` route requires **analyst+**.

| Method | Path | Notes |
|---|---|---|
| GET | `/parse-errors/ingestion-results` | The **Ingestion Results** page (Inventory hub): one row per import with its format chain (`detected_file_type` → `format_override` → `final_file_type`), `file_retained` and `retained_until`. |
| GET | `/parse-errors/ingestion-results/{job_id}/uninterpreted` | **v2.418.0** — the lines an import did not interpret, as REDACTED shapes: `{job_id, original_filename, tool_name, total, distinct, shapes[]}`. Only parsers that publish them (NetExec today) have rows. |
| GET | `/parse-errors/` | Filterable list. |
| GET | `/parse-errors/{error_id}` | Detail with `user_message` + full file preview. |
| GET | `/parse-errors/stats/summary` | Aggregate counts by type, status, file. |
| PUT | `/parse-errors/{error_id}/status?status=unresolved\|reviewed\|fixed\|ignored` | Update status (query parameter, not a body). |
| DELETE | `/parse-errors/{error_id}` | |

### 4.10 Agents (project-scoped) — **removed in v2.295.0**

There is no agent CRUD surface. The `/agents/*` routes (list, create, detail,
update, delete, rotate-key, renew-key) existed to manage the **unscoped global
agent key** — a credential bound to no session that reached the whole
project with full write authority.

Agent rows are auto-provisioned by the session start endpoint, and every key it
mints is bound to exactly one `AgentSession`. Get a key by starting a session:
`POST /assist/start` (§1.2 — the one entry point since v2.433.0; the per-object
`/test-plans/generate`, `/test-plans/{plan_id}/execute` and
`/scopes/{scope_id}/recon/start` routes were removed). Sessions are managed uniformly: `GET /projects/{id}/agent-sessions` (the timeline), `GET /projects/{id}/agent-sessions/{sid}` (one session, v2.432.0), `POST /projects/{id}/agent-sessions/{sid}/end` (owner or project admin — this is what revokes the key) and `POST …/{sid}/resume` (rotates the key, re-issues the prompt and MCP setup). A key with no session
binding is now **rejected at authentication** (403), so any credential predating
the removal is inert.

### 4.11 Host tests

A **host test** is one check proposed for one host — by a person or by an agent — and it is shown on that host's page (the Tests section). There is no plan to register, no run to open and no approval step. Test plans, plan entries and history, execution runs and their results, sanity checks, the offline execution bundle (`export-bundle` / `import-results`) and the plan execution report were **removed in v2.442.0**; every `/test-plans/*` and `/execution-sessions/*` route is gone. On upgrade, each proposed test of each plan entry became a host test (its `label` is the plan's title) and each execution result became an evidence record (migrations `d7e1a9c4b602`, then `f4b8d2a6c917`, which drops the old tables — back the database up first, see `README.md`).

A test carries `tool`, `description`, `command` (may use `{ip}` / `{fqdn}`), `rationale`, `expected_result`, `references`, `priority` (`critical`…`info`), `label`, `target_fqdn` (optional — a name observed at the host), `status` (`proposed` → `in_progress` → `done`, or `dismissed`), `assigned_to_id`, `tester_summary`, `source` (`agent` \| `person`), the proposer's attribution (`created_by`, `agent_session_id`, `agent_model`, `agent_client`, `prompt_version`), `revision` and `evidence_count`. What a test **produced** is not written on the test: it is an evidence record that names it (`host_test_id`; §5.6a).

| Method | Path | Notes |
|---|---|---|
| POST | `/host-tests` | Analyst. Body `{tests: [...]}` — 1–200 tests, each `{request_key, host_id, tool, description, rationale, command?, expected_result?, references?, priority?, label?, target_fqdn?, vulnerability_id?, assigned_to_id?}`. 201 `{items}`. `vulnerability_id` (v2.445.0) names the scanner observation the test confirms — it must be on the same host (`422`) — and is stored as the issue's identity (`issue_key`, `issue_title`), so the link survives a re-scan that replaces the row. The batch is validated as a whole (an unknown host is a 404 and nothing is written). `request_key` makes a retry safe: the same key with the same content returns the existing test; with different content it is a `409`. `target_fqdn` must be a name observed at that host (`422`); an assignee must be a project analyst or admin (`422`). |
| GET | `/host-tests` | Any member. `host_id`, `status`, `label` (exact), `assigned_to_id`, `agent_session_id`, `mine` (assigned to the caller), `active_only` (proposed or in progress), `q` (a Hosts query — the tests on the hosts it matches), `limit` (≤200) / `offset`. Returns `{items, total, has_more}`, newest first. |
| GET | `/host-tests/{test_id}` | Any member. One test; `404` for another project's. |
| PATCH | `/host-tests/{test_id}` | Analyst. `{expected_revision, status?, assigned_to_id?, tester_summary?, dismissed_reason?}`. `expected_revision` is the revision the caller read: a stale write is a `409` and changes nothing. `status: "dismissed"` needs `dismissed_reason`; `status: "done"` needs an evidence record for the test or a `tester_summary` saying why none was run (`422` otherwise). `assigned_to_id: null` unassigns. |
| POST | `/host-tests/{test_id}/result` | Analyst (v2.443.0) — a person's result for a test they ran, in one call: `{expected_revision, request_key, outcome: finding\|no_finding\|inconclusive\|failed, summary, command?, raw_output?, observed_ip?}`. Writes the evidence record (the same one `POST /agent/evidence` writes; `command` defaults to the test's with `{ip}`/`{fqdn}` filled) and moves the test: `finding` / `no_finding` → `done`, the others → `in_progress`. `409` on a stale revision (nothing is written). Every result takes the test's revision — one that leaves the status unchanged too (v2.445.1) — so a copy read before it is stale. A repeated `request_key` with the same result returns the stored record and writes nothing; the same key with a changed outcome, summary, output or author is a `409` (v2.445.1), never a success that kept the old record. Returns `{test, evidence}`. Page route only: an agent uses `POST /agent/evidence` then `PATCH`. |

**Planned, tested, untouched — one definition each** (`host_test_queries`, `host_query_predicates.untouched_conditions`), used by the Hosts list, the DSL (`has:planned`, `has:tested`, `has:untouched`), `/coverage` and the Operations terrain: a host is *planned* while it has a test that is `proposed` or `in_progress`; *tested* once an evidence record with outcome `finding`, `no_finding` or `inconclusive` exists for it (a `failed` attempt or an `info` record is not a test); *untouched* when it has no follow, note, non-dismissed test, evidence record or finding endpoint. The DSL's `testlabel:"…"` matches hosts carrying a test with that label, whatever its status.

### 4.12 Scanning by agents

There is no recon-run record: the `/recon-sessions/*` routes and the agent's `/agent/recon/*` routes were removed. An agent's scanning is visible as its uploads (the scans and upload batches it produced; each job carries `ingestion_jobs.agent_session_id`) and as its session's API-call feed on the session page (`/agent-sessions/{id}`). The agent-side scope reads and upload routes are in §5.

### 4.13 Other project-scoped routers

These mount under `/projects/{project_id}/...` alongside the above. Most are dashboard/analytics or host-management surfaces driving specific UI pages; see `/docs` for the per-route field shapes.

| Base path | Purpose |
|---|---|
| `/posture` | Security Posture roll-up — deterministic condition label (`action_required` / `needs_assessment` / `insufficient_evidence` / `no_urgent_signals`), plain-language conclusion, and the condition-family × site heatmap. Drives the frontend **Posture** tab. |
| `/posture/evidence` | Per-assessment-domain coverage (eligible vs assessed hosts: discovery, service/version, vulnerability, web/TLS, auth/SMB/AD, validation) + `matrix` (domain × segment: sites, or most-specific subnets when no site is defined, plus `unmapped` for hosts outside every scoped subnet; each cell `eligible` / `assessed` / `gap`) + contributing tools + parse-error data quality. Three states only — assessed, not assessed, not applicable: a project is one assessment window, so nothing is judged by age. `GET /posture/evidence/{domain}/gaps?segment=<key>` lists one cell's hosts. Drives the frontend **Evidence** tab. |
| `/insights/subnets` | Per-subnet insights (exposure + neglect + hygiene, worst-first). Drives the frontend **Segments** tab (Subnet lens). |
| `/insights/systemic` | Systemic insights — pattern families, estate blind spots, segment outliers, diagnostic profiles. Drives the frontend **Patterns** tab. |
| `/attention` | Project "needs help" attention model (the site-metrics arc). |
| `/sites` | Site entity management (tier / owner / coverage). |
| `/coverage` | Project coverage summary (drives v3 Operations). |
| `/workbench` | Batched Operations workbench + since-last-visit cursor. `GET /workbench/investigate?tier=1..5` is the "Worth a look" queue on its own (untouched hosts with a reason, ranked by a stated tier in SQL; carries whole-queue `tier_counts`; 503 on failure); `GET /workbench/terrain` counts hosts per /24 (IPv6 /64) as tested / planned / worked / untouched for the Operations terrain; `POST /workbench/seen {as_of}` acknowledges the displayed snapshot; `GET /workbench/my-activity` is the caller's recent work. |
| `/client-reports` | **v2.380.0** — the client report (Reports page). Reads need auditor, drafts analyst, issuing and re-rendering project admin. `GET`/`POST ""`, `GET`/`PATCH`/`DELETE /{id}`, `POST /{id}/preview` (202, a report job), `/{id}/issue` (freezes and numbers it), `/{id}/render` (retry an issued report's files), `/{id}/revise` (201), `GET /{id}/files/{fmt}`, `GET /{id}/scope.csv` (v2.441.0: the complete scope as CSV — a draft's live scope, an issued report's frozen one, so its SHA-256 matches what the report prints. A report whose scope is over its template's `scope_inline_max` / `scope_domains_inline_max` names this file instead of listing the scope, and its summary carries `scope_external` {networks, domains, inline_max, domains_inline_max, file: {name, sha256, bytes}}. Cells starting `=`, `+`, `-` or `@` get a leading apostrophe, so a spreadsheet does not run them); `GET`/`PUT /profile`, `GET /templates` (and `/templates/problems` for templates that cannot be offered), `GET /team`. **Template files (v2.431.0):** `PUT /templates/{name}/assets/{asset_id}` (multipart `file`; **global admin only**, instance-wide, audited as `report_template_asset_uploaded`) validates the bytes against the asset's kind and guidance (PNG / JPEG header and pixel size, a macro-free `.docx`; `max_bytes`, `min_width`/`min_height`; a different `aspect` is a warning) and answers `{template, warnings}` (422 with the reason when refused); `DELETE` the same path removes the upload (404 when none); `GET …/{asset_id}/preview` serves the raster image the render would use (upload, else server-installed; never an SVG or the Word file). Each listed asset carries `present`, `installed`, `source` (`uploaded`/`installed`/null), `kind`, `uploadable`, the guidance fields and `upload` (size, pixels, sha256, who, when). See `report-templates/README.md`. |
| `/webhooks` | Per-project outbound webhook subscriptions + delivery records. |
| `/hosts/tags` | Project tag catalog with host counts (`host_tags`). |
| `/hosts/bulk/*` | Bulk host operations (`host_bulk`): `POST /hosts/bulk/tags`, `/bulk/assign` (assign hosts to a user — analyst+), `/bulk/unassign` (remove the **caller's own** assignment — any project member, so an assigned viewer/auditor can drop it), `/bulk/follow`. |
| `/hosts/views`, `/hosts/views/{id}/promote` | Saved Hosts-page filter/view state per user (`host_filter_views`). |
| `/hosts/query/schema`, `/hosts/query/validate`, `/hosts/query/history` | Boolean query-DSL catalogue, validate/match-count preview, and per-user query history (`host_queries`). |
| `/scopes/subnet-labels`, `/scopes/subnets/{id}/labels` | Subnet labelling (`subnet_labels`; mounted before `scopes` so the static prefix wins route resolution). |
| `/agent-sessions`, `/agent-sessions/by-model-tool`, `/agent-sessions/{sid}` | Unified agent-session timeline across workflows, plus by-(model, tool) aggregates (the Agent Sessions page). A row's `kind` is `project` (every session since v2.337.0) or a legacy `assist` row. A project session's row carries `host_test_count` and `evidence_count` (the host tests it proposed and the evidence records it wrote — v2.442.0; they replace `phases`), `assist_session_id` (its detail row: notes and the API-call feed are keyed by it, a different id sequence), `last_activity_at`, `operator_role`, and the caller's `can_end` (owner or project admin) / `can_resume` (owner) on an active session. `GET /agent-sessions/{sid}` returns that row for one project session (404 for a legacy row); the session page reads it. |
| `/assist-sessions/{id}/api-activity` | The agent API-call log of one session, keyed by the session's detail row (`assist_session_id` on the session row). Filters: `method`, `status_min`, `status_max`, `host_id`, `target_ip`, `mine`, `limit`, `offset`. Returns `{total, items[]}`. See §6.7. |

---

## 5. Agent API (`/api/v1/agent/*`) — X-API-Key auth

All endpoints in this section require `X-API-Key: nm_agent_<plaintext>` in the request headers. Project scope is implicit from the key.

**One key, one session, every surface.** The Swagger tags — `agent-browse`, `agent-host-tests`, `agent-proposals` (evidence and proposals), `agent-scope` (scope reads and uploads; `agent-recon` until v2.433.1), `agent-assist`, `agent-feedback` — group the routes by kind of work; they are not scopes, and nothing is rejected for being "the wrong workflow" (that model ended in v2.337.0). There is no required order either (v2.433.0): the operator drives the agent, and the agent uploads, proposes tests and records what it ran as the work needs, within the operator's project role. No step waits on a human approval.

**Session lifecycle:** work, in whatever order it needs (there is no setup call — the environment probe was removed in v2.434.0): to read a scope, `GET /agent/scopes` then `/agent/scopes/{scope_id}/subnets|domains` or the target files; to add scanner output, `POST /agent/uploads` (no run needed) and poll `GET /agent/uploads/{job_id}`; to propose tests, `POST /agent/host-tests {tests: [...]}`; to work one, `PATCH /agent/host-tests/{test_id}` to `in_progress`, run it, `POST /agent/evidence` with `host_test_id` and a `request_key`, then `PATCH` it to `done` → `POST /agent/session/end` when the operator says they are finished. `GET /agent/identity` reports `can_write_project_data`, `key_expires_at` and `renew_path`. The only read-back is the session prompt's (project, scope, working directory); the per-run read-back went with execution runs in v2.442.0.

**The safety rules are the agent's, not the server's** (`agent_policy.SAFETY_RULES`): show the operator every command; the operator drives (propose next steps, don't take them unasked); stay inside the declared scope — a target outside it needs the operator's explicit go-ahead, and a name in scope does not scope its address; write output into the working directory — outside it, installing software, or changing settings/credentials needs explicit go-ahead; record every command and outcome verbatim and upload scanner output. There is no approved-tool allowlist, no approve-by-exception rule, no mandatory target check and no plan approval (all retired in v2.433.0, prompt 3.0.0). `/.well-known/networkmapper.json` publishes `command_approval: "operator_driven"` and no longer carries `plan_execution_requires_human_approval`. `backend/tests/test_well_known.py` pins its `safety_properties`: the server-enforced claims (`server_executes_commands: false`, `agent_authority`, `agent_key_binding`, time-limited renewable keys, persistent audit trail), the not-enforced ones (`command_approval`, `command_approval_enforced_by`), and the retired claims as absent.

Most endpoints below — the assist, host-test, evidence, proposal and scope-read routes — are also **MCP tools** (§5.9): same key, same checks, same audit row. The §5.1 browse reads and the file downloads are not.

The contract agents follow is the [agent guide](AGENT_GUIDE.md), served at `GET /api/v1/agents-guide`.

### 5.1 Project context (every session)

| Method | Path | Notes |
|---|---|---|
| GET | `/agent/project` | Project metadata (name, description, status). |
| GET | `/agent/dashboard` | Stats summary for the bound project. |
| GET | `/agent/hosts` | Paginated host list. Supports `limit` (1–5000, default 500) / `offset` and the discrete filters (state, ports, services, subnets, `has_*_vulns`, search). |
| GET | `/agent/hosts/{host_id}` | Host detail with ports, services, vulns. |
| PATCH | `/agent/hosts/{host_id}` | Correct hostname / OS (analyst operator); setting `os_name` re-derives `os_family`. |
| GET | `/agent/scans` | Scan list. |
| GET | `/agent/scopes` | Scope list with subnet counts. |
| GET · POST | `/agent/hosts/{host_id}/notes` | Read a host's notes; create one from the agent's identity. An `@username` in an agent's note notifies nobody. |
| POST | `/agent/hosts/{host_id}/follow` | Follow a host. |

### 5.2 Host tests

The agent side of §4.11 — the same contract and the same service (`host_test_service`), authenticated by the session key. A test an agent proposes is `source: "agent"` and carries the session's id, model, client and prompt version. Writes need the operator to hold `analyst`; there is no approval step. Which hosts and which tests is the operator's request.

| Method | Path | Notes |
|---|---|---|
| POST | `/agent/host-tests` | `{tests: [...], agent_model?}` — 1–200 tests in one validated batch (fields and refusals as in §4.11). 201 `{items}`. Each test needs its own `request_key`; a retry with the same key and content returns the existing test. MCP `host_tests_propose`. |
| GET | `/agent/host-tests` | `host_id`, `status`, `label`, `assigned_to_id`, `agent_session_id`, `mine` (assigned to the session's operator), `active_only`, `q` (a Hosts query), `limit` / `offset` → `{items, total, has_more}`. MCP `host_tests_list`. |
| GET | `/agent/host-tests/{test_id}` | One test. MCP `host_tests_get`. |
| PATCH | `/agent/host-tests/{test_id}` | `{expected_revision, status?, assigned_to_id?, tester_summary?, dismissed_reason?}` — `409` when the revision is stale. MCP `host_tests_update`. The result of a test is recorded with `POST /agent/evidence` (§5.6a), not here. |

(§5.3 and §5.4 — the plan-generation and execution workflows — were removed with test plans in v2.442.0.)

### 5.5 Scope reads and uploads

There is no run to open (recon runs and the `/agent/recon/*` routes were removed): an agent reads a scope by id and uploads what its tools produced. Routes in `agent_recon.py`.

| Method | Path | Notes |
|---|---|---|
| GET | `/agent/scopes` | The project's scopes (`agent_browse.py`; MCP `assist_list_scopes`). |
| GET | `/agent/scopes/{scope_id}/subnets` · `/agent/scopes/{scope_id}/domains` | The scope's CIDRs and declared domains (`{domain, include_subdomains}`), paged by `offset` / `limit` (default 500, max 2000). MCP `scope_list_subnets` / `scope_list_domains`. 404 for a scope outside the key's project. |
| GET | `/agent/scopes/{scope_id}/hosts.ndjson` · `/live-hosts.txt` · `/web-targets.txt` | Auditor floor (`AGENT_READ_ROLE_OVERRIDES`). File-shaped target lists for the next tool — every in-scope host as NDJSON, one IP per line, one http/https URL per line — `curl` them to disk; not MCP tools. Subnet scope only. |
| GET | `/agent/scopes/{scope_id}/named-targets.ndjson` | Auditor floor. Name scope as NDJSON, one object per name the scope's domain rules cover: `name`, `name_id`, `scope_rule {domain, include_subdomains, match}`, `addresses [{ip_address, record_type, last_observed, host_id, in_subnet_scope}]` (current A/AAAA batch), `unresolved`, `reason`, `web [{interface_id, url, scheme, port, ip_address, at_current_address, source, status_code, title, observed_at}]` (interfaces whose `name_id` is the name). Declared domains with no observed name are listed unresolved. SAN-only and co-hosted names are excluded; a name never puts its address in scope. 404 for another project's scope. |
| POST | `/agent/uploads` | Multipart upload of scanner output, belonging to the agent session (`ingestion_jobs.agent_session_id`). Form fields: `file`, `tool_name`, `command_run`, `batch` (sweep label — batches are keyed per agent session + label), optional `skip_informational` (Nessus). 409 `duplicate_scan` for an identical file; 503 + `Retry-After` when the batch label is briefly contended. |
| GET | `/agent/uploads/{job_id}` | Poll an upload's parse status (`queue_age_s`, `parse_s`, `last_error`). Only this session's jobs — 404 otherwise. MCP `get_upload_job`. |

### 5.6 Feedback ingest

| Method | Path | Notes |
|---|---|---|
| POST | `/agent/feedback` | Record structured feedback, at the moment of friction. Body includes `source`, `prompt_version`, `overall_rating` (1–5), `api_critiques[]`, `tool_suggestions[]`, `friction_notes`, `agent_metrics{}`. `source` is required: `assist`, `reconnaissance` or `testing` (`plan_generation` and `in_session_execution` are still accepted from older clients; `exported_execution` is refused — it was for offline result bundles). `assist_session_id` is an optional body field for a pre-consolidation assist session; the project and session come from the key. |

### 5.6a Evidence and proposals (v2.436.0)

| Method | Path | Notes |
|---|---|---|
| POST | `/agent/evidence` | Record a command run against a host and its result: `host_id`, `tool`, `outcome` (`finding` · `no_finding` · `inconclusive` · `failed` · `info`), `summary`, optional `host_test_id` (the test it answers — the same host, and then `request_key` is required), `request_key` (re-sending it returns the record already stored; a different payload under the same key is a `409`), `command`, `raw_output` (≤5 MB → 413; stored in the record — since 2.439.0, when it stopped being a file that outlived a deleted host; NUL characters are removed), `finding_id`, `finding_host_id`, `observed_ip` (an IP literal), `executed_at`, `agent_model`. Immutable — no update or delete route. A record with outcome `finding`, `no_finding` or `inconclusive` is what makes its host *tested*; when it answers a test aimed at a name (`target_fqdn`) and carries `observed_ip`, it also writes that name's `TESTED` observation. |
| GET | `/agent/evidence` · `/agent/evidence/{id}/raw` | This project's records (`host_id`, `host_test_id`, `finding_id`, `agent_session_id`, `limit`, `offset`) · one record's raw output. |
| POST | `/agent/proposals/finding-text` | `{finding_id, fields: {field: text}, rationale?, evidence_ids?, agent_model?}` — one proposal per field (`description`, `impact`, `recommendation`, `references`, `steps_to_reproduce`, `cvss_vector` — validated now). Returns `{proposals: [...]}`. |
| POST | `/agent/proposals/finding` | `{title, severity, host_ids, status? (open|confirmed), report_text?, …}` — a new finding, created by the person who accepts it. |
| POST | `/agent/proposals/observation` | `{vulnerability_id, action (promote|dismiss), scope? (host|issue), severity?, summary?, …}` — accepted through the promote route's own logic. |
| POST | `/agent/proposals/endpoint-status` | `{finding_id, finding_host_id, host_status (open|remediated|retest|false_positive), …}`. |
| GET | `/agent/proposals` | This project's proposals and their decisions (`status`, `kind`, `finding_id`, `mine`). |

Writes need the operator's project write role. `evidence_ids` must name records in this project (404).

### 5.7 Assist workflow (read-only Q&A — v2.64.0)

For "ask questions about this project" agents. These reads are the DEFAULT surface of every session — no run, no special key (`require_assist_scope` is gone). Project membership is the floor, and a read needs the role its page needs (v2.428.0): the NDJSON exports and client reports require `auditor`, ingestion issues and uninterpreted lines `analyst`; attachments and screenshots any member. The table below is a sample. Since v2.428.0 the agent reads mirror the pages — `/assist/workbench`, `/assist/workbench/investigate`, `/assist/workbench/terrain`, `/assist/evidence/gaps`, `/assist/scans/compare`, `/assist/scanner-observations[/hosts]`, `/assist/client-reports[/{id}[/files/{fmt}]]`, each wrapping its page's service; the agent guide's assist table describes them. The router also serves `/assist/hosts/count`, `/assist/hosts/{id}/findings` (raw scanner observations), `/assist/hosts/{id}/web-interfaces`, `/assist/hosts/{id}/notes`, `/assist/findings[/{id}]`, `/assist/posture`, `/assist/patterns`, `/assist/segments`, `/assist/coverage`, `/assist/vocabulary`, `/assist/notes`, `/assist/names`, `/assist/ingestion-issues`, `/assist/hosts/{id}/access` (NetExec / SMBMap results beside the raw line — which may carry credentials, shown as found), `/assist/uninterpreted-lines?job_id=` (redacted lines a parser did not read), `/assist/attachments/{id}` and `/assist/web-interfaces/{id}/screenshot`, and the `hosts.ndjson` / `report-context.ndjson` downloads.

| Method | Path | Notes |
|---|---|---|
| GET | `/agent/assist/context` | Project + assist-session context. |
| GET | `/agent/assist/hosts` | Paginated, filterable host list (read-only). Accepts the discrete filters AND a `q=` boolean query DSL — see below. **Returns `{items, total, has_more, limit, offset}`** (2.440.0; it was a bare array, and an agent counted a 500-row page as the answer). `ports=` is port numbers (any of them open; a range or name is a 422 naming it, never silently ignored). `services=` is the service the scanner identified on an open port, on any port — the Hosts page's filter and `q=service:` (2.440.0; it used to mean "the name's standard ports open", so `services=vnc` counted masscan-only 5900s and missed VNC on 5800). |
| GET | `/agent/assist/hosts/count` | **v2.291.0** — how many hosts match, same filters + `q` DSL. Shares the query builder with the list (whose `total` is the same COUNT), so "which hosts" and "how many hosts" cannot drift. |
| GET | `/agent/assist/hosts/{host_id}` · `/agent/assist/hosts/by-ip/{ip}` | Host detail with ports, services, vulns — by id or by address. |
| GET | `/agent/assist/scopes` | Scope list. |
| GET | `/agent/assist/scans` | Scan list (`tool=` narrows to one tool). Rows carry `time_source`: `start_time`/`end_time` are UTC instants (with an offset) unless it is `tool_clock` — the scanner's zone-less wall clock, returned without one (v2.434.1). |
| GET | `/agent/assist/session` | Current assist-session metadata. |

**`q=` query DSL (the marquee assist feature).** `GET /agent/assist/hosts` accepts a `q=` parameter carrying the **same boolean query DSL as the Hosts page**: field predicates (`state:`, `ip:`, `hostname:` (alias `host:`), `port:`, `os:` (OS name or OS family), `service:`, `version:` (service product/version) — these three match **open ports only** unless the value names a state after `@`: `port:22@closed`, `service:ssh@filtered`, `port:22@any` (v2.403.0; a closed/filtered port's service name is nmap's guess from the port number); `portstate:` alone is a separate "has a port in this state" condition — `path:` (a content-discovery path), `subnet:`, `tag:`, `label:`, `site:` (`site:none` = inside a scoped subnet that carries no site), `conclusion:` (what a finished review concluded — e.g. `conclusion:needs_evidence`), `cve:`, `vuln:`, `issue:` (exactly one scanner-observation issue by its `issue_key` — `check:…`, `cve:…`, `title:…`), `kind:` (misconfiguration \| vulnerability \| informational), `check:` (a misconfiguration check id, e.g. `check:smb_signing_not_required`), `scope:` (subnet \| name \| none — the three scope-coverage states), `exploitport:`, `header:`, `webtitle:`, `tech:`, `org:`, `certorg:`, `asn:`, `country:`, `note:`, `scan:`, `firstseen:` / `changedsince:` / `vulnsince:` (time windows, (start, end]; quote the ISO value — `firstseen:"2026-09-19T20:00:00Z"`, `changedsince:"<start>..<end>"`, `vulnsince:"critical@<start>..<end>"` with severity and time matched on the same observation), `has:`, `follow:` (watching / in_review / reviewed / none / in_review_any, judged for the session's operator), `assigned:` (alias `assignee:`, taking `me` / `any` / `none` / a username / an id)), combined with `AND` / `OR` / `NOT` and parentheses (comma = OR within a field; a repeated field = AND). `has:` takes one of `eol` · `smb_unsigned` · `weak_auth` · `cert_issue` · `weak_tls` · `cleartext` · `critical`/`high`/`medium`/`low` · `local_admin` · `writable_share` · `exploit` · `critical_exploit` (a critical that is itself exploitable — same row) · `web` · `open_ports` · `tested` · `planned` · `untouched` (no review, note, plan entry or finding endpoint) · `notes` · `stale_review`. It is ANDed with the discrete filter params. `follow:` is judged for, and `assigned:me` resolves to, the **operator who started the session** (so `assigned:me` means "hosts assigned to that operator"); `assigned:`/`assignee:` also accept a **username** (case-insensitive; the value a user actually knows, since ids aren't surfaced) or a numeric user id. The DSL only filters; it never mutates follow/assignment state. A malformed query returns **400** (clean error, not a 500); any `q=` returns 400 if the session has no bound operator. Backed by `host_query_dsl.parse_query` / `evaluate`.

The JWT side (operator) lives under `/projects/{id}/assist/*`: `POST /assist/start` opens a session and returns a fresh key + prompt + per-client MCP config (shown once), with `agent_session_id` (the session's id — the one `/agent-sessions/{id}` takes) beside `assist_session_id` (its detail row, which the `/assist/sessions/*` routes below take; rows there carry `agent_session_id` too, set only for a project session — a pre-consolidation assist row has no session page, so it is null there); `POST /assist/sessions/{session_id}/end` revokes the key (session row kept for audit); `GET /assist/sessions` lists sessions (`?status=active|ended`, `?mine=`, `limit`/`offset`) and `GET /assist/sessions/{id}` returns one with the notes it wrote, its attribution (`agent_model`, `agent_tool`, `prompt_version` — §6.8), and call/note/feedback counts. `GET /projects/{id}/assist-sessions/{sid}/api-activity` is the per-session audit feed (§6.7).

Session status is **derived**: a session whose keys have all expired reports `ended` immediately, with an hourly sweep converging the stored column. Nothing accumulates as "active" waiting to be tidied up by hand.

### 5.8 Cross-workflow endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/agent/identity` | **v2.278.0** — what this key is: its session, the operator it acts for — which since v2.309.0 *is* its authority, so v2.311.0 also returns that operator's `project_role` and a precomputed `can_write_project_data` rather than making the agent infer it or discover it from a 403 — when it expires (`key_expires_at`), and where to renew (`renew_path`). |
| POST | `/agent/tool-suggestions` | **v2.278.0** — propose a tool the catalogue does not have, with rationale. Lands as `suggested` for an admin to curate. Catalogue intake only — since v2.433.0 it neither grants nor withholds anything. Deliberately ungated. |

### 5.9 MCP transport (`POST /api/v1/mcp`)

JSON-RPC 2.0 over a single POST (Streamable HTTP, tools-only subset; protocol `2025-06-18` / `2025-03-26`). `initialize`, `tools/list`, `tools/call`, `ping`.

* **Auth.** `initialize` / `tools/list` / `ping` need no key. `tools/call` reads `X-API-Key` **or** `Authorization: Bearer` and forwards it to the underlying endpoint in-process — so workflow scope, the operator's project role, and the audit log are unchanged. The MCP layer makes no authorization decision.
* **401 vs. isError.** No usable credential answers a real **HTTP 401** with a plain RFC 6750 challenge (`WWW-Authenticate: Bearer realm="BlueStick assist"`, plus `error="invalid_token"` when a key was sent) (a fact about the connection, which a client can act on). A valid key that may not perform *this* call returns the endpoint's 403 as an `isError` tool result (a fact about one call, which the model should read and work around).
* **Unfiltered listing.** `tools/list` returns the whole catalogue (56 tools at 2.442.0; `GET /references/mcp-tools` is the live count) to every session; each tool's `workflows` field (`assist`, `testing`, `scope` — `testing` replaced `plan_generation` and `execution` in v2.442.0; `scope` was `recon` until recon runs were removed) is a grouping for the reference page, not a filter. Listing was never authorisation — the endpoint behind a tool decides on every call.
* **Ceilings (pre-auth).** 1 MiB request body read through a capped stream; JSON-RPC batches capped at 50 messages, and refused outright under protocol `2025-06-18` (which removed batching) — allowed only when `2025-03-26` is declared.
* **Not tools:** the file-shaped endpoints (`report-context.ndjson`, `scopes/{scope_id}/hosts.ndjson`, `scopes/{scope_id}/live-hosts.txt`, `scopes/{scope_id}/web-targets.txt`, `scopes/{scope_id}/named-targets.ndjson`, `POST uploads`) stay `curl` — they belong on disk, not in a model's context.

`GET /api/v1/mcp-telemetry/summary` (**admin**) reports per-tool call counts and outcomes, including `unknown_tools_called`. Full detail — client setup, the certificate (local root CA), the tool registry, the guardrail model — is in [MCP.md](MCP.md).

---

## 6. Important response shapes

### 6.1 `POST /agent/host-tests` (request) and a host test (response)

The JWT route `POST /projects/{id}/host-tests` takes the same body without `agent_model`.

```json
{
  "agent_model": "claude-opus-5-5",
  "tests": [
    {
      "request_key": "smb-review-10.0.1.5-signing",
      "host_id": 123,
      "tool": "netexec",
      "description": "Confirm SMB signing is not required",
      "command": "nxc smb {ip} --gen-relay-list relay.txt",
      "rationale": "Nessus reports signing not required; a relay needs it confirmed from this segment.",
      "expected_result": "The host is listed in relay.txt",
      "references": ["https://attack.mitre.org/techniques/T1557/001/"],
      "priority": "high",
      "label": "SMB review 2026-10-01"
    }
  ]
}
```

`201 {"items": [ … ]}`; each item (also what `GET` and `PATCH` return):

```json
{
  "id": 501,
  "project_id": 4,
  "host_id": 123,
  "host_ip": "10.0.1.5",
  "name_id": null,
  "target_fqdn": null,
  "tool": "netexec",
  "description": "Confirm SMB signing is not required",
  "command": "nxc smb {ip} --gen-relay-list relay.txt",
  "rationale": "Nessus reports signing not required; a relay needs it confirmed from this segment.",
  "expected_result": "The host is listed in relay.txt",
  "references": ["https://attack.mitre.org/techniques/T1557/001/"],
  "priority": "high",
  "label": "SMB review 2026-10-01",
  "status": "proposed",
  "assigned_to_id": null,
  "assigned_to": null,
  "tester_summary": null,
  "source": "agent",
  "agent_session_id": 88,
  "created_by_user_id": 7,
  "created_by": "Dana Analyst",
  "agent_model": "claude-opus-5-5",
  "agent_client": "claude-code 2.1.0",
  "prompt_version": "4.0.0",
  "dismissed_by_id": null,
  "dismissed_at": null,
  "dismissed_reason": null,
  "revision": 1,
  "issue_key": null,
  "issue_title": null,
  "evidence_count": 0,
  "last_outcome": null,
  "unpromoted_findings": 0,
  "finding_ids": [],
  "created_at": "2026-10-01T09:42:11Z",
  "updated_at": "2026-10-01T09:42:11Z"
}
```

`issue_key` / `issue_title` are the weakness the test confirms (set from `vulnerability_id` at proposal; null for a test about nothing scanned). `last_outcome`, `unpromoted_findings` (results that showed an issue and are on no finding yet) and `finding_ids` summarise its evidence so a list row can say where the test stands without a second request.

- `{ip}` and `{fqdn}` in `command` are placeholders the reader resolves; they are stored as written.
- `references` must be HTTP(S) URLs (at most 20); unknown fields are refused (`422`).
- `assigned_to` / `created_by` are display names (full name, else username).
- `revision` goes up by one on every `PATCH`; send the value you read as `expected_revision`.

### 6.2 `POST /agent/evidence` for a test (request)

```json
{
  "host_id": 123,
  "host_test_id": 501,
  "request_key": "smb-review-10.0.1.5-signing-run1",
  "tool": "netexec",
  "command": "nxc smb 10.0.1.5 --gen-relay-list relay.txt",
  "outcome": "finding",
  "summary": "Signing is not required; the host is in the relay list.",
  "raw_output": "SMB  10.0.1.5  445  FS01  [*] Windows Server 2019 (signing:False)",
  "observed_ip": "10.0.1.5",
  "executed_at": "2026-10-01T10:02:00Z"
}
```

`host_test_id` must be a test on the same host (`422` otherwise) and then `request_key` is required. The test's `evidence_count` rises; its `status` does not change — close it with `PATCH … {"status": "done"}`.

### 6.3 `POST /projects/{id}/assist/start` (response)

The one session start (v2.433.0 — the per-plan `/execute` start this section used to show was removed).

```json
{
  "assist_session_id": 31,
  "agent_session_id": 58,
  "project_id": 3,
  "project_name": "Q2 external",
  "agent_id": 9,
  "api_key": "nm_agent_<plaintext shown once>",
  "instructions": "...<markdown block pasted into the user's terminal agent>...",
  "mcp_clients": [ { "...": "per-client connect recipe, with the sandbox flags" } ],
  "mcp_url": "https://<host>/api/v1/mcp",
  "key_ttl_hours": 24
}
```

Status **201**. `api_key` is the plaintext, shown exactly once — the caller must display/copy it before dismissing the response. The hash lives in `api_keys`; subsequent recovery is not possible.

### 6.4, 6.5 — removed

The offline execution bundle (`POST …/test-plans/{plan_id}/export-bundle` and `…/import-results`) was removed with test plans in v2.442.0.

### 6.6 `POST /agent/feedback` (request)

```json
{
  "source": "testing",
  "prompt_version": "4.0.0",
  "overall_rating": 4,
  "api_critiques": [
    {
      "endpoint": "/agent/host-tests",
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

Null metrics are acceptable — the guide explicitly notes that agents running in restricted sandboxes may not see their own token/cost/wall-clock numbers. `project_id` and the session come from the API key; `source` is required in the body (`assist` \| `reconnaissance` \| `testing`); `assist_session_id` is an optional body field for a pre-consolidation assist session.

### 6.7 `GET /api/v1/projects/{project_id}/assist-sessions/{assist_session_id}/api-activity`

The agent API-call log of one session (the per-plan feed, v2.24.0, went with plans). `assist_session_id` is the session's detail row — the `assist_session_id` field on its `/agent-sessions` row, not the session id.

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
      "method": "PATCH",
      "path": "/api/v1/agent/host-tests/501",
      "path_template": "/api/v1/agent/host-tests/{test_id}",
      "path_params": {"test_id": 501},
      "query_params": null,
      "request_body_summary": {
        "expected_revision": 1,
        "status": "in_progress"
      },
      "status_code": 200,
      "response_bytes": 912,
      "duration_ms": 22,
      "scope_id": null,
      "referenced_host_ids": [],
      "referenced_entry_ids": [],
      "referenced_target_ips": []
    }
  ]
}
```

- **Request bodies are summarised, not raw.** Captured for mutations only (POST/PATCH/DELETE), capped at 8 KiB; multipart bodies record metadata only (no file payload). Sensitive-shaped fields (`api_key`, `authorization`, `password`, `secret`, `token`) are stripped as defence-in-depth.
- **Response bodies are NOT captured.** Only the `Content-Length` header value (when set) lands in `response_bytes`.
- **The `referenced_*` lists** are parsed out of path + query + body so a filter like `?target_ip=10.0.0.5` is a single indexed query.
- Authentication: **JWT only** — agents cannot read their own audit log.

### 6.8 Agent attribution (v2.434.0)

The environment probe (`POST /agent/session/environment`, MCP `record_environment`) was removed in v2.434.0 with its columns; BlueStick no longer records the operator's machine or checks it against the tool catalogue. What an `AgentSession` records about the agent comes from three sources (`agent_session_service`):

| Field | Source |
|---|---|
| `generated_by_tool` (the client / harness) | The MCP `initialize` handshake's `clientInfo` — `"<name> <version>"` — when the handshake carries a live key (`record_mcp_client`; it never fails the handshake). Otherwise the first authenticated call's `User-Agent` (a curl agent: `curl/8.5.0`), written once and replaced by a later handshake name, never the reverse. The MCP loopback forwards the client's own `User-Agent`; httpx's default is ignored. |
| `prompt_version` | Set by the server at session start and at resume (`PROMPT_VERSION`). |
| `generated_by_model` | Self-reported: optional `agent_model` (≤200 chars, cut to the column width) on `POST /agent/host-tests`, `POST /agent/evidence`, the `POST /agent/proposals/*` routes and `POST /agent/session/end` — MCP `host_tests_propose`, `record_evidence`, the `propose_*` tools, `end_session`. The session keeps the LAST value reported. |

A host test snapshots the session's model, client and prompt version when it is proposed (`agent_model`, `agent_client`, `prompt_version`), and an evidence record its model and client when it is recorded, so a session that switches models does not relabel earlier work.

---

## 7. Error shapes

**An `/api/v1/agent/…` path that is not an endpoint (v2.444.0)** answers `404` with more than the framework's `{"detail": "Not Found"}`, because the caller is usually an agent that guessed a URL:

```json
{
  "detail": "No agent endpoint at GET /api/v1/agent/recon/context.",
  "hint": "The endpoints that exist are in the guide (GET /api/v1/agents-guide) and, over MCP, tools/list. If you expected this one to exist, say so: POST /api/v1/agent/feedback (MCP submit_feedback) with the path you tried and what you were trying to do.",
  "guide": "/api/v1/agents-guide",
  "feedback": "/api/v1/agent/feedback"
}
```

The call is recorded in the session's API activity when its key is a real agent key (`path_template` null). A missing object on a real route keeps that route's own `404`.

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

- **Prompt sanitization** is applied to `POST /llm-providers/{id}/complete` on the server before forwarding. Patterns stripped: `X-API-Key: nm_agent_*` lines, credential bullets (Access key / Secret key / Password / Username / API key / PDCP token / Secret), and bare `nm_agent_` tokens ≥20 chars. (The frontend twin, `utils/promptSanitizer.ts`, was deleted with the in-app agent panel in 5.313.0.) **If you change the bullet shape in `agent_prompt_service._integration_block`, update `prompt_sanitizer.py` in lockstep or secrets will leak.**
- **SSRF validation** runs on every `base_url` accepted by `/llm-providers/` and `/integrations/`. The validator resolves the hostname and rejects RFC1918, CGNAT, loopback, link-local, and IPv6 equivalents (including `169.254.169.254` metadata). Private addresses are allowed only by carve-out: for LLM providers, Ollama alone; for integrations, ollama, nessus, openvas, nuclei, burp and generic_api. Metadata / link-local ranges are refused regardless.
- **IP-pinning transport** re-resolves hostnames at connect time inside every outbound LLM provider call, closing the DNS rebinding TOCTOU window between the validator and the actual request. Redirects are disabled so a 302 can't land on a private IP.
- **Max-length caps** on high-risk text fields: HostNote body 16 KB, plan title 200 chars, plan description 4 KB, entry rationale 4 KB, entry notes 8 KB, entry findings 16 KB, archive reason 2 KB.
- **File uploads** enforce per-extension magic-byte checks. `.xml`/`.nessus` must start with `<`, `.json` with `{` or `[`, `.gnmap` with `#` or `Host:`, text files may not contain NUL bytes. Filenames are slugified before filesystem use. Chunk-level size cap prevents unbounded streams.
- **JSON depth guard** on `/import-results` rejects payloads nested deeper than 20 levels via a byte-level pre-scan.
- **XML parsing.** Two-pronged defense (v2.41.0): `nessus_parser.py` uses `defusedxml.ElementTree`; the nmap, masscan and openvas parsers use lxml through `xml_stream_helpers.iterparse_safe` (`resolve_entities=False, no_network=True, huge_tree=False`). Both approaches disable external entities, DTD fetching, and entity expansion at parse time.
- **EyeWitness ZIPs** (v2.41.0) have per-file (50 MB), running-total (500 MB), and entry-count (5000) decompression-bomb caps. The streaming extractor counts bytes mid-stream and aborts + unlinks the partial file if either cap is exceeded, so a spoofed central-directory size field can't defeat the check.
- **BloodHound JSON ≥50 MB** streams via `ijson` instead of `json.load` (v2.41.0); the structure (`[…]`, `{"data": […]}`, `{"computers": […]}`) is auto-detected by peeking the first 64 KB.

---

## 9. Operational notes

- **Schema management.** Tables are owned by **Alembic**. Every backend boot runs `alembic upgrade head` before serving traffic. Migrations live in `backend/alembic/versions/`; baseline at `b46cd59c17f5_baseline_schema`. The previous startup-DDL path has been retired — the model is the schema, and Alembic enforces it.
- **Upload flow is async.** `POST /upload/` returns a queued `IngestionJob` — poll `GET /upload/jobs/{id}` for status. Don't expect a parsed scan in the upload response.
- **API keys are shown once.** The `/assist/start` (and `/agent-sessions/{sid}/resume`) response includes the plaintext key exactly once. Store it or discard it immediately; recovery is not possible. The hash lives in `api_keys`.
- **Orphan jobs get reaped.** Jobs stuck in `processing` with a heartbeat older than `INGESTION_JOB_TIMEOUT` × `INGESTION_ORPHAN_CUTOFF_MULTIPLIER` (default 1.5) are re-queued automatically by the worker's reaper, up to `INGESTION_MAX_RETRIES` while the file still exists; after that they are failed ("worker likely crashed") and admins are notified.
- **Workflow-scoped agent guide** (`documentation/AGENT_GUIDE.md`). Agents may fetch `GET /api/v1/agents-guide?workflow=testing` (or `reconnaissance`, or `assist`) to get the workflow-sliced subset; omitting `workflow` returns the whole guide. The older names `plan_generation` and `execution` resolve to `testing`. The server parses HTML-comment section markers so one source file emits multiple slices.
- **Health probes.** nginx serves `/live` (liveness, static) and proxies `/health` and `/ready` to the backend's `/health` (5 s timeouts on purpose); the frontend container's Docker HEALTHCHECK requests `https://localhost/`.
- **Version visibility.** `GET /` returns `{message, version, frontend_version, instance_id, cors_origins}`. The UI shows both versions in the user menu under **About BlueStick**. Backend and frontend stay in lockstep per-release; always update both.

---

This document was last reconciled with the routers at v2.428.0 (frontend 5.309.1, 2026-09-26); the agent, host-test and session sections were brought up to v2.442.0 (2026-10-01). **It is not an exhaustive route list and never stays one for long** — sections 4 and 5 give the shape and the contracts that matter; for every route, parameter and schema use the live OpenAPI at `https://<host>/docs` (Swagger UI), `/redoc`, or `/openapi.json`, all proxied by nginx (the backend's own :8000 is not published). Use `/docs` (Swagger UI) for interactive exploration and field-level schemas — this guide is architectural context and high-signal shape references, not a replacement for OpenAPI.
