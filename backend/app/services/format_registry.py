"""The formats the ingestion dispatcher knows, by their ``file_type`` key.

v2.351.0 (staged-import phase A).  ``_build_parsing_attempts`` emits
``(file_type, parser_class, description)`` descriptors; until now the
``file_type`` keys lived only inside that function.  This registry names
every one of them, with a human label and the parser that handles it, so:

* the ingestion job can record the chain *detected format → override →
  final format* as typed keys the UI can label;
* an operator override ("parse this as …") resolves to exactly one parser;
* the upload modal's format list (phase C) has one source.

``tests/test_ingestion_format_chain.py`` pins the registry to the
dispatcher: every key the dispatcher can emit must be here, and every key
here must resolve to an importable parser.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Dict, Optional, Tuple, Type


@dataclass(frozen=True)
class FormatSpec:
    file_type: str
    label: str
    family: str          # port | vuln | web | dns | auth | other
    module: str
    class_name: str
    description: str


def _spec(file_type: str, label: str, family: str, module: str, class_name: str, description: str) -> FormatSpec:
    return FormatSpec(file_type, label, family, module, class_name, description)


FORMATS: Dict[str, FormatSpec] = {
    s.file_type: s for s in (
        _spec("nmap_xml", "Nmap XML", "port", "app.parsers.nmap_parser", "NmapXMLParser", "Nmap XML file"),
        _spec("nmap_gnmap", "Nmap grepable (.gnmap)", "port", "app.parsers.gnmap_parser", "GnmapParser", "Nmap .gnmap file"),
        _spec("gnmap_txt", "Nmap grepable (.txt)", "port", "app.parsers.gnmap_parser", "GnmapParser", "Greppable scan output (.txt)"),
        _spec("masscan_xml", "Masscan XML", "port", "app.parsers.masscan_parser", "MasscanParser", "Masscan XML file"),
        _spec("masscan_json", "Masscan JSON", "port", "app.parsers.masscan_parser", "MasscanParser", "Masscan JSON file"),
        _spec("masscan_list", "Masscan list (-oL)", "port", "app.parsers.masscan_parser", "MasscanParser", "Masscan list output file"),
        _spec("naabu_json", "Naabu JSON", "port", "app.parsers.naabu_parser", "NaabuParser", "Naabu JSON output"),
        _spec("naabu_output", "Naabu host:port text", "port", "app.parsers.naabu_parser", "NaabuParser", "Naabu output file"),
        _spec("rustscan_output", "RustScan text", "port", "app.parsers.rustscan_parser", "RustScanParser", "RustScan output file"),
        _spec("nessus_xml", "Nessus (.nessus)", "vuln", "app.services.nessus_integration_service", "NessusIntegrationService", "Nessus vulnerability scan"),
        _spec("openvas_xml", "OpenVAS / Greenbone XML", "vuln", "app.parsers.openvas_parser", "OpenVASParser", "OpenVAS/Greenbone XML report"),
        _spec("nikto_json", "Nikto JSON", "vuln", "app.parsers.nikto_parser", "NiktoParser", "Nikto JSON report"),
        _spec("nikto_csv", "Nikto CSV", "vuln", "app.parsers.nikto_parser", "NiktoParser", "Nikto CSV report"),
        _spec("nikto_output", "Nikto text", "vuln", "app.parsers.nikto_parser", "NiktoParser", "Nikto text report"),
        _spec("nuclei_json", "Nuclei JSON / JSONL", "vuln", "app.parsers.nuclei_parser", "NucleiParser", "Nuclei results (JSON/JSONL)"),
        _spec("httpx_json","httpx JSON / JSONL", "web", "app.parsers.httpx_parser", "HttpxParser", "httpx web fingerprint (JSON/JSONL)"),
        _spec("whatweb_json", "WhatWeb JSON", "web", "app.parsers.whatweb_parser", "WhatwebParser", "whatweb web fingerprint (JSON/JSONL)"),
        _spec("testssl_json", "testssl.sh JSON", "web", "app.parsers.testssl_parser", "TestsslParser", "testssl.sh TLS assessment (JSON)"),
        _spec("eyewitness_json", "EyeWitness JSON", "web", "app.parsers.eyewitness_parser", "EyewitnessParser", "EyeWitness report"),
        _spec("eyewitness_csv", "EyeWitness CSV", "web", "app.parsers.eyewitness_parser", "EyewitnessParser", "Eyewitness report"),
        _spec("eyewitness_zip", "EyeWitness ZIP bundle", "web", "app.parsers.eyewitness_parser", "EyewitnessParser", "EyeWitness bundle (zip with report + screenshots)"),
        _spec("dirbuster_json", "Directory brute-force JSON", "web", "app.parsers.dirbuster_parser", "DirBusterParser", "Web content discovery JSON"),
        _spec("dirbuster_csv", "Directory brute-force CSV", "web", "app.parsers.dirbuster_parser", "DirBusterParser", "Web content discovery CSV"),
        _spec("dirbuster_output", "Directory brute-force text", "web", "app.parsers.dirbuster_parser", "DirBusterParser", "Web content discovery output"),
        _spec("dns_csv", "DNS records CSV", "dns", "app.parsers.dns_parser", "DNSParser", "DNS records CSV file"),
        _spec("dnsx_json", "dnsx JSON / JSONL", "dns", "app.parsers.dnsx_parser", "DnsxParser", "dnsx DNS resolution JSON"),
        _spec("amass_json", "Amass / Subfinder JSON", "dns", "app.parsers.amass_parser", "AmassParser", "Amass/Subfinder JSON output"),
        _spec("amass_output", "Hostname list (Amass / Subfinder text)", "dns", "app.parsers.amass_parser", "AmassParser", "Amass/Subfinder output file"),
        _spec("rdap_json", "RDAP JSON / NDJSON", "dns", "app.parsers.rdap_parser", "RdapParser", "RDAP network registration (JSON/NDJSON)"),
        _spec("netexec_json", "NetExec JSON", "auth", "app.parsers.netexec_parser", "NetexecParser", "NetExec JSON output"),
        _spec("netexec_output", "NetExec text", "auth", "app.parsers.netexec_parser", "NetexecParser", "NetExec output file"),
        _spec("smbmap_json", "SMBMap JSON", "auth", "app.parsers.smbmap_parser", "SMBMapParser", "SMBMap JSON output"),
        _spec("smbmap_output", "SMBMap text", "auth", "app.parsers.smbmap_parser", "SMBMapParser", "SMBMap output file"),
        # v2.417.0 (review R03) — the label says what is imported: computers'
        # addresses and names, none of BloodHound's security content.
        _spec("bloodhound_json", "BloodHound computers JSON (addresses only)", "auth", "app.parsers.bloodhound_parser", "BloodHoundParser", "BloodHound/SharpHound JSON export"),
    )
}


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
