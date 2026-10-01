# BlueStick Architecture

> **Last verified against:** backend 2.427.1 / frontend 5.309.1 (2026-09-26)

BlueStick is a multi-user, multi-project pentest-operations platform that ingests scanner output, deduplicates hosts, correlates them to project scopes, enriches findings with vulnerability data, and records agent-assisted testing: tests proposed on hosts and the evidence of what they produced. This document is the canonical architecture reference — update it when domains, endpoints, or workflows change materially.

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
  Browser (React + Tailwind v4 + Radix, Observable Plot charts, three.js terrain, react-router)
```

- **Frontend container** (`networkmapper-frontend-1`) runs Nginx to terminate TLS, serve the Vite build, and reverse-proxy `/api/*` to the backend. The SPA is built on Tailwind v4 + Radix (shadcn-style primitives) + Sonner + lucide-react — Material UI was fully removed in the v4 frontend line (the app is now on the v5 line). Charts with real axes use **Observable Plot** (`components/charts/PlotFigure.tsx`, 5.307.0); the Operations address terrain is **three.js** (`components/operations/TerrainScene.tsx`), loaded as a lazy chunk only when scrolled near; small inline visuals stay hand-built SVG.
- **Backend container** (`networkmapper-backend-1`) runs `uvicorn app.main:app` with multiple workers. Exposes `/api/v1` and `/health`.
- **Worker container** (`networkmapper-worker-1`) runs `python -m app.worker` — a single long-lived process that LISTENs on the Postgres channel `ingestion_jobs`, polls for queued work with `SELECT … FOR UPDATE SKIP LOCKED`, processes each job through the parser pipeline, and reaps orphaned jobs whose heartbeat has gone stale. The backend writes upload files to a shared volume; the worker reads from the same path.
- **Report-worker container** (`networkmapper-report-worker-1`) runs `python -m app.report_worker` (v2.196.0) — a sibling of the ingestion worker that LISTENs on the `report_jobs` channel and generates the heavy report formats (JSON, agent-package and markdown-bundle archives, and the Quarto client-report renders — PDF export was removed in v2.196.1; CSV and HTML stream synchronously from the API) off the request path. Both workers share the same LISTEN/poll/reconnect/heartbeat/reaper machinery in **`app/worker_loop.py`** (`run_listen_loop`, `install_signal_handlers`, `write_heartbeat`); each entry point only supplies its channel name and per-job processing callback. The report-worker has its own heartbeat-freshness healthcheck (own heartbeat path, reuses `WORKER_HEARTBEAT_TIMEOUT`) and a `mem_limit` sized for an in-memory build of a capped report. JSON and the agent package stream every matching host chunk by chunk straight to the artifact file (`ReportGenerator.write_json_report` / `write_agent_package`, v2.394.0; 80k hosts: ~80 s, ~0.5 GB peak, a 2.6 GB file), so they are never truncated; only the markdown bundle is still built in memory and capped at `REPORT_MAX_INMEMORY_HOSTS`. The async report pipeline is covered in §8.
- **Database container** (`networkmapper-db-1`) runs PostgreSQL 16 with a persistent volume for data. Schema is owned by **Alembic** — `alembic upgrade head` runs on every backend boot before the app serves traffic. The previous startup-DDL compatibility path (`Base.metadata.create_all` plus a hand-rolled migration list in `app/db/init.py`, and `_ensure_schema` calls in services) has been retired; `app/db/init.py` now does nothing but bring the database up to the Alembic head (serialized across the API + worker processes). The model is the schema, and Alembic enforces it. Migrations live in `backend/alembic/versions/` (~133 revisions at v2.427), baseline at `b46cd59c17f5_baseline_schema`, one linear chain — a second head is a merge bug. No head is named here on purpose: it moves every few releases. Trust `alembic heads` / the `versions/` dir over any chain quoted in prose.

A single `docker-compose.yml` wires all five. `scripts/deploy.sh` is the unified entry point (first-time setup, rebuild, reconfigure IP, nuclear cleanup, security status, `.env`/SSL backup, roll back — §11).

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

- Terminal-side agents (Claude Code, Codex, manual curl) authenticate with an `X-API-Key: nm_agent_...` header. Keys are `APIKey` rows in `api_keys` (`models_auth.py`), hashed at rest; the plaintext is shown to the operator exactly once.
- **A key binds to ONE project-scoped `AgentSession`** (`agent_sessions`, `models_agent.py`) — v2.337.0. There are no per-workflow keys, no workflow guards (`require_plan_scope`, `require_recon_scope`, `require_assist_scope`, `require_execution_session_scope` and `deny_scoped_keys` are all gone) and no capability grants (deleted v2.309.0). **The key's authority is its OPERATOR's project role**, re-resolved on every request by `enforce_agent_operator_access` (`app/api/deps.py`), mounted on every `/agent/*` router: writes need the operator to hold `analyst`, bulk exports `auditor`; a role change, a removed membership or a deactivated account reaches keys already in the field immediately. Session-metadata writes (renew, end, feedback, tool suggestions) are exempt from the write floor.
- **The operator drives; the agent opens its own work** (v2.433.0 — the "agent on rails" model of approved tools → recon run → drafted plan → human approval → execution with a mandatory per-host sanity check was retired). An agent reads a scope by id (`/agent/scopes/{scope_id}/subnets|domains` and the target files `hosts.ndjson` / `live-hosts.txt` / `web-targets.txt`); there is no recon-run record (removed with its `/agent/recon/*` and `/recon-sessions` routes). Uploads belong to the session (`POST /agent/uploads` → an `IngestionJob` with `agent_session_id`, no run needed; batches keyed `(agent_session_id, label)`). `POST /agent/host-tests` puts individual tests on hosts (`host_tests`, shown on each host's page) and `POST /agent/evidence` records what the agent ran, naming the test it answers — no plan, no run and no approval step (test plans and execution runs were removed in v2.442.0, §5). Each row carries the `agent_session_id` of the session that wrote it; a session may write any number, in any order. `agent_session_service.end_agent_session` — the one path for the operator's End, the agent's `end_session` and the hourly lapse sweep — revokes the keys; nothing else needs closing, because tests and evidence are project data that another session or a person carries on. The safety rules (`agent_policy.SAFETY_RULES`) are the agent's contract, which the server cannot enforce: show every command, the operator drives, stay in the declared scope, write into the working directory, record every command.
- Sessions are started by an operator from **Operations → Start Agent Session** (`POST /projects/{id}/assist/start` — the one entry point since v2.433.0; pages about one object open the same dialog with a one-line task), listed and ended at `/projects/{id}/agent-sessions`, and resumed with key rotation (`…/agent-sessions/{sid}/resume`). Keys default to 24 h (`AGENT_KEY_TTL_HOURS`) and the agent can renew its own while the session is under `AGENT_SESSION_MAX_LIFETIME_HOURS` (168 h). **Ending the session, not expiry, is the revocation control.**
- The `/api/v1/agent/*` surface is its own set of FastAPI routers — physically separate from the JWT-user surface — and is **exempt** from `require_password_changed` because the agent has no credentials to rotate. `POST /api/v1/mcp` serves the same endpoints as MCP tools by looping back in-process with the caller's key; **the MCP layer makes no authorization decision**.

### 2.3 Project scoping

Every data-bearing endpoint (upload, hosts, scans, scopes, host tests, etc.) is nested under `/api/v1/projects/{project_id}/...`. The `get_current_project` dependency loads the project, verifies the JWT user is a member, and enforces the required role. Cross-project reads/writes are blocked at the dependency layer; a second project's data is invisible unless the user is explicitly a member.

Portfolio-level endpoints (`/api/v1/portfolio/dashboard`) are global — they return cross-project summaries of the projects the caller belongs to (a global admin sees every non-archived one). Oversight (`/api/v1/oversight/dashboard`, v2.377.0) is the administrators' programme view of EVERY registered project, archived included; its router is mounted with `require_role(ADMIN)`, so members and project admins get 403. Both read the same counting services (§3, "Cross-project counts").

---

## 3. Backend package map

```
backend/app/
├── main.py                   # app factory, lifespan, GET /, /health, /.well-known/networkmapper.json
├── startup.py                # seed_default_admin, ensure_default_project, seed_system_identity,
│                             # expired-session cleanup + agent-API-call/audit retention loops
├── data/                     # parser_coverage.json ("What BlueStick reads"), tool_registry_seed.json
├── worker.py                 # ingestion worker entry point (python -m app.worker)
├── report_worker.py          # report worker entry point (python -m app.report_worker)
├── worker_loop.py            # shared LISTEN/poll/reconnect/heartbeat/reaper loop
│                             # used by both workers (run_listen_loop, write_heartbeat)
├── core/
│   ├── config.py             # settings (SECRET_KEY, CREDENTIAL_ENCRYPTION_KEY, DB URL, timeouts)
│   ├── security.py           # password hashing, JWT mint/verify, audit logging
│   └── request_context.py    # pure-ASGI middleware: X-Request-ID + per-request latency
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
│       ├── portfolio.py      # cross-project dashboard (members' projects)
│       ├── oversight.py      # administrators' programme dashboard (global admins only)
│       ├── llm_providers.py  # per-user LLM provider credentials
│       ├── integrations.py   # per-user scanner tool credentials
│       ├── feedback.py       # agent feedback ingest (API-key) + admin triage (JWT)
│       ├── agent_browse.py        # /agent/* — shared reads, identity,
│       │                          #   session renew/end, feedback, tool suggestions, host writes
│       ├── host_tests.py          # host tests — ONE router factory mounted twice: /projects/{id}/host-tests (JWT) and /agent/host-tests (key)
│       ├── agent_recon.py         # /agent/* — scope reads (/agent/scopes/{scope_id}/subnets|domains + target files) + uploads (POST /agent/uploads)
│       ├── agent_assist.py        # /agent/* — the default read surface: inventory, findings, posture…
│       ├── agent_assist_operations.py  # agent reads of Operations (workbench, Worth a look, terrain), Evidence gaps, scan compare
│       ├── agent_assist_reporting.py   # agent reads of scanner observations and client reports
│       ├── mcp_assist.py          # POST /mcp — MCP transport (loops back in-process; no authz of its own)
│       ├── mcp_tools.py           # declarative MCP tool registry (56 tools → /agent/* routes)
│       ├── mcp_telemetry.py       # admin — per-tool MCP outcomes
│       ├── agent_sessions.py      # JWT — list / end / resume a project's agent sessions
│       ├── agent_activity.py      # JWT, project-scoped (NOT under /agent) — human-facing read of the agent API call log
│       ├── agent_common.py        # shared helpers for the agent routers
│       ├── agent_schemas.py       # Pydantic models for the agent surface
│       └── (project-scoped) upload, scans, hosts (+ host_follow, host_notes,
│                             host_tags, host_bulk, host_filter_views, host_queries),
│                             findings (+ findings_bulk), dns_names (mounted at /names —
│                             names parsed from uploads; the server resolves nothing),
│                             scanner_observations, webhooks, dashboard,
│                             workbench (+ /investigate, /terrain), scopes (+ subnet_labels),
│                             sites, attention, insights, posture, coverage, export,
│                             reports (+ report_drafts), client_reports, parse_errors,
│                             host_tests, proposals, assist
│                             (top-level, not per project: references — /references/*,
│                             activity — cross-project tool activity at /activity)
│                             # `ls` this directory — the list above drifts
├── db/
│   ├── session.py            # SessionLocal + engine
│   ├── init.py               # startup: runs `alembic upgrade head`, serialized
│   │                         # across API + worker (no more create_all/ALTER DDL)
│   ├── models.py             # core (Host, Port, Scan, ScanBatch, IngestionJob, ReportJob,
│   │                         # Host/PortScanHistory, Scope/ScopeDomain, Subnet, Site,
│   │                         # WebInterface/WebPath, DNSName/DNSRecord, ParseError,
│   │                         # Annotation + AnnotationStatusHistory, NoteAttachment,
│   │                         # host follow/tags/filter-views/queries, Operations/Activity cursors)
│   ├── models_findings.py    # Finding spine — canonical correlated-finding layer
│   │                         # (Finding, FindingHost (per-endpoint state), FindingVulnerability,
│   │                         # FindingStatusHistory) that powers reports / posture / comments
│   ├── models_reports.py     # client reports: ReportProfile, Report (issued snapshot), ReportFile
│   ├── models_project.py     # Project, ProjectMembership, Notification, NoteMention,
│   │                         # WebhookConfig, WebhookDelivery
│   ├── models_auth.py        # User (+ TOTP fields), UserRole, UserRecoveryCode, UserSession,
│   │                         # AuditLog, APIKey, SystemIdentity
│   ├── models_agent.py       # Agent, AgentSession, AssistSession, AgentFeedback,
│   │                         # AgentApiCall, McpToolCall, AgentRateBucket
│   ├── models_host_tests.py  # HostTest (v2.442.0 — replaced the plan / entry / run / result / sanity-check models)
│   │                         # (agent KEYS are `APIKey` in models_auth.py, table `api_keys`)
│   ├── models_llm.py         # LLMProvider (Fernet-encrypted api_key)
│   ├── models_integrations.py# IntegrationCredential (Fernet-encrypted secrets)
│   ├── models_vulnerability.py # Vulnerability, HostAttribute (+ the VulnerabilitySource /
│   │                         # VulnerabilitySeverity enums; there is no CVE model)
│   ├── models_attribution.py # NetworkAttribution (RDAP), host links
│   ├── models_tools.py       # tool_registry
│   ├── model_registry.py     # imports every model module — `alembic check` fails
│   │                         # (proposes dropping tables) when a new module is missing here
│   ├── models_confidence.py  # per-attribute confidence + conflict tracking, NetexecResult
│   ├── cursor_upsert.py      # race-safe per-(user, project) cursor upsert
│   └── backfill_vulnerability_exploitable.py # one-shot v2.83.2 backfill
├── services/                 # ~95 modules — `ls` is the source; the ones that carry a subsystem:
│   ├── host_query.py, host_query_dsl.py, host_query_predicates.py, host_serialization.py
│   │                         # the Hosts query DSL (the SAME engine serves the UI, agents and
│   │                         # MCP) and the one host/note serializer (`note_load_options`)
│   ├── finding_service.py    # the findings spine: promote/dismiss (issue- or host-scoped),
│   │                         # per-endpoint state, history
│   ├── finding_actions.py    # the finding writes a click AND an accepted proposal run:
│   │                         # authored-content rule (FindingActor), report text, promote/dismiss
│   ├── proposal_service.py, agent_evidence_service.py
│   │                         # agent proposals (a person accepts/rejects) + evidence records
│   ├── scanner_observation_service.py, vuln_identity.py, misconfig_checks.py, misconfig_backfill.py
│   │                         # scanner rows grouped by issue (issue_key / check_id), bulk
│   │                         # promote; the weakness catalog and its backfill
│   ├── staged_import_service.py, format_registry.py, scan_batch_service.py, import_attention_service.py
│   │                         # staged upload review: detection basis, format override,
│   │                         # retention; FORMATS is the one list of parseable file types;
│   │                         # upload batches; failed/partial imports that still need attention
│   ├── line_shapes.py        # ShapeTally — redacted shapes of the lines a parser did not interpret
│   ├── parser_coverage.py    # serves app/data/parser_coverage.json ("What BlueStick reads")
│   ├── os_family.py, os_eol.py # OS family derived from the OS name; end-of-life OS catalog
│   ├── address_terrain_service.py # /workbench/terrain — hosts per /24 (IPv6 /64) by work state
│   ├── client_report_service.py, client_report_render.py, quarto_render.py (+ quarto_fields.lua),
│   │   report_template_service.py, report_draft_service.py
│   │                         # client reports: content, issue/revise/addenda, Quarto rendering (§8)
│   ├── host_query_suggest.py # DSL value autocomplete (/hosts/query/suggest)
│   ├── totp_service.py, webhook_dispatcher.py
│   ├── job_transitions.py    # locked status transitions shared by both durable queues
│   ├── agent_session_service.py, agent_prompt_service.py, agent_prompt_history.py
│   │                         # one project session + key, the prompt + PROMPT_VERSION
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
│   ├── posture_service.py        # /posture composition (label + conclusion + heatmap + disposition)
│   ├── engagement_metrics_service.py # cross-project counts: targets, tested, findings, judged / not-yet-judged
│   │                             # observations, defect rate, period activity, contributors, testers, growth
│   ├── project_signals_service.py # per-project open tasks, runs, admins, last import, "quiet"
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
│   ├── report_templates.py         # the host report's stylesheet and table scripts
│   ├── subnet_calculator.py      # (the subnet CSV reader is parsers/subnet_parser.py)
│   ├── command_explanation_service.py
│   ├── prompt_sanitizer.py         # strips secrets before LLM calls
│   ├── url_validator.py            # SSRF validation + IP-pinning httpx client
│   ├── host_test_service.py        # host tests: idempotent batch create, revision-checked update, list
│   ├── host_test_queries.py        # the ONE definition of "planned" and "tested" hosts
│   ├── llm_provider_service.py     # Fernet crypto, multi-provider chat completion
│   ├── integration_service.py      # Fernet crypto, scanner credential CRUD
│   ├── nessus_integration_service.py  # direct-from-Nessus pulls (if configured)
│   ├── sbom_service.py             # /reference/sbom — reads requirements.txt
│   │                               # + frontend package-lock.json, memoised by
│   │                               # manifest mtimes + app_version (v2.20.0)
│   └── agent_api_log_service.py    # middleware + helpers for the agent API call
│                                   # audit log (v2.24.0)
├── parsers/                  # one module per tool — `ls` is the source:
│   ├── nmap_parser.py / gnmap_parser.py / masscan_parser.py / naabu_parser.py / rustscan_parser.py
│   ├── nessus_parser.py (defusedxml) / openvas_parser.py / nuclei_parser.py
│   ├── eyewitness_parser.py / httpx_parser.py / whatweb_parser.py / testssl_parser.py / nikto_parser.py
│   ├── dns_parser.py / dnsx_parser.py / amass_parser.py / rdap_parser.py / subnet_parser.py
│   ├── bloodhound_parser.py / smbmap_parser.py / netexec_parser.py / dirbuster_parser.py
│   └── shared helpers: content_detection.py (looks_like_* sniffers), parser_utils.py
│                         # (ensure_scan, extract_first_ip, read_tool_text, record_savepoint),
│                         # xml_stream_helpers.py (iterparse_safe — the lxml XXE flags),
│                         # streaming_json.py, nse_vulns.py (nmap NSE vuln results)
└── schemas/
    ├── schemas.py           # primary Pydantic request/response models
    ├── findings.py          # Finding-spine request/response shapes
    ├── client_reports.py, dns_names.py
    ├── metric.py            # the slim posture metric (value, numerator/denominator, drilldown_filter)
    ├── host_test_schemas.py # host-test create / batch / update shapes (shared by people and agents)
    └── pagination.py        # shared paginated-response envelope
```

Routers stay thin — business logic lives in `services/`. Parsers only normalize external formats to the canonical host/port/vulnerability representation; they never touch HTTP concerns.

**Subsystems a new dev should know about (where they live):**

- **Finding spine** — `db/models_findings.py` (`Finding`, `FindingHost`, `FindingStatusHistory`) is the canonical correlated-finding layer that de-duplicates raw vulnerabilities into one record per finding-across-hosts. Reports, the posture dashboard, and finding-comments all read this spine rather than raw `vulnerabilities`. Schemas in `schemas/findings.py`.
- **Sites** — `Site` (`db/models.py`) is a project-scoped grouping of subnets/hosts with tiered weighting; managed via `endpoints/sites.py` and feeds per-site attention rollups.
- **Posture hub** — the manager-facing analytics surface, four read-only lenses over the existing host/finding data, all composing (never re-collecting):
  - **Posture** (`endpoints/posture.py` → `posture_service`) — the executive rollup: a deterministic security-condition label (`action_required` / `needs_assessment` / `insufficient_evidence` / `no_urgent_signals`, evidence-gated so an unassessed estate never reads clear), a plain-language conclusion, and the condition-family × segment heatmap. There is no remediation flow (removed in v2.374; pinned absent): a project is one assessment window ending at the report.
  - **Patterns** (frontend `/posture/patterns` → `endpoints/insights.py` `/insights/systemic` → `systemic_insight_service`) — cross-sectional analysis: recurring weaknesses grouped into program-level **pattern families** (`pattern_families.py`: identity & auth, encryption & trust, lifecycle & patching, legacy & cleartext, lateral-movement, vulnerability & technology monocultures), classified `isolated` / `recurring` / `estate_wide`.
  - **Segments** (frontend `/posture/segments` → `endpoints/insights.py` `/insights/subnets` → `subnet_insight_service`) — the per-subnet exposure/neglect/hygiene lens plus a server-authoritative per-site rollup, behind a Site | Subnet toggle.
  - **Evidence** (`endpoints/posture.py` `/posture/evidence` → `evidence_service`) — per-assessment-domain coverage (eligible vs assessed hosts: discovery, service/version, vulnerability, web/TLS, auth/SMB/AD, validation) answering whether the conclusions are trustworthy.
  - The per-condition host sets live once in `host_condition_sets.py`, so a systemic count and its `has:<condition>` drill-down on the Hosts page resolve the identical hosts. Legacy frontend paths `/insights` and `/insights/systemic` redirect into the hub. (The backend `endpoints/insights.py` routes were **not** renamed — only the frontend page paths moved.)
- **Cross-project counts** (v2.376.0–2.378.0) — Portfolio (members) and Oversight (global admins) never compute figures themselves: `engagement_metrics_service` owns targets, tested (in review or reviewed, each host once), findings (issues, false positives excluded), scanner observations split judged / not yet judged, the defect rate over tested targets, period activity, contributors (a union over records that already carry an author — not the auth-only `AuditLog`), per-tester rows and the growth series; `project_signals_service` owns open tasks (host tests still to do — proposed or in progress), active sessions (open agent sessions — `agent_sessions.status = 'active'`), admins, last import and "quiet" (the pending-plan-review count went with plan approval in v2.433.0, and blocked runs with execution runs in v2.442.0). "Judged" is the host inspector's `_vuln_coverage` rule as SQL over the stored `vulnerabilities.issue_key` (migration `f7b2d4e6a8c1`), pinned row by row by `tests/test_engagement_metrics.py`. Figures are labelled current, selected period or through-end; there is no remediation dimension. Design: `PORTFOLIO.md` (local).
- **Scanner observations and the weakness catalog** — raw `vulnerabilities` rows are *scanner observations* until someone judges them. `vuln_identity.py` is the ONE answer to "what issue is this?" (exact CVE, else normalised title; never fuzzy), stored as `vulnerabilities.issue_key` and used by the inspector's grouping, the finding dedup and cross-host promote. `misconfig_checks.py` is the catalog of misconfigurations the parsers recognise — one title/severity/write-up per check whatever tool saw it, `record_misconfig` the only write path, keyed by `check_id` (the DSL's `kind:` / `check:`); `scripts/backfill_misconfigs.py` (`misconfig_backfill.py`) records it for earlier imports, and `scripts/repair_netexec_results.py` (`netexec_repair.py`) corrects NetExec rows an older parser rule wrote wrongly (logins that were results, "None" host names, mount-daemon ports named "nfs"). The Findings hub's "Scanner observations" view (`endpoints/scanner_observations.py` → `scanner_observation_service`) groups rows by issue across hosts and bulk-promotes them.
- **Findings hub** — Findings, a finding's page and the client **Reports** page (`/reports`) share one hub (`config/navigation.tsx`); see §8 for client reports.
- **Parser coverage ("What BlueStick reads")** — `app/data/parser_coverage.json`, served by `parser_coverage.py` at `GET /references/parser-coverage` and shown at `/reference/tool-coverage`: per tool, what it reports, the level BlueStick takes each item to (observation / field / text / stored / discarded), where it is shown and the known gaps. `tests/test_parser_coverage.py` pins it to the parsers, so a parser change updates its rows in the same commit.
- **Uninterpreted lines** — a parser may publish `last_parse_stats["uninterpreted"]`: the lines it dropped or kept only as text, as REDACTED shapes (`line_shapes.ShapeTally`), stored on `ingestion_jobs.uninterpreted_lines` and shown on Ingestion Results, to agents (`assist_list_uninterpreted_lines`) and in `collect-logs.sh`. `documentation/PARSE_AUDIT_BRIEF.md` is how an on-site agent turns them into parser fixes without samples leaving the client network.
- **OS family** — `os_family.py` derives nmap's `osfamily` vocabulary from an OS name when no scanner supplied a family (Nessus, NetExec, corrections); it fills a blank and never replaces a scanned value.
- **Operations workbench** — `endpoints/workbench.py`: the personal surface, `GET /workbench/investigate` ("Worth a look": untouched hosts with a reason, ranked by a stated tier IN SQL, with `tier_counts`) and `GET /workbench/terrain` (`address_terrain_service`: hosts per /24 — IPv6 /64 — as tested / planned / worked / untouched, drawn by the three.js terrain). "Untouched" is one definition, `host_query_predicates.untouched_conditions`, shared with the DSL's `has:untouched`.
- **Host inspector by service** — `components/host-inspector/`: Weaknesses first, then Services with per-port evidence panels (`ServiceEvidencePanel.tsx`), host-level evidence, then notes.
- **Webhooks** — `WebhookConfig` (`db/models_project.py`) + `endpoints/webhooks.py` manage per-project outbound notification hooks (egress goes through the SSRF-safe HTTP client).
- **Annotations (notes) + attachments** — the note system is `Annotation` in `db/models.py` with generalized targets (host, finding, etc. — see `finding_id`). **Notes are discussion (v2.446.0):** a thread carries a type and a pin, nothing else — status, assignee, due date, resolution, the status history and the note → finding promotion were removed from the application (work is a host test and its evidence, §5; a finding comes from a scanner observation, a test result, or is written directly). The columns and `annotation_status_history` were dropped in v2.447.0 (migration `c2e6a4f8d103`; the downgrade recreates them empty). A thread's type is one of `observation`, `question`, `decision`, `handoff`; `finding` and `action` are older labels a thread may still carry but can no longer be given. Report screenshots go on a finding's own comments; file attachments live in `NoteAttachment` and are purged on note delete.

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
2. **Worker claims the job.** `poll_and_run_one()` claims the oldest queued job through `job_transitions.claim_oldest_queued` (`FOR UPDATE SKIP LOCKED`), transitions it to `processing` inside the same transaction (so competing workers skip it) and records `claimed_at` as a fencing token that conditions the later heartbeat / complete / fail writes, then runs outside the row lock. Every job status write goes through `job_transitions`.
3. **Parser dispatch.** `_process_job` sniffs the file sample, builds a list of parser attempts (most-specific first), and executes them in sequence. The first parser that returns a non-empty result wins. Each parser call is wrapped in a `ParseFailure` boundary so a crash records a `parse_errors` row with a user-facing message and binds the ID back onto the job.
4. **Heartbeat.** Parsers call `service.update_heartbeat(job_id, progress=...)` periodically. The heartbeat check also serves as a cooperative cancellation and timeout point — if the job was cancelled by a user or has exceeded `INGESTION_JOB_TIMEOUT`, the parser raises `ParseFailure` with a terminal message.
5. **Terminal state.** Success → `status='completed'`, `scan_id` set, `tool_name` set. Parse failure → `status='failed'`, `retry_count++`, `last_error` populated with a trimmed traceback or the user message. Unexpected exception → same, but with a `traceback` last_error.
6. **Orphan reaping.** On a monotonic deadline of ~12 poll intervals (~1 min, idle or busy — `worker_loop.run_listen_loop`'s `periodic` callbacks, beside the backlog check and the webhook sweep), the worker runs `reap_orphaned_jobs()`: a `processing` job whose heartbeat is older than `INGESTION_JOB_TIMEOUT` × `INGESTION_ORPHAN_CUTOFF_MULTIPLIER` (default 1.5) is re-queued automatically while `retry_count` ≤ `INGESTION_MAX_RETRIES` (default 2) and its upload is still on disk; otherwise it fails with a "worker likely crashed" message and admins are notified. Closes the gap when a worker dies mid-parse.

Inside a parser, two rules keep one bad record or byte from failing the import: a parser that skips a failing record wraps it in `parser_utils.record_savepoint` (a failed flush otherwise poisons the whole session), and a parser whose text lines reach a Text column reads the file with `parser_utils.read_tool_text` (UTF-16 decoded, NUL removed — PostgreSQL rejects NUL). A parser may also report the lines it did not interpret as redacted shapes (`ingestion_jobs.uninterpreted_lines`, §3). Files uploaded together form a `ScanBatch`; a failed or partial import stays on the attention list until dismissed or superseded by a clean import of the same file (`import_attention_service`).

Parsed hosts flow through `host_deduplication_service` (dedupe by IP within project), `subnet_correlation` (bind hosts to scopes via an IP trie), and `vulnerability_service` (enrichment). `ports_of_interest` is the catalog of exposed services that the subnet insights and Operations read.

---

## 5. Host tests + agent workflow

Agent-assisted testing is recorded as **host tests** and **evidence records**: what was proposed for a host, and what was actually run against it. The `/api/v1/agent/*` surface is split into routers by KIND OF WORK — `agent_browse` (shared reads, identity, session lifecycle), `host_tests` (its agent router), `agent_proposals` (evidence + proposals), `agent_recon` (scope reads + uploads), `agent_assist` (plus `agent_browse.renewal_router` and the agent feedback router; `agent_activity` is the JWT viewer of the call log, not part of this surface) — but since v2.337.0 that split is code organisation, not authorization: **one project-scoped session and key reaches all of them** (§2.2). There is no required order and no approval step (v2.433.0): the operator drives, and the agent works within the operator's project role.

**Test plans and execution runs were removed in v2.442.0.** `TestPlan`, `TestPlanEntry`, `TestPlanHistory`, `ExecutionSession`, `TestExecutionResult`, `HostSanityCheck` and `ImportedResultFile`, their routers (`test_plans`, `test_plan_bundles`, `execution_sessions`, `agent_test_plans`, `agent_execution`), `test_plan_service`, the offline bundle (`bundle_service`, `bundle_import_service`), the plan execution report (`export_service`) and the pages built on them are gone. Two Alembic revisions carry the change: `d7e1a9c4b602_host_tests` creates `host_tests`, adds `evidence_records.host_test_id` / `request_key` / `request_hash` and `dns_records.evidence_record_id`, and converts the old data — every proposed test of every plan entry becomes a host test (labelled with the plan's title; `done` / `dismissed` / `proposed` / `in_progress` from the entry, `dismissed` when the plan was archived), every execution result becomes an evidence record (executed + finding → `finding`, executed → `inconclusive`, failed → `failed`, skipped / not applicable → `info`; pending rows are not converted), and it stops with the offending id rather than skip a shape it does not recognise. `f4b8d2a6c917_drop_test_plans` then drops the seven tables and the columns that pointed at them (`findings.exec_result_id`, `dns_records.exec_result_id`, `annotations.plan_id` — a plan's notes move to the plan's project — and `test_plan_id` / `execution_session_id` on `agent_feedback` and `agent_api_calls`). Sanity checks, plan history and the plan / run structure have no replacement table: the pre-upgrade backup is the only copy, and the downgrade recreates the schema empty.

### 5.1 Host tests

A `HostTest` (`models_host_tests.py`) is one check on one host: `tool`, `description`, `command` (with `{ip}` / `{fqdn}` placeholders), `rationale`, `expected_result`, `references`, `priority`, a free `label` (a batch of proposed tests shares one), optional `target_fqdn` / `name_id` (a name observed at the host — the snapshot survives the name's deletion), `status` (`proposed` → `in_progress` → `done`, or `dismissed` with a reason, who and when), `assigned_to_id`, `tester_summary`, `source` (`agent` | `person`) and the proposer's attribution (user, session, model, client, prompt version). It belongs to its host (`ON DELETE CASCADE`) and is shown on the host page's Tests section; there is no project-wide tests page.

- **One contract for people and agents.** `endpoints/host_tests.py` builds its router once (`make_router(reader, writer)`) and `api.py` mounts it twice: under `/projects/{id}` for the JWT user (any member reads, `analyst` writes) and under `/agent` for the session key (the router-level operator gate decides writes). Both call `host_test_service` with the same Pydantic schemas (`schemas/host_test_schemas.py`), and the MCP tools `host_tests_propose|list|get|update` advertise those schemas.
- **Creation is a validated batch** (1–200) and is idempotent per test: `request_key` (unique per project) plus a hash of the content — the same key and content returns the existing row, the same key with different content is a `409`. An unknown host fails the whole batch.
- **Updates are revision-checked.** `PATCH` carries `expected_revision`; the update is one conditional `UPDATE … WHERE revision = :expected`, so a stale write changes nothing and answers `409`. `done` needs an evidence record or a `tester_summary` saying why none was run; `dismissed` needs a reason.
- **No approval gate.** A proposed test is visible at once. Which hosts and which tests is the operator's request to the agent; the server has no selection policy.
- **Planned / tested** are two SQL definitions in `host_test_queries.py`, used by the Hosts list and DSL (`has:planned`, `has:tested`), `/coverage`, the per-host assessment (`host_assessment_service`), Evidence and the Operations terrain: *planned* = the host has a test that is `proposed` or `in_progress`; *tested* = the host has an evidence record whose outcome is `finding`, `no_finding` or `inconclusive`. *Untouched* (`host_query_predicates.untouched_conditions`) = no follow, note, non-dismissed test, evidence record or finding endpoint.
- **My Tasks** (`operations_read_service.compute_my_tasks`) is the tests still to do that are assigned to the caller, on a host they have in review, or unassigned and critical/high; `project_signals_service.open_tasks` and the engagement tester rows count active host tests the same way.

### 5.2 Working a test: evidence

The agent is a **coordinator, not an executor**: the operator's terminal runs every command, under the client's sandbox. To work a test the agent (or a person) moves it to `in_progress`, runs the command, and records what came back with **`POST /agent/evidence`** — `host_test_id`, a `request_key` (required with a test; a retry returns the stored record), the command as run, the outcome, a summary and the raw output — then marks the test `done`. Evidence is immutable (§5.7). When the test was aimed at a name (`target_fqdn`) and the record carries `observed_ip` with a test outcome, `agent_evidence_service` writes the name's `TESTED` observation through `dns_name_service.record_observation(evidence_record_id=…)`; a failed attempt, an `info` record or a record with no observed address binds nothing.

**A person adds a test by hand (5.324.0).** *Add test* in the Tests section and *Add a test* on an open weakness open one side panel (`AddTestPanel`, rendered once by `hostTestsController`) that posts a single test to the same `POST /projects/{id}/host-tests` an agent proposes through — no second route. From a weakness it sends that row's `vulnerability_id`, defaults the priority to its severity and, when no reason is typed, gives the weakness as the rationale.

**Replays and revisions (v2.445.1).** `record_result` leaves replay validation to `record_evidence` (`server_timed=True` keeps the server's clock out of the fingerprint): the same result under a key returns the stored record, a changed one is a 409. It always runs the revision-conditional UPDATE, so a result that leaves the status unchanged still consumes the revision. `update_test` validates the state the row will have (a finished test cannot lose its only explanation). The routes serialize before `commit()` — the session expires on commit, and reading each row's id afterwards was one SELECT per test. In the browser, `hostTestsController` drops an answer that is not for its latest request: the inspector stays mounted while the analyst steps between hosts.

**A person works a test on its row (v2.443.0).** The Tests section's one primary action, *Record result*, calls `POST /projects/{id}/host-tests/{test_id}/result` (`host_test_service.record_result`): it writes the same evidence record through `agent_evidence_service.record_evidence` (attributed to the user, no session) and moves the test in the same transaction — `finding` / `no_finding` close it, `inconclusive` / `failed` leave it in progress. Under a record whose outcome is `finding`, *Create finding* calls `POST /projects/{id}/evidence/{id}/finding` (`agent_evidence_service.create_finding_from_evidence` → `FindingService.create_finding`, the record row locked), which attaches the record's host and links the record to the finding. An agent's finding stays a proposal (§5.7); only a person's click creates one directly.

**A test can belong to a weakness (v2.445.0 / 5.322.0).** `host_tests.issue_key` / `issue_title` (migration `a9c3e7f1b248`) are set at proposal from `vulnerability_id` (`host_test_service.resolve_issue` — the observation must be on the same host) and hold the ISSUE's identity (`vuln_identity`), not the row id, so a re-scan that replaces the row keeps the link. Two things follow, and together they make one promotion path instead of two: (1) *Promote to finding* under a linked test's result calls the same `promote_or_dismiss_vulnerability(scope="host")` the weakness row's button calls — it joins the issue's finding (`dedup_key`) rather than creating a parallel one; an unlinked test still creates a finding from a title and severity. (2) Promoting the observation from the weakness row links that issue's `finding`-outcome evidence on the host to the finding (`agent_evidence_service.link_issue_evidence`, called from `finding_actions`). On the page the host's tests are loaded ONCE (`host-inspector/hostTestsController.tsx`; context in `hostTestsContext.ts`, which has no API-client import) and read by both the Weaknesses rows (`VulnerabilityGroup`: a closed row says where its tests stand, an open one lists them with *Record result* and *Ask agent to propose a test*) and the Tests section. A result is recorded in one side panel owned by the controller. The Evidence section lists only records that answer no test (`GET /evidence?unlinked=true`, "Other evidence"); a test's records are under the test.

BlueStick cannot enforce the command rules (commands run on the operator's machine; the client's sandbox is what holds), which is what `GET /.well-known/networkmapper.json` publishes: `command_approval: "operator_driven"`, `command_approval_enforced_by: "agent_and_client_sandbox"`, beside the properties the server DOES enforce (it executes nothing; a key's authority is its operator's project role; keys are session-bound, time-limited and audited). The one read-back is the session prompt's (`agent_policy.render_read_back`): project, operator, scope and working directory. The per-run read-back, the sanity-check gate (v2.22.0–v2.432.x) and the optional target checks that followed it are all gone with execution runs.

**`POST /agent/feedback`** takes friction notes, API critiques and tool suggestions at the moment they happen; `source` is `assist`, `reconnaissance` or `testing`. Rows land in `agent_feedback` with `status='new'`; a human triages via the admin-only `/api/v1/feedback/...` surface.

The endpoint shapes and the working rules are in the agent guide, **`documentation/AGENT_GUIDE.md`**, which agents fetch via `GET /api/v1/agents-guide` (optionally `?workflow=testing|reconnaissance|assist`). The guide uses HTML-comment section markers (`<!-- agents:section tags="..." -->`) so a single source file can emit workflow-scoped slices.

**BlueStick does not direct the agent's work (v2.434.0).** The per-session environment probe (`POST /agent/session/environment`, MCP `record_environment` and its columns), the server's recon planning (`recon_planning_service`: a recommended scan sequence, a tool catalogue with commands, swap rules), the Tool Reference "Host readiness" check (`GET /references/tool-readiness`) and the preflight script (`scripts/preflight.sh`, `GET /references/preflight-script`) were removed. Which tools the operator has and which command fits their machine is the agent's and operator's business. What stays is the tool registry (a catalogue: `reference` / `suggested` / `rejected`, `ingestible`) and `tool_output_contract` (the upload extensions each parseable tool's output must carry, which the Tool Reference commands and the upload-format docs are checked against).

**Attribution (v2.434.0).** An `AgentSession` records who did the work from three sources (`agent_session_service`): the **client** (`generated_by_tool`) from the MCP `initialize` handshake's `clientInfo` name + version when the handshake carries a live key (`record_mcp_client`, called by `mcp_assist.py`), else from the first authenticated call's `User-Agent` (`deps.py`, written once; the handshake overwrites it); the **prompt version**, set by the server at session start and resume; and the **model** (`generated_by_model`), the agent's optional self-report `agent_model` on `POST /agent/host-tests`, `POST /agent/evidence`, the proposal routes and `POST /agent/session/end` (`note_agent_model` — the session keeps the last one). A host test and an evidence record snapshot the attribution when they are written.

### 5.3 Agent API call audit log (v2.24.0)

A Starlette middleware (`app/services/agent_api_log_service.py`) wraps every `/api/v1/agent/*` request. After the response is sent (so the agent's loop is never blocked), it writes one row to `agent_api_calls` capturing: method, full path + path template, path params, query params, status code, response size, duration, source IP, user agent, API key prefix (never the raw key). For mutations, a JSON-safe body summary is stored (8 KiB cap, multipart skipped, sensitive-shaped fields stripped as defence-in-depth).

The middleware extracts referenced `host_ids` / `entry_ids` / `target_ips` from path + query + body so "did the agent query the right hosts?" is a one-indexed-query answer. Surfaced via `GET /api/v1/projects/{id}/assist-sessions/{assist_session_id}/api-activity` (JWT-authenticated — the agent cannot read its own audit log) and the `AgentActivityLog` component on each agent session's page (`/agent-sessions/{id}`).

Retention: the API runs a daily loop (`startup.agent_api_call_retention_loop`) that purges `agent_api_calls` rows older than `AGENT_API_CALL_RETENTION_DAYS` (default 90; ≤0 disables) and, only when `AUDIT_LOG_RETENTION_DAYS` is set (default 0 = keep forever), old `audit_logs` rows.

### 5.4 Offline bundle — removed

The exported-mode bundle (`export-bundle` / `import-results`) was removed with test plans in v2.442.0. There is no offline execution path.

### 5.5 Assist (Workflow E — query the project)

For the senior-tester case where the operator just wants to *query* a project — "which hosts expose FTP?", "summarize my critical findings", "what did the last recon turn up?" (v2.64.0). Assist is the DEFAULT surface of every session.

1. **`POST /projects/{id}/assist/start`** (JWT user, `assist.py`) — the one session start since v2.433.0 — mints the project session and returns the key plus the agent prompt. It requires only `auditor` (lowered from `analyst` in v2.308.0 — safe because a key carries its operator's permissions). Sessions are listed and ended from the project's agent-sessions surface.
2. The agent reads through `/agent/assist/*` (`agent_assist.py`): context, hosts (list / count / detail / vulnerabilities / notes / web interfaces), findings, posture, patterns, segments, coverage, vocabulary, names, scans, ingestion issues, plus the NDJSON downloads (`hosts.ndjson`, `report-context.ndjson`); and, since v2.428.0, what the other pages show — the Operations workbench, Worth a look and terrain, Evidence gaps and scan compare (`agent_assist_operations.py`), scanner observations by issue and client reports (`agent_assist_reporting.py`). **Each agent read wraps the service its page uses** (`workbench_service`, `scan_diff_service`, `host_detail_service`, `scanner_observation_service`, `client_report_service`…), so an agent and a page cannot disagree on a number; read roles equal the page's (`deps.AGENT_READ_ROLE_OVERRIDES`). The same host query DSL as the Hosts page drives `q=`.
3. **Reads need project membership; bulk exports need `auditor`; writes are whatever the operator's role allows.** There is no "assist-scoped" key and no capability grant (both gone — v2.337.0 / v2.309.0): an analyst's assist session can write notes, review status and hostname/OS corrections; an auditor's or viewer's cannot, because its operator cannot. Nothing narrows writes to "assigned" hosts. An agent cannot triage (promote or dismiss) a finding or change its report text under any role — it PROPOSES (see §5.7).
4. **Managed and reviewable from one page per session** (v2.432.0 / 5.312.0). Agent Sessions (`/agent-activity`) leads with the live sessions — each with its key state, last call, its work (`host_test_count` / `evidence_count` on the row, two grouped queries in `agent_session_service._attach_work_counts`) and Resume / End — then the history. `/agent-sessions/{id}` (keyed by the SESSION id) adds the tests the session proposed (`GET /host-tests?agent_session_id=`), the notes the agent wrote and the per-session API-call feed, both keyed by the session's `assist_sessions` detail row, whose id the session row names (`assist_session_id`); `/assist-sessions/{id}` redirects through it. End / Resume rights come from the row (`can_end` / `can_resume`, the rules the routes enforce), never from the global role. A session whose key has expired reports as `ended` immediately — derived on read, with an hourly sweep converging the stored column (`assist_session_service`).

### 5.6 MCP transport (Workflow F — all of the above, as tools)

Every workflow above is also reachable over the **Model Context Protocol** at `POST /api/v1/mcp` (`mcp_assist.py` for the transport, `mcp_tools.py` for the declarative registry). A `tools/call` loops back into the same `/agent/*` endpoint **in-process** via an ASGI transport, forwarding the caller's `X-API-Key`, so auth, the operator-role check, row scope, and the audit log run unchanged — the MCP layer makes no authorization decision of its own.

`tools/list` returns the WHOLE catalogue to every session (56 tools at 2.442.0; the per-workflow filter went with the per-workflow keys in v2.337.0). Listing was always presentation rather than authorisation — the endpoint behind a tool decides on every call. Bulk, file-shaped endpoints (NDJSON streams, target lists, `POST /agent/uploads`) are deliberately *not* tools.

See [MCP.md](MCP.md) for the transport details, the certificate (local root CA + fingerprint check), the tool catalogue, and the guardrail model.

### 5.7 Evidence records and proposals (v2.436.0)

The rule: an agent's change is a **proposal** when it alters what the team has concluded or what the client report says; everything else it writes is direct and attributed to its session.

- **Evidence records** (`evidence_records`, `agent_evidence_service`; `POST /agent/evidence`) are direct and immutable: host, optional finding / endpoint, tool, command, outcome, summary, session + model + client. Raw output is kept in the row (`raw_output`: Text, deferred so a list never loads it, 5 MB cap, NUL removed) with a 2,000-character preview. Until 2.439.0 it was a file under `uploads/evidence/<project>/`, which a host or project deletion left behind. The migration `e3a7c1d9f5b2` read those files into the rows and left them on disk to be removed by hand. A record may name the host test it answers (`host_test_id`, with a `request_key` that makes a retry return the stored record), or stand alone — this is how ad-hoc agent work leaves an audit trail. Records with outcome `finding`, `no_finding` or `inconclusive` are what make a host *tested* (§5.1).
- **Proposals** (`agent_proposals`, `proposal_service`; `POST /agent/proposals/*`) have five kinds: `finding_text` (one row per report field), `finding_create`, `observation_promote`, `observation_dismiss`, `endpoint_status`. Each keeps its payload, rationale, cited evidence ids and attribution (`source` agent | llm_draft, session, model, client, prompt version).
- **Accepting runs the same code as the click** (`finding_actions.py`: `apply_report_text`, `promote_or_dismiss_vulnerability`; `FindingService.create_finding` / `set_endpoint_status`) as the person accepting, inside a savepoint. So the authored-content rule decides who may accept report text, and a refusal leaves the proposal pending with the reason in `error` (not for a 403, which is about the caller). Accepting a field's text supersedes that field's other pending proposals; several stand side by side until then, so output from different models can be compared. A bulk accept never chooses between them (`competing_drafts`). Deciding locks the proposal row (`get_proposal(for_update=True)`, `populate_existing`), so two concurrent accepts apply it once. An accepted `cvss_vector` clears the score: a 3.x vector recomputes it, a 4.0 one leaves it for the analyst, because the old score belonged to the old vector.
- **"Needs review"** is one definition (`proposal_service._about_findings` / `pending_per_finding`): the proposals naming a finding, plus promote/dismiss proposals on a scanner observation that evidences it (`finding_vulnerabilities`). It is derived, never stamped, because the link can change. The finding's Proposals section and the report's pending warning both read it.
- Reviewer routes are project-scoped (`proposals.py`: list, summary, accept, reject, bulk; evidence reads); agent routes are in `agent_proposals.py`.
- **The in-app drafter is the same path** (v2.437.0): "Draft empty sections" (`POST /reports/draft/finding-text`) creates `finding_text` proposals with `source: llm_draft` and the provider's model — one review path, one UI.
- **Provenance** (`proposal_service._provenance`, 2.439.1): an accepted change names the proposal it came from on the history row the change itself writes. That is the creation row (`create_finding(summary=)`), the endpoint row (`set_endpoint_status(note=)`) or the promote/dismiss summary, and for report text (which has no history otherwise) one same-status row: "Impact set from agent proposal #N (model, agent session #S)", or "from AI draft #N". "My activity" shows a same-status history row's summary instead of "Marked … <status>".
- **Notifications** (`proposal_service._notify_finding_people`): the finding's author and owner, one unread notification per person per agent session, updated in place, and computed once per request rather than per proposal. **What a person is shown matches what they were told** (2.440.0): `proposal_service.findings_of` (authored or owned) through `_about_findings` scopes the list (`mine`), the summary (`pending_mine`), the notification's link (`scope=mine`) and the top-bar count. A project admin sees everyone's by default.
- **Report text is a rewrite** (2.440.0): accepting replaces the section with the proposed value, so the agent is told (MCP `propose_finding_text`, the guide, the "Work on this with your agent" task) to send the complete new section, report-ready, with the critique in `rationale`. **Report issue** warns (never blocks) on pending proposals for reported findings and on images from agent-written notes (`client_report_service` summary).
- **UI** (v5.316.0): the Proposals page (`/proposals`, Workflows hub; pending by kind, filters, bulk accept/reject); the finding page's Proposals section (report text grouped by field, so drafts from different models sit side by side; accept, accept and edit, reject with an optional reason — `decision_note`, which the proposing agent reads back through `list_proposals`); agent evidence on the host inspector; a pending count in the top bar (hidden at zero); "Work on this with your agent" in the finding's Report text section (5.317.0), which hands the operator's agent session a review task (`agentInstruction.reviewFinding`) whose output comes back as proposals. All share `components/proposals/ProposalItem.tsx`.

---

## 6. Security model

Security work landed across v2.9.5, v2.9.7, and v2.9.8. The below is the current state; see CHANGELOG for the audit trail.

- **JWT signing + Fernet credential encryption** use **separate keys** by design. `settings.SECRET_KEY` signs JWTs; `settings.CREDENTIAL_ENCRYPTION_KEY` derives a Fernet key via HKDF for LLM provider + integration credential encryption at rest. A compatibility fallback to `SECRET_KEY` exists when `CREDENTIAL_ENCRYPTION_KEY` is unset (logged once as a deprecation warning); the fallback will be hard-removed in a future major.
- **SSRF protection** is two-layered. `require_public_http_url()` validates user-supplied `base_url` values at save time — parses the URL, enforces `http`/`https` scheme, resolves the hostname via `getaddrinfo`, and rejects every resolved IP in RFC1918, CGNAT, loopback, link-local (including `169.254.169.254`), and IPv6 equivalents. On top of that, `safe_http_client(allow_private=...)` returns an `httpx.Client` with a custom `HTTPTransport` that **re-resolves and re-validates** every outbound hostname at connect time, closing the DNS-rebinding TOCTOU window the plain validator leaves open. Redirects are disabled so a 302 can't land on a private IP. Ollama is the sole integration type with `allow_private=True` because users legitimately run it on localhost.
- **XXE protection** — three coverage strategies depending on parser:
  - `nessus_parser.py` uses `defusedxml.ElementTree` for parser entry points (`parse`, `iterparse`, `fromstring`) which disables entity expansion, external DTD fetching, and entity references at parse time. Type annotations still reference `xml.etree.ElementTree.Element` because defusedxml only wraps the parser — not the element tree.
  - `nmap_parser.py`, `masscan_parser.py` and `openvas_parser.py` stream with lxml through `xml_stream_helpers.iterparse_safe`, which sets `resolve_entities=False, no_network=True, huge_tree=False` in one place (v2.41.0). lxml is kept because `defusedxml.lxml` is deprecated upstream as of 0.7.1; the explicit flags defeat billion-laughs entity expansion, SYSTEM-entity local-file disclosure, and the huge-tree memory exhaustion vector.
  - All other parsers ingest JSON / CSV / text and don't construct XML at all.
- **Decompression-bomb caps** — `eyewitness_parser.py` enforces per-file (50 MB), running-total (500 MB), and entry-count (5000) caps on uploaded EyeWitness ZIPs (v2.41.0). The streaming extractor counts bytes on the way out and aborts + unlinks the partial file if either cap is exceeded mid-stream, so a spoofed central-directory `uncompressed_size` field can't defeat the check.
- **Streaming JSON for large BloodHound exports** — `bloodhound_parser.py` switches from `json.load` to `ijson.items` for files ≥50 MB (v2.41.0). The structure is auto-detected by peeking the first 64 KB (top-level array vs `{data: [...]}` vs `{computers: [...]}`); files below the threshold keep the fast `json.load` path.
- **Login rate limiting** — `core/security.login_throttle_exceeded()` (v2.41.0) reads `audit_logs` for the trailing 15-minute window and rejects with HTTP 429 if ≥10 failed logins exist for the requested username (across all IPs) or ≥20 failed logins from the requesting IP (across all usernames). Defends against the distributed brute force that previously defeated the per-account 5-strike lockout. The per-account lockout in `authenticate_user()` still applies on top of this.
- **Username-enumeration timing** — `authenticate_user()` (v2.41.0) calls `pwd_context.verify(password, _DUMMY_PASSWORD_HASH)` in the unknown-user, inactive-user, and locked-account branches so all four paths pay the same bcrypt cost. The pre-fix timing channel (5 ms unknown vs 80 ms known) is closed.
- **Prompt sanitization** — `backend/app/services/prompt_sanitizer.py` strips `X-API-Key: nm_agent_...` lines, redacts credential bullets (Access key / Secret key / Password / Username / API key / PDCP token / Secret), and catches bare `nm_agent_` tokens of 20+ chars. The `/llm-providers/{id}/complete` endpoint calls it before forwarding. (Its client-side twin `utils/promptSanitizer.ts` was deleted with the in-app agent panel in 5.313.0.) Changing bullet labels in `agent_prompt_service._integration_block` requires updating the sanitizer in lockstep.
- **File upload validation** — `ingestion_service` enforces a max chunk-by-chunk size during streaming, slugifies filenames, and runs a magic-byte check after write (`.xml`/`.nessus` must start with `<`, `.json` with `{` or `[`, `.gnmap` with `#` or `Host:`, text files must not contain NUL bytes).
- **Pydantic `max_length` caps** on high-risk write schemas: AnnotationBase.body 16 KB, SubnetBase.description 1 KB, ScopeBase.name 256, and the host-test schemas (`host_test_schemas.py`: tool 100, description / command / rationale / expected result / tester summary 10,000 each, dismissal reason 2,000, label 255, at most 20 HTTP(S) references of ≤2,048 characters, at most 200 tests per batch; unknown fields and NUL are refused).
- **CORS** — origins restricted to configured values (`settings.CORS_ORIGINS`); never `*`. Deploy script regenerates `.env` with the detected host IP.
- **HTTPS** — production deployments terminate TLS at the Nginx frontend container using self-signed certs by default (`scripts/deploy.sh` generates them during first-time setup). Security headers are set in `ssl-nginx.conf`.

---

## 7. Frontend architecture (React + Vite + Tailwind v4 + Radix)

The frontend is a Vite-built React SPA. Material UI + Emotion were fully removed in the v4 frontend line; the substrate is now **Tailwind v4** for styling, **Radix** primitives wrapped shadcn-style under `components/ui/`, **Sonner** for toasts, and **lucide-react** for icons. Navigation is organised into NINE hubs — Operations (the landing page), Inventory, Findings, Posture, Workflows, Collaboration, plus the utility hubs Settings, Administration (global admins) and Reference — declared as data in a single manifest (`config/navigation.tsx`, `HUB_DEFS`) and rendered by `components/Layout.tsx` + `components/HubRedirect.tsx`. There are no per-hub page components.

```
frontend/src/
├── pages/                   # route-level views — `ls frontend/src/pages` is the source; by hub:
│   │ Operations · PortfolioDashboard · Oversight (global admins)
│   │ Inventory:  Hosts, HostDetail, Names, Scans, ScanDetail, ScanDiff,
│   │             ParseErrors (the Ingestion Results page), Scopes
│   │ Findings:   Findings (+ the Scanner observations view), FindingDetail, Reports, ReportDetail
│   │ Posture:    SecurityPosture, Segments, Patterns, Evidence
│   │ Workflows:  ProjectActivity (Agent Sessions), AgentSessionDetail (AssistSessions
│   │             redirects to it), Proposals, ToolActivity, Feedback (admins)
│   │             (no Test Plans or Executions pages since 5.320.0: tests are on the host page;
│   │             /test-plans/* and /executions/* redirect)
│   │ Collaboration: Activity
│   │ Settings:   ProjectSettings, IntegrationSettings
│   │ Administration: AllProjects, SystemSettings
│   │ Reference:  Reference, ToolReference, ToolCoverage ("What BlueStick reads"),
│   │             McpReference, SbomReference, DefaultCredentials, userguide/
│   │ Personal:   Profile, LLMSettings
│   │ Gates:      Login, ForceChangePassword, ForceTwoFactorSetup · NotFound
│   └── userguide/           # UserGuideShell + GettingStarted / Triage / Data / Admin / Agents guides
├── components/
│   ├── ui/                  # ~30 primitives — mostly Radix-wrapped shadcn-style
│   │                        # (dialog, select, tabs, tooltip, …) plus local ones
│   │                        # (SeverityBar, data-table, code-block, meta-field, info-tip)
│   ├── host-inspector/      # the inspector's sections (InspectorSection, ServiceEvidencePanel,
│   │                        # PortDetailsCard, VulnerabilityGroup, HostConflictsPanel, NoteComposer,
│   │                        # HostTestsSection — the host's tests — and HostEvidenceSection, …)
│   ├── scans/               # UploadReviewDialog (staged upload), FormatRetryDialog, ImportResult,
│   │                        # UninterpretedLines, ScanBatchList
│   ├── charts/              # PlotFigure — the Observable Plot wrapper
│   ├── operations/          # AddressTerrainSection + TerrainScene (three.js, lazy)
│   ├── findings/            # ScannerObservations
│   ├── reports/             # TemplateImages, EngagementSettingsFields
│   ├── posture/, oversight/, hosts/, mcp/, proposals/, agent-sessions/
│   └── (shared widgets)     # Layout, HubRedirect, UserMenu (About → versions), CommandPalette,
│                            # HostInspector, HostFilters, HostCommandBar, ToolReadyOutput, …
├── contexts/
│   ├── AuthContext.tsx      # JWT token + user profile + must_change / 2FA gates
│   ├── ProjectContext.tsx   # active project, member list, switcher
│   ├── ThemeContext.tsx     # theme selection, persisted to localStorage
│   │                        # (the five palettes live in theme/palettes.ts)
│   └── ToastContext.tsx     # success/warning/error toasts (Sonner-backed)
├── hooks/                   # 15 — notably:
│   ├── useUploadReview.ts   # the staged-upload state machine (API injected, so it tests with renderHook)
│   ├── useVisibilityPoll.ts # THE way to poll: no ticks in a hidden tab, no overlap, back-off
│   ├── useLatestRequest.ts, useDebouncedValue.ts, useKeyboardShortcuts.ts
│   └── useConfirm.tsx       # destructive-action confirmation dialog
├── services/
│   └── api/                 # axios client split into per-domain modules
│                            # (hosts, scans, scopes, dashboard, … + shared primitives)
├── config/
│   └── navigation.tsx       # single navigation manifest (hubs, roles, palette)
├── theme/                   # palettes.ts (the five palettes), tokens.ts, cssVars.ts
├── data/                    # uploadFormats.ts — test-pinned to documentation/UPLOAD_FORMATS.md
├── utils/                   # pure helpers (no HTTP client) — `ls` is the source; notably:
│   ├── uiStyles.ts          # safeFallback + stickyBelowChrome (truncation is Tailwind + cn())
│   ├── cn.ts                # class merge, with the named spacing scale registered
│   ├── severity.ts, findingStatus.ts # severity tokens; the finding vocabulary
│   ├── apiErrors.ts         # formatApiError (handles Pydantic 422 arrays)
│   └── statusMeta.ts        # chip colors + label formatting
├── tests/                   # Vitest suites
├── index.tsx                # BrowserRouter + providers
└── App.tsx                  # routes (React.lazy pages) + ProtectedRoute role gates
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

- **Structured logs** — backend + workers log to stdout via `logging.basicConfig`. `scripts/collect-logs.sh` produces an anonymised bundle, safe to share, covering the backend, worker, report-worker, nginx and db logs, the ingestion queue, and a per-format parser audit. The parser audit holds field-coverage counts only. `scripts/scrub_logs.py` rewrites every file:
  - Values harvested from the database and `.env` become stable pseudonyms.
  - So does anything shaped like an address, name, URL, account or secret.
  - SQL error row data is removed.
  - If scrubbing fails, no bundle is written.
- **Audit trail** — `audit_logs` captures login/logout, password change, role change, session revoke, upload, scan delete, and other security-relevant events with user_id + IP + user agent.
- **Health checks** — `/health` (backend; nginx proxies it and `/ready` with 5 s timeouts and serves `/live` itself), a Docker HEALTHCHECK on `https://localhost/` (frontend), `pg_isready` (db), heartbeat-freshness checks (both workers — each rewrites its own heartbeat file every loop, so a wedged worker goes stale → unhealthy).
- **Async report pipeline** — heavy report formats (JSON, agent package, markdown bundle, client-report HTML/DOCX renders) are generated off the request path. The API enqueues a `report_jobs` row (`ReportJobService`), the **report-worker** claims it via `SELECT … FOR UPDATE SKIP LOCKED`, builds the artifact with `report_generator.py`, and writes it to the shared `uploads/report_artifacts` volume; the UI polls job status and downloads the finished file. Mirrors the ingestion pipeline (same `worker_loop.py`, same orphan-reaping semantics). See §1 for the container.
- **Client reports (Quarto, v2.379.0–2.381.0)** — the findings-first client deliverable, on its own page (`/reports`) with history and addenda. `endpoints/client_reports.py` → `client_report_service.py` decides what a report contains (confirmed / accepted-risk / remediated findings; false-positive endpoints dropped; open/retest counted, not shown), freezes it on ISSUE (project admin; numbered, `reports.snapshot` holds the dataset and the cumulative reported state) and computes an addendum's delta against the baseline's frozen state by finding AND endpoint (new findings, further systems, withdrawals — never remediation). Rendering runs on the report worker (`client_report_render.py` → `quarto_render.py`): the template in the root `report-templates/<name>/` is filled in Jinja's sandbox with every value Markdown-escaped, written Markdown is inserted by `quarto_fields.lua` from `data.json` after Quarto's filters, and Quarto (only in the `report-worker` image target) produces HTML and DOCX (plus the Quarto source as a zip) in a throwaway directory with a clean environment. There is no PDF format (removed v2.407.0): the Word report carries the design (`reference.docx`: cover page, header/footer, page numbers) and is exported to PDF from Word; a PDF stored with a report issued earlier still downloads. `quarto_fields.lua` reads each field with a trailing newline — without one, `pandoc.read` drops a table's last row out of the table. The pentest template's Word post-processor (`scripts/fix-docx-report.py`) frames screenshots and puts the installed `logo` and `cover` (title page image) assets in place of the placeholder drawings named `bluestick-logo` / `bluestick-cover` in the shipped `reference.docx`, scaled to fit their boxes. Draft previews are ordinary expiring report jobs; an issued report's files are kept in `uploads/client_reports` with their SHA-256. A finding's report text (`findings.description` … `cvss_score`) is authored content, seeded once from the scanner row or source note; report images are opt-in (`note_attachments.include_in_report`). A template's OWN images (logo, cover art) are declared in its `template.json` under `assets` (`id`, `path` relative to the template folder, `label`, `description`, `note`, `required`, `formats`); `quarto_render.template_assets` validates each path (relative, inside the folder, image extension, a narrow charset because it is printed unescaped) and reports `present` (a regular file, no symlink — `_copy_template` skips symlinks). `GET /client-reports/templates` carries them; since v2.431.0 a global administrator may also UPLOAD a PNG / JPEG / `.docx` asset from the Reports page (`template_asset_store.py`: stored in `uploads/template_assets/<template>/`, never in the read-only template folder; the bytes validated against the asset's kind and the template's `max_bytes` / `min_width` / `min_height` / `aspect` guidance; audited). The render places an upload over the template's own file in its private copy (`quarto_render._place_overrides`, before `_apply_replacements`), and `report_template_service.fingerprint` includes uploads, so an issued report refuses to re-render with other branding (with no upload the fingerprint is unchanged). The Reports page's "Templates" section lists every installed template with its files, each with its guidance and a thumbnail (server paths, install steps and upload for global admins only), while a draft chooses its template beside the Preview buttons (saved at once) and shows its files as one line unless a required one is missing (5.286.0). A missing REQUIRED image makes preview/issue/render refuse with 409 naming `report-templates/<name>/<path>`, and the template reads one with `asset("id")` — the path when installed, else `''`, so an absent optional logo leaves the layout unchanged. Three templates ship (v2.409.0): `pentest` (the default), `executive-brief` (leadership; Word + HTML) and `remediation-worklist` (host-by-host, the minimal commented starter); `report-templates/README.md` is the authoring guide, and a folder that cannot be offered is listed with its reason by `GET /client-reports/templates/problems` (global admins, on the Reports page) instead of vanishing.
  - **Scope cutoff (v2.441.0, `report_scope.py`).** A project with thousands of scoped networks printed a table that ran for pages. Each template's `template.json` sets `scope_inline_max` / `scope_domains_inline_max` (default 25; `null` for a template that prints no scope). `ClientReportService._scope_block` adds to the dataset's `scope`: totals (networks, IPv4 addresses, IPv6 networks, domains, sites), a per-site summary (15 rows plus "Other sites"), whether each list is printed, and, when either is over, the name and SHA-256 of the separate scope CSV. Templates print the totals and name that file instead of the list. The CSV (`report_scope.scope_csv`: deterministic, UTF-8 with BOM, formula-looking cells prefixed with an apostrophe) is served by `GET /client-reports/{id}/scope.csv` and the agent's `/assist/client-reports/{id}/scope.csv` from `ClientReportService.content()`: a draft's live scope, an issued report's snapshot. So an issued report's file and fingerprint never change. The summary's `scope_external` puts a "Scope file — send it with the report" section on the report page and a line in the issue dialog, a warning and never a block.
- **Queue metrics** — admin-only `GET /api/v1/system/queue-metrics` (`system_metrics.py` → `queue_metrics_service.py`) reports depth/in-flight/failure counts for **both** durable job queues (`ingestion_jobs` + `report_jobs`) so operators can see backlog and stuck work at a glance.
- **Parse errors** — `parse_errors` rows surface on the **Ingestion Results** page (Inventory hub, beside Scans; `/parse-errors` redirects there) with `user_message` strings tuned for operators, the format chain and the uninterpreted lines. Linked to `ingestion_jobs` via FK so the UI can show "this scan failed — see error #42".
- **Dead-letter columns** — `ingestion_jobs.retry_count` + `ingestion_jobs.last_error` surface in the `IngestionJobSchema` so the UI can highlight jobs that crashed repeatedly. `ingestion_jobs.skipped_count` + `parser_warnings` (v2.22.0) carry per-job parser quality stats (how many records were dropped, what malformed), persisted from `parser.last_parse_stats` after each successful parse.
- **Orphan reaping** — covered in §4; this is the recovery path when a worker segfaults or the container is killed mid-parse.
- **Version visibility** — `GET /` returns `{message, version, frontend_version, instance_id, cors_origins}` (the backend version is the `version` key), the user menu's **About BlueStick** item shows both (the bottom-right VersionFooter was retired by UX audit #12 and its component deleted in 5.247.0), and startup logs print them on every boot.

---

## 9. Extending the system

**Adding a new parser.**
1. Drop a file in `backend/app/parsers/{tool}_parser.py`. Inherit structure from an existing parser; use `parser_utils` for shared concerns (`ensure_scan`, `extract_first_ip`, `record_savepoint` around any record whose failure you catch and skip, `read_tool_text` for text that reaches a Text column). Accept `source_tool=` in `parse_file(**kwargs)`.
2. Add its `looks_like_*` sniffer to `content_detection.py`, its attempt to `ingestion_service._build_parsing_attempts` under the file type it serves (a try-anyway attempt wrapped in `_fallback`), its class to `build_parser_dispatch_map`, and its `file_type` to `format_registry.FORMATS`. Four tests pin this chain — `test_parser_dispatch_contract.py`, `test_ingestion_format_chain.py`, the content-detection module-surface test in `test_phase1_regressions.py`, and `test_parser_coverage.py`.
3. Describe what it reads in `app/data/parser_coverage.json` (the "What BlueStick reads" page) in the same commit; if the extension is new, update `ALLOWED_UPLOAD_EXTENSIONS`, `frontend/src/data/uploadFormats.ts` and `documentation/UPLOAD_FORMATS.md` together (test-pinned).
4. Add fixtures to `artifacts/` (or `backend/tests/fixtures/`) and a test in `backend/tests/` that runs the parser against the fixture and checks the canonical host/port shape.
5. If the parser touches XML: use `defusedxml` (as `nessus_parser.py`) or `xml_stream_helpers.iterparse_safe` (as the nmap, masscan and openvas parsers). Both defeat XXE; never reach for bare `xml.etree` or `lxml.etree`.

`documentation/PARSERS.md` has the full walkthrough.

**Adding a new agent workflow.**
1. Keys are not scoped any more (§2.2): every key belongs to one project session and carries its operator's project role. Decide instead which ROLE a call needs (reads: membership; bulk exports: `auditor`; writes: `analyst`) and what record the work leaves (a host test, an evidence record, a proposal, a note, an upload — each carries the session's id).
2. Add the endpoint to the right module under `backend/app/api/v1/endpoints/` (`agent_browse.py`, `host_tests.py`, `agent_proposals.py`, `agent_recon.py`, `agent_assist.py`, or a new one mounted under `/agent` in `api.py`). `enforce_agent_operator_access` is mounted on every agent router and applies the role floor; object-level rules (an upload belongs to its session; a host test or evidence record belongs to the key's project) stay in the handler or its service. A write that changes what the team concluded is a proposal (§5.7), not a direct route. If it should be an MCP tool, add it to `mcp_tools.py` — the MCP layer makes no authorization decision; `test_mcp_enum_contract.py` and `test_agent_capabilities.py` (the exact list of gated agent write routes) must be updated with it.
3. Tag new agent-guide sections with the workflow name so they appear in the sliced response; update the prompt builder in `agent_prompt_service.py` if needed **and bump the prompt version** by prepending an entry to `PROMPT_VERSION_HISTORY` in `agent_prompt_history.py` (`PROMPT_VERSION` is derived from it), so agents on the old prompt can detect they're stale.
4. Add contract tests to `backend/tests/` that exercise the new endpoint with a session key (`test_host_tests.py` shows the pattern). The agent API call log middleware will capture the new endpoint automatically — extend the `_collect_referenced_ids` helper if the call carries host/entry references the parser doesn't already pick up.

**Adding a new UI feature.**
1. Route pages belong in `frontend/src/pages/`; shared components in `frontend/src/components/`.
2. Reuse the per-domain typed clients in `services/api/` (import them through the `services/api` barrel, which tests mock) and the shared `uiStyles.ts` helpers. `uiStyles.ts` exports only `safeFallback` and `stickyBelowChrome`; truncation is Tailwind (`truncate`, `line-clamp-*`, `break-words`) composed with `cn()` (`utils/cn.ts`). There is no `sx` prop anywhere — that was MUI.
3. Test worst-case data (200-char hostname, long filename, null values, empty arrays) at desktop widths, including a narrowed window. Desktop-only product — no mobile layouts (UI_STYLE_GUIDE.md §3).
4. Before shipping: type check clean under strict mode, handle Pydantic 422 shapes in error paths.

**Adding a new service layer module.**
- Single responsibility per file. If a service commits internally, document the policy in the module docstring (see `integration_service.py`, `llm_provider_service.py`). If it defers commit to the caller, say so (see `host_test_service.py`, `agent_evidence_service.py`). Commit boundary ambiguity is an audit finding waiting to happen.

---

## 10. Test & quality gates

- **Backend** — `pytest` with a 68% coverage floor in `backend/pytest.ini` (the floor is a ratchet — raise it as coverage climbs). The suite has **~2,100 test functions** (more once parametrized) in ~245 modules (v2.427) across service, parser, and contract layers under `backend/tests/`. `conftest.py` uses the SQLAlchemy join-to-outer-transaction + nested savepoint pattern so services that commit internally (integration, LLM provider, agent API log middleware) don't break test isolation. **Postgres is the preferred test backend** — the harness auto-creates a `<app-db>_test` database on the app's own Postgres server when reachable, falling back to in-memory SQLite when not. The Postgres path lets the Postgres-only code (`pg_advisory_lock`, masscan batch-upserts, the raw `pg_catalog` SQL in `delete_scan`) actually run.
- **Frontend** — Vitest + Testing Library. Coverage spans page-level views (Hosts, Operations, ProjectActivity, AgentSessionDetail, the scan compare view), shared components (HostFilters, HostCommandBar, HostInspector, ProposeTestsDialog), and pure utilities (host query-DSL translation, tool-ready output, navigation manifest, version consistency).
- **No hosted CI** — the GitHub Actions workflow was removed in 2026-09 (it had never run on this repository). The same three gates are run locally before a push: `scripts/test-alembic-roundtrip.sh` walks every migration down and back up against a throwaway Postgres; the backend suite plus `alembic check` (model/migration drift) run in a one-off container (recipe in CONTRIBUTING.md; drop `--no-cov` to check the 68% floor from `backend/pytest.ini`); the frontend runs `tsc --noEmit` → `vitest run` → `npm run build`.
- **Type safety** — frontend runs TypeScript strict mode with `noUnusedLocals` / `noUnusedParameters`, so an unused import fails `npm run build` (and the image build); every PR should typecheck clean before merge. Backend uses gradual typing via type hints but does not enforce mypy in CI.

---

## 11. Deployment

Single entry point: `./scripts/deploy.sh`. Options:

1. **Start / rebuild** — builds and starts containers using the current `.env`.
2. **First-time setup** — auto-detects host IP, generates `.env` from `.env.example`, generates SSL certs, starts all five containers. Creates a default `admin` user on first boot. The password is taken from `DEFAULT_ADMIN_PASSWORD` when that is set to something other than the literal `admin`; otherwise a cryptographically random URL-safe password is generated (v2.90.3), never logged (the boot log names the file), and written mode 0600 to `/app/uploads/initial-admin-password.txt`. `must_change_password=True` is set on the row so first login forces a rotation regardless of source.
3. **Reconfigure IP** — regenerates `.env` + SSL certs for a new host IP.
4. **Nuclear clean** — removes all Docker data (containers, volumes, network) and rebuilds from scratch. Destructive.
5. **Security status** — reports SSL state and whether the database port is exposed.
6. **Back up `.env` + SSL** — copies them to the parent folder.
7. **Roll back** — returns to the previous build.

Auxiliary scripts (`scripts/README.md` has the full list):

- `scripts/collect-logs.sh` — anonymised diagnostics bundle (with `scripts/scrub_logs.py`).
- `scripts/backup-db.sh` / `restore-db.sh` — database (+ `uploads/`) backup and restore.
- `scripts/upgrade-instance.sh` — carry a running instance's local state into a freshly copied source tree, then deploy.
- `scripts/status.sh` — quick container status check.
- `scripts/transfer-images.sh` — export/import container images for offline or air-gapped moves.
- `scripts/generate-ssl-cert.sh` / `generate-ssl-cert-simple.sh` — SSL certificate helpers (also invoked by `deploy.sh` during first-time setup).

**Scaling.** The stateless backend and frontend containers can run multiple replicas; the ingestion and report workers are each currently a single long-lived process and should not be replicated as-is (the `FOR UPDATE SKIP LOCKED` claim is safe for multiple workers, but the orphan-reaper logic assumes one reaper per queue). Postgres needs external strategies (managed service, read replicas) for anything beyond single-host deployments. There is no general job scheduler: beyond the two workers' periodic callbacks (the ingestion worker's reaper, backlog check and webhook sweep), the API runs two background loops — expired-session cleanup and agent-API-call / audit-log retention (`startup.py`); re-correlation and vuln refresh remain on-demand.

---

When a domain, endpoint, or workflow changes, update the relevant section here and bump the version stamp at the top so future maintainers have an accurate map.
