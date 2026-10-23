"""Per-host evidence freshness and coverage (v2.348.0; design review item 4).

A recent host observation must not make every fact about the host look
current.  This block says, per assessment domain, when the host was last
checked — or that it never was — so the inspector can put freshness beside
the assertion instead of one "last seen" at the top:

* observed        — the newest scan that saw the host at all;
* vulnerabilities — newest vulnerability observation OR vulnerability-scanner
                    run over the host (a clean scan is an assessment), or not
                    assessed; and, beside it, whether a vulnerability scan
                    authenticated to the host (yes / no / not stated) — shown,
                    never a different kind of "assessed";
* web / TLS      — newest web-interface / cert evidence, or not assessed,
                    or not applicable (no web port);
* auth / SMB      — SMB-signing or NetExec evidence present, or not, or n/a;
* tested          — newest executed test result on a plan entry for the host;
* conflicts       — how many values scans disagree on;
* open ports not in the latest scan — ports whose own last observation is
                    older than the host's, i.e. the latest sweep did not see
                    them open.

Eligible and assessed ARE ``evidence_service``'s host predicates
(``domain_eligible_condition`` / ``domain_assessed_condition``), so the
inspector and the Evidence page agree on what "not applicable" and
"assessed" mean; this module adds only the provenance dates.
"""
from __future__ import annotations

from datetime import timedelta, timezone
from typing import Any, Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models
from app.db.models_proposals import EvidenceRecord
from app.db.models_host_tests import TESTED_OUTCOMES
from app.db.models_vulnerability import Vulnerability
from app.services.evidence_service import (
    domain_assessed_condition, domain_eligible_condition, host_vuln_scan_credentialed,
    vuln_scanned_filter, vulnerability_evidence_filter,
)
from app.services.host_detail_service import host_conflict_counts

# The Evidence domains this block reports.
_DOMAINS = ("vuln_assessment", "web_tls", "auth_smb_ad")

# A port observed within this window of the host's newest observation counts
# as seen by the same sweep (parsers stamp rows over a few seconds).
_SAME_SWEEP = timedelta(hours=1)


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def not_revalidated_port_ids(host: models.Host) -> List[int]:
    """The host's OPEN ports that a later observation of the host did not
    re-observe: the port's own ``last_seen`` is more than a sweep older than
    the host's.  The ONE rule behind the inspector's "not revalidated" beside
    a port and the assessment's ``open_ports_not_in_latest_scan`` count (the
    browser had its own copy, with a ``first_seen`` fallback and every port
    state — review 2026-10-23b, FEP-8).  Provenance, never "closed": the newer
    observation may not have probed the port.  A port with no ``last_seen``
    is not known to be older, so it is not marked."""
    host_seen = _aware(host.last_seen)
    if host_seen is None:
        return []
    out: List[int] = []
    for p in host.ports:
        ps = _aware(p.last_seen)
        if p.state == "open" and ps is not None and ps < host_seen - _SAME_SWEEP:
            out.append(p.id)
    return out


def host_assessment(db: Session, host: models.Host) -> Dict[str, Any]:
    hid = host.id

    last_tested_at, tests_executed = (
        db.query(
            func.max(func.coalesce(EvidenceRecord.executed_at, EvidenceRecord.created_at)),
            func.count(EvidenceRecord.id),
        )
        .filter(
            EvidenceRecord.host_id == hid,
            EvidenceRecord.outcome.in_(TESTED_OUTCOMES),
        )
        .one()
    )

    # Provenance: the newest vulnerability evidence — a row (a misconfiguration
    # check is not a vulnerability assessment, v2.415.0) or a vulnerability
    # scanner's run over the host, which is an assessment even when it
    # reported nothing (v2.372.0).
    last_vuln_at = (
        db.query(func.max(Vulnerability.last_seen))
        .filter(Vulnerability.host_id == hid, vulnerability_evidence_filter())
        .scalar()
    )
    last_vuln_scan_at = (
        db.query(func.max(models.HostScanHistory.discovered_at))
        .join(models.Scan, models.HostScanHistory.scan_id == models.Scan.id)
        .filter(models.HostScanHistory.host_id == hid, vuln_scanned_filter())
        .scalar()
    )
    vuln_dates = [d for d in (_aware(last_vuln_at), _aware(last_vuln_scan_at)) if d is not None]

    # Eligible / assessed are the Evidence page's own host predicates, read
    # for this one host in one statement — never restated here (review
    # 2026-10-23b, INV-3: this copy had drifted on web, counting a web
    # interface of another project).
    flags = db.execute(
        select(*[
            cond(d).label(f"{d}_{kind}")
            for d in _DOMAINS
            for kind, cond in (("eligible", domain_eligible_condition), ("assessed", domain_assessed_condition))
        ]).where(models.Host.id == hid)
    ).one()._mapping

    # Provenance only: when the web evidence was gathered (the same rows
    # ``domain_assessed_condition("web_tls")`` reads).
    last_web_at = (
        db.query(func.max(models.WebInterface.last_seen))
        .filter(models.WebInterface.host_id == hid,
                models.WebInterface.project_id == host.project_id)
        .scalar()
    )

    stale_open = len(not_revalidated_port_ids(host))

    return {
        "last_observed_at": host.last_seen,
        "vuln_assessed": bool(flags["vuln_assessment_assessed"]),
        "last_vuln_assessed_at": max(vuln_dates) if vuln_dates else None,
        # Beside "assessed", never part of it (review 2026-10-01): did a
        # vulnerability scan log in to this host?  yes / no / not_stated, or
        # None when the host is not assessed.  One definition, shared with the
        # Evidence page's counts and the DSL's ``vulnscan:``.
        "vuln_scan_credentialed": host_vuln_scan_credentialed(db, hid),
        "web_eligible": bool(flags["web_tls_eligible"]),
        "web_assessed": bool(flags["web_tls_assessed"]),
        "last_web_assessed_at": last_web_at,
        "auth_eligible": bool(flags["auth_smb_ad_eligible"]),
        "auth_assessed": bool(flags["auth_smb_ad_assessed"]),
        "tests_executed": int(tests_executed or 0),
        "last_tested_at": last_tested_at,
        # The Hosts-list badge's and the conflicts pane's count — host and
        # port rows (INV-1).
        "conflicts": host_conflict_counts(db, [hid]).get(hid, 0),
        "open_ports_not_in_latest_scan": stale_open,
    }
