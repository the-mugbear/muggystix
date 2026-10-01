# BlueStick Scripts

This directory contains utility scripts for deployment and maintenance.

## Scripts Overview

### Deployment Scripts

- **`deploy.sh`** - **Main deployment script**; interactive menu with seven options:
  1. Start / Rebuild — takes a pre-deploy backup first (database **and** `uploads/`; it aborts if either cannot be written — `BACKUP_ALLOW_MISSING_UPLOADS=1` accepts a database-only backup), snapshots the current images for option 7, then: builds (a failed build stops here, the old containers still running); starts the database, backend and workers; **waits for the backend** — up to `DEPLOY_HEALTH_TIMEOUT` seconds (default 1800), printing progress and whether a schema migration is running; then starts the frontend. It tells the two failures apart: the backend container **exited or keeps restarting** → the boot migration failed, roll back with option 7; the backend is **still running at the timeout** → it has not crashed (a long migration holds the migration lock) — do **not** roll back, follow the logs and finish with `docker compose up -d`. After a healthy start it lists any data repairs this instance has not run (see `data_repairs.py`)
  2. First-time setup (generate `.env` + SSL certs + start)
  3. Reconfigure IP address
  4. Nuclear clean — destroys **this Compose project's** containers, database volume, networks and built images (selected only by the `com.docker.compose.project=<name>` label, so another copy of the tree on the same host is never touched) and this folder's `.env`. Before anything is removed it saves `.env` + `ssl/` the way option 6 does (the dump's encrypted secrets need the key in `.env`) and runs `backup-db.sh`; if either fails it stops and asks for a second typed confirmation (`DELETE WITHOUT BACKUP`). Base images, Docker's build cache, `./uploads` and `./ssl` are left
  5. Security status check
  6. Back up `.env` + SSL to the parent folder (before a re-copy deploy)
  7. Roll back to the previous build (the images snapshotted before the last option-1 deploy, with an optional DB restore)
- **`upgrade-instance.sh`** - Upgrade a file-copy deployment (no git on the host). Precondition, done by hand first: rename the running instance's folder to `<name>_backup_<date>`, copy the new source tree next to it and rename it to the **original** folder name (docker compose keys the database volume on the folder name — `<name>_postgres_data`). Then, from the new folder, `./scripts/upgrade-instance.sh`: it verifies that volume exists (refuses otherwise, `--allow-fresh-db` to override), carries `.env`, `ssl/certs/*`, the uploads dir (moved; `--copy-uploads` to copy), a custom `NGINX_CONFIG`, `.deploy-rollback-state` and the report-template images each `template.json` declares (a logo…) across, reports `.env` keys the new `.env.example` adds and any top-level files only the old folder has, then runs `deploy.sh` option 1 and checks the API's reported version against `platform_version.json`. Idempotent: identical files are skipped, differing ones abort rather than overwrite. The exception is a CA-issued certificate already installed in the new folder (`ca/local-ca.sh install` before the upgrade): it is kept, and the old instance's certificate is not carried. `--no-deploy` stops before the deploy, `--from <dir>` names the old folder when auto-detection is ambiguous, `--yes` answers yes to every confirmation.

### Maintenance Scripts

- **`collect-logs.sh`** - Anonymised diagnostics bundle (container logs, ingestion queue, parser audit, agent feedback), safe to share once its feedback has been read. Options: `--since 72h`, `--terms FILE` (extra names to remove) and `--no-feedback`.
  - `feedback.txt` holds the agents' feedback **in full**: the newest 200 entries, with date, source, rating, prompt version, client and model, friction notes, endpoint critiques, tool suggestions and reviewer notes. It is scrubbed like every file, but it is free text by design, so a name the database never held can survive. The end of the run gives the count and asks you to read the file before moving the bundle. `--no-feedback` leaves it out.
  - `tls.txt` reports the certificate nginx actually serves: self-signed or CA-issued, expiry, SAN counts, whether it names `HOST_IP`, whether it is the file in `ssl/certs` and matches its key. It shows derived facts only, never names. The health checks use `curl -k`, so this file is the only place a certificate problem appears. The probe gives up after 10 s, and a refused or failed connection is recorded as the result; it never stops the collection.
  - `versions_and_schema.txt` also carries the **data-repair ledger** (repair names, mode, when, a row total — the table stores counts only, never a row value) and the Postgres memory settings with the two signals for changing them: the cache-hit ratio (`blks_hit` / `blks_read`) and temp-file volume (`temp_bytes`) since the last stats reset.
  - `agent_surface.txt` holds agent sessions, refused agent calls by route template, MCP tool outcomes and client handshakes, proposals by kind and status (with accept errors), evidence records (by outcome, with the size of their raw output), and feedback. It holds counts only, because a refused call never leaves a traceback. MCP client names, and the tool names callers sent, are free text, so they are harvested (not client versions: a version names no one, and one shaped like a date erased that date from every log line). Names BlueStick's own source uses (such as `claude-code` or a catalogue tool) stay readable; anything else becomes a pseudonym.
  - `request_timing.txt` summarises the backend access log per method and route template: requests, p50 and p95 request time, mean `db_ms` (time inside database statements) and `db_n` (statement count), and the number of `SLOW request` lines — sorted by total time, the top 60 routes. Only the template is shown, never the path; a request that matched no route is counted under `(unmatched route)`. Logs written by a build without those fields get one line saying so.
  - `sql_statements.txt` lists the 40 statements the database spent most time in, from `pg_stat_statements`: calls, total and mean milliseconds, rows, and the normalised text (constants are `$1`, `$2` …, so table and column names only), cut to 400 characters. Utility statements are left out and any quoted literal that remains is replaced. The script runs `CREATE EXTENSION IF NOT EXISTS pg_stat_statements` — the one thing it writes to the database. When the library is not preloaded (`shared_preload_libraries`, set in `docker-compose.yml`; a database container started before that needs recreating) or the role may not create the extension, the file holds one line saying why and the collection carries on.
- **`diag_summaries.py`** - Writes those two files for `collect-logs.sh` (stdlib only, host `python3`): `request-timing <backend log>` and `sql-statements` (rows on stdin). Not run by hand.
- **`scrub_logs.py`** - The scrubber `collect-logs.sh` runs over the bundle (stdlib only, host `python3`); it fails closed. A harvested value made only of digits and punctuation (a date, a time, a version) is never replaced, because it names no one and replacing it would blank that date or version throughout the logs. Secrets are the exception.
- **`parse-audit-agent-prompt.md`** - Bootstrap prompt for an agent auditing parse accuracy on the client network (what the operator provides, the report of redacted shapes it returns); the short version is `documentation/PARSE_AUDIT_BRIEF.md`.
- **`status.sh`** - Quick status check of the running instance: container health, the **newest backup and its age** (a warning when it is older than `BACKUP_WARN_DAYS`, default 7, or has no uploads archive), and the **data repairs not yet applied** with the command for each
- **`check.sh`** - **The gate** — one command, run before every push (there is no hosted CI): `./scripts/check.sh` or `make check`. (1) the backend suite in the **report-worker** image, with `report-templates/` mounted, which **fails if any test skipped for want of Quarto or the templates** — in the plain `backend` image those tests, the hostile-text contract among them, skip and the run is green; (2) `ruff check` over `backend/` in the same image (pyflakes rules only, `backend/ruff.toml`, `--no-cache`) — **any finding fails the gate**; (3) frontend `tsc --noEmit` and `vitest run`; (4) `test-alembic-roundtrip.sh` (every downgrade, then `alembic check`). `--fast` skips (4). Every step runs even when one fails; a one-screen summary ends the run and the exit status is non-zero on any failure. It needs the stack up and the images built, builds and pulls nothing, and sets `BLUESTICK_SKIP_DB_INIT=1`, so it cannot migrate the development database. From a git worktree it runs the worktree's source in the main checkout's compose project (`CHECK_COMPOSE_ROOT` overrides). `CHECK_PYTEST_ARGS="-k name"` passes arguments to pytest
- **`seed_demo_data.py`** - Seed a realistic demo project (hosts, scopes, findings) so the Posture hub (Posture / Segments / Patterns / Evidence) and Findings are evaluable on a fresh install. Options: `--name`, `--hosts` (default 400), `--wipe` (delete an existing project of the same name first). Runs inside the backend container.
- **`seed_eval_scenarios.py`** - A SMALL project where every host is placed on purpose, each with an "open X, expect Y" check, for evaluating a feature by eye. `--wipe` deletes only its own project and rebuilds. Runs inside the backend container.
- **`seed_acceptance_fixtures.py`** - Prepare a project for an MCP acceptance run (`documentation/MCP_ACCEPTANCE_QUESTIONS.md`) through the real API routes: four role accounts (`acc-lead`, `acc-analyst`, `acc-auditor`, `acc-viewer`), a host note thread with an image, a finding comment thread with a report image, an issued client report, and a NetExec import with uninterpreted lines. Prints analyst / auditor / viewer agent keys and writes them, with the accounts' passwords and TOTP secrets, to `uploads/acceptance-fixtures.json` (mode 0600). `--project ID` picks the project. Idempotent: a re-run reuses the accounts, mints fresh keys and skips fixtures already present. Runs inside the backend container; local development only.
- **`seed_named_assets.py`** - Layer the named-asset scenario onto that demo project (imported-but-unresolved FQDNs, domain scope, a load balancer with four vhosts, a rotated address, per-vhost findings, host tests aimed at a name and tested bindings). Goes through the real write paths. `--reset` removes only the rows a previous run created (tracked in a per-run manifest under the uploads dir) and re-seeds. Neither seed is run by `deploy.sh` — they are dev/demo-only and always manual.
- **`transfer-images.sh`** - Export/import container images for offline or air-gapped moves: the backend, frontend and report-worker images and the PostgreSQL image `docker-compose.yml` names (`postgres.tar`), so the target host pulls nothing
- **`generate-ssl-cert.sh`** / **`generate-ssl-cert-simple.sh`** - SSL certificate generators (also invoked by `deploy.sh` during first-time setup). They make a self-signed certificate; for one signed by your own root CA, see `ca/local-ca.sh` and [`ca/README.md`](../ca/README.md). `deploy.sh` option 3 (Reconfigure IP) regenerates a self-signed certificate but keeps a CA-issued one, and refuses (changing nothing) when a CA-issued certificate does not name the new address.
- **`postgres/init-ssl.sh`** / **`postgres/ensure-ssl.sh`** - PostgreSQL TLS setup: `init-ssl.sh` is mounted into the `db` container's init directory by compose; `deploy.sh` runs `ensure-ssl.sh` inside `db` after start.
- **`rdap-lookup.py`** - Bulk RDAP lookup that writes a file you upload like any scanner output (the RDAP parser turns it into per-host registration attribution). Runs wherever you have connectivity, outside BlueStick on purpose — the server makes no network queries.

### Database Scripts

The schema is owned exclusively by **Alembic** — every backend container runs
`alembic upgrade head` on boot. There is no manual "create tables" step; never
run `Base.metadata.create_all` against a live database (it bypasses Alembic
version tracking).

- **`backup-db.sh`** - Back up the database: logical `pg_dump` (custom format), or a raw volume snapshot if Postgres is down. A logical dump carries the `pg_trgm` extension + all indexes. It also archives `uploads/` (evidence images, issued reports, screenshots) as `nm-uploads-<timestamp>.tar.gz`. Backups go to a sibling `<project>-db-backups` directory next to the project folder, so replacing the folder does not wipe them; override with `BACKUP_DIR=/path`.
  - **Success means read back.** The dump is listed with `pg_restore --list` (a truncated dump has no table of contents) and each tar archive is listed to its end before the script reports success; a `.meta` line records it.
  - **A failed uploads archive is a failed backup** (exit 1, the database artifact kept): the dump alone restores rows that point at files which are gone. `--allow-missing-uploads` (or `BACKUP_ALLOW_MISSING_UPLOADS=1`) accepts a database-only backup; `deploy.sh`'s pre-deploy gate honours the same variable.
  - **Private files.** `umask 077`; the backup directory is set to mode 700 and every artifact to 0600, owned by the user who ran the script — the archives are streamed out of the helper container and written by the shell, so nothing is root-owned and no `sudo` is needed. (Archives made before this are root-owned and world-readable inside a directory that is now 700; retention removes them as they age out.)
  - **No image to pull.** `tar` runs in the database's own image (the `db` container's, else the one `docker-compose.yml` names), never `alpine`.
  - **Retention.** After a successful backup the newest `BACKUP_KEEP` backups are kept (default 10; `0` keeps all), each with its `.meta` and uploads archive; what was pruned and the free space left are printed. Never pruned: the backup just made, and the pre-deploy dump option 7 would restore. A **backlog** — more than `BACKUP_PRUNE_MAX` (default 3) to delete in one run, as on the first run over a directory that was never pruned — is reported and **not** deleted; `BACKUP_PRUNE_BACKLOG=1 ./scripts/backup-db.sh` deletes it once, deliberately.
