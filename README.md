# BlueStick

> **Verified against:** backend 2.450.0 / frontend 5.329.0 (2026-10-01). New here? See [CONTRIBUTING.md](CONTRIBUTING.md) for the conventions you'll need on day one.

BlueStick is a network visibility and review platform for aggregating host intelligence from one or more networks. It ingests output from security tooling, normalizes hosts and ports into a shared model, and presents dashboards for triage, reporting, and analyst follow-up. Analysts can flag hosts for review, attach notes, and revisit the same asset as fresh scan data arrives.

## Tech Stack

- Backend: Python 3.11, FastAPI, SQLAlchemy 2.0, Alembic migrations
- Database: PostgreSQL 16
- Frontend: React 18, Vite, TypeScript, **Radix UI primitives + Tailwind CSS 4** (shadcn-style; MUI-free since 4.0.0), TanStack Table, lucide-react icons, cmdk command palette, sonner toasts, **Observable Plot** for charts with axes, and three.js for the address terrain on Posture (loaded lazily); small inline visuals are hand-built SVG
- Authentication: JWT for humans — a binary global role (admin / member) plus a per-project role (admin > analyst > auditor > viewer), TOTP 2FA enforced by default — and a per-session, project-scoped X-API-Key for agents
- Client reports: Quarto on a dedicated report-worker container (HTML, Word, and the `.qmd` source)
- Deployment: Docker and Docker Compose

## Core Workflows

- **Ingest** scan output from Nmap (XML + grepable), Masscan, Naabu, RustScan, Nessus, OpenVAS, Nuclei, NetExec, EyeWitness, httpx, WhatWeb, testssl.sh, Nikto, Amass/Subfinder, BloodHound/SharpHound, DirBuster/Gobuster/ffuf/Feroxbuster/Dirsearch, dnsx, RDAP, SMBMap, DNS CSV, and subnet CSV sources. Upload is staged: the format is detected, the operator reviews it (and can override it) and then imports — see [Upload Formats](documentation/UPLOAD_FORMATS.md) for the full table. **Reference → What BlueStick reads** says, per tool, what each import keeps, where it is shown and its known gaps, and every import reports the lines it did not interpret (as redacted shapes) on **Ingestion Results**.
- **Deduplicate** hosts by IP so repeated scans update a shared asset record instead of creating parallel copies; track per-attribute confidence + conflicts across the scan history.
- **Triage** hosts from the **Hosts** page (a boolean query DSL with autocomplete) and the host inspector, organised by service: weaknesses first, then each service with its per-port evidence. Scanner rows are *scanner observations* until someone judges them; the **Findings** hub groups them by issue (a shared weakness catalog with check ids, so `kind:` / `check:` filters work across tools), promotes or dismisses them per host or per issue, and holds the confirmed findings. The OS family is derived from the OS name when no scanner reported one.
- **Analyse posture** — the **Posture** hub answers a manager's questions across four tabs: the executive security condition, remediation trajectory, and highest-leverage action — plus where the team has been (an address terrain showing which /24s are tested, planned, worked or untouched; the three.js map opens on demand), scanner observations by severity and the three scope states (**Posture**); segment comparison by site or subnet (**Segments**); recurring weaknesses grouped into program-level pattern families (**Patterns**); and whether the conclusions are trustworthy via per-domain assessment coverage (**Evidence**). A cross-project **Portfolio** view and, for global administrators, the **Oversight** programme dashboard (any subset of projects) round out the manager-facing surfaces.
- **Operations** — the landing hub, and the signed-in person's own page: one sentence about your queue, your own work — only what needs you — **Changed since review** (hosts *you* reviewed that changed afterwards, or that you concluded need more evidence), **Untouched, with a reason** (what to pick up next, ranked by a stated tier) and one line about your own agent sessions. Project status is not here: it is on **Posture**.
- **Collaborate** — each authenticated user can mark a host with a personal follow status and add attributed review notes; host-note threads and finding comments share one Collaboration feed, and an `@mention` notifies the person named and then the rest of the discussion.
- **Agent workflows** — an operator starts **one project-scoped agent session** (one time-limited, renewable X-API-Key) for an AI/terminal-side agent (Claude Code, Codex, etc.). The key may do exactly what its operator's project role allows — there are no per-workflow keys and no separate capability grants. The operator drives the agent, and the agent does its own work in whatever order it needs — nothing waits on an approval step (v2.433.0):
  - **AI Assist** (the default) — answers ad-hoc questions over all project data using the same query DSL as the Hosts page (e.g. "show me the hosts I have in review"), and records notes and review status when the operator's role permits writes. An auditor's or viewer's agent is read-only because its operator is.
  - **Scanning** — agent reads a scope's ranges, names and target lists, runs scanners on the operator's machine and uploads the output to the session
  - **Tests on hosts** — agent proposes individual tests on the hosts the operator names (tool, command, why); they appear on each host's page at once, with no plan to open and nothing to approve. A person can work them too
  - **Evidence** — agent runs a test and records the command, the outcome and the output as an evidence record, which is what marks the host tested; the session records the agent's client, model and prompt version, and each test and evidence record keeps a snapshot of them. A finding the evidence shows is a *proposal* a person accepts
  - Every agent request is recorded in an audit log surfaced in the UI so users can review exactly what their agent did and which hosts it touched. The contract agents read at startup is the [agent guide](documentation/AGENT_GUIDE.md), served at `GET /api/v1/agents-guide`.
