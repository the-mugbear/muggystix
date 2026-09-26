"""Catalog observations from evidence already stored (v2.414.0).

The misconfiguration catalog (``misconfig_checks``) is applied by the
parsers at import, so data imported before it has the evidence but not the
scanner observations: a NetExec banner with "(signing:False)", an nmap
vnc-info saying "None", an SMBMap NULL session.  This reads that evidence
with the parsers' own rules (``netexec_line_checks``, ``script_text_check``)
and records what they would have.  ``record_misconfig`` upserts, so running
it twice changes nothing.

v2.415.0 — it also gives rows stored under a tool's own title (Nikto /
Nuclei header results, testssl protocol and HSTS checks, mapped Nessus
plugins) the catalog check and title.

What it cannot rebuild: nmap vulnerability-script results and vulners
entries (only the scripts' text was stored, not their tables).  Re-process
the file for those.

Run it: ``docker compose exec backend python scripts/backfill_misconfigs.py``.
"""
from __future__ import annotations

from collections import Counter
from typing import Dict, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db import models
from app.db.models_confidence import NetexecResult
from app.db.models_vulnerability import VulnerabilitySource
from app.parsers.netexec_parser import netexec_line_checks
from app.parsers.nse_vulns import script_text_check
from app.services import smb_signing as smb_signing_states
from app.db.models_findings import Finding
from app.db.models_vulnerability import Vulnerability
from app.services.misconfig_checks import (
    CHECKS, NESSUS_PLUGIN_CHECKS, NUCLEI_TEMPLATE_CHECKS, nikto_header_check, nuclei_header_check,
    port_row, record_misconfig,
)


def backfill_misconfigs(db: Session, project_id: Optional[int] = None) -> Dict[str, int]:
    """Record catalog observations from stored evidence; returns counts per
    check.  Flushes; the caller commits."""
    counts: Counter = Counter()

    def _hosts(query, host_column):
        query = query.join(models.Host, host_column == models.Host.id)
        return query.filter(models.Host.project_id == project_id) if project_id else query

    # v2.415.0 — nxc prints a line only for a host that answered; hosts
    # imported before 2.413.0 from nxc alone were left "unknown".
    answered = _hosts(db.query(NetexecResult.host_id), NetexecResult.host_id).distinct().subquery()
    marked = (
        db.query(models.Host)
        .filter(models.Host.id.in_(db.query(answered.c.host_id)),
                or_(models.Host.state.is_(None), models.Host.state == "unknown"))
        .update({models.Host.state: "up"}, synchronize_session=False)
    )
    if marked:
        counts["hosts marked up (NetExec answered)"] += marked

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

    counts.update(_adopt_tool_titled_rows(db, project_id))
    db.flush()
    return dict(counts)


_TESTSSL_CATALOG = {"SSLv2": "tls_deprecated_protocol", "SSLv3": "tls_deprecated_protocol",
                    "TLS1": "tls_deprecated_protocol", "TLS1_1": "tls_deprecated_protocol",
                    "HSTS": "http_missing_hsts"}


def _check_for_row(v: Vulnerability) -> Optional[str]:
    """The catalog check an already-stored scanner row is, by the same
    wording rules the parsers apply now (v2.415.0)."""
    source = getattr(v.source, "value", v.source)
    if source == "nikto":
        return nikto_header_check(v.title or "")
    if source == "nuclei":
        if v.plugin_id in NUCLEI_TEMPLATE_CHECKS:
            return NUCLEI_TEMPLATE_CHECKS[v.plugin_id]
        matcher = (v.title or "").rsplit(": ", 1)[-1] if ": " in (v.title or "") else None
        return nuclei_header_check(v.plugin_id or "", matcher)
    if source == "testssl":
        return _TESTSSL_CATALOG.get(v.plugin_id or "")
    if source == "nessus":
        return NESSUS_PLUGIN_CHECKS.get(v.plugin_id or "")
    return None


def _adopt_tool_titled_rows(db: Session, project_id: Optional[int]) -> Counter:
    """Rows imported before a tool's results were mapped onto the catalog
    keep the tool's title and group apart ("Strict-Transport-Security header
    missing" from Nikto beside "HSTS not set" from testssl).  Give them the
    check and the catalog title — the scanner's severity and write-up stay —
    and move a finding promoted from one onto the check's key, so the next
    promotion of the weakness joins it."""
    counts: Counter = Counter()
    query = db.query(Vulnerability).filter(
        Vulnerability.check_id.is_(None),
        Vulnerability.source.in_(("NIKTO", "NUCLEI", "TESTSSL", "NESSUS")),
    )
    if project_id:
        query = query.join(models.Host, Vulnerability.host_id == models.Host.id).filter(
            models.Host.project_id == project_id)
    adopted: Dict[int, str] = {}
    for v in query.yield_per(500):
        check_id = _check_for_row(v)
        if not check_id:
            continue
        v.check_id = check_id
        v.title = CHECKS[check_id].title  # the listener recomputes issue_key
        adopted[v.id] = check_id
        counts[f"{check_id} (retitled)"] += 1
    if adopted:
        db.flush()
        for finding in db.query(Finding).filter(Finding.vuln_id.in_(list(adopted))):
            finding.dedup_key = f"check:{adopted[finding.vuln_id]}"
    return counts
