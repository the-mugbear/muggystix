#!/usr/bin/env bash
# ------------------------------------------------------------------
# local-ca.sh — a local certificate authority for a BlueStick deployment.
#
# Replaces the self-signed certificate with one signed by YOUR root CA, so
# each analyst machine trusts one root, once, and BlueStick's certificate can
# be reissued (renewal, new address) without touching any client.  The guide
# is ca/README.md; read it first.
#
# Where each step runs:
#   check, root, server   an ADMIN workstation — never the BlueStick host (the
#                         root key must not live on the server it vouches for)
#   install               the BlueStick host, from its deployment folder
#   verify, trust-help    an analyst machine
#
# Usage:
#   ./ca/local-ca.sh check                    validate ca.conf, change nothing
#   ./ca/local-ca.sh root                     create the root CA (once)
#   ./ca/local-ca.sh server                   issue BlueStick's certificate
#   ./ca/local-ca.sh install DIR [--dry-run]  install DIR's certificate on this host
#   ./ca/local-ca.sh verify URL ROOT_CRT      check a deployment against the root
#   ./ca/local-ca.sh trust-help ROOT_CRT      print the per-OS / per-agent trust steps
#
# ca.conf (copied from ca.conf.example) must be edited first: every value that
# goes into the root is required, and placeholders are refused.
#
# LOCAL_CA_PASSFILE=<file> supplies the root key's passphrase non-interactively
# (automation and tests only — by default openssl prompts for it).
# ------------------------------------------------------------------
set -euo pipefail

CA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF="${LOCAL_CA_CONF:-$CA_DIR/ca.conf}"
OUT="$CA_DIR/out"
ROOT_DIR="$OUT/root"
SERVER_DIR="$OUT/server"
ROOT_KEY="$ROOT_DIR/rootCA.key"
ROOT_CRT="$ROOT_DIR/rootCA.crt"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLUE}  $*${NC}"; }
ok()    { echo -e "${GREEN}✔ $*${NC}"; }
warn()  { echo -e "${YELLOW}! $*${NC}"; }
fail()  { echo -e "${RED}✖ $*${NC}" >&2; exit 1; }
step()  { echo ""; echo -e "${BLUE}== $* ==${NC}"; }

confirm() {
    echo -n "$1 [y/N]: "
    local answer; read -r answer
    [[ "$answer" =~ ^[Yy] ]]
}

# openssl passphrase arguments for the root key: a file when LOCAL_CA_PASSFILE
# is set, otherwise none (openssl prompts).  Arrays, so a path may hold spaces.
PASS_OUT=()
PASS_IN=()
if [[ -n "${LOCAL_CA_PASSFILE:-}" ]]; then
    [[ -r "$LOCAL_CA_PASSFILE" ]] || { echo "LOCAL_CA_PASSFILE ($LOCAL_CA_PASSFILE) is not readable." >&2; exit 1; }
    PASS_OUT=(-pass "file:$LOCAL_CA_PASSFILE")
    PASS_IN=(-passin "file:$LOCAL_CA_PASSFILE")
fi

require_openssl() {
    command -v openssl >/dev/null || fail "openssl not found on PATH."
    local v; v="$(openssl version | awk '{print $2}')"
    case "$v" in
        0.*|1.0.*|1.1.0*) fail "OpenSSL $v is too old — 1.1.1 or newer is needed (-addext, name constraints)." ;;
    esac
}

