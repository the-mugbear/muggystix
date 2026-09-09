#!/usr/bin/env bash
# ------------------------------------------------------------------
# upgrade-instance.sh — carry a running BlueStick instance's local state
# into a freshly copied source tree, then deploy it.
#
# For hosts that deploy by file copy (no git access).  The expected
# starting point, all done by the operator BEFORE running this:
#
#   1. The running instance's folder was renamed, e.g.
#        /srv/bluestick  ->  /srv/bluestick_backup_2026-09-09
#   2. The new source tree was copied next to it and renamed to the
#      ORIGINAL folder name:
#        /srv/muggystix-main  ->  /srv/bluestick
#   3. This script is run from the NEW folder:
#        cd /srv/bluestick && ./scripts/upgrade-instance.sh
#
# Why the name matters: docker compose keys volumes, networks and
# container labels on the project name, which defaults to the folder
# name.  Keeping the old name is what makes the new code find the old
# database volume (<name>_postgres_data) instead of starting empty.
# This script checks that volume exists before it touches anything.
#
# What it carries across (none of it is in version control):
#   .env                     secrets + host IP + CORS
#   ssl/certs/*.crt|*.key    the TLS identity MCP clients already trust
#   <UPLOAD_DIR>  (./uploads) scan storage, report artifacts, seed manifests
#   NGINX_CONFIG             only if .env points at a custom nginx conf
#   .deploy-rollback-state   so deploy.sh option 7 still knows the prior build
#
# Then it hands off to ./scripts/deploy.sh option 1 (pre-deploy DB backup,
# rollback image tags, rebuild, recreate, health wait) and verifies the
# version the API reports matches platform_version.json.
#
# Usage:
#   ./scripts/upgrade-instance.sh [--from <old-folder>] [--copy-uploads]
#                                 [--no-deploy] [--allow-fresh-db] [--yes]
#
#   --from <dir>       The renamed old instance.  Auto-detected as the newest
#                      sibling named <name>_backup_* / <name>-backup_* that
#                      contains a docker-compose.yml, when only one exists.
#   --copy-uploads     Copy the uploads dir instead of moving it (slower,
#                      doubles disk, but leaves the old folder self-contained).
#   --no-deploy        Stop after carrying state across; print the deploy
#                      command instead of running it.
#   --allow-fresh-db   Proceed even when no <project>_postgres_data volume
#                      exists (a genuinely new install, or a deliberate reset).
#   --yes              Skip confirmation prompts.
#
# Idempotent: a file already present and identical in the new folder is
# skipped; one that is present and DIFFERENT aborts rather than being
# overwritten, so a half-run can be re-run safely and a wrong --from can't
# clobber real state.
# ------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}  $*${NC}"; }
ok()      { echo -e "${GREEN}✔ $*${NC}"; }
warn()    { echo -e "${YELLOW}! $*${NC}"; }
fail()    { echo -e "${RED}✖ $*${NC}" >&2; exit 1; }
section() { echo ""; echo -e "${BLUE}== $* ==${NC}"; }

FROM=""
COPY_UPLOADS=0
DEPLOY=1
ALLOW_FRESH_DB=0
YES=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --from) [[ $# -ge 2 ]] || fail "--from needs a path"; FROM="$2"; shift 2 ;;
        --from=*) FROM="${1#--from=}"; shift ;;
        --copy-uploads) COPY_UPLOADS=1; shift ;;
        --no-deploy) DEPLOY=0; shift ;;
        --allow-fresh-db) ALLOW_FRESH_DB=1; shift ;;
        --yes|-y) YES=1; shift ;;
        -h|--help) sed -n '2,50p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) fail "Unknown argument: $1 (see --help)" ;;
    esac
done

confirm() {
    # confirm "<question>" — returns 0 on yes.  --yes answers yes to all.
    if [[ $YES -eq 1 ]]; then return 0; fi
    echo -n "$1 [y/N]: "
    local answer; read -r answer
    [[ "$answer" =~ ^[Yy] ]]
}

# Read KEY from an env file: last uncommented assignment wins, surrounding
# quotes stripped.  Prints nothing when unset.
env_get() {
    local file="$1" key="$2"
    [[ -f "$file" ]] || return 0
    { grep -E "^[[:space:]]*${key}=" "$file" || true; } | tail -n 1 | sed -E "s/^[[:space:]]*${key}=//; s/^[\"']//; s/[\"']\$//"
}

