#!/usr/bin/env bash
#
# BlueStick — the gate: one command for "is this tree releasable?"
#
#   ./scripts/check.sh            # everything
#   ./scripts/check.sh --fast     # skip the Alembic round trip (step 4)
#   ./scripts/check.sh --only backend     # one step: backend | ruff | frontend | alembic
#   make check                    # the same as the first
#
# There is no hosted CI: this is what stands between a change and main.  It
# was four hand-typed commands in two images, and the documented backend
# recipe (the `backend` image) has no Quarto — so the client-report contract
# tests, the hostile-text test among them, SKIPPED and the run was green
# (review 2026-10-01 B7).
#
# Steps (all are run even when one fails, so the summary is complete):
#
#   1. Backend suite in the REPORT-WORKER image — the backend image plus
#      Quarto — with report-templates/ mounted, SHARDED (below).  FAILS if
#      any test was skipped for want of Quarto, the templates or another
#      mount (scripts/, the agent guide, the repository root): here they
#      must run.
#   2. Backend lint: `ruff check` in the same image — pyflakes (F) rules only
#      (backend/ruff.toml), --no-cache.  Any finding fails the gate.
#   3. Frontend: `tsc --noEmit`, `eslint` (no warnings allowed), then
#      `vitest run`, then the Playwright suites (`npx playwright test`:
#      presentation mode's rendered overflow check and multi-user run,
#      against the Vite dev server and an in-memory backend — no stack
#      needed; Chromium from `npx playwright install chromium`).  On the
#      host; needs node.
#   4. scripts/test-alembic-roundtrip.sh — every migration down and back up
#      against a throwaway Postgres, then `alembic check` (models vs
#      migrations).  Skipped by --fast.
#
# Exit status: 0 only if every step that ran passed.
#
# It follows the documented recipe: a one-off container started from the
# repository root, the working tree's backend/ mounted over /app, the tests
# mounted explicitly (so a git worktree tests ITS tests, not the main
# checkout's).  Nothing here can migrate the development database: importing
# the app runs no migration (only `python -m app.db.migrate` does, and the
# containers here replace the image's command).  The stack must be up (the suite creates and drops
# its own databases on the running db container); nothing is built or pulled.
#
# Shards (step 1).  The suite's test FILES are split into CHECK_SHARDS groups
# (default 4) and each group runs at once in its own container: its own
# pytest process, its own test database (the name carries the container's
# host name — backend/tests/conftest.py).  No package does this (pytest-xdist
# would be a new dependency); the script does.
#
#   * Every test file runs exactly once.  The files are listed from
#     backend/tests, each is given to one shard, and before anything starts
#     the shards' lists together are compared with a second, independent
#     listing — a file in no shard, or in two, fails the step.
#   * Balanced by time, not by count: scripts/check-shard-weights.txt holds
#     seconds per test file; files are dealt heaviest first to the lightest
#     shard (ties by name, so the plan is the same every time).  A file the
#     weights do not name gets a default weight and still runs — a new test
#     file needs no edit there — and a weight for a file that is gone is
#     ignored.  Refresh the weights when the shards drift apart (the summary
#     shows the longest shard against the wall time):
#         ./scripts/check.sh --update-shard-weights
#     runs step 1 alone with pytest's --durations and, if it PASSES, rewrites
#     the file from what each test took (CHECK_SHARD_WEIGHTS_OUT=path writes
#     elsewhere).  Commit the result.
#   * The verdict is the single run's: the step fails if any shard fails or
#     ends without pytest's tally line, and the Quarto / template skip check
#     reads every shard's log.  The summary line carries the summed counts.
#   * CHECK_SHARDS=1 is the single process this step was before: one
#     container, pytest given the tests directory, its output live.
#   * The number is capped at the machine's processors (nproc) and by the
#     database: each shard is allowed SHARD_CONNECTIONS connections of what
#     max_connections leaves free when the step starts.  A lowered number is
#     said.
#   * No test is pinned to a shard or to a serial pass: none needs the server
#     to itself.  What was checked is listed above SHARD_CONNECTIONS below.
#
# From a git worktree: the worktree has no .env and no running stack, so the
# compose project is the MAIN checkout's and only the source comes from the
# worktree.  That is detected; override with
#   CHECK_COMPOSE_ROOT=/path/to/main/checkout ./scripts/check.sh
#
# Extra pytest arguments, given to every shard:
#   CHECK_PYTEST_ARGS="-k quarto -x" ./scripts/check.sh
# (a shard in which the arguments select nothing is not a failure, as long
# as some shard ran tests).
#

set -uo pipefail

FAST=0
ONLY="${CHECK_ONLY:-}"
UPDATE_WEIGHTS=0
MODE=run
PLAN_SHARDS=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --fast) FAST=1; shift ;;
        --only)
            [[ $# -ge 2 ]] || { echo "--only needs a step: backend | ruff | frontend | alembic" >&2; exit 2; }
            ONLY="$2"; shift 2 ;;
        --update-shard-weights) UPDATE_WEIGHTS=1; ONLY=backend; shift ;;
        # The next three run nothing: they exist for tests/test_ops_scripts.py.
        --plan-shards)
            [[ $# -ge 2 ]] || { echo "--plan-shards needs a number" >&2; exit 2; }
            MODE=plan; PLAN_SHARDS="$2"; shift 2 ;;
        --sum-tallies) MODE=sum; shift ;;
        --mount-skips) MODE=mount_skips; shift ;;
        -h|--help) awk 'NR > 1 { if ($0 !~ /^#/) exit; print }' "$0"; exit 0 ;;
        *) echo "Unknown argument: $1 (see --help)" >&2; exit 2 ;;
    esac
