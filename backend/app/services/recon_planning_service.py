"""
Recon planning helpers — extracted from agent_recon.py in v2.27.0.

Four functions that take scope + size + known-host inputs and produce
recon-planning suggestions for the agent:

* ``analyze_scope_size`` — classify the scope as small / medium / large
  based on address count and recommend an appropriate discovery tool.
* ``masscan_rate_for_bucket`` — pick a masscan rate that finishes in
  a reasonable time without flooding the network.
* ``build_tool_catalog`` — produce the recon-tool catalog
  ``/agent/recon/context`` returns (discovery, service probe, web
  fingerprinting; size-aware).
* ``build_recommended_sequence`` — assemble the 3-step starter plan
  (discovery → service probe → web fingerprinting) with concrete
  commands, durations, and output filenames tailored to this scope's
  size.

These are pure functions: no DB access, no FastAPI deps; they take
plain inputs (CIDR lists, host counts, dicts) and return dicts /
lists ready to embed in the agent_schemas response shapes.
"""
from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_network
from typing import Any, Callable, Dict, List, Optional


def analyze_scope_size(subnet_cidrs: List[str]) -> Dict[str, Any]:
    """Compute scale signals for a scope from its CIDR list.

    Returns totals + a recommended discovery approach keyed by size:

      - ``≤256`` addresses → ``nmap_sn`` (a direct sweep finishes in
        minutes, results are thorough).
      - ``257–4096`` → ``rustscan_nmap`` (rustscan finds the live hits
        in seconds, nmap service-probes only those hits).
      - ``>4096`` → ``masscan`` (parallel TCP SYN; the only tool that
        finishes a /16 in under a few minutes).

    Thresholds are pragmatic, not theoretical — they track how long
    each tool actually takes at default settings on commodity hardware.
    Agents use ``recommended_discovery`` to pick a tool without doing
    IP math themselves.
    """
    total = 0
    largest = 0
    for cidr in subnet_cidrs:
        try:
            net = ip_network(cidr, strict=False)
            n = int(net.num_addresses)
            total += n
            if n > largest:
                largest = n
        except ValueError:
            continue

    if total == 0:
        # v2.25.0 — was "tiny" but that value was undocumented and
        # leaked through to clients consuming the size_bucket field.
        # Empty scopes behave identically to small ones from the
        # recommendation side, so collapse them into "small" and let
        # the agent + UI treat the contract as small / medium / large
        # exhaustively.
        recommended = "nmap_sn"
        bucket = "small"
    elif total <= 256:
        recommended = "nmap_sn"
        bucket = "small"  # /24 or smaller
    elif total <= 4096:
        recommended = "rustscan_nmap"
        bucket = "medium"  # between /24 and /20
    else:
        recommended = "masscan"
        bucket = "large"  # /20 or bigger

    # Rough duration estimates per discovery path at defaults.  Used
    # so the agent can report an expected wall-clock to the user
    # during the approval step instead of "running a scan, unknown
    # duration".  Numbers are order-of-magnitude — network latency,
    # IDS dampening, and firewall behavior dominate real runs.
    if bucket == "small":
        estimates = {
            "nmap_sn": "~1–5 min",
            "masscan": "~30s",
            "rustscan_nmap": "~1–2 min",
        }
    elif bucket == "medium":
        estimates = {
            "nmap_sn": "~15–45 min",
            "masscan": "~1–3 min",
            "rustscan_nmap": "~3–8 min",
        }
    elif bucket == "large":
        estimates = {
            "nmap_sn": "~2–6 hours",
            "masscan": "~1–3 min",
            "rustscan_nmap": "~5–15 min",
        }
    else:
        estimates = {"nmap_sn": "n/a", "masscan": "n/a", "rustscan_nmap": "n/a"}

    return {
        "total_addresses": total,
        "largest_subnet_size": largest,
        "cidr_count": len([c for c in subnet_cidrs if c]),
        "size_bucket": bucket,
        "recommended_discovery": recommended,
        "estimated_durations": estimates,
        "note": (
            "`recommended_discovery` is a starting suggestion based on the "
            "number of addresses in scope.  You may deviate if you have good "
            "reason (e.g. the network is known to be tarpitting SYN packets, "
            "ICMP is blocked, the user wants thoroughness over speed) — just "
            "justify the choice to the user."
        ),
    }


def masscan_rate_for_bucket(size_bucket: str) -> int:
    """Pick a masscan rate that finishes in a reasonable time without
    flooding a modest network.  Small buckets don't benefit from a high
    rate (the whole sweep is a few hundred packets); large buckets need
    the higher rate to finish in minutes instead of hours."""
    return {"small": 1000, "medium": 2500, "large": 5000}.get(size_bucket, 1000)


