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
#   ./scripts/backup-db.sh --allow-missing-uploads
#   BACKUP_DIR=/mnt/usb ./scripts/backup-db.sh   # custom destination
#   BACKUP_KEEP=30 ./scripts/backup-db.sh        # keep the newest 30 (default 10; 0 = keep all)
#
# Flags and environment:
#   --volume                 Take a raw volume snapshot even if the database is up.
#   --allow-missing-uploads  Exit 0 when the database was backed up but the
#                            uploads archive (evidence images, issued reports,
#                            screenshots) could not be written.  Without it
#                            that is a FAILED backup (exit 1): the dump alone
#                            restores rows that point at files which are gone.
#                            BACKUP_ALLOW_MISSING_UPLOADS=1 does the same.
#   BACKUP_DIR               Where backups are written (made mode 700).
#   BACKUP_KEEP              How many backups to keep (default 10, 0 = all).
#                            The backup just made, and the pre-deploy dump a
#                            rollback (deploy.sh option 7) would restore, are
#                            never pruned.
#   BACKUP_PRUNE_BACKLOG=1   Allow one run to delete MORE than a few old backups
#                            (BACKUP_PRUNE_MAX, default 3).  Without it a
#                            backlog — a directory never pruned before, or a
#                            lowered BACKUP_KEEP — is reported, not deleted.
#
# What "success" means (exit 0): the database artifact was written AND read
# back TO ITS END (pg_restore -f /dev/null for a dump — every data block is
# decompressed and parsed; a gzip/tar listing for a snapshot), and the uploads
# archive was written and read back.  The .meta beside each backup records the
# size (bytes=) and SHA-256 (sha256=) of the artifact, and of the uploads
# archive (uploads_bytes= / uploads_sha256=): restore-db.sh compares them
# before it touches the database, so a file cut short or altered on the way
# to another host is refused.  Everything is written mode 0600, owned by the
# user running the script: a dump holds password hashes, TOTP ciphertext and
# client data.
#
# A logical backup (.dump) is portable and supports cross-version
# restore — the backend's boot-time `alembic upgrade head` migrates a
# restored older schema forward.  A volume snapshot (.tar.gz) is an
# exact byte clone of PGDATA and only restores into the same PostgreSQL
# major version; it is the last-resort path for when Postgres will not
# even start.  Restore either with ./scripts/restore-db.sh.
#

set -e

# Everything this script creates is private to the operator: 0600 files in a
# 0700 directory.  It used to inherit the shell's umask, so every deploy left
# a full dump world-readable under the usual 022 (review 2026-10-01 R29).
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

GREEN='\033[0;32m'; BLUE='\033[0;34m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
print_info()    { echo -e "${BLUE} $1${NC}"; }
print_success() { echo -e "${GREEN} $1${NC}"; }
print_error()   { echo -e "${RED} $1${NC}"; }
print_warning() { echo -e "${YELLOW} $1${NC}"; }

# --- Flags ---
FORCE_VOLUME=0
ALLOW_MISSING_UPLOADS="${BACKUP_ALLOW_MISSING_UPLOADS:-0}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --volume)
            FORCE_VOLUME=1
            shift
            ;;
        --allow-missing-uploads)
            ALLOW_MISSING_UPLOADS=1
            shift
            ;;
        -h|--help)
            sed -n '2,58p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            echo "Run with --help for usage." >&2
            exit 2
            ;;
    esac
done

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

# --- Credential-encryption key fingerprint ---------------------------------
# TOTP/2FA secrets and the LLM-provider, scanner-integration and webhook credentials are stored
# in the DB as Fernet ciphertext, keyed (via HKDF) off CREDENTIAL_ENCRYPTION_KEY
# — or SECRET_KEY when that's unset — which lives ONLY in .env, never in the
# database.  Restoring this dump onto a deployment with a different key leaves
# every such secret undecryptable (MFA silently stops working).  We stamp a
# one-way fingerprint of the key into the backup metadata so restore-db.sh can
# warn on a mismatch.  It's a SALTED, TRUNCATED SHA-256 — enough to detect
# "different key", never enough to help recover the key itself.
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
    [[ -z "$src" ]] && return 1   # no key configured — nothing to fingerprint
    printf 'networkmapper-keyfp-v1:%s' "$src" | _sha256_hex | cut -c1-16
}

