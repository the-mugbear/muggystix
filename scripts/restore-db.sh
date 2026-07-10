#!/bin/bash
#
# BlueStick — Database Restore
#
# Imports a backup produced by backup-db.sh into the deployment.
#
#   .dump    (logical)  — restored into a freshly recreated database.
#                         The backend's boot-time `alembic upgrade head`
#                         then migrates the restored schema forward to
#                         whatever the deployed code expects.  This is
#                         the normal recovery path and supports an older
#                         backup → newer code.
#   .tar.gz  (volume)   — raw PGDATA replacement: an exact byte clone.
#                         Same PostgreSQL major version only.
#
# A backup whose schema is NEWER than the deployed code cannot be
# migrated forward — alembic will not recognise the revision.  In that
# case the backend stays unhealthy after restore; roll the code forward
# (or restore a matching-or-older backup) instead.
#
# Usage:
#   ./scripts/restore-db.sh                       # pick from ./backups/ interactively
#   ./scripts/restore-db.sh backups/nm-pgdump-YYYYMMDD-HHMMSS.dump
#   ./scripts/restore-db.sh --no-safety-backup    # skip the pre-restore safety backup
#
# Flags:
#   --no-safety-backup     Don't take a fresh backup of the CURRENT database
#                          before the restore overwrites it.  Use this only
#                          when the current database is unrecoverable anyway
#                          (out of disk, corrupt, …) and you accept the loss.
#                          Default behaviour ABORTS the restore if the safety
#                          backup fails, since the operator typed RESTORE
#                          expecting recoverability.
#   --ignore-key-mismatch  Skip the credential-encryption-key check.  The
#                          restore normally warns (and, interactively, asks for
#                          a second confirmation) when the backup was encrypted
#                          under a DIFFERENT key than this deployment's .env —
#                          because that leaves restored MFA secrets and stored
#                          credentials undecryptable.  Use this to proceed
#                          unattended once you understand the consequence.
#

set -e

# --- Parse flags (positional args follow) ---
NO_SAFETY_BACKUP=0
IGNORE_KEY_MISMATCH=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-safety-backup)
            NO_SAFETY_BACKUP=1
            shift
            ;;
        --ignore-key-mismatch)
            IGNORE_KEY_MISMATCH=1
            shift
            ;;
        -h|--help)
            sed -n '2,40p' "$0"
            exit 0
            ;;
        --)
            shift
            break
            ;;
        -*)
            echo "Unknown flag: $1" >&2
            echo "Run with --help for usage." >&2
            exit 2
            ;;
        *)
            break
            ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

GREEN='\033[0;32m'; BLUE='\033[0;34m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
print_info()    { echo -e "${BLUE} $1${NC}"; }
print_success() { echo -e "${GREEN} $1${NC}"; }
print_error()   { echo -e "${RED} $1${NC}"; }
print_warning() { echo -e "${YELLOW} $1${NC}"; }

# --- Resolve the Compose command ---
if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    DC="docker-compose"
else
    print_error "Neither 'docker compose' nor 'docker-compose' is available."
    exit 1
fi

env_val() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d'=' -f2-; }
PG_USER="$(env_val POSTGRES_USER)"; PG_USER="${PG_USER:-nmapuser}"
PG_DB="$(env_val POSTGRES_DB)";     PG_DB="${PG_DB:-networkMapper}"

# --- Credential-encryption key fingerprint (mirror of backup-db.sh) --------
# Recomputes the one-way fingerprint of THIS deployment's key so we can compare
# it to the value stamped in the backup's .meta.  See backup-db.sh for why.
_sha256_hex() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum | cut -d' ' -f1
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 | cut -d' ' -f1
    elif command -v openssl >/dev/null 2>&1; then
        openssl dgst -sha256 | sed 's/^.*= *//'
    else
        return 1
    fi
}
key_fingerprint() {
    local src
    src="$(env_val CREDENTIAL_ENCRYPTION_KEY)"
    [[ -z "$src" ]] && src="$(env_val SECRET_KEY)"
    [[ -z "$src" ]] && return 1
    printf 'networkmapper-keyfp-v1:%s' "$src" | _sha256_hex | cut -c1-16
}

BACKUP_DIR="$PROJECT_ROOT/backups"

# --- Resolve the EXACT postgres volume for this stack ---
# A loose `--filter name=postgres_data` + `head -1` can pick another
# Compose project's volume on a shared host — and a volume restore
# `rm -rf`s + overwrites it.  Resolve authoritatively from the db
# container's mounts; fall back to an exact <project>_postgres_data
# match.  Fail closed (empty output) rather than guess.
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

