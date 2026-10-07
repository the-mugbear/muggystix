# BlueStick API Guide

> **Last verified against:** backend 2.429.0 / frontend 5.310.1 (2026-09-26); the areas changed since then were re-checked against 2.450.0 / 5.329.0 (2026-10-02) — and see the note at the end: the live OpenAPI is the authority for the full route list.

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

{"username": "admin", "password": "<your password>"}
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

There is ONE operator entry point: `POST /api/v1/projects/{id}/assist/start` (floor `auditor`), the **Operations → Start Agent Session** dialog. Pages about one object (a scope, a host, a host selection) open the same dialog with a one-line task to hand the agent; they mint nothing of their own.

The agent opens whatever work it needs itself, with the same key and in whatever order the work needs: it reads a scope (`GET /agent/scopes`, `/agent/scopes/{scope_id}/subnets|domains` and the target files), uploads scanner output (`POST /agent/uploads`, nothing to open first), proposes tests on hosts (`POST /agent/host-tests`) and records what it ran as evidence (`POST /agent/evidence`, with `host_test_id` when it answers a test) — nothing waits on approval (§4.11). Operators list, end and resume sessions at `GET /projects/{id}/agent-sessions`, `POST …/agent-sessions/{sid}/end`, `POST …/agent-sessions/{sid}/resume` (resume rotates the key and re-issues the prompt; the previous key is revoked).

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

`get_current_agent` (`app/api/deps.py`) authenticates the key and resolves its session; `enforce_agent_operator_access`, mounted on every `/agent/*` router, then re-checks the **operator's** project role on that request. That role is the only authorisation: a key is not limited to one kind of work.

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

