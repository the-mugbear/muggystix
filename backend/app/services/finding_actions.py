"""The finding changes a person makes, callable from more than one place (v2.436.0).

A person edits a finding's report text or promotes / dismisses a scanner
observation through the findings routes.  An agent's PROPOSAL of the same
change must, when a person accepts it, do exactly what that person's click
would have done — same validation, same authorship rule, same history — so
the logic lives here and both paths call it.  Before this it was written
inside the route handlers, where a service could not reach it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.security import check_permissions
from app.db.models_auth import User, UserRole
from app.db.models_findings import Finding, FindingStatus
from app.db.models_project import ProjectMembership, ProjectRole
from app.db.models_vulnerability import Vulnerability
from app.services.cvss_service import CvssError, normalize_cvss
from app.services.finding_service import FindingService
from app.services.report_text import REPORT_TEXT_FIELDS


@dataclass
class FindingActor:
    """Who is acting, as far as authored-content rights go (v2.375.0): a
    finding's report text and title are its author's, or a project admin's."""
    user_id: int
    is_project_admin: bool

    def may_modify(self, finding: Finding) -> bool:
        return self.is_project_admin or (
            finding.created_by_id is not None and finding.created_by_id == self.user_id
        )


def finding_actor(db: Session, project_id: int, user: User) -> FindingActor:
    if user.role == UserRole.ADMIN:
        return FindingActor(user_id=user.id, is_project_admin=True)
    role = (
        db.query(ProjectMembership.role)
        .filter(ProjectMembership.project_id == project_id, ProjectMembership.user_id == user.id)
        .scalar()
    )
    return FindingActor(
        user_id=user.id,
        is_project_admin=bool(role) and check_permissions(role, ProjectRole.ADMIN.value),
    )


def require_modify(actor: FindingActor, finding: Finding, what: str) -> None:
    if not actor.may_modify(finding):
        raise HTTPException(
            status_code=403,
            detail=f"Only the finding's author or a project admin can {what}.",
        )


def apply_report_text(
    finding: Finding, actor: FindingActor, sent: Dict[str, Optional[object]],
) -> Dict[str, object]:
    """Apply report-text fields (the five sections, ``cvss_vector``,
    ``cvss_score``) that are present in ``sent``.  Only a field whose value
    actually changes needs the right, so resending an unchanged form after a
    triage edit is not refused.  Returns the applied changes; the caller
    commits."""
    changes: Dict[str, object] = {}
    for field in REPORT_TEXT_FIELDS:
        if field in sent:
            value = (str(sent[field] or "")).strip() or None
            if value != getattr(finding, field):
                changes[field] = value
    if "cvss_vector" in sent or "cvss_score" in sent:
        vector = sent["cvss_vector"] if "cvss_vector" in sent else finding.cvss_vector
        score = sent["cvss_score"] if "cvss_score" in sent else finding.cvss_score
        try:
            vector, score = normalize_cvss(vector, score)
        except CvssError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        if vector != finding.cvss_vector:
            changes["cvss_vector"] = vector
        if score != finding.cvss_score:
            changes["cvss_score"] = score
    if changes:
        require_modify(actor, finding, "edit its report text")
        for field, value in changes.items():
            setattr(finding, field, value)
    return changes


def promote_or_dismiss_vulnerability(
    db: Session,
    *,
    vuln: Vulnerability,
    project_id: int,
    actor_id: int,
    severity: Optional[str] = None,
    status: Optional[str] = None,
    owner_id: Optional[int] = None,
    summary: Optional[str] = None,
    scope: Optional[str] = None,
) -> Finding:
    """Promote a scanner observation to a finding, or dismiss it.

    v2.360.0 — a false-positive dismissal is about THIS host unless the caller
    says the whole issue.  v2.366.0 — a PROMOTION may be too (``scope:
    "host"``): "confirmed" is then recorded for the host that was looked at,
    not for every host carrying the issue.  The default for a promotion stays
    "issue".  Accepted risk is issue-wide: a decision about the issue, not a
    host.  The caller commits.
    """
    status = status or FindingStatus.CONFIRMED.value
    is_fp = status == FindingStatus.FALSE_POSITIVE.value
    scope = scope or ("host" if is_fp else "issue")
    if scope == "host" and status == FindingStatus.ACCEPTED_RISK.value:
        raise HTTPException(
            status_code=422,
            detail="scope='host' does not apply to accepted risk, which is a decision "
                   "about the issue on every host.",
        )
    svc = FindingService(db)
    if scope == "host" and is_fp:
        return svc.dismiss_vulnerability_on_host(
            vuln=vuln, project_id=project_id, actor_id=actor_id,
            severity=severity, owner_id=owner_id, summary=summary,
        )
    return svc.promote_vulnerability(
        vuln=vuln, project_id=project_id, actor_id=actor_id,
        severity=severity, status=status, owner_id=owner_id, summary=summary,
        only_this_host=(scope == "host"),
    )
