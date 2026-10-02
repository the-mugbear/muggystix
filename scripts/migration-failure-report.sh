#!/bin/bash
#
# A boot migration failed: collect, in ONE text file, what is needed to say why
# and what to do next.  Read-only — it changes nothing, restarts nothing, and
# its database queries give up after 5 seconds on a lock.
#
# What it collects:
#  1. the verdict, when the logs state one: NOTHING WAS CHANGED (the upgrade
#     rolled back; the database is at the revision it started from) or schema
#     left PARTWAY (restore the pre-deploy backup);
#  2. the failure itself — the last traceback and migration message from the
#     backend, worker and report-worker, and PostgreSQL's own ERROR / FATAL
#     lines (the database usually names the real cause);
#  3. how far it got — the last "Running upgrade" lines, and whether the
#     upgrade has started over;
#  4. the revision stamped in the database, the revision the deploy recorded
#     before it started, and the head the code expects;
#  5. the containers — state, restart count, exit code and whether the kernel
#     killed one for memory (OOMKilled), with each memory limit;
#  6. disk space, and the database's size and largest tables (row counts only);
#  7. the database settings that can end a long migration (timeouts);
#  8. the one known refusal: projects with two scanner findings for one issue
#     (a count — revision b2e5a8c1d4f6 will not run until they are resolved);
#  9. live sessions and lock waits, as migration-status.sh shows them.
#
# What it leaves out: SQL parameter values ("[parameters: …]") are removed and
# IPv4 addresses are replaced.  It is NOT the anonymised bundle collect-logs.sh
# makes — an error message can still name a host or a finding — so READ THE
# FILE before sending it anywhere.
#
# Usage: ./scripts/migration-failure-report.sh [--since 6h] [--out FILE]
#

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(dirname "$SCRIPT_DIR")"

SINCE="6h"
OUT=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --since) SINCE="${2:?--since needs a duration, e.g. 6h}"; shift 2 ;;
        --out)   OUT="${2:?--out needs a file name}"; shift 2 ;;
        *) echo "Usage: $0 [--since 6h] [--out FILE]" >&2; exit 2 ;;
    esac
done
OUT=${OUT:-"bluestick_migration_failure_$(date -u +"%Y%m%d_%H%M%SZ").txt"}

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

psql_q() {
    "${DC[@]}" exec -T -e PGOPTIONS='-c lock_timeout=5000 -c statement_timeout=30000' \
        db psql -X -P pager=off -U "$DB_USER" -d "$DB_NAME" "$@"
}
q() {  # LABEL SQL
    echo ""
    echo "--- $1 ---"
    psql_q -c "$2" 2>&1 || echo "(query failed — the table may be locked or absent at this revision)"
}
section() { echo ""; echo "================================================================"; echo "== $1"; echo "================================================================"; }

# Parameter values out, IPv4 addresses replaced, lines kept to a readable width.
redact() {
    sed -E 's/\[parameters: .*$/[parameters: removed]/; s/\b([0-9]{1,3}\.){3}[0-9]{1,3}\b/<ip>/g' | cut -c1-600
}

APP_LOGS=$("${DC[@]}" logs --since "$SINCE" backend worker report-worker 2>&1 || true)
DB_LOGS=$("${DC[@]}" logs --since "$SINCE" db 2>&1 || true)

