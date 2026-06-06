#!/usr/bin/env bash
# BlueStick recon preflight — scripts/preflight.sh (v2.13.3)
#
# Queries the local host for the reconnaissance tools referenced by
# BlueStick's agent workflow and prints installation guidance for
# any that are missing or misconfigured.
#
# Reputable-sources policy (hard rule):
#   - Install URLs point ONLY at the official upstream GitHub org /
#     vendor page, or at a distribution package manager (apt, brew,
#     cargo, pipx, go install).
#   - No third-party Docker images unless the maintainer publishes
#     one under the official org namespace.
#   - Commercial tools point at the vendor's own download page.
#
# Usage:
#   ./preflight.sh             Human-readable report (default)
#   ./preflight.sh --json      Machine-readable JSON (for agents)
#   ./preflight.sh --strict    Exit 1 if any recon-essential tool is
#                              missing.  Useful for CI / agent gates.
#   ./preflight.sh --help      Show this help.
#
# This script is also served at GET /api/v1/references/preflight-script
# on the BlueStick API so agents can curl it directly.

set -u

# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------
JSON_OUTPUT=false
STRICT=false
for arg in "$@"; do
  case "$arg" in
    --json)   JSON_OUTPUT=true ;;
    --strict) STRICT=true ;;
    -h|--help)
      sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "unknown flag: $arg" >&2
      echo "use --help for usage" >&2
      exit 2
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Output helpers — track one row per tool for end-of-run rendering.
# ---------------------------------------------------------------------------
declare -a RESULT_NAMES=()
declare -A RESULT_STATUS=()    # ok | warn | missing
declare -A RESULT_PATH=()
declare -A RESULT_VERSION=()
declare -A RESULT_ESSENTIAL=() # true | false
declare -A RESULT_ISSUE=()
declare -A RESULT_FIX=()
declare -A RESULT_HINTS=()     # newline-joined "provider=command" pairs
declare -A RESULT_HOMEPAGE=()
declare -A RESULT_DESC=()

record() {
  local name=$1 status=$2 path=$3 version=$4 essential=$5
  local issue=${6:-} fix=${7:-}
  RESULT_NAMES+=("$name")
  RESULT_STATUS[$name]=$status
  RESULT_PATH[$name]=$path
  RESULT_VERSION[$name]=$version
  RESULT_ESSENTIAL[$name]=$essential
  RESULT_ISSUE[$name]=$issue
  RESULT_FIX[$name]=$fix
}

set_meta() {
  local name=$1 desc=$2 homepage=$3
  RESULT_DESC[$name]=$desc
  RESULT_HOMEPAGE[$name]=$homepage
}

add_hints() {
  # Usage: add_hints tool "provider1=command1" "provider2=command2" ...
  local name=$1 ; shift
  local joined=""
  for pair in "$@"; do
    joined+="$pair"$'\n'
  done
  RESULT_HINTS[$name]=$joined
}

# ---------------------------------------------------------------------------
# Per-tool check helpers.  Each returns (via `record`) exactly one row.
# ---------------------------------------------------------------------------

# simple_check <name> <binary> <version_cmd> <essential>
# Binary present + version string — no deeper semantics.
simple_check() {
  local name=$1 binary=$2 version_cmd=$3 essential=$4
  local path
  if path=$(command -v "$binary" 2>/dev/null); then
    local version
    version=$(eval "$version_cmd" 2>&1 | head -1 | tr -d '\n' || echo "")
    record "$name" ok "$path" "$version" "$essential"
  else
    record "$name" missing "" "" "$essential"
  fi
}

check_masscan() {
  local path version
  if ! path=$(command -v masscan 2>/dev/null); then
    record masscan missing "" "" false
    return
  fi
  version=$(masscan --version 2>&1 | head -1 | tr -d '\n' || echo "")
  # Privilege check — masscan needs CAP_NET_RAW or sudo.
  local issue="" fix=""
  local have_priv=false
  if sudo -n true 2>/dev/null; then
    have_priv=true
  elif getcap "$path" 2>/dev/null | grep -q cap_net_raw; then
    have_priv=true
  fi
  if $have_priv; then
    record masscan ok "$path" "$version" false
  else
    issue="needs sudo (non-interactive) OR cap_net_raw=eip on the binary; neither present"
    fix="sudo setcap cap_net_raw=eip $path   # one-time; survives until the binary is replaced"
    record masscan warn "$path" "$version" false "$issue" "$fix"
  fi
}

