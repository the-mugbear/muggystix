#!/usr/bin/env bash
# Alembic round-trip sanity check (v2.42.0).
#
# Boots a throwaway Postgres in Docker, runs ``alembic upgrade head``,
# walks back through every migration via ``alembic downgrade -1`` until
# we hit the baseline, then re-runs ``alembic upgrade head``.  Any step
# that errors out fails the script with a clear summary so an operator
# (or future CI) sees which revision's downgrade is broken.
#
# Each migration ships with a ``downgrade()`` body in this repo; this
# script is what ensures those bodies actually work before a release.
# Without it, a broken downgrade silently bitrots until the day someone
# tries to roll back in prod.
#
# Run with:  ./scripts/test-alembic-roundtrip.sh
# Or:        make alembic-roundtrip

set -euo pipefail

CONTAINER="${ALEMBIC_TEST_CONTAINER:-nm-alembic-roundtrip-pg}"
DB_USER="${ALEMBIC_TEST_USER:-nmaptest}"
DB_PASS="${ALEMBIC_TEST_PASS:-nmaptest}"
DB_NAME="${ALEMBIC_TEST_DB:-nmaptest}"
DB_PORT="${ALEMBIC_TEST_PORT:-55432}"
PG_IMAGE="${ALEMBIC_TEST_IMAGE:-postgres:16}"

cleanup() {
    docker rm -f "${CONTAINER}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[1/5] Starting throwaway Postgres (${PG_IMAGE}) on ${DB_PORT}…"
docker run -d --rm \
    --name "${CONTAINER}" \
    -e POSTGRES_USER="${DB_USER}" \
    -e POSTGRES_PASSWORD="${DB_PASS}" \
    -e POSTGRES_DB="${DB_NAME}" \
    -p "${DB_PORT}:5432" \
    "${PG_IMAGE}" >/dev/null

echo "[2/5] Waiting for Postgres to accept connections…"
for i in {1..30}; do
    if docker exec "${CONTAINER}" pg_isready -U "${DB_USER}" -d "${DB_NAME}" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

cd "$(dirname "$0")/../backend"

export DATABASE_URL="postgresql://${DB_USER}:${DB_PASS}@localhost:${DB_PORT}/${DB_NAME}"
# Provide just enough config for alembic env.py — it reads from settings.
export JWT_SECRET_KEY="${JWT_SECRET_KEY:-alembic-roundtrip-test-only}"
export CREDENTIAL_ENCRYPTION_KEY="${CREDENTIAL_ENCRYPTION_KEY:-alembic-roundtrip-test-only}"

echo "[3/5] alembic upgrade head (forward chain)…"
alembic upgrade head

echo "[4/5] Walking the chain backward via 'alembic downgrade -1'…"
# Step back one revision at a time until we hit the baseline.  If any
# downgrade fails, the trap fires + cleanup + exit 1.
while true; do
    current=$(alembic current 2>/dev/null | awk '/^[a-f0-9]/ {print $1; exit}')
    if [[ -z "${current}" ]]; then
        echo "  reached base (no current revision)"
        break
    fi
    echo "  downgrading from ${current}…"
    alembic downgrade -1
done

echo "[5/5] alembic upgrade head (forward chain again, to verify idempotency)…"
alembic upgrade head

echo
echo "✓ Alembic round-trip passed.  Every downgrade() inverted cleanly."
