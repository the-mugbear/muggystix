#!/bin/bash
#
# BlueStick — Database Backup
#
# Produces a portable logical backup (pg_dump custom format) when the
# database container is running, or a raw volume snapshot (tar) when
# Postgres cannot start.
#
# Backups land OUTSIDE the project folder by default — in a sibling directory
# "<project>-db-backups" next to the project root — so an in-place update that
# replaces the folder's contents (download fresh source over the same path)
# does NOT wipe your backups.  Override the location with BACKUP_DIR=/path.
# (The DB itself lives in a Docker volume, which also survives a folder
# replace; this just keeps the dump file safe too.)
#
# Usage:
#   ./scripts/backup-db.sh                       # auto: pg_dump if db up, else volume tar
#   ./scripts/backup-db.sh --volume              # force a raw volume snapshot
#   BACKUP_DIR=/mnt/usb ./scripts/backup-db.sh   # custom destination
#
# A logical backup (.dump) is portable and supports cross-version
# restore — the backend's boot-time `alembic upgrade head` migrates a
# restored older schema forward.  A volume snapshot (.tar.gz) is an
# exact byte clone of PGDATA and only restores into the same PostgreSQL
# major version; it is the last-resort path for when Postgres will not
# even start.  Restore either with ./scripts/restore-db.sh.
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

GREEN='\033[0;32m'; BLUE='\033[0;34m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
print_info()    { echo -e "${BLUE} $1${NC}"; }
print_success() { echo -e "${GREEN} $1${NC}"; }
print_error()   { echo -e "${RED} $1${NC}"; }
print_warning() { echo -e "${YELLOW} $1${NC}"; }

# --- Resolve the Compose command (v2 plugin preferred; see deploy.sh) ---
if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    DC="docker-compose"
else
    print_error "Neither 'docker compose' nor 'docker-compose' is available."
    exit 1
fi

# --- DB credentials (from .env, falling back to docker-compose defaults) ---
env_val() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d'=' -f2-; }
PG_USER="$(env_val POSTGRES_USER)"; PG_USER="${PG_USER:-nmapuser}"
PG_DB="$(env_val POSTGRES_DB)";     PG_DB="${PG_DB:-networkMapper}"

# Default OUTSIDE the project root (sibling dir) so an in-place folder-content
# replace doesn't wipe the dump.  Override with BACKUP_DIR=/path.
BACKUP_DIR="${BACKUP_DIR:-$(dirname "$PROJECT_ROOT")/$(basename "$PROJECT_ROOT")-db-backups}"
mkdir -p "$BACKUP_DIR"
print_info "Backups → $BACKUP_DIR"
TS="$(date +%Y%m%d-%H%M%S)"

FORCE_VOLUME=0
[[ "${1:-}" == "--volume" ]] && FORCE_VOLUME=1

# --- Is the db container up and accepting connections? ---
db_is_ready() {
    # `exec` fails outright if the container is not running, so this
    # doubles as a container-up check.
    $DC exec -T db pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1
}

# --- Resolve the EXACT postgres volume for this stack ---
# A loose `--filter name=postgres_data` substring match + `head -1` can
# pick another Compose project's *_postgres_data volume on a shared host
# — backing up the wrong DB, or (on restore) overwriting it.  Resolve
# authoritatively from the db container's mounts; fall back to an exact
# <project>_postgres_data match.  Fail closed (empty output) rather than
# guess.
resolve_volume() {
    local cid vol project expected
    cid="$($DC ps -aq db 2>/dev/null | head -1)"
    if [[ -n "$cid" ]]; then
        vol="$(docker inspect "$cid" \
            --format '{{range .Mounts}}{{if eq .Destination "/var/lib/postgresql/data"}}{{.Name}}{{end}}{{end}}' \
            2>/dev/null)"
        if [[ -n "$vol" ]]; then
            printf '%s\n' "$vol"
            return 0
        fi
    fi
    project="$(env_val COMPOSE_PROJECT_NAME)"
    if [[ -z "$project" ]]; then
        project="$(basename "$PROJECT_ROOT" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')"
    fi
    expected="${project}_postgres_data"
    if docker volume ls -q 2>/dev/null | grep -qx "$expected"; then
        printf '%s\n' "$expected"
        return 0
    fi
    return 1
}

do_pgdump() {
    local out="$BACKUP_DIR/nm-pgdump-$TS.dump"
    local meta="$out.meta"
    print_info "Database is up — taking a logical backup (pg_dump -Fc)..."

    if ! $DC exec -T db pg_dump -U "$PG_USER" -Fc "$PG_DB" > "$out" 2>/dev/null; then
        print_error "pg_dump failed."
        rm -f "$out"
        return 1
    fi
    if [[ ! -s "$out" ]]; then
        print_error "pg_dump produced an empty file — aborting."
        rm -f "$out"
        return 1
    fi

    # Capture the schema (alembic) revision so restore can sanity-check
    # the migration direction.
    local rev
    rev="$($DC exec -T db psql -U "$PG_USER" -d "$PG_DB" -tAc \
        "SELECT version_num FROM alembic_version" 2>/dev/null | tr -d '[:space:]')"
    rev="${rev:-unknown}"

    {
        echo "type=pgdump"
        echo "created=$TS"
        echo "database=$PG_DB"
        echo "alembic_revision=$rev"
        echo "app_version=$(env_val APP_VERSION)"
    } > "$meta"

    print_success "Backup written: $out ($(du -h "$out" | cut -f1))"
    print_info    "Schema revision: $rev"
}

do_volume_tar() {
    local vol
    vol="$(resolve_volume)"
    if [[ -z "$vol" ]]; then
        print_error "Could not uniquely identify this stack's postgres volume."
        print_error "Start the db container, or set COMPOSE_PROJECT_NAME in .env."
        return 1
    fi
    local out="nm-pgdata-$TS.tar.gz"
    print_info "Taking a raw volume snapshot of '$vol'..."
    if ! docker run --rm \
        -v "$vol":/data:ro \
        -v "$BACKUP_DIR":/backup \
        alpine tar czf "/backup/$out" -C /data . ; then
        print_error "Volume snapshot failed."
        return 1
    fi
    {
        echo "type=volume"
        echo "created=$TS"
        echo "volume=$vol"
    } > "$BACKUP_DIR/$out.meta"
    print_success "Backup written: $BACKUP_DIR/$out ($(du -h "$BACKUP_DIR/$out" | cut -f1))"
    print_warning "Raw volume snapshots restore only into the same PostgreSQL major version."
}

echo "=============================================="
echo "   BlueStick — Database Backup"
echo "=============================================="

if [[ "$FORCE_VOLUME" -eq 1 ]]; then
    do_volume_tar
elif db_is_ready; then
    do_pgdump
else
    print_warning "Database container is not running/ready — falling back to a raw volume snapshot."
    do_volume_tar
fi

echo ""
print_info "Restore with:  ./scripts/restore-db.sh <backup-file>"
