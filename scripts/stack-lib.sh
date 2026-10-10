#!/bin/bash
#
# BlueStick — shared stack helpers, SOURCED by deploy.sh and restore-db.sh.
# Not run by hand.
#
# One implementation of "start the stack and know what state it is in"
# (review 2026-10-01, branch review S3/S4): deploy.sh option 1, the rollback
# (option 7) and restore-db.sh each had their own start and their own wait,
# and two of the three ended silently under `set -e` when `up` failed.
#
# The caller defines, before sourcing:
#   DC                               the compose command ("docker compose")
#   print_info / print_success / print_warning / print_error
# and runs from the project root (the functions read ./.env).
#
# Nothing here exits the script except `ask`, at end of input with no default.
#
# (A file beside the scripts rather than in scripts/lib/: the repository's
# .gitignore ignores every `lib/` folder.)
#

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    echo "stack-lib.sh is sourced by deploy.sh and restore-db.sh; it does nothing on its own." >&2
    exit 2
fi

# backend/app/db/init.py _MIGRATION_LOCK_KEY — the session-level advisory lock
# held for the whole of `alembic upgrade head`.
MIGRATION_LOCK_KEY=738582901
# Restarts of ONE container before it counts as crash-looping (a single
# restart can be a database that was not accepting connections yet).
BACKEND_RESTART_LIMIT=3

# ------------------------------------------------------------------
# ask VAR "prompt" [default]
#
# `read` returns non-zero at end of input, and under `set -e` that ended the
# script without a word — upgrade-instance.sh pipes exactly "1\n" into
# deploy.sh, so any further prompt (low disk, a missing .env) killed the
# deploy silently.  With a default the default is taken and said; without
# one the script stops with a message naming the prompt.
# ------------------------------------------------------------------
ask() {
    local __var="$1" __prompt="$2" __reply=""
    printf '%s' "$__prompt"
    if ! IFS= read -r __reply && [[ -z "$__reply" ]]; then
        echo ""
        if [[ $# -ge 3 ]]; then
            print_info "No answer on standard input — taking the default: '${3}'."
            __reply="$3"
        else
            print_error "No answer on standard input (end of input) at: ${__prompt}"
            print_error "This step needs an operator. Run the script from a terminal and answer it."
            exit 1
        fi
    fi
    printf -v "$__var" '%s' "$__reply"
}

backend_probe() {
    $DC exec -T backend python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=3).status==200 else 1)" >/dev/null 2>&1
}

# The migration's own failure line from the backend log of the last $1
# seconds, or nothing.  A failed migration does NOT stop the container:
# uvicorn's supervisor stays up and respawns the workers, each of which
# re-runs the upgrade and fails again — status "running", restart count 0,
# for as long as anyone waits (2026-10-02: a deploy waited on exactly that).
backend_migration_failure() {
    $DC logs --since "${1:-300}s" backend 2>/dev/null | grep "DATABASE MIGRATION FAILED" | tail -n 1 | cut -c1-700
}

# "<status> <restart count>" of the backend container, e.g. "running 0".
backend_container_state() {
    local cid
    cid="$($DC ps -aq backend 2>/dev/null | head -1)"
    if [[ -z "$cid" ]]; then
        echo "missing 0"
        return 0
    fi
    docker inspect "$cid" --format '{{.State.Status}} {{.RestartCount}}' 2>/dev/null || echo "unknown 0"
}

_stack_env_val() { grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2-; }

# Run one SQL statement in the db container; prints the bare result.
stack_psql() {
    local pg_user pg_db
    pg_user="$(_stack_env_val POSTGRES_USER)"
    pg_db="$(_stack_env_val POSTGRES_DB)"
    $DC exec -T db psql -U "${pg_user:-nmapuser}" -d "${pg_db:-networkMapper}" -tAc "$1" 2>/dev/null
}

migration_lock_held() {
    local held
    held="$(stack_psql "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted AND classid = 0 AND objid = ${MIGRATION_LOCK_KEY}" | tr -dc '0-9')"
    [[ -n "$held" && "$held" -gt 0 ]]
}

