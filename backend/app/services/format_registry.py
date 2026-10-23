"""The ONE parser registry: every format the ingestion dispatcher knows, by
its ``file_type`` key, and what recognises it.

Everything that used to be a separate list a new parser had to be added to is
built from this module (hazard H-g, backlog 2026-10-22):

* the dispatcher's attempts — ``detect(filename, sample)``, which
  ``IngestionService._build_parsing_attempts`` returns when there is no
  operator override: ``DETECTION`` says, per file extension, which formats are
  tried in which order, and each ``FormatSpec`` says what recognises it (its
  ``sniffer``, a filename ``hint``);
* the dispatch map — ``ingestion_service.build_parser_dispatch_map`` is the
  parser classes named here;
* the sniffer surface — ``sniffers()`` names the ``looks_like_*`` predicates in
  ``parsers/content_detection.py`` this registry uses, and a test holds that
  equal to the module's public ones;
* the coverage page — ``app/data/parser_coverage.json`` must give every key
  here to exactly one tool (``tests/test_parser_coverage.py``);
* the staged review's and the upload chooser's format list, the job's
  detected → override → final chain and its labels.

Adding a parser = one ``_spec`` line (with its sniffer), its key in the
``DETECTION`` order of the extensions it arrives with, and its coverage rows.
The four guard tests read this module rather than a list of their own
(``test_parser_dispatch_contract``, ``test_ingestion_format_chain``, the
content-detection surface test in ``test_phase1_regressions``,
``test_parser_coverage``); ``tests/test_parser_registry_old_equals_new.py``
pins what detection answered before the registry was made the one source.

Imports stay lazy: parser modules and sniffers are imported when a file is
detected or dispatched, never at import time, so a parser module that cannot
be imported degrades to "unavailable" instead of breaking the service.
"""
from __future__ import annotations

import importlib
import inspect
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Dict, List, Optional, Tuple, Type

logger = logging.getLogger(__name__)

# Where a sniffer named without a module lives.
CONTENT_DETECTION = "app.parsers.content_detection"


@dataclass(frozen=True)
class FormatSpec:
    file_type: str
    label: str
    family: str          # port | vuln | web | dns | auth | other
    module: str
    class_name: str
    description: str
    # What recognises the content: a predicate's name in
    # ``parsers/content_detection.py``, or ``"module:function"`` for one that
    # lives beside its parser.  None: the extension alone decides.
    sniffer: Optional[str] = None
    # Filename tokens that recognise the format without its content (a hint,
    # tried before the sniffer — the dispatcher's historical behaviour).
    hints: Tuple[str, ...] = ()


def _spec(file_type: str, label: str, family: str, module: str, class_name: str, description: str,
          sniffer: Optional[str] = None, hints: Tuple[str, ...] = ()) -> FormatSpec:
    return FormatSpec(file_type, label, family, module, class_name, description, sniffer, hints)


_EYEWITNESS_HINTS = ("eyewitness", "report")

