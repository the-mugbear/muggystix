#!/usr/bin/env bash
#
# BlueStick Transfer Tool
# Export and import the complete application including database data
#
# Usage:
#   ./scripts/transfer-images.sh export <output-dir>  - Export images and database
#   ./scripts/transfer-images.sh import <input-dir>   - Import images and database
#
# What gets exported:
#   - Application Docker images (backend, frontend)
#   - PostgreSQL database dump (all data)
#   - Environment configuration template
#
# The exported directory can be copied to another host for complete application transfer.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Detect which docker compose command is available.
# Prefer the v2 plugin (`docker compose`); fall back to the EOL v1 standalone
# (`docker-compose`) only if the plugin isn't present.
COMPOSE_CMD=""
if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD="docker-compose"
fi

log() {
  echo "[transfer] $*"
}

error() {
  echo "[transfer] ERROR: $*" >&2
  exit 1
}

usage() {
  cat <<USAGE
BlueStick Transfer Tool - Complete application export/import with data

Usage:
  $0 export <output-dir>    Export images and database to directory
  $0 import <input-dir>     Import images and restore database from directory

Examples:
  # Export everything
  ./scripts/transfer-images.sh export /tmp/nm-export

  # Copy to another host
  scp -r /tmp/nm-export user@newhost:/tmp/

  # Import on new host
  cd /path/to/BlueStick
  ./scripts/transfer-images.sh import /tmp/nm-export

What gets transferred:
  ✓ Backend application image
  ✓ Frontend application image
  ✓ PostgreSQL database dump (all data)
  ✓ Database credentials (db-config.env)
  ✓ Docker Compose configuration
  ✓ Environment configuration template

Note: PostgreSQL base image is pulled automatically on import.
USAGE
}

check_dependencies() {
  # Check for docker
  if ! command -v docker >/dev/null 2>&1; then
    error "Required command not found: docker"
  fi

  # Check for docker compose (already detected in COMPOSE_CMD)
  if [[ -z "$COMPOSE_CMD" ]]; then
    error "Docker Compose not found. Install 'docker compose' (v2) or 'docker-compose' (v1)"
  fi
}

check_db_running() {
  log "Checking if database is running..."
  if ! $COMPOSE_CMD ps db 2>/dev/null | grep -q "Up"; then
    error "Database container is not running. Start it with: $COMPOSE_CMD up -d db"
  fi
}

export_app() {
  local output_dir="$1"

  if [[ -z "$output_dir" ]]; then
    usage
    exit 1
  fi

  mkdir -p "$output_dir"
  output_dir="$(cd "$output_dir" && pwd)"

  cd "$PROJECT_ROOT"

  log "Exporting BlueStick to: $output_dir"

  # Build images if needed
  log "Building application images..."
  $COMPOSE_CMD build backend frontend

  # Get image names - use the images that were just built
  local backend_image frontend_image
  backend_image=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep backend | grep -v '<none>' | head -1)
  frontend_image=$(docker images --format '{{.Repository}}:{{.Tag}}' | grep frontend | grep -v '<none>' | head -1)

  # Verify we found the images
  if [[ -z "$backend_image" || -z "$frontend_image" ]]; then
    error "Could not find images. Ensure images are built with: $COMPOSE_CMD build"
  fi

  # Export backend image
  log "Exporting backend image: $backend_image"
  docker save "$backend_image" -o "$output_dir/backend.tar"
  echo "$backend_image" > "$output_dir/backend-tag.txt"

  # Export frontend image
  log "Exporting frontend image: $frontend_image"
  docker save "$frontend_image" -o "$output_dir/frontend.tar"
  echo "$frontend_image" > "$output_dir/frontend-tag.txt"

  # Export database
  log "Exporting database..."
  check_db_running

  # Read database connection info from environment or defaults
  local db_name="${POSTGRES_DB:-networkMapper}"
  local db_user="${POSTGRES_USER:-nmapuser}"
  local db_pass="${POSTGRES_PASSWORD:-nmappass}"

  # Use docker exec to run pg_dump
  $COMPOSE_CMD exec -T db pg_dump -U "$db_user" "$db_name" > "$output_dir/database.sql"

  # Save database credentials for import
  cat > "$output_dir/db-config.env" <<EOF
