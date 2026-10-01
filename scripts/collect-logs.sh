#!/bin/bash
#
# Collect an ANONYMISED diagnostics bundle from a BlueStick deployment — safe
# to hand to someone outside the engagement (e.g. to audit a production
# instance whose parsers see far more real tool output than a dev box).
#
# Usage:
#   ./scripts/collect-logs.sh [--since 72h] [--terms FILE]... [--no-feedback]
#
#   --since DUR   only container logs newer than DUR (docker syntax: 30m, 72h,
#                 2026-09-20).  Default: everything the containers still hold.
#   --terms FILE  extra values to remove, one per line (a client name, an
#                 internal domain, a codename).  "category<TAB>value" also
#                 works; categories are listed in scripts/scrub_logs.py.
#   --no-feedback leave the agents' free-text feedback (feedback.txt) out.
#
# What it collects: platform/container versions and state, migration state,
# the ingestion queue (formats, outcomes, skip counts, warnings, tracebacks),
# parse errors, a PARSER AUDIT of aggregate field coverage (how many rows of
# each format carry each field — counts only, never values), health checks,
# the TLS certificate nginx serves (derived facts: CA-issued or self-signed,
# expiry, whether it names HOST_IP), agent-surface outcomes (refused calls by
# route, MCP tool outcomes, proposals, evidence — counts only), the agents'
# feedback IN FULL (feedback.txt: the newest 200 entries' ratings and free
# text — read it before sending), and the backend / worker / report-worker /
# frontend (nginx) / db logs.
#
# How identifying information is removed:
#   * Collection happens in a private temp directory (mode 700).  Nothing
#     unscrubbed is ever written to the current directory, and the temp
#     directory is deleted on exit whatever happens.
#   * Nothing is collected that has no diagnostic value and names the
#     deployment: no hostname, user, working directory, interface/route/DNS
#     configuration, directory listings or untracked file names.  .env values
#     are shown only when they are plainly non-identifying (numbers, booleans,
#     log levels); every other value is redacted AND scrubbed from the logs.
#   * Upload filenames are replaced by "<job N>.<ext>" at query time.
#   * scripts/scrub_logs.py then rewrites every file: values harvested from
#     the database (project/user/site/label/scope/client names, e-mails,
#     upload filenames, hostnames, NetBIOS and DNS names, scope and AD
#     domains, registrant and certificate orgs, NetExec usernames) plus
#     anything SHAPED like an address, name, URL, account or secret become
#     stable pseudonyms (<ip-3>, <host-12>, <file-2> …).
#   * Fails closed: if python3 is missing or the scrubber fails, no bundle
#     is produced.
#
# Residual risk: free text the database never saw (e.g. a name inside a
# parser's exception message for a file that failed before import) is only
# caught if it has an identifying SHAPE.  Pass --terms with the client's
# name(s) and skim the bundle before sending it.  feedback.txt is the one file
# made of free text by design (agents describing their work): read it, or
# collect with --no-feedback.

set -euo pipefail

GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_info() { echo -e "${BLUE}📋 $1${NC}"; }
print_success() { echo -e "${GREEN}✅ $1${NC}"; }
print_error() { echo -e "${RED}❌ $1${NC}"; }
print_warning() { echo -e "${YELLOW}⚠️  $1${NC}"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRUBBER="$SCRIPT_DIR/scrub_logs.py"
# Everything below reads .env, uploads/, docker-compose.yml and
# platform_version.json relative to the deployment root.  Run from scripts/
# (production, 2026-09-26) every one of them read "(not found)" while docker
# compose — which searches parent directories — still found the stack.  The
# bundle is still written where the script was run from.
ORIG_PWD="$PWD"

SINCE=""
EXTRA_TERMS=()
WITH_FEEDBACK=true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --since) SINCE="${2:?--since needs a value}"; shift 2 ;;
        --no-feedback) WITH_FEEDBACK=false; shift ;;
        --terms)
            [[ -r "${2:-}" ]] || { print_error "--terms: cannot read '${2:-}'"; exit 1; }
            # Absolute, so it still resolves after the cd below.
            EXTRA_TERMS+=("$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"); shift 2 ;;
        -h|--help) sed -n '2,/^$/p' "$0"; exit 0 ;;  # the header comment, however long
        *) print_error "Unknown argument: $1 (see --help)"; exit 1 ;;
    esac
done

cd "$SCRIPT_DIR/.."

# Fail closed BEFORE collecting anything: no scrubber, no bundle.
if ! command -v python3 >/dev/null 2>&1; then
    print_error "python3 is required to anonymise the bundle (standard library only). Nothing was collected."
    exit 1
fi
if [[ ! -r "$SCRUBBER" ]]; then
    print_error "Missing $SCRUBBER — copy the whole scripts/ directory. Nothing was collected."
    exit 1
fi

