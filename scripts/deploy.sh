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
    echo -n "Enter choice [1]: "
    read -r choice

    # Default to first option
    if [[ -z "$choice" ]]; then
        choice=1
    fi

    if [[ "$choice" -eq $(( ${#ips[@]} + 1 )) ]] 2>/dev/null; then
        echo -n "Enter IP address or hostname: "
        read -r SELECTED_IP
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
    if [[ -n "$have" && "$have" == "$want"* ]]; then
        if docker tag "$floating" "$pinned" 2>/dev/null; then
            print_info "Base image: local $floating is $have — tagged as $pinned (no pull needed)."
        fi
    else
        print_warning "Base image $pinned is not on this host (local $floating is ${have:-unknown})."
        print_warning "Docker will pull it; on an isolated host, load it or set the override in .env"
        print_warning "(POSTGRES_IMAGE / PYTHON_IMAGE / NODE_IMAGE / NGINX_IMAGE)."
    fi
}

ensure_pinned_base_images() {
    local env_override
    # The database image: whatever compose resolves (the pin, or POSTGRES_IMAGE).
    local db_image
    db_image="$($DC config --images db 2>/dev/null | head -1)"
    if [[ "$db_image" =~ ^postgres:([0-9]+)\.([0-9]+)$ ]]; then
        adopt_local_base_image "$db_image" "postgres:${BASH_REMATCH[1]}" PG_VERSION \
            "${BASH_REMATCH[1]}.${BASH_REMATCH[2]}-"
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
    echo "Would you like to generate one now? (Y/n): "
    read -r answer
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

    mkdir -p "$dest"

    if [[ -f ".env" ]]; then
        cp -p .env "$dest/.env"
        print_success "Backed up .env"
        found=1
    else
        print_warning ".env not found — skipping"
    fi

    if [[ -d "ssl" ]]; then
        cp -a ssl "$dest/ssl"
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
    [[ -f "$dest/.env" ]] && echo "    cp -p \"$dest/.env\" ./.env"
    [[ -d "$dest/ssl" ]] && echo "    cp -a \"$dest/ssl/.\" ./ssl/"
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
        echo "Continue anyway? [y/N]: "
        read -r answer
        [[ "$answer" =~ ^[Yy]$ ]] || { print_error "Deploy cancelled — free some disk space and re-run."; exit 1; }
    fi
}

# After a healthy deploy: remove what this deploy made obsolete.  Untagged
# (dangling) images only — the :previous rollback tags are kept, so option 7
# still works — and build cache older than a week (recent layers keep the
# next rebuild fast).
prune_after_deploy() {
    print_info "Reclaiming disk: dangling images and build cache older than 7 days..."
    docker image prune -f >/dev/null 2>&1 || true
    docker builder prune -f --filter until=168h >/dev/null 2>&1 || true
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
            echo "PREDEPLOY_DB_DUMP|NONE (forced)" >> "$ROLLBACK_STATE_FILE"
            return 0
        fi
        echo "PREDEPLOY_DB_DUMP|${dump}" >> "$ROLLBACK_STATE_FILE"
        print_info "Pre-deploy DB backup: $dump"
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
        echo "PREDEPLOY_DB_DUMP|NONE (forced)" >> "$ROLLBACK_STATE_FILE"
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
# Returns 0 healthy, 2 crashed / crash-looping, 1 still starting at timeout.
# DEPLOY_HEALTH_TIMEOUT (seconds, default 1800) bounds the wait.
# ------------------------------------------------------------------
DEPLOY_HEALTH_TIMEOUT="${DEPLOY_HEALTH_TIMEOUT:-1800}"
# backend/app/db/init.py _MIGRATION_LOCK_KEY — the session-level advisory lock
# held for the whole of `alembic upgrade head`.
MIGRATION_LOCK_KEY=738582901
# Restarts of ONE container before it counts as crash-looping (a single
# restart can be a database that was not accepting connections yet).
BACKEND_RESTART_LIMIT=3

backend_probe() {
    $DC exec -T backend python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=3).status==200 else 1)" >/dev/null 2>&1
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

migration_lock_held() {
    local pg_user pg_db held
    pg_user="$(grep -E '^POSTGRES_USER=' .env 2>/dev/null | tail -1 | cut -d= -f2-)"
    pg_db="$(grep -E '^POSTGRES_DB=' .env 2>/dev/null | tail -1 | cut -d= -f2-)"
    held="$($DC exec -T db psql -U "${pg_user:-nmapuser}" -d "${pg_db:-networkMapper}" -tAc \
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted AND classid = 0 AND objid = ${MIGRATION_LOCK_KEY}" \
        2>/dev/null | tr -dc '0-9')"
    [[ -n "$held" && "$held" -gt 0 ]]
}

wait_for_backend_healthy() {
    local timeout="${1:-$DEPLOY_HEALTH_TIMEOUT}" elapsed=0 status restarts last_note=0
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
        if (( elapsed - last_note >= 30 )); then
            last_note=$elapsed
            if migration_lock_held; then
                print_info "  ${elapsed}s — a schema migration is running (migration lock held). Waiting; do not interrupt."
            else
                print_info "  ${elapsed}s — backend is ${status:-starting}, restarts: ${restarts:-0}."
            fi
        fi
        sleep 5
        elapsed=$((elapsed + 5))
    done
    return 1
}

# After a healthy start: one-off data corrections this instance has no
# recorded run of (the data-repair ledger, review 2026-10-01 B10).  A
# reminder with the exact commands — nothing is run here: one of them deletes
# rows, and the other must not run beside a live import.
print_pending_data_repairs() {
    local pending
    pending="$($DC exec -T backend python scripts/data_repairs.py --pending 2>/dev/null || true)"
    if [[ -n "$pending" ]]; then
        echo ""
        print_warning "Data repairs not yet applied on this instance:"
        echo "$pending" | sed 's/^/  /'
        print_info "They correct rows written by older versions. Run them when no import is in"
        print_info "progress; ./scripts/status.sh repeats this list until each has been run."
    fi
}

# Re-point the :rollback image tags and (prompted) restore the pre-deploy DB
# backup, then bring the stack back up WITHOUT rebuilding.
rollback_to_previous() {
    print_header "Roll Back to Previous Build"
    if [[ ! -s "$ROLLBACK_STATE_FILE" ]]; then
        print_error "No rollback snapshot found ($ROLLBACK_STATE_FILE)."
        print_info "A snapshot is created automatically the next time you run option 1."
        return 1
    fi

    local predeploy_dump="" reverted=0 line svc ref
    while IFS='|' read -r svc ref; do
        if [[ -z "$svc" ]]; then
            continue
        fi
        if [[ "$svc" == "PREDEPLOY_DB_DUMP" ]]; then
            predeploy_dump="$ref"
            continue
        fi
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
        print_error "No snapshot images could be reverted."
        return 1
    fi

    print_info "Restarting the stack on the previous images (no rebuild)..."
    $DC up -d --no-build

    # The previous image expects the previous schema; a migration that partially
    # applied leaves the DB ahead of it. Offer to restore the pre-deploy backup.
    if [[ -n "$predeploy_dump" && -f "$predeploy_dump" ]]; then
        echo ""
        print_warning "The previous build expects the PRE-deploy database schema."
        print_warning "If the failed deploy applied a migration, restore the pre-deploy backup:"
        echo "    $predeploy_dump"
        echo -n "Restore the pre-deploy database backup now? [y/N] "
        read -r restore_confirm
        if [[ "$restore_confirm" == "y" || "$restore_confirm" == "Y" ]]; then
            if [[ -x "scripts/restore-db.sh" ]]; then
                ./scripts/restore-db.sh "$predeploy_dump"
            else
                print_error "scripts/restore-db.sh not found — restore manually:"
                echo "    ./scripts/restore-db.sh \"$predeploy_dump\""
            fi
        else
            print_info "Skipped DB restore. If the old build misbehaves, restore manually:"
            echo "    ./scripts/restore-db.sh \"$predeploy_dump\""
        fi
    else
        print_warning "No pre-deploy DB backup was recorded — if the failed deploy migrated the"
        print_warning "schema, the previous build may error against it. Restore a backup manually."
    fi

    echo ""
    print_success "Rollback complete. Verify with option 5 / your health checks."
}

# ------------------------------------------------------------------
# Main menu
# ------------------------------------------------------------------
echo "=============================================="
echo "   BlueStick Deployment"
echo "=============================================="
echo ""
echo "1) Start / Rebuild"
echo "   $DC up --build -d"
echo ""
echo "2) First-time setup (generate .env + SSL certs + start)"
echo "   For new installations"
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
echo "   (e.g. after a failed boot migration), with optional DB restore"
echo ""
echo "Enter your choice (1-7): "
read -r DEPLOY_CHOICE

case $DEPLOY_CHOICE in
    1)
        print_header "Starting BlueStick..."
        ensure_env
        CONFIGURED_IP=$(get_configured_ip)
        ensure_ssl_certs "$CONFIGURED_IP"
        ensure_uploads_dir

        # B2-1 — before rebuilding in place, snapshot the current images and
        # take a DB backup so a deploy whose boot migration fails (crash-loop,
        # no prior image) can be rolled back via option 7.
        check_free_disk
        snapshot_images_for_rollback
        if ! predeploy_db_backup; then
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
        #   3. `up -d` for everything, which starts the frontend (nginx) on
        #      the now-healthy backend.
        ensure_pinned_base_images
        print_info "Building images..."
        if ! CACHE_BUST=$(date +%s) $DC build; then
            echo ""
            print_error "The image build failed. Nothing was restarted: the previous containers are"
            print_error "still running the previous build. Fix the error above and deploy again."
            exit 1
        fi

        print_info "Starting the database, backend and workers..."
        up_rc=0
        $DC up -d db backend worker report-worker || up_rc=$?
        if [[ "$up_rc" -ne 0 ]]; then
            print_warning "'$DC up' returned $up_rc — checking the backend before deciding what that means."
        fi

        # Verify the backend actually came up (migrations ran, uvicorn bound).
        wait_rc=0
        wait_for_backend_healthy || wait_rc=$?
        if [[ "$wait_rc" -eq 0 ]]; then
            # The frontend waits on a healthy backend; it is healthy now.
            $DC up -d
            print_success "Deployment complete!"
            prune_after_deploy
            print_pending_data_repairs
        elif [[ "$wait_rc" -eq 2 ]]; then
            echo ""
            print_error "The backend is not staying up — the boot migration (or startup) failed."
            print_warning "Check logs:   $DC logs backend | tail -n 50   (look for 'DATABASE MIGRATION FAILED')"
            print_warning "Roll back:    re-run this script and choose option 7 (Roll back to previous build)"
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
            print_info "When it is healthy, finish the deploy (starts the frontend):"
            print_info "               $DC up -d"
            print_info "Wait longer next time:  DEPLOY_HEALTH_TIMEOUT=7200 ./scripts/deploy.sh"
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

        print_info "Building and starting containers..."
        ensure_uploads_dir
        ensure_pinned_base_images
        CACHE_BUST=$(date +%s) $DC up --build -d

        print_info "Waiting for services to start..."
        sleep 10

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
            $DC restart backend > /dev/null 2>&1
            sleep 5
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
        echo "Type 'DELETE EVERYTHING' to confirm: "
        read -r CONFIRM

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

        # Create a minimal .env if missing so Compose can parse the config
        if [[ ! -f ".env" ]]; then
            print_info "Creating temporary .env for teardown..."
            echo "HOST_IP=127.0.0.1" > .env
            echo "REACT_APP_API_URL=https://127.0.0.1" >> .env
            echo "CORS_ORIGINS=https://127.0.0.1" >> .env
            echo "SECRET_KEY=teardown" >> .env
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
            echo "Type 'DELETE WITHOUT BACKUP' to continue anyway, anything else to stop: "
            read -r CONFIRM_NO_BACKUP || CONFIRM_NO_BACKUP=""
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
        rollback_to_previous
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