check_httpx() {
  local path version
  if ! path=$(command -v httpx 2>/dev/null); then
    record httpx missing "" "" true
    return
  fi
  # ProjectDiscovery httpx identifies itself; the Python httpx CLI does not.
  local version_raw
  version_raw=$(httpx -version 2>&1 | head -20)
  if echo "$version_raw" | grep -qi projectdiscovery; then
    # Extract the version line (typically "Current httpx version vX.Y.Z ...")
    version=$(echo "$version_raw" | grep -i "httpx version" | head -1 | tr -d '\n')
    if [[ -z "$version" ]]; then
      version=$(echo "$version_raw" | tail -1 | tr -d '\n')
    fi
    record httpx ok "$path" "$version" true
  else
    local issue="Python httpx CLI shadows ProjectDiscovery httpx on PATH"
    local fix="Fetch the ProjectDiscovery binary: https://github.com/projectdiscovery/httpx/releases/latest (linux_amd64.zip), extract, and call by explicit path (e.g. /opt/httpx/httpx)"
    record httpx warn "$path" "Python httpx (wrong binary)" true "$issue" "$fix"
  fi
}

check_go_bin() {
  # Special handling for Go tools that are commonly installed at ~/go/bin/X
  # but not necessarily on PATH.  Checks PATH first, then ~/go/bin.
  local name=$1 binary=$2 version_cmd=$3 essential=$4
  local path version
  if path=$(command -v "$binary" 2>/dev/null); then
    version=$(eval "$version_cmd" 2>&1 | head -1 | tr -d '\n' || echo "")
    record "$name" ok "$path" "$version" "$essential"
  elif [[ -x "$HOME/go/bin/$binary" ]]; then
    path="$HOME/go/bin/$binary"
    version=$("$path" -version 2>&1 | head -1 | tr -d '\n' || echo "")
    local issue="installed at $path but not on PATH"
    local fix="export PATH=\"\$PATH:\$HOME/go/bin\"   # add to your shell profile"
    record "$name" warn "$path" "$version" "$essential" "$issue" "$fix"
  else
    record "$name" missing "" "" "$essential"
  fi
}

# ---------------------------------------------------------------------------
# Tool registry — name, description, homepage, install hints (official only)
# ---------------------------------------------------------------------------

# Order here drives output order.  Recon-essential tools are marked
# essential=true; support tools (curl, jq, etc.) are essential=true too
# because the agent workflow breaks without them.  Optional credentialed /
# specialized tools are essential=false.

# --- Core support ---
set_meta curl      "HTTP client for BlueStick API calls + release fetches"        "https://curl.se"
add_hints curl     "apt=sudo apt install curl" "brew=brew install curl"
simple_check curl curl "curl --version" true

set_meta jq        "JSON processor for parsing API responses + JSONL scans"            "https://jqlang.github.io/jq/"
add_hints jq       "apt=sudo apt install jq" "brew=brew install jq" "binary=https://github.com/jqlang/jq/releases/latest"
simple_check jq jq "jq --version" true

set_meta xmllint   "XML parsing for nmap / masscan / nessus output (libxml2-utils)"   "https://gitlab.gnome.org/GNOME/libxml2"
add_hints xmllint  "apt=sudo apt install libxml2-utils" "brew=brew install libxml2"
simple_check xmllint xmllint "xmllint --version" false

set_meta python3   "Python 3 interpreter — used by eyewitness, smbmap, gvm-tools, and ad-hoc XML parsing"  "https://www.python.org"
add_hints python3  "apt=sudo apt install python3 python3-pip python3-venv" "brew=brew install python@3.12"
simple_check python3 python3 "python3 --version" true