POSTGRES_DB=$db_name
POSTGRES_USER=$db_user
POSTGRES_PASSWORD=$db_pass
EOF

  # Copy docker-compose.yml for deployment on new host
  log "Copying docker-compose.yml..."
  if [[ -f "$PROJECT_ROOT/docker-compose.yml" ]]; then
    cp "$PROJECT_ROOT/docker-compose.yml" "$output_dir/"
  else
    log "WARNING: docker-compose.yml not found"
  fi

  # Copy .env.example as template
  log "Copying environment configuration template..."
  if [[ -f "$PROJECT_ROOT/.env.example" ]]; then
    cp "$PROJECT_ROOT/.env.example" "$output_dir/"
  else
    log "WARNING: .env.example not found"
  fi

  # Create README
  cat > "$output_dir/README.txt" <<EOF
BlueStick Export - $(date)
================================

This directory contains a complete BlueStick application export:
  - backend.tar              Backend Docker image
  - frontend.tar             Frontend Docker image
  - database.sql             PostgreSQL database dump
  - db-config.env            Database credentials
  - docker-compose.yml       Docker Compose configuration
  - .env.example     Environment configuration template

To import on another host:
  1. Install Docker and Docker Compose on the target host
     - For Python 3.12+ systems, use Docker Compose v2 (built-in):
       https://docs.docker.com/compose/install/
     - Avoid standalone docker-compose v1 on Python 3.12+ (distutils removed)
  2. Copy this entire directory to the target host
  3. Extract/copy the BlueStick scripts directory or use the included docker-compose.yml
  4. (Optional) Configure .env from .env.example
  5. Run: ./scripts/transfer-images.sh import /path/to/this/directory
  6. Start the application: docker compose up -d (or docker-compose up -d)

The import process will:
  - Load the Docker images with proper tags
  - Create database volume and restore all data
  - Preserve all scans, hosts, users, and configuration

Note: The docker-compose.yml is included for reference. You can use it directly
or integrate with an existing BlueStick repository.

Python 3.12+ Users: If you get "distutils missing" errors with docker-compose v1,
install Docker Compose v2 (plugin) instead. The import script supports both versions.
EOF

  # Create troubleshooting guide
  cat > "$output_dir/TROUBLESHOOTING.txt" <<'TROUBLE'
BlueStick Import Troubleshooting Guide
==========================================

ISSUE: "distutils missing" or "No module named 'distutils'" error
-----------------------------------------------------------------
Problem: You're using docker-compose v1 (standalone) with Python 3.12+
Cause: Python 3.12 removed the distutils module, breaking docker-compose v1

Solutions (choose one):

1. Install Docker Compose v2 (RECOMMENDED):
   # Remove old docker-compose
   sudo rm /usr/local/bin/docker-compose

   # Install Docker Compose v2 plugin
   # Follow: https://docs.docker.com/compose/install/linux/

   # Verify installation
   docker compose version

2. Use Docker Desktop (includes Compose v2):
   # Download from: https://docs.docker.com/desktop/

3. Install distutils compatibility package (TEMPORARY FIX):
   # Debian/Ubuntu
   sudo apt-get install python3-setuptools

   # This may not work on all systems with Python 3.12+

4. Use Python 3.11 for docker-compose:
   # Install Python 3.11
   sudo apt-get install python3.11

   # Reinstall docker-compose with Python 3.11
   python3.11 -m pip install docker-compose

After fixing, verify with:
  docker compose version  (v2)
  OR
  docker-compose version  (v1)

Then run the import again:
  ./scripts/transfer-images.sh import /path/to/export

ISSUE: Images not loading or "image not found"
-----------------------------------------------
Problem: Docker can't find the imported images

Solution:
  # Check if images were loaded
  docker images | grep networkmapper

  # If missing, manually load them
  docker load -i backend.tar
  docker load -i frontend.tar

  # Verify tags
  docker images | grep networkmapper
  # Should show: networkmapper-backend:latest and networkmapper-frontend:latest

ISSUE: Database connection errors
----------------------------------
Problem: Backend can't connect to database

