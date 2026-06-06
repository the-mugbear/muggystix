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
generate_env() {
    local ip="$1"

    if [[ ! -f ".env.example" ]]; then
        print_error ".env.example not found"
        exit 1
    fi

    # Generate a secret key
    local secret_key
    if command -v python3 >/dev/null 2>&1; then
        secret_key=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    else
        secret_key=$(openssl rand -base64 32 2>/dev/null || head -c 32 /dev/urandom | base64)
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

    # Write to a temp file first so a sed/IO failure can't leave a
    # half-written .env on disk.  Atomic rename only after sed exits 0.
    local tmp
    tmp="$(mktemp ".env.tmp.XXXXXX")"
    if ! sed \
        -e "s|^HOST_IP=.*|HOST_IP=${ip}|" \
        -e "s|^REACT_APP_API_URL=.*|REACT_APP_API_URL=https://${ip}|" \
        -e "s|^CORS_ORIGINS=.*|CORS_ORIGINS=${cors}|" \
        -e "s|^SECRET_KEY=.*|SECRET_KEY=${secret_key}|" \
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
    mkdir -p uploads/ingestion_queue
    # 1777 (sticky bit) allows the container's appuser (UID 999) to write
    # while preventing users from deleting each other's files.  Full 777
    # would work but is unnecessarily permissive on shared hosts.
    chmod 1777 uploads/ingestion_queue
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
echo "Enter your choice (1-5): "
read -r DEPLOY_CHOICE

case $DEPLOY_CHOICE in
    1)
        print_header "Starting BlueStick..."
        ensure_env
        CONFIGURED_IP=$(get_configured_ip)
        ensure_ssl_certs "$CONFIGURED_IP"
        ensure_uploads_dir
        # CACHE_BUST forces the frontend builder to re-run npm run build
        # on every deploy.  Without it, Docker reuses the cached COPY +
        # build layers when the source dir hash hasn't changed since the
        # last build — fine for unchanged code, but it silently masks
        # legitimate rebuilds when only specific files changed (e.g.
        # config tweaks or selective file copies the user made out-of-band).
        # The cost of always rebusting is ~30s per deploy; the cost of
        # NOT busting is shipping a stale bundle and not knowing it.
        CACHE_BUST=$(date +%s) $DC up --build -d

        print_success "Deployment complete!"
        echo ""
        echo "  Frontend: https://${CONFIGURED_IP:-localhost}"
        echo "  Backend:  https://${CONFIGURED_IP:-localhost}:8000"
        echo "  API Docs: https://${CONFIGURED_IP:-localhost}:8000/docs"
        echo ""
        print_info "Default admin credentials: admin / admin"
        print_info "Password change is required on first login."
        ;;

    2)
        print_header "First-time Setup"

        select_ip
        generate_env "$SELECTED_IP"

        ensure_ssl_certs "$SELECTED_IP"

        print_info "Building and starting containers..."
        ensure_uploads_dir
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
        echo "  Backend:  https://${SELECTED_IP}:8000"
        echo ""

        echo ""
        print_header "Default Admin Credentials"
        echo "  Username: admin"
        echo "  Password: admin"
        print_info "Password change is required on first login."
        echo ""

        check_ssl_status
        ;;

    3)
        print_header "Reconfigure IP Address"

        select_ip

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

        # Regenerate SSL certs for new IP
        print_info "Regenerating SSL certificates..."
        rm -f ssl/certs/networkmapper.key ssl/certs/networkmapper.crt ssl/certs/openssl.conf
        ensure_ssl_certs "$SELECTED_IP"

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
        echo "Type 'DELETE EVERYTHING' to confirm: "
        read -r CONFIRM

        if [[ "$CONFIRM" != "DELETE EVERYTHING" ]]; then
            print_info "Operation cancelled"
            exit 0
        fi

        # Create a minimal .env if missing so Compose can parse the config
        if [[ ! -f ".env" ]]; then
            print_info "Creating temporary .env for teardown..."
            echo "HOST_IP=127.0.0.1" > .env
            echo "REACT_APP_API_URL=https://127.0.0.1" >> .env
            echo "CORS_ORIGINS=https://127.0.0.1" >> .env
            echo "SECRET_KEY=teardown" >> .env
        fi

        # Best-effort backup BEFORE destroying the database.  backup-db.sh
        # auto-selects: a logical pg_dump if the db container is up, or a
        # raw volume snapshot if Postgres is down.  Backups land in
        # ./backups/ — a host directory the teardown below does NOT touch,
        # so the artifact survives the nuke.  Failure is non-fatal: the
        # user explicitly asked to destroy everything.
        if [[ -x "scripts/backup-db.sh" ]]; then
            print_info "Backing up the database before teardown..."
            ./scripts/backup-db.sh || print_warning "Backup failed — continuing with teardown."
        else
            print_warning "scripts/backup-db.sh not found — skipping pre-teardown backup."
        fi

        # Stop and remove containers, volumes, and networks
        print_info "Stopping containers..."
        $DC down --remove-orphans --volumes 2>/dev/null || true

        # Remove named volumes explicitly in case compose missed them
        docker volume rm networkmapper_postgres_data 2>/dev/null || true
        # Catch alternate naming conventions (docker compose v2 uses hyphens)
        docker volume ls -q --filter "name=networkmapper" 2>/dev/null | xargs -r docker volume rm 2>/dev/null || true

        # Remove all project images — both hyphen and underscore naming
        print_info "Removing images..."
        docker images --filter "reference=networkmapper-*" -q 2>/dev/null | xargs -r docker rmi -f 2>/dev/null || true
        docker images --filter "reference=networkmapper_*" -q 2>/dev/null | xargs -r docker rmi -f 2>/dev/null || true

        # Remove any containers that survived (e.g. stopped/dead)
        docker ps -a --filter "name=networkmapper" -q 2>/dev/null | xargs -r docker rm -f 2>/dev/null || true

        # Prune dangling images and build cache from this project
        docker image prune -f 2>/dev/null || true
        docker builder prune -f 2>/dev/null || true

        # Clean up .env so first-time setup starts fresh
        rm -f .env

        print_success "BlueStick containers, volumes, and images removed."
        print_info "Other Docker projects on this system were NOT affected."
        ;;

    5)
        check_ssl_status
        ;;

    *)
        print_error "Invalid choice. Please select 1-5."
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
