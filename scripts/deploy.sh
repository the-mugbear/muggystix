#!/bin/bash

#
# BlueStick Deployment Helper
#
# For most cases, you can simply run:
#   docker compose up -d
#
# This script handles first-time setup tasks like SSL certificate generation,
# environment configuration with auto-detected IP, and common operations.
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
PURPLE='\033[0;35m'
NC='\033[0m'

print_info() { echo -e "${BLUE} $1${NC}"; }
print_success() { echo -e "${GREEN} $1${NC}"; }
print_error() { echo -e "${RED} $1${NC}"; }
print_warning() { echo -e "${YELLOW} $1${NC}"; }
print_header() { echo -e "${PURPLE} $1${NC}"; }

cd "$PROJECT_ROOT"

# ------------------------------------------------------------------
# Resolve the Compose command.
#
# Legacy docker-compose v1 (the Python 1.29.x line, now EOL) crashes
# with "KeyError: 'ContainerConfig'" when recreating containers whose
# images were built by BuildKit — i.e. every modern rebuild.  The
# failure surfaces on the recreate path, so it bites every `up --build`
# that picks up a freshly built image, not just crash recovery.
# Prefer the Docker Compose v2 plugin ("docker compose") and only fall
# back to the v1 binary if the plugin is genuinely unavailable.
# ------------------------------------------------------------------
if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    DC="docker-compose"
    print_warning "Using legacy docker-compose v1. If a rebuild fails with"
    print_warning "\"KeyError: 'ContainerConfig'\", install the Docker Compose v2"
    print_warning "plugin (docker-compose-plugin) — v1 is incompatible with"
    print_warning "BuildKit-built images."
else
    print_error "Neither 'docker compose' (v2 plugin) nor 'docker-compose' found."
    print_error "Install Docker Compose before running this script."
    exit 1
fi

# The commit shown under About BlueStick. The frontend build context has no
# .git/, so it is passed in as a build arg; a file-copied tree without .git/
# keeps whatever GIT_COMMIT the caller set, else "unknown".
if [ -z "${GIT_COMMIT:-}" ]; then
    GIT_COMMIT=$(git -C "$(dirname "$0")/.." rev-parse --short HEAD 2>/dev/null || echo unknown)
fi
export GIT_COMMIT

# ------------------------------------------------------------------
# Rollback support (B2-1)
#
# Option 1 rebuilds images in place (same tag), so a deploy whose boot
# migration fails crash-loops with no prior image to revert to.  Before each
# rebuild we (a) snapshot the running images to :rollback tags (cheap — a tag
# is a ref, not a copy) and (b) take a pre-deploy DB backup, recording both in
# a state file.  Option 7 re-points the snapshot tags and (prompted) restores
# the backup.
# ------------------------------------------------------------------
ROLLBACK_STATE_FILE="$PROJECT_ROOT/.deploy-rollback-state"
ROLLBACK_SERVICES="backend worker report-worker frontend"

# Shared with restore-db.sh: `ask`, the staged start, the backend wait.
if [[ ! -f "$SCRIPT_DIR/stack-lib.sh" ]]; then
    print_error "scripts/stack-lib.sh is missing — this copy of the scripts folder is incomplete."
    exit 1
fi
# shellcheck source=stack-lib.sh
source "$SCRIPT_DIR/stack-lib.sh"

# ------------------------------------------------------------------
# The rollback state file: one `KEY|value` line per fact.
#   <service>|<image ref>            the image a :previous tag goes back to
#   PREDEPLOY_DB_DUMP|<path>         the dump option 7 restores
#   PREDEPLOY_ALEMBIC_REVISION|<rev> the schema revision that dump holds —
#                                    what the previous build expects
#   DEPLOY_IN_PROGRESS|<when>|<id>   a deploy started and has not finished
#                                    healthy; <id> is the backend image it built
# ------------------------------------------------------------------
state_get() {
    [[ -f "$ROLLBACK_STATE_FILE" ]] || return 0
    grep "^$1|" "$ROLLBACK_STATE_FILE" 2>/dev/null | tail -1 | cut -d'|' -f2- || true
}
state_del() {
    [[ -f "$ROLLBACK_STATE_FILE" ]] || return 0
    local tmp="${ROLLBACK_STATE_FILE}.edit"
    grep -v "^$1|" "$ROLLBACK_STATE_FILE" > "$tmp" 2>/dev/null || true
    mv "$tmp" "$ROLLBACK_STATE_FILE"
}
state_set() {
    state_del "$1"
    echo "$1|$2" >> "$ROLLBACK_STATE_FILE"
}

# The image compose builds or names for a service.  `config --images SERVICE`
# also lists the images of every service it depends on, in no fixed order
# (asking for the frontend's answered with the backend's one time in two), so
# the one NAMED for the service is picked: <project>-<service>, else a name
# ending in the service's, else — the database has no dependencies — the
# first line.
service_image_ref() {
    local service="$1" listed project ref=""
    listed="$($DC config --images "$service" 2>/dev/null || true)"
    [[ -n "$listed" ]] || return 0
    if [[ "$service" == "db" ]]; then
        printf '%s\n' "$listed" | head -1
        return 0
    fi
    project="$(compose_project_name)"
    ref="$(printf '%s\n' "$listed" | grep -xE "${project:-[^/]+}[-_]${service}(:[^/]+)?" | head -1 || true)"
    [[ -n "$ref" ]] || ref="$(printf '%s\n' "$listed" | grep -E "(^|[-_/])${service}(:[^/]+)?\$" | head -1 || true)"
    printf '%s\n' "$ref"
}

# A .env that option 4 wrote only so Compose could parse the file during a
# teardown.  It must never become a deployment's configuration.
TEARDOWN_ENV_MARKER="# BLUESTICK-TEARDOWN-TEMPORARY-ENV"
env_is_teardown_leftover() {
    [[ -f .env ]] || return 1
    grep -qxF "$TEARDOWN_ENV_MARKER" .env 2>/dev/null || grep -qx 'SECRET_KEY=teardown' .env 2>/dev/null
}