# Carry one regular file: absent → copy; identical → skip; different → abort.
carry_file() {
    local rel="$1" src="$OLD/$1" dst="$PROJECT_ROOT/$1"
    if [[ ! -f "$src" ]]; then
        return 1
    fi
    if [[ -f "$dst" ]]; then
        if cmp -s "$src" "$dst"; then
            ok "$rel already present and identical — skipped"
            return 0
        fi
        fail "$rel exists in the new folder and DIFFERS from the old instance's.
   Refusing to overwrite.  Diff them and delete the new one if the old is correct:
     diff '$src' '$dst'"
    fi
    mkdir -p "$(dirname "$dst")"
    cp -p "$src" "$dst"
    ok "$rel copied"
}

# ------------------------------------------------------------------
section "Preconditions"
# ------------------------------------------------------------------
[[ -f docker-compose.yml ]] || fail "No docker-compose.yml in $PROJECT_ROOT — run this from the NEW source tree's scripts/ dir."
[[ -x scripts/deploy.sh ]]   || fail "scripts/deploy.sh missing or not executable in $PROJECT_ROOT."
command -v docker >/dev/null || fail "docker not found on PATH."
if docker compose version >/dev/null 2>&1; then DC="docker compose"; else DC="docker-compose"; fi

NAME="$(basename "$PROJECT_ROOT")"
PARENT="$(dirname "$PROJECT_ROOT")"
info "New instance folder: $PROJECT_ROOT"

