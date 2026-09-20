# BlueStick Architecture

> **Last verified against:** backend 2.370.2 / frontend 5.248.1 (2026-09-19)

BlueStick is a multi-user, multi-project pentest-operations platform that ingests scanner output, deduplicates hosts, correlates them to project scopes, enriches findings with vulnerability data, and drives agent-assisted test-plan generation and execution. This document is the canonical architecture reference — update it when domains, endpoints, or workflows change materially.

---

## 1. System topology

Five containers, one Docker network:

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
           │                                   └────┬──────────────┬──────┘
           │                                        ▲              ▲
           │                       LISTEN           │              │  LISTEN
           │                       ingestion_jobs   │              │  report_jobs
           │                          ┌─────────────┴───┐   ┌──────┴─────────────┐
           │                          │  Worker         │   │  Report-worker     │
           │                          │  (python -m     │   │  (python -m        │
           │                          │  app.worker)    │   │  app.report_worker)│
           │                          │  - ingestion +  │   │  - async JSON/md/  │
           │                          │    orphan reaper│   │    zip generation  │
           │                          └─────────────────┘   └────────────────────┘
           ▼               (both share app/worker_loop.py: LISTEN/poll/reap/heartbeat)
  Browser (React + Tailwind v4 + Radix, hand-rolled SVG/CSS charts, react-router)