# --- Host discovery ---
set_meta nmap      "Canonical network scanner — service detection, NSE scripts, top-1000 probe"  "https://nmap.org"
add_hints nmap     "apt=sudo apt install nmap" "brew=brew install nmap" "binary=https://nmap.org/download.html" "source=https://github.com/nmap/nmap   # official mirror"
simple_check nmap nmap "nmap --version" true

set_meta masscan   "Fast parallel TCP SYN scanner for /20+ scopes"                     "https://github.com/robertdavidgraham/masscan"
add_hints masscan  "apt=sudo apt install masscan" "brew=brew install masscan" "source=git clone https://github.com/robertdavidgraham/masscan && cd masscan && make && sudo make install" "binary=https://github.com/robertdavidgraham/masscan/releases   # official releases" "privilege_fix=sudo setcap cap_net_raw=eip \$(which masscan)"
check_masscan

set_meta rustscan  "Async TCP connect scanner + nmap service-detect pipeline — non-privileged fallback"   "https://github.com/RustScan/RustScan"
add_hints rustscan "cargo=cargo install rustscan" "brew=brew install rustscan" "binary=https://github.com/RustScan/RustScan/releases/latest" "docker=docker run --rm rustscan/rustscan:latest -a <cidr>   # official image from RustScan org"
simple_check rustscan rustscan "rustscan --version" false

# --- Web fingerprinting ---
set_meta httpx     "ProjectDiscovery httpx — fast HTTP fingerprinter (status, title, tech, TLS, favicon)"  "https://github.com/projectdiscovery/httpx"
add_hints httpx    "go=go install github.com/projectdiscovery/httpx/cmd/httpx@latest" "binary=https://github.com/projectdiscovery/httpx/releases/latest   # prebuilt zip for linux/macOS/windows" "brew=brew install projectdiscovery/tap/httpx   # official tap" "docker=docker run --rm projectdiscovery/httpx:latest   # official image"
check_httpx

set_meta eyewitness "Screenshot + fingerprint pass for web services"                   "https://github.com/RedSiege/EyeWitness"
add_hints eyewitness "git=git clone https://github.com/RedSiege/EyeWitness && cd EyeWitness/Python/setup && sudo ./setup.sh" "apt=sudo apt install eyewitness   # Kali Linux only" "note=Red Siege is the official continuation; the old FortyNorthSecurity repo is archived.  Pull from RedSiege only."
simple_check eyewitness eyewitness "eyewitness --help 2>&1 | head -1" false

set_meta nikto     "Web misconfiguration / CVE scanner (intrusive)"                    "https://github.com/sullo/nikto"
add_hints nikto    "apt=sudo apt install nikto" "brew=brew install nikto" "git=git clone https://github.com/sullo/nikto   # sullo is the upstream author"
simple_check nikto nikto "nikto -Version 2>&1" false

# --- DNS enumeration ---
set_meta subfinder "ProjectDiscovery subfinder — passive subdomain discovery"          "https://github.com/projectdiscovery/subfinder"
add_hints subfinder "go=go install github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest" "binary=https://github.com/projectdiscovery/subfinder/releases/latest" "brew=brew install projectdiscovery/tap/subfinder"
check_go_bin subfinder subfinder "subfinder -version" false

set_meta amass     "OWASP Amass — DNS enumeration + attack surface mapping"             "https://github.com/owasp-amass/amass"
add_hints amass    "apt=sudo apt install amass" "brew=brew install amass" "snap=sudo snap install amass" "binary=https://github.com/owasp-amass/amass/releases/latest   # owasp-amass org (post-v3 home)"
simple_check amass amass "amass -version" false

set_meta dig       "DNS lookup (bind9-dnsutils / bind-tools)"                          "https://bind9.readthedocs.io/"
add_hints dig      "apt=sudo apt install bind9-dnsutils" "brew=brew install bind"
simple_check dig dig "dig -v 2>&1" true

