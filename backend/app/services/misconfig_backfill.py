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
from typing import Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db import models
from app.db.models_confidence import NetexecResult
from app.db.models_vulnerability import VulnerabilitySource
from app.parsers.netexec_parser import netexec_check_evidence, netexec_line_checks, nfs_finding_port
from app.parsers.nse_vulns import script_text_check
from app.services import smb_signing as smb_signing_states
from app.db.models_findings import Finding, FindingSource
from app.db.models_vulnerability import Vulnerability
from app.services.misconfig_checks import (
    CHECKS, NESSUS_PLUGIN_CHECKS, NUCLEI_TEMPLATE_CHECKS, nikto_header_check, nuclei_header_check,
    port_row, record_misconfig,
)


#: The count key under which a run reports findings it did not re-key (see
#: ``_adopt_tool_titled_rows``); the pairs themselves go to ``unmerged``.
UNMERGED_KEY = "findings left on their own key (another finding is this check's)"


def backfill_misconfigs(
    db: Session, project_id: Optional[int] = None, unmerged: Optional[List[dict]] = None,
) -> Dict[str, int]:
    """Record catalog observations from stored evidence; returns counts per
    check.  Flushes; the caller commits.

    ``unmerged`` — a list the caller passes to learn which findings were NOT
    moved onto their catalog check's key because the project already has a
    scanner finding there.  One dict per pair: ``project_id``, ``check_id``,
    ``finding_id`` (left alone) and ``kept_finding_id`` (the check's).  They
    are two findings for one weakness; a person merges them, never this."""
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
                auth_success=row.auth_success, smbv1=row.smbv1, shares=row.shares,
            )
            source = VulnerabilitySource.NETEXEC
        port_number, port_id = row.port, None
        if checks and (row.protocol or "").lower() == "nfs":
            port_number, port_id = nfs_finding_port(db, row.host_id, row.port)
        for check_id in checks:
            record_misconfig(
                db, check_id=check_id, host_id=row.host_id, scan_id=row.scan_id, source=source,
                port_number=port_number, port_id=port_id,
                evidence=netexec_check_evidence(check_id, row.raw_output or "", row.shares, row.port, port_number),
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

    counts.update(_adopt_tool_titled_rows(db, project_id, unmerged))
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


#: The tools whose stored rows are given their catalog check.
_ADOPTED_SOURCES = ("NIKTO", "NUCLEI", "TESTSSL", "NESSUS")


def _adopt_tool_titled_rows(
    db: Session, project_id: Optional[int], unmerged: Optional[List[dict]] = None,
) -> Counter:
    """Rows imported before a tool's results were mapped onto the catalog
    keep the tool's title and group apart ("Strict-Transport-Security header
    missing" from Nikto beside "HSTS not set" from testssl).  Give them the
    check and the catalog title — the scanner's severity and write-up stay —
    and move a finding promoted from one onto the check's key, so the next
    promotion of the weakness joins it.

    A project has ONE scanner finding per issue key (``uq_finding_scanner_issue``).
    When the check already has one — two tools' findings map to it, or one was
    promoted after the catalog existed — the second finding keeps the key it
    has and the pair is reported (``unmerged``): re-keying it would be refused
    by the index and fail the whole run, and which of two findings' text,
    status and history survives is a person's decision."""
    counts: Counter = Counter()
    query = db.query(Vulnerability).filter(
        Vulnerability.check_id.is_(None),
        Vulnerability.source.in_(_ADOPTED_SOURCES),
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
    # Every finding promoted from one of these tools' catalog rows that is not
    # on its check's key — those adopted now, and those an earlier run left
    # alone, so a pair is reported by EVERY run until someone merges it.
    scanner = FindingSource.SCANNER.value
    promoted_query = (
        db.query(Finding, Vulnerability.check_id)
        .join(Vulnerability, Vulnerability.id == Finding.vuln_id)
        .filter(Vulnerability.check_id.isnot(None), Vulnerability.source.in_(_ADOPTED_SOURCES))
    )
    if project_id:
        promoted_query = promoted_query.filter(Finding.project_id == project_id)
    promoted = [
        (finding, check_id) for finding, check_id in promoted_query.order_by(Finding.id)
        if finding.dedup_key != f"check:{check_id}"
    ]
    if not promoted:
        return counts
    # (project, key) -> the scanner finding that holds the key: those stored,
    # then those this run moves (the session does not autoflush).
    holder: Dict[tuple, int] = {
        (pid, key): fid
        for pid, key, fid in db.query(Finding.project_id, Finding.dedup_key, Finding.id)
        .filter(Finding.project_id.in_({f.project_id for f, _ in promoted}),
                Finding.source == scanner,
                Finding.dedup_key.in_({f"check:{c}" for _, c in promoted}))
        .order_by(Finding.id.desc())
    }
    for finding, check_id in promoted:
        key = f"check:{check_id}"
        if finding.source != scanner:
            finding.dedup_key = key  # outside the index: any number may share a key
            continue
        kept = holder.get((finding.project_id, key))
        if kept is not None and kept != finding.id:
            counts[UNMERGED_KEY] += 1
            if unmerged is not None:
                unmerged.append({
                    "project_id": finding.project_id, "check_id": check_id,
                    "finding_id": finding.id, "kept_finding_id": kept,
                })
            continue
        finding.dedup_key = key
        holder[(finding.project_id, key)] = finding.id
    return counts
