# BlueStick

> **Verified against:** backend 2.254.1 / frontend 5.152.1 (2026-08-11). New here? See [CONTRIBUTING.md](CONTRIBUTING.md) for the conventions you'll need on day one.

BlueStick is a network visibility and review platform for aggregating host intelligence from one or more networks. It ingests output from security tooling, normalizes hosts and ports into a shared model, and presents dashboards for triage, reporting, and analyst follow-up. Analysts can flag hosts for review, attach notes, and revisit the same asset as fresh scan data arrives.

## Tech Stack

- Backend: Python 3.11, FastAPI, SQLAlchemy 2.0, Alembic migrations
- Database: PostgreSQL 16
- Frontend: React 18, Vite, TypeScript, **Radix UI primitives + Tailwind CSS 4** (shadcn-style; MUI-free since 4.0.0), TanStack Table, Chart.js, lucide-react icons
- Authentication: JWT for humans (role-based, TOTP 2FA enforced by default) + per-plan / per-scope / per-session X-API-Key for agents
- Deployment: Docker and Docker Compose

## Core Workflows

- **Ingest** scan output from Nmap (XML + grepable), Masscan, Naabu, RustScan, Nessus, OpenVAS, NetExec, Eyewitness, httpx, WhatWeb, testssl.sh, Nikto, Amass/Subfinder, BloodHound/SharpHound, DirBuster/Gobuster/ffuf/Feroxbuster/Dirsearch, dnsx, RDAP, SMBMap, DNS CSV, and subnet CSV sources. Format is auto-detected at upload — see [Upload Formats](documentation/UPLOAD_FORMATS.md) for the full table.
- **Deduplicate** hosts by IP so repeated scans update a shared asset record instead of creating parallel copies; track per-attribute confidence + conflicts across the scan history.
- **Triage** host discoveries, open ports, findings/vulnerabilities, web-interface inventory, and parse failures from the **Hosts** page (with a boolean query DSL) and the **Findings** spine.
- **Analyse posture** — the **Posture** hub answers a manager's questions across four tabs: the executive security condition, remediation trajectory, and highest-leverage action (**Posture**); segment comparison by site or subnet (**Segments**); recurring weaknesses grouped into program-level pattern families (**Patterns**); and whether the conclusions are trustworthy via per-domain assessment coverage (**Evidence**). A network **Topology** map and a cross-project **Portfolio** view round out the manager-facing surfaces.
- **Collaborate** — each authenticated user can mark a host with a personal follow status and add attributed review notes (threaded, @mentions) that surface in host and activity feeds.
- **Agent workflows** — **four** structured surfaces for AI/terminal-side agents (Claude Code, Codex, etc.), each minting a scope-bound, time-limited X-API-Key:
  - **Recon** — agent populates host data for a scope from scanner output
  - **Plan generation** — agent reads candidate hosts and drafts a structured test plan
  - **Execution** — agent works through an approved plan with per-test human approval, per-host sanity-check gates, and a per-session environment probe so commands match the operator's host
  - **AI Assist** — an interactive agent that answers ad-hoc questions over all project data using the same query DSL as the Hosts page (e.g. "show me the hosts I have in review"), and — when the operator grants it — records notes and review status on hosts assigned to them
  - Every agent request is recorded in an audit log surfaced in the UI so users can review exactly what their agent did and which hosts it touched. The contract agents read at startup is [AGENTS.md](AGENTS.md).
- **MCP** — every agent workflow is also reachable over the [Model Context Protocol](documentation/MCP.md) at `/api/v1/mcp`, so an MCP-capable client (VS Code Copilot, Claude Code, Codex) calls them as native tools instead of shelling `curl`. `tools/list` is scoped to the workflow the caller's key belongs to; the session dialogs emit ready-to-paste client config, and `scripts/trust-cert.sh` handles the self-signed certificate each client rejects by default.
- **Approve by exception, not by default** — an agent may run an approved tool against a host already in the inventory and write its output into the session's working directory without asking each time; anything outside those bounds stops for the operator. Which tools are approved is a table an admin vets ([Tool Reference](documentation/MCP.md#the-tool-registry)), and an agent that needs something else records the request rather than substituting.
- **Export** scoped data and operational reports for downstream analysis (CSV, JSON, HTML; large PDF/JSON/zip bundles run as **async report jobs** on a dedicated report-worker container).