def build_tool_catalog(
    subnet_cidrs: List[str],
    scope_size: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Return a structured tool catalog with CIDRs pre-resolved.

    Each entry is ``{phase, tool, command, rationale, intrusive,
    output_format, estimated_duration?}``.  Phase groups: discovery,
    service_probe, web, dns, smb, credentialed.  ``intrusive=False``
    means safe enough to run without per-command approval escalation
    (the per-run approval still stands).

    v2.13.0 — the discovery section is now reordered by ``scope_size``:
    whichever tool has the best speed/accuracy trade-off for this
    scope appears first.  Pre-v2.13.0 nmap always led, which caused
    agents to pick it even on /16 scopes where it takes hours.  The
    rest of the catalog is order-stable.
    """
    # Space-joined for tools that accept multiple positional CIDRs (nmap, masscan).
    # Comma-joined for tools whose `-a` flag wants a single comma-delimited arg (rustscan).
    # Pre-v2.42.0 the rustscan + masscan paths used `first_cidr` only, silently dropping
    # every subnet after the first on multi-CIDR scopes (review C-RR-1).
    cidr_list = " ".join(subnet_cidrs) if subnet_cidrs else "{cidr}"
    rustscan_cidr_list = ",".join(subnet_cidrs) if subnet_cidrs else "{cidr}"
    bucket = (scope_size or {}).get("size_bucket", "small")
    durations = (scope_size or {}).get("estimated_durations", {})
    masscan_rate = masscan_rate_for_bucket(bucket)

    discovery = {
        "nmap_sn": {
            "phase": "discovery",
            "tool": "nmap",
            "command": f"nmap -sn -T3 -PE -PP -PS22,80,443 -PA80 -oX nmap-sweep.xml {cidr_list}",
            "rationale": (
                "Direct ICMP + selective TCP sweep.  Thorough and non-intrusive, "
                "but sequential — appropriate only for small scopes (≤/24).  "
                "Raw-socket probe types (ICMP echo / timestamp) fall back to "
                "TCP connect when run unprivileged; accuracy is slightly lower "
                "but the scan still completes."
            ),
            "intrusive": False,
            "output_format": "xml",
            "estimated_duration": durations.get("nmap_sn"),
            "best_for": "small scopes where thoroughness > speed",
            "preflight": "nmap --version  # any recent nmap works; 7.x+ preferred",
            "requires_privileges": "optional — better accuracy with sudo/cap_net_raw, works unprivileged",
            "alternatives": ["rustscan_nmap", "masscan (large scopes only)"],
        },
        "rustscan_nmap": {
            "phase": "discovery",
            "tool": "rustscan",
            "command": (
                f"rustscan -a {rustscan_cidr_list} --ulimit 5000 -- -sV -oX rustscan-nmap.xml"
            ),
            "rationale": (
                "Rustscan front-end → nmap back-end.  Rustscan finds live + "
                "open-port hosts in seconds, the piped nmap does service "
                "detection only on the hits.  3–5× faster than a bare `nmap -sn` "
                "sweep on sparse medium-sized scopes.  Upload the nmap XML output."
            ),
            "intrusive": False,
            "output_format": "xml",
            "estimated_duration": durations.get("rustscan_nmap"),
            "best_for": "medium scopes (/20–/24) with unknown density",
            "preflight": "rustscan --version && nmap --version",
            "requires_privileges": (
                "none by default — rustscan uses TCP connect scans.  nmap -sV "
                "runs unprivileged too.  Good fallback when masscan fails on "
                "privilege requirements."
            ),
            "alternatives": ["masscan (faster on large scopes, needs root)", "nmap_sn (slower, small scopes)"],
            "install_hints": {
                "cargo": "cargo install rustscan",
                "brew": "brew install rustscan",
                "apt": "not in Debian/Ubuntu repos — use cargo or prebuilt binary",
                "binary": "https://github.com/RustScan/RustScan/releases/latest",
                "docker": "docker run --rm -v $PWD:/out rustscan/rustscan:latest -a <cidr>",
                "note": "rustscan is the masscan fallback when raw-socket privileges aren't available — keep it installed for this reason",
            },
        },
        "masscan": {
            "phase": "discovery",
            "tool": "masscan",
            "command": (
                f"sudo masscan --rate={masscan_rate} -p22,80,443,445,3389,8080,8443 "
                f"-oX masscan-sweep.xml {cidr_list}"
            ),
            "rationale": (
                f"Fast parallel TCP SYN sweep at {masscan_rate}pps.  Only tool "
                f"that finishes a /16 in under a few minutes.  Follow up with "
                f"`nmap -sV -sC --top-ports 1000 -iL <hits.txt>` on the live "
                f"hits for service detection.\n\n"
                f"**Requires raw-socket privileges** — the command uses `sudo` "
                f"by default.  Alternative: grant the capability once with "
                f"`sudo setcap cap_net_raw=eip $(which masscan)`, then run "
                f"without sudo.  If neither is available, fall back to rustscan."
            ),
            "intrusive": False,
            "output_format": "xml",
            "estimated_duration": durations.get("masscan"),
            "best_for": "large scopes (/20 or bigger) where speed is critical",
            "preflight": (
                "masscan --version && (sudo -n true 2>/dev/null || getcap "
                "$(which masscan) 2>/dev/null | grep -q cap_net_raw) || echo "
                "'WARNING: masscan needs sudo or cap_net_raw=eip — fall back "
                "to rustscan'"
            ),
            "requires_privileges": "raw_socket (sudo OR cap_net_raw=eip on the binary)",
            "alternatives": ["rustscan_nmap (non-privileged, ~5× slower)", "nmap_sn (non-privileged, slow on /16+)"],
            "install_hints": {
                "apt": "sudo apt install masscan",
                "brew": "brew install masscan",
                "source": "git clone https://github.com/robertdavidgraham/masscan && cd masscan && make && sudo make install",
                "binary": "https://github.com/robertdavidgraham/masscan/releases   # official releases from author Robert David Graham",
                "privilege_fix": "sudo setcap cap_net_raw=eip $(which masscan)   # grants raw-socket capability without needing sudo",
                "note": "No official Docker image — upstream ships source only.  Avoid third-party images unless you trust the maintainer; build from the official repo or use distro packages.",
            },
        },
    }
    order = {
        "small": ["nmap_sn", "rustscan_nmap", "masscan"],
        "medium": ["rustscan_nmap", "masscan", "nmap_sn"],
        "large": ["masscan", "rustscan_nmap", "nmap_sn"],
    }.get(bucket, ["nmap_sn", "rustscan_nmap", "masscan"])

    return [
        *[discovery[k] for k in order],
        # --- Port + service enumeration ---
        {
            "phase": "service_probe",
            "tool": "nmap",
            "command": "nmap -sV -sC -T3 --top-ports 1000 -iL live-hosts.txt -oX nmap-services.xml",
            "rationale": (
                "Default service detection.  Runs on the live-host list from "
                "the discovery phase — never on the full CIDR.  Top-1000 ports "
                "covers >95% of real-world services at a fraction of the cost "
                "of `-p-`.  Breadth-first across hosts, not depth-first per host."
            ),
            "intrusive": False,
            "output_format": "xml",
            "best_for": "first-pass service enumeration on discovered hosts",
            "preflight": "nmap --version",
            "requires_privileges": "optional (sudo yields -sS SYN scan; unprivileged uses -sT TCP connect)",
            "alternatives": ["rustscan (combined with -sV)", "naabu + tech-detect"],
        },
        {
            "phase": "service_probe",
            "tool": "nmap",
            "command": "nmap -sV -sC -T3 -p- -oX nmap-deep.xml {ip}",
            "rationale": (
                "Full-port deep scan — escalation path, not default.  Use only "
                "on high-value hosts (domain controllers, known-critical "
                "servers) flagged during top-1000-ports triage.  Expect ~20–40 "
                "min per host."
            ),
            "intrusive": False,
            "output_format": "xml",
            "best_for": "targeted deep-dive on selected high-value hosts",
            "preflight": "nmap --version",
            "requires_privileges": "optional (same as above)",
            "alternatives": ["masscan -p0-65535 (faster, needs root)"],
        },
        # --- Web fingerprinting ---
        {
            "phase": "web",
            "tool": "httpx",
            "command": (
                "httpx -l targets.txt -sc -title -server -tech-detect -favicon "
                "-tls-probe -cdn -json -o httpx.jsonl"
            ),
            "rationale": (
                "Fast web fingerprinter from ProjectDiscovery. Captures status, "
                "title, server header, tech stack (Wappalyzer), favicon hash, "
                "TLS / CDN signals. Canonical first pass before screenshot/attack "
                "tools. Input is a newline-delimited target list (`host:port` or "
                "URL). Output is JSONL — one JSON object per target.\n\n"
                "**Binary-name collision warning:** in Python-heavy environments, "
                "`httpx` on PATH often resolves to the Python httpx CLI "
                "(`pip install httpx[cli]`), which uses completely different "
                "flags and will reject `-sc -title -tech-detect` with an error.  "
                "Always run the preflight check before using this entry — if "
                "the wrong CLI is on PATH, install ProjectDiscovery's with "
                "`go install github.com/projectdiscovery/httpx/cmd/httpx@latest` "
                "and call the binary by explicit path (`~/go/bin/httpx`)."
            ),
            "intrusive": False,
            "output_format": "jsonl",
            "preflight": (
                "httpx -version 2>&1 | grep -qi projectdiscovery || echo "
                "'WRONG httpx — Python CLI shadows ProjectDiscovery httpx on "
                "PATH; install via go install github.com/projectdiscovery/"
                "httpx/cmd/httpx@latest and use explicit path'"
            ),
            "requires_privileges": "none",
            "alternatives": [
                "nmap_web (always-available fallback — see below; ingests via nmap-XML)",
                "eyewitness (heavier; screenshots too)",
                "nikto (intrusive; CVE-focused)",
            ],
            "install_hints": {
                "go": "go install github.com/projectdiscovery/httpx/cmd/httpx@latest   # puts binary at ~/go/bin/httpx",
                "binary": "https://github.com/projectdiscovery/httpx/releases/latest — fetch httpx_*_linux_amd64.zip, unzip, use explicit path",
                "docker": "docker run --rm -v $PWD:/out projectdiscovery/httpx:latest -l /out/targets.txt -json -o /out/httpx.jsonl",
                "brew": "brew install projectdiscovery/tap/httpx   # on macOS — takes precedence over Python httpx",
                "note": "Go is NOT usually available in sandboxed agent environments.  If `go` isn't installed, the prebuilt binary from GitHub releases is the most reliable path (no compiler needed).  When neither install lands, use the nmap_web entry below — it produces XML that ingests through the same parser as the discovery scan.",
            },
        },
        {
            # Always-available web-enrichment fallback when ProjectDiscovery
            # httpx is unavailable (Python-CLI collision, no Go toolchain,
            # sandboxed environment).  Output is plain nmap XML, so it
            # rides the existing nmap parser into Port.scripts / HostScript
            # — same enrichment shape as the discovery scan, no new
            # parser to maintain.  Coverage is narrower than httpx (no
            # favicon hash, no per-target tech stack) but enough to pull
            # title, server, methods, common paths, and TLS details for
            # the canonical web ports.
            "phase": "web",
            "tool": "nmap",
            "command": (
                "nmap -sV --script "
                "http-title,http-server-header,http-headers,http-methods,"
                "http-enum,ssl-cert,ssl-enum-ciphers "
                "-p 80,443,8080,8443,8000,8888 "
                "-oX nmap-web.xml -iL targets.txt"
            ),
            "rationale": (
                "Web-service enrichment via nmap HTTP NSE scripts — the "
                "**always-available** fallback when ProjectDiscovery httpx "
                "is missing or shadowed by the Python httpx CLI.  Outputs "
                "standard nmap XML, so it goes through the same ingestion "
                "path as the discovery scan: title, server header, "
                "supported methods, common content (`http-enum`), and "
                "TLS certificate + cipher details all land in "
                "`Port.scripts` / `HostScript` automatically.  Use this "
                "when the preflight check on the `httpx` entry above "
                "fails — no install required (nmap is already mandatory "
                "for discovery)."
            ),
            "intrusive": False,
            "output_format": "xml",
            "best_for": "web enrichment on hosts without working httpx",
            "preflight": "nmap --version",
            "requires_privileges": "none",
            "alternatives": [
                "httpx (preferred when ProjectDiscovery binary is available)",
                "eyewitness (adds screenshots on top of fingerprinting)",
            ],
        },
        {
            "phase": "web",
            "tool": "eyewitness",
            "command": "eyewitness --web -f targets.txt -d eyewitness-results",
            "rationale": (
                "Screenshot pass for rapid visual triage of web services. Heavier "
                "than httpx (spins a headless browser per target) — run httpx first "
                "to cull dead targets, then eyewitness on the survivors."
            ),
            "intrusive": False,
            "output_format": "json",
            "preflight": "eyewitness --help 2>&1 | head -1",
            "requires_privileges": "none",
            "alternatives": ["httpx (lightweight, no screenshots)"],
        },
        {
            "phase": "web",
            "tool": "nikto",
            "command": "nikto -h https://{ip} -o nikto.json -Format json",
            "rationale": "Web misconfiguration / common-CVE scan.  Moderate noise.",
            "intrusive": True,
            "output_format": "json",
            "preflight": "nikto -Version",
            "requires_privileges": "none",
            "alternatives": ["nuclei -t exposures/ (faster, more targeted)"],
        },
        # --- DNS ---
        {
            "phase": "dns",
            "tool": "subfinder",
            "command": "subfinder -d {domain} -o subfinder.txt",
            "rationale": "Passive subdomain discovery — no traffic to the target network.",
            "intrusive": False,
            "output_format": "txt",
            "preflight": "subfinder -version",
            "requires_privileges": "none",
            "alternatives": ["amass enum -passive"],
        },
        # --- SMB / Windows enumeration ---
        {
            "phase": "smb",
            "tool": "netexec",
            "command": "netexec smb {ip_list} --shares -u '' -p ''",
            "rationale": "Null-session SMB enumeration — read-only, safe starting point for Windows hosts.",
            "intrusive": False,
            "output_format": "json",
            "preflight": "netexec --version",
            "requires_privileges": "none",
            "alternatives": ["smbmap -H {ip}", "nmap --script smb-enum-shares -p445 {ip}"],
        },
        # --- Credentialed (requires integration config) ---
        {
            "phase": "credentialed",
            "tool": "nuclei",
            "command": "nuclei -l targets.txt -t cves/ -t exposures/ -je nuclei.json",
            "rationale": "Template-based CVE + exposure detection. Fast, fine-grained.",
            "intrusive": True,
            "output_format": "json",
            "preflight": "nuclei -version",
            "requires_privileges": "none (PDCP token recommended for cloud templates)",
            "alternatives": ["nikto (older, broader, slower)"],
        },
    ]


def _env_tool_unavailable(env: Optional[Dict[str, Any]], tool: str) -> Optional[str]:
    """Return a short reason if the env probe says ``tool`` won't work.

    Two signals are honoured:
      * ``tools_status`` — preflight-script JSON shape ``[{name, status, issue}]``
        where ``status`` is ``ok | warn | missing | info``.  Authoritative
        when present because the preflight script actually invoked the
        binary (catches the httpx Python-CLI collision, masscan
        raw-socket privilege gap, etc.).
      * ``tools_available`` — the simpler ``{tool: bool}`` map captured at
        env-probe time.  Less precise (a present-on-PATH httpx may still
        be the wrong binary) but works without the agent running the
        preflight first.

    Returns ``None`` when the tool looks usable.  Otherwise returns a
    short reason string suitable for surfacing in the swap-step note.
    """
    if not env:
        return None
    # tools_status: authoritative — only flag a problem on warn / missing.
    # Accept BOTH canonical shapes (v2.44.4):
    #   * list of dicts {name, status, issue} — mirrors preflight.sh's
    #     .tools[] output; documented shape.
    #   * dict keyed by name {tool: {status, issue, ...}} — observed
    #     in the wild because agents reshape for easier client-side
    #     lookup.  Both produce the same swap decision; accepting both
    #     means a slightly-wrong agent payload doesn't silently fall
    #     through to the less-precise tools_available fallback.
    status_list = env.get("tools_status")
    entry: Optional[Dict[str, Any]] = None
    if isinstance(status_list, list):
        for candidate in status_list:
            if isinstance(candidate, dict) and candidate.get("name") == tool:
                entry = candidate
                break
    elif isinstance(status_list, dict):
        payload = status_list.get(tool)
        if isinstance(payload, dict):
            entry = {"name": tool, **payload}
    if entry is not None:
        status = (entry.get("status") or "").lower()
        if status in ("warn", "missing"):
            issue = entry.get("issue") or status
            return f"preflight {status}: {issue}"
        # ok / info → tool is fine
        return None
    # Fallback to the boolean inventory.
    tools_available = env.get("tools_available") or {}
    if isinstance(tools_available, dict) and tools_available.get(tool) is False:
        return f"{tool} not on PATH"
    return None


# Probe os_family → the install-hint provider key a host-readiness UI
# should prefer when generating install guidance for missing tools.
_OS_INSTALL_PROVIDER = {
    "linux": "apt",
    "darwin": "brew",
    "windows": "binary",
}


def _normalize_tools_status(raw: Any) -> Dict[str, Dict[str, Any]]:
    """Normalize an env probe's ``tools_status`` (list-form or dict-form)
    to ``{tool_name: {status, path, issue, ...}}`` — accepts both shapes,
    same rationale as ``_env_tool_unavailable``."""
    out: Dict[str, Dict[str, Any]] = {}
    if isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict) and entry.get("name"):
                out[str(entry["name"])] = entry
    elif isinstance(raw, dict):
        for name, payload in raw.items():
            if isinstance(payload, dict):
                out[str(name)] = {"name": name, **payload}
    return out


def build_tool_readiness(
    probe: Optional[Dict[str, Any]] = None,
    probed_at: Any = None,
) -> Dict[str, Any]:
    """Cross-reference the agent tool catalog against an environment probe.

    Produces a per-tool installed / missing / warn / unknown breakdown
    plus summary counts, so a "host readiness" UI can show what the
    operator still needs and generate install guidance.  ``probe`` is the
    EnvironmentSummary JSON from the user's most recent session probe, or
    ``None`` when they have never run an agent workflow — every tool then
    reports ``unknown``.
    """
    # Dedupe the catalog by tool binary — several catalog entries
    # (nmap_sn, rustscan_nmap, …) map to the same installed binary.
    tools: Dict[str, Dict[str, Any]] = {}
    for entry in build_tool_catalog([]):
        name = entry.get("tool")
        if not name:
            continue
        slot = tools.setdefault(
            name,
            {"tool": name, "phases": set(), "intrusive": False, "install_hints": {}},
        )
        if entry.get("phase"):
            slot["phases"].add(entry["phase"])
        slot["intrusive"] = slot["intrusive"] or bool(entry.get("intrusive"))
        for key, val in (entry.get("install_hints") or {}).items():
            slot["install_hints"].setdefault(key, val)

    probe = probe or {}
    available = probe.get("tools_available") or {}
    status_map = _normalize_tools_status(probe.get("tools_status"))
    os_family = (str(probe.get("os_family") or "")).lower() or None

    _STATUS = {
        "ok": "installed",
        "info": "installed",
        "warn": "warn",
        "missing": "missing",
    }

    items: List[Dict[str, Any]] = []
    for name in sorted(tools):
        slot = tools[name]
        status, path, issue = "unknown", None, None
        st = status_map.get(name)
        if st:
            status = _STATUS.get((str(st.get("status") or "")).lower(), "unknown")
            path = st.get("path") or None
            issue = st.get("issue") or None
        elif name in available:
            status = "installed" if available.get(name) else "missing"
        items.append(
            {
                "tool": name,
                "phases": sorted(slot["phases"]),
                "intrusive": slot["intrusive"],
                "status": status,
                "path": path,
                "issue": issue,
                "install_hints": slot["install_hints"],
            }
        )

    summary = {"installed": 0, "missing": 0, "warn": 0, "unknown": 0, "total": len(items)}
    for it in items:
        summary[it["status"]] = summary.get(it["status"], 0) + 1

    return {
        "has_probe": bool(probe),
        "os_family": os_family,
        "os_release": probe.get("os_release"),
        "shell": probe.get("shell"),
        "probed_at": probed_at.isoformat() if hasattr(probed_at, "isoformat") else probed_at,
        "preferred_provider": _OS_INSTALL_PROVIDER.get(os_family) if os_family else None,
        "summary": summary,
        "tools": items,
    }


def _manual_action_step(
    step: Dict[str, Any],
    *,
    original_tool: str,
    fallback_tool: str,
    original_reason: str,
    fallback_reason: str,
    acceptable_fallbacks: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """Emit a placeholder step when neither the default tool nor the
    documented fallback is usable in this environment.

    The previous behavior (pre-v2.42.0, review C-RR-3) was to swap the
    step regardless, producing an "adapted" command that still wouldn't
    run because the replacement was itself missing.  This makes the
    blocked state explicit so the agent surfaces it instead of trying
    the bad command and reporting back a failure.

    v2.44.5 — when the rule declares ``acceptable_fallbacks_when_blocked``,
    those entries ride along so the agent has a concrete user-approval
    request ready instead of dead-ending.  The fallbacks are NOT
    auto-executed; they require explicit per-command approval since
    they involve different tools / lower coverage / both.
    """
    note = (
        f"BLOCKED: neither {original_tool} ({original_reason}) nor the "
        f"documented fallback {fallback_tool} ({fallback_reason}) is usable "
        f"in this environment.  Install one before continuing — see the "
        f"`install_hints` block on the catalog entry for either tool, or "
        f"ask the user to install via their package manager.  Do NOT "
        f"improvise an alternate tool without per-command approval."
    )
    if acceptable_fallbacks:
        labels = ", ".join(f.get("tool", "?") for f in acceptable_fallbacks)
        note += (
            f"  Pre-vetted alternatives requiring explicit user approval: "
            f"{labels}.  See `acceptable_fallbacks` on this step."
        )
    return {
        **step,
        "tool": None,
        "command": None,
        "estimated_duration": None,
        "note": note,
        "output_file": None,
        "upload_after": False,
        "original_tool": original_tool,
        "fallback_tool": fallback_tool,
        "blocked_reason": "neither_available",
        "acceptable_fallbacks": acceptable_fallbacks or [],
    }


def _swap_step_to_eyewitness(step: Dict[str, Any], reason: str) -> Dict[str, Any]:
    """Replace a httpx step with an eyewitness command + adaptation note.

    Caller MUST first confirm that eyewitness is itself available — use
    ``_env_tool_unavailable(env, 'eyewitness')`` and route to
    ``_manual_action_step`` if both tools are missing.
    """
    return {
        **step,
        "tool": "eyewitness",
        "command": "eyewitness --web -f web_targets.txt -d eyewitness-results",
        "estimated_duration": "~5–20 min depending on web host count",
        "note": (
            f"Adapted from httpx to eyewitness because {reason}. "
            "eyewitness is heavier (spins a headless browser per target) "
            "but works without ProjectDiscovery's httpx binary.  Same "
            "input file shape (one URL per line); upload the JSON "
            "output when the run completes."
        ),
        "output_file": "eyewitness-results/Report.json",
        "original_tool": "httpx",
        "swap_reason": reason,
    }


def _swap_step_to_rustscan(step: Dict[str, Any], reason: str) -> Dict[str, Any]:
    """Replace a masscan step with a rustscan command + adaptation note.

    Caller MUST first confirm that rustscan is itself available — use
    ``_env_tool_unavailable(env, 'rustscan')`` and route to
    ``_manual_action_step`` if both tools are missing.  Caller is ALSO
    responsible for collapsing the subsequent service-probe step
    (rustscan combines discovery + service-probe in one pass; leaving
    the standalone nmap -iL live-hosts.txt step downstream produces a
    contradictory plan because rustscan doesn't emit live-hosts.txt).
    """
    # Extract the CIDR list from the original masscan command so the
    # rustscan equivalent targets the same scope.  The masscan command
    # ends with the CIDR(s); take everything after the last `-oX file`.
    cmd = step.get("command") or ""
    cidr = ""
    if " -oX " in cmd:
        # masscan args land as "... -oX masscan-sweep.xml 10.0.0.0/16 192.168.0.0/24"
        tail = cmd.split(" -oX ", 1)[1]
        parts = tail.split(maxsplit=1)
        if len(parts) > 1:
            # masscan accepts space-separated CIDRs; rustscan -a wants comma-separated.
            cidr = ",".join(parts[1].split())
    return {
        **step,
        "tool": "rustscan",
        "command": (
            f"rustscan -a {cidr} --ulimit 5000 -- "
            f"-sV -sC --top-ports 1000 -oX rustscan-nmap.xml"
        ),
        "estimated_duration": "~5–15 min (vs ~1–3 min for masscan; rustscan "
                              "is TCP-connect-only so it's slower but needs no privileges)",
        "note": (
            f"Adapted from masscan to rustscan because {reason}. "
            "rustscan uses TCP-connect scans so it runs unprivileged, "
            "and its piped nmap layer produces the same XML the parser "
            "wants.  Combines discovery + service probe in one pass, so "
            "the downstream service-probe step has been collapsed."
        ),
        "output_file": "rustscan-nmap.xml",
        "original_tool": "masscan",
        "swap_reason": reason,
    }


def _collapse_service_probe_after_rustscan(sequence: List[Dict[str, Any]]) -> None:
    """After a masscan→rustscan swap, replace the now-incoherent standalone
    nmap -iL live-hosts.txt service-probe step with a synthesized
    web-fingerprint step matching the canonical rustscan_nmap shape.
    Mutates `sequence` in place.  Idempotent — second call is a no-op
    because the rewritten step no longer matches the detector predicate.
    """
    for j, step in enumerate(sequence):
        cmd_j = step.get("command") or ""
        if (
            isinstance(cmd_j, str)
            and step.get("phase") == "service_probe"
            and "-iL live-hosts.txt" in cmd_j
        ):
            sequence[j] = _rustscan_followup_web_step(step.get("step", j + 1))
            return


@dataclass(frozen=True)
class SwapRule:
    """Declarative description of one tool-substitution rule.

    Adding a new rule (e.g. nmap→naabu, nikto→nuclei) is appending a row to
    ``_SWAP_RULES`` plus its swap_fn — no edits to the dispatch loop.

    Ordering note: rules with `reshape` should appear first in
    ``_SWAP_RULES`` so later rules see the already-reshaped sequence.
    """
    name: str                                         # for logs
    detect: Callable[[str], bool]                     # match a command string to this rule
    original_tool: str                                # _env_tool_unavailable key
    fallback_tool: str                                # _env_tool_unavailable key
    swap: Callable[[Dict[str, Any], str], Dict[str, Any]]   # (step, reason) -> new step
    reshape: Optional[Callable[[List[Dict[str, Any]]], None]] = None
    # v2.44.5 — when both original and fallback are unusable, the rule
    # may declare pre-vetted alternatives that require *explicit* user
    # approval before the agent can attempt them.  Surfaces on the
    # `manual_action_required` placeholder so the agent has a concrete
    # proposal to put in front of the user instead of dead-ending the
    # conversation.  Each entry: {tool, command, rationale,
    # coverage_loss}.  None = no acceptable fallback; agent must
    # request user input or install a tool to proceed.
    acceptable_fallbacks_when_blocked: Optional[List[Dict[str, str]]] = None


def _detect_masscan(cmd: str) -> bool:
    return "masscan" in cmd.split()


def _detect_httpx(cmd: str) -> bool:
    return cmd.lstrip().startswith("httpx ")


def _drop_redundant_screenshot_step(sequence: List[Dict[str, Any]]) -> None:
    """After an httpx→eyewitness swap, drop the standalone step-4
    ``web_screenshot`` step (v2.45.5).

    The web_screenshot step exists to run eyewitness *after* httpx
    culls dead targets.  When httpx itself is unavailable and the
    swap rule has already turned the step-3 web step INTO eyewitness,
    a second eyewitness step is pure redundancy — eyewitness
    fingerprints AND screenshots in one pass.  Remove it so the agent
    doesn't run eyewitness twice.  Mutates ``sequence`` in place;
    idempotent (a second call finds no web_screenshot step).
    """
    sequence[:] = [s for s in sequence if s.get("phase") != "web_screenshot"]


_SWAP_RULES: List[SwapRule] = [
    SwapRule(
        name="masscan_to_rustscan",
        detect=_detect_masscan,
        original_tool="masscan",
        fallback_tool="rustscan",
        swap=_swap_step_to_rustscan,
        reshape=_collapse_service_probe_after_rustscan,
        acceptable_fallbacks_when_blocked=[
            {
                "tool": "nmap",
                "command": "nmap -sn -T3 -PE -PP -PS22,80,443 -PA80 -oX nmap-sweep.xml <CIDR>",
                "rationale": (
                    "nmap host sweep covers the same goal (find live IPs) without "
                    "raw-socket privileges.  Slower than masscan/rustscan on /20+ "
                    "scopes but well-tolerated; no auth or root needed."
                ),
                "coverage_loss": "Linear (not parallel) scanning — multiply estimated duration by ~5-10× for /16+ scopes.",
            },
        ],
    ),
    SwapRule(
        name="httpx_to_eyewitness",
        detect=_detect_httpx,
        original_tool="httpx",
        fallback_tool="eyewitness",
        swap=_swap_step_to_eyewitness,
        # When step 3 swaps httpx→eyewitness, the standalone step-4
        # web_screenshot (also eyewitness) is redundant — drop it.
        reshape=_drop_redundant_screenshot_step,
        acceptable_fallbacks_when_blocked=[
            {
                "tool": "curl",
                "command": "curl -ksIL --max-time 10 https://<host>:<port>/ | head -20",
                "rationale": (
                    "Per-target HEAD request gets you HTTP status, server banner, "
                    "redirect chain, and TLS basics — covers ~70% of the httpx "
                    "fingerprinting surface for a triage pass.  No tech-detection, "
                    "no favicon hash, no JARM."
                ),
                "coverage_loss": "No tech stack inference, no TLS cert deep-dive, no per-method probing.",
            },
            {
                "tool": "whatweb",
                "command": "whatweb -a 3 --color=never https://<host>:<port>/",
                "rationale": (
                    "Ruby-based tech fingerprinter.  Ships in Debian/Kali apt "
                    "repos.  Closest open-source approximation to httpx's tech "
                    "detection when ProjectDiscovery's binary isn't available."
                ),
                "coverage_loss": "Slower per-target than httpx (no concurrency by default); no JSON output (text + grepable formats only).",
            },
        ],
    ),
]


def _apply_environment_substitutions(
    sequence: List[Dict[str, Any]], environment: Dict[str, Any]
) -> None:
    """Walk each rule in ``_SWAP_RULES`` once against the sequence.  For each
    rule's first matching step, swap to the fallback tool — or emit a
    ``manual_action_required`` placeholder if the fallback is also missing.
    Mutates ``sequence`` in place.

    Each rule fires at most once because the canonical sequence has at most
    one discovery step and at most one httpx step.  Rule order matters only
    when reshapes touch the same indices — declare reshape rules first.
    """
    for rule in _SWAP_RULES:
        for i, step in enumerate(sequence):
            cmd = step.get("command") or ""
            if not isinstance(cmd, str) or not rule.detect(cmd):
                continue
            original_reason = _env_tool_unavailable(environment, rule.original_tool)
            if not original_reason:
                break  # this rule doesn't fire on the env; move to next rule
            fallback_reason = _env_tool_unavailable(environment, rule.fallback_tool)
            if fallback_reason:
                sequence[i] = _manual_action_step(
                    step,
                    original_tool=rule.original_tool,
                    fallback_tool=rule.fallback_tool,
                    original_reason=original_reason,
                    fallback_reason=fallback_reason,
                    acceptable_fallbacks=rule.acceptable_fallbacks_when_blocked,
                )
            else:
                sequence[i] = rule.swap(step, original_reason)
                if rule.reshape is not None:
                    rule.reshape(sequence)
            break  # at most one matching step per rule


def _rustscan_followup_web_step(step_number: int) -> Dict[str, Any]:
    """Synthesize the web-fingerprint step that replaces the standalone
    service-probe step after a masscan→rustscan swap.  Matches the shape
    of the canonical `recommended == "rustscan_nmap"` step 2 in
    build_recommended_sequence so the post-swap sequence is internally
    consistent.
    """
    return {
        "step": step_number,
        "phase": "web",
        "command": (
            "httpx -l web_targets.txt -sc -title -server -tech-detect "
            "-favicon -tls-probe -cdn -json -o httpx.jsonl"
        ),
        "estimated_duration": "~2–10 min depending on web host count",
        "note": (
            "The masscan→rustscan swap upstream means step 1 already "
            "produced service-detected hosts.  Generate web_targets.txt "
            "from the rustscan-nmap.xml (hosts with port 80/443/8080/8443 "
            "open), then httpx fingerprints each.  Skip eyewitness unless "
            "the user wants screenshots — httpx covers tech stack, title, "
            "TLS, favicon in one pass."
        ),
        "output_file": "httpx.jsonl",
        "upload_after": True,
        "synthesized_after": "masscan_to_rustscan_swap",
    }


def build_recommended_sequence(
    subnet_cidrs: List[str],
    scope_size: Dict[str, Any],
    known_hosts_with_ports: int,
    environment: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Build a concrete 3-step starter sequence tailored to this scope.

    The agent can still deviate, but a pre-stitched plan eliminates the
    common failure mode of "agent picks discovery tool from catalog
    entry #1 and lets it run for hours."  Emphasizes staged execution:
    fast sweep → live-hit list → service probe only on hits.

    v2.13.0 — added because recon runs were defaulting to a full-CIDR
    nmap -sV pass right after a single `nmap -sn`, which is both slow
    and the wrong shape (probes dead addresses).

    v2.39.0 — accepts the optional ``environment`` probe dict.  When the
    probe shows a step's default tool is missing or broken (e.g. httpx
    on PATH is the Python CLI, not ProjectDiscovery's binary; masscan
    is missing or lacks raw-socket privileges), the corresponding step
    is swapped for a working alternative.  Swapped steps carry
    ``original_tool`` + ``swap_reason`` fields so the agent / UI can
    surface the adaptation transparently.  Closes recon feedback #2
    where the static sequence proposed httpx after preflight flagged it
    as broken, forcing the agent to manually renegotiate the plan.
    """
    bucket = scope_size.get("size_bucket", "small")
    recommended = scope_size.get("recommended_discovery", "nmap_sn")
    durations = scope_size.get("estimated_durations", {})
    # See build_tool_catalog for why these are joined differently.
    # Pre-v2.42.0 the masscan + rustscan branches used a single first_cidr,
    # silently dropping every subnet after the first (review C-RR-1).
    cidr_list = " ".join(subnet_cidrs) if subnet_cidrs else "{cidr}"
    rustscan_cidr_list = ",".join(subnet_cidrs) if subnet_cidrs else "{cidr}"
    masscan_rate = masscan_rate_for_bucket(bucket)

    if recommended == "masscan":
        step1_cmd = (
            f"sudo masscan --rate={masscan_rate} -p22,80,443,445,3389,8080,8443 "
            f"-oX masscan-sweep.xml {cidr_list}"
        )
        step1_note = (
            f"{scope_size.get('total_addresses')} addresses across {len(subnet_cidrs)} subnet(s) "
            f"in scope — a full nmap -sn would take hours.  Masscan at {masscan_rate}pps covers "
            f"the same range in a few minutes and produces a live-host list. "
            f"**Requires raw-socket privileges** — uses `sudo` by default; "
            f"alternative is `sudo setcap cap_net_raw=eip $(which masscan)` "
            f"once and then run without sudo.  If neither is available "
            f"(unprivileged agent, no sudo grant), **fall back to rustscan** "
            f"(~5–15 min instead of ~1–3 min): "
            f"`rustscan -a {rustscan_cidr_list} --ulimit 5000 -- -sV -sC --top-ports 1000 "
            f"-oX rustscan-nmap.xml`.  Preflight check to detect which path works: "
            f"`sudo -n true 2>/dev/null || getcap $(which masscan) 2>/dev/null "
            f"| grep -q cap_net_raw || echo 'FALL BACK TO RUSTSCAN'`."
        )
    elif recommended == "rustscan_nmap":
        step1_cmd = (
            f"rustscan -a {rustscan_cidr_list} --ulimit 5000 -- "
            f"-sV -sC --top-ports 1000 -oX rustscan-nmap.xml"
        )
        step1_note = (
            f"{scope_size.get('total_addresses')} addresses across {len(subnet_cidrs)} subnet(s) "
            f"in scope — rustscan finds the live hits in seconds, the piped nmap "
            f"service-probes only those hits.  Single output file, one upload."
        )
    else:  # nmap_sn
        step1_cmd = (
            f"nmap -sn -T3 -PE -PP -PS22,80,443 -PA80 "
            f"-oX nmap-sweep.xml {cidr_list}"
        )
        step1_note = (
            f"{scope_size.get('total_addresses')} addresses across {len(subnet_cidrs)} subnet(s) "
            f"in scope — small enough that a direct nmap sweep completes in minutes with full "
            f"ICMP + selective TCP coverage."
        )

    sequence = [
        {
            "step": 1,
            "phase": "discovery",
            "command": step1_cmd,
            "estimated_duration": durations.get(recommended),
            "note": step1_note,
            "output_file": (
                "masscan-sweep.xml" if recommended == "masscan"
                else "rustscan-nmap.xml" if recommended == "rustscan_nmap"
                else "nmap-sweep.xml"
            ),
            "upload_after": True,
        },
    ]

    # Step 2: service probe only on live hits — never on full CIDR.
    # Rustscan path combined discovery + service probe in step 1, so
    # step 2 is web fingerprinting instead of a second nmap pass.
    if recommended == "rustscan_nmap":
        sequence.append({
            "step": 2,
            "phase": "web",
            "command": (
                "httpx -l web_targets.txt -sc -title -server -tech-detect "
                "-favicon -tls-probe -cdn -json -o httpx.jsonl"
            ),
            "estimated_duration": "~2–10 min depending on web host count",
            "note": (
                "Step 1 already produced service-detected hosts.  Generate "
                "web_targets.txt from the rustscan-nmap.xml (hosts with "
                "port 80/443/8080/8443 open), then httpx fingerprints each. "
                "Skip eyewitness unless the user wants screenshots — httpx "
                "covers tech stack, title, TLS, favicon in one pass."
            ),
            "output_file": "httpx.jsonl",
            "upload_after": True,
        })
    else:
        sequence.append({
            "step": 2,
            "phase": "service_probe",
            "command": (
                "nmap -sV -sC -T3 --top-ports 1000 "
                "-iL live-hosts.txt -oX nmap-services.xml"
            ),
            "estimated_duration": "~5–30 min depending on live host count",
            "note": (
                "Extract the live-host IP list from step 1's upload (check "
                "/agent/recon/summary for hosts_discovered + the hosts[] "
                "breakdown, or parse the raw file).  Top-1000 ports covers "
                ">95% of real services.  Do NOT run this on the full CIDR — "
                "it would probe dead addresses for hours."
            ),
            "output_file": "nmap-services.xml",
            "upload_after": True,
        })

    # Step 3: web fingerprinting for the service-probe path; DNS or
    # credentialed scanning for the rustscan path (which already did web
    # fingerprinting in step 2).
    if recommended == "rustscan_nmap":
        sequence.append({
            "step": 3,
            "phase": "optional",
            "command": "# Iterate based on findings: deep scan (-p-) on high-value hosts, nikto on unusual web apps, credentialed scans if integrations configured.",
            "estimated_duration": "varies",
            "note": (
                "After step 2, review /agent/recon/summary and decide: "
                "escalate to nmap -p- on domain controllers / critical "
                "servers, run nuclei/nessus via integrations, or complete "
                "the session.  Avoid running additional broad sweeps."
            ),
            "output_file": None,
            "upload_after": False,
        })
    else:
        sequence.append({
            "step": 3,
            "phase": "web",
            "command": (
                "httpx -l web_targets.txt -sc -title -server -tech-detect "
                "-favicon -tls-probe -cdn -json -o httpx.jsonl"
            ),
            "estimated_duration": "~2–10 min depending on web host count",
            "note": (
                "Extract hosts with port 80/443/8080/8443 open from step 2's "
                "upload.  httpx is the canonical web fingerprinter — runs in "
                "seconds per target, populates the web_interfaces table with "
                "tech stack, title, TLS, favicon hash."
            ),
            "output_file": "httpx.jsonl",
            "upload_after": True,
        })
        # v2.45.5 — eyewitness promoted from fallback-only to an explicit
        # OPTIONAL step 4.  Previously eyewitness reached the sequence
        # solely via the httpx→eyewitness swap rule (fires only when
        # httpx is unavailable), so a normal run captured NO
        # screenshots at all — httpx -json carries no image data and
        # the catalog's httpx command has no -ss flag.  This step is
        # the documented "httpx culls dead targets, eyewitness
        # screenshots the survivors" two-stage flow, made explicit.
        # It feeds the WebInterface.screenshot_path the eyewitness
        # parser already populates.  Optional + approval-gated: the
        # agent runs it only if the operator wants visual triage.
        sequence.append({
            "step": 4,
            "phase": "web_screenshot",
            "command": (
                "eyewitness --web -f web_targets.txt -d eyewitness-results "
                "--no-prompt"
            ),
            "estimated_duration": "~5–20 min — headless browser per web host",
            "note": (
                "OPTIONAL visual-triage pass.  Run AFTER step 3's httpx "
                "has culled dead targets — point eyewitness at the SURVIVING "
                "web hosts only (the live URLs httpx confirmed), not the raw "
                "web_targets list.  eyewitness spins a headless browser per "
                "target so it is minutes-per-host, not seconds — skip it on "
                "very large web surfaces unless the operator wants "
                "screenshots.  Upload populates WebInterface.screenshot_path; "
                "the screenshots then render in the host detail UI.  If "
                "eyewitness isn't installed, httpx with `-ss` is the "
                "alternative — ask the operator before adding screenshot "
                "capture to the httpx step."
            ),
            "output_file": "eyewitness-results/report.json",
            "upload_after": True,
            "optional": True,
        })

    # v2.13.2 — default-flip.  Previously, when known hosts existed we
    # suggested skipping step 1 (discovery) — which silently narrowed
    # scope and missed new / changed hosts.  feedback #5 showed an agent
    # doing exactly that.  New default: the plan ALWAYS leads with
    # comprehensive discovery.  When prior data exists, step-0 becomes
    # a warning that flags the narrowing option instead of recommending
    # it.  The pre-built command + host list for the narrow path is
    # available at `known_hosts_probe` on the context response.
    if known_hosts_with_ports > 0:
        sequence.insert(0, {
            "step": 0,
            "phase": "note",
            "command": None,
            "estimated_duration": None,
            "note": (
                f"This scope already has {known_hosts_with_ports} host(s) "
                f"with open ports from prior recon.  The default plan below "
                f"runs **comprehensive** fresh discovery to catch new or "
                f"changed hosts.  If the user instead asks you to *only* "
                f"deepen on the already-known hosts (faster, but misses "
                f"anything new since the last recon), use the pre-built "
                f"command in `known_hosts_probe.command` from "
                f"`/agent/recon/context` with `known_hosts_probe.live_hosts` "
                f"as the target list — do NOT silently narrow on your own."
            ),
            "output_file": None,
            "upload_after": False,
        })

    # v2.39.0 introduced environment-aware substitutions; v2.42.0 moved the
    # rules to the _SWAP_RULES registry above so adding a new rule is a row,
    # not an extra branch in a dispatch loop.
    if environment:
        _apply_environment_substitutions(sequence, environment)

    return sequence
