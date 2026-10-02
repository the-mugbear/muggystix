#!/bin/bash
#
# Is a boot migration working, blocked, or restarting?  Read-only, and safe to
# run WHILE a migration is in progress.
#
# What it shows:
# - the containers and how long each has been up (a backend that keeps
#   restarting starts the upgrade over every time);
# - the revisions logged by whichever process runs the upgrade ("Running upgrade A -> B"), how many,
#   and any migration failure message;
# - the schema revision stamped in the database (it only moves when the whole
#   upgrade commits — the upgrade is ONE transaction);
# - what the database is doing right now: every non-idle session except this
#   script's own, how long its transaction and its current statement have run,
#   and whether it is waiting on a lock;
# - who blocks whom, when anything is waiting.
#
# How to read it: ONE session whose pid stays the same between runs, with
# "in_transaction" about as long as you have been waiting, is the migration.
# It is working unless its wait_event_type is "Lock".  A pid that changes on
# every run is not progress: it is a process polling for the migration lock,
# or a backend that is restarting.
#
# It never waits behind the migration: its own queries read only the system
# catalogs and alembic_version, and give up after 5 seconds on a lock.
# Do not interrupt a running migration — a kill rolls the whole upgrade back
# and the next start repeats it.
#
# Usage: ./scripts/migration-status.sh [--watch [SECONDS]]
#

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"

WATCH=0
if [[ "${1:-}" == "--watch" ]]; then
    WATCH="${2:-30}"
    [[ "$WATCH" =~ ^[0-9]+$ && "$WATCH" -ge 5 ]] || { echo "--watch takes a number of seconds, 5 or more" >&2; exit 2; }
elif [[ -n "${1:-}" ]]; then
    echo "Usage: $0 [--watch [SECONDS]]" >&2
    exit 2
fi

if docker compose version >/dev/null 2>&1; then
    DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
    DC=(docker-compose)
else
    echo "No docker compose binary on PATH." >&2
    exit 1
fi

# Read (never source) .env — its values are data, not shell.
env_value() {
    [[ -f .env ]] || return 0
    { grep -E "^[[:space:]]*$1=" .env || true; } | tail -n 1 | cut -d'=' -f2- | sed -E 's/^["'\'']//; s/["'\'']$//'
}
DB_NAME=$(env_value POSTGRES_DB); DB_NAME=${DB_NAME:-networkMapper}
DB_USER=$(env_value POSTGRES_USER); DB_USER=${DB_USER:-nmapuser}

# Every query gives up rather than queue behind the migration's locks.
psql_q() {
    "${DC[@]}" exec -T -e PGOPTIONS='-c lock_timeout=5000 -c statement_timeout=15000' \
        db psql -X -P pager=off -U "$DB_USER" -d "$DB_NAME" "$@"
}

section() { echo ""; echo "=== $1 ==="; }

report() {
    echo "BlueStick migration status — $(date -u +"%Y-%m-%d %H:%M:%SZ")"

    section "Containers (a backend that is 'Restarting', or up for only seconds, is starting the upgrade over)"
    "${DC[@]}" ps --format 'table {{.Service}}\t{{.State}}\t{{.Status}}' 2>&1 \
        || "${DC[@]}" ps 2>&1

    # Whichever process takes the migration lock first runs the upgrade: the
    # backend or one of the two workers.
    section "Revisions logged (backend, worker, report-worker — whichever took the migration lock)"
    local upgrades
    upgrades=$("${DC[@]}" logs backend worker report-worker 2>/dev/null | grep "Running upgrade" || true)
    if [[ -z "$upgrades" ]]; then
        echo "(no 'Running upgrade' line in the logs — no migration has started in these containers, or the log rotated)"
    else
        echo "steps logged: $(echo "$upgrades" | wc -l)   distinct: $(echo "$upgrades" | sed -E 's/.*Running upgrade //' | sort -u | wc -l)"
        echo "(more steps than distinct revisions means the upgrade has started over)"
        echo "$upgrades" | tail -n 5 | cut -c1-220
    fi
    local failures
    failures=$("${DC[@]}" logs --tail 400 backend worker report-worker 2>/dev/null \
        | grep -E "NOTHING WAS CHANGED|schema left PARTWAY|Traceback|refus|still migrating" | tail -n 5 || true)
    if [[ -n "$failures" ]]; then
        echo ""
        echo "Messages worth reading (newest last):"
        echo "$failures" | cut -c1-300
    fi

    section "Schema revision stamped in the database (moves only when the whole upgrade commits)"
    psql_q -A -t -c "SELECT version_num FROM alembic_version;" 2>&1 || echo "(could not read alembic_version)"

    section "What the database is doing now (this script's own session left out)"
    psql_q -c "
        SELECT pid, state, coalesce(wait_event_type, '-') AS wait_event_type, coalesce(wait_event, '-') AS wait_event,
               date_trunc('second', now() - xact_start)  AS in_transaction,
               date_trunc('second', now() - query_start) AS this_statement,
               left(regexp_replace(query, '\s+', ' ', 'g'), 100) AS statement
          FROM pg_stat_activity
         WHERE pid <> pg_backend_pid() AND datname = current_database() AND state <> 'idle'
         ORDER BY xact_start NULLS LAST;" 2>&1 || echo "(query failed)"

    section "Who is waiting on whom (empty = nothing is blocked)"
    psql_q -c "
        SELECT w.pid AS waiting_pid, b.pid AS blocked_by_pid, b.state AS blocker_state,
               date_trunc('second', now() - b.xact_start) AS blocker_in_transaction,
               left(regexp_replace(w.query, '\s+', ' ', 'g'), 70) AS waiting_statement,
               left(regexp_replace(b.query, '\s+', ' ', 'g'), 70) AS blocker_statement
          FROM pg_stat_activity w
          JOIN LATERAL unnest(pg_blocking_pids(w.pid)) AS blocker(pid) ON true
          JOIN pg_stat_activity b ON b.pid = blocker.pid
         WHERE w.datname = current_database();" 2>&1 || echo "(query failed)"

    echo ""
    echo "Reading it: one pid that stays the same between runs, with in_transaction about as long as"
    echo "you have waited, is the migration. It is working unless wait_event_type is Lock."
}

if [[ "$WATCH" -gt 0 ]]; then
    while true; do
        clear 2>/dev/null || true
        report
        echo ""
        echo "(refreshing every ${WATCH}s — Ctrl-C to stop)"
        sleep "$WATCH"
    done
else
    report
fi
