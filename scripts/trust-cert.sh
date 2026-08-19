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
# Usage:  ./scripts/trust-cert.sh [--print-only]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_SRC="${BLUESTICK_CERT:-$REPO_ROOT/ssl/certs/networkmapper.crt}"
TRUST_DIR="${BLUESTICK_TRUST_DIR:-$HOME/.bluestick}"
CERT_DEST="$TRUST_DIR/bluestick.pem"
HASH_DIR="$TRUST_DIR/certs.d"

if [[ ! -f "$CERT_SRC" ]]; then
    cat >&2 <<EOF
Certificate not found: $CERT_SRC

Run this on the machine hosting BlueStick, or fetch the certificate from the
deployment first and point BLUESTICK_CERT at it:

    curl -sk https://<bluestick-host>/api/v1/references/tls-certificate -o bluestick.pem
    BLUESTICK_CERT=\$PWD/bluestick.pem $0

(Fetching over the untrusted connection is trust-on-first-use. On a network you
do not control, copy ssl/certs/networkmapper.crt off the deployment host
instead.)
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

cat <<EOF

Installed this deployment's certificate:
  subject : $SUBJECT
  expires : $EXPIRES
  pem     : $CERT_DEST
  dir     : $HASH_DIR  ($CERT_HASH.0)

Add these to your shell profile (~/.bashrc, ~/.zshrc):

    export NODE_EXTRA_CA_CERTS="$CERT_DEST"   # VS Code Copilot, Claude Code
    export SSL_CERT_DIR="$HASH_DIR"           # Codex

Both are read when the client process STARTS, so restart the client (or open a
new shell and relaunch it) — exporting them inside an already-running session
changes nothing for that session.

Verify:
    claude mcp list      # bluestick-* should report Connected
EOF