FORMATS: Dict[str, FormatSpec] = {
    s.file_type: s for s in (
        _spec("nmap_xml", "Nmap XML", "port", "app.parsers.nmap_parser", "NmapXMLParser", "Nmap XML file",
              "looks_like_nmap_xml"),
        _spec("nmap_gnmap", "Nmap grepable (.gnmap)", "port", "app.parsers.gnmap_parser", "GnmapParser", "Nmap .gnmap file"),
        _spec("gnmap_txt", "Nmap grepable (.txt)", "port", "app.parsers.gnmap_parser", "GnmapParser", "Greppable scan output (.txt)",
              "looks_like_gnmap"),
        _spec("masscan_xml", "Masscan XML", "port", "app.parsers.masscan_parser", "MasscanParser", "Masscan XML file",
              "looks_like_masscan_xml"),
        _spec("masscan_json", "Masscan JSON", "port", "app.parsers.masscan_parser", "MasscanParser", "Masscan JSON file",
              "looks_like_masscan_json", ("masscan",)),
        _spec("masscan_list", "Masscan list (-oL)", "port", "app.parsers.masscan_parser", "MasscanParser", "Masscan list output file",
              "looks_like_masscan_list"),
        _spec("naabu_json", "Naabu JSON", "port", "app.parsers.naabu_parser", "NaabuParser", "Naabu JSON output",
              "looks_like_naabu"),
        _spec("naabu_output", "Naabu host:port text", "port", "app.parsers.naabu_parser", "NaabuParser", "Naabu output file",
              "looks_like_naabu"),
        _spec("rustscan_output", "RustScan text", "port", "app.parsers.rustscan_parser", "RustScanParser", "RustScan output file",
              "looks_like_rustscan"),
        _spec("nessus_xml", "Nessus (.nessus)", "vuln", "app.services.nessus_integration_service", "NessusIntegrationService", "Nessus vulnerability scan",
              "is_nessus_sample"),
        _spec("openvas_xml", "OpenVAS / Greenbone XML", "vuln", "app.parsers.openvas_parser", "OpenVASParser", "OpenVAS/Greenbone XML report",
              "looks_like_openvas"),
        _spec("nikto_json", "Nikto JSON", "vuln", "app.parsers.nikto_parser", "NiktoParser", "Nikto JSON report",
              "looks_like_nikto"),
        _spec("nikto_csv", "Nikto CSV", "vuln", "app.parsers.nikto_parser", "NiktoParser", "Nikto CSV report",
              "looks_like_nikto"),
        _spec("nikto_output", "Nikto text", "vuln", "app.parsers.nikto_parser", "NiktoParser", "Nikto text report",
              "looks_like_nikto"),
        _spec("nuclei_json", "Nuclei JSON / JSONL", "vuln", "app.parsers.nuclei_parser", "NucleiParser", "Nuclei results (JSON/JSONL)",
              "looks_like_nuclei"),
        _spec("httpx_json", "httpx JSON / JSONL", "web", "app.parsers.httpx_parser", "HttpxParser", "httpx web fingerprint (JSON/JSONL)",
              "app.parsers.httpx_parser:looks_like_httpx"),
        _spec("whatweb_json", "WhatWeb JSON", "web", "app.parsers.whatweb_parser", "WhatwebParser", "whatweb web fingerprint (JSON/JSONL)",
              "app.parsers.whatweb_parser:looks_like_whatweb"),
        _spec("testssl_json", "testssl.sh JSON", "web", "app.parsers.testssl_parser", "TestsslParser", "testssl.sh TLS assessment (JSON)",
              "app.parsers.testssl_parser:looks_like_testssl"),
        _spec("eyewitness_json", "EyeWitness JSON", "web", "app.parsers.eyewitness_parser", "EyewitnessParser", "EyeWitness report",
              "looks_like_eyewitness_json", _EYEWITNESS_HINTS),
        _spec("eyewitness_csv", "EyeWitness CSV", "web", "app.parsers.eyewitness_parser", "EyewitnessParser", "Eyewitness report",
              "looks_like_eyewitness_csv", _EYEWITNESS_HINTS),
        _spec("eyewitness_zip", "EyeWitness ZIP bundle", "web", "app.parsers.eyewitness_parser", "EyewitnessParser", "EyeWitness bundle (zip with report + screenshots)"),
        _spec("dirbuster_json", "Directory brute-force JSON", "web", "app.parsers.dirbuster_parser", "DirBusterParser", "Web content discovery JSON",
              "looks_like_dirbuster"),
        _spec("dirbuster_csv", "Directory brute-force CSV", "web", "app.parsers.dirbuster_parser", "DirBusterParser", "Web content discovery CSV",
              "looks_like_dirbuster"),
        _spec("dirbuster_output", "Directory brute-force text", "web", "app.parsers.dirbuster_parser", "DirBusterParser", "Web content discovery output",
              "looks_like_dirbuster"),
        _spec("dns_csv", "DNS records CSV", "dns", "app.parsers.dns_parser", "DNSParser", "DNS records CSV file",
              "looks_like_dns_csv"),
        _spec("dnsx_json", "dnsx JSON / JSONL", "dns", "app.parsers.dnsx_parser", "DnsxParser", "dnsx DNS resolution JSON",
              "looks_like_dnsx"),
        _spec("amass_json", "Amass / Subfinder JSON", "dns", "app.parsers.amass_parser", "AmassParser", "Amass/Subfinder JSON output",
              "looks_like_amass"),
        _spec("amass_output", "Hostname list (Amass / Subfinder text)", "dns", "app.parsers.amass_parser", "AmassParser", "Amass/Subfinder output file",
              "looks_like_amass"),
        _spec("rdap_json", "RDAP JSON / NDJSON", "dns", "app.parsers.rdap_parser", "RdapParser", "RDAP network registration (JSON/NDJSON)",
              "looks_like_rdap"),
        _spec("netexec_json", "NetExec JSON", "auth", "app.parsers.netexec_parser", "NetexecParser", "NetExec JSON output",
              "looks_like_netexec"),
        _spec("netexec_output", "NetExec text", "auth", "app.parsers.netexec_parser", "NetexecParser", "NetExec output file",
              "looks_like_netexec"),
        _spec("smbmap_json", "SMBMap JSON", "auth", "app.parsers.smbmap_parser", "SMBMapParser", "SMBMap JSON output",
              "looks_like_smbmap"),
        _spec("smbmap_output", "SMBMap text", "auth", "app.parsers.smbmap_parser", "SMBMapParser", "SMBMap output file",
              "looks_like_smbmap"),
        # v2.417.0 (review R03) — the label says what is imported: computers'
        # addresses and names, none of BloodHound's security content.
        _spec("bloodhound_json", "BloodHound computers JSON (addresses only)", "auth", "app.parsers.bloodhound_parser", "BloodHoundParser", "BloodHound/SharpHound JSON export",
              "looks_like_bloodhound"),
    )
}

