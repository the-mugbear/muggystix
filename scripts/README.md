# BlueStick Scripts

This directory contains utility scripts for deployment and maintenance.

## Scripts Overview

### Deployment Scripts

- **`deploy.sh`** - **Main deployment script**; interactive menu with seven options:
  1. Start / Rebuild — takes a pre-deploy database backup first (and aborts if it cannot), snapshots the current images for option 7, then rebuilds
  2. First-time setup (generate `.env` + SSL certs + start)
  3. Reconfigure IP address
  4. Nuclear clean (destroy ALL data and rebuild)
  5. Security status check
  6. Back up `.env` + SSL to the parent folder (before a re-copy deploy)
  7. Roll back to the previous build (the images snapshotted before the last option-1 deploy, with an optional DB restore)
- **`upgrade-instance.sh`** - Upgrade a file-copy deployment (no git on the host). Precondition, done by hand first: rename the running instance's folder to `<name>_backup_<date>`, copy the new source tree next to it and rename it to the **original** folder name (docker compose keys the database volume on the folder name — `<name>_postgres_data`). Then, from the new folder, `./scripts/upgrade-instance.sh`: it verifies that volume exists (refuses otherwise, `--allow-fresh-db` to override), carries `.env`, `ssl/certs/*`, the uploads dir (moved; `--copy-uploads` to copy), a custom `NGINX_CONFIG`, `.deploy-rollback-state` and the report-template images each `template.json` declares (a logo…) across, reports `.env` keys the new `.env.example` adds and any top-level files only the old folder has, then runs `deploy.sh` option 1 and checks the API's reported version against `platform_version.json`. Idempotent: identical files are skipped, differing ones abort rather than overwrite. The exception is a CA-issued certificate already installed in the new folder (`ca/local-ca.sh install` before the upgrade): it is kept, and the old instance's certificate is not carried. `--no-deploy` stops before the deploy, `--from <dir>` names the old folder when auto-detection is ambiguous, `--yes` answers yes to every confirmation.

### Maintenance Scripts

- **`collect-logs.sh`** - Anonymised diagnostics bundle (container logs, ingestion queue, parser audit, agent feedback), safe to share once its feedback has been read. Options: `--since 72h`, `--terms FILE` (extra names to remove) and `--no-feedback`.
  - `feedback.txt` holds the agents' feedback **in full**: the newest 200 entries, with date, source, rating, prompt version, client and model, friction notes, endpoint critiques, tool suggestions and reviewer notes. It is scrubbed like every file, but it is free text by design, so a name the database never held can survive. The end of the run gives the count and asks you to read the file before moving the bundle. `--no-feedback` leaves it out.
  - `tls.txt` reports the certificate nginx actually serves: self-signed or CA-issued, expiry, SAN counts, whether it names `HOST_IP`, whether it is the file in `ssl/certs` and matches its key. It shows derived facts only, never names. The health checks use `curl -k`, so this file is the only place a certificate problem appears. The probe gives up after 10 s, and a refused or failed connection is recorded as the result; it never stops the collection.
  - `agent_surface.txt` holds agent sessions, refused agent calls by route template, MCP tool outcomes and client handshakes, proposals by kind and status (with accept errors), evidence records (by outcome, with the size of their raw output), and feedback. It holds counts only, because a refused call never leaves a traceback. MCP client names, and the tool names callers sent, are free text, so they are harvested (not client versions: a version names no one, and one shaped like a date erased that date from every log line). Names BlueStick's own source uses (such as `claude-code` or a catalogue tool) stay readable; anything else becomes a pseudonym.
