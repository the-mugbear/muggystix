#!/bin/bash
#
# This script checks the status of the BlueStick application instances.
#
# What it does:
# - Checks the status of the production and test instances of the application.
# - For each running instance, it displays the URLs for the frontend, backend, and API docs.
# - It also performs a quick health check to see if the frontend and backend are responding.
# - Lists available management scripts and quick actions for managing the instances.
# - Shows resource usage by displaying running Docker containers and volumes.
#
# How it does it:
# - Uses `docker-compose ps` to check the status of the containers for each instance.
# - Uses `curl` to perform health checks on the frontend and backend URLs.
# - Displays a list of other useful management scripts.
# - Provides common `docker-compose` commands for starting, stopping, and managing the instances.
# - Uses `docker ps` and `docker volume ls` to show resource usage.
#
# BlueStick Status and Management Script
# Shows status of all instances and provides quick management options
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Function to print colored output
print_info() { echo -e "${BLUE} $1${NC}"; }
print_success() { echo -e "${GREEN} $1${NC}"; }
print_warning() { echo -e "${YELLOW} $1${NC}"; }
print_error() { echo -e "${RED} $1${NC}"; }
print_header() { echo -e "${CYAN} $1${NC}"; }

echo "========================================"
echo "       BlueStick Status"
echo "========================================"
echo

cd "$PROJECT_ROOT"

# Prefer the docker compose v2 plugin; fall back to the EOL v1 standalone.
if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    DC="docker-compose"
else
    print_error "Neither 'docker compose' (v2) nor 'docker-compose' (v1) is available."
    exit 1
fi

# Check production instance
print_header "Production Instance Status"
# Asked of compose, not grepped for by name: the containers are named after
# the folder (or COMPOSE_PROJECT_NAME), which is not "networkmapper" on a host
# installed anywhere else — the check then said "not running" and skipped
# every probe below.
if [ -n "$($DC ps --status running -q 2>/dev/null)" ]; then
    print_success "Production instance is running"
    # The backend container port is NOT published (its ports block in
    # docker-compose.yml is commented out) and it serves plain HTTP, so
    # https://localhost:8000 was wrong on both counts — it printed two URLs
    # nobody can open and made the health probe below fail permanently,
    # yielding a yellow "Backend may be starting up…" on every run against a
    # perfectly healthy deployment.  Everything reaches the backend through
    # nginx on the frontend port; see the same note in collect-logs.sh.
    echo "  Frontend: https://localhost:3000"
    echo "  Backend:  https://localhost:3000/api/v1  (proxied via nginx)"
    echo "  API Docs: https://localhost:3000/docs"

    # Check if frontend is responding
    if curl -k -s https://localhost:3000 >/dev/null 2>&1; then
        print_success "Frontend is responding"
    else
        print_warning "Frontend may be starting up..."
    fi

    # Backend health, proxied through nginx (`location = /health`).  Falls back
    # to asking the container directly, which still works if nginx is down or
    # the frontend port was remapped.
    if curl -k -s -f https://localhost:3000/health >/dev/null 2>&1; then
        print_success "Backend is healthy"
    elif $DC exec -T backend python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=3).status==200 else 1)" >/dev/null 2>&1; then
        print_success "Backend is healthy (direct; nginx not serving /health)"
    else
        print_warning "Backend may be starting up..."
    fi
else
    print_warning "Production instance is not running"
fi
echo

# Newest backup and its age (review 2026-10-01 B11).  Backups are only taken
# by a deploy, a restore's safety copy or by hand, so on a host nobody has
# redeployed for a month the newest one is a month old — and nothing said so.
print_header "Backups"
BACKUP_DIR="${BACKUP_DIR:-$(dirname "$PROJECT_ROOT")/$(basename "$PROJECT_ROOT")-db-backups}"
BACKUP_WARN_DAYS="${BACKUP_WARN_DAYS:-7}"
# `|| true`: with no match ls exits non-zero, and this script runs under set -e.
newest_backup="$(ls -1t "$BACKUP_DIR"/nm-pgdump-*.dump "$BACKUP_DIR"/nm-pgdata-*.tar.gz 2>/dev/null | head -1 || true)"
if [[ -z "$newest_backup" ]]; then
    print_warning "No database backup found in $BACKUP_DIR"
    echo "  Take one:  ./scripts/backup-db.sh"