# Default OUTSIDE the project root (sibling dir) so an in-place folder-content
# replace doesn't wipe the dump.  Override with BACKUP_DIR=/path.
BACKUP_DIR="${BACKUP_DIR:-$(dirname "$PROJECT_ROOT")/$(basename "$PROJECT_ROOT")-db-backups}"
mkdir -p "$BACKUP_DIR"
# An existing directory keeps whatever mode it was created with (755 before
# this change), which left earlier dumps listable and readable.
if ! chmod 700 "$BACKUP_DIR" 2>/dev/null; then
    print_warning "Could not set $BACKUP_DIR to mode 700 (not the owner, or a filesystem"
    print_warning "without Unix permissions) — make sure only you can read it."
fi
print_info "Backups → $BACKUP_DIR"
TS="$(date +%Y%m%d-%H%M%S)"

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

# --- An image this host already has, to run tar in --------------------------
# tar runs in a container because the files belong to the app's user (UID
# 999) and PGDATA to postgres, not to the operator.  It used to be `alpine`,
# which the stack never ships: on an isolated network the pull failed and the
# uploads were silently not backed up (review 2026-10-01 R30).  The database
# image is always present where there is a database to back up, and carries
# tar + gzip.  Taken from the db CONTAINER when one exists (the exact image it
# runs, by id), else from what compose resolves for the service; never pulled.
helper_image() {
    local cid img
    cid="$($DC ps -aq db 2>/dev/null | head -1)"
    if [[ -n "$cid" ]]; then
        img="$(docker inspect "$cid" --format '{{.Image}}' 2>/dev/null)"
        if [[ -n "$img" ]] && docker image inspect "$img" >/dev/null 2>&1; then
            printf '%s\n' "$img"
            return 0
        fi
    fi
    img="$($DC config --images db 2>/dev/null | head -1)"
    if [[ -n "$img" ]] && docker image inspect "$img" >/dev/null 2>&1; then
        printf '%s\n' "$img"
        return 0
    fi
    return 1
}

# A gzip'd tar that lists to the end was written completely.
archive_is_readable() {
    tar tzf "$1" >/dev/null 2>&1
}

# Size and SHA-256 of a finished artifact, for the .meta.
file_bytes() {
    stat -c %s "$1" 2>/dev/null || wc -c < "$1" | tr -d ' '
}
file_sha256() {
    _sha256_hex < "$1" 2>/dev/null || echo unknown
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

    # Read it back — all of it.  A non-empty file is not a restorable one.
    #
    # `pg_restore --list` alone proves little: it reads the table of contents,
    # which pg_dump writes at the FRONT of a custom-format archive, before any
    # table data.  A dump cut to half its size by a full disk still lists
    # every entry (checked on a real dump: 1050 of 1050).  So after the list —
    # kept for the entry count and the clearer message when the header itself
    # is bad — the whole archive is converted to SQL and thrown away
    # (`-f /dev/null`): pg_restore decompresses and parses every data block
    # and fails with "could not read from input file: end of file" on a
    # truncated one.  No database is touched (branch review S1).
    local entries
    if ! entries="$($DC exec -T db pg_restore --list < "$out" 2>/dev/null | grep -c '^[0-9]')" \
        || [[ "${entries:-0}" -eq 0 ]]; then
        print_error "The dump could not be read back (pg_restore --list) — it is NOT a usable backup."
        print_error "Check free disk space in $BACKUP_DIR and re-run."
        rm -f "$out"
        return 1
    fi
    print_info "Verifying the dump by reading it to its end..."
    if ! $DC exec -T db pg_restore -f /dev/null < "$out" >/dev/null 2>&1; then
        print_error "The dump is INCOMPLETE or corrupt: pg_restore could not read it to its end"
        print_error "(its table of contents lists $entries entries, but the data behind them is not all there)."
        print_error "It is NOT a usable backup and was removed. Check free disk space in $BACKUP_DIR and re-run."
        rm -f "$out"
        return 1
    fi
    chmod 600 "$out"

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
        echo "key_fingerprint=$(key_fingerprint || echo unknown)"
        echo "verified=pg_restore full read ($entries entries)"
        echo "bytes=$(file_bytes "$out")"
        echo "sha256=$(file_sha256 "$out")"
    } > "$meta"

    print_success "Backup written: $out ($(du -h "$out" | cut -f1))"
    print_success "Verified: pg_restore read all $entries entries and their data to the end of the file."
    print_info    "Schema revision: $rev"
    META_FILE="$meta"
    DB_ARTIFACT="$out"
}