# --- Port scan / service probe (ProjectDiscovery suite) ---
set_meta naabu     "ProjectDiscovery naabu — fast port scanner"                        "https://github.com/projectdiscovery/naabu"
add_hints naabu    "go=go install github.com/projectdiscovery/naabu/v2/cmd/naabu@latest" "binary=https://github.com/projectdiscovery/naabu/releases/latest" "brew=brew install projectdiscovery/tap/naabu"
check_go_bin naabu naabu "naabu -version" false

# --- Windows / SMB enumeration ---
set_meta netexec   "NetExec (NXC) — null-session SMB enum, the CrackMapExec continuation"  "https://github.com/Pennyw0rth/NetExec"
add_hints netexec  "pipx=pipx install git+https://github.com/Pennyw0rth/NetExec" "apt=sudo apt install netexec   # Debian trixie / Kali Linux" "source=git clone https://github.com/Pennyw0rth/NetExec && cd NetExec && pipx install ."
simple_check netexec netexec "netexec --version 2>&1" false

set_meta smbmap    "SMB share enumeration (Python)"                                    "https://github.com/ShawnDEvans/smbmap"
add_hints smbmap   "pipx=pipx install smbmap" "apt=sudo apt install smbmap" "source=git clone https://github.com/ShawnDEvans/smbmap && cd smbmap && pipx install ."
simple_check smbmap smbmap "smbmap -v 2>&1 | head -1" false

# --- Vulnerability scanners (intrusive / credentialed) ---
set_meta nuclei    "ProjectDiscovery nuclei — template-based CVE + exposure scanner"   "https://github.com/projectdiscovery/nuclei"
add_hints nuclei   "go=go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest" "binary=https://github.com/projectdiscovery/nuclei/releases/latest" "brew=brew install projectdiscovery/tap/nuclei" "docker=docker run --rm projectdiscovery/nuclei:latest"
check_go_bin nuclei nuclei "nuclei -version" false

set_meta bloodhound-py "BloodHound Python collector (bloodhound.py)"                    "https://github.com/dirkjanm/BloodHound.py"
add_hints bloodhound-py "pipx=pipx install bloodhound" "source=git clone https://github.com/dirkjanm/BloodHound.py && cd BloodHound.py && pipx install ."
simple_check bloodhound-py bloodhound-python "bloodhound-python --help 2>&1 | head -1" false

set_meta gvm-tools "Greenbone GVM / OpenVAS CLI (python-gvm + gvm-tools)"              "https://github.com/greenbone/gvm-tools"
add_hints gvm-tools "pipx=pipx install gvm-tools" "apt=sudo apt install gvm-tools" "source=git clone https://github.com/greenbone/gvm-tools && cd gvm-tools && pipx install ."
simple_check gvm-tools gvm-cli "gvm-cli --version 2>&1" false

# Nessus is commercial — no open-source install path.  Record as an
# informational entry so the agent knows where to look if the user has
# a licence.
RESULT_NAMES+=(nessus)
RESULT_STATUS[nessus]=info
RESULT_PATH[nessus]=""
RESULT_VERSION[nessus]=""
RESULT_ESSENTIAL[nessus]=false
RESULT_ISSUE[nessus]="commercial scanner — no open-source install"
RESULT_FIX[nessus]=""
RESULT_DESC[nessus]="Tenable Nessus — commercial vulnerability scanner (integration via REST API)"
RESULT_HOMEPAGE[nessus]="https://www.tenable.com/products/nessus"
RESULT_HINTS[nessus]=$'vendor=https://www.tenable.com/downloads/nessus   # free Essentials tier available\n'

# --- Optional language runtimes for installing the Go / Rust tools above ---
set_meta go        'Go toolchain — needed for ProjectDiscovery "go install" paths'     "https://go.dev"
add_hints go       "apt=sudo apt install golang-go" "brew=brew install go" "binary=https://go.dev/dl/"
simple_check go go "go version" false

set_meta cargo     'Rust cargo — needed for "cargo install rustscan"'                  "https://www.rust-lang.org/tools/install"
add_hints cargo    "rustup=curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh   # official Rust installer" "apt=sudo apt install rustc cargo" "brew=brew install rust"
simple_check cargo cargo "cargo --version" false