# The schema revision the database is at; empty when it cannot be read (db
# down, or a database with no alembic_version table).
database_alembic_revision() {
    stack_psql "SELECT version_num FROM alembic_version" | tr -d '[:space:]'
}

# Wait for PostgreSQL to accept connections.  $1 = seconds (default 60).
wait_for_database() {
    local timeout="${1:-60}" waited=0 pg_user pg_db
    pg_user="$(_stack_env_val POSTGRES_USER)"
    pg_db="$(_stack_env_val POSTGRES_DB)"
    while [[ "$waited" -lt "$timeout" ]]; do
        if $DC exec -T db pg_isready -U "${pg_user:-nmapuser}" -d "${pg_db:-networkMapper}" >/dev/null 2>&1; then
            return 0
        fi
        sleep 2
        waited=$((waited + 2))
    done
    return 1
}

# ------------------------------------------------------------------
# Waiting for the backend (review 2026-10-01 R31)
#
# The backend runs `alembic upgrade head` at import, before it serves
# anything, so "not healthy yet" has two very different meanings:
#
#   * still migrating — the container is running and a session holds the
#     migration advisory lock.  The only correct action is to wait; rolling
#     back the images now would point the OLD code at a half-migrated schema.
#   * crashed — the container exited, or Docker keeps restarting it;
#   * the migration failed — the container stays "running" while its workers
#     retry and fail (see backend_migration_failure).
#
# $1 = seconds to wait.  Returns 0 healthy, 2 crashed / crash-looping,
# 1 still starting at the timeout.
# ------------------------------------------------------------------
wait_for_backend_healthy() {
    local timeout="${1:-1800}" elapsed=0 status restarts last_note=0 failure
    print_info "Waiting for the backend (schema migrations run before it serves; up to ${timeout}s)..."
    while [[ "$elapsed" -lt "$timeout" ]]; do
        if backend_probe; then
            print_success "Backend is healthy (boot + migrations succeeded)."
            return 0
        fi
        read -r status restarts <<< "$(backend_container_state)"
        case "$status" in
            exited|dead)
                print_error "The backend container has stopped (state: $status)."
                return 2
                ;;
        esac
        if [[ "${restarts:-0}" =~ ^[0-9]+$ && "${restarts:-0}" -ge "$BACKEND_RESTART_LIMIT" ]]; then
            print_error "The backend container has restarted ${restarts} times — it is crash-looping."
            return 2
        fi
        failure="$(backend_migration_failure $((elapsed + 120)))"
        if [[ -n "$failure" ]]; then
            print_error "The schema migration FAILED — the backend will keep retrying it and failing the same way."
            echo "  ${failure#*| }"
            print_info "What failed and why, in one file: ./scripts/migration-failure-report.sh"
            return 2
        fi
        if (( elapsed - last_note >= 30 )); then
            last_note=$elapsed
            if migration_lock_held; then
                print_info "  ${elapsed}s — a schema migration is running (migration lock held). Waiting; do not interrupt."
            else
                print_info "  ${elapsed}s — backend is ${status:-starting}, restarts: ${restarts:-0}."
            fi
        fi
        sleep "${STACK_WAIT_INTERVAL:-5}"
        elapsed=$((elapsed + 5))
    done
    return 1
}