- **MCP** — every agent workflow is also reachable over the [Model Context Protocol](documentation/MCP.md) at `/api/v1/mcp`, so an MCP-capable client (VS Code Copilot, Claude Code, Codex) calls them as native tools instead of shelling `curl`. Configure **one** server entry, `bluestick`; `tools/list` returns the whole catalogue to every session, and whether a call succeeds is decided per request by the operator's project role — the MCP layer makes no authorization decision of its own. The Start Agent Session dialog emits ready-to-paste client config, and clients trust BlueStick's certificate through the local root CA (`ca/local-ca.sh`), installed once per analyst machine — no per-client pinning.
- **The operator drives** — the agent shows the operator every command, proposes next steps rather than taking them unasked, stays inside the project's declared scope, and writes its output into the session's working directory; a target outside the scope, anything outside that directory, or a change to the operator's machine waits for their explicit go-ahead. There is no approved-tool allowlist — the Tool Reference is a catalogue, not a permission list. BlueStick cannot enforce these rules — commands run on the operator's machine; the client's sandbox is the real boundary, and the server adds the record of every command the agent reports.
- **Client reports** — the **Reports** page (Findings hub) renders a findings-first report from a Quarto template in `report-templates/` (Penetration test report, Executive brief, Remediation worklist) to HTML, Word and the `.qmd` source. Drafts preview; issuing freezes and numbers a report; issued reports are revised or followed by addenda, never edited. There is no PDF output — Word exports it. See [report-templates/README.md](report-templates/README.md) to add a template.
- **Export** scoped data and operational reports for downstream analysis — CSV and HTML stream synchronously; JSON, agent-package and markdown-bundle archives run as **async report jobs** on the report-worker container.

The app opens on the **Operations** hub; everything else hangs off **Inventory**, **Findings**, **Posture**, **Workflows** and **Collaboration**, with **Settings**, **Administration** (global admins) and **Reference** at the foot of the sidebar.

## Repository Layout

```text
backend/app/
  api/v1/endpoints/   FastAPI route modules
  main.py, startup.py App entrypoint; first-boot seeding (default admin, default project), background loops
  data/               parser_coverage.json (the "What BlueStick reads" page), tool_registry_seed.json
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
  config/             navigation.tsx — the nine-hub navigation manifest
  data/, theme/       Upload-format table (test-pinned to the docs); palettes and tokens
  pages/              Route-level screens
  services/           API client and typed contracts
  tests/              Vitest test suites

documentation/        Architecture, API, MCP, parsers, upload-format, UI-style, testing and parse-audit docs
report-templates/     Client report templates (Quarto), mounted read-only into the backend and report worker
scripts/              Deployment and operational helpers
artifacts/            Fixture data for parser and ingestion testing
```

## Quick Start

### Full stack with Docker

**A new host uses option 2.** It generates `.env` (with a random `SECRET_KEY`) and self-signed SSL certs, then builds and starts the stack. To have agents and browsers trust it without per-client workarounds, replace the certificate with one from a local CA afterwards ([`ca/README.md`](ca/README.md)):

```bash
./scripts/deploy.sh        # choose option 2, "First-time setup"
```