set_meta pipx      "pipx — isolated Python tool installs (needed for netexec, smbmap, gvm-tools)"  "https://pipx.pypa.io"
add_hints pipx     "apt=sudo apt install pipx" "brew=brew install pipx" "pip=python3 -m pip install --user pipx"
simple_check pipx pipx "pipx --version" false

set_meta docker    "Docker — run tools in containers when native install is awkward"    "https://docs.docker.com/get-docker/"
add_hints docker   "apt=sudo apt install docker.io" "brew=brew install --cask docker" "binary=https://docs.docker.com/engine/install/"
simple_check docker docker "docker --version" false

# ---------------------------------------------------------------------------
# Render output
# ---------------------------------------------------------------------------

count_ok=0
count_warn=0
count_missing=0
count_info=0
essential_ok=0
essential_total=0

for name in "${RESULT_NAMES[@]}"; do
  case "${RESULT_STATUS[$name]}" in
    ok)      ((count_ok++)) ;;
    warn)    ((count_warn++)) ;;
    missing) ((count_missing++)) ;;
    info)    ((count_info++)) ;;
  esac
  if [[ "${RESULT_ESSENTIAL[$name]}" == "true" ]]; then
    ((essential_total++))
    [[ "${RESULT_STATUS[$name]}" == "ok" ]] && ((essential_ok++))
  fi
done

GENERATED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
HOSTNAME=$(hostname 2>/dev/null || echo "unknown")

if $JSON_OUTPUT; then
  # JSON output — match the install_hints shape used by /agent/recon/context.
  {
    printf '{\n'
    printf '  "generated_at": "%s",\n' "$GENERATED_AT"
    printf '  "host": "%s",\n' "$HOSTNAME"
    printf '  "summary": {\n'
    printf '    "total": %d,\n' "${#RESULT_NAMES[@]}"
    printf '    "ok": %d,\n' "$count_ok"
    printf '    "warn": %d,\n' "$count_warn"
    printf '    "missing": %d,\n' "$count_missing"
    printf '    "info": %d,\n' "$count_info"
    printf '    "essential_ready": %d,\n' "$essential_ok"
    printf '    "essential_total": %d\n' "$essential_total"
    printf '  },\n'
    printf '  "tools": [\n'
    first=true
    for name in "${RESULT_NAMES[@]}"; do
      $first || printf ',\n'
      first=false
      # Escape values for JSON.  Using argv (not stdin) so bash's
      # here-string `<<<` doesn't append a newline to every value.
      esc() { python3 -c 'import json,sys; print(json.dumps(sys.argv[1]), end="")' "${1:-}"; }
      printf '    {\n'
      printf '      "name": %s,\n'        "$(esc "$name")"
      printf '      "description": %s,\n' "$(esc "${RESULT_DESC[$name]:-}")"
      printf '      "homepage": %s,\n'    "$(esc "${RESULT_HOMEPAGE[$name]:-}")"
      printf '      "status": %s,\n'      "$(esc "${RESULT_STATUS[$name]}")"
      printf '      "essential": %s,\n'   "${RESULT_ESSENTIAL[$name]}"
      printf '      "path": %s,\n'        "$(esc "${RESULT_PATH[$name]:-}")"
      printf '      "version": %s,\n'     "$(esc "${RESULT_VERSION[$name]:-}")"
      printf '      "issue": %s,\n'       "$(esc "${RESULT_ISSUE[$name]:-}")"
      printf '      "fix": %s,\n'         "$(esc "${RESULT_FIX[$name]:-}")"
      printf '      "install_hints": {'
      hints=${RESULT_HINTS[$name]:-}
      hint_first=true
      while IFS= read -r pair; do
        [[ -z "$pair" ]] && continue
        provider=${pair%%=*}
        command=${pair#*=}
        $hint_first || printf ','
        hint_first=false
        printf '\n        %s: %s' "$(esc "$provider")" "$(esc "$command")"
      done <<<"$hints"
      if ! $hint_first; then
        printf '\n      '
      fi
      printf '}\n'
      printf '    }'
    done
    printf '\n  ]\n'
    printf '}\n'
  }
  # strict mode: fail if anything essential is missing or warned
  if $STRICT && (( essential_ok < essential_total )); then
    exit 1
  fi
  exit 0
fi

# -- Human-readable output --

# ANSI colors (auto-disabled if stdout isn't a TTY).
if [[ -t 1 ]]; then
  C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'
  C_BLUE=$'\033[36m';  C_DIM=$'\033[2m';    C_RESET=$'\033[0m'
else
  C_GREEN=""; C_YELLOW=""; C_RED=""; C_BLUE=""; C_DIM=""; C_RESET=""
fi

status_badge() {
  case "$1" in
    ok)      printf "%s[ OK      ]%s" "$C_GREEN"  "$C_RESET" ;;
    warn)    printf "%s[ WARN    ]%s" "$C_YELLOW" "$C_RESET" ;;
    missing) printf "%s[ MISSING ]%s" "$C_RED"    "$C_RESET" ;;
    info)    printf "%s[ INFO    ]%s" "$C_BLUE"   "$C_RESET" ;;
  esac
}