{
    echo "BlueStick migration failure report — $(date -u +"%Y-%m-%d %H:%M:%SZ")"
    echo "Logs since: $SINCE.  SQL parameter values removed, IPv4 addresses replaced."
    echo "NOT anonymised beyond that: read this file before sending it anywhere."

    section "1. Verdict stated by the application (newest last)"
    verdict=$(echo "$APP_LOGS" | grep -E "NOTHING WAS CHANGED|schema left PARTWAY" | tail -n 3 | redact)
    if [[ -n "$verdict" ]]; then
        echo "$verdict"
        echo ""
        echo "NOTHING WAS CHANGED = the upgrade is one transaction and it rolled back: the database is at"
        echo "the revision it started from; no restore is needed.  schema left PARTWAY = restore the"
        echo "pre-deploy backup (./scripts/deploy.sh option 7 restores first when the revision moved)."
    else
        echo "(neither message is in the logs since $SINCE — the process may have been killed before it"
        echo " could report, see section 5, or the logs rotated: re-run with a longer --since)"
    fi

    section "2a. The failure — last traceback and migration messages (backend, worker, report-worker)"
    # The LAST traceback block: from its 'Traceback' line to the end of the
    # exception that follows it.
    echo "$APP_LOGS" | awk '
        /Traceback \(most recent call last\)/ { block = ""; capture = 1; lines = 0; after = -1 }
        capture { block = block $0 "\n"; lines++ }
        # The exception line ends the traceback; keep the 25 lines after it —
        # the statement SQLAlchemy prints, or the rest of a multi-line message.
        capture && after < 0 && /^[^|]*\| *[A-Za-z_.]+(Error|Exception|Refused|Failure)[:(]/ { after = 25 }
        capture && after >= 0 { if (after == 0) { last = block; capture = 0 } after-- }
        capture && lines > 160 { last = block; capture = 0 }
        END { if (capture) last = block; printf "%s", last }' | redact | tail -n 190
    echo ""
    echo "--- every line that names an error (last 40) ---"
    echo "$APP_LOGS" | grep -E "ERROR|CRITICAL|Error:|Exception|refus|cannot be created|Killed|MemoryError|No space left" \
        | grep -v "Running upgrade" | tail -n 40 | redact

    section "2b. The failure — PostgreSQL's own ERROR / FATAL / PANIC lines (last 40)"
    echo "$DB_LOGS" | grep -E "ERROR|FATAL|PANIC|terminating|canceling|deadlock|out of memory|No space left|could not" \
        | tail -n 40 | redact

    section "3. How far it got"
    upgrades=$(echo "$APP_LOGS" | grep "Running upgrade" || true)
    if [[ -z "$upgrades" ]]; then
        echo "(no 'Running upgrade' line since $SINCE)"
    else
        echo "steps logged: $(echo "$upgrades" | wc -l)   distinct revisions: $(echo "$upgrades" | sed -E 's/.*Running upgrade //' | sort -u | wc -l)"
        echo "(more steps than distinct revisions = the upgrade started over, i.e. a process restarted)"
        echo "--- first step ---"
        echo "$upgrades" | head -n 1 | cut -c1-240
        echo "--- last 8 steps ---"
        echo "$upgrades" | tail -n 8 | cut -c1-240
    fi

    section "4. Revisions: stamped in the database / recorded before the deploy / expected by the code"
    echo "stamped in the database now: $(psql_q -A -t -c "SELECT version_num FROM alembic_version;" 2>&1 | tr '\n' ' ')"
    if [[ -f .deploy-rollback-state ]]; then
        echo "recorded by deploy.sh before it started:"
        grep -E "^(PREDEPLOY_ALEMBIC_REVISION|PREDEPLOY_DB_DUMP|DEPLOY_IN_PROGRESS)" .deploy-rollback-state | sed 's/^/  /' || true
    else
        echo "(.deploy-rollback-state not found — this start was not made by deploy.sh option 1)"
    fi
    # The head the tree expects: the one revision no other revision names as
    # its parent.  Read from the files, so it works with every container down.
    if [[ -d backend/alembic/versions ]]; then
        all=$(grep -hE "^revision *(: *str *)?= *['\"]" backend/alembic/versions/*.py | sed -E "s/.*['\"]([0-9a-f]+)['\"].*/\1/" | sort -u)
        parents=$(grep -hE "^down_revision" backend/alembic/versions/*.py | grep -oE "[0-9a-f]{12}" | sort -u)
        echo "head expected by this source tree: $(comm -23 <(echo "$all") <(echo "$parents") | tr '\n' ' ')"
    fi
    [[ -f platform_version.json ]] && echo "platform_version.json: $(tr -d '\n ' < platform_version.json)"

    section "5. Containers: state, restarts, exit code, killed for memory"
    for svc in db backend worker report-worker frontend; do
        cid=$("${DC[@]}" ps -a -q "$svc" 2>/dev/null | head -n 1)
        if [[ -z "$cid" ]]; then
            echo "$svc: no container"
            continue
        fi
        docker inspect --format "$svc: status={{.State.Status}} restarts={{.RestartCount}} exit_code={{.State.ExitCode}} oom_killed={{.State.OOMKilled}} started={{.State.StartedAt}} finished={{.State.FinishedAt}} memory_limit_bytes={{.HostConfig.Memory}}" "$cid" 2>&1
    done
    echo ""
    echo "oom_killed=true, or exit_code=137, means the kernel stopped the process for memory: the"
    echo "migration did not fail on its own, and raising that service's limit in .env is the fix"
    echo "(BACKEND_MEM_LIMIT, WORKER_MEM_LIMIT, REPORT_WORKER_MEM_LIMIT)."
    echo ""
    echo "--- host memory ---"
    free -h 2>/dev/null || echo "(free not available)"

    section "6. Disk space and database size"
    df -h . 2>/dev/null
    docker system df 2>/dev/null || true
    q "Database size on disk" "SELECT pg_size_pretty(pg_database_size(current_database())) AS database_size;"
    q "Largest tables (estimated rows)" "SELECT relname AS table, n_live_tup AS rows, pg_size_pretty(pg_total_relation_size(relid)) AS size FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 15;"

    section "7. Database settings that can end a long migration"
    # Without this script's own timeouts (PGOPTIONS), which would otherwise
    # be reported as the server's.
    echo ""
    echo "--- Timeouts and memory (the server's values) ---"
    "${DC[@]}" exec -T db psql -X -P pager=off -U "$DB_USER" -d "$DB_NAME" -c "SELECT name, setting, unit, source FROM pg_settings WHERE name IN ('statement_timeout','lock_timeout','idle_in_transaction_session_timeout','idle_session_timeout','tcp_keepalives_idle','max_locks_per_transaction','maintenance_work_mem','work_mem','shared_buffers','max_wal_size','temp_file_limit') ORDER BY name;" 2>&1 || echo "(query failed)"

    section "8. Known refusal: two scanner findings for one issue (revision b2e5a8c1d4f6)"
    q "Issues with more than one scanner finding (must be 0 before that revision runs)" "SELECT count(*) AS duplicated_issues, coalesce(sum(n), 0) AS findings_involved FROM (SELECT count(*) AS n FROM findings WHERE source = 'scanner' AND dedup_key IS NOT NULL AND dedup_key NOT LIKE 'row:%' GROUP BY project_id, dedup_key HAVING count(*) > 1) d;"
    q "Invalid indexes left by an interrupted build" "SELECT c.relname AS index, t.relname AS on_table FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid JOIN pg_class t ON t.oid = i.indrelid WHERE NOT i.indisvalid;"

    section "9. Live sessions and lock waits"
    q "Sessions that are not idle" "SELECT pid, state, coalesce(wait_event_type, '-') AS wait_event_type, date_trunc('second', now() - xact_start) AS in_transaction, date_trunc('second', now() - query_start) AS this_statement, left(regexp_replace(query, '\\s+', ' ', 'g'), 100) AS statement FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND datname = current_database() AND state <> 'idle' ORDER BY xact_start NULLS LAST;"
    q "Who is waiting on whom" "SELECT w.pid AS waiting_pid, b.pid AS blocked_by_pid, date_trunc('second', now() - b.xact_start) AS blocker_in_transaction, left(regexp_replace(b.query, '\\s+', ' ', 'g'), 70) AS blocker_statement FROM pg_stat_activity w JOIN LATERAL unnest(pg_blocking_pids(w.pid)) AS blocker(pid) ON true JOIN pg_stat_activity b ON b.pid = blocker.pid WHERE w.datname = current_database();"
} > "$OUT" 2>&1

chmod 600 "$OUT" 2>/dev/null || true
echo "Wrote $OUT ($(wc -l < "$OUT") lines)."
echo "Read it before sending it anywhere: SQL parameter values and IPv4 addresses are removed, but an"
echo "error message can still name a host or a finding."
echo ""
awk '/^== 2a\. /{exit} /^== 1\. /{show=1} show && !/^====/' "$OUT"
