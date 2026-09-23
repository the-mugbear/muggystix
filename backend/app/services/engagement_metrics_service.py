"""Per-project engagement counts shared by every cross-project surface.

Portfolio (members) and Oversight (global admins) must report the SAME numbers
for a project, so neither computes them itself.  Everything here is a handful
of grouped queries over a list of project ids — never one query per project,
host or row.

What is counted (PORTFOLIO.md, "Metrics and counting rules"):

* **Targets** — hosts in the project's inventory (one per IP per project).
* **Review** — distinct hosts with a review record in ``in_review`` /
  ``reviewed`` (any reviewer).  *Tested* is either, each host once, so it can
  be less than in-review + reviewed when two people hold one host in
  different states.  "Watching" is not testing.
* **Findings** — distinct ``Finding`` rows by the finding's severity, without
  false positives: a finding whose own status is ``false_positive``, or every
  one of whose endpoints is, is not a result.  A finding with no endpoint rows
  still counts.  One finding on 40 hosts counts once; ``affected_targets`` is
  the distinct hosts with a live-or-remediated (not false-positive) endpoint.
* **Scanner observations** — ``vulnerabilities`` rows by the scanner's
  severity, split into *judged* and *not yet judged* by the host inspector's
  rule (``host_serialization._vuln_coverage``): a row is judged when a scanner
  finding for its issue — promoted from this row, or sharing its issue key —
  includes the row's host, in any endpoint state (promoted, false positive
  here, accepted risk, …).  A finding that covers the issue only on OTHER hosts
  leaves this row not yet judged.  ``tests/test_engagement_metrics.py`` pins
  the SQL to ``_vuln_coverage`` so the two cannot drift.

Informational and unknown severities are left out of every severity block.
This measures judging, not fixing — there is no remediation dimension.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List

from sqlalchemy import and_, case, distinct, exists, func, or_
from sqlalchemy.orm import Session

from app.db.models import FollowStatus, Host, HostFollow
from app.db.models_findings import (
    Finding, FindingHost, FindingHostStatus, FindingSource, FindingStatus,
)
from app.db.models_vulnerability import (
    Vulnerability, VulnerabilitySeverity, enum_value,
)

SEVERITIES = ("critical", "high", "medium", "low")
_VULN_SEVERITIES = tuple(VulnerabilitySeverity(s) for s in SEVERITIES)


@dataclass
class SeverityCounts:
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0

    def add(self, severity: str, n: int) -> None:
        if severity in SEVERITIES:
            setattr(self, severity, getattr(self, severity) + int(n or 0))

    def as_dict(self) -> Dict[str, int]:
        return {s: getattr(self, s) for s in SEVERITIES}


@dataclass
class ProjectEngagement:
    host_count: int = 0
    hosts_in_review: int = 0
    hosts_reviewed: int = 0
    hosts_tested: int = 0
    findings: SeverityCounts = field(default_factory=SeverityCounts)
    finding_affected_targets: int = 0
    observations: SeverityCounts = field(default_factory=SeverityCounts)
    observations_judged: SeverityCounts = field(default_factory=SeverityCounts)
    observations_unjudged: SeverityCounts = field(default_factory=SeverityCounts)


def observation_judged_on_host():
    """Correlated EXISTS over an outer query on ``Vulnerability`` joined to
    ``Host``: a scanner finding for this row's issue includes this row's host.

    The SQL form of ``_vuln_coverage(...)["finding_on_this_host"]`` — the
    inspector evaluates it per row in Python, which cannot scale to an
    organisation's whole inventory.
    """
    return exists().where(
        FindingHost.finding_id == Finding.id,
        FindingHost.host_id == Vulnerability.host_id,
        Finding.project_id == Host.project_id,
        Finding.source == FindingSource.SCANNER.value,
        or_(
            Finding.vuln_id == Vulnerability.id,
            and_(
                Vulnerability.issue_key.isnot(None),
                Finding.dedup_key == Vulnerability.issue_key,
            ),
        ),
    )


def finding_is_a_result():
    """SQL condition on ``Finding``: not dismissed as a false positive —
    neither by its own status nor on every one of its endpoints."""
    fp_endpoint = FindingHostStatus.FALSE_POSITIVE.value
    has_endpoint = exists().where(FindingHost.finding_id == Finding.id)
    has_real_endpoint = exists().where(
        FindingHost.finding_id == Finding.id,
        FindingHost.host_status != fp_endpoint,
    )
    return and_(
        Finding.status != FindingStatus.FALSE_POSITIVE.value,
        or_(~has_endpoint, has_real_endpoint),
    )


def project_engagement(db: Session, project_ids: Iterable[int]) -> Dict[int, ProjectEngagement]:
    """Current engagement counts for each project id (every id present)."""
    ids: List[int] = list(dict.fromkeys(project_ids))
    out: Dict[int, ProjectEngagement] = {pid: ProjectEngagement() for pid in ids}
    if not ids:
        return out

    for pid, n in (
        db.query(Host.project_id, func.count(Host.id))
        .filter(Host.project_id.in_(ids))
        .group_by(Host.project_id)
        .all()
    ):
        out[pid].host_count = n

    review_states = (FollowStatus.IN_REVIEW, FollowStatus.REVIEWED)
    for pid, status, n in (
        db.query(Host.project_id, HostFollow.status, func.count(distinct(HostFollow.host_id)))
        .join(Host, Host.id == HostFollow.host_id)
        .filter(Host.project_id.in_(ids), HostFollow.status.in_(review_states))
        .group_by(Host.project_id, HostFollow.status)
        .all()
    ):
        if enum_value(status) == FollowStatus.IN_REVIEW.value:
            out[pid].hosts_in_review = n
        else:
            out[pid].hosts_reviewed = n
    for pid, n in (
        db.query(Host.project_id, func.count(distinct(HostFollow.host_id)))
        .join(Host, Host.id == HostFollow.host_id)
        .filter(Host.project_id.in_(ids), HostFollow.status.in_(review_states))
        .group_by(Host.project_id)
        .all()
    ):
        out[pid].hosts_tested = n

    is_result = finding_is_a_result()
    for pid, severity, n in (
        db.query(Finding.project_id, Finding.severity, func.count(Finding.id))
        .filter(
            Finding.project_id.in_(ids),
            Finding.severity.in_(SEVERITIES),
            is_result,
        )
        .group_by(Finding.project_id, Finding.severity)
        .all()
    ):
        out[pid].findings.add(severity, n)
    for pid, n in (
        db.query(Finding.project_id, func.count(distinct(FindingHost.host_id)))
        .join(FindingHost, FindingHost.finding_id == Finding.id)
        .filter(
            Finding.project_id.in_(ids),
            Finding.severity.in_(SEVERITIES),
            Finding.status != FindingStatus.FALSE_POSITIVE.value,
            FindingHost.host_status != FindingHostStatus.FALSE_POSITIVE.value,
        )
        .group_by(Finding.project_id)
        .all()
    ):
        out[pid].finding_affected_targets = n

    judged = observation_judged_on_host()
    for pid, severity, total, n_judged in (
        db.query(
            Host.project_id,
            Vulnerability.severity,
            func.count(Vulnerability.id),
            func.sum(case((judged, 1), else_=0)),
        )
        .select_from(Vulnerability)
        .join(Host, Host.id == Vulnerability.host_id)
        .filter(
            Host.project_id.in_(ids),
            Vulnerability.severity.in_(_VULN_SEVERITIES),
        )
        .group_by(Host.project_id, Vulnerability.severity)
        .all()
    ):
        sev = enum_value(severity)
        n_judged = int(n_judged or 0)
        e = out[pid]
        e.observations.add(sev, total)
        e.observations_judged.add(sev, n_judged)
        e.observations_unjudged.add(sev, total - n_judged)

    return out