- **`scrub_logs.py`** - The scrubber `collect-logs.sh` runs over the bundle (stdlib only, host `python3`); it fails closed. A harvested value made only of digits and punctuation (a date, a time, a version) is never replaced, because it names no one and replacing it would blank that date or version throughout the logs. Secrets are the exception.
- **`parse-audit-agent-prompt.md`** - Bootstrap prompt for an agent auditing parse accuracy on the client network (what the operator provides, the report of redacted shapes it returns); the short version is `documentation/PARSE_AUDIT_BRIEF.md`.
- **`status.sh`** - Quick status check of the running instance
- **`seed_demo_data.py`** - Seed a realistic demo project (hosts, scopes, findings) so the Posture hub (Posture / Segments / Patterns / Evidence) and Findings are evaluable on a fresh install. Options: `--name`, `--hosts` (default 400), `--wipe` (delete an existing project of the same name first). Runs inside the backend container.
- **`seed_eval_scenarios.py`** - A SMALL project where every host is placed on purpose, each with an "open X, expect Y" check, for evaluating a feature by eye. `--wipe` deletes only its own project and rebuilds. Runs inside the backend container.
- **`seed_acceptance_fixtures.py`** - Prepare a project for an MCP acceptance run (`documentation/MCP_ACCEPTANCE_QUESTIONS.md`) through the real API routes: four role accounts (`acc-lead`, `acc-analyst`, `acc-auditor`, `acc-viewer`), a host note thread with an image, a finding comment thread with a report image, an issued client report, and a NetExec import with uninterpreted lines. Prints analyst / auditor / viewer agent keys and writes them, with the accounts' passwords and TOTP secrets, to `uploads/acceptance-fixtures.json` (mode 0600). `--project ID` picks the project. Idempotent: a re-run reuses the accounts, mints fresh keys and skips fixtures already present. Runs inside the backend container; local development only.
- **`seed_named_assets.py`** - Layer the named-asset scenario onto that demo project (imported-but-unresolved FQDNs, domain scope, a load balancer with four vhosts, a rotated address, per-vhost findings, named plan entries and tested bindings). Goes through the real write paths. `--reset` removes only the rows a previous run created (tracked in a per-run manifest under the uploads dir) and re-seeds. Neither seed is run by `deploy.sh` — they are dev/demo-only and always manual.
- **`transfer-images.sh`** - Export/import container images for offline or air-gapped moves
- **`generate-ssl-cert.sh`** / **`generate-ssl-cert-simple.sh`** - SSL certificate generators (also invoked by `deploy.sh` during first-time setup). They make a self-signed certificate; for one signed by your own root CA, see `ca/local-ca.sh` and [`ca/README.md`](../ca/README.md). `deploy.sh` option 3 (Reconfigure IP) regenerates a self-signed certificate but keeps a CA-issued one, and refuses (changing nothing) when a CA-issued certificate does not name the new address.
- **`postgres/init-ssl.sh`** / **`postgres/ensure-ssl.sh`** - PostgreSQL TLS setup: `init-ssl.sh` is mounted into the `db` container's init directory by compose; `deploy.sh` runs `ensure-ssl.sh` inside `db` after start.
- **`rdap-lookup.py`** - Bulk RDAP lookup that writes a file you upload like any scanner output (the RDAP parser turns it into per-host registration attribution). Runs wherever you have connectivity, outside BlueStick on purpose — the server makes no network queries.

### Database Scripts

The schema is owned exclusively by **Alembic** — every backend container runs
`alembic upgrade head` on boot. There is no manual "create tables" step; never
run `Base.metadata.create_all` against a live database (it bypasses Alembic
version tracking).

- **`backup-db.sh`** - Back up the database: logical `pg_dump` (custom format), or a raw volume snapshot if Postgres is down. A logical dump carries the `pg_trgm` extension + all indexes. It also archives `uploads/` (evidence images, issued reports, screenshots) as `nm-uploads-<timestamp>.tar.gz`. Backups go to a sibling `<project>-db-backups` directory next to the project folder, so replacing the folder does not wipe them; override with `BACKUP_DIR=/path`.
- **`restore-db.sh`** - Restore a `backup-db.sh` artifact — the database and its matching `uploads/` archive. It stops the app containers (keeping `db` up), takes a safety backup of the current database first (`--no-safety-backup` to skip) and checks the credential-encryption key matches (`--ignore-key-mismatch` to skip). The backend's boot-time `alembic upgrade head` then migrates the restored schema forward.
- **`backfill_misconfigs.py`** - Record misconfiguration-catalog observations from evidence already stored, for imports made before v2.414.0. Idempotent. `docker compose exec backend python scripts/backfill_misconfigs.py [--project ID]`.
- **`repair_netexec_results.py`** - Correct NetExec rows stored by older parser rules: before v2.428.4, command and module results (`[-] ERROR(…)`, `[+] Executed command…`) stored as logins, and hosts named "None"; before v2.430.1, the NFS mount daemon's ports stored as an "nfs" service (renamed `mountd` only when NetExec alone named them — never 2049, an nmap `-sV` identification or another tool's "nfs"). A dry run unless `--apply`; idempotent. `--project ID` is the numeric project id (`SELECT id, name FROM projects;`); without it every project is checked. `docker compose exec backend python scripts/repair_netexec_results.py [--project ID] [--apply]`.
- **`test-alembic-roundtrip.sh`** - Pre-release sanity check (run it locally — there is no hosted CI): spins up a throwaway Postgres and verifies every migration's `downgrade()` reverses cleanly (upgrade → downgrade → upgrade).
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
- **ANALYST**: Scope management, scan upload, triage, test plans, notes; an agent session started by an analyst can write
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
- **Stack management** (`deploy.sh`, `status.sh`, `backup-db.sh`, `restore-db.sh`, `upgrade-instance.sh`, `transfer-images.sh`, `test-alembic-roundtrip.sh`): Docker and Docker Compose on the host. `collect-logs.sh` also needs `python3`.
- **Host-side helpers** (`rdap-lookup.py`, the certificate generators): neither the stack nor a database.