## Repository Layout

```text
backend/app/
  api/v1/endpoints/   FastAPI route modules
  core/               Settings, auth, security helpers
  db/                 SQLAlchemy models and session setup
  parsers/            Tool-specific parsers
  schemas/            Pydantic API schemas
  services/           Ingestion, export, follow-up, reporting, posture, and finding-correlation logic
  worker_loop.py      Shared durable-queue worker loop (ingestion + report workers)

frontend/src/
  components/         Shared UI components
  contexts/           Auth/theme context providers
  pages/              Route-level screens
  services/           API client and typed contracts
  tests/              Vitest test suites

documentation/        Architecture, API, deployment, upload, and testing docs
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
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Local frontend development

```bash
cd frontend
npm install
npm start            # dev server with hot reload (alias for `npm run dev` → vite)
npm run build        # production build (vite build)
npm test -- --run    # Vitest suites
```

## Operations

```bash
./scripts/deploy.sh        # unified deploy menu (start/rebuild, first-time setup, reconfigure IP, nuclear clean, security status)
./scripts/status.sh        # quick container health check
./scripts/collect-logs.sh  # bundle backend + worker + report-worker + db + nginx logs for support
./scripts/seed_demo_data.py  # populate a realistic demo project (so Posture, Segments/Patterns, and Findings are evaluable)
```

## Schema & Migrations

- Tables are owned by Alembic — migrations live in `backend/alembic/versions/`. Every startup runs `alembic upgrade head` before serving traffic.
- Baseline revision: `b46cd59c17f5_baseline_schema.py`. Subsequent migrations layer on additive changes (plan-generation metadata, ingestion-quality columns, environment probe columns, agent API call log).
- The previous startup-DDL compatibility path (`app/db/init.py` plus `_ensure_schema` calls in services) is gone; the model is the schema, and Alembic enforces it.

## Asynchronous ingestion

`POST /api/v1/projects/{id}/upload/` queues an ingestion job and returns immediately; a separate worker container processes the queue and the UI polls `/upload/jobs/{job_id}` for completion. Parser failures are recorded with structured user-facing messages on the Parse Errors page.

## Agent audit trail

When an agent works against a plan (recon, generation, or execution), BlueStick records every inbound `/api/v1/agent/*` request — method, resolved path, status, duration, body summary (mutations only), and the host/entry/IP references parsed out of the call. The activity table is surfaced on the test plan detail page so users can verify their agent queried the right hosts.

## Documentation

- [Contributing](CONTRIBUTING.md) — **start here to maintain the project**: versioning, schema/migration ownership, the file-size policy, host-dedup model, agent workflows, build/test/CI
- [Agent Guide (AGENTS.md)](AGENTS.md) — the contract every agent reads at startup
- [Architecture](documentation/ARCHITECTURE.md) — system topology, package map, security model
- [API Guide](documentation/API_GUIDE.md) — endpoint reference with auth, shapes, error contracts
- [Upload Formats](documentation/UPLOAD_FORMATS.md) — supported scanner exports + auto-detection rules
- [Testing Framework](documentation/TESTING_FRAMEWORK_DOCUMENTATION.md) — pytest + Vitest harness, and CI
- [UI Style Guide](documentation/UI_STYLE_GUIDE.md) — frontend behavioral contract
- [Scripts](scripts/README.md) — deployment and maintenance helpers
- For SBOM, visit **Reference → Software Bill of Materials** in the running app — the live page reflects the deployed build's resolved dependency tree.
