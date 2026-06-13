# BlueStick Scripts

This directory contains utility scripts for deployment and maintenance.

## Scripts Overview

### Deployment Scripts

- **`deploy.sh`** - **Main deployment script**; interactive menu with five options:
  1. Start / Rebuild
  2. First-time setup (generate `.env` + SSL certs + start)
  3. Reconfigure IP address
  4. Nuclear clean (destroy ALL data and rebuild)
  5. Security status check

### Maintenance Scripts

- **`collect-logs.sh`** - Comprehensive log collection with authentication debugging
- **`status.sh`** - Quick status check for all instances
- **`seed_demo_data.py`** - Seed a realistic demo project (hosts, scopes, findings) so Posture/Insights/Findings are evaluable on a fresh install. Runs inside the backend container.
- **`transfer-images.sh`** - Export/import container images for offline or air-gapped moves
- **`preflight.sh`** - Environment probe helper used by the agentic recon workflow
- **`generate-ssl-cert.sh`** / **`generate-ssl-cert-simple.sh`** - SSL certificate generators (also invoked by `deploy.sh` during first-time setup)

### Database Scripts

The schema is owned exclusively by **Alembic** — every backend container runs
`alembic upgrade head` on boot. There is no manual "create tables" step; never
run `Base.metadata.create_all` against a live database (it bypasses Alembic
version tracking).

- **`backup-db.sh`** - Back up the database: logical `pg_dump` (custom format), or a raw volume snapshot if Postgres is down. A logical dump carries the `pg_trgm` extension + all indexes.
- **`restore-db.sh`** - Restore from a `backup-db.sh` artifact, then run `alembic upgrade head` to bring the schema to the current revision.
- **`test-alembic-roundtrip.sh`** - CI/pre-release sanity check: spins up a throwaway Postgres and verifies every migration's `downgrade()` reverses cleanly (upgrade → downgrade → upgrade).
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

Use the admin web UI at **System Settings** or the API:

```bash
# Via API (requires admin JWT token; nginx proxies /api on :443)
curl -k -X POST https://localhost/api/v1/auth/register \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username": "analyst1", "email": "analyst@example.com", "password": "SecurePass123!"}'
```

## User Roles

Roles are assigned **per project** (a user can be analyst on one project, viewer on another):

- **ADMIN**: Full system access, user management, configuration
- **ANALYST**: Scope management, scan upload, test-plan approval, notes, starting agent sessions
- **AUDITOR**: Read-only access with audit-log visibility
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

All scripts require:
- Docker and Docker Compose
- Running BlueStick containers
- PostgreSQL database connectivity
