#!/usr/bin/env bash
#
# BlueStick — the gate: one command for "is this tree releasable?"
#
#   ./scripts/check.sh            # everything
#   ./scripts/check.sh --fast     # skip the Alembic round trip (step 4)
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
#      Quarto — with report-templates/ mounted.  FAILS if any test was
#      skipped for want of Quarto or the templates: here they must run.
#   2. Backend lint: `ruff check` in the same image — pyflakes (F) rules only
#      (backend/ruff.toml), --no-cache.  Any finding fails the gate.
#   3. Frontend: `tsc --noEmit`, then `vitest run` (on the host; needs node).
#   4. scripts/test-alembic-roundtrip.sh — every migration down and back up
#      against a throwaway Postgres, then `alembic check` (models vs
#      migrations).  Skipped by --fast.
#
# Exit status: 0 only if every step that ran passed.
#
# It follows the documented recipe: a one-off container started from the
# repository root, the working tree's backend/ mounted over /app, the tests
# mounted explicitly (so a git worktree tests ITS tests, not the main
# checkout's), and BLUESTICK_SKIP_DB_INIT=1 so nothing here can migrate the
# development database.  The stack must be up (the suite creates and drops
# its own database on the running db container); nothing is built or pulled.
#
# From a git worktree: the worktree has no .env and no running stack, so the
# compose project is the MAIN checkout's and only the source comes from the
# worktree.  That is detected; override with
#   CHECK_COMPOSE_ROOT=/path/to/main/checkout ./scripts/check.sh
#
# Extra pytest arguments:  CHECK_PYTEST_ARGS="-k quarto -x" ./scripts/check.sh
#

set -uo pipefail

FAST=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --fast) FAST=1; shift ;;
        -h|--help) sed -n '2,41p' "$0"; exit 0 ;;
        *) echo "Unknown argument: $1 (see --help)" >&2; exit 2 ;;
    esac
done

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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

compose() { docker compose -f "$COMPOSE_ROOT/docker-compose.yml" --project-directory "$COMPOSE_ROOT" "$@"; }

# The image compose builds for a service.  `config --images SERVICE` also
# lists the images of the services it depends on (the database), in no fixed
# order — taking the first line ran Alembic inside the postgres image one
# time in two.  So the database's image is removed from the list first.
service_image() {
    local db_image
    db_image="$(compose config --images db 2>/dev/null | head -1)"
    compose config --images "$1" 2>/dev/null | grep -vxF "${db_image:-<none>}" | head -1
}

LOG_DIR="$(mktemp -d "${TMPDIR:-/tmp}/bluestick-check.XXXXXX")"
declare -a STEP_NAMES=() STEP_RESULTS=() STEP_NOTES=()
record() { STEP_NAMES+=("$1"); STEP_RESULTS+=("$2"); STEP_NOTES+=("$3"); }
elapsed() { local s=$(( SECONDS - $1 )); printf '%dm%02ds' $(( s / 60 )) $(( s % 60 )); }
banner() { echo ""; echo "=== $1 ==="; }

# --- 1. Backend suite, in the report-worker image ---------------------------
backend_suite() {
    local log="$LOG_DIR/backend.log" started=$SECONDS image
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
    # though they are mounted.  `-rs` prints one line per skip reason, which
    # is what the Quarto check below reads.
    compose run --rm --no-deps "${mounts[@]}" \
        -e COVERAGE_FILE=/tmp/.coverage -e BLUESTICK_SKIP_DB_INIT=1 -e DB_POOL_SIZE=5 \
        -e REPORT_TEMPLATES_DIR=/app/report-templates -e BLUESTICK_REPO_ROOT=/repo \
        report-worker \
        sh -c "quarto --version >/dev/null 2>&1 || { echo 'CHECK: quarto is not in this image'; exit 97; }
               python -c 'import pytest' 2>/dev/null || { echo 'CHECK: pytest is not in this image (built with INSTALL_DEV=false?)'; exit 98; }
               cd /tmp && python -m pytest /app/tests -q -p no:cacheprovider --rootdir=/app -c /app/pytest.ini --no-cov -rs ${CHECK_PYTEST_ARGS:-}" \
        2>&1 | tee "$log"
    local rc=${PIPESTATUS[0]}

    local tally quarto_skips
    tally="$(grep -E '^=+ .*(passed|failed|error|no tests ran).* in [0-9.]+s' "$log" | tail -1 | sed -E 's/^=+ //; s/ =+$//')"
    # A Quarto/template test that skipped ran nowhere: that is the defect this
    # script exists to close.  Matched on the skip REASON pytest prints.
    quarto_skips="$(grep -E '^SKIPPED \[[0-9]+\]' "$log" | grep -icE 'quarto|report-templates' || true)"

    if [[ "$rc" -ne 0 ]]; then
        record "backend suite" FAIL "${tally:-pytest exited $rc} ($(elapsed "$started")) — $log"
    elif [[ "${quarto_skips:-0}" -ne 0 ]]; then
        grep -E '^SKIPPED \[[0-9]+\]' "$log" | grep -iE 'quarto|report-templates' | sed 's/^/  /'
        record "backend suite" FAIL "$quarto_skips Quarto/template skip reason(s): those tests did not run — $log"
    else
        local other_skips
        other_skips="$(grep -cE '^SKIPPED \[[0-9]+\]' "$log" || true)"
        if [[ "${other_skips:-0}" -ne 0 ]]; then
            echo ""
            echo "Skipped (not Quarto / templates — listed so a new skip is seen):"
            grep -E '^SKIPPED \[[0-9]+\]' "$log" | sed 's/^/  /'
        fi
        record "backend suite" PASS "${tally:-ok} ($(elapsed "$started")); no Quarto/template test skipped"
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
        -e BLUESTICK_SKIP_DB_INIT=1 \
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
    banner "3/4 Frontend (tsc --noEmit, vitest run)"
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
    log="$LOG_DIR/vitest.log"
    if ( cd "$SRC/frontend" && npx vitest run ) 2>&1 | tee "$log"; then
        record "frontend vitest" PASS "$(grep -E '^ *Tests +' "$log" | tail -1 | sed -E 's/^ *Tests +//; s/\x1b\[[0-9;]*m//g') ($(elapsed "$started"))"
    else
        record "frontend vitest" FAIL "failures — $log"
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

backend_suite
backend_lint
frontend_checks
alembic_roundtrip

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
    echo "  PASSED$([[ "$FAST" -eq 1 ]] && echo ' (--fast: the Alembic round trip was not run)')"
    rm -rf "$LOG_DIR"
    exit 0
fi
echo "  FAILED: $failed step(s). Full output: $LOG_DIR"
exit 1
