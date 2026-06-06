# BlueStick Architecture

> **Last verified against:** backend 2.24.0 / frontend 2.19.0 (2026-05-15)

BlueStick is a multi-user, multi-project pentest-operations platform that ingests scanner output, deduplicates hosts, correlates them to project scopes, enriches findings with vulnerability data, and drives agent-assisted test-plan generation and execution. This document is the canonical architecture reference — update it when domains, endpoints, or workflows change materially.

---

## 1. System topology

Four containers, one Docker network:

```
┌──────────────────────┐   HTTPS 443      ┌────────────────────────────────┐
│  Frontend (Nginx)    │ ◀───────────────▶│  React SPA (frontend/src, Vite) │
│  TLS termination +   │                   └──────────────┬─────────────────┘
│  static asset serve  │                                  │  REST + JWT
└──────────┬───────────┘                                  │  (or X-API-Key for /agent/*)
           │                                              ▼
           │                                   ┌──────────────────────────┐
           │                                   │  Backend (Uvicorn)       │
           │ Same-origin /api/v1 proxy ───────▶│  FastAPI, app/main.py    │
           │                                   │  - api/v1 routers        │
           │                                   │  - services/             │
           │                                   │  - parsers/              │
           │                                   └──────────┬───────────────┘
           │                                              │
           │                              SQL + pg_notify │
           │                                              ▼
           │                                   ┌──────────────────────────┐
           │                                   │  PostgreSQL 16           │
           │                                   │  (networkmapper-db-1)    │
           │                                   └──────────┬───────────────┘
           │                                              ▲
           │                                              │ LISTEN ingestion_jobs
           │                                   ┌──────────┴───────────────┐
           │                                   │  Worker (python -m       │
           │                                   │  app.worker)             │
           │                                   │  - ingestion + orphan    │
           │                                   │    reaper                │
           │                                   └──────────────────────────┘
           ▼
  Browser (React + MUI, Chart.js, react-router)
```

- **Frontend container** (`networkmapper-frontend-1`) runs Nginx to terminate TLS, serve the Vite build, and reverse-proxy `/api/*` to the backend.
- **Backend container** (`networkmapper-backend-1`) runs `uvicorn app.main:app` with multiple workers. Exposes `/api/v1` and `/health`.
- **Worker container** (`networkmapper-worker-1`) runs `python -m app.worker` — a single long-lived process that LISTENs on the Postgres channel `ingestion_jobs`, polls for queued work with `SELECT … FOR UPDATE SKIP LOCKED`, processes each job through the parser pipeline, and reaps orphaned jobs whose heartbeat has gone stale. The backend writes upload files to a shared volume; the worker reads from the same path.
- **Database container** (`networkmapper-db-1`) runs PostgreSQL 16 with a persistent volume for data. Schema is owned by **Alembic** — `alembic upgrade head` runs on every backend boot before the app serves traffic. The previous startup-DDL compatibility path (`app/db/init.py`, `_ensure_schema` calls in services) has been retired; the model is the schema, and Alembic enforces it. Migrations live in `backend/alembic/versions/`, baseline at `b46cd59c17f5_baseline_schema`.

A single `docker-compose.yml` wires all four. `scripts/deploy.sh` is the unified entry point (first-time SSL setup, rebuild, nuclear cleanup, status).

---

## 2. Identity, multi-tenancy, and access control

BlueStick has **two parallel authentication systems** because it serves two very different consumer types.

### 2.1 JWT (human users)

- Users live in `users` (`app/db/models_auth.py`) with roles `admin`, `analyst`, `auditor`, `viewer`.
- `POST /api/v1/auth/login` returns a JWT signed with `settings.SECRET_KEY`. Sessions are tracked in `user_sessions` so tokens can be revoked server-side.
- A default admin account (`admin` / `admin`) is seeded on first boot with `must_change_password=True`; the forced-change flow gates every JWT-bearing request behind `require_password_changed` until the user rotates the password.
- RBAC checks are enforced in endpoint dependencies via `require_role(...)` and the project-scoped `require_project_role(...)`.

### 2.2 Agent API keys (non-human agents)

- Terminal-side agents (Claude Code, Codex, manual curl) authenticate with an `X-API-Key: nm_agent_...` header. Keys live in `agent_api_keys` and are hashed at rest.
- Keys are scoped tightly: **per-project, per-test-plan, per-workflow**. A key minted for plan generation cannot execute. A key minted for test plan #42 cannot touch plan #43. Keys are short-lived (24h default) and are minted either by the user clicking "Start Agentic Recon" on a Scopes page or by calling `/execute` on an approved test plan.
- The `/api/v1/agent/*` router is its own FastAPI `APIRouter` — completely separate from the JWT-user surface — and is **exempt** from `require_password_changed` because the agent has no credentials to rotate.
- The dependency chain for every agent endpoint is: `Depends(require_plan_scope)` → verifies the key exists, is not expired, binds to the requested `plan_id` if the endpoint is plan-scoped. `deny_scoped_keys` is the inverse: used on endpoints that must not accept a plan-scoped key (e.g. listing all plans).

