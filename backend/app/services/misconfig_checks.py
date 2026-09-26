"""Misconfigurations the parsers recognise, as one catalog (v2.412.0).

A tool's report of a weakness used to become a scanner observation only if
its parser hand-wrote one.  Each check here has ONE title, severity and
write-up whichever tool saw it, so the same weakness reported by nmap and
NetExec is one issue on Findings › Scanner observations (issues group by
title) and promotes to one finding.  A parser recognises the signal; the
catalog names it.  ``record_misconfig`` is the only write path.

Adding a check: an entry here, the parser that recognises it, a test, and
the tool's rows in ``app/data/parser_coverage.json``.  Keep titles stable —
the title is the issue key.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.db import models
from app.db.models_vulnerability import Vulnerability, VulnerabilitySeverity, VulnerabilitySource


@dataclass(frozen=True)
class MisconfigCheck:
    id: str
    title: str
    severity: VulnerabilitySeverity
    description: str
    solution: str


CHECKS: Dict[str, MisconfigCheck] = {c.id: c for c in (
    MisconfigCheck(
        id="vnc_no_auth",
        title="VNC server does not require authentication",
        severity=VulnerabilitySeverity.HIGH,
        description="The VNC service offers the \"None\" security type.",
        solution="Enable VNC authentication and limit the port to management networks.",
    ),
    MisconfigCheck(
        id="smb_signing_not_required",
        title="SMB signing not required",
        severity=VulnerabilitySeverity.MEDIUM,
        description="The SMB service does not require message signing.",
        solution="Require SMB signing on servers (Group Policy or Samba \"server signing = mandatory\").",
    ),
    MisconfigCheck(
        id="smb_null_session",
        title="SMB null or guest session allowed",
        severity=VulnerabilitySeverity.MEDIUM,
        description="The SMB service accepted an anonymous (null) or guest login.",
        solution="Disable null sessions and the guest account for SMB.",
    ),
    MisconfigCheck(
        # Title kept from the NetExec parser's v2.390.0 observation, so
        # existing rows stay the same issue.
        id="smbv1_enabled",
        title="SMBv1 enabled",
        severity=VulnerabilitySeverity.MEDIUM,
        description="The SMB service accepts the SMBv1 protocol.",
        solution="Disable SMBv1 on the server.",
    ),
    # v2.413.0 — TLS on any service, from nmap's ssl-enum-ciphers / ssl-cert.
    # Self-signed is deliberately not a check: RDP's default certificate is
    # self-signed, and flagging it marked nearly every Windows host (review
    # 2026-09-23 C6a).
    MisconfigCheck(
        id="tls_deprecated_protocol",
        title="Deprecated TLS/SSL protocol offered",
        severity=VulnerabilitySeverity.MEDIUM,
        description="The service offers SSLv2, SSLv3, TLS 1.0 or TLS 1.1.",
        solution="Offer TLS 1.2 and TLS 1.3 only.",
    ),
    MisconfigCheck(
        id="tls_cert_expired",
        title="TLS certificate expired",
        severity=VulnerabilitySeverity.MEDIUM,
        description="The service's certificate had expired when it was scanned.",
        solution="Replace the certificate.",
    ),
    # v2.414.0 — HTTP response headers, one title whichever web scanner said
    # so (Nikto 013587 / 007352 / 000287, Nuclei http-missing-security-headers,
    # testssl HSTS).  Low: hardening, not a way in.
    MisconfigCheck(
        id="http_missing_hsts",
        title="HTTP Strict-Transport-Security header missing",
        severity=VulnerabilitySeverity.LOW,
        description="The HTTPS response does not set Strict-Transport-Security.",
        solution="Send Strict-Transport-Security with a max-age of at least 180 days.",
    ),
    MisconfigCheck(
        id="http_missing_csp",
        title="Content-Security-Policy header missing",
        severity=VulnerabilitySeverity.LOW,
        description="The response does not set a Content-Security-Policy.",
        solution="Define a Content-Security-Policy for the application.",
    ),
    MisconfigCheck(
        id="http_missing_xcto",
        title="X-Content-Type-Options header missing",
        severity=VulnerabilitySeverity.LOW,
        description="The response does not set X-Content-Type-Options: nosniff.",
        solution="Send X-Content-Type-Options: nosniff.",
    ),
    MisconfigCheck(
        id="http_missing_frame_protection",
        title="Clickjacking protection missing (X-Frame-Options / frame-ancestors)",
        severity=VulnerabilitySeverity.LOW,
        description="The response sets neither X-Frame-Options nor a CSP frame-ancestors directive.",
        solution="Send Content-Security-Policy: frame-ancestors (or X-Frame-Options).",
    ),
    MisconfigCheck(
        id="http_version_disclosure",
        title="Software version disclosed in HTTP headers",
        severity=VulnerabilitySeverity.LOW,
        description="A response header (X-Powered-By, X-AspNet-Version…) names the software and version.",
        solution="Remove or blank the header.",
    ),
    MisconfigCheck(
        id="ftp_anonymous",
        title="Anonymous FTP login allowed",
        severity=VulnerabilitySeverity.MEDIUM,
        description="The FTP service accepted the anonymous account.",
        solution="Disable anonymous FTP unless the service is meant to be public.",
    ),
)}


def port_row(db: Session, host_id: int, port_number: Optional[int], protocol: str = "tcp") -> Optional[models.Port]:
    if not port_number:
        return None
    return (
        db.query(models.Port)
        .filter(models.Port.host_id == host_id, models.Port.port_number == port_number,
                models.Port.protocol == protocol)
        .first()
    )


def record_misconfig(
    db: Session,
    *,
    check_id: str,
    host_id: int,
    scan_id: int,
    source: VulnerabilitySource,
    port_number: Optional[int] = None,
    evidence: Optional[str] = None,
    name_id: Optional[int] = None,
    port_id: Optional[int] = None,
) -> Vulnerability:
    """One scanner observation for ``check_id`` on this host (and port, and
    the named endpoint a web scanner tested).  ``port_id`` when the caller
    already holds the row."""
    from app.parsers.parser_utils import upsert_vulnerability  # parsers import services

    check = CHECKS[check_id]
    port = None if port_id else port_row(db, host_id, port_number)
    return upsert_vulnerability(
        db=db,
        host_id=host_id,
        scan_id=scan_id,
        source=source,
        title=check.title,
        severity=check.severity,
        plugin_id=check.id,
        port_id=port_id or (port.id if port else None),
        description=check.description,
        solution=check.solution,
        plugin_output=(evidence or "")[:4000] or None,
        name_id=name_id,
        check_id=check.id,
    )


# --- Web header results → checks (v2.414.0) --------------------------------
# From the tools' own wording: Nikto's plugins/nikto_headers.plugin (013587
# "Suggested security header missing: <header>", 000287 "Retrieved <header>
# header: <value>") and db_tests (007352 "The X-Content-Type-Options header is
# not set"); Nuclei's http-missing-security-headers matcher names.

_MISSING_HEADER = {
    "strict-transport-security": "http_missing_hsts",
    "content-security-policy": "http_missing_csp",
    "x-content-type-options": "http_missing_xcto",
    "x-frame-options": "http_missing_frame_protection",
}
_DISCLOSING_HEADERS = ("x-powered-by", "x-aspnet-version", "x-aspnetmvc-version")
_NIKTO_MISSING = re.compile(r"suggested security header missing:\s*([a-z0-9-]+)", re.IGNORECASE)
_NIKTO_RETRIEVED = re.compile(r"retrieved ([a-z0-9-]+) header:", re.IGNORECASE)


def nikto_header_check(message: str) -> Optional[str]:
    """The catalog check a Nikto message reports, if it is a header one."""
    text = message or ""
    missing = _NIKTO_MISSING.search(text)
    if missing:
        return _MISSING_HEADER.get(missing.group(1).lower())
    if "x-content-type-options header is not set" in text.lower():
        return "http_missing_xcto"
    retrieved = _NIKTO_RETRIEVED.search(text)
    if retrieved and retrieved.group(1).lower() in _DISCLOSING_HEADERS:
        return "http_version_disclosure"
    return None


def nuclei_header_check(template_id: str, matcher: Optional[str]) -> Optional[str]:
    if template_id != "http-missing-security-headers" or not matcher:
        return None
    return _MISSING_HEADER.get(matcher.lower())


# --- Vulnerability scanners' own checks → catalog (v2.415.0) ---------------
# Each id checked against the vendor's page (tenable.com/plugins/nessus/<id>,
# projectdiscovery/nuclei-templates, 2026-09-26).  The row keeps the scanner's
# own severity and write-up; it gains the catalog title and check, so the same
# weakness from nmap, NetExec and Nessus is one issue.  OpenVAS is not mapped:
# no OID list could be verified.
NESSUS_PLUGIN_CHECKS: Dict[str, str] = {
    "57608": "smb_signing_not_required",   # SMB Signing not required
    "26920": "smb_null_session",           # SMB NULL Session Authentication
    "96982": "smbv1_enabled",              # Server Message Block (SMB) Protocol Version 1 Enabled (uncredentialed check)
    "10079": "ftp_anonymous",              # Anonymous FTP Enabled
    "26925": "vnc_no_auth",                # VNC Server Unauthenticated Access
    "104743": "tls_deprecated_protocol",   # TLS Version 1.0 Protocol Detection
    "157288": "tls_deprecated_protocol",   # TLS Version 1.1 Deprecated Protocol
    "20007": "tls_deprecated_protocol",    # SSL Version 2 and 3 Protocol Detection
    "15901": "tls_cert_expired",           # SSL Certificate Expiry
    "142960": "http_missing_hsts",         # HSTS Missing From HTTPS Server (RFC 6797)
}
NUCLEI_TEMPLATE_CHECKS: Dict[str, str] = {
    "ftp-anonymous-login": "ftp_anonymous",
    "deprecated-tls": "tls_deprecated_protocol",
    "expired-ssl": "tls_cert_expired",
}

# The kinds a weakness is (v2.415.0): the host filter ``kind:`` and the
# inspector / Scanner observations split.
KIND_MISCONFIGURATION = "misconfiguration"
KIND_VULNERABILITY = "vulnerability"
KIND_INFORMATIONAL = "informational"
KINDS = (KIND_MISCONFIGURATION, KIND_VULNERABILITY, KIND_INFORMATIONAL)


def vuln_kind(check_id: Optional[str], severity) -> str:
    """A catalog check is a misconfiguration; otherwise an informational row
    is informational and anything rated is a vulnerability."""
    if check_id:
        return KIND_MISCONFIGURATION
    value = getattr(severity, "value", severity)
    return KIND_INFORMATIONAL if str(value or "").lower() == "info" else KIND_VULNERABILITY
