#!/bin/bash

#
# This script collects logs and debugging information from the BlueStick application.
#
# What it does:
# - Creates a timestamped directory to store the collected information.
# - Gathers system information, network configuration, and environment files.
# - Collects Docker container status, image metadata, and service version details.
# - Retrieves ingestion job metadata and recent parse errors from the database.
# - Collects logs from the backend, frontend, and database containers.
# - Performs application health checks and analyzes error patterns.
# - Gathers Git repository information and file system details.
# - Collects authentication and audit logs, and provides browser storage information.
# - Creates a summary file and a compressed archive of the collected data.
#
# How it does it:
# - Uses a series of shell commands to collect information and save it to text files.
# - Interacts with Docker and docker-compose to get container information and logs.
# - Connects to the database to query for ingestion job and parse error data.
# - Performs health checks by making HTTP requests to the backend and frontend.
# - Analyzes logs for common error patterns.
# - Creates a compressed tarball of the collected logs for easy sharing.
#

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

print_info() { echo -e "${BLUE}📋 $1${NC}"; }
print_success() { echo -e "${GREEN}✅ $1${NC}"; }
print_error() { echo -e "${RED}❌ $1${NC}"; }
print_warning() { echo -e "${YELLOW}⚠️  $1${NC}"; }

# Detect which Docker Compose form is available.  Modern hosts ship
# only the V2 plugin (`docker compose`); the legacy V1 binary
# (`docker-compose`) is gone.  Without this wrapper every collection
# step that hardcoded `docker-compose` silently failed and produced
# empty log files — exactly the symptom operators report when
# troubleshooting a broken deployment.
COMPOSE_CMD=()
COMPOSE_FORM="unavailable"
if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD=(docker compose)
    COMPOSE_FORM="docker compose (V2 plugin)"
elif command -v docker-compose >/dev/null 2>&1 && docker-compose --version >/dev/null 2>&1; then
    COMPOSE_CMD=(docker-compose)
    COMPOSE_FORM="docker-compose (V1 binary)"
fi