- **`restore-db.sh`** - Restore a `backup-db.sh` artifact — the database and its matching `uploads/` archive. **The backup is read back first** (`pg_restore --list` for a dump, a tar listing for a snapshot and the uploads archive): an unreadable file is refused with the current database untouched. It then stops the app containers (keeping `db` up), takes a safety backup of the current database (`--no-safety-backup` to skip; no retention pass, so the backup being restored cannot be pruned) and checks the credential-encryption key matches (`--ignore-key-mismatch` to skip). `tar` runs in the database's own image. The backend's boot-time `alembic upgrade head` then migrates the restored schema forward.
- **`data_repairs.py`** - Show the **data-repair ledger** (table `data_repairs`): the one-off corrections of rows written by older versions, and which of them this instance has run. Read-only. `docker compose exec backend python scripts/data_repairs.py [--pending] [--json]`. `deploy.sh` (after a healthy start) and `status.sh` print the pending ones with the exact command. Nothing runs a repair automatically: the two below stay operator actions, and each records its own run. A run limited to `--project` is not recorded — it does not settle the repair for the instance. A new database (no hosts when the ledger is created) is recorded as needing neither.
- **`backfill_misconfigs.py`** - Record misconfiguration-catalog observations from evidence already stored, for imports made before v2.414.0. Idempotent. `docker compose exec backend python scripts/backfill_misconfigs.py [--project ID]`. A run over every project is recorded in the data-repair ledger. Run it when no import is in progress (it writes through the same application-level de-duplication an import uses).
- **`repair_netexec_results.py`** - Correct NetExec rows stored by older parser rules: before v2.428.4, command and module results (`[-] ERROR(…)`, `[+] Executed command…`) stored as logins, and hosts named "None"; before v2.430.1, the NFS mount daemon's ports stored as an "nfs" service (renamed `mountd` only when NetExec alone named them — never 2049, an nmap `-sV` identification or another tool's "nfs"). A dry run unless `--apply`; idempotent. `--project ID` is the numeric project id (`SELECT id, name FROM projects;`); without it every project is checked. `docker compose exec backend python scripts/repair_netexec_results.py [--project ID] [--apply]`. The data-repair ledger records it when it is settled for the whole instance: an `--apply` over every project, or a dry run over every project that found nothing to correct.
- **`test-alembic-roundtrip.sh`** - Pre-release sanity check (run it locally — there is no hosted CI; `check.sh` runs it as its last step): spins up a throwaway Postgres — the image `docker-compose.yml` names, or the running `db` container's when that tag is not on the host — and verifies every migration's `downgrade()` reverses cleanly (upgrade → downgrade → upgrade), then `alembic check`.
- **`apply_scope_labels.py`** - Bulk-assign subnet labels to a project's scope from a CSV (CIDR column + label column). Runs inside the backend container, matches CIDRs to existing subnets, find-or-creates each label, and assigns it. Idempotent and **dry-run by default** (pass `--apply` to write):
  ```bash
  docker compose exec backend python /app/scripts/apply_scope_labels.py \
      --project-id <ID> --csv /app/scripts/labels.csv          # dry run
  docker compose exec backend python /app/scripts/apply_scope_labels.py \
      --project-id <ID> --csv /app/scripts/labels.csv --apply  # write
  ```
  Columns default to CIDR=0, label=1, comma-delimited (override with `--cidr-col` / `--label-col` / `--delimiter`); `--color <palette>` colors any labels it creates. The dry run lists unmatched CIDRs so you can validate before applying.

