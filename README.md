# BlueStick

BlueStick is a network visibility and review platform for aggregating host intelligence from one or more networks. It ingests output from security tooling, normalizes hosts and ports into a shared model, and presents dashboards for triage, reporting, and analyst follow-up. Analysts can flag hosts for review, attach notes, and revisit the same asset as fresh scan data arrives.

## Tech Stack

- Backend: Python 3.11, FastAPI, SQLAlchemy 2.0, Alembic migrations
- Database: PostgreSQL 16
- Frontend: React 18, Vite, TypeScript, Material UI 6, Chart.js
- Authentication: JWT for humans (role-based) + per-plan / per-scope X-API-Key for agents
- Deployment: Docker and Docker Compose

## Core Workflows

- **Ingest** scan output from Nmap, Masscan, Naabu, RustScan, Nessus, OpenVAS, NetExec, Eyewitness, httpx, Nikto, Amass, BloodHound, DirBuster/Gobuster/ffuf, DNS CSV, and subnet CSV sources.
- **Deduplicate** hosts by IP so repeated scans update a shared asset record instead of creating parallel copies; track per-attribute confidence + conflicts across the scan history.
- **Triage** host discoveries, open ports, vulnerabilities, risk insights, web-interface inventory, and parse failures from a unified dashboard.
- **Collaborate** — each authenticated user can mark a host with a personal follow status and add attributed review notes that surface in host and activity feeds.
- **Agent workflows** — three structured surfaces for AI/terminal-side agents (Claude Code, Codex, etc.):
  - **Recon** — agent populates host data for a scope from scanner output
  - **Plan generation** — agent reads candidate hosts and drafts a structured test plan
  - **Execution** — agent works through an approved plan with per-test human approval, per-host sanity-check gates, and a per-session environment probe so commands match the operator's host
  - Every agent request is recorded in an audit log surfaced on the plan detail page so users can review exactly what their agent did and which hosts it touched.
- **Export** scoped data and operational reports for downstream analysis (CSV, JSON, HTML, PDF).

## Repository Layout

```text
backend/app/
  api/v1/endpoints/   FastAPI route modules
  core/               Settings, auth, security helpers
  db/                 SQLAlchemy models and session setup
  parsers/            Tool-specific parsers
  schemas/            Pydantic API schemas
  services/           Ingestion, risk, export, follow-up, and reporting logic

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

```bash
docker compose up --build -d
docker compose logs backend
```

The backend seeds a default admin account automatically on first boot if no admin exists. Check the backend logs for the generated credentials, or set `DEFAULT_ADMIN_PASSWORD` before startup.

Frontend: `https://localhost`
Backend API: `https://localhost:8000`
OpenAPI docs: `https://localhost:8000/docs`

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
./scripts/deploy.sh        # unified deploy menu (first-time setup, rebuild, nuclear clean)
./scripts/status.sh        # quick container health check
./scripts/collect-logs.sh  # bundle backend + worker + db + nginx logs for support
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

- [CHANGELOG](CHANGELOG.md) — release-by-release history
- [Agent Guide (AGENTS.md)](AGENTS.md) — the contract every agent reads at startup
- [Architecture](documentation/ARCHITECTURE.md) — system topology, package map, security model
- [API Guide](documentation/API_GUIDE.md) — endpoint reference with auth, shapes, error contracts
- [Upload Formats](documentation/UPLOAD_FORMATS.md) — supported scanner exports + auto-detection rules
- [Testing Framework](documentation/TESTING_FRAMEWORK_DOCUMENTATION.md) — pytest + Vitest harness
- [UI Style Guide](documentation/UI_STYLE_GUIDE.md) — frontend behavioral contract
- [Scripts](scripts/README.md) — deployment and maintenance helpers
- For SBOM, visit **Reference → Software Bill of Materials** in the running app — the live page reflects the deployed build's resolved dependency tree.
