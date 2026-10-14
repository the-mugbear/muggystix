#!/usr/bin/env bash
# The database container's entrypoint: give PostgreSQL its TLS certificate,
# then hand over to the image's own entrypoint.  Mounted by docker-compose.yml;
# not run by hand.
#
# Why it exists.  The certificate is BlueStick's (./ssl/certs, mounted at
# /certs) and its key is mode 600 and owned by whoever deployed — as it should
# be.  PostgreSQL runs as the `postgres` user, which cannot read that key.  The
# step this replaces (an initdb script, so it ran once, as `postgres`) found
# the key unreadable, logged one line and left TLS off for the life of the
# database: on every ordinary installation the backend's `sslmode=prefer`
# quietly meant "no TLS".
#
# This runs as root on EVERY start, before the image drops to `postgres`:
# it copies the pair to a folder only `postgres` can read and turns TLS on
# with command-line settings.  So a renewed certificate is picked up by a
# restart, and nothing is written into the data directory.
#
# TLS is turned on only for a pair PostgreSQL will accept — a certificate and
# a key that parse and belong together.  Anything else (no files, a folder
# Docker made where a file was missing, a key from another certificate) and
# the database starts WITHOUT TLS, saying so on three lines: a database that
# does not start takes the whole application down, which is worse than
# traffic in the clear on the compose network.  `deploy.sh` option 5 shows
# which it is.
set -euo pipefail

CERT_SOURCE=/certs/networkmapper.crt
KEY_SOURCE=/certs/networkmapper.key
TLS_DIR=/var/lib/postgresql/tls

say() { echo "[db-tls] $*" >&2; }

tls_off() {
    local why="$1"; shift
    say "PostgreSQL is starting WITHOUT TLS: $why"
    say "Connections from the application stay on the compose network, unencrypted."
    say "Fix the certificate in ssl/certs and restart the database to turn it on."
    exec docker-entrypoint.sh "$@"
}

# Only the server itself gets the settings: `docker compose run db psql …`
# and the like pass through untouched.
if [[ "${1:-}" != "postgres" ]]; then
    exec docker-entrypoint.sh "$@"
fi

reason=""
if [[ "$(id -u)" != "0" ]]; then
    reason="this container does not start as root, so the key cannot be handed to the postgres user"
elif [[ ! -f "$CERT_SOURCE" || ! -f "$KEY_SOURCE" ]]; then
    reason="ssl/certs/networkmapper.crt and .key are not both files"
elif ! cert_pub="$(openssl x509 -in "$CERT_SOURCE" -noout -pubkey 2>/dev/null)" || [[ -z "$cert_pub" ]]; then
    reason="ssl/certs/networkmapper.crt is not a certificate"
elif ! key_pub="$(openssl pkey -in "$KEY_SOURCE" -pubout 2>/dev/null)" || [[ -z "$key_pub" ]]; then
    reason="ssl/certs/networkmapper.key is not a private key (or is passphrase-protected)"
elif [[ "$cert_pub" != "$key_pub" ]]; then
    reason="ssl/certs/networkmapper.key does not belong to networkmapper.crt"
fi
if [[ -n "$reason" ]]; then
    tls_off "$reason" "$@"
fi

install -d -o postgres -g postgres -m 700 "$TLS_DIR"
install -o postgres -g postgres -m 644 "$CERT_SOURCE" "$TLS_DIR/server.crt"
install -o postgres -g postgres -m 600 "$KEY_SOURCE" "$TLS_DIR/server.key"
say "TLS is on (certificate: ssl/certs/networkmapper.crt)."

exec docker-entrypoint.sh "$@" \
    -c ssl=on \
    -c "ssl_cert_file=$TLS_DIR/server.crt" \
    -c "ssl_key_file=$TLS_DIR/server.key"