# --- The files the database points at ---------------------------------------
# Evidence images (note_attachments), issued client reports (client_reports)
# and EyeWitness screenshots (web_screenshots) live under ./uploads, not in
# the database.  A database-only backup restored note_attachments rows and
# issued reports whose files were gone (review 2026-09-23 R9).  Left out on
# purpose: ingestion_queue (retained scan inputs, re-uploadable, often GBs),
# report_artifacts (download artifacts that expire anyway) and the one-time
# initial-admin-password.txt.
#
# The archive is streamed to stdout and written by THIS shell, so the file is
# the operator's and mode 0600 without sudo.  The container used to write it
# through a bind mount of the backup directory, which left a root-owned,
# world-readable archive the operator could not even delete.
backup_uploads() {
    local meta="$1"
    if [[ ! -d "$PROJECT_ROOT/uploads" ]]; then
        print_warning "No uploads/ directory — nothing but the database to back up."
        return 0
    fi
    local image
    if ! image="$(helper_image)"; then
        print_error "No database image on this host to run tar in (start the db container once,"
        print_error "or load the image) — evidence files are NOT backed up."
        return 1
    fi
    local out="nm-uploads-$TS.tar.gz"
    print_info "Archiving uploads/ (evidence images, issued reports, screenshots)..."
    if ! docker run --rm \
        -v "$PROJECT_ROOT/uploads":/uploads:ro \
        --entrypoint tar \
        "$image" czf - -C /uploads \
            --exclude=./ingestion_queue --exclude=./report_artifacts \
            --exclude=./initial-admin-password.txt . > "$BACKUP_DIR/$out" \
        || ! archive_is_readable "$BACKUP_DIR/$out"; then
        print_error "Archiving uploads/ failed — the database backup is kept, but evidence files are NOT backed up."
        rm -f "$BACKUP_DIR/$out"
        return 1
    fi
    chmod 600 "$BACKUP_DIR/$out"
    {
        echo "uploads_archive=$out"
        echo "uploads_bytes=$(file_bytes "$BACKUP_DIR/$out")"
        echo "uploads_sha256=$(file_sha256 "$BACKUP_DIR/$out")"
    } >> "$meta"
    print_success "Uploads archived: $BACKUP_DIR/$out ($(du -h "$BACKUP_DIR/$out" | cut -f1))"
}

do_volume_tar() {
    local vol image
    vol="$(resolve_volume)" || true
    if [[ -z "$vol" ]]; then
        print_error "Could not uniquely identify this stack's postgres volume."
        print_error "Start the db container, or set COMPOSE_PROJECT_NAME in .env."
        return 1
    fi
    if ! image="$(helper_image)"; then
        print_error "No database image on this host to run tar in — cannot snapshot the volume."
        return 1
    fi
    local out="nm-pgdata-$TS.tar.gz"
    print_info "Taking a raw volume snapshot of '$vol'..."
    if ! docker run --rm \
        -v "$vol":/data:ro \
        --entrypoint tar \
        "$image" czf - -C /data . > "$BACKUP_DIR/$out" \
        || ! archive_is_readable "$BACKUP_DIR/$out"; then
        print_error "Volume snapshot failed."
        rm -f "$BACKUP_DIR/$out"
        return 1
    fi
    chmod 600 "$BACKUP_DIR/$out"
    {
        echo "type=volume"
        echo "created=$TS"
        echo "volume=$vol"
        echo "key_fingerprint=$(key_fingerprint || echo unknown)"
        echo "verified=tar listing"
        echo "bytes=$(file_bytes "$BACKUP_DIR/$out")"
        echo "sha256=$(file_sha256 "$BACKUP_DIR/$out")"
    } > "$BACKUP_DIR/$out.meta"
    META_FILE="$BACKUP_DIR/$out.meta"
    DB_ARTIFACT="$BACKUP_DIR/$out"
    print_success "Backup written: $BACKUP_DIR/$out ($(du -h "$BACKUP_DIR/$out" | cut -f1))"
    print_warning "Raw volume snapshots restore only into the same PostgreSQL major version."
}