### 2.3 Project scoping

Every data-bearing endpoint (upload, hosts, scans, scopes, test plans, etc.) is nested under `/api/v1/projects/{project_id}/...`. The `get_current_project` dependency loads the project, verifies the JWT user is a member, and enforces the required role. Cross-project reads/writes are blocked at the dependency layer; a second project's data is invisible unless the user is explicitly a member.

Portfolio-level endpoints (`/api/v1/portfolio/dashboard`) are global — they return cross-project summaries for users who can see multiple projects.

---

## 3. Backend package map

```
backend/app/
├── main.py                   # app factory, startup hooks, _seed_default_admin, _ensure_default_project
├── worker.py                 # ingestion worker entry point (python -m app.worker)
├── core/
│   ├── config.py             # settings (SECRET_KEY, CREDENTIAL_ENCRYPTION_KEY, DB URL, timeouts)
│   └── security.py           # password hashing, JWT mint/verify, audit logging
├── api/v1/
│   ├── api.py                # router registration — two tiers: top-level + /projects/{id}
│   └── endpoints/
│       ├── auth.py           # login, logout, change-password, sessions, profile
│       ├── users.py          # admin CRUD + /users/directory (picker — open to any user)
│       ├── audit.py          # admin audit log browsing
│       ├── projects.py       # project CRUD + membership (add/remove/role)
│       ├── notifications.py  # read/unread, mark-seen
│       ├── portfolio.py      # cross-project dashboard
│       ├── llm_providers.py  # per-user LLM provider credentials
│       ├── integrations.py   # per-user scanner tool credentials
│       ├── feedback.py       # agent feedback ingest (API-key) + admin triage (JWT)
│       ├── agent_browse.py        # /agent/* — shared reads (projects, hosts, scans, scopes)
│       ├── agent_test_plans.py    # /agent/* — plan-generation workflow
│       ├── agent_execution.py     # /agent/* — execution workflow + environment probe
│       ├── agent_recon.py         # /agent/* — agentic reconnaissance
│       ├── agent_activity.py      # JWT — human-facing read of the agent API call log
│       ├── agent_common.py        # shared helpers for the four agent routers
│       ├── agent_schemas.py       # Pydantic models for the agent surface
│       └── (project-scoped) upload, scans, hosts, host_follow, host_notes,
│                             dashboard, scopes, export, dns, parse_errors, reports,
│                             risk, agents, test_plans
├── db/
│   ├── session.py            # SessionLocal + engine
│   ├── init.py               # startup DDL: create_all + ALTER TABLE compatibility
│   ├── models.py             # core (Host, Port, Scan, IngestionJob)
│   ├── models_project.py     # Project, ProjectMembership
│   ├── models_auth.py        # User, UserRole, UserSession, AuditLog
│   ├── models_agent.py       # Agent, AgentApiKey, TestPlan, TestPlanEntry,
│   │                         # ExecutionSession, TestExecutionResult,
│   │                         # HostSanityCheck, AgentFeedback, ImportedResultFile
│   ├── models_llm.py         # LLMProvider (Fernet-encrypted api_key)
│   ├── models_integrations.py# IntegrationCredential (Fernet-encrypted secrets)
│   ├── models_risk.py        # risk scoring + ports-of-interest tables
│   ├── models_vulnerability.py # Vulnerability, VulnerabilitySource, CVE
│   ├── models_confidence.py  # per-attribute confidence + conflict tracking
│   └── models_unified.py     # unified derived views (dashboard)
├── services/
│   ├── ingestion_service.py  # upload write, magic-byte validation, worker loop
│   │                         # (parser dispatch, retry_count/last_error, orphan reaper)
│   ├── host_deduplication_service.py
│   ├── subnet_correlation.py # host ↔ subnet mapping (ip_trie for speed)
│   ├── ip_trie.py
│   ├── vulnerability_service.py
│   ├── vulnerability_db_service.py
│   ├── cve_correlation_service.py
│   ├── confidence_service.py
│   ├── risk_assessment_service.py
│   ├── risk_insight_service.py
│   ├── ports_of_interest.py
│   ├── host_follow_service.py
│   ├── notification_service.py
│   ├── parse_error_service.py
│   ├── export_service.py + report_templates.py
│   ├── dns_service.py + dns_validation.py
│   ├── subnet_calculator.py + subnet_parser.py
│   ├── command_explanation_service.py
│   ├── agent_prompt_service.py     # builds plan-gen/execution/recon prompts
│   ├── prompt_sanitizer.py         # strips secrets before LLM calls
│   ├── url_validator.py            # SSRF validation + IP-pinning httpx client
│   ├── test_plan_service.py        # TestPlan CRUD, entry management, progress rollup
│   ├── bundle_service.py           # export: zip plan for offline agent
│   ├── bundle_import_service.py    # import: idempotent results ingest
│   ├── llm_provider_service.py     # Fernet crypto, multi-provider chat completion
│   ├── integration_service.py      # Fernet crypto, scanner credential CRUD
│   ├── nessus_integration_service.py  # direct-from-Nessus pulls (if configured)
│   ├── sbom_service.py             # /reference/sbom — reads requirements.txt
│   │                               # + frontend package-lock.json, memoised by
│   │                               # manifest mtimes + app_version (v2.20.0)
│   └── agent_api_log_service.py    # middleware + helpers for the agent API call
│                                   # audit log (v2.24.0)
├── parsers/
│   ├── nmap_parser.py / gnmap_parser.py
│   ├── masscan_parser.py / naabu_parser.py / rustscan_parser.py
│   ├── nessus_parser.py + openvas_parser.py   # defusedxml-hardened for XXE
│   ├── nmap_parser.py / masscan_parser.py     # lxml with resolve_entities=False, no_network=True, huge_tree=False (v2.41.0)
│   ├── eyewitness_parser.py / dns_parser.py
│   ├── nikto_parser.py / amass_parser.py / bloodhound_parser.py
│   ├── dirbuster_parser.py / smbmap_parser.py / netexec_parser.py
│   └── parser_utils.py (shared ensure_scan/extract_first_ip helpers)
└── schemas/
    ├── schemas.py           # primary Pydantic request/response models
    └── risk_schemas.py      # risk-assessment specific shapes
```