```

- **Frontend container** (`networkmapper-frontend-1`) runs Nginx to terminate TLS, serve the Vite build, and reverse-proxy `/api/*` to the backend. The SPA is built on Tailwind v4 + Radix (shadcn-style primitives) + Sonner + lucide-react — Material UI was fully removed in the v4 frontend line (the app is now on the v5 line).
- **Backend container** (`networkmapper-backend-1`) runs `uvicorn app.main:app` with multiple workers. Exposes `/api/v1` and `/health`.
- **Worker container** (`networkmapper-worker-1`) runs `python -m app.worker` — a single long-lived process that LISTENs on the Postgres channel `ingestion_jobs`, polls for queued work with `SELECT … FOR UPDATE SKIP LOCKED`, processes each job through the parser pipeline, and reaps orphaned jobs whose heartbeat has gone stale. The backend writes upload files to a shared volume; the worker reads from the same path.
- **Report-worker container** (`networkmapper-report-worker-1`) runs `python -m app.report_worker` (v2.196.0) — a sibling of the ingestion worker that LISTENs on the `report_jobs` channel and generates the heavy report formats (JSON, agent-package and markdown-bundle archives — PDF export was removed in v2.196.1; CSV and HTML stream synchronously from the API) off the request path. Both workers share the same LISTEN/poll/reconnect/heartbeat/reaper machinery in **`app/worker_loop.py`** (`run_listen_loop`, `install_signal_handlers`, `write_heartbeat`); each entry point only supplies its channel name and per-job processing callback. The report-worker has its own heartbeat-freshness healthcheck (own heartbeat path, reuses `WORKER_HEARTBEAT_TIMEOUT`) and a `mem_limit` sized for an in-memory build of a capped report. The async report pipeline is covered in §8.
- **Database container** (`networkmapper-db-1`) runs PostgreSQL 16 with a persistent volume for data. Schema is owned by **Alembic** — `alembic upgrade head` runs on every backend boot before the app serves traffic. The previous startup-DDL compatibility path (`Base.metadata.create_all` plus a hand-rolled migration list in `app/db/init.py`, and `_ensure_schema` calls in services) has been retired; `app/db/init.py` now does nothing but bring the database up to the Alembic head (serialized across the API + worker processes). The model is the schema, and Alembic enforces it. Migrations live in `backend/alembic/versions/` (~110 revisions at v2.370), baseline at `b46cd59c17f5_baseline_schema`, one linear chain — a second head is a merge bug. No head is named here on purpose: it moves every few releases. Trust `alembic heads` / the `versions/` dir over any chain quoted in prose.

A single `docker-compose.yml` wires all five. `scripts/deploy.sh` is the unified entry point (first-time SSL setup, rebuild, nuclear cleanup, status).

---

## 2. Identity, multi-tenancy, and access control

BlueStick has **two parallel authentication systems** because it serves two very different consumer types.

### 2.1 JWT (human users)

- Users live in `users` (`app/db/models_auth.py`). **The global role is binary** — `ADMIN` (user management, system settings, audit log) or `MEMBER` (v2.46.0). The four-tier vocabulary — admin > analyst > auditor > viewer — is the **per-project** `ProjectRole` on `ProjectMembership.role` (`models_project.py`), which is where every granular check happens.
- `POST /api/v1/auth/login` returns a JWT signed with `settings.SECRET_KEY`. Sessions are tracked in `user_sessions` so tokens can be revoked server-side.
- A default admin account is seeded on first boot by `app/startup.py` with `must_change_password=True`. Its password is `DEFAULT_ADMIN_PASSWORD` when that is set to something other than the literal `admin`, otherwise a random one written mode 0600 to `uploads/initial-admin-password.txt` and never logged — there is no `admin`/`admin` account; the forced-change flow gates every JWT-bearing request behind `require_password_changed` until the user rotates the password.
- RBAC checks are enforced in endpoint dependencies: `require_role(...)` gates ADMIN only; everything granular is the project-scoped `require_project_role(...)`.
- **Two-factor (TOTP)** — **enforced by default** (`REQUIRE_2FA=true`): a login without an enrolled second factor is walked through `ForceTwoFactorSetup` before any data endpoint answers, the same way `ForceChangePassword` gates a fresh password. Recovery codes live in `user_recovery_codes`; an admin can reset a user's 2FA. Users enrol an authenticator app via `/api/v1/auth/2fa/*` (`two_factor.py`): `setup` returns the provisioning secret/QR, `enable` verifies a code and returns one-time recovery codes, `disable` / `recovery-codes` manage the rest. TOTP secret + recovery state live on the `users` row; login challenges the second factor when 2FA is enabled.

### 2.2 Agent sessions and keys (non-human agents)

- Terminal-side agents (Claude Code, Codex, manual curl) authenticate with an `X-API-Key: nm_agent_...` header. Keys are `ApiKey` rows in `api_keys` (`models_auth.py`), hashed at rest; the plaintext is shown to the operator exactly once.
- **A key binds to ONE project-scoped `AgentSession`** (`agent_sessions`, `models_agent.py`) — v2.337.0. There are no per-workflow or per-plan keys, no workflow guards (`require_plan_scope`, `require_recon_scope`, `require_assist_scope`, `require_execution_session_scope` and `deny_scoped_keys` are all gone) and no capability grants (deleted v2.309.0). **The key's authority is its OPERATOR's project role**, re-resolved on every request by `enforce_agent_operator_access` (`app/api/deps.py`), mounted on every `/agent/*` router: writes need the operator to hold `analyst`, bulk exports `auditor`; a role change, a removed membership or a deactivated account reaches keys already in the field immediately. Session-metadata writes (renew, environment probe, end, feedback, tool suggestions) are exempt from the write floor.
- **What the agent is working on is a PHASE the session opens**, not a property of the key: `POST /agent/recon/start {scope_id}` → a `ReconSession`; `POST /agent/test-plans {title}` → a draft `TestPlan`; `POST /agent/execution-sessions/start {plan_id}` → an `ExecutionSession` on a **human-approved** plan. Assist reads need no phase. Each phase links back through `agent_session_id`; a session may open several. What keeps the record trustworthy is object-level: the approval gate, one active run per plan, and a run belongs to the session that opened it.
- Sessions are started by an operator from the UI (AI Assist, Start Agentic Recon, Generate with AI, Execute with AI — all four mint the same kind of key and differ only in which phase is pre-opened), listed and ended at `/projects/{id}/agent-sessions`, and resumed with key rotation (`…/agent-sessions/{sid}/resume`). Keys default to 24 h (`AGENT_KEY_TTL_HOURS`) and the agent can renew its own while the session is under `AGENT_SESSION_MAX_LIFETIME_HOURS` (168 h). **Ending the session, not expiry, is the revocation control.**
- The `/api/v1/agent/*` surface is its own set of FastAPI routers — physically separate from the JWT-user surface — and is **exempt** from `require_password_changed` because the agent has no credentials to rotate. `POST /api/v1/mcp` serves the same endpoints as MCP tools by looping back in-process with the caller's key; **the MCP layer makes no authorization decision**.

### 2.3 Project scoping

Every data-bearing endpoint (upload, hosts, scans, scopes, test plans, etc.) is nested under `/api/v1/projects/{project_id}/...`. The `get_current_project` dependency loads the project, verifies the JWT user is a member, and enforces the required role. Cross-project reads/writes are blocked at the dependency layer; a second project's data is invisible unless the user is explicitly a member.

Portfolio-level endpoints (`/api/v1/portfolio/dashboard`) are global — they return cross-project summaries for users who can see multiple projects.

---

## 3. Backend package map

```
backend/app/
├── main.py                   # app factory, startup hooks, _seed_default_admin, _ensure_default_project
├── worker.py                 # ingestion worker entry point (python -m app.worker)
├── report_worker.py          # report worker entry point (python -m app.report_worker)
├── worker_loop.py            # shared LISTEN/poll/reconnect/heartbeat/reaper loop
│                             # used by both workers (run_listen_loop, write_heartbeat)
├── core/
│   ├── config.py             # settings (SECRET_KEY, CREDENTIAL_ENCRYPTION_KEY, DB URL, timeouts)
│   └── security.py           # password hashing, JWT mint/verify, audit logging
├── api/v1/
│   ├── api.py                # router registration — two tiers: top-level + /projects/{id}
│   └── endpoints/
│       ├── auth.py           # login, logout, change-password, sessions, profile
│       ├── two_factor.py     # /auth/2fa/* — TOTP enrol/enable/disable + recovery codes
│       ├── users.py          # admin CRUD + /users/directory (picker — open to any user)
│       ├── audit.py          # admin audit log browsing
│       ├── system_metrics.py # admin GET /system/queue-metrics (both job queues)
│       ├── projects.py       # project CRUD + membership (add/remove/role)
│       ├── notifications.py  # read/unread, mark-seen
│       ├── portfolio.py      # cross-project dashboard
│       ├── llm_providers.py  # per-user LLM provider credentials
│       ├── integrations.py   # per-user scanner tool credentials
│       ├── feedback.py       # agent feedback ingest (API-key) + admin triage (JWT)
│       ├── agent_browse.py        # /agent/* — shared reads, identity, the ONE environment probe,
│       │                          #   session renew/end, feedback, tool suggestions, host writes
│       ├── agent_test_plans.py    # /agent/* — planning phase (POST /agent/test-plans opens a draft)
│       ├── agent_execution.py     # /agent/* — execution phase (POST /agent/execution-sessions/start)
│       ├── agent_recon.py         # /agent/* — reconnaissance phase (POST /agent/recon/start, upload)
│       ├── agent_assist.py        # /agent/* — the default read surface: inventory, findings, posture…
│       ├── mcp_assist.py          # POST /mcp — MCP transport (loops back in-process; no authz of its own)
│       ├── mcp_tools.py           # declarative MCP tool registry (55 tools → /agent/* routes)
│       ├── mcp_telemetry.py       # admin — per-tool MCP outcomes
│       ├── agent_sessions.py      # JWT — list / end / resume a project's agent sessions
│       ├── agent_activity.py      # JWT — human-facing read of the agent API call log
│       ├── agent_common.py        # shared helpers for the agent routers
│       ├── agent_schemas.py       # Pydantic models for the agent surface
│       └── (project-scoped) upload, scans, hosts (+ host_follow, host_notes,
│                             host_tags, host_bulk, host_filter_views, host_queries),
│                             findings (+ findings_bulk), dns_names (mounted at /names —
│                             names parsed from uploads; the server resolves nothing),
│                             webhooks, dashboard, workbench, scopes (+ subnet_labels),
│                             sites, attention, insights, posture, coverage, export,
│                             reports (+ report_drafts), parse_errors, references,
│                             test_plans (+ test_plan_bundles), assist,
│                             recon_sessions, execution_sessions
│                             # `ls` this directory — the list above drifts
├── db/
│   ├── session.py            # SessionLocal + engine
│   ├── init.py               # startup: runs `alembic upgrade head`, serialized
│   │                         # across API + worker (no more create_all/ALTER DDL)
│   ├── models.py             # core (Host, Port, Scan, IngestionJob, ReportJob,
│   │                         # Site, Annotation + AnnotationStatusHistory,
│   │                         # NoteAttachment, host follow/tags/filter-views/queries)
│   ├── models_findings.py    # Finding spine — canonical correlated-finding layer
│   │                         # (Finding, FindingHost, FindingStatusHistory) that
│   │                         # powers reports / posture / finding-comments
│   ├── models_project.py     # Project, ProjectMembership, WebhookConfig
│   ├── models_auth.py        # User, UserRole, UserSession, AuditLog (+ TOTP fields)
│   ├── models_agent.py       # Agent, AgentSession, TestPlan, TestPlanEntry, TestPlanHistory,
│   │                         # ReconSession, ExecutionSession, AssistSession,
│   │                         # TestExecutionResult, HostSanityCheck, AgentFeedback,
│   │                         # ImportedResultFile, AgentApiCall, McpToolCall, AgentRateBucket
│   │                         # (agent KEYS are `ApiKey` in models_auth.py, table `api_keys`)
│   ├── models_llm.py         # LLMProvider (Fernet-encrypted api_key)
│   ├── models_integrations.py# IntegrationCredential (Fernet-encrypted secrets)
│   ├── models_vulnerability.py # Vulnerability, HostAttribute (+ the VulnerabilitySource /
│   │                         # VulnerabilitySeverity enums; there is no CVE model)
│   ├── models_findings.py    # Finding, FindingHost (per-endpoint state), history
│   ├── models_attribution.py # NetworkAttribution (RDAP), host links
│   ├── models_tools.py       # tool_registry
│   ├── model_registry.py     # imports every model module — CI's `alembic check` fails
│   │                         # (proposes dropping tables) when a new module is missing here
│   └── models_confidence.py  # per-attribute confidence + conflict tracking
├── services/                 # ~80 modules — `ls` is the source; the ones that carry a subsystem:
│   ├── host_query.py, host_query_dsl.py, host_query_predicates.py, host_serialization.py
│   │                         # the Hosts query DSL (the SAME engine serves the UI, agents and
│   │                         # MCP) and the one host/note serializer (`note_load_options`)
│   ├── finding_service.py    # the findings spine: promote/dismiss (issue- or host-scoped),
│   │                         # per-endpoint state, history
│   ├── staged_import_service.py, format_registry.py
│   │                         # staged upload review: detection basis, format override,
│   │                         # retention; FORMATS is the one list of parseable file types
│   ├── job_transitions.py    # locked status transitions shared by both durable queues
│   ├── agent_session_service.py, agent_prompt_service.py, agent_prompt_history.py
│   │                         # one project session + key, phases, the prompt + PROMPT_VERSION
│   ├── dns_name_service.py   # named assets: the single write path for name observations
│   ├── attribution_correlation.py, host_assessment_service.py, scope_coverage.py
│   │                         # RDAP attribution; per-domain evidence freshness; scope membership
│   ├── operations_read_service.py, host_change_service.py
│   │                         # the Operations surface: blockers, change inbox, "worth a look"
│   ├── tool_registry_service.py, mcp_client_setup_service.py, mcp_telemetry_service.py
│   ├── ingestion_service.py  # upload write, magic-byte validation, per-job parser
│   │                         # dispatch + retry_count/last_error (the LISTEN/poll/
│   │                         # reaper loop itself lives in worker_loop.py)
│   ├── report_job_service.py # report_jobs queue (enqueue/claim/heartbeat/complete)
│   ├── report_generator.py   # builds CSV/JSON/HTML/markdown + archive report artifacts (no PDF since v2.196.1)
│   ├── queue_metrics_service.py # operational metrics for both durable job queues
│   ├── host_deduplication_service.py
│   ├── subnet_correlation.py # host ↔ subnet mapping (ip_trie for speed)
│   ├── ip_trie.py
│   ├── vulnerability_service.py
│   ├── confidence_service.py
│   ├── risk_insight_service.py
│   ├── posture_service.py        # /posture composition (label + conclusion + remediation flow + heatmap)
│   ├── systemic_insight_service.py # cross-sectional pattern families + blind spots + monocultures
│   ├── subnet_insight_service.py # per-subnet exposure/neglect/hygiene lens
│   ├── pattern_families.py       # the program-level weakness taxonomy + classify()
│   ├── evidence_service.py       # per-domain assessment-coverage (eligible vs assessed)
│   ├── attention_service.py      # per-project / per-site exposure + neglect rollups
│   ├── host_condition_sets.py    # per-condition host-id sets (shared by systemic + the has: DSL)
│   ├── ports_of_interest.py
│   ├── host_follow_service.py
│   ├── notification_service.py
│   ├── parse_error_service.py
│   ├── export_service.py + report_templates.py
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
│   ├── eyewitness_parser.py / dns_parser.py / dnsx_parser.py
│   ├── nikto_parser.py / amass_parser.py / bloodhound_parser.py
│   ├── httpx_parser.py / dirbuster_parser.py / smbmap_parser.py / netexec_parser.py
│   └── parser_utils.py (shared ensure_scan/extract_first_ip helpers)
└── schemas/
    ├── schemas.py           # primary Pydantic request/response models
    ├── findings.py          # Finding-spine request/response shapes
    ├── test_plan_schemas.py # test-plan / entry / proposed-test shapes
    └── pagination.py        # shared paginated-response envelope
```

Routers stay thin — business logic lives in `services/`. Parsers only normalize external formats to the canonical host/port/vulnerability representation; they never touch HTTP concerns.

**Subsystems a new dev should know about (where they live):**

- **Finding spine** — `db/models_findings.py` (`Finding`, `FindingHost`, `FindingStatusHistory`) is the canonical correlated-finding layer that de-duplicates raw vulnerabilities into one record per finding-across-hosts. Reports, the posture dashboard, and finding-comments all read this spine rather than raw `vulnerabilities`. Schemas in `schemas/findings.py`.
- **Sites** — `Site` (`db/models.py`) is a project-scoped grouping of subnets/hosts with tiered weighting; managed via `endpoints/sites.py` and feeds per-site attention rollups.
- **Posture hub** — the manager-facing analytics surface, four read-only lenses over the existing host/finding data, all composing (never re-collecting):
  - **Posture** (`endpoints/posture.py` → `posture_service`) — the executive rollup: a deterministic security-condition label (`action_required` / `needs_assessment` / `insufficient_evidence` / `no_urgent_signals`, evidence-gated so an unassessed estate never reads clear), a plain-language conclusion, the in-engagement remediation flow (from `FindingStatusHistory`), and the condition-family × site heatmap.
  - **Patterns** (frontend `/posture/patterns` → `endpoints/insights.py` `/insights/systemic` → `systemic_insight_service`) — cross-sectional analysis: recurring weaknesses grouped into program-level **pattern families** (`pattern_families.py`: identity & auth, encryption & trust, lifecycle & patching, legacy & cleartext, lateral-movement, vulnerability & technology monocultures), classified `isolated` / `recurring` / `estate_wide`.
  - **Segments** (frontend `/posture/segments` → `endpoints/insights.py` `/insights/subnets` → `subnet_insight_service`) — the per-subnet exposure/neglect/hygiene lens plus a server-authoritative per-site rollup, behind a Site | Subnet toggle.
  - **Evidence** (`endpoints/posture.py` `/posture/evidence` → `evidence_service`) — per-assessment-domain coverage (eligible vs assessed hosts: discovery, service/version, vulnerability, web/TLS, auth/SMB/AD, validation) answering whether the conclusions are trustworthy.
  - The per-condition host sets live once in `host_condition_sets.py`, so a systemic count and its `has:<condition>` drill-down on the Hosts page resolve the identical hosts. Legacy frontend paths `/insights` and `/insights/systemic` redirect into the hub. (The backend `endpoints/insights.py` routes were **not** renamed — only the frontend page paths moved.)
- **Webhooks** — `WebhookConfig` (`db/models_project.py`) + `endpoints/webhooks.py` manage per-project outbound notification hooks (egress goes through the SSRF-safe HTTP client).
- **Annotations (notes) + attachments** — the note system is `Annotation` (+ `AnnotationStatusHistory`) in `db/models.py` with generalized targets (host, finding, etc. — see `finding_id`); file attachments live in `NoteAttachment` and are purged on note delete.

---

## 4. Scan ingestion pipeline

Uploads flow through an asynchronous worker so the request path never blocks on parsing. The flow is deliberately unforgiving of crashes — every state transition is durable in Postgres.

0. **Staged review (the UI path, v2.351.0+).** The Scans page uploads with `stage=true`: the job is registered as `staged`, no worker touches it, and `staged_import_service.detect_for_job` reports each candidate format with its *basis* — recognised by `structure`, by `filename` only, or a `fallback` the dispatcher would merely try. The operator reviews (`UploadReviewDialog` + `useUploadReview`), optionally chooses a format, and `POST /upload/jobs/{id}/start` moves the job to `queued` under a row lock. An identical file already in the project (a scan, or a staged/queued/processing job — SHA-256) is refused with 409 `duplicate_scan`, and start re-runs that check. Files are retained `INGESTION_RETAIN_FILES_DAYS` (default 7) after a job finishes so a wrong format can be retried or the file re-processed; an unstarted staged job expires after 24 h. Agents and direct API callers skip staging and queue immediately. **The real parsers can never be dry-run** (they write and commit incrementally), so a preview is detection plus a read-only sample, never a parser run.
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

Parsed hosts flow through `host_deduplication_service` (dedupe by IP within project), `subnet_correlation` (bind hosts to scopes via an IP trie), and `vulnerability_service` (enrichment). `risk_insight_service` and `ports_of_interest` compute the derived tables that power the dashboard.

---

## 5. Test plan + agent workflow

The test plan system is the operational backbone for agent-assisted pentesting. The `/api/v1/agent/*` surface is split into routers by KIND OF WORK — `agent_browse` (shared reads, identity, session lifecycle), `agent_test_plans`, `agent_execution`, `agent_recon`, `agent_assist`, `agent_activity` — but since v2.337.0 that split is code organisation, not authorization: **one project-scoped session and key reaches all of them**, and a session OPENS A PHASE for each kind of work (§2.2). The "workflow" letters below name the kinds of work, not kinds of key.

### 5.1 Plan generation (Workflow A)

The session opens a draft with **`POST /agent/test-plans {title}`** (or is handed the `plan_id` of a draft the operator created with *Generate with AI* — or from a FIXED host selection, `source_kind='manual_hosts'`, in which case `/context` restricts `candidate_hosts` to that list and says so in a `source` block). Then:

1. **`GET /agent/test-plans/{plan_id}/context`** — paginated candidate hosts. Returns `CandidateHost` rows with vuln counts, services, ports, a `meets_policy` flag computed by `_evaluate_host_policy()`, and an `inferred_service_hints` list that fills in canonical service names (smb, rdp, mssql, …) when nmap missed service detection. The response also carries the selection policy as a string so the agent can echo it in rationales.
2. **`POST /agent/test-plans/{plan_id}/entries`** — batch-create entries. Each entry has a host_id, priority, test_phase, a list of `proposed_tests`, and a rationale. Response is wrapped: `{"entries": [...]}`.
3. **`GET /agent/test-plans/{plan_id}/validate`** — dry-run summary. Returns `PreSubmitReport` with `warnings`, `by_priority`, `by_phase`, and a `coverage` block split into `policy_matching_remaining` (real missed scope, actionable) and `non_policy_with_open_ports` (correctly skipped, informational). Never blocks on coverage — only on empty plan, missing description, or short rationales.
4. **`POST /agent/test-plans/{plan_id}/submit`** — moves plan to `pending_review`.
5. **`POST /agent/feedback`** — agents post friction notes, API critiques, and tool suggestions at the end of every run. Rows land in `agent_feedback` with `status='new'`; a human triages via the admin-only `/api/v1/feedback/...` surface.

The selection policy, rubric, and endpoint shapes are documented exhaustively in **AGENTS.md** at the project root, which agents fetch via `GET /api/v1/agents-guide?workflow=plan_generation`. The guide uses HTML-comment section markers (`<!-- agents:section tags="..." -->`) so a single source file can emit workflow-scoped slices to save tokens.

### 5.2 Execution (Workflow B)

Once a human has **approved** a plan in the UI, the user clicks "Execute with AI":

1. The execution phase is opened on the approved plan — by the agent with **`POST /agent/execution-sessions/start {plan_id}`**, or by the operator's **`POST /projects/{id}/test-plans/{plan_id}/execute`** (JWT), which mints a project session with the run already open and hands the operator the key + instructions. Either way `open_execution_phase` enforces the one gate the consolidation kept: the plan must be human-approved and non-empty, one run per plan is active at a time, a run this session already has open is reused and another session's active run is paused. The start response carries a `read_back` of the run's concrete bounds (the plan's hosts, the working directory) that the agent states before acting.
2. Agent calls **`GET /agent/test-plans/{plan_id}/execution-context`** — returns hosts, entries, and `proposed_tests[]` with `{ip}` placeholders already resolved.
3. **Per-entry: sanity check → approval → results.** For each entry, the agent MUST first run a host sanity check (banner grab, DNS lookup, ICMP) and POST it to `/sanity-check`. The UI gates test execution on a passing sanity check. Then for each test the agent requests user approval in the terminal, runs the command locally, and POSTs results to `/test-results`. Status transitions on each entry track progress.
4. **`POST /agent/test-plans/{plan_id}/entries/{entry_id}/complete`** — closes the entry.
5. **`POST /agent/feedback`** — same endpoint, different workflow tag.

The agent is a **coordinator, not an executor**. The user's terminal runs every command. BlueStick provides the plan, the sanity check gate, the audit trail, and the result schema.

**Sanity-check enforcement (v2.22.0).** `POST /complete` now refuses (`400`) when an entry has no passing `HostSanityCheck` and no explicit `override_reason` on the body. The override is intentional, audited, and length-capped — the audit trail's core safety claim ("target was verified before testing was closed") is now an invariant, not visibility-only.

**Per-session environment probe (v2.23.0; one endpoint since v2.337.0).** A session begins with `POST /agent/session/environment` — ONE probe per session (it replaced the three per-phase probe endpoints): OS family, shell, PowerShell version + execution policy, real-vs-stub Python, WSL availability, tools on PATH, plus the agent's `agent_model` / `agent_tool` / `agent_prompt_version`. The probe is snapshotted onto every recon/execution run the session opens and echoed in later context responses, so command flavour matches the operator's actual host. Per session, per user — two operators on one project produce independent probes.

### 5.3 Agent API call audit log (v2.24.0)

A Starlette middleware (`app/services/agent_api_log_service.py`) wraps every `/api/v1/agent/*` request. After the response is sent (so the agent's loop is never blocked), it writes one row to `agent_api_calls` capturing: method, full path + path template, path params, query params, status code, response size, duration, source IP, user agent, API key prefix (never the raw key). For mutations, a JSON-safe body summary is stored (8 KiB cap, multipart skipped, sensitive-shaped fields stripped as defence-in-depth).

The middleware extracts referenced `host_ids` / `entry_ids` / `target_ips` from path + query + body so "did the agent query the right hosts?" is a one-indexed-query answer. Surfaced via `GET /api/v1/projects/{id}/test-plans/{plan_id}/api-activity` (JWT-authenticated — the agent cannot read its own audit log) and the `AgentActivityLog` component, shown on the test plan's API-calls tab (`pages/test-plan/ApiCallsTab.tsx`), the recon run page and the assist sessions page.

Retention helper `purge_older_than(db, days)` exists; no automatic schedule yet — operators cron it once volume is understood.

### 5.4 Offline bundle (Workflow D — exported mode)

For air-gapped or long-running engagements, a user can export a plan as a zip bundle instead of running the live API flow:

1. **`POST /projects/{id}/test-plans/{plan_id}/bundle`** (JWT user) — `bundle_service.build_export_bundle` creates an `ExecutionSession` in `exported` mode with a unique `bundle_id`, renders four files into a zip: `manifest.json`, `plan.json`, `instructions.md`, `results_schema.json`. The plan transitions from `approved` to `in_progress`. **No API key is minted** — offline bundles have no network identity.
2. The operator runs the plan against their own infrastructure, producing a `results.json` file that matches `results_schema.json`.
3. **`POST /projects/{id}/test-plans/{plan_id}/import-results`** (JWT user) — `bundle_import_service.import_results_file` validates the file (JSON depth bomb guard, strict `is_finding` bool check, cross-plan/cross-bundle rejection, idempotent `(session, entry, test_index)` upserts), correlates by `bundle_id`, and ingests results + sanity checks + feedback in a single transaction.

Re-importing the same file is safe — it upserts, doesn't append. `is_final=True` transitions the session to `completed`; partial imports leave it `active`.

### 5.5 Assist (Workflow E — query the project)

For the senior-tester case where the operator just wants to *query* a project — "which hosts expose FTP?", "summarize my critical findings", "what did the last recon turn up?" — without a plan or an approval ceremony (v2.64.0). Assist is the DEFAULT surface of every session: it needs no phase.

1. **`POST /projects/{id}/assist/start`** (JWT user, `assist.py`) mints the same project session as every other entry point, with no phase pre-opened, and returns the key plus the agent prompt. It requires only `auditor` (lowered from `analyst` in v2.308.0 — safe because a key carries its operator's permissions). Sessions are listed and ended from the project's agent-sessions surface.
2. The agent reads through `/agent/assist/*` (`agent_assist.py`): context, hosts (list / count / detail / vulnerabilities / notes / testing / web interfaces), findings, posture, patterns, segments, coverage, vocabulary, names, scans, ingestion issues, plus the NDJSON downloads (`hosts.ndjson`, `report-context.ndjson`). The same host query DSL as the Hosts page drives `q=`.
3. **Reads need project membership; bulk exports need `auditor`; writes are whatever the operator's role allows.** There is no "assist-scoped" key and no capability grant (both gone — v2.337.0 / v2.309.0): an analyst's assist session can write notes, review status and hostname/OS corrections; an auditor's or viewer's cannot, because its operator cannot. Nothing narrows writes to "assigned" hosts. An agent cannot approve a plan or triage a finding under any role.
4. **Reviewable after the fact.** `/assist-sessions` lists every session with what it produced; the detail view leads with the notes the agent wrote (its only durable output) over the per-session API-call feed. A session whose key has expired reports as `ended` immediately — derived on read, with an hourly sweep converging the stored column (`assist_session_service`).

### 5.6 MCP transport (Workflow F — all of the above, as tools)

Every workflow above is also reachable over the **Model Context Protocol** at `POST /api/v1/mcp` (`mcp_assist.py` for the transport, `mcp_tools.py` for the declarative registry). A `tools/call` loops back into the same `/agent/*` endpoint **in-process** via an ASGI transport, forwarding the caller's `X-API-Key`, so auth, capability gating, row scope, and the audit log run unchanged — the MCP layer makes no authorization decision of its own.

`tools/list` returns the WHOLE catalogue to every session (55 tools; the per-workflow filter went with the per-workflow keys in v2.337.0). Listing was always presentation rather than authorisation — the endpoint behind a tool decides on every call. Bulk, file-shaped endpoints (NDJSON streams, target lists, `recon/upload`) are deliberately *not* tools.

See [MCP.md](MCP.md) for the transport details, the per-client certificate-pinning story, the approved-tool registry, and the guardrail model.

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
- **Pydantic `max_length` caps** on high-risk write schemas: AnnotationBase.body 16 KB, SubnetBase.description 1 KB, ScopeBase.name 256, UserPlanCreate.title 200, EntryCreate.rationale 4 KB, EntryUpdate.findings 16 KB, RejectRequest.reason 2 KB.
- **Execution report raw_output** is trimmed to 16 KB per test result in the rendered view (JSON/HTML/CSV/PDF). The full dump remains in the DB.
- **CORS** — origins restricted to configured values (`settings.CORS_ORIGINS`); never `*`. Deploy script regenerates `.env` with the detected host IP.
- **HTTPS** — production deployments terminate TLS at the Nginx frontend container using self-signed certs by default (`scripts/deploy.sh` generates them during first-time setup). Security headers are set in `ssl-nginx.conf`.

---

## 7. Frontend architecture (React + Vite + Tailwind v4 + Radix)

The frontend is a Vite-built React SPA. Material UI + Emotion were fully removed in the v4 frontend line; the substrate is now **Tailwind v4** for styling, **Radix** primitives wrapped shadcn-style under `components/ui/`, **Sonner** for toasts, and **lucide-react** for icons. Navigation is organised into SIX hubs — Operations (the landing page), Inventory, Posture, Workflows, Collaboration, Settings — declared as data in a single manifest (`config/navigation.tsx`, `HUB_DEFS`) and rendered by `components/Layout.tsx` + `components/HubRedirect.tsx`. There are no per-hub page components.

```
frontend/src/
├── pages/                   # route-level views — `ls frontend/src/pages` is the source; by hub:
│   │ Operations · PortfolioDashboard
│   │ Inventory:  Hosts, HostDetail, Scans, ScanDetail, ScanDiff, Names, Findings,
│   │             FindingDetail, Scopes, ParseErrors (Ingestion Results), NetworkTopology
│   │ Posture:    SecurityPosture, Segments, Patterns, Evidence
│   │ Workflows:  TestPlans, TestPlanCompare, PlanCompare, ExecutionsList, ExecutionDetail,
│   │             ReconRunsList, ReconRunDetail, ReconCompare, AssistSessions
│   │ Collaboration: Activity, ProjectActivity, ToolActivity, Feedback
│   │ Settings / reference: ProjectSettings, SystemSettings, LLMSettings,
│   │             IntegrationSettings, Profile, Reference, ToolReference, McpReference,
│   │             SbomReference, DefaultCredentials
│   │ Gates:      Login, ForceChangePassword, ForceTwoFactorSetup
│   ├── test-plan/           # the plan detail: TestPlanLayout + PlanTab, RunsTab,
│   │                        # ActivityTab, ApiCallsTab (agent call log), DangerTab
│   └── userguide/           # UserGuideShell + GettingStarted / Triage / Data / Admin / Agents guides
├── components/
│   ├── ui/                  # ~30 primitives — mostly Radix-wrapped shadcn-style
│   │                        # (dialog, select, tabs, tooltip, …) plus local ones
│   │                        # (SeverityBar, data-table, code-block, meta-field, info-tip)
│   ├── host-inspector/      # the inspector's sections (InspectorSection, PortDetailsCard,
│   │                        # VulnerabilityGroup, HostConflictsPanel, NoteComposer, …)
│   ├── scans/               # UploadReviewDialog (staged upload), FormatRetryDialog, ImportResult
│   ├── posture/, mcp/, execution/, workflow/, hosts/
│   └── (shared widgets)     # Layout, HubRedirect, UserMenu (About → versions), CommandPalette,
│                            # HostInspector, HostFilters, HostCommandBar, ToolReadyOutput, …
├── contexts/
│   ├── AuthContext.tsx      # JWT token + user profile + must_change / 2FA gates
│   ├── ProjectContext.tsx   # active project, member list, switcher
│   ├── ThemeContext.tsx     # theme selection, persisted to localStorage
│   │                        # (the five palettes live in theme/palettes.ts)
│   └── ToastContext.tsx     # success/warning/error toasts (Sonner-backed)
├── hooks/                   # 13 — notably:
│   ├── useUploadReview.ts   # the staged-upload state machine (API injected, so it tests with renderHook)
│   ├── useVisibilityPoll.ts # THE way to poll: no ticks in a hidden tab, no overlap, back-off
│   ├── useLatestRequest.ts, useDebouncedValue.ts, useCompareSelection.ts, useKeyboardShortcuts.ts
│   ├── useConfirm.tsx       # destructive-action confirmation dialog
│   ├── useReconPlan.ts      # /Scopes "Start Agentic Recon" lifecycle
│   └── useReportDownload.ts # execution report-dialog state machine
├── services/
│   └── api/                 # axios client split into per-domain modules
│                            # (hosts, scans, scopes, dashboard, … + shared primitives)
├── config/
│   └── navigation.tsx       # single navigation manifest (hubs, roles, palette)
├── utils/
│   ├── uiStyles.ts          # shared overflow/clamp/truncate helpers
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

- **Structured logs** — backend + workers log to stdout via `logging.basicConfig`. `scripts/collect-logs.sh` bundles backend/worker/db/nginx logs plus auth audit events for support cases.
- **Audit trail** — `audit_logs` captures login/logout, password change, role change, session revoke, upload, scan delete, and other security-relevant events with user_id + IP + user agent.
- **Health checks** — `/health` (backend), `/health.html` (frontend nginx), `pg_isready` (db), heartbeat-freshness checks (both workers — each rewrites its own heartbeat file every loop, so a wedged worker goes stale → unhealthy).
- **Async report pipeline** — heavy report formats (PDF, JSON, zip bundles) are generated off the request path. The API enqueues a `report_jobs` row (`ReportJobService`), the **report-worker** claims it via `SELECT … FOR UPDATE SKIP LOCKED`, builds the artifact with `report_generator.py`, and writes it to the shared `uploads/report_artifacts` volume; the UI polls job status and downloads the finished file. Mirrors the ingestion pipeline (same `worker_loop.py`, same orphan-reaping semantics). See §1 for the container.
- **Queue metrics** — admin-only `GET /api/v1/system/queue-metrics` (`system_metrics.py` → `queue_metrics_service.py`) reports depth/in-flight/failure counts for **both** durable job queues (`ingestion_jobs` + `report_jobs`) so operators can see backlog and stuck work at a glance.
- **Parse errors** — `parse_errors` has a dedicated browse page with `user_message` strings tuned for operators. Linked to `ingestion_jobs` via FK so the UI can show "this scan failed — see error #42".
- **Dead-letter columns** — `ingestion_jobs.retry_count` + `ingestion_jobs.last_error` surface in the `IngestionJobSchema` so the UI can highlight jobs that crashed repeatedly. `ingestion_jobs.skipped_count` + `parser_warnings` (v2.22.0) carry per-job parser quality stats (how many records were dropped, what malformed), persisted from `parser.last_parse_stats` after each successful parse.
- **Orphan reaping** — covered in §4; this is the recovery path when a worker segfaults or the container is killed mid-parse.
- **Version visibility** — `GET /` returns `{message, version, frontend_version, instance_id, cors_origins}` (the backend version is the `version` key), the user menu's **About BlueStick** item shows both (the bottom-right VersionFooter was retired by UX audit #12 and its component deleted in 5.247.0), and startup logs print them on every boot.

---

## 9. Extending the system

**Adding a new parser.**
1. Drop a file in `backend/app/parsers/{tool}_parser.py`. Inherit structure from an existing parser; use `parser_utils.ensure_scan` and `extract_first_ip` for shared concerns.
2. Register the parser in `ingestion_service._build_parsing_attempts` under the file-type it serves.
3. Add fixtures to `artifacts/` and a test in `backend/tests/` that runs the parser against the fixture and checks the canonical host/port shape.
4. If the parser touches XML: for stdlib parsing use `defusedxml.ElementTree` (matches `nessus_parser.py` and `openvas_parser.py`); for lxml streaming parsing pass `resolve_entities=False, no_network=True, huge_tree=False` to `etree.iterparse` (matches `nmap_parser.py` and `masscan_parser.py`). Both approaches defeat XXE; never reach for bare `xml.etree` or `lxml.etree` without one of these.

**Adding a new agent workflow.**
1. Decide the scope: does the agent need project-level access or plan-level access? Which fields must its API key bind to?
2. Add the endpoint to the right per-workflow module under `backend/app/api/v1/endpoints/` (`agent_browse.py`, `agent_test_plans.py`, `agent_execution.py`, `agent_recon.py`, `agent_assist.py`, or a new one mounted in `api.py`). Use the matching dependency: `require_plan_scope` for plan-scoped, `require_recon_scope` for scope-bound, `require_assist_scope` for read-only assist, `deny_scoped_keys` for project-global.
3. Tag new AGENTS.md sections with the workflow name so they appear in the sliced response; update the prompt builder in `agent_prompt_service.py` **and bump `PROMPT_VERSION`** so agents on the old prompt can detect they're stale.
4. Add contract tests to `backend/tests/` that exercise the new endpoint against a fixture plan. The agent API call log middleware will capture the new endpoint automatically — extend the `_collect_referenced_ids` helper if the call carries host/entry references the parser doesn't already pick up.

**Adding a new UI feature.**
1. Route pages belong in `frontend/src/pages/`; shared components in `frontend/src/components/`.
2. Reuse `services/api.ts` typed clients and the shared `uiStyles.ts` helpers. `uiStyles.ts` exports only `safeFallback` and `stickyBelowChrome`; truncation is Tailwind (`truncate`, `line-clamp-*`, `break-words`) composed with `cn()` (`utils/cn.ts`). There is no `sx` prop anywhere — that was MUI.
3. Test worst-case data (200-char hostname, long filename, null values, empty arrays) at desktop widths, including a narrowed window. Desktop-only product — no mobile layouts (UI_STYLE_GUIDE.md §3).
4. Before shipping: type check clean under strict mode, handle Pydantic 422 shapes in error paths.

**Adding a new service layer module.**
- Single responsibility per file. If a service commits internally, document the policy in the module docstring (see `integration_service.py`, `llm_provider_service.py`). If it defers commit to the caller, say so (see `bundle_service.py`, `bundle_import_service.py`). Commit boundary ambiguity is an audit finding waiting to happen.

---

## 10. Test & quality gates

- **Backend** — `pytest` with a 68% coverage floor enforced in CI (the floor is a ratchet — raise it as coverage climbs). The suite runs **~1,850 tests** in ~200 modules (v2.370) across service, parser, and contract layers under `backend/tests/`. `conftest.py` uses the SQLAlchemy join-to-outer-transaction + nested savepoint pattern so services that commit internally (integration, LLM provider, agent API log middleware) don't break test isolation. **Postgres is the preferred test backend** — the harness auto-creates a `<app-db>_test` database on the app's own Postgres server when reachable, falling back to in-memory SQLite when not. The Postgres path lets the Postgres-only code (`pg_advisory_lock`, masscan batch-upserts, the raw `pg_catalog` SQL in `delete_scan`) actually run.
- **Frontend** — Vitest + Testing Library. Coverage spans page-level views (Hosts, Operations, ProjectActivity, the execution + recon detail/list views, the compare views), shared components (HostFilters, HostCommandBar, HostLineagePanel, ExecutionSession), and pure utilities (host query-DSL translation, tool-ready output, navigation manifest, version consistency).
- **CI** — `.github/workflows/ci.yml` runs on push-to-main and PRs, three jobs: **alembic-roundtrip** walks every migration down and back up against a throwaway Postgres (`scripts/test-alembic-roundtrip.sh`); **backend** boots a Postgres service, runs `alembic upgrade head` + `alembic check` (model/migration drift), then `python -m pytest -q` enforcing the 68% floor from `backend/pytest.ini`; **frontend** (Node 22) runs `tsc --noEmit` → `vitest run` → `npm run build`. Locally the backend suite runs in a one-off container with `backend/` mounted — see CONTRIBUTING.md; its harness sets `BLUESTICK_SKIP_DB_INIT=1` so a test run never migrates the developer's database.
- **Type safety** — frontend runs TypeScript strict mode with `noUnusedLocals` / `noUnusedParameters`, so an unused import fails `npm run build` (and the image build); every PR should typecheck clean before merge. Backend uses gradual typing via type hints but does not enforce mypy in CI.

---

## 11. Deployment

Single entry point: `./scripts/deploy.sh`. Options:

1. **Start / rebuild** — builds and starts containers using the current `.env`.
2. **First-time setup** — auto-detects host IP, generates `.env` from `.env.example`, generates SSL certs, starts all five containers. Creates a default `admin` user on first boot. The password is taken from `DEFAULT_ADMIN_PASSWORD` if set; otherwise a cryptographically random URL-safe password is generated (v2.90.3), printed once to backend boot stdout, and written to `/app/uploads/initial-admin-password.txt` for retrieval. `must_change_password=True` is set on the row so first login forces a rotation regardless of source.
3. **Reconfigure IP** — regenerates `.env` + SSL certs for a new host IP.
4. **Nuclear clean** — removes all Docker data (containers, volumes, network) and rebuilds from scratch. Destructive.
5. **Security status** — reports SSL state and whether the database port is exposed.

Auxiliary scripts:

- `scripts/collect-logs.sh` — bundle logs for support.
- `scripts/status.sh` — quick container status check.
- `scripts/preflight.sh` — environment-probe helper for the agentic recon workflow.
- `scripts/transfer-images.sh` — export/import container images for offline or air-gapped moves.
- `scripts/generate-ssl-cert.sh` / `generate-ssl-cert-simple.sh` — SSL certificate helpers (also invoked by `deploy.sh` during first-time setup).

**Scaling.** The stateless backend and frontend containers can run multiple replicas; the ingestion and report workers are each currently a single long-lived process and should not be replicated as-is (the `FOR UPDATE SKIP LOCKED` claim is safe for multiple workers, but the orphan-reaper logic assumes one reaper per queue). Postgres needs external strategies (managed service, read replicas) for anything beyond single-host deployments. There is no background job scheduler beyond the two workers — scheduled maintenance (re-correlation, vuln refresh, agent-API-log purge) is currently triggered on-demand.

---

This document reflects the backend 2.254.1 / frontend 5.152.1 (2026-08-11) state of the system. When a domain, endpoint, or workflow changes, update the relevant section here and bump the version stamp at the top so future maintainers have an accurate map.