echo "=============================================="
echo "   BlueStick — Database Restore"
echo "=============================================="

# --- Pick the backup file ---
BACKUP_FILE="${1:-}"
if [[ -z "$BACKUP_FILE" ]]; then
    mapfile -t files < <(ls -1t "$BACKUP_DIR"/nm-*.dump "$BACKUP_DIR"/nm-*.tar.gz 2>/dev/null || true)
    if [[ ${#files[@]} -eq 0 ]]; then
        print_error "No backups found in $BACKUP_DIR"
        exit 1
    fi
    echo ""
    echo "Available backups (newest first):"
    for i in "${!files[@]}"; do
        meta="${files[$i]}.meta"
        rev=""
        [[ -f "$meta" ]] && rev="  [$(grep -E '^alembic_revision=' "$meta" 2>/dev/null | cut -d= -f2)]"
        echo "  $((i + 1))) $(basename "${files[$i]}")${rev}"
    done
    echo ""
    echo -n "Select [1]: "
    read -r choice
    choice="${choice:-1}"
    if ! [[ "$choice" =~ ^[0-9]+$ ]] || (( choice < 1 || choice > ${#files[@]} )); then
        print_error "Invalid choice."
        exit 1
    fi
    BACKUP_FILE="${files[$((choice - 1))]}"
fi

[[ -f "$BACKUP_FILE" ]] || { print_error "Backup file not found: $BACKUP_FILE"; exit 1; }
print_info "Selected: $BACKUP_FILE"

# --- Credential-encryption key check ---------------------------------------
# If the backup was taken under a different CREDENTIAL_ENCRYPTION_KEY/SECRET_KEY
# than this .env holds, every restored TOTP secret and stored credential becomes
# undecryptable — MFA silently stops working.  Warn loudly (and, interactively,
# demand a second confirmation) before the operator commits.  Backups made
# before this feature carry no fingerprint; we can't verify those, so we say so
# rather than blocking.
check_key_fingerprint() {
    if [[ "$IGNORE_KEY_MISMATCH" -eq 1 ]]; then
        print_warning "Skipping credential-encryption-key check (--ignore-key-mismatch)."
        return 0
    fi

    local meta backup_fp current_fp
    meta="${BACKUP_FILE}.meta"
    backup_fp=""
    [[ -f "$meta" ]] && backup_fp="$(grep -E '^key_fingerprint=' "$meta" 2>/dev/null | tail -1 | cut -d= -f2-)"
    current_fp="$(key_fingerprint 2>/dev/null || true)"

    if [[ -z "$backup_fp" || "$backup_fp" == "unknown" ]]; then
        print_warning "This backup carries no credential-encryption-key fingerprint"
        print_warning "(made before this check, or with no key set) — can't verify it."
        print_warning "If MFA fails to work after restore, the backup was encrypted under"
        print_warning "a different SECRET_KEY/CREDENTIAL_ENCRYPTION_KEY than this .env holds."
        return 0
    fi
    if [[ -z "$current_fp" ]]; then
        print_warning "No SECRET_KEY/CREDENTIAL_ENCRYPTION_KEY set in this .env — can't"
        print_warning "verify it matches the backup. Set it before users rely on MFA."
        return 0
    fi
    if [[ "$backup_fp" == "$current_fp" ]]; then
        print_success "Credential-encryption key matches the backup."
        return 0
    fi

    # Mismatch — the dangerous case.
    echo ""
    print_error "==========================================================="
    print_error "  CREDENTIAL-ENCRYPTION KEY MISMATCH"
    print_error "==========================================================="
    print_warning "This backup was encrypted under a DIFFERENT key than this"
    print_warning "deployment's .env (backup: $backup_fp, current: $current_fp)."
    print_warning ""
    print_warning "If you restore now, for every user/account in the backup:"
    print_warning "  • TOTP / 2FA will stop working (codes always rejected)"
    print_warning "  • stored LLM / integration / webhook credentials won't decrypt"
    print_warning "  (Recovery codes still work — they're hashed, not encrypted.)"
    print_warning ""
    print_warning "To keep them working, copy the SOURCE deployment's key into this"
    print_warning ".env BEFORE restoring, then re-run:"
    print_warning "    SECRET_KEY=<old value>              # and, if it was set,"
    print_warning "    CREDENTIAL_ENCRYPTION_KEY=<old value>"
    print_warning "(Leave POSTGRES_*, HOST_IP, CORS_ORIGINS as they are.)"
    echo ""

    if [[ ! -t 0 ]]; then
        print_error "Non-interactive shell — aborting rather than silently breaking MFA."
        print_error "Re-run with --ignore-key-mismatch to proceed anyway."
        exit 1
    fi
    echo -n "Type 'KEYS-DIFFER' to acknowledge and restore anyway: "
    read -r ack
    [[ "$ack" == "KEYS-DIFFER" ]] || { print_info "Cancelled."; exit 0; }
}
check_key_fingerprint

# --- Confirm (destructive) ---
echo ""
print_warning "This OVERWRITES the current '$PG_DB' database with the backup."
echo -n "Type 'RESTORE' to confirm: "
read -r confirm
[[ "$confirm" == "RESTORE" ]] || { print_info "Cancelled."; exit 0; }

# --- Safety backup of the current database first ---
# Fails closed: the operator typed RESTORE expecting recoverability, so
# silently degrading to "continuing anyway" is the wrong default.  Use
# --no-safety-backup to opt out explicitly (e.g. the disk is full and the
# current database is already unrecoverable).
if [[ "$NO_SAFETY_BACKUP" -eq 1 ]]; then
    print_warning "Safety backup SKIPPED (--no-safety-backup)."
    print_warning "If this restore overwrites a recoverable database, the current"
    print_warning "state will be unrecoverable."
else
    print_info "Taking a safety backup of the CURRENT database first..."
    if ! "$SCRIPT_DIR/backup-db.sh"; then
        print_error "Safety backup failed — aborting restore."
        print_error "Re-run with --no-safety-backup to override (only do this if the"
        print_error "current database is already unrecoverable)."
        exit 1
    fi
fi

case "$BACKUP_FILE" in
  *.dump)
    print_info "Logical restore — bringing up the database container only..."
    $DC up -d db
    print_info "Waiting for PostgreSQL to accept connections..."
    ready=0
    for _ in $(seq 1 30); do
        if $DC exec -T db pg_isready -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1; then
            ready=1; break
        fi
        sleep 2
    done
    [[ "$ready" -eq 1 ]] || { print_error "Database did not become ready."; exit 1; }

    print_info "Recreating the '$PG_DB' database (drop + create)..."
    $DC exec -T db psql -U "$PG_USER" -d postgres -v ON_ERROR_STOP=1 <<SQL
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
  WHERE datname = '$PG_DB' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS "$PG_DB";
CREATE DATABASE "$PG_DB" OWNER "$PG_USER";
SQL

    print_info "Restoring dump into '$PG_DB'..."
    $DC exec -T db pg_restore -U "$PG_USER" -d "$PG_DB" \
        --no-owner --no-privileges < "$BACKUP_FILE"
    print_success "Dump restored."
    ;;

  *.tar.gz)
    # Resolve the volume BEFORE `down` — the db container is the
    # authoritative source and is gone after teardown.
    vol="$(resolve_volume)"
    [[ -n "$vol" ]] || {
        print_error "Could not uniquely identify this stack's postgres volume — aborting."
        print_error "Set COMPOSE_PROJECT_NAME in .env if multiple stacks share this host."
        exit 1
    }
    print_warning "Raw volume restore — target volume: $vol"
    print_warning "Stopping ALL containers..."
    $DC down
    print_info "Replacing the contents of volume '$vol'..."
    docker run --rm \
        -v "$vol":/data \
        -v "$BACKUP_DIR":/backup:ro \
        alpine sh -c "rm -rf /data/* 2>/dev/null; tar xzf /backup/$(basename "$BACKUP_FILE") -C /data"
    print_success "Volume contents replaced."
    ;;

  *)
    print_error "Unrecognised backup type (expected .dump or .tar.gz): $BACKUP_FILE"
    exit 1
    ;;
esac

# --- Bring the full stack up; the backend runs `alembic upgrade head` ---
echo ""
print_info "Starting the full stack — the backend will migrate the schema forward..."
$DC up -d

print_info "Waiting for the backend to become healthy..."
ok=0
bid="$($DC ps -q backend 2>/dev/null || true)"
for _ in $(seq 1 30); do
    status="unknown"
    [[ -n "$bid" ]] && status="$(docker inspect --format '{{.State.Health.Status}}' "$bid" 2>/dev/null || echo unknown)"
    if [[ "$status" == "healthy" ]]; then ok=1; break; fi
    sleep 5
    bid="$($DC ps -q backend 2>/dev/null || true)"
done

echo ""
if [[ "$ok" -eq 1 ]]; then
    print_success "Restore complete — backend healthy, schema migrated to head."
else
    print_error "Backend did not become healthy within the timeout."
    print_warning "If the backup's schema is NEWER than the deployed code, alembic"
    print_warning "cannot migrate it forward.  Inspect the migration log:"
    echo "    $DC logs backend | grep -iE 'alembic|revision'"
fi