Routers stay thin — business logic lives in `services/`. Parsers only normalize external formats to the canonical host/port/vulnerability representation; they never touch HTTP concerns.

---

## 4. Scan ingestion pipeline

Uploads flow through an asynchronous worker so the request path never blocks on parsing. The flow is deliberately unforgiving of crashes — every state transition is durable in Postgres.

1. **Upload arrives.** `POST /api/v1/projects/{id}/upload/` streams the file to `uploads/ingestion_queue/{job_uuid}/{safe_filename}`. `ingestion_service.create_job`:
   - slugifies the filename to prevent log injection and FS traversal,
   - enforces `MAX_FILE_SIZE` chunk-by-chunk during the stream,
   - runs `_validate_content_matches_extension()` — a magic-byte check that rejects the obvious nonsense (`.xml` that doesn't start with `<`, `.json` that doesn't start with `{` or `[`, text files with NUL bytes),
   - writes an `ingestion_jobs` row with status `queued`,
   - fires `pg_notify('ingestion_jobs', job_id)` to wake the worker.
2. **Worker claims the job.** `poll_and_run_one()` runs `SELECT id FROM ingestion_jobs WHERE status='queued' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED`, transitions it to `processing` inside the same transaction (so competing workers skip it), then runs outside the row lock.
3. **Parser dispatch.** `_process_job` sniffs the file sample, builds a list of parser attempts (most-specific first), and executes them in sequence. The first parser that returns a non-empty result wins. Each parser call is wrapped in a `ParseFailure` boundary so a crash records a `parse_errors` row with a user-facing message and binds the ID back onto the job.
4. **Heartbeat.** Parsers call `service.update_heartbeat(job_id, progress=...)` periodically. The heartbeat check also serves as a cooperative cancellation and timeout point — if the job was cancelled by a user or has exceeded `INGESTION_JOB_TIMEOUT`, the parser raises `ParseFailure` with a terminal message.
5. **Terminal state.** Success → `status='completed'`, `scan_id` set, `tool_name` set. Parse failure → `status='failed'`, `retry_count++`, `last_error` populated with a trimmed traceback or the user message. Unexpected exception → same, but with a `traceback` last_error.
6. **Orphan reaping.** Every ~12 idle ticks (~1 min), the worker runs `reap_orphaned_jobs()` — transitions any `processing` job whose heartbeat is older than 3× `INGESTION_JOB_TIMEOUT` to `failed` with a clear "worker crashed" message and bumps `retry_count`. Closes the gap when a worker dies mid-parse.