# The one class the executor dispatches on its own path (``_process_job``'s
# Nessus branch), so it is detected but never in the dispatch map.
OWN_PATH_CLASSES = ("NessusIntegrationService",)

# A ``(file_type, shape)`` entry is tried only when the sample's first byte
# says so: Nikto's JSON saved as .txt is labelled as the JSON it is (the
# parser reads it by content, v2.424.1).
JSON_SHAPED = "json-shaped"
NOT_JSON_SHAPED = "not-json-shaped"

# Per extension, the formats tried in order — each when its hint or sniffer
# recognises the file.  Ordered by structural specificity: httpx first in JSON
# (``tech`` + ``webserver`` + ``url`` rarely false-positives), Nuclei before the
# probes whose fields it shares, the hint-driven masscan / EyeWitness late.
DETECTION: Tuple[Tuple[Tuple[str, ...], Tuple[object, ...]], ...] = (
    ((".gnmap",), ("nmap_gnmap",)),
    ((".json", ".jsonl", ".ndjson"), (
        "httpx_json", "whatweb_json", "testssl_json", "nuclei_json", "bloodhound_json", "amass_json",
        "dnsx_json", "rdap_json", "naabu_json", "netexec_json", "masscan_json", "eyewitness_json",
        "nikto_json", "smbmap_json", "dirbuster_json",
    )),
    # EyeWitness bundle (report JSON + screenshots); the parser unzips it.
    ((".zip",), ("eyewitness_zip",)),
    ((".csv",), ("eyewitness_csv", "nikto_csv", "dirbuster_csv", "dns_csv")),
    ((".txt",), (
        "rustscan_output", "smbmap_output", ("nikto_json", JSON_SHAPED), ("nikto_output", NOT_JSON_SHAPED),
        "amass_output", "netexec_output", "naabu_output", "dirbuster_output", "masscan_list", "gnmap_txt",
    )),
)

# .xml is a decision tree, not a list (v2.45.1): nmap's XML root is shared by
# masscan (``scanner="masscan"``), and an nmap file whose NSE output quotes
# "Greenbone" must not go to OpenVAS.  The first sniffer that recognises the
# file decides the order; ``*`` marks a fallback (tried without having been
# recognised — the staged review must never call it "recognised by
# structure").  Nothing recognised: the try-everything order, all fallbacks.
# Nessus is always the last-ditch attempt (an .xml that is a mislabelled
# .nessus export).
XML_DECISIONS: Tuple[Tuple[Optional[str], Tuple[str, ...]], ...] = (
    ("masscan_xml", ("masscan_xml", "nmap_xml*")),
    ("openvas_xml", ("openvas_xml", "nmap_xml*")),
    ("nmap_xml", ("nmap_xml", "masscan_xml*")),
    (None, ("nmap_xml*", "openvas_xml*", "masscan_xml*")),
)
XML_LAST = "nessus_xml*"