compose() {
    if [[ ${#COMPOSE_CMD[@]} -eq 0 ]]; then
        echo "ERROR: no docker compose binary available on PATH" >&2
        return 127
    fi
    "${COMPOSE_CMD[@]}" "$@"
}

compose_available() {
    [[ ${#COMPOSE_CMD[@]} -gt 0 ]]
}

# Load optional environment configuration for database credential overrides
if [[ -f ".env" ]]; then
    # shellcheck disable=SC1091
    source .env
fi
DB_NAME=${POSTGRES_DB:-networkMapper}
DB_USER=${POSTGRES_USER:-nmapuser}

# Create timestamp for the log collection
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
LOG_DIR="troubleshooting_logs_$TIMESTAMP"

print_info "Starting BlueStick log collection..."
print_info "Creating log directory: $LOG_DIR"
mkdir -p "$LOG_DIR"

# System Information
print_info "Collecting system information..."
{
    echo "=== SYSTEM INFORMATION ==="
    echo "Date: $(date)"
    echo "Host: $(hostname)"
    echo "OS: $(uname -a)"
    echo "Docker Version: $(docker --version 2>/dev/null || echo 'Docker not found')"
    echo "Docker Compose Form Detected: ${COMPOSE_FORM}"
    if compose_available; then
        echo "Docker Compose Version: $(compose version 2>&1 | head -n 1)"
    else
        echo "Docker Compose Version: NOT FOUND on PATH — neither 'docker compose' nor 'docker-compose' works"
    fi
    echo "Current Directory: $(pwd)"
    echo "User: $(whoami)"
    echo ""
} > "$LOG_DIR/system_info.txt"

# Network Configuration
print_info "Collecting network configuration..."
{
    echo "=== NETWORK CONFIGURATION ==="
    echo "IP Addresses:"
    ip addr show 2>/dev/null || ifconfig 2>/dev/null || echo "Network info not available"
    echo ""
    echo "Routing Table:"
    ip route show 2>/dev/null || route -n 2>/dev/null || echo "Route info not available"
    echo ""
    echo "DNS Configuration:"
    cat /etc/resolv.conf 2>/dev/null || echo "DNS config not available"
    echo ""
} > "$LOG_DIR/network_info.txt"

# Environment Files
print_info "Collecting environment configuration..."
{
    echo "=== ENVIRONMENT CONFIGURATION ==="
    echo "Contents of .env (secrets redacted):"
    if [[ -f ".env" ]]; then
        sed -E \
            -e 's/(SECRET_KEY=).*/\1[REDACTED]/' \
            -e 's/(PASSWORD=).*/\1[REDACTED]/' \
            -e 's/(TOKEN=).*/\1[REDACTED]/' \
            .env
    else
        echo "File not found"
    fi
    echo ""
    echo "Contents of docker-compose.yml:"
    if [[ -f "docker-compose.yml" ]]; then
        cat docker-compose.yml
    else
        echo "File not found"
    fi
    echo ""
} > "$LOG_DIR/environment_config.txt"

# Docker Status
print_info "Collecting Docker container status..."
{
    echo "=== DOCKER CONTAINER STATUS ==="
    echo "Running containers:"
    docker ps 2>/dev/null || echo "Failed to get container status"
    echo ""
    echo "All containers:"
    docker ps -a 2>/dev/null || echo "Failed to get all containers"
    echo ""
    echo "Docker images:"
    docker images 2>/dev/null || echo "Failed to get images"
    echo ""
    echo "Docker networks:"
    docker network ls 2>/dev/null || echo "Failed to get networks"
    echo ""
    echo "Docker volumes:"
    docker volume ls 2>/dev/null || echo "Failed to get volumes"
    echo ""
} > "$LOG_DIR/docker_status.txt"

# Container image metadata (version tracking)
print_info "Collecting container image metadata..."
{
    echo "=== CONTAINER IMAGES ==="
    echo "Compose form: ${COMPOSE_FORM}"
    if compose_available; then
        compose images 2>&1 || echo "compose images returned non-zero"
    else
        docker images 2>&1 || echo "Failed to list docker images"
    fi

    # Include exited containers via -a so failed deployments still
    # surface their image metadata.
    for service in backend frontend db worker; do
        container_id=""
        if compose_available; then
            container_id=$(compose ps -a -q "$service" 2>/dev/null | head -n 1)
        fi
        if [[ -n "$container_id" ]]; then
            echo ""
            echo "--- $service ($container_id) ---"
            docker inspect --format 'Image: {{.Config.Image}}' "$container_id" 2>&1 || echo "Unable to inspect image"
            docker inspect --format 'ImageID: {{.Image}}' "$container_id" 2>&1 || true
            docker inspect --format 'Created: {{.Created}}' "$container_id" 2>&1 || true
            docker inspect --format 'State: {{.State.Status}} (exit={{.State.ExitCode}}, restartCount={{.RestartCount}})' "$container_id" 2>&1 || true
            docker inspect --format 'RepoDigests:\n{{range $idx, $digest := .RepoDigests}}  - {{$digest}}\n{{else}}  (none){{end}}' "$container_id" 2>&1 || true
        else
            echo ""
            echo "--- $service ---"
            echo "(no container — never started or already removed)"
        fi
    done
} > "$LOG_DIR/container_images.txt"

# Service runtime metadata (application-reported versions)
print_info "Collecting service version metadata..."
{
    echo "=== SERVICE VERSION DETAILS ==="

    backend_container=""
    if compose_available; then
        backend_container=$(compose ps -q backend 2>/dev/null | head -n 1)
    fi

    if [[ -n "$backend_container" ]]; then
        echo "--- deployed versions (backend root '/') ---"
        # The backend root returns {version, frontend_version, instance_id} — the
        # authoritative deployed-version signal for BOTH services.  The frontend
        # is a minified Vite build (no grep-able REACT_APP_* strings, no
        # build-info.json served), so the deployed frontend version is taken from
        # the FRONTEND_VERSION build-arg the backend reports (which is what the
        # app's VersionFooter renders).  The backend image ships curl (the
        # Dockerfile HEALTHCHECK uses it).
        compose exec -T backend curl -fsS http://localhost:8000/ 2>&1 || echo "backend root '/' unreachable"
        echo
        echo "--- backend settings ---"
        (compose exec -T backend python - <<'PY'
from app.main import app
try:
    from app.core.config import settings
except Exception:
    settings = None

print(f"fastapi_app_version: {getattr(app, 'version', 'unknown')}")
if settings is not None:
    print(f"nessus_commit_batch_size: {getattr(settings, 'NESSUS_COMMIT_BATCH_SIZE', 'unknown')}")
    print(f"nessus_plugin_output_max_chars: {getattr(settings, 'NESSUS_PLUGIN_OUTPUT_MAX_CHARS', 'unknown')}")
PY
        ) 2>&1 || echo "Unable to query backend settings (container may be unhealthy)"
    else
        echo "--- backend ---"
        echo "backend container not running"
    fi
} > "$LOG_DIR/service_versions.txt"

# Ingestion job metadata (for background parser debugging)
print_info "Collecting ingestion job metadata..."
db_container_id=""
if compose_available; then
    db_container_id=$(compose ps -q db 2>/dev/null | head -n 1)
fi
if [[ -n "$db_container_id" ]] && docker inspect --format '{{.State.Running}}' "$db_container_id" 2>/dev/null | grep -q true; then
    {
        echo "=== INGESTION JOBS (LAST 25) ==="
        compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" \
            -c "SELECT id, original_filename, status, tool_name, file_size, progress, message, error_message, parse_error_id, retry_count, created_at, started_at, completed_at, last_heartbeat FROM ingestion_jobs ORDER BY created_at DESC LIMIT 25;" 2>&1 || echo "Failed to query ingestion_jobs"

        echo ""
        echo "=== STUCK / LONG-RUNNING JOBS ==="
        compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" \
            -c "SELECT id, original_filename, status, progress, started_at, last_heartbeat, EXTRACT(EPOCH FROM (NOW() - COALESCE(last_heartbeat, started_at)))::int AS secs_since_heartbeat, EXTRACT(EPOCH FROM (NOW() - started_at))::int AS total_elapsed_secs FROM ingestion_jobs WHERE status = 'processing' ORDER BY started_at;" 2>&1 || echo "Failed to query stuck jobs"

        echo ""
        echo "=== FAILED JOB TRACEBACKS (LAST 10 — full last_error column) ==="
        # last_error stores the trimmed traceback when _run_job's unexpected-exception
        # path fires (e.g. PendingRollbackError chains).  These cases don't write a
        # parse_errors row because the session is already poisoned by the time the
        # error handler runs, so the traceback in last_error is the only artifact.
        # Use -A to render each row as key=value blocks so long tracebacks aren't
        # truncated to terminal width.
        compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" -A -F $'\t' \
            -c "\\x on" \
            -c "SELECT id, original_filename, status, retry_count, error_message, last_error, completed_at FROM ingestion_jobs WHERE last_error IS NOT NULL OR status = 'failed' ORDER BY created_at DESC LIMIT 10;" 2>&1 || echo "Failed to query failed job tracebacks"

        echo ""
        echo "=== RECENT PARSE ERRORS (LAST 25) ==="
        compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" \
            -c "SELECT id, filename, file_type, error_type, error_message, user_message, status, created_at FROM parse_errors ORDER BY created_at DESC LIMIT 25;" 2>&1 || echo "Failed to query parse_errors"
    } > "$LOG_DIR/ingestion_jobs.txt"
else
    {
        echo "=== INGESTION JOBS — DATABASE UNAVAILABLE ==="
        echo "Database container is not running.  Cannot query ingestion_jobs / parse_errors."
        echo "Compose form: ${COMPOSE_FORM}"
        if [[ -n "$db_container_id" ]]; then
            echo "DB container exists but is not in 'Running' state:"
            docker inspect --format 'state={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} startedAt={{.State.StartedAt}} finishedAt={{.State.FinishedAt}}' "$db_container_id" 2>&1 || true
        else
            echo "No db container ID could be resolved via compose ps."
        fi
    } > "$LOG_DIR/ingestion_jobs.txt"
fi

# Database schema & migration state.  The single most useful signal when a
# feature "isn't there" after a deploy: if alembic `current` != `heads`, a
# migration didn't apply (the boot upgrade failed), which explains schema-shaped
# 500s, missing-column errors, and absent pg_trgm/evidence-search behaviour.
print_info "Collecting database schema & migration state..."
{
    echo "=== ALEMBIC MIGRATION STATE ==="
    backend_container_id=""
    if compose_available; then
        backend_container_id=$(compose ps -q backend 2>/dev/null | head -n 1)
    fi
    if [[ -n "$backend_container_id" ]] && docker inspect --format '{{.State.Running}}' "$backend_container_id" 2>/dev/null | grep -q true; then
        echo "--- alembic current (revision stamped in the DB) ---"
        compose exec -T backend sh -c 'cd /app && alembic current' 2>&1 || echo "Failed to read alembic current"
        echo ""
        echo "--- alembic heads (revision the deployed code expects) ---"
        compose exec -T backend sh -c 'cd /app && alembic heads' 2>&1 || echo "Failed to read alembic heads"
        echo ""
        echo "NOTE: if 'current' != 'heads', a migration did not apply on boot — this"
        echo "is the most likely root cause of schema-shaped errors. Check backend logs"
        echo "for the alembic upgrade failure."
    else
        echo "backend container not running — cannot read Alembic state"
    fi

    echo ""
    if [[ -n "$db_container_id" ]] && docker inspect --format '{{.State.Running}}' "$db_container_id" 2>/dev/null | grep -q true; then
        echo "=== POSTGRES EXTENSIONS (pg_trgm backs /hosts evidence search) ==="
        compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" \
            -c "SELECT extname, extversion FROM pg_extension ORDER BY extname;" 2>&1 || echo "Failed to query pg_extension"

        echo ""
        echo "=== TRIGRAM (GIN) INDEXES ==="
        compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" \
            -c "SELECT indexname, tablename FROM pg_indexes WHERE indexdef ILIKE '%gin_trgm_ops%' OR indexname LIKE 'ix_trgm%' ORDER BY indexname;" 2>&1 || echo "Failed to query trgm indexes"

        echo ""
        echo "=== KEY TABLE ROW COUNTS ==="
        compose exec -T db psql -U "$DB_USER" -d "$DB_NAME" \
            -c "SELECT 'hosts_v2' AS table, count(*) FROM hosts_v2 UNION ALL SELECT 'subnets', count(*) FROM subnets UNION ALL SELECT 'subnet_labels', count(*) FROM subnet_labels UNION ALL SELECT 'subnet_label_assignments', count(*) FROM subnet_label_assignments UNION ALL SELECT 'host_query_history', count(*) FROM host_query_history UNION ALL SELECT 'vulnerabilities', count(*) FROM vulnerabilities;" 2>&1 || echo "Failed to query table counts"
    else
        echo "Database container not running — cannot query schema/extension state."
    fi
} > "$LOG_DIR/db_schema.txt"

# Container Logs
# Always attempt log collection when compose is available — `compose
# logs` returns crash output from EXITED containers too, which is the
# whole point of debugging a failed deployment.  The previous gate
# (`docker-compose ps -q`) hid these logs whenever the legacy binary
# was missing OR every container was already dead.
print_info "Collecting container logs..."
if compose_available; then
    collect_service_logs() {
        local service="$1"
        local outfile="$2"
        local label="$3"

        {
            echo "=== ${label} ==="
            echo "Compose form: ${COMPOSE_FORM}"
            # `compose logs --no-color` works for both running and
            # exited containers as long as the container record still
            # exists.  2>&1 captures compose's own error output
            # (e.g. "service not found") into the file so empty logs
            # are never mysterious.
            if ! compose logs --no-color --tail=all --timestamps "$service" 2>&1; then
                echo ""
                echo "[collect-logs] compose logs '$service' exited non-zero — see above."
            fi

            local cid
            cid=$(compose ps -a -q "$service" 2>/dev/null | head -n 1)
            if [[ -n "$cid" ]]; then
                echo ""
                echo "--- container state ($cid) ---"
                docker inspect --format 'status={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} restartCount={{.RestartCount}} startedAt={{.State.StartedAt}} finishedAt={{.State.FinishedAt}}' "$cid" 2>&1 || true
                echo ""
                echo "--- last 200 lines via docker logs (fallback) ---"
                docker logs --tail 200 --timestamps "$cid" 2>&1 || echo "docker logs failed for $cid"
            else
                echo ""
                echo "[collect-logs] No container ID resolved for service '$service' — service may not be defined in this compose file."
            fi
        } > "$outfile"
    }

    print_info "Collecting backend logs..."
    collect_service_logs backend  "$LOG_DIR/backend_logs.txt"  "BACKEND CONTAINER LOGS"

    print_info "Collecting frontend logs..."
    collect_service_logs frontend "$LOG_DIR/frontend_logs.txt" "FRONTEND CONTAINER LOGS"

    print_info "Collecting database logs..."
    collect_service_logs db       "$LOG_DIR/database_logs.txt" "DATABASE CONTAINER LOGS"

    print_info "Collecting worker logs..."
    collect_service_logs worker   "$LOG_DIR/worker_logs.txt"   "WORKER CONTAINER LOGS"

    # All container logs combined — useful for chronological correlation
    print_info "Collecting combined container logs..."
    {
        echo "=== ALL CONTAINER LOGS COMBINED ==="
        echo "Compose form: ${COMPOSE_FORM}"
        compose logs --no-color --tail=all --timestamps 2>&1 || echo "Failed to get combined logs"
    } > "$LOG_DIR/all_container_logs.txt"

    # Compose ps -a snapshot (running + exited) — critical for failed deploys
    {
        echo "=== COMPOSE PS (RUNNING + EXITED) ==="
        compose ps -a 2>&1 || echo "compose ps -a failed"
    } > "$LOG_DIR/compose_ps.txt"
else
    print_warning "No docker compose binary detected on PATH — cannot collect container logs"
    {
        echo "=== CONTAINER LOGS UNAVAILABLE ==="
        echo "Neither 'docker compose' (V2 plugin) nor 'docker-compose' (V1 binary) is available on PATH."
        echo "Install one of them and re-run this script.  On Debian/Ubuntu:"
        echo "    sudo apt-get install docker-compose-plugin"
        echo ""
        echo "Raw docker containers (in case the deployment is running without compose):"
        docker ps -a 2>&1 || echo "docker ps -a failed"
    } > "$LOG_DIR/container_logs_not_available.txt"
fi

# Application Health Checks
print_info "Performing application health checks..."
{
    echo "=== APPLICATION HEALTH CHECKS ==="
    # IMPORTANT: only nginx/frontend ports are published to the host (443 + 3000,
    # both HTTPS).  backend (:8000) and db (:5432) are container-internal — they
    # are reached from outside ONLY through the nginx proxy on 443, or via
    # `compose exec`.  Curling :8000 from the host always fails on a HEALTHY
    # deploy, which is why this section talks to nginx and probes the backend
    # internally instead.

    CONFIGURED_IP=""
    [[ -f ".env" ]] && CONFIGURED_IP=$(grep "^HOST_IP=" .env | cut -d'=' -f2)
    BASE="https://${CONFIGURED_IP:-localhost}"   # 443 is the default HTTPS port
    echo "Base URL (nginx on 443): $BASE"
    [[ -z "$CONFIGURED_IP" ]] && echo "(no HOST_IP in .env — defaulting to localhost)"

    echo ""
    echo "Nginx static health ($BASE/health):"
    if curl -k -s --max-time 10 "$BASE/health" | grep -qi healthy; then
        echo "✅ nginx /health responding (frontend container up)"
    else
        echo "❌ nginx /health not responding"
    fi

    echo ""
    echo "Backend reachable through the nginx proxy ($BASE/openapi.json):"
    PROXY_CODE=$(curl -k -s -o /dev/null -w "%{http_code}" --max-time 15 "$BASE/openapi.json" 2>/dev/null || echo "000")
    if [[ "$PROXY_CODE" == "200" ]]; then
        echo "✅ backend reachable via proxy (HTTP 200)"
    else
        echo "❌ backend via proxy returned HTTP $PROXY_CODE (nginx up but backend unreachable?)"
    fi

    echo ""
    echo "Backend DB-probe (internal — the REAL readiness check):"
    if compose_available && [[ -n "$(compose ps -q backend 2>/dev/null)" ]]; then
        # /health on the backend does a SELECT 1; 'database: ok' means the app
        # can reach Postgres.  503 / unreachable = the actionable failure.
        compose exec -T backend curl -fsS --max-time 10 http://localhost:8000/health 2>&1 \
            || echo "❌ backend /health probe failed (DB unreachable, or backend unhealthy)"
    else
        echo "backend container not running — cannot run the internal DB probe"
    fi

    echo ""
    echo "CORS preflight (OPTIONS via nginx → /api/v1/auth/login):"
    # auth/login is an always-present, unauthenticated route; dashboard is now
    # project-scoped (/api/v1/projects/{id}/dashboard/...) so the old
    # /api/v1/dashboard/stats probe 404'd regardless.
    CORS_CODE=$(curl -k -s -o /dev/null -w "%{http_code}" -X OPTIONS \
        -H "Origin: $BASE" \
        -H "Access-Control-Request-Method: POST" \
        -H "Access-Control-Request-Headers: Content-Type" \
        --max-time 10 "$BASE/api/v1/auth/login" 2>/dev/null || echo "000")
    if [[ "$CORS_CODE" == "200" || "$CORS_CODE" == "204" ]]; then
        echo "✅ CORS preflight ok (HTTP $CORS_CODE)"
    else
        echo "❌ CORS preflight returned HTTP $CORS_CODE (check CORS_ORIGINS in .env)"
    fi

    echo ""
    echo "Port connectivity (host-published ports only — 443/3000 are nginx):"
    for port in 443 3000; do
        if ss -tln 2>/dev/null | grep -q ":$port "; then
            echo "✅ Port $port is listening"
        else
            echo "❌ Port $port is not listening"
        fi
    done
    echo "(backend :8000 and db :5432 are intentionally NOT host-published — internal only)"
} > "$LOG_DIR/health_checks.txt"

# Recent Git History
print_info "Collecting Git information..."
{
    echo "=== GIT REPOSITORY INFORMATION ==="
    echo "Current branch:"
    git branch --show-current 2>/dev/null || echo "Not a git repository"
    echo ""
    echo "Recent commits (last 10):"
    git log --oneline -10 2>/dev/null || echo "No git history available"
    echo ""
    echo "Git status:"
    git status 2>/dev/null || echo "Not a git repository"
    echo ""
    echo "Uncommitted changes:"
    git diff --name-only 2>/dev/null || echo "No git repository or no changes"
} > "$LOG_DIR/git_info.txt"

# File System Information
print_info "Collecting file system information..."
{
    echo "=== FILE SYSTEM INFORMATION ==="
    echo "Current directory contents:"
    ls -la
    echo ""
    echo "Disk space:"
    df -h . 2>/dev/null || echo "Disk info not available"
    echo ""
    echo "Key file sizes:"
    if [[ -f "docker-compose.yml" ]]; then
        echo "docker-compose.yml: $(wc -l < docker-compose.yml) lines"
    fi
    if [[ -d "uploads" ]]; then
        echo "Uploads directory: $(ls uploads/ 2>/dev/null | wc -l) files"
        echo "Upload sizes:"
        du -sh uploads/* 2>/dev/null | head -10 || echo "No uploads"
    fi
} > "$LOG_DIR/filesystem_info.txt"

# Error Patterns Analysis
print_info "Analyzing error patterns..."
{
    echo "=== ERROR PATTERN ANALYSIS ==="
    echo "Recent errors in backend logs:"
    if [[ -f "$LOG_DIR/backend_logs.txt" ]]; then
        grep -i "error\|exception\|traceback\|failed" "$LOG_DIR/backend_logs.txt" | tail -20 || echo "No errors found"
    else
        echo "Backend logs not available"
    fi

    echo ""
    echo "Full tracebacks in backend logs (last 5, with surrounding context):"
    # tail -20 of single-line matches strips the body of multi-line Python
    # tracebacks — exactly the data that makes a failure diagnosable.
    # grep -A 40 -B 2 keeps each traceback intact; awk picks the last 5.
    if [[ -f "$LOG_DIR/backend_logs.txt" ]]; then
        grep -n -A 40 -B 2 "Traceback (most recent call last)" "$LOG_DIR/backend_logs.txt" 2>/dev/null \
            | awk 'BEGIN{RS="--\n"} {a[NR]=$0} END{start=(NR>5)?NR-4:1; for(i=start;i<=NR;i++) print a[i] "--"}' \
            || echo "No tracebacks found"
    else
        echo "Backend logs not available"
    fi

    echo ""
    echo "Worker / ingestion failures (last 5 tracebacks, with context):"
    if [[ -f "$LOG_DIR/worker_logs.txt" ]]; then
        grep -n -A 40 -B 2 "Traceback (most recent call last)" "$LOG_DIR/worker_logs.txt" 2>/dev/null \
            | awk 'BEGIN{RS="--\n"} {a[NR]=$0} END{start=(NR>5)?NR-4:1; for(i=start;i<=NR;i++) print a[i] "--"}' \
            || echo "No worker tracebacks found"
    else
        echo "Worker logs not available"
    fi

    echo ""
    echo "Recent CORS-related issues:"
    if [[ -f "$LOG_DIR/all_container_logs.txt" ]]; then
        grep -i "cors\|origin\|access-control" "$LOG_DIR/all_container_logs.txt" | tail -10 || echo "No CORS issues found"
    else
        echo "Container logs not available"
    fi
    
    echo ""
    echo "Database connection issues:"
    if [[ -f "$LOG_DIR/backend_logs.txt" ]]; then
        grep -i "database\|postgres\|connection" "$LOG_DIR/backend_logs.txt" | tail -10 || echo "No database issues found"
    else
        echo "Backend logs not available"
    fi
} > "$LOG_DIR/error_analysis.txt"

# Authentication and Audit Logs
print_info "Collecting authentication and audit logs..."
{
    echo "=== AUTHENTICATION AND AUDIT LOGS ==="
    echo "Frontend Browser Logs (if available):"
    # Try to extract authentication logs from browser developer tools if running
    if compose_available && [[ -n "$(compose ps -q frontend 2>/dev/null)" ]]; then
        echo "Frontend authentication logging is instrumented - check browser console for:"
        echo "- AUTH category logs"
        echo "- LOGIN_ATTEMPT, LOGIN_SUCCESS, LOGIN_FAILED audit events"
        echo "- PROTECTED_ROUTE access decisions"
        echo "- Authentication state changes"
        echo ""
    fi

    echo "Backend Authentication Logs:"
    if [[ -f "$LOG_DIR/backend_logs.txt" ]]; then
        grep -i "auth\|login\|token\|password\|audit\|bcrypt\|jwt" "$LOG_DIR/backend_logs.txt" | tail -30 || echo "No authentication events found"
    else
        echo "Backend logs not available"
    fi

    echo ""
    echo "Audit Endpoint Activity:"
    if [[ -f "$LOG_DIR/backend_logs.txt" ]]; then
        grep -i "audit.*log\|/api/v1/audit" "$LOG_DIR/backend_logs.txt" | tail -20 || echo "No audit endpoint activity found"
    else
        echo "Backend logs not available"
    fi

    echo ""
    echo "Authentication Errors:"
    if [[ -f "$LOG_DIR/backend_logs.txt" ]]; then
        grep -i "401\|unauthorized\|forbidden\|invalid.*password\|bcrypt.*error" "$LOG_DIR/backend_logs.txt" | tail -20 || echo "No authentication errors found"
    else
        echo "Backend logs not available"
    fi
} > "$LOG_DIR/auth_audit_logs.txt"

# Browser Storage Information
print_info "Collecting browser storage information..."
{
    echo "=== BROWSER STORAGE ANALYSIS ==="
    echo "This log collection cannot directly access browser storage, but here's what to check:"
    echo ""
    echo "LocalStorage Keys to Examine:"
    echo "- auth_token: JWT token for API authentication"
    echo "- auth_user: Serialized user object with role information"
    echo ""
    echo "SessionStorage Keys to Examine:"
    echo "- debug_session_id: Logging session identifier"
    echo ""
    echo "Browser Console Commands for Manual Debugging:"
    echo "// Check authentication state"
    echo "console.log('Auth Token:', localStorage.getItem('auth_token'));"
    echo "console.log('Auth User:', localStorage.getItem('auth_user'));"
    echo "console.log('Session ID:', sessionStorage.getItem('debug_session_id'));"
    echo ""
    echo "// Export authentication logs (if logger is available)"
    echo "if (window.logger) { console.log(logger.getAuthLogs()); }"
    echo ""
    echo "// Check comprehensive logs"
    echo "if (window.logger) { console.log(logger.exportLogs()); }"
} > "$LOG_DIR/browser_storage_info.txt"

# Create summary file
print_info "Creating troubleshooting summary..."
{
    echo "=== TROUBLESHOOTING SUMMARY ==="
    echo "Generated: $(date)"
    PLATFORM_VER=""
    if [[ -f "platform_version.json" ]]; then
        PLATFORM_VER=$(cat platform_version.json 2>/dev/null)
    fi
    echo "BlueStick Version: ${PLATFORM_VER:-unknown}"
    echo "Authentication System: JWT with comprehensive logging"
    echo ""
    
    # Quick health summary
    if [[ -f ".env" ]]; then
        CONFIGURED_IP=$(grep "^HOST_IP=" .env | cut -d'=' -f2)
        echo "Configured for network deployment on: $CONFIGURED_IP"
    else
        echo "Not configured for network deployment"
    fi
    
    echo ""
    echo "Container Status:"
    if compose_available; then
        compose ps -a 2>&1 || echo "compose ps -a failed"
    else
        echo "No docker compose binary available — see container_logs_not_available.txt"
    fi
    
    echo ""
    echo "Files collected in this troubleshooting package:"
    echo "- system_info.txt: System and software versions"
    echo "- network_info.txt: Network configuration and connectivity"
    echo "- environment_config.txt: Environment and Docker configuration"
    echo "- docker_status.txt: Docker container, image, and network status"
    echo "- container_images.txt: Image tags/digests for deployed services"
    echo "- service_versions.txt: Application-reported version knobs"
    echo "- backend_logs.txt: Backend application logs (incl. exited containers)"
    echo "- frontend_logs.txt: Frontend application logs (incl. exited containers)"
    echo "- database_logs.txt: Database logs (incl. exited containers)"
    echo "- worker_logs.txt: Ingestion worker logs (incl. exited containers)"
    echo "- all_container_logs.txt: Combined timestamped logs"
    echo "- compose_ps.txt: Snapshot of compose ps -a (running + exited)"
    echo "- health_checks.txt: Application connectivity and health tests"
    echo "- git_info.txt: Repository status and recent changes"
    echo "- filesystem_info.txt: File system and directory information"
    echo "- ingestion_jobs.txt: Ingestion queue snapshot + full last_error tracebacks for failed jobs"
    echo "- error_analysis.txt: Error pattern analysis (full backend + worker tracebacks)"
    echo "- auth_audit_logs.txt: Authentication and audit event logs"
    echo "- browser_storage_info.txt: Browser storage debugging guide"
    echo ""
    echo "Anonymization applied:"
    echo "- IP addresses replaced with X.X.X.X (127.0.0.1 and 0.0.0.0 preserved)"
    echo "- SECRET_KEY, PASSWORD values replaced with [REDACTED]"
    echo ""
    echo "To share these logs:"
    echo "1. Create archive: tar -czf ${LOG_DIR}.tar.gz $LOG_DIR"
    echo "2. Share the archive file for troubleshooting"
} > "$LOG_DIR/README.txt"

# ------------------------------------------------------------------
# Anonymize collected data — replace IP addresses and secrets
# ------------------------------------------------------------------
print_info "Anonymizing collected logs..."

# Anonymize each collected file in place: redact secrets from env dumps, then
# replace IPv4/IPv6 addresses (preserving 127.0.0.1 and 0.0.0.0).
# Two-pass approach: first protect safe IPs, then replace all others.
for file in "$LOG_DIR"/*.txt; do
    [[ -f "$file" ]] || continue

    # 1. Replace secrets in-place
    sed -i -E \
        -e 's/(SECRET_KEY=).*/\1[REDACTED]/g' \
        -e 's/(JWT_SECRET_KEY=).*/\1[REDACTED]/g' \
        -e 's/(PASSWORD=).*/\1[REDACTED]/g' \
        -e 's/(POSTGRES_PASSWORD=).*/\1[REDACTED]/g' \
        -e 's/(DEFAULT_ADMIN_PASSWORD=).*/\1[REDACTED]/g' \
        "$file"

    # 2. Replace IPv4 addresses (but preserve 127.0.0.1 and 0.0.0.0)
    #    Marker swap: protect safe IPs -> replace all IPs -> restore safe IPs
    sed -i \
        -e 's/127\.0\.0\.1/__LOCALHOST_SAFE__/g' \
        -e 's/0\.0\.0\.0/__ANYADDR_SAFE__/g' \
        -e 's/[0-9]\{1,3\}\.[0-9]\{1,3\}\.[0-9]\{1,3\}\.[0-9]\{1,3\}/X.X.X.X/g' \
        -e 's/__LOCALHOST_SAFE__/127.0.0.1/g' \
        -e 's/__ANYADDR_SAFE__/0.0.0.0/g' \
        "$file"

    # 3. Replace IPv6 addresses (groups of hex separated by colons, 3+ groups)
    sed -i -E 's/([0-9a-fA-F]{1,4}:){2,}[0-9a-fA-F]{1,4}(\/[0-9]+)?/X:X:X::X/g' "$file"
done

print_success "IP addresses and secrets anonymized"

# Create compressed archive
print_info "Creating compressed archive..."
tar -czf "${LOG_DIR}.tar.gz" "$LOG_DIR" 2>/dev/null

# Final output
echo ""
print_success "Log collection completed!"
echo ""
print_info "Troubleshooting package created:"
echo "📁 Directory: $LOG_DIR"
echo "📦 Archive: ${LOG_DIR}.tar.gz"
echo ""
print_info "Package contents:"
ls -la "$LOG_DIR"
echo ""
print_success "IP addresses and secrets have been anonymized automatically."
echo ""
print_info "To view the summary: cat $LOG_DIR/README.txt"
print_info "To extract archive: tar -xzf ${LOG_DIR}.tar.gz"
echo ""