Parsed hosts flow through `host_deduplication_service` (dedupe by IP within project), `subnet_correlation` (bind hosts to scopes via an IP trie), and `vulnerability_service` / `cve_correlation_service` (enrichment). `risk_insight_service` and `ports_of_interest` compute the derived tables that power the dashboard.

---

## 5. Test plan + agent workflow

The test plan system is the operational backbone for agent-assisted pentesting. It has three modes:

### 5.1 Plan generation (Workflow A)

Agents call the `/api/v1/agent/*` surface with a plan-generation-scoped key:

1. **`GET /agent/test-plans/{plan_id}/context`** — paginated candidate hosts. Returns `CandidateHost` rows with vuln counts, services, ports, a `meets_policy` flag computed by `_evaluate_host_policy()`, and an `inferred_service_hints` list that fills in canonical service names (smb, rdp, mssql, …) when nmap missed service detection. The response also carries the selection policy as a string so the agent can echo it in rationales.
2. **`POST /agent/test-plans/{plan_id}/entries`** — batch-create entries. Each entry has a host_id, priority, test_phase, a list of `proposed_tests`, and a rationale. Response is wrapped: `{"entries": [...]}`.
3. **`GET /agent/test-plans/{plan_id}/validate`** — dry-run summary. Returns `PreSubmitReport` with `warnings`, `by_priority`, `by_phase`, and a `coverage` block split into `policy_matching_remaining` (real missed scope, actionable) and `non_policy_with_open_ports` (correctly skipped, informational). Never blocks on coverage — only on empty plan, missing description, or short rationales.
4. **`POST /agent/test-plans/{plan_id}/submit`** — moves plan to `pending_review`.
5. **`POST /agent/feedback`** — agents post friction notes, API critiques, and tool suggestions at the end of every run. Rows land in `agent_feedback` with `status='new'`; a human triages via the admin-only `/api/v1/feedback/...` surface.

The selection policy, rubric, and endpoint shapes are documented exhaustively in **AGENTS.md** at the project root, which agents fetch via `GET /api/v1/agents-guide?workflow=plan_generation`. The guide uses HTML-comment section markers (`<!-- agents:section tags="..." -->`) so a single source file can emit workflow-scoped slices to save tokens.

### 5.2 Execution (Workflow B)

Once a human has **approved** a plan in the UI, the user clicks "Execute with AI":

1. **`POST /projects/{id}/test-plans/{plan_id}/execute`** (JWT user) mints a new **plan-scoped execution API key** and returns instructions + the key to the user to paste into their terminal agent.
2. Agent calls **`GET /agent/test-plans/{plan_id}/execution-context`** — returns hosts, entries, and `proposed_tests[]` with `{ip}` placeholders already resolved.
3. **Per-entry: sanity check → approval → results.** For each entry, the agent MUST first run a host sanity check (banner grab, DNS lookup, ICMP) and POST it to `/sanity-check`. The UI gates test execution on a passing sanity check. Then for each test the agent requests user approval in the terminal, runs the command locally, and POSTs results to `/test-results`. Status transitions on each entry track progress.
4. **`POST /agent/test-plans/{plan_id}/entries/{entry_id}/complete`** — closes the entry.
5. **`POST /agent/feedback`** — same endpoint, different workflow tag.

The agent is a **coordinator, not an executor**. The user's terminal runs every command. BlueStick provides the plan, the sanity check gate, the audit trail, and the result schema.

**Sanity-check enforcement (v2.22.0).** `POST /complete` now refuses (`400`) when an entry has no passing `HostSanityCheck` and no explicit `override_reason` on the body. The override is intentional, audited, and length-capped — the audit trail's core safety claim ("target was verified before testing was closed") is now an invariant, not visibility-only.

**Per-session environment probe (v2.23.0).** The agent's first call on a new execution session is `POST /agent/execution-sessions/{session_id}/environment` — a small capability probe (OS family, shell, PowerShell version + execution policy, real-vs-stub Python, WSL availability, tools on PATH). The recon equivalent is `POST /agent/recon/sessions/{session_id}/environment`. The probe is persisted on the session row with `environment_probed_at`, `environment_probed_by_user_id` (denormalized FK), and `environment_probed_from_ip` for the audit trail. Subsequent `/context` calls echo the probe back so the agent doesn't re-send. **Plans describe test intent; the executing agent translates intent into command flavour at run-time using the probe** — two operators on the same project produce different commands for the same intent (Kali vs Windows + RemoteSigned), and the user's `command_run` per test records the actual translation.