done
case "$ONLY" in
    ""|backend|ruff|frontend|alembic) ;;
    *) echo "Unknown step '$ONLY': backend | ruff | frontend | alembic" >&2; exit 2 ;;
esac

DEFAULT_SHARDS=4
if [[ ! "${CHECK_SHARDS:-$DEFAULT_SHARDS}" =~ ^[1-9][0-9]*$ ]]; then
    echo "CHECK_SHARDS must be a whole number, 1 or more (got '${CHECK_SHARDS:-}')" >&2
    exit 2
fi

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- The shard plan (no Docker: these are pure, and tested as such) ----------
TESTS_DIR="$SRC/backend/tests"
SHARD_WEIGHTS_FILE="$SRC/scripts/check-shard-weights.txt"
# Seconds for a test file the weights do not name (about the mean of those
# they do).  It only steers the balance; the file runs either way.
SHARD_DEFAULT_WEIGHT=1.0

# Every test file, relative to backend/tests, one per line — what pytest
# collects when given the directory (pytest.ini: python_files = test_*.py).
list_test_files() {
    ( cd "$TESTS_DIR" 2>/dev/null && find . -type f -name 'test_*.py' ) | sed 's|^\./||' | LC_ALL=C sort
}

# The same set, found another way (the shell's own glob): the list the plan
# is checked against.
list_test_files_again() {
    (
        cd "$TESTS_DIR" 2>/dev/null || exit 0
        shopt -s globstar nullglob dotglob
        for f in **/test_*.py; do [[ -f "$f" ]] && printf '%s\n' "$f"; done
    ) | LC_ALL=C sort
}

