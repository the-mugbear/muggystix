# BlueStick Scripts

This directory contains utility scripts for deployment and maintenance.

## Scripts Overview

### Deployment Scripts

- **`deploy.sh`** - **Main deployment script** with all deployment options
  - Local development deployment
  - Network production deployment
  - Test instance deployment
  - Nuclear clean rebuild

### Maintenance Scripts

- **`collect-logs.sh`** - Comprehensive log collection with authentication debugging
- **`status.sh`** - Quick status check for all instances
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

On first startup, the backend logs will display the generated admin credentials:

```
============================================================
DEFAULT ADMIN ACCOUNT CREATED
  Username: admin
  Password: <randomly generated>
  *** CHANGE THIS PASSWORD IMMEDIATELY ***
============================================================
```

### Controlling Default Admin via Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DEFAULT_ADMIN_USERNAME` | `admin` | Initial admin username |
| `DEFAULT_ADMIN_EMAIL` | `admin@networkmapper.local` | Initial admin email |
| `DEFAULT_ADMIN_PASSWORD` | *(random)* | Initial admin password |

If `DEFAULT_ADMIN_PASSWORD` is not set, a secure random password is generated and logged to stdout on first boot.

### Creating Additional Users

Use the admin web UI at **System Settings** or the API:

```bash
# Via API (requires admin JWT token)
curl -X POST https://localhost:8000/api/v1/auth/register \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"username": "analyst1", "email": "analyst@example.com", "password": "SecurePass123!"}'
```

## User Roles

- **ADMIN**: Full system access, user management, configuration
- **ANALYST**: Risk assessments, detailed analysis, report generation
- **AUDITOR**: Read access + export capabilities for compliance
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

# Check logs (admin password is printed here on first boot)
docker compose logs backend

# Force restart
docker compose restart backend db
```

## Script Dependencies

All scripts require:
- Docker and Docker Compose
- Running BlueStick containers
- PostgreSQL database connectivity