## User Management

A default admin account is **created automatically** on first application boot when no admin user exists in the database.

### Default Admin Credentials

On first startup, when `DEFAULT_ADMIN_PASSWORD` is unset, the backend generates a secure random password and **writes it to a file** (it is **not** logged):

```
./uploads/initial-admin-password.txt   (mode 0600)
```

Read it there, log in as `admin`, and change it immediately. Startup **fails closed** if that volume isn't writable — set `DEFAULT_ADMIN_PASSWORD` (or fix the `uploads` volume) rather than ending up with an unrecoverable admin. The file is removed once the password is changed.

> **2FA:** `REQUIRE_2FA` defaults to `true`, so the first login walks the admin through TOTP enrolment. Set `REQUIRE_2FA=false` in `.env` to make it opt-in.

### Controlling Default Admin via Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DEFAULT_ADMIN_USERNAME` | `admin` | Initial admin username |
| `DEFAULT_ADMIN_PASSWORD` | *(random → `uploads/initial-admin-password.txt`)* | Initial admin password |

### Creating Additional Users

Use the admin web UI at **Administration → System** (`/system-settings`) or the API:

```bash
# Via API (requires admin JWT token; nginx proxies /api on :443)
curl -k -X POST https://localhost/api/v1/auth/register \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username": "analyst1", "email": "analyst@example.com", "password": "SecurePass123!"}'
```