# ------------------------------------------------------------------
# Preflight: required external commands.
#
# Without this check, missing tools surface only on the destructive
# path (the IP-reconfigure flow `rm`s the cert pair before invoking
# the generator — if openssl is absent the host is left with a stale
# .env and no certs).  Failing fast at script entry is the right
# default.
# ------------------------------------------------------------------
preflight_required_tools() {
    local missing=()
    for tool in openssl sed grep awk; do
        command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
    done
    if [[ ${#missing[@]} -gt 0 ]]; then
        print_error "Missing required tool(s): ${missing[*]}"
        print_error "Install them via your package manager before re-running"
        print_error "(on Debian/Ubuntu: sudo apt-get install ${missing[*]})."
        exit 1
    fi
}
preflight_required_tools

# ------------------------------------------------------------------
# Detect host IP addresses
# ------------------------------------------------------------------
detect_ips() {
    local ips=()

    # Always include localhost
    ips+=("127.0.0.1")

    # Get non-loopback IPv4 addresses from network interfaces
    if command -v ip >/dev/null 2>&1; then
        while IFS= read -r addr; do
            [[ -n "$addr" ]] && ips+=("$addr")
        done < <(ip -4 addr show scope global 2>/dev/null | grep -oP 'inet \K[0-9.]+')
    elif command -v ifconfig >/dev/null 2>&1; then
        while IFS= read -r addr; do
            [[ -n "$addr" ]] && ips+=("$addr")
        done < <(ifconfig 2>/dev/null | grep -oP 'inet \K[0-9.]+' | grep -v '127.0.0.1')
    fi

    # Deduplicate
    printf '%s\n' "${ips[@]}" | sort -u -t. -k1,1n -k2,2n -k3,3n -k4,4n
}

# ------------------------------------------------------------------
# Prompt user to select or enter an IP address
# ------------------------------------------------------------------
select_ip() {
    local ips=()
    while IFS= read -r ip; do
        ips+=("$ip")
    done < <(detect_ips)

    echo ""
    print_header "Select the IP address for BlueStick"
    echo ""
    for i in "${!ips[@]}"; do
        local label=""
        if [[ "${ips[$i]}" == "127.0.0.1" ]]; then
            label=" (localhost)"
        fi
        echo "  $((i + 1))) ${ips[$i]}${label}"
    done
    echo "  $((${#ips[@]} + 1))) Enter a custom IP/hostname"
    echo ""
    local choice
    ask choice "Enter choice [1]: "

    # Default to first option
    if [[ -z "$choice" ]]; then
        choice=1
    fi

    if [[ "$choice" -eq $(( ${#ips[@]} + 1 )) ]] 2>/dev/null; then
        ask SELECTED_IP "Enter IP address or hostname: "
        if [[ -z "$SELECTED_IP" ]]; then
            print_error "No IP address provided"
            exit 1
        fi
    elif [[ "$choice" -ge 1 && "$choice" -le ${#ips[@]} ]] 2>/dev/null; then
        SELECTED_IP="${ips[$((choice - 1))]}"
    else
        print_error "Invalid choice"
        exit 1
    fi

    print_success "Using IP: $SELECTED_IP"
}

# ------------------------------------------------------------------
# Generate .env from .env.example with the selected IP
# ------------------------------------------------------------------
# A URL-safe random secret (it may end up inside DATABASE_URL).
random_secret() {
    if command -v python3 >/dev/null 2>&1; then
        python3 -c "import secrets; print(secrets.token_urlsafe(32))"
    else
        openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
    fi
}

# The Compose project name of THIS tree — what every container, volume,
# network and built image of this instance is labelled with
# (com.docker.compose.project).  Asked of compose itself, which applies its
# own rules (COMPOSE_PROJECT_NAME, a top-level `name:`, else the normalised
# directory name); the fallback reproduces them for the v1 binary, whose
# `config` prints no name.
compose_project_name() {
    local project
    project="$($DC config 2>/dev/null | awk '/^name:/ {print $2; exit}')"
    [[ -z "$project" ]] && project="${COMPOSE_PROJECT_NAME:-}"
    [[ -z "$project" ]] && project="$(grep -E '^COMPOSE_PROJECT_NAME=' .env 2>/dev/null | tail -1 | cut -d= -f2-)"
    [[ -z "$project" ]] && project="$(basename "$PROJECT_ROOT" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')"
    printf '%s\n' "$project"
}

# Whether this stack's Postgres volume already exists (then it was initialised
# with some password, and a new one in .env would lock the app out).
postgres_volume_exists() {
    local project
    project="$(compose_project_name)"
    docker volume ls -q 2>/dev/null | grep -qx "${project}_postgres_data"
}

# ------------------------------------------------------------------
# Pinned base images without a pull (review 2026-10-01 B5).
#
# docker-compose.yml and the Dockerfiles name exact releases
# (postgres:16.13, python:3.11.16-slim-trixie, node:22.23.2-alpine,
# nginx:1.31.3-alpine) where they used to name floating tags.  A host that
# pulled the floating tag earlier already HAS that release under the old
# name, and an isolated host cannot pull the new one.  When the pinned name
# is missing locally and the floating one is the very same release (checked
# by the version the image itself declares), give it the pinned name.
# Anything else is left alone: docker pulls it, or the operator sets
# POSTGRES_IMAGE / PYTHON_IMAGE / NODE_IMAGE / NGINX_IMAGE in .env.
# ------------------------------------------------------------------
image_declared_version() {
    # $1 image, $2 the env var the official image declares its version in
    docker image inspect "$1" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
        | awk -F= -v key="$2" '$1 == key {print $2; exit}'
}

adopt_local_base_image() {
    # $1 pinned ref, $2 floating ref it replaced, $3 version env var, $4 version prefix expected
    local pinned="$1" floating="$2" var="$3" want="$4" have
    docker image inspect "$pinned" >/dev/null 2>&1 && return 0
    docker image inspect "$floating" >/dev/null 2>&1 || return 0
    have="$(image_declared_version "$floating" "$var")"
    # Exact, or the release followed by a packaging suffix (PG_VERSION is
    # "16.13-1.pgdg13+1").  A bare prefix match took 16.1 for 16.13.
    if [[ -n "$have" && ( "$have" == "$want" || "$have" == "$want"-* ) ]]; then
        if docker tag "$floating" "$pinned" 2>/dev/null; then
            print_info "Base image: local $floating is $have — tagged as $pinned (no pull needed)."
        fi
    else
        print_warning "Base image $pinned is not on this host (local $floating is ${have:-unknown})."
        print_warning "Docker will pull it; on an isolated host, load it or set the override in .env"
        print_warning "(POSTGRES_IMAGE / PYTHON_IMAGE / DEBIAN_IMAGE / NODE_IMAGE / NGINX_IMAGE)."
    fi
}

ensure_pinned_base_images() {
    local env_override
    # The database image: whatever compose resolves (the pin, or POSTGRES_IMAGE).
    local db_image
    db_image="$($DC config --images db 2>/dev/null | head -1)"
    if [[ "$db_image" =~ ^postgres:([0-9]+)\.([0-9]+)$ ]]; then
        adopt_local_base_image "$db_image" "postgres:${BASH_REMATCH[1]}" PG_VERSION \
            "${BASH_REMATCH[1]}.${BASH_REMATCH[2]}"
    fi
    # Build bases: the Dockerfile's default unless .env overrides it.
    local spec arg dockerfile floating var pinned version
    for spec in \
        "PYTHON_IMAGE|backend/Dockerfile|python:3.11-slim|PYTHON_VERSION" \
        "NODE_IMAGE|frontend/Dockerfile|node:22-alpine|NODE_VERSION" \
        "NGINX_IMAGE|frontend/Dockerfile|nginx:alpine|NGINX_VERSION"; do
        IFS='|' read -r arg dockerfile floating var <<< "$spec"
        env_override="$(grep -E "^${arg}=" .env 2>/dev/null | tail -1 | cut -d= -f2-)"
        [[ -n "${!arg:-}" || -n "$env_override" ]] && continue
        pinned="$(sed -n "s/^ARG ${arg}=//p" "$dockerfile" 2>/dev/null | head -1)"
        [[ -z "$pinned" ]] && continue
        # python:3.11.16-slim-trixie -> 3.11.16 ; nginx:1.31.3-alpine -> 1.31.3
        version="${pinned#*:}"; version="${version%%-*}"
        adopt_local_base_image "$pinned" "$floating" "$var" "$version"
    done
}

# The base image a build stage will use: the environment, else .env, else the
# Dockerfile's own default.
resolved_base_image() {
    # $1 build arg, $2 Dockerfile
    local value
    value="${!1:-}"
    [[ -z "$value" ]] && value="$(grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2-)"
    [[ -z "$value" ]] && value="$(sed -n "s/^ARG $1=//p" "$2" 2>/dev/null | head -1)"
    printf '%s\n' "$value"
}

# Say, BEFORE the build, which pinned base images this host does not hold
# (branch review B2, the cheap half).  The build pulls them — and fails on a
# host with no route to the registry, a long way into its output.  A failed
# build restarts nothing, so this is a warning, not a stop.
warn_missing_base_images() {
    local spec arg dockerfile image
    local -a missing=()
    for spec in \
        "PYTHON_IMAGE|backend/Dockerfile" \
        "DEBIAN_IMAGE|backend/Dockerfile" \
        "NODE_IMAGE|frontend/Dockerfile" \
        "NGINX_IMAGE|frontend/Dockerfile"; do
        IFS='|' read -r arg dockerfile <<< "$spec"
        image="$(resolved_base_image "$arg" "$dockerfile")"
        [[ -z "$image" ]] && continue
        docker image inspect "$image" >/dev/null 2>&1 || missing+=("$arg=$image")
    done
    [[ "${#missing[@]}" -eq 0 ]] && return 0

    echo ""
    print_warning "Base image(s) NOT in this host's image store — the build will pull them:"
    local entry
    for entry in "${missing[@]}"; do
        print_warning "    ${entry#*=}   (override: ${entry%%=*} in .env)"
    done
    # One question to the registry about the first missing image: "can this
    # host pull at all?", before the build spends minutes finding out.  A
    # manifest lookup of a multi-platform image takes 10–15 s on a good
    # connection, hence the generous limit (BASE_IMAGE_PROBE_TIMEOUT).
    local first="${missing[0]#*=}" reach_rc=0 limit="${BASE_IMAGE_PROBE_TIMEOUT:-45}"
    print_info "Asking the registry for $first (up to ${limit}s)..."
    if command -v timeout >/dev/null 2>&1; then
        timeout "$limit" docker manifest inspect "$first" >/dev/null 2>&1 || reach_rc=$?
    else
        docker manifest inspect "$first" >/dev/null 2>&1 || reach_rc=$?
    fi
    if [[ "$reach_rc" -ne 0 ]]; then
        print_warning "The registry did not answer for $first within ${limit}s: this host may be OFFLINE"
        print_warning "(or the tag does not exist). If it is, the build fails at its first FROM line."
        print_warning "Nothing is restarted when a build fails — the running containers keep the current"
        print_warning "build. To deploy on a host that cannot pull: load the image (docker load) or point"
        print_warning "the override at a tag this host holds."
    else
        print_info "The registry answered for $first, so the pull should succeed."
    fi
    print_info "The build also downloads packages (PyPI, npm, Debian/Alpine, and Quarto from GitHub)"
    print_info "for every layer that is not already in Docker's build cache."
    echo ""
}

# The database is not built — `up` pulls its image when the host lacks it, and
# a host that cannot pull finds out after the build, with `up` failing before
# it recreates anything (branch review S2).  Checked, and pulled if need be,
# BEFORE the build.  Returns 1 when the image cannot be had.
ensure_db_image_local() {
    local db_image
    db_image="$(service_image_ref db)"
    if [[ -z "$db_image" ]]; then
        return 0   # compose v1 prints no image list; `up` decides
    fi
    if docker image inspect "$db_image" >/dev/null 2>&1; then
        return 0
    fi
    print_warning "The database image $db_image is not on this host — pulling it now..."
    if $DC pull db; then
        return 0
    fi
    print_error "The database image $db_image is not on this host and could not be pulled."
    print_error "Nothing was built or restarted. Load it (docker load -i postgres.tar), or set"
    print_error "POSTGRES_IMAGE in .env to a PostgreSQL 16 image this host holds, then deploy again."
    return 1
}

# Point an EXISTING .env at a new address: only HOST_IP, REACT_APP_API_URL
# and CORS_ORIGINS change.  Reconfigure used to re-render .env from
# .env.example with a NEW SECRET_KEY — which signed everyone out and, with
# credential encryption keyed off SECRET_KEY by default, made every stored
# TOTP secret and integration/LLM credential undecryptable; it also dropped
# every setting the operator had added (review 2026-09-23).
update_env_address() {
    local ip="$1" cors="$2" tmp
    tmp="$(mktemp ".env.tmp.XXXXXX")"
    if ! sed \
        -e "s|^HOST_IP=.*|HOST_IP=${ip}|" \
        -e "s|^REACT_APP_API_URL=.*|REACT_APP_API_URL=https://${ip}|" \
        -e "s|^CORS_ORIGINS=.*|CORS_ORIGINS=${cors}|" \
        .env > "$tmp" || [[ ! -s "$tmp" ]]; then
        rm -f "$tmp"
        print_error "Failed to update .env"
        return 1
    fi
    # Keys the file did not have yet.
    grep -q '^HOST_IP=' "$tmp" || echo "HOST_IP=${ip}" >> "$tmp"
    grep -q '^REACT_APP_API_URL=' "$tmp" || echo "REACT_APP_API_URL=https://${ip}" >> "$tmp"
    grep -q '^CORS_ORIGINS=' "$tmp" || echo "CORS_ORIGINS=${cors}" >> "$tmp"
    chmod --reference=.env "$tmp" 2>/dev/null || chmod 600 "$tmp"
    mv "$tmp" .env
    print_success "Updated .env for $ip (secrets and other settings kept)"
}

generate_env() {
    local ip="$1"

    if [[ ! -f ".env.example" ]]; then
        print_error ".env.example not found"
        exit 1
    fi

    # Build CORS origins — always include both localhost and 127.0.0.1
    # so the app works regardless of which URL the user types
    local cors="https://${ip},https://${ip}:3000"
    if [[ "$ip" == "127.0.0.1" ]]; then
        cors="https://127.0.0.1,https://127.0.0.1:3000,https://localhost,https://localhost:3000"
    elif [[ "$ip" == "localhost" ]]; then
        cors="https://localhost,https://localhost:3000,https://127.0.0.1,https://127.0.0.1:3000"
    else
        # For network IPs, also allow localhost for local debugging
        cors="https://${ip},https://${ip}:3000,https://localhost,https://localhost:3000,https://127.0.0.1,https://127.0.0.1:3000"
    fi

    # An existing .env keeps its secrets and settings; only the address moves.
    if [[ -f ".env" ]]; then
        update_env_address "$ip" "$cors"
        return $?
    fi

    # A NEW deployment gets its own secrets: the JWT secret, a credential-
    # encryption key of its own (so rotating SECRET_KEY never destroys stored
    # TOTP secrets and credentials), and a database password instead of the
    # compose default — unless a database volume already exists, which was
    # initialised with some other password.
    local secret_key cred_key pg_password=""
    secret_key="$(random_secret)"
    cred_key="$(random_secret)"
    if postgres_volume_exists; then
        print_warning "A database volume already exists — keeping its password (the compose default"
        print_warning "unless you set POSTGRES_PASSWORD before). Set it in .env if it was changed."
    else
        pg_password="$(random_secret)"
    fi

    # Write to a temp file first so a sed/IO failure can't leave a
    # half-written .env on disk.  Atomic rename only after sed exits 0.
    local tmp
    tmp="$(mktemp ".env.tmp.XXXXXX")"
    if ! sed \
        -e "s|^HOST_IP=.*|HOST_IP=${ip}|" \
        -e "s|^REACT_APP_API_URL=.*|REACT_APP_API_URL=https://${ip}|" \
        -e "s|^CORS_ORIGINS=.*|CORS_ORIGINS=${cors}|" \
        -e "s|^SECRET_KEY=.*|SECRET_KEY=${secret_key}|" \
        -e "s|^# CREDENTIAL_ENCRYPTION_KEY=.*|CREDENTIAL_ENCRYPTION_KEY=${cred_key}|" \
        ${pg_password:+-e "s|^# POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${pg_password}|"} \
        .env.example > "$tmp"
    then
        rm -f "$tmp"
        print_error "Failed to render .env from .env.example"
        return 1
    fi
    if [[ ! -s "$tmp" ]]; then
        rm -f "$tmp"
        print_error ".env render produced empty output — refusing to overwrite"
        return 1
    fi
    chmod 600 "$tmp"   # it holds the secrets
    mv "$tmp" .env

    print_success "Generated .env for $ip"
}

# ------------------------------------------------------------------
# Ensure .env exists, create if missing
# ------------------------------------------------------------------
ensure_env() {
    if [[ -f ".env" ]]; then
        return 0
    fi

    print_warning ".env not found"
    echo ""
    local answer
    ask answer "Would you like to generate one now? (Y/n): "
    if [[ "$answer" =~ ^[Nn] ]]; then
        print_error "Cannot proceed without .env"
        exit 1
    fi

    select_ip
    generate_env "$SELECTED_IP"
}

# ------------------------------------------------------------------
# Ensure the uploads directory tree exists with correct permissions
# so the bind-mounted volume is writable by the container user.
# ------------------------------------------------------------------
ensure_uploads_dir() {
    # The container runs as appuser (UID/GID 999) and bind-mounts ./uploads, so
    # the worker-written dirs must be writable by UID 999: ingestion_queue (the
    # ingestion worker) and report_artifacts (the async report worker).
    #
    # CR4-5c preferred least privilege (dir owned by 999, mode 0750), but the
    # only way to chown is as root — and a deploy script must NEVER block on a
    # sudo password prompt mid-run.  So: do the clean thing automatically when
    # we already have root, leave a correctly-owned dir untouched, and
    # otherwise fall back to a writable mode the unprivileged deploy user can
    # set itself — no sudo, no prompt.
    for d in uploads/ingestion_queue uploads/report_artifacts; do
        mkdir -p "$d"
        if [ "$(id -u)" -eq 0 ]; then
            # Already root (e.g. sudo ./deploy.sh): least privilege, no prompt.
            chown -R 999:999 "$d"
            chmod 750 "$d"
            continue
        fi
        # Already owned by the container user from a previous privileged setup?
        # Leave it — repeat deploys stay silent and least-privilege preserved.
        if [ "$(stat -c '%u' "$d" 2>/dev/null || echo -1)" = "999" ]; then
            chmod 750 "$d" 2>/dev/null || true
            continue
        fi
        # Unprivileged deploy: make the dir writable for the container without
        # root.  Sticky bit so co-tenants can't delete each other's files.  For
        # least privilege on a shared host, run ONCE manually:
        #   sudo chown -R 999:999 "$d" && sudo chmod 750 "$d"
        chmod 1777 "$d"
    done
}

# ------------------------------------------------------------------
# Read HOST_IP from .env
# ------------------------------------------------------------------
get_configured_ip() {
    grep "^HOST_IP=" .env 2>/dev/null | cut -d'=' -f2
}

# ------------------------------------------------------------------
# SSL certificate check / generation
# ------------------------------------------------------------------
# A certificate whose issuer is not itself was issued by the operator's CA
# (ca/local-ca.sh): it is theirs, never ours to regenerate.
cert_is_ca_issued() {
    local crt="ssl/certs/networkmapper.crt" issuer subject
    [[ -f "$crt" ]] && command -v openssl >/dev/null || return 1
    issuer="$(openssl x509 -in "$crt" -noout -issuer 2>/dev/null | sed 's/^issuer=//')"
    subject="$(openssl x509 -in "$crt" -noout -subject 2>/dev/null | sed 's/^subject=//')"
    [[ -n "$issuer" && "$issuer" != "$subject" ]]
}

cert_names_ip() {
    openssl x509 -in ssl/certs/networkmapper.crt -noout -ext subjectAltName 2>/dev/null \
        | tail -n +2 | tr -d ' ' | tr ',' '\n' | grep -qxF "IPAddress:$1"
}

ensure_ssl_certs() {
    if [[ -f "ssl/certs/networkmapper.crt" && -f "ssl/certs/networkmapper.key" ]]; then
        print_success "SSL certificates found"
        return 0
    fi

    print_warning "SSL certificates not found - generating self-signed certs..."
    local ip="${1:-localhost}"

    if [[ -x "scripts/generate-ssl-cert-simple.sh" ]]; then
        rm -f ssl/certs/networkmapper.key ssl/certs/networkmapper.crt ssl/certs/openssl.conf
        ./scripts/generate-ssl-cert-simple.sh "$ip"
        print_success "SSL certificates generated for $ip"
    else
        print_error "SSL certificate generation script not found!"
        print_info "Generate certs manually and place them at:"
        print_info "  ssl/certs/networkmapper.crt"
        print_info "  ssl/certs/networkmapper.key"
        return 1
    fi
}

# ------------------------------------------------------------------
# SSL status check
# ------------------------------------------------------------------
check_ssl_status() {
    print_header "Security Status"

    PG_SSL=$($DC exec -T db psql -U nmapuser -d networkMapper -tAc "SHOW ssl;" 2>/dev/null || echo "unknown")
    if [[ "$PG_SSL" == "on" ]]; then
        print_success "PostgreSQL SSL: ENABLED"
    elif [[ "$PG_SSL" == "off" ]]; then
        print_warning "PostgreSQL SSL: DISABLED"
    else
        print_info "PostgreSQL SSL: UNKNOWN (database may not be ready)"
    fi

    DB_SSL_MODE=$($DC exec -T backend printenv DATABASE_SSL_MODE 2>/dev/null || echo "unknown")
    echo "  DATABASE_SSL_MODE: $DB_SSL_MODE"

    DB_PORT_PUBLISHED=$($DC ps db --format json 2>/dev/null | grep -o '"PublishedPort":[0-9]*' | head -1 | cut -d':' -f2)
    if [[ -z "$DB_PORT_PUBLISHED" ]] || [[ "$DB_PORT_PUBLISHED" == "0" ]]; then
        print_success "Database port: NOT exposed (Docker network only)"
    else
        print_warning "Database port: EXPOSED on port $DB_PORT_PUBLISHED"
    fi
    echo ""
}

# ------------------------------------------------------------------
# Back up the environment-specific config (.env + ssl/) to the parent
# folder.  Neither is in version control — .env holds secrets (SECRET_KEY,
# DB creds) and ssl/ holds the TLS cert + private key — so a re-copy /
# overwrite deploy (the air-gapped, no-GitHub workflow) would otherwise
# lose them.  Stashing a copy OUTSIDE the project dir lets it survive
# the directory being overwritten.
# ------------------------------------------------------------------
backup_config() {
    print_header "Back up .env + SSL to the parent folder"

    local parent stamp dest found
    parent="$(dirname "$PROJECT_ROOT")"
    stamp="$(date +%Y%m%d-%H%M%S)"
    dest="$parent/$(basename "$PROJECT_ROOT")-config-backup-$stamp"
    found=0

    if ! mkdir -p "$dest"; then
        print_error "Could not create $dest — nothing was backed up."
        return 1
    fi

    # "Backed up" is printed only after the copy succeeded, and a failed copy
    # is this function's failure: option 4 deletes .env next, and it used to
    # read the success line of a copy that had not happened (branch review S6).
    if [[ -f ".env" ]]; then
        if ! cp -p .env "$dest/.env"; then
            print_error "Copying .env to $dest FAILED — it is NOT backed up."
            return 1
        fi
        print_success "Backed up .env"
        found=1
    else
        print_warning ".env not found — skipping"
    fi

    if [[ -d "ssl" ]]; then
        if ! cp -a ssl "$dest/ssl"; then
            print_error "Copying ssl/ to $dest FAILED — the TLS certificate and key are NOT backed up."
            return 1
        fi
        print_success "Backed up ssl/ (TLS cert + key)"
        found=1
    else
        print_warning "ssl/ not found — skipping"
    fi

    if [[ "$found" -eq 0 ]]; then
        rmdir "$dest" 2>/dev/null || true
        print_error "Nothing to back up — no .env or ssl/ in $PROJECT_ROOT"
        exit 1
    fi

    # The backup contains secrets (SECRET_KEY, DB password, TLS private
    # key) — lock it down to the current user only.
    chmod -R go-rwx "$dest" 2>/dev/null || true

    echo ""
    print_success "Config backed up to:"
    echo "    $dest"
    echo ""
    print_info "To restore into a freshly-copied project directory, run from"
    print_info "the project root:"
    if [[ -f "$dest/.env" ]]; then echo "    cp -p \"$dest/.env\" ./.env"; fi
    if [[ -d "$dest/ssl" ]]; then echo "    cp -a \"$dest/ssl/.\" ./ssl/"; fi
    # Explicit: the function used to return the status of the last `[[ -d ]]`
    # test, i.e. failure whenever ssl/ was absent.
    return 0
}

# ------------------------------------------------------------------
# Rollback helpers (B2-1)
# ------------------------------------------------------------------

# Snapshot the currently-running built images to :rollback tags and record the
# original image ref per service, so option 7 can restore the prior build.
# Best-effort: a first-ever deploy (no prior images) records nothing.
# Production filled its disk on 2026-09-24: Postgres PANICked ("No space left
# on device") and crash-looped until space was freed.  Every option-1 deploy
# busts the build cache (CACHE_BUST) and leaves the previous images untagged;
# nothing removed either, so each deploy grew Docker's disk use.
MIN_FREE_GB=${MIN_FREE_GB:-15}

check_free_disk() {
    local avail_kb docker_root
    avail_kb=$(df -Pk . 2>/dev/null | awk 'NR==2 {print $4}')
    docker_root=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)
    if [[ -n "$docker_root" && -d "$docker_root" ]]; then
        local docker_kb
        docker_kb=$(df -Pk "$docker_root" 2>/dev/null | awk 'NR==2 {print $4}')
        if [[ -n "$docker_kb" && ( -z "$avail_kb" || "$docker_kb" -lt "$avail_kb" ) ]]; then
            avail_kb=$docker_kb
        fi
    fi
    if [[ -z "$avail_kb" ]]; then
        return 0
    fi
    if (( avail_kb < MIN_FREE_GB * 1024 * 1024 )); then
        print_warning "Only $(( avail_kb / 1024 / 1024 )) GB free (below ${MIN_FREE_GB} GB). A full disk stops Postgres."
        print_warning "Reclaim space first:  docker builder prune -f   and   docker image prune -f"
        print_warning "(Neither touches running containers, volumes or the rollback images.)"
        # At end of input (a piped run: upgrade-instance.sh) the answer is the
        # safe one, and it is said — the deploy used to end here in silence.
        local answer
        ask answer "Continue anyway? [y/N]: " "n"
        [[ "$answer" =~ ^[Yy]$ ]] || { print_error "Deploy cancelled — free some disk space and re-run (MIN_FREE_GB=0 skips this check)."; exit 1; }
    fi
}

# After a healthy deploy: remove what this deploy made obsolete.
#
#   * Untagged (dangling) images OF THIS COMPOSE PROJECT only — selected by
#     the com.docker.compose.project label every image compose builds carries.
#     It was a host-wide `docker image prune -f`, which also removed another
#     project's untagged images (branch review).  The :previous rollback tags
#     are tags, so their images are not dangling and are kept.
#   * Build cache older than a week.  The cache has no project label — it is
#     host-wide by nature — but it holds nothing that cannot be rebuilt, and
#     every deploy adds to it (CACHE_BUST): this is what filled the production
#     disk on 2026-09-24.  DEPLOY_PRUNE_BUILD_CACHE=0 leaves it alone on a
#     host that shares Docker with other builds.
prune_after_deploy() {
    local project
    project="$(compose_project_name)"
    if [[ -n "$project" ]]; then
        print_info "Reclaiming disk: this project's untagged images..."
        docker image prune -f --filter "label=com.docker.compose.project=${project}" >/dev/null 2>&1 || true
    fi
    if [[ "${DEPLOY_PRUNE_BUILD_CACHE:-1}" != "0" ]]; then
        print_info "Reclaiming disk: Docker build cache older than 7 days (DEPLOY_PRUNE_BUILD_CACHE=0 skips this)..."
        docker builder prune -f --filter until=168h >/dev/null 2>&1 || true
    fi
    local avail_kb
    avail_kb=$(df -Pk . 2>/dev/null | awk 'NR==2 {print $4}')
    if [[ -n "$avail_kb" ]]; then
        print_info "Free disk now: $(( avail_kb / 1024 / 1024 )) GB"
    fi
}

snapshot_images_for_rollback() {
    local tmp="${ROLLBACK_STATE_FILE}.tmp"
    : > "$tmp"
    local svc id ref count=0
    for svc in $ROLLBACK_SERVICES; do
        id="$($DC images -q "$svc" 2>/dev/null | head -1)"
        if [[ -z "$id" ]]; then
            continue
        fi
        # The compose image ref for this service — the non-rollback RepoTag.
        ref="$(docker image inspect "$id" \
            --format '{{range .RepoTags}}{{println .}}{{end}}' 2>/dev/null \
            | grep -v '^bluestick-rollback-' | grep ':' | head -1)"
        if [[ -z "$ref" ]]; then
            continue
        fi
        if docker tag "$id" "bluestick-rollback-${svc}:previous" 2>/dev/null; then
            echo "${svc}|${ref}" >> "$tmp"
            count=$((count + 1))
        fi
    done
    if [[ "$count" -gt 0 ]]; then
        mv "$tmp" "$ROLLBACK_STATE_FILE"
        print_info "Snapshotted $count image(s) for rollback (option 7 reverts them)."
    else
        rm -f "$tmp"
    fi
}

# Record the dump a rollback restores, with the schema revision it holds
# (backup-db.sh writes alembic_revision to the dump's .meta).  The revision is
# how option 7 tells "the failed deploy migrated the schema" from "it did not".
record_predeploy_dump() {
    local dump="$1" rev=""
    state_set PREDEPLOY_DB_DUMP "$dump"
    if [[ -f "${dump}.meta" ]]; then
        rev="$(grep -E '^alembic_revision=' "${dump}.meta" 2>/dev/null | tail -1 | cut -d= -f2-)"
    fi
    state_set PREDEPLOY_ALEMBIC_REVISION "${rev:-unknown}"
}

# Take a pre-deploy DB backup and record its path in the state file so a
# rollback can offer to restore the exact schema the old image expects.
predeploy_db_backup() {
    if [[ ! -x "scripts/backup-db.sh" ]]; then
        print_warning "scripts/backup-db.sh not found — skipping pre-deploy DB backup."
        return 0
    fi
    print_info "Taking a pre-deploy database backup..."
    # Review B2 — this used to warn and continue on a failed backup. Rolling
    # back re-points the image tags, but an older image may not run against a
    # schema a newer migration already changed, so the DB dump IS the rollback
    # path for anything schema-touching. Continuing without one means the
    # operator discovers recovery is impossible only after a bad migration,
    # which is the worst possible moment to find out. Fail closed instead.
    # Only a dump written by THIS run counts.  Taking the newest file in the
    # folder recorded an earlier deploy's dump whenever this run produced a
    # volume snapshot instead (db container down), and a rollback would then
    # have restored days-old data without saying so.
    local backup_dir dump marker
    backup_dir="${BACKUP_DIR:-$(dirname "$PROJECT_ROOT")/$(basename "$PROJECT_ROOT")-db-backups}"
    marker="$(mktemp)"
    if ./scripts/backup-db.sh; then
        dump="$(find "$backup_dir" -maxdepth 1 -name 'nm-pgdump-*.dump' -newer "$marker" -size +0 2>/dev/null \
            | xargs -r ls -t 2>/dev/null | head -1)"
        rm -f "$marker"
        if [[ -z "$dump" ]]; then
            print_error "backup-db.sh did not write a new database dump in $backup_dir"
            print_error "(it takes a volume snapshot instead when the db container is not running,"
            print_error " and a rollback can only restore a dump). Start the stack, then deploy again."
            print_error "Refusing to deploy without a restorable backup."
            print_info  "Override for a disposable environment: DEPLOY_WITHOUT_BACKUP=1 ./scripts/deploy.sh"
            [[ "${DEPLOY_WITHOUT_BACKUP:-0}" == "1" ]] || return 1
            print_warning "DEPLOY_WITHOUT_BACKUP=1 — continuing with NO restorable database backup."
            record_predeploy_dump "NONE (forced)"
            return 0
        fi
        record_predeploy_dump "$dump"
        print_info "Pre-deploy DB backup: $dump (schema revision $(state_get PREDEPLOY_ALEMBIC_REVISION))"
        return 0
    fi
    rm -f "$marker"

    print_error "Pre-deploy backup FAILED (see backup-db.sh's own message above)."
    print_error "Rollback would not be able to restore this instance, so this deploy is aborting."
    print_info  "If the database dump was written and only the uploads archive failed, and a"
    print_info  "database-only backup is acceptable for this deploy:"
    print_info  "    BACKUP_ALLOW_MISSING_UPLOADS=1 ./scripts/deploy.sh"
    print_info  "Fix the backup (check disk space and that the db container is up), or, for a"
    print_info  "disposable environment where losing the data is acceptable, re-run with:"
    print_info  "    DEPLOY_WITHOUT_BACKUP=1 ./scripts/deploy.sh"
    if [[ "${DEPLOY_WITHOUT_BACKUP:-0}" == "1" ]]; then
        print_warning "DEPLOY_WITHOUT_BACKUP=1 — proceeding with NO restorable database backup."
        # Recorded so a later rollback tells the operator the DB cannot be
        # restored, instead of silently offering a restore that can't happen.
        record_predeploy_dump "NONE (forced)"
        return 0
    fi
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
#   * crashed — the container exited, or Docker keeps restarting it.  That
#     is when option 7 is the answer.
#
# The old wait was 90 s with one message for both ("the boot migration may
# have failed … roll back"), and `up --build -d` under `set -e` could end the
# script before even that.
#
# wait_for_backend_healthy (scripts/stack-lib.sh, shared with restore-db.sh)
# returns 0 healthy, 2 crashed / crash-looping, 1 still starting at timeout.
# DEPLOY_HEALTH_TIMEOUT (seconds, default 1800) bounds the wait.
# ------------------------------------------------------------------
DEPLOY_HEALTH_TIMEOUT="${DEPLOY_HEALTH_TIMEOUT:-1800}"

# Is the backend CONTAINER running the image that was just built?
# (branch review S2).  `up` can fail before it recreates anything — the
# database image could not be pulled, a port is taken — and the container
# that then answers /health is the OLD build: the script used to print
# "Backend is healthy (boot + migrations succeeded)" for it.
# Returns 0 yes, 1 no (a different image), 2 cannot tell.
backend_runs_built_image() {
    local ref built cid running
    ref="$(service_image_ref backend)"
    [[ -n "$ref" ]] || return 2
    built="$(docker image inspect "$ref" --format '{{.Id}}' 2>/dev/null || true)"
    cid="$($DC ps -aq backend 2>/dev/null | head -1)"
    [[ -n "$built" && -n "$cid" ]] || return 2
    running="$(docker inspect "$cid" --format '{{.Image}}' 2>/dev/null || true)"
    [[ -n "$running" ]] || return 2
    [[ "$built" == "$running" ]]
}

built_backend_image_id() {
    local ref
    ref="$(service_image_ref backend)"
    [[ -n "$ref" ]] || return 0
    docker image inspect "$ref" --format '{{.Id}}' 2>/dev/null || true
}

# After a healthy start: one-off data corrections this instance has no
# recorded run of (the data-repair ledger, review 2026-10-01 B10).  A
# reminder with the exact commands — nothing is run here: one of them deletes
# rows, and the other must not run beside a live import.
print_pending_data_repairs() {
    local pending
    # A failed read is "could not check", never "nothing pending": the error
    # used to be swallowed and the reminder silently skipped.
    if ! pending="$($DC exec -T backend python scripts/data_repairs.py --pending 2>/dev/null)"; then
        echo ""
        print_warning "Could not check the data-repair ledger (the read failed). See what this"
        print_warning "instance still owes with:  ./scripts/status.sh"
        return 0
    fi
    if [[ -n "$pending" ]]; then
        echo ""
        print_warning "Data repairs not yet applied on this instance:"
        echo "$pending" | sed 's/^/  /'
        print_info "They correct rows written by older versions. Run them when no import is in"
        print_info "progress; ./scripts/status.sh repeats this list until each has been run."
    fi
}

# ------------------------------------------------------------------
# A deploy that did not finish (branch review S5).
#
# Option 1 snapshots the running images and takes a database dump BEFORE it
# builds.  Run again after a FAILED deploy, it used to do both again — and
# the snapshot then held the failed build, the dump the half-migrated
# database: the good rollback point was overwritten by the thing to roll
# back from.  DEPLOY_IN_PROGRESS is written before the build and removed only
# when the deploy ends healthy (or a rollback does).
# ------------------------------------------------------------------
deploy_marker_started() { local v; v="$(state_get DEPLOY_IN_PROGRESS)"; printf '%s\n' "${v%%|*}"; }
deploy_marker_image()   { local v; v="$(state_get DEPLOY_IN_PROGRESS)"; [[ "$v" == *"|"* ]] && printf '%s\n' "${v#*|}" || true; }

# The marked deploy DID finish — by hand (`up -d` after the wait timed out):
# the backend answers and runs the image that deploy built.
marked_deploy_completed() {
    local marked cid running
    marked="$(deploy_marker_image)"
    [[ -n "$marked" ]] || return 1
    cid="$($DC ps -q backend 2>/dev/null | head -1)"
    [[ -n "$cid" ]] || return 1
    running="$(docker inspect "$cid" --format '{{.Image}}' 2>/dev/null || true)"
    [[ "$running" == "$marked" ]] || return 1
    backend_probe
}

# Decide what option 1 does about the rollback point.  Returns 1 to abort.
prepare_rollback_point() {
    if [[ -n "$(state_get DEPLOY_IN_PROGRESS)" ]]; then
        if [[ "${DEPLOY_NEW_SNAPSHOT:-0}" == "1" ]]; then
            print_warning "DEPLOY_NEW_SNAPSHOT=1 — discarding the unfinished deploy's rollback point and"
            print_warning "taking a new one from what is running now."
            state_del DEPLOY_IN_PROGRESS
        elif marked_deploy_completed; then
            print_info "The previous deploy was finished by hand and is healthy — taking a new rollback point."
            state_del DEPLOY_IN_PROGRESS
        fi
    fi

    if [[ -z "$(state_get DEPLOY_IN_PROGRESS)" ]]; then
        snapshot_images_for_rollback
        predeploy_db_backup || return 1
        return 0
    fi

    # ---- an unfinished deploy: keep its rollback point --------------------
    local dump recorded_rev current_rev
    dump="$(state_get PREDEPLOY_DB_DUMP)"
    recorded_rev="$(state_get PREDEPLOY_ALEMBIC_REVISION)"
    echo ""
    print_warning "The previous deploy (started $(deploy_marker_started)) did not finish healthy."
    print_warning "KEEPING its rollback images (bluestick-rollback-*:previous): they are the last build"
    print_warning "that worked. A new snapshot now would capture the failed build instead."

    current_rev="$(database_alembic_revision || true)"
    if [[ -n "$dump" && -f "$dump" && -n "$current_rev" && -n "$recorded_rev" \
          && "$recorded_rev" != "unknown" && "$current_rev" == "$recorded_rev" ]]; then
        # The failed deploy never migrated: the database is still the previous
        # build's, and may hold work done since.  A fresh dump of it is a
        # better rollback point than the older one, for the same build.
        print_info "The database is still at the pre-deploy schema ($current_rev): the failed deploy did"
        print_info "not migrate it. Taking a fresh pre-deploy backup (the previous one is kept too)."
        predeploy_db_backup || return 1
        return 0
    fi

    if [[ -n "$dump" && -f "$dump" ]]; then
        print_warning "KEEPING the recorded pre-deploy database backup — no new one is taken, because the"
        print_warning "database may already be migrated (schema now: ${current_rev:-unreadable}; at the backup: ${recorded_rev:-unknown}):"
        echo "    $dump"
        print_info "It is protected from backup retention while it is recorded here."
        print_info "(If that deploy was in fact completed and you want a new rollback point:"
        print_info " DEPLOY_NEW_SNAPSHOT=1 ./scripts/deploy.sh)"
        return 0
    fi

    print_warning "No restorable pre-deploy database backup is recorded for that deploy"
    print_warning "(recorded: ${dump:-nothing}). Taking a safety backup of the database AS IT IS NOW; it is"
    print_warning "not recorded as the rollback dump, because it may hold a migrated schema."
    if [[ -x "scripts/backup-db.sh" ]] && ./scripts/backup-db.sh; then
        return 0
    fi
    print_error "The safety backup failed."
    if [[ "${DEPLOY_WITHOUT_BACKUP:-0}" == "1" ]]; then
        print_warning "DEPLOY_WITHOUT_BACKUP=1 — continuing with NO backup."
        return 0
    fi
    print_info "Override for a disposable environment: DEPLOY_WITHOUT_BACKUP=1 ./scripts/deploy.sh"
    return 1
}

# Option 7.  In this order: re-point the :previous image tags; stop the app;
# compare the database's schema revision with the one recorded at the
# pre-deploy backup and, when it moved, restore that backup (restore-db.sh);
# only then start the previous build — backend first, WITHOUT rebuilding —
# and say what state the instance ended in.
rollback_to_previous() {
    print_header "Roll Back to Previous Build"
    if [[ ! -s "$ROLLBACK_STATE_FILE" ]]; then
        print_error "No rollback snapshot found ($ROLLBACK_STATE_FILE)."
        print_info "A snapshot is created automatically the next time you run option 1."
        return 1
    fi

    # ---- 1. Point the image names back at the previous build --------------
    local predeploy_dump recorded_rev reverted=0 svc ref
    predeploy_dump="$(state_get PREDEPLOY_DB_DUMP)"
    recorded_rev="$(state_get PREDEPLOY_ALEMBIC_REVISION)"
    if [[ ( -z "$recorded_rev" || "$recorded_rev" == "unknown" ) && -f "${predeploy_dump}.meta" ]]; then
        # A state file written before the revision was recorded: the dump's
        # own .meta has it.
        recorded_rev="$(grep -E '^alembic_revision=' "${predeploy_dump}.meta" 2>/dev/null | tail -1 | cut -d= -f2-)"
    fi
    [[ "$recorded_rev" == "unknown" ]] && recorded_rev=""

    while IFS='|' read -r svc ref; do
        case "$svc" in
            ""|PREDEPLOY_DB_DUMP|PREDEPLOY_ALEMBIC_REVISION|DEPLOY_IN_PROGRESS) continue ;;
        esac
        if docker image inspect "bluestick-rollback-${svc}:previous" >/dev/null 2>&1 && [[ -n "$ref" ]]; then
            if docker tag "bluestick-rollback-${svc}:previous" "$ref" 2>/dev/null; then
                print_info "Reverted ${svc} → ${ref}"
                reverted=$((reverted + 1))
            fi
        else
            print_warning "No snapshot image for ${svc}; leaving it as-is."
        fi
    done < "$ROLLBACK_STATE_FILE"

    if [[ "$reverted" -eq 0 ]]; then
        print_error "No snapshot images could be reverted. Nothing was stopped or changed."
        return 1
    fi

    # ---- 2. Stop the app; the database stays up ---------------------------
    # The old order started the previous images FIRST and offered the database
    # restore afterwards.  After a deploy that migrated the schema the previous
    # backend cannot find the database's revision and crash-loops; `up -d`
    # then failed (ending the script under `set -e`) or waited on the
    # frontend's health dependency — and the restore was never offered
    # (branch review B1).  Nothing is started here until the schema question
    # is settled.
    print_info "Stopping the application containers (the database stays up)..."
    $DC stop backend worker report-worker frontend >/dev/null 2>&1 || true
    local db_rc=0
    $DC up -d --no-build db || db_rc=$?
    if [[ "$db_rc" -ne 0 ]] || ! wait_for_database 60; then
        print_error "The database container did not come up ('$DC up -d db' returned $db_rc)."
        rollback_summary "STOPPED" \
            "The image names point at the previous build; the application containers are stopped." \
            "Look at:  $DC logs db | tail -n 50      then run option 7 again."
        return 1
    fi

    # ---- 3. Is the database still at the schema the previous build expects? -
    local current_rev schema_moved=1 restored=0 answer
    current_rev="$(database_alembic_revision || true)"
    if [[ -n "$current_rev" && -n "$recorded_rev" && "$current_rev" == "$recorded_rev" ]]; then
        schema_moved=0
    fi

    local have_dump=0
    [[ -n "$predeploy_dump" && -f "$predeploy_dump" ]] && have_dump=1

    echo ""
    if [[ "$schema_moved" -eq 0 ]]; then
        print_success "The database is at the schema the previous build expects ($current_rev):"
        print_success "the failed deploy did not migrate it. No database restore is needed."
        if [[ "$have_dump" -eq 1 ]]; then
            print_info "(To also discard what was written since the deploy began, restore by hand later:"
            print_info "  ./scripts/restore-db.sh \"$predeploy_dump\")"
        fi
    else
        if [[ -n "$current_rev" && -n "$recorded_rev" ]]; then
            print_warning "The failed deploy MIGRATED the database: it is at schema revision $current_rev,"
            print_warning "and the previous build expects $recorded_rev. The previous build cannot start on it."
        else
            print_warning "Cannot confirm the database is at the schema the previous build expects"
            print_warning "(now: ${current_rev:-unreadable}; at the pre-deploy backup: ${recorded_rev:-not recorded})."
            print_warning "If the failed deploy applied a migration, the previous build cannot start on it."
        fi

        if [[ "$have_dump" -eq 1 && -x "scripts/restore-db.sh" ]]; then
            print_info "The pre-deploy backup, taken just before that deploy:"
            echo "    $predeploy_dump"
            print_info "Restoring it returns the database to that moment (a safety backup of the current"
            print_info "database is taken first). Anything written since the deploy began is lost."
            ask answer "Restore the pre-deploy database backup now? [Y/n] " "y"
            if [[ ! "$answer" =~ ^[Nn] ]]; then
                # The same restore an operator runs by hand — restore-db.sh —
                # told that the confirmation was given here and that this
                # script starts the stack afterwards.
                local restore_rc=0
                ./scripts/restore-db.sh --yes --no-start "$predeploy_dump" || restore_rc=$?
                if [[ "$restore_rc" -ne 0 ]]; then
                    rollback_summary "STOPPED" \
                        "The database restore FAILED (exit $restore_rc; its own message is above). The application containers are stopped; the image names point at the previous build." \
                        "Fix what it reported, then:  ./scripts/restore-db.sh \"$predeploy_dump\"   (it starts the stack when it succeeds)."
                    return 1
                fi
                restored=1
            fi
        else
            if [[ "$have_dump" -eq 0 ]]; then
                print_error "No restorable pre-deploy database backup is recorded (recorded: ${predeploy_dump:-nothing})."
                print_info  "Choose one yourself from the backup folder:  ./scripts/restore-db.sh"
            else
                print_error "scripts/restore-db.sh is missing or not executable — cannot restore from here."
            fi
        fi

        if [[ "$restored" -eq 0 ]]; then
            echo ""
            print_warning "Without the restore, the previous build starts on a schema it may not know."
            print_warning "If it does not, its backend will not start (alembic: \"Can't locate revision\")."
            ask answer "Type 'START ANYWAY' to start the previous build on the database as it is: " ""
            if [[ "$answer" != "START ANYWAY" ]]; then
                rollback_summary "STOPPED" \
                    "The image names point at the previous build. The database was NOT changed. The application containers are stopped." \
                    "Restore, which also starts the stack:  ./scripts/restore-db.sh${predeploy_dump:+ \"$predeploy_dump\"}    Or go forward again: option 1."
                return 1
            fi
        fi
    fi

    # ---- 4. Start the previous build: backend alone, wait, then the rest ---
    echo ""
    print_info "Starting the previous build (no rebuild)..."
    local start_rc=0
    stack_start_staged "$DEPLOY_HEALTH_TIMEOUT" --no-build || start_rc=$?
    case "$start_rc" in
        0)
            # The instance is on the previous build again: the unfinished
            # deploy is over, and the next option 1 takes a new rollback point.
            state_del DEPLOY_IN_PROGRESS
            rollback_summary "RUNNING" \
                "The previous build is running and its backend is healthy.$([[ "$restored" -eq 1 ]] && echo ' The database is the pre-deploy backup.')" \
                "Check the version under About BlueStick. To try the upgrade again: option 1."
            return 0
            ;;
        3)
            rollback_summary "PARTLY UP" \
                "The previous build's backend is healthy, but starting the workers or the frontend failed ('$DC up' output above)." \
                "Retry:  $DC up -d --no-build      and look at:  $DC ps"
            return 1
            ;;
        2)
            rollback_summary "NOT RUNNING" \
                "The previous build's backend does not stay up$([[ "$restored" -eq 0 ]] && echo ' — most likely the schema is newer than it knows')." \
                "Look at:  $DC logs backend | tail -n 50$([[ "$restored" -eq 0 && "$have_dump" -eq 1 ]] && echo "      Restore, which also starts the stack:  ./scripts/restore-db.sh \"$predeploy_dump\"")"
            return 1
            ;;
        *)
            rollback_summary "STILL STARTING" \
                "The previous build's backend is running but not healthy after ${DEPLOY_HEALTH_TIMEOUT}s (after a restore it migrates nothing, so this is unusual)." \
                "Watch:  $DC logs -f backend      When it is healthy:  $DC up -d --no-build"
            return 1
            ;;
    esac
}

# The last thing option 7 prints: the state the instance is in, and the next
# step.  $1 state word, $2 what is true now, $3 what to do.
rollback_summary() {
    echo ""
    echo "=============================================="
    echo "   Rollback — instance state: $1"
    echo "=============================================="
    echo "  $2"
    echo "  Next: $3"
    echo ""
}

# ------------------------------------------------------------------
# Main menu
# ------------------------------------------------------------------
echo "=============================================="
echo "   BlueStick Deployment"
echo "=============================================="
echo ""
echo "1) Start / Rebuild  (upgrade an existing instance — keep the stack running)"
echo "   Backs up the database, builds, starts the backend and waits for its"
echo "   migrations, then starts the workers and the frontend"
echo ""
echo "2) First-time setup (generate .env + SSL certs + start)"
echo "   For new installations — a new host starts here, not with option 1"
echo ""
echo "3) Reconfigure IP address"
echo "   Regenerate .env and SSL certs for a new IP"
echo ""
echo "4) Nuclear clean (destroy ALL data and rebuild)"
echo ""
echo "5) Security status check"
echo ""
echo "6) Back up .env + SSL to the parent folder"
echo "   Stash environment config outside the project dir before a re-copy deploy"
echo ""
echo "7) Roll back to previous build"
echo "   Revert to the images snapshotted before the last option-1 deploy"
echo "   (e.g. after a failed boot migration); restores the pre-deploy database"
echo "   backup first when that deploy migrated the schema"
echo ""
ask DEPLOY_CHOICE "Enter your choice (1-7): "

case $DEPLOY_CHOICE in
    1)
        print_header "Starting BlueStick..."
        # An aborted Nuclear clean used to leave its throwaway .env behind
        # (SECRET_KEY=teardown); deploying with it signed every token with a
        # published constant.
        if env_is_teardown_leftover; then
            print_error ".env is the temporary file an interrupted Nuclear clean (option 4) wrote —"
            print_error "it is not a configuration (its SECRET_KEY is a fixed placeholder)."
            print_error "Restore your real .env from the config backup (<project>-config-backup-<time>"
            print_error "next to this folder), or delete it and run option 2 for a new installation."
            exit 1
        fi
        ensure_env
        CONFIGURED_IP=$(get_configured_ip)
        ensure_ssl_certs "$CONFIGURED_IP"
        ensure_uploads_dir

        # B2-1 — before rebuilding in place, snapshot the current images and
        # take a DB backup so a deploy whose boot migration fails (crash-loop,
        # no prior image) can be rolled back via option 7.  After a deploy
        # that did not finish, the EXISTING rollback point is kept instead
        # (prepare_rollback_point).
        check_free_disk
        if ! prepare_rollback_point; then
            # `exit`, not `return`: this is the script's top level, where
            # bash refuses `return` (it only stopped the deploy because
            # set -e caught that error).
            print_error "Aborting deploy — no restorable pre-deploy backup."
            exit 1
        fi

        # CACHE_BUST forces the frontend builder to re-run npm run build
        # on every deploy.  Without it, Docker reuses the cached COPY +
        # build layers when the source dir hash hasn't changed since the
        # last build — fine for unchanged code, but it silently masks
        # legitimate rebuilds when only specific files changed (e.g.
        # config tweaks or selective file copies the user made out-of-band).
        # The cost of always rebusting is ~30s per deploy; the cost of
        # NOT busting is shipping a stale bundle and not knowing it.
        #
        # Three steps, not one `up --build -d` (review 2026-10-01 R31):
        #   1. build — a failed build stops here with the OLD containers still
        #      running and nothing to roll back;
        #   2. start the database, backend and workers and WAIT for the
        #      backend ourselves.  `up` on the whole stack waits on the
        #      frontend's `depends_on: service_healthy` and returns non-zero
        #      if the backend is not healthy in time — which, under `set -e`,
        #      ended this script in the middle of a healthy migration;
        #   3. the workers, then `up -d` for everything, which starts the
        #      frontend (nginx) on the now-healthy backend.
        # The backend starts ALONE (branch review S4 — see scripts/stack-lib.sh).
        ensure_pinned_base_images
        # Before the build, so a host that cannot get the image stops with
        # nothing built and nothing restarted.
        if ! ensure_db_image_local; then
            exit 1
        fi
        warn_missing_base_images

        # From here until a healthy finish the deploy is "in progress": a
        # re-run keeps the rollback point taken above.
        state_set DEPLOY_IN_PROGRESS "$(date +%Y-%m-%dT%H:%M:%S)|"

        print_info "Building images..."
        if ! CACHE_BUST=$(date +%s) $DC build; then
            echo ""
            print_error "The image build failed. Nothing was restarted: the previous containers are"
            print_error "still running the previous build. Fix the error above and deploy again"
            print_error "(the rollback snapshot taken above is kept for that run)."
            exit 1
        fi
        state_set DEPLOY_IN_PROGRESS "$(deploy_marker_started)|$(built_backend_image_id)"

        print_info "Starting the database and the backend (the workers are stopped until it is healthy)..."
        up_rc=0
        stack_start_backend || up_rc=$?

        # Is the container that will answer /health the build just made?
        built_rc=0
        backend_runs_built_image || built_rc=$?
        if [[ "$built_rc" -eq 1 ]]; then
            echo ""
            print_error "The backend container was NOT recreated: it is still running the PREVIOUS build"
            print_error "('$DC up' returned $up_rc; its message is above). The new images are built but not running."
            # The workers were stopped for the staged start; their containers
            # are still the previous build's, so put them back.
            if $DC start worker report-worker >/dev/null 2>&1; then
                print_info "The previous build's workers were started again: the instance is as it was."
            else
                print_warning "The ingestion and report workers are STOPPED (they could not be started again):"
                print_warning "    $DC start worker report-worker"
            fi
            print_info "Fix what '$DC up' reported, then run option 1 again (the rollback point is kept)."
            exit 1
        fi
        if [[ "$up_rc" -ne 0 && -z "$($DC ps -aq backend 2>/dev/null | head -1)" ]]; then
            echo ""
            print_error "'$DC up' returned $up_rc and created no backend container (its message is above)."
            print_error "Nothing is running that was not running before. Fix it and run option 1 again"
            print_error "(the rollback point is kept)."
            exit 1
        fi
        if [[ "$up_rc" -ne 0 && "$built_rc" -eq 0 ]]; then
            print_warning "'$DC up' returned $up_rc — the backend container is the new build; checking whether it comes up."
        elif [[ "$up_rc" -ne 0 ]]; then
            print_warning "'$DC up' returned $up_rc — checking the backend before deciding what that means."
        fi
        if [[ "$built_rc" -eq 2 ]]; then
            print_warning "Could not confirm which image the backend container runs — compare the version"
            print_warning "under About BlueStick with platform_version.json once it is up."
        fi

        # Verify the backend actually came up (migrations ran, uvicorn bound).
        wait_rc=0
        wait_for_backend_healthy "$DEPLOY_HEALTH_TIMEOUT" || wait_rc=$?
        if [[ "$wait_rc" -eq 0 ]]; then
            # The workers and the frontend wait on a healthy backend; it is
            # healthy now.  Captured: a failure here used to end the script
            # under `set -e` with no message.
            rest_rc=0
            stack_start_rest || rest_rc=$?
            if [[ "$rest_rc" -ne 0 ]]; then
                echo ""
                print_error "The backend is healthy on the new build, but starting the workers or the frontend"
                print_error "failed ('$DC up' returned $rest_rc; its message is above). The deploy is NOT complete."
                print_info "See what is up:   $DC ps"
                print_info "Retry:            $DC up -d      (or run option 1 again — the rollback point is kept)"
                print_info "Do NOT roll back for this alone: the schema is already migrated."
                exit 1
            fi
            state_del DEPLOY_IN_PROGRESS
            print_success "Deployment complete!"
            prune_after_deploy
            print_pending_data_repairs
        elif [[ "$wait_rc" -eq 2 ]]; then
            echo ""
            print_error "The backend is not staying up — the boot migration (or startup) failed."
            print_warning "Check logs:   $DC logs backend | tail -n 50   (look for 'DATABASE MIGRATION FAILED')"
            print_warning "Roll back:    re-run this script and choose option 7 (Roll back to previous build)."
            print_warning "              It restores the pre-deploy database backup first when the schema moved."
            print_info    "The rollback point (previous images + pre-deploy backup) is kept until a deploy or a"
            print_info    "rollback ends healthy — running option 1 again will not overwrite it."
            echo ""
            exit 1
        else
            echo ""
            print_warning "The backend is still starting after ${DEPLOY_HEALTH_TIMEOUT}s — it has NOT crashed."
            if migration_lock_held; then
                print_warning "A schema migration is still running. Do NOT roll back: the previous build"
                print_warning "cannot run against a half-migrated schema. Let it finish."
            else
                print_warning "No migration lock is held, and the container is running without restarts."
            fi
            print_info "Watch it:      $DC logs -f backend"
            print_info "When it is healthy, finish the deploy (starts the workers and the frontend):"
            print_info "               $DC up -d"
            print_info "Wait longer next time:  DEPLOY_HEALTH_TIMEOUT=7200 ./scripts/deploy.sh"
            print_migration_stall_help
            print_info "Roll back (option 7) ONLY if the logs show 'DATABASE MIGRATION FAILED' or the"
            print_info "container starts restarting."
            echo ""
            exit 1
        fi
        echo ""
        # The backend port is not published; everything is proxied by nginx
        # on the frontend origin.  Printing :8000 URLs sent operators to a
        # dead port.
        echo "  Frontend: https://${CONFIGURED_IP:-localhost}"
        echo "  Backend:  https://${CONFIGURED_IP:-localhost}/api/v1  (proxied via nginx)"
        echo "  API Docs: https://${CONFIGURED_IP:-localhost}/docs"
        echo ""
        print_info "Default admin: username 'admin'. If DEFAULT_ADMIN_PASSWORD was not set,"
        print_info "the password was auto-generated on the FIRST boot — it is in"
        print_info "./uploads/initial-admin-password.txt and nowhere else (it is never logged)."
        print_info "Password change is required on first login."
        ;;

    2)
        print_header "First-time Setup"

        select_ip
        generate_env "$SELECTED_IP"

        ensure_ssl_certs "$SELECTED_IP"

        ensure_uploads_dir
        ensure_pinned_base_images

        # The same three steps as option 1, and for the same reasons: one
        # `up --build -d` under `set -e` ended this script without a word
        # when the build failed or when `up` gave up waiting for a backend
        # that was still creating the schema — and "setup complete" was
        # printed ten seconds later whatever state the backend was in.
        print_info "Building images..."
        if ! CACHE_BUST=$(date +%s) $DC build; then
            echo ""
            print_error "The image build failed. Nothing was started. Fix the error above and run"
            print_error "option 2 again (.env and the certificates are kept)."
            exit 1
        fi

        setup_rc=0
        stack_start_staged "$DEPLOY_HEALTH_TIMEOUT" || setup_rc=$?
        if [[ "$setup_rc" -eq 2 ]]; then
            echo ""
            print_error "The backend is not staying up — first-time setup is NOT complete."
            print_warning "Check logs:   $DC logs backend | tail -n 50   (look for 'DATABASE MIGRATION FAILED')"
            print_info "Fix what the log reports, then run option 1 (Start / Rebuild)."
            exit 1
        elif [[ "$setup_rc" -eq 1 ]]; then
            echo ""
            print_warning "The backend is still starting after ${DEPLOY_HEALTH_TIMEOUT}s — it has NOT crashed."
            print_info "Watch it:      $DC logs -f backend"
            print_info "When it is healthy, finish the setup (starts the workers and the frontend):"
            print_info "               $DC up -d"
            print_migration_stall_help
            exit 1
        elif [[ "$setup_rc" -ne 0 ]]; then
            echo ""
            print_error "The backend is healthy, but starting the workers or the frontend failed"
            print_error "(the message from '$DC up' is above). First-time setup is NOT complete."
            print_info "See what is up:   $DC ps"
            print_info "Retry:            $DC up -d"
            exit 1
        fi

        # Configure PostgreSQL SSL if script exists
        if [[ -x "scripts/postgres/ensure-ssl.sh" ]]; then
            print_info "Configuring database SSL..."
            # Resolve the db container dynamically — the name varies with the
            # compose project (COMPOSE_PROJECT_NAME / directory name), so the
            # hardcoded "networkmapper-db-1" wasn't reliable.
            db_cid="$($DC ps -q db 2>/dev/null)"
            if [[ -n "$db_cid" ]]; then
                docker cp scripts/postgres/ensure-ssl.sh "$db_cid":/tmp/ensure-ssl.sh 2>/dev/null || true
                $DC exec -T db bash /tmp/ensure-ssl.sh 2>&1 | grep -E "\[ensure-ssl\]" || true
            fi
            sleep 2
            # Captured and checked: under `set -e` a failed restart ended the
            # script here, and a fixed sleep said nothing about the backend.
            restart_rc=0
            $DC restart backend > /dev/null 2>&1 || restart_rc=$?
            ssl_wait_rc=0
            wait_for_backend_healthy "$DEPLOY_HEALTH_TIMEOUT" || ssl_wait_rc=$?
            if [[ "$restart_rc" -ne 0 || "$ssl_wait_rc" -ne 0 ]]; then
                echo ""
                print_error "The backend did not come back after the database SSL step"
                print_error "('$DC restart backend' returned $restart_rc). First-time setup is NOT complete."
                print_info "Check:   $DC ps      and      $DC logs backend | tail -n 50"
                exit 1
            fi
        fi

        print_success "First-time setup complete!"
        echo ""
        echo "  Frontend: https://${SELECTED_IP}"
        echo "  Backend:  https://${SELECTED_IP}/api/v1  (proxied via nginx)"
        echo ""

        echo ""
        print_header "Default Admin Credentials"
        echo "  Username: admin"
        echo "  Password: auto-generated (unless DEFAULT_ADMIN_PASSWORD was set)."
        echo "            It is in ./uploads/initial-admin-password.txt and nowhere else"
        echo "            (it is never written to the logs)."
        print_info "Password change is required on first login."
        echo ""

        check_ssl_status
        ;;

    3)
        print_header "Reconfigure IP Address"

        select_ip

        # A CA-issued certificate is kept, never replaced by a self-signed
        # one: every analyst machine trusts its root, not a new self-signed
        # certificate.  It must already name the new address.
        keep_cert=0
        if cert_is_ca_issued; then
            if cert_names_ip "$SELECTED_IP"; then
                print_info "ssl/certs/networkmapper.crt is issued by your CA and names $SELECTED_IP — keeping it."
                keep_cert=1
            else
                print_error "ssl/certs/networkmapper.crt is issued by your CA and does not name $SELECTED_IP."
                print_info "Reconfiguring would replace it with a self-signed certificate. Instead:"
                print_info "  1. add $SELECTED_IP to SERVER_IPS in ca/ca.conf, keeping the current address"
                print_info "  2. ./ca/local-ca.sh server on the admin workstation, then install it on this host"
                print_info "  3. run this option again"
                print_info "Nothing was changed. See ca/README.md, 'Renewing, changing address'."
                exit 1
            fi
        fi

        # Atomic reconfigure: snapshot the current .env + cert pair into
        # a backup dir, run the regenerate, and roll back on any failure
        # so the host can't end up wedged between two configurations
        # (new .env pointing at the new IP, stale certs still bound to
        # the old IP, or worse — no certs at all because the generator
        # failed after the destructive ``rm`` below).
        backup_dir=".reconfigure-backup-$$"
        mkdir -p "$backup_dir"
        [[ -f .env ]] && cp -p .env "$backup_dir/.env"
        for f in networkmapper.crt networkmapper.key openssl.conf; do
            [[ -f "ssl/certs/$f" ]] && cp -p "ssl/certs/$f" "$backup_dir/$f"
        done

        # Trap unset on success below; if execution exits before that
        # via set -e or an explicit error, this restores the snapshot.
        trap '
            rc=$?
            if [[ $rc -ne 0 ]]; then
                print_error "Reconfigure failed (exit $rc) — restoring previous .env and SSL certs"
                if [[ -f "$backup_dir/.env" ]]; then
                    cp -p "$backup_dir/.env" .env
                fi
                for f in networkmapper.crt networkmapper.key openssl.conf; do
                    if [[ -f "$backup_dir/$f" ]]; then
                        cp -p "$backup_dir/$f" "ssl/certs/$f"
                    fi
                done
            fi
            rm -rf "$backup_dir"
        ' EXIT

        generate_env "$SELECTED_IP"

        # Regenerate the self-signed certificate for the new IP (a CA-issued
        # one was checked above and is kept).
        if [[ $keep_cert -eq 0 ]]; then
            print_info "Regenerating SSL certificates..."
            rm -f ssl/certs/networkmapper.key ssl/certs/networkmapper.crt ssl/certs/openssl.conf
            ensure_ssl_certs "$SELECTED_IP"
        fi

        # Success — drop the rollback trap and the snapshot.
        trap - EXIT
        rm -rf "$backup_dir"

        echo ""
        print_success "Configuration updated for $SELECTED_IP"
        print_info "Run option 1 (Start / Rebuild) to apply changes."
        ;;

    4)
        print_header "Nuclear Clean"
        print_warning "WARNING: This will destroy ALL data including the database!"
        print_warning "It removes the Compose project '$(compose_project_name)' — its containers, database"
        print_warning "volume, networks and built images — and this folder's .env. Other Compose projects"
        print_warning "on this host are not touched. A backup of the database, uploads, .env and SSL is"
        print_warning "taken first, outside this folder."
        ask CONFIRM "Type 'DELETE EVERYTHING' to confirm: " ""

        if [[ "$CONFIRM" != "DELETE EVERYTHING" ]]; then
            print_info "Operation cancelled"
            exit 0
        fi

        # ---- Save what a restore needs, BEFORE anything is destroyed -------
        # (review 2026-10-01 R28).  The database dump is useless without the
        # key that encrypted what is in it: TOTP secrets and stored
        # integration / LLM credentials are Fernet ciphertext keyed off
        # CREDENTIAL_ENCRYPTION_KEY (or SECRET_KEY), which lives only in .env
        # — and this option deletes .env.  So .env and ssl/ are saved the way
        # option 6 saves them, next to the dump, and both locations are
        # printed.  A subshell: backup_config exits when there is nothing to
        # save, which must not end the script here.
        nuke_backup_failed=0
        if [[ -f ".env" || -d "ssl" ]]; then
            if ! ( backup_config ); then
                print_error "Saving .env + SSL failed."
                nuke_backup_failed=1
            fi
        else
            print_info "No .env or ssl/ here — no configuration to save."
        fi

        # Create a minimal .env if missing so Compose can parse the config.
        # It is removed on EVERY way out of this option — a cancelled second
        # confirmation, a failed step, Ctrl-C — not only at the end of a
        # completed teardown: left behind, it looked like a configuration and
        # the next deploy ran with SECRET_KEY=teardown.  Option 1 refuses a
        # .env carrying the marker line, should one survive anyway.
        nuke_temp_env=0
        if [[ ! -f ".env" ]]; then
            print_info "Creating temporary .env for teardown..."
            nuke_temp_env=1
            trap 'if [[ "${nuke_temp_env:-0}" == "1" ]]; then rm -f "$PROJECT_ROOT/.env"; fi' EXIT
            trap 'exit 130' INT TERM
            {
                echo "$TEARDOWN_ENV_MARKER"
                echo "HOST_IP=127.0.0.1"
                echo "REACT_APP_API_URL=https://127.0.0.1"
                echo "CORS_ORIGINS=https://127.0.0.1"
                echo "SECRET_KEY=teardown"
            } > .env
        fi

        # backup-db.sh auto-selects: a logical pg_dump if the db container is
        # up, or a raw volume snapshot if Postgres is down, plus an archive of
        # uploads/.  It writes to the sibling <project>-db-backups directory —
        # outside the project, which the teardown below does not touch.
        nuke_backup_dir="${BACKUP_DIR:-$(dirname "$PROJECT_ROOT")/$(basename "$PROJECT_ROOT")-db-backups}"
        if [[ -x "scripts/backup-db.sh" ]]; then
            print_info "Backing up the database and uploads before teardown..."
            if ./scripts/backup-db.sh; then
                print_success "Database + uploads backup: $nuke_backup_dir"
            else
                print_error "The database / uploads backup FAILED."
                nuke_backup_failed=1
            fi
        else
            print_error "scripts/backup-db.sh not found — no backup was taken."
            nuke_backup_failed=1
        fi

        # A failed backup used to be a warning on the way to the delete.  It
        # is now a stop: the operator typed the first confirmation expecting
        # the backup this option promises.
        if [[ "$nuke_backup_failed" -ne 0 ]]; then
            echo ""
            print_error "The pre-teardown backup is INCOMPLETE (see above). Continuing destroys the"
            print_error "database, its volume and .env with no complete backup to restore from."
            ask CONFIRM_NO_BACKUP "Type 'DELETE WITHOUT BACKUP' to continue anyway, anything else to stop: " ""
            if [[ "$CONFIRM_NO_BACKUP" != "DELETE WITHOUT BACKUP" ]]; then
                print_info "Stopped. Nothing was removed."
                exit 1
            fi
        fi

        # ---- Remove THIS compose project's resources, and only those ------
        # (review 2026-10-01 R27).  This used `--filter name=networkmapper`,
        # a SUBSTRING match, for containers and volumes, a hardcoded
        # `networkmapper_postgres_data`, and `networkmapper-*` image names:
        # a second copy of the tree on the same host (networkmapper-test,
        # old-networkmapper…) lost its containers, and its database volume if
        # it was stopped.  Everything compose creates carries the label
        # com.docker.compose.project=<name>; that label is the only selector
        # used here.
        nuke_project="$(compose_project_name)"
        if [[ -z "$nuke_project" ]]; then
            print_error "Could not determine this stack's Compose project name — refusing to remove"
            print_error "anything by guesswork. Set COMPOSE_PROJECT_NAME in .env and re-run."
            exit 1
        fi
        nuke_label="label=com.docker.compose.project=${nuke_project}"
        print_info "Removing the Compose project '${nuke_project}' (containers, volumes, networks, images)..."

        # Counted before the teardown, so the closing lines can say what went.
        nuke_containers="$(docker ps -aq --filter "$nuke_label" 2>/dev/null | wc -l | tr -d ' ')"
        nuke_volumes="$(docker volume ls -q --filter "$nuke_label" 2>/dev/null | wc -l | tr -d ' ')"
        nuke_images="$(docker images -q --filter "$nuke_label" 2>/dev/null | sort -u | wc -l | tr -d ' ')"

        # Images are collected now: `down` removes the containers that
        # reference them, the label filter still finds them afterwards, but a
        # list taken first cannot be affected by anything in between.
        mapfile -t nuke_image_ids < <(docker images -q --filter "$nuke_label" 2>/dev/null | sort -u)

        $DC down --remove-orphans --volumes 2>/dev/null || true

        # What `down` left behind — a container compose no longer knows as a
        # service, a volume of a renamed service — still by label only.
        docker ps -aq --filter "$nuke_label" 2>/dev/null | xargs -r docker rm -f >/dev/null 2>&1 || true
        docker volume ls -q --filter "$nuke_label" 2>/dev/null | xargs -r docker volume rm >/dev/null 2>&1 || true
        docker network ls -q --filter "$nuke_label" 2>/dev/null | xargs -r docker network rm >/dev/null 2>&1 || true

        # This project's built images, including the :previous rollback tags
        # (they are tags of images this project built, so they carry its
        # label).  Base images (postgres, python, node, nginx) are shared with
        # other projects and are left.
        if [[ "${#nuke_image_ids[@]}" -gt 0 ]]; then
            printf '%s\n' "${nuke_image_ids[@]}" | xargs -r docker rmi -f >/dev/null 2>&1 || true
        fi
        rm -f "$ROLLBACK_STATE_FILE"

        # Clean up .env so first-time setup starts fresh
        rm -f .env

        # The database is gone, so the first-boot admin credential file no
        # longer opens anything — remove it so the next deploy's freshly
        # generated credential is the only one an operator can find.
        rm -f uploads/initial-admin-password.txt

        nuke_left_containers="$(docker ps -aq --filter "$nuke_label" 2>/dev/null | wc -l | tr -d ' ')"
        nuke_left_volumes="$(docker volume ls -q --filter "$nuke_label" 2>/dev/null | wc -l | tr -d ' ')"
        echo ""
        if [[ "$nuke_left_containers" -eq 0 && "$nuke_left_volumes" -eq 0 ]]; then
            print_success "Removed Compose project '${nuke_project}': ${nuke_containers} container(s), ${nuke_volumes} volume(s), ${nuke_images} image(s), and .env."
        else
            print_error "Compose project '${nuke_project}' was NOT fully removed: ${nuke_left_containers} container(s) and ${nuke_left_volumes} volume(s) remain."
            print_info  "List them:  docker ps -a --filter $nuke_label ;  docker volume ls --filter $nuke_label"
        fi
        print_info "Only resources labelled com.docker.compose.project=${nuke_project} were touched."
        print_info "Left in place: base images, Docker's build cache (shared with other projects —"
        print_info "'docker builder prune' reclaims it), ./uploads, ./ssl, and the backups:"
        print_info "    database + uploads: $nuke_backup_dir"
        print_info "    .env + SSL:         $(dirname "$PROJECT_ROOT")/$(basename "$PROJECT_ROOT")-config-backup-<timestamp>  (printed above)"
        print_info "To restore: copy the saved .env back FIRST (it holds the key the dump's"
        print_info "encrypted secrets need), start the stack, then ./scripts/restore-db.sh <dump>."
        ;;

    5)
        check_ssl_status
        ;;

    6)
        backup_config
        ;;

    7)
        # The function prints the state the instance ended in; a rollback
        # that did not end healthy is this script's failure.
        rollback_to_previous || exit 1
        ;;

    *)
        print_error "Invalid choice. Please select 1-7."
        exit 1
        ;;
esac

echo ""
print_success "Done!"
echo ""
print_info "Useful commands:"
echo "  $DC up -d         - Start the application"
echo "  $DC down          - Stop the application"
echo "  $DC logs backend  - View backend logs"
echo "  $DC ps            - Container status"
echo "  ./scripts/collect-logs.sh    - Collect debug logs"
echo "  ./scripts/status.sh          - Quick status check"
echo "  ./scripts/backup-db.sh       - Back up the database"
echo "  ./scripts/restore-db.sh      - Restore the database from a backup"
echo ""