# stdin: test files.  stdout: "<shard>\t<weight>\t<file>", one line per file
# read — by construction a file cannot be left out: every line that comes in
# is printed with a shard number between 1 and $1.  Heaviest first, each to
# the shard that is lightest so far (the lowest-numbered of equals).
assign_shards() {
    awk -v wf="$SHARD_WEIGHTS_FILE" -v def="$SHARD_DEFAULT_WEIGHT" '
        BEGIN {
            while ((getline line < wf) > 0) {
                if (line ~ /^[ \t]*(#|$)/) continue
                split(line, part, /[ \t]+/)
                if (part[1] ~ /^[0-9]+(\.[0-9]+)?$/ && part[2] != "") weight[part[2]] = part[1]
            }
        }
        { printf "%s\t%s\n", (($0 in weight) ? weight[$0] : def), $0 }
    ' | LC_ALL=C sort -t "$(printf '\t')" -k1,1gr -k2,2 | awk -F '\t' -v n="$1" '
        {
            best = 1
            for (i = 2; i <= n; i++) if (load[i] + 0 < load[best] + 0) best = i
            load[best] += $1
            printf "%d\t%s\t%s\n", best, $1, $2
        }
    '
}

# $1 = a plan (assign_shards' output).  Fails, saying what differs, unless
# the plan holds every test file exactly once.
plan_is_complete() {
    local planned expected
    planned="$(printf '%s\n' "$1" | awk -F '\t' 'NF { print $3 }' | LC_ALL=C sort)"
    expected="$(list_test_files_again)"
    if [[ -z "$expected" ]]; then
        echo "check.sh: no test files found under $TESTS_DIR" >&2
        return 1
    fi
    if [[ "$planned" != "$expected" ]]; then
        echo "check.sh: the shard plan is NOT the test suite — nothing was run." >&2
        echo "  ('<' = in a shard but not a test file, or there twice; '>' = a test file in no shard)" >&2
        diff <(printf '%s\n' "$planned") <(printf '%s\n' "$expected") | grep -E '^[<>]' | sed 's/^/    /' >&2
        return 1
    fi
    return 0
}

# stdin: pytest tally lines ("3 failed, 970 passed, 7 warnings in 81.20s
# (0:01:21)").  stdout: the counts summed, in pytest's order.
sum_tallies() {
    awk '
        BEGIN { split("failed passed skipped deselected xfailed xpassed warnings errors", order, " ") }
        {
            sub(/\r$/, ""); gsub(/^=+ +| +=+$/, ""); sub(/ in [0-9.]+s.*$/, "")
            n = split($0, parts, /, */)
            for (i = 1; i <= n; i++) {
                if (split(parts[i], kv, " ") < 2 || kv[1] !~ /^[0-9]+$/) continue
                key = kv[2]
                if (key == "error") key = "errors"
                if (key == "warning") key = "warnings"
                count[key] += kv[1]
            }
        }
        END {
            out = ""
            for (i = 1; i <= 8; i++) if (order[i] in count) {
                out = out (out == "" ? "" : ", ") count[order[i]] " " order[i]
                delete count[order[i]]
            }
            for (key in count) out = out (out == "" ? "" : ", ") count[key] " " key
            print out
        }
    '
}

# stdin: pytest output.  stdout: the SKIPPED lines of tests that did not run
# because something this script provides was missing — Quarto, the templates,
# or any mount (scripts/, the agent guide, the repository root, a file outside
# backend/).  Here everything is mounted, so such a skip means the test ran
# nowhere: the corroboration template test skipped as "the shipped templates
# are not mounted here", a reason the old 'quarto|report-templates' match did
# not see (review 2026-10-23, H-OPS1).  Matched on the REASON pytest prints;
# tests/test_ops_scripts.py checks that every such reason in the suite matches.
MOUNT_SKIP_REASON='quarto|report-templates|not mounted|not reachable|not present|repo(sitory)? root'
mount_skips() {
    grep -aE '^SKIPPED \[[0-9]+\]' | grep -aiE "$MOUNT_SKIP_REASON" || true
}

if [[ "$MODE" == sum ]]; then
    sum_tallies
    exit 0
fi
if [[ "$MODE" == mount_skips ]]; then
    mount_skips
    exit 0
fi
if [[ "$MODE" == plan ]]; then
    [[ "$PLAN_SHARDS" =~ ^[1-9][0-9]*$ ]] || { echo "--plan-shards needs a number, 1 or more" >&2; exit 2; }
    plan="$(list_test_files | assign_shards "$PLAN_SHARDS")"
    plan_is_complete "$plan" || exit 1
    printf '%s\n' "$plan"
    exit 0
fi

# The compose project: this tree if it is a deployment (.env present), else
# the main checkout of the worktree.
COMPOSE_ROOT="${CHECK_COMPOSE_ROOT:-}"
if [[ -z "$COMPOSE_ROOT" ]]; then
    if [[ -f "$SRC/.env" ]]; then
        COMPOSE_ROOT="$SRC"
    else
        COMPOSE_ROOT="$(git -C "$SRC" worktree list --porcelain 2>/dev/null | sed -n '1s/^worktree //p')"
    fi
fi
if [[ -z "$COMPOSE_ROOT" || ! -f "$COMPOSE_ROOT/docker-compose.yml" || ! -f "$COMPOSE_ROOT/.env" ]]; then
    echo "check.sh: no deployment found to run the backend suite in (looked for .env +" >&2
    echo "docker-compose.yml in '${COMPOSE_ROOT:-$SRC}'). Set CHECK_COMPOSE_ROOT." >&2
    exit 2
fi

COMPOSE=(docker compose -f "$COMPOSE_ROOT/docker-compose.yml" --project-directory "$COMPOSE_ROOT")
compose() { "${COMPOSE[@]}" "$@"; }

# The image compose builds for a service.  `config --images SERVICE` also
# lists the images of the services it depends on (the database), in no fixed
# order — taking the first line ran Alembic inside the postgres image one
# time in two, and removing only the database's still left the image of any
# OTHER dependency to be picked.  So the image NAMED for the service is
# selected, the way stack-lib.sh's service_image_ref does:
# <project>-<service>, else a name ending in the
# service's.  (Each script has its own compose command, so the three lines
# are repeated rather than shared.)
service_image() {
    local service="$1" listed project ref=""
    listed="$(compose config --images "$service" 2>/dev/null || true)"
    [[ -n "$listed" ]] || return 0
    project="$(compose config 2>/dev/null | awk '/^name:/ {print $2; exit}')"
    ref="$(printf '%s\n' "$listed" | grep -xE "${project:-[^/]+}[-_]${service}(:[^/]+)?" | head -1 || true)"
    [[ -n "$ref" ]] || ref="$(printf '%s\n' "$listed" | grep -E "(^|[-_/])${service}(:[^/]+)?\$" | head -1 || true)"
    printf '%s\n' "$ref"
}

LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/bluestick-check.XXXXXX")"

# --- Leave nothing behind ---------------------------------------------------
# Interrupted (Ctrl-C, a closed terminal), this script used to leave its log
# directory in /tmp and — because the suite never reached its own
# end-of-session drop — one `<db>_test_<host>_<pid>` database on the
# development Postgres per aborted run.
#
#   * The log directory is removed on every exit EXCEPT a failed run, whose
#     summary names it.
#   * Every suite container of THIS run (one, or one per shard) is removed.
#     Their names carry this script's PID, so two gates running at once — two
#     checkouts — never touch each other's.
#   * THIS run's test databases are dropped: each shard's, and the scratch
#     databases a migration test makes beside it (`…_<pid>_boot`).  A name
#     carries its suite container's host name, which is read while the
#     container runs (watch_suite_container); only databases named exactly
#     `<POSTGRES_DB>_test_<that host>_<digits>[_<suffix>]` are dropped —
#     never the application database, never another run's.
KEEP_LOGS=0
SUITE_CONTAINER="bluestick-check-$$"       # shard N of several: …-sN
declare -a SUITE_CONTAINERS=() SUITE_PIDS=() WATCHER_PIDS=()

env_value() { grep -E "^$1=" "$COMPOSE_ROOT/.env" 2>/dev/null | tail -1 | cut -d= -f2-; }
pg_user() { local u; u="$(env_value POSTGRES_USER)"; printf '%s' "${u:-nmapuser}"; }
pg_db()   { local d; d="$(env_value POSTGRES_DB)";   printf '%s' "${d:-networkMapper}"; }

suite_host_file() { printf '%s/suite-host.%s' "$LOG_DIR" "$1"; }

# Write down a suite container's host name, if it exists and is not yet known.
record_suite_host() {
    local file host
    file="$(suite_host_file "$1")"
    [[ -s "$file" ]] && return 0
    host="$(docker inspect --format '{{.Config.Hostname}}' "$1" 2>/dev/null || true)"
    [[ -n "$host" ]] || return 1
    printf '%s\n' "$host" > "$file"
}

drop_run_test_databases() {
    local file host user db prefix names name
    user="$(pg_user)"; db="$(pg_db)"
    [[ "$db" != *"'"* && "$db" != *'"'* ]] || return 0
    for file in "$LOG_DIR"/suite-host.*; do
        [[ -s "$file" ]] || continue
        # The same normalisation backend/tests/conftest.py applies to the host name.
        host="$(tr '[:upper:]' '[:lower:]' < "$file" | tr -cd 'a-z0-9' | cut -c1-16)"
        [[ -n "$host" ]] || continue
        prefix="${db}_test_${host}_"
        names="$(compose exec -T db psql -U "$user" -d postgres -tAc \
            "SELECT datname FROM pg_database WHERE left(datname, ${#prefix}) = '${prefix}' AND substr(datname, ${#prefix} + 1) ~ '^[0-9]+(_[a-z0-9]+)?\$' AND datname <> '${db}'" \
            2>/dev/null </dev/null || true)"
        while IFS= read -r name; do
            [[ -n "$name" ]] || continue
            if compose exec -T db psql -U "$user" -d postgres -tAc \
                "DROP DATABASE IF EXISTS \"${name}\" WITH (FORCE)" >/dev/null 2>&1 </dev/null; then
                echo "check.sh: dropped this run's test database ${name}" >&2
            fi
        done <<< "$names"
    done
}

cleanup() {
    local rc=$? pid name
    trap - EXIT INT TERM HUP
    for pid in "${WATCHER_PIDS[@]}"; do kill "$pid" 2>/dev/null; done
    # An interrupted suite container may still be running, or shutting down;
    # it is ours.  Its host name is asked for one last time first: without it
    # the container's database cannot be found.
    for name in "${SUITE_CONTAINERS[@]}"; do
        record_suite_host "$name"
        docker rm -f "$name" >/dev/null 2>&1 || true
    done
    if [[ "${#SUITE_PIDS[@]}" -gt 0 ]]; then
        # The `compose run` clients of the shards: ended, then waited for, so
        # that none creates its container after the removal above — and the
        # removal is repeated for one that just did.
        for pid in "${SUITE_PIDS[@]}"; do kill "$pid" 2>/dev/null; done
        for pid in "${SUITE_PIDS[@]}"; do wait "$pid" 2>/dev/null; done
        for name in "${SUITE_CONTAINERS[@]}"; do
            record_suite_host "$name"
            docker rm -f "$name" >/dev/null 2>&1 || true
        done
    fi
    drop_run_test_databases
    if [[ "$KEEP_LOGS" -ne 1 ]]; then
        rm -rf "$LOG_DIR"
    fi
    exit "$rc"
}
trap cleanup EXIT
trap 'echo "" >&2; echo "check.sh: interrupted — cleaning up." >&2; exit 130' INT TERM HUP

# Record a suite container's host name as soon as it exists (it is --rm, so
# it cannot be asked afterwards).
watch_suite_container() {
    for _ in $(seq 1 240); do
        record_suite_host "$1" && return 0
        sleep 0.5
    done
}

declare -a STEP_NAMES=() STEP_RESULTS=() STEP_NOTES=()
record() { STEP_NAMES+=("$1"); STEP_RESULTS+=("$2"); STEP_NOTES+=("$3"); }
minutes() { printf '%dm%02ds' $(( $1 / 60 )) $(( $1 % 60 )); }
elapsed() { minutes $(( SECONDS - $1 )); }
banner() { echo ""; echo "=== $1 ==="; }

# --- 1. Backend suite, in the report-worker image ---------------------------

# What a shard may hold open on the database server at once, for the cap on
# the number of shards.  A shard's test engine has no pool (one connection
# per test session, a few more in the lock and race tests); the application's
# own pool (DB_POOL_SIZE=5) is barely touched under test; the largest user is
# test_db_init_concurrent_boot.py, whose five real processes each connect.
# Measured on 2026-10-10 with four shards (sampled each second, two runs):
# the whole server peaked at 29 client connections, 16 of them the running
# application's and 13 the four shards' — under 4 a shard at the worst
# moment, of max_connections 120.  12 leaves room for a shard to be unlucky.
#
# Tests that cannot share the server with another shard: NONE, by reading —
#   * the test database's name carries the container's host name
#     (conftest._test_database_name), and so do the scratch databases the
#     migration tests derive from it (`…_boot`, `…_b8e2`, `…_c8f2`, `…_f5c2`);
#   * advisory locks (the boot lock, the housekeeping leader lock, the seed
#     lock) are per database in PostgreSQL, and every read of
#     pg_stat_activity / pg_locks in a test is filtered to current_database();
#   * test_stale_test_database_sweep.py makes databases under its own prefix
#     (`sweepcheck<pid>…`); the file is in one shard, and conftest's sweep at
#     a shard's start only drops `<db>_test_…` databases more than a day old
#     with nobody connected — never a live shard's;
#   * nothing listens on a port; files go under each container's own /tmp
#     (UPLOAD_DIR resolves to /tmp/uploads there), and /app is not writable
#     by the suite's user;
#   * no test changes a server-wide setting, a role or an extension.
# A test that turns out to need the server alone belongs in a serial pass
# after the shards — say which and why here; never skip or retry it.
SHARD_CONNECTIONS=12

db_scalar() {
    compose exec -T db psql -U "$(pg_user)" -d postgres -tAc "$1" 2>/dev/null </dev/null | tr -d '[:space:]'
}

# $1 = the number of test files.  Sets SHARDS; says why when it is fewer than
# was asked for.
resolve_shard_count() {
    local want="${CHECK_SHARDS:-$DEFAULT_SHARDS}" cpus max reserved used room by_db
    SHARDS="$want"
    cpus="$(nproc 2>/dev/null || echo 1)"
    if (( SHARDS > cpus )); then
        SHARDS="$cpus"
        echo "check.sh: $want shards asked for, $cpus processor(s) here — running $SHARDS."
    fi
    (( SHARDS > $1 )) && SHARDS="$1"
    if (( SHARDS > 1 )); then
        max="$(db_scalar 'SHOW max_connections')"
        reserved="$(db_scalar 'SHOW superuser_reserved_connections')"
        used="$(db_scalar 'SELECT count(*) FROM pg_stat_activity')"
        if [[ "$max" =~ ^[0-9]+$ && "$reserved" =~ ^[0-9]+$ && "$used" =~ ^[0-9]+$ ]]; then
            room=$(( max - reserved - used ))
            by_db=$(( room / SHARD_CONNECTIONS ))
            (( by_db < 1 )) && by_db=1
            if (( SHARDS > by_db )); then
                echo "check.sh: the database has $room connection(s) free (max_connections $max, $used in use);" \
                     "at $SHARD_CONNECTIONS a shard that is room for $by_db — running $by_db, not $SHARDS."
                SHARDS="$by_db"
            fi
        fi
    fi
    (( SHARDS < 1 )) && SHARDS=1
    return 0
}

# The command a suite container runs.  $1 = what pytest is given.
# `-rfEs` prints one line per failure, error and skip reason: the skip lines
# are what the Quarto check reads, the others are repeated under the summary.
suite_shell() {
    local extra="${CHECK_PYTEST_ARGS:-}"
    [[ "$UPDATE_WEIGHTS" -eq 1 ]] && extra="--durations=0 --durations-min=0 $extra"
    printf '%s' "quarto --version >/dev/null 2>&1 || { echo 'CHECK: quarto is not in this image'; exit 97; }
               python -c 'import pytest' 2>/dev/null || { echo 'CHECK: pytest is not in this image (built with INSTALL_DEV=false?)'; exit 98; }
               cd /tmp && python -m pytest $1 -q -p no:cacheprovider --rootdir=/app -c /app/pytest.ini --no-cov -rfEs $extra"
}

suite_tally() {
    grep -aE '^=+ .* in [0-9.]+s( \([0-9:]+\))? =+' "$1" 2>/dev/null | tail -1 | sed -E 's/\r$//; s/^=+ //; s/ =+$//'
}

# stdin: suite logs' `--durations` lines.  stdout: the weights file.
write_shard_weights() {
    echo "# Seconds each backend test file takes (setup + call + teardown of its tests),"
    echo "# for scripts/check.sh to balance its shards.  Generated — refresh with"
    echo "#     ./scripts/check.sh --update-shard-weights"
    echo "# A file not listed here still runs (default weight $SHARD_DEFAULT_WEIGHT); a line for a file"
    echo "# that is gone is ignored.  <seconds> <path under backend/tests>"
    awk '
        { sub(/\r$/, "") }
        $1 ~ /^[0-9.]+s$/ && ($2 == "setup" || $2 == "call" || $2 == "teardown") {
            file = $3; sub(/::.*$/, "", file); sub(/^(\/app\/)?tests\//, "", file)
            seconds = $1; sub(/s$/, "", seconds)
            total[file] += seconds
        }
        END { for (file in total) printf "%.2f %s\n", total[file], file }
    ' | LC_ALL=C sort -k2,2
}

backend_suite() {
    local started=$SECONDS image i name log
    banner "1/4 Backend suite (report-worker image: Quarto + templates)"

    image="$(service_image report-worker)"
    if [[ -z "$image" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
        record "backend suite" FAIL "report-worker image '${image:-?}' is not built — docker compose build report-worker"
        return
    fi
    if [[ -z "$(compose ps -q db 2>/dev/null)" ]]; then
        record "backend suite" FAIL "the db container is not running — start the stack first"
        return
    fi

    local -a mounts=(
        -v "$SRC/backend:/app"
        -v "$SRC/backend/tests:/app/tests"
        -v "$SRC/backend/pytest.ini:/app/pytest.ini:ro"
        -v "$SRC/scripts:/app/scripts:ro"
        -v "$SRC/report-templates:/app/report-templates:ro"
        -v "$SRC/platform_version.json:/app/platform_version.json:ro"
        -v "$SRC/documentation/AGENT_GUIDE.md:/app/AGENT_GUIDE.md:ro"
        -v "$SRC/frontend/package-lock.json:/app/frontend-package-lock.json:ro"
    )
    [[ -d "$SRC/artifacts" ]] && mounts+=(-v "$SRC/artifacts:/app/artifacts:ro")
    # Tests that compare the backend with files outside backend/ (the frontend
    # sources, documentation/, the operator scripts) look for the repository:
    # BLUESTICK_REPO_ROOT, and scripts/ two levels above tests/.  Without
    # these they skip, in every other recipe.
    mounts+=(-v "$SRC:/repo:ro" -v "$SRC/scripts:/scripts:ro")
    [[ -f "$COMPOSE_ROOT/ssl/certs/networkmapper.crt" ]] && \
        mounts+=(-v "$COMPOSE_ROOT/ssl/certs/networkmapper.crt:/certs/networkmapper.crt:ro")

    # DB_POOL_SIZE: the report-worker service runs with a pool of 3; the suite
    # is the API's, so it gets the API's default.  REPORT_TEMPLATES_DIR: its
    # default is <cwd>/report-templates and pytest runs from /tmp, so without
    # it the tests that read the shipped templates skip ("not mounted") even
    # though they are mounted.
    # PYTHONUNBUFFERED: a shard's pytest writes to a pipe (`run -T`), which
    # Python buffers, so the live line below read a log minutes behind (48 %
    # shown at 74 %, 2026-10-23).
    local -a suite_env=(
        -e COVERAGE_FILE=/tmp/.coverage -e DB_POOL_SIZE=5 -e PYTHONUNBUFFERED=1
        -e REPORT_TEMPLATES_DIR=/app/report-templates -e BLUESTICK_REPO_ROOT=/repo
    )

    local files plan
    files="$(list_test_files)"
    if [[ -z "$files" ]]; then
        record "backend suite" FAIL "no test files under $TESTS_DIR"
        return
    fi
    if grep -qvE '^[A-Za-z0-9_./-]+$' <<< "$files"; then
        grep -vE '^[A-Za-z0-9_./-]+$' <<< "$files" | sed 's/^/  /'
        record "backend suite" FAIL "a test file's name has a character a shard's command line cannot carry (listed above) — rename it"
        return
    fi
    resolve_shard_count "$(grep -c '' <<< "$files")"

    local -a logs=() rcs=()
    if (( SHARDS == 1 )); then
        # The single process: pytest is given the directory, its output is live.
        name="$SUITE_CONTAINER"; log="$LOG_DIR/backend.log"
        SUITE_CONTAINERS+=("$name"); logs+=("$log")
        # Named, so its host name — part of the test database's name — can be
        # read while it runs (see cleanup above).
        watch_suite_container "$name" &
        WATCHER_PIDS+=($!)
        compose run --rm --no-deps --name "$name" "${mounts[@]}" "${suite_env[@]}" \
            report-worker sh -c "$(suite_shell /app/tests)" 2>&1 | tee "$log"
        rcs+=("${PIPESTATUS[0]}")
    else
        plan="$(assign_shards "$SHARDS" <<< "$files")"
        if ! plan_is_complete "$plan"; then
            record "backend suite" FAIL "the shard plan left a test file out or held one twice (above) — nothing was run"
            return
        fi
        echo "$(grep -c '' <<< "$files") test files in $SHARDS shards (weights: ${SHARD_WEIGHTS_FILE#"$SRC"/}):"
        awk -F '\t' '{ n[$1]++; w[$1] += $2 } END { for (i = 1; (i in n); i++) printf "  shard %d  %3d files  planned %4.0fs\n", i, n[i], w[i] }' <<< "$plan"

        local targets
        for i in $(seq 1 "$SHARDS"); do
            name="${SUITE_CONTAINER}-s$i"; log="$LOG_DIR/backend-shard-$i.log"
            targets="$(awk -F '\t' -v s="$i" '$1 == s { printf "/app/tests/%s ", $3 }' <<< "$plan")"
            SUITE_CONTAINERS+=("$name"); logs+=("$log")
            watch_suite_container "$name" &
            WATCHER_PIDS+=($!)
            # `exec`: the background process IS the compose client, so the
            # cleanup can end it.  -T and no stdin: four clients share no terminal.
            ( exec "${COMPOSE[@]}" run --rm --no-deps -T --name "$name" "${mounts[@]}" "${suite_env[@]}" \
                report-worker sh -c "$(suite_shell "$targets")" ) >"$log" 2>&1 </dev/null &
            SUITE_PIDS+=($!)
        done

        # Wait for all of them, saying every so often how far each has got
        # (pytest's own percentage, of that shard's tests).
        local running tick=0 line pct
        while :; do
            running=0
            for i in "${!SUITE_PIDS[@]}"; do kill -0 "${SUITE_PIDS[$i]}" 2>/dev/null && running=$((running + 1)); done
            (( running == 0 )) && break
            sleep 2
            tick=$((tick + 1))
            if (( tick % 10 == 0 )); then
                line="  $(elapsed "$started")"
                for i in "${!logs[@]}"; do
                    pct="$(grep -aoE '\[ *[0-9]+%\]' "${logs[$i]}" 2>/dev/null | tail -1 | tr -d '[] ')"
                    line+="   shard $((i + 1)) ${pct:-starting}"
                done
                echo "$line"
            fi
        done
        for i in "${!SUITE_PIDS[@]}"; do
            wait "${SUITE_PIDS[$i]}" 2>/dev/null
            rcs+=("$?")
        done
        SUITE_PIDS=()
    fi
    for i in "${WATCHER_PIDS[@]}"; do kill "$i" 2>/dev/null; wait "$i" 2>/dev/null; done
    WATCHER_PIDS=()

    # --- The verdict, over every log ----------------------------------------
    local tally seconds longest=0 ran=0 summed where quarto_skips other_skips
    local -a tallies=() broken=()
    for i in "${!logs[@]}"; do
        tally="$(suite_tally "${logs[$i]}")"
        tallies+=("$tally")
        where="${logs[$i]}"
        (( SHARDS > 1 )) && where="shard $((i + 1)): ${logs[$i]}"
        if [[ -z "$tally" ]]; then
            # No tally line: pytest did not finish (killed, crashed at
            # collection, the image has no Quarto / pytest).
            broken+=("$where (pytest exited ${rcs[$i]} with no tally)")
        elif [[ "${rcs[$i]}" -eq 0 ]]; then
            ran=$((ran + 1))
        elif [[ "${rcs[$i]}" -eq 5 && "$SHARDS" -gt 1 ]]; then
            :   # CHECK_PYTEST_ARGS selected nothing in this shard; judged below
        else
            broken+=("$where")
        fi
        seconds="$(sed -nE 's/.* in ([0-9]+)(\.[0-9]+)?s.*/\1/p' <<< "$tally")"
        [[ "$seconds" =~ ^[0-9]+$ ]] && (( seconds > longest )) && longest="$seconds"
        (( SHARDS > 1 )) && printf '  shard %d  %s\n' $((i + 1)) "${tally:-no tally — pytest exited ${rcs[$i]}}"
    done
    summed="$(printf '%s\n' "${tallies[@]}" | sum_tallies)"
    if (( SHARDS > 1 )); then
        summed="${summed:-no tally} in $SHARDS shards, $(elapsed "$started") wall (longest shard $(minutes "$longest"))"
    else
        summed="${tallies[0]:-pytest exited ${rcs[0]}} ($(elapsed "$started"))"
    fi
    # A Quarto/template/mount test that skipped ran nowhere: that is the
    # defect this script exists to close (mount_skips, above).
    mount_skipped="$(cat "${logs[@]}" 2>/dev/null | mount_skips)"
    quarto_skips="$(grep -c . <<< "$mount_skipped" || true)"

    if [[ "${#broken[@]}" -gt 0 ]]; then
        echo ""
        for i in "${!logs[@]}"; do
            # The failures themselves, for a shard whose output was not live.
            if (( SHARDS > 1 )) && [[ "${rcs[$i]}" -ne 0 && "${rcs[$i]}" -ne 5 ]]; then
                echo "--- shard $((i + 1)) (${logs[$i]}) ---"
                if grep -qaE '^=+ (FAILURES|ERRORS) =+' "${logs[$i]}"; then
                    sed -nE '/^=+ (FAILURES|ERRORS) =+/,$p' "${logs[$i]}"
                else
                    tail -n 40 "${logs[$i]}"
                fi
            fi
        done
        echo ""
        echo "Failed:"
        cat "${logs[@]}" 2>/dev/null | grep -aE '^(FAILED|ERROR) ' | sed 's/^/  /'
        record "backend suite" FAIL "$summed — open $(printf '%s; ' "${broken[@]}" | sed 's/; $//')"
    elif (( ran == 0 )); then
        record "backend suite" FAIL "no tests ran (pytest's arguments selected nothing) — $LOG_DIR"
    elif [[ "${quarto_skips:-0}" -ne 0 ]]; then
        echo ""
        sed 's/^/  /' <<< "$mount_skipped"
        record "backend suite" FAIL "$quarto_skips Quarto/template/mount skip reason(s): those tests did not run — $LOG_DIR"
    else
        other_skips="$(cat "${logs[@]}" | grep -acE '^SKIPPED \[[0-9]+\]' || true)"
        if [[ "${other_skips:-0}" -ne 0 ]]; then
            echo ""
            echo "Skipped (not Quarto / templates / a mount — listed so a new skip is seen):"
            cat "${logs[@]}" | grep -aE '^SKIPPED \[[0-9]+\]' | sed 's/^/  /'
        fi
        if [[ "$UPDATE_WEIGHTS" -eq 1 ]]; then
            local out="${CHECK_SHARD_WEIGHTS_OUT:-$SHARD_WEIGHTS_FILE}"
            if [[ -n "${CHECK_PYTEST_ARGS:-}" ]]; then
                echo "check.sh: CHECK_PYTEST_ARGS is set — the weights were NOT written (they must come from the whole suite)."
            else
                cat "${logs[@]}" | write_shard_weights > "$out.tmp" && mv "$out.tmp" "$out"
                echo "check.sh: wrote $(grep -cvE '^#' "$out") test files' seconds to $out"
            fi
        fi
        record "backend suite" PASS "$summed; no Quarto/template/mount test skipped"
    fi
}

# --- 2. Backend lint (ruff), in the same image ------------------------------
backend_lint() {
    local log="$LOG_DIR/ruff.log" started=$SECONDS image
    banner "2/4 Backend lint (ruff: pyflakes rules, backend/ruff.toml)"

    image="$(service_image report-worker)"
    if [[ -z "$image" ]] || ! docker image inspect "$image" >/dev/null 2>&1; then
        record "backend ruff" FAIL "report-worker image '${image:-?}' is not built — docker compose build report-worker"
        return
    fi
    # Run FROM /app so ruff finds /app/ruff.toml by itself: with an explicit
    # --config its per-file-ignores globs ("tests/**") resolve against the
    # working directory, and from anywhere else the tests' fixture imports
    # are reported.  --no-cache: /app is the working tree, and a cache
    # directory written there would be root-owned.
    compose run --rm --no-deps -v "$SRC/backend:/app" \
        report-worker \
        sh -c "python -m ruff --version >/dev/null 2>&1 || { echo 'CHECK: ruff is not in this image (built with INSTALL_DEV=false?)'; exit 98; }
               cd /app && python -m ruff check . --no-cache --output-format concise" \
        2>&1 | tee "$log"
    local rc=${PIPESTATUS[0]}

    if [[ "$rc" -eq 0 ]]; then
        record "backend ruff" PASS "no findings ($(elapsed "$started"))"
    elif [[ "$rc" -eq 98 ]]; then
        record "backend ruff" FAIL "ruff is not in the report-worker image — rebuild it with the dev requirements"
    else
        local found
        found="$(grep -E '^Found [0-9]+ error' "$log" | tail -1)"
        record "backend ruff" FAIL "${found:-ruff exited $rc} — $log"
    fi
}

# --- 3. Frontend ------------------------------------------------------------
frontend_checks() {
    local started=$SECONDS log="$LOG_DIR/frontend.log"
    banner "3/4 Frontend (tsc --noEmit, eslint, vitest run, playwright)"
    if ! command -v npx >/dev/null 2>&1; then
        record "frontend tsc" FAIL "npx is not on PATH (install Node; see frontend/Dockerfile for the version)"
        record "frontend vitest" FAIL "not run"
        return
    fi
    if [[ ! -d "$SRC/frontend/node_modules" ]]; then
        record "frontend tsc" FAIL "frontend/node_modules is missing — cd frontend && npm ci"
        record "frontend vitest" FAIL "not run"
        return
    fi
    if ( cd "$SRC/frontend" && npx tsc --noEmit -p . ) 2>&1 | tee "$log"; then
        record "frontend tsc" PASS "no type errors ($(elapsed "$started"))"
    else
        record "frontend tsc" FAIL "type errors — $log"
    fi
    started=$SECONDS
    log="$LOG_DIR/eslint.log"
    if ( cd "$SRC/frontend" && npm run --silent lint -- --max-warnings 0 ) 2>&1 | tee "$log"; then
        record "frontend lint" PASS "no errors, no warnings ($(elapsed "$started"))"
    else
        record "frontend lint" FAIL "lint errors or warnings — $log"
    fi
    started=$SECONDS
    log="$LOG_DIR/vitest.log"
    if ( cd "$SRC/frontend" && npx vitest run ) 2>&1 | tee "$log"; then
        record "frontend vitest" PASS "$(grep -E '^ *Tests +' "$log" | tail -1 | sed -E 's/^ *Tests +//; s/\x1b\[[0-9;]*m//g') ($(elapsed "$started"))"
    else
        record "frontend vitest" FAIL "failures — $log"
    fi
    started=$SECONDS
    log="$LOG_DIR/playwright.log"
    if ( cd "$SRC/frontend" && CI=1 npx playwright test ) 2>&1 | tee "$log"; then
        record "frontend e2e" PASS "$(grep -E '^ *[0-9]+ passed' "$log" | tail -1 | sed -E 's/^ *//; s/\x1b\[[0-9;]*m//g') ($(elapsed "$started"))"
    elif grep -q "Executable doesn't exist" "$log"; then
        record "frontend e2e" FAIL "no Playwright browser — cd frontend && npx playwright install chromium"
    else
        record "frontend e2e" FAIL "failures — $log"
    fi
}

# --- 4. Alembic round trip + alembic check ----------------------------------
alembic_roundtrip() {
    local started=$SECONDS log="$LOG_DIR/alembic.log" backend_image
    if [[ "$FAST" -eq 1 ]]; then
        record "alembic round trip" SKIP "--fast (run without it before pushing a migration)"
        return
    fi
    banner "4/4 Alembic round trip + alembic check (throwaway Postgres)"
    backend_image="$(service_image backend)"
    if ALEMBIC_TEST_BACKEND_IMAGE="${ALEMBIC_TEST_BACKEND_IMAGE:-${backend_image:-networkmapper-backend}}" \
        "$SRC/scripts/test-alembic-roundtrip.sh" 2>&1 | tee "$log"; then
        record "alembic round trip" PASS "every downgrade inverts; models match migrations ($(elapsed "$started"))"
    else
        record "alembic round trip" FAIL "see $log"
    fi
}

case "$ONLY" in
    backend)  backend_suite ;;
    ruff)     backend_lint ;;
    frontend) frontend_checks ;;
    alembic)  FAST=0; alembic_roundtrip ;;
    *)
        backend_suite
        backend_lint
        frontend_checks
        alembic_roundtrip
        ;;
esac

# --- Summary (one screen) ---------------------------------------------------
echo ""
echo "=============================================================="
echo "  BlueStick check — $(git -C "$SRC" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')" \
     "@ $(git -C "$SRC" rev-parse --short HEAD 2>/dev/null || echo '?')$([[ -n "$(git -C "$SRC" status --porcelain 2>/dev/null)" ]] && echo ' (uncommitted changes)')"
[[ "$COMPOSE_ROOT" != "$SRC" ]] && echo "  source: $SRC   compose project: $COMPOSE_ROOT"
echo "=============================================================="
failed=0
for i in "${!STEP_NAMES[@]}"; do
    printf '  %-5s %-20s %s\n' "${STEP_RESULTS[$i]}" "${STEP_NAMES[$i]}" "${STEP_NOTES[$i]}"
    [[ "${STEP_RESULTS[$i]}" == "FAIL" ]] && failed=$((failed + 1))
done
echo "--------------------------------------------------------------"
if [[ "$failed" -eq 0 ]]; then
    if [[ -n "$ONLY" ]]; then
        echo "  PASSED — the $ONLY step ONLY; this is not the gate"
    else
        echo "  PASSED$([[ "$FAST" -eq 1 ]] && echo ' (--fast: the Alembic round trip was not run)')"
    fi
    exit 0          # the EXIT trap removes the log directory
fi
KEEP_LOGS=1         # a failed run keeps its logs: the summary names them
echo "  FAILED: $failed step(s). Full output: $LOG_DIR"
exit 1
