#!/usr/bin/env bash
set -euo pipefail

CERT_SOURCE="/certs/networkmapper.crt"
KEY_SOURCE="/certs/networkmapper.key"
CERT_TARGET="$PGDATA/server.crt"
KEY_TARGET="$PGDATA/server.key"

if [[ -f "$CERT_SOURCE" && -f "$KEY_SOURCE" ]]; then
  if [[ ! -r "$CERT_SOURCE" || ! -r "$KEY_SOURCE" ]]; then
    echo "[init-ssl] SSL certificates exist but are not readable by postgres user; leaving SSL disabled" >&2
    exit 0
  fi

  echo "[init-ssl] Installing SSL certificates for Postgres"
  cp "$CERT_SOURCE" "$CERT_TARGET"
  chmod 644 "$CERT_TARGET"
  cp "$KEY_SOURCE" "$KEY_TARGET"
  chmod 600 "$KEY_TARGET"
  chown postgres:postgres "$CERT_TARGET" "$KEY_TARGET" 2>/dev/null || true

  if grep -q "^ssl\s*=" "$PGDATA/postgresql.conf"; then
    sed -i "s/^ssl\s*=.*/ssl = on/" "$PGDATA/postgresql.conf"
  else
    echo "ssl = on" >> "$PGDATA/postgresql.conf"
  fi

  if grep -q "^ssl_cert_file\s*=" "$PGDATA/postgresql.conf"; then
    sed -i "s#^ssl_cert_file\s*=.*#ssl_cert_file = 'server.crt'#" "$PGDATA/postgresql.conf"
  else
    echo "ssl_cert_file = 'server.crt'" >> "$PGDATA/postgresql.conf"
  fi

  if grep -q "^ssl_key_file\s*=" "$PGDATA/postgresql.conf"; then
    sed -i "s#^ssl_key_file\s*=.*#ssl_key_file = 'server.key'#" "$PGDATA/postgresql.conf"
  else
    echo "ssl_key_file = 'server.key'" >> "$PGDATA/postgresql.conf"
  fi
else
  echo "[init-ssl] SSL certificates not found; leaving Postgres SSL disabled" >&2
fi
