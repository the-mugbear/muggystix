#!/bin/bash
#
# This script checks the status of the BlueStick application instances.
#
# What it does:
# - Checks the status of the production and test instances of the application.
# - For each running instance, it displays the URLs for the frontend, backend, and API docs.
# - It also performs a quick health check to see if the frontend and backend are responding.
# - Lists available management scripts and quick actions for managing the instances.
# - Shows resource usage by displaying running Docker containers and volumes.
#
# How it does it:
# - Uses `docker-compose ps` to check the status of the containers for each instance.
# - Uses `curl` to perform health checks on the frontend and backend URLs.
# - Displays a list of other useful management scripts.
# - Provides common `docker-compose` commands for starting, stopping, and managing the instances.
# - Uses `docker ps` and `docker volume ls` to show resource usage.
#
# BlueStick Status and Management Script
# Shows status of all instances and provides quick management options
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Function to print colored output
print_info() { echo -e "${BLUE} $1${NC}"; }
print_success() { echo -e "${GREEN} $1${NC}"; }
print_warning() { echo -e "${YELLOW} $1${NC}"; }
print_error() { echo -e "${RED} $1${NC}"; }
print_header() { echo -e "${CYAN} $1${NC}"; }

echo "========================================"
echo "       BlueStick Status"
echo "========================================"
echo

cd "$PROJECT_ROOT"

# Prefer the docker compose v2 plugin; fall back to the EOL v1 standalone.
if docker compose version >/dev/null 2>&1; then
    DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    DC="docker-compose"
else
    print_error "Neither 'docker compose' (v2) nor 'docker-compose' (v1) is available."
    exit 1
fi

# Check production instance
print_header "Production Instance Status"
if $DC ps | grep -q "networkmapper.*Up"; then
    print_success "Production instance is running"
    echo "  Frontend: https://localhost:3000"
    echo "  Backend:  https://localhost:8000"
    echo "  API Docs: https://localhost:8000/docs"

    # Check if frontend is responding
    if curl -k -s https://localhost:3000 >/dev/null 2>&1; then
        print_success "Frontend is responding"
    else
        print_warning "Frontend may be starting up..."
    fi

    # Check if backend is responding
    if curl -k -s https://localhost:8000/health >/dev/null 2>&1; then
        print_success "Backend is healthy"
    else
        print_warning "Backend may be starting up..."
    fi
else
    print_warning "Production instance is not running"
fi
echo

echo

# Show available scripts
print_header "Available Management Scripts"
echo "Deployment Scripts:"
echo "  ./scripts/deploy.sh            - Unified deployment script (all options)"
echo
echo "User Management:"
echo "  Default admin account is created automatically on first boot"
echo "  Set DEFAULT_ADMIN_PASSWORD env var to control the initial password"
echo
echo "Maintenance:"
echo "  ./scripts/collect-logs.sh      - Collect comprehensive logs for debugging"
echo "  ./scripts/status.sh            - Show this status (current script)"
echo

# Show quick actions
print_header "Quick Actions"
echo "Production Instance:"
echo "  Start:  $DC up -d"
echo "  Stop:   $DC down"
echo "  Logs:   $DC logs -f"
echo "  Reset:  $DC down -v && $DC up -d"
echo

# Show disk usage
print_header "Resource Usage"
echo "Docker containers:"
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" | grep -E "(networkmapper|NAMES)"
echo
echo "Docker volumes:"
docker volume ls | grep -E "(networkmapper|postgres)"
echo

print_info "For detailed logs: ./scripts/collect-logs.sh"
print_info "For all deployments: ./scripts/deploy.sh"