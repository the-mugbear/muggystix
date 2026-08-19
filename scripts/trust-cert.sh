#!/usr/bin/env bash
#
# Trust this deployment's TLS certificate in your MCP client — without turning
# certificate verification off, and without moving anything to plain HTTP.
#
# BlueStick is self-hosted on a private address, so it will never have a
# certificate from a public CA. Every MCP client therefore refuses the
# connection until it is told to trust THIS certificate. What to set differs by
# client, which is the whole reason this script exists:
#
#   * VS Code Copilot and Claude Code are Node/Electron. Node ignores the OS
#     trust store, so installing the cert system-wide does nothing for them;
#     they read NODE_EXTRA_CA_CERTS, which takes a single PEM file.
#   * Codex is a Rust binary built against native-tls (OpenSSL). It ignores
#     NODE_EXTRA_CA_CERTS entirely — advice this project shipped for a while and
#     which could only ever fail. It reads SSL_CERT_DIR, which takes a
#     *directory of hash-named symlinks*, not a file. (SSL_CERT_FILE was tested
#     against codex 0.147.0 and did not take effect; SSL_CERT_DIR did.)
#
# Both mechanisms ADD this certificate to the client's existing trust, verified
# against codex 0.147.0: a client pinned this way still validates every public
# host normally. That is the difference between this and
# NODE_TLS_REJECT_UNAUTHORIZED=0 / --insecure, which switch verification off for
# everything the process talks to.
#
# Usage:
#   ./scripts/trust-cert.sh                      # cert from this checkout
#   ./scripts/trust-cert.sh --url https://HOST   # fetch it from a deployment
#
# The --url form exists because the client and the deployment are often not the
# same machine, and the operator running the client has no reason to have this
# repository. The script itself is downloadable from the deployment:
#
#   curl -sk https://HOST/api/v1/references/trust-cert-script -o trust-cert.sh
#   less trust-cert.sh          # it installs a trust anchor — read it first
#   bash trust-cert.sh --url https://HOST
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd || echo "")"
TRUST_DIR="${BLUESTICK_TRUST_DIR:-$HOME/.bluestick}"
CERT_DEST="$TRUST_DIR/bluestick.pem"
HASH_DIR="$TRUST_DIR/certs.d"
BLUESTICK_URL=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --url)
            BLUESTICK_URL="${2:-}"
            [[ -n "$BLUESTICK_URL" ]] || { echo "--url needs a value, e.g. --url https://10.0.0.5" >&2; exit 2; }
            shift 2
            ;;
        -h|--help)
            sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown argument: $1 (try --help)" >&2
            exit 2
            ;;
    esac
done

CERT_SRC="${BLUESTICK_CERT:-}"
FETCHED_TMP=""

if [[ -z "$CERT_SRC" && -n "$BLUESTICK_URL" ]]; then
    # Fetch over the very connection we are about to start trusting: that is
    # trust-on-first-use, and the script says so rather than pretending the
    # download was verified. The fingerprint printed at the end is what makes it
    # checkable — compare it against the one shown on the deployment's
    # /reference/mcp page (or read off the host directly) before relying on it.
    FETCHED_TMP="$(mktemp)"
    trap 'rm -f "$FETCHED_TMP"' EXIT
    echo "Fetching certificate from ${BLUESTICK_URL%/}/api/v1/references/tls-certificate"
    if ! curl -fsSk "${BLUESTICK_URL%/}/api/v1/references/tls-certificate" -o "$FETCHED_TMP"; then
        echo "Could not fetch the certificate from $BLUESTICK_URL" >&2
        exit 1
    fi
    CERT_SRC="$FETCHED_TMP"
elif [[ -z "$CERT_SRC" ]]; then
    CERT_SRC="${REPO_ROOT:+$REPO_ROOT/ssl/certs/networkmapper.crt}"
fi

if [[ -z "$CERT_SRC" || ! -f "$CERT_SRC" ]]; then
    cat >&2 <<EOF
No certificate to install.

Run this on the machine hosting BlueStick (it reads ssl/certs/networkmapper.crt
from the checkout), or point it at the deployment:

    $0 --url https://<bluestick-host>

or supply the file yourself:

    BLUESTICK_CERT=/path/to/bluestick.pem $0
EOF
    exit 1
fi

# Sanity-check before installing anything: a corrupt or truncated file trusted
# silently is worse than one that fails loudly here.
if ! openssl x509 -in "$CERT_SRC" -noout >/dev/null 2>&1; then
    echo "Not a valid PEM certificate: $CERT_SRC" >&2
    exit 1
fi

mkdir -p "$HASH_DIR"
cp "$CERT_SRC" "$CERT_DEST"
chmod 644 "$CERT_DEST"

# OpenSSL looks up trust anchors in SSL_CERT_DIR by subject-name hash, so the
# directory needs a <hash>.0 symlink — a plain copy in there is never found.
CERT_HASH="$(openssl x509 -hash -noout -in "$CERT_DEST")"
cp "$CERT_DEST" "$HASH_DIR/bluestick.pem"
ln -sf bluestick.pem "$HASH_DIR/$CERT_HASH.0"

SUBJECT="$(openssl x509 -in "$CERT_DEST" -noout -subject | sed 's/^subject=//')"
EXPIRES="$(openssl x509 -in "$CERT_DEST" -noout -enddate | sed 's/^notAfter=//')"
# The check that makes a fetched certificate trustworthy: compare this against
# the fingerprint shown on the deployment's /reference/mcp page. Installing a
# trust anchor without ever comparing it is where trust-on-first-use turns into
# trusting whatever answered.
FINGERPRINT="$(openssl x509 -in "$CERT_DEST" -noout -fingerprint -sha256 | sed 's/^.*=//')"

cat <<EOF

Installed this deployment's certificate:
  subject : $SUBJECT
  expires : $EXPIRES
  sha256  : $FINGERPRINT
  pem     : $CERT_DEST
  dir     : $HASH_DIR  ($CERT_HASH.0)

Compare that sha256 against the one on the deployment's /reference/mcp page
before you rely on this — especially if the certificate was downloaded.

Add these to your shell profile (~/.bashrc, ~/.zshrc):

    export NODE_EXTRA_CA_CERTS="$CERT_DEST"   # VS Code Copilot, Claude Code
    export SSL_CERT_DIR="$HASH_DIR"           # Codex

Both are read when the client process STARTS, so restart the client (or open a
new shell and relaunch it) — exporting them inside an already-running session
changes nothing for that session.

Verify:
    claude mcp list      # bluestick-* should report Connected
EOF