print_tool_row() {
  local name=$1
  local badge ver_label
  badge=$(status_badge "${RESULT_STATUS[$name]}")
  ver_label="${RESULT_VERSION[$name]:-}"
  if [[ -z "$ver_label" && "${RESULT_STATUS[$name]}" == "missing" ]]; then
    ver_label="(not installed)"
  fi
  printf "%s %-18s %-40s %s\n" "$badge" "$name" "$ver_label" "${RESULT_PATH[$name]:-}"
  if [[ -n "${RESULT_ISSUE[$name]:-}" ]]; then
    printf "                    %sIssue:%s %s\n" "$C_DIM" "$C_RESET" "${RESULT_ISSUE[$name]}"
  fi
  if [[ -n "${RESULT_FIX[$name]:-}" ]]; then
    printf "                    %sFix:%s %s\n" "$C_DIM" "$C_RESET" "${RESULT_FIX[$name]}"
  fi
  if [[ "${RESULT_STATUS[$name]}" == "missing" || "${RESULT_STATUS[$name]}" == "warn" ]]; then
    if [[ -n "${RESULT_HOMEPAGE[$name]:-}" ]]; then
      printf "                    %sUpstream:%s %s\n" "$C_DIM" "$C_RESET" "${RESULT_HOMEPAGE[$name]}"
    fi
    if [[ -n "${RESULT_HINTS[$name]:-}" ]]; then
      printf "                    %sInstall (pick one):%s\n" "$C_DIM" "$C_RESET"
      while IFS= read -r pair; do
        [[ -z "$pair" ]] && continue
        provider=${pair%%=*}
        command=${pair#*=}
        printf "                      %s%-9s%s %s\n" "$C_DIM" "$provider" "$C_RESET" "$command"
      done <<<"${RESULT_HINTS[$name]}"
    fi
  fi
}

printf "\n"
printf "BlueStick recon preflight — %s\n" "$GENERATED_AT"
printf "Host: %s\n" "$HOSTNAME"
printf "=========================================================================\n"

for name in "${RESULT_NAMES[@]}"; do
  print_tool_row "$name"
done

printf "=========================================================================\n"
printf "Summary: %s%d ok%s, %s%d warn%s, %s%d missing%s, %s%d info%s  "   \
  "$C_GREEN" "$count_ok" "$C_RESET" \
  "$C_YELLOW" "$count_warn" "$C_RESET" \
  "$C_RED" "$count_missing" "$C_RESET" \
  "$C_BLUE" "$count_info" "$C_RESET"
printf "(essential: %d/%d ready)\n\n" "$essential_ok" "$essential_total"

if (( count_missing > 0 )) || (( count_warn > 0 )); then
  printf "%sInstallation guidance above points only at official upstream repositories%s\n" "$C_DIM" "$C_RESET"
  printf "%sand verified distribution packages.  No third-party binaries.%s\n\n" "$C_DIM" "$C_RESET"
fi

if $STRICT && (( essential_ok < essential_total )); then
  exit 1
fi
exit 0
