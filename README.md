# BlueStick

> **Verified against:** backend 2.370.1 / frontend 5.248.1 (2026-09-19). New here? See [CONTRIBUTING.md](CONTRIBUTING.md) for the conventions you'll need on day one.

BlueStick is a network visibility and review platform for aggregating host intelligence from one or more networks. It ingests output from security tooling, normalizes hosts and ports into a shared model, and presents dashboards for triage, reporting, and analyst follow-up. Analysts can flag hosts for review, attach notes, and revisit the same asset as fresh scan data arrives.

## Tech Stack

- Backend: Python 3.11, FastAPI, SQLAlchemy 2.0, Alembic migrations
- Database: PostgreSQL 16
- Frontend: React 18, Vite, TypeScript, **Radix UI primitives + Tailwind CSS 4** (shadcn-style; MUI-free since 4.0.0), TanStack Table, lucide-react icons, reactflow for the Topology map (no chart library — charts are hand-rolled SVG/CSS)
- Authentication: JWT for humans — a binary global role (admin / member) plus a per-project role (admin > analyst > auditor > viewer), TOTP 2FA enforced by default — and a per-session, project-scoped X-API-Key for agents
- Deployment: Docker and Docker Compose

## Core Workflows

- **Ingest** scan output from Nmap (XML + grepable), Masscan, Naabu, RustScan, Nessus, OpenVAS, NetExec, Eyewitness, httpx, WhatWeb, testssl.sh, Nikto, Amass/Subfinder, BloodHound/SharpHound, DirBuster/Gobuster/ffuf/Feroxbuster/Dirsearch, dnsx, RDAP, SMBMap, DNS CSV, and subnet CSV sources. Upload is staged: the format is detected, the operator reviews it (and can override it) and then imports — see [Upload Formats](documentation/UPLOAD_FORMATS.md) for the full table.
- **Deduplicate** hosts by IP so repeated scans update a shared asset record instead of creating parallel copies; track per-attribute confidence + conflicts across the scan history.
- **Triage** host discoveries, open ports, findings/vulnerabilities, web-interface inventory, and parse failures from the **Hosts** page (with a boolean query DSL) and the **Findings** spine.
- **Analyse posture** — the **Posture** hub answers a manager's questions across four tabs: the executive security condition, remediation trajectory, and highest-leverage action (**Posture**); segment comparison by site or subnet (**Segments**); recurring weaknesses grouped into program-level pattern families (**Patterns**); and whether the conclusions are trustworthy via per-domain assessment coverage (**Evidence**). A network **Topology** map and a cross-project **Portfolio** view round out the manager-facing surfaces.
- **Collaborate** — each authenticated user can mark a host with a personal follow status and add attributed review notes (threaded, @mentions) that surface in host and activity feeds.
- **Agent workflows** — an operator starts **one project-scoped agent session** (one time-limited, renewable X-API-Key) for an AI/terminal-side agent (Claude Code, Codex, etc.). The key may do exactly what its operator's project role allows — there are no per-workflow keys and no separate capability grants — and the session opens a *phase* for each kind of work:
  - **AI Assist** (the default, no phase) — answers ad-hoc questions over all project data using the same query DSL as the Hosts page (e.g. "show me the hosts I have in review"), and records notes and review status when the operator's role permits writes. An auditor's or viewer's agent is read-only because its operator is.
  - **Recon** — agent populates host data for a scope from scanner output
  - **Plan generation** — agent reads candidate hosts and drafts a structured test plan for human approval
  - **Execution** — agent works through a **human-approved** plan with per-host sanity-check gates; one environment probe per session makes commands match the operator's host
  - Every agent request is recorded in an audit log surfaced in the UI so users can review exactly what their agent did and which hosts it touched. The contract agents read at startup is [AGENTS.md](AGENTS.md).
