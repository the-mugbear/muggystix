"""Catalog observations from evidence already stored (v2.414.0).

The misconfiguration catalog (``misconfig_checks``) is applied by the
parsers at import, so data imported before it has the evidence but not the
scanner observations: a NetExec banner with "(signing:False)", an nmap
vnc-info saying "None", an SMBMap NULL session.  This reads that evidence
with the parsers' own rules (``netexec_line_checks``, ``script_text_check``)
and records what they would have.  ``record_misconfig`` upserts, so running
it twice changes nothing.

What it cannot rebuild: nmap vulnerability-script results and vulners
entries (only the scripts' text was stored, not their tables) and web header
results already stored under a tool's own title.  Re-process the file for
those.

Run it: ``docker compose exec backend python scripts/backfill_misconfigs.py``.
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, Optional

from sqlalchemy.orm import Session

from app.db import models
from app.db.models_confidence import NetexecResult
from app.db.models_vulnerability import VulnerabilitySource
from app.parsers.netexec_parser import netexec_line_checks
from app.parsers.nse_vulns import script_text_check
from app.services import smb_signing as smb_signing_states
from app.services.misconfig_checks import port_row, record_misconfig


def backfill_misconfigs(db: Session, project_id: Optional[int] = None) -> Dict[str, int]:
    """Record catalog observations from stored evidence; returns counts per
    check.  Flushes; the caller commits."""
    counts: Counter = Counter()

    def _hosts(query, host_column):
        query = query.join(models.Host, host_column == models.Host.id)
        return query.filter(models.Host.project_id == project_id) if project_id else query

    # NetExec and SMBMap rows (one table, `tool` tells them apart).
    for row in _hosts(db.query(NetexecResult), NetexecResult.host_id).yield_per(500):
        if row.tool == "smbmap":
            checks = ["smb_null_session"] if row.auth_success and (row.username or "").lower() in ("", "guest") else []
            source = VulnerabilitySource.SMBMAP
        else:
            checks = netexec_line_checks(
                row.protocol, row.raw_output or "", username=row.username,
                auth_success=row.auth_success, smbv1=row.smbv1,
            )
            source = VulnerabilitySource.NETEXEC
        for check_id in checks:
            record_misconfig(
                db, check_id=check_id, host_id=row.host_id, scan_id=row.scan_id, source=source,
                port_number=row.port, evidence=row.raw_output,
            )
            counts[check_id] += 1

    # nmap port scripts (vnc-info, ftp-anon).
    port_scripts = _hosts(
        db.query(models.Script, models.Port).join(models.Port, models.Script.port_id == models.Port.id),
        models.Port.host_id,
    ).filter(models.Script.script_id.in_(("vnc-info", "ftp-anon")))
    for script, port in port_scripts.yield_per(500):
        check_id = script_text_check(script.script_id, script.output)
        if check_id:
            record_misconfig(
                db, check_id=check_id, host_id=port.host_id, scan_id=script.scan_id,
                source=VulnerabilitySource.NMAP, port_id=port.id,
                evidence=f"{script.script_id}: {(script.output or '').strip()}",
            )
            counts[check_id] += 1

    # nmap host scripts: smb-protocols (SMBv1) and the SMB signing posture the
    # security-mode scripts set on the host.
    host_scripts = _hosts(db.query(models.HostScript), models.HostScript.host_id).filter(
        models.HostScript.script_id.in_(("smb-protocols", "smb-security-mode", "smb2-security-mode"))
    )
    for script in host_scripts.yield_per(500):
        host = db.get(models.Host, script.host_id)
        smb_port = next((p for p in (445, 139) if port_row(db, script.host_id, p)), None)
        if script.script_id == "smb-protocols":
            check_id = script_text_check(script.script_id, script.output)
        elif host is not None and host.smb_signing in smb_signing_states.RELAYABLE:
            check_id = "smb_signing_not_required"
        else:
            check_id = None
        if check_id:
            record_misconfig(
                db, check_id=check_id, host_id=script.host_id, scan_id=script.scan_id,
                source=VulnerabilitySource.NMAP, port_number=smb_port,
                evidence=f"{script.script_id}: {(script.output or '').strip()}",
            )
            counts[check_id] += 1

    db.flush()
    return dict(counts)