### 5.3 Agent API call audit log (v2.24.0)

A Starlette middleware (`app/services/agent_api_log_service.py`) wraps every `/api/v1/agent/*` request. After the response is sent (so the agent's loop is never blocked), it writes one row to `agent_api_calls` capturing: method, full path + path template, path params, query params, status code, response size, duration, source IP, user agent, API key prefix (never the raw key). For mutations, a JSON-safe body summary is stored (8 KiB cap, multipart skipped, sensitive-shaped fields stripped as defence-in-depth).

The middleware extracts referenced `host_ids` / `entry_ids` / `target_ips` from path + query + body so "did the agent query the right hosts?" is a one-indexed-query answer. Surfaced via `GET /api/v1/projects/{id}/test-plans/{plan_id}/api-activity` (JWT-authenticated — the agent cannot read its own audit log) and the `AgentActivityLog` component on TestPlanDetail.

Retention helper `purge_older_than(db, days)` exists; no automatic schedule yet — operators cron it once volume is understood.

### 5.4 Offline bundle (Workflow D — exported mode)

For air-gapped or long-running engagements, a user can export a plan as a zip bundle instead of running the live API flow:

1. **`POST /projects/{id}/test-plans/{plan_id}/bundle`** (JWT user) — `bundle_service.build_export_bundle` creates an `ExecutionSession` in `exported` mode with a unique `bundle_id`, renders four files into a zip: `manifest.json`, `plan.json`, `instructions.md`, `results_schema.json`. The plan transitions from `approved` to `in_progress`. **No API key is minted** — offline bundles have no network identity.
2. The operator runs the plan against their own infrastructure, producing a `results.json` file that matches `results_schema.json`.
3. **`POST /projects/{id}/test-plans/{plan_id}/import-results`** (JWT user) — `bundle_import_service.import_results_file` validates the file (JSON depth bomb guard, strict `is_finding` bool check, cross-plan/cross-bundle rejection, idempotent `(session, entry, test_index)` upserts), correlates by `bundle_id`, and ingests results + sanity checks + feedback in a single transaction.

Re-importing the same file is safe — it upserts, doesn't append. `is_final=True` transitions the session to `completed`; partial imports leave it `active`.

---

## 6. Security model

Security work landed across v2.9.5, v2.9.7, and v2.9.8. The below is the current state; see CHANGELOG for the audit trail.

- **JWT signing + Fernet credential encryption** use **separate keys** by design. `settings.SECRET_KEY` signs JWTs; `settings.CREDENTIAL_ENCRYPTION_KEY` derives a Fernet key via HKDF for LLM provider + integration credential encryption at rest. A compatibility fallback to `SECRET_KEY` exists when `CREDENTIAL_ENCRYPTION_KEY` is unset (logged once as a deprecation warning); the fallback will be hard-removed in a future major.
- **SSRF protection** is two-layered. `require_public_http_url()` validates user-supplied `base_url` values at save time — parses the URL, enforces `http`/`https` scheme, resolves the hostname via `getaddrinfo`, and rejects every resolved IP in RFC1918, CGNAT, loopback, link-local (including `169.254.169.254`), and IPv6 equivalents. On top of that, `safe_http_client(allow_private=...)` returns an `httpx.Client` with a custom `HTTPTransport` that **re-resolves and re-validates** every outbound hostname at connect time, closing the DNS-rebinding TOCTOU window the plain validator leaves open. Redirects are disabled so a 302 can't land on a private IP. Ollama is the sole integration type with `allow_private=True` because users legitimately run it on localhost.
- **XXE protection** — three coverage strategies depending on parser:
  - `nessus_parser.py` and `openvas_parser.py` use `defusedxml.ElementTree` for parser entry points (`parse`, `iterparse`, `fromstring`) which disables entity expansion, external DTD fetching, and entity references at parse time. Type annotations still reference `xml.etree.ElementTree.Element` because defusedxml only wraps the parser — not the element tree.
  - `nmap_parser.py` and `masscan_parser.py` use `lxml.etree.iterparse` directly with `resolve_entities=False, no_network=True, huge_tree=False` (v2.41.0). lxml is kept because `defusedxml.lxml` is deprecated upstream as of 0.7.1; the explicit flags defeat billion-laughs entity expansion, SYSTEM-entity local-file disclosure, and the huge-tree memory exhaustion vector.
  - All other parsers ingest JSON / CSV / text and don't construct XML at all.
- **Decompression-bomb caps** — `eyewitness_parser.py` enforces per-file (50 MB), running-total (500 MB), and entry-count (5000) caps on uploaded EyeWitness ZIPs (v2.41.0). The streaming extractor counts bytes on the way out and aborts + unlinks the partial file if either cap is exceeded mid-stream, so a spoofed central-directory `uncompressed_size` field can't defeat the check.
- **Streaming JSON for large BloodHound exports** — `bloodhound_parser.py` switches from `json.load` to `ijson.items` for files ≥50 MB (v2.41.0). The structure is auto-detected by peeking the first 64 KB (top-level array vs `{data: [...]}` vs `{computers: [...]}`); files below the threshold keep the fast `json.load` path.
- **Login rate limiting** — `core/security.login_throttle_exceeded()` (v2.41.0) reads `audit_logs` for the trailing 15-minute window and rejects with HTTP 429 if ≥10 failed logins exist for the requested username (across all IPs) or ≥20 failed logins from the requesting IP (across all usernames). Defends against the distributed brute force that previously defeated the per-account 5-strike lockout. The per-account lockout in `authenticate_user()` still applies on top of this.
- **Username-enumeration timing** — `authenticate_user()` (v2.41.0) calls `pwd_context.verify(password, _DUMMY_PASSWORD_HASH)` in the unknown-user, inactive-user, and locked-account branches so all four paths pay the same bcrypt cost. The pre-fix timing channel (5 ms unknown vs 80 ms known) is closed.
- **JSON depth-bomb guard** — `bundle_import_service._assert_json_depth_ok()` scans uploaded results files byte-by-byte before `json.loads`, rejecting anything nested deeper than 20 levels. Legitimate results rarely exceed 5–6. Handles string literals and escape sequences to avoid false positives.
- **Prompt sanitization** — `frontend/src/utils/promptSanitizer.ts` and `backend/app/services/prompt_sanitizer.py` are a **matched pair**. Both strip `X-API-Key: nm_agent_...` lines, redact credential bullets (Access key / Secret key / Password / Username / API key / PDCP token / Secret), and catch bare `nm_agent_` tokens of 20+ chars. The `/llm-providers/{id}/complete` endpoint calls the server sanitizer before forwarding so a caller bypassing the frontend can't deliberately leak. Changing bullet labels in `agent_prompt_service._integration_block` requires updating **both** sanitizers in lockstep.
- **File upload validation** — `ingestion_service` enforces a max chunk-by-chunk size during streaming, slugifies filenames, and runs a magic-byte check after write (`.xml`/`.nessus` must start with `<`, `.json` with `{` or `[`, `.gnmap` with `#` or `Host:`, text files must not contain NUL bytes).
- **Pydantic `max_length` caps** on high-risk write schemas: HostNote body 16 KB, SubnetBase.description 1 KB, ScopeBase.name 256, UserPlanCreate.title 200, EntryCreate.rationale 4 KB, EntryUpdate.findings 16 KB, RejectRequest.reason 2 KB.
- **Execution report raw_output** is trimmed to 16 KB per test result in the rendered view (JSON/HTML/CSV/PDF). The full dump remains in the DB.
- **CORS** — origins restricted to configured values (`settings.CORS_ORIGINS`); never `*`. Deploy script regenerates `.env` with the detected host IP.
- **HTTPS** — production deployments terminate TLS at the Nginx frontend container using self-signed certs by default (`scripts/deploy.sh` generates them during first-time setup). Security headers are set in `ssl-nginx.conf`.

---

## 7. Frontend architecture (React + Vite + MUI)

```
frontend/src/
├── pages/                   # route-level views
│   ├── Dashboard.tsx, PortfolioDashboard.tsx, Scans.tsx, ScanDetail.tsx,
│   │ Hosts.tsx, HostDetail.tsx, Scopes.tsx, ScopeDetail.tsx,
│   │ TestPlans.tsx, TestPlanDetail.tsx, Feedback.tsx, Activity.tsx,
│   │ RiskAssessment.tsx, ParseErrors.tsx, Reference.tsx, ToolReference.tsx,
│   │ UserGuide.tsx, DefaultCredentials.tsx,
│   │ Login.tsx, ForceChangePassword.tsx, Profile.tsx, ProjectSettings.tsx,
│   │ SystemSettings.tsx, LLMSettings.tsx, IntegrationSettings.tsx
├── components/              # shared widgets
│   ├── Layout.tsx, VersionFooter.tsx, UserMenu.tsx, PasswordField.tsx,
│   │ AccessibleIconButton.tsx, CriticalFindingsWidget.tsx,
│   │ HostFilters.tsx, ExportDialog.tsx, InAppAgentPanel.tsx,
│   │ ProposedTestList.tsx, ServiceActions.tsx, ScopeExport.tsx,
│   │ OutOfScopeExport.tsx, ToolReadyOutput.tsx, LastUpdated.tsx,
│   │ PageSkeleton.tsx
├── contexts/
│   ├── AuthContext.tsx      # JWT token + user profile + must_change gate
│   ├── ProjectContext.tsx   # active project, member list, switcher
│   ├── ThemeContext.tsx     # light/dark toggle (persisted to localStorage)
│   └── ToastContext.tsx     # success/warning/error toasts
├── hooks/
│   ├── useConfirm.tsx       # destructive-action confirmation dialog
│   ├── useReconPlan.ts      # /Scopes "Start Agentic Recon" lifecycle
│   └── useReportDownload.ts # /TestPlanDetail report-dialog state machine
├── services/
│   └── api.ts               # axios client, typed interfaces, auth interceptors
├── utils/
│   ├── uiStyles.ts          # shared overflow/clamp/truncate sx objects
│   ├── apiErrors.ts         # formatApiError (handles Pydantic 422 arrays)
│   ├── promptSanitizer.ts   # client-side LLM prompt redaction
│   └── statusMeta.ts        # chip colors + label formatting
└── App.tsx                  # BrowserRouter + ProtectedRoute + role gates
```

Important frontend contracts (enforced by `UI_STYLE_GUIDE.md`):

- **No page-level horizontal overflow.** DB/API values never push the page wider than the viewport.
- **Treat all external values as unbounded.** Every text-bearing component defines truncate/wrap/clamp/collapse behavior.
- **Handle null/empty/loading/error states.** Use `safeFallback()`; never render raw `null`.
- **Tables use `tableLayout: 'fixed'`** with explicit column width strategies.
- **Flex children that truncate include `minWidth: 0`.**
- **`formatApiError()` must flatten Pydantic 422 arrays to strings** — the array shape `[{type, loc, msg, input}]` can't render directly in JSX without triggering React error #31.

---

## 8. Observability, operations, and recovery

- **Structured logs** — backend + worker log to stdout via `logging.basicConfig`. `scripts/collect-logs.sh` bundles backend/worker/db/nginx logs plus auth audit events for support cases.
- **Audit trail** — `audit_logs` captures login/logout, password change, role change, session revoke, upload, scan delete, and other security-relevant events with user_id + IP + user agent.
- **Health checks** — `/health` (backend), `/health.html` (frontend nginx), `pg_isready` (db), `/proc`-grep (worker).
- **Parse errors** — `parse_errors` has a dedicated browse page with `user_message` strings tuned for operators. Linked to `ingestion_jobs` via FK so the UI can show "this scan failed — see error #42".
- **Dead-letter columns** — `ingestion_jobs.retry_count` + `ingestion_jobs.last_error` surface in the `IngestionJobSchema` so the UI can highlight jobs that crashed repeatedly. `ingestion_jobs.skipped_count` + `parser_warnings` (v2.22.0) carry per-job parser quality stats (how many records were dropped, what malformed), persisted from `parser.last_parse_stats` after each successful parse.
- **Orphan reaping** — covered in §4; this is the recovery path when a worker segfaults or the container is killed mid-parse.
- **Version visibility** — `/` returns `{backend, frontend_version}`, the VersionFooter component renders them in the bottom-right of every page, and startup logs print them on every boot.

---

## 9. Extending the system

**Adding a new parser.**
1. Drop a file in `backend/app/parsers/{tool}_parser.py`. Inherit structure from an existing parser; use `parser_utils.ensure_scan` and `extract_first_ip` for shared concerns.
2. Register the parser in `ingestion_service._build_parsing_attempts` under the file-type it serves.
3. Add fixtures to `artifacts/` and a test in `backend/tests/` that runs the parser against the fixture and checks the canonical host/port shape.
4. If the parser touches XML: for stdlib parsing use `defusedxml.ElementTree` (matches `nessus_parser.py` and `openvas_parser.py`); for lxml streaming parsing pass `resolve_entities=False, no_network=True, huge_tree=False` to `etree.iterparse` (matches `nmap_parser.py` and `masscan_parser.py`). Both approaches defeat XXE; never reach for bare `xml.etree` or `lxml.etree` without one of these.

**Adding a new agent workflow.**
1. Decide the scope: does the agent need project-level access or plan-level access? Which fields must its API key bind to?
2. Add the endpoint to the right per-workflow module under `backend/app/api/v1/endpoints/` (`agent_browse.py`, `agent_test_plans.py`, `agent_execution.py`, `agent_recon.py`, or a new one mounted in `api.py`). Use the matching dependency: `require_plan_scope` for plan-scoped, `require_recon_scope` for scope-bound, `deny_scoped_keys` for project-global.
3. Tag new AGENTS.md sections with the workflow name so they appear in the sliced response; update the prompt builder in `agent_prompt_service.py` **and bump `PROMPT_VERSION`** so agents on the old prompt can detect they're stale.
4. Add contract tests to `backend/tests/` that exercise the new endpoint against a fixture plan. The agent API call log middleware will capture the new endpoint automatically — extend the `_collect_referenced_ids` helper if the call carries host/entry references the parser doesn't already pick up.

**Adding a new UI feature.**
1. Route pages belong in `frontend/src/pages/`; shared components in `frontend/src/components/`.
2. Reuse `services/api.ts` typed clients and the shared `uiStyles.ts` helpers. Never write one-off ellipsis `sx` blocks — import `singleLineEllipsisSx`, `twoLineClampSx`, etc.
3. Test worst-case data (200-char hostname, long filename, null values, empty arrays) at mobile and desktop widths.
4. Before shipping: type check clean under strict mode, handle Pydantic 422 shapes in error paths.

**Adding a new service layer module.**
- Single responsibility per file. If a service commits internally, document the policy in the module docstring (see `integration_service.py`, `llm_provider_service.py`). If it defers commit to the caller, say so (see `bundle_service.py`, `bundle_import_service.py`). Commit boundary ambiguity is an audit finding waiting to happen.

---

## 10. Test & quality gates

- **Backend** — `pytest` with a 70% coverage floor. The suite currently runs **251 tests** across service, parser, and contract layers under `backend/tests/`. `conftest.py` uses the SQLAlchemy join-to-outer-transaction + nested savepoint pattern so services that commit internally (integration, LLM provider, agent API log middleware) don't break test isolation. **Postgres is the preferred test backend** — the harness auto-creates a `<app-db>_test` database on the app's own Postgres server when reachable, falling back to in-memory SQLite when not. The Postgres path lets the Postgres-only code (`pg_advisory_lock`, masscan batch-upserts, the raw `pg_catalog` SQL in `delete_scan`) actually run.
- **Frontend** — Vitest + Testing Library. Coverage is thinner than backend — dashboard + version/build flows are covered; host, activity, and upload flows are still on the roadmap.
- **CI** — not yet wired; tests run locally via `docker-compose exec backend python -m pytest` and `cd frontend && npx tsc --noEmit && npm test`.
- **Type safety** — frontend runs TypeScript strict mode; every PR should typecheck clean before merge. Backend uses gradual typing via type hints but does not enforce mypy in CI.

---

## 11. Deployment

Single entry point: `./scripts/deploy.sh`. Options:

1. **Start / rebuild** — builds and starts containers using the current `.env`.
2. **First-time setup** — auto-detects host IP, generates `.env` from `.env.example`, generates SSL certs, starts all four containers. Creates a default `admin` user on first boot. The password is taken from `DEFAULT_ADMIN_PASSWORD` if set; otherwise a cryptographically random URL-safe password is generated (v2.90.3), printed once to backend boot stdout, and written to `/app/uploads/initial-admin-password.txt` for retrieval. `must_change_password=True` is set on the row so first login forces a rotation regardless of source.
3. **Reconfigure IP** — regenerates `.env` + SSL certs for a new host IP.
4. **Nuclear clean** — removes all Docker data (containers, volumes, network) and rebuilds from scratch. Destructive.
5. **Security status** — reports SSL state and whether the database port is exposed.

Auxiliary scripts:

- `scripts/collect-logs.sh` — bundle logs for support.
- `scripts/status.sh` — quick container status check.
- `scripts/preflight.sh` — environment-probe helper for the agentic recon workflow.
- `scripts/transfer-images.sh` — export/import container images for offline or air-gapped moves.
- `scripts/generate-ssl-cert.sh` / `generate-ssl-cert-simple.sh` — SSL certificate helpers (also invoked by `deploy.sh` during first-time setup).

**Scaling.** The stateless backend and frontend containers can run multiple replicas; the worker is currently a single long-lived process and should not be replicated as-is (the `FOR UPDATE SKIP LOCKED` pattern is safe for multi-worker but the orphan reaper logic assumes one reaper). Postgres needs external strategies (managed service, read replicas) for anything beyond single-host deployments. There is no background job scheduler beyond the worker — scheduled maintenance (re-correlation, vuln refresh) is currently triggered on-demand.

---

This document reflects the v2.24.0 state of the system. When a domain, endpoint, or workflow changes, update the relevant section here and bump the version stamp at the top so future maintainers have an accurate map.