- **MCP** — every agent workflow is also reachable over the [Model Context Protocol](documentation/MCP.md) at `/api/v1/mcp`, so an MCP-capable client (VS Code Copilot, Claude Code, Codex) calls them as native tools instead of shelling `curl`. Configure **one** server entry, `bluestick`; `tools/list` returns the whole catalogue to every session, and whether a call succeeds is decided per request by the operator's project role — the MCP layer makes no authorization decision of its own. The session dialogs emit ready-to-paste client config, and `scripts/trust-cert.sh` handles the self-signed certificate each client rejects by default.
- **Approve by exception, not by default** — an agent may run an approved tool against a host already in the inventory and write its output into the session's working directory without asking each time; anything outside those bounds stops for the operator. Which tools are approved is a table an admin vets ([Tool Reference](documentation/MCP.md#5-the-tool-registry)), and an agent that needs something else records the request rather than substituting. BlueStick cannot enforce this — commands run on the operator's machine; the client's sandbox is the real boundary, and the server adds the record.
- **Export** scoped data and operational reports for downstream analysis — CSV and HTML stream synchronously; JSON, agent-package and markdown-bundle archives run as **async report jobs** on a dedicated report-worker container. (PDF export was removed in v2.196.1; the interactive HTML report is the handover format.)

The app opens on the **Operations** hub; everything else hangs off **Inventory**, **Posture**, **Workflows**, **Collaboration** and **Settings**.

## Repository Layout

```text
backend/app/
  api/v1/endpoints/   FastAPI route modules
  main.py, startup.py App entrypoint; first-boot seeding (default admin, default project)
  worker.py, report_worker.py   The two worker container entrypoints
  api/deps.py         Auth dependencies (get_current_user, require_project_role, agent access)
  core/               Settings + password/JWT primitives
  db/                 SQLAlchemy models and session setup
  parsers/            Tool-specific parsers
  schemas/            Pydantic API schemas
  services/           Ingestion, export, follow-up, reporting, posture, and finding-correlation logic
  worker_loop.py      Shared durable-queue worker loop (ingestion + report workers)

frontend/src/
  components/         Shared UI components
  contexts/           Auth/theme context providers
  hooks/, utils/      Shared hooks; pure helpers (kept free of the HTTP client so they test without it)
  config/             navigation.tsx — the six-hub navigation manifest
  data/, theme/       Upload-format table (test-pinned to the docs); palettes and tokens
  pages/              Route-level screens
  services/           API client and typed contracts
  tests/              Vitest test suites

documentation/        Architecture, API, MCP, parsers, upload-format, UI-style and testing docs
scripts/              Deployment and operational helpers
artifacts/            Fixture data for parser and ingestion testing
```

## Quick Start

### Full stack with Docker

First-time setup is easiest via the deploy script, which generates `.env` (with a random `SECRET_KEY`) and self-signed SSL certs, then starts the stack:

```bash
./scripts/deploy.sh        # choose option 2, "First-time setup"
```

Or do it manually — every service reads `.env`, so it must exist first:

```bash
cp .env.example .env       # then set SECRET_KEY (and optionally DEFAULT_ADMIN_PASSWORD)
docker compose up --build -d
```

On first boot the backend seeds a default admin account. If `DEFAULT_ADMIN_PASSWORD` is unset, the generated password is written to **`./uploads/initial-admin-password.txt`** (mode 0600) — it is **not** printed to the logs. Read it there, then change it on first login.

> **2FA is enforced by default** (`REQUIRE_2FA=true`): the first login walks you through TOTP enrolment. Set `REQUIRE_2FA=false` in `.env` to make it opt-in (e.g. an air-gapped lab).