# Locate the old instance.
if [[ -z "$FROM" ]]; then
    candidates=()
    for d in "$PARENT/${NAME}_backup_"* "$PARENT/${NAME}-backup-"* "$PARENT/${NAME}_backup-"* "$PARENT/${NAME}-backup_"*; do
        [[ -d "$d" && -f "$d/docker-compose.yml" ]] && candidates+=("$d")
    done
    if [[ ${#candidates[@]} -eq 0 ]]; then
        fail "No renamed old instance found next to $PROJECT_ROOT.
   Expected a sibling like '${NAME}_backup_<date>' containing docker-compose.yml.
   Pass it explicitly:  --from /path/to/old-folder"
    elif [[ ${#candidates[@]} -gt 1 ]]; then
        echo "Several candidate old instances found:" >&2
        for d in "${candidates[@]}"; do echo "   $d" >&2; done
        fail "Ambiguous — pass the right one with --from <dir>."
    fi
    FROM="${candidates[0]}"
fi
OLD="$(cd "$FROM" 2>/dev/null && pwd)" || fail "--from '$FROM' is not a directory."
[[ "$OLD" != "$PROJECT_ROOT" ]] || fail "--from points at the new folder itself."
[[ -f "$OLD/docker-compose.yml" ]] || fail "$OLD has no docker-compose.yml — is that really the old instance?"
[[ -f "$OLD/.env" ]] || fail "$OLD has no .env — nothing to carry across.  If the old instance kept its .env elsewhere, copy it in by hand."
ok "Old instance folder: $OLD"

# Versions, for the record.
ver() { python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('backend','?')+' / '+d.get('frontend','?'))" "$1" 2>/dev/null || echo "?"; }
OLD_VER="$(ver "$OLD/platform_version.json")"
NEW_VER="$(ver "$PROJECT_ROOT/platform_version.json")"
info "Version (backend / frontend):  $OLD_VER  ->  $NEW_VER"
if [[ "$OLD_VER" == "$NEW_VER" ]]; then
    warn "Old and new report the same version — is the new folder really the new code?"
fi

# ------------------------------------------------------------------
section "Docker project identity"
# ------------------------------------------------------------------
# Compose project name: COMPOSE_PROJECT_NAME from the .env that is about to
# be carried across, else the folder name.  The old instance ran under the
# name of the folder it was in at the time, which — if the operator followed
# the rename recipe — is this folder's name.
PROJECT="$(env_get "$OLD/.env" COMPOSE_PROJECT_NAME)"
if [[ -n "$PROJECT" ]]; then
    info "Project name pinned by .env: $PROJECT"
else
    PROJECT="$(echo "$NAME" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9_-]//g')"
    info "Project name from folder: $PROJECT"
fi

DB_VOLUME="${PROJECT}_postgres_data"
if docker volume inspect "$DB_VOLUME" >/dev/null 2>&1; then
    ok "Database volume found: $DB_VOLUME"
else
    warn "No database volume named $DB_VOLUME."
    others="$(docker volume ls --format '{{.Name}}' | grep '_postgres_data$' || true)"
    if [[ -n "$others" ]]; then
        echo "   Postgres volumes that DO exist on this host:"
        echo "$others" | sed 's/^/     /'
        echo "   The old instance probably ran under one of those project names."
        echo "   Fix: rename this folder to that name (minus _postgres_data), or set"
        echo "   COMPOSE_PROJECT_NAME=<that name> in $OLD/.env before re-running."
    fi
    if [[ $ALLOW_FRESH_DB -eq 1 ]]; then
        warn "--allow-fresh-db given: continuing; the deploy will create an EMPTY database."
    else
        fail "Refusing to continue — deploying now would start with an empty database.
   Pass --allow-fresh-db only if that is what you want."
    fi
fi

running="$(docker ps --filter "label=com.docker.compose.project=$PROJECT" --format '{{.Names}} ({{.Status}})' || true)"
if [[ -n "$running" ]]; then
    info "Containers currently running under project '$PROJECT':"
    echo "$running" | sed 's/^/     /'
    info "They stay up: deploy.sh takes a live pg_dump before rebuilding and recreates them."
else
    info "No containers running under project '$PROJECT' (the pre-deploy backup will be a volume snapshot)."
fi

# ------------------------------------------------------------------
section "Carrying local state across"
# ------------------------------------------------------------------
carry_file ".env" || fail ".env vanished mid-run?"

certs_found=0
for f in ssl/certs/networkmapper.crt ssl/certs/networkmapper.key ssl/certs/openssl.conf; do
    if carry_file "$f"; then certs_found=1; fi
done
if [[ $certs_found -eq 0 ]]; then
    warn "No TLS certificate in $OLD/ssl/certs — deploy.sh will generate a new one."
    warn "Every MCP client that trusted the old fingerprint will need scripts/trust-cert.sh again."
fi

NGINX_CONF="$(env_get "$PROJECT_ROOT/.env" NGINX_CONFIG)"
if [[ -n "$NGINX_CONF" && "$NGINX_CONF" != "./ssl-nginx.conf" && "$NGINX_CONF" != "ssl-nginx.conf" ]]; then
    if [[ "$NGINX_CONF" = /* ]]; then
        info "NGINX_CONFIG is absolute ($NGINX_CONF) — shared path, nothing to carry."
    else
        carry_file "$NGINX_CONF" || warn "NGINX_CONFIG=$NGINX_CONF is set but not found in the old folder."
    fi
fi

carry_file ".deploy-rollback-state" || info "No .deploy-rollback-state in the old folder (option 7 rollback history starts fresh)."

# Uploads: the live data directory.  Moved (instant, same filesystem) unless
# --copy-uploads.  Respects UPLOAD_DIR when .env overrides the default.
UPLOAD_DIR="$(env_get "$PROJECT_ROOT/.env" UPLOAD_DIR)"
UPLOAD_DIR="${UPLOAD_DIR:-./uploads}"
if [[ "$UPLOAD_DIR" = /* ]]; then
    info "UPLOAD_DIR is absolute ($UPLOAD_DIR) — shared path, nothing to move."
else
    rel="${UPLOAD_DIR#./}"
    src="$OLD/$rel"; dst="$PROJECT_ROOT/$rel"
    if [[ ! -d "$src" ]]; then
        warn "No $rel/ in the old folder — nothing to carry (deploy.sh will create an empty one)."
    elif [[ -d "$dst" ]] && [[ -n "$(ls -A "$dst" 2>/dev/null)" ]]; then
        if [[ -n "$(ls -A "$src" 2>/dev/null)" ]]; then
            fail "$rel/ exists and is non-empty in BOTH folders.  Refusing to merge blindly.
   If the new one is just what deploy.sh created, empty it and re-run:  rm -rf '$dst'"
        else
            ok "$rel/ already in the new folder; the old one is empty — nothing to move."
        fi
    else
        [[ -d "$dst" ]] && rmdir "$dst"
        size="$(du -sh "$src" 2>/dev/null | cut -f1)"
        if [[ $COPY_UPLOADS -eq 1 ]]; then
            info "Copying $rel/ ($size)…"
            cp -a "$src" "$dst"
            ok "$rel/ copied (old folder keeps its copy)"
        else
            mv "$src" "$dst"
            ok "$rel/ moved ($size) — a rollback to the old folder must move it back"
        fi
    fi
fi

# ------------------------------------------------------------------
section "Configuration drift"
# ------------------------------------------------------------------
# Settings the new version's .env.example declares (uncommented) that the
# carried .env does not set.  Informational: defaults apply, but the operator
# should know a new knob exists.
if [[ -f .env.example ]]; then
    missing=()
    while IFS= read -r key; do
        [[ -z "$key" ]] && continue
        if ! grep -qE "^[[:space:]]*${key}=" .env; then missing+=("$key"); fi
    done < <(grep -E '^[A-Z][A-Z0-9_]*=' .env.example | cut -d= -f1 | sort -u)
    if [[ ${#missing[@]} -gt 0 ]]; then
        warn "Keys in the new .env.example that your .env does not set (defaults apply):"
        printf '     %s\n' "${missing[@]}"
    else
        ok ".env sets every key .env.example declares."
    fi
fi
if [[ -z "$(env_get .env CREDENTIAL_ENCRYPTION_KEY)" ]]; then
    warn "CREDENTIAL_ENCRYPTION_KEY unset — stored integration credentials are tied to SECRET_KEY (backend warns at boot)."
fi

# Anything else at the top level of the old folder that the new one lacks.
# Not copied — listed so a custom file the operator relies on isn't lost.
skip_re='^(\.git|\.env.*|ssl|uploads|node_modules|build|dist|__pycache__|\.pytest_cache|\.venv|venv|\.deploy-rollback-state|CLAUDE\.md|CHANGELOG\.md|\.DS_Store)$'
extras=()
for entry in "$OLD"/* "$OLD"/.[!.]*; do
    [[ -e "$entry" ]] || continue
    base="$(basename "$entry")"
    [[ "$base" =~ $skip_re ]] && continue
    [[ "$base" == "${UPLOAD_DIR#./}" ]] && continue
    [[ -e "$PROJECT_ROOT/$base" ]] || extras+=("$base")
done
if [[ ${#extras[@]} -gt 0 ]]; then
    warn "Present in the old folder but not in the new one (NOT copied — do so by hand if you rely on them):"
    printf '     %s\n' "${extras[@]}"
fi

# ------------------------------------------------------------------
section "Deploy"
# ------------------------------------------------------------------
if [[ $DEPLOY -eq 0 ]]; then
    info "--no-deploy: state carried across.  When ready:"
    echo "     cd '$PROJECT_ROOT' && ./scripts/deploy.sh    # option 1"
    exit 0
fi

echo "deploy.sh option 1 will: back up the DB, tag the running images for rollback,"
echo "rebuild, recreate every container, and wait for the backend to come healthy."
echo "Boot runs every pending Alembic migration ($OLD_VER -> $NEW_VER)."
if ! confirm "Deploy now?"; then
    info "Not deploying.  When ready:  cd '$PROJECT_ROOT' && ./scripts/deploy.sh    # option 1"
    exit 0
fi

# deploy.sh is menu-driven; feed it option 1.  With .env and certs in place
# option 1 asks nothing else.
set +e
printf '1\n' | ./scripts/deploy.sh
deploy_rc=$?
set -e
if [[ $deploy_rc -ne 0 ]]; then
    fail "deploy.sh exited with $deploy_rc.  Roll back with:  ./scripts/deploy.sh  (option 7)
   To return to the old folder entirely: $DC down; swap the folder names back;
   move $rel/ back into it; docker compose up -d there."
fi

# ------------------------------------------------------------------
section "Verify the new code is what's serving"
# ------------------------------------------------------------------
# platform_version.json is bind-mounted, so the banner alone proves nothing —
# but after a rebuild+recreate the API's reported version must match it, and
# the containers must be younger than this run.
HOST_IP="$(env_get .env HOST_IP)"
EXPECTED="$(python3 -c "import json; print(json.load(open('platform_version.json'))['backend'])" 2>/dev/null || true)"
if [[ -n "$HOST_IP" && -n "$EXPECTED" ]] && command -v curl >/dev/null; then
    reported="$(curl -sk --max-time 10 "https://${HOST_IP}/api/v1/" 2>/dev/null \
        | python3 -c "import json,sys; print(json.load(sys.stdin).get('version',''))" 2>/dev/null || true)"
    if [[ -z "$reported" ]]; then
        reported="$(curl -sk --max-time 10 "https://${HOST_IP}/" -H 'Accept: application/json' 2>/dev/null \
            | python3 -c "import json,sys; print(json.load(sys.stdin).get('version',''))" 2>/dev/null || true)"
    fi
    if [[ "$reported" == "$EXPECTED" ]]; then
        ok "API reports backend $reported (matches platform_version.json)"
    elif [[ -z "$reported" ]]; then
        warn "Could not read a version from https://${HOST_IP}/ — check manually: curl -sk https://${HOST_IP}/api/v1/"
    else
        warn "API reports backend '$reported' but platform_version.json says '$EXPECTED' — the rebuild may not have landed."
    fi
fi
echo ""
$DC ps --format 'table {{.Name}}\t{{.Status}}\t{{.CreatedAt}}' 2>/dev/null || $DC ps
echo ""
ok "Upgrade complete.  Old instance retained at: $OLD"
info "Confirm the footer in the browser shows frontend $(ver platform_version.json | cut -d/ -f2 | tr -d ' ')."
info "Once satisfied, the old folder can be removed; it holds a copy of .env and the TLS key."