# --- Retention ---------------------------------------------------------------
# Nothing ever removed a backup, and every deploy takes one: the backup
# directory grew until the disk filled (review 2026-10-01 B11).  Keep the
# newest BACKUP_KEEP database artifacts, each with its .meta and the uploads
# archive its .meta names.  Never removed: the backup just made, and the
# pre-deploy dump recorded for a rollback.
prune_old_backups() {
    local just_made="$1" keep="${BACKUP_KEEP:-10}"
    if ! [[ "$keep" =~ ^[0-9]+$ ]]; then
        print_warning "BACKUP_KEEP='$keep' is not a number — nothing pruned."
        return 0
    fi
    if [[ "$keep" -eq 0 ]]; then
        print_info "BACKUP_KEEP=0 — every backup is kept."
        return 0
    fi

    local rollback_dump=""
    if [[ -f "$PROJECT_ROOT/.deploy-rollback-state" ]]; then
        rollback_dump="$(grep '^PREDEPLOY_DB_DUMP|' "$PROJECT_ROOT/.deploy-rollback-state" 2>/dev/null \
            | tail -1 | cut -d'|' -f2-)"
    fi

    local -a all=() doomed=()
    mapfile -t all < <(ls -1t "$BACKUP_DIR"/nm-pgdump-*.dump "$BACKUP_DIR"/nm-pgdata-*.tar.gz 2>/dev/null || true)
    local f uploads seen=0 pruned=0
    for f in "${all[@]}"; do
        seen=$((seen + 1))
        if [[ "$seen" -le "$keep" || "$f" == "$just_made" || "$f" == "$rollback_dump" ]]; then
            continue
        fi
        doomed+=("$f")
    done
    if [[ "${#doomed[@]}" -eq 0 ]]; then
        print_info "Retention: ${#all[@]} backup(s), keeping the newest $keep — nothing to prune."
        return 0
    fi

    # In steady state one backup ages out per run.  More than a few at once
    # is a BACKLOG — the first run with retention on a directory that was
    # never pruned, or a lowered BACKUP_KEEP — and deleting dozens of backups
    # as a side effect of an unrelated deploy is not something to do unasked.
    # Say what would go, and how to do it deliberately.
    local backlog_limit="${BACKUP_PRUNE_MAX:-3}"
    if [[ "${#doomed[@]}" -gt "$backlog_limit" && "${BACKUP_PRUNE_BACKLOG:-0}" != "1" ]]; then
        print_warning "Retention: ${#doomed[@]} backup(s) are older than the newest $keep (BACKUP_KEEP) — more than"
        print_warning "one run normally ages out, so NOTHING was deleted. Oldest: $(basename "${doomed[-1]}")"
        print_warning "Directory size: $(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1). To delete them, once:"
        print_warning "    BACKUP_PRUNE_BACKLOG=1 ./scripts/backup-db.sh"
        print_warning "To keep them all and silence this:  BACKUP_KEEP=0"
        return 0
    fi

    for f in "${doomed[@]}"; do
        uploads=""
        if [[ -f "$f.meta" ]]; then
            uploads="$(grep -E '^uploads_archive=' "$f.meta" 2>/dev/null | tail -1 | cut -d= -f2-)"
        fi
        rm -f -- "$f" "$f.meta"
        if [[ -n "$uploads" ]]; then
            rm -f -- "$BACKUP_DIR/$(basename "$uploads")"
        fi
        print_info "Pruned: $(basename "$f")${uploads:+ + $(basename "$uploads")}"
        pruned=$((pruned + 1))
    done
    print_info "Retention: pruned $pruned backup(s) older than the newest $keep (BACKUP_KEEP)."
}

report_free_space() {
    local free
    free="$(df -Ph "$BACKUP_DIR" 2>/dev/null | awk 'NR==2 {print $4}')"
    if [[ -n "$free" ]]; then
        print_info "Free space in the backup location: $free"
    fi
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

# Only after a database backup succeeded (set -e stops on a failed one).
UPLOADS_FAILED=0
if [[ -n "${META_FILE:-}" ]]; then
    backup_uploads "$META_FILE" || UPLOADS_FAILED=1
fi

if [[ "$UPLOADS_FAILED" -eq 1 ]]; then
    echo ""
    if [[ "$ALLOW_MISSING_UPLOADS" == "1" ]]; then
        print_warning "Continuing WITHOUT an uploads archive (--allow-missing-uploads): this backup"
        print_warning "restores the database only — evidence images and issued report files are not in it."
        echo "uploads_archive_missing=allowed" >> "$META_FILE"
    else
        print_error "BACKUP INCOMPLETE: the database artifact is kept at"
        print_error "    ${DB_ARTIFACT:-$BACKUP_DIR}"
        print_error "but ./uploads (evidence images, issued reports, screenshots) was NOT archived."
        print_info  "Fix the cause above and re-run, or accept a database-only backup with:"
        print_info  "    ./scripts/backup-db.sh --allow-missing-uploads"
        report_free_space
        exit 1
    fi
fi

echo ""
prune_old_backups "${DB_ARTIFACT:-}"
report_free_space
print_info "Restore with:  ./scripts/restore-db.sh <backup-file>"