class FallbackAttempt(tuple):
    """A parser the dispatcher tries WITHOUT having recognised the content —
    the XML branch's "try the others" tail.  Still a plain
    ``(file_type, parser_class, description)`` to every consumer (and to the
    dispatch contract tests); the staged review reads ``fallback`` so an
    attempt nothing recognised is never shown as "recognised by structure"."""
    fallback = True


ParserDescriptor = Tuple[str, Type, str]


def format_label(file_type: Optional[str]) -> Optional[str]:
    if not file_type:
        return None
    spec = FORMATS.get(file_type)
    return spec.label if spec else file_type


def resolve_parser(file_type: str) -> Optional[Tuple[Type, str]]:
    """``(parser_class, description)`` for a registry key, or None when the
    key is unknown or its parser module cannot be imported."""
    spec = FORMATS.get(file_type)
    if spec is None:
        return None
    try:
        module = importlib.import_module(spec.module)
        return getattr(module, spec.class_name), spec.description
    except (ImportError, AttributeError):
        return None


def parser_classes() -> List[Type]:
    """Every importable parser class the registry names, once each, in
    registry order (the dispatch map's keys plus the own-path classes)."""
    seen: Dict[Type, None] = {}
    for key in FORMATS:
        resolved = resolve_parser(key)
        if resolved is not None:
            seen.setdefault(resolved[0], None)
    return list(seen)


def sniffers() -> Dict[str, str]:
    """``{sniffer name: module}`` for every sniffer the registry names."""
    out: Dict[str, str] = {}
    for spec in FORMATS.values():
        if spec.sniffer:
            module, _, name = spec.sniffer.rpartition(":")
            out[name] = module or CONTENT_DETECTION
    return out


def detection_keys() -> List[str]:
    """Every ``file_type`` the dispatcher can emit, as the tables name them."""
    keys = [entry if isinstance(entry, str) else entry[0]
            for _exts, order in DETECTION for entry in order]
    keys += [k.rstrip("*") for decider, order in XML_DECISIONS for k in ((decider,) if decider else ()) + order]
    keys.append(XML_LAST.rstrip("*"))
    return keys


@lru_cache(maxsize=None)
def _sniffer(spec_sniffer: str) -> Tuple[Callable, bool]:
    """The predicate and whether it takes the filename (a sniffer takes
    ``(sample)`` or ``(sample, filename)``)."""
    module, _, name = spec_sniffer.rpartition(":")
    fn = getattr(importlib.import_module(module or CONTENT_DETECTION), name)
    return fn, len(inspect.signature(fn).parameters) >= 2


def _recognises(spec: FormatSpec, filename: str, sample: bytes) -> bool:
    if any(token in filename for token in spec.hints):
        return True
    if spec.sniffer is None:
        return True
    fn, takes_name = _sniffer(spec.sniffer)
    return bool(fn(sample, filename) if takes_name else fn(sample))


def _json_shaped(sample: bytes) -> bool:
    return sample.lstrip(b"\xef\xbb\xbf").lstrip()[:1] in (b"[", b"{")


def _attempt(key: str) -> Optional[ParserDescriptor]:
    fallback = key.endswith("*")
    key = key.rstrip("*")
    resolved = resolve_parser(key)
    if resolved is None:
        logger.warning("Parser for %s unavailable", key)
        return None
    cls, description = resolved
    return FallbackAttempt((key, cls, description)) if fallback else (key, cls, description)


def detect(filename: str, sample: bytes) -> List[ParserDescriptor]:
    """The dispatcher's attempts for a file with no operator override, in the
    order they are tried.  ``filename`` is lower-cased by the caller."""
    keys: List[str] = []
    is_xml = filename.endswith(".xml")
    if filename.endswith(".nessus") or (is_xml and _recognises(FORMATS["nessus_xml"], filename, sample)):
        keys.append("nessus_xml")
    if is_xml:
        for decider, order in XML_DECISIONS:
            if decider is None or _recognises(FORMATS[decider], filename, sample):
                keys.extend(order)
                break
        keys.append(XML_LAST)
    else:
        for extensions, order in DETECTION:
            if not filename.endswith(extensions):
                continue
            for entry in order:
                key, shape = (entry, None) if isinstance(entry, str) else entry
                if shape is not None and _json_shaped(sample) != (shape == JSON_SHAPED):
                    continue
                if _recognises(FORMATS[key], filename, sample):
                    keys.append(key)
            break
    return [a for a in map(_attempt, keys) if a is not None]
