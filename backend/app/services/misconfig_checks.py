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
) -> Vulnerability:
    """One scanner observation for ``check_id`` on this host (and port)."""
    from app.parsers.parser_utils import upsert_vulnerability  # parsers import services

    check = CHECKS[check_id]
    port = port_row(db, host_id, port_number)
    return upsert_vulnerability(
        db=db,
        host_id=host_id,
        scan_id=scan_id,
        source=source,
        title=check.title,
        severity=check.severity,
        plugin_id=check.id,
        port_id=port.id if port else None,
        description=check.description,
        solution=check.solution,
        plugin_output=(evidence or "")[:4000] or None,
    )