**An existing instance is upgraded with option 1 — with the stack still running.** Option 1 takes a database dump before it builds, and refuses to go on without one; a stopped database gives it only a raw volume snapshot, which a rollback cannot restore. So do not `docker compose down` before an upgrade, and do not use option 1 for a first install (there is nothing to back up yet: use option 2).

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
pip install -r requirements-dev.txt -c constraints.txt   # runtime + pytest/ruff, every version fixed by constraints.txt
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
./scripts/status.sh        # container health, the newest backup and its age, data repairs not yet applied
./scripts/check.sh         # THE GATE before a push (there is no CI): backend suite in the report-worker image (fails if a Quarto test skipped), ruff (any finding fails), frontend tsc + vitest, Alembic round trip; --fast skips the last; `make check`
./scripts/collect-logs.sh  # ANONYMISED diagnostics bundle (logs, ingestion queue, parser audit, served TLS certificate, agent-surface outcomes, request timing by route, top SQL statements, agent feedback) — read feedback.txt (free text) before sharing; needs python3; --since 72h, --terms FILE, --no-feedback
./scripts/backup-db.sh     # database + uploads/ backup (pg_dump, or a raw volume snapshot if Postgres is down); read to its end before it reports success, size + SHA-256 recorded; mode 0600; keeps the newest BACKUP_KEEP (default 10)
./scripts/restore-db.sh    # restore the database and uploads/ from a backup-db.sh artifact (size, SHA-256 and a full read are checked before anything is dropped; ends with the state the instance is in)
docker compose exec backend python scripts/data_repairs.py   # which one-off data repairs this instance has run, and the command for each pending one
./scripts/upgrade-instance.sh  # carry a running instance's local state into a freshly copied source tree, then deploy (for hosts that deploy by file copy; read its header for the expected folder layout)