All traffic goes through the nginx/frontend container on `443`, which proxies the backend (the backend's own `:8000` is **not** published to the host by default — the mapping is commented out in `docker-compose.yml`):

- **App:** `https://localhost`
- **Swagger UI:** `https://localhost/docs` · **ReDoc:** `https://localhost/redoc` · **OpenAPI JSON:** `https://localhost/openapi.json` (all proxied by nginx; also linked from **Reference** in the app)

### Local backend development

```bash
cd backend
pip install -r requirements.txt
export DATABASE_URL=... SECRET_KEY=...       # both required
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

**Importing `app.main` runs `alembic upgrade head` against `DATABASE_URL`** — so
does any script or `python -c` probe that imports the app. Never point it at a
database you do not want migrated. (The test suite is exempt: its harness sets
`BLUESTICK_SKIP_DB_INIT=1`.) Without `SECRET_KEY` / `JWT_SECRET_KEY` the API
REFUSES TO START against Postgres; only a sqlite `DATABASE_URL` (dev/test) gets
an ephemeral signing secret, with a warning.

### Local frontend development

```bash
cd frontend
npm install
npm start            # dev server with hot reload (alias for `npm run dev` → vite)
npm run build        # typecheck + production build (tsc --noEmit && vite build); an unused import or parameter FAILS it
npm test -- --run    # Vitest suites
```

## Operations

```bash
./scripts/deploy.sh        # unified deploy menu (start/rebuild, first-time setup, reconfigure IP, nuclear clean, security status, back up .env + SSL, roll back to the previous build)
./scripts/status.sh        # quick container health check
./scripts/collect-logs.sh  # ANONYMISED diagnostics bundle (logs, ingestion queue, parser audit) — safe to share; needs python3; --since 72h, --terms FILE
./scripts/backup-db.sh     # database backup (pg_dump, or a raw volume snapshot if Postgres is down)
./scripts/restore-db.sh    # restore from a backup-db.sh artifact
./scripts/upgrade-instance.sh  # carry a running instance's local state into a freshly copied source tree, then deploy (for hosts that deploy by file copy; read its header for the expected folder layout)

# Seeds run INSIDE the backend container (scripts/ is bind-mounted at /app/scripts); deploy.sh runs none of them:
docker compose exec backend python scripts/seed_demo_data.py        # a realistic demo project (Posture, Segments/Patterns, Findings become evaluable)
docker compose exec backend python scripts/seed_named_assets.py     # then the named-asset scenario on it (--reset rebuilds)
docker compose exec backend python scripts/seed_eval_scenarios.py   # a small project of hand-placed scenarios, each with "open X, expect Y" (--wipe rebuilds)
```

## Schema & Migrations

- Tables are owned by Alembic — migrations live in `backend/alembic/versions/`. Every startup runs `alembic upgrade head` before serving traffic.
- Baseline revision: `b46cd59c17f5_baseline_schema.py`. Subsequent migrations layer on additive changes (plan-generation metadata, ingestion-quality columns, environment probe columns, agent API call log).
- `app/db/init.py` builds no schema itself — it takes an advisory lock and runs `alembic upgrade head` (skipped under `BLUESTICK_SKIP_DB_INIT=1`, test harness only). The old `create_all` + hand-rolled migration list is gone; the model is the schema, and Alembic enforces it.
- `scripts/test-alembic-roundtrip.sh` walks every revision down and back up, so a new migration needs a real `downgrade()`; `alembic check` catches model/migration drift. There is no hosted CI — run both locally before pushing a migration.

## Asynchronous ingestion

`POST /api/v1/projects/{id}/upload/` (analyst project role) stores the file and returns immediately. With `stage=true` — what the UI does — the job is registered as `staged`: `GET /upload/jobs/{id}/detection` reports the detected format and `POST /upload/jobs/{id}/start` queues it, optionally as a chosen format. An identical file already in the project is refused with 409 `duplicate_scan`. A separate worker container processes the queue and the UI polls `/upload/jobs/{job_id}` for completion. Parser failures are recorded with structured user-facing messages on the Parse Errors page.

## Agent audit trail

Whatever an agent session is doing (assist, recon, plan generation or execution), BlueStick records every inbound `/api/v1/agent/*` request — method, resolved path, status, duration, body summary (mutations only), and the host/entry/IP references parsed out of the call. The activity table is surfaced on the test plan's API-calls tab, the recon run page and the assist sessions page so users can verify their agent queried the right hosts.

## Documentation

- [Contributing](CONTRIBUTING.md) — **start here to maintain the project**: versioning, schema/migration ownership, the file-size policy, host-dedup model, agent workflows, build/test/CI
- [Agent Guide (AGENTS.md)](AGENTS.md) — the contract every agent reads at startup
- [Architecture](documentation/ARCHITECTURE.md) — system topology, package map, security model
- [API Guide](documentation/API_GUIDE.md) — endpoint reference with auth, shapes, error contracts
- [Upload Formats](documentation/UPLOAD_FORMATS.md) — supported scanner exports + detection rules
- [Parsers](documentation/PARSERS.md) — what each parser writes, and how to add one
- [MCP](documentation/MCP.md) — the agent surface as MCP tools, client setup, the certificate
- [Assist Tools](documentation/ASSIST_TOOLS.md) — how the agent read surface was derived, and the review rule for adding to it
- [Testing Framework](documentation/TESTING_FRAMEWORK_DOCUMENTATION.md) — pytest + Vitest harness, and CI
- [UI Style Guide](documentation/UI_STYLE_GUIDE.md) — frontend behavioral contract
- [Scripts](scripts/README.md) — deployment and maintenance helpers
- For SBOM, visit **Reference → Software Bill of Materials** in the running app — the live page reflects the deployed build's resolved dependency tree.