# ------------------------------------------------------------------
# Staged start (branch review S4).
#
# The backend ALONE first: ONE process migrates, and nothing else is
# connected while it does.  Every process runs `alembic upgrade head` at
# start under the migration advisory lock, so a worker started beside the
# backend would either be the one that migrates (and the health wait below
# watches the backend) or sit waiting for the lock.  The workers are
# therefore stopped before the backend starts and started only after it is
# healthy.
#
# (This used to matter for a second reason: a waiter blocked inside
# `pg_advisory_lock` held a snapshot, and a `CREATE INDEX CONCURRENTLY` in
# the migrating process waits for every other snapshot — each side waited on
# the other.  Waiters now poll `pg_try_advisory_lock` idle, and the current
# release's revisions build their indexes inside the upgrade transaction.
# Older revisions in the chain — a first install, an upgrade from a much
# older build — still build concurrently, and any other open transaction on
# the database stalls those; one more reason to start with nothing else
# connected.)
#
#   stack_start_backend [--no-build]   stop the workers; up -d db backend.
#                                      Returns `up`'s status.
#   stack_start_rest [--no-build]      up -d worker report-worker, then
#                                      up -d (the frontend).  Returns the
#                                      first non-zero status.
#   stack_start_staged TIMEOUT [--no-build]
#                                      both, with the wait between.  Returns
#                                      0 everything up and the backend healthy,
#                                      1 backend still starting at the timeout,
#                                      2 backend crashed / crash-looping,
#                                      3 backend healthy but a later `up` failed.
#                                      STACK_UP_RC holds the first `up`'s status.
# No `up` here can end the calling script: every status is captured.
# ------------------------------------------------------------------
stack_start_backend() {
    local rc=0
    $DC stop worker report-worker >/dev/null 2>&1 || true
    # shellcheck disable=SC2086
    $DC up -d ${1:-} db backend || rc=$?
    return "$rc"
}

stack_start_rest() {
    local rc=0 rc_all=0
    # shellcheck disable=SC2086
    $DC up -d ${1:-} worker report-worker || rc=$?
    # shellcheck disable=SC2086
    $DC up -d ${1:-} || rc_all=$?
    [[ "$rc" -ne 0 ]] && return "$rc"
    return "$rc_all"
}

stack_start_staged() {
    local timeout="$1" no_build="${2:-}" wait_rc=0
    STACK_UP_RC=0
    print_info "Starting the database and the backend (the workers wait until it is healthy)..."
    stack_start_backend "$no_build" || STACK_UP_RC=$?
    if [[ "$STACK_UP_RC" -ne 0 ]]; then
        print_warning "'$DC up' returned $STACK_UP_RC — checking the backend before deciding what that means."
    fi
    wait_for_backend_healthy "$timeout" || wait_rc=$?
    if [[ "$wait_rc" -ne 0 ]]; then
        return "$wait_rc"
    fi
    print_info "Starting the workers and the frontend..."
    if ! stack_start_rest "$no_build"; then
        return 3
    fi
    return 0
}

# ------------------------------------------------------------------
# print_tunable_settings
#
# The settings an operator is most often told to change after a look at a
# host's logs (memory, Postgres sizing, timeouts, retention), each with the
# value in force and where it comes from — so that they are known to exist
# before a problem asks for them.  Read-only; never prints a secret (the list
# below is the whole of what is shown).
#
# The value: .env, else — for the settings the SCRIPTS read — the shell's
# environment, else the default.  The default is read from where it is
# defined (docker-compose.yml's or a script's ``${NAME:-default}``, else the
# commented ``# NAME=value`` line of .env.example), never repeated here, so
# it cannot drift.  A default that cannot be found is said to be built in.
# ------------------------------------------------------------------
TUNABLE_SETTINGS=(
    "Memory"
    "BACKEND_MEM_LIMIT|API container memory limit"
    "WORKER_MEM_LIMIT|import worker memory limit (raise for Nessus files over 2 GB)"
    "REPORT_WORKER_MEM_LIMIT|report worker memory limit"
    "FRONTEND_MEM_LIMIT|web server (nginx) memory limit"
    "Database (PostgreSQL)"
    "PG_SHARED_BUFFERS|Postgres cache; about a quarter of the host's RAM on a dedicated host"
    "PG_EFFECTIVE_CACHE_SIZE|what the planner assumes the OS caches"
    "PG_WORK_MEM|memory per sort or hash, per query step"
    "PG_MAINTENANCE_WORK_MEM|memory for index builds (boot migrations)"
    "PG_MAX_CONNECTIONS|must exceed UVICORN_WORKERS x (DB_POOL_SIZE + DB_MAX_OVERFLOW) plus the two workers"
    "PG_LOG_MIN_DURATION_MS|statements slower than this are logged"
    "API"
    "UVICORN_WORKERS|API worker processes"
    "DB_POOL_SIZE|connections each API worker keeps"
    "DB_MAX_OVERFLOW|extra connections each API worker may open"
    "API_STATEMENT_TIMEOUT_MS|a page's query is cancelled after this (503)"
    "SLOW_REQUEST_MS|requests slower than this are logged as SLOW"
    "Imports"
    "MAX_FILE_SIZE|largest scan upload, bytes (nginx allows 2 GB; the lower wins)"
    "MAX_REQUEST_BODY_BYTES|largest other request body, bytes"
    "INGESTION_JOB_TIMEOUT|seconds one import may run"
    "INGESTION_ORPHAN_CUTOFF_MULTIPLIER|a silent import is declared dead after this many timeouts"
    "INGESTION_RETAIN_FILES_DAYS|days an imported file is kept for re-processing"
    "Reports"
    "REPORT_RENDER_TIMEOUT_SECONDS|seconds one report render may run"
    "REPORT_WORKER_STOP_GRACE|keep above the render timeout"
    "Deploy, logs and backups"
    "DEPLOY_HEALTH_TIMEOUT|seconds a deploy waits for the backend (covers the boot migration)"
    "BACKEND_HEALTH_START_PERIOD|how long Docker calls the backend 'starting'"
    "BACKEND_STOP_GRACE|time a request in flight gets to finish on a rebuild"
    "MIN_FREE_GB|free disk a deploy asks for before it builds"
    "LOG_MAX_SIZE|size of one container log file"
    "LOG_MAX_FILE|log files kept per container"
    "BACKUP_KEEP|database backups kept (0 = all)"
    "BACKUP_WARN_DAYS|status.sh warns when the newest backup is older"
)