else
    backup_count="$(ls -1 "$BACKUP_DIR"/nm-pgdump-*.dump "$BACKUP_DIR"/nm-pgdata-*.tar.gz 2>/dev/null | wc -l | tr -d ' ')"
    backup_mtime="$(stat -c %Y "$newest_backup" 2>/dev/null || stat -f %m "$newest_backup" 2>/dev/null || echo 0)"
    backup_age_s=$(( $(date +%s) - backup_mtime ))
    if (( backup_age_s < 3600 )); then
        backup_age="$(( backup_age_s / 60 )) minute(s)"
    elif (( backup_age_s < 172800 )); then
        backup_age="$(( backup_age_s / 3600 )) hour(s)"
    else
        backup_age="$(( backup_age_s / 86400 )) day(s)"
    fi
    backup_line="Newest backup: $(basename "$newest_backup"), $backup_age old ($backup_count kept in $BACKUP_DIR)"
    if (( backup_age_s > BACKUP_WARN_DAYS * 86400 )); then
        print_warning "$backup_line"
        echo "  Older than ${BACKUP_WARN_DAYS} day(s) (BACKUP_WARN_DAYS). Take one:  ./scripts/backup-db.sh"
    else
        print_success "$backup_line"
    fi
    if ! grep -q '^uploads_archive=' "$newest_backup.meta" 2>/dev/null; then
        print_warning "It has no uploads archive: evidence images and issued reports are not in it."
    fi
fi
echo

# One-off data corrections this instance has no recorded run of (the
# data-repair ledger, review 2026-10-01 B10).  Read-only; prints the commands.
print_header "Data Repairs"
if pending_repairs="$($DC exec -T backend python scripts/data_repairs.py --pending 2>/dev/null)"; then
    if [[ -n "$pending_repairs" ]]; then
        print_warning "Data repairs not yet applied:"
        echo "$pending_repairs" | sed 's/^/  /'
    else
        print_success "Every known data repair has been applied"
    fi
else
    print_info "Could not read the data-repair ledger (backend not running, or older than the ledger)"
fi
echo

# The settings an operator can tune, with the values in force (the same
# readout a deploy ends with).
if [[ -f "$SCRIPT_DIR/stack-lib.sh" ]]; then
    print_header "Settings"
    # shellcheck source=stack-lib.sh
    source "$SCRIPT_DIR/stack-lib.sh"
    print_tunable_settings
    echo
fi

# Show available scripts
print_header "Available Management Scripts"
echo "Deployment Scripts:"
echo "  ./scripts/deploy.sh            - Unified deployment script (all options)"
echo
echo "User Management:"
echo "  Default admin account is created automatically on first boot"
echo "  Set DEFAULT_ADMIN_PASSWORD env var to control the initial password"
echo
echo "Maintenance:"
echo "  ./scripts/collect-logs.sh      - Collect comprehensive logs for debugging"
echo "  ./scripts/status.sh            - Show this status (current script)"
echo

# Show quick actions
print_header "Quick Actions"
echo "Production Instance:"
echo "  Start:  $DC up -d"
echo "  Stop:   $DC down"
echo "  Logs:   $DC logs -f"
echo

# Show disk usage
print_header "Resource Usage"
echo "Docker containers:"
$DC ps -a --format "table {{.Name}}\t{{.Service}}\t{{.Status}}" 2>/dev/null || $DC ps -a
echo
COMPOSE_PROJECT="$($DC ps -a --format '{{.Project}}' 2>/dev/null | head -n 1)"
if [ -n "$COMPOSE_PROJECT" ]; then
    echo "Docker volumes:"
    docker volume ls --filter "label=com.docker.compose.project=$COMPOSE_PROJECT"
    echo
fi
echo "Disk (this folder's filesystem — uploads, and usually the database and Docker):"
df -h . | sed 's/^/  /'
echo

print_info "For detailed logs: ./scripts/collect-logs.sh"
print_info "For all deployments: ./scripts/deploy.sh"