## User Roles

There are two layers. The **global** role is binary — **admin** (user management, system settings, the audit log) or **member** — and gates nothing else. Everything else is a **per-project** role (a user can be analyst on one project, viewer on another):

- **ADMIN**: Manage project membership, plus everything an analyst can do
- **ANALYST**: Scope management, scan upload, triage, host tests, notes; an agent session started by an analyst can write
- **AUDITOR**: Read-only access with audit-log visibility, exports and reports; can start a read-only agent session
- **VIEWER**: Read-only access to scan results and dashboards

## Password Requirements

Passwords must meet these criteria:
- At least 12 characters long
- Contains uppercase letters
- Contains lowercase letters
- Contains numbers
- Contains special characters (!@#$%^&*()_+-=[]{}|;:,.<>?)

## Troubleshooting

### Database Connection Issues
```bash
# Check container status
docker compose ps

# Check logs (note: the initial admin password is NOT here — it's in
# ./uploads/initial-admin-password.txt)
docker compose logs backend

# Force restart
docker compose restart backend db
```

## Script Dependencies

- **Inside the backend container** (the seeds, `apply_scope_labels.py`, `backfill_misconfigs.py`, `repair_netexec_results.py`): the running stack — `scripts/` is bind-mounted at `/app/scripts`.
- **Stack management** (`deploy.sh`, `status.sh`, `backup-db.sh`, `restore-db.sh`, `upgrade-instance.sh`, `transfer-images.sh`, `test-alembic-roundtrip.sh`, `check.sh` — which also needs Node for the frontend step): Docker and Docker Compose on the host. `collect-logs.sh` also needs `python3`.
- **Host-side helpers** (`rdap-lookup.py`, the certificate generators): neither the stack nor a database.