Solution:
  # Check if database is running
  docker compose ps

  # Check database logs
  docker compose logs db

  # Ensure database credentials match
  cat db-config.env

  # Restart database
  docker compose restart db

ISSUE: "permission denied" errors
----------------------------------
Problem: Script can't execute or access files

Solution:
  # Make script executable
  chmod +x scripts/transfer-images.sh

  # Check file ownership
  ls -la

  # Fix ownership if needed
  sudo chown -R $USER:$USER .

For more help, see: https://docs.docker.com/compose/
TROUBLE

  log ""
  log "✓ Export complete!"
  log ""
  log "Exported files:"
  log "  - backend.tar ($(du -h "$output_dir/backend.tar" | cut -f1))"
  log "  - frontend.tar ($(du -h "$output_dir/frontend.tar" | cut -f1))"
  log "  - database.sql ($(du -h "$output_dir/database.sql" | cut -f1))"
  log "  - db-config.env"
  log "  - docker-compose.yml"
  log "  - .env.example"
  log "  - TROUBLESHOOTING.txt"
  log ""
  log "Next steps:"
  log "  1. Copy $output_dir to target host"
  log "  2. Run: ./scripts/transfer-images.sh import $output_dir"
  log ""
  log "See README.txt for import instructions and TROUBLESHOOTING.txt for common issues."
}

import_app() {
  local input_dir="$1"

  if [[ -z "$input_dir" || ! -d "$input_dir" ]]; then
    error "Input directory not found: $input_dir"
  fi

  input_dir="$(cd "$input_dir" && pwd)"
  cd "$PROJECT_ROOT"

  log "Importing BlueStick from: $input_dir"

  # Verify required files
  local required_files=(backend.tar frontend.tar database.sql db-config.env)
  for file in "${required_files[@]}"; do
    if [[ ! -f "$input_dir/$file" ]]; then
      error "Required file missing: $file"
    fi
  done

  # Load database config
  source "$input_dir/db-config.env"

  # Import images and tag them
  log "Loading backend image..."
  docker load -i "$input_dir/backend.tar"

  log "Loading frontend image..."
  docker load -i "$input_dir/frontend.tar"

  # Re-tag images if tag metadata exists
  if [[ -f "$input_dir/backend-tag.txt" && -f "$input_dir/frontend-tag.txt" ]]; then
    local backend_tag frontend_tag
    backend_tag=$(cat "$input_dir/backend-tag.txt")
    frontend_tag=$(cat "$input_dir/frontend-tag.txt")

    log "Tagging images for docker compose..."
    # Tag with the original names if they're not the standard ones
    if [[ "$backend_tag" != "networkmapper-backend:latest" ]]; then
      docker tag "$backend_tag" networkmapper-backend:latest 2>/dev/null || log "Backend image already tagged"
    fi
    if [[ "$frontend_tag" != "networkmapper-frontend:latest" ]]; then
      docker tag "$frontend_tag" networkmapper-frontend:latest 2>/dev/null || log "Frontend image already tagged"
    fi
  fi

  # Start database to restore data
  log "Starting database container..."
  export POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD
  $COMPOSE_CMD up -d db

  # Wait for database to be ready
  log "Waiting for database to be ready..."
  for i in {1..30}; do
    if $COMPOSE_CMD exec -T db pg_isready -U "$POSTGRES_USER" >/dev/null 2>&1; then
      break
    fi
    if [[ $i -eq 30 ]]; then
      error "Database failed to start"
    fi
    sleep 1
  done

  log "Restoring database..."
  $COMPOSE_CMD exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" < "$input_dir/database.sql"

  log ""
  log "✓ Import complete!"
  log ""
  log "Database restored with all data preserved."
  log ""
  log "Next steps:"
  log "  1. Update configuration files (.env or docker-compose.yml)"
  log "  2. Start application: $COMPOSE_CMD up -d"
  log "  3. Verify: https://localhost:3000"
}

main() {
  check_dependencies

  if [[ $# -lt 1 ]]; then
    usage
    exit 1
  fi

  case "$1" in
    export)
      export_app "${2:-}"
      ;;
    import)
      import_app "${2:-}"
      ;;
    -h|--help|help)
      usage
      ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