# The default of $1 where it is defined, or nothing.
_tunable_default() {
    local name="$1" found="" file
    for file in docker-compose.yml scripts/*.sh; do
        [[ -f "$file" ]] || continue
        found="$(grep -o "\${${name}:-[^}]*}" "$file" 2>/dev/null | head -n 1)"
        if [[ -n "$found" ]]; then
            found="${found#*:-}"
            printf '%s\n' "${found%\}}"
            return 0
        fi
    done
    if [[ -f .env.example ]]; then
        grep -E "^# ?${name}=[^ ]+$" .env.example 2>/dev/null | head -n 1 | cut -d= -f2-
    fi
}

print_tunable_settings() {
    local entry name what value source default
    echo ""
    print_info "Settings you can tune (value in force; change one in .env, then run option 1 again):"
    for entry in "${TUNABLE_SETTINGS[@]}"; do
        if [[ "$entry" != *"|"* ]]; then
            echo "  $entry"
            continue
        fi
        name="${entry%%|*}"
        what="${entry#*|}"
        default="$(_tunable_default "$name")"
        value="$(_stack_env_val "$name")"
        if [[ -n "$value" ]]; then
            source=".env"
        elif [[ -n "${!name:-}" && "${!name}" != "$default" ]]; then
            # (A script that already applied its own default has the variable
            # set to it: that is still the default.)
            value="${!name}"
            source="environment"
        else
            value="${default:-built-in}"
            source="default"
        fi
        printf '    %-36s %-14s %-12s %s\n' "$name" "$value" "($source)" "$what"
    done
    echo "  Every setting, with its explanation: .env.example"
}

# What to type when a migration seems stuck (printed by the callers' "still
# starting" branch; README "Upgrading" carries the same text).
print_migration_stall_help() {
    print_info "If the migration lock is held and nothing moves for a long time, look for a"
    print_info "session waiting on it (a worker started by hand):"
    print_info "    $DC exec db psql -U <user> -d <database> -c \"SELECT pid, application_name, state, wait_event_type, wait_event, left(query, 60) FROM pg_stat_activity WHERE datname = current_database() AND pid <> pg_backend_pid()\""
    print_info "Safe to do:   $DC stop worker report-worker     (start them again when the backend is healthy)"
    print_info "Never restart the backend while it holds the migration lock."
}