# Docker Compose V2 plugin or the legacy V1 binary.
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
compose_available() { [[ ${#COMPOSE_CMD[@]} -gt 0 ]]; }

# Read (never source) .env — its values are data, not shell.
env_value() {
    [[ -f .env ]] || return 0
    { grep -E "^[[:space:]]*$1=" .env || true; } | tail -n 1 | cut -d'=' -f2- | sed -E 's/^["'\'']//; s/["'\'']$//'
}
# One rule for .env values, used both to display them and to harvest what the
# scrubber must remove: shown = plainly non-identifying; everything else is
# redacted in configuration.txt AND removed wherever it appears in the logs.
ENV_ENUM_KEYS='^(DATABASE_SSL_MODE|JWT_ALGORITHM|ALGORITHM|COOKIE_SAMESITE|ENVIRONMENT|LOG_LEVEL|TZ|NODE_ENV)$'
env_value_shown() {  # KEY VALUE
    echo "$1" | grep -qiE '(PASS|SECRET|KEY|TOKEN|SALT|CREDENTIAL|DSN|DATABASE_URL)' && return 1
    echo "$2" | grep -qiE '^(true|false|yes|no|on|off|[0-9]+(\.[0-9]+)?[a-z]{0,2}|debug|info|warning|error|critical)?$' && return 0
    echo "$1" | grep -qE "$ENV_ENUM_KEYS" && echo "$2" | grep -qE '^[A-Za-z0-9_-]{1,20}$'
}
env_lines() {  # KEY<TAB>VALUE per assignment, quotes stripped
    [[ -f .env ]] || return 0
    { grep -E '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=' .env || true; } | while IFS= read -r line; do
        key=${line%%=*}; key=${key//[[:space:]]/}; val=${line#*=}; val=${val#[\"\']}; val=${val%[\"\']}
        printf '%s\t%s\n' "$key" "$val"
    done
}

DB_NAME=$(env_value POSTGRES_DB); DB_NAME=${DB_NAME:-networkMapper}
DB_USER=$(env_value POSTGRES_USER); DB_USER=${DB_USER:-nmapuser}

TIMESTAMP=$(date -u +"%Y%m%d_%H%M%SZ")
LOG_NAME="bluestick_diagnostics_$TIMESTAMP"
WORK=$(mktemp -d)
chmod 700 "$WORK"
trap 'rm -rf "$WORK"' EXIT
LOG_DIR="$WORK/$LOG_NAME"
TERMS="$WORK/terms.tsv"
mkdir -p "$LOG_DIR"
: > "$TERMS"

container_running() {
    local cid
    compose_available || return 1
    cid=$(compose ps -q "$1" 2>/dev/null | head -n 1)
    [[ -n "$cid" ]] && docker inspect --format '{{.State.Running}}' "$cid" 2>/dev/null | grep -q true
}
DB_UP=false; container_running db && DB_UP=true
BACKEND_UP=false; container_running backend && BACKEND_UP=true

psql_q() {
    compose exec -T db psql -X -P pager=off -U "$DB_USER" -d "$DB_NAME" "$@"
}
# One labelled aggregate query; a query that fails (older schema) says so
# and the collection carries on.
q() {
    echo ""
    echo "--- $1 ---"
    psql_q -c "$2" 2>&1 || echo "(query failed — see above)"
}

print_info "Starting anonymised BlueStick diagnostics collection..."

# ----------------------------------------------------------------------
# Values to remove: harvested first, used by the scrubber at the end.
# ----------------------------------------------------------------------
print_info "Harvesting identifying values to remove (kept in a private temp file)..."
{
    # The machine and the account running this.
    printf 'host\t%s\n' "$(hostname 2>/dev/null || true)"
    printf 'host\t%s\n' "$(hostname -f 2>/dev/null || true)"
    printf 'user\t%s\n' "$(whoami 2>/dev/null || true)"
    # The deploy directory names the compose project (container/volume names);
    # only when it is not the product's own name.
    deploy_dir=$(basename "$PWD")
    if ! echo "$deploy_dir" | grep -qiE 'networkmapper|bluestick|muggystix'; then
        printf 'deploydir\t%s\n' "$deploy_dir"
    fi
    # Every .env value configuration.txt does not show.  A bare IP or
    # localhost is left to the scrubber's address rule (which keeps loopback).
    env_lines | while IFS=$'\t' read -r key v; do
        { [[ -z "$v" ]] || env_value_shown "$key" "$v"; } && continue
        # A list (CORS origins): each element's host, then the whole value.
        echo "$v" | tr ',' '\n' | sed -E 's#^[a-z]+://##; s#[:/].*$##' \
            | { grep -vE '^([0-9.]+|localhost|)$' || true; } \
            | while IFS= read -r part; do printf 'env\t%s\n' "$part"; done
        echo "$v" | grep -qE '^[0-9.]+$' || printf 'secret\t%s\n' "$v"
    done
} >> "$TERMS" 2>/dev/null || true

if $DB_UP; then
    # category|table|column — each its own query so a column an older schema
    # lacks costs one category, not the harvest.
    HARVEST=(
        "project|projects|name" "project|projects|slug"
        "user|users|username" "email|users|email" "fullname|users|full_name"
        "site|sites|name" "site|subnets|site" "label|subnet_labels|name" "scope|scopes|name"
        "client|report_profiles|client_name" "report|reports|title"
        "file|ingestion_jobs|original_filename" "file|ingestion_jobs|filename"
        "file|scans|filename" "file|parse_errors|filename"
        "host|hosts_v2|hostname" "host|hosts_v2|netbios_name" "fqdn|dns_names|fqdn"
        "domain|scope_domains|domain" "domain|netexec_results|domain_name"
        "host|netexec_results|hostname" "user|netexec_results|username"
        "org|network_attributions|org_name" "org|network_attributions|as_name"
        "org|network_attributions|handle"
        "org|web_interfaces|cert_subject_org" "org|web_interfaces|cert_issuer_org"
        # Caller-supplied MCP labels, grouped in agent_surface.txt: a client
        # names itself freely ("Acme audit workstation"), and an unknown tool
        # name is whatever the caller sent.  Names in BlueStick's own source
        # (claude-code, the catalogue's tools) survive the vocabulary pass.
        # (Not client_version: a version names no one, and clients report
        # versions like "2026-09-30" — harvested, that date vanished from
        # every log timestamp in the bundle.)
        "mcp|mcp_tool_calls|client_name"
        "mcp|mcp_tool_calls|tool_name" "mcp|mcp_tool_calls|rpc_method"
    )
    harvest_failed=0
    for spec in "${HARVEST[@]}"; do
        IFS='|' read -r cat tbl col <<< "$spec"
        if ! psql_q -A -t -F $'\t' -v ON_ERROR_STOP=1 -c \
            "SELECT DISTINCT '$cat', regexp_replace($col::text, '[\t\r\n]+', ' ', 'g') FROM $tbl WHERE $col IS NOT NULL AND length($col::text) BETWEEN 3 AND 300" \
            >> "$TERMS" 2>/dev/null; then
            harvest_failed=$((harvest_failed + 1))
        fi
    done
    [[ $harvest_failed -gt 0 ]] && print_warning "$harvest_failed value harvest queries failed (older schema?) — those names are only caught by shape."
else
    print_warning "Database is not running: names known only to the database (e.g. short hostnames) cannot be harvested. Pass --terms and review the bundle before sharing."
fi
for f in ${EXTRA_TERMS[@]+"${EXTRA_TERMS[@]}"}; do cat "$f" >> "$TERMS"; echo >> "$TERMS"; done

# ----------------------------------------------------------------------
# Platform and containers
# ----------------------------------------------------------------------
print_info "Collecting platform information..."
{
    echo "=== PLATFORM ==="
    echo "Collected (UTC): $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "Kernel: $(uname -srm)"
    echo "CPUs: $(nproc 2>/dev/null || echo unknown)"
    echo "Memory:"; free -h 2>/dev/null || echo "  unavailable"
    echo "Disk (deployment filesystem):"; df -h . 2>/dev/null | awk '{print $2, $3, $4, $5}' || true
    echo "Docker: $(docker --version 2>/dev/null || echo 'not found')"
    echo "Compose form: ${COMPOSE_FORM}"
    compose_available && echo "Compose: $(compose version 2>&1 | head -n 1)"
    echo "python3 (scrubber): $(python3 --version 2>&1)"
    [[ -f platform_version.json ]] && { echo "platform_version.json:"; cat platform_version.json; }
    echo ""
    echo "Uploads directory (counts and sizes only — no file names):"
    if [[ -d uploads ]]; then
        echo "  total: $(du -sh uploads 2>/dev/null | cut -f1), files: $(find uploads -type f 2>/dev/null | wc -l)"
        find uploads -mindepth 1 -maxdepth 1 -type d 2>/dev/null | while IFS= read -r d; do
            echo "  $(basename "$d"): $(find "$d" -type f 2>/dev/null | wc -l) files, $(du -sh "$d" 2>/dev/null | cut -f1)"
        done
    else
        echo "  (no uploads/ here)"
    fi
    echo ""
    # Production filled its disk on 2026-09-24 and Postgres crash-looped
    # ("No space left on device"); what held the space was not in the bundle.
    # Sizes by kind only — no image, container or volume names.
    echo "Docker disk use (by kind; old images and build cache are reclaimable):"
    docker system df 2>/dev/null | sed 's/^/  /' || echo "  unavailable"
    echo ""
    echo "Not collected on purpose: host name, user, working directory, interfaces,"
    echo "routes, resolv.conf, directory listings, untracked file names."
} > "$LOG_DIR/platform.txt" 2>&1

print_info "Collecting container state and images..."
{
    # This compose project only — never `docker ps -a` / `docker volume ls`,
    # which list every other workload on the host.
    echo "=== CONTAINERS (this compose project) ==="
    if compose_available; then compose ps -a 2>&1 || true; else echo "compose unavailable"; fi
    for service in backend worker report-worker frontend db; do
        cid=""
        compose_available && cid=$(compose ps -a -q "$service" 2>/dev/null | head -n 1)
        echo ""
        echo "--- $service ---"
        if [[ -n "$cid" ]]; then
            docker inspect --format 'Image: {{.Config.Image}}
ImageID: {{.Image}}
Created: {{.Created}}
State: {{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} restarts={{.RestartCount}} started={{.State.StartedAt}} finished={{.State.FinishedAt}}
MemoryLimit: {{.HostConfig.Memory}}' "$cid" 2>&1 || true
        else
            echo "(no container — never started or removed)"
        fi
    done
    echo ""
    echo "--- resource usage (this project's running containers) ---"
    running_ids=$(compose ps -q 2>/dev/null || true)
    if [[ -n "$running_ids" ]]; then
        # shellcheck disable=SC2086
        docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}' $running_ids 2>&1 || true
    else
        echo "(none running)"
    fi
} > "$LOG_DIR/containers.txt" 2>&1

print_info "Collecting configuration (values redacted unless plainly harmless)..."
{
    echo "=== .env (a value is shown only if it is a number, boolean, log level or a known enum) ==="
    if [[ -f .env ]]; then
        env_lines | while IFS=$'\t' read -r key val; do
            if [[ -z "$val" ]] || env_value_shown "$key" "$val"; then
                echo "$key=$val"
            else
                echo "$key=[REDACTED]"
            fi
        done
    else
        echo "(no .env)"
    fi
    echo ""
    echo "=== docker-compose.yml ==="
    cat docker-compose.yml 2>/dev/null || echo "(not found)"
} > "$LOG_DIR/configuration.txt" 2>&1

print_info "Collecting service versions and settings..."
{
    echo "=== SERVICE VERSIONS ==="
    if $BACKEND_UP; then
        echo "--- backend root '/' (deployed backend + frontend versions) ---"
        compose exec -T backend curl -fsS http://localhost:8000/ 2>&1 || echo "backend root unreachable"
        echo ""
        echo "--- ingestion settings ---"
        # app.core.config only — importing app.main would run migrations.
        compose exec -T backend python -c '
from app.core.config import settings as s
for k in ("APP_VERSION", "MAX_FILE_SIZE", "INGESTION_RETAIN_FILES_DAYS", "NESSUS_COMMIT_BATCH_SIZE",
          "NESSUS_PLUGIN_OUTPUT_MAX_CHARS", "NESSUS_SKIP_INFORMATIONAL_DEFAULT", "REPORT_ARTIFACT_TTL_HOURS"):
    # No backslash inside the f-string: Python < 3.12 rejects it (the
    # backend image is 3.11 — "SyntaxError ... cannot include a backslash").
    missing = "(not defined in this version)"
    print(f"{k}: {getattr(s, k, missing)}")
' 2>&1 || echo "Unable to read settings"
        echo ""
        echo "--- alembic current (stamped in the DB) vs heads (expected by the code) ---"
        compose exec -T backend sh -c 'cd /app && alembic current 2>&1; echo; alembic heads 2>&1' 2>&1 || echo "Failed to read Alembic state"
        echo "If current != heads, a migration did not apply at boot — see backend logs."
    else
        echo "backend container not running"
    fi
    if $DB_UP; then
        q "Postgres extensions" "SELECT extname, extversion FROM pg_extension ORDER BY extname;"
        q "Database size on disk" "SELECT pg_size_pretty(pg_database_size(current_database())) AS database_size;"
        q "Largest tables" "SELECT relname AS table, n_live_tup AS rows, pg_size_pretty(pg_total_relation_size(relid)) AS size FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 30;"
    fi
} > "$LOG_DIR/versions_and_schema.txt" 2>&1

# ----------------------------------------------------------------------
# Ingestion queue and parser audit (aggregates; file names never selected)
# ----------------------------------------------------------------------
print_info "Collecting ingestion queue and parse errors..."
if $DB_UP; then
    {
        echo "=== INGESTION QUEUE ==="
        echo "File names are shown as <job N>.<extension>."
        q "Last 50 jobs" "SELECT id, '<job ' || id || '>' || coalesce(substring(original_filename from '\.[A-Za-z0-9]{1,8}$'), '') AS file, status, tool_name, detected_file_type AS detected, format_override AS override, final_file_type AS final, source_tool, pg_size_pretty(file_size) AS size, skipped_count AS skipped, partial, retry_count AS retries, created_at, started_at, completed_at FROM ingestion_jobs ORDER BY created_at DESC LIMIT 50;"
        q "Processing now (stuck?)" "SELECT id, status, progress, started_at, last_heartbeat, EXTRACT(EPOCH FROM (NOW() - COALESCE(last_heartbeat, started_at)))::int AS secs_since_heartbeat FROM ingestion_jobs WHERE status = 'processing' ORDER BY started_at;"
        echo ""
        echo "--- Failed jobs: message and full traceback (last 15) ---"
        psql_q -A -c "\\x on" -c "SELECT id, coalesce(final_file_type, detected_file_type, tool_name) AS format, status, retry_count, error_message, parser_warnings, last_error, completed_at FROM ingestion_jobs WHERE last_error IS NOT NULL OR status = 'failed' ORDER BY created_at DESC LIMIT 15;" 2>&1 || echo "(query failed)"
        q "Parse errors by format and type" "SELECT file_type, error_type, status, count(*), max(created_at) AS latest FROM parse_errors GROUP BY 1, 2, 3 ORDER BY 4 DESC;"
        echo ""
        echo "--- Last 25 parse errors (no file preview, no error_details) ---"
        psql_q -A -c "\\x on" -c "SELECT id, file_type, error_type, error_message, user_message, status, created_at FROM parse_errors ORDER BY created_at DESC LIMIT 25;" 2>&1 || echo "(query failed)"
    } > "$LOG_DIR/ingestion.txt" 2>&1

    print_info "Collecting parser audit (aggregate field coverage — counts only)..."
    {
        echo "=== PARSER AUDIT ==="
        echo "Counts of rows and of rows carrying each field, per format/source."
        echo "Use it to see what each parser actually extracts from real output, what"
        echo "lands only in raw blobs, and which fields stay empty. No values are selected"
        echo "except tool vocabulary (script ids, JSON keys, record types, plugin titles)."

        echo ""; echo "##### Imports"
        q "Jobs by format and outcome" "SELECT coalesce(final_file_type, detected_file_type, tool_name, '?') AS format, status, count(*) AS jobs, sum(coalesce(skipped_count, 0)) AS skipped_rows, count(*) FILTER (WHERE partial) AS partial, count(*) FILTER (WHERE coalesce(parser_warnings, '') <> '') AS with_warnings, count(*) FILTER (WHERE format_override IS NOT NULL) AS overridden, pg_size_pretty(sum(file_size)) AS bytes FROM ingestion_jobs GROUP BY 1, 2 ORDER BY 1, 2;"
        q "Detection disagreed with the final format" "SELECT detected_file_type, format_override, final_file_type, count(*) FROM ingestion_jobs WHERE final_file_type IS DISTINCT FROM detected_file_type GROUP BY 1, 2, 3 ORDER BY 4 DESC;"
        # v2.420.0 — for testing new tools by upload: how each format was
        # recognised and what every recent import produced, row type by row
        # type.  Counts and format names only.
        q "Detection chain for every import (detected -> chosen -> parsed)" "SELECT coalesce(detected_file_type, '-') AS detected, coalesce(format_override, '-') AS chosen, coalesce(final_file_type, '-') AS parsed_by, coalesce(source_tool, '-') AS source_tool, status, count(*) AS jobs FROM ingestion_jobs GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 3, 5;"
        q "What each recent import produced (last 30 with a scan)" "SELECT j.id AS job, coalesce(j.final_file_type, j.tool_name) AS format, j.status, j.partial, j.skipped_count AS skipped, left(coalesce(j.progress, ''), 60) AS summary, (SELECT count(*) FROM host_scan_history h WHERE h.scan_id = j.scan_id) AS hosts, (SELECT count(*) FROM host_scan_history h WHERE h.scan_id = j.scan_id AND h.host_created) AS new_hosts, (SELECT count(*) FROM port_scan_history p WHERE p.scan_id = j.scan_id) AS ports, (SELECT count(*) FROM port_scan_history p WHERE p.scan_id = j.scan_id AND p.port_created) AS new_ports, (SELECT count(*) FROM vulnerabilities v WHERE v.scan_id = j.scan_id) AS obs_new, (SELECT count(*) FROM vulnerabilities v WHERE v.last_seen_scan_id = j.scan_id AND v.scan_id IS DISTINCT FROM j.scan_id) AS obs_reseen, (SELECT count(*) FROM web_interfaces w WHERE w.scan_id = j.scan_id) AS web, (SELECT count(*) FROM web_paths w WHERE w.scan_id = j.scan_id) AS paths, (SELECT count(*) FROM netexec_results n WHERE n.scan_id = j.scan_id) AS access, (SELECT count(*) FROM dns_records d WHERE d.scan_id = j.scan_id) AS dns, coalesce((j.uninterpreted_lines::jsonb->>'total')::int, 0) AS not_read FROM ingestion_jobs j WHERE j.scan_id IS NOT NULL ORDER BY j.id DESC LIMIT 30;"
        q "Parser warnings, grouped (first 300 chars)" "SELECT coalesce(final_file_type, tool_name) AS format, left(parser_warnings, 300) AS warning, count(*) FROM ingestion_jobs WHERE coalesce(parser_warnings, '') <> '' GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 60;"
        # v2.418.0 — the lines parsers did not interpret, as REDACTED shapes
        # (app/services/line_shapes.py: addresses, names, credentials, hashes,
        # paths and flag values already replaced at import).  Scrubbed again
        # with the rest of the bundle.  This is what replaces real samples.
        q "Lines not interpreted, by shape (redacted at import)" "SELECT coalesce(final_file_type, tool_name) AS format, s->>'kind' AS kind, sum((s->>'count')::int) AS lines, count(DISTINCT j.id) AS imports, left(s->>'shape', 240) AS shape FROM ingestion_jobs j, LATERAL jsonb_array_elements(coalesce(j.uninterpreted_lines::jsonb->'shapes', '[]'::jsonb)) AS s GROUP BY 1, 2, 5 ORDER BY 3 DESC LIMIT 200;"
        q "Scans by tool" "SELECT tool_name, scan_type, time_source, count(*) AS scans, count(command_line) AS with_command_line, count(version) AS with_version, count(start_time) AS with_start_time FROM scans GROUP BY 1, 2, 3 ORDER BY 4 DESC;"

        echo ""; echo "##### Hosts and ports"
        q "Host field coverage" "SELECT count(*) AS hosts, count(hostname) AS hostname, count(netbios_name) AS netbios, count(os_name) AS os_name, count(os_family) AS os_family, count(*) FILTER (WHERE os_name IS NOT NULL AND os_family IS NULL) AS os_without_family, count(mac_address) AS mac, count(mac_vendor) AS mac_vendor, count(smb_signing) AS smb_signing FROM hosts_v2;"
        q "Host state and hostname source" "SELECT state, hostname_source, count(*) FROM hosts_v2 GROUP BY 1, 2 ORDER BY 3 DESC;"
        q "OS families" "SELECT os_family, os_type, count(*) FROM hosts_v2 GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 30;"
        q "Port field coverage" "SELECT protocol, state, count(*) AS ports, count(service_name) AS service, count(service_product) AS product, count(service_version) AS version, count(*) FILTER (WHERE service_version IS NOT NULL AND service_product IS NULL) AS version_without_product, count(service_extrainfo) AS extrainfo, count(service_tunnel) AS tunnel, count(reason) AS reason, count(service_method) AS method FROM ports_v2 GROUP BY 1, 2 ORDER BY 3 DESC;"
        q "Port service_method (probed vs table-guessed)" "SELECT service_method, count(*) FROM ports_v2 GROUP BY 1 ORDER BY 2 DESC;"
        q "Port scripts by id (NSE, masscan banners)" "SELECT script_id, count(*) AS rows, avg(length(output))::int AS avg_len, max(length(output)) AS max_len FROM scripts_v2 GROUP BY 1 ORDER BY 2 DESC LIMIT 80;"
        q "Host scripts by id" "SELECT script_id, count(*) AS rows, avg(length(output))::int AS avg_len FROM host_scripts_v2 GROUP BY 1 ORDER BY 2 DESC LIMIT 40;"

        echo ""; echo "##### Scanner observations"
        q "Vulnerabilities by source and severity" "SELECT source, severity, count(*) AS rows, count(cvss_score) AS cvss, count(cvss_vector) AS vector, count(cve_id) AS cve, count(*) FILTER (WHERE exploitable) AS exploitable, count(plugin_output) AS output, count(solution) AS solution, count(description) AS description, count(\"references\") AS refs, count(port_id) AS on_port FROM vulnerabilities GROUP BY 1, 2 ORDER BY 1, 2;"
        q "Rows whose references hold more CVEs than cve_id" "SELECT source, count(*) FROM vulnerabilities WHERE \"references\" ~* 'CVE-[0-9]{4}-[0-9]+.*CVE-[0-9]{4}-[0-9]+' GROUP BY 1 ORDER BY 2 DESC;"
        q "Observations by source, catalog check and severity (all sources)" "SELECT source, coalesce(check_id, '-') AS check_id, severity, count(*) AS rows, count(DISTINCT host_id) AS hosts, count(port_id) AS on_port, count(name_id) AS on_name FROM vulnerabilities GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC LIMIT 120;"
        q "Most frequent titles from the other scanners (Nikto, nuclei, testssl, nmap, NetExec, SMBMap)" "SELECT source, severity, coalesce(check_id, '-') AS check_id, left(title, 90) AS title, count(*) AS rows FROM vulnerabilities WHERE upper(source::text) NOT IN ('NESSUS', 'OPENVAS') GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 80;"
        q "Most frequent Nessus/OpenVAS plugins (vendor titles)" "SELECT source, severity, plugin_id, left(title, 90) AS title, count(*) AS hosts FROM vulnerabilities WHERE source::text IN ('nessus', 'openvas', 'NESSUS', 'OPENVAS') GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 60;"

        echo ""; echo "##### Web"
        q "Web interfaces by source: field coverage" "SELECT source, count(*) AS rows, count(*) FILTER (WHERE host_id IS NULL) AS no_host, count(status_code) AS status, count(title) AS title, count(server_header) AS server, count(*) FILTER (WHERE technologies IS NOT NULL AND technologies::text NOT IN ('null', '[]', '{}')) AS tech, count(favicon_hash) AS favicon, count(tls_info) AS tls_info, count(cert_not_after) AS cert_expiry, count(cert_self_signed) AS self_signed_known, count(tls_weak_protocol) AS weak_tls_known, count(*) FILTER (WHERE tls_weak_protocol) AS weak_tls, count(cert_subject_org) AS cert_org, count(screenshot_path) AS screenshot, count(page_text) AS page_text, count(raw) AS raw FROM web_interfaces GROUP BY 1 ORDER BY 2 DESC;"
        q "Keys present in web_interfaces.raw, per source (data kept only in the blob)" "SELECT source, k AS raw_key, count(*) FROM web_interfaces, LATERAL jsonb_object_keys(CASE WHEN jsonb_typeof(raw::jsonb) = 'object' THEN raw::jsonb ELSE '{}'::jsonb END) AS k GROUP BY 1, 2 ORDER BY 1, 3 DESC;"
        q "Keys present in web_interfaces.tls_info, per source" "SELECT source, k AS tls_key, count(*) FROM web_interfaces, LATERAL jsonb_object_keys(CASE WHEN jsonb_typeof(tls_info::jsonb) = 'object' THEN tls_info::jsonb ELSE '{}'::jsonb END) AS k GROUP BY 1, 2 ORDER BY 1, 3 DESC;"
        q "Web interface status codes" "SELECT source, status_code, count(*) FROM web_interfaces GROUP BY 1, 2 ORDER BY 1, 3 DESC;"
        q "Discovered paths by source" "SELECT source, count(*) AS rows, count(size) AS size, count(status_code) AS status, count(*) FILTER (WHERE status_code BETWEEN 300 AND 399) AS redirects FROM web_paths GROUP BY 1 ORDER BY 2 DESC;"

        echo ""; echo "##### SMB / AD"
        q "NetExec / SMBMap outcomes by protocol" "SELECT tool, protocol, port, count(*) AS rows, count(*) FILTER (WHERE auth_success) AS login_ok, count(*) FILTER (WHERE auth_success IS FALSE) AS login_failed, count(*) FILTER (WHERE auth_success IS NULL) AS not_a_login, count(*) FILTER (WHERE local_admin) AS local_admin, count(*) FILTER (WHERE writable_share) AS writable_share, count(*) FILTER (WHERE shares IS NOT NULL AND shares::text NOT IN ('null', '[]', '{}')) AS with_shares, count(*) FILTER (WHERE length(raw_output) >= 10000) AS output_cut FROM netexec_results GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 40;"
        q "NetExec / SMBMap rows" "SELECT tool, protocol, count(*) AS rows, count(*) FILTER (WHERE auth_success) AS auth_success, count(*) FILTER (WHERE local_admin) AS local_admin, count(*) FILTER (WHERE smbv1) AS smbv1, count(domain_name) AS domain, count(username) AS username, count(*) FILTER (WHERE username ~* '(brute|dumping|enumerat|forcing)') AS status_line_as_username, count(shares) AS shares, count(users) AS users_col, count(groups) AS groups_col, count(policies) AS policies_col, count(host_id) AS on_host FROM netexec_results GROUP BY 1, 2 ORDER BY 3 DESC;"

        echo ""; echo "##### Names and registration"
        q "DNS records by type" "SELECT record_type, count(*) AS rows, count(resolver_name) AS with_resolver, count(ttl) AS with_ttl, count(scan_id) AS from_scan FROM dns_records GROUP BY 1 ORDER BY 2 DESC;"
        q "DNS names by kind" "SELECT kind, count(*) FROM dns_names GROUP BY 1 ORDER BY 2 DESC;"
        q "Network attribution (RDAP) coverage" "SELECT source, registry, count(*) AS blocks, count(asn) AS asn, count(as_name) AS as_name, count(org_name) AS org, count(country) AS country, count(cloud_provider) AS cloud FROM network_attributions GROUP BY 1, 2 ORDER BY 3 DESC;"
    } > "$LOG_DIR/parser_audit.txt" 2>&1
else
    echo "Database container not running — ingestion queue and parser audit unavailable." > "$LOG_DIR/ingestion.txt"
fi

# ----------------------------------------------------------------------
# Agent surface (2.438.0): refused calls never leave a traceback, so an
# agent failing quietly after an upgrade shows only here.  Counts only —
# path TEMPLATES (no ids), never bodies, rationales, commands or payloads.
# ----------------------------------------------------------------------
if $DB_UP; then
    print_info "Collecting agent-surface outcomes (counts only)..."
    {
        echo "=== AGENT SURFACE (counts; no bodies, commands or text) ==="
        q "Agent sessions by status" "SELECT status, count(*) FROM agent_sessions GROUP BY 1 ORDER BY 2 DESC;"
        q "Agent API calls by status class (last 7 days)" "SELECT (status_code / 100) || 'xx' AS class, count(*), count(*) FILTER (WHERE via_mcp) AS via_mcp FROM agent_api_calls WHERE created_at > now() - interval '7 days' GROUP BY 1 ORDER BY 1;"
        q "Refused / failed agent calls by route (last 7 days)" "SELECT method, coalesce(path_template, '(unmatched route)') AS route, status_code, coalesce(error_class, '-') AS error_class, count(*), max(created_at) AS last FROM agent_api_calls WHERE status_code >= 400 AND created_at > now() - interval '7 days' GROUP BY 1, 2, 3, 4 ORDER BY 5 DESC LIMIT 40;"
        q "MCP tool calls by tool and outcome (last 7 days)" "SELECT coalesce(tool_name, rpc_method, '-') AS tool, outcome, coalesce(error_code::text, '-') AS error_code, count(*), round(avg(duration_ms)) AS avg_ms FROM mcp_tool_calls WHERE created_at > now() - interval '7 days' GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 60;"
        q "MCP handshakes by client (last 7 days; the client names itself only in initialize)" "SELECT coalesce(client_name, '(unnamed)') AS client, coalesce(client_version, '-') AS version, coalesce(protocol_version, '-') AS protocol, count(*) AS handshakes FROM mcp_tool_calls WHERE rpc_method = 'initialize' AND created_at > now() - interval '7 days' GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 20;"
        q "Proposals by kind and status (an accept refused by the target keeps its reason in error)" "SELECT kind, status, source, count(*), count(error) AS with_accept_error FROM agent_proposals GROUP BY 1, 2, 3 ORDER BY 1, 2;"
        q "Evidence records by outcome" "SELECT outcome, count(*), count(raw_output_bytes) AS with_raw_output, pg_size_pretty(coalesce(sum(raw_output_bytes), 0)) AS raw_bytes FROM evidence_records GROUP BY 1 ORDER BY 2 DESC;"
        q "Agent feedback by source" "SELECT source, count(*), round(avg(overall_rating), 1) AS avg_rating, max(created_at) AS last FROM agent_feedback GROUP BY 1 ORDER BY 2 DESC;"
    } > "$LOG_DIR/agent_surface.txt" 2>&1
fi

# ----------------------------------------------------------------------
# Agent feedback — the agents' own words about what got in their way: the
# most direct account of agent trouble there is (diag 4: ten feedback rows
# the bundle could only count).  FREE TEXT: it is scrubbed like every other
# file, but a name the database never held survives, so the operator reads
# feedback.txt before moving the bundle (the end of the run says so), or
# leaves it out with --no-feedback.
# ----------------------------------------------------------------------
FEEDBACK_LIMIT=200
FEEDBACK_ROWS=0
if $DB_UP && $WITH_FEEDBACK; then
    print_info "Collecting agent feedback (newest $FEEDBACK_LIMIT, free text — review before sending)..."
    FEEDBACK_ROWS=$(psql_q -A -t -c "SELECT count(*) FROM agent_feedback;" 2>/dev/null | tr -dc '0-9')
    FEEDBACK_ROWS=${FEEDBACK_ROWS:-0}
    {
        echo "=== AGENT FEEDBACK (free text, scrubbed — READ BEFORE SENDING) ==="
        echo "Entries in the database: $FEEDBACK_ROWS; shown: the newest $FEEDBACK_LIMIT, newest first."
        echo "Ratings are 1-5. client/model: the agent session's attribution."
        echo ""
        # One "field: value" per line, a blank line between entries (psql's
        # aligned expanded form padded every separator to the widest value —
        # lines thousands of characters long).  JSON pretty-printed to read.
        psql_q -x -A -F ': ' -c "SELECT f.id, f.created_at, f.source, f.status, f.prompt_version,
                f.overall_rating AS rating, s.generated_by_tool AS client,
                coalesce(f.agent_metrics->>'model', s.generated_by_model) AS model,
                f.friction_notes,
                jsonb_pretty(f.api_critiques::jsonb) AS api_critiques,
                jsonb_pretty(f.tool_suggestions::jsonb) AS tool_suggestions,
                f.reviewer_notes
            FROM agent_feedback f LEFT JOIN agent_sessions s ON s.id = f.agent_session_id
            ORDER BY f.created_at DESC, f.id DESC LIMIT $FEEDBACK_LIMIT;" 2>&1 \
            || echo "(query failed — see above)"
    } > "$LOG_DIR/feedback.txt" 2>&1
elif ! $WITH_FEEDBACK; then
    echo "Agent feedback left out (--no-feedback)." > "$LOG_DIR/feedback.txt"
fi

# ----------------------------------------------------------------------
# TLS certificate actually served.  The health checks use curl -k, so a
# certificate problem is invisible everywhere else in the bundle.  Derived
# facts only: no subject, issuer or SAN values (they name the deployment).
# ----------------------------------------------------------------------
print_info "Collecting the served TLS certificate (derived facts only)..."
{
    echo "=== TLS CERTIFICATE (derived facts; no names) ==="
    if ! command -v openssl >/dev/null 2>&1; then
        echo "openssl not on PATH — unavailable"
    else
        chain="$WORK/tls_chain.pem"
        # Bounded, and a refused / stalled connection is a RESULT: under
        # pipefail a failed s_client used to end the whole collection (and the
        # trap deleted everything gathered) exactly when the frontend was down.
        tls_timeout=(); command -v timeout >/dev/null 2>&1 && tls_timeout=(timeout 10)
        echo | ${tls_timeout[@]+"${tls_timeout[@]}"} openssl s_client -connect localhost:443 -servername localhost -showcerts 2>/dev/null \
            | sed -n '/-BEGIN CERTIFICATE-/,/-END CERTIFICATE-/p' > "$chain" || true
        if [[ ! -s "$chain" ]]; then
            echo "No certificate served on localhost:443 (connection refused, TLS failed, or no answer within 10 s)."
        else
            echo "certificates presented: $(grep -c 'BEGIN CERTIFICATE' "$chain")"
            if [[ "$(openssl x509 -in "$chain" -noout -issuer_hash)" == "$(openssl x509 -in "$chain" -noout -subject_hash)" ]]; then
                echo "issued by: itself (self-signed — not from the local root CA)"
            else
                echo "issued by: a CA (issuer differs from subject — ca/local-ca.sh install)"
            fi
            echo "expires: $(openssl x509 -in "$chain" -noout -enddate | cut -d= -f2)"
            if ! openssl x509 -in "$chain" -noout -checkend 0 >/dev/null; then
                echo "EXPIRED"
            elif ! openssl x509 -in "$chain" -noout -checkend 2592000 >/dev/null; then
                echo "expires within 30 days"
            fi
            san=$(openssl x509 -in "$chain" -noout -ext subjectAltName 2>/dev/null || true)
            echo "SAN entries: $(grep -o 'DNS:' <<<"$san" | wc -l) DNS name(s), $(grep -o 'IP Address:' <<<"$san" | wc -l) IP address(es)"
            host_ip=$(env_value HOST_IP)
            if [[ -z "$host_ip" ]]; then
                echo "HOST_IP (.env) named in the certificate: HOST_IP not set"
            elif grep -qE "IP Address:${host_ip//./\\.}(,|$)" <<<"$san"; then
                echo "HOST_IP (.env) named in the certificate: yes"
            else
                echo "HOST_IP (.env) named in the certificate: NO — clients reaching it by address will refuse it"
            fi
            if [[ -f ssl/certs/networkmapper.crt ]]; then
                served_fp=$(openssl x509 -in "$chain" -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2)
                file_fp=$(openssl x509 -in ssl/certs/networkmapper.crt -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2)
                if [[ "$served_fp" == "$file_fp" ]]; then
                    echo "served == ssl/certs/networkmapper.crt: yes"
                else
                    echo "served == ssl/certs/networkmapper.crt: NO — nginx is serving an older certificate (restart the frontend)"
                fi
                if [[ -r ssl/certs/networkmapper.key ]]; then
                    cert_pub=$(openssl x509 -in ssl/certs/networkmapper.crt -noout -pubkey 2>/dev/null | openssl sha256 2>/dev/null)
                    key_pub=$(openssl pkey -in ssl/certs/networkmapper.key -pubout 2>/dev/null | openssl sha256 2>/dev/null)
                    [[ -n "$cert_pub" && "$cert_pub" == "$key_pub" ]] \
                        && echo "ssl/certs key matches its certificate: yes" \
                        || echo "ssl/certs key matches its certificate: NO"
                else
                    echo "ssl/certs key matches its certificate: key not readable by this user"
                fi
            else
                echo "ssl/certs/networkmapper.crt: not found"
            fi
        fi
    fi
} > "$LOG_DIR/tls.txt" 2>&1 || echo "(a TLS check command failed; the facts above are what was gathered)" >> "$LOG_DIR/tls.txt"

# ----------------------------------------------------------------------
# Container logs
# ----------------------------------------------------------------------
print_info "Collecting container logs${SINCE:+ (since $SINCE)}..."
LOG_ARGS=(--no-color --timestamps)
[[ -n "$SINCE" ]] && LOG_ARGS+=(--since "$SINCE")
if compose_available; then
    for service in backend worker report-worker frontend db; do
        {
            echo "=== ${service} LOGS ==="
            compose logs "${LOG_ARGS[@]}" "$service" 2>&1 || echo "[collect-logs] compose logs '$service' failed"
        } > "$LOG_DIR/logs_${service}.txt"
    done
else
    echo "No docker compose binary on PATH — container logs unavailable." > "$LOG_DIR/logs_unavailable.txt"
fi

# ----------------------------------------------------------------------
# Health and analysis
# ----------------------------------------------------------------------
print_info "Performing health checks..."
{
    echo "=== HEALTH ==="
    # Only nginx (443) is published; backend :8000 and db :5432 are internal.
    BASE="https://localhost"
    echo "nginx /health: $(curl -k -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE/health" 2>/dev/null || echo 000)"
    echo "backend via nginx (/openapi.json): $(curl -k -s -o /dev/null -w '%{http_code}' --max-time 15 "$BASE/openapi.json" 2>/dev/null || echo 000)"
    echo "backend /ready via nginx: $(curl -k -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE/ready" 2>/dev/null || echo 000)"
    if $BACKEND_UP; then
        echo "backend internal /health (DB probe):"
        compose exec -T backend curl -fsS --max-time 10 http://localhost:8000/health 2>&1 || echo "  failed"
    fi
    echo ""
    for port in 443 3000; do
        if ss -tln 2>/dev/null | grep -q ":$port "; then echo "port $port listening"; else echo "port $port NOT listening"; fi
    done
} > "$LOG_DIR/health.txt" 2>&1

print_info "Analysing error patterns..."
tracebacks() {
    [[ -f "$1" ]] || { echo "(no log)"; return; }
    grep -n -A 40 -B 2 "Traceback (most recent call last)" "$1" 2>/dev/null \
        | awk 'BEGIN{RS="--\n"} {a[NR]=$0} END{start=(NR>10)?NR-9:1; for(i=start;i<=NR;i++) print a[i] "--"}' \
        || echo "(no tracebacks)"
}
{
    echo "=== ERROR ANALYSIS ==="
    for service in backend worker report-worker; do
        f="$LOG_DIR/logs_${service}.txt"
        echo ""
        echo "##### $service — error line counts by logger/message head"
        if [[ -f "$f" ]]; then
            grep -E ' (ERROR|CRITICAL|WARNING) ' "$f" 2>/dev/null | sed -E 's/^[^ ]+ +//; s/[0-9]+/N/g' \
                | cut -c1-160 | sort | uniq -c | sort -rn | head -40 || true
        fi
        echo ""
        echo "##### $service — last 10 tracebacks"
        tracebacks "$f"
    done
    echo ""
    echo "##### Parser 'Skipping' / 'malformed' lines (worker)"
    grep -iE 'skipping|malformed|unrecognised|unrecognized|could not parse' "$LOG_DIR/logs_worker.txt" 2>/dev/null \
        | sed -E 's/^[^ ]+ +//' | cut -c1-200 | sort | uniq -c | sort -rn | head -40 || true
    echo ""
    echo "##### Auth events (backend)"
    grep -iE 'login|unauthori[sz]ed|forbidden|locked|2fa|totp' "$LOG_DIR/logs_backend.txt" 2>/dev/null | tail -30 || true
} > "$LOG_DIR/error_analysis.txt" 2>&1

{
    echo "=== CODE ==="
    if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        echo "branch: $(git branch --show-current 2>/dev/null)"
        git log --oneline -10 2>/dev/null || true
        echo ""
        echo "modified tracked files (untracked names are not collected):"
        git status --short --untracked-files=no 2>/dev/null || true
    else
        echo "Not a git checkout (file-copy deployment) — versions are in platform.txt."
    fi
} > "$LOG_DIR/code.txt" 2>&1

cat > "$LOG_DIR/README.txt" <<EOF
=== BLUESTICK DIAGNOSTICS BUNDLE (ANONYMISED) ===
Collected (UTC): $(date -u +%Y-%m-%dT%H:%M:%SZ)${SINCE:+
Container logs since: $SINCE}

Files:
- platform.txt            kernel, CPU/memory/disk, versions, upload dir counts
- containers.txt          container state, images, limits, resource usage
- configuration.txt       .env keys (values redacted unless numeric/boolean), docker-compose.yml
- versions_and_schema.txt deployed versions, ingestion settings, Alembic state, table sizes
- ingestion.txt           ingestion queue, failed-job tracebacks, parse errors
- parser_audit.txt        per-format field coverage (counts only): what each parser
                          extracts, what lands only in raw blobs, what stays empty;
                          and the lines parsers did not interpret, as redacted
                          shapes (values replaced by <IP>, <HOST>, <VALUE>…);
                          per import: how it was recognised and what it
                          produced (hosts, ports, observations, web, paths,
                          access results, DNS); observations by source and
                          catalog check; NetExec/SMBMap outcomes by protocol
- logs_<service>.txt      backend, worker, report-worker, frontend (nginx), db
- health.txt              reachability through nginx and the internal DB probe
- tls.txt                 the certificate nginx serves: self-signed or CA-issued,
                          expiry, SAN counts, whether it names HOST_IP, whether it
                          is the file in ssl/certs and matches its key (no names)
- agent_surface.txt       agent sessions, refused agent calls by route, MCP tool
                          outcomes and clients, proposals (with accept errors),
                          evidence records (outcomes, raw-output size),
                          feedback — counts only
- feedback.txt            agent feedback IN FULL (newest 200): ratings, friction
                          notes, endpoint critiques, tool suggestions, reviewer
                          notes. FREE TEXT — scrubbed, but read it before sending;
                          collect with --no-feedback to leave it out
- error_analysis.txt      error counts, tracebacks, parser skip lines, auth events
- code.txt                branch / recent commits when deployed from git
- anonymisation.txt       how many values of each kind were replaced

Anonymisation: identifying values are replaced by stable pseudonyms — the
same address/name/file keeps the same token throughout (<ip-private-3>,
<host-12>, <file-2>, <user-1>, <url-4>, <secret-1> …). Database row data
inside SQL errors is removed. See scripts/scrub_logs.py for the rules.
EOF

# ----------------------------------------------------------------------
# Scrub (fail closed), then publish the bundle
# ----------------------------------------------------------------------
print_info "Anonymising the bundle..."
# The product's own words (tool names, field names) are not identifying when
# they turn up as a host or user name (a lab host called "nessus").
VOCAB_ARGS=()
for d in "$SCRIPT_DIR/../backend/app"; do
    [[ -d "$d" ]] && VOCAB_ARGS+=(--vocabulary "$d")
done
if ! python3 "$SCRUBBER" "$LOG_DIR" --terms "$TERMS" --report "$LOG_DIR/anonymisation.txt" \
        ${VOCAB_ARGS[@]+"${VOCAB_ARGS[@]}"}; then
    print_error "Anonymisation failed — no bundle was produced (the raw collection has been deleted)."
    exit 1
fi

tar -czf "$WORK/$LOG_NAME.tar.gz" -C "$WORK" "$LOG_NAME"
mv "$WORK/$LOG_NAME.tar.gz" "$ORIG_PWD/$LOG_NAME.tar.gz"

echo ""
print_success "Anonymised diagnostics bundle: $ORIG_PWD/$LOG_NAME.tar.gz"
cat "$LOG_DIR/anonymisation.txt"
echo ""
print_warning "Skim it before sending: tar -xzf $LOG_NAME.tar.gz && grep -ri '<client name>' $LOG_NAME/"
if $WITH_FEEDBACK && [[ "${FEEDBACK_ROWS:-0}" -gt 0 ]]; then
    print_warning "It includes agent feedback as free text ($FEEDBACK_ROWS entries, newest $FEEDBACK_LIMIT): read $LOG_NAME/feedback.txt before moving the bundle."
    print_info "To send without it: re-run with --no-feedback (or delete feedback.txt and re-pack the bundle)."
fi
print_info "Names only the operator knows can be added with --terms FILE."