# Seeds run INSIDE the backend container (scripts/ is bind-mounted at /app/scripts); deploy.sh runs none of them:
docker compose exec backend python scripts/seed_demo_data.py        # a realistic demo project (Posture, Segments/Patterns, Findings become evaluable)
docker compose exec backend python scripts/seed_named_assets.py     # then the named-asset scenario on it (--reset rebuilds)
docker compose exec backend python scripts/seed_eval_scenarios.py   # a small project of hand-placed scenarios, each with "open X, expect Y" (--wipe rebuilds)
```

### Deploying, backups and what the host needs

- **A deploy waits for the backend, and tells a slow migration from a crash.** Option 1 checks first that the database image is on the host (pulling it if it can; it stops, with nothing built, if it cannot) and lists any pinned base image the host lacks. It then builds (a failed build restarts nothing), stops the two workers, starts the database and **the backend alone**, and waits up to `DEPLOY_HEALTH_TIMEOUT` seconds (default 1800) — the backend serves only after its schema migrations finish. Only then does it start the workers and the frontend. "Healthy" is reported only for the build just made: if `docker compose up` failed before it recreated the backend container, the script says the previous build is still running, starts its workers again and stops. If the backend container **exited or keeps restarting**, roll back (option 7). If it is **still running** at the timeout it has not crashed: follow `docker compose logs -f backend` and finish with `docker compose up -d`; rolling back in the middle of a migration points the old build at a half-migrated schema.
- **A failed deploy keeps its rollback point.** Before it builds, option 1 tags the running images `bluestick-rollback-<service>:previous` and records the pre-deploy dump and its schema revision in `.deploy-rollback-state`. Until a deploy or a rollback ends healthy, running option 1 again **keeps** those — it says so — instead of snapshotting the failed build over them; the recorded dump is never pruned. (If the database is still at the pre-deploy schema, a fresh dump is taken and recorded, so work done since is not lost. `DEPLOY_NEW_SNAPSHOT=1` discards the kept rollback point.)
- **Rolling back (option 7)** re-points the images, **stops the application**, and compares the database's schema revision with the one recorded at the pre-deploy backup. Unchanged: it starts the previous build, nothing else. Changed — the failed deploy migrated the database, and the previous build cannot start on it — it says so and restores the pre-deploy dump first (it asks; the default is yes; a safety backup of the current database is taken before), and it does not start the previous build on the newer schema unless you type `START ANYWAY`. It then starts the backend, waits for it, starts the rest, and ends with one line saying what state the instance is in and what to do next. The manual equivalent, if the script cannot finish: `./scripts/restore-db.sh <PREDEPLOY_DB_DUMP>` (the path is in `.deploy-rollback-state`), which also starts the stack.
- **Backups** are taken before every option-1 deploy and by `./scripts/backup-db.sh`, into a sibling `<project>-db-backups` folder (mode 700, files 0600). A dump is verified by reading it **to its end** (`pg_restore -f /dev/null`; listing it is not enough — the table of contents is at the front of the file, so a dump cut short still lists every entry), and its size and SHA-256 are written to the `.meta` beside it, with those of the `uploads/` archive. `restore-db.sh` compares both and repeats the full read before it stops or drops anything, so a file truncated or altered in a copy is refused while the database still exists (`--force-unverified` overrides, for the case where the file is the only copy). A backup that fails its read-back, or whose `uploads/` archive failed, is a failed backup. The newest `BACKUP_KEEP` (default 10; `0` = all) are kept. (A directory that was never pruned is reported, not emptied: `BACKUP_PRUNE_BACKLOG=1 ./scripts/backup-db.sh` clears the backlog once.) **Nothing schedules them**: on a host that is not redeployed, add a cron entry — `./scripts/status.sh` shows the newest backup's age. A dump is only restorable with the `.env` it was taken under (`CREDENTIAL_ENCRYPTION_KEY` / `SECRET_KEY` decrypt the TOTP secrets and stored credentials in it): keep a copy with `deploy.sh` option 6. Option 4 (Nuclear clean) saves both before it removes anything, and only removes this Compose project's resources.
- **The first-boot admin password** is in `./uploads/initial-admin-password.txt` and nowhere else — it is never written to the logs.
- **Data repairs.** A few releases correct rows that older versions wrote (`scripts/backfill_misconfigs.py`, `scripts/repair_netexec_results.py`). They are not migrations and nothing runs them automatically; the `data_repairs` table records each run, and `deploy.sh` / `status.sh` list the ones this instance still owes, with the exact command. Run them when no import is in progress.
- **Base images are pinned** to exact releases — `postgres:16.13` in `docker-compose.yml`; `python:3.11.16-slim-trixie`, `debian:12.15-slim` (the stage that downloads Quarto), `node:22.23.2-alpine` and `nginx:1.31.3-alpine` in the Dockerfiles — and Python packages, transitive ones included, by `backend/constraints.txt`, so a rebuild of the same commit is the same image. `POSTGRES_IMAGE` / `PYTHON_IMAGE` / `DEBIAN_IMAGE` / `NODE_IMAGE` / `NGINX_IMAGE` in `.env` override a pin for a host that holds another tag.
- **A deploy builds, and a build needs the network.** Option 1 always rebuilds: besides the base images it downloads Python packages (PyPI), npm packages, Debian/Alpine packages and Quarto (GitHub) for every layer that is not already in Docker's build cache. Before building it lists the pinned base images the host does not hold and asks the registry whether it answers. There is **no deploy path that uses pre-built images**: a host that cannot reach those sources cannot deploy a new build (the unused `transfer-images.sh` was removed in v2.449.2).
- **Container logs rotate** (`LOG_MAX_SIZE` × `LOG_MAX_FILE`, default 20 MB × 5 per container).
- **PostgreSQL memory** defaults suit a 2–4 GB host and are set in `.env` (`PG_SHARED_BUFFERS`, `PG_EFFECTIVE_CACHE_SIZE`, `PG_WORK_MEM`, `PG_MAINTENANCE_WORK_MEM`). Change them on a signal, read from `pg_stat_database` (the query is in `.env.example`; `collect-logs.sh` includes it): **`temp_bytes` climbing** → raise `PG_WORK_MEM` (per sort, per connection — step it); **`blks_read` far above `blks_hit`'s growth on a warm instance** → raise `PG_SHARED_BUFFERS`. Statements slower than `PG_LOG_MIN_DURATION_MS` (default 500) are in the `db` log.
- **Where the time goes.** Every API request writes one access line with its duration, the time spent in database statements (`db_ms`), the statement count (`db_n`) and the route template; a request at or over `SLOW_REQUEST_MS` (default 1000) also writes a `SLOW request` warning. `collect-logs.sh` rolls these up per route in `request_timing.txt` (requests, p50 / p95, mean `db_ms` and `db_n`, slow count) and lists the statements the database spent most time in in `sql_statements.txt` (from `pg_stat_statements`: calls, total and mean ms, rows, the normalised text — no values). The script creates the `pg_stat_statements` extension if it is missing; the library is preloaded by `docker-compose.yml`, and when it is not available the file says so in one line.

## Schema & Migrations

- Tables are owned by Alembic — migrations live in `backend/alembic/versions/`. Every startup runs `alembic upgrade head` before serving traffic.
- Baseline revision: `b46cd59c17f5_baseline_schema.py`. Subsequent migrations layer on additive changes (plan-generation metadata, ingestion-quality columns, agent API call log; the environment probe columns were added and later dropped).
- `app/db/init.py` builds no schema itself — it takes an advisory lock and runs `alembic upgrade head` (skipped under `BLUESTICK_SKIP_DB_INIT=1`, test harness only). The old `create_all` + hand-rolled migration list is gone; the model is the schema, and Alembic enforces it.
- **Upgrading across v2.449.0 — back up first** (`./scripts/backup-db.sh`): revision `b8e2a5c7d1f3` drops a table. An agent session used to be two rows with two ids — the session (`agent_sessions`) and a second row (`assist_sessions`) that the session pages, the notes list and the API-call feed were addressed by. It is one row with one id now. The revision:
  - copies onto each session what only its second row carried (stated purpose, last activity, model / client / prompt version; for a session from before v2.337.0 also its ended status and time), and creates a session for any second row that had none, so nothing is orphaned;
  - stamps every agent API call and feedback row that named only the second row with its session — no call and no feedback row loses its session — then drops `agent_api_calls.assist_session_id`, `agent_feedback.assist_session_id` and the `assist_sessions` table;
  - keeps each old id on its session (`agent_sessions.legacy_assist_session_id`), so a bookmarked or pasted `/assist-sessions/<id>` link still opens the right session. One exception: if two old rows pointed at the same session (the application never wrote that, but the schema did not rule it out), the session keeps the **lowest** of their ids as its old id — a link made with the other one no longer finds it. The calls and feedback of both are kept on the session.

  Nothing is lost that a page shows: sessions, their keys, calls, notes, tests, evidence and feedback are all kept, and sessions from before v2.337.0 gain a session page they did not have. What to expect afterwards: a session's number in the start dialog, on Agent Sessions and in its address is the same one everywhere (it already was the number the agent reports); the operator-side API paths `/projects/{id}/assist/sessions…` and `/projects/{id}/assist-sessions/{id}/api-activity` are deprecated: they still answer for an old id, and for a session's own id (a browser tab left open across the upgrade sends that) — use `/projects/{id}/agent-sessions…` (see `documentation/API_GUIDE.md` §4.13); `GET /projects/{id}/assist/sessions` (the list) is gone. The downgrade recreates the table with one row per session and refills the dropped columns for the pre-v2.337.0 sessions only; the backup is the exact copy.

  The same upgrade applies `c9f3b6d8e2a4`: it adds `note_attachments.caption` and the `report_images` table. Nothing is backfilled and no data is changed; reports already issued keep rendering from the live attachments. From now on issuing a report copies its evidence images into `uploads/client_reports/<project>/<report>/evidence/` (included in `backup-db.sh`'s uploads archive), so expect that directory to grow by the size of the ticked images per issued report, and issuing is refused (409) when a ticked image's file is missing. An issued report whose render FAILED before the upgrade must be revised — the shipped templates changed, so "Render again" refuses it. The downgrade drops the column (captions are lost) and the table; copied files stay on disk, unread.
- **Upgrading across v2.448.0 — back up first** (`./scripts/backup-db.sh`), **and run the check below before you upgrade.** Seven revisions apply, in this order:
  - `a1d4f7b9c2e3` adds `ingestion_jobs.in_progress_scan_id`: the scan an import is writing, so a worker killed mid-import no longer leaves a partial scan behind when the job is picked up again. Existing rows get NULL.
  - `b2e5a8c1d4f6` adds a unique index: one scanner finding per issue in a project. **It refuses to run when a project already has two scanner findings for one issue.** It merges nothing — each finding has its own report text, status, history, comments, endpoints and proposals — so the migration stops, lists the findings, and the backend does not start. Look before upgrading, on the running version:

    ```sql
    SELECT project_id, dedup_key, array_agg(id ORDER BY id)
    FROM findings
    WHERE source = 'scanner' AND dedup_key IS NOT NULL
      AND substr(dedup_key, 1, 4) <> 'row:'
    GROUP BY project_id, dedup_key HAVING count(*) > 1;
    ```

    (`docker compose exec db psql -U nmapuser -d networkMapper`, or the user and database your `.env` names.) No rows: nothing to do. For each row, on the version you are upgrading **from**: keep one finding of the group, add the other's hosts to it, delete the rest — then upgrade. If `scripts/backfill_misconfigs.py` was run on a build older than this one, it may have moved two scanner findings of one project onto the same catalog check (for example a Nikto and a testssl finding for missing HSTS); that pair is what this revision's pre-check finds and refuses to migrate over. Merge the two findings by hand and upgrade again. From this build on the backfill never creates such a pair: it leaves the second finding on its own key and names both findings on every run until they are merged.

    If the upgrade has already stopped on this, the backend log names the findings in a line that starts `DATABASE MIGRATION FAILED — NOTHING WAS CHANGED`, and option 1 ends with "The backend is not staying up". That line means what it says: the whole upgrade ran in one transaction and was rolled back, so the database is still at the revision the previous build runs. **Roll the build back (`deploy.sh` option 7); do not restore.** Option 7 compares the database's schema revision with the one recorded at the pre-deploy backup, finds it unchanged, and starts the previous build with no restore. Then resolve the findings on the previous build and deploy again.
  - `c3f6b9d2e5a7` adds indexes for the read path (substring search on port service, product and version; `vulnerabilities.issue_key`; foreign keys on the newer tables; the Scans summary's open-port count). Nothing is dropped. Its ten indexes are built **inside the upgrade transaction**, with the other revisions: the application is not serving yet, so nothing is blocked that would otherwise run. Expect about 15–20 seconds more on the first start at 70,000 hosts / 1,000,000 ports, under a second on a small database; the backend serves only when the upgrade has finished (see "A deploy waits for the backend" above: a backend that is still running has not crashed). An interrupted upgrade leaves nothing half-built: the transaction is rolled back and the next start runs it again from the beginning.

    **How the upgrade runs, and what to read if it stops.** Every process (the backend, the ingestion worker, the report worker) still migrates the schema when it starts, one at a time: one takes the migration lock and upgrades, the others wait and log `waiting for another process to finish database migrations` every 15 seconds, then find nothing left to do. A waiter gives up after `DB_MIGRATION_LOCK_TIMEOUT` seconds (default 3600) and is restarted by Docker to wait again. `deploy.sh` and `restore-db.sh` stop the two workers and start **the backend alone**, so it is the one that migrates, and start the workers only once it is healthy. If the upgrade stops, the backend log has one line starting `DATABASE MIGRATION FAILED`:
    - `… — NOTHING WAS CHANGED`: the upgrade was rolled back and the database is still at the revision the previous build runs. Roll the build back (`deploy.sh` option 7) — **do not restore**; option 7 sees the unchanged revision and restores nothing. The duplicate refusal of `b2e5a8c1d4f6` above is this case.
    - `… — schema left PARTWAY at revision …`: the schema moved and the previous build may not run on it. Roll back with option 7 and let it **restore the pre-deploy backup** (it asks; answer yes; it takes a safety backup of the current database first), after which it starts the previous build. By hand, if option 7 cannot finish: `./scripts/restore-db.sh <PREDEPLOY_DB_DUMP>` — the path is the `PREDEPLOY_DB_DUMP|` line of `.deploy-rollback-state`, and the previous images are tagged `bluestick-rollback-<service>:previous`.

    After any upgrade, one check is worth running: `SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;`. No rows is the expected answer. A row is an index an interrupted build of an **earlier** release left unusable (the backend also warns about it at start); rebuild it with `REINDEX INDEX CONCURRENTLY <name>;` — the application can stay up.

    If you started the stack another way (`docker compose up -d` by hand) and the migration does not move — the deploy keeps printing "a schema migration is running" and the backend log shows no new line for many minutes — look at what the database's sessions are doing:

    ```sql
    SELECT pid, application_name, state, wait_event_type, wait_event,
           now() - query_start AS waiting, left(query, 60) AS query
    FROM pg_stat_activity
    WHERE datname = current_database() AND pid <> pg_backend_pid()
    ORDER BY query_start;
    ```

    The session that is migrating shows `state = active`; when it is waiting for something, `wait_event_type = Lock` and the row to find is the one holding that lock — typically a session that is `idle in transaction` on a table the migration needs (a worker started by hand in the middle of an import, a `psql` window left open). Processes waiting their turn to migrate are `idle` and hold nothing. The safe way out is to take the other sessions away: `docker compose stop worker report-worker` (start them again when the backend is healthy), and close any open `psql` transaction. **Never restart the backend while it holds the migration lock**: the upgrade is rolled back and starts again from the beginning.
  - `d4a7c1e3f6b8` creates `data_repairs`, the ledger of one-off data corrections. A database with no hosts records both known repairs as not needed; a database with hosts starts with an empty ledger, and `deploy.sh` / `status.sh` name each repair it still owes until it has been run once (see "Data repairs" above).
  - `e5b8d2f4a7c9` adds `host_scan_history.credentialed`: whether a Nessus scan's authenticated checks ran on the host. Earlier imports stay NULL ("the scan did not say"); nothing is backfilled.
  - `f6c9e3a5b8d1` adds `ingestion_jobs.in_progress_created_port_ids`: one nullable JSON column holding the ports a Nessus import attempt created, read only by the cleanup of a failed, cancelled or killed import. Existing rows get NULL.
  - `a7d1f4b6c9e2` adds `DNSX` to the `vulnerabilitysource` type: a name server that allowed a zone transfer (dnsx `-axfr`) becomes a scanner observation, "DNS zone transfer allowed", on that server's host. Nothing is backfilled — re-import a dnsx file to get the observation. The downgrade deletes those observations.

  What else changes on this upgrade:
  - **The import time limit now applies to every parser.** `INGESTION_JOB_TIMEOUT` (default 1800 seconds, 30 minutes) is checked each time a parser reports progress, and every parser now does; before, several formats (OpenVAS, nuclei, httpx among them) never did, so neither the limit nor a cancel reached them. An import that runs longer fails with "Parse timed out". Raise the value in `.env` before importing a file you expect to take longer.
  - **What a failed, cancelled or timed-out import leaves.** No scan, and nothing that only it created (its new hosts, ports and observations are removed); it does not undo what it changed on hosts and ports that already existed, the names (`DNSName`) it added or the conflict history, and a row a later scan re-observed is kept and moved to that scan (`documentation/PARSERS.md`, "What a failed import leaves").
  - **API requests have a statement timeout.** `API_STATEMENT_TIMEOUT_MS` (default 30000; `0` turns it off) is the longest any one database statement of an API request may run before Postgres cancels it; the request answers 503 ("Narrow the filter or try again"). Imports, report rendering, migrations and the seed / repair scripts are never limited. Routes that stream a whole export lift it for themselves: the host inventory CSV and HTML exports, the tool-ready host list, the DNS names export, and the agents' host NDJSON, report-context and scope target-file downloads. So do the routes that delete or rebuild in bulk — deleting a project, a scan, a scope, a subnet or a user, and re-correlating hosts to subnets (asked for, or after a scope upload that adds a subnet): they are no longer cut off at 30 seconds, and **deleting a large project or scan can take a minute or more** — let it finish.
  - **The next `docker compose up` recreates every container, the database included**, because each service's definition changed: container logs now rotate (`LOG_MAX_SIZE` × `LOG_MAX_FILE`), the database image is pinned (`postgres:16.13`, was `postgres:16`), and Postgres starts with two more flags (`log_min_duration_statement`, `shared_preload_libraries=pg_stat_statements`). Expect a short outage while the database restarts. The data is in the `postgres_data` volume and is not touched.
  - **Backups are now pruned.** `backup-db.sh` — which every option-1 deploy runs — keeps the newest `BACKUP_KEEP` backups (default 10; `0` keeps all), each with its uploads archive. A folder holding more than three backups beyond that is a backlog: it is reported and nothing is deleted, until you run `BACKUP_PRUNE_BACKLOG=1 ./scripts/backup-db.sh` once, or set `BACKUP_KEEP=0`. Move any old backup you want to keep out of the folder first.
  - **The host inventory CSV export has one column fewer.** "Open Notes" is gone: notes stopped having a status in v2.446.0 (the behaviour) and the status column itself was dropped in v2.447.0 (revision `c2e6a4f8d103`), so since v2.446.0 the column only repeated "Notes". It sat between "Test Findings" and "Untriaged Vulns": anything that reads the file by position finds "Untriaged Vulns", "Tags", "Notes", "Last Seen", "Scan File" and "Scan Date" one column to the left.
  - **An issued client report whose render failed before the upgrade cannot be rendered again.** The three shipped templates changed, so their fingerprints no longer match the one recorded when the report was issued, and a retry stops with "The template has changed since this report was issued". Revise the report to issue it with the current template. Issued reports whose files already exist are unaffected: their files are kept as they are.
- **Upgrading across v2.447.0 — back up first** (`./scripts/backup-db.sh`). Revision `c2e6a4f8d103` drops what host notes no longer use: a note's status, assignee, due date and resolution summary, and the note status history. Notes themselves, their threads and their images are untouched. The downgrade restores the columns, not the values.
- **Upgrading across v2.442.0 — back up first** (`./scripts/backup-db.sh`, with the application stopped). That release replaces test plans and execution runs with tests on hosts: revision `d7e1a9c4b602` converts every proposed test into a host test (labelled with its plan's title) and every execution result into an evidence record, and `f4b8d2a6c917` then drops the old tables. Sanity checks, plan history and the plan / run structure have no replacement table, so the backup is the only copy; the downgrade restores the schema, not the data. The conversion stops with the offending row's id rather than skip a record it does not recognise.
- `scripts/test-alembic-roundtrip.sh` walks every revision down and back up, so a new migration needs a real `downgrade()`; `alembic check` catches model/migration drift. There is no hosted CI — `./scripts/check.sh` runs both, with the backend and frontend suites, before a push.

## Asynchronous ingestion

`POST /api/v1/projects/{id}/upload/` (analyst project role) stores the file and returns immediately. With `stage=true` — what the UI does — the job is registered as `staged`: `GET /upload/jobs/{id}/detection` reports the detected format and `POST /upload/jobs/{id}/start` queues it, optionally as a chosen format. An identical file already in the project is refused with 409 `duplicate_scan`. A separate worker container processes the queue and the UI polls `/upload/jobs/{job_id}` for completion. Parser failures, and the lines an import did not interpret, are shown on the **Ingestion Results** page (Inventory hub, beside Scans).

## Agent audit trail

Whatever an agent session is doing (assist, scanning, proposing or running tests), BlueStick records every inbound `/api/v1/agent/*` request — method, resolved path, status, duration, body summary (mutations only), and the host/entry/IP references parsed out of the call. The activity table is surfaced on each session's page under Workflows → Agent Sessions so users can verify their agent queried the right hosts.

## Documentation

- [Contributing](CONTRIBUTING.md) — **start here to maintain the project**: versioning, schema/migration ownership, the file-size policy, host-dedup model, agent workflows, build/test/CI
- [Agent Guide](documentation/AGENT_GUIDE.md) — the contract every BlueStick agent reads at startup (not instructions for working on this repository; it was `AGENTS.md` at the root until 2.427.1)
- [Architecture](documentation/ARCHITECTURE.md) — system topology, package map, security model
- [API Guide](documentation/API_GUIDE.md) — endpoint reference with auth, shapes, error contracts
- [Upload Formats](documentation/UPLOAD_FORMATS.md) — supported scanner exports + detection rules
- [Parsers](documentation/PARSERS.md) — what each parser writes, and how to add one
- [MCP](documentation/MCP.md) — the agent surface as MCP tools, client setup, the certificate
- [Assist Tools](documentation/ASSIST_TOOLS.md) — how the agent read surface was derived, and the review rule for adding to it
- [MCP acceptance questions](documentation/MCP_ACCEPTANCE_QUESTIONS.md) — a repeatable question set for testing a live agent session against the app's pages
- [Testing Framework](documentation/TESTING_FRAMEWORK_DOCUMENTATION.md) — pytest + Vitest harness, and the local gate (`scripts/check.sh`; there is no hosted CI)
- [UI Style Guide](documentation/UI_STYLE_GUIDE.md) — frontend behavioral contract
- [Scripts](scripts/README.md) — deployment and maintenance helpers
- [Local CA](ca/README.md) — replace the self-signed certificate with one from your own root CA, which each analyst machine trusts once (recommended on closed networks)
- [Report templates](report-templates/README.md) — authoring a client report template
- [Parse audit brief](documentation/PARSE_AUDIT_BRIEF.md) — how an on-site agent audits parse accuracy without samples leaving the client network
- For SBOM, visit **Reference → Software Bill of Materials** in the running app — the live page reflects the deployed build's resolved dependency tree.
