"""
Content detection — extracted from ingestion_service.py in v2.27.0.

A family of small "does this file look like X?" predicates the
ingestion service uses to dispatch an upload to the right parser
when the file extension alone isn't decisive.  These were class
methods bound to ``IngestionService`` (no ``self`` references) —
hoisting them to module level lets the parser-attempt builder be
tested in isolation and keeps ``ingestion_service.py`` focused on
the queue + lifecycle concerns it actually owns.

All functions take a small sample of the file (the first ~64 KiB
as read by ``IngestionService._read_sample``) plus the user-
supplied filename, and return a bool.  False positives are
acceptable for the heuristics — the parser itself will reject
content it can't make sense of — but false negatives cost the user
a successful auto-detection.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional, Tuple


# v2.45.1 — match the FIRST element tag of an XML document, skipping
# the XML prolog.  Used by the structural format detectors below —
# content-based keyword scanning is unreliable on XML formats that
# nest captured text (e.g. nmap NSE script output can include cert
# subjects or page titles with arbitrary content from the scanned
# target).
#
# v2.49.5 — the v2.45.1 single-shot regex assumed a fixed prolog
# order (decl → DOCTYPE → PIs → root) and did not handle XML
# comments at all.  Real ``nmap -oX`` output writes:
#     <?xml version="1.0" encoding="UTF-8"?>
#     <?xml-stylesheet href="file:///.../nmap.xsl" type="text/xsl"?>
#     <!-- Nmap 7.94 scan initiated Mon May 22 ... -->
#     <nmaprun ...>
# That prolog contains a comment between the stylesheet PI and the
# root, which the old regex couldn't skip — so it returned ``None``,
# both ``looks_like_nmap_xml`` and the in-``looks_like_openvas`` nmap
# guard short-circuited, and detection fell through to the keyword
# fallback.  That fallback then mis-fired whenever NSE script output
# captured text like ``Greenbone AG`` (TLS cert subject) plus a
# ``<report`` token elsewhere in the file — re-opening the exact
# class of bug v2.45.1 was meant to close.
#
# The fix: consume prolog items iteratively in any order — XML
# declarations, DOCTYPEs, processing instructions, AND comments —
# then capture the next element name.  No assumption about ordering;
# any number of each construct can appear.
_PROLOG_ITEM_RE = re.compile(
    rb"""
    \s*                       # leading whitespace
    (?:
        <\?xml[^>]*\?>        # XML declaration
      | <!DOCTYPE[^>]*>       # DOCTYPE (no internal-subset support - uncommon
                              # in scanner output and would need a parser, not
                              # a regex, to handle correctly)
      | <\?[^>]*\?>           # processing instruction
      | <!--.*?-->            # comment (non-greedy, spans newlines via DOTALL)
    )
    """,
    re.VERBOSE | re.DOTALL,
)
_FIRST_ELEMENT_NAME_RE = re.compile(
    rb"\s*<([A-Za-z_][A-Za-z0-9_.\-]*)",
)


def _xml_root_element(sample: bytes) -> str | None:
    """Return the root element name of an XML sample, or None if the
    sample doesn't look like XML at all.  Lowercase for comparison
    convenience.  Works on the raw bytes so we don't mis-handle BOMs
    or non-UTF-8 encodings — we only need the ASCII subset to read
    the tag name.
    """
    # Strip a UTF-8 BOM if present — some Windows scanner exports
    # write one and ``\s*`` won't skip it (BOM is not whitespace).
    if sample.startswith(b"\xef\xbb\xbf"):
        sample = sample[3:]
    pos = 0
    while True:
        m = _PROLOG_ITEM_RE.match(sample, pos)
        if not m:
            break
        pos = m.end()
    m = _FIRST_ELEMENT_NAME_RE.match(sample, pos)
    if not m:
        return None
    try:
        return m.group(1).decode("ascii").lower()
    except UnicodeDecodeError:  # pragma: no cover — unlikely on ASCII tag names
        return None


# ──────────────────────────────────────────────────────────────────────
# Structural JSON probe — used by the format detectors below to inspect
# the actual key set of the first record instead of substring-matching
# on the raw bytes.  The regression history of the substring approach
# is the case material: v2.45.1 routed masscan JSON to NaabuParser
# because both formats happen to contain the string ``"port"`` and
# ``"ip"`` somewhere in their bodies; that re-fired periodically as
# tools added overlapping fields.  The structural probe inspects the
# top-level shape (array element, single object, or JSONL line) and
# returns the first record's actual dict so detectors can write
# ``"ports" in rec`` instead of ``b'"ports"' in sample`` — same intent,
# but no false positives from a nested string somewhere downstream.
#
# Bounded: ``raw_decode`` only parses one object regardless of file
# size, and JSONL fallback only reads the first non-empty line.  No
# memory cost even on multi-hundred-MB scanner exports.


def _peek_json_shape(sample: bytes) -> Tuple[str, Optional[dict]]:
    """Probe the top-level JSON shape of ``sample``.

    Returns ``(kind, first_record)`` where ``kind`` is one of:

    * ``'array'``  — sample starts with ``[`` and the first element
      was successfully parsed (record is that element if it's a dict,
      None otherwise).
    * ``'object'`` — sample starts with ``{`` and parses as a single
      JSON object (record is the object).
    * ``'jsonl'``  — sample doesn't start with ``[`` or ``{`` but the
      first non-empty line is a parseable JSON object (record is that
      line's parsed dict).
    * ``'unknown'`` — none of the above; caller should fall back to
      whatever text-path heuristic applies (gobuster output, smbmap
      console, …).

    The record is always either a dict or None.  Detectors should
    treat ``record is None`` the same way they treat ``kind ==
    'unknown'`` — neither tells them anything about the format.
    """
    text = sample.decode("utf-8", errors="ignore").lstrip("﻿").lstrip()
    if not text:
        return ("unknown", None)

    if text.startswith("["):
        # Skip the bracket + any whitespace, then raw_decode the first
        # element.  If the element isn't a dict (e.g. ``[[1,2], [3,4]]``)
        # we still return ``kind='array'`` so the caller knows the
        # outer shape — they just won't be able to inspect keys.
        idx = 1
        n = len(text)
        while idx < n and text[idx].isspace():
            idx += 1
        if idx >= n:
            return ("array", None)
        try:
            first, _ = json.JSONDecoder().raw_decode(text[idx:])
        except json.JSONDecodeError:
            return ("array", None)
        return ("array", first if isinstance(first, dict) else None)

    if text.startswith("{"):
        try:
            obj, _ = json.JSONDecoder().raw_decode(text)
        except json.JSONDecodeError:
            return ("object", None)
        if not isinstance(obj, dict):
            return ("object", None)
        # Detect JSONL by checking whether there's another '{' after
        # the parsed object — if so, the "object" classification was
        # misleading and the caller should treat this as JSONL.  The
        # actual first record is the same though, so this only matters
        # for callers that distinguish JSONL from object-of-records.
        return ("object", obj)

    # JSONL fallback — try the first non-empty line that opens with
    # ``{``.  Bound the scan to a few KiB of the sample so a giant
    # leading comment block (rare but possible) doesn't burn cycles.
    scan = text[:8192]
    for line in scan.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return ("unknown", None)
        return ("jsonl", obj if isinstance(obj, dict) else None)

    return ("unknown", None)


def _has_any(record: Optional[dict], keys: Tuple[str, ...]) -> bool:
    """Return True iff ``record`` is a dict and contains any of the
    given keys.  Helper for the structural detectors so the
    "is dict + has key" check doesn't repeat at every call site.
    """
    if not isinstance(record, dict):
        return False
    return any(k in record for k in keys)


def is_nessus_sample(sample: bytes) -> bool:
    sample_text = sample.decode("utf-8", errors="ignore").lower()
    indicators = [
        "nessusclientdata_v2",
        "<reporthost",
        "pluginid",
        "tenable",
        "reportitem",
    ]
    return sum(1 for token in indicators if token in sample_text) >= 3

def looks_like_netexec(sample: bytes, filename: str) -> bool:
    lowered = sample.decode("utf-8", errors="ignore").lower()
    name = filename.lower()
    indicators = [
        "netexec",
        "nxc",
        "spider",
        "smb         ",
        "ldap        ",
        "winrm       ",
    ]
    if any(token in lowered for token in indicators):
        return True
    return "netexec" in name or "nxc" in name

def looks_like_openvas(sample: bytes, filename: str) -> bool:
    """Detect an OpenVAS/Greenbone XML report.

    v2.45.1 — STRUCTURAL detection.  The old "openvas/greenbone keyword
    anywhere in the body" rule produced false positives on legitimate
    nmap XML files whose NSE script output captured strings like
    ``"OPENVAS Scan"`` (an HTTP title scraped by ``http-title``) or
    ``"Greenbone AG"`` (a TLS cert subject scraped by ``ssl-cert``).
    Those nmap files routed to OpenVASParser before NmapXMLParser
    got a chance — operators had to sanitize the offending text out
    of their own scan outputs as a workaround.

    The new rule:
      * If the XML root element is ``nmaprun``, this is unambiguously
        nmap output — return False even if the body mentions OpenVAS
        somewhere.
      * If the XML root is one of OpenVAS's well-known shapes
        (``report``, ``get_reports_response``, ``openvas-results``,
        ``omp-report``), return True.
      * Filename match (``openvas``, ``greenbone``, ``gvm`` in the
        filename) still wins — operators who name their files
        descriptively get explicit routing.
      * Last-resort fallback: only if the file LACKS a recognizable
        XML root entirely do we fall back to keyword scanning, and
        even then we require BOTH a vendor keyword AND a structural
        OpenVAS element name to fire — protects against keyword-only
        matches in non-XML or malformed payloads.
    """
    name = filename.lower()
    if any(token in name for token in ["openvas", "greenbone", "gvm"]):
        return True

    root = _xml_root_element(sample)
    if root == "nmaprun":
        # Unambiguous nmap output — never route to OpenVAS.
        return False
    if root in {"report", "get_reports_response", "openvas-results", "omp-report"}:
        return True
    if root is not None:
        # XML, but not one of OpenVAS's shapes.  Don't claim it.
        return False

    # No identifiable XML root — fall back to keyword pairing only
    # (require BOTH a vendor keyword AND a structural element name).
    lowered = sample.decode("utf-8", errors="ignore").lower()
    has_vendor = "openvas" in lowered or "greenbone" in lowered
    has_structure = "<result>" in lowered or "<report" in lowered
    return has_vendor and has_structure


def looks_like_nmap_xml(sample: bytes) -> bool:
    """Detect nmap XML by root element.

    Added in v2.45.1 alongside the looks_like_openvas tightening, so
    the ingestion dispatcher can put nmap_xml ahead of openvas_xml
    when both detectors would have matched the old keyword-only
    openvas rule.  Cheap to call (regex over the first element of
    the sample).
    """
    return _xml_root_element(sample) == "nmaprun"

def looks_like_naabu(sample: bytes, filename: str) -> bool:
    """Naabu signature: top-level array (or JSONL) of flat ``{ip, port,
    protocol}`` records.  Distinguished from masscan / nikto / httpx
    by inspecting the first record's key set — no substring matching
    on the raw bytes, which produced the v2.45.1 misroute history
    (the literal ``"port"`` and ``"ip"`` strings appear in many
    formats whose records have nothing in common with naabu's shape).
    """
    if "naabu" in filename.lower():
        return True
    # v2.57.0 — early-out when the filename screams nikto.  Pre-fix,
    # the JSON-shape check below would happily accept nikto's
    # ``{ip, port, id, msg, severity}`` records because none of the
    # existing exclusions matched (no ``vulnerabilities`` array, no
    # httpx markers).  The bulk-upload regression on 2026-05-26
    # confirmed this in a real workload: nikto_sample.json landed as
    # ``tool_name='naabu'``.  Filename routing is the cheapest fix
    # for the common case; the per-record nikto-XXX id guard below
    # covers the residual where the filename was renamed.
    if "nikto" in filename.lower():
        return False
    _, rec = _peek_json_shape(sample)
    if rec is None:
        return False
    # naabu emits {"ip": ..., "port": <int>, "protocol": ..., "host": ...}
    # — singular ``port``, no nested ``ports`` array.
    if "port" not in rec:
        return False
    if "ip" not in rec and "host" not in rec:
        return False
    # Exclude masscan: nested ``ports`` array.
    if "ports" in rec:
        return False
    # Exclude nikto JSON: per-finding ``vulnerabilities`` array
    # (Nikto's --Format json wraps findings in this) OR per-record
    # ``id`` starting with ``nikto-`` (the actual shape Nikto's stock
    # output writes — observed in artifacts/manual/nikto_sample.json).
    # Both forms exist in the wild because Nikto's JSON output mode
    # has changed across versions.
    if "vulnerabilities" in rec:
        return False
    nikto_id = str(rec.get("id") or "")
    if nikto_id.lower().startswith("nikto-"):
        return False
    # Belt-and-braces: ``msg`` + ``severity`` together is a Nikto
    # vocabulary that naabu doesn't share.  Catches renamed-id
    # configurations and other variants.
    if "msg" in rec and "severity" in rec:
        return False
    # Exclude httpx: its records carry web-fingerprint markers naabu
    # never emits.
    if _has_any(rec, ("url", "tech", "webserver", "title")):
        return False
    return True

def looks_like_rustscan(sample: bytes, filename: str) -> bool:
    lowered = sample.decode("utf-8", errors="ignore").lower()
    name = filename.lower()
    return ("rustscan" in name) or ("open " in lowered and "->" in lowered) or ("rustscan" in lowered)

def looks_like_amass(sample: bytes, filename: str) -> bool:
    """Detect Amass / Subfinder JSON output.

    Each record carries a hostname (``name``) plus either a single
    ``addresses`` array (amass) or ``ip`` field (subfinder).  Filename
    match still wins outright.  Pure substring detection (the pre-
    structural form) was too broad — ``"name"`` and ``"addresses"``
    appear in many other tools' nested fields.
    """
    name = filename.lower()
    if any(token in name for token in ["amass", "subfinder"]):
        return True
    _, rec = _peek_json_shape(sample)
    if rec is None:
        return False
    # Amass: {"name": "<host>", "addresses": [{"ip": "...", ...}, ...]}
    # Subfinder: {"name": "<host>", "host": "<host>", "input": "<root>"}
    has_name = "name" in rec
    if not has_name:
        return False
    if "addresses" in rec:
        return True
    # Subfinder's distinguishing field — combined with ``name`` it's
    # unlikely to collide with anything else.
    if "input" in rec and "source" in rec:
        return True
    return False

def looks_like_nikto(sample: bytes, filename: str) -> bool:
    lowered = sample.decode("utf-8", errors="ignore").lower()
    name = filename.lower()
    return "nikto" in name or "nikto v" in lowered or "target ip:" in lowered

def looks_like_eyewitness_json(sample: bytes) -> bool:
    """EyeWitness JSON: per-target record carrying ``screenshot_path``
    (modern) or ``remote_system`` (legacy), plus optional ``page_title``
    + ``url``.  Filename routing catches the common cases; this is
    the fallback for unusually-named exports.

    May surface as a top-level array of records OR an object with a
    ``results``/``pages``/``servers`` array — handle both by peeking
    at the record itself rather than the wrapping shape.
    """
    _, rec = _peek_json_shape(sample)
    if rec is None:
        return False
    # If the top-level is an EyeWitness wrapper object, the wrapper
    # itself doesn't carry the marker fields — they're on inner
    # records.  Probe the wrapper for known array keys and look
    # inside.
    candidates = [rec]
    for key in ("results", "pages", "servers", "data"):
        val = rec.get(key)
        if isinstance(val, list) and val and isinstance(val[0], dict):
            candidates.append(val[0])
    for c in candidates:
        if _has_any(c, ("screenshot_path", "remote_system")):
            return True
        if "page_title" in c and "url" in c:
            return True
    return False

def looks_like_smbmap(sample: bytes, filename: str) -> bool:
    """SMBMap: filename win + console-output marker + structural JSON
    check.  The structural path looks for ``shares`` as a key in the
    first record (rather than substring-matching ``"shares"`` which
    collides with several other formats).
    """
    name = filename.lower()
    if "smbmap" in name:
        return True
    lowered = sample.decode("utf-8", errors="ignore").lower()
    # Console-output path — SMBMap's tabular text uses ``[+]`` plus
    # the literal column header ``disk``.
    if "[+]" in lowered and "disk" in lowered:
        return True
    # JSON path — first record has ``shares`` (array of share dicts).
    _, rec = _peek_json_shape(sample)
    if rec is None:
        return False
    if "shares" in rec:
        return True
    # Object-wrapped form: {"hosts": [{"ip": ..., "shares": [...]}, ...]}
    hosts = rec.get("hosts") if isinstance(rec, dict) else None
    if isinstance(hosts, list) and hosts and isinstance(hosts[0], dict):
        if "shares" in hosts[0]:
            return True
    return False

def looks_like_dirbuster(sample: bytes, filename: str) -> bool:
    """Detect ffuf / feroxbuster / dirsearch JSON + gobuster text.

    Each JSON dialect probes the first record:
      * ffuf: wrapper object with ``results`` array; each result has
        ``url`` + ``status``.
      * feroxbuster / dirsearch: flat array of records with ``url`` +
        ``status_code`` (ferox) or ``url`` + ``status`` (dirsearch).
    Gobuster: text path uses the ``(Status: NNN)`` line marker.
    """
    name = filename.lower()
    tool_names = ["dirbuster", "gobuster", "feroxbuster", "ffuf", "dirsearch"]
    if any(t in name for t in tool_names):
        return True

    _, rec = _peek_json_shape(sample)
    if isinstance(rec, dict):
        # ffuf wrapper: {"results": [{"url": ..., "status": ...}, ...]}
        results = rec.get("results")
        if isinstance(results, list) and results and isinstance(results[0], dict):
            inner = results[0]
            if "url" in inner and ("status" in inner or "status_code" in inner):
                return True
        # Flat record (feroxbuster / dirsearch / ffuf already unwrapped)
        if "url" in rec and ("status_code" in rec or "status" in rec):
            # Avoid claiming httpx records — they also have url+status_code
            # but carry web-fingerprint markers dirbusters never emit.
            if _has_any(rec, ("tech", "webserver", "title")):
                return False
            return True

    # Gobuster text-output fallback.
    lowered = sample.decode("utf-8", errors="ignore").lower()
    if "(status:" in lowered and "http" in lowered:
        return True
    return False

def looks_like_dns_csv(sample: bytes) -> bool:
    """Detect DNS-records CSV: header row contains a type column AND a
    name column AND an address column, in any order.  The normalisation
    in ``DNSParser._normalize_row_keys`` accepts several aliases per
    column; this heuristic mirrors that set so a header that the parser
    would accept also passes the dispatcher gate.

    Returning False routes a non-DNS CSV to a `parse_errors` row
    instead of into a silent `tool_name='dns'` scan with zero records.
    """
    text = sample.decode("utf-8", errors="ignore")
    header = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        header = stripped
        break
    if not header:
        return False

    # Try the three delimiters DNSParser itself sniffs for.
    for delim in (",", "\t", ";"):
        if delim not in header:
            continue
        cols = {c.strip().strip('"').lower() for c in header.split(delim)}
        type_aliases = {"record_type", "recordtype", "type", "record type"}
        name_aliases = {"name", "domain", "dns_name", "hostname", "host"}
        addr_aliases = {"address", "ip_address", "ip", "value", "target"}
        if cols & type_aliases and cols & name_aliases and cols & addr_aliases:
            return True
    return False


def looks_like_gnmap(sample: bytes) -> bool:
    """Detect nmap/masscan greppable output (lines starting with 'Host:')."""
    text = sample.decode("utf-8", errors="ignore")
    host_lines = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Host:") and ("Ports:" in stripped or "Status:" in stripped):
            host_lines += 1
            if host_lines >= 2:
                return True
        # Only match '# Nmap' headers — '# Masscan' headers appear in both
        # gnmap (-oG) and list (-oL) output, so they are not a reliable
        # indicator of greppable format.
        if stripped.startswith("# Nmap"):
            return True
    return host_lines >= 1

def looks_like_masscan_list(sample: bytes) -> bool:
    """Detect masscan list output (``Timestamp: … Host: … Ports: …``)."""
    text = sample.decode("utf-8", errors="ignore")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Timestamp:") and "Host:" in stripped and "Ports:" in stripped:
            return True
        # Comment header unique to masscan output files
        if stripped.startswith("# Masscan") or stripped.startswith("# masscan"):
            return True
    return False

def looks_like_masscan_xml(sample: bytes) -> bool:
    """Detect masscan XML output (nmaprun element with scanner='masscan')."""
    text = sample.decode("utf-8", errors="ignore").lower()
    return 'scanner="masscan"' in text or "scanner='masscan'" in text

def looks_like_masscan_json(sample: bytes) -> bool:
    """Masscan JSON: top-level array of records with ``ip`` + nested
    ``ports`` array.  Each port object carries ``port`` + ``proto``
    + ``status`` (open).  Distinguished from naabu (which has flat
    singular ``port`` per record, no nested ``ports``) by inspecting
    the first record's shape.
    """
    _, rec = _peek_json_shape(sample)
    if rec is None:
        return False
    # Top-level identifiers
    if "ip" not in rec and "addr" not in rec:
        return False
    ports = rec.get("ports")
    if not isinstance(ports, list) or not ports:
        return False
    inner = ports[0]
    if not isinstance(inner, dict):
        return False
    # Inner port object has port + proto (and usually status)
    return "port" in inner and "proto" in inner

def looks_like_bloodhound(sample: bytes, filename: str) -> bool:
    """BloodHound / SharpHound: filename win + structural probe.

    Two common JSON shapes — flat array of node dicts (each carrying
    ``Properties`` with ``dnshostname``/``name``), or wrapped object
    with ``data``/``computers`` array.  The wrapped form is what
    SharpHound emits.
    """
    name = filename.lower()
    if any(token in name for token in ["bloodhound", "sharphound", "computers"]):
        return True
    _, rec = _peek_json_shape(sample)
    if rec is None:
        return False
    # Flat node form: top-level array element is a node dict with Properties
    props = rec.get("Properties") if isinstance(rec, dict) else None
    if isinstance(props, dict) and _has_any(
        props, ("dnshostname", "name", "operatingsystem", "objectid")
    ):
        return True
    # Wrapped form: {"data": [...]} or {"computers": [...]}
    for key in ("data", "computers"):
        wrapped = rec.get(key) if isinstance(rec, dict) else None
        if isinstance(wrapped, list) and wrapped and isinstance(wrapped[0], dict):
            inner_props = wrapped[0].get("Properties")
            if isinstance(inner_props, dict) and _has_any(
                inner_props, ("dnshostname", "name", "operatingsystem", "objectid")
            ):
                return True
    return False


def looks_like_dnsx(sample: bytes, filename: str) -> bool:
    """Detect dnsx (ProjectDiscovery) JSON / JSONL output.

    dnsx writes one record per query with a ``host`` field plus one or
    more record-type arrays (``a``, ``aaaa``, ``ptr``, ``cname``,
    ``mx``, ``ns``, ``txt``, ``soa``).  A ``resolver`` field is
    typically present (which NS answered) but isn't required — older
    dnsx versions and some flag combinations omit it.

    Filename match wins outright (operators commonly name files like
    ``dnsx-output.json``); structural detection covers the rest.
    Distinguishes from amass (``name`` + ``addresses``) and httpx
    (``url`` + ``tech``) by the combination of ``host`` + a DNS
    record-type field.
    """
    if "dnsx" in filename.lower():
        return True
    _, rec = _peek_json_shape(sample)
    if rec is None:
        return False
    if "host" not in rec:
        return False
    return _has_any(rec, ("a", "aaaa", "ptr", "cname", "mx", "ns", "txt", "soa"))