**Audit events.** Every change above is written to the audit log (`GET /audit`, global admins) with the project's id and who did it. The `action` values: `project_created`, `project_updated`, `project_archived`, `project_unarchived` (a `PUT` that flips `is_archived` is recorded as one of those two, any other `PUT` as `project_updated`), `project_deleted`, `project_member_added`, `project_member_role_changed`, `project_member_removed`.

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
| GET | `/portfolio/dashboard` | Cross-project summary (host count, open criticals, recent scans, attention items) scoped to projects the user can see. A project's `active_sessions` counts its open agent sessions (`agent_sessions.status = 'active'`). |
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
| GET | `/feedback/` | admin | List all agent feedback rows. Supports `?status=new\|reviewed\|actioned\|dismissed`, `?source=assist\|reconnaissance\|testing` (older rows may carry `plan_generation`, `in_session_execution` or `exported_execution`), plus `min_rating`, `has_tool_suggestions`, `has_api_critiques`, `search`, `project_id`, `skip`, `limit`. Returns the standard `Paginated` envelope (`{items, total, skip, limit, has_more}`; v2.428.2 — it was a bare array). Each row carries who and where: `project_name`, `agent_name`, `client_name` (the MCP client the session connected with), `agent_session_id` (the session; its page, with its API calls, is `/agent-sessions/{agent_session_id}`), `session_has_page` (false for a pre-v2.337.0 session that no page lists) and `session_api_calls`. (`session_page_id` and `assist_session_id` went in v2.449.0: a session has one id.) |
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
| GET | `/references/tools` | **v2.277.0** — the tool catalogue: every tool BlueStick knows about, with install/usage knowledge, phases, intrusiveness and whether BlueStick parses its output (`ingestible`). `?status=reference\|suggested\|rejected`, `?category=`. One source for the Tool Reference page and the agent's `list_tools`. No status is a permission: the operator decides what their agent runs. |
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
| GET | `/scans/{scan_id}` | Scan detail. `scan_info` lists what the scan was asked to probe (nmap's `<scaninfo>`: `type`, `protocol`, `numservices`, `services` — the raw port-range string); the scan page prints it as "Scanned:". |
| GET | `/scans/{scan_id}/host-snapshots` | The hosts as THIS scan observed them (`state`, `search`, `skip`, `limit` ≤1000; `Paginated`): `hostname_at_scan`, `state_at_scan`, `host_created`, `observed_port_count` / `open_port_count`, at most 50 `ports`, and `credentialed` — whether the scan authenticated to the host (true / false when the scanner said so — Nessus; null when it did not say). Does not change as later scans run. |
| DELETE | `/scans/{scan_id}` | **Project admin** (`require_project_role(ADMIN)`; an analyst gets 403). Deletes scan + history rows; hosts seen in other scans are preserved. |
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
| POST | `/hosts/{host_id}/follow` | Set status: `in_review`, `reviewed`. Per-user. (`watching` is a retired state: still accepted for old clients, offered nowhere.) |
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
| GET | `/findings` | List findings. Filterable; `sort` = `severity` \| `status` \| `title` \| `host_count` \| `source` \| `created_at` \| `owner`. **A list row's `hosts` is a preview of at most 5 endpoints** (the first by id; with `host_id=X`, host X's own endpoint rows come first and the rest fill the remaining places by id — so a host's findings read that host's `host_status` from the list without fetching each finding); `host_count` is the true number of endpoint rows and `endpoint_status_counts` covers all of them (review 2026-10-01 C2 — a row used to carry every endpoint). Report text is left off the list. |
| GET | `/findings/{finding_id}` | Finding detail: **every** endpoint (`hosts`, each with `id`, `host_id`, `ip_address`, `hostname`, `name_id`, `fqdn`, `host_status`), `host_count`, `endpoint_status_counts`, `report_text`. |
| POST | `/findings` | Create a finding. |
| PATCH | `/findings/{finding_id}` | Update metadata. |
| POST | `/findings/{finding_id}/status` | Transition disposition (`summary` is the reason for a terminal determination: the pages ask for it, the server accepts none). |
| POST | `/findings/{finding_id}/hosts` | Attach a host to the finding. |
| DELETE | `/findings/{finding_id}/hosts/{host_id}` | Detach a host. |
| PATCH · DELETE | `/findings/{finding_id}/endpoints/{finding_host_id}` | ONE endpoint row's own state (`open` / `remediated` / `retest` / `false_positive`). The finding's status is the ISSUE's; never read it as the state of a given host. Responses carry `endpoint_status_counts`. |
| PATCH | `/findings/{finding_id}/endpoints` | Analyst. **Several endpoints at once** (review 2026-10-01 B13): `{finding_host_ids: [1–500 ids], host_status, summary?}` → the finding (as `GET /findings/{id}`). The same change as the single route, with the same history line per endpoint that moves (`summary` is appended to each). All-or-nothing: an id that is not an endpoint of this finding is a `404` naming it and nothing is written; an unknown `host_status`, an empty list or more than 500 ids is a `422`. Endpoints already in that state are left alone. People only — an agent's endpoint change is `POST /agent/proposals/endpoint-status`. |
| GET | `/vulnerabilities/{vuln_id}/promote-preview` | What promoting this scanner observation would do — the finding it would join, the hosts carrying the issue. |
| POST | `/vulnerabilities/{vuln_id}/promote` | Promote or dismiss a scanner observation: `{vuln_id, status?, severity?, owner_id?, summary?, scope?}` — `vuln_id` is required and must equal the path id (400). **`scope: "host"`** acts on the inspected host only — a `false_positive` dismissal defaults to it; a promotion may use it (v2.366.0: the finding is still the issue's, one per `dedup_key`, but only this host is attached, so "confirmed" is never recorded for hosts nobody verified). `scope: "issue"` is every host carrying it, and is the API default for a promotion. `accepted_risk` is issue-wide only: with `scope: "host"` → **422**. |
| GET | `/findings/{finding_id}/history` · `GET`/`POST /findings/{finding_id}/notes` | Status history; the comment / evidence thread (a terminal determination's reason is optional). A same-status history row records a change that is not a disposition: an endpoint's state, added hosts, or report text from an accepted proposal. An accepted proposal names itself in the row's `summary` (2.439.1). |
| GET | `/findings/{finding_id}/images` | The finding's images (on its comments and its source-note thread) as the client report sees them: `{items: [{id, note_id, filename, caption, content_type, size_bytes, in_report, printable, placed_in, uploaded_by_id, by_agent, created_at, can_edit}], caption_max}`. `placed_in` is the report-text fields whose Markdown places the image with `![caption](evidence:<id>)`; a ticked image with none prints under Evidence. `can_edit` is the caller's right to tick, caption or delete it. Any member. |
| PATCH · DELETE | `/hosts/notes/attachments/{attachment_id}` | An image on a note or a finding comment. `PATCH {include_in_report?, caption?}` (either or both; analyst, and the image's uploader, a project admin or a global admin): the "In report" tick, and the caption the report prints under the image (one line, at most 2,000 characters → 422 when longer; `null` or `""` clears it and the report prints the file name). **An image that a finding's report text places cannot be un-ticked or deleted: `409` naming the section(s)**; remove the `![…](evidence:<id>)` reference from the section first. Deleting the comment or note that holds such an image is refused the same way. An issued report is unaffected either way: it holds its own copy of each image. |
| POST | `/findings/bulk/status` · `/findings/bulk/assign` | Bulk transitions and assignment. |
| GET | `/scanner-observations` | **v2.386.0** — scanner rows grouped by ISSUE (`Vulnerability.issue_key`) across the project's hosts, with `host_count` and `judged_host_count`. `search`, `severity`, `kind` (misconfiguration \| vulnerability \| informational), `include_judged`, `min_hosts`, `sort` (`severity` default \| `hosts` = most widespread first, v2.429.0), `skip`, `limit` (≤200). Drives the Findings page's *Scanner observations* view. |
| GET | `/scanner-observations/hosts?issue_key=` | The hosts carrying one issue (`limit` ≤5000). |
| POST | `/scanner-observations/promote` | Analyst+. `{items: [{issue_key, host_ids?}]}` — promote several issues at once, each on every host carrying it or exactly the named ones (validated all-or-nothing; 422). An issue that already has a finding JOINS it, and the bulk path never changes that finding's status. |

**Agent proposals and evidence (v2.436.0).** An agent never changes what the team concluded directly: it proposes, and a person decides.

| Method | Path | Notes |
|---|---|---|
| GET | `/proposals` | Proposals, newest first. `status` (default `pending`; `accepted` · `rejected` · `superseded`), `kind` (`finding_text` · `finding_create` · `observation_promote` · `observation_dismiss` · `endpoint_status`), `finding_id`, `host_id` (its observations and endpoints), `agent_session_id`, `limit` ≤500, `offset`. A `finding_text` row carries `current_value` (the finding's text now) beside the proposed `payload.value`, and (2.454.0) `base_value` — the field's text when it was proposed, also kept as `payload.base_value`, set by the server — with `base_recorded` (false for proposals made before 2.454.0) and, while pending, `changed_since_proposed` (the field was edited after the draft was written; null when unknown). Accepting replaces the whole section, so the page warns on it; every row carries its session, `agent_model`, `agent_client`, `prompt_version`, `source` (`agent` · `llm_draft`), and `target` {`finding_title`, `observation_title`, `host_id`, `host_ip`} (v2.437.0). `finding_id` also matches promote / dismiss proposals on a scanner observation that evidences the finding (2.439.0). The same rule decides the report's `pending_proposals`, because dismissing one drops an endpoint from the report. |
| GET | `/proposals/summary` | `{pending, by_kind, pending_mine, by_kind_mine, viewer_is_project_admin}` — the project's pending count and the caller's own (proposals about findings they authored or own, the ones they are notified about). The top bar shows a person their own, and a project admin the project's (2.440.0: an admin's review of every finding had put the whole run in every member's top bar). `GET /proposals?mine=true` is the list narrowed the same way; the Proposals page's "Findings: mine / everyone's" and a proposal notification's link (`scope=mine`) use it. |
| POST | `/proposals/{id}/accept` | Analyst+. `{note?, edited_value?}` (`edited_value`: report text only — accept with your edit). Runs the SAME code as the equivalent click, as you: report text needs the finding's author or a project admin (403 otherwise, the proposal stays pending). Accepting report text marks that field's other pending proposals `superseded`. A refusal from the underlying action (e.g. the target changed) is kept on the proposal's `error` and the proposal stays pending; a decided one → 409. |
| POST | `/proposals/{id}/reject` | Analyst+. `{note?}`. |
| POST | `/proposals/bulk` | Analyst+. `{ids (≤200), action: accept|reject, note?}` — each decided on its own; returns `decided` and `failed` [{id, status_code, detail}]. An accept never chooses between several pending drafts of one finding's field in the same batch: those fail with 409 "choose one on the finding" (2.439.0). Deciding locks the proposal row, so two concurrent decisions on one proposal apply it once; the other gets 409. |
| POST | `/evidence/{id}/finding` | Analyst (v2.443.0; joins the issue since v2.445.0). `{title?, severity?, status?: open\|confirmed (default confirmed)}` — makes a finding of an evidence record whose outcome is `finding`. **When the record's test names a scanner observation still on the host** (`issue_key`), this IS that observation's promotion on this host (`promote_or_dismiss_vulnerability`, `scope: host`): it joins the issue's finding if one exists, takes the issue's title and severity — the `severity` sent is ignored on this path (v2.445.1; it used to override the scanner's) — and returns `joined_issue: true`. **Otherwise** it creates a finding from `title` + `severity` (both then required, `422` without), attached to the record's host (at the name its test was aimed at) and owned by the caller (v2.452.2 — as a promoted observation's finding is; unowned, it was on nobody's Operations list). Either way the record is linked to the finding. **Joining never re-judges the issue** (review 2026-10-01 R9): a result on one host moves an existing finding only from `open` / `retest` to `confirmed`; a finding the team concluded (`accepted_risk`, `remediated`, `false_positive`) keeps its status, and `confirmed` is never taken back to `open`. The response's `status` is the finding's as it stands, which may differ from the one sent. `409` when the record already belongs to a finding, `422` for any other outcome. Returns `{finding_id, title, severity, status, evidence_id, joined_issue}`. |
| GET | `/evidence` · `/evidence/{id}/raw` | Evidence records (an agent's, or a person's test result) (`host_id`, `host_test_id`, `finding_id`, `agent_session_id`, `unlinked` — `true` keeps records that answer no test, the host page's "Other evidence" — `limit`, `offset`) with a 2,000-character preview · the whole raw output (text). Any member. |

**Notifications (v2.437.0).** A proposal on a finding notifies its author and its owner (`type: "proposal"`), except the person whose agent or draft it is: ONE unread notification per person per agent session (per finding for an in-app draft), updated as the run proposes more. `finding_id` is set when it covers one finding; otherwise `source_type: "agent_session"` + `source_id` point at the session's proposals. The client report's summary carries `pending_proposals` [{id, ref, title, count}], `agent_images` (images from agent-written notes) and `agent_evidence_records` (test results an agent recorded, of the `evidence_records` printed) — warnings before issuing, never blocks.

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
| GET | `/reports/jobs/{job_id}` | Job detail (status, progress, error). Every job response carries `requested_by_id` (the user who queued it; null once that account is deleted) — the requester may dismiss, retry or cancel their own job. |
| GET | `/reports/jobs/{job_id}/download` | Stream the finished artifact (only once `status=complete`). |
| POST | `/reports/jobs/{job_id}/dismiss` | Hide a finished/failed job from the report-jobs tray. Needs **analyst**, or to be the person who requested the export (403 otherwise). |
| POST | `/reports/jobs/{job_id}/retry` · `/cancel` | Re-queue a failed job; cancel a queued job before the worker claims it (409 otherwise). Needs **analyst**, or to be the person who requested the export. All three refuse a client-report job (`report_type: "client"`) with **409**: those belong to the Reports page (preview again, or `POST /client-reports/{id}/render`). |
| GET | `/reports/limits` | Host cap per format (`null` = every matching host). CSV, JSON and the agent package are uncapped (v2.394.0); HTML is capped at `ReportGenerator.MAX_REPORT_HOSTS`; the markdown bundle at `REPORT_MAX_INMEMORY_HOSTS`. |
| POST | `/reports/draft/finding-text` | `{finding_id, fields?, provider_id?}` → `{proposals, declined, provider_id, provider_type, model_id, usage}`: the operator's LLM provider drafts Markdown for one finding's empty report sections (default: the empty ones of description / impact / recommendation). **Since v2.437.0 the draft is a set of `finding_text` proposals** (`source: llm_draft`, the provider's model), reviewed and accepted like an agent's (§4.5); nothing is written until one is accepted. **The model may decline a section** (2.455.0): one the finding's data does not support gets no proposal and is listed in `declined` `{field: what would let it be written}` (also named in the drafts' `rationale`); declining every section is a 200 with no proposals, not a failure. Analyst+ (proposing changes nothing; accepting still needs the author or a project admin); the finding's author and owner are notified when someone else drafts. 400 no provider / nothing empty; 502 provider failed or answered unreadably. |

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
`POST /assist/start` (§1.2 — the one entry point). Sessions are managed uniformly: `GET /projects/{id}/agent-sessions` (the timeline), `GET /projects/{id}/agent-sessions/{sid}` (one session, v2.432.0), `POST /projects/{id}/agent-sessions/{sid}/end` (owner or project admin — this is what revokes the key) and `POST …/{sid}/resume` (rotates the key, re-issues the prompt and MCP setup). A key with no session
binding is **rejected at authentication** (403).

### 4.11 Host tests

A **host test** is one check proposed for one host — by a person or by an agent — and it is shown on that host's page (the Tests section). There is nothing to register or open first, and no approval step. (Upgrading an instance from before v2.442.0: see `README.md`.)

A test carries `tool`, `description`, `command` (may use `{ip}` / `{fqdn}`), `rationale`, `expected_result`, `references`, `priority` (`critical`…`info`), `label`, `target_fqdn` (optional — a name observed at the host), `status` (`proposed` → `in_progress` → `done`, or `dismissed`), `assigned_to_id`, `tester_summary`, `source` (`agent` \| `person`), the proposer's attribution (`created_by`, `agent_session_id`, `agent_model`, `agent_client`, `prompt_version`), `revision` and `evidence_count`. What a test **produced** is not written on the test: it is an evidence record that names it (`host_test_id`; §5.6a).

| Method | Path | Notes |
|---|---|---|
| POST | `/host-tests` | Analyst. Body `{tests: [...]}` — 1–200 tests, each `{request_key, host_id, tool, description, rationale, command?, expected_result?, references?, priority?, label?, target_fqdn?, vulnerability_id?, assigned_to_id?}`. 201 `{items}`. `vulnerability_id` (v2.445.0) names the scanner observation the test confirms — it must be on the same host (`422`) — and is stored as the issue's identity (`issue_key`, `issue_title`), so the link survives a re-scan that replaces the row. The batch is validated as a whole (an unknown host is a 404 and nothing is written). `request_key` makes a retry safe: the same key with the same content returns the existing test; with different content it is a `409`. `target_fqdn` must be a name observed at that host (`422`); an assignee must be a project analyst or admin (`422`). A test created for someone else notifies them — one `assignment` notification per assignee per request (`source_type: host_test`), none for a retry or for a test you give yourself. |
| GET | `/host-tests` | Any member. `host_id`, `status`, `label` (exact), `assigned_to_id`, `agent_session_id`, `mine` (assigned to the caller), `active_only` (proposed or in progress), `q` (a Hosts query — the tests on the hosts it matches), `limit` (≤200) / `offset`. Returns `{items, total, has_more}`, newest first. |
| GET | `/host-tests/{test_id}` | Any member. One test; `404` for another project's. |
| PATCH | `/host-tests/{test_id}` | Analyst. `{expected_revision, status?, assigned_to_id?, tester_summary?, dismissed_reason?}`. `expected_revision` is the revision the caller read: a stale write is a `409` and changes nothing. `status: "dismissed"` needs `dismissed_reason`; `status: "done"` needs an evidence record for the test or a `tester_summary` saying why none was run (`422` otherwise). `assigned_to_id: null` unassigns; handing the test to another person notifies them (`assignment`, `source_type: host_test`, `host_id` set). |
| POST | `/host-tests/{test_id}/result` | Analyst (v2.443.0) — a person's result for a test they ran, in one call: `{expected_revision, request_key, outcome: finding\|no_finding\|inconclusive\|failed, summary, command?, raw_output?, observed_ip?}`. Writes the evidence record (the same one `POST /agent/evidence` writes; `command` defaults to the test's with `{ip}`/`{fqdn}` filled) and moves the test: `finding` / `no_finding` → `done`, the others → `in_progress`. `409` on a stale revision (nothing is written). Every result takes the test's revision — one that leaves the status unchanged too (v2.445.1) — so a copy read before it is stale. A repeated `request_key` with the same result returns the stored record and writes nothing; the same key with a changed outcome, summary, output or author is a `409` (v2.445.1), never a success that kept the old record. The same key sent twice AT ONCE (a double click) is one record too: the second request returns the stored record and the test as the first left it, not a `409`. `raw_output` is at most 5 MB (`422` over 5,242,880 characters, `413` when the text is within that but larger once UTF-8 encoded); NUL is refused in the other text fields (`422`) and removed from `raw_output`. Returns `{test, evidence}`. Page route only: an agent uses `POST /agent/evidence` then `PATCH`. |

**Planned, tested, untouched — one definition each** (`host_test_queries`, `host_query_predicates.untouched_conditions`), used by the Hosts list, the DSL (`has:planned`, `has:tested`, `has:untouched`), `/coverage` and the address terrain (Posture's "Where the team has been"): a host is *planned* while it has a test that is `proposed` or `in_progress`; *tested* once an evidence record with outcome `finding`, `no_finding` or `inconclusive` exists for it (a `failed` attempt or an `info` record is not a test); *untouched* when it has no follow, note, non-dismissed test, evidence record or finding endpoint. The DSL's `testlabel:"…"` matches hosts carrying a test with that label, whatever its status.

### 4.12 Scanning by agents

An agent's scanning is visible as its uploads (the scans and upload batches it produced; each job carries `ingestion_jobs.agent_session_id`) and as its session's API-call feed on the session page (`/agent-sessions/{id}`). The agent-side scope reads and upload routes are in §5.

### 4.13 Other project-scoped routers

These mount under `/projects/{project_id}/...` alongside the above. Most are dashboard/analytics or host-management surfaces driving specific UI pages; see `/docs` for the per-route field shapes.

| Base path | Purpose |
|---|---|
| `/posture` | Security Posture roll-up — deterministic condition label (`action_required` / `needs_assessment` / `insufficient_evidence` / `no_urgent_signals`), plain-language conclusion, and the condition-family × site heatmap. Drives the frontend **Posture** tab. |
| `/posture/evidence` | Per-assessment-domain coverage (eligible vs assessed hosts: discovery, service/version, vulnerability, web/TLS, auth/SMB/AD, validation) + `matrix` (domain × segment: sites, or most-specific subnets when no site is defined, plus `unmapped` for hosts outside every scoped subnet; each cell `eligible` / `assessed` / `gap`) + contributing tools + parse-error data quality. Three states only — assessed, not assessed, not applicable: a project is one assessment window, so nothing is judged by age. The `vuln_assessment` domain also carries `credentialed` = `{credentialed, not_credentialed, credentials_not_stated}` when it has assessed hosts: of the ASSESSED hosts, how many a vulnerability scan authenticated to (the three add up to `coverage.numerator`; "assessed" itself is unchanged — any scanner run counts). A clean result from a scan that did not log in is weaker evidence; each count is the Hosts query `vulnscan:credentialed` / `uncredentialed` / `unstated`. `GET /posture/evidence/{domain}/gaps?segment=<key>` lists one cell's hosts. Drives the frontend **Evidence** tab. |
| `/insights/subnets` | Per-subnet insights (exposure + neglect + hygiene, worst-first). Drives the frontend **Segments** tab (Subnet lens). |
| `/insights/systemic` | Systemic insights — pattern families, estate blind spots, segment outliers, diagnostic profiles. Drives the frontend **Patterns** tab. |
| `/attention` | Project "needs help" attention model (the site-metrics arc). |
| `/sites` | Site entity management (tier / owner / coverage). |
| `/coverage` | Project coverage summary (drives v3 Operations). |
| `/workbench` | Batched Operations workbench + since-last-visit cursor — the CALLER'S own page (v2.451.0), shown as tabs, one full list at a time (v2.452.0). **The tabs' lists, each paged with `limit` (1–100, default 25) and `offset`, each the same function that produces its count below — so the count is the size of the list (pinned by `tests/test_operations_tabs.py`):** `GET /workbench/findings` (findings that need the caller; `?need=decide|write` (v2.453.0) narrows it to the findings with a decision waiting — under investigation, or a proposal — or to those missing only report text; `need_counts {decide, write}` are whole-list figures that add up to `total_open`, also on `my_work` as `findings_to_decide` / `findings_to_write`; `total_open` is the whole list), `GET /workbench/hosts` (hosts the caller has In Review, most recently taken first, with open ports and critical / high observation counts; `in_review_count` is the whole list — the hosts `follow:mine` lists), `GET /workbench/tests?kind=assigned\|in_review\|triage` (tests to do: ONE list, each test once under its strongest kind — assigned to the caller, on a host they review, unassigned critical / high "free to claim" — by priority within a kind; rows carry `tool`; the list's size is `total_open`, or `group_counts[kind]` with `kind`, and the counts stay whole-list; an unknown `kind` is 422), `GET /workbench/followups` (the caller's finished reviews that are not done; `total`; 503 when it cannot be computed — never an empty list) and `GET /workbench/investigate` (below). `GET /workbench?include_rows=false` is the page's light call: every count, the blockers and the since-last-visit diff with every `items` list empty (13 statements against 23; default `true`, which the agents' read keeps). `GET /workbench?include_investigate=` (default true) returns `my_work` (`total`, `hosts_in_review`, `tests_assigned`, `tests_on_hosts_in_review`, `findings_needing_me`, `to_claim` — the caller's queue as one number), `my_queue`, `my_tasks` (rows cut PER GROUP; `group_counts` counts each test once under its strongest reason, `reason_counts` overlap), `my_findings` (owned findings that NEED their owner: `needs[{kind: under_investigation \| missing_text \| proposals, text}]`, `missing_text[]`, `pending_proposals`; `total_open` counts those, not ownership), `followups` ("Changed since review": the caller's OWN finished reviews that are not done — never a teammate's; one row per host, `total` hosts, exactly the Hosts list `follow:revisit`; rows no longer carry `mine` / `reviewer` / `reviewer_id`, and `mine_total` / `host_total` are gone), `since_last_visit`, `blockers`. There are no project-wide measures: `GET /workbench/measures`, `include_measures` and the `measures` / `measures_unavailable` fields were removed in v2.451.0 (project status is Posture's; the same numbers are the totals of `has:tested` and `has:untouched has:critical`, and the terrain's sums). `GET /workbench/investigate?tier=1..5&limit=1..100&offset=` is the "Untouched, with a reason" queue on its own (untouched hosts with a reason, ranked by a stated tier in SQL; whole-queue `tier_counts` and `queue_total` whatever the paging; 503 on failure). `POST /workbench/followups/still-reviewed {host_ids (1–200)}` moves `reviewed_at` to now on the caller's OWN finished reviews of those hosts — conclusion untouched, one statement, all or nothing: 409 with `detail.host_ids` when any host is not in the project, has no finished review of the caller's, or was concluded `needs_evidence`. `GET /workbench/terrain` counts hosts per /24 (IPv6 /64) as tested / planned / worked / untouched for the address terrain, which the Posture overview shows since 5.330.0 (the path is unchanged); `POST /workbench/seen {as_of}` acknowledges the displayed snapshot. Removed in v2.451.1, both unread by any page: the response's `team_review` roster (the team's In Review hosts are the Hosts list `follow:in_review`) and `GET /workbench/my-activity` (404; "my activity" is the Collaboration page, `/activity?author=me`). |
| `/client-reports` | **v2.380.0** — the client report (Reports page). Reads need auditor, drafts analyst, issuing and re-rendering project admin. `PATCH` and `DELETE` of a draft read it under a row lock, so one that races an issue answers 409 instead of changing or deleting the issued report. **An addendum's summary `delta`** is `{new_findings, findings_with_new_endpoints, findings_with_changed_severity, withdrawn}`: the third counts reported findings whose severity differs from the baseline's frozen value (severity only — never title or status). In the dataset such a finding has `change: "severity_changed"` (or `"new_hosts"` when it is also on further systems) and `previous_severity` / `previous_severity_label`. **How a finding was confirmed:** with a template whose `template.json` sets `"evidence_records": true` (`pentest`), each finding's `confirmations` lists its linked evidence records with outcome `finding` (`id`, `tool`, `host`, `summary`, `command`, `output`, `output_truncated`, `executed_at`, `date`, `by`, `by_agent`; at most 10, `confirmations_omitted` counts the rest), frozen at issue; a finding keeps them only where the report prints them (a finding the template shows without its details — an addendum's already-reported finding — has an empty list), and the summary carries `evidence_records` (printed), `evidence_records_not_printed` and `agent_evidence_records`. **Images:** each finding carries `images` (every image ticked "In report": `attachment_id`, `file`, `caption`, `placed_in`, and — what THIS report's template does with it — `printed` and `printed_in`), `placed` (per report-text field, the images its Markdown places with `![caption](evidence:<id>)` — only that finding's own ticked images) and `evidence` (the images no field places: the trailing evidence block); the summary carries `images`, `images_placed` / `images_unplaced` (what the authors did), `images_skipped`, and where the report's template prints them: `images_printed` (inside a written section), `images_trailing` (the trailing evidence block), `images_not_printed` (ticked, printed nowhere by this template) — the three add up to `images` — with `images_not_printed_reasons` `{finding_not_detailed, section_not_printed, no_evidence_block}` and `template_images` `{fields, trailing}` (the template's `template.json` → `images`). They are measured by filling the template with the report's data, are `null` when that cannot be done, and are absent from a report issued before they existed (say nothing about where its images print). A placement is exactly `![alt](evidence:<id>)` (optionally with a double-quoted title) on one line. Issuing copies the bytes of every PRINTED image into the report's own storage (`uploads/client_reports/<project>/<report>/evidence/`, table `report_images`) and the issued report renders from those copies; `/{id}/issue` answers `409` when a printed image's file is missing from storage (an image the template does not print is neither copied nor required). Discarding a draft removes any copies a failed issue left for it. `GET`/`POST ""`, `GET`/`PATCH`/`DELETE /{id}`, `POST /{id}/preview` (202, a report job), `/{id}/issue` (freezes and numbers it), `/{id}/render` (retry an issued report's files), `/{id}/revise` (201), `GET /{id}/files/{fmt}`, `GET /{id}/scope.csv` (v2.441.0: the complete scope as CSV — a draft's live scope, an issued report's frozen one, so its SHA-256 matches what the report prints. A report whose scope is over its template's `scope_inline_max` / `scope_domains_inline_max` names this file instead of listing the scope, and its summary carries `scope_external` {networks, domains, inline_max, domains_inline_max, file: {name, sha256, bytes}}. Cells starting `=`, `+`, `-` or `@` get a leading apostrophe, so a spreadsheet does not run them); `GET`/`PUT /profile`, `GET /templates` (and `/templates/problems` for templates that cannot be offered), `GET /team`. **Template files (v2.431.0):** `PUT /templates/{name}/assets/{asset_id}` (multipart `file`; **global admin only**, instance-wide, audited as `report_template_asset_uploaded`) validates the bytes against the asset's kind and guidance (PNG / JPEG header and pixel size, a macro-free `.docx`; `max_bytes`, `min_width`/`min_height`; a different `aspect` is a warning) and answers `{template, warnings}` (422 with the reason when refused); `DELETE` the same path removes the upload (404 when none); `GET …/{asset_id}/preview` serves the raster image the render would use (upload, else server-installed; never an SVG or the Word file). Each listed asset carries `present`, `installed`, `source` (`uploaded`/`installed`/null), `kind`, `uploadable`, the guidance fields and `upload` (size, pixels, sha256, who, when). See `report-templates/README.md`. |
| `/webhooks` | Per-project outbound webhook subscriptions + delivery records. |
| `/hosts/tags` | Project tag catalog with host counts (`host_tags`). |
| `/hosts/bulk/*` | Bulk host operations (`host_bulk`): `POST /hosts/bulk/tags`, `/bulk/assign` (assign hosts to a user — analyst+), `/bulk/unassign` (remove the **caller's own** assignment — any project member, so an assigned viewer/auditor can drop it), `/bulk/follow`. |
| `/hosts/views`, `/hosts/views/{id}/promote` | Saved Hosts-page filter/view state per user (`host_filter_views`). |
| `/hosts/query/schema`, `/hosts/query/validate`, `/hosts/query/history` | Boolean query-DSL catalogue, validate/match-count preview, and per-user query history (`host_queries`). |
| `/scopes/subnet-labels`, `/scopes/subnets/{id}/labels` | Subnet labelling (`subnet_labels`; mounted before `scopes` so the static prefix wins route resolution). |
| `/agent-sessions`, `/agent-sessions/by-model-tool`, `/agent-sessions/{sid}` | The agent-session timeline, plus by-(model, tool) aggregates (the Agent Sessions page). A row's `kind` is `project` (every session since v2.337.0) or a legacy `assist` row. **`id` is the session's only id** (v2.449.0): every route here takes it, and the row no longer names a second one (`assist_session_id` / `agent_session_id` are gone). A row carries `status` (the stored status — `active` / `ended`; a legacy row may read `expired`), `key_expires_at` and `renewable_until` (an active session whose key has run out is resumable, not ended — "can an agent use it now" is `key_expires_at` in the future), `host_test_count` and `evidence_count` (the host tests it proposed and the evidence records it wrote), `call_count`, `note_count`, `connection` (`none` / `mcp` / `curl`, from observed calls) and `first_call_at` (v2.449.0), `last_activity_at`, `operator_role`, `feedback_count`, and the caller's `can_end` (owner or project admin) / `can_resume` (owner, while the session is still inside its lifetime) on an active project session. Filters: `kind`, `status`, `user_id`, `agent_id`, `model`, `tool`, `limit`, `offset`; ordered and paged in SQL, `total` is the filtered count. `GET /agent-sessions/{sid}` returns that row for one session — a project session or a legacy assist one (404 for a pre-v2.337.0 row of any other kind); the session page reads it. |
| `/agent-sessions/{sid}/notes` | The notes the session's agent wrote, newest first: `{total, items[{id, host_id, host_ip, hostname, body, created_at}]}`; `limit` (default 50, ≤500) cuts `items` only. Project viewer. (v2.449.0 — they were inline in `GET /assist/sessions/{id}`.) |
| `/agent-sessions/{sid}/api-activity` | The agent API-call log of one session, by the session id. Filters: `method`, `status_min`, `status_max`, `host_id`, `target_ip`, `mine`, `limit`, `offset`. Returns `{total, items[]}`. See §6.7. |
| `/assist-sessions/{old_id}`, `/assist-sessions/{old_id}/api-activity`, `/assist/sessions/{old_id}`, `POST /assist/sessions/{old_id}/end` | **Deprecated.** Until v2.449.0 a session also had an `assist_sessions` row and these paths took that row's id. The id is resolved in two steps, both inside the path's project: first as an OLD id (`agent_sessions.legacy_assist_session_id` — the session that row belonged to), else as the session's own id (the start response still returns `assist_session_id`, equal to the session id, and a browser tab loaded before the upgrade sends it here). The old id wins when both could match. They answer with the current handler: the two reads return the session row (its `id` is the one to use from then on), the feed and End behave as their `/agent-sessions/{sid}/…` counterparts. 404 for an id that is neither, or that is another project's. `GET /assist/sessions` (the list keyed by the old ids) is gone: use `GET /agent-sessions`. |

---

## 5. Agent API (`/api/v1/agent/*`) — X-API-Key auth

All endpoints in this section require `X-API-Key: nm_agent_<plaintext>` in the request headers. Project scope is implicit from the key.

**One key, one session, every surface.** The Swagger tags — `agent-browse`, `agent-host-tests`, `agent-proposals` (evidence and proposals), `agent-scope` (scope reads and uploads; `agent-recon` until v2.433.1), `agent-assist`, `agent-feedback` — group the routes by kind of work; they are not scopes, and nothing is rejected for being "the wrong workflow" (that model ended in v2.337.0). There is no required order either (v2.433.0): the operator drives the agent, and the agent uploads, proposes tests and records what it ran as the work needs, within the operator's project role. No step waits on a human approval.

**Session lifecycle:** work, in whatever order it needs (there is no setup call): to read a scope, `GET /agent/scopes` then `/agent/scopes/{scope_id}/subnets|domains` or the target files; to add scanner output, `POST /agent/uploads` and poll `GET /agent/uploads/{job_id}`; to propose tests, `POST /agent/host-tests {tests: [...]}`; to work one, `PATCH /agent/host-tests/{test_id}` to `in_progress`, run it, `POST /agent/evidence` with `host_test_id` and a `request_key`, then `PATCH` it to `done` → `POST /agent/session/end` when the operator says they are finished. `GET /agent/identity` reports `can_write_project_data`, `key_expires_at` and `renew_path`. The read-back is the session prompt's (project, scope, working directory).

**The safety rules are the agent's, not the server's** (`agent_policy.SAFETY_RULES`): show the operator every command; the operator drives (propose next steps, don't take them unasked); stay inside the declared scope — a target outside it needs the operator's explicit go-ahead, and a name in scope does not scope its address; write output into the working directory — outside it, installing software, or changing settings/credentials needs explicit go-ahead; record every command and outcome verbatim and upload scanner output. The server prescribes no tool list, no target check and no approval step. `/.well-known/networkmapper.json` publishes `command_approval: "operator_driven"`. `backend/tests/test_well_known.py` pins its `safety_properties`: the server-enforced claims (`server_executes_commands: false`, `agent_authority`, `agent_key_binding`, time-limited renewable keys, persistent audit trail), the not-enforced ones (`command_approval`, `command_approval_enforced_by`), and that no claim the server cannot back is published.

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
| GET | `/agent/scopes` | Scope list with every subnet CIDR; `subnet_total` is the list's length (it read 0 until v2.456.0). |
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


### 5.5 Scope reads and uploads

There is nothing to open: an agent reads a scope by id and uploads what its tools produced. Routes in `agent_recon.py`.

| Method | Path | Notes |
|---|---|---|
| GET | `/agent/scopes` | The project's scopes (`agent_browse.py`; MCP `assist_list_scopes`). |
| GET | `/agent/scopes/{scope_id}/subnets` · `/agent/scopes/{scope_id}/domains` | The scope's CIDRs and declared domains (`{domain, include_subdomains}`), paged by `offset` / `limit` (default 500, max 2000). MCP `scope_list_subnets` / `scope_list_domains`. 404 for a scope outside the key's project. |
| GET | `/agent/scopes/{scope_id}/hosts.ndjson` · `/live-hosts.txt` · `/web-targets.txt` | Auditor floor (`AGENT_READ_ROLE_OVERRIDES`). File-shaped target lists for the next tool — every in-scope host as NDJSON (each open port with `service`, `tunnel`, and `method`: nmap's "table" guess vs "probed"), one IP per line, one http/https URL per line — `curl` them to disk; not MCP tools. Subnet scope only. A web target is a port whose service was identified as HTTP (https when TLS-wrapped); the common-web-port table applies only when nothing identified the port (no name, "unknown", or a `table` guess), so imaps or ssh-on-443 is never listed; IPv6 hosts are bracketed in the URL (v2.453.1). |
| GET | `/agent/scopes/{scope_id}/named-targets.ndjson` | Auditor floor. Name scope as NDJSON, one object per name the scope's domain rules cover: `name`, `name_id`, `scope_rule {domain, include_subdomains, match}`, `addresses [{ip_address, record_type, last_observed, host_id, in_subnet_scope}]` (current A/AAAA batch), `unresolved`, `reason`, `web [{interface_id, url, scheme, port, ip_address, at_current_address, source, status_code, title, observed_at}]` (interfaces whose `name_id` is the name). Declared domains with no observed name are listed unresolved. SAN-only and co-hosted names are excluded; a name never puts its address in scope. 404 for another project's scope. |
| POST | `/agent/uploads` | Multipart upload of scanner output, belonging to the agent session (`ingestion_jobs.agent_session_id`). Form fields: `file`, `tool_name`, `command_run`, `batch` (sweep label — batches are keyed per agent session + label), optional `skip_informational` (Nessus). 409 `duplicate_scan` for an identical file; 503 + `Retry-After` when the batch label is briefly contended. |
| GET | `/agent/uploads/{job_id}` | Poll an upload's parse status (`queue_age_s`, `parse_s`, `last_error`). Only this session's jobs — 404 otherwise. MCP `get_upload_job`. |

### 5.6 Feedback ingest

| Method | Path | Notes |
|---|---|---|
| POST | `/agent/feedback` | Record structured feedback, at the moment of friction. Body includes `source`, `prompt_version`, `overall_rating` (1–5), `api_critiques[]`, `tool_suggestions[]`, `friction_notes`, `agent_metrics{}`. `source` is required: `assist`, `reconnaissance` or `testing` (`plan_generation` and `in_session_execution` are still accepted from older clients; `exported_execution` is refused). The project and the session come from the key, never the body (the optional `assist_session_id` body field went in v2.449.0; a direct HTTP caller that still sends it is ignored, the MCP tool no longer lists it). |

### 5.6a Evidence and proposals (v2.436.0)

| Method | Path | Notes |
|---|---|---|
| POST | `/agent/evidence` | Record a command run against a host and its result: `host_id`, `tool`, `outcome` (`finding` · `no_finding` · `inconclusive` · `failed` · `info`), `summary`, optional `host_test_id` (the test it answers — the same host, and then `request_key` is required), `request_key` (re-sending it returns the record already stored; a different payload under the same key is a `409`), `command`, `raw_output` (≤5 MB as UTF-8: over that the call is a `413`, checked before anything is hashed or stored — never a `422` echoing the text; stored in the record — since 2.439.0, when it stopped being a file that outlived a deleted host; NUL characters are removed), `finding_id`, `finding_host_id`, `observed_ip` (an IP literal), `executed_at`, `agent_model`. Immutable — no update or delete route. A record with outcome `finding`, `no_finding` or `inconclusive` is what makes its host *tested*; when it answers a test aimed at a name (`target_fqdn`) and carries `observed_ip`, it also writes that name's `TESTED` observation. |
| GET | `/agent/evidence` · `/agent/evidence/{id}/raw` | This project's records (`host_id`, `host_test_id`, `finding_id`, `agent_session_id`, `limit`, `offset`) · one record's raw output. |
| POST | `/agent/proposals/finding-text` | `{finding_id, fields: {field: text}, rationale?, evidence_ids?, agent_model?}` — one proposal per field (`description`, `impact`, `recommendation`, `references`, `steps_to_reproduce`, `cvss_vector` — validated now). A section may place the finding's own images with `![caption](evidence:<id>)`: an id that is not an image of that finding (see `images` on `GET /agent/assist/findings/{id}`) is a `422` naming it; accepting a proposal that places an image not ticked "In report" is a `409` until a person ticks it (the proposal stays pending with the reason). Returns `{proposals: [...]}` — an acknowledgement, not an echo (v2.456.0): each row has the proposal's `id`, `field`, `status`, target and attribution plus `value_chars` / `base_value_chars`, and leaves out `payload`, `current_value` and `base_value` (the texts are in `GET /agent/proposals`). |
| POST | `/agent/proposals/finding` | `{title, severity, host_ids, status? (open|confirmed), report_text?, …}` — a new finding, created by the person who accepts it. The answer's `payload` carries `report_text_chars` (`{field: length}`) in place of the `report_text` sent (v2.456.0). Each `report_text` field is at most 32,768 characters (`422`). With no finding there is no author or owner to tell, so the project's admins are notified — one `proposal` notification per admin per agent session (`source_type: agent_session_new`), not the operator whose agent it is. |
| POST | `/agent/proposals/observation` | `{vulnerability_id, action (promote|dismiss), scope? (host|issue), severity?, summary?, …}` — accepted through the promote route's own logic. When the observation has no finding yet, the project's admins are notified as for a proposed finding; when it already evidences a finding, that finding's author and owner are (the same per-session `proposal` notification as a report-text proposal). |
| POST | `/agent/proposals/endpoint-status` | `{finding_id, finding_host_id, host_status (open|remediated|retest|false_positive), …}`. `finding_host_id` is on each row of `GET /agent/assist/findings/{id}` → `hosts`. |
| GET | `/agent/proposals` | This project's proposals and their decisions (`status`, `kind`, `finding_id`, `mine`). |

Writes need the operator's project write role. `evidence_ids` must name records in this project (404) and holds at most 100 ids (422).

### 5.7 Assist workflow (read-only Q&A — v2.64.0)

For "ask questions about this project" agents. These reads are the DEFAULT surface of every session. Project membership is the floor, and a read needs the role its page needs (v2.428.0): the NDJSON exports and client reports require `auditor`, ingestion issues and uninterpreted lines `analyst`; attachments and screenshots any member. The table below is a sample. Since v2.428.0 the agent reads mirror the pages — `/assist/workbench`, `/assist/workbench/investigate`, `/assist/workbench/terrain`, `/assist/evidence/gaps`, `/assist/scans/compare`, `/assist/scans/{id}/hosts`, `/assist/scanner-observations[/hosts]`, `/assist/client-reports[/{id}[/files/{fmt}]]`, each wrapping its page's service; the agent guide's assist table describes them. `/assist/client-reports/{id}` carries what the Reports page's data holds, from the same `ClientReportService.content`: per finding `confirmations` + `confirmations_omitted`, and in an addendum `change` (`new` · `new_hosts` · `severity_changed`) with `previous_severity` / `previous_severity_label`; `delta.findings_with_changed_severity`; `summary.evidence_records` / `evidence_records_not_printed` / `agent_evidence_records` (`confirmations` and `evidence_records` are only what the report prints); per finding `images[]` with `placed_in`, `printed`, `printed_in` (no `file`, no `placed` map — the render's own), and the summary's `images_printed` / `images_trailing` / `images_not_printed` / `images_not_printed_reasons` / `template_images`. `/assist/findings/{id}` lists at most 100 `scanner_evidence` rows, with `scanner_evidence_total` and `scanner_evidence_truncated`. The router also serves `/assist/hosts/count`, `/assist/hosts/{id}/findings` (raw scanner observations), `/assist/hosts/{id}/web-interfaces`, `/assist/hosts/{id}/notes`, `/assist/findings[/{id}]`, `/assist/posture`, `/assist/patterns`, `/assist/segments`, `/assist/coverage`, `/assist/vocabulary`, `/assist/notes`, `/assist/names`, `/assist/ingestion-issues`, `/assist/hosts/{id}/access` (NetExec / SMBMap results beside the raw line — which may carry credentials, shown as found), `/assist/uninterpreted-lines?job_id=` (redacted lines a parser did not read), `/assist/attachments/{id}` and `/assist/web-interfaces/{id}/screenshot`, and the `hosts.ndjson` / `report-context.ndjson` downloads.

| Method | Path | Notes |
|---|---|---|
| GET | `/agent/assist/context` | Project + session context; `default_host_view` (`name`, `filters`) is the view the Hosts page opens on, or null (v2.453.4). `/agent/assist/posture` carries `scanner_observations` (`total` with informational excluded, `informational`, `by_severity`, `hosts_by_severity` — raw scanner rows); `/agent/assist/ingestion-issues` counts `failed` / `expired` / `discarded` / `needs_attention` as the Ingestion Results page does. |
| GET | `/agent/assist/hosts` | Paginated, filterable host list (read-only). Accepts the discrete filters AND a `q=` boolean query DSL — see below. **Returns `{items, total, has_more, limit, offset}`** (2.440.0; it was a bare array, and an agent counted a 500-row page as the answer). `ports=` is port numbers (any of them open; a range or name is a 422 naming it, never silently ignored). `services=` is the service the scanner identified on an open port, on any port — the Hosts page's filter and `q=service:` (2.440.0; it used to mean "the name's standard ports open", so `services=vnc` counted masscan-only 5900s and missed VNC on 5800). |
| GET | `/agent/assist/hosts/count` | **v2.291.0** — how many hosts match, same filters + `q` DSL. Shares the query builder with the list (whose `total` is the same COUNT), so "which hosts" and "how many hosts" cannot drift. |
| GET | `/agent/assist/hosts/{host_id}` · `/agent/assist/hosts/by-ip/{ip}` | Host detail with ports, services, vulns — by id or by address. |
| GET | `/agent/assist/scopes` | Scope list. |
| GET | `/agent/assist/scans` | Scan list as a page — `{items, total, has_more, limit, offset}` (v2.453.3; it was a bare array) — `tool=` narrows to one tool. Rows carry `time_source`: `start_time`/`end_time` are UTC instants (with an offset) unless it is `tool_clock` — the scanner's zone-less wall clock, returned without one (v2.434.1). `scan_info` is nmap's scanned port list per scan type / protocol (`type`, `protocol`, `numservices`, `services`); empty for other tools. |
| GET | `/agent/assist/scans/{scan_id}/hosts` | The hosts one scan observed, as it observed them — the same rows as `GET /scans/{scan_id}/host-snapshots` (`scan_snapshot_service.scan_host_snapshots`), including `credentialed` (true / false when the scanner said whether it authenticated, null when it did not say). `state`, `search`, `skip`, `limit` (≤1000); the `Paginated` envelope. MCP `assist_list_scan_hosts`. |
| GET | `/agent/assist/session` | Current assist-session metadata. |

**`q=` query DSL (the marquee assist feature).** `GET /agent/assist/hosts` accepts a `q=` parameter carrying the **same boolean query DSL as the Hosts page**: field predicates (`state:`, `ip:`, `hostname:` (alias `host:`), `port:`, `os:` (OS name or OS family), `service:`, `version:` (service product/version) — these three match **open ports only** unless the value names a state after `@`: `port:22@closed`, `service:ssh@filtered`, `port:22@any` (v2.403.0; a closed/filtered port's service name is nmap's guess from the port number); `portstate:` alone is a separate "has a port in this state" condition — `path:` (a content-discovery path), `subnet:`, `tag:`, `label:`, `site:` (`site:none` = inside a scoped subnet that carries no site), `conclusion:` (what a finished review concluded — e.g. `conclusion:needs_evidence`), `cve:`, `vuln:`, `issue:` (exactly one scanner-observation issue by its `issue_key` — `check:…`, `cve:…`, `title:…`), `kind:` (misconfiguration \| vulnerability \| informational), `check:` (a misconfiguration check id, e.g. `check:smb_signing_not_required`), `scope:` (subnet \| name \| none — the three scope-coverage states), `vulnscan:` (credentialed \| uncredentialed \| unstated — whether the vulnerability scan of an ASSESSED host authenticated; a host that is not assessed matches none), `exploitport:`, `header:`, `webtitle:`, `tech:`, `org:`, `certorg:`, `asn:`, `country:`, `note:`, `scan:`, `firstseen:` / `changedsince:` / `vulnsince:` (time windows, (start, end]; quote the ISO value — `firstseen:"2026-09-19T20:00:00Z"`, `changedsince:"<start>..<end>"`, `vulnsince:"critical@<start>..<end>"` with severity and time matched on the same observation), `has:`, `follow:` (in_review / reviewed / none — team-level: `in_review` and its synonym `in_review_any` match a host ANY teammate has In Review, `reviewed` one any teammate marked Reviewed, `none` one nobody has in either state — and two per-caller values (for an agent, the session's operator): `mine` — the caller has it In Review — and `revisit` (v2.451.0) — a finished review of the caller's that is not done: it concluded `needs_evidence`, or the host gained an open port or a critical / high scanner observation after THAT review; Operations' "Changed since review" list; the retired `watching` is still accepted and is judged on the caller's own row), `assigned:` (alias `assignee:`, taking `me` / `any` / `none` / a username / an id)), combined with `AND` / `OR` / `NOT` and parentheses (comma = OR within a field; a repeated field = AND). `has:` takes one of `eol` · `smb_unsigned` · `weak_auth` · `cert_issue` · `weak_tls` · `cleartext` · `critical`/`high`/`medium`/`low` · `local_admin` · `writable_share` · `exploit` · `critical_exploit` (a critical that is itself exploitable — same row) · `web` · `open_ports` · `tested` · `planned` · `untouched` (no review or assignment, note, host test that was not dismissed, evidence record or finding endpoint) · `notes` · `stale_review` · `changed_since_review` (reviewed, then an open port first seen or a critical / high scanner observation recorded after the review, ANY teammate's — with `OR conclusion:needs_evidence` it is the team-wide list; Operations' "Changed since review" is the caller's own, `follow:revisit`). It is ANDed with the discrete filter params. `follow:` is judged for, and `assigned:me` resolves to, the **operator who started the session** (so `assigned:me` means "hosts assigned to that operator"); `assigned:`/`assignee:` also accept a **username** (case-insensitive; the value a user actually knows, since ids aren't surfaced) or a numeric user id. The DSL only filters; it never mutates follow/assignment state. A malformed query returns **400** (clean error, not a 500); any `q=` returns 400 if the session has no bound operator. Backed by `host_query_dsl.parse_query` / `evaluate`.

The JWT side (operator): `POST /projects/{id}/assist/start` opens a session and returns a fresh key + prompt + per-client MCP config (shown once), with `agent_session_id` — the session's id, and its only one (v2.449.0: a start writes one `agent_sessions` row; `assist_session_id` is still in the response as a deprecated copy of the same value). Everything else about a session is under `/projects/{id}/agent-sessions` (§4.13), by that id: `GET /agent-sessions` lists them (`?status=`, `?user_id=`, `?kind=`, `limit`/`offset`), `GET /agent-sessions/{id}` returns one with its attribution (`generated_by_model`, `generated_by_tool`, `prompt_version` — §6.8) and call / note / feedback counts, `GET /agent-sessions/{id}/notes` the notes it wrote, `GET /agent-sessions/{id}/api-activity` its audit feed (§6.7), `POST /agent-sessions/{id}/end` revokes the key (session row kept for audit) and `POST /agent-sessions/{id}/resume` rotates it on the same session.

Session `status` is the **stored** column (`active` / `ended`), never derived from the key: an active session whose key has run out is still `active` — it is resumable — and whether an agent can use it now is `key_expires_at` in the future. The hourly sweep (`lapse_expired_agent_sessions`) ends a session only once its keys have expired AND its renewal deadline (`renewable_until`) has passed, so nothing accumulates as "active" waiting to be tidied up by hand.

### 5.8 Cross-workflow endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/agent/identity` | **v2.278.0** — what this key is: its session, the operator it acts for — which since v2.309.0 *is* its authority, so v2.311.0 also returns that operator's `project_role` and a precomputed `can_write_project_data` rather than making the agent infer it or discover it from a 403 — when it expires (`key_expires_at`), and where to renew (`renew_path`). |
| POST | `/agent/tool-suggestions` | **v2.278.0** — propose a tool the catalogue does not have, with rationale. Lands as `suggested` for an admin to curate. Catalogue intake only — since v2.433.0 it neither grants nor withholds anything. Deliberately ungated. |

### 5.9 MCP transport (`POST /api/v1/mcp`)

JSON-RPC 2.0 over a single POST (Streamable HTTP, tools-only subset; protocol `2025-06-18` / `2025-03-26`). `initialize`, `tools/list`, `tools/call`, `ping`.

* **Auth.** `initialize` / `tools/list` / `ping` need no key. `tools/call` reads `X-API-Key` **or** `Authorization: Bearer` and forwards it to the underlying endpoint in-process — so workflow scope, the operator's project role, and the audit log are unchanged. The MCP layer makes no authorization decision.
* **401 vs. isError.** No usable credential answers a real **HTTP 401** with a plain RFC 6750 challenge (`WWW-Authenticate: Bearer realm="BlueStick assist"`, plus `error="invalid_token"` when a key was sent) (a fact about the connection, which a client can act on). A valid key that may not perform *this* call returns the endpoint's 403 as an `isError` tool result (a fact about one call, which the model should read and work around).
* **Unfiltered listing.** `tools/list` returns the whole catalogue (`GET /references/mcp-tools` is the live list and count — no number is kept in the docs) to every session; each tool's `workflows` field (`assist`, `testing`, `scope`) is a grouping for the reference page, not a filter. Listing was never authorisation — the endpoint behind a tool decides on every call.
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
  "agent_session_id": 58,
  "assist_session_id": 58,
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

Status **201**. `agent_session_id` is the session's id; `assist_session_id` repeats it (deprecated — until v2.449.0 it was the id of a second row). `api_key` is the plaintext, shown exactly once — the caller must display/copy it before dismissing the response. The hash lives in `api_keys`; subsequent recovery is not possible.

### 6.4, 6.5 — removed


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

Null metrics are acceptable — the guide explicitly notes that agents running in restricted sandboxes may not see their own token/cost/wall-clock numbers. `project_id` and the session come from the API key; `source` is required in the body (`assist` \| `reconnaissance` \| `testing`). The body never names the session.

### 6.7 `GET /api/v1/projects/{project_id}/agent-sessions/{session_id}/api-activity`

The agent API-call log of one session, by the session id (the deprecated `/assist-sessions/{old_id}/api-activity` path in §4.13 still answers for sessions that had a second id before v2.449.0).

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

BlueStick does not record the operator's machine or check it against the tool catalogue. What an `AgentSession` records about the agent comes from three sources (`agent_session_service`):

| Field | Source |
|---|---|
| `generated_by_tool` (the client / harness) | The MCP `initialize` handshake's `clientInfo` — `"<name> <version>"` — when the handshake carries a live key (`record_mcp_client`; it never fails the handshake). Otherwise the first authenticated call's `User-Agent` (a curl agent: `curl/8.5.0`), written once and replaced by a later handshake name, never the reverse. The MCP loopback forwards the client's own `User-Agent`; httpx's default is ignored. |
| `prompt_version` | Set by the server at session start and at resume (`PROMPT_VERSION`). |
| `generated_by_model` | Self-reported: optional `agent_model` (≤100 chars) on `POST /agent/host-tests`, `POST /agent/evidence`, the `POST /agent/proposals/*` routes and `POST /agent/session/end` — MCP `host_tests_propose`, `record_evidence`, the `propose_*` tools, `end_session`. The session keeps the LAST value reported. |

A host test snapshots the session's model, client and prompt version when it is proposed (`agent_model`, `agent_client`, `prompt_version`), and an evidence record its model and client when it is recorded, so a session that switches models does not relabel earlier work.

---

## 7. Error shapes

**An `/api/v1/agent/…` path that is not an endpoint (v2.444.0)** answers `404` with more than the framework's `{"detail": "Not Found"}`, because the caller is usually an agent that guessed a URL:

```json
{
  "detail": "No agent endpoint at GET /api/v1/agent/targets.",
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
- **Max-length caps** on high-risk text fields: a host note's body 16 KB; the host-test fields (`host_test_schemas.py`: tool 100, description / command / rationale / expected result / tester summary 10,000 each, dismissal reason 2,000, label 255, at most 20 references, at most 200 tests per batch); a proposed finding's report-text fields 32,768 each.
- **File uploads** enforce per-extension magic-byte checks. `.xml`/`.nessus` must start with `<`, `.json` with `{` or `[`, `.gnmap` with `#` or `Host:`, text files may not contain NUL bytes. Filenames are slugified before filesystem use. Chunk-level size cap prevents unbounded streams.
- **XML parsing.** Two-pronged defense (v2.41.0): `nessus_parser.py` uses `defusedxml.ElementTree`; the nmap, masscan and openvas parsers use lxml through `xml_stream_helpers.iterparse_safe` (`resolve_entities=False, no_network=True, huge_tree=False`). Both approaches disable external entities, DTD fetching, and entity expansion at parse time.
- **EyeWitness ZIPs** (v2.41.0) have per-file (50 MB), running-total (500 MB), and entry-count (5000) decompression-bomb caps. The streaming extractor counts bytes mid-stream and aborts + unlinks the partial file if either cap is exceeded, so a spoofed central-directory size field can't defeat the check.
- **BloodHound JSON ≥50 MB** streams via `ijson` instead of `json.load` (v2.41.0); the structure (`[…]`, `{"data": […]}`, `{"computers": […]}`) is auto-detected by peeking the first 64 KB.

---

## 9. Operational notes

- **Schema management.** Tables are owned by **Alembic**. Every backend boot runs `alembic upgrade head` before serving traffic. Migrations live in `backend/alembic/versions/`; baseline at `b46cd59c17f5_baseline_schema`. The previous startup-DDL path has been retired — the model is the schema, and Alembic enforces it.
- **Statement timeout.** A request's SQL statement that runs longer than `API_STATEMENT_TIMEOUT_MS` (default 30000; `0` = off) is cancelled and answered `503`. Streamed exports are exempt, because their queries run after the response has started and cover a whole project or scope: `GET /reports/hosts/csv` and `/reports/hosts/html`, `GET /names/export`, `GET /hosts/tool-ready` in the `names` format, and the agents' `/agent/assist/hosts.ndjson`, `/agent/assist/report-context.ndjson` and the four `/agent/scopes/{scope_id}/…` target files. So are the routes that issue one large statement by design: deleting a project, a scan, a scope, a subnet or a user (the cascade), `POST /scopes/correlate-all` and `POST /scopes/upload-subnets` (correlation over every host). Workers and scripts are never limited.
- **Upload flow is async.** `POST /upload/` returns a queued `IngestionJob` — poll `GET /upload/jobs/{id}` for status. Don't expect a parsed scan in the upload response.
- **API keys are shown once.** The `/assist/start` (and `/agent-sessions/{sid}/resume`) response includes the plaintext key exactly once. Store it or discard it immediately; recovery is not possible. The hash lives in `api_keys`.
- **Orphan jobs get reaped.** Jobs stuck in `processing` with a heartbeat older than `INGESTION_JOB_TIMEOUT` × `INGESTION_ORPHAN_CUTOFF_MULTIPLIER` (default 1.5) are re-queued automatically by the worker's reaper, up to `INGESTION_MAX_RETRIES` while the file still exists; after that they are failed ("worker likely crashed") and admins are notified.
- **Workflow-scoped agent guide** (`documentation/AGENT_GUIDE.md`). Agents may fetch `GET /api/v1/agents-guide?workflow=testing` (or `reconnaissance`, or `assist`) to get the workflow-sliced subset; omitting `workflow` returns the whole guide. `plan`, `plan_generation`, `exec` and `execution` are accepted as aliases of `testing`. The server parses HTML-comment section markers so one source file emits multiple slices.
- **Health probes.** nginx serves `/live` (liveness, static) and proxies `/health` and `/ready` to the backend's `/health` (5 s timeouts on purpose); the frontend container's Docker HEALTHCHECK requests `https://localhost/`.
- **Version visibility.** `GET /` returns `{message, version, frontend_version, instance_id, cors_origins}`. The UI shows both versions in the user menu under **About BlueStick**. Backend and frontend stay in lockstep per-release; always update both.

---

This document was last reconciled with the routers at v2.428.0 (frontend 5.309.1, 2026-09-26); the agent, host-test and session sections were brought up to v2.442.0 (2026-10-01), and the workbench, findings, client-report, session and statement-timeout parts to v2.450.0 (2026-10-02). **It is not an exhaustive route list and never stays one for long** — sections 4 and 5 give the shape and the contracts that matter; for every route, parameter and schema use the live OpenAPI at `https://<host>/docs` (Swagger UI), `/redoc`, or `/openapi.json`, all proxied by nginx (the backend's own :8000 is not published). Use `/docs` (Swagger UI) for interactive exploration and field-level schemas — this guide is architectural context and high-signal shape references, not a replacement for OpenAPI.