# ------------------------------------------------------------------
# IPv4 helpers
# ------------------------------------------------------------------
ip_to_int() {
    local IFS=. a b c d
    read -r a b c d <<< "$1"
    echo $(( (a << 24) + (b << 16) + (c << 8) + d ))
}
int_to_ip() {
    local n=$1
    echo "$(( (n >> 24) & 255 )).$(( (n >> 16) & 255 )).$(( (n >> 8) & 255 )).$(( n & 255 ))"
}
valid_ipv4() {
    local ip=$1 part
    [[ "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] || return 1
    local IFS=.
    for part in $ip; do (( part <= 255 )) || return 1; done
}
prefix_mask() {  # prefix length -> mask as an integer
    local p=$1
    (( p == 0 )) && { echo 0; return; }
    echo $(( (0xFFFFFFFF << (32 - p)) & 0xFFFFFFFF ))
}
ip_in_cidr() {   # ip cidr -> 0 when inside
    local ip=$1 net=${2%/*} p=${2#*/}
    local mask; mask=$(prefix_mask "$p")
    (( ( $(ip_to_int "$ip") & mask ) == ( $(ip_to_int "$net") & mask ) ))
}
documentation_ip() {  # RFC 5737 documentation ranges: never a real deployment
    [[ "$1" == 192.0.2.* || "$1" == 198.51.100.* || "$1" == 203.0.113.* ]]
}

# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------
load_conf() {
    if [[ ! -f "$CONF" ]]; then
        fail "No configuration at $CONF.
   Copy the template and set every value first:
     cp '$CA_DIR/ca.conf.example' '$CONF'
   See ca/README.md, step 1."
    fi
    # shellcheck disable=SC1090
    source "$CONF"
    validate_conf
}

# A server name outside the root's name constraint: say which domains are
# permitted and what would work, both ways round — a name inside them, or the
# domain that would cover this name (its parent, or the name itself when the
# parent is a bare TLD such as "home"). A guess-and-retry loop otherwise.
outside_domains_message() {
    local name=$1 first=${PERMITTED_DNS_DOMAINS%% *} parent=${1#*.}
    local cover=$name
    [[ "$parent" == *.* ]] && cover=$parent
    local msg="SERVER_DNS_NAMES: '$name' is outside PERMITTED_DNS_DOMAINS (${PERMITTED_DNS_DOMAINS:-none}) — clients would reject the certificate."
    if [[ -n "$first" && "$first" != *CHANGE_ME* ]]; then
        msg+=$'\n'"       Either name the server inside it: '$first'"
        local host=${name%%.*}
        [[ "$host" != "${first%%.*}" ]] && msg+=" or '$host.$first'"
        msg+=","
    else
        msg+=$'\n'"       Either"
    fi
    msg+=$'\n'"       or keep '$name' and set PERMITTED_DNS_DOMAINS=\"$cover\" (the constraint is in the root: changing it after \`root\` means a new root)."
    printf '%s' "$msg"
}

validate_conf() {
    local problems=() v
    local required=(ROOT_COMMON_NAME ROOT_ORGANIZATION ROOT_VALIDITY_DAYS PERMITTED_IPV4_CIDRS
                    PERMITTED_DNS_DOMAINS SERVER_DNS_NAMES SERVER_IPS SERVER_VALIDITY_DAYS)
    for v in "${required[@]}"; do
        local value="${!v:-}"
        if [[ -z "$value" ]]; then
            problems+=("$v is empty — it is required")
        elif [[ "$value" == *CHANGE_ME* ]]; then
            problems+=("$v still says CHANGE_ME — set your own value")
        elif [[ "$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')" == *example* ]]; then
            problems+=("$v uses an example value ('$value') — set your own")
        fi
    done

    local name_re='^[A-Za-z0-9 .,_()-]+$'
    [[ -n "${ROOT_COMMON_NAME:-}" && ! "$ROOT_COMMON_NAME" =~ $name_re ]] && \
        problems+=("ROOT_COMMON_NAME may contain letters, digits, spaces and . , - _ ( ) only")
    [[ -n "${ROOT_ORGANIZATION:-}" && ! "$ROOT_ORGANIZATION" =~ $name_re ]] && \
        problems+=("ROOT_ORGANIZATION may contain letters, digits, spaces and . , - _ ( ) only")

    if [[ -n "${ROOT_VALIDITY_DAYS:-}" ]]; then
        if ! [[ "$ROOT_VALIDITY_DAYS" =~ ^[0-9]+$ ]] || (( ROOT_VALIDITY_DAYS < 1 || ROOT_VALIDITY_DAYS > 3650 )); then
            problems+=("ROOT_VALIDITY_DAYS must be a number of days from 1 to 3650")
        fi
    fi
    if [[ -n "${SERVER_VALIDITY_DAYS:-}" ]]; then
        if ! [[ "$SERVER_VALIDITY_DAYS" =~ ^[0-9]+$ ]] || (( SERVER_VALIDITY_DAYS < 1 || SERVER_VALIDITY_DAYS > 397 )); then
            problems+=("SERVER_VALIDITY_DAYS must be 1-397 (browsers and macOS reject longer server certificates)")
        fi
    fi

    local cidr
    for cidr in ${PERMITTED_IPV4_CIDRS:-}; do
        [[ "$cidr" == *CHANGE_ME* ]] && continue
        if ! [[ "$cidr" == */* ]] || ! valid_ipv4 "${cidr%/*}" || ! [[ "${cidr#*/}" =~ ^[0-9]{1,2}$ ]] || (( ${cidr#*/} > 32 )); then
            problems+=("PERMITTED_IPV4_CIDRS: '$cidr' is not an IPv4 network in CIDR form (e.g. 10.20.0.0/16)")
            continue
        fi
        local net=${cidr%/*} p=${cidr#*/}
        if (( p < 8 )); then
            problems+=("PERMITTED_IPV4_CIDRS: '$cidr' is broader than /8 — name the networks the deployment really uses")
        fi
        local aligned; aligned=$(int_to_ip $(( $(ip_to_int "$net") & $(prefix_mask "$p") )))
        [[ "$aligned" != "$net" ]] && problems+=("PERMITTED_IPV4_CIDRS: '$cidr' has host bits set — the network is $aligned/$p")
        documentation_ip "$net" && problems+=("PERMITTED_IPV4_CIDRS: '$cidr' is a documentation-only range — set your real network")
    done

    local domain_re='^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$'
    local domain
    for domain in ${PERMITTED_DNS_DOMAINS:-}; do
        [[ "$domain" == *CHANGE_ME* ]] && continue
        [[ "$domain" == .* ]] && { problems+=("PERMITTED_DNS_DOMAINS: '$domain' — drop the leading dot (a domain already covers its subdomains)"); continue; }
        [[ "$domain" =~ $domain_re ]] || problems+=("PERMITTED_DNS_DOMAINS: '$domain' is not a lower-case DNS domain")
    done

    local name matched
    for name in ${SERVER_DNS_NAMES:-}; do
        [[ "$name" == *CHANGE_ME* ]] && continue
        [[ "$name" =~ $domain_re ]] || { problems+=("SERVER_DNS_NAMES: '$name' is not a lower-case DNS name"); continue; }
        matched=0
        for domain in ${PERMITTED_DNS_DOMAINS:-}; do
            [[ "$name" == "$domain" || "$name" == *".$domain" ]] && matched=1
        done
        (( matched )) || problems+=("$(outside_domains_message "$name")")
    done

    local ip
    for ip in ${SERVER_IPS:-}; do
        [[ "$ip" == *CHANGE_ME* ]] && continue
        valid_ipv4 "$ip" || { problems+=("SERVER_IPS: '$ip' is not an IPv4 address"); continue; }
        documentation_ip "$ip" && problems+=("SERVER_IPS: '$ip' is a documentation-only address — set the real one")
        matched=0
        for cidr in ${PERMITTED_IPV4_CIDRS:-}; do
            [[ "$cidr" == */* ]] && valid_ipv4 "${cidr%/*}" && ip_in_cidr "$ip" "$cidr" && matched=1
        done
        (( matched )) || problems+=("SERVER_IPS: '$ip' is outside PERMITTED_IPV4_CIDRS (${PERMITTED_IPV4_CIDRS:-none}) — clients would reject the certificate.
       Use an address inside those networks, or add a network that contains $ip to PERMITTED_IPV4_CIDRS.")
    done

    if (( ${#problems[@]} )); then
        echo -e "${RED}✖ $CONF is not ready:${NC}" >&2
        printf '   - %s\n' "${problems[@]}" >&2
        echo "   Every root value is required and must be yours — see ca/README.md, step 1." >&2
        exit 1
    fi
}

name_constraints() {
    local nc="critical" cidr domain
    for cidr in $PERMITTED_IPV4_CIDRS; do
        nc+=",permitted;IP:${cidr%/*}/$(int_to_ip "$(prefix_mask "${cidr#*/}")")"
    done
    for domain in $PERMITTED_DNS_DOMAINS; do
        nc+=",permitted;DNS:$domain"
    done
    echo "$nc"
}

server_san() {
    local san="" name ip
    for name in $SERVER_DNS_NAMES; do san+="${san:+,}DNS:$name"; done
    for ip in $SERVER_IPS; do san+="${san:+,}IP:$ip"; done
    echo "$san"
}

show_conf() {
    info "Root:         CN=$ROOT_COMMON_NAME, O=$ROOT_ORGANIZATION, $ROOT_VALIDITY_DAYS days"
    info "Constrained:  IPv4 $PERMITTED_IPV4_CIDRS · DNS $PERMITTED_DNS_DOMAINS"
    info "Server cert:  $(server_san), $SERVER_VALIDITY_DAYS days"
}

fingerprint() { openssl x509 -in "$1" -noout -fingerprint -sha256 | cut -d= -f2; }

# ------------------------------------------------------------------
# Commands
# ------------------------------------------------------------------
cmd_check() {
    require_openssl
    load_conf
    ok "$CONF is complete."
    show_conf
    info "Name constraints: $(name_constraints)"
}

looks_like_deployment() {
    [[ -f "$CA_DIR/../.env" && -f "$CA_DIR/../ssl/certs/networkmapper.crt" ]]
}

cmd_root() {
    require_openssl
    load_conf
    step "Create the root CA"
    [[ -e "$ROOT_KEY" ]] && fail "A root CA already exists at $ROOT_DIR — refusing to overwrite it.
   Every analyst machine trusts THAT root; replacing it means re-installing
   the new one everywhere.  To rotate deliberately, move $ROOT_DIR aside first
   (ca/README.md, 'Replacing the root')."
    if looks_like_deployment; then
        warn "This looks like a BlueStick deployment folder (.env and ssl/certs are here)."
        warn "The root key should be created and kept on an admin workstation, not on the server it vouches for."
        confirm "Create the root CA here anyway?" || fail "Stopped. Copy the ca/ folder to an admin workstation and run it there."
    fi
    show_conf
    echo ""
    info "You will be asked for a passphrase for the root key.  Store it separately"
    info "from the key; without it no certificate can be issued or renewed."
    umask 077
    mkdir -p "$ROOT_DIR"
    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:4096 -aes-256-cbc \
        ${PASS_OUT[@]+"${PASS_OUT[@]}"} -out "$ROOT_KEY"
    openssl req -x509 -new -key "$ROOT_KEY" ${PASS_IN[@]+"${PASS_IN[@]}"} -sha256 -days "$ROOT_VALIDITY_DAYS" \
        -subj "/CN=$ROOT_COMMON_NAME/O=$ROOT_ORGANIZATION" \
        -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -addext "subjectKeyIdentifier=hash" \
        -addext "nameConstraints=$(name_constraints)" \
        -out "$ROOT_CRT"
    chmod 600 "$ROOT_KEY"; chmod 644 "$ROOT_CRT"
    ok "Root CA created"
    info "Certificate (give this to analysts):  $ROOT_CRT"
    info "Private key (keep offline, back up):  $ROOT_KEY"
    info "SHA-256 fingerprint: $(fingerprint "$ROOT_CRT")"
    echo ""
    info "Next: ./ca/local-ca.sh server"
}

cmd_server() {
    require_openssl
    load_conf
    step "Issue BlueStick's server certificate"
    [[ -f "$ROOT_KEY" && -f "$ROOT_CRT" ]] || fail "No root CA in $ROOT_DIR — run ./ca/local-ca.sh root first."
    show_conf
    umask 077
    if [[ -f "$SERVER_DIR/networkmapper.crt" ]]; then
        local previous; previous="$SERVER_DIR/previous-$(date -u +%Y%m%dT%H%M%SZ)"
        mkdir -p "$previous"
        mv "$SERVER_DIR"/networkmapper.* "$previous"/ 2>/dev/null || true
        rm -f "$SERVER_DIR/rootCA.crt"
        info "The previous certificate was moved to $previous"
    fi
    mkdir -p "$SERVER_DIR"
    local key="$SERVER_DIR/networkmapper.key" crt="$SERVER_DIR/networkmapper.crt"
    local csr ext; csr="$(mktemp)"; ext="$(mktemp)"
    trap 'rm -f "$csr" "$ext"' RETURN
    local cn; cn="$(echo "$SERVER_DNS_NAMES" | awk '{print $1}')"
    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$key"
    openssl req -new -key "$key" -subj "/CN=$cn" -out "$csr"
    cat > "$ext" <<EOF
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectKeyIdentifier=hash
authorityKeyIdentifier=keyid
subjectAltName=$(server_san)
EOF
    info "Signing — you will be asked for the root key's passphrase."
    openssl x509 -req -in "$csr" -CA "$ROOT_CRT" -CAkey "$ROOT_KEY" ${PASS_IN[@]+"${PASS_IN[@]}"} \
        -CAserial "$ROOT_DIR/rootCA.srl" -CAcreateserial \
        -days "$SERVER_VALIDITY_DAYS" -sha256 -extfile "$ext" -out "$crt"
    chmod 600 "$key"; chmod 644 "$crt"
    # openssl verify applies the root's name constraints: a certificate that
    # clients would reject fails here instead of in front of an analyst.
    openssl verify -CAfile "$ROOT_CRT" "$crt" >/dev/null || fail "The new certificate does not verify against the root."
    install -m 644 "$ROOT_CRT" "$SERVER_DIR/rootCA.crt"
    ok "Server certificate issued and verified against the root"
    info "Valid until: $(openssl x509 -in "$crt" -noout -enddate | cut -d= -f2)"
    info "Copy this folder to the BlueStick host (it holds the server key — not the root key):"
    info "  $SERVER_DIR/networkmapper.crt, networkmapper.key, rootCA.crt"
    echo ""
    info "e.g.  scp -r '$SERVER_DIR' admin@<bluestick-host>:/tmp/bluestick-cert"
    info "then, on the host, from the deployment folder:"
    info "  ./ca/local-ca.sh install /tmp/bluestick-cert --dry-run"
    info "  ./ca/local-ca.sh install /tmp/bluestick-cert"
}

env_get() {
    local file="$1" key="$2"
    [[ -f "$file" ]] || return 0
    { grep -E "^[[:space:]]*${key}=" "$file" || true; } | tail -n 1 | sed -E "s/^[[:space:]]*${key}=//; s/^[\"']//; s/[\"']\$//"
}

# The deployment folder install writes into: BLUESTICK_DIR when set, else the
# current directory (the README says to cd there), else the folder above this
# script.  The script's own location used to be the only candidate, so a copy
# of ca/ outside the deployment, or a checkout elsewhere, always failed.
deployment_dir() {
    local candidates=() c missing report=""
    if [[ -n "${BLUESTICK_DIR:-}" ]]; then
        candidates=("$BLUESTICK_DIR")
    else
        candidates=("$PWD" "$CA_DIR/..")
    fi
    for c in "${candidates[@]}"; do
        c="$(cd "$c" 2>/dev/null && pwd)" || { report+="
   $c: does not exist"; continue; }
        missing=()
        [[ -f "$c/docker-compose.yml" ]] || missing+=("docker-compose.yml")
        [[ -f "$c/.env" ]] || missing+=(".env")
        [[ -d "$c/ssl/certs" ]] || missing+=("ssl/certs/")
        if (( ${#missing[@]} == 0 )); then
            echo "$c"
            return 0
        fi
        report+="
   $c: no ${missing[*]}"
    done
    fail "No BlueStick deployment folder found (it needs docker-compose.yml, .env and ssl/certs/):$report
   cd into the deployment folder first, or set BLUESTICK_DIR=/path/to/deployment."
}

cmd_install() {
    local src="${1:-}" dry=0
    [[ "${2:-}" == "--dry-run" || "${1:-}" == "--dry-run" ]] && dry=1
    [[ "$src" == "--dry-run" ]] && src="${2:-}"
    [[ -n "$src" ]] || fail "Usage: ./ca/local-ca.sh install DIR [--dry-run]   (DIR holds networkmapper.crt, networkmapper.key, rootCA.crt)"
    require_openssl

    local root_dir; root_dir="$(deployment_dir)"
    step "Install on this BlueStick host ($root_dir)"
    local crt="$src/networkmapper.crt" key="$src/networkmapper.key" ca="$src/rootCA.crt"
    local f
    for f in "$crt" "$key" "$ca"; do [[ -f "$f" ]] || fail "Missing $f"; done
    [[ -e "$src/rootCA.key" ]] && fail "$src contains rootCA.key — the ROOT key must never be copied to the server.
   Delete it from this host (shred -u '$src/rootCA.key') and copy only out/server/."

    # The pair belongs together, a CA signed it, and that CA is the one given.
    [[ "$(openssl x509 -in "$crt" -noout -pubkey)" == "$(openssl pkey -in "$key" -pubout)" ]] \
        || fail "networkmapper.key does not belong to networkmapper.crt."
    [[ "$(openssl x509 -in "$crt" -noout -issuer)" != "issuer=$(openssl x509 -in "$crt" -noout -subject | sed 's/^subject=//')" ]] \
        || fail "networkmapper.crt is self-signed — issue it with ./ca/local-ca.sh server."
    openssl verify -CAfile "$ca" "$crt" >/dev/null 2>&1 || fail "networkmapper.crt does not verify against $ca (wrong root, or outside its name constraints)."
    openssl x509 -in "$crt" -noout -checkend 0 >/dev/null || fail "networkmapper.crt has expired."
    openssl x509 -in "$crt" -noout -checkend $((30 * 86400)) >/dev/null || warn "networkmapper.crt expires within 30 days — plan a renewal."
    ok "Certificate and key match, and verify against the root"

    # Clients reach BlueStick at HOST_IP (the connect instructions and CORS are
    # built from it), so the certificate must name that address.
    local host_ip; host_ip="$(env_get "$root_dir/.env" HOST_IP)"
    [[ -n "$host_ip" ]] || fail "No HOST_IP in $root_dir/.env."
    local san; san="$(openssl x509 -in "$crt" -noout -ext subjectAltName 2>/dev/null | tail -n +2 | tr -d ' ')"
    [[ ",$san," == *",IPAddress:$host_ip,"* ]] || fail "The certificate does not name HOST_IP $host_ip (it names: $san).
   Add it to SERVER_IPS in ca.conf and re-run ./ca/local-ca.sh server."
    ok "The certificate names HOST_IP $host_ip"

    if (( dry )); then
        ok "Dry run: everything checks out.  Nothing was changed."
        info "Run again without --dry-run to install and restart the web and API containers."
        return 0
    fi

    local backup; backup="$root_dir/ssl/certs.backup-$(date -u +%Y%m%dT%H%M%SZ)"
    mkdir -p "$backup"
    cp -p "$root_dir"/ssl/certs/networkmapper.* "$backup"/ 2>/dev/null || true
    [[ -f "$root_dir/ssl/certs/openssl.conf" ]] && cp -p "$root_dir/ssl/certs/openssl.conf" "$backup"/
    ok "Previous certificate backed up to $backup"

    install -m 644 "$crt" "$root_dir/ssl/certs/networkmapper.crt"
    install -m 600 "$key" "$root_dir/ssl/certs/networkmapper.key"
    # The self-signed generator's config describes a certificate that is gone.
    rm -f "$root_dir/ssl/certs/openssl.conf"
    ok "Installed into $root_dir/ssl/certs"

    local dc="docker compose"; docker compose version >/dev/null 2>&1 || dc="docker-compose"
    info "Restarting the web (nginx) and API containers to load it…"
    if ! (cd "$root_dir" && $dc restart frontend backend >/dev/null); then
        warn "The restart failed — the certificate is installed but not loaded yet."
        warn "Run it yourself: (cd '$root_dir' && $dc restart frontend backend)"
    fi

    local served="" i
    for i in $(seq 1 30); do
        served="$(openssl s_client -connect "$host_ip:443" -servername "$host_ip" </dev/null 2>/dev/null \
            | openssl x509 -noout -fingerprint -sha256 2>/dev/null | cut -d= -f2 || true)"
        [[ -n "$served" ]] && break
        sleep 2
    done
    if [[ "$served" == "$(fingerprint "$crt")" ]]; then
        ok "https://$host_ip is serving the new certificate"
    else
        warn "Could not confirm https://$host_ip serves the new certificate (got '${served:-nothing}')."
        warn "Check: $dc ps; $dc logs frontend"
    fi
    echo ""
    info "Now: shred -u '$key'   (the copy you brought over; the installed one is in ssl/certs)"
    info "Rollback: cp -p '$backup'/* '$root_dir/ssl/certs/' && (cd '$root_dir' && $dc restart frontend backend)"
    info "To change address later: reissue naming both addresses first; deploy.sh option 3 keeps a"
    info "CA-issued certificate only when it names the new address (ca/README.md, 'New address')."
    info "Analyst machines: ./ca/local-ca.sh trust-help rootCA.crt"
}

cmd_verify() {
    local url="${1:-}" ca="${2:-}"
    [[ -n "$url" && -n "$ca" ]] || fail "Usage: ./ca/local-ca.sh verify https://HOST[:PORT] ROOT_CRT"
    [[ -f "$ca" ]] || fail "$ca not found."
    require_openssl
    local hostport="${url#https://}"; hostport="${hostport%%/*}"
    local host="${hostport%:*}" port=443
    [[ "$hostport" == *:* ]] && port="${hostport##*:}"
    local check=(-verify_hostname "$host")
    valid_ipv4 "$host" && check=(-verify_ip "$host")
    local result
    result="$(openssl s_client -connect "$host:$port" -servername "$host" -CAfile "$ca" \
        -verify_return_error "${check[@]}" </dev/null 2>&1 || true)"
    if grep -q "Verify return code: 0 (ok)" <<< "$result"; then
        ok "$url presents a certificate issued by this root, valid for $host"
    else
        echo "$result" | grep -E "verify error|Verify return code|error" | head -5 >&2
        fail "$url did not verify against $ca."
    fi
}

cmd_trust_help() {
    local ca="${1:-rootCA.crt}"
    local path; path="$(cd "$(dirname "$ca")" 2>/dev/null && pwd)/$(basename "$ca")"
    [[ -f "$ca" ]] && info "Root: $path  (SHA-256 $(fingerprint "$ca"))" || warn "$ca not found here; the commands below assume it."
    cat <<EOF

Compare the fingerprint above with the one the administrator gave you before
trusting it.  Then, once per analyst machine:

  1. The system trust store (curl, Go and most Rust tools, browsers)
     Linux (Debian/Ubuntu/Kali):
       sudo cp '$path' /usr/local/share/ca-certificates/bluestick-root.crt
       sudo update-ca-certificates
     Linux (RHEL/Fedora):
       sudo cp '$path' /etc/pki/ca-trust/source/anchors/bluestick-root.crt
       sudo update-ca-trust
     macOS:
       sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain '$path'
     Windows (elevated PowerShell):
       certutil -addstore -f Root rootCA.crt
     Firefox keeps its own store: Settings > Privacy & Security > Certificates >
     View Certificates > Authorities > Import.

  2. Agents that ignore the system store — add to your shell profile:
       export NODE_EXTRA_CA_CERTS='$path'                        # Claude Code, VS Code (Node)
       export SSL_CERT_DIR=/etc/ssl/certs                         # Codex (after step 1, Linux)
       export REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt   # Python tools
     If the retired trust-cert.sh set these before, point them here: the old
     self-signed certificate is no longer served.

  3. Check:
       ./ca/local-ca.sh verify https://<bluestick-host> '$path'
       curl https://<bluestick-host>/health          # no -k
EOF
}

case "${1:-help}" in
    check)       cmd_check ;;
    root)        cmd_root ;;
    server)      cmd_server ;;
    install)     shift; cmd_install "$@" ;;
    verify)      shift; cmd_verify "$@" ;;
    trust-help)  shift; cmd_trust_help "$@" ;;
    -h|--help|help) sed -n '2,31p' "$0" | sed 's/^# \{0,1\}//' ;;
    *) fail "Unknown command '$1' — see: ./ca/local-ca.sh help" ;;
esac
