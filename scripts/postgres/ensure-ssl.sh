#!/usr/bin/env bash
# This script ensures PostgreSQL SSL is configured
# Run this after database startup to enable SSL

set -euo pipefail

echo "[ensure-ssl] Configuring PostgreSQL SSL..."

# Check if certificates are available
if [[ ! -f "/certs/networkmapper.crt" ]] || [[ ! -f "/certs/networkmapper.key" ]]; then
    echo "[ensure-ssl] SSL certificates not found at /certs/, skipping SSL configuration"
    exit 0
fi

# Copy certificates to PostgreSQL data directory
echo "[ensure-ssl] Installing SSL certificates..."
cp /certs/networkmapper.crt /var/lib/postgresql/data/server.crt
chmod 644 /var/lib/postgresql/data/server.crt

cp /certs/networkmapper.key /var/lib/postgresql/data/server.key
chmod 600 /var/lib/postgresql/data/server.key

chown postgres:postgres /var/lib/postgresql/data/server.crt /var/lib/postgresql/data/server.key 2>/dev/null || true

# Enable SSL in PostgreSQL
echo "[ensure-ssl] Enabling SSL in PostgreSQL configuration..."
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "ALTER SYSTEM SET ssl = 'on';" 2>/dev/null || true
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "ALTER SYSTEM SET ssl_cert_file = 'server.crt';" 2>/dev/null || true
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "ALTER SYSTEM SET ssl_key_file = 'server.key';" 2>/dev/null || true

# Reload PostgreSQL configuration
psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT pg_reload_conf();" 2>/dev/null || true

echo "[ensure-ssl] SSL configuration complete"
