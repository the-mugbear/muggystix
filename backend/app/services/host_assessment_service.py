"""Per-host evidence freshness and coverage (v2.348.0; design review item 4).

A recent host observation must not make every fact about the host look
current.  This block says, per assessment domain, when the host was last
checked — or that it never was — so the inspector can put freshness beside
the assertion instead of one "last seen" at the top:

* observed        — the newest scan that saw the host at all;
* vulnerabilities — newest vulnerability observation OR vulnerability-scanner
                    run over the host (a clean scan is an assessment), or not
                    assessed;
* web / TLS       — newest web-interface / cert evidence, or not assessed,
                    or not applicable (no web port);
* auth / SMB      — SMB-signing or NetExec evidence present, or not, or n/a;
* tested          — newest executed test result on a plan entry for the host;
* conflicts       — how many values scans disagree on;
* open ports not in the latest scan — ports whose own last observation is
                    older than the host's, i.e. the latest sweep did not see
                    them open.

Eligibility mirrors ``evidence_service`` so the inspector and the Evidence
page agree on what "not applicable" means.
"""
from __future__ import annotations

from datetime import timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.db import models
from app.db.models_agent import TestExecutionResult, TestExecutionStatus, TestPlanEntry
from app.db.models_confidence import ConflictHistory, NetexecResult
from app.db.models_vulnerability import Vulnerability
from app.services.evidence_service import (
    _AUTH_PORTS, _WEB_PORTS, vuln_scanned_filter, vulnerability_evidence_filter,
)

# A port observed within this window of the host's newest observation counts
# as seen by the same sweep (parsers stamp rows over a few seconds).
_SAME_SWEEP = timedelta(hours=1)


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def host_assessment(db: Session, host: models.Host) -> Dict[str, Any]:
    hid = host.id

    last_tested_at, tests_executed = (
        db.query(
            func.max(func.coalesce(TestExecutionResult.executed_at, TestExecutionResult.created_at)),
            func.count(TestExecutionResult.id),
        )
        .join(TestPlanEntry, TestExecutionResult.entry_id == TestPlanEntry.id)
        .filter(
            TestPlanEntry.host_id == hid,
            TestExecutionResult.status == TestExecutionStatus.EXECUTED.value,
        )
        .one()
    )

    last_vuln_at, vuln_count = (
        db.query(func.max(Vulnerability.last_seen), func.count(Vulnerability.id))
        # A misconfiguration check is not a vulnerability assessment (v2.415.0).
        .filter(Vulnerability.host_id == hid, vulnerability_evidence_filter())
        .one()
    )
    # A vulnerability scanner's run over the host is an assessment even when it
    # reported nothing (v2.372.0) — same rule as the Evidence page.
    last_vuln_scan_at = (
        db.query(func.max(models.HostScanHistory.discovered_at))
        .join(models.Scan, models.HostScanHistory.scan_id == models.Scan.id)
        .filter(models.HostScanHistory.host_id == hid, vuln_scanned_filter())
        .scalar()
    )
    vuln_dates = [d for d in (_aware(last_vuln_at), _aware(last_vuln_scan_at)) if d is not None]

    open_ports = [p for p in host.ports if p.state == "open"]
    web_eligible = any(
        p.port_number in _WEB_PORTS or (p.service_name or "").lower().startswith("http")
        for p in open_ports
    )
    auth_eligible = any(p.port_number in _AUTH_PORTS for p in open_ports)

    last_web_at, web_count = (
        db.query(func.max(models.WebInterface.last_seen), func.count(models.WebInterface.id))
        .filter(models.WebInterface.host_id == hid)
        .one()
    )

    auth_assessed = bool(host.smb_signing is not None) or bool(
        db.query(NetexecResult.id).filter(NetexecResult.host_id == hid).first()
    )

    conflicts = (
        db.query(func.count(ConflictHistory.id))
        .filter(
            or_(
                ConflictHistory.host_id == hid,
                ConflictHistory.port_id.in_([p.id for p in host.ports] or [-1]),
            )
        )
        .scalar()
    ) or 0

    host_seen = _aware(host.last_seen)
    stale_open = 0
    if host_seen is not None:
        for p in open_ports:
            ps = _aware(p.last_seen)
            if ps is not None and ps < host_seen - _SAME_SWEEP:
                stale_open += 1

    return {
        "last_observed_at": host.last_seen,
        "vuln_assessed": bool(vuln_count) or last_vuln_scan_at is not None,
        "last_vuln_assessed_at": max(vuln_dates) if vuln_dates else None,
        "web_eligible": web_eligible,
        "web_assessed": bool(web_count),
        "last_web_assessed_at": last_web_at,
        "auth_eligible": auth_eligible,
        "auth_assessed": auth_assessed,
        "tests_executed": int(tests_executed or 0),
        "last_tested_at": last_tested_at,
        "conflicts": int(conflicts),
        "open_ports_not_in_latest_scan": stale_open,
    }